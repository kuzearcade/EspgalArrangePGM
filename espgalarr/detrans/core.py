"""Function-level de-translation driver: effects -> recovered 68k instruction list."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import expr as X
from . import symexec, templates as T
from .expr import FLAGS
from .mips import decode_all
from .templates import M68, AddrMap

ORDER = ("C", "V", "Z", "N", "X")


@dataclass
class Result:
    start: int
    end: int
    insns: list[M68]
    effects: list
    calls: list[int] = field(default_factory=list)
    labels: set = field(default_factory=set)

    @property
    def n_known(self) -> int:
        return sum(1 for i in self.insns if i.mnem and not i.is_label)

    @property
    def n_unknown(self) -> int:
        return sum(1 for i in self.insns if not i.mnem and not i.is_label)

    def text(self, header: bool = True) -> str:
        lines = []
        if header:
            lines.append(f"; de-translated from MIPS {self.start:#x}-{self.end:#x}")
        lines.append(f"F_{self.start:x}:")
        for i in self.insns:
            if i.is_label and i.addr == self.start:
                continue
            t = i.text()
            if i.mnem and i.conf < 0.95:
                t = f"{t:40s}; conf {i.conf:.2f}" + (f" {i.note}" if i.note else "")
            lines.append(t)
        return "\n".join(lines) + "\n"


def _alias_pass(effects):
    """The compiler forwards a value it has just stored into a register slot into the
    following instructions instead of reloading the slot (`move.w (a1),d7 ; bset #9,d7`
    becomes `d7 = [a1] ; d7 = or([a1], 0x200)`).  Replace such forwarded expressions by
    the register leaf so the recogniser sees `or(d7, 0x200)`."""
    out = []
    aliases: dict = {}
    for e in effects:
        k = e[0]
        if k in ("label", "call", "callreg", "jump", "rts", "jumpreg", "jumptable"):
            aliases.clear()
            out.append(e)
            continue
        if aliases:
            if k == "store":
                v = e[2]
                for old, new in aliases.items():
                    v = X.subst(v, old, new)
                d = e[1]
                if d[0] == "mem":
                    a = d[1]
                    for old, new in aliases.items():
                        a = X.subst(a, old, new)
                    d = (d[0], a, d[2])
                e = (k, d, v, e[-1])
            elif k == "ccr" and e[1][0] == "ccr":
                snap = e[1][1]
                new_snap = []
                for f, b in snap:
                    for old, new in aliases.items():
                        b = X.subst(b, old, new)
                    new_snap.append((f, b))
                e = (k, ("ccr", tuple(new_snap)) + tuple(e[1][2:]), e[-1])
            elif k == "branch":
                c = e[1]
                for old, new in aliases.items():
                    c = X.subst(c, old, new)
                e = (k, c) + tuple(e[2:])
        out.append(e)
        if k == "store" and e[1][0] == "slot":
            name, size = e[1][1], e[1][2]
            v = e[2]
            vs = X.strip(v)
            if vs[0] not in ("c", "r68") and size >= 2:
                # drop aliases that were about the previous version of this register
                for old in [o for o, n in aliases.items() if n[1] == name]:
                    del aliases[old]
                ver = _version_after(effects, e)
                aliases[vs] = ("r68", name, ver)
    return out


def _version_after(effects, store):
    """Version number the executor gives the register after this store (the next load
    of that slot shows it); derived from the stored expression's own leaves when
    possible, else 1 + the highest version seen so far for the register."""
    name = store[1][1]
    best = 0
    for e in effects:
        if e is store:
            break
        if e[0] == "store" and e[1][0] == "slot" and e[1][1] == name:
            best += 1
    return best + 1


def flag_items(effects):
    """Expand ('ccr', ...) effects into ('F', [(flag, expr), ...], addr) assignment groups
    (one per CCR store, listing the flags that store explicitly assigns), in order."""
    out = []     # list of ('P', effect) / ('F', [(flag, expr)...], addr) / ('U', effect)
    snap = dict(X.entry_snap())
    for e in effects:
        k = e[0]
        if k == "ccr":
            val = e[1]
            if val[0] != "ccr":
                out.append(("U", e))
                continue
            new = dict(val[1])
            assigned = X.ccr_assigned(val)
            grp = [(f, new[f]) for f in ORDER if f in assigned or new[f] != snap[f]]
            if grp:
                out.append(("F", grp, e[-1]))
            snap = new
        else:
            if k == "label":
                snap = dict(e[2]) if len(e) > 3 else dict(X.entry_snap())
            elif k in ("call", "callreg"):
                snap = dict(X.entry_snap())
            out.append(("P", e))
    return out


def _window(primaries, pos, back=2, fwd=2):
    lo = max(0, pos - back)
    hi = min(len(primaries), pos + fwd)
    return [(i, primaries[i]) for i in range(lo, hi)]


def detranslate(code: bytes, base: int, amap: AddrMap | None = None, read_word=None) -> Result:
    amap = amap or AddrMap()
    ex = symexec.Executor(decode_all(code, base), read_word=read_word)
    effects = _alias_pass(ex.run())
    items = flag_items(effects)
    # primaries in order, with their index in items
    prim: list[M68] = []
    prim_pos: list[int] = []
    blocks: list[int] = []        # block id per primary (ownership windows do not cross)
    bid = 0
    calls = []
    for idx, it in enumerate(items):
        if it[0] != "P":
            continue
        e = it[1]
        k = e[0]
        if k == "label":
            bid += 1
            m = M68("", "", [], 1.0, e[1], is_label=True)
        elif k == "store":
            m = T.recognise_store(e, amap)
        elif k == "branch":
            m = _branch(e, amap)
        elif k == "jump":
            tgt = e[1]
            if ex.start <= tgt < ex.end:
                m = M68("bra", "", [f"L_{tgt:x}"], 1.0, e[-1])
            else:
                m = M68("jmp", "", [amap.code_label(tgt)], 1.0, e[-1])
            m.target = tgt
        elif k == "call":
            m = M68("jsr", "", [amap.code_label(e[1])], 1.0, e[-1])
            m.target = e[1]
            calls.append(e[1])
        elif k == "rts":
            m = M68("rts", "", [], 1.0, e[-1])
        elif k == "jumptable":
            idx = e[1]
            # index expression: mem[base + idx*4]; find the 68k register in it
            lv = sorted(T.leaves(idx))
            reg = lv[0][1] if lv else "?"
            m = M68("jmptbl", "", [reg, "(" + ",".join(f"L_{t:x}" for t in e[2]) + ")"], 0.8, e[-1],
                    note="jump table (68k: jmp tbl(pc,dN.w) or similar)")
        elif k == "jumpreg":
            r = T._reg(e[1])
            if r and r[0] == "a":
                m = M68("jmp", "", [f"({r})"], 1.0, e[-1])
            else:
                m = M68("", "", [], 0.0, e[-1], note=f"jmp {X.fmt(e[1])}")
        elif k == "callreg":
            r = T._reg(e[1])
            if r and r[0] == "a":
                m = M68("jsr", "", [f"({r})"], 1.0, e[-1])
            else:
                m = M68("", "", [], 0.0, e[-1], note=f"jsr {X.fmt(e[1])}")
        elif k == "mips":
            m = M68("", "", [], 0.0, e[-1], note=f"mips {e[1].text()}")
        else:
            m = M68("", "", [], 0.0, e[-1], note=repr(e))
        if k in ("call", "callreg"):
            bid_after = bid + 1
        else:
            bid_after = bid
        m.raw = e if k == "store" else m.raw
        prim.append(m)
        prim_pos.append(idx)
        blocks.append(bid)
        bid = bid_after
    # ---- flag ownership.  CCR stores all alias one address, so the compiler keeps them
    # in 68k instruction order; only the slot/memory stores float.  Build contiguous runs
    # of flag assignments (a run ends when a flag is re-assigned, when the assignments
    # start referencing a different store, or at a control transfer) and attach each run
    # to the store it is about.
    import bisect
    CONTROL = {"branch", "jump", "call", "rts", "jumpreg", "callreg", "label", "mips"}

    def block_at(pos):
        """Block of a flag item that sits just before primary `pos`."""
        if not prim:
            return 0
        if pos > 0 and prim[pos - 1].mnem == "jsr" and pos < len(prim):
            return blocks[pos]          # right after a call: the new block
        return blocks[pos - 1] if pos > 0 else blocks[0]

    def ref_owner(pos, f, b):
        """Store primary referenced by a non-constant flag expression (nearest)."""
        best = None
        for i, m in _window(prim, pos, 3, 3):
            if not m.mnem or m.raw is None or m.raw[0] != "store" or blocks[i] != block_at(pos):
                continue
            if f not in m.flags_expected:
                continue
            sc = T.flag_val_matches(f, b, m)
            if sc > 1:
                key = (-sc, abs((i + 0.5) - pos))
                if best is None or key < best[0]:
                    best = (key, i)
        return None if best is None else best[1]

    runs = []          # dicts: {'items': [(f,b,addr)], 'owner': idx|None, 'pos': int}
    cur = None
    last_ctrl = -1

    def close():
        nonlocal cur
        if cur is not None and cur["items"]:
            runs.append(cur)
        cur = None

    for idx, it in enumerate(items):
        if it[0] == "P":
            k = it[1][0]
            if k in CONTROL:
                close()
            continue
        pos = bisect.bisect_left(prim_pos, idx)
        if it[0] == "U":
            close()
            runs.append({"items": [("?", it[1][1], it[1][-1])], "owner": None, "pos": pos})
            continue
        _, grp, addr = it
        for f, b in grp:
            const = b[0] in ("true", "false")
            owner = None if const else ref_owner(pos, f, b)
            if cur is not None:
                have = cur["flags"]
                conflict = f in have and not (have[f] == X.FALSE and b != X.FALSE)
                if conflict or (owner is not None and cur["owner"] is not None and owner != cur["owner"]) \
                        or (cur["pos"] != pos and block_at(cur["pos"]) != block_at(pos)):
                    close()
            if cur is None:
                cur = {"items": [], "flags": {}, "owner": None, "pos": pos}
            if owner is not None and cur["owner"] is None:
                cur["owner"] = owner
            cur["items"].append((f, b, addr))
            cur["flags"][f] = b
    close()

    unclaimed = []    # (pos, flag, expr, addr)
    for ri, run in enumerate(runs):
        if run["items"][0][0] == "?":
            unclaimed.append((run["pos"], "?", run["items"][0][1], run["items"][0][2]))
            continue
        owner = run["owner"]
        pos = run["pos"]
        if owner is None:
            # constants only (or references nothing we know): nearest store expecting all
            fl = [f for f, _, _ in run["items"]]
            best = None
            for i, m in _window(prim, pos, 2, 2):
                if not m.mnem or m.raw is None or m.raw[0] != "store" or blocks[i] != block_at(pos):
                    continue
                if not m.flags_expected or any(f not in m.flags_expected for f in fl):
                    continue
                if any(f in m.flags_got for f in fl):
                    continue
                if not all(b[0] in ("true", "false") for _, b, _ in run["items"]):
                    continue
                key = (0 if i < pos else 1, abs((i + 0.5) - pos))
                if best is None or key < best[0]:
                    best = (key, i)
            if best is None:
                for f, b, addr in run["items"]:
                    unclaimed.append((pos, f, b, addr))
                continue
            owner = best[1]
        m = prim[owner]
        spill_before, spill_after = [], []
        seen_core = False
        for f, b, addr in run["items"]:
            fits = f in m.flags_expected and (f not in m.flags_got or (m.flags_got[f] == X.FALSE and b != X.FALSE))
            if fits:
                m.flags_got[f] = b
                seen_core = True
            elif b[0] in ("true", "false") and not seen_core:
                spill_before.append((f, b, addr))
            elif b[0] in ("true", "false"):
                spill_after.append((f, b, addr))
            else:
                unclaimed.append((pos, f, b, addr))
        # spill constants to the neighbouring runs' owners
        for sp, target in ((spill_before, ri - 1), (spill_after, ri + 1)):
            if not sp:
                continue
            tgt = runs[target]["owner"] if 0 <= target < len(runs) else None
            if tgt is None and 0 <= target < len(runs):
                # neighbour not yet resolved (constants-only run): give it the spill
                runs[target]["items"] = (sp + runs[target]["items"]) if target > ri else (runs[target]["items"] + sp)
                continue
            if tgt is not None:
                mt = prim[tgt]
                for f, b, addr in sp:
                    if f in mt.flags_expected and f not in mt.flags_got:
                        mt.flags_got[f] = b
                    else:
                        unclaimed.append((pos, f, b, addr))
            else:
                for f, b, addr in sp:
                    unclaimed.append((pos, f, b, addr))
    # flags-only groups
    extra: dict[int, list[M68]] = {}
    group = []
    gpos = None
    for pos, f, b, addr in unclaimed + [(None, None, None, None)]:
        if group and (pos != gpos or f == "?" or any(f == g[0] and not (g[1] == X.FALSE and b != X.FALSE) for g in group)):
            extra.setdefault(gpos, []).extend(T.recognise_flags_only(group, amap))
            group = []
        if pos is None:
            break
        if f == "?":
            extra.setdefault(pos, []).append(M68("", "", [], 0.0, addr, note=f"CCR = {X.fmt(b)}"))
            continue
        group.append((f, b, addr))
        gpos = pos
    out: list[M68] = []
    for i, m in enumerate(prim):
        for x in extra.get(i, []):
            out.append(x)
        out.append(m)
    for x in extra.get(len(prim), []):
        out.append(x)
    _finish(out)
    _peepholes(out)
    _branch_chains(out)
    _tail_expand(out)
    r = Result(ex.start, ex.end, out, effects, calls, ex.labels)
    return r


def _branch(e, amap) -> M68:
    cond, tgt, addr = e[1], e[2], e[-1]
    snap = e[3] if len(e) > 4 else None
    cc = T.cond_code(cond)
    if (cc is None or cc in ("t", "f")) and snap is not None:
        cc2 = T.semantic_cc(cond, snap)
        if cc2 is not None:
            cc = cc2
    m = M68("b" + cc, "", [f"L_{tgt:x}"], 1.0, addr) if cc else M68("", "", [], 0.0, addr, note=f"if {X.fmtb(cond)} goto L_{tgt:x}")
    m.target = tgt
    m.cond = cond
    return m


def _finish(out: list[M68]) -> None:
    """Confidence from flag coverage; bset/bclr decisions."""
    for m in out:
        if not m.mnem or m.is_label:
            continue
        exp = m.flags_expected
        got = m.flags_got
        alt_bit = getattr(m, "alt_bit", None)
        if alt_bit is not None:
            bit, bmn = alt_bit
            if set(got) <= {"Z"} and "Z" in got and got["Z"][0] in ("bnot", "bit", "eq0", "ne0"):
                m.mnem, m.ops, m.size = bmn, [f"#{bit}", m.ops[1]], ""
                m.flags_expected, m.note = "Z", ""
                continue
            m.note = ""
        if exp:
            missing = [f for f in exp if f not in got]
            if m.result is not None and X.strip(m.result)[0] == "c":
                # constant source: the compiler folds the N/Z diamonds away
                missing = [f for f in missing if f not in "NZ"]
            if missing:
                # the translator omits dead flag updates in places, so missing flags only
                # mildly reduce the confidence of a recognised data operation
                if missing == ["X"]:
                    m.conf = min(m.conf, 0.95)
                elif len(missing) == len(exp):
                    m.conf = min(m.conf, 0.85)
                    m.note = (m.note + " no flags").strip()
                else:
                    m.conf = min(m.conf, 0.9)
                    m.note = (m.note + f" flags missing {''.join(missing)}").strip()


SIZE_BYTES = {"b": 1, "w": 2, "l": 4}


REGNO = {f"d{i}": i for i in range(8)} | {f"a{i}": 8 + i for i in range(8)}


def _reglist_text(mask: int) -> str:
    parts = []
    for base, name in ((0, "d"), (8, "a")):
        i = 0
        while i < 8:
            if mask & (1 << (base + i)):
                j = i
                while j + 1 < 8 and mask & (1 << (base + j + 1)):
                    j += 1
                parts.append(f"{name}{i}" if i == j else f"{name}{i}-{name}{j}")
                i = j + 1
            else:
                i += 1
    return "/".join(parts)


def _d16(op: str, an: str):
    """displacement of a 'd(aN)' operand or None."""
    m = re.fullmatch(r"(-?\d+)\(" + an + r"\)", op)
    return int(m.group(1)) if m else None


def _movem_plain(out: list[M68]) -> None:
    """movem.s list,d(aN) / movem.s d(aN),list: >= 2 flag-less moves of ascending
    registers to/from consecutive displacements."""
    i = 0
    while i < len(out):
        m = out[i]
        if m.mnem != "move" or m.size not in ("w", "l") or m.flags_got or len(m.ops) != 2:
            i += 1
            continue
        step = 2 if m.size == "w" else 4
        for store in (True, False):
            regop, memop = (m.ops[0], m.ops[1]) if store else (m.ops[1], m.ops[0])
            if regop not in REGNO:
                continue
            an = next((c for c in REGNO if c[0] == "a" and (_d16(memop, c) is not None or memop == f"({c})")), None)
            if an is None:
                continue
            d0 = 0 if memop == f"({an})" else _d16(memop, an)
            j = i
            regs = []
            while j < len(out):
                x = out[j]
                if x.mnem == "move" and x.size == m.size and not x.flags_got and len(x.ops) == 2:
                    r_, mo = (x.ops[0], x.ops[1]) if store else (x.ops[1], x.ops[0])
                    d = 0 if mo == f"({an})" else _d16(mo, an)
                    if r_ in REGNO and d == d0 + step * len(regs) and (not regs or REGNO[r_] > REGNO[regs[-1]]):
                        regs.append(r_)
                        j += 1
                        continue
                break
            if len(regs) >= 2:
                mask = sum(1 << REGNO[r] for r in regs)
                mem = memop
                ops = [_reglist_text(mask), mem] if store else [mem, _reglist_text(mask)]
                new = M68("movem", m.size, ops, min(z.conf for z in out[i:j]), m.addr)
                out[i:j] = [new]
                break
        i += 1


def _movem(out: list[M68]) -> None:
    """movem.l list,-(aN): stores [-4k(aN)].l = reg ... then adda #-4n,aN;
       movem.l (aN)+,list: loads reg = [4k(aN)].l ... then adda #4n,aN."""
    i = 0
    while i < len(out):
        m = out[i]
        # push form
        if m.mnem == "move" and m.size == "l" and len(m.ops) == 2 and m.ops[0] in REGNO and not m.flags_got:
            an = None
            for cand in ("a7", "a6", "a5", "a4", "a3", "a2", "a1", "a0"):
                if _d16(m.ops[1], cand) is not None:
                    an = cand
                    break
            if an is not None:
                j = i
                regs = {}
                while j < len(out):
                    x = out[j]
                    if x.mnem == "move" and x.size == "l" and len(x.ops) == 2 and x.ops[0] in REGNO and not x.flags_got:
                        d = _d16(x.ops[1], an)
                        if d is not None and d < 0 and d % 4 == 0 and d not in regs:
                            regs[d] = x.ops[0]
                            j += 1
                            continue
                    break
                x = out[j] if j < len(out) else None
                if x is not None and len(regs) >= 2 and x.mnem in ("subq", "lea", "suba") and x.ops[-1] == an:
                    n = -int(x.ops[0][1:]) if x.mnem == "subq" else (_d16(x.ops[0], an) if x.mnem == "lea" else -int(x.ops[0][1:], 0))
                    if n == min(regs) and sorted(regs) == list(range(-4 * len(regs), 0, 4)):
                        # order check: highest register at the highest address
                        order = [regs[d] for d in sorted(regs)]
                        if all(REGNO[a] < REGNO[b] for a, b in zip(order, order[1:])):
                            mask = sum(1 << REGNO[r] for r in order)
                            new = M68("movem", "l", [_reglist_text(mask), f"-({an})"], min(z.conf for z in out[i:j]), m.addr)
                            out[i:j + 1] = [new]
                            i += 1
                            continue
        # pop form
        if m.mnem == "move" and m.size == "l" and len(m.ops) == 2 and m.ops[1] in REGNO and not m.flags_got:
            an = None
            for cand in ("a7", "a6", "a5", "a4", "a3", "a2", "a1", "a0"):
                if _d16(m.ops[0], cand) is not None or m.ops[0] == f"({cand})":
                    an = cand
                    break
            if an is not None:
                j = i
                regs = {}
                while j < len(out):
                    x = out[j]
                    if x.mnem == "move" and x.size == "l" and len(x.ops) == 2 and x.ops[1] in REGNO and not x.flags_got:
                        d = 0 if x.ops[0] == f"({an})" else _d16(x.ops[0], an)
                        if d is not None and d >= 0 and d % 4 == 0 and d not in regs:
                            regs[d] = x.ops[1]
                            j += 1
                            continue
                    break
                x = out[j] if j < len(out) else None
                if x is not None and len(regs) >= 2 and x.mnem in ("addq", "lea", "adda") and x.ops[-1] == an:
                    n = int(x.ops[0][1:]) if x.mnem == "addq" else (_d16(x.ops[0], an) if x.mnem == "lea" else int(x.ops[0][1:], 0))
                    if n == 4 * len(regs) and sorted(regs) == list(range(0, 4 * len(regs), 4)):
                        order = [regs[d] for d in sorted(regs)]
                        if all(REGNO[a] < REGNO[b] for a, b in zip(order, order[1:])):
                            mask = sum(1 << REGNO[r] for r in order)
                            new = M68("movem", "l", [f"({an})+", _reglist_text(mask)], min(z.conf for z in out[i:j]), m.addr)
                            out[i:j + 1] = [new]
                            i += 1
                            continue
        i += 1


def _eval_flags(b, env) -> bool:
    k = b[0]
    if k == "flag":
        return env[b[1]]
    if k == "true":
        return True
    if k == "false":
        return False
    if k == "bnot":
        return not _eval_flags(b[1], env)
    if k == "band":
        return _eval_flags(b[1], env) and _eval_flags(b[2], env)
    if k == "bor":
        return _eval_flags(b[1], env) or _eval_flags(b[2], env)
    if k == "bite":
        return _eval_flags(b[2], env) if _eval_flags(b[1], env) else _eval_flags(b[3], env)
    if k in ("ne", "eq") and len(b) == 3:
        f1, f2 = T._flagtest_flag(b[1]), T._flagtest_flag(b[2])
        if f1 and f2:
            return (env[f1] != env[f2]) if k == "ne" else (env[f1] == env[f2])
    raise ValueError("not a flag expression")


CC_TABLE = {
    "eq": lambda e: e["Z"], "ne": lambda e: not e["Z"], "mi": lambda e: e["N"], "pl": lambda e: not e["N"],
    "cs": lambda e: e["C"], "cc": lambda e: not e["C"], "vs": lambda e: e["V"], "vc": lambda e: not e["V"],
    "ls": lambda e: e["C"] or e["Z"], "hi": lambda e: not (e["C"] or e["Z"]),
    "lt": lambda e: e["N"] != e["V"], "ge": lambda e: e["N"] == e["V"],
    "le": lambda e: e["Z"] or e["N"] != e["V"], "gt": lambda e: not e["Z"] and e["N"] == e["V"],
}


def _branch_chains(out: list[M68]) -> None:
    """The compiler splits Bcc on compound conditions (blt = N^V, ble, bhi...) into
    chains of simple branches and local labels; fold such chains back into one Bcc."""
    import itertools
    i = 0
    while i < len(out):
        m = out[i]
        if m.is_label or not m.mnem.startswith("b") or not hasattr(m, "cond") or m.mnem in ("bra", "bsr"):
            i += 1
            continue
        # window: branches with flag conditions and labels, up to 8 entries
        j = i
        win = []
        while j < len(out) and len(win) < 8:
            x = out[j]
            if x.is_label or (x.mnem.startswith("b") and hasattr(x, "cond") and x.mnem not in ("bra", "bsr")):
                win.append(x)
                j += 1
            else:
                break
        folded = False
        for n in range(len(win), 1, -1):      # longest chain first
            w = win[:n]
            branches = [x for x in w if not x.is_label]
            labels = {x.addr for x in w if x.is_label}
            if len(branches) < 2 or w[-1].is_label is False and False:
                continue
            ext = {x.target for x in branches if x.target not in labels}
            if len(ext) != 1:
                continue
            tgt = ext.pop()
            # labels inside must only be targeted from inside the chain
            inside_ids = {id(x) for x in w}
            if any(getattr(y, "target", None) in labels for y in out if id(y) not in inside_ids):
                continue
            try:
                taken = set()
                for vals in itertools.product((False, True), repeat=4):
                    env = dict(zip("NZVC", vals))
                    k = 0
                    result = None
                    while k < len(w):
                        x = w[k]
                        if x.is_label:
                            k += 1
                            continue
                        if _eval_flags(x.cond, env):
                            if x.target == tgt:
                                result = True
                                break
                            # jump to a local label
                            k = next(idx for idx, y in enumerate(w) if y.is_label and y.addr == x.target)
                            continue
                        k += 1
                    taken.add(vals if result else None)
                    if result:
                        taken.add(vals)
            except (ValueError, StopIteration):
                continue
            taken.discard(None)
            for cc, fn in CC_TABLE.items():
                if all((vals in taken) == fn(dict(zip("NZVC", vals))) for vals in itertools.product((False, True), repeat=4)):
                    new = M68("b" + cc, "", [f"L_{tgt:x}"], min(x.conf for x in branches), m.addr)
                    new.target = tgt
                    new.cond = None
                    out[i:i + n] = [new] + [x for x in w if x.is_label and False]
                    folded = True
                    break
            if folded:
                break
        i += 1


def _tail_expand(out: list[M68]) -> None:
    """The compiler merges identical tails (one `jr $ra` serves several 68k `rts`):
    inline `bra L` when L's block is a short terminal block (<= 2 instructions ending
    in rts/jmp/bra)."""
    starts = {m.addr: i for i, m in enumerate(out) if m.is_label}
    for i, m in enumerate(out):
        if m.mnem != "bra" or getattr(m, "target", None) not in starts:
            continue
        j = starts[m.target] + 1
        blk = []
        while j < len(out) and not out[j].is_label and len(blk) < 3:
            blk.append(out[j])
            if out[j].mnem in ("rts", "jmp", "bra"):
                break
            j += 1
        if blk and blk[-1].mnem in ("rts", "jmp") and len(blk) <= 2 and all(b.mnem for b in blk):
            copies = []
            for b in blk:
                c = M68(b.mnem, b.size, list(b.ops), b.conf, m.addr, b.note, b.flags_expected, dict(b.flags_got))
                copies.append(c)
            out[i:i + 1] = copies


def _peepholes(out: list[M68]) -> None:
    """(aN)+ / -(aN) folding, movem."""
    _movem(out)
    _movem_plain(out)
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        # op ...(aN)... ; addq.l #s,aN   -> (aN)+
        if b.mnem == "addq" and b.size in ("", "l") and len(b.ops) == 2 and b.ops[1][0] == "a" and not b.flags_got:
            an = b.ops[1]
            s = int(b.ops[0][1:])
            sz = SIZE_BYTES.get(a.size, 0)
            if an == "a7" and sz == 1:
                sz = 2
            if a.mnem and sz == s and f"({an})" in a.ops and not a.is_label:
                a.ops = [f"({an})+" if o == f"({an})" else o for o in a.ops]
                del out[i + 1]
                continue
        # subq.l #s,aN ; op ...(aN)...  -> -(aN)
        if a.mnem == "subq" and a.size in ("", "l") and len(a.ops) == 2 and a.ops[1][0] == "a" and not a.flags_got:
            an = a.ops[1]
            s = int(a.ops[0][1:])
            sz = SIZE_BYTES.get(b.size, 0)
            if an == "a7" and sz == 1:
                sz = 2
            if b.mnem and sz == s and f"({an})" in b.ops and not b.is_label:
                b.ops = [f"-({an})" if o == f"({an})" else o for o in b.ops]
                del out[i]
                continue
        i += 1
