#!/bin/bash
# ps2bul.sh RUNDIR ISO: PCSX2 no-input run (Chihiro; pad script RUNDIR/pad.txt as the reference traces
# ps2long7) dumping EE RAM at the bullet samples (RUNDIR/../bsamples.json, from bsamples.py)
LAUNCH=$(cd "$(dirname "$0")/.." && pwd)/pcsx2_instance.sh   # run slot + private data dir
RUN=$(cd "$1" && pwd)
FR=$(python3 -c "import json; print(','.join(str(f) for _, _, f in json.load(open('$RUN/../bsamples.json'))))")
cd $RUN && ESPGAL_PAD=$RUN/pad.txt ESPGAL_DUMP_DIR=$RUN ESPGAL_POKE=c310f9:ff ESPGAL_POKE_FROM=3300 \
  ESPGAL_DUMP=$FR ESPGAL_QUIT=197600 QT_QPA_PLATFORM=wayland \
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 14000 $LAUNCH -batch -nogui -fastboot -- "$2" > $RUN/run.log 2>&1
echo pcsx2=$?
