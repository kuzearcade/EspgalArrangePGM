#!/bin/bash
# longrun.sh BUILD TAG [kak|blk]: the long shooting runs to PCSX2 frame 26000 (ps2kak7: Chihiro,
# Kakusei and the barrier; ps2blk7: Black), replayed with shoot.lua KEY_TIMER (the key follows
# the play-time timer, so the stage changes line up) and compared per logic frame (longcmp.py).
. "$(dirname "$0")/env.sh"
B=$(cd "$1" && pwd); T=$2; W=${3:-kak}; C=$COMPARE_DIR; O=$C/runs/$T
mkdir -p "$O"
cd "$REPO"
if [ "$W" = blk ]; then X=(SHOOT_EVENTS="440,P1 Right,1;450,P1 Right,0"); S=${BLK_SHIFT:-101}; else X=(); S=0; fi
env "${X[@]}" KEY_TIMER=1 CTR_TABLE=$C/${W}7_ctr_table.txt TG_TABLE=$C/${W}7_tg_table.txt \
  SHOOT_CTR_EVENTS="$(cat "$C/${W}7_events.txt")" SHOOT_END=36000 TIMING_OUT=$O/long_$W.txt \
  MEM=${MEM:-6G} mame_run "$O/nv_l$W" 7000 "$LUA/shoot.lua" "$B/dropin"
python3 "$CMP/longcmp.py" "$O/long_$W.txt" "$PS2_TRACES/ps2${W}7" "$W" "$S"
