"""Turn recovered Arrange routines (work/ps2/detrans/arrange_*.s) into PGM 68k source.

A recovered routine prints RAM operands as Arrange addresses and calls as `F_<ps2 address>`.
Porting it means:
  * RAM operands -> PGM addresses: the region map learned by ramxlate.py, plus overrides for
    areas the port placed itself (bullet pool, walker variables, sprite lists);
  * calls -> the 68k routine the PS2 function translates (address map), another ported routine
    (emitted in the same unit), or a known 68k equivalent of a native PS2 service;
  * nothing unknown left (`; ???` lines, unmapped calls or addresses) -- otherwise the routine is
    reported with the reasons and not ported.
The output is plain asm68k source (no original bytes), assembled into free program ROM by the
build like asm/m68k/*.s.
"""
from __future__ import annotations

import collections
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ramxlate import RamXlate

OUT = Path("work") / "ps2" / "detrans"
ROM_HI = 0x200000
HW_LO = 0x900000            # video/palette/IO/sound/cart hardware: kept as is


# Arrange-only RAM placed by the port (layout stage), keyed by the de-translator's printed
# (extrapolated) Arrange address. Bullet variables: Arrange block 0x81D4C6.. = arcade 0x81B552..
# except the walker group count/delta, which the port keeps in their own words.
ARRANGE_OVERRIDES = {
    0x81D4C6: 0x81B552, 0x81D4C8: 0x81B554, 0x81D4CC: 0x81B558, 0x81D4CD: 0x81B559,
    0x81D4CE: 0x818352, 0x81D4D6: 0x818354, 0x81D4D7: 0x818355, 0x81D4D8: 0x81B564,
    0x81E496: 0xD00000, 0x81E498: 0xD00002,
}


@dataclass
class Ported:
    fn: int
    text: str = ""
    callees: set = field(default_factory=set)      # PS2 functions that must be ported too
    problems: list = field(default_factory=list)


_SAVE = re.compile(r"^\s+move\.l\s+([da][0-7]),(-(\d+))?\(a7\)\s*(;.*)?$")
_ADJ = re.compile(r"^\s+lea(\.l)?\s+-(\d+)\(a7\),a7\s*(;.*)?$")


def fix_stack_saves(lines: list[str]) -> list[str]:
    """`move.l rN,-k(a7)` ... `lea -K(a7),a7` stores below the stack pointer before moving it,
    which an interrupt would clobber. Move the adjustment first and rebase the stores
    (a bare `(a7)` store in such a run is the lowest slot, -K)."""
    out, i = [], 0
    while i < len(lines):
        j = i
        while j < len(lines) and _SAVE.match(lines[j]):
            j += 1
        m = _ADJ.match(lines[j]) if j < len(lines) and j > i else None
        if m:
            k_total = int(m.group(2))
            out.append(f"\tlea\t-{k_total}(a7),a7")
            for line in lines[i:j]:
                mm = _SAVE.match(line)
                off = -int(mm.group(3)) if mm.group(2) else -k_total
                out.append(f"\tmove.l\t{mm.group(1)},{off + k_total}(a7)")
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    return out


class NewVars:
    """Fresh storage for Arrange-only RAM (extrapolated addresses with no arcade counterpart).
    Addresses within `gap` bytes form a cluster that keeps its internal layout."""

    def __init__(self, base: int, size: int, gap: int = 0x40):
        self.base, self.size, self.gap = base, size, gap
        self.addrs: set[int] = set()
        self.map: dict[int, int] = {}

    def want(self, a: int) -> None:
        self.addrs.add(a)

    def assign(self) -> None:
        nxt = self.base
        cl: list[int] = []
        for a in sorted(self.addrs) + [None]:
            if cl and (a is None or a - cl[-1] > self.gap):
                lo = cl[0] & ~1
                for x in cl:
                    self.map[x] = nxt + (x - lo)
                nxt += ((cl[-1] - lo + 4) + 3) & ~3
                cl = []
            if a is not None:
                cl.append(a)
        assert nxt <= self.base + self.size, "Arrange-only variables overflow their area"
        self.end = nxt


class Porter:
    def __init__(self, ram: RamXlate, func_map: dict[int, int], native: dict[int, int] | None = None,
                 src_dir: Path = OUT):
        self.ram = ram
        self.func_map = func_map            # PS2 function -> 68k routine
        self.native = native or {}          # PS2 native service -> 68k routine
        self.src_dir = src_dir
        self.newvars: NewVars | None = None

    @classmethod
    def load(cls, overrides: dict[int, int] | None = None) -> "Porter":
        if overrides is None:
            overrides = ARRANGE_OVERRIDES
        amap = json.loads((OUT / "addrmap_arrange.json").read_text())
        funcs = {int(k, 16): int(v, 16) for k, v in amap["funcs"].items()}
        summ = OUT / "summary.json"
        if summ.exists():           # unchanged Arrange functions = their 68k original
            for k, v in json.loads(summ.read_text())["funcs"].items():
                if v.get("m68k") is not None:
                    funcs.setdefault(int(k, 16), int(v["m68k"]))
        from .helpers import CALLEES
        for k, v in CALLEES.items():        # what Arika's helpers call, as 68k routines
            funcs.setdefault(k, v)
        native = {}
        nsm = OUT / "native_service_map.json"
        if nsm.exists():
            for k, v in json.loads(nsm.read_text()).items():
                if v.get("m68k_helper"):
                    native[int(k, 16)] = int(v["m68k_helper"], 16)
        return cls(RamXlate.load(overrides=overrides), funcs, native)

    def source(self, fn: int) -> str | None:
        for name in (f"arrange_{fn:x}.s", f"elf_{fn:x}.s"):
            p = self.src_dir / name
            if p.exists():
                return p.read_text()
        return None

    def _addr(self, v: int, problems: list, extrapolated: bool) -> int:
        if v < ROM_HI:
            return v
        if not extrapolated:
            return v            # exact/interpolated arcade sample: same variable in both builds
        t = self.ram(v)
        if t is not None:
            return t
        if self.newvars is not None and 0x800000 <= v < 0x830000:
            if v in self.newvars.map:
                return self.newvars.map[v]
            self.newvars.want(v)
            problems.append(f"new variable {v:#x} not yet assigned")
            return v
        if 0x800000 <= v < 0x820000:
            problems.append(f"RAM {v:#x} not mapped")
        elif v >= HW_LO and v < 0xE00000:
            return v
        else:
            problems.append(f"address {v:#x} not mapped")
        return v

    def port(self, fn: int, local: set[int] | None = None) -> Ported:
        """Port one function. `local` = PS2 functions emitted in the same unit (called by label)."""
        local = local or set()
        res = Ported(fn)
        src = self.source(fn)
        if src is None:
            res.problems.append("no recovered source")
            return res
        out = []
        for line in src.splitlines():
            code = line.split(";", 1)[0].rstrip()
            note = line.split(";", 1)[1] if ";" in line else ""
            if not code.strip():
                if "???" in note:
                    res.problems.append("unknown: " + note.strip()[:80])
                continue
            m = re.match(r"^\s+(jsr|jmp|bsr|bra)\s+F_([0-9a-f]+)\s*$", code)
            if m:
                tgt = int(m.group(2), 16)
                if "mips-args" in note and tgt not in self.native:
                    # the callee reads values passed in MIPS registers (a code pointer for a
                    # helper, a service argument): the 68k call does not carry them
                    res.problems.append(f"call {tgt:#x} passes {note.split('mips-args', 1)[1].strip()} natively")
                op = "jsr" if m.group(1) in ("jsr", "bsr") else "jmp"
                if tgt in local or tgt == fn:
                    out.append(f"\t{op}\tF_{tgt:x}")
                    res.callees.add(tgt)
                elif tgt in self.func_map:
                    out.append(f"\t{op}\t${self.func_map[tgt]:x}.l")
                elif tgt in self.native:
                    out.append(f"\t{op}\t${self.native[tgt]:x}.l")
                else:
                    out.append(f"\t{op}\tF_{tgt:x}")
                    res.callees.add(tgt)
                continue

            if "ARG_A" in code:
                res.problems.append("uses a MIPS argument the routine is entered with: " + code.strip()[:60])
            if "RET_V0" in code:
                res.problems.append("uses a native routine's return value ($v0): " + code.strip()[:60])
            extrap = "conf 0.60" in note or "conf 0.30" in note

            def sub(mm):
                v = int(mm.group(2), 16)
                return f"{mm.group(1)}${self._addr(v, res.problems, extrap):x}{mm.group(3)}"
            code = re.sub(r"(#?)\$([0-9a-f]{6,8})(\.l|\b)", sub, code)
            out.append(code)
        res.text = "\n".join(fix_stack_saves(out)) + "\n"
        return res

    def port_unit(self, roots: list[int], max_funcs: int = 64) -> tuple[str, dict[int, Ported]]:
        """Port roots plus every unmapped PS2 callee they reach (one asm unit)."""
        todo, done = list(roots), {}
        while todo and len(done) < max_funcs:
            f = todo.pop()
            if f in done:
                continue
            done[f] = None
            r = self.port(f, local=set(done) | set(todo))
            done[f] = r
            for c in r.callees:
                if c not in done:
                    todo.append(c)
        units = set(done)
        # re-port with the final local set so every intra-unit call uses its label
        res = {f: self.port(f, local=units) for f in units}
        return "\n".join(res[f].text for f in sorted(res)), res


if __name__ == "__main__" and "--plan" not in __import__("sys").argv:
    import collections
    import sys
    pt = Porter.load()
    summ = json.loads((OUT / "summary.json").read_text())["funcs"]
    reasons = collections.Counter()
    ok = []
    for k, v in summ.items():
        if v.get("kind") not in ("near", "new"):
            continue
        fn = int(k, 16)
        r = pt.port(fn)
        if r.problems:
            reasons[r.problems[0].split(" ")[0]] += 1
        else:
            ok.append((fn, v["kind"], len(r.callees)))
    print(f"translatable on their own: {len(ok)} "
          f"(near {sum(1 for x in ok if x[1] == 'near')}, new {sum(1 for x in ok if x[1] == 'new')}); "
          f"first blocking reason: {dict(reasons)}")
    if "-v" in sys.argv:
        for fn, kind, nc in ok:
            print(f"  {fn:#x} {kind} callees-to-port {nc}")


def plan(newvar_base: int = 0xE09800, newvar_size: int = 0x6800, max_funcs: int = 64):
    """Port every changed Arrange routine that has a 68k original, with its callee closure.
    Returns (units, porter): units = {68k original: (asm text, ported dict)} for the clean ones,
    and the reasons for the rest."""
    pt = Porter.load()
    pt.newvars = NewVars(newvar_base, newvar_size)
    rep = json.loads((OUT / "port_report.json").read_text())
    roots = {}
    for r in rep:
        if r["m68k"] and r["structural"]:
            roots.setdefault(r["arrange_fn"], r["m68k"])
    # pass 1: collect Arrange-only variables over every candidate unit
    for fn in roots:
        pt.port_unit([fn], max_funcs)
    pt.newvars.assign()
    units, failed = {}, {}
    for fn, orig in roots.items():
        text, res = pt.port_unit([fn], max_funcs)
        probs = [f"{f:#x}: {p}" for f, r in res.items() for p in r.problems]
        if probs or len(res) >= max_funcs:
            failed[fn] = probs[:3] or ["closure too large"]
        else:
            units[orig] = (fn, text, res)
    return units, failed, pt


def _main_plan():
    import collections
    units, failed, pt = plan()
    print(f"portable units: {len(units)}, failed: {len(failed)}, Arrange-only variables: "
          f"{len(pt.newvars.map)} ({pt.newvars.end - pt.newvars.base:#x} bytes)")
    c = collections.Counter(v[0].split(": ", 1)[1].split(" ")[0] for v in failed.values())
    print("first failure kinds:", dict(c))

if __name__ == "__main__" and "--plan" in __import__("sys").argv:
    _main_plan()


_ENTRY_CACHE: dict = {}


def _code_refs():
    """(sorted branch/call targets with their sources, set of code-pointer constants) of the ROM."""
    if "refs" in _ENTRY_CACHE:
        return _ENTRY_CACHE["refs"]
    import struct
    from ..pgm import rom as rom_mod
    from .port import WORK
    tg = []
    for line in (WORK / "pgm" / "dis" / "p_rom.lst").read_text().splitlines():
        m = re.match(r"^([0-9a-f]{6})\s+\S+\s+(b\w+|jsr|jmp|db\w+)\s+.*?\$([0-9a-f]+)\s*$", line)
        if m:
            tg.append((int(m.group(3), 16), int(m.group(1), 16)))
    tg.sort()
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    ptrs = set()
    for i in range(0, len(p) - 3, 2):
        v = struct.unpack_from(">I", p, i)[0]
        if 0x100000 <= v < 0x200000:
            ptrs.add(v)
    _ENTRY_CACHE["refs"] = (tg, [t for t, _ in tg], ptrs)
    return _ENTRY_CACHE["refs"]


def _only_immediates_differ(rec, k68) -> bool:
    """Do an aligned arcade-twin instruction and its 68k original differ only in immediate
    constants?  (A differing address or displacement means the address/record mapping
    disagrees there, and the Arrange text would be mapped the same wrong way.)"""
    from .verify import recovered_canon
    a, b = recovered_canon(rec), k68.canon
    if a is None:
        return False
    ma, _, oa = a.text.partition(" ")
    mb, _, ob = b.text.partition(" ")
    if ma != mb:
        return False
    xa, xb = _split_ops(oa), _split_ops(ob)
    return len(xa) == len(xb) and all(x == y or x.startswith("#") and y.startswith("#") for x, y in zip(xa, xb))


def _split_ops(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    return out + ([cur.strip()] if cur.strip() else [])


class _FixedMap:
    """An address map with some PS2 addresses pinned to their 68k address (twin evidence)."""

    def __init__(self, base, fixes: dict[int, int]):
        self._base, self._fixes = base, fixes

    def map_const(self, v: int):
        if v in self._fixes:
            return self._fixes[v], "twin", 1.0
        return self._base.map_const(v)

    def __getattr__(self, name):
        return getattr(self._base, name)


VERIFIED = OUT / "ps2_verified.json"     # tools/ps2check_dumps.py --verified


def ps2_verified() -> dict[str, set[int]]:
    """Arrange routines (per overlay) that matched the PS2 on every captured state."""
    if not VERIFIED.exists():
        return {}
    return {ov: {int(x, 16) for x in fns} for ov, fns in json.loads(VERIFIED.read_text())["verified"].items()}


def _entries_into(lo: int, hi: int) -> bool:
    """Is any address strictly inside (lo, hi) reached from outside [lo, hi) or by a pointer?"""
    import bisect as _b
    tg, keys, ptrs = _code_refs()
    i = _b.bisect_right(keys, lo)
    while i < len(tg) and tg[i][0] < hi:
        if not (lo <= tg[i][1] < hi):
            return True
        i += 1
    return any(lo < v < hi for v in ptrs) if hi - lo < 0x4000 else True


class OverlaySource:
    """Recovered Arrange routines of one overlay pair, de-translated on demand, with their
    68k originals (anchors) for the changed ones."""

    def __init__(self, a_name: str, r_name: str, index=None, main_funcs: dict | None = None,
                 arrange_records: bool = False):
        self.arrange_records = arrange_records
        from ..diff import mips_funcs
        from ..pgm import rom as rom_mod
        from ..ps2.overlay import Overlay
        from .port import (STAGE_PAIRS, WORK, _arrange_map, _fallback_pairs, learn_overlay_map)
        from .verify import Index, LearnedMap, compare_function, load_listing
        from . import core
        from .apply import is_translated, _reader
        self.core, self._reader, self.is_translated = core, _reader, is_translated
        disc = WORK / "ps2" / "disc" / "A0A0"
        self.A, self.R = Overlay(disc / a_name), Overlay(disc / r_name)
        self.index = index or Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
        lm = LearnedMap.load(OUT / "addrmap_arcade.json")
        self.alm = learn_overlay_map(self.A, self.index, lm) if (a_name, r_name) in STAGE_PAIRS else lm
        fa = mips_funcs.split_functions(self.A.image, self.A.image_base, self.A.header.text_end)
        fb = mips_funcs.split_functions(self.R.image, self.R.image_base, self.R.header.text_end)
        self.fb = {f.start: f for f in fb}
        diff = mips_funcs.match(fa, fb)
        p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
        self.amap = _arrange_map(p, self.A, self.R, diff, self.alm)
        if main_funcs:
            for k, v in main_funcs.items():
                self.amap.funcs.setdefault(k, v)
        routine_starts = {int(k, 16) for k in json.loads((WORK / "pgm" / "routines.json").read_text())}
        self.roots: dict[int, int] = {}          # arrange fn -> 68k original (changed routines)
        self.call_votes: dict = collections.defaultdict(collections.Counter)
        self.trust: dict[int, bool] = {}
        self.trust_info: dict[int, tuple] = {}
        self.unshifted: dict[int, int] = {}
        self.needs_layout: dict[int, str] = {}
        self.layout_only: dict[int, bool] = {}
        self.region: dict[int, tuple] = {}       # arrange fn -> (68k start, end, ends with rts/jmp/bra)
        self._text: dict[int, str] = {}
        self.twin: dict[int, tuple] = {}      # arrange fn -> (arcade twin, aligned 68k start)
        self.trust_why: dict[int, str | None] = {}
        self.addr_fix: dict[int, dict[int, int]] = {}   # arrange fn -> {PS2 address: 68k address}
        self.verified = ps2_verified().get(r_name, set())
        from . import helpers
        # the stage overlays call helpers in their mode's main overlay: read through both
        main_a, main_r = helpers.main_overlay("A0A8"), helpers.main_overlay("A0A9")
        self._rd_a, self._rd_r = helpers.reader(self.A, main_a), helpers.reader(self.R, main_r)
        self._code_a = lambda a, n: (self.A if self.A.contains(a) else main_a).read(a, n)
        self._code_r = lambda a, n: (self.R if self.R.contains(a) else main_r).read(a, n)
        for m in diff.matches:
            if m.kind != "near":
                continue
            ca = self.A.read(m.a.start, m.a.size)
            if not is_translated(ca):
                continue
            try:
                ra = core.detranslate(ca, m.a.start, self.alm, read_word=self._rd_a)
            except Exception:  # noqa: BLE001
                continue
            # the arcade mode calls Arika's helpers where the 68k has the fragment inline
            ra_inl = helpers.inline_result(ra, self._code_a, self._rd_a, self.alm)
            rep = compare_function(self.index, ra_inl)
            rep0_anchor = rep.anchor
            if rep.anchor is None:
                _fallback_pairs(self.index, ra, rep, routine_starts)
            if rep.anchor is not None:
                self.roots[m.b.start] = rep.anchor
                # trusted: the arcade twin de-translates exactly to a self-contained 68k region
                pos = self.index.pos_of(rep.anchor)
                end_pos = pos + rep.n_m68
                ks = self.index.ks
                region_end = ks[end_pos].addr if end_pos < len(ks) else rep.anchor
                start = rep.anchor
                # the de-translator writes movem saves/restores as plain moves, so alignment
                # starts after `movem.l regs,-(a7)` and stops before `movem.l (a7)+,regs ; rts`
                ported_text = self.text(m.b.start) or ""
                saves = bool(re.search(r"^\s+move\.l\s+[ad][0-7],-\d+\(a7\)", ported_text, re.M))
                if saves and pos > 0 and ks[pos - 1].canon.mnem == "movem" and "-(a7)" in ks[pos - 1].canon.text:
                    start = ks[pos - 1].addr
                    pos -= 1
                if end_pos + 1 < len(ks) and ks[end_pos].canon.mnem == "movem" and "(a7)+" in ks[end_pos].canon.text \
                        and ks[end_pos + 1].canon.mnem == "rts":
                    end_pos += 2
                    region_end = ks[end_pos].addr if end_pos < len(ks) else region_end
                prev_term = pos > 0 and ks[pos - 1].canon.mnem in ("rts", "jmp", "bra", "rte")
                rep.anchor = start
                self.roots[m.b.start] = start
                last_term = end_pos > pos and ks[end_pos - 1].canon.mnem in ("rts", "jmp", "bra", "rte")
                entered = _entries_into(rep.anchor, region_end)
                self.twin[m.b.start] = (m.a.start, rep0_anchor)
                self.trust_info[m.b.start] = (rep.anchor, region_end, rep.unknown, rep.exact, rep.n_m68,
                                              prev_term or rep.anchor in routine_starts, last_term, entered)
                exact = (rep.unknown == 0 and rep.exact == rep.n_m68 and rep.n_m68 > 0
                         and region_end - rep.anchor >= 6 and not entered)
                # PS2-verified: the Arrange routine matched the PS2 on every captured state
                # (tools/ps2check_dumps.py --verified), the twin aligns with no inserted or
                # missing instruction (operands may differ: they are replaced anyway), and the
                # learned call map confirms the anchor (no near-identical sibling routine)
                structural = (rep.unknown == 0 and rep.n_m68 > 0 and region_end - rep.anchor >= 6
                              and not entered and not any(a is None or b is None for a, b in rep.mismatches))
                if structural and not last_term:
                    # the 68k falls through at the region end: the twin must end there as well
                    # (a twin that returns there was split off a longer 68k routine: the port
                    # would return early and skip the rest)
                    recs = [i for i in ra_inl.insns if not i.is_label and i.mnem]
                    paired = {id(a) for a, _ in rep.pairs}
                    structural = bool(recs) and id(recs[-1]) in paired
                # (an anchor from the alignment itself, not a fallback pairing that leaves no
                # instruction pairs to check; a call map that knows the twin)
                mapped = self.alm.funcs.get(m.a.start)
                confirmed = rep0_anchor is not None and mapped is not None and mapped in (rep.anchor, rep0_anchor)
                verified = m.b.start in self.verified and structural and confirmed and not exact
                if verified:
                    # the twin differs from the 68k only in immediates and absolute addresses the
                    # learned map got wrong: port the Arrange routine with those addresses fixed
                    fixes = self._twin_address_fixes(m.b.start, m.a.start, ca, ra_inl, rep)
                    verified = fixes is not None
                    if verified:
                        self.addr_fix[m.b.start] = fixes
                        self._text.pop(m.b.start, None)
                self.trust[m.b.start] = exact or verified
                self.trust_why[m.b.start] = "twin exact" if exact else "PS2-verified" if verified else None
                self.region[m.b.start] = (rep.anchor, region_end, last_term)
            # learn call targets: the n-th call of the Arrange routine <-> the n-th call of its
            # arcade twin (aligned), where only the arcade side is resolved
            try:
                bmap = _FixedMap(self.amap, self.addr_fix[m.b.start]) if self.addr_fix.get(m.b.start) else self.amap
                rb = core.detranslate(self.R.read(m.b.start, m.b.size), m.b.start, bmap, read_word=self._rd_r)
            except Exception:  # noqa: BLE001
                continue
            self._unshift(m.b.start, ra, rb)
            ca_calls = [i.text().split() for i in ra.insns if i.mnem in ("jsr", "jmp", "bsr")]
            cb_calls = [i.text().split() for i in rb.insns if i.mnem in ("jsr", "jmp", "bsr")]
            ka = ["F" if t[-1].startswith("F_") else ("R" if t[-1].startswith("$") else "?") for t in ca_calls]
            kb = ["F" if t[-1].startswith("F_") else ("R" if t[-1].startswith("$") else "?") for t in cb_calls]
            sm = difflib.SequenceMatcher(None, [t[-1] if t[-1].startswith("$") else "x" for t in ca_calls],
                                         [t[-1] if t[-1].startswith("$") else "x" for t in cb_calls], autojunk=False)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op in ("equal", "replace") and i2 - i1 == j2 - j1:
                    for x, y in zip(ca_calls[i1:i2], cb_calls[j1:j2]):
                        if x[-1].startswith("$") and y[-1].startswith("F_"):
                            self.call_votes[int(y[-1][2:], 16)][int(x[-1][1:].split(".")[0], 16)] += 1

        # an anchor claimed by several Arrange routines is ambiguous (near-identical sibling
        # routines, e.g. per player): only an exact twin can be trusted there
        claims = collections.Counter(self.roots.values())
        for fn, why in self.trust_why.items():
            if why == "PS2-verified" and claims[self.roots[fn]] > 1:
                self.trust[fn], self.trust_why[fn] = False, None
        self.funcs = dict(self.amap.funcs)
        for b, c in self.call_votes.items():
            a, v = c.most_common(1)[0]
            if b not in self.funcs and v >= 0.75 * sum(c.values()):
                self.funcs[b] = a

    def _unshift(self, fn: int, ra, rb) -> None:
        """Undo the Arrange enemy-record growth (0x24 -> 0x2C bytes) in a changed routine, guided
        by its arcade twin: aligned displacements/immediates that equal the arcade value shifted by
        8 bytes per record take the arcade value. Records whether anything Arrange-only still
        depends on the new layout, and whether any real change is left at all."""
        from .verify import recovered_canon

        def key(c):
            parts = [c.mnem + "." + c.size]
            for op in c.ops:
                if op[0] in ("imm", "abs", "label", "func", "pc"):
                    parts.append(op[0])
                elif op[0] == "d16":
                    parts.append("d16|" + str(op[1]))
                else:
                    parts.append("|".join(str(x) for x in op))
            return " ".join(parts)

        def shifted(v: int) -> int:
            return v + 8 * (v // 0x24) if v >= 0 else -((-v) + 8 * ((-v) // 0x24))
        ia = [(i, recovered_canon(i)) for i in ra.insns if not i.is_label and i.mnem]
        ib = [(i, recovered_canon(i)) for i in rb.insns if not i.is_label and i.mnem]
        sm = difflib.SequenceMatcher(None, [key(c) if c else "?" for _, c in ia],
                                     [key(c) if c else "?" for _, c in ib], autojunk=False)
        aligned_b = set()
        changed = False
        undo = []
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                (xa, ca), (xb, cb) = ia[blk.a + k], ib[blk.b + k]
                aligned_b.add(id(xb))
                if ca is None or cb is None:
                    continue
                for n, (oa, ob) in enumerate(zip(ca.ops, cb.ops)):
                    va = oa[2] if oa[0] == "d16" else (oa[1] if oa[0] == "imm" else None)
                    vb = ob[2] if ob[0] == "d16" else (ob[1] if ob[0] == "imm" else None)
                    if not isinstance(va, int) or not isinstance(vb, int) or va == vb:
                        continue
                    if abs(va) >= 0x24 and vb == shifted(va) and n < len(xb.ops):
                        undo.append((xb, n, xb.ops[n]))
                        old = str(vb) if oa[0] == "d16" else None
                        if oa[0] == "d16":
                            xb.ops[n] = re.sub(rf"^(-?\$?){old}\(", lambda mm: f"{va}(", xb.ops[n]) \
                                if re.match(rf"^-?{old}\(", xb.ops[n]) else xb.ops[n].replace(f"{vb}(", f"{va}(")
                        else:
                            xb.ops[n] = f"#{va}" if xb.ops[n].startswith("#") else xb.ops[n]
                        self.unshifted[fn] = self.unshifted.get(fn, 0) + 1
                c2 = recovered_canon(xb)
                if c2 is None or c2.text != ca.text:
                    changed = True
        extra_b = [(x, c) for x, c in ib if id(x) not in aligned_b]
        if len(extra_b) or len(ia) != len(aligned_b):
            changed = True
        for x, c in extra_b:
            if c is None:
                continue
            for op in c.ops:
                if op[0] == "d16" and str(op[1]).startswith("a") and abs(op[2]) >= 0x24:
                    self.needs_layout[fn] = f"Arrange-only {x.text().strip()}"
        self.layout_only[fn] = not changed
        if self.arrange_records:
            # the build uses the Arrange 0x2C enemy records: keep Arrange's own offsets
            for xb, n, op in undo:
                xb.ops[n] = op
            self.needs_layout.pop(fn, None)
        self._text[fn] = self._inline_helpers(rb.text(header=False))

    def _twin_address_fixes(self, fn: int, tw: int, twin_code: bytes, ra_inl, rep) -> dict | None:
        """{Arrange PS2 address: 68k address} correcting the learned address map, from the
        twin/68k alignment (twin address P <-> 68k Y where they disagree) and the Arrange/twin
        alignment (Arrange address Q <-> P at the same place). None when an operand differs
        in anything but an immediate or an absolute address, or the evidence is inconsistent."""
        import difflib
        from . import helpers
        from .port import _shape_key
        from .templates import AddrMap
        from .verify import recovered_canon
        raw = helpers.inline_result(self.core.detranslate(twin_code, tw, AddrMap(), read_word=self._rd_a),
                                    self._code_a, self._rd_a, AddrMap())
        mi = [i for i in ra_inl.insns if not i.is_label and i.mnem]
        ri = [i for i in raw.insns if not i.is_label and i.mnem]
        if len(mi) != len(ri) or any(a.mnem != b.mnem for a, b in zip(mi, ri)):
            return None
        pos = {id(x): k for k, x in enumerate(mi)}
        p2y: dict[int, int] = {}
        for x, k68 in rep.mismatches:
            cx, craw = recovered_canon(x), recovered_canon(ri[pos[id(x)]])
            if cx is None or craw is None or cx.mnem != k68.canon.mnem or len(cx.ops) != len(k68.canon.ops):
                return None
            for om, orw, ok in zip(cx.ops, craw.ops, k68.canon.ops):
                if om == ok or om[0] == ok[0] == "imm":
                    continue
                if not (om[0] == orw[0] == ok[0] == "abs"):
                    return None
                if p2y.setdefault(orw[1], ok[1]) != ok[1]:
                    return None
        if not p2y:
            return {}
        code = self.R.read(fn, self.fb[fn].size)
        arr = helpers.inline_result(self.core.detranslate(code, fn, AddrMap(), read_word=self._rd_r),
                                    self._code_r, self._rd_r, AddrMap())
        ai = [recovered_canon(i) for i in arr.insns if not i.is_label and i.mnem]
        bi = [recovered_canon(i) for i in ri]
        sm = difflib.SequenceMatcher(None, [_shape_key(c) if c else "?" for c in ai],
                                     [_shape_key(c) if c else "?" for c in bi], autojunk=False)
        fixes: dict[int, int] = {}
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                ca_, cb_ = ai[blk.a + k], bi[blk.b + k]
                for oa, ob in zip(ca_.ops, cb_.ops):
                    if oa[0] == ob[0] == "abs" and ob[1] in p2y:
                        if fixes.setdefault(oa[1], p2y[ob[1]]) != p2y[ob[1]]:
                            return None
        return fixes

    def _inline_helpers(self, text: str) -> str:
        """Arika's native-convention helpers (in the main overlay) back to inline 68k."""
        from . import helpers
        return helpers.inline(text, self._code_r, self._rd_r, self.amap)

    def text(self, fn: int) -> str | None:
        if fn in self._text:
            return self._text[fn]
        f = self.fb.get(fn)
        if f is None:
            return None
        code = self.R.read(f.start, f.size)
        if not self.is_translated(code):
            return None
        amap = _FixedMap(self.amap, self.addr_fix[fn]) if self.addr_fix.get(fn) else self.amap
        try:
            res = self.core.detranslate(code, f.start, amap, read_word=self._rd_r)
        except Exception:  # noqa: BLE001
            return None
        t = self._inline_helpers(res.text(header=False))
        self._text[fn] = t
        return t


def overlay_porter(src: OverlaySource, newvars: NewVars | None = None) -> Porter:
    base = Porter.load()
    pt = Porter(base.ram, {**base.func_map, **src.funcs}, base.native)
    pt.newvars = newvars
    pt.source = lambda fn: src.text(fn) or base.source(fn)
    return pt


def overlay_units(src: "OverlaySource", newvars: NewVars, trusted_only: bool = True) -> tuple[list[dict], dict]:
    """Ported, assemblable units of one overlay pair: [{"orig", "end", "fn", "text"}], failures.
    Call twice with the same NewVars: first to collect Arrange-only variables (before
    newvars.assign()), then to produce the final text."""
    from ..pgm import asm68k
    pt = overlay_porter(src, newvars)
    units, failed = [], {}
    for fn, orig in sorted(src.roots.items(), key=lambda kv: kv[1]):
        if trusted_only and not src.trust.get(fn):
            failed[fn] = "arcade twin not exact or region entered from outside"
            continue
        if src.layout_only.get(fn):
            failed[fn] = "no change beyond relocation/record layout (original kept)"
            continue
        if fn in src.needs_layout:
            failed[fn] = "needs the Arrange record layout: " + src.needs_layout[fn]
            continue
        text, res = pt.port_unit([fn])
        probs = [f"{f:#x}: {p}" for f, r in res.items() for p in r.problems]
        if probs:
            failed[fn] = probs[0]
            continue
        start, end, last_term = src.region[fn]
        if not last_term:
            text += f"\tjmp\t${end:x}.l\n"      # the 68k code falls through to the next fragment
        try:
            asm68k.assemble(text, 0x200000)
        except Exception as e:  # noqa: BLE001
            failed[fn] = f"asm: {e}"
            continue
        units.append({"orig": orig, "end": end, "fn": fn, "text": text, "trust": src.trust_why.get(fn)})
    return units, failed


NEWVAR_BASE, NEWVAR_SIZE = 0xE09800, 0x6800     # enhanced RAM for Arrange-only variables


def port_all(pairs: list[tuple[str, str]], cache: Path | None = None, arrange_records: bool = False) -> dict:
    """Port every trusted unit of the given overlay pairs (shared Arrange-only variables).
    Returns {"units": [...], "failed": {...}, "newvars": {arr: pgm}}; cached in `cache`."""
    import hashlib
    from .verify import Index, load_listing
    from .port import WORK
    here = Path(__file__).parent
    key = hashlib.sha1(("".join(sorted(f.read_text() for f in here.glob("*.py"))) + repr(pairs)
                        + repr(arrange_records)
                        + (VERIFIED.read_text() if VERIFIED.exists() else "")).encode()).hexdigest()
    if cache and cache.exists():
        d = json.loads(cache.read_text())
        if d.get("key") == key:
            return d
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    srcs = [(a, r, OverlaySource(a, r, index=index, arrange_records=arrange_records)) for a, r in pairs]
    nv = NewVars(NEWVAR_BASE, NEWVAR_SIZE)
    for _, _, src in srcs:
        overlay_units(src, nv)
    nv.assign()
    units, failed = [], {}
    for a, r, src in srcs:
        u, f = overlay_units(src, nv)
        for x in u:
            x["overlay"] = r
        units += u
        failed.update({f"{r}:{k:#x}": v for k, v in f.items()})
    d = {"key": key, "units": units, "failed": failed,
         "newvars": {f"{k:#x}": f"{v:#x}" for k, v in sorted(nv.map.items())}}
    if cache:
        cache.write_text(json.dumps(d, indent=0))
    return d
