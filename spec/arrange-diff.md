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
| Denser/faster barrages, rank | rank 0x1481A4, bullet speed adds 0x813B00.. ; stage scripts | partly ported: 162 recovered constant changes (main core + per-stage overlays) are patched in place; Arrange's rank base tables (240 throughout, so the rank sits at its 0xF8 cap), player template and level 6 at every spawn, and the enemy timing fixes below; rewritten script routines pending reassembly |
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

## Enemy timing (stage `code`)

Measured against PCSX2: Arrange stage 1 with Chihiro, no input after the start, invincible,
with the RNG counter 0x801176 synced at the first scroll (PS2 frame = MAME frame + 1746).
Scroll and stage event timing were already the same; these differences put enemies,
bullets and RNG calls out of step (with all of them the enemy hitbox pools match through
MAME frame 7260, the end of the trace, keyed on the game frame counter; before them they split
at 1756):

- **Enemy removed mid-routine** (asm/m68k/gate_exit_a.s, gate_exit_b.s at 0x15F242 /
  0x1602CE): when 0x15EC28 / 0x15FC5E remove the enemy (0x14F0D8), the PS2 checks a6 after
  the call and leaves the routine; the 68k carried on and moved the next record's enemy.
- **Path end returns to the command loop** (code.py `PATH_END`): path command 10 (0x14F38E)
  drops the interpreter's frame (`addq.w #8,a7`) and jumps to 0x14F0D8, which ends the path
  and points a6 at the driven record. The PS2's handler (A0A9 0x78B8A0: `j 0x786540`, a0 = 0)
  returns to the command loop instead, which goes on reading the path for that record, so
  the record moves once more by the path's velocity (stage 6 play 3546: +0x37). The 68k
  handler now tail-calls 0x14F0D8.
- **Stage 6 boss part offsets** (code.py `MISANCHORED`): the porter's constants for 0x1A0D8C
  (A0A5 0x6FC140, the twelve `addi.l` part offsets) were aligned two entries off at the last
  three; the Arrange code has the arcade's values in the arcade's order (stage 6 play 19173:
  parts 9-11 placed at 7-9's offsets).
- **Hidden `bsr` starts enter once** (code.py `_hidden_start` users): when a unit's routine
  starts with a 68k `bsr` (2 or 4 bytes) just before the anchor, the `jmp` there ran into the
  anchor's own `jmp` and jumped to a broken address (six stage 6 A0A5 units, among them the
  stage 6 boss's 0x6A9A90 at 0x1A3A5A: 68k exception at the boss in invincible runs). Only the
  start is hooked now; a unit whose anchor other code enters keeps the original.
- **Stage 6 boss volley delay** (code.py `TASK_BYTES`, 0x1A2143): the boss task init
  0x1A2112 (A0A5 0x6E10F0) sets $6/$7(a4) with `move.w #$204e`; Arrange stores 0x20 / 0x80
  (A0A5 0x6E1374). With 0x4E the task asked for the boss's next phase early (play 19902).
- **Units anchored one instruction late** (code.py `_hidden_start`): 17 ported units whose
  routine starts one instruction before the anchor (the listing hid the start behind
  misdecoded data) ran that instruction twice (double motion); the jump now goes in one
  instruction earlier. In 16 the first instruction is the same; in one (A0A0 0x6F1930 at
  0x1AF61E, the stage 1 mid-boss) the Arrange routine first calls its own version of the
  68k's callee 0x1B11C2 (enlarged record offsets), so its timers ran down twice per frame.
- **Rank base tables** (`rank_patches`, A0A9 0xBF85D0): 240 throughout, so the rank starts at
  the 0xF8 cap (the arcade's are 16..132).
- **Player template and level** (`template_patches`, asm/m68k/player_level.s): Arrange's
  template differs at +0x50..+0x57 and +0x8F, and every spawn sets the level +0x76 to 6.
- **Turret volley timer** (`turret_patches`, 0x16C7AC): Arrange's stage 1 turret (A0A0
  0x671F50) reloads its volley timer with 0x60 - [0x8139CE] (rank/4) instead of
  0x60 - [0x8139CC] (rank/4 + rank/16). The MIPS function matcher masks lui-loaded addresses,
  so it paired this routine with its arcade twin as identical and the porter never saw the
  change; a scan of every exact pair (lui-loaded RAM addresses and constants mapped through
  the learned maps) finds no other.
- **Hitbox table overruns** (`hitbox_table_patches`): the shared hitbox setup (0x14F118)
  copies count+1 entries, and four tables hold only count: the stage 1 mid-boss (0x1AF3D4,
  12 entries) and the stage 2, 3 and 4 tables at 0x1B1C52 (10), 0x1B54DA (8) and 0x1B7DC4
  (11). The arcade builds the extra hitbox from the code after the table (an active box of
  garbage size). The PS2 copies are followed by zeros (an empty box); each table moves to a
  copy with an empty extra entry. The other setups with a count before the `lea` copy only
  their own entries.
- **Tail call read as an outlined return** (detrans/porter.py `_region_exit`): a label
  between a ported routine's final `jsr` and its `rts` hid the tail call, so the port jumped
  back to the region end and the 68k after it ran as well (A0A0 0x6EB130 at 0x1B0D88: the
  mid-boss moved twice per frame). The only unit this changed.
- **Stage 1 mid-boss** (espgalarr/midboss.py, A0A0): the porter refuses two of its
  changed routines, ported whole with port_roots: 0x6F1B50 (68k 0x1B11CA, damage: the parts'
  hit counts summed into the core record's +0x24, at most 0x40, before each part kill's
  score) and 0x6F8940 (0x1B1596, a wing part's death also cancels part script 4 and starts
  6). Two more (0x6D3300, 0x6F9500) port normally once their callees are mapped
  (verify.FUNC_FIXES: 0x9AD700 = 0x13AD2C, cancel part script; three one-`lui` entries and
  the ELF copy of 0x11B168). The other refused ones differ only by the record growth.
- **Init entries that fall into their run entry** (porter `_dispatched_entry`): the part
  script engine (0x13AB9C) calls one entry per slot and frame, init first, run after; the
  68k init falls into the run code the same frame, the PS2 routine returns (the run starts
  a frame later). A routine reached only through code pointers whose 68k region ends at
  another code pointer keeps its returns (A0A0 0x6DC450 / 0x1B1B18: the mid-boss volley
  timer drew its RNG a frame early; the other units it touches only lose a dead jump).
- **Longer hidden starts** (code.py `_hidden_start`): a `bsr` to the routine the unit calls
  first (six stage 6 units called it twice) and hand-checked multi-instruction starts
  (LEAD_STARTS: A0A5 0x69A600 from 0x1ABB84, which called 0x17D100 twice).
- **Record offsets in kept arcade code** (code.py `kept_record_sites`): the layout stage
  converts the cross-record displacements its logged runs and static pass reach; routines
  left to the arcade code carry more on paths those runs never took. The porter's alignment
  of each such routine with its Arrange twin gives the pairs (register, arcade offset,
  Arrange offset); the build converts each matching displacement the layout stage left as in
  the input ROM (31; one it already converted can equal another field's arcade offset, and
  converting those again stalled stage 5), plus EXTRA_RECORD_SITES for routines the porter
  cannot anchor (0x1B16C2 = A0A0 0x6FA0C0, the mid-boss's exit wrote its flag into another
  record's y). A `movem` displacement sits after the register mask word
  (records.displacement_offsets used to read the mask as the displacement, so the "already
  converted" test never matched and six movem sites were converted twice: 0x18CAAA,
  0x18CAC6, 0x18FC94 and the stage 2 enemy's third part at 0x1B3CD4 / 0x1B3D4C / 0x1B3D52,
  whose part flew off at once instead of holding still).
- **Small enemy animation** (code.py ANIM_START, init 0x1654F4): the PS2 data (both modes,
  A0A9 0xC13400..) starts the record with the animation's first frame and the animation at
  the second entry, so it plays 31 steps instead of the ROM's 32 and the enemy is released 2
  frames sooner.
- **Stage tables** (data stage, diff/stagetables.py): Arrange changes values in the 68k data
  tables embedded in the stage overlays (task timers and bullet counts, angle steps, speed
  tables, formation positions), found code-anchored: paired references of matched stage
  functions, the arcade copy cut to its longest prefix found once in the ROM. 15 tables in
  stages 1 to 6 (e.g. the stage 2 enemy's volley task 0x1B4A72: one volley per burst every 3
  frames instead of two every 5, which ends it and starts the next script 100 play frames
  sooner). Skipped: record-offset-only changes (layout stage) and two stage 6 tables whose
  Arrange bytes are each other's ROM bytes (0x1A7666 / 0x1AB402: the matcher crossed their
  functions). The ending overlay's two paired tables are handled below (staff roll) or are a
  wrong pair (0x0D5540, a palette-like table whose Arrange reference at A0A6 0x650008 is an
  unrelated table of a rewritten routine).
- **Ending staff roll** (code.py ending_patches): the ending (A0A6, loaded after the stage 6
  boss; PCSX2 frame 120039 in an invincible Chihiro run) plays a different script for the
  arcade's player 0x187790 (script at 0x187E4A, set at 0x187776). The arcade shows pictures
  and scrolls Cave's staff list (51 entries); Arrange scrolls 11 lines of the PS2 staff
  ("##################" .. "#############") with the same scroll routine 0x1878C2 and font
  (A0A6 0x6508A0 = A0B0 0x6681E0, the PS2 copy of 0x187B4A), delays 0xE0, 0x200, then 0x100
  each. The script and text move to a copy built from A0A6 (pointers loader-filled in the
  file; text addresses from a PCSX2 RAM dump). The Arrange script has no stop entry: PS2-only
  code (A0A6 0x64EC40, a native timer/fade sequence) takes over 977 frames after the last line
  starts; the copy ends with the arcade's stop entry at that delay, so the PGM then runs the
  arcade's end (0x80 frames, fade). PCSX2 without input: script start 120089, lines at
  +225, +738, then every 257 frames (123141 for line 11), Arrange end state at 124118 (a run
  pressing A every 200 frames left the ending at 122926, before line 11: probably a skip).
- **Stage 2 ring task** (code.py TASK_IMMEDIATES / TASK_SKIPS, init 0x191958 = A0A1 0x652510,
  no porter twin): reload 16 frames instead of 8 and 7 rings of 32 shots instead of 5, and
  the init no longer sets 0x81B55A to 0xE (and 0x81B562 to the difference). The arcade code
  drew its RNG on other frames, which changed the next enemy's random target.
- **Wrong twin pair** (porter NOT_TWINS): the function matcher paired A0A8 0x738BF0 (the
  scroll accumulator clear 0x1173D2) with A0A9 0x7FC3C0 (ratio 0.71, another three-store
  routine: +0xE of a6 and two words), and the port replaced the clear with it. The stage
  setup's clear then kept the 0x20 fraction stage 2's ending scroll left in 0x80704E, so stage
  3's boss scroll (0x20 per frame) stepped a frame early. The other 12 near pairs below 0.9
  that back ported units (0x166E62, 0x1B016E, 0x1B162C, 0x1B4ABE, 0x19C224, 0x19C91E,
  0x19CDC4, 0x1B99C0, 0x1BCCD2, 0x1BCC6A, 0x1A5FF8, 0x1A6108) were checked by hand: each port
  keeps its 68k original's shape (instruction sequence similarity 0.78 to 1.00) and variables;
  the low MIPS ratio comes from Arrange's bullet helpers (0x8E08B0 for 0x1713EC, the ELF copies
  of the angle helpers) and more shots per volley. Guard (code.py `_foreign_twin`): a unit
  whose 68k original uses absolute work-RAM variables and whose port uses only other ones
  (none in common, ARRANGE_OVERRIDES targets excepted) is not installed; it flags the wrong
  pair above and none of the 306 units in use (the build is byte-identical).
- **PS2 constants the PGM ROM does not have** (code.py TASK_IMMEDIATES): both PS2 modes can
  differ from the PGM ROM in a routine's constants (the porter, comparing the two PS2 modes,
  keeps the ROM's then): stage 4's enemy states 1 and 3 (0x1B94F6, 0x1B9628) band their random
  target angle at y 0x4300 / 0x4500 and 0x42C0 / 0x4540 (ROM 0x3C00 / 0x4C00, 0x3A00 / 0x4E00).
  tools/imm_audit.py finds these systematically: every arcade-overlay function is
  de-translated and placed in the listing by instruction shape (constants abstracted, so it
  does not anchor to a sibling that has the PS2's constants), then its immediates are compared
  with the ROM's and the Arrange twin's value is read at the same place. On stage 4 it reports
  exactly the four above. Ported after reading the code (code.py TASK_COMPARES /
  TASK_COMPARE_SPANS, checked against the Arrange MIPS):
  stage 6 run 0x1A5D58 (A0A5 0x6C2E20) wraps its step through the 12 longs at 0x1A5DD2 at
  0x30, not 0x18 (half the table); stage 5 run 0x1BC33E (A0A4 0x64AA50) ends its $132(a6)
  sweep at 0xB4 (its 0x60..0xB4 loop bound; the PS2 computes it as a label distance), not
  0x114. Not ported: stage 6 0x1A5B1C (`move.w #$21,d0`, PS2 0x20) also calls a PS2-only
  wrapper (A0A5 0x6B1330: offsets d2/d3 from $12/$13(a4) via 0x118768) before 0x1713EC, a
  code change rather than a constant; the rest are misplacements (a PS2 dispatch with an extra
  case: 0x1820AC, 0x1A1B7A; another callee: 0x1B1616; swapped sibling routines: 0x16DF2A /
  0x16DF82; `cmp #0 / blt` vs `cmp #1 / blt`: 0x18B92A), or have no Arrange twin (0x147C74,
  0x1268A0, 0x16D17A, 0x14F92E, 0x162126). About 1300 mode-core functions (short or unusual
  ones) find no place, so the audit is not complete for the core.
- **Task inits too short to anchor** (code.py TASK_IMMEDIATES): a 2- or 3-instruction task
  init that falls into its run routine has no ROM anchor of its own, so the port report left
  its Arrange constants "not aligned to ROM". Resolved through the next routine's anchor (the
  old instruction once in the 0x40 bytes before it, the Arrange twin loading the new value):
  stage 3 0x1966A4 (6 -> 1), stage 4 0x19C912 (reload 0x10 -> 0xA), 0x19CBC6, 0x19CC8A,
  0x19CC96.
- **The rest of the "not aligned to ROM" constants** (code.py TASK_IMMEDIATES, TASK_BYTES,
  TASK_QUICK, TASK_MOVEQ, TASK_SKIPS; port report of 2026-10-28, 100 items): placed by aligning
  the arcade and Arrange MIPS of each pair (the arcade twin loads the ROM value at the same
  spot) and searching the old instruction between the nearest anchored routines. 68k code
  often stores two bytes as one `move.w #$XXYY`, which the Arrange MIPS writes byte by byte,
  so some changes are one byte of a word (TASK_BYTES). Real changes: stage 1 task init
  0x18DD40 (3 -> 5, 4 -> 6); stage 3 0x195914 / 0x195918 (`addq #2` -> 1, `addi.b #$20` -> 8)
  and init 0x1967A6 (+$14 0x1E -> 0x32, +$16 0xFFFF -> 0xFFF8, and like the stage 2 ring task
  it no longer sets 0x81B55A, to 0x12); stage 4 0x19C8A2 (4 -> 6), 0x19CCDA (0x1880 ->
  0x2080), 0x19F0BE (0x1007 -> 0x1004), 0x19E8F6 (0x0404 -> 0x0303); stage 6 0x1A550A (7 ->
  5), 0x1A5BDE (0x13 -> 0x1C), 0x1A2B26 (0x10 -> 0x30), 0x1A60D0 (0x8080 -> 0x4040). Mode
  core: the ten `moveq #$58,d0; sub.w $8139CC,d0` bullet timers 0x1592B4 .. 0x15BACE belong
  in order to the A0A8 run routines 0xB12160 .. 0xB3F7B0; Arrange has 0x52 in the first six,
  0x54 in the seventh (0x15ABAA) and keeps 0x58 in the last three. The porter placed four;
  0x1592B4 and 0x15A248 are inits falling into their run, and 0xB17800 anchored on 0xB31050's
  0x15AABE (the two are alike), so its 0x52 overrode 0x15ABAA's 0x54 (the build's one
  "conflicting recovered constants" warning; code.py MISANCHORED drops it, its own timer is
  0x159944). Not changes: 31 record strides (`lea 36` -> 0x2C, `lea 34` -> 0x2A, done by the
  layout stage, e.g. the 15 in 0x19DE9C), 0x195456 `move.w #$18C` (= 11 records, layout
  immediates), 16 code pointers (PS2 addresses), 9 in ELF functions (both modes), and items
  placed since (the short-init pass above; the porter now places 0x1967B2 0xE -> 0x1A). Left out: A0A1 0x65D3C0
  (stage 2, 0x191F68..0x192356) and A0A5 0x6D35C0 (stage 6, 0x1A685E..0x1A6B16) changed
  structurally (Arrange code added; their constants repeat in the routine and do not place
  once), A0A9 0x951230 `move.b #6,55(a6)` -> 0xE, A0A5 0x6E10F0 `move.b #$4E,7(a4)` -> 0x80
  and 0x6C9040 / 0x6D01A0 (no single ROM instruction near the anchors), and A0A9 0xA25860
  (the de-translation is misaligned: `moveq #0` -> 0xFE00FE00).
- **Table addresses learned from another stage** (detrans/tables.py TableMap): the stage
  overlays share their load address, so a confident sample could name another stage's table;
  A0A3 0x708920 (stage 4's explosion table, 0x199B7A) was ported as 0x1B2080 (stage 2 code),
  and the explosion read pieces until a 0xFFFF (226 RNG draws). A mapping whose ROM bytes are
  not the PS2's is now replaced by the table search, or the longest prefix of the PS2 bytes
  found in the ROM (identical copies are interchangeable).
- **Mode-core and hidden stage tables** (diff/stagetables.py CORE_TABLES, EXTRA): four enemy
  init blocks of the mode core that Arrange changed (0x16B1E4 timer reload 0x0303 -> 0x0404,
  0x16ABBE, 0x167756, 0x157BA2; the core's other differing tables are sprite numbers, score
  values and hitbox offsets, not transplanted), and stage 5 task 4's init block 0x1BD862
  (only unmatched code references it) with its run's shot count (code.py TASK_MOVEQ, 8 ->
  0x18).
- **Hit marks** (score.py, spec/arrange-scoring.md): cleared where Arrange's enemy routines
  clear them (with the hit flash) and by the record init, instead of every frame.
- **Lag frames**: MAME drops frames where the 68k overruns (native PGM slowdown, e.g. one at
  frame 3468 of the stage 1 run, 238 in 7260 frames) while PCSX2 does not; comparisons key
  on the game's frame counter (0x801164 / PS2 0x642298).
- **Stage 1 boss target** (midboss.py, A0A0 0x698B20 = 0x18954A): before picking its next
  random target (script 2) Arrange calls 0x189722 (a target near the player), whose RNG draws
  change the target the arcade code then picks. Item spawns store the killed enemy's hit
  count in the item (+0x14, 0x16FD4E; Arrange 0x7CD080; only when the spawner is a hitbox
  record, at most 0x40), which the gold popup indexes its table with (the ported popup spawn
  now zero-extends that offset: with counts other than 0 it read past the table and crashed).
- **Power Shot sparks** (chars.py POWER_HIT_ASM): the Power Shot hit handlers load their hit
  record through ELF 0x205E70, which the porter maps to the arcade's 0x12475A; the Arrange
  copy also sets the spark timer (+0x30 = spread index / 4, reload +0x31 = 7) and widens the
  hit box (+0x10 / +0x14) by the spread offsets. Without it a hitting shot spawned a spark
  (0x17AE52, three RNG draws) every frame instead of every 8th.
- **Power-up drop** (midboss.py CORE_ROOTS, A0A9 0x8829E0 = 0x16E14A): the P item starts at
  speed 9 (arcade 0x0D) with angle 0x18 + random / 4 (arcade 0x18 + random & 0xF; RNG entry
  0x11A48C instead of 0x11A548). The porter has no twin for it.
- **Stage 1 boss arms** (code.py boss_arm_patches, A0A0 0x6A6DD0 = 0x18AE54): the arms'
  volley mode fires 5 shots per burst (arcade 7), so they return to idle sooner.
- **Turret death** (midboss.py TURRET_DEATH, A0A0 0x674C00 = 0x16C89C): after its kill score
  Arrange's stage 1 turret clears the record's hit marks (+0x24 long); the kept marks were
  counted again by a later kill's item drops (the only kill site of the 108 in the Arrange
  overlays followed by such a clear).
- **Stage 1 boss block** (porter ARRANGE_OVERRIDES): Arrange adds a long at +0x1C of the
  block at 0x813D08 (PS2 0xC29B90) and drops the arcade's unused long at +0xC0, so its arm
  entries (0x813D24) and part entries (0x813D50) sit 4 bytes later. The linear address map
  sent the ported part updater (A0A0 0x690590 = 0x18CBBC) to a new variable instead of the
  part entries, which then never moved (the boss's spawned parts aimed at a stale point),
  and another unit (0x6C8930 = 0x18E44A) cleared the arm entry's mode where Arrange clears
  its new long (an override of None makes an address Arrange-only even where the learned
  map claims an exact sample). Only these addresses are remapped: other code shares the area with another
  layout (the collision's words there).

Stage start: the PS2 runs the same intro logic as the arcade (controller 0x15EC28 releasing
the stage-start objects, the player entry) but fast-forwarded while it loads (about 18
logic ticks per vsync with the game counter stalled; 434 RNG draws from the stage setup to
the first scroll on both, 178 mod 256). Nothing to port. The comparisons sync the RNG
counter at the game start (MAME frame 1463, before the stage setup's draws) to the PS2's
value there (0x21: 33 draws during boot, path-dependent), which lines up the RNG-seeded
enemy timers from the start.

Comparison status (work/scratch tools; PCSX2 traces of Arrange stage 1, Chihiro):
- PS2 timing the comparisons have to replay (not Arrange gameplay): the PS2 emulates slowdown
  by freezing whole logic frames, but its frame toggle 0x358048 (= 0x801166: which player's
  collision runs this frame) and counter 0x35804C (= 0x801164, whose bit 1 alternates the
  collision's pickup half) keep counting through them, so after an odd number of frozen frames
  P1's collision runs on the other frames. On the PGM a lag frame advances neither. The MAME
  scripts can replay both per logic frame (shoot.lua / timing_xl.lua CTR_TABLE, TG_TABLE from a
  PCSX2 trace of 0x358048..0x35804F). The PS2's 0x642298 used as the key is a play-time
  counter: it stops at the stage 1 boss's end and through each stage change (it does not
  restart per stage), where MAME's counter runs on and restarts; stage 2 is keyed on play frames (MAME 0x813A1E = PS2 0x642298 - 0x400A).
  The PS2 also shows a freed record as 0x8000 for one frame now and then (ignored).
- shooting (C held, A bursts; inputs by game counter, C at the PS2's counter, A one later),
  replaying the PS2's toggle and counter: positions, shots, marks, score, RNG and the mid-boss
  match to the end of the trace (MAME 7260);
- no input: the enemy hitbox records match through the stage 1 boss and its end (MAME frame
  18360) and, keyed on play frames, all of stage 2 (29667 play frames, to the stage 3 load at
  PS2 frame 50500) all of stage 3 (33878 play frames, to the stage 4 load at PS2 frame
  84778) all of stage 4 (31718 play frames), all of
  stage 5 (17633 play frames) and stage 6 through play frame 20069 (the boss; TODO.md). The MAME script
  selects each stage's PS2 toggle/counter table and start RNG by the stage number 0x81399E
  (timing_xl.lua STAGES "S<n>:table:rng"); tools/ps2_play_table.py builds them from a
  trace. Each key's toggle and counter come from the last PS2 sample of the key before
  (counter + 1, toggle flipped): normally that equals the key's own first sample, but at a
  heavy frame the PS2 counter can stall for a vsync while the logic frame already shows the
  new key (stage 3 play 33739, stage 4 31579, stage 5 17231, stage 6 18982), and a
  first-sample table then hands that frame the counter of the frame before. The game derives
  0x80116A..0x801172 (counter & 3 / & 7 / & 0xF / & 0x1F) from it; with the stale counter
  MAME's effect pool handler (0x179B76) ran its off-screen check (only when counter & 7 is 0)
  one frame early at the stage 4 boss exit, freed an effect the PS2 kept, and the explosion
  that followed found a free effect slot: five spark RNG draws (0x179B14) the PS2 skipped
  with a full pool. Not an Arrange difference: the boss-exit code (A0A3 0x699890 = arcade
  0x19A770, the clear 0x13B3F4 and its explosion table 0x13BA8C) matches the PS2. The 16-bit 0x642298 is total play time: it does not restart per stage (stage 3
  starts at 29667 past stage 2's 0x400A, stage 4 at 33879 past that) and wraps. Stage 3
  needs the PS2's toggle and counter replayed (timing_xl.lua PLAY_TABLE keyed on play frames;
  the invincible player's contact with an enemy runs on P1's collision frames). Stage 2
  needs them too, for the RNG: with them, the RNG matches through stage 2 and into stage 3,
  and stage 3 matches with no RNG set at its start (STAGES "S1:<stage 2 table>:;S2:<stage 3
  table>:"). Without them, MAME used to reach stage 3 20 draws ahead. The cause is the
  stage 2 boss's death at play 29344-29351 (about 50 explosions). Each explosion that
  starts spawns one spark (spawner 0x179B14, five draws) if the 20-slot spark pool
  0x81CA5E has a free slot. A spark's speed byte (+0x1A) is decremented on frames where
  the counter 0x801164 & 1 (or & 3) is 0 (0x179B76), so when sparks leave the screen
  depends on the counter's phase. The PS2's slowdown before 29344 shifts that phase.
  MAME's own counter frees four slots at 29350, the PS2 only at 29354, so MAME's four spawns
  at 29351-29353 draw 20 numbers that the PS2's (pool full) do not. This is not a port
  difference. MAME lag frames end mid
  logic frame: the comparisons keep the last frame of each play counter value. The PS2 trace
  sometimes catches a record mid-update during its slowdown (ignored, one frame).
- player record (shooting run, 0x80EE96 = PS2 0xC310C0): the lean counters +0x52..+0x57 and
  the countdown period +0x8E match (code.LEAN_PATCHES / COUNTDOWN_PERIOD: Arrange's twins are
  ELF routines only its mode calls, so the porter left their constants out). Not ported,
  presentation only: Arrange's player animation (the tail A0A9 0x8316A0 dispatches +0x58
  through a per-character handler table at +0x9A, ELF 0x2D7730 Chihiro / 0x2D7790 Black, whose
  handlers call new generic animators 0x83FC60 / 0x840C90 / 0x841D90 / 0x842F80 on ELF frame
  tables of Arrange's own player sprites, bank A020 numbers about 0xED5..0x1017), so the frame
  timer and index +0x5A / +0x5C (Arrange's idle animation steps every 3 frames over 8 frames,
  the arcade's every 2 over 16), the sprite fields +6..+0xF and +0x9A..+0x9D differ; also +0x29.

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
