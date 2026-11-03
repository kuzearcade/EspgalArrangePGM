#!/bin/bash
# kakusei.sh BUILD TAG: the Kakusei bullet test. The stage 1 shooting run with the Kakusei press
# (kakusei_ctr_events.txt), logging the whole bullet pool (0xD00000, 800 slots) from frame 3800
# to 5000, against the PS2's (ps2kak4): bullets as multisets per game counter (bulset.py).
. "$(dirname "$0")/env.sh"
B=$(cd "$1" && pwd); T=$2; C=$COMPARE_DIR; O=$C/runs/$T
mkdir -p "$O"
cd "$REPO"
CTR_TABLE=$C/ctr_table.txt TG_TABLE=$C/tg_table.txt SHOOT_CTR_EVENTS="$(cat "$C/kakusei_ctr_events.txt")" SHOOT_END=5100 \
  BUL_LOG=$O/kakusei_bul.txt BUL_FROM=3800 BUL_TO=5000 BUL_END=0xD0C7FF TIMING_OUT=$O/kakusei_shoot.txt \
  MEM=${MEM:-6G} mame_run "$O/nv_k" 1500 "$LUA/shoot.lua" "$B/dropin"
python3 "$CMP/bulset.py" "$O/kakusei_bul.txt" "$PS2_TRACES/ps2kak4"
