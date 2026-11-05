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
| Extends at 80,000,000 and 280,000,000 | extend tables 0x178D7E / 0x178D8E | ported: the stage `code` copies them from Arrange (A0A9 0xB346D0 / 0xB346E0, 80,000,000 / 280,000,000 for every dip setting, same units as the arcade's 4M/14M). The earlier build wrote 8,000,000 / 28,000,000 |
| No continues / no mid-game join | continue tests 0x1791DE, 0x17955C (the continue screen's game-over path, as the PS2), 0x140DA8 | ported (bne->bra); checked against PCSX2 (no-input run without invincibility): same deaths and game-over states frame for frame, no continue screen, back to the attract mode (0x1795D6, patched before, hung at "Continue? 9") |
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
- **Stage 1 object type 0x17 palette slots** (code.py `MISANCHORED`): the porter paired A0A0
  0x6D01A0 (the handler 0x16002A + its run routine 0x160032, entered from table 0x14F3AE) with an
  unrelated arcade function (A0AA 0x6C8870, a byte-copy loop), and its six `move.w #slot,d0`
  constants for the `jsr $117e02` palette loads came out shifted by one (0x15, 0x15, 0x16, 0x14,
  0x13, 0x11, 0x12; slot 0x10 never loaded). Arrange (A0A0 0x6D0228..0x6D0374) and the arcade
  mode (A0AA 0x6CC944) both load slots 0x15, 0x16, 0x14, 0x13, 0x11, 0x12, 0x10 from the same
  seven tables as the ROM (0xC83B8, 0xC8378, 0xC8478, 0xC84B8, 0xC83F8, 0xC3E38, 0xC69F8); only
  slot 0x12's colours (0xC3E38) differ, and the data stage already replaces that palette. The
  six patches are dropped. Its sibling type 0x15 (0x15F010, the stage 1 start, same seven loads)
  was not patched; type 0x17 did not run in the no-input or fire-held runs.
- **Hidden `bsr` starts enter once** (code.py `_hidden_start` users): when a unit's routine
  starts with a 68k `bsr` (2 or 4 bytes) just before the anchor, the `jmp` there ran into the
  anchor's own `jmp` and jumped to a broken address (six stage 6 A0A5 units, among them the
  stage 6 boss's 0x6A9A90 at 0x1A3A5A: 68k exception at the boss in invincible runs). Only the
  start is hooked now; a unit whose anchor other code enters keeps the original.
- **Stage 6 boss volley delay** (code.py `TASK_BYTES`, 0x1A2143): the boss task init
  0x1A2112 (A0A5 0x6E10F0) sets $6/$7(a4) with `move.w #$204e`; Arrange stores 0x20 / 0x80
  (A0A5 0x6E1374). With 0x4E the task asked for the boss's next phase early (play 19902).
- **Byte constants inside word immediates** (code.py `TASK_BYTES` / `TASK_NEG_BYTES`,
  tools/init_audit.py): Arika's code stores 68k immediates byte by byte, so an Arrange change
  to one byte of a `move.w #$XXYY,d(An)` does not align with any 68k instruction for the porter.
  The audit pairs the arcade-mode and Arrange stage functions (mips_funcs.match), places the
  arcade function's constant byte stores in the ROM by their (displacement, byte) sequence
  (record offsets converted), and lists the bytes Arrange changes; 15 more found this way
  (stage 4 0x19EF06, the rest stage 6, among them the boss's two volley sub-task inits). The
  stage 6 boss's volley sub-task init 0x1A53A4 (A0A5 0x6B28FC, unpaired by the matcher) was
  found by hand: reload $7 4 -> 6, $11 0xF8 -> 0xF9 (negated on the mirrored side: 0x08 ->
  0x07), $12 4 -> 0x18.
- **Enemy bullet speed** (asm/m68k/bullet_speed.s): Arrange's bullet set-ups (A0A9 0x8E7B90 and
  the three other allocators' copies) call 0x8AFE40, `addq.w #1,d7`, after adding the rank to the
  speed class; the arcade mode's (A0A8) do not. Every enemy bullet is one speed step faster
  (PCSX2: stage 6 play 34436 bullets at speed 0x15 where the 68k had 0x14). The 68k set-ups
  0x1714BE (0x1713EC / 0x17135E) and 0x171084 (0x170FB8 / 0x170F36) call a stub that adds it.
- **Kakusei extra shots** (porter.py KAKUSEI_HELPERS / SPREAD_ENTRIES / BULLET_ALLOCATORS,
  kakusei.py, data/kakusei_sites.json from tools/kakusei_sites.py): Arrange fires enemy volleys
  through 63 helpers in its mode core (A0A9 0x8B7EE0..0x8E0E30) that call the allocators
  (0x8E7010 = 0x1713EC, 0x8DFCA0 = 0x17135E, 0x8C2230 = 0x170FB8, 0x8E8CA0 = 0x170F36) and,
  while 0x813A18 is 2 (Kakusei with gems: 0x8139FE set, gems 0x813A02 non-zero), fire one more
  shot at speed + 6 (the wrappers 0x8E08B0 / 0x8E0E30 first, the volleys after their shots;
  the three-way spread 0x8B64E0, entered from 0x8E7000 / 0x8DFC90 with its allocator in the
  native argument $a0, at two points). Arrange calls them where the arcade calls an allocator
  directly, so some volleys also differ outside Kakusei (0x8C7520: a second shot at speed + 3;
  0x8E7000: three shots where the call votes had mapped one). The porter maps none of them
  (a ported unit ports those it calls; the spread entries get the spread's source with the
  allocator bound) and maps the allocators to the 68k's. Kept arcade routines: the 68k calls
  aligned one to one with an Arrange helper call (578; 188 others do not align, 104 are not
  placed) call the helpers ported once (porter.port_roots). PCSX2 vs MAME shooting run with
  Kakusei (Triangle = MAME Button 2, two counters after the PS2's press): the bullet pools are
  the same set of bullets (slot order aside) for all 115 counters before Kakusei and 167 in it,
  up to a volley the alignment left out (TODO.md); the build without this port differed from
  counter 0xDD1, before Kakusei.
- **Stage 6 boss launched enemies** (asm/m68k/boss6_launch_timer.s, code.py `TASK_BYTES`
  0x1A6E4C): the boss sub-task 0x1A5B1C fires four bullets that turn into small enemies
  (0x1777FC, type 0x7E, 0x1A6C24) at the bottom of the screen. Arrange (A0A5 0x6B2440, through
  the PS2-only wrapper 0x6B1330, type 0x20) fires them one speed step faster: PCSX2 dumps show
  the same bullets at the same positions and angles with speed 0x15 (+0x1A) where the 68k had
  0x14, run by the edge converter 0x94B210 (= 0x1777FC). The step is Arrange's for every enemy
  bullet ("Enemy bullet speed" above); the arcade's type 0x20 is a spawner bullet, so the 68k
  keeps type 0x21. The enemy's timer sets
  record flag 0xA601 (arcade 0xA201, A0A5 0x69E4D8), and when its lifetime $26 (0x3FC) runs
  out Arrange only fires its burst (A0A5 0x69E178: 0x69D3A0 = j 0x69C950, the twin of
  0x1A6CE0) and lets it fly on, where the 68k self-destructs it (0x1A6D48).
- **Fan start angles and bounds the porter left** (code.py `TASK_SUBIS`, `TASK_COMPARES`
  cmpi.b, `TASK_QUICK` subq, two `TASK_IMMEDIATES`; tools/twin_audit.py): the store audit's
  unplaced and "structural" functions were rechecked by de-translating both twins of every
  changed stage pair and aligning them by shape. All byte-store differences it lists were
  already in the build (patched or inside a ported unit); what was missing are immediates the
  porter does not patch: `subi.w #n,d1` fan starts (the translator emits `addiu -n`), so fans
  whose count and step it changed were off centre — stage 2 0x1B4874 (-0x24) and 0x1B4BEC
  (`subq` 8), stage 4 0x1B9CAA / 0x1B9CEA (-0x62, 15 bullets 0xE apart) and 0x1BA094 (+0x1A),
  stage 5 0x1BE126 / 0x1BE162 / 0x1BE19C (-0x48 / -0x2A / -0x14); stage 2 0x1B4834's first fan
  (15 bullets 9 apart); and `cmpi.b` bounce bounds, stage 6 0x1A5CD0 / 0x1A5CEC (0xAC, 0x2C)
  and stage 3 0x195A3E / 0x195A4C (0x28 / 0x58). The code changes are ported whole (restructured.py,
  "Restructured volleys"): stage 5 0x1BE0C2 (A0A4 0x668CB0; its third and fourth fans
  compute angles but fire nothing on the PS2 either), stage 3 0x195922 (A0A2 0x6981A0, five
  bullets where the arcade fires two, with Arrange's bullet types 0x26 / 0x27); stage 1
  0x1B1596 is midboss.py's; stage 6's effect-object layout is TYPE_7F_LAYOUT.
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
  file; text addresses from a PCSX2 RAM dump). The Arrange script has no stop entry.
- **Ending staff-roll task** (code.py ENDING_TASK): Arrange rewrote the run part of the staff
  task 0xF (0x18794E = A0A6 0x64EE80; its init 0x64EAB0 / end 0x64EA50 are the arcade's
  0x18793C / 0x187922). The arcade waits for the stop entry, 0x80 frames, fades palette 31
  (table 0x1879C0) and the music, then ends the task (message 2: task 0xE "GAME OVER", then
  name entry). Arrange counts frames (0x650C08): from frame 0x3D a new press of any button by
  either player skips; at frame 0xF3C (or on the skip) palette 31 fades with the same table
  (A0A6 0x650890 = ROM 0x1879C0); when the fade ends a skip ends the task (music fade 0x7968A0
  = 0x17D116 at the skip), otherwise the PS2 shows the Arika logo (A0A6 0x64EC40, a PS2
  picture): 0x210 frames scrolling up to the screen centre, 0xF0 held, then the music fade,
  a 0x78-frame fade and the end. The PGM draws the word in the staff font on the same path
  (presentation approximated). PCSX2, no input: script 120089, fade 123990, logo 124118..124648
  (scroll), 124888 (fade), end 125008, then GAME OVER and name entry; a Square press at
  121000 set the skip at 121002 and ended the task at 121130. MAME (invincible run, script
  at 128673): frame counter 4028 when the fade ends as on the PS2, logo scroll 529 frames,
  end 361 frames later (PS2 360 + 1); relative to the script start the MAME lines come 1
  frame later than the PS2's up to line 4 (+997) and 8 later from line 5 (+1261, PS2 +1253):
  7 lag frames in between, also in the build with the arcade task (not an ending change). On
  current builds the gap is 0 or 1 frame (line 5 at +1254 / +1255): the arcade's end-of-scroll
  palette fade, see spec/timing.md "Staff roll". A Button 2 press at +911 ends the ending at +1048 (PS2 +1042).
  The ending's init (0x183B24 = A0A6 0x649890, near 0.97) differs only in PS2-side work
  (clearing its own variables 0x650C00.. and a PS2 buffer, ELF 0x167AA0) and its palette
  sources (same bytes as the ROM's); not ported.
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
  ones) find no place in imm_audit; a constant both PS2 modes share is a PS2-port difference
  (left out, as the ELF constants), so for Arrange changes the twins are audited instead
  (next item).
- **Mode-core constants** (tools/core_audit.py; code.py TASK_IMMEDIATES / TASK_SUBIS /
  TASK_BYTES "mode-core"): an Arrange change in the cores shows as an A0A8 / A0A9 twin pair
  differing in a constant. core_audit takes the 1056 pairs of mips_funcs.match whose code
  differs (176 more are identical) and 123 pairs of functions the matcher leaves unpaired on
  both sides, paired by de-translated shape (mutual best, ratio >= 0.75); of these 1179, 190
  are not translated code and 942 de-translate with no constant change (addresses / record
  layout only). It anchors each remaining arcade twin in the ROM listing
  (verify.compare_function) and checks every changed immediate against a built ROM (86
  changed immediates; on a build with the 0x155D5A .. 0x157BD2 entries below but not yet the
  fan angles: 38 ok, 2 in ported units, 25 missing, 5 changed otherwise, 16 unplaced). Ported
  from it (all reached by the no-input run, MAME breakpoints): 0x155D5A (A0A9 0x9DC1F0, shape
  pair): first volley from -0x28 (-0x1C) with d6 / d7 5 / 0x10 (7 / 8), second volley step 2
  (3) with 7 shots a side (6); 0x15734E (A0A9 0xA43BD0, shape pair): shot speeds -9 (-6; the
  `move.l #$fffa000c,d0` and `move.w #-6,d0`), fire period 10 (7) less the rank; 0x157BD2
  (A0A9 0xA49C10, shape pair): fire period 6 (7); the start angles of five fans whose counts
  and steps the porter already changed (0x168D2A / 0x168D5C -0x1E, 0x16A53A -0x21, 0x16AE48
  -0x1E, 0x16B462 -0x38: centred on the new count x step). Already in the build: the rank-scaled
  fire periods of 0x1592B4.. (`moveq #$58` -> 0x52 / 0x54: the ten A0A9 sites agree with the
  ROM's), the core turret (restructured.py), record strides (0x14F0E4, 0x11B2E2, 0x11B340,
  0x11C04C: the layout stage). Not ported, with the reason:
  - player animation thresholds and timers (0x127EC2, 0x1287AC, 0x128582, 0x128724, 0x123832:
    +0x5C / +0x62 of the player; TODO.md, player animation);
  - 0x17863A, shot damage to an enemy (`5 + stage` -> 3, `0x19 + 0x8139A0` -> 0xD): never
    executed in the build (breakpoints in the no-input and the shooting runs), the Arrange
    characters' ported shots do not use it;
  - 0x13F8EC: a palette slot (`jsr $117e02` d0 0xA -> 3) and a sprite parameter (d4 0xA -> 3):
    presentation; the PS2's palette slots do not map one to one;
  - restructured routines, now ported whole (below, "Rewritten mode-core enemy runs");
  - look-alike siblings paired crosswise (0x14C844 / 0x14D5C6: #$C / #$B; 0x170D34: other RAM
    variables), PS2 representation (0x177EB4: 4-byte table entries for the 68k's 8), the ending
    (0x183244: ending_patches), 0x15ABAA (two Arrange functions anchor on 0x15AABE, see
    MISANCHORED), and A0A9 0xA25860 (misaligned de-translation).
  Unplaced: 16 changed immediates of anchored pairs have no 68k instruction (de-translation
  differs in form, e.g. the turret's `move.b #6,55(a6)`, placed by hand in restructured.py);
  the 683 arcade-core functions the matcher leaves unpaired and the shape pairing cannot pair
  have no Arrange twin with the same shape (rewritten or PS2-only code).
- **Rewritten mode-core enemy runs** (restructured.py ROOTS ("A0A9", "A0A8"), TEXT_FIXES /
  ACCEPTED "A0A9", CORE_MULT_PTRS; code.py MISANCHORED): three enemy run routines Arrange
  rewrote are ported whole, entered at their 68k starts (each a `jsr $14f216` / `moveq #$59`
  routine; core_audit's anchors were mid-routine or crosswise):
  - A0A9 0xADEEF0 = 0x16778A: its fan fires 7 shots (-0xC x 3, then +0x30, +0xC, +0xC) through
    Arrange's shot helper where the arcade fires 5 (the porter's `addi.b #$24` -> 0xF4 at
    0x167970 was a misreading of that change);
  - A0A9 0xAE50C0 = 0x167E7E (the audit's 0x167F9C is inside it; its 0x16848A -> 0xD0 was
    misplaced the same way);
  - A0A9 0x9D64F0 = 0x150D7C (`cmpi.w #$d5`, as the arcade's 0x150D8A): the port report paired
    it with 0x161EB4, whose real twin (A0A9 0x75D030) keeps `cmpi.w #$400`, so the porter's
    0x161EBE -> 0xD5 was a misplacement and is dropped. Its shots go through Arrange's helper
    A0A9 0x8C02F0 (two shots, speed +3, plus the Kakusei shot), which the Kakusei site list
    had missed because of the crosswise pair.
  De-translation fixes, each checked against the PS2 data or the 68k: the guessed tables
  0x16BF60 -> 0x16BF84 (the arcade's draw jump table) and 0x167D80 -> 0x167E24 (an RNG-picked
  table: PS2 0xC129B0's sprites 0x3858.. are the ROM's 0x2E6C18..); ELF 0x1D0960 = 0x11911C
  (its 0x8139A2 mapped as 0x800EFC) and ELF 0x1BB970 = 0x11AA26 (the RNG pick, mis-split at
  its flag code); A0A9 0x9B6810, which takes a5 + $1E and the table PS2 0xC0B2B0 (= ROM
  0x15C852 by sprite_ids.located) natively, inlined as the arcade's 0x15100E; and Arrange's
  second pointer table (PS2 0xC12950, absent from the ROM, whose 0x167D20 is code), rebuilt
  from the arcade's 0x167CE0 by the offset the two first tables share (the 0x813A4E..
  variables, three entries on). A left-over flags line before `jsr $178140` is harmless (the
  callee reads no X).
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
  layout stage, e.g. the 15 in 0x19DE9C; the six in the stage 4 boss child walkers 0x19E7F2 /
  0x19EB88 are listed by hand: nothing in the ROM references those two routines and no stock
  run reaches them), 0x195456 `move.w #$18C` (= 11 records, layout
  immediates), 16 code pointers (PS2 addresses), 9 in ELF functions (both modes), and items
  placed since (the short-init pass above; the porter now places 0x1967B2 0xE -> 0x1A). A0A1 0x65D3C0 (stage 2,
  run 0x1920AA), A0A5 0x6D35C0 (stage 6, run 0x1A688E) and A0A9 0x951230 (core turret init
  0x17795A, `move.w #$106,$36(a6)` low byte 6 -> 0xE) changed structurally: see
  "Restructured volleys" below. A0A5 0x6E10F0 `move.b #$4E,7(a4)` -> 0x80 is TASK_BYTES
  0x1A2143. Left out: A0A0 0x6D01A0's `move.w #0,d0` -> 0x10 (a misalignment: stage 1 object
  type 0x17 palette slots above), and two pairs that are no Arrange change:
  - A0A0 0x6C9040 (`move.b #$18,46(a6)` -> 0x26) is the stage 1 init 0x15FAB2 (arcade A0AA
    0x6C4C10): both store the same bytes (0x26, 0, 0x20, 0, ..., 0x18, 3, 0x20) and Arrange's
    displacements are the arcade's + 8 (the 0x2C record), so its `46(a6)` lines up with the
    arcade's `move.b #$18,46(a6)`. The layout stage already converts the 68k's offsets
    ($26 -> $2E .. $30 -> $38); the built ROM matches Arrange.
  - A0A9 0xA25860 (`moveq #0,d1` -> 0xFE00FE00 and three more) was paired with an unrelated
    arcade function (A0A8 0x9A4AE0, a drawing routine on 0x8136F0; 404 structural
    differences). Its twins are A0A8 0xBA5200 = 68k 0x15373E (and A0A9 0xA1E090 = A0A8
    0xB9DB00 = 0x1530FA), a two-record enemy's damage handler: no immediate differs; Arrange's
    survivor path adds `move.l d4,36(a6) / move.l d4,80(a6)` after the +0x23 clears (the hit
    marks, which score.py's MARK_TRAPS already clear with every such store: the built ROM has
    `trap #14` there), and calls ELF 0x1C4E70, the copy of 0x118B30.
- **Restructured volleys** (restructured.py): Arrange routines that add shots to a volley, so their constants repeat and place nowhere; ported whole with
  porter.port_roots and entered at the 68k run routine (the inits fall into it). Stage 2 ring
  task run 0x1920AA (A0A1 0x65D3C0): both volleys gain shots (the second fires five per side
  at 0x20 steps, speed steps -1 / +4, instead of two at 0x40), the first's speed step between
  its halves is +9 (arcade +6). Stage 6 run 0x1A688E (A0A5 0x6D35C0; its init 0x1A685E keeps
  TASK_BYTES 0x1A6861, the next task's 0x1A6B19 / 0x1A6B4B): speed 0xFFF9 (0xFFFB), shots at
  -4 / +4 of the angle, then on the positive side seven more (+0x4B, +6, +6, +0x43, +8, +8,
  +8); the negative side keeps the arcade's five. Mode-core turret (68k init
  0x17795A, run 0x1779E2; A0A9 0x951230 / 0x952100): fire period 0xE (6); the volley (A0A9
  0x8CF270) is two shots at angle 0x80 -/+ 0xC at speed $3E - 6 (the arcade fires one at
  $3E - 1 and swings $3E by $3A between 0 and -0xC every shot; Arrange does not), plus a
  centre shot at speed + 6 while 0x813A18 is 2. Stage 3 volley task 0x195922 (A0A2 0x6981A0):
  five bullets a side (types 0x18 / 0x26 / 0x27 / 0x26 / 0x18) where the arcade fires two;
  its bullet-limit calls (A0A9 0x8E6C40, 0x4F) keep the arcade's group-count variables and
  values (0x12 / 0xE), and the de-translation's dropped `cmpi.w #1,54(a5)` (A0A2 0x69B248,
  computed without a store) is restored: without it the last volley's 0x1C delay was lost,
  the volley ended early and the boss turned at play 13903. Arrange's bullet types 0x26 / 0x27
  (A0A9 0x95B070 / 0x95E010, run handlers 0x95BF20 / 0x95EEC0, read from the PS2's runtime
  descriptors) are added to a copied descriptor table (BULLET_TYPES; their sprites 0x32F4.. /
  0x3300.. are ROM sprites 0x28D470 / 0x28D620, frame tables 0x1764F0 / 0x176694). So is type
  0x28 (A0A9 0x960FB0, run handler 0x961E60; 41 entries, clamp 0x28), which stage 5 fires (PCSX2
  dumps at PS2 frame 129791 hold 8 of them, an older run 417; the 68k clamped them to 0x27):
  its run-init is the arcade's type 0x0F / 0x22 one (0x175808 / 0x176896) and its 27 sprites
  (atlas 0x33C1..0x33DB, 9 frames of 3, A0A9 0xC057C0) are the ROM's (codes 0x28EF94..,
  frame table 0x175984; the build checks the PS2 table against it), so it needs no new
  graphics. Its run handler draws the bullet zoomed, `ori.l #$50005000` (IGS023 sprite words:
  zoom index 0xA, no grow) where the arcade's types `or` d5 = 0x80008000 (unzoomed); no other
  Arrange bullet does (only A0A9 0x8EA93C uses the same flags).
  Stage 5 fan task 0x1BE0C2 (A0A4 0x668CB0): fans of 19 and 15 angles through Arrange's
  multi-shot helpers (A0A9 0x8C8940 / 0x8C7520: three / two shots at speed +0 / +3 / +6, plus
  the 0x813A18 = 2 extra), the third and fourth loops fire nothing (no call in the MIPS).
  Stage 6 boss spread task run 0x1A5E2C (A0A5
  0x6C3FC0, entered from its ported init A0A5 0x6C3B30): fixed fans where the arcade fires one
  shot at an RNG-drawn angle and speed (two 0x11A0BC draws a volley, which split the RNG at
  play 41305 and so the boss's random targets at 41706). Stage 6 object type 0x7F (68k init
  0x1A7728, run 0x1A7778; A0A5 0x6E01F0): its spawners 0x1A6108 / 0x1A61D2 are ported Arrange
  units writing Arrange's spawn layout (a long $1C, speed -4 and bullet type 0x24, the flag at
  $22, a word at $24, the timer at $20/$21), so restructured.py moves the arcade handler's
  offsets to match (TYPE_7F_LAYOUT; with the arcade offsets the flag read 0 and the object
  slowed down instead of flying on, play 48145). Stage 6 piece type 0x4B (run 0x1A78AC): the
  ported Arrange spawner makes 20 pieces (index $1C) whose targets come from a 24-entry table
  (A0A5 0x72FE90; the arcade's 0x1A7A3C has 16, and the data stage copies in place only what
  fits), so pieces 16..19 read code as targets (play 62549): restructured.py copies the whole
  table to free ROM and points its two `lea $1a7a3c(pc),a0 / nop` readers there (PIECE_TABLE).
- **Stage 6 boss damage chain** (restructured.py BOSS6_DAMAGE): the 68k routine 0x1A3E10..0x1A47C4
  runs one block per boss form (OR the parts' hit flags, subtract their damage from the form's
  HP, on death call the reward routine with the HP gauge value). Arrange splits it into
  functions that tail-call the next (A0A5 0x70B7E0, 0x70D3B0, 0x70EF90, 0x710C20, 0x718200,
  0x71D800, 0x720E30). The address map pairs them one block off (0x70D3B0 with 0x1A402C,
  0x718200 with 0x1A4142, ...), which is where the "HP 0x1388 -> 0x1B58" of the constant audit
  came from: block for block the HP gauge values (0x1388, 0x1B58, 0x11170, 0x1388, 0x1B58,
  0x2328, 0x13880) and death thresholds are the arcade's, and so are the part sets. What
  Arrange adds: while a form lives it clears each part's record words +0x24 / +0x26 (the
  0x2C-record fields Arrange's scoring uses), and when it dies it merges the parts' +0x24
  (summed into part 0's, capped at 0x40) and +0x26 (or) before the reward routine (the ported
  units 0x1A488C ..). All seven functions are ported whole and entered at 0x1A3E10, their
  tail calls pointed at each other (the porter had mapped them to the wrong blocks). The final
  form (A0A5 0x720E30 = 0x1A4666) has one part: it clears part 0's +0x23..+0x27 while it lives
  (no merge), and its death routine (A0A5 0x727320 = 0x1A4CEC) calls 0x112F02 first (its ELF
  copy 0x188940), runs the rest as the arcade does (A0A9 0x805AE0, `j 0x804C40`, is the 68k's
  0x17EC80 = `bra $17ec0c`; verify.FUNC_FIXES), and sets 0x813A8E bit 7 / calls 0x119500 last
  and only while 0x813A40 is 0. (The TODO's "PS2 variable 0xB03078" is not referenced in A0A5:
  the routine's variables are 0x813A46, 0x813A40 and 0x813A8E.) The two mappings also let two
  stage 2 units port (A0A1 0x69CB40 = 0x1912D4, 0x6D51D0 = 0x1B1E96, the second with Arrange's
  added damage call 0x6D52D0). A fire-held invincible run (out_f3) kills the final form at
  frame 150001: part 0's +0x24 stays at most 0x10 (0 in 2079 of the last 3000 frames; the arcade
  block let it grow to 0xDB84), 0x813A46 = 1, 0x813A8E bit 7, the 0x13880 reward, then the stage
  end. Earlier effect of the chain in a fire-held invincible MAME run:
  the parts' +0x24 counters, which the ported Arrange shot code raises on each hit and the
  arcade routine never cleared, now reset every frame while a form lives, and the stage 6 boss
  goes down sooner (stage end at play 0x7DB8 instead of 0x877F); not compared against the PS2
  (no PCSX2 run with the same input: Arrange's characters and inputs differ). The no-input run
  is unchanged (no part dies) apart from 12 more MAME lag frames in the stage 6 boss (the
  ported chain is slower).
- **Stage clear without story text** (restructured.py CLEAR_TEXT): the clear sequence (68k
  0x180C4E, A0A9 0x874EE0) types the stage's story text in step 5 (0x18162A, one character a
  frame, 28 frames for line 0) before its 0x80-frame wait. Arrange's text table (A0A9
  0xC024F0) has no text for any stage (pointers -1) and its routine ends on its first call
  (PCSX2 watch of the step byte, PS2 0xC39210: step 4 to 6 in one frame), so the clear lasts
  273 logic frames after stage 1, as on the PS2, not 300. The 68k table's stages 1..4 point to a
  one-character blank line. (The PS2 then loads the next stage from disc for about 170 frames
  with its counters stopped; the PGM has no load.) Arrange's first fade script of the clear
  (A0A9 0xC01ED0) also drops palette 0x0080; not ported: on the 68k it shortens step 1 by a
  frame, which the PS2 (its own fade code) does not. Arrange's general bullet helper A0A9 0x8E08B0
  (ported as 0x1713EC) has the same extra shot while 0x813A18 is 2; the port does not add it
  (open, TODO.md).
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

Kakusei slow motion: the same on the PS2 as in the arcade, nothing to port. While 0x813A18 is 2
(Kakusei with gems) the level setter (arcade 0x147FC8; Arrange ELF 0x232D20 -> 0x231840, called
where arcade mode calls A0A8 0x809470) copies the frame toggle (0x801166; PS2 0x358048) into
the gate 0x8139FA every frame, and the play counter update (arcade 0x147C90; Arrange ELF
0x22FD50, the gate at PS2 0x6422B6) and the enemy routines that test the gate skip every other
frame; bullets still move every frame. Arrange's copy of this RAM block is at PS2 0x6422xx
(68k address - 0x1D1744: 0x813A1E = 0x6422DA, 0x813A18 = 0x6422D4). PCSX2 (stage 1, Triangle at
frame 6000): from Kakusei (frame 6016) the gate alternates and 0x813A1E advances every other
frame. The PS2's "play time" 0x642298 is the low word of 0x8139DA, a timer that runs every
frame in both (only the pause flag 0x8139F4 stops it; a death scales it to 7/16), so per-stage
comparisons in Kakusei key on it (MAME 0x8139DC minus its offset to 0x813A1E at the stage's
first play frame): stage 2 in Kakusei (PCSX2 ps2k_s2, Kakusei from play 0x7DC to the stage end)
matches the PS2's enemy records at all 57006 timer frames but 49 single-frame samples (the PS2's
frozen frames). MAME's further drop to 0.2..0.4 play frames per frame under the Kakusei
bullet load is native PGM lag (lag frames run no logic), item 8 of TODO.md.

Kakusei end: when the gems run out (the options' Kakusei state 0x13182C = A0A9 0x99FEA0 drains
P1 +0x68 by 0x28 a tick), the options are rebuilt (0x124F2E = ELF 0x20C380, layouts 0x12F722 =
PS2 0xC07E70) with a type 0x14 burst object (0x1318C8 = A0A9 0x9A0DE0) that steps a size table
and writes each step's last word into the options entry's +0x0A / +0x0C, its bullet-cancelling
hitbox (the Arrange collision cancels bullets inside it). Arrange's size tables for option
levels 0..3 (PS2 0xC085D0 / 0xC08810, the arcade's 0x12FE50 / 0x130090) have that word zero in
all 25 steps (the sprite and motion fields are the arcade's): its burst cancels no bullets;
level 4's (0x130AB0) is the arcade's. kakusei.KAKUSEI_END_SIZES copies the words from A0A9 at
build time (the Kakusei test's split at counter 0xFA4: MAME cancelled a bullet the PS2 keeps).

Stage start: the PS2 runs the same intro logic as the arcade (controller 0x15EC28 releasing
the stage-start objects, the player entry) but fast-forwarded while it loads (about 18
logic ticks per vsync with the game counter stalled; 434 RNG draws from the stage setup to
the first scroll on both, 178 mod 256). Nothing to port. The comparisons sync the RNG
counter at the game start (MAME frame 1463, before the stage setup's draws) to the PS2's
value there (0x21: 33 draws during boot, path-dependent), which lines up the RNG-seeded
enemy timers from the start.

Bullet comparison (tools/bullets): the enemy bullet pools of the no-input run, PS2 0xC39A20 and
MAME 0xD00000 (800 slots of 0x40), at 70 samples (bsamples.py: every 1000..4000 play frames per
stage; PCSX2 RAM dumps, ps2bul.sh; MAME dumps at the same play frames, mbul.sh: stage 1 keyed on
the counter replay, play + 0x3E5, stages 2..6 on play frames). bstage.py compares bullets as
multisets of (x, y, speed, angle), slot order ignored; btypes.py counts them per bullet type
(typemap.py: run handler -> type, the PS2's from the runtime descriptors, whose handler fields
the PS2 fills at run time, and its run-inits' installed $22; the 68k's from each descriptor's
run-init); bparent.py groups them by emitter ($2A); bosstask.py / subtasks.py / shotcalls.py
name the emitting task and compare its shot calls. On 2e8cdc4: 18 of 70 samples identical; the
rank term (0x813B00 / PS2 0x3586F0, the gp-0x7600 word) is 0 on both, the speed tables and the
descriptors match, and no bullet differs only in speed at the same place. The gaps come from
emitters that call Arrange's shot helpers or fire more shots (TODO.md item 2 lists them); the
largest is the type 0x20 spawner's three children per tick (type 0x0D), then type 0x28 (clamped
to 0x27 on the 68k). Types 0x02 / 0x03 share a descriptor, and handler 0x170020 (68k) = 0x8B0850
(PS2) is the cancelled-bullet state: not differences.

Stage sub-tasks (the volleys of bosses and mid-bosses fire from them, with the boss record as
emitter): each stage, and each boss, installs a sub-task table (68k pointer 0x812D88, PS2
0xC475B4, big-endian MIPS addresses in EE RAM), index for index the same on both. Byte 1 of a
slot is not the table index. Comparing each entry's shot calls (68k: bullet library calls
reachable from the entry; PS2: helper calls of the split function) found the Arrange volleys
ported whole in restructured.py ROOTS: stage 1 0x01 / 0x0B, stage 4 0x0B / 0x29, stage 6 0x03 /
0x19 / 0x21, and the stage 2 boss part run 0x192E3C (pointer table 0x14F712; A0A1 0x6731B0, a
5-way type 0x0B fan where the arcade fires one shot, the PS2's other half of the pattern at
plays 16000 / 21000). On a204896 plus these: 32 of 70 samples identical (23 before).
The dispatcher (0x13AC5C) indexes the table by the slot's type byte x 8, + 4 once the slot is
started (bit 0 of byte 0): each pair of entries is a type's init and run. Table 0x1B6D90 (stage 3,
PS2 0x6CBA60) type 0x02's run 0x1B72A8 = A0A2 0x6B08D0 is ported too: per offset of its table
(PS2 0x6CBAC0, the same four pointers to the same 0x80-byte blocks as the arcade's 0x1B7398) two
type 0x15 shots at the angle -/+ 0x30 and the centre type 0x0B one speed step (+3) faster, where
the arcade fires the centre alone. 0x1B738A branches into its body (`bcc $1b7326`, its rts),
which stays the arcade's: a root replaces only the entry. The no-input run never starts it
(MAME: the table is current at frames 67776..69162, slots of types 0x00 / 0x01 only).
typemap.json's m68k side lacks the run handlers of the ported bullet types (the build's "Arrange
bullet types 0x26..0x28" block, its addresses moving per build): bparent.py / btypes.py drop
those bullets from their counts (stage 3 play 10000: 33 of record 26's), so count by raw $22 /
$2A before reading a gap as missing shots.
The stage 1 boss's first visit (plays 6000 / 7000, record 90 running the core 0x188A56) fires
from its phase table 0x18AA44 (0x18ACFE / 0x18AE54 / 0x18AEF6 / 0x18B0B8), not from sub-tasks.
Phase 3 (0x18AEF6 = A0A0 0x6A7E20) fires its type 0x0E groups through a local wrapper (A0A0
0x6A7C40: the arcade's angle window 0x38..0xC8 around the Kakusei helper A0A9 0x8C9CE0) and
each of its two type 6 shots as a 3-way spread (A0A9 0x8DB320). The call votes had mapped the
wrapper to 0x17135E (porter.INLINED_WRAPPERS now keeps it unmapped), and kakusei_sites.json
had paired the type 0x0E call 0x18AFA4 with the spread helper, so MAME fired spreads there.
Ported whole: plays 6000 .. 13000 and 15000 / 16000 identical (6000 / 7000 had none in common).
MAME's samples have to be the finished logic step (timing_xl2.lua DUMP_LAST=1, the last frame
with that play value): when the 68k lags a step spans two frames, and a dump on its first frame
can hold half a volley (stage 4 play 16000: type 0x73's 96-bullet ring cut at angle 0x50, all of
it unmoved). Three gaps with no extra direct helper calls:
- Stage 2's small enemy (run 0x192C58, A0A1 0x674E70) fires its type 0x0D pair at any angle and
  x, from an offset along the shot (table 0x15CE52); the arcade only for angles 0x20..0xE0 and
  x < 0x5A00, from the enemy itself. The MIPS has none of those compares (the bullets outside the
  window are on the PS2 only: 11 / 45 / 43 at plays 23500 / 26000 / 28500). Replaced in place
  (restructured.STAGE2_SMALL_FIRE: its death path calls unported score code).
- Stage 1's type 0x66 enemy (run 0x18E59C, A0A0 0x6753B0; records 12..22 at play 14000) fires
  every shot of its rings (12 of type 0x0A, step 0x15, then 6 of type 2, step 0x2A); the arcade
  skips the angles outside 0x10..0xF0 (`cmpi.b #$10,d1` / `cmpi.b #$f0,d1` at 0x18E62E). The
  MIPS loop (A0A0 0x675C00) has no compare, and the 20 bullets on the PS2 only are exactly those
  angles (0x00..0x0A, 0xF1..0xFE). The test is removed in place (restructured.STAGE1_RING_WINDOW;
  the routine's death path calls unported score code). Stage 1's samples were dumped on the
  finished step too (a DUMP_LAST copy of the stage 1 tap): all 11 are identical.
- Stage 4 enemy type 0x74 (run 0x19D312, A0A3 0x6C2790): a 9-shot type 0x0D fan (from -0x2C,
  step 0xB, offsets from 0x15CD52) where the arcade fires one shot; its death path's extra call
  A0A9 0x9B8400 (native: three calls into 0x9B7E60..) is left out. The bullets whose emitter is
  not a record (PS2 0x7B9C, MAME 0x838C) are the same stream from freed records.
- The stage 6 mid-boss (type 0x46, sub-task table 0x1AC48E = PS2 0x72BBB0) fires its sub-tasks
  0x09 / 0x01 (A0A5 0x65A920 / 0x651720) through A0A5 0x6505A0, a trampoline (`jr $a0`) called
  with a Kakusei helper's address: pairs of type 0x19 (speed -2) and 0x0F (+2) shots for the
  arcade's single 0x0F ones, and 5 x 3 type 0x0D fans. The porter now ports a trampoline call
  with a constant $a0 as a call of that function (porter.is_trampoline). 0x651720's tables are
  the arcade's (0x1AC8C8 record offsets, 0x1AC8B8 counts / lists: the PS2's lists are their
  first entries) and its end routine A0A5 0x64EE10 is the arcade's 0x1AC594.
- Mode-core runs the Kakusei sites never reached: in several runs the built ROM keeps the
  arcade's plain allocator calls (0x1713EC / 0x170FB8) where Arrange calls its multi-shot helpers,
  the calls not lining up one to one (an arcade loop, or two helper calls for one). Ported whole:
  types 0x3B / 0x29 / 0x26 (A0A9 0xA15D10 / 0xA054A0 / 0xA0DFD0 = 0x154306 / 0x152A86 / 0x153CD6),
  0x30 (0xAF8190 = 0x159216), 0x2C (0xA40CD0 = 0x1559D6), 0x39 (0x75D030 = 0x161EB4), 0x2F
  (0x74E5E0 = 0x16137A) and 0xA1 (0xAB7330 = 0x16AC34). Their death routines are the arcade's
  (0x1542B4 / 0x152A34 / 0x153C84 / 0x1591CA, entered by `bmi`): Arrange's are the same code with
  the hit-mark merge, which score.py hooks there, and their tables match the PS2's to the 0xFFFF
  end (0x15482A = PS2 0xC0F940 ..). Sprite lookups through A0A9 0x9B6810 with PS2 0xC0CD30 are
  ROM 0x15E352 (sprite_ids.located, all 32), inlined; type 0x2C's death table is 0x155BB0 (the
  map guessed 0x155BAE). The stage 3 boss's sub-task 0x0A (0x1974E6 = A0A2 0x6617D0, the same
  code) fires through the state table 0x197556 (PS2 0x6CABD0, init / fire pairs): the inits are
  the arcade's, the four fire routines (A0A2 0x6629F0 / 0x6652B0 / 0x6680D0 / 0x66C2B0) fire 8 /
  8 / 13 / 16 helper calls for 3 / 3 / 8 / 8 and are ported at their entries.
- Not ported: the stage 5 boss's sub-task 3 run (A0A4 0x65CCC0 = 0x1BD028; it calls A0A9
  0x9B1E90, the sub-task clear by type = 0x13B026, and the wrapper 0x655A60 with its angle in
  $a0) splits stage 5 at play 9457 (records 94 / 101 without the PS2's flag bits 0x2400). Mapping
  0x9B1E90 in verify.FUNC_FIXES let the automatic port take A0A4 0x64EAF0, whose inlined limit
  setter writes the build's 0x818352 / 0x818354 (the walkers' count) instead of the arcade's
  0x81B55A / 0x81B562: a stage 5 split at play 7005. The de-translator's labels past an inlined
  helper (L_01f...) are made per function (restructured._own_labels).

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
  stage 5 (17633 play frames) and all of stage 6 (63466 play frames). The MAME script
  selects each stage's PS2 toggle/counter table and start RNG by the stage number 0x81399E (stage 2's
  table from the PS2 traces of frames 20300..62000 joined, ps2_play_table.py MIN_STUCK 10: without
  it stage 2 runs on MAME's own stage 1 counter, whose phase depends on the clear's length)
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
  ELF routines only its mode calls, so the porter left their constants out). Arrange's player
  animation is ported (espgalarr/anim.py, spec/arrange-characters.md "Player animation"): in
  the shooting run the state +0x58, frame timer and index +0x5A / +0x5C, offsets +6..+9 and
  size +0x0E match the PS2 frame for frame from the first state change (1960 of 1960
  animation steps; before it the idle loop's phase differs by the PS2's stage load), and the
  sprite +0x0A pairs one to one with the PS2's A020 numbers (30 sprites). +0x9A holds the
  handler table (a 68k address, not the PS2's). Still differing, not animation: +0x1's 0x10
  flicker bit and +0x29 (constant phase offsets), +0x0, +0x19, +0x36, +0x98 (as before).

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
