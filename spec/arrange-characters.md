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

## Buttons (ported: chars.BUTTON_ASM)

Arrange's player update (0x82B970) after the barrier and Kakusei checks (which stay the
arcade's, D = barrier by code.BARRIER_PATCHES): A held sets +0x40 = 4 at once (the arcade
waits 25 frames, +0x4E) with the arcade's speed ramp to the focused speed; the first Power
volley arms +0x99 = 0x10 tail volleys, counted down one per volley (+0x2E/+0x2F), so a tap
fires 16 volleys and holding past them ends the Power Shot on release. The end clears +0x40,
runs the arcade laser end 0x124A50 (sound off) and for character 0 sets +0x52..+0x55 = 0,
+0x56 = 1, +0x57 = 0 (shared ELF 0x1FD840; the start sets +0x56 = 1, +0x57 = 0). A never fires
the normal shot; C fires it every other frame (0x830B20: +1 bit 4 toggled), also while the
Power Shot is on. The arcade's A logic (0x122B08..0x122CC8), its rapid-fire block (0x1229BE)
and the laser beam helpers (0x124BD6 / 0x12480C) are no longer used.

Checked against PCSX2 (Square held / tapped 4 frames from frame 3600): the Power Shot is on
2 frames after the press with +0x99 = 0xF, +0x99 reaches 0 after 16 volleys, 16 volleys after
a tap, the same record counts every 2 frames; C held keeps 12-14 normal shots in flight.

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

## Kakusei-form normal shot (ported: chars.kakusei_handlers)

Patterns 0xBF78E0 (3 shots per volley, type 0x8008: Ageha's Kakusei-shot handler slots 2/3).
Handler 2 (Arrange 0x983E70): muzzle, then flight setup from the record table 0xBF7BD0;
handler 3 (0x985770): flight drawn zoomed by power level (0xC07970); hit: hit record 0xBF7D00
(4-sprite lists after the set's 2K lists, as in the normal form) and a fixed-zoom hit
animation (0x97EA40). Ported at build time; the zoomed draws use the full zoom helper
0x11626A, the hit sound 0x7980F0 lost its native arguments and is the arcade's 0x17D3DC.

Kakusei-form switch: Arrange uses the ELF routine 0x2035E0 (the arcade mode's overlay has
its own twin of 0x124668). Differences from the arcade, ported as a replacement of 0x124668:
the switch turns the Power Shot off (+0x40, +1 bit 7, +0x99), clears +0x8A, sets +0x94 = 4;
the normal form zeroes +0x52..+0x55; the Kakusei form keeps the shot palette +0x48 (the arcade
switches it to +0x91, purple shots) and sets +0x56 = 1, +0x57 = 0 (arcade 4).

Checked against PCSX2 (gems 299 / gauge 0x418 written every frame, Triangle tapped, Circle
held 100 frames, work/scratch/ps2kak, tests/lua/powertest.lua ESPGAL_KAKUSEI=1
ESPGAL_BUTTON="P1 Button 3" ESPGAL_DELAY=40 ESPGAL_RECORDS=0-34): the same positions, types
(0x804C), index words and sprite cycle at every sample, 3 shots per volley up to 29 in flight,
+0x48 = 2 throughout, green shots in the screenshots. Open: on the PS2 a hit Kakusei shot also
carries type bit 0x2000 (0xA6CC, port 0x86CC); no handler reads or sets it, so it comes from
Arrange's collision / scoring code (not ported).

## Power Shot (ported: espgalarr/chars.py, chihiro_power)

Arrange 0x826610 + spawner 0x8383E0 (stubs 0x827CC0 / 0x827CF0, dispatch slots 4/5) is Ageha's
laser (0x1230CA + 0x12376C) reworked; the entry format (0x26 bytes, type 0x8020, Kakusei
form 0x8028) and the shot types are the laser's, so the Power Shot takes the laser's handler
slots 8-11 (Arrange 0x98A650 / 0x98BA10 / 0x98CF30 / 0x98E2F0; Tateha's laser is 0x8030,
slots 12/13). Player fields: +0x57 lever angle (maintained by the arcade laser code
0x1240D8..), +0x62 / +0x64 animation counters (the laser's, Arrange resets +0x62 to 8),
+0x98 deferred shot sound (Arrange only; the port plays the arcade's 0x123372 at once).

| | normal form (+0x66 = 0) | Kakusei form (+0x66 = 2) |
|---|---|---|
| patterns (per level count, 0x26-byte entries) | 0xBF9E40: 1 shot per volley | 0xC02520: 4 |
| flight records (10 bytes, 0x12471E) | 0xBF9F50 (5) | 0xC02860 (20) |
| hit records (24 bytes, list at +0x10, 0x12475A) | 0xBFA060 (5) | 0xC029F0 (20) |

Records come from pool record 35 on (the normal shot keeps 0..34); volley count from the
second volley array (+0x81268E, Chihiro 0x10), cap 0x12795C (Arrange 0xBFC838, same values).
Each `shots per volley` records scanned move the spread index on (d4 += 4, d5 += 8): d5 is
added to the first angle byte, d4 is stored at +0x2A and selects the offsets added to record
+0x10 / +0x14 (0xBFCD90 / 0xBFCDD0), the zoom of the draw (0xC07880) and the trail draw. The
second angle byte is +0x57 plus random/2 (0x11A0D8) unless +1 bit 5. Loader-filled pointers:
entries by index word take a new animation / auxiliary list when the first angle byte +0x1A
changes; animation lists (3 sprites and 0) follow the hit records, auxiliary lists (4
sprites) start at the next 16-byte boundary, the hit lists (28 sprites, one per distinct hit
record) follow them (checked against PCSX2 RAM, re-checked at build time).

Drawing: the shots go to Arrange's new sprite layer 2 (PS2 0x198430, zoomed), the trails to
new layer 1 (0x1985C0, plain); the port calls the arcade helpers 0x11626A / 0x1162E0 and moves
the appended entry from layer 0x804C36 to the new layer (a full layer drops the sprite; the
PS2 does not check). Trail: on its first frame a Power Shot record takes a trail record
(0x9990C0; 64 x 0x22 per player, PS2 0xC33A70 / 0xC342F0, port layout.TRAIL_POOL): a 6-position
history of the shot, scrolled with the shots and drawn by spread index (table 0xC079D0, 1 to 4
sprites from the lists 0xC07A50). Arrange's shot walker 0x981510 walks the trails after both
shot pools; the port calls the arcade walker 0x126960 then the trail walk at the frame call
0x17C854.

PCSX2 reference (Chihiro, Square held from frame 3600, work/scratch/ps2pow): the Power Shot
starts 2 frames after the press (+0x40 = 4), one record per 2 frames from record 35, type
0x8060 (0x8020 | player bit +0x49), +0x1A = 0x14 + 8k, +0x2A = 4k, 16 records and 16 trails
alive after 30 frames; record 35 moves +0xDF per frame.

## Black's shots (ported: espgalarr/black.py, chars.BLACK_POWER_*)

Normal shot (Arrange 0x824F50, spawners 0x835A40 / 0x836F10 = the same code; dispatch slots
2/3): every shot of a volley owns a pool block (5 records, 8 in the Kakusei form) and takes
its first free record; volley count +0x81268A (Black 5, 8 in the Kakusei form) capped by
0x127958; the slot list holds 7 records and their 7 entries (0x28 bytes). Patterns 0xBFB350 /
0xBFBE40, types 0x8010 / 0x8018 (Tateha's handler slots 4-7: Arrange 0x986310 / 0x987570 /
0x988580 / 0x989650). List rule as Chihiro's with the x offset word +2 as the key (checked
against PCSX2 RAM). Flight records 0xBFB7D0 / 0xBFC1D0, hit records 0xBFB9F0 / 0xBFC360 (10
bytes; the Kakusei tables have no terminator: one record per entry index). Hits go through
the Arrange-only ELF routine 0x205830: one of three overlapping 4-sprite explosion lists
(table 0xBFBE10) picked by the ELF random 0x1BB5E0 (table 0x2D5100 of 0..2 by the arcade
random counter 0x801176 & 0x7F); both rebuilt as 68k code. Hit sound: arcade 0x17D490.

Power Shot (dispatch slots 6/7): normal form 0x827D20 = Chihiro's routine with spread angle
step 5 and an exit when a scanned record does not complete a volley block; Kakusei form
0x8293C0 takes blocks of 4 consecutive free records at once (its levels count 3 and hold 4
entries). Spawner 0x83A140 copies both angle bytes (no lever angle, no random). Patterns
0xC03030 (key +0x1A) / 0xC06580 (key +2), records (0xC03140, 0xC031B0) / (0xC068C0, 0xC06A50),
types 0x8030 / 0x8038 (Tateha's laser slots 12-15: 0x98FA20 / 0x990DE0 / 0x9929B0 /
0x993D70). The explosion angle adds the random byte once more per 16 of spread index (a loop
the porter could not express, rewritten in chars.py). Trails of type 0x40 + spread index
(trail draws 0x9977B0 / 0x997A30 / 0x997F40 / 0x9986C0, lists 0xC07AF0..). Volley counts:
arrays 2/3 of 0xBFC810 (5 and 0x10).

Checked against PCSX2 (Black, invincible, C held, Square held, Kakusei with C, Kakusei with
Square; work/scratch/ps2blk, tests/lua/shottrace.lua): the PCSX2 pad input reaches the game
one frame later than the MAME script, so A-button phases are compared PS2 frame r against
MAME frame r-1. Normal shots, Power Shot spawns and flight match sample for sample until the
first hits. Open: (1) the PS2 registers hits earlier and with type bit 0x2000 (0xA4F4 / 0xA4DC;
ours 0x84F4 a few frames later), so the score is higher on the PS2: Arrange's collision side,
not the shot code; (2) in the Kakusei Power Shot phase the 68k drops frames (50+ shots,
trails and Kakusei bullets; native PGM slowdown); (3) Black's Kakusei normal shots are
magenta on the PS2 and blue-violet here (palette of the encoded sprites, graphics work).

C rapid fire (Arrange 0x830B20, chars.py power_c): +1 bit 4 is toggled every frame C is held,
and the normal shot fires on the frames where the bit was clear before the toggle (the MIPS
tests it, toggles, and fires on Z set). The port had the test inverted, and the comparisons
sent C a counter early (the PS2 pad reaches the game a frame after the trace's frame, as for
A): the two cancelled out, with the bit in the opposite phase, until a barrier, whose start
clears the bit (0x122A3C) on both machines. From then on every normal-shot volley came a key
late, and in the Kakusei test the stage 1 mid-boss took its first hits two keys after the PS2
(key 0xC01). Both are fixed (the C events in tools/compare/make_tables.py): the Kakusei test's
enemy records match the PS2 over the whole trace, and +1 bit 4 matches in the shooting run.

Pending: the player tail 0x8316A0 (per-character animation table +0x9A), Arrange's shot
sounds (0x797B40 / 0x798750 / 0x7992B0, deferred sound +0x98), the collision difference above
(ported since: spec/arrange-collision.md).

Player update: Arrange 0x82B970 (no exact twin; its barrier part is ported as constants,
stage `code` BARRIER_PATCHES). Rapid fire on C: Arrange 0x830B20 (ported as the input-bit
swap at 0x1229BE).

## Player animation (ported: espgalarr/anim.py)

The player's animation state +0x58 (24 states, index x4) runs a handler every frame
(dispatchers 0x1233B8, 0x123528, 0x12395E). The arcade has one table 0x131C5A for both
characters; Arrange one per character (ELF 0x2D7730 Chihiro, 0x2D7790 Black; the loader puts
it in the character record +0x10, player setup copies it to +0x9A, and handlers switch state
through it). Handlers step a frame table: pointer table [+0x4A x 4 + +0x66 x 2] (Chihiro /
Black x normal / Kakusei form) -> frame list [+0x5C] -> 16-byte record (x/y offset long, a
long added to it, sprite long, size word, +0x3A word; the arcade's layout, Arrange's sprites
are bank A020 numbers). Every reload+1 frames (timer +0x5A) a record is copied to +6..+0xF.

| State | Chihiro / Black handler (A0A9) | Pattern | Frame table (ELF) | reload, end, restart / next |
|---|---|---|---|---|
| 0 | 0x844550 | loop (0x83FC60) | 0x2D77F0 | 2, 0x20, 0 (idle: 8 frames of 3; arcade 16 of 2) |
| 1 | 0x844570 | loop | 0x2D7A80 | 1, 0x90, 0x70 |
| 2 | 0x844590 | loop, sets +0x1C bit 6 | 0x2D7A80 | 1, 0x90, 0x70 |
| 3, 4 | 0x845510, 0x845530 | loop | 0x2D85D0, 0x2D8BE0 | 1, 0x4C, 0x2C |
| 5, 7, 8 | 0x845550, 0x8465B0, 0x8465D0 | once (0x840C90) | 0x2D91F0, 0x2D9340, 0x2D9490 | 2, 0x10, next 0 |
| 6 | 0x845570 | once, sets bit 6 | 0x2D91F0 | 2, 0x10, next 0 |
| 9 | 0x8465F0 | once | 0x2D95E0 | 1, 0x10, next 0x3C |
| 10..13 | none | (arcade kept) | | |
| 14 | 0x846610 / 0x846630 | once | 0x2DA650 | 1, 0x14 / 0x18, next 0x24 |
| 15 | 0x846650 / 0x846670 | once with alternative (0x841D90) | 0x2DA840 | 1, 0x34 / 0x38, next 0 (0x38 when +0x40) |
| 16, 17 | 0x846690, 0x846950 | reset: 0x124668, state 0 / 0x24 | | |
| 18 | 0x846C10 | once | 0x2DAE10 | 1, 0x58, next 0x4C |
| 19, 21 | 0x847C30, 0x849B50 | loop | 0x2DB520, 0x2DB840 | 1, 0x10, 0 |
| 20 | 0x848B30 | once | 0x2DB670 | 1, 0x14, next 0x54 |
| 22 | 0x84AA50 / 0x84BB90 | once with alternative, clears +0x5F | 0x2DB990 | 1, 0x0C / 0x10, next 0 (0x24) |
| 23 | 0x84CCD0 (0x842F80) | entry: records also set +0x5A and +0x6C, +0x6C moves the player up to 0x700, a flagged record calls the character's voice | 0x2DBAC0 | end 0x88 (arcade 0x80) |

"once": at `end` clear +0x5A/+0x5C (and +0x3A, +0x5F where listed), switch to `next`. The
port writes these handlers in 68k from the parameters (stubs read from the MIPS at build time,
full handlers' tables re-checked there), copies the 16 frame tables (690 records, 379 sprites
of bank A020, encoded by the graphics stage) and replaces the 17 `lea $131c5a.l,a0` with a call
that loads the character's table into a0 and +0x9A. The entry animation's voices (ELF
0x2161C0 -> A0A9 table 0xBFCD40: sound calls 0x153F80, bank 2 voices 5 / 0xB / 0x17 / 0x11)
keep the arcade's call (0x1266CE -> 0x127D66: SFX 0x39 / 0x3F / 0x65 / 0x5F, the same voice
indices); the 24 character-voice SFX hold Arrange's samples (espgalarr/voices.py).

Checks (shooting runs, work/scratch/shoot.sh, PS2 0xC310C0 watched): Chihiro 1960 of 1960
animation steps equal (state, index, offsets, size, sprite pairing); Black (Right on the select
screen on both) 30 (state, index) pairs with equal records and the same frames per index in
each state (the inputs drift from the PS2's, so steps are not compared in order); MAME
snapshots of the idle, both lean and Kakusei-form poses next to PCSX2 snapshots show the same
sprites.

Checks: PCSX2 runs with the pad script tools/pcsx2_pad_arrange_stage1.txt select Chihiro
(Circle at 3000; Right before it selects Black). PS2 pad bits: 0x80 Square = shot (A),
0x10 Triangle = Kakusei (B), 0x20 Circle = C, 0x40 Cross = D (barrier); directions 0x1000
up, 0x2000 right, 0x4000 down, 0x8000 left.
