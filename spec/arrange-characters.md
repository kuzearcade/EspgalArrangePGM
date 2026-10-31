# Arrange characters (Chihiro, Black): player logic map

PS2 Arrange mode offers only Chihiro and Black. The port gives them the arcade character
slots by the player record's character word +0x4A (P1 0x80EE96, PS2 0xC310C0): Chihiro 0
(Ageha's slot), Black 2 (Tateha's). Values below are addresses in the Arrange overlay A0A9
(PS2) and in the arcade program (68k). Arrange data is copied from the disc at build time.

## Character records (ported)

| | arcade | Arrange (A0A9 data) |
|---|---|---|
| base records, read by 0x123BAA at player setup | table 0x127832 -> 0x12783A, 0x12784A | table 0xBFC738 -> 0xBFC700, 0xBFC720 |
| Kakusei-form toggle (+0x66 = 0/2), read by 0x124668 at the end of a Kakusei | table 0x12785A (4 records) | table 0xBFC7C0 (same order) |

A record: +0x10 and +0x14 longs, normal speed (+0x35, +0x1A), focused speed (+0x34),
+0x2C..+0x2F, +0x66. Arrange records are 0x20 bytes, the first 16 are the 68k record; only
the speeds differ (Chihiro 19/10, Black 22/15; Ageha 22/12, Tateha 17/10). Stage `code`,
`character_patches`. Checked in MAME against PCSX2 (Chihiro, Right then focused Left): 0x234
and 0x128 per 4 frames and the same stopping point in both; stock Ageha 0x28C and 0x164.
The Kakusei-form speeds are copied but not measured yet.

## Shots (pending)

Shot dispatch, same index in both games: `+0x66*2 + +0x4A*4 + +0x40*4` (+0x40 = 4 while the
laser / Power Shot is on). Arcade table 0x122CF0, Arrange table 0xBFC6D0:

| slot | arcade | Arrange |
|---|---|---|
| char 0, normal (+0x66 0 / 2) | 0x122D10 / 0x122DB8 | 0x824EF0 / 0x824F20 -> 0x823BF0 (a0 = 1 / 2) |
| char 2, normal | 0x122E60 / 0x122F2E | 0x8265B0 / 0x8265E0 -> 0x824F50 |
| char 0, laser / Power Shot | 0x1230CA / 0x123174 | 0x827CC0 / 0x827CF0 -> 0x826610 (a0 = 1 / 2), then 0x830B20 |
| char 2, laser / Power Shot | 0x12321E / 0x122FFC | 0x82A850 / 0x82A880 -> 0x827D20 |

The Arrange entries are stubs calling the routine with a native argument in $a0 (the
porter refuses native-argument calls): these are hand ports. Chihiro's normal shot 0x823BF0
mirrors the arcade's (find free slots in the player-shot pool, 0x34-byte records, as many as
the pattern needs, then spawn), with differences: 7 slot pointers instead of 5, +4 slots
while +0x66 is set, pattern tables chosen by the argument, spawn through 0x834450 (arcade
0x1235A0). Its de-translation has unknown loop flags: port from the MIPS.

Volley counts: arcade table 0x1278AA (per character two word arrays, copied by 0x123B42
into the per-player shot state 0x81268A/0x812692), Arrange table 0xBFC810 (same lengths;
Chihiro 7 and 0x10, Black 5/6 and 0x10; Ageha 7..10 and 8). They must land with the
Arrange shot pool (98 entries instead of 34, held back in stage `code`): with the arcade
routine and pool, 7 volleys of 5 shots already exceed the 34 entries.

Player update: Arrange 0x82B970 (no exact twin; its barrier part is ported as constants,
stage `code` BARRIER_PATCHES). Rapid fire on C: Arrange 0x830B20 (ported as the input-bit
swap at 0x1229BE).

Checks: PCSX2 runs with the pad script tools/pcsx2_pad_arrange_stage1.txt select Chihiro
(Circle at 3000; Right before it selects Black). PS2 pad bits: 0x80 Square = shot (A),
0x10 Triangle = Kakusei (B), 0x20 Circle = C, 0x40 Cross = D (barrier); directions 0x1000
up, 0x2000 right, 0x4000 down, 0x8000 left.
