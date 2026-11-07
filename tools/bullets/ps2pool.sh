#!/bin/bash
# ps2pool.sh DIR: bullet samples under fire, PS2 side: the both-shots run (ps2both7's pad script, P1
# invincible, both buttons held from frame 3500) dumping EE RAM (DIR/ps2/ee_<frame>.bin) at the PCSX2
# frames in DIR/frames.txt (firekeys.py); one PCSX2 run to the last frame (about 5 minutes for the
# whole run)
R=$(cd "$(dirname "$0")/../.." && pwd)
. "$R/tools/compare/env.sh"
D=$(cd "$1" && pwd); O=$D/ps2
mkdir -p "$O"
FR=$(cat "$D/frames.txt"); LAST=${FR##*,}
cp "$PS2_TRACES/ps2both7/pad.txt" "$O/pad.txt"
cd "$O" && ESPGAL_PAD=$O/pad.txt ESPGAL_DUMP_DIR=$O ESPGAL_POKE=c310f9:ff ESPGAL_POKE_FROM=3300 \
  ESPGAL_DUMP=$FR ESPGAL_QUIT=$((LAST + 30)) QT_QPA_PLATFORM=wayland \
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout -k 30 7200 \
  "$PCSX2" -batch -nogui -fastboot -- "$PS2_ISO" > "$O/run.log" 2>&1
echo pcsx2=$? dumps=$(ls "$O" | grep -c '^ee_')
