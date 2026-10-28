"""Arrange palette diff: palettes are referenced individually by the 68k code (`lea $C3xxx`),
so the matched arcade/arrange PS2 functions give, for each ROM palette, the address of the
Arrange copy (in the ELF or overlay). Changed copies are returned for the data stage."""
from __future__ import annotations

import bisect
import collections
from dataclasses import dataclass
from pathlib import Path

from ..pgm import rom as rom_mod
from ..ps2 import elf as elfm, overlay
from . import datablock as db, mips_funcs

PAL_LO, PAL_HI = 0xC3A78, 0xC84F8
PAL_SIZE = 0x40


@dataclass
class PaletteChange:
    rom_off: int
    arrange_addr: int
    data: bytes
    equal_bytes: int


def _looks_like_palette(b: bytes) -> bool:
    words = [(b[i] << 8) | b[i + 1] for i in range(0, len(b), 2)]
    return all(w <= 0x7FFF for w in words) and len(set(words)) > 4


def analyse(work: Path) -> tuple[list[PaletteChange], int]:
    p = rom_mod.RomSet(work).p_rom_68k()[:0x200000]
    ovs = overlay.load_all(work / "ps2" / "disc")
    A, R = ovs["ov_arcade.bin"], ovs["ov_arrange.bin"]
    e = elfm.Elf(work / "ps2" / "disc" / "SLPS_253.52")
    mem = elfm.Memory(e, [R])
    sa = db.match_segments(p, A.image, 0, 0x200000, min_len=48)
    sa.sort(key=lambda x: x.ov_off)
    alo = [x.ov_off for x in sa]

    def rom_of(addr):
        o = addr - A.image_base
        i = bisect.bisect_right(alo, o) - 1
        if i >= 0 and sa[i].ov_off <= o < sa[i].ov_end:
            return sa[i].rom_off + (o - sa[i].ov_off)
        return None
    fa = mips_funcs.split_functions(A.image, A.image_base, A.load_addr + A.header.text_size)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.load_addr + R.header.text_size)
    diff = mips_funcs.match(fa, fb)
    pairs: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in diff.matches:
        if len(m.a.refs) != len(m.b.refs):
            continue
        for x, y in zip(m.a.refs, m.b.refs):
            r = rom_of(x)
            if r is not None and PAL_LO <= r < PAL_HI and (r - PAL_LO) % 0x20 == 0:
                pairs[r][y] += 1
    changes: list[PaletteChange] = []
    same = 0
    for r in sorted(pairs):
        y, _ = pairs[r].most_common(1)[0]
        try:
            b = mem.read(y, PAL_SIZE)
        except ValueError:
            continue
        if len(b) < PAL_SIZE:
            continue
        if b == p[r:r + PAL_SIZE]:
            same += 1
        elif _looks_like_palette(b):
            changes.append(PaletteChange(r, y, bytes(b), sum(1 for i in range(PAL_SIZE) if b[i] == p[r + i])))
    return changes, same
