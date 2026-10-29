"""Function-level de-translation driver: effects -> recovered 68k instruction list."""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field

from . import expr as X
from . import symexec, templates as T
from .expr import FLAGS
from .mips import decode_all
from .templates import M68, AddrMap

ORDER = ("C", "V", "Z", "N", "X")
DEBUG_RUNS = None     # set to a list to collect the flag runs (debugging)


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
        return sum(1 for i in self.insns if not i.mnem and not i.is_label and not i.is_comment)

    def text(self, header: bool = True) -> str:
        lines = []
        if header:
            lines.append(f"; de-translated from MIPS {self.start:#x}-{self.end:#x}")
        lines.append(f"F_{self.start:x}:")
        own = f"L_{self.start:x}"
        if any(own in op for i in self.insns if not i.is_label for op in i.ops):
            lines.append(f"{own}:")             # a branch back to the entry
        for i in self.insns:
            if i.is_label and i.addr == self.start:
                continue
            t = i.text()
            if i.mnem and i.conf < 0.95:
                t = f"{t:40s}; conf {i.conf:.2f}" + (f" {i.note}" if i.note else "")
            if i.args:
                a = ",".join(f"{r}={v}" for r, v in sorted(i.args.items()))
                t = f"{t} mips-args {a}" if ";" in t else f"{t:40s}; mips-args {a}"
            lines.append(t)
        return "\n".join(lines) + "\n"


def _alias_pass(effects):
    """The compiler forwards a value it has just stored into a register slot into the
    following instructions instead of reloading the slot (`move.w (a1),d7 ; bset #9,d7`
    becomes `d7 = [a1] ; d7 = or([a1], 0x200)`).  Replace such forwarded expressions by
    the register leaf so the recogniser sees `or(d7, 0x200)`."""
    out = []
    aliases: dict = {}
    # slot stores whose value the compiler may have forwarded into *earlier* (scheduled
    # up) memory stores: pre-register them for the 4 effects before the slot store
    early: dict[int, list] = collections.defaultdict(list)
    for j, e in enumerate(effects):
        if e[0] == "store" and e[1][0] == "slot" and e[1][2] >= 2:
            vs = X.strip(e[2])
            if vs[0] not in ("c", "r68"):
                lo = max(0, j - 24)
                for t in range(j - 1, lo - 1, -1):
                    if effects[t][0] in ("label", "call", "callreg", "jump", "rts", "jumpreg", "jumptable", "branch"):
                        lo = t + 1      # the hoisted uses stay inside the basic block
                        break
                for t in range(lo, j):
                    early[t].append((vs, ("r68", e[1][1], _version_after(effects, e)), j))
    first_use: dict[int, int] = {}      # slot store index -> first effect renamed to use it
    for idx, e in enumerate(effects):
        k = e[0]
        if k in ("label", "call", "callreg", "jump", "rts", "jumpreg", "jumptable"):
            aliases.clear()
            out.append(e)
            continue
        if k == "store" and e[1][0] == "mem":
            for old_, new_, j in early.get(idx, ()):
                if old_[0] == "mem" and old_[1] == e[1][1]:
                    continue            # this store rewrites the loaded location itself
                if old_ not in aliases and (X.refs(e[2], lambda x: x == old_) or X.refs(e[1][1], lambda x: x == old_)):
                    first_use.setdefault(j, idx)
                aliases.setdefault(old_, new_)
        if k == "ccr" and e[1][0] == "ccr" and early.get(idx):
            # flags computed before such a store name its address the same way
            snap = e[1][1]
            for old_, new_, j in early[idx]:
                snap2 = tuple((f, _subst_addr(b, old_, new_)) for f, b in snap)
                if snap2 != snap:
                    first_use.setdefault(j, idx)
                snap = snap2
            e = (k, ("ccr", snap) + tuple(e[1][2:]), e[-1])
        if k == "store" and e[1][0] == "slot":
            # aliases about this register's previous value do not apply to its own update
            for old_ in [o for o, n in aliases.items() if n[1] == e[1][1]]:
                del aliases[old_]
        if aliases:
            if k == "store":
                v = e[2]
                for old, new in aliases.items():
                    if e[1][0] == "mem" and old[0] == "mem" and old[1] == e[1][1]:
                        continue        # read-modify-write of this very location (eori.w #1,m)
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
                        if _direct_test(b, old):
                            continue        # the stored value's own N/Z: keep as is
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
    # the renamed uses come first now: move the register load up to the first of them when
    # nothing in between reads the register or changes what its value is computed from
    for j in sorted(first_use, reverse=True):
        i0 = first_use[j]
        ld = out[j]
        name, ver = ld[1][1], _version_after(effects, effects[j])
        deps = {x[1] for x in _r68_leaves(ld[2])} | {name}
        between = out[i0:j]
        if any(x[0] == "store" and x[1][0] == "slot" and x[1][1] in deps for x in between):
            continue
        if X.refs(ld[2], lambda y: y[0] == "mem") and any(x[0] == "store" and x[1][0] == "mem" for x in between):
            continue                    # a load does not move above a memory store
        if any(X.refs(x[1:-1], lambda y: y[0] == "r68" and y[1] == name and (len(y) < 3 or y[2] != ver))
               for x in between):
            continue
        out.insert(i0, out.pop(j))
    return out


def _r68_leaves(e) -> list:
    out = []
    X.refs(e, lambda y: out.append(y) if isinstance(y, tuple) and y and y[0] == "r68" else False)
    return out


def _arg_text(v) -> str:
    """A MIPS argument value as `$k`, `dN`/`aN`, `aN+k` or `[aN+k].s` (68k memory), else '?'."""
    if v[0] == "c":
        return f"${v[1] & 0xFFFFFFFF:x}"
    if v[0] == "ret" and v[1] == 2:
        return "v0"                     # the previous native call's return value
    if v[0] == "arg":
        return f"ARG_A{v[1] - 4}"       # passed on from this routine's own arguments
    s = X.strip(v)
    if s[0] == "r68":
        return s[1]
    if s[0] == "add" and s[1][0] == "r68" and s[2][0] == "c":
        return f"{s[1][1]}{X.signed(s[2][1]):+d}"         # an address: lea k(aN)
    if s[0] == "mem" and s[2] in (1, 2, 4):
        ea = s[1]
        base, off = (ea, 0) if ea[0] == "r68" else (ea[1], X.signed(ea[2][1])) \
            if ea[0] == "add" and ea[1][0] == "r68" and ea[2][0] == "c" else (None, 0)
        if base is not None:
            return f"[{base[1]}{off:+d}].{'bwl'[s[2] // 2]}"
    return "?"


def _call_args(target: int, regs: dict, read_word) -> dict:
    """MIPS argument registers the target reads ({"a0": text}): native service arguments,
    code pointers for helpers, loads the translator moved from a callee into its callers."""
    used = symexec.reads_arg_regs(target, read_word)
    return {f"a{r - 4}": _arg_text(v) for r, v in regs.items()
            if r in used and v[0] != "in" and not (v[0] == "ret" and v[1] != 2)}


def _nz_about(f: str, b, m) -> bool:
    """Is the N/Z assignment b about instruction m's result?  (Z of a subtraction may be
    written as the comparison of its operands.)"""
    res = X.strip(m.result) if m.result is not None else None
    if res is not None and _direct_test(b, res):
        return True
    if T.flag_val_matches(f, b, m) == 3 and res is not None and res[0] not in ("mem", "r68", "c"):
        # a computed result (add/sub/...) inside a cast; a plain moved value tested inside
        # a larger expression is another instruction's (cmp #k after the move)
        return True
    if f == "N" and res is not None and res[0] == "and" and res[2][0] == "c" and b[0] == "bit" \
            and X.strip(b[1]) == X.strip(res[1]) and res[2][1] >> b[2] & 1:
        return True                     # andi: the mask keeps the sign bit of the operand
    if f == "Z" and res is not None and res[0] == "sub":
        c = b[1] if b[0] == "bnot" and b[1][0] == "ne" else b if b[0] == "eq" else None
        if c is not None and len(c) == 3:
            return {X.strip(c[1]), X.strip(c[2])} == {X.strip(res[1]), X.strip(res[2])}
    return False


def _subst_addr(e, old, new):
    """Replace `old` by `new` where it is a memory address inside e."""
    if not isinstance(e, tuple):
        return e
    if e and e[0] == "mem" and len(e) > 1 and e[1] == old:
        return ("mem", new) + tuple(e[2:])
    return tuple(_subst_addr(x, old, new) if isinstance(x, tuple) else x for x in e)


def _direct_test(b, e) -> bool:
    """Is b a direct N/Z test (eq0/ne0/bit/lt0/ge0) of expression e?"""
    if b[0] in ("eq0", "ne0", "bit", "lt0", "ge0") and isinstance(b[1], tuple):
        return X.strip(b[1]) == X.strip(e)
    if b[0] == "bnot":
        return _direct_test(b[1], e)
    return False


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


def detranslate(code: bytes, base: int, amap: AddrMap | None = None, read_word=None,
                extra_labels=None) -> Result:
    amap = amap or AddrMap()
    ex = symexec.Executor(decode_all(code, base), read_word=read_word, extra_labels=extra_labels)
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
                if len(e) > 3:
                    m.args = _call_args(tgt, e[2], read_word)
            m.target = tgt
        elif k == "call":
            m = M68("jsr", "", [amap.code_label(e[1])], 1.0, e[-1])
            m.target = e[1]
            # MIPS argument registers holding constants that the callee reads: what a native
            # PS2 service (or a helper taking a code pointer) is passed
            m.args = _call_args(e[1], e[2], read_word)
            calls.append(e[1])
        elif k == "rts":
            m = M68("rts", "", [], 1.0, e[-1])
        elif k == "jumptable":
            m = _jumptable(e)
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
        if getattr(m, "pre", None) is not None:
            m.pre.is_pre_test = True
            prim.append(m.pre)          # test for a branch on a value (no flag update)
            prim_pos.append(idx)
            blocks.append(bid)
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

    # positions of flag items that reference each store's result (N/Z disambiguation:
    # `tst E ; move E,dN` vs the move's own flags scheduled before its store)
    result_refs: dict[int, list[int]] = collections.defaultdict(list)
    for idx, it in enumerate(items):
        if it[0] != "F":
            continue
        pos_ = bisect.bisect_left(prim_pos, idx)
        for f, b in it[1]:
            if b[0] in ("true", "false") or f not in "NZ":
                continue
            for i, m in _window(prim, pos_, 3, 3):
                if m.mnem and m.raw is not None and m.raw[0] == "store" and T.flag_val_matches(f, b, m) == 3:
                    result_refs[i].append(pos_)

    def across_control(i, pos, f, b):
        """A branch/jump between store i and the flag item before primary pos.  A later
        store cannot own flags consumed before it ran; an earlier store's flags may be
        recomputed in a branch's fall-through, but only its own C/V/X or a direct N/Z test
        of its result (`cmp #k,<result>` after the branch is another instruction)."""
        if i < pos:
            res = prim[i].result
            if f not in "NZ" or res is not None and _direct_test(b, X.strip(res)):
                return False
            lo, hi = i + 1, pos
        else:
            lo, hi = pos, i
        return any(prim[j].mnem.startswith("b") and getattr(prim[j], "target", None) is not None
                   or prim[j].mnem in ("bra", "jmp", "rts") for j in range(lo, hi))

    def ref_owner(pos, f, b):
        """Store primary referenced by a non-constant flag expression (nearest)."""
        best = None
        for i, m in _window(prim, pos, 3, 3):
            if not m.mnem or m.raw is None or m.raw[0] != "store" or blocks[i] != block_at(pos):
                continue
            if across_control(i, pos, f, b):
                continue
            if f not in m.flags_expected:
                continue
            sc = T.flag_val_matches(f, b, m)
            if sc == 2 and f in "NZ":
                continue                # N/Z about an operand: another instruction's test
            if sc > 1:
                if sc == 3 and f in "NZ" and i >= pos and any(p > i for p in result_refs.get(i, ())):
                    continue        # a later flag group is about this store: this one is a tst
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
            o = cur["owner"]
            if o is not None and any(f in "NZ" and b[0] not in ("true", "false", "flag")
                                     and not _nz_about(f, b, prim[o])
                                     for f, b, _ in cur["items"]):
                # its N/Z test something else than the owner's result: a cmp/tst whose
                # C/V merely mention the owner's operands
                cur["owner"] = None
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
                if not conflict and owner is not None and cur["owner"] is None and cur.get("fresh_clear") \
                        and owner < pos:
                    conflict = True         # a new instruction's clear, then the previous one's late flag
                if not conflict and not const and cur["owner"] is None and any(x[1] == X.TRUE for x in cur["items"]):
                    conflict = True         # folded constant N/Z of a move #imm: that instruction is complete
                if conflict and owner is not None and owner == cur["owner"] and have[f][0] not in ("true", "false"):
                    conflict = False        # refinement of the same instruction's flag
                if not conflict and cur["owner"] is not None and f in "NZ" and not const \
                        and b[0] != "flag" and not _nz_about(f, b, prim[cur["owner"]]):
                    conflict = True         # another instruction's test after the owner's late flags
                if conflict or (owner is not None and cur["owner"] is not None and owner != cur["owner"]) \
                        or (cur["pos"] != pos and block_at(cur["pos"]) != block_at(pos)):
                    close()
            if cur is None:
                cur = {"items": [], "flags": {}, "owner": None, "pos": pos}
            if owner is not None and cur["owner"] is None:
                cur["owner"] = owner
            cur["items"].append((f, b, addr))
            cur["flags"][f] = b
            cur["fresh_clear"] = (cur["owner"] is None and len(cur["items"]) >= 3
                                  and all(x[1] == X.FALSE for x in cur["items"]))
    close()
    if DEBUG_RUNS is not None:
        DEBUG_RUNS.extend(dict(r, owner_text=prim[r["owner"]].text().strip() if r["owner"] is not None else None) for r in runs)

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
            for i, m in _window(prim, pos, 3, 2):
                if not m.mnem or m.raw is None or m.raw[0] != "store" or blocks[i] != block_at(pos):
                    continue
                if not m.flags_expected or any(f not in m.flags_expected for f in fl):
                    continue
                if any(f in m.flags_got for f in fl):
                    continue
                if not all(b[0] in ("true", "false") for _, b, _ in run["items"]):
                    continue
                exact = set(fl) == set(m.flags_expected)    # e.g. clr: exactly NZVC
                key = (0 if i < pos else 1, 0 if exact else 1, abs((i + 0.5) - pos))
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
            fits = f in m.flags_expected and (f not in m.flags_got or (m.flags_got[f] == X.FALSE and b != X.FALSE)
                                              or (b[0] not in ("true", "false") and m.flags_got[f][0] not in ("true", "false")))
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
    # a late C/V/X (recomputed after a branch region, or scheduled after later moves) for the
    # nearest earlier store that sets it and whose operands it mentions; an identical
    # recomputation of a flag the store already has adds nothing
    kept = []
    for pos, f, b, addr in unclaimed:
        if f in "CVX" and b[0] not in ("true", "false", "flag"):
            for i in range(pos - 1, max(-1, pos - 9), -1):
                m = prim[i]
                if m.mnem in ("bra", "jmp", "rts", "jsr") or m.is_label or getattr(m, "target", None) is not None:
                    break
                if m.raw is not None and m.raw[0] == "store" and f in m.flags_expected \
                        and T.flag_val_matches(f, b, m) >= 2:
                    # mentions this store's operands (their old versions): its own carry,
                    # attached if missing, else recomputed (the result named by its alias)
                    m.flags_got.setdefault(f, b)
                    break
            else:
                kept.append((pos, f, b, addr))
                continue
            continue
        kept.append((pos, f, b, addr))
    unclaimed = kept
    # flags-only groups
    extra: dict[int, list[M68]] = {}
    group = []
    gpos = None
    def flush(group, gpos):
        group = [(f, X.simp(b), a) for f, b, a in group]
        kept_ = [g for g in group if g[1] != ("flag", g[0])]     # `C=C`: unchanged, no-op
        group = kept_ or group
        if all(b[0] in ("true", "false", "flag") for _, b, _ in group):
            # leftover constant clears / re-assignments of entry flags: harmless
            txt = ", ".join(f"{f}={X.fmtb(b)}" for f, b, _ in group)
            return [M68("", "", [], 1.0, group[0][2], note=f"(flags {txt})", is_comment=True)]
        got = T.recognise_flags_only(group, amap)
        rest = [g for g in group if g[1][0] not in ("true", "false")]
        if got and not got[0].mnem and rest and len(rest) < len(group):
            # constants re-stating a previous move's flags, then a btst/tst's own flag
            alt = T.recognise_flags_only(rest, amap)
            if alt and alt[0].mnem:
                alt[0].conf *= 0.9
                return alt
        return got

    for pos, f, b, addr in unclaimed + [(None, None, None, None)]:
        if group and (pos != gpos or f == "?" or any(f == g[0] and not (g[1] == X.FALSE and b != X.FALSE) for g in group)):
            extra.setdefault(gpos, []).extend(flush(group, gpos))
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
    _add_or_sub(out)
    _finish(out)
    _peepholes(out)
    _branch_chains(out)
    _tail_expand(out)
    out = expand_jumptables(out)
    r = Result(ex.start, ex.end, out, effects, calls, ex.labels)
    return r


def _jumptable(e) -> M68:
    """mem[base + idx*4] with idx = shl(dN,k): the ROM's idiom is
       add.w dN,dN ; move.w T(pc,dN.w),dN ; jmp T(pc,dN.w) ; T: dc.w L-T,...
    (the translator's table holds 32-bit addresses, the 68k one 16-bit offsets)."""
    addr_e = e[1]
    targets = e[2]
    a = X.strip(addr_e) if addr_e[0] == "mem" else addr_e
    if a[0] == "mem":
        a = a[1]
    idx = None
    if a[0] == "add":
        for p in (a[1], a[2]):
            if p[0] != "c":
                idx = p
    shift = 0
    reg = None
    if idx is not None:
        q = X.strip(idx)
        if q[0] == "shl" and q[2][0] == "c":
            shift = q[2][1]
            q = X.strip(q[1])
        if q[0] == "r68":
            reg = q[1]
    if reg is None:
        m = M68("", "", [], 0.0, e[-1], note=f"jump table on {X.fmt(addr_e)}: " + ", ".join(f"L_{t:x}" for t in targets))
        return m
    m = M68("jmptbl", "", [reg], 0.9, e[-1])
    m.table = list(targets)
    m.pre_double = (shift == 2)      # register holds the entry index: the 68k doubled it first
    m.target = targets[0]
    return m


def expand_jumptables(insns: list[M68]) -> list[M68]:
    """Replace jmptbl pseudo-instructions by the 68k idiom + an inline dc.w table."""
    out = []
    for m in insns:
        if m.mnem != "jmptbl":
            out.append(m)
            continue
        reg = m.ops[0]
        t = f"T_{m.addr:x}"
        if m.pre_double:
            out.append(M68("add", "w", [reg, reg], m.conf, m.addr))
        out.append(M68("move", "w", [f"{t}(pc,{reg}.w)", reg], m.conf, m.addr))
        out.append(M68("jmp", "", [f"{t}(pc,{reg}.w)"], m.conf, m.addr))
        lab = M68("", "", [], 1.0, m.addr, is_label=True)
        lab.label_text = t
        out.append(lab)
        for i in range(0, len(m.table), 8):
            d = M68("dc", "w", [f"L_{x:x}-{t}" for x in m.table[i:i + 8]], 1.0, m.addr)
            out.append(d)
    return out


def _branch(e, amap) -> M68:
    cond, tgt, addr = e[1], e[2], e[-1]
    snap = e[3] if len(e) > 4 else None
    cc = T.cond_code(cond)
    if (cc is None or cc in ("t", "f")) and snap is not None:
        cc2 = T.semantic_cc(cond, snap)
        if cc2 is not None:
            cc = cc2
    pre = None
    if not cc:
        dt = T.direct_test(cond, amap, addr)
        if dt is not None:
            pre, cc = dt
    m = M68("b" + cc, "", [f"L_{tgt:x}"], 1.0, addr) if cc else M68("", "", [], 0.0, addr, note=f"if {X.fmtb(cond)} goto L_{tgt:x}")
    m.pre = pre
    m.target = tgt
    m.cond = cond
    return m


def _finish(out: list[M68]) -> None:
    """Confidence from flag coverage; bset/bclr decisions."""
    for m in out:
        if not m.mnem or m.is_label:
            continue
        if m.conf == 0.0 and any("[" in o or "?" in o for o in m.ops):
            # an operand we could not express (unknown base register): not an instruction
            m.note = f"{m.mnem}.{m.size} {','.join(m.ops)}" if m.size else f"{m.mnem} {','.join(m.ops)}"
            m.mnem, m.ops = "", []
            continue
        exp = m.flags_expected
        got = m.flags_got
        alt_bit = getattr(m, "alt_bit", None)
        if alt_bit is not None:
            bit, bmn = alt_bit
            nonconst = {f for f, v in got.items() if v not in (X.TRUE, X.FALSE)}
            if nonconst == {"Z"} and got["Z"][0] in ("bnot", "bit", "eq0", "ne0"):
                m.mnem, m.ops, m.size = bmn, [f"#{bit}", m.ops[1]], ""
                m.flags_expected, m.note = "Z", ""
                m.flags_got = {"Z": got["Z"]}
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


# The translator implements dbra as `subq.w #1,dN ; bpl` (loop while dN >= 0), which differs
# from dbra when the count starts negative. Folding restores the arcade instruction (the
# port wants that); the PS2 check turns it off.
DBRA_FOLD = True

# movem.w d(aN),list sign-extends each word into the whole register; the translator only
# writes the low words.  Folding the word loads restores the arcade instruction (the port
# wants that); the PS2 check (ps2check) turns it off to model what the PS2 actually does.
MOVEM_W_LOADS = True


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
        for store in (True, False) if MOVEM_W_LOADS or m.size == "l" else (True,):
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


def _eval(e, env):
    """Value (int) or truth (bool) of an expression; leaves are looked up in env."""
    if e in env:
        return env[e]
    k = e[0]
    M = 0xFFFFFFFF
    if k == "c":
        return e[1] & M
    if k in ("lo16", "lo8"):
        return _eval(e[1], env) & (0xFFFF if k == "lo16" else 0xFF)
    if k in ("sext16", "sext8"):
        v, b = _eval(e[1], env), 16 if k == "sext16" else 8
        v &= (1 << b) - 1
        return (v - (1 << b) if v >> (b - 1) else v) & M
    if k in ("add", "sub", "and", "or", "xor", "shl", "shr"):
        a, b = _eval(e[1], env), _eval(e[2], env)
        return {"add": a + b, "sub": a - b, "and": a & b, "or": a | b, "xor": a ^ b,
                "shl": a << (b & 31), "shr": a >> (b & 31)}[k] & M
    if k == "true":
        return True
    if k == "false":
        return False
    if k == "bit":
        return bool(_eval(e[1], env) >> e[2] & 1)
    if k == "eq0":
        return _eval(e[1], env) == 0
    if k == "ne0":
        return _eval(e[1], env) != 0
    if k == "bnot":
        return not _eval(e[1], env)
    if k == "band":
        return _eval(e[1], env) and _eval(e[2], env)
    if k == "bor":
        return _eval(e[1], env) or _eval(e[2], env)
    if k in ("bite", "ite"):
        return _eval(e[2], env) if _eval(e[1], env) else _eval(e[3], env)
    raise KeyError(e)


SAMPLES = (0, 1, 0x7F, 0x80, 0xD0, 0xFF, 0x100, 0x7FFF, 0x8000, 0x8001, 0xFFFF, 0x10000,
           0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x12345678)


def _add_or_sub(out: list[M68]) -> None:
    """`addi #-k` and `subi #k` give the same result but opposite carries: keep whichever the
    translator's C (or X) expression computes."""
    for m in out:
        if m.mnem not in ("addi", "add") or not m.ops or not m.ops[0].startswith("#") or m.result is None:
            continue
        f = "C" if "C" in m.flags_got else "X" if "X" in m.flags_got else None
        if f is None or m.flags_got[f][0] in ("true", "false"):
            continue
        r = X.strip(m.result)
        if r[0] != "add" or r[2][0] != "c":
            continue
        bits = {"b": 8, "w": 16, "l": 32}.get(m.size, 32)
        mask = (1 << bits) - 1
        k = r[2][1] & mask
        if not k >> (bits - 1):
            continue                    # only a negative constant can be a subtraction
        x = r[1]
        try:
            agree_sub = agree_add = 0
            for v in SAMPLES:
                got = _eval(m.flags_got[f], {x: v & 0xFFFFFFFF, X.strip(x): v & 0xFFFFFFFF})
                agree_add += got == ((v & mask) + k > mask)
                agree_sub += got == ((v & mask) < ((-k) & mask))
        except (KeyError, IndexError, TypeError):
            continue
        if agree_sub == len(SAMPLES) and agree_add < len(SAMPLES):
            m.mnem = "subi"
            m.ops = [f"#${(-k) & mask:x}"] + m.ops[1:]


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


def _exg(out: list[M68]) -> None:
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        if (a.mnem in ("move", "movea") and b.mnem in ("move", "movea") and a.size in ("l", "") and b.size in ("l", "")
                and not a.flags_got and not b.flags_got and len(a.ops) == 2 and len(b.ops) == 2
                and a.ops[0] in REGNO and a.ops[1] in REGNO and a.ops[0] == b.ops[1] and a.ops[1] == b.ops[0]
                and a.ops[0] != a.ops[1]):
            out[i:i + 2] = [M68("exg", "", [a.ops[1], a.ops[0]], min(a.conf, b.conf), a.addr)]
            continue
        i += 1


def _is_sign_test(cond, reg):
    """(True, negated) if cond tests the sign of 16-bit register `reg`."""
    neg = False
    if cond[0] == "bnot":
        neg, cond = True, cond[1]
    if cond[0] == "bit" and cond[2] in (15, 31) and X.strip(cond[1])[0] == "r68" and X.strip(cond[1])[1] == reg:
        return True, neg
    return False, neg


def _dbra(out: list[M68]) -> None:
    """dbra dN,LOOP: `subq.w #1,dN` (no flags) then a sign test of dN: either
    `bmi EXIT ; bra LOOP` or `bpl LOOP`."""
    if not DBRA_FOLD:
        return
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        if getattr(b, "is_pre_test", False) and i + 2 < len(out) and a.mnem == "subq" \
                and b.ops == [a.ops[1]] and getattr(out[i + 2], "cond", None) is not None:
            del out[i + 1]              # the sign test the branch-on-value added: dbra needs none
            continue
        if a.mnem == "subq" and a.size == "w" and a.ops[0] == "#1" and a.ops[1] in REGNO and a.ops[1][0] == "d" \
                and not a.flags_got and getattr(b, "cond", None) is not None and not b.is_label:
            ok, neg = _is_sign_test(b.cond, a.ops[1])
            if ok and not neg and i + 2 < len(out) and out[i + 2].mnem == "bra":
                loop, exit_ = out[i + 2].target, b.target
                d = M68("dbra", "", [a.ops[1], f"L_{loop:x}"], a.conf, a.addr)
                d.target = loop
                br = M68("bra", "", [f"L_{exit_:x}"], 1.0, b.addr)
                br.target = exit_
                out[i:i + 3] = [d, br]
                continue
            if ok and neg:
                d = M68("dbra", "", [a.ops[1], f"L_{b.target:x}"], a.conf, a.addr)
                d.target = b.target
                out[i:i + 2] = [d]
                continue
        i += 1


def _tas(out: list[M68]) -> None:
    """tas <ea>: the translator tests the byte (N, Z of the old value, V=C=0), then sets
    bit 7 without touching the flags; rendered as `tst.b X ; ori.b #-$80,X`."""
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        if a.mnem == "tst" and a.size == "b" and b.mnem == "ori" and b.size == "b" \
                and b.ops == ["#-$80", a.ops[0]] and set(b.flags_got) <= {"N"}:
            out[i:i + 2] = [M68("tas", "", [a.ops[0]], min(a.conf, b.conf), b.addr)]
        i += 1


def _peepholes(out: list[M68]) -> None:
    """(aN)+ / -(aN) folding, movem, exg, dbra, tas."""
    _movem(out)
    _movem_plain(out)
    _exg(out)
    _dbra(out)
    _tas(out)
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
