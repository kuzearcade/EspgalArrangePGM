"""Build stage 'code': 68k gameplay changes for Arrange mode.

Rules with addresses established in spec/pgm-gameplay.md are patched directly here; the
rules that need the recovered Arrange routines (player characters, shots, barrier, economy,
scoring) are added as the de-translator delivers them (see spec/arrange-diff.md).
"""
from __future__ import annotations

import struct

from ..pgm import m68k

# Extend thresholds (in 10-point units) indexed by the dip-switch setting; the last entry
# (0xFFFFFFFF) is "no extend" and is kept. Arrange: 80,000,000 and 280,000,000 points.
EXTEND_TABLE_1 = 0x178D7E
EXTEND_TABLE_2 = 0x178D8E
ARRANGE_EXTEND_1 = 8_000_000
ARRANGE_EXTEND_2 = 28_000_000


def extend_patches(ps: m68k.PatchSet, p: bytes) -> None:
    for table, value in ((EXTEND_TABLE_1, ARRANGE_EXTEND_1), (EXTEND_TABLE_2, ARRANGE_EXTEND_2)):
        for i in range(4):
            cur = struct.unpack_from(">I", p, table + 4 * i)[0]
            if cur != 0xFFFFFFFF:
                ps.add(table + 4 * i, struct.pack(">I", value), f"extend threshold {i}")


# "No continues": the continue flow checks the continue-block flag 0x8139B2 with
# `tst.w $8139B2 ; bne.w <skip>` at these sites; turning the bne into bra always skips.
CONTINUE_BNE_SITES = (0x1791DE, 0x1795D6, 0x140DA8)


def continue_patches(ps: m68k.PatchSet, p: bytes) -> None:
    for a in CONTINUE_BNE_SITES:
        assert p[a:a + 2] == b"\x66\x00", f"expected bne.w at {a:#x}"
        ps.add(a, b"\x60\x00", "no continues: bne.w -> bra.w")


def run(state) -> None:
    ps = m68k.PatchSet()
    p = bytes(state.p_rom)
    extend_patches(ps, p)
    continue_patches(ps, p)
    ps.apply(state.p_rom)
    state.note(f"code: {len(ps.patches)} patches (extends 80M/280M, continues disabled)")
