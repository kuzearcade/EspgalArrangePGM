# PGM Espgaluda (espgalbl) — gameplay facts (addresses, fields, formulas)

Concise reference for the Arrange-mode port. All addresses are 68k addresses (ROM at 0,
RAM 0x800000-0x81FFFF). Detailed evidence is in `work/pgm/notes/gameplay.md`. No original code
bytes are recorded here.

## Conventions
* Positions are signed words in **1/64 pixel** (`asr.l #6` in the sprite builder). Object +2 is the
  hardware X axis = the game's vertical (scroll) axis, 0..0x7000 (448 px); +4 is hardware Y = the
  game's horizontal axis, 0..0x3800 (224 px). Player clamps: +2 in 0x700..0x6500, +4 handled by
  the same code (0x1243BE).
* Scores are stored in units of 10 points: the arcade draws the stored value followed by one more digit, the player's continue count (score record +0x0A; 0 without continues), so the display reads value * 10 + continues. (Arrange drops that digit and scores in points: spec/arrange-diff.md, HUD row.)
* 0x801166 = frame parity (0/1); several per-player routines run P1 on even, P2 on odd frames.
* Inputs: 0x8011FE (P1 held) / 0x801204 (P2 held), 0x801200 / 0x801206 (new presses).
  Bit layout after 0x113B58: 0 up, 1 down, 2 left, 3 right, 4 A (shot), 5 B (Kakusei),
  6 C (guard barrier), 7 D. Bits 6/7 swap when 0x80118E (button-config) is set and 0x801186 == 0.

## Program skeleton
| what | address |
|---|---|
| reset / init | 0x111FD4 -> 0x1128F2 |
| main loop | 0x112912: 0x11278A (frame counters), 0x137CC0 (test mode), **0x1179E4 task scheduler**, 0x11F290, 0x113D7A (sprite list), 0x112B8C (VBL wait), 0x113C08 (input edges) |
| task slots | 20 x 0x54 bytes at 0x809870 (pending list 0x808AF8, byte count 0x809188); slot: +0 id\|0x8000, +2 state, +0x40 first-run flag, +0x42/+0x46 context longs, +0x4A priority, +0x4C serial, +0x50 creator PC |
| task table | 0x117810, 8 bytes/entry {handler, priority}; create: d0 = id, `jsr 0x117ABC` (a0 = slot) |
| key tasks | 0 = 0x17E9F4 HUD, 2/3 = 0x1226DC/0x12271C player spawn, **5 = 0x17C824 gameplay frame**, 8 = 0x13CF08 boot, 0xA = 0x147C74 global bookkeeping (rank, world switch), 0x10 = 0x137E28 attract, 0x13 = 0x181E26 death/respawn/continue manager |

Gameplay frame (task 5) order: 0x17A898 (effects 0x81CF60), 0x14EE3C (enemy scripts),
0x17BE44 (enemy housekeeping), 0x16E72E (gems), 0x179862 (messages), 0x179B6E (explosions),
0x126960 (player shots), 0x12DBFE (rapier), 0x1312EA (options), 0x17B0A0, 0x123946/0x123972
(player state machines), 0x12392E/0x12393A (player update), 0x16DBC8 (pool A), 0x17242A (enemy
bullets), 0x123E64 (cancel timer), then 0x11B1D0 (player collision; P1/P2 alternate by parity).

## Object system
Generic record (0x40 bytes unless noted), a6-relative:
| off | meaning |
|---|---|
| 0x00 w | flags: bit15 active; bit13 (enemies) shootable; bit8 (player) double-barrier-cost pending; bits 0x1000/0x800 "touched by P1/P2" (0x80B0F2); low byte bit0 P2-owned (score), bit4 hit, bit6 cancelled/dying |
| 0x02 w / 0x04 w | x / y (1/64 px) |
| 0x06 w / 0x08 w | sprite x/y offset added at draw time |
| 0x0A l | sprite code: bits 0-22 B-ROM offset (hi 7 bits in word 2), bits 24-28 palette, 29-30 flip |
| 0x0E w | sprite size (wide<<9 \| high) |
| 0x10 l / 0x14 l | players: hitbox x extents (+x, -x) / y extents (+y, -y); other objects: anim pointer |
| 0x16 w | motion dx cache / counters |
| 0x18 w | timer (enemies: **HP**; players: held inputs) |
| 0x1A b / 0x1B b | **speed class** (index into PT1/PT2) / **direction** (bits 0-5 = 1/64 turn, bits 6-7 = negate x/y) |
| 0x1C w | extra word OR'd into sprite word 2 |
| 0x1E l | velocity (dx<<16 \| dy) for bullets ; shots: target enemy pointer |
| 0x22 l | think/handler pointer (bullets: 0x170020 normal, 0x16FFEE cancelled, 0x1700DA gold, 0x170160 gold-2) |
| 0x26 w | gold sprite index (bullets) |
| 0x28 w | gold value (bullets, in 10-pt units) |
| 0x2A l | parent object pointer |
| 0x36 w | collector flags (bits 0x10 P1 / 0x08 P2) |
| 0x38 w | gem type when cancelled (-1 = vanish) |
| 0x3C l | collecting player pointer |

**Velocity tables (PT1 0x920 / PT2 0xD20 and T1/T2 records)**: both are velocity vectors indexed
by `[speed class (+0x1A)][direction]`. T2 (0x400 bytes per class) = 256 directions x (dx,dy)
words; the common reader 0x1188C6/0x11822A uses only every 4th direction (frame<<4) = 64
directions, the fine reader 0x118768 uses all 256. T1 (0x208 bytes per class) = 8-byte header +
64 directions x (dx,dy) longs with 4 extra fraction bits (readers 0x118246/0x118924 `asr.l #4`).
**T3/T4 (0x61920/0x61D20) are the same tables with every velocity halved** = the awakened
(Kakusei) world. 0x8139E8/0x8139EC hold the active T1/T2 base; 0x8139F4 != 0 freezes motion.
Direction 0 = +x (up the screen), 0x10 = +y, 0x20 = -x, 0x30 = -y (player table 0x1278FC).

Pools:
| pool | base | stride | count | update |
|---|---|---|---|---|
| players | 0x80EE96 (P1), 0x80EF36 (P2) | 0xA0 | 2 | 0x12392E / 0x12393A |
| player shots | 0x80EFD6 / 0x80F6F2 | 0x34 | 35 each (active counts 0x812920/0x812922) | 0x126960, handlers 0x126A22[(flags&0x3C)/4] |
| rapier segments | 0x810E26 / 0x810E86 | – | 0x810EE6 / 0x810EE8 | 0x12DBFE |
| options | 0x812572 / 0x8125F4 | – | – | 0x1312EA |
| enemy scripts | 0x814410 (2 boss) 0x8144B0 (8 special types 0x20-0x23) 0x814730 (48 normal) | 0x50 | 58 | 0x14ECFA; slot +6 enemy obj, +0xC type, +0xD flags, +0x12 script PC, +0x4C think |
| enemies | 0x815680 | 0x24 | 120; counts 0x816760/0x816762/0x816764 | spawned by scripts; +0xE/+0x10/+0x12/+0x14 hitbox (right,left,bottom,top), **+0x18 HP**, +0x1A secondary HP, +0x22 gem count, +0x23 gem mode |
| pool A (big items) | 0x81743E (8), 0x81763E (2), 0x8176BE (2), 0x81773E (1) | 0x40 | 13 (0x81777E) | 0x16DBC8; alloc 0x16DAD8 |
| gems | 0x817780 | 0x1E | 100 (0x818338) | 0x16E72E; alloc 0x16E702 -> 0x16FD26; collect 0x16F7DA |
| enemy bullets | 0x818352 | 0x40 | up to 0x81B55A blocks (unrolled loops) ; 0x81B552 active/frame | 0x17242A |
| messages | 0x81B614 | 0x40 | 80 | 0x179862 (tables 0xC2920/0xC29C0) |
| explosions | 0x81CA5E | 0x40 | 0x81CF5E | 0x179B6E |
| effects | 0x81CF60 | 0x30 | 0x81D500 | 0x17A898 |
| misc | 0x81DD02 | 0x26 | 0x81DE7E / 0x81DCFE | 0x17BE60 / 0x17B0A0 |
| HUD records | 0x81E11A | 0x64 | 2 | 0x17F37C |
| high scores | 0x80107E | 0x1A | 5 | defaults 0x178C72 (stored 500,000..100,000, shown 5M..1M with the continue digit), insert 0x178F42/0x178F7E; drawn by 0x13E1C2 (score) and 0x13E240 (continue digit, entry +0x16) |

Sprite output: objects append 12-byte entries {pos long (x<<16\|y)>>6 masked 0x7FF03FF \|
0x80008000, code long (+0xA), size (+0xE), extra (+0x1C)} to per-layer work lists (helpers
0x116428/0x116464/0x1164A8 -> 0x8051EE+0x806FEC; 0x1161EA/0x11626A -> 0x804C36+0x806FE6; 0x114AF0
-> 0x80366A+0x806FD2; 0x80120A+0x806FCA ...; 27 counters 0x806FCA..0x806FFE summed by 0x113D7A).
0x1140C4 converts them into IGS023 sprite entries at 0x800000 (10 bytes: x\|zoom, y\|zoom,
pal/flip/boff-hi, boff-lo, wide<<9\|high) adding scroll 0x807058, inserting a dummy entry every 51,
max 251 sprites. Zoom code tables 0x114FAE/0x115220.

## Player
Fields (P1 base 0x80EE96, P2 0x80EF36):
| off | meaning |
|---|---|
| +0x18 / +0x19 | held / newly-pressed input bytes (see bit layout) |
| +0x1A / +0x1B | speed class / direction (from 0x1278FC by direction nibble) |
| +0x20 | shot power 0..8 (items: +2 at 0x123BFC, max at 0x123CDE) |
| +0x24 | guard-barrier gauge 0..0xFA0; barrier costs 0x400 (0x7D0 with flag bit8); item +0x7D0 |
| +0x28 | shot animation phase |
| +0x30 / +0x32 | current dx/dy from the T2 table |
| +0x34 / +0x35 | alternate / base speed class from the mode record |
| +0x39 | invincibility: 0 none, 0xF0 respawn, 0xFE barrier, 0xFF permanent |
| +0x40 | rapier (focused shot) active |
| +0x44 | bank/tilt accumulator |
| +0x48 / +0x1D | palette bytes (+0x46/+0x47 normal, +0x90/+0x91 awakened) |
| +0x49 | player index |
| +0x4A | **character: 0 Ageha, 1 Tateha** (from 0x81398C / 0x81398E) |
| +0x58 | state id (jump table 0x131C5A: 12 handlers 0x127DB8..); 0x48 barrier, 0x5C death |
| +0x5E | Kakusei transition running (0x114 frames, timer +0x70) |
| +0x5F | guard barrier active |
| +0x66 | **awakened flag: 0 normal, 2 Kakusei** (comes from the mode record) |
| +0x6E | **gem counter 0..500** |
| +0x73/+0x74/+0x7A | level-up accumulator (+1 per frame, +4 while firing, threshold +0x7A) |
| +0x76 | **formation level 0/2/4/6** (options; feeds rank and gem drops) |
| +0x8A / +0x94 | gold sprite index / **gold value** (reset 0 / 4 on Kakusei start and when gems hit 0) |

Mode records 0x12785A -> {Ageha normal 0x12786A, Ageha awakened 0x12787A, Tateha normal
0x12788A, Tateha awakened 0x12789A} = {hitbox x long, hitbox y long, speed class, alt class, 4
bytes, mode word}. Toggle routine 0x124668 loads the record for `char*2 + awakened` and copies
+0x10/+0x14/+0x35/+0x34/+0x2C..+0x2F/+0x66. Speed classes: Ageha 22 normal (3.84 px/f) / 27
awakened (4.72), Tateha 17 (2.97) / 22 (3.84); alt classes 12/10 and 10/14 (focused). Hitbox
normal (+0xC0,-0x80)/(+0x80,-0x80), awakened (+0x100,-0xC0)/(+0xA0,-0xA0) in 1/64 px, +0x10 per
formation level.

Movement: 0x1228E6 (direction -> +0x1B, `jsr 0x1181E2` -> +0x30/+0x32), 0x12418C /
0x1243C8 (apply, clamp, tilt). Shots: 0x126960 handlers; rapier 0x12DBFE; kill reward 0x178140.

Kakusei toggle (0x122A90): B new press (or auto with 0x813A8E bit7), refused during barrier;
starting from normal with gems > 0 calls 0x1488FC (0x813B96 = 1 world switch) and 0x1488DA;
sets +0x70 = 0x114, +0x72 = old mode, +0x8A = 0, +0x94 = 4, +0x5E = 1, 0x124A50, 0x17D398.
Drain: `-1 gem per frame` while +0x66 != 0 (0x1252B6); at 0 gems 0x1488FC/0x1488DA again,
+0x94 = 4 (the awakened state persists with 0 gems = "zesshikai"; bullets no longer slowed because
task 0xA (0x147EA6) only selects T3/T4 while gems != 0).

Guard barrier (0x122A02): C held and +0x24 != 0 and not already active: +0x5F = 1, state 0x48,
+0x24 -= 0x400, +0x68 = 0x200, +0x39 = 0xFE, 0x124D42, 0x124A50; cancel-all-bullets API
0x11B156.. (0x81B556 duration, 0x81B558 mode: -1 vanish, 1 gems, 2 gold).

Death: 0x11C216 marks the player (flag 0x10, 0x80B0FE = 1) unless +0x39 != 0; death sequence
0x1233D0 (rank accumulator 0x8139DA *= 7/16, respawn flags 0x8128FE/0x812902, 0x81290E += 2);
lives 0x8139D6 (P1) / 0x8139D8 (P2) decremented in task 0x13 (0x181EAA / 0x181ED2); respawn
template 0x12263A copied at 0x12275A, +0x39 = 0xF0, gems reset to 250 (0xFA).

## Economy / scoring
| item | address / formula |
|---|---|
| score | long 0x81B57E (P1) / 0x81B598 (P2), units of 10, clamp 0x3B9AC9FF; record 0x81B57C/0x81B596: +0 active, +2 score, +6 last drawn, +0xA continue count bumped by 0x178EFC/0x178F08 (max 9; drawn as the score's last digit, copied into the ranking entry +0x16), +0xC drawn copy, +0xE HUD column, +0x10/+0x14 extend thresholds, +0x18 dip |
| lives | 0x8139D6 / 0x8139D8; initial from decoded dip byte 0x801006 (lives setting) via 0x178E82 {2,3,4,0,1} (observed 2); -1 on death; +1 per extend (max 8); -1 = not playing |
| extends | thresholds from dip 0x801005: 0x178D7E {400000, 800000, 200000, none} / 0x178D8E {1400000, 2000000, 600000, none} (x10 points); checked each frame in 0x177E44; award 0x17D78E |
| gems | player +0x6E, max 500; +2 per gem (0x16F7F4) and +1 score (10 pts) per gem (0x16F7DC); kill drops 0x178140: n = (enemy +0x22 + level/2)/2 [+1 if enemy +2 >= 0x5C00], or level-scaled when enemy +0x23 > 0 (level 0: n/2, 2: n-n/4, 4: n, 6: n+1..2); bosses (+0x23 < 0) instead cost gems: -(25 + 2*stage) or -(5 + 0x8139A6) and convert bullets to gold (0x11B190) |
| Kakusei gold | 0x1705FA: every bullet owned by the killed enemy becomes gold (handler 0x1700DA) worth **player +0x94** (10-pt units); +0x94 starts at 4 and **+4 per converted bullet** while +0x8A < 0x3DE (+0xA each) => max 400 = 4000 points per gold; scored when the gold spawns (+0x16 delay == 0) or after the delay, credited to the killer (0x1780BA by +0x36 bits) |
| cancelled bullets | mode 1 (0x171EB0) -> 0x16FFEE handler: becomes a gem (0x16E702) when +0x38 >= 0, 10 pts/frame while collected flags set; mode -1 (0x17193E) -> vanish |
| rank | 0x8139B6 = base[0x801004 difficulty (forced 1 when 0x801186)][stage 0x81399E] (6-byte tables 0x148110/0x148116/0x14811C/0x148122 = {16,16,24,32,32,0} {48,60,72,72,84,84} {60,76,92,108,108,124} {68,84,100,116,116,132}, pointers 0x148128) + (0x8139DA >> 9, >> 8 while 0x8139FC awakened world) + max_p(2*power + 0x40 if awakened + 0x90 if gems>0) + 2*max_p(level), clamped to 0xF8 (0xFF while the awakened world 0x813A18 is on); 0x8139DA += 1 per frame (0x147D48), *7/16 on death; 0x813B02/0x813B04 bullet speed/class add from 0x148140 {7,13,19,25} / 0x148148 {18,12,6,2} by max level; 0x813B00 = rank class offset added to bullet speed class |
| stage | 0x81399E (0..5), 0x8139A0 = *2, 0x8139A2 = *4, 0x8139A6; scroll position 0x8139E0/0x8139E2 (event list pointer 0x813CE8, 12-byte events {scroll word, word, type byte, script id, long}) |
| enemy score | constant per enemy type passed in d0 to 0x178140 (e.g. 0x516 = 13,020 pts at 0x15151E) |
| enemy HP | object +0x18 (word), damaged at 0x11B4F2/0x11B64A by shot +0x18 (3/4 damage in 2P); initial values set per type by the init routines (tables 0x14F3AE types < 0x80, 0x16D6D8 types >= 0x80; 8 bytes {init, think}); largest immediates 0xC00 (0x1617C6/0x1617FC), 0xA00, 0x900, 0x800 — boss HP is set inside the type routines, not a separate table |
| continue | records 0x81B5E4 (P1) / 0x81B5FC (P2) 0x18 bytes: +0 state 1..4 (handlers 0x1791BA), +2, +4, +6 countdown, +8; digit at 0x81B5EE; 0x8139B2 blocks continue |
| 2-player | 0x813998 |
| counters | 0x81B564 cumulative bullet-frames, 0x81293C/0x81293E gem-type cycling (0x137AAE..), 0x813A1E play frame counter |

## HUD
* Task 0 (0x17E9F4): 0x177E44 draws the score and, one column right of its units, the continue count as text-layer tiles through 0x117726
  (queue at 0x808A9C -> tilemap 0x904000, tile code 0xB0000x + digit<<16, x = 0xD8, y = record
  +0xE (0x900 P1 / 0x1A00 P2); digits split by 0x177F24 powers of ten); 0x17ED06/0x17EF48
  (stage banners, state 0x81E110), 0x17F37C per-player HUD records 0x81E11A/0x81E17E:
  0x17F780 guard gauge bar ((+0x24+25)/50, max 80 px), 0x17F962 gem counter digits (player +0x6E),
  0x17FBF0 second counter via +0x60 pointer, sprites pushed with 0x114AF0 / 0x1713EC.
* Formation icon: 0x1A5BDE picks tile by max level (0x80EF0C/0x80EFAC) when the awakened world is
  off (0x813A18).
* Palettes: sprite palettes copied from ROM 0xC3A78.. to 0xA01000+32*n by 0x137D66.

## ROM data tables (to merge into pgm-data-tables.md)
| addr | size | meaning |
|---|---|---|
| 0x117810 | 0x1D x 8 | task table |
| 0x1182E8, 0x118528 | 64 w | direction -> T1 record offset |
| 0x114FAE, 0x115220 | ptrs | sprite size/zoom code helpers |
| 0x12263A | 0x50 w | player object template |
| 0x1278AA | 4 l | per-character shot pattern tables (0x1278BA, 0x1278C4, 0x1278CE, 0x1278DA) |
| 0x12785A | 4 l | mode records (0x12786A, 0x12787A, 0x12788A, 0x12789A), 0x10 bytes each |
| 0x1278FC | 16 b | direction nibble -> direction byte |
| 0x127960 / 0x12796C | | player sprite offsets / codes |
| 0x127C32 | | Kakusei transition sprites |
| 0x131C1A/0x131C2A/0x131C3A/0x131C4A | 8 w each | direction -> player state (normal / rapier) |
| 0x131C5A | 12 l | player state handlers |
| 0x14F3AE / 0x16D6D8 | 8 b/type | enemy type init/think routines |
| 0x148110..0x148127, 0x148128 | | base rank per difficulty/stage |
| 0x148138 / 0x148140 / 0x148148 | 4 w | level -> rank class / bullet speed adds |
| 0x148088 / 0x148096 / 0x1480A4 | | world-switch palette fade jobs |
| 0x16E078 | 16 l | pool A handlers |
| 0x16E76C | 13 l | gem handlers |
| 0x16FDF2 | | gem type init records |
| 0x170212 | | gold sprite codes by +0x26 |
| 0x126A22 | 16 l | player shot handlers |
| 0x177F24 | 10 l | powers of ten |
| 0x178C72.. (0x178CCE/0x178CE2/0x178D1E..) | | default high scores / names |
| 0x178D7E / 0x178D8E | 4 l | extend thresholds by dip |
| 0x178E82 | 5 w | starting lives by dip |
| 0x11B056 | 64 l | gem scatter velocity table (random index 0x801176) |
| 0x0C2920 / 0x0C29C0 | | message records |
