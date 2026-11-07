#!/bin/bash
# bothrun.sh BUILD TAG [CLOCK_SCALE]: the both-shots run (ps2both_full: Chihiro, invincible, Square +
# Circle held from PCSX2 frame 3500, stage 1 to the stage 6 boss) replayed with shoot.lua
# KEY_TIMER KEY_UNWRAP on the both_tables.py tables ($COMPARE_DIR/both_*), then compared per logic
# frame (bothcmp.py). CLOCK_SCALE (e.g. 2 = the MiSTer core's 40 MHz 68k) runs the 68k faster
# through a Lua clock_scale (the comparison holds at any speed; the lag differs).
. "$(dirname "$0")/env.sh"
B=$(cd "$1" && pwd); T=$2; C=$COMPARE_DIR; O=$C/runs/$T
mkdir -p "$O"
cd "$REPO"
X=()
if [ -n "$3" ]; then echo "manager.machine.devices[\":maincpu\"].clock_scale = $3" > "$O/oc.lua"; X=(EXTRA_LUA=$O/oc.lua); fi
( export "${X[@]}" KEY_TIMER=1 KEY_UNWRAP=1 CTR_TABLE=$C/both_ctr_table.txt TG_TABLE=$C/both_tg_table.txt \
    SHOOT_CTR_EVENTS="$(cat "$C/both_events.txt")" SHOOT_END=${SHOOT_END:-150000} TIMING_OUT=$O/long_both.txt \
    MEM=${MEM:-6G}; mame_run "$O/nv_both" 20000 "$LUA/shoot.lua" "$B/dropin" )
python3 "$CMP/bothcmp.py" "$O/long_both.txt" "$PS2_TRACES/../ps2both_full"
