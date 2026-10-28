"""Function-level de-translation driver: effects -> recovered 68k instruction list."""
from __future__ import annotations

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
            if k in ("label", "call", "callreg"):
                snap = dict(X.entry_snap())
            out.append(("P", e))
    return out


def _window(primaries, pos, back=2, fwd=2):
    lo = max(0, pos - back)
    hi = min(len(primaries), pos + fwd)
    return [(i, primaries[i]) for i in range(lo, hi)]


def detranslate(code: bytes, base: int, amap: AddrMap | None = None) -> Result:
    amap = amap or AddrMap()
    ex = symexec.Executor(decode_all(code, base))
    effects = ex.run()
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
    r = Result(ex.start, ex.end, out, effects, calls, ex.labels)
    return r


def _branch(e, amap) -> M68:
    cond, tgt, addr = e[1], e[2], e[-1]
    cc = T.cond_code(cond)
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
            if missing:
                if missing == ["X"]:
                    m.conf = min(m.conf, 0.9)
                elif len(missing) == len(exp):
                    m.conf = min(m.conf, 0.7)
                    m.note = (m.note + " no flags").strip()
                else:
                    m.conf = min(m.conf, 0.8)
                    m.note = (m.note + f" flags missing {''.join(missing)}").strip()


SIZE_BYTES = {"b": 1, "w": 2, "l": 4}


def _peepholes(out: list[M68]) -> None:
    """(aN)+ / -(aN) folding."""
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        # op ...(aN)... ; addq.l #s,aN   -> (aN)+
        if b.mnem == "addq" and b.size == "" and len(b.ops) == 2 and b.ops[1][0] == "a" and not b.flags_got:
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
        if a.mnem == "subq" and a.size == "" and len(a.ops) == 2 and a.ops[1][0] == "a" and not a.flags_got:
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
