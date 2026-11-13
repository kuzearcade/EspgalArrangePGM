# PS2-vs-MAME comparisons

How a build is checked against PS2 Arrange (spec/arrange-diff.md, "Comparison status"). The PS2
side is PCSX2 traces of RAM per frame; the MAME side replays the PS2's frame timing and logs the
same RAM. Everything taken from the game (traces, tables, MAME logs) stays in git-ignored
directories; only the scripts are here.

Comparison data lives only in the main checkout's `work/` (`work/compare`, the PCSX2 traces under
`work/scratch` that `work/compare/ps2/` links to), never inside an agent worktree: worktrees are
removed when their branch is merged. Agent worktrees link `work/` to the main one, so write
through that link or by absolute path. A hard-link snapshot of the 2026-11-06 data is kept
outside the repository in `~/espgalarr_data_keep/2026-11-06`.

Paths come from `env.sh` (override in the environment):

| variable | default | what |
|---|---|---|
| `COMPARE_DIR` | `work/compare` | replay tables, MAME run outputs (`runs/TAG/`) |
| `PS2_TRACES` | `$COMPARE_DIR/ps2` | PCSX2 trace directories (`watch.txt`, `pad.txt`) |
| `MAME` | `~/Arcade-NMK16_MiSTer/mame/mame` | MAME with the `espgalarr` set (patches/mame) |
| `PCSX2` | `tools/pcsx2_instance.sh` | PCSX2 with patches/pcsx2/espgal-trace.diff, run through the launcher (a run slot, private data dir, fixed clock) |
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
| `shootcmp.py` | stage 1 shooting (`shoot.lua` KEY_TIMER, shoot_ctr_events) | ps2shoot2 | play timer |
| `stage2cmp.py` | stages 2-6 no input (`timing_xl2.lua`, play_table_s2..s6) | ps2long34 | play frame |
| `stages.py` (`stagecmp.py`) | the same run, stages 3-6 | ps2long45, 6, 7 | play frame |
| `bulset.py` | Kakusei shooting run (`shoot.lua` KEY_TIMER, BUL_LOG) | ps2kak4 | play timer |
| `longcmp.py` (`longrun.sh BUILD TAG kak\|blk`) | the long shooting runs, `shoot.lua` KEY_TIMER (kak7 / blk7 tables) | ps2kak7 / ps2blk7 | logic frame, through the stage changes |
| `bothcmp.py` (`bothrun.sh BUILD TAG [CLOCK_SCALE]`) | both shots held, stage 1 to the stage 6 boss, `shoot.lua` KEY_TIMER KEY_UNWRAP (both_* tables, `both_tables.py`) | ps2both_full | unwrapped logic frame |
| `lagmap.py` / `busymap.py` (`perfcheck.sh BUILD TAG [SCALE] [BASE_TAG]`) | the both, kak and blk runs at a 68k clock scale with `busytap.lua` (cycles per logic step) | ps2both_full, ps2kak7, ps2blk7 | play timer; reports lag per window and hot zones against the PS2, and the clock each window needs (spec/timing.md, "40 MHz audit") |

The enemy records are compared on their first words (flags, x, y; PS2 0xC27A10 0x58 each = MAME
0xE08200 0x2C each); bullets as multisets of (x, y, speed, angle), slot order ignored. Known
differences on a matching build: stage 2 after its end (198 frames), stage 3 at play 33739,
stage 4 at 31579, stage 6 at 18982; stage 1 shooting: p1 bytes from frame 1860.

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
- **Frames before play** (since the select-to-stage change, no hidden demo): shoot.lua,
  timing_xl.lua and timing_xl2.lua count frames as before it: from the frame stage 1's play timer
  0x8139DC leaves 0 the script's frame resumes at 1461 (where play used to start), and the game
  counter is set to the old flow's 0x335 (CTR_START) at play step 1, so every frame constant and
  table keeps its meaning. The counter-keyed replays then ran a logic step behind the game, so
  the stage 1 shooting run and the Kakusei test now key on the play timer (KEY_TIMER=1).
- **The long traces' key** (shoot.lua KEY_TIMER, tools/compare/longrun.sh) is the play timer
  0x8139DC + KEY_PLAY_OFF from frame 1800 (it stops through stage clears, as the PS2's does). It
  also used to start as MAME's counter at frame 1800 (out_m38 on: one boot lag frame fewer in the
  Kakusei run). The Black tables are keyed PS2 key + 0x3E5 + BLK_SHIFT (100).
- **The six-stage both-shots run** (ps2both_full = work/scratch/forkD/ps2both7 to PCSX2 frame
  29899 + work/compare/ps2both_s46_f8 from 29900 to 95000; Chihiro, invincible, Square +
  Circle held from frame 3500): the 16-bit play time passes 0xFFFF in stage 6 (PCSX2 frame
  71971), so `both_tables.py` unwraps the PS2 key (+0x10000 per wrap) and `shoot.lua`
  KEY_UNWRAP=1 does the same with MAME's. `bothrun.sh` takes an optional 68k clock scale (a
  Lua `clock_scale`; the comparison holds at any speed) for lag measurements (spec/timing.md).
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

## Bullet samples (tools/bullets)

The per-stage bullet comparison uses the same layout: `$COMPARE_DIR/bullets` (`BULLET_DIR`)
holds `bsamples.json` (from `tools/bullets/bsamples.py`, sample points read from the no-input
PCSX2 traces in `$PS2_TRACES`: ps2long, ps2long34, ps2long45, ps2long6, ps2long7),
`bsamples.mame.txt` (line 1: the stages 2-6 `stage:play` list, line 2: the stage 1 replay keys),
the PCSX2 dumps `ps2bul/` (`tools/bullets/ps2bul.sh`) and the MAME dumps
`mbul_<TAG>_<c|a>/` (`tools/bullets/mbul.sh BUILD TAG c|a`: stages 2-6 through
tests/lua/timing_xl2.lua DUMP_PLAYS, stage 1 through tests/lua/timing_xl.lua DUMP_KEYS; both
with DUMP_LAST=1, the finished logic step). The analysis scripts (bparent.py, bstage.py, ...)
read the same directory; `typemap.json` (bullet type -> run handler, for btypes.py / bparent.py)
comes from `typemap.py EE_DUMP BUILD/dropin/espgalarr.zip` run in it, with `A0A9_DIS` an
ov_arrange listing in `addr mnemonic operands` lines (the work/ps2/dis listing without its
instruction-word column). Rebuilding the set from the traces: `bsamples.py`, the two lines of
bsamples.mame.txt (`stage:play` hex for stages 2-6, stage 1 keys = play + 0x3E5 hex),
`ps2bul.sh $BULLET_DIR/ps2bul ISO` with ps2long7's pad.txt in it (about 10 minutes), then
`mbul.sh` c and a (c about 80 minutes). On main all 70 samples are identical. The PS2 pool is
800 records at 0xC39C20 (counter 0xC46420); readers that started at 0xC39A20 took the 0x200 bytes
before it as 8 records, which made stage 5's samples look like 3 extra PS2 bullets.

## Bullet samples under fire (tools/bullets fire*)

The samples above come from no-input runs. Under fire (the both-shots run: Chihiro, P1
invincible, both buttons held from PCSX2 frame 3500, stage 1 to the stage 6 boss) the same
comparison runs on the both-shots replay's keys, in a directory DIR of your choice:

1. `firekeys.py $COMPARE_DIR/ps2both_full/watch.txt DIR 10` picks 10 keys per stage, evenly
   spread and 0x100 keys clear of the stage changes (or `firekeys.py WATCH DIR KEY...` for given
   keys) and writes DIR/timers.txt (MAME play timers, key + 0xB2) and DIR/frames.txt (each key's
   first PCSX2 frame in the merged trace).
2. `ps2pool.sh DIR`: one PCSX2 run with ps2both7's pad script dumping EE RAM at those frames
   (DIR/ps2/ee_<frame>.bin; about 5 minutes for the whole run).
3. `firepool.sh BUILD DIR [END]`: the both-shots replay (bothrun.sh's set-up) with
   tests/lua/pooldump.lua dumping the pool at the key's last frame, the finished logic step
   (DIR/mame_<build>/t_<timer>.bin; POOL_TASKS=1 adds the task slots 0x812D8C as .tsk).
4. `firecmp.py DIR DIR/mame_<build> [-v]`: bullets per key as bstage.py's multisets (the PS2 pool
   read from 0xC39C20, 800 records).

Where a key differs, dense keys around it (step 2 and 3 with explicit keys) find the first
differing volley; a bullet's +0x3C is its owner (the PS2's and MAME's record addresses differ:
PS2 0xC27A10 + 0x2C n = MAME 0xE08200 + 0x2C n), +0x16 a per-frame counter of the bullet, and
the task slots (PS2 0xC475B8 = 68k 0x812D8C, 0x40 each) hold the owner task's countdowns. The
first run (2026-11-07, out_m242, 60 keys; data work/scratch/fork13/fire) and its findings are in
TODO.md ("Bullets under fire").
