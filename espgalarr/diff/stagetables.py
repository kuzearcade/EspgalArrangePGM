"""Code-anchored diff of the data tables embedded in the stage overlays.

Arrange's stage overlays (A0A0..A0A5) carry the same 68k data tables as the arcade-mode ones
(A0AA..A0AF), big-endian as in the ROM, with some values changed (enemy task timers and
bullet counts, speed tables, formation positions). For every matched pair of stage functions
(arcade twin, Arrange twin), each paired data reference x (arcade overlay) / y (Arrange
overlay) gives a table: the arcade bytes from x up to the next referenced address (at most
MAX), cut to the longest prefix found exactly once in the ROM, are the ROM table; the Arrange
bytes at y over the same length are its Arrange version.

Skipped: tables whose Arrange bytes also occur verbatim elsewhere in the ROM (two similar
tables whose functions the matcher crossed, e.g. 0x1A7666 / 0x1AB402 in stage 6), and changes
that are only the 0x24 -> 0x2C enemy record offsets (the layout stage converts those).
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
from pathlib import Path

from ..pgm import rom as rom_mod
from ..ps2 import overlay
from . import mips_funcs

MAX = 0x200
MIN = 8
# Mode cores (A0A8, A0A9): their tables also hold sprite numbers, score values the code stage
# patches and hitbox offsets, so only these enemy init blocks (copied into the enemy object
# by the shared enemy inits; Arrange changes timer reloads and counts) are transplanted
CORE_TABLES = {0x16B1E4, 0x16ABBE, 0x167756, 0x157BA2}
# stage 5 enemy task 4's init block (0x1BD89A copies 12 words): Arrange fires 3 / 3 volleys
# of a 0x18-step pattern (arcade 2 / 2, 8)
EXTRA = [(0x1BD862, "A0AE", 0x686F60, "A0A4", 0x68F760, 24)]
# (arcade-mode, Arrange) stage overlays; the ending (A0B0 / A0A6) is left out
PAIRS = [("A0A8", "A0A9"), ("A0AA", "A0A0"), ("A0AB", "A0A1"), ("A0AC", "A0A2"), ("A0AD", "A0A3"), ("A0AE", "A0A4"),
         ("A0AF", "A0A5")]


@dataclass
class StageTable:
    rom_off: int
    arrange: bytes
    overlay: str
    arrange_addr: int


def _record_offsets_only(a: bytes, r: bytes) -> bool:
    from .. import records
    for o in range(0, len(a) - 1, 2):
        va, vr = int.from_bytes(a[o:o + 2], "big", signed=True), int.from_bytes(r[o:o + 2], "big", signed=True)
        if va != vr and records.convert(va) != vr:
            return False
    return True


def analyse(work: Path) -> tuple[list[StageTable], list[tuple[int, str]]]:
    """(tables to transplant, skipped (rom offset, reason))."""
    p = bytes(rom_mod.RomSet(work).p_rom_68k()[:0x200000])
    out: list[StageTable] = []
    skipped: list[tuple[int, str]] = []
    seen: set[int] = set()
    for an, rn in PAIRS:
        A = overlay.Overlay(work / "ps2" / "disc" / "A0A0" / an)
        R = overlay.Overlay(work / "ps2" / "disc" / "A0A0" / rn)
        fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
        fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
        pairs: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        for m in mips_funcs.match(fa, fb).matches:
            for x, y in zip(m.a.refs, m.b.refs):
                if A.contains(x) and R.contains(y) and x >= A.header.text_end:
                    pairs[x][y] += 1
        xs = sorted(pairs)
        for i, x in enumerate(xs):
            y = pairs[x].most_common(1)[0][0]
            size = min(xs[i + 1] - x if i + 1 < len(xs) else MAX, MAX)
            a, r = A.read(x, size), R.read(y, size)
            n = min(len(a), len(r)) & ~1
            while n >= MIN and p.count(a[:n]) != 1:
                n -= 2
            if n < MIN or a[:n] == r[:n]:
                continue
            a, r = a[:n], r[:n]
            k = p.find(a)
            if k in seen or (an == "A0A8" and k not in CORE_TABLES):
                continue
            seen.add(k)
            if p.find(r) >= 0:
                skipped.append((k, f"{rn} {y:#x}: Arrange bytes occur elsewhere in the ROM"))
            elif _record_offsets_only(a, r):
                skipped.append((k, f"{rn} {y:#x}: record offsets only"))
            else:
                out.append(StageTable(k, bytes(r), rn, y))
    # tables only unmatched code references (a task init the matcher left unpaired), placed by
    # hand: (ROM, arcade overlay, its copy, Arrange overlay, its copy, bytes); the arcade copy
    # must equal the ROM
    for k, an, x, rn, y, n in EXTRA:
        A = overlay.Overlay(work / "ps2" / "disc" / "A0A0" / an)
        R = overlay.Overlay(work / "ps2" / "disc" / "A0A0" / rn)
        assert A.read(x, n) == p[k:k + n], f"arcade copy of {k:#x}"
        if k not in seen and R.read(y, n) != p[k:k + n]:
            seen.add(k)
            out.append(StageTable(k, R.read(y, n), rn, y))
    return out, skipped
