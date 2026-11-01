"""Build stage 'data': transplant Arrange data tables into the program ROM.

Sprite palettes whose Arrange copy (anchored through matched PS2 functions) differs from the
ROM, and the stage overlays' embedded tables Arrange changed (diff/stagetables.py: enemy task
timers and bullet counts, speeds, positions).
"""
from __future__ import annotations

from ..diff import palettes as pal_mod
from ..diff import stagetables
from ..pgm import m68k


def run(state) -> None:
    changes, same = pal_mod.analyse(state.work)
    ps = m68k.PatchSet()
    for c in changes:
        ps.add(c.rom_off, c.data, f"palette {c.rom_off:#x} from {c.arrange_addr:#x}")
    ps.apply(state.p_rom)
    state.note(f"data: {len(changes)} palettes replaced ({same} unchanged): "
               + ", ".join(f"{c.rom_off:#x}" for c in changes))
    tables, skipped = stagetables.analyse(state.work)
    ps = m68k.PatchSet()
    for t in tables:
        ps.add(t.rom_off, t.arrange, f"stage table {t.rom_off:#x} from {t.overlay}:{t.arrange_addr:#x}")
    ps.apply(state.p_rom)
    state.note(f"data: {len(tables)} stage tables replaced ({len(skipped)} skipped): "
               + ", ".join(f"{t.rom_off:#x}" for t in tables))
