# Timing: slowdown on the PS2, in MAME and on the PGM

How fast the game logic runs against the video frames, measured in the no-input run of the
comparisons (tools/compare, the stage 2..6 run of `timing_xl2.lua`) and the Kakusei shooting
test. A frame "lags" when the logic does not step in it.

## How it is measured

- PS2 (PCSX2 traces, tools/compare/ps2_traces.sh): the game counter 0x35804C (watch column
  0x358048:8, bytes 4..5) and play time 0x642298 advance once per logic step. A lag frame repeats
  both.
- MAME: the game counter 0x801164 (stage 1, the counter replay key) and play time 0x813A1E
  (stages 2..6) advance once per logic step. Long holds (stage clear, loading, runs over 30 frames)
  are left out on both sides.
- 68k profile: MAME `-debug -debugger none` with `tests/lua/proftrace.lua` as EXTRA_LUA of
  `timing_xl2.lua` or `shoot.lua` / `shoot_k.lua`. PROF_DIR / PROF_WINDOWS "from:count" (frames) /
  PROF_PLAYS "stage:play:count" (stage 0x81399E, play hex). It writes a cycle-stamped
  instruction trace per window (about 1 MB per frame; debug mode runs at about 340%).
  `tools/prof68k.py TRACE OUT/p_alloc.txt` prints cycles per frame, per owner (the build's added
  blocks, from `p_alloc.txt`, which the build writes) and per routine (exclusive and inclusive, the
  trace's own call targets).
- Exactness of a change that should not alter the output: `tests/lua/splist.lua` hashes the
  finished sprite lists at every SPL_EVERY-th play frame (at the builder's DMA-on write), so two
  builds can be compared by play frame whatever their lag.

## The PS2

The PS2 slows down too, but rarely and always one frame at a time. Its logic skips single frames
in fixed patterns: every second frame at stage 4 around PCSX2 frame 97026, every fifth frame in
stage 6 from 138779. In the Kakusei test (ps2kak4, frames 5900..6700) it steps every frame.

| stage | PS2 lag frames | MAME out_m25 (main 3a37ce6) | MAME with the run builder |
|---|---|---|---|
| 1 | 100 of 10880 (0.9%) | 436 (3.6%); counter key: 6837 to frame 48250 | 358 (2.9%); counter key: 5946 |
| 2 | 175 of 29839 (0.6%) | 12014 of 41958 (28.6%) | 9176 of 38898 (23.6%) |
| 3 | 161 of 22777 (0.7%) | 14065 of 48214 (29.2%) | 10281 of 44430 (23.1%) |
| 4 | 568 of 32285 (1.8%) | 15625 of 47626 (32.8%) | 13598 of 45599 (29.8%) |
| 5 | 188 of 17820 (1.1%) | 5924 of 23558 (25.1%) | 4819 of 22453 (21.5%) |
| 6 | 600 of 64064 (0.9%) | 12193 of 77728 (15.7%) | 11121 of 76656 (14.5%) |

(lagcmp in work/scratch; the PS2 windows are those of fullcmp's stage comparison. Stage 1's
frame window is fixed, so the counter-key totals are the better figure there.)

## Played run: both shots held, stages 1-6 (TODO 8b)

The both-shots trace (ps2both_full: Chihiro, invincible, Square + Circle held from PCSX2 frame
3500 to 95000, stage 1 to the stage 6 boss's final form) replayed on out_m152 with
tools/compare/bothrun.sh, at MAME's 20 MHz and with the 68k clock scaled (Lua `clock_scale`;
the MiSTer core runs its 68k at 40 MHz, and its attract cycle measured as MAME at about
1.8-2x). Logic steps per stage are the same on every run (the replay is keyed on play time);
lag counts frames that repeat a step, holds over 30 frames left out (none occur):

| stage | PS2 lag | steps/s | MAME 20 MHz lag | steps/s | 1.8x lag | steps/s | 2x lag | steps/s |
|---|---|---|---|---|---|---|---|---|
| 1 | 1.8% | 58.8 | 10.2% | 53.1 | 1.7% | 58.2 | 1.6% | 58.2 |
| 2 | 1.7% | 58.9 | 31.1% | 40.8 | 3.0% | 57.4 | 2.5% | 57.7 |
| 3 | 1.2% | 59.2 | 31.6% | 40.5 | 1.7% | 58.1 | 1.4% | 58.4 |
| 4 | 1.4% | 59.1 | 34.9% | 38.5 | 3.6% | 57.0 | 2.9% | 57.5 |
| 5 | 1.7% | 58.9 | 40.6% | 35.2 | 5.5% | 55.9 | 3.6% | 57.1 |
| 6 | 1.2% | 59.2 | 39.1% | 36.0 | 8.6% | 54.1 | 4.9% | 56.3 |

(PS2 at 59.94 Hz, PGM at 59.17 Hz: 1.3% of the gap is the refresh rate.) Under fire a 20 MHz
68k (MAME, a real PGM) runs the game at 60-70% of the PS2's speed from stage 2 on, more than in
the no-input run above: that is the arcade-like pace players notice, since every bullet moves
per logic step. The bullets themselves match: the both-shots replay's enemy records equal the
PS2's step for step through stage 5, and the no-input bullet samples are identical. With the
68k at 1.8-2x the port is within 1-9% of the PS2 (the MiSTer core's case); stage 6 is the
heaviest. Nothing else in the port changes the pace; the remaining lag is the 68k's cycles
(see below).

## MAME's lag

MAME's lag is the 68k missing vblanks. The main loop (0x112912) runs the frame's logic, then the
sprite builder (0x113D7A), then waits in 0x112B8C for the vblank IRQ to clear 0x8011C0. A step
that takes a little more than one frame (338k cycles at 20 MHz, 59.17 Hz) misses its vblank and
waits a whole one. So the long boss sections run at exactly 30 Hz with about 40% of the cycles
idle. The stage 2 boss (play 29k..41k) is an example: about 373k cycles per step against 338k.

0x112B8C also sets a forced wait: 0x8011C0 = 0x8011A4 vblanks while 0x816764 is set. 0x8011A4 is
set by stage events and bosses (0x112EC2 = 2, 0x112ED2 = 3, 0x112EE2 = 4, cleared by 0x112F02). One
example is the slow motion after a boss explodes: the shake routine 0x149DCC, whose Arrange twin
A0A9 0x7E1E50 writes the PS2's own wait variables 0xB03078 = 2 / 0xB03074 instead. The code after
`bra.w $112c46` at 0x112BCE (a bullet-count slowdown table at 0x112C78, 0x8011A0) is never
reached. Kakusei sets no wait (0x8011A4 is 0 in the Kakusei test). MAME's Kakusei half rate (0.45
steps per frame in the test from frame 4800) is plain overload: Kakusei adds Arrange collision's
conversion pass (0x20AE7A, 4-7%), Chihiro's Kakusei shots and the bullet runs.

Profile of the heavy sections (out_s8 = out_m23, 40-frame windows; share of all cycles):

| routine | stage 2 boss | stage 3 | stage 4 boss | stage 6 boss | notes |
|---|---|---|---|---|---|
| 0x112B8C vblank wait (idle) | 42% | 32% | 23% | 21% | |
| bullet run handlers (0x1740B4..0x177xxx) | 13% | 21% | 25% | 21% | arcade code, Arrange's bullets |
| 0x17242A pool update (0x173086 loop) | 7% | 11% | 11% | 13% | arcade, 800 slots unrolled |
| sprite_builder.s (ours) | 11% | 15% | 17% | 17% | before the run builder |
| Arrange collision (ours, ported) | 3% | 4% | 4% | 5% | 800-slot scans |
| allocators 0x1713EC / 0x17135E | 3% | 4% | 6% | 2% | first-free scan from slot 0 |
| ported Arrange routines, bullet helpers | <1% | <1% | <1% | <1% | |

Apart from the sprite builder, the load is the arcade's own code doing Arrange's work: more and
faster bullets in the 800-slot pool (the arcade engine runs every slot each frame and scans for a
free one from slot 0 on each shot). The PS2 runs the same translated code on a much faster CPU.
The ported routines cost no more than the arcade code they replace (main's type 0x3E run,
0x15734E: about 5k cycles per frame against the arcade's 9k). Its extra stage 6 lag on out_m25
(+1716 frames, the stage's first 9000 frames) comes from the bullets it fires: the type at
0x175220 doubles to 55k cycles per frame and the allocator to 21k.

The sprite builder (asm/m68k/sprite_builder.s, replacing 0x113D7A) builds every sprite (up to
1656 with the continuation list of the `espgalarr` MAME config); the arcade's capped the list at
251. It now copies runs of entries with no per-entry slot or cadence checks: about 146 cycles per
sprite instead of 238. Its lists are identical (24517 hashes over stages 2..6, 7959 of them with
continuation entries). The comparisons still match, and the Kakusei test still matches to counter
0xF4A. The split at 0xF4B stays the same with the new lag, so it is not a slowdown alignment: four
bullets of the same type, speed and angle at another position. It came from the inputs: the
PS2 run presses Cross (D, the barrier) at frame 6300 and the MAME events did not (P1 Button 4 is
in the PGM's Service port); with the press the bullets match to 0xF69 (TODO.md item 7). The press
has to land at counter 0xF48, one before the Triangle-style keying: at 0xF49 the barrier is then
in effect on both machines, while one counter later MAME first fires a 4-shot Power Shot volley
(chars.py, aim jitter through the RNG entry 0x11A0D8), four extra draws that change record 90's
random turn target (0x1B0DF2). With the press there and the Kakusei-end burst's sizes
(kakusei.KAKUSEI_END_SIZES) the bullets match over the whole logged window (now 652 counters,
0xDB7 to 0x1042, none differing; since the select-to-stage change the test keys its replay on the
play timer, shoot.lua KEY_TIMER, as the long runs do: the logic-frame key ran a step behind).

## Staff roll

Measured with `tests/lua/staffroll.lua` on the invincible fire-held run (work/scratch long.sh's
inputs) to the ending, a state saved at the script start, `tests/lua/proftrace.lua` over the
frames around the lag, and `tools/prof_passes.py` (cycles per main-loop pass). Line starts
relative to the script start (each delay takes its count + 1 frames):

| line | out_m25 (3a37ce6) | out_k5 (= 9a0e896) | PS2 (no input) |
|---|---|---|---|
| 4 | +997 | +997 | +996 |
| 5 | +1254 | +1255 (lag frame at +1005) | +1253 |
| 6, 7 | +1511, +1768 | +1512, +1769 | |

No forced wait (0x8011A4 / 0x816764 stay 0). The lag frame is one main-loop pass of 337870
cycles (normal passes 52k..70k): the ending's routine (0x183DDE) starts its end-of-scroll palette
fade (script 0x183FB0: 13 palettes of 32 colours) when the picture scroll reaches 0x7000, and the
fade service (0x11F290 -> 0x11F2E2) steps all 416 colours in one frame, about 270k cycles. Passes
alternate by about 15k: the arcade's ending draws a 17-sprite group (0x186CFE -> 0x114DCE) on
every other logic step, with more sprite-builder work. On out_m25 the fade step fell on a light
step (323910 cycles, fits); on out_k5 on a heavy one (overrun by ~2k cycles with the vblank IRQ),
so it costs 0 or 1 frame depending on the logic toggle's phase at the ending, set by the run's
whole history. All of that is arcade code (our share of the pass: the sprite builder, ~16k), so a
PGM running the arcade code lags the same way; the PS2's faster CPU does not. The earlier "7 lag
frames" (before e7dc3bc) do not reproduce on either build measured here.

## Real hardware

MAME runs the PGM's 68000 at 20 MHz (pgm.cpp: "verified on real board") with no wait states, and
the screen at 59.17 Hz. A real PGM is therefore at most as fast as MAME, and slower if its ROM,
RAM or cart accesses add wait states (unknown for the DDP2 cart's ARM shared RAM at 0xD00000,
where the 800-slot bullet pool lives). Note also that the Arrange-only RAM at 0xE00000.. (sprite
layers, item pool, new variables) and the continuation sprite list exist only in the `espgalarr`
MAME config, not on a stock PGM. The arcade game itself slows down on the board (Cave's bullet
counts), so some lag is expected. But Arrange's PS2 pacing (almost no lag) is out of reach on a
20 MHz 68000 without cutting the per-bullet work of the arcade engine. The aim is "no slower than
the arcade engine needs for Arrange's bullets": no added code should cost more than the arcade
code it replaces.

## Ending (TODO 8a, 2026-11-07)

The both-shots run from the final form's kill to name entry, PS2 (work/compare/ps2both_end's pad,
snapshots every 10 frames) against MAME out_m297 (the replay on work/scratch/fork12/tables), in
video frames from the kill (work/scratch/s8a):

- kill -> clear bonus: 926 PS2 frames, 864 MAME frames, but the play timer steps 620 times on both:
  the same logic, the boss explosion's slowdown differs (306 / 244 lag frames).
- bonus -> staff roll: the PS2 stalls once for 57 frames (its frame counter 0x35804C stops: a disc
  load), MAME drops 26 frames to slowdown.
- staff roll: the same lines, spacing and speed (0.5 px a frame); every line crosses mid-screen
  118 frames later on the PS2 than on MAME, the first text appears at the bottom at kill + 1247 /
  + 1129, the last credit -> logo gap is 100 / 90 frames (snapshot step 10).
- Arika logo, GAME OVER: the same sequence and lengths, 118 frames later on the PS2.
- before name entry the PS2 shows NOW LOADING and stalls 102 frames (counter stopped); MAME goes on.

So the ending's logic matches the PS2; the differences in video time are the PS2's disc loads and
the slowdowns. Arrange's ending run also sets 0x650C00 when the scroll ends (not ported): nothing
observable depends on it.

## 40 MHz audit (out_m321, 2026-11-13)

The MiSTer core runs the 68k at 40 MHz (MAME `clock_scale` 2). Where does the port still slow
down more than the PS2, what costs the cycles there, and what would close the gap?

### Method (re-run with `tools/compare/perfcheck.sh BUILD TAG [SCALE] [BASE_TAG]`)

- **Runs.** The three played PCSX2 traces are replayed at the chosen clock:
  - both: ps2both_full, stages 1-6, Chihiro with both shots held (Kakusei from her gauge);
  - kak: ps2kak7, Kakusei and barrier, stages 1-3;
  - blk: ps2blk7, Black, stages 1-3.
- **Lag map.** `tools/compare/lagmap.py` takes the video frames per logic step on both
  machines, keyed on the play timer as bothcmp / longcmp are. Holds of 30+ frames (loads, stage
  changes) count as one frame. It reports lag per stage and per window of 300 steps (5 s), and
  the hot zones: windows where MAME lags 2+ points more than the PS2. `--base` shows, for each
  zone hot now or in an earlier run, the MAME frames then and now.
- **Step costs.** `tests/lua/busytap.lua` taps 0x8011C0: it counts the vblank wait's spin reads
  (26 cycles each at 0x112C46) and logs the wait's start and the IRQ's clear. From these,
  `tools/compare/busymap.py` gets every logic step's 68k cycles (IRQs included), with no
  debugger.
  - The costs do not depend on the clock: 20 and 40 MHz runs agree to 0.5% (median), and the
    tapped runs are identical to untapped ones.
  - So one run predicts the lag at any clock: a step overruns when its cost exceeds 20 MHz x
    scale / 59.17 cycles (338k at 20 MHz, 676k at 40). Predicted against measured stage 6 lag:
    7.5% / 7.4% at 40 MHz, 1.5% / 1.6% at 60 MHz.
  - The forced waits (0x8011A4: boss explosions and the like) are counted apart. The PS2 takes
    them too, and they are almost all of its lag; it overruns 0.3-1.1% of the steps.
- **Profiles.** `tests/lua/proftrace.lua` `PROF_TIMERS` opens trace windows on the play timer
  (key + 0xB2), so they land on the same moment at any clock. Attribution:
  - `tools/prof68k.py`: cycles per routine;
  - `tools/prof_attrib.py`: cycles per category, over the steps above a threshold;
  - `tools/prof_alloc_sites.py`: allocator cycles per call site.

### Where 40 MHz still lags (both run, keys = PS2 0x642298 unwrapped; PS2 frame = PCSX2 frame)

| stage | PS2 lag | MAME 40 MHz lag | steps/s PS2 / MAME |
|---|---|---|---|
| 1 | 1.7% | 1.6% | 58.9 / 58.2 |
| 2 | 1.7% | 2.5% | 58.9 / 57.7 |
| 3 | 1.2% | 1.4% | 59.2 / 58.3 |
| 4 | 1.4% | 2.9% | 59.1 / 57.5 |
| 5 | 1.7% | 3.6% | 58.9 / 57.0 |
| 6 | 1.2% | 7.4% | 59.2 / 54.8 |

(PGM 59.17 Hz against the PS2's 59.94: 1.3% of any speed ratio is the refresh rate.) Kakusei
(kak): 1.6 / 2.9 / 0.3% in stages 1-3. Black (blk): 1.3 / 2.7 / 0.0%. Over the whole both run,
MAME at 40 MHz takes 2567 frames more than the PS2 (about 43 s), and 2529 of them fall in these
zones:

| stage | keys | PS2 frame | PS2 / MAME lag | extra frames | what |
|---|---|---|---|---|---|
| 2 | 0x421b-0x4346 | 20900 | 0.0 / 21.3% | 81 | stage 2 boss death burst (allocations) |
| 3 | 0x7e3f-0x8096 | 37040 | 0.0 / 6.0% | 38 | stage 3 end |
| 4 | 0xa0a5-0xa2fc | 46503 | 0.0 / 6.8% | 44 | |
| 4 | 0xb5bd-0xb940 | 51903 | 2.5 / 20.1% | 204 | stage 4 boss (allocations 30%, handlers 40%) |
| 5 | 0xbdfd-0xc054 | 54602 | 0.2 / 9.9% | 65 | |
| 5 | 0xc181-0xc3d8 | 55503 | 4.6 / 12.2% | 54 | (allocations 20%, collision 16%) |
| 5 | 0xdff9-0xe250 | 63333 | 11.1 / 17.2% | 50 | stage 5 boss |
| 6 | 0xe520-0xf45b | 64969 | 3.0 / 18.5% | 762 | stage 6 opening; 132 steps over 1M cycles at 0xef74-0xf16a |
| 6 | 0x11658-0x12b6f | 77691 | 0.1 / 5-8% | ~190 | mid stage 6 (several 600-1200-step windows) |
| 6 | 0x135fc-0x1397f | 85828 | 4.8 / 20.4% | 186 | before the final boss (allocations 29%) |
| 6 | 0x13aac-0x141b3 | 87073 | 5.0 / 15-20% | 290 | |
| 6 | 0x1440c-0x14b13 | 89520 | 10.2 / 29-32% | 416 | final boss |
| 6 | 0x157f8-0x158fc | 94722 | 6.5 / 15.0% | 28 | end of the trace |

Also in the shorter runs: kak 0x45fb-0x4726 (13.3%, stage 2 boss), blk 0x4aad-0x4d04 (25.4%
against 7.0%, Black at the stage 2 boss). The full lists are in perfcheck's report
(`$COMPARE_DIR/runs/TAG.txt`, `lagmap_*.tsv` per window): those are the re-check points.

### The clock each part needs

The share of logic steps that overrun at each clock comes from the both run's step costs (each
overrun costs at least one lag frame on top of the forced waits):

| stage | 36 MHz | 40 | 44 | 48 | 52 | 56 | 60 | 64 | PS2-equal at |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.4% | 0.3 | 0.1 | 0.0 | | | | | 35 MHz |
| 2 | 1.8 | 1.3 | 0.5 | 0.1 | 0.1 | 0.1 | 0.1 | 0.0 | 44 |
| 3 | 0.9 | 0.6 | 0.4 | 0.2 | 0.1 | 0.0 | | | 46 |
| 4 | 2.8 | 1.9 | 1.6 | 0.9 | 0.2 | 0.0 | | | 51 |
| 5 | 4.5 | 2.3 | 1.1 | 0.6 | 0.3 | 0.2 | 0.1 | 0.1 | 52 |
| 6 | 13.3 | 7.5 | 4.0 | 2.7 | 2.1 | 1.8 | 1.5 | 1.2 | 64 |

("PS2-equal" is the clock at which no more steps overrun than the PS2's lag beyond its forced
waits.) Notes on the clock:

- A 60 MHz run (scale 3) measured 0.9-1.6% lag in every stage, PS2-like everywhere.
- Kakusei's stage 2 needs 51 MHz; Black's needs 48.
- Stage 6's tail is long: steps of 1.0-2.1M cycles (3-6 frames at 20 MHz) at the opening
  (0xef74-0xf16a) and around the final boss.
- The board measured as "1.8x or more" (the core's docs/espgalarr.md: ROM cache misses and
  bridged accesses). At 36 MHz-equivalent stage 6 overruns 13% of its steps.

**Overclocking further.** In the core, the 68k is clk_cpu / 2, and clk_cpu (80 MHz) closed with
+0.13 ns worst setup slack, so fmax is about 82 MHz. 48 MHz (clk_cpu 96) or 60 MHz (120) would
need the fx68k and ROM-cache paths re-timed or pipelined, which is not a parameter change.
Stages 2-5 would come to PS2 pace at about 52 MHz; stage 6 would not at any clock reachable
there. Cutting the cycles is the better lever.

### What the cycles go to (steps over 676k cycles, the 40 MHz budget)

| category | stage 4 boss | stage 5 0xc2ad | stage 6 opening | before final boss |
|---|---|---|---|---|
| bullet run handlers (arcade 0x16F000-0x17FFFF) | 40% | 33% | 34% | 37% |
| bullet allocators 0x17135E / 0x1713EC (first-free scans) | 30% | 20% | 16% | 29% |
| Arrange collision (ours, 0x20B25A shot loop + 0x20B954 player scan) | 0 | 16% | 22% | 7% |
| other arcade code (enemies, tasks, IRQ) | 12% | 13% | 12% | 9% |
| sprite builder (ours) | 8% | 8% | 8% | 8% |
| pool update loop 0x17242A | 4% | 4% | 3% | 3% |
| Chihiro's shots and Power Shot (ours) | 5% | 6% | 4% | 4% |

The busiest single routine is 0x175220 (the common aimed bullet's run, arcade): 23% of all
cycles in stage 6's opening, ~400 cycles per bullet, 200 bullets a frame.

### Optimisation candidates (exact: same slots, same records, same sprite lists)

Each one is checked with the comparisons (bothrun / longrun records and score must stay
identical, `splist.lua` hashes for the sprite lists), then perfcheck with `--base`.

1. **Allocator bursts**: done in out_m323 (`espgalarr/pgm/allocburst.py`, results below). The
   allocators scan the 800-slot pool from slot 0 for each bullet (24 cycles per occupied slot,
   5-10k a call when the pool is busy), and our generated code calls them in bursts with nothing
   in between that can free a slot (the Kakusei extra-shot helpers, the restructured volleys).
2. **Arrange collision shot loop**: done in out_m325 (`collision.shot_table`, results below).
   It was 8% of all cycles in stage 6's opening.
3. **0x175220 and its sibling run handlers** (arcade). Each bullet tests three globals (0x813A1C,
   0x813A16, 0x813A18), about 50-70 cycles. They are constant through the pool loop (to be
   confirmed with a write watch). Hoisting them needs handler variants chosen once per frame,
   for example through a RAM vector. About 2% in total.
4. **Pool update loop** (0x17242A). Two `addq.w #1,abs.l` per live bullet (0x81B552 / 0x81B564)
   could live in registers and be stored once.
   - Readers found: the wait 0x112BA8, the collision 0x20B984, the sprite builder 0x21C282 and
     0x17340E. All are outside the loop, but a handler-side read must be ruled out with a watch.
   - Saves 32 cycles per live bullet, about 1.5-2%.
5. **Allocator scan** itself: displacement-unrolled tests (`tst.w $40(a0)` ...) are about 12%
   faster per slot. This helps the non-burst calls too.
6. **Sprite path**. Bullets are written as 10-byte entries into a layer by the handlers, then
   copied again by the sprite builder (about 90 cycles a sprite, 8%). Writing the layer in the
   final list format and position would save most of the copy, but needs the layer order fixed
   in the list: a larger change.

What a uniform saving buys at 40 MHz (overrun steps in the both run):

| stage | now | -10% | -15% | -20% | -25% | -30% |
|---|---|---|---|---|---|---|
| 1 | 19 | 3 | 1 | 0 | 0 | 0 |
| 2 | 139 | 35 | 9 | 6 | 6 | 6 |
| 3 | 91 | 59 | 38 | 26 | 12 | 4 |
| 4 | 281 | 216 | 149 | 68 | 10 | 0 |
| 5 | 247 | 105 | 72 | 46 | 30 | 18 |
| 6 | 2199 | 1111 | 863 | 709 | 599 | 519 |

About 20% brings stages 1-5 to the PS2's pace at 40 MHz (Kakusei and Black runs alike: 232 ->
54 and 215 -> 20 overruns). Stage 6 keeps a heavy tail: its 1-2M-cycle steps are where the
allocator bursts are, so candidate 1 cuts those more than uniformly.

### Allocator bursts (out_m323, perfcheck pc323 against pc321)

- **How it works.** `espgalarr/pgm/allocburst.py` rewrites every assembled text that calls an
  allocator.
  - A call made right after another allocation, with only pure instructions since, resumes the
    scan from the slot the previous call took. Pure means writing only registers, the stack, or
    memory outside the pool.
  - The text is emitted twice: as written, and as a copy entered after an allocation. Calls into
    in-text helpers and loop back edges stay in the copy, so a volley loop scans from slot 0
    only once.
  - The resuming entries sit at 0x3FFC00. The cursor is at 0x818358, in the old arcade pool RAM
    that nothing references any more.
  - 244 head calls and 461 next calls.
  - `ESPGAL_NO_ALLOC_BURSTS=1` builds without the rewrite.
- **Exactness checks.**
  - `tests/test_allocburst.py` runs random programs through the original and rewritten text and
    compares the paths taken.
  - `BURSTCHECK=1 perfcheck.sh` loads `tests/lua/bursttap.lua`, which checks at every cursor
    store that all slots below it are occupied: 594,272 stores in the three runs, 0 violations.
  - Against pc321, the PS2 comparison reports are identical, and the game state is identical
    at every key where neither run lagged (87,807 / 32,225 / 32,300 keys).

| | pc321 | pc323 |
|---|---|---|
| both: lag stages 1-6 | 1.6 / 2.5 / 1.4 / 2.9 / 3.6 / 7.4% | 1.5 / 2.4 / 0.9 / 2.4 / 2.4 / 5.9% |
| both: extra frames vs the PS2 | 2567 | 1753 |
| kak: extra frames | 142 (pc322) | 136 |
| blk: extra frames | 125 (pc322) | 105 |
| clock to match the PS2, stages 1-6 (MHz) | 35 / 44 / 46 / 51 / 52 / 64 | 32 / 44 / 36 / 44 / 44 / 50 |
| worst step, stages 2 / 6 (k cycles) | 2336 / 2101 | 1275 / 1299 |

(pc322 = the first version, which resumed only along straight lines: 2297 extra frames in the
both run.)

**Zones that improved:**
- stage 4 boss 0xb5bd: 1127 -> 1080 frames;
- stage 5 0xdff9: 725 -> 680;
- stage 6 0x135fc / 0x13aac: 1131 -> 1049 and 1127 -> 1043;
- stage 6 final boss 0x1440c: 1274 -> 1166.

**Zones that did not move** (other costs dominate):
- stage 2 boss death 0x421b: 381 frames. Its 2M-cycle step is gone, but the lag comes from
  many steps just over one frame.
- stage 6 opening 0xe520: 4784 -> 4713. The run handlers take 33-40% of the hot steps there.
- stage 6 0x13f5c and 0x149e8: unchanged.

The next candidates for these zones are 2 (collision) and 3 (handler 0x175220).

### Collision shot table (out_m325, perfcheck pc325 against pc323)

- **How it works.** `collision.shot_table` rewrites the two shots-against-hitboxes loops of the
  ported collision (Arrange 0x89D980 / 0x8A1940; one per hitbox list).
  - The new-group word 0xE0AFF0 is constant for a hitbox. It was tested twice per shot; now each
    loop has two copies, one for each value.
  - The shots do not move during the collision, and the bias d7 is constant for a list. So once
    a list's 4th hitbox reaches the shot loop, the live shots go into a table at 0xE0F400: the
    record and the four biased extents the loop compared (12 bytes a shot). The remaining
    hitboxes of the list run over the table: no empty slots, no re-adding d7, no reloads.
  - The first three hitboxes of a list walk the pool, as before. Building the table costs about
    180 cycles a live shot, and a table pass saves 30-60 a shot. Building it for every list
    (pc324) made the boss phases, where only one or two hitboxes reach the loop, 1% slower.
  - The d7 re-basing is not folded into the bounds: the tests are unsigned 16-bit compares, and
    that would change them where the values wrap. The table holds the biased values instead.
  - `ESPGAL_NO_SHOT_TABLE=1` builds without it.
- **Exactness checks.** Against pc323, the PS2 comparison reports are identical, and the game
  state is identical at every key where neither run lagged (89,257 / 32,359 / 32,476 keys).
  `tests/test_shot_table.py` checks the rewrite's structure.

| | pc323 | pc325 |
|---|---|---|
| both: lag stages 1-6 | 1.5 / 2.4 / 0.9 / 2.4 / 2.4 / 5.9% | 1.5 / 1.4 / 0.9 / 2.3 / 2.2 / 4.4% |
| both: extra frames vs the PS2 | 1753 | 1109 |
| kak: extra frames | 136 | 11 |
| blk: extra frames | 105 | 1 |
| clock to match the PS2, stages 1-6 (MHz) | 32 / 44 / 36 / 44 / 44 / 50 | 32 / 39 / 36 / 44 / 43 / 49 |
| kak / blk stage 2 (MHz) | 50 / 48 | 45 / 42 |

Mean 68k cycles per step fell 1.5-2% per stage. The busy zones fell most: the stage 2 boss
death 7% (both 0x421b: 381 -> 307 frames; kak 0x45fb 8%; blk 0x4aad 7%, 806 -> 702 frames)
and stage 6's opening 4% (0xe520: 2515 -> 2226 frames, 0xee80: 1896 -> 1815). The stage 4
boss (1080 frames) and the stretch before and at the final boss (0.2-0.4%) hardly moved: few
hitboxes there, and the run handlers dominate (candidate 3).

