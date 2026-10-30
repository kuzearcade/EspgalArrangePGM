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
  * `bchg #0` <-> `eori #1` (flags dead); `add #0,Dn` <-> `move Dn,Dn` (a tst).
"""
from __future__ import annotations

from .verify import recovered_canon

INV = {"bcc": "bcs", "bcs": "bcc", "beq": "bne", "bne": "beq", "bpl": "bmi", "bmi": "bpl", "bvc": "bvs",
       "bvs": "bvc", "bge": "blt", "blt": "bge", "bgt": "ble", "ble": "bgt", "bhi": "bls", "bls": "bhi"}
FLAG_READERS = set(INV) | {"addx", "subx", "negx", "roxl", "roxr"} | {"s" + c[1:] for c in INV} \
    | {"db" + c[1:] for c in INV}
FLAG_ONLY = {"cmp", "cmpa", "btst"}
SZ = {"b": 1, "w": 2, "l": 4}


def gaps(rep, res, index) -> list[tuple]:
    """[(twin run, 68k run, next 68k insn, previous 68k insn, next twin insn)] between
    consecutive aligned pairs."""
    T = [i for i in res.insns if not i.is_label and not i.is_comment]
    ti = {id(x): n for n, x in enumerate(T)}
    ps = sorted((ti[id(x)], index.pos_of(k.addr)) for x, k in rep.pairs if id(x) in ti)
    out = []
    for (a1, b1), (a2, b2) in zip(ps, ps[1:]):
        if a2 > a1 + 1 or b2 > b1 + 1:
            out.append((T[a1 + 1:a2], index.ks[b1 + 1:b2], index.ks[b2], index.ks[b1], T[a2]))
    return out


def unexplained(rep, res, index, funcs: dict, targets) -> list[tuple]:
    """The gaps no rule explains."""
    return [g for g in gaps(rep, res, index) if rule(*g, res, index, funcs, targets) is None]


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


def _maps(f, x, funcs) -> bool:
    return f == x or (isinstance(f, str) and f.startswith("f_") and funcs.get(int(f[2:], 16)) == x)


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


def rule(tw, k68, nxt, prv, tnext, res, index, funcs, targets) -> str | None:
    """Name of the rule that makes the twin run equivalent to the 68k run, or None."""
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
        if n_t == 2 and ct[0].mnem == "jsr" and ct[1].mnem == "rts" and _maps(ct[0].ops[0][1], ck[0].ops[0][1], funcs):
            return "tail-call"
        if n_t == 1 and ct[0].mnem == "jsr" and tnext.mnem == "rts" and nxt.canon.mnem == "rts" \
                and _maps(ct[0].ops[0][1], ck[0].ops[0][1], funcs):
            return "tail-call"
    if n_k == 1 and ck[0].mnem in INV and n_t == 3 and ct[0].mnem == INV[ck[0].mnem] and ct[1].mnem == "jsr" \
            and ct[2].mnem == "rts" and _maps(ct[1].ops[0][1], ck[0].ops[0][1], funcs):
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
    return None
