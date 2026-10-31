# Espgaluda PS2: Arrange mode vs Arcade mode

Findings about how the PS2 ARRANGE mode differs from ARCADE mode, from static analysis of
`SLPS_253.52` and the A0A0 overlays. Addresses only; no original bytes. Sections are appended by
different passes.

## Music

Both modes keep the arcade 68k music request path: the 68k stub `0x17df18` (table at 68k 0x17cee0,
24 entries of 2 x BE16: stream index, secondary command) is called with D0 = music command by the
per-command stubs at 0x17df54..0x17e0f2. On PS2 the Z80 send is replaced by a lookup of a 13-entry u32
stream table (arrange `0xbf6c20`, arcade `0xcdaa10`, see spec/ps2-formats.md "Streamed music path")
and a call to `snd_stream_request(0, id, loop=1, vol=0xdc)` (0x12d5b0). The 68k-side table is identical
in both overlays; only the stream table differs, so the mode decides which ADX of `A980/A980`
(29 streams, stereo 48 kHz ADX, all long tracks with header loop points) is played.

Loop behaviour: every request passes loop=1, which the stream service turns into `ADXT_SetLpFlg(1)`
before `ADXT_StartAfs`, so BGM and jingles loop at the loop points stored in their ADX headers
(e.g. stage-clear 11: 5.4 s -> end, 25: 7.9 s -> end). A new request fades the running stream
(flag 0x10) and starts the new one; cmd 14 maps to the 1-second non-looping clip 9 in both modes,
i.e. "silence". Requests are suppressed while the 68k word at PS2 0x358008 (arrange) / 0x358340
(arcade) is non-zero (attract/sound-off flag; 68k ~0x81dbc0 / ~0x81def8 under the provisional mapping).

| 68k cmd | stub (68k) | situation (evidence) | ARCADE A980 idx (len) | ARRANGE A980 idx (len) |
|---|---|---|---|---|
| 6 | 0x17df54 | title / attract init (0x13f81c clears 0x8135c0.. and loads palettes) | 16 (59 s) | 28 (57 s) |
| 10 | 0x17dfba | stage 1 BGM (stage table 0x18061c[0]; game-start routines 0x15f02a/0x160044/0x121fc6) | 0 (188 s) | 18 (206 s) |
| 12 | 0x17dfce | stage 2 BGM (table[1]; stage-2 event list 0x14b426) | 1 (231 s) | 19 (404 s) |
| 13 | 0x17dfe2 | stage 3 BGM (table[2]; 0x14b696) | 2 (188 s) | 20 (254 s) |
| 15 | 0x17dff6 | stage 4 BGM (table[3]; 0x14ba58) | 5 (214 s) | 21 (205 s) |
| 20 | 0x17e00a | stage 5 BGM, 5-1 and 5-2 (table[4],[5]; 0x14bcc6); PS2 handler only re-requests when stage index == 4 | 6 (216 s) | 22 (311 s) |
| 11 | 0x17dfa6 | boss BGM, stages 1-4 and 5-2 boss (event lists 0x14b1b8/0x14b43a/0x14b6aa/0x14ba6c/0x14be6e; boss object 0x1badb6) | 10 (188 s) | 24 (180 s) |
| 16 | 0x17e032 | stage 5-1 boss (0x14bcd2; object handler 0x1adcce) | 14 (155 s) | 27 (192 s) |
| 8 | 0x17df7e | stage clear jingle (0x180b80, sets flag 0x81e3cc) | 11 (9 s, loops) | 25 (15 s, loops) |
| 18 | 0x17e05c | ending / staff roll (0x183e22 after building 7x28 text entries from 0xd5230) | 13 (229 s) | 26 (270 s) |
| 9 | 0x17df92 | name entry (0x182662 after ranking check 0x178fe2, 33 s timer) | 8 (76 s) | 23 (299 s) |
| 14 | 0x17e070 | sound-test slot after name entry (game over?); never requested by code | 9 (1 s, silent) | 9 (1 s, silent) |
| 19 | (none) | alias of stage 3 (same stream as 13) | 2 | 20 |
| 0-5, 7, 17 | 0x17e084.., 0x17df6a, 0x17e048 | stream index 0 = stop; secondary command 1-7 only | - | - |

(The sound-test list at 68k 0x138a48 orders the commands 6,10,12,13,15,20,11,16,8,18,9,14, matching
the situations above. The stage-5-2 event list 0x14be62 requests cmd 10 with D7=0x55 before its boss;
the D7 value is the secondary parameter and does not affect the stream choice.)

A980 entries 3, 4, 7, 12, 15 (short non-looping stereo clips) and 17 (identical to 9) are not
referenced by either stream table. `A980/A981` (10 short mono clips) is registered as partition 0x981
by the ELF at start-up together with A980, but no code in the ELF or either overlay forms a 0x0981xxxx
stream id and the in-memory ADXT_StartMem*/StartSj entry points are unreferenced, so the A981 voices
are not played through the stream path in either mode.

### Music implementation (ported)

- The arcade sequencer is bypassed: the 68k BGM request routines (0x17DF18 / 0x17DF36) no longer
  upload instrument/song banks; they send the BGM index itself as the song id (`asr #2` of the
  table offset) with the original command types 0x11/0x12.
- The Z80 driver image is extended (our code at Z80 0x6900, the former BGM instrument window)
  and relocated into free program ROM; the driver's song-play / song-stop / song-fade C functions
  (Z80 0x2E47 / 0x2DAA / 0x2DC7) are redirected to a stream player that programs one ICS2115
  voice (voice 15, in the sequencer's range 0-15; the SFX allocator cycles 16-31) with a
  bank-resident looped 8-bit u-law sample. Fade is currently an immediate stop.
- Each Arrange track is converted to mono u-law at the highest rate at which every in-game track
  keeps >= 90 s (others >= 30 s) inside one 1 MB bank; with the 12 MB window this is 11025 Hz with
  76-95 s loops cut at the most self-similar point, the title (57 s) and stage-clear jingle intact.
  All original SFX samples stay in place; the arcade BGM instrument samples are blanked.
- Verified in MAME: stage-1 run of the built set shows the stream starting on the game's BGM
  request and persisting through shot effects, at a loudness close to the original mix.

## Gameplay rules (status)

Source of the rule list: Arika's Arrange page, ja.wikipedia, Impress (see the plan); exact
values come from the PS2 code. Port status: **ported** = applied by `espgalarr/stages/code.py`,
**pending** = waiting for the de-translated Arrange routines (espgalarr/detrans).

Checks: `tools/rules_check.py mame <machine> <rompath>` (tests/lua/rules.lua: start values,
extends, continue, barrier tap/hold, Kakusei) and `tools/rules_check.py ps2 <dir>` (the same
quantities read from PCSX2 EE dumps of PS2 Arrange stage 1 with Chihiro: P1 record 0xC310C0,
lives 0x642292, score record 0xC2B0D0, 68k byte order).

| Rule | PGM anchor (spec/pgm-gameplay.md) | Status |
|---|---|---|
| Extends at 80,000,000 and 280,000,000 | extend tables 0x178D7E / 0x178D8E (10-pt units) | ported (all dip settings except "none"); checked in MAME: a life at 80M and at 280M (arcade 4M/14M) |
| No continues / no mid-game join | continue flag tests 0x1791DE, 0x1795D6, 0x140DA8 | ported (bne->bra); checked in MAME: coin + start after game over does not continue (the arcade continues) |
| New characters Chihiro / Black (speeds, hitboxes, Normal/Power Shot) | character records 0x127832/0x12785A, shot dispatch 0x122CF0, shot routines 0x122D10.., player update 0x12392E | records ported (Chihiro takes Ageha's slot, Black Tateha's): movement matches PCSX2 exactly; shots, volley counts, shot pool and graphics pending (spec/arrange-characters.md) |
| Guard barrier minimum cost 1/3, bullet->gem / gold conversion, auto-barrier full drain | barrier start 0x122A02 (Arrange 0x82B970), gauge +0x24 | start ported (stage `code`, BARRIER_PATCHES): 0x540 per press (arcade 0x400), auto-barrier 0xFA0 (arcade 0x7D0). Checked in MAME against the PS2: D tap costs 0x540 and lasts 137 frames (PS2 4302..4434-4438), held -4/frame after 55 frames, the auto-barrier on a hit in Kakusei empties the gauge. Bullet->gem/gold conversion not measured yet |
| More gems, cheaper Kakusei | kill reward 0x178140, drain 0x1252B6 | kill rewards ported (spec/arrange-scoring.md: extra gems from the hit count, Kakusei 3 + stage / 13 + 2 * stage gems per kill). Same in both: 100 gems and a full gauge at game start, 250 gems at respawn, Kakusei drain 1 gem per 6 frames without kills, Kakusei on B |
| New counter + simultaneous-multiplier counter | gold value 0x1705FA, score 0x178104.. | ported (spec/arrange-scoring.md): scores x10, kill score value * (hits + 10), the counter, its run-down and display, the score popups, gold items' hit-count bonus, the end-of-game bonus x100, 800 item slots; checked against PCSX2 |
| Denser/faster barrages, rank | rank 0x1481A4, bullet speed adds 0x813B00.. ; stage scripts | partly ported: 162 recovered constant changes (main core + per-stage overlays) are patched in place; rewritten script routines pending reassembly |
| Shot / barrier button swap (C <-> D) | input tests 0x1229BE (rapid-fire, D), 0x122A02 (barrier start, C), 0x1316FE (barrier held, C) | ported: the barrier starts and holds on D, the rapid-fire toggle moves to C (Arrange 0x830B20). Checked in MAME: D starts and holds the barrier, C does not |
| HUD counters | 0x17F37C per-player HUD, 0x177E44 score | pending |
| Redrawn player/effect art (78 sprites) | animation records 0x142910.., frame arrays 0x141C16.. (size immediates e.g. 0x141896/0x141A64) | 63 ported, 15 pending |
| Sprite palettes | 0xC3A78.. (0x40 each) | 12 replaced via anchored Arrange copies |

## Recovered constant changes (detrans/port.py)

Near-matched Arrange routines are de-translated and aligned to the arcade twin and to the real
68k routine; immediates that differ are patched in place (build stage `code`).

Held back as RAM-layout changes (they must be applied together with a pool relocation):
- enemy object record 0x24 -> 0x2C bytes (pools 0x815680 x90 and 0x816328 x30);
- player shot pool walk count 0x22 -> 0x62 at 0x1269A6 (the Arrange shot pool has 98 entries
  instead of 34; applying the count alone overruns the pool and stalls the game).

Learned PS2->68k function anchors are wrong where the 68k has near-identical copies of a
routine (sprite layers, two angle routines): detrans/verify.FUNC_FIXES corrects the ones found,
and the porter refuses a unit whose calls differ from the arcade's at the same position.

Applied examples: the barrier-held input test `btst #6` -> `btst #7` at 0x1316FE (Arrange
button layout, applied with the barrier start), bullet/enemy pattern constants in the stage scripts.

## Mode-setup glue and the Arrange RAM layout

The PS2 mode setup (native PS2 code at ELF 0x13ADA0) tests the mode flag and calls one of two
parallel lists of game-start initialisers: the Arcade overlay's when the flag is set, the Arrange
overlay's otherwise. The lists pair positionally (confirmed by function twins). They are the
68k game-start RAM clear split into callable pieces; comparing them gives the Arrange RAM layout:

| area | Arcade | Arrange |
|---|---|---|
| enemy scripts + enemy records from 0x814410 | 0x302E bytes | 0x33EE bytes (+0x3C0 = 120 records x 8: record 0x24 -> 0x2C) |
| object area 0x813D08 (0x144A bytes) | at 0x813D08 | moved to 0x8138A8 (same size) |
| enemy bullets (0x40-byte slots) | 200 slots, 0x3224 bytes | 800 slots, 0xC824 bytes |
| effects 0x81CA5E, explosions 0x81CF60, 0x81D506, pool A 0x81743E | unchanged | unchanged |

The 28 call targets left unmapped by the de-translator are all referenced only from six native
PS2 functions (0x13ADA0, 0x143820, 0x149FE0, 0x1EB390, 0x2661A0, 0x26AFE0): half are Arcade
overlay functions (Arcade mode only, not part of the port), the rest are these Arrange
initialisers or native helpers. Native PS2 functions are excluded from porting.

Hardware consequence: the Arrange bullet pool (about 50 KB) does not fit in the PGM's 128 KB
68k work RAM next to the existing data; it needs extra RAM (candidate: the DDP2 cart's ARM
shared RAM at 0xD00000-0xD0FFFF, 64 KB, unused by the bootleg).

Arrange bullet walkers (PS2 0x8DFCA0 and siblings, twins of 0x170F48 etc.): same group-of-10
slot scan as the arcade, bounded by their own group count and removed-group delta. Only the
Arrange initialiser (PS2 0x8B62C4) writes them: group count 0x4F (800 bullets), delta 0xFFFF.
The arcade stage scripts' bullet caps (0x81B55A = 6..0x13, delta 0x81B562) no longer limit the
walkers in Arrange. Ported as described in STATUS.md (pool in ARM RAM, walker reads redirected).

Sprite lists written by the bullet update 0x17242A: a4 = 0x805A5E (filled from its base every
frame; 0x806B92 while 0x8139B4 is set) and a5 = 0x8051EE (appended); gems use 0x803E9E (gem
pool update 0x16E72E). Arrange sizes: 800, 720 and 800 entries.

## Native PS2 services called by recovered Arrange routines

Excluding native PS2 glue from porting leaves 36 native services that recovered Arrange routines
call (mapping in work/ps2/detrans/native_service_map.json, rebuilt by analysis scripts):

- **Sprite submission (12 wrappers):** reserve an entry on a sprite layer, then a native emitter
  writes it. The PS2 sprite builder (ELF 0x191BB0, Arcade copy 0x93D4B0) references the layer
  lists in the 68k builder's order, which pairs every PS2 list with a 68k layer (validated on
  Arcade: 5/5 known pairs). Emitters map to the 68k helper forms: 0x19B020 = F0 and 0x199090 = F2
  (confirmed), 0x1999F0 = F4 and 0x19B470 = F6 (likely). 10 wrappers map to existing 68k helpers.
- **Arrange sprite layers:** three new layers (160, 128 and 160 entries) and larger lists for
  0x803E9E (100 -> 800 entries), 0x8051EE (180 -> 720) and 0x805A5E (200 -> 800).
- **Other:** sprite/tile bank loaders (no-op on PGM); three wrappers that reduce to known 68k
  routine pairs; mode setup (game-start init); IOP RPC requests (sound effects, likely Z80 SFX);
  per-player routines (input, hypothesis); 8 unidentified, each called by one routine.

RAM budget implied by Arrange (beyond the original layout): bullets 600 x 0x40 = 37.5 KB, sprite
lists ~27 KB (larger lists + 3 new layers), enemy records +960 bytes. About 65 KB in total, which
the PGM's 128 KB work RAM cannot absorb; extra RAM is required.
