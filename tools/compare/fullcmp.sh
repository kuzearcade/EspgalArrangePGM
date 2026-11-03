#!/bin/bash
# fullcmp.sh BUILD TAG: the full PS2-vs-MAME comparison of a build (BUILD/dropin):
#   a: stage 1 no-input run, counter replay (ctr/tg_table_long)          -> poolctr.py
#   d: stage 1 shooting run (ctr/tg_table, shoot_ctr_events)             -> shootcmp.py
#   c: stages 2-6 no-input run, play-frame replay (play_table_s2..s6),
#      stopped after stage 6 (END_STAGE 5; END_FRAME 400000 is a cap)    -> stage2cmp.py, stages.py
# Tables: $COMPARE_DIR (make_tables.py); traces: $PS2_TRACES; outputs: $COMPARE_DIR/runs/TAG/.
# PARALLEL=0 runs the three MAME runs one after another (default: all three at once).
. "$(dirname "$0")/env.sh"
B=$(cd "$1" && pwd); T=$2; C=$COMPARE_DIR; O=$C/runs/$T
mkdir -p "$O"
cd "$REPO"
run_a() { CTR_TABLE=$C/ctr_table_long.txt TG_TABLE=$C/tg_table_long.txt TIMING_OUT=$O/timing_a.txt \
          mame_run "$O/nv_a" 3000 "$LUA/timing_xl.lua" "$B/dropin"; }
run_c() { STAGES="S1:$C/play_table_s2.txt:;S2:$C/play_table_s3.txt:0x50;S3:$C/play_table_s4.txt:0x48;S4:$C/play_table_s5.txt:0x92;S5:$C/play_table_s6.txt:0xeb" \
          END_STAGE=5 END_FRAME=${END_FRAME:-400000} TIMING_OUT=$O/timing_c.txt \
          mame_run "$O/nv_c" 20000 "$LUA/timing_xl2.lua" "$B/dropin"; }
run_d() { CTR_TABLE=$C/ctr_table.txt TG_TABLE=$C/tg_table.txt SHOOT_CTR_EVENTS="$(cat "$C/shoot_ctr_events.txt")" \
          TIMING_OUT=$O/shoot.txt mame_run "$O/nv_d" 1800 "$LUA/shoot.lua" "$B/dropin"; }
if [ "${PARALLEL:-1}" = 0 ]; then run_a; run_c; run_d; else run_a & run_c & run_d & wait; fi
python3 "$CMP/poolctr.py" "$O/timing_a.txt" "$PS2_TRACES/ps2long"
python3 "$CMP/shootcmp.py" "$O/shoot.txt" "$PS2_TRACES/ps2shoot2"
python3 "$CMP/stage2cmp.py" "$O/timing_c.txt" "$PS2_TRACES/ps2long34"
python3 "$CMP/stages.py" "$O/timing_c.txt" "$PS2_TRACES"
