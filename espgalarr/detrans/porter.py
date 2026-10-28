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


_SAVE = re.compile(r"^\s+move\.l\s+([da][0-7]),(-(\d+))?\(a7\)\s*$")
_ADJ = re.compile(r"^\s+lea(\.l)?\s+-(\d+)\(a7\),a7\s*$")


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


def plan(newvar_base: int = 0xE09000, newvar_size: int = 0x7000, max_funcs: int = 64):
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


class OverlaySource:
    """Recovered Arrange routines of one overlay pair, de-translated on demand, with their
    68k originals (anchors) for the changed ones."""

    def __init__(self, a_name: str, r_name: str, index=None, main_funcs: dict | None = None):
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
        self._text: dict[int, str] = {}
        for m in diff.matches:
            if m.kind != "near":
                continue
            ca = self.A.read(m.a.start, m.a.size)
            if not is_translated(ca):
                continue
            try:
                ra = core.detranslate(ca, m.a.start, self.alm, read_word=_reader(self.A))
            except Exception:  # noqa: BLE001
                continue
            rep = compare_function(self.index, ra)
            if rep.anchor is None:
                _fallback_pairs(self.index, ra, rep, routine_starts)
            if rep.anchor is not None:
                self.roots[m.b.start] = rep.anchor
            # learn call targets: the n-th call of the Arrange routine <-> the n-th call of its
            # arcade twin (aligned), where only the arcade side is resolved
            try:
                rb = core.detranslate(self.R.read(m.b.start, m.b.size), m.b.start, self.amap, read_word=_reader(self.R))
            except Exception:  # noqa: BLE001
                continue
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

        self.funcs = dict(self.amap.funcs)
        for b, c in self.call_votes.items():
            a, v = c.most_common(1)[0]
            if b not in self.funcs and v >= 0.75 * sum(c.values()):
                self.funcs[b] = a

    def text(self, fn: int) -> str | None:
        if fn in self._text:
            return self._text[fn]
        f = self.fb.get(fn)
        if f is None:
            return None
        code = self.R.read(f.start, f.size)
        if not self.is_translated(code):
            return None
        try:
            res = self.core.detranslate(code, f.start, self.amap, read_word=self._reader(self.R))
        except Exception:  # noqa: BLE001
            return None
        t = res.text(header=False)
        self._text[fn] = t
        return t


def overlay_porter(src: OverlaySource, newvars: NewVars | None = None) -> Porter:
    base = Porter.load()
    pt = Porter(base.ram, {**base.func_map, **src.funcs}, base.native)
    pt.newvars = newvars
    pt.source = lambda fn: src.text(fn) or base.source(fn)
    return pt
