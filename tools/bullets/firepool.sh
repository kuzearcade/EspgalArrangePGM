#!/bin/bash
# firepool.sh BUILD DIR [END]: bullet samples under fire, MAME side: the both-shots replay
# (tools/compare/bothrun.sh's set-up) dumping the enemy bullet pool at the play timers in
# DIR/timers.txt (firekeys.py) into DIR/mame_<build name>/ (tests/lua/pooldump.lua)
R=$(cd "$(dirname "$0")/../.." && pwd)
. "$R/tools/compare/env.sh"
B=$(cd "$1" && pwd); D=$(cd "$2" && pwd); O=$D/mame_$(basename "$B")
C=$COMPARE_DIR
mkdir -p "$O"
cd "$REPO"
( export KEY_TIMER=1 KEY_UNWRAP=1 CTR_TABLE=$C/both_ctr_table.txt TG_TABLE=$C/both_tg_table.txt \
    SHOOT_CTR_EVENTS="$(cat "$C/both_events.txt")" SHOOT_END=${3:-100000} TIMING_OUT=$O/long_both.txt \
    EXTRA_LUA=$LUA/pooldump.lua POOL_TIMERS="$(cat "$D/timers.txt")" POOL_DIR=$O MEM=${MEM:-6G}
  mame_run "$O/nv" 30000 "$LUA/shoot.lua" "$B/dropin" )
ls "$O" | grep -c '^t_'
