"""Learn how Arrange RAM addresses correspond to arcade (PGM) RAM addresses.

The Arrange build moved many RAM blocks to other PS2 buffers (same layout, other place) and
resized a few. The de-translator prints Arrange addresses through a map learned from the
arcade build, so a moved block shows up at some other, consistent "68k" address. Aligning every
changed Arrange routine with its arcade twin pairs their absolute RAM operands; each pair votes
for "Arrange address A is arcade address B". Pairs with a common delta over a contiguous range of
Arrange addresses form a region, and any Arrange address inside a region translates with that
region's delta. Addresses outside every region are reported, not guessed.

Output: work/ps2/detrans/ram_xlate.json = {"pairs": {arr: {pgm: votes}}, "regions": [[lo, hi,
delta, votes], ...]}.
"""
from __future__ import annotations

import collections
import difflib
import json
from pathlib import Path

from ..diff import mips_funcs
from ..pgm import rom as rom_mod
from ..ps2.overlay import Overlay
from . import core
from .apply import is_translated, _reader
from .port import (OUT, STAGE_PAIRS, WORK, ElfAsOverlay, _arrange_map, _shape_key,
                   learn_overlay_map)
from .verify import Index, LearnedMap, load_listing, recovered_canon

RAM_LO, RAM_HI = 0x800000, 0x820000          # arcade 68k work RAM


def _ram_operands(c) -> list[int]:
    out = []
    for op in c.ops:
        if op[0] in ("abs", "imm") and isinstance(op[1], int) and 0x800000 <= op[1] < 0x900000:
            out.append(op[1])
    return out


def collect_pairs(A, R, lm: LearnedMap, votes: dict, verbose: bool = False) -> int:
    fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
    diff = mips_funcs.match(fa, fb)
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    amap = _arrange_map(p, A, R, diff, lm)
    n = 0
    for m in diff.matches:
        if m.kind != "near":
            continue
        code_a, code_b = A.read(m.a.start, m.a.size), R.read(m.b.start, m.b.size)
        if not is_translated(code_a) or not is_translated(code_b):
            continue
        try:
            ra = core.detranslate(code_a, m.a.start, lm, read_word=_reader(A))
            rb = core.detranslate(code_b, m.b.start, amap, read_word=_reader(R))
        except Exception:  # noqa: BLE001
            continue
        ia = [recovered_canon(i) for i in ra.insns if not i.is_label and i.mnem]
        ib = [recovered_canon(i) for i in rb.insns if not i.is_label and i.mnem]
        ka = [_shape_key(c) if c else "?" for c in ia]
        kb = [_shape_key(c) if c else "?" for c in ib]
        sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                ca, cb = ia[blk.a + k], ib[blk.b + k]
                if ca is None or cb is None:
                    continue
                xa, xb = _ram_operands(ca), _ram_operands(cb)
                if len(xa) != len(xb):
                    continue
                for a, b in zip(xa, xb):
                    if RAM_LO <= a < RAM_HI:
                        votes[b][a] += 1
                        n += 1
    if verbose:
        print(f"  {n} operand pairs")
    return n


def regions(votes: dict, min_votes: int = 2, max_gap: int = 0x400) -> list[list[int]]:
    """Group Arrange addresses (sorted) into runs that share one delta (pgm - arr)."""
    best = {}
    for b, c in votes.items():
        a, v = c.most_common(1)[0]
        if v >= min_votes and v >= 0.75 * sum(c.values()):
            best[b] = (a - b, v)
    out: list[list[int]] = []
    for b in sorted(best):
        d, v = best[b]
        if out and out[-1][2] == d and b - out[-1][1] <= max_gap:
            out[-1][1] = b + 2
            out[-1][3] += v
        else:
            out.append([b, b + 2, d, v])
    return out


def learn(verbose: bool = False, stages: bool = True) -> dict:
    disc = WORK / "ps2" / "disc" / "A0A0"
    lm = LearnedMap.load(OUT / "addrmap_arcade.json")
    votes: dict = collections.defaultdict(collections.Counter)
    collect_pairs(Overlay(disc / "A0A8"), Overlay(disc / "A0A9"), lm, votes, verbose)
    collect_pairs(Overlay(disc / "A0A8"), ElfAsOverlay(WORK / "ps2" / "disc" / "SLPS_253.52"), lm, votes, verbose)
    if stages:
        index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
        for a_name, r_name in STAGE_PAIRS:
            A, R = Overlay(disc / a_name), Overlay(disc / r_name)
            slm = learn_overlay_map(A, index, lm)
            collect_pairs(A, R, slm, votes, verbose)
    regs = regions(votes)
    res = {"pairs": {hex(b): {hex(a): v for a, v in c.items()} for b, c in sorted(votes.items())},
           "regions": [[hex(lo), hex(hi), d, v] for lo, hi, d, v in regs]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ram_xlate.json").write_text(json.dumps(res, indent=1))
    return res


class RamXlate:
    """Translate an Arrange RAM address (as printed by the de-translator) to a PGM address."""

    def __init__(self, data: dict, overrides: dict[int, int] | None = None):
        self.regions = [(int(lo, 16), int(hi, 16), d) for lo, hi, d, _ in data["regions"]]
        self.overrides = dict(overrides or {})
        self.pairs = {}
        for b, c in data.get("pairs", {}).items():
            a, v = max(c.items(), key=lambda kv: kv[1])
            if v >= 2 and v >= 0.75 * sum(c.values()):
                self.pairs[int(b, 16)] = int(a, 16)

    @classmethod
    def load(cls, path: Path = OUT / "ram_xlate.json", overrides=None) -> "RamXlate":
        return cls(json.loads(Path(path).read_text()), overrides)

    def __call__(self, addr: int) -> int | None:
        if addr in self.overrides:
            return self.overrides[addr]
        if addr in self.pairs:
            return self.pairs[addr]
        for lo, hi, d in self.regions:
            if lo <= addr < hi:
                return addr + d
        return None


if __name__ == "__main__":
    import sys
    r = learn(verbose="-v" in sys.argv, stages="--no-stages" not in sys.argv)
    print(f"{len(r['pairs'])} Arrange addresses paired, {len(r['regions'])} regions")
    for lo, hi, d, v in r["regions"]:
        if d:
            print(f"  {lo}-{hi} delta {d:+#x} ({v} votes)")
