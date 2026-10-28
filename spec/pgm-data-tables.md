# PGM Espgaluda (espgalbl) — program ROM data-table map

Addresses are 68k addresses (the 4 MB cartridge program maps at 0x000000). Facts here come
from static analysis of the bootleg program and from matching the tables against the copies
embedded in the PS2 port's `ov_arcade.bin` / `ov_arrange.bin` overlays. No original bytes are
recorded in this repository; the build reads everything from the user's inputs.

## Layout overview

| Range | Content | Notes |
|---|---|---|
| 0x000000–0x0003FF | 68k vector table | SP 0x820000, reset PC 0x111FD4, IRQ1..11 at 0x1120C4.. |
| 0x000200–0x00031F | tiny stubs called via `jsr $200` / `jsr $220` | referenced from 0x13A792 / 0x177E16 |
| 0x000920–0x000D1F | **PT1**: 256 longs → 0x208-byte velocity-vector records (T1) | indexed by speed (object byte 0x1A) |
| 0x000D20–0x00111F | **PT2**: 256 longs → 0x400-byte velocity-vector records (T2) | 64 directions × 2 words (dx, dy) |
| 0x001120–0x02191F | T1 records (256 × 0x208) | speed N: 8-byte header + 64 × (dx.l, dy.l) 4-bit fixed point |
| 0x021920–0x06191F | T2 records (256 × 0x400) | speed N: 64 directions × (dx.w, dy.w), 8 sub-steps per 16 bytes |
| 0x061920–0x08291F | T3: alternate set of T1-type records | selected at runtime via RAM 0x8139E8 (`move.l #$61920,$8139E8` at 0x147FDA) |
| 0x082920–0x0C291F | T4: alternate set of T2-type records | via RAM 0x8139EC |
| 0x0C2920–0x0C2A5F | pointer table (longs) → 0x0C2A84.. short records | used with `lea $C2920,a1` at 0x1798B4 (text/menu?) |
| 0x0C29C0–0x0C2A5F | second pointer table → interleaved records | 0x1798C0 |
| 0x0C3A78–0x0C84F7 | **palettes**: 0x40-byte (32 × 15-bit) sprite palettes, some 0x20-byte halves | ~0x4A80 bytes, referenced individually by `lea $C3xxx,a0` |
| 0x0C84F8–0x0D522F | **script block A** (0xCD38 bytes) | single `lea` at 0x14A79A; **replaced in the Arrange overlay** |
| 0x0D5230–0x0D563F | small table (0x410) | 0x183DFE |
| 0x0D5640–0x0E23DF | **script block B** (0xCDA0 bytes) | `lea` at 0x183C42; **replaced in the Arrange overlay** |
| 0x0E23E0–0x0E363B | small tables | 0x155882, 0x143424, 0x143434 |
| 0x0E363C–0x0E3xxx | object-handler stub table: longs → 6-byte `jmp` stubs at 0x1BE6BA.. | dispatch by object type |
| 0x0E3xxx–0x10FFFF | not embedded in the PS2 overlays; sound data suspected (see sound spec) |
| 0x110000–0x1BFFFF | 68k code | |
| 0x1C0000–0x1FFFFF | Z80 sound driver + sequence data (Z80 opcodes visible at 0x1C0000) | details in spec/pgm-memory-map.md |
| 0x200000–0x3FFFFF | empty (0xFF) | **free for new code/data** |

## How the PS2 overlays embed these tables

`espgalarr/diff/datablock.py` aligns the ROM against each overlay. In `ov_arcade.bin` the ROM
tables 0x001327–0x0E363C are present almost verbatim (one shift per table; entry 0 of T2/T3/T4
differs). In `ov_arrange.bin` the same holds except that **0x0C2920–0x0DF04C is largely replaced**
(script blocks A/B, part of the palette block). Many "differences" between the arcade and
arrange overlays are relocated 32-bit little-endian pointers into the overlay itself, so the
mode diff must compare pointer *targets*, not pointer values (see Phase 3 in the plan).

Open questions tracked here:
- T1–T4 are speed×direction vector tables (sin/cos × speed); the sprite index tables are elsewhere (see spec/pgm-gameplay.md).
- Exact record format of script blocks A and B (enemy/bullet scripts vs stage event lists).
- What selects T3/T4 (0x8139E8/0x8139EC): a second angular resolution or a scaled copy?
