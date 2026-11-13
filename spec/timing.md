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
