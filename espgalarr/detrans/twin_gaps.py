"""Gaps in the alignment of an arcade twin with its 68k region that are the translator's own
rewrites of the same code (not inserted or missing behaviour).

Between two aligned instruction pairs, the twin run (de-translated MIPS) and the 68k run
are equivalent when one of these holds:

  * flag-only twin instructions (tst/cmp/btst) with no 68k counterpart: the translator's own
    flag computations (e.g. `swap d0 ; tst.l d0`);
  * the same immediate bytes stored (a word/long store split into byte stores), flags dead;
  * conditional returns: `bcc L ; rts` (L right after) <-> `bcs <rts block>`, and
    `bcc <rts>` <-> `bcs L ; rts`; a branch to an rts <-> rts;
  * tail calls: `bra/jmp X` <-> `jsr F ; rts` (F the translation of X), `bcc X` <->
    `bcs L ; jsr F ; rts`, and `jmp X` <-> `jsr F` before the aligned rts;
  * calls <-> calls of a callee the map does not know yet (the port refuses unresolved ones);
  * a 68k branch to the next instruction; a twin branch to its next instruction (the jump
    over an entry's padding); dead 68k code after an unconditional jump (no branch target);
  * `bhi L` <-> `bcs M ; bne L` (M next), `bls L` <-> `bcs L ; beq L`;
  * register saves `movem R,-(a7)` <-> stores of R below a7 + `adda`; restores with the same
    register list;
  * `bchg #0` <-> `eori #1` (flags dead); `add #0,Dn` <-> `move Dn,Dn` (a tst);
  * straight-line runs with the same effect (registers, stores in order, calls, flags unless
    dead), also over several neighbouring gaps (the translator moves a `lea` or a store
    across independent instructions);
  * branch runs that reach the same places for every combination of flags (`bge` written
    as `bpl ; bvs ; bmi ; bvc`, inverted branches around a `bra`);
  * outlined fragments: `jsr F` <-> the 68k run F de-translates to (F's final rts left out),
    and tail calls to an F that is the translation of the 68k branch target;
  * `subq.w #1,Dn` <-> `subq.l #1,Dn` right after `move.w <ea>,Dn ; beq` (no borrow);
  * a sprite's graphics address <-> its PS2 sprite number (detrans/sprite_ids.py);
  * runs that split into pieces each explained by one of these.
"""
from __future__ import annotations

import difflib

from .verify import recovered_canon

INV = {"bcc": "bcs", "bcs": "bcc", "beq": "bne", "bne": "beq", "bpl": "bmi", "bmi": "bpl", "bvc": "bvs",
       "bvs": "bvc", "bge": "blt", "blt": "bge", "bgt": "ble", "ble": "bgt", "bhi": "bls", "bls": "bhi"}
FLAG_READERS = set(INV) | {"addx", "subx", "negx", "roxl", "roxr"} | {"s" + c[1:] for c in INV} \
    | {"db" + c[1:] for c in INV}
FLAG_ONLY = {"cmp", "cmpa", "btst"}
SZ = {"b": 1, "w": 2, "l": 4}


def _spans(rep, res, index):
    T = [i for i in res.insns if not i.is_label and not i.is_comment]
    ti = {id(x): n for n, x in enumerate(T)}
    ps = sorted((ti[id(x)], index.pos_of(k.addr)) for x, k in rep.pairs if id(x) in ti)
    return T, [(a1, a2, b1, b2) for (a1, b1), (a2, b2) in zip(ps, ps[1:]) if a2 > a1 + 1 or b2 > b1 + 1]


def gaps(rep, res, index) -> list[tuple]:
    """[(twin run, 68k run, next 68k insn, previous 68k insn, next twin insn)] between
    consecutive aligned pairs."""
    T, sp = _spans(rep, res, index)
    return [(T[a1 + 1:a2], index.ks[b1 + 1:b2], index.ks[b2], index.ks[b1], T[a2]) for a1, a2, b1, b2 in sp]


class Ctx:
    """What the rules beyond a single gap need: the aligned pairs (where a twin label lands
    in the 68k), the arcade code of a callee (`body(F)` -> its instructions or None) and the
    sprite-number table."""

    def __init__(self, rep, res, index, funcs, body=None, ids=None):
        self.res, self.index, self.funcs, self.body, self.ids = res, index, funcs, body, ids
        self.pair = {id(x): k for x, k in rep.pairs}
        self._same: dict = {}
        self.depth = 0

    def lands(self, label: str):
        """The instruction a twin label is put on (labels and comments skipped)."""
        ins = self.res.insns
        for n, i in enumerate(ins):
            if i.is_label and i.text().rstrip(":").strip() == label:
                for j in ins[n + 1:]:
                    if not (j.is_label or j.is_comment):
                        return j
                return None
        return None

    def same_code(self, f, addr: int) -> bool:
        """Is the arcade routine f (`f_<hex>`) the translation of the 68k code at addr?"""
        if self.body is None or not (isinstance(f, str) and f.startswith("f_")):
            return False
        key = (f, addr)
        if key not in self._same:
            b = self.body(int(f[2:], 16))
            try:
                pos = self.index.pos_of(addr)
            except (KeyError, ValueError, TypeError):
                pos = None
            ok = b is not None and pos is not None and self.index.ks[pos].addr == addr
            self._same[key] = False          # (recursion guard)
            if ok:
                insns, labels = b
                ok = any(_code_eq(insns, labels, self.index.ks[pos:pos + n], None, self.funcs, stack=True, ctx=self)
                         for n in {len(insns), len(insns) - 1})
            self._same[key] = ok
        return self._same[key]


def unexplained(rep, res, index, funcs: dict, targets, body=None, ids=None) -> list[tuple]:
    """The gaps no rule explains (neighbouring gaps are also tried together)."""
    ctx = Ctx(rep, res, index, funcs, body, ids)
    T, sp = _spans(rep, res, index)
    ks = index.ks
    out, i = [], 0
    while i < len(sp):
        a1, a2, b1, b2 = sp[i]
        g = (T[a1 + 1:a2], ks[b1 + 1:b2], ks[b2], ks[b1], T[a2])
        if rule(*g, res, index, funcs, targets, ctx) is not None:
            i += 1
            continue
        for j in range(i + 1, min(i + 3, len(sp))):
            c2, d2 = sp[j][1], sp[j][3]
            tw, k68 = T[a1 + 1:c2], ks[b1 + 1:d2]
            if len(tw) + len(k68) <= 40 and not any(k.addr in targets for k in k68) \
                    and same_effect(tw, k68, ks[d2], funcs, ctx):
                i = j + 1
                break
        else:
            out.append(g)
            i += 1
    return out


def _label(res, label: str, before) -> bool:
    """Is `label:` among the labels right before the instruction `before`?"""
    for n, i in enumerate(res.insns):
        if i is before:
            k = n - 1
            while k >= 0 and (res.insns[k].is_label or res.insns[k].is_comment):
                if res.insns[k].is_label and res.insns[k].text().rstrip(":").strip() == label:
                    return True
                k -= 1
            return False
    return False


def _rts_block(res, label: str) -> bool:
    for n, i in enumerate(res.insns):
        if i.is_label and i.text().rstrip(":").strip() == label:
            for j in res.insns[n + 1:]:
                if not (j.is_label or j.is_comment):
                    return j.mnem == "rts"
    return False


def _rts68(index, addr) -> bool:
    try:
        k = index.ks[index.pos_of(addr)]
    except (KeyError, IndexError, ValueError, TypeError):
        return False
    return k.addr == addr and k.canon.mnem == "rts"


def _maps(f, x, funcs, ctx=None) -> bool:
    if isinstance(f, str) and f.startswith("f_") and funcs.get(int(f[2:], 16)) is not None:
        f = funcs[int(f[2:], 16)]
    if f == x:
        return True
    if ctx is None or not isinstance(x, int):
        return False
    if isinstance(f, int):
        return _skips_to(ctx.index, x, f)
    return ctx.same_code(f, x)


def _skips_to(index, x: int, f: int) -> bool:
    """Does the 68k code at x reach f doing nothing but nops and loads of an address register
    that is written again before it is read (the translator enters after them)?"""
    try:
        p = index.pos_of(x)
    except (KeyError, ValueError, TypeError):
        return False
    ks = index.ks
    if p >= len(ks) or ks[p].addr != x:
        return False
    for _ in range(4):
        c = ks[p].canon
        if c.mnem == "nop" or (c.mnem in ("lea", "movea") and c.ops[-1][0] == "an" and _dead(ks, p + 1, c.ops[-1][1])):
            p += 1
            if p < len(ks) and ks[p].addr == f:
                return True
            continue
        return False
    return False


def _dead(ks, p: int, reg: str) -> bool:
    """Is reg written before it is read in the straight-line 68k code from position p?"""
    for k in ks[p:p + 12]:
        c = k.canon
        if c.mnem in INV or c.mnem in ("bra", "jmp", "jsr", "rts", "dbf"):
            return False
        reads = [o for o in c.ops[:-1] if reg in o[1:]] if c.ops else []
        last = c.ops[-1] if c.ops else None
        if reads or (last is not None and last[0] not in ("an",) and reg in last[1:]):
            return False
        if c.mnem in ("lea", "movea") and last == ("an", reg):
            return True
    return False


def _stores(cs):
    """{(base, offset): byte} written by a run of immediate stores, or None."""
    mem = {}
    for c in cs:
        if c.mnem != "move" or len(c.ops) != 2 or c.ops[0][0] != "imm" or c.ops[1][0] not in ("d16", "abs", "ind"):
            return None
        n, v = SZ[c.size], c.ops[0][1]
        dst = ("d16", c.ops[1][1], 0) if c.ops[1][0] == "ind" else c.ops[1]
        base, off = (dst[:2], dst[2]) if dst[0] == "d16" else (("abs",), dst[1])
        for k in range(n):
            mem[base + (off + k,)] = (v >> (8 * (n - 1 - k))) & 0xFF
    return mem


def rule(tw, k68, nxt, prv, tnext, res, index, funcs, targets, ctx=None) -> str | None:
    """Name of the rule that makes the twin run equivalent to the 68k run, or None."""
    r = _rule1(tw, k68, nxt, prv, tnext, res, index, funcs, targets, ctx)
    if r is None and ctx is not None and (len(tw) > 1 or len(k68) > 1):
        r = _compose(tw, k68, nxt, prv, tnext, res, index, funcs, targets, ctx)
    return r


def _compose(tw, k68, nxt, prv, tnext, res, index, funcs, targets, ctx) -> str | None:
    """The runs split into consecutive pieces, each explained by one rule (or the same
    instruction on both sides)."""
    nt, nk = len(tw), len(k68)
    if nt > 16 or nk > 24:
        return None
    reach = {(0, 0): ()}
    order = sorted({(i, j) for i in range(nt + 1) for j in range(nk + 1)})
    for i, j in order:
        if (i, j) not in reach:
            continue
        for di in range(0, min(4, nt - i) + 1):
            for dj in range(0, min(8, nk - j) + 1):
                if (di, dj) == (0, 0) or (di, dj) == (nt, nk) or (i + di, j + dj) in reach:
                    continue
                t, k = tw[i:i + di], k68[j:j + dj]
                if di == dj == 1 and recovered_canon(t[0]) is not None \
                        and recovered_canon(t[0]).text == k[0].canon.text and k[0].canon.mnem not in INV \
                        and k[0].canon.mnem not in ("bra", "dbf"):
                    why = "same"
                else:
                    why = _rule1(t, k, k68[j + dj] if j + dj < nk else nxt, k68[j - 1] if j else prv,
                                 tw[i + di] if i + di < nt else tnext, res, index, funcs, targets, ctx)
                if why is not None:
                    reach[(i + di, j + dj)] = reach[(i, j)] + (why,)
    got = reach.get((nt, nk))
    return "+".join(got) if got else None


def _rule1(tw, k68, nxt, prv, tnext, res, index, funcs, targets, ctx) -> str | None:
    ct = [recovered_canon(x) for x in tw]
    ck = [k.canon for k in k68]
    if any(c is None for c in ct):
        return None
    flags_dead = nxt is None or nxt.canon.mnem not in FLAG_READERS
    n_k, n_t = len(ck), len(ct)
    if not ck and ct and all(c.mnem in FLAG_ONLY for c in ct):
        return "flag-only"
    if ck and not ct and prv.canon.mnem in ("bra", "jmp", "rts") and not any(k.addr in targets for k in k68):
        return "dead"
    if not ck and n_t == 1 and ct[0].mnem == "bra" and _label(res, tw[0].ops[0], tnext):
        return "twin-bra-next"
    if n_k == 1 and not ct and ck[0].mnem == "bra" and ck[0].ops[0][1] == nxt.addr:
        return "bra-next"
    if n_k == 1 and n_t == 2 and ck[0].mnem == "bhi" and ct[0].mnem == "bcs" and ct[1].mnem == "bne" \
            and _label(res, tw[0].ops[0], tnext):
        return "bhi"
    if n_k == 1 and n_t == 2 and ck[0].mnem == "bls" and {ct[0].mnem, ct[1].mnem} == {"bcs", "beq"} \
            and tw[0].ops[0] == tw[1].ops[0]:
        return "bls"
    if n_k == 1 and n_t == 1 and ck[0].mnem == "bra" and ct[0].mnem == "rts" and _rts68(index, ck[0].ops[0][1]):
        return "bra-rts"
    if n_k == 1 and n_t == 2 and ck[0].mnem in INV and ct[0].mnem == INV[ck[0].mnem] and ct[1].mnem == "rts" \
            and _rts68(index, ck[0].ops[0][1]):
        return "condret"
    if n_k == 2 and ck[1].mnem == "rts" and ck[0].mnem in INV and n_t == 1 and ct[0].mnem == INV[ck[0].mnem] \
            and ck[0].ops[0][1] == nxt.addr and _rts_block(res, tw[0].ops[0]):
        return "condret"
    if n_k == 1 and ck[0].mnem in ("bra", "jmp"):
        if n_t == 2 and ct[0].mnem == "jsr" and ct[1].mnem == "rts" and _maps(ct[0].ops[0][1], ck[0].ops[0][1], funcs, ctx):
            return "tail-call"
        if n_t == 1 and ct[0].mnem == "jsr" and tnext.mnem == "rts" and nxt.canon.mnem == "rts" \
                and _maps(ct[0].ops[0][1], ck[0].ops[0][1], funcs, ctx):
            return "tail-call"
    if n_k == 1 and ck[0].mnem in INV and n_t == 3 and ct[0].mnem == INV[ck[0].mnem] and ct[1].mnem == "jsr" \
            and ct[2].mnem == "rts" and _maps(ct[1].ops[0][1], ck[0].ops[0][1], funcs, ctx):
        return "tail-call"
    if n_k == 1 and ck[0].mnem == "movem" and ck[0].ops[1] == ("pre", "a7") and ct and ct[-1].mnem == "adda" \
            and ct[-1].ops[1] == ("an", "a7") \
            and all(c.mnem == "move" and c.ops[1][0] in ("d16", "ind") and c.ops[1][1] == "a7"
                    and c.ops[0][0] in ("dn", "an") for c in ct[:-1]):
        regs = {int(c.ops[0][1][1]) + (8 if c.ops[0][0] == "an" else 0) for c in ct[:-1]}
        if regs == {b for b in range(16) if ck[0].ops[0][1] >> b & 1}:
            return "stack-save"
    if n_k == 1 and n_t == 1 and ck[0].mnem == ct[0].mnem == "movem" \
            and ck[0].ops[0] == ct[0].ops[0] == ("post", "a7") and ck[0].ops[1] == ct[0].ops[1]:
        return "stack-restore"
    if ck and n_k == n_t and all(a.mnem == "jsr" == b.mnem for a, b in zip(ck, ct)):
        return "call"
    if ck and ct and flags_dead:
        a, b = _stores(ct), _stores(ck)
        if a is not None and a == b:
            return "split-store"
    if n_k == 1 and n_t == 1 and ck[0].mnem == "bchg" and ct[0].mnem == "eor" and ck[0].ops[0] == ("imm", 0) \
            and ct[0].ops[0] == ("imm", 1) and ck[0].ops[1] == ct[0].ops[1] and flags_dead:
        return "bchg"
    if n_k == 1 and n_t == 1 and ck[0].mnem == "add" and ck[0].ops[0] == ("imm", 0) and ct[0].mnem == "cmp" \
            and ct[0].ops == (("imm", 0), ck[0].ops[1]) and ck[0].size == ct[0].size:
        return "tst-noop"
    if n_k == n_t == 1 and ck[0].mnem == ct[0].mnem == "add" and ck[0].size == "w" and ct[0].size == "l" \
            and ck[0].ops == (("imm", 0xFFFF), ck[0].ops[1]) and ct[0].ops == (("imm", 0xFFFFFFFF), ck[0].ops[1]) \
            and ck[0].ops[1][0] == "dn" and flags_dead and prv.canon.mnem == "beq" and k68[0].addr not in targets \
            and _sets_word(index, prv.addr, ck[0].ops[1]):
        # Dn.w was just found non-zero: the decrement does not borrow into the upper word
        return "no-borrow"
    if n_k == n_t == 1 and ck[0].mnem == ct[0].mnem == "move" and ck[0].size == ct[0].size == "l" \
            and ck[0].ops[1] == ct[0].ops[1] == ("dn", "d2") and ck[0].ops[0][0] == ct[0].ops[0][0] == "imm" \
            and ct[0].ops[0][1] < 0x8000 <= 0x80000 <= ck[0].ops[0][1] \
            and (ctx is None or ctx.ids is None or ctx.ids.get(ct[0].ops[0][1]) == ck[0].ops[0][1]):
        return "sprite-number"
    if ctx is not None:
        if ck and ct and all(c.mnem in INV or c.mnem == "bra" for c in ck) \
                and all(c.mnem in INV or c.mnem == "bra" for c in ct) and _same_branches(tw, ct, k68, ck, nxt, tnext, ctx):
            return "branch-tree"
        o = _outlined(tw, ct, k68, ck, nxt, tnext, ctx)
        if o:
            return o
        if ck and ct and same_effect(tw, k68, nxt, funcs, ctx):
            return "same-effect"
    return None


def _sets_word(index, addr, reg) -> bool:
    """Is the 68k instruction before addr `move.w <ea>,reg` or `tst.w reg`?"""
    try:
        p = index.pos_of(addr)
    except (KeyError, ValueError, TypeError):
        return False
    if p <= 0:
        return False
    c = index.ks[p - 1].canon
    return c.size == "w" and ((c.mnem == "move" and c.ops[1] == reg) or (c.mnem == "cmp" and c.ops == (("imm", 0), reg)))


# ---------------------------------------------------------------- branch runs

_CONDS = {
    "bra": lambda n, z, v, c: True, "beq": lambda n, z, v, c: z, "bne": lambda n, z, v, c: not z,
    "bcs": lambda n, z, v, c: c, "bcc": lambda n, z, v, c: not c, "bmi": lambda n, z, v, c: n,
    "bpl": lambda n, z, v, c: not n, "bvs": lambda n, z, v, c: v, "bvc": lambda n, z, v, c: not v,
    "bhi": lambda n, z, v, c: not c and not z, "bls": lambda n, z, v, c: c or z,
    "bge": lambda n, z, v, c: n == v, "blt": lambda n, z, v, c: n != v,
    "bgt": lambda n, z, v, c: not z and n == v, "ble": lambda n, z, v, c: z or n != v,
}


def _same_branches(tw, ct, k68, ck, nxt, tnext, ctx) -> bool:
    """Do both branch runs end up at the same place for every combination of N, Z, V, C?"""
    def dest68(a):
        for n, k in enumerate(k68):
            if k.addr == a:
                return ("at", n)
        return ("next",) if a == nxt.addr else ("ext", a)

    def dest_tw(label):
        x = ctx.lands(label)
        if x is None:
            return None
        for n, t in enumerate(tw):
            if t is x:
                return ("at", n)
        if x is tnext:
            return ("next",)
        k = ctx.pair.get(id(x))
        if k is None:
            return None
        return ("next",) if k.addr == nxt.addr else ("ext", k.addr)

    d68 = [dest68(c.ops[0][1]) if c.ops and c.ops[0][0] == "label" and isinstance(c.ops[0][1], int) else None
           for c in ck]
    dtw = [dest_tw(x.ops[0]) if x.ops else None for x in tw]
    if None in d68 or None in dtw:
        return False

    def run(cs, ds, flags):
        pc, steps = 0, 0
        while pc < len(cs) and steps < 32:
            steps += 1
            if _CONDS[cs[pc].mnem](*flags):
                d = ds[pc]
                if d[0] != "at":
                    return d
                if d[1] <= pc:
                    return None
                pc = d[1]
            else:
                pc += 1
        return ("next",) if pc >= len(cs) else None

    for b in range(16):
        flags = tuple(bool(b >> k & 1) for k in range(4))
        x, y = run(ck, d68, flags), run(ct, dtw, flags)
        if x is None or x != y:
            return False
    return True


# ---------------------------------------------------------------- outlined fragments

def _code_eq(insns, labels, ks, end_addr, funcs, stack: bool = False, ctx=None) -> bool:
    """_code_eq_strict, or (with ctx) an alignment whose gaps the rules explain."""
    if _code_eq_strict(insns, labels, ks, end_addr, funcs, stack, ctx):
        return True
    if ctx is None or ctx.depth > 1 or not ks or not insns:
        return False
    cs = [recovered_canon(x) for x in insns]
    if any(c is None for c in cs):
        return False
    if not stack and any(o[0] in ("ind", "post", "pre", "d16", "d8") and o[1] == "a7" for c in cs for o in c.ops):
        return False
    a, b = [c.key for c in cs], [k.canon.key for k in ks]
    blocks = [x for x in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() if x.size]
    if not blocks:
        return False
    # aligned pieces: branch for branch; between them: a gap a rule explains
    at = {}
    for x in blocks:
        for n in range(x.size):
            at[x.a + n] = x.b + n
    at[len(cs)] = len(ks)
    edges = [(-1, -1)] + [(x.a + x.size - 1, x.b + x.size - 1) for x in blocks]
    starts = [(x.a, x.b) for x in blocks] + [(len(cs), len(ks))]
    ctx.depth += 1
    try:
        for (a1, b1), (a2, b2) in zip(edges, starts):
            if a2 == a1 + 1 and b2 == b1 + 1:
                continue
            tw, k68 = insns[a1 + 1:a2], ks[b1 + 1:b2]
            nxt = ks[b2] if b2 < len(ks) else _END
            prv = ks[b1] if b1 >= 0 else _END
            tnext = insns[a2] if a2 < len(insns) else None
            if any(recovered_canon(x).mnem in INV or recovered_canon(x).mnem in ("bra", "jmp", "dbf") for x in tw) \
                    or any(k.canon.mnem in INV or k.canon.mnem in ("bra", "jmp", "dbf") for k in k68):
                return False                  # (branches only where they align)
            if _rule1(tw, k68, nxt, prv, tnext, _NORES, ctx.index, funcs, frozenset(), ctx) is None:
                return False
    finally:
        ctx.depth -= 1
    for n, c in enumerate(cs):
        if n not in at:
            continue
        k = ks[at[n]].canon
        for o, ok in zip(c.ops, k.ops):
            if o[0] == "label":
                m = labels.get(o[1])
                if m is None or m not in at or ok[1] != (ks[at[m]].addr if at[m] < len(ks) else end_addr):
                    return False
            elif o[0] == "func" and not _maps(o[1], ok[1], funcs, ctx):
                return False
    return True


class _NORES:
    insns: list = []


class _End:
    """Past a fragment's end (its caller may read the flags)."""
    addr = None

    class canon:
        mnem = "beq"
        ops = ()


_END = _End()


def _code_eq_strict(insns, labels, ks, end_addr, funcs, stack: bool = False, ctx=None) -> bool:
    """Is a de-translated instruction list (labels: {label: index it is put on}) the 68k
    sequence ks, branch for branch? A label past the end is end_addr; `jsr F ; rts` may stand
    for the 68k's `jmp`/`bra` to F's code (a tail call). Without `stack` the code may not
    address memory through a7 (an outlined fragment is called: the call pushes a return
    address the inline 68k code does not see)."""
    cs = [recovered_canon(x) for x in insns]
    if any(c is None for c in cs):
        return False
    at, i, j, pend = {}, 0, 0, []
    while i < len(cs) and j < len(ks):
        c, k = cs[i], ks[j].canon
        at[i] = j
        if not stack and any(o[0] in ("ind", "post", "pre", "d16", "d8") and o[1] == "a7" for o in c.ops):
            return False
        if c.mnem == "jsr" and i + 1 < len(cs) and cs[i + 1].mnem == "rts" and k.mnem in ("jmp", "bra") \
                and k.ops and k.ops[0][0] in ("func", "label") and isinstance(k.ops[0][1], int) \
                and _maps(c.ops[0][1], k.ops[0][1], funcs, ctx):
            at[i + 1] = j
            i, j = i + 2, j + 1
            continue
        if c.mnem == k.mnem == "jsr" and k.ops[0][0] == "label" and isinstance(k.ops[0][1], int):
            # the 68k's bsr to a local label <-> a call of its translation
            if not _maps(c.ops[0][1], k.ops[0][1], funcs, ctx):
                return False
            i, j = i + 1, j + 1
            continue
        if c.key != k.key:
            return False
        for o, ok in zip(c.ops, k.ops):
            if o[0] == "label":
                pend.append((o[1], ok[1]))
            elif o[0] == "func" and not _maps(o[1], ok[1], funcs, ctx):
                return False
        i, j = i + 1, j + 1
    if i != len(cs) or j != len(ks):
        return False
    at[len(cs)] = len(ks)
    for lab, addr in pend:
        n = labels.get(lab)
        if n is None or n not in at:
            return False
        want = ks[at[n]].addr if at[n] < len(ks) else end_addr
        if want is None or addr != want:
            return False
    return True


def _outlined(tw, ct, k68, ck, nxt, tnext, ctx) -> str | None:
    """`jsr F` <-> the 68k run F de-translates to (the translator's outlined fragment)."""
    if ctx.body is None or not ct or ct[0].mnem != "jsr" or len(ct) > 2:
        return None
    f = ct[0].ops[0][1]
    if not (isinstance(f, str) and f.startswith("f_")) or ctx.funcs.get(int(f[2:], 16)) is not None:
        return None
    b = ctx.body(int(f[2:], 16))
    if b is None:
        return None
    insns, labels = b
    last = recovered_canon(insns[-1]) if insns else None
    if last is None:
        return None
    if len(ct) == 1 and last.mnem == "rts" and _code_eq(insns[:-1], labels, k68, nxt.addr, ctx.funcs, ctx=ctx):
        return "outlined"
    # a fragment ending in `jmp (An)` for the 68k's `jsr (An)`: the jump's target returns to
    # right after the call to the fragment
    if len(ct) == 1 and last.mnem == "jmp" and ck and ck[-1].mnem == "jsr" and ck[-1].ops == last.ops \
            and last.ops[0][0] == "ind" and _code_eq(insns[:-1], labels, k68[:-1], None, ctx.funcs, ctx=ctx):
        return "outlined-call"
    # a fragment ending in a jump: the twin returns right after the call (on the 68k the jump's
    # target returns to the caller of the routine)
    if last.mnem in ("jmp", "bra") and _code_eq(insns, labels, k68, None, ctx.funcs, ctx=ctx) \
            and (ct[-1].mnem == "rts" if len(ct) == 2 else tnext.mnem == "rts"):
        return "outlined-tail"
    return None


# ---------------------------------------------------------------- straight-line effects

M32 = 0xFFFFFFFF
_MASK = {"b": 0xFF, "w": 0xFFFF, "l": M32, "": M32}
_BYTES = {"b": 1, "w": 2, "l": 4, "": 4}


def _lin(e):
    if isinstance(e, int):
        return (), e
    if e[0] == "+":
        return e[1], e[2]
    return (e,), 0


def _add(a, b):
    ta, ca = _lin(a)
    tb, cb = _lin(b)
    t = tuple(sorted(ta + tb, key=repr))
    c = (ca + cb) & M32
    if not t:
        return c
    return t[0] if len(t) == 1 and c == 0 else ("+", t, c)


def _sx(e, size):
    if isinstance(e, int):
        m = _MASK[size]
        e &= m
        return (e - m - 1) & M32 if e > m >> 1 else e
    return ("sx", size, e)


def _lo(e, size):
    if size in ("l", ""):
        return e
    if isinstance(e, int):
        return e & _MASK[size]
    if e[0] == "ins":
        if e[1] == size:
            return e[3]
        if _BYTES[e[1]] > _BYTES[size]:
            return _lo(e[3], size)
    return ("lo", size, e)


def _ins(old, size, v):
    if size in ("l", ""):
        return v
    if isinstance(old, int) and isinstance(v, int):
        m = _MASK[size]
        return (old & ~m & M32) | (v & m)
    return ("ins", size, old, v)


class _Sym:
    def __init__(self, funcs, ctx):
        self.funcs, self.ctx = funcs, ctx
        self.r: dict = {}
        self.eff: list = []
        self.f = {k: ("f0", k) for k in "NZVCX"}

    def reg(self, n):
        return self.r.get(n, ("r", n))

    def ea(self, op, size):
        """Address of a memory operand (pre/post-increment applied)."""
        k = op[0]
        if k == "ind":
            return self.reg(op[1])
        if k == "d16":
            return _add(self.reg(op[1]), op[2] & M32)
        if k == "d8":
            x = self.reg(op[2])
            x = _sx(_lo(x, "w"), "w") if op[3] == "w" else x
            return _add(_add(self.reg(op[1]), x), op[4] & M32)
        if k in ("abs", "pc"):
            return op[1]
        if k == "absx":
            x = self.reg(op[2])
            x = _sx(_lo(x, "w"), "w") if op[3] == "w" else x
            return _add(x, op[1])
        n = _BYTES[size] if not (size == "b" and op[1] == "a7") else 2
        if k == "pre":
            self.r[op[1]] = _add(self.reg(op[1]), -n & M32)
            return self.reg(op[1])
        if k == "post":
            a = self.reg(op[1])
            self.r[op[1]] = _add(a, n)
            return a
        raise ValueError(k)

    def load(self, a, size):
        for e in reversed(self.eff):
            if e[0] == "st" and e[2] == a and e[1] == size:
                return e[3]
            if e[0] != "st" or not _apart(e[2], _BYTES[e[1]], a, _BYTES[size]):
                break
        return ("ld", size, a, len(self.eff))

    def read(self, op, size):
        k = op[0]
        if k == "imm":
            return op[1] & _MASK[size]
        if k in ("dn", "an"):
            return _lo(self.reg(op[1]), size)
        return self.load(self.ea(op, size), size)

    def loc(self, op, size):
        """(kind, where) of a destination, side effects of its addressing applied once."""
        if op[0] in ("dn", "an"):
            return ("reg", op[1])
        return ("mem", self.ea(op, size))

    def get(self, loc, size):
        return _lo(self.reg(loc[1]), size) if loc[0] == "reg" else self.load(loc[1], size)

    def put(self, loc, size, v):
        if loc[0] == "reg":
            self.r[loc[1]] = _ins(self.reg(loc[1]), size, v)
            return
        a = loc[1]
        if isinstance(v, int):
            for k in range(_BYTES[size]):           # constant stores compare bytewise
                self.eff.append(("st", "b", _add(a, k), (v >> (8 * (_BYTES[size] - 1 - k))) & 0xFF))
        else:
            self.eff.append(("st", size, a, v))

    def nz(self, size, v, vc=True):
        self.f["N"], self.f["Z"] = ("N", size, v), ("Z", size, v)
        if vc:
            self.f["V"] = self.f["C"] = 0

    def step(self, c) -> bool:
        m, s, ops = c.mnem, c.size, c.ops
        if m == "move" and len(ops) == 2:
            v = self.read(ops[0], s)
            self.put(self.loc(ops[1], s), s, v)
            self.nz(s, v)
        elif m == "movea":
            v = self.read(ops[0], s or "l")
            self.r[ops[1][1]] = _sx(v, "w") if s == "w" else v
        elif m == "lea":
            self.r[ops[1][1]] = self.ea(ops[0], "l")
        elif m == "adda":
            v = self.read(ops[0], s or "l")
            self.r[ops[1][1]] = _add(self.reg(ops[1][1]), _sx(v, "w") if s == "w" else v)
        elif m in ("add", "sub", "and", "or", "eor", "cmp") and len(ops) == 2:
            a = self.read(ops[0], s)
            d = self.loc(ops[1], s)
            b = self.get(d, s)
            if m == "add" and isinstance(a, int) and s == "l" and d[0] == "reg" and d[1][0] == "a":
                v = _add(b, a)
            else:
                v = (m, s, b, a)
            if m == "cmp":
                v = ("sub", s, b, a)
            else:
                self.put(d, s, v)
            if m in ("add", "sub", "cmp"):
                self.f["N"], self.f["Z"] = ("N", s, v), ("Z", s, v)
                self.f["V"], self.f["C"] = ("V", m, s, b, a), ("C", m if m != "cmp" else "sub", s, b, a)
                if m != "cmp":
                    self.f["X"] = self.f["C"]
            else:
                self.nz(s, v)
        elif m in ("asl", "lsl", "lsr", "asr") and ops:
            if len(ops) == 1:
                cnt, d = 1, self.loc(ops[0], "w")
                s = "w"
            else:
                cnt, d = self.read(ops[0], "l"), self.loc(ops[1], s)
            b = self.get(d, s)
            kind = "lsl" if m == "asl" else m
            v = (kind, s, b, cnt)
            self.put(d, s, v)
            self.f["N"], self.f["Z"] = ("N", s, v), ("Z", s, v)
            self.f["C"] = self.f["X"] = ("Cs", kind, s, b, cnt)
            self.f["V"] = ("Vasl", s, b, cnt) if m == "asl" else 0
        elif m in ("ext", "swap", "neg", "not"):
            d = self.loc(ops[0], s or "l")
            if m == "ext":
                v = _sx(self.get(d, "b" if s == "w" else "w"), "b" if s == "w" else "w")
                v = _lo(v, s)
                s2 = s
            elif m == "swap":
                v, s2 = ("swap", self.get(d, "l")), "l"
            else:
                s2 = s
                b = self.get(d, s)
                v = (m, s, b)
            self.put(d, s2, v)
            if m == "neg":
                self.f["N"], self.f["Z"] = ("N", s2, v), ("Z", s2, v)
                self.f["V"] = self.f["C"] = self.f["X"] = ("negf", s2, v)
            else:
                self.nz(s2, v)
        elif m == "exg":
            x, y = ops[0][1], ops[1][1]
            self.r[x], self.r[y] = self.reg(y), self.reg(x)
        elif m == "jsr":
            t = ops[0][1]
            if isinstance(t, str):
                t = self.funcs.get(int(t[2:], 16)) if t.startswith("f_") else None
            if not isinstance(t, int) or ops[0][0] != "func":
                return False
            self.eff.append(("call", t, tuple(sorted((k, v) for k, v in self.r.items()))))
            n = len(self.eff)
            self.r = {f"{x}{i}": ("after", n, f"{x}{i}") for x in "da" for i in range(8)}
            self.f = {k: ("after", n, k) for k in "NZVCX"}
        else:
            return False
        return True


def _apart(a, n, b, m) -> bool:
    """Do [a, a+n) and [b, b+m) certainly not overlap (same base, distinct offsets)?"""
    ta, ca = _lin(a)
    tb, cb = _lin(b)
    if ta != tb:
        return False
    d = (cb - ca) & M32
    return n <= d <= (1 << 32) - m


def same_effect(tw, k68, nxt, funcs, ctx) -> bool:
    """Do the two straight-line runs leave registers, memory (stores in order), calls and
    flags the same (flags may differ when the next 68k instruction does not read them)?"""
    ct = [recovered_canon(x) for x in tw]
    if any(c is None for c in ct):
        return False
    a, b = _Sym(funcs, ctx), _Sym(funcs, ctx)
    try:
        if not all(a.step(c) for c in ct) or not all(b.step(k.canon) for k in k68):
            return False
    except (ValueError, KeyError, IndexError, TypeError):
        return False
    if a.eff != b.eff:
        return False
    regs = set(a.r) | set(b.r)
    if any(a.reg(r) != b.reg(r) for r in regs):
        return False
    dead = nxt is None or nxt.canon.mnem not in FLAG_READERS
    return dead or a.f == b.f
