"""Code-anchored table diff: for every ROM table that matched PS2 code references (arcade
function -> arrange twin), fetch the Arrange overlay's copy and compare it with the ROM.

Result: a list of TableDiff(rom_off, size, arrange_bytes) for tables whose Arrange copy
differs, where `size` is the verbatim run the arcade overlay carries for that table
(bounded by the next code-referenced address), so it never spills into a neighbour.
"""
from __future__ import annotations

import bisect
import collections
from dataclasses import dataclass
from pathlib import Path

from ..pgm import rom as rom_mod
from ..ps2 import overlay
from . import datablock as db, mips_funcs


@dataclass
class TableDiff:
    rom_off: int
    size: int
    arrange: bytes
    arcade_equal: int       # bytes equal between ROM and the arrange copy
    votes: int


def analyse(work: Path) -> tuple[list[TableDiff], list[tuple[int, int]]]:
    p = rom_mod.RomSet(work).p_rom_68k()[:0x200000]
    ovs = overlay.load_all(work / "ps2" / "disc")
    A, R = ovs["ov_arcade.bin"], ovs["ov_arrange.bin"]
    sa = db.match_segments(p, A.image, 0, 0x200000, min_len=48)
    sa.sort(key=lambda x: x.ov_off)
    alo = [x.ov_off for x in sa]

    def seg_of(addr):
        o = addr - A.image_base
        i = bisect.bisect_right(alo, o) - 1
        if i >= 0 and sa[i].ov_off <= o < sa[i].ov_end:
            return sa[i], o
        return None, None

    fa = mips_funcs.split_functions(A.image, A.image_base, A.load_addr + A.header.text_size)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.load_addr + R.header.text_size)
    diff = mips_funcs.match(fa, fb)
    pairs: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in diff.matches:
        for x, y in zip(m.a.refs, m.b.refs):
            seg, o = seg_of(x)
            if seg is not None:
                pairs[seg.rom_off + (o - seg.ov_off)][y] += 1
    refs = sorted(pairs)
    out: list[TableDiff] = []
    same: list[tuple[int, int]] = []
    for i, r in enumerate(refs):
        y, votes = pairs[r].most_common(1)[0]
        seg, o = seg_of(y - R.image_base + A.image_base) if False else (None, None)
        # size: to the end of the arcade verbatim run, bounded by the next referenced address
        aseg = None
        for s in sa:
            if s.rom_off <= r < s.rom_end:
                aseg = s
                break
        if aseg is None:
            continue
        end = aseg.rom_end
        if i + 1 < len(refs):
            end = min(end, refs[i + 1])
        size = end - r
        ro = y - R.image_base
        if not (0 <= ro and ro + size <= len(R.image)) or size <= 0:
            continue
        arr = R.image[ro:ro + size]
        eq = sum(1 for k in range(size) if arr[k] == p[r + k])
        if eq == size:
            same.append((r, size))
        else:
            out.append(TableDiff(r, size, bytes(arr), eq, votes))
    return out, same
