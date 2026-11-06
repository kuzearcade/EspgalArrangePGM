# PS2-vs-MAME comparisons

How a build is checked against PS2 Arrange (spec/arrange-diff.md, "Comparison status"). The PS2
side is PCSX2 traces of RAM per frame; the MAME side replays the PS2's frame timing and logs the
same RAM. Everything taken from the game (traces, tables, MAME logs) stays in git-ignored
directories; only the scripts are here.

Paths come from `env.sh` (override in the environment):

| variable | default | what |
|---|---|---|
| `COMPARE_DIR` | `work/compare` | replay tables, MAME run outputs (`runs/TAG/`) |
| `PS2_TRACES` | `$COMPARE_DIR/ps2` | PCSX2 trace directories (`watch.txt`, `pad.txt`) |
| `MAME` | `~/Arcade-NMK16_MiSTer/mame/mame` | MAME with the `espgalarr` set (patches/mame) |
| `PCSX2` | `~/pcsx2/build/bin/pcsx2-qt` | PCSX2 with patches/pcsx2/espgal-trace.diff |
| `PS2_ISO`, `MAME_CFG` | under `work/` | the disc image, MAME's cfg directory |
| `MEM` | `8G` | memory limit of each emulator run (systemd-run scope) |

## End to end

1. PS2 traces, once (hours; one PCSX2 at a time, it opens a window; tools/pcsx2_README.txt):
   `tools/compare/ps2_traces.sh` (or names: `ps2_traces.sh ps2shoot2 ps2tg`). All runs are
   stage 1 Arrange with Chihiro and P1 invincible; ps2long3..7 are consecutive windows of one
   no-input run, joined into ps2long34 / ps2long45.
2. Replay tables: `python3 tools/compare/make_tables.py $PS2_TRACES $COMPARE_DIR`.
3. Build: `python3 -m espgalarr.cli build --out out_x --port-routines all`.
4. Compare: `tools/compare/fullcmp.sh out_x TAG` (three MAME runs at once; `PARALLEL=0` one
   after another), and the Kakusei test `tools/compare/kakusei.sh out_x TAG`.

## What each comparison checks

| script | MAME run (tests/lua) | PS2 trace | keyed on |
|---|---|---|---|
| `poolctr.py` | stage 1 no input (`timing_xl.lua`, ctr/tg_table_long) | ps2long | game counter |
| `shootcmp.py` | stage 1 shooting (`shoot.lua`, shoot_ctr_events) | ps2shoot2 | game counter |
| `stage2cmp.py` | stages 2-6 no input (`timing_xl2.lua`, play_table_s2..s6) | ps2long34 | play frame |
| `stages.py` (`stagecmp.py`) | the same run, stages 3-6 | ps2long45, 6, 7 | play frame |
| `bulset.py` | Kakusei shooting run (`shoot.lua` BUL_LOG) | ps2kak4 | game counter |
| `longcmp.py` (`longrun.sh BUILD TAG kak\|blk`) | the long shooting runs, `shoot.lua` KEY_TIMER (kak7 / blk7 tables) | ps2kak7 / ps2blk7 | logic frame, through the stage changes |

The enemy records are compared on their first words (flags, x, y; PS2 0xC27A10 0x58 each = MAME
0xE08200 0x2C each); bullets as multisets of (x, y, speed, angle), slot order ignored. Known
differences on a matching build: stage 2 after its end (198 frames), stage 3 at play 33739,
stage 4 at 31579, stage 6 at 18982; stage 1 shooting: p1 bytes from frame 1860, pool from 2570.

Replay keys: the PS2 slows down by freezing logic frames while its game counter (0x35804C) and
frame toggle (0x358048) keep counting; MAME lags differently. The MAME scripts therefore write
the PS2's counter and toggle per logic frame: by logic-frame key (play time 0x642298 + 0x3E5) for
stage 1 (ctr/tg tables), by play frame 0x813A1E for stages 2-6 (play tables, built by
tools/ps2_play_table.py with MIN_STUCK 10). `timing_xl2.lua` stops after stage 6 (END_STAGE 5:
the play counter's reset after the stage; END_FRAME is only a cap).

Nothing is keyed on MAME frames or on how much MAME lagged before a stage, so the comparisons
hold with the 68k overclocked (MAME's `clock_scale`) as at 20 MHz:
- **Stage 1's key** starts at the first play frame as play 0x813A1E + KEY_PLAY_OFF (0x333).
  Until the PS2 tables start (key 0x19D3 long, 0x485 shooting), the counter, its mod-24 copy
  0x801170 and the toggle are written as the identity (counter = key, toggle = key & 1), which
  is what a lag-free boot leaves. The key used to be MAME's own counter at frame 1800, which
  also counts boot and select steps: one lag frame fewer before the stage (the sprite-builder
  speed-up, out_m38) shifted the whole replay a step, and stage 1 split from MAME frame 7669.
- **The long traces' key** (shoot.lua KEY_TIMER, tools/compare/longrun.sh) is the play timer
  0x8139DC + KEY_PLAY_OFF from frame 1800 (it stops through stage clears, as the PS2's does). It
  also used to start as MAME's counter at frame 1800 (out_m38 on: one boot lag frame fewer in the
  Kakusei run). The Black tables are keyed PS2 key + 0x3E5 + BLK_SHIFT (100).
- **Each stage's RNG 0x801176** is set while its play counter is 0: stage 2 0xF1, stage 3 0x50,
  stage 4 0x48, stage 5 0x92, stage 6 0xEB. Stage 2's used to be left to the run, and arrived
  as 0xF1 only at 20 MHz (1.5x: 0xCB, 2x: 0x70).

`timing_xl2.lua` options for digging into a split: SAVE_AT (save state `s6`; reload with
`-state s6` and FT0), W_ADDR / W_LEN / W_FROM / W_TO / WLOG (write tap with pc and stack,
W_REG), DUMPS / DUMP_PLAYS / DUMP_DIR (RAM + bullet pool dumps, as tools/bullets uses),
REC_LOG, MEM_LOG, EXTRA_LUA (extra script, e.g. debugger breakpoints with
`-debug -debugger none -log`). `shoot.lua`: BUL_LOG (bullet pool per frame), BTAP_LOG (new
bullets with pc and stack), EXTRA_LUA.

## Code helpers

Run from a checkout that has `work/` (the disc and listing):

- `twin_of.py RN AN 68K...`: the arcade/Arrange function pair whose arcade twin aligns with each
  68k address, with both call lists.
- `mipsread.py FN [END [OVERLAY]]`: an overlay's MIPS with the 68k register slots named.
- `dis68.py BUILD ADDR [N]`: a build's P ROM disassembled (what a port placed).
- `tools/kakusei_sites.py --left`: the functions whose helper calls do not line up with the
  arcade's (candidates for whole-routine ports).
