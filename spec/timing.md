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

| stage | PS2 lag frames | MAME out_m25 (main b2fe75c) | MAME with the run builder |
|---|---|---|---|
| 1 | 100 of 10880 (0.9%) | 436 (3.6%); counter key: 6837 to frame 48250 | 358 (2.9%); counter key: 5946 |
| 2 | 175 of 29839 (0.6%) | 12014 of 41958 (28.6%) | 9176 of 38898 (23.6%) |
| 3 | 161 of 22777 (0.7%) | 14065 of 48214 (29.2%) | 10281 of 44430 (23.1%) |
| 4 | 568 of 32285 (1.8%) | 15625 of 47626 (32.8%) | 13598 of 45599 (29.8%) |
| 5 | 188 of 17820 (1.1%) | 5924 of 23558 (25.1%) | 4819 of 22453 (21.5%) |
| 6 | 600 of 64064 (0.9%) | 12193 of 77728 (15.7%) | 11121 of 76656 (14.5%) |

(lagcmp in work/scratch; the PS2 windows are those of fullcmp's stage comparison. Stage 1's
frame window is fixed, so the counter-key totals are the better figure there.)

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
bullets of the same type, speed and angle at another position.

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
