"""Build stage 'data': transplant Arrange data tables into the program ROM.

Currently: sprite palettes whose Arrange copy (anchored through matched PS2 functions) differs
from the ROM. Other tables follow as the de-translated Arrange code pins their formats.
"""
from __future__ import annotations

from ..diff import palettes as pal_mod
from ..pgm import m68k


def run(state) -> None:
    changes, same = pal_mod.analyse(state.work)
    ps = m68k.PatchSet()
    for c in changes:
        ps.add(c.rom_off, c.data, f"palette {c.rom_off:#x} from {c.arrange_addr:#x}")
    ps.apply(state.p_rom)
    state.note(f"data: {len(changes)} palettes replaced ({same} unchanged): "
               + ", ".join(f"{c.rom_off:#x}" for c in changes))
