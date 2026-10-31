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

## Buttons

Arrange's player update (0x82B970) fires the Power Shot on A: pressing A sets +0x40 = 4 at
once (the arcade waits 25 frames before its laser), and after release it keeps firing 0x10
more volleys (+0x99). C is rapid fire (0x830B20, +1 bit 4 toggled every frame) and fires the
normal shot. The port keeps the arcade's button logic (A: shot, then laser after 25 frames;
C: the arcade's rapid-fire toggle, moved from D), so on PGM the normal shot comes from C or
tapping A; the Power Shot on A is pending with the player update.

## Player-shot pool (ported)

Arcade 35 records of 0x34 bytes per player, Arrange 99 (98 shots and a terminator). Stage
`layout` (`arrange_shots`, `--arcade-shots` keeps the arcade pools): both pools in enhanced
RAM at 0xE0C800 (23 base references, walker counts 0x22 -> 0x62, game-start clear
asm/m68k/shot_pool.s); Chihiro's slot list (7 longs) follows them.

## Normal shot (ported: espgalarr/chars.py)

Shot dispatch, same index in both games: `+0x66*2 + +0x4A*4 + +0x40*4` (+0x40 = 4 while the
laser / Power Shot is on). Arcade table 0x122CF0, Arrange table 0xBFC6D0:

| slot | arcade | Arrange |
|---|---|---|
| char 0, normal (+0x66 0 / 2) | 0x122D10 / 0x122DB8 | 0x824EF0 / 0x824F20 -> 0x823BF0 (a0 = 1 / 2) |
| char 2, normal | 0x122E60 / 0x122F2E | 0x8265B0 / 0x8265E0 -> 0x824F50 |
| char 0, laser / Power Shot | 0x1230CA / 0x123174 | 0x827CC0 / 0x827CF0 -> 0x826610 (a0 = 1 / 2), then 0x830B20 |
| char 2, laser / Power Shot | 0x12321E / 0x122FFC | 0x82A850 / 0x82A880 -> 0x827D20 |

Slots 0/1 now run Chihiro's normal shot: new 68k code following 0x823BF0 and its spawner
0x834450 (same structure as the arcade 0x122D10/0x1235A0: free records from the pool, as many
as the volley needs; 7 slot pointers; +4 volleys while +0x66 is set; then the arcade's shot
sound step 0x123372). Patterns (A0A9 0xBF7040 / 0xBF78E0: per level a count and 0x2C-byte
entries with the object fields inline) are copied; the loader-filled pointers (+0x0A muzzle
list, +0x1E flight list) are assigned by the rule in chars.py (entries by index word, a new
16-byte list per shape), checked against PCSX2 RAM and re-checked at build time. The sprite
numbers (bank A020, Arrange-only art) are encoded into A/B free space by the graphics stage
(13 sprites).

Shot handlers: the per-frame walker 0x126980 calls a handler by type (table 0x126A22);
Arrange rewrote all 16 (A0A9 table 0xC07990). Chihiro's shot spawns as type 0x40 (handler 0,
0x981C40: muzzle flash, then flight setup from the record table 0xBF73D0) and flies as 0x44
(handler 1, 0x9832D0: speed from the angle bytes when the +0x2A timer runs out, off-screen
removal, zoomed draw with the per-power-level zoom 0xC07940; on a hit: score 10*(level+1),
hit record 0xBF7550, 4-frame hit animation). Ported at build time by porter.port_roots with
the fixes listed in chars.py (draw calls, the two record tables, P2's score record 0x81B596).
Ageha's types used the same handler slots; Tateha's (0x8010/0x8018) do not.

Checked against PCSX2 (Chihiro, C held from stage scroll 12, tests/lua/shottest.lua with
ESPGAL_SHOT_HOLD): the same trajectory frame by frame (x 0x336C, 0x336C, 0x32AC, 0x31EC,
0x36A4, ... speed 0x4B8), the same type changes 0x8040 -> 0x8140 -> 0x8044, and 12-14 shots
in flight in both. Found on the way: the recovered constant at 0x1473D6 (from an ELF
function, shared by both PS2 modes) set the one-player flag 0x813998 to 0, which applied the
volley cap; constants recovered from ELF functions are no longer applied.

Volley counts: arcade table 0x1278AA (per character two word arrays, copied by 0x123B42
into the per-player shot state 0x81268A/0x812692), Arrange table 0xBFC810 (same lengths).
Character 0's arrays are copied (Chihiro 7 and 0x10); the cap table (0x127958) is the same
in both games. Black's are pending with his shots.

Pending: the Kakusei-form patterns (0xBF78E0, type 0x48) need handlers 2/3; the Power Shot
(0x826610, patterns 0xBF9E40 / 0xC02520, its own pool part from record 35) and the A-button
logic; Black (0x824F50, 0x827D20); the player tail 0x8316A0 (per-character animation table
+0x9A) and the shot sounds (0x797B40 / 0x798750 / 0x7992B0).

Player update: Arrange 0x82B970 (no exact twin; its barrier part is ported as constants,
stage `code` BARRIER_PATCHES). Rapid fire on C: Arrange 0x830B20 (ported as the input-bit
swap at 0x1229BE).

Checks: PCSX2 runs with the pad script tools/pcsx2_pad_arrange_stage1.txt select Chihiro
(Circle at 3000; Right before it selects Black). PS2 pad bits: 0x80 Square = shot (A),
0x10 Triangle = Kakusei (B), 0x20 Circle = C, 0x40 Cross = D (barrier); directions 0x1000
up, 0x2000 right, 0x4000 down, 0x8000 left.
