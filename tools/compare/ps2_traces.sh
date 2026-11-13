#!/bin/bash
# ps2_traces.sh NAME...: take the PCSX2 reference traces the comparisons use (all: every NAME below)
# into $PS2_TRACES/NAME/ (watch.txt, pad.txt, run.log). Every run is PS2 Arrange stage 1 with
# Chihiro (tools/pcsx2_pad_arrange_stage1.txt up to frame 3400), P1 invincible from 3300
# (0xC310F9 = 0xFF), one PCSX2 at a time. PCSX2 needs patches/pcsx2/espgal-trace.diff and
# tools/pcsx2_README.txt's settings; it opens a window (QT_QPA_PLATFORM=wayland).
#   ps2long          no input, 9000-50000: stage 1 hitbox pool (poolctr.py)
#   ps2long2         no input, 9000-26000, frame toggle: ctr/tg_table_long (stage 1 replay)
#   ps2long3..7      no input, consecutive windows to frame 200000: stages 2-6 (joined below)
#   ps2shoot2        shooting run (pads/shoot_extra.txt): counter, pool, score, P1 record
#   ps2tg            the same shooting run's frame toggle: tg_table.txt
#   ps2kak4          shooting run + Kakusei (Triangle at 6000, Cross at 6300): the bullet pool
#   ps2kak7          ps2kak4's inputs with the shooting pattern continued to frame 26000 (the rest
#                    of stage 1, stage 2, the start of stage 3): ps2shoot2's watch + the toggle
#   ps2blk7          Black (Right on the select screen), the shooting pattern to frame 26000
# Joined (no PCSX2 run): ps2long34 = ps2long3 + ps2long4, ps2long45 = ps2long4 + ps2long5.
. "$(dirname "$0")/env.sh"
trace() {   # trace NAME WATCH RANGE QUIT TIMEOUT [PAD_EXTRA...]
  local n=$1 w=$2 r=$3 q=$4 t=$5; shift 5
  local d=$PS2_TRACES/$n
  mkdir -p "$d"
  # BLACK=1: Right on the select screen (frame 2980) picks Black
  { grep -v '^\s*$' "$REPO/tools/pcsx2_pad_arrange_stage1.txt" | awk '$1 < 3400'
    [ -n "$BLACK" ] && printf '2980 2000\n2986 0\n'; } | sort -n -s -k1,1 > "$d/pad.txt"
  for x in "$@"; do cat "$x" >> "$d/pad.txt"; done
  (cd "$d" && ESPGAL_PAD=$d/pad.txt ESPGAL_DUMP_DIR=$d ESPGAL_POKE=c310f9:ff ESPGAL_POKE_FROM=${POKE_FROM:-3300} \
    ESPGAL_WATCH=$w ESPGAL_WATCH_RANGE=$r ESPGAL_QUIT=$q QT_QPA_PLATFORM=wayland \
    systemd-run --user --scope -q -p MemoryMax=$MEM -p MemorySwapMax=0 timeout "$t" \
    "$PCSX2" -batch -nogui -fastboot -- "$PS2_ISO" > "$d/run.log" 2>&1)
  echo "$n pcsx2=$?"
}
L=642298:10,c27a10:14a0
for n in "${@:-ps2long ps2long2 ps2long3 ps2long4 ps2long5 ps2long6 ps2long7 ps2shoot2 ps2tg ps2kak4}"; do
  for n in $n; do
    case $n in
      ps2long)   trace $n $L 9000-50000 50010 14000 ;;
      ps2long2)  trace $n $L,358048:8 9000-26000 26010 9000 ;;
      ps2long3)  trace $n $L,358048:8 20300-40000 40010 9000 ;;
      ps2long4)  trace $n $L,358048:8 39900-62000 62010 9000 ;;
      ps2long5)  trace $n $L,358048:8 61900-90000 90010 9000 ;;
      ps2long6)  trace $n $L,358048:8 85000-125000 125010 9000 ;;
      ps2long7)  trace $n $L,358048:8,358028:2 117000-200000 200010 9000 ;;
      ps2shoot2) trace $n $L,c2b0d0:34,c310c0:a0,35804c:4 3546-9006 9010 3600 "$CMP/pads/shoot_extra.txt" ;;
      ps2tg)     trace $n 642298:10,358048:8 3550-9006 9008 3600 "$CMP/pads/shoot_extra.txt" ;;
      ps2kak4)   trace $n 642298:2,6422d4:2,6422ba:2,6422be:2,35804c:4,c39c20:c840,c27a10:14a0 5900-6700 6710 3000 \
                   "$CMP/pads/kakusei_extra.txt" ;;
      ps2kak7)   trace $n 642298:10,c27a10:14a0,c2b0d0:34,c310c0:a0,35804c:4,358048:8 3546-26000 26010 14000 \
                   "$CMP/pads/kakusei_long.txt" ;;
      ps2blk7)   BLACK=1 POKE_FROM=3500 trace $n 642298:10,c27a10:14a0,358048:8,358028:2,c2b0d0:34,c310c0:a0 3300-26000 26010 14000 \
                   "$CMP/pads/black_long.txt" ;;
      *) echo "unknown trace $n"; exit 1 ;;
    esac
  done
done
for j in "ps2long34 ps2long3 ps2long4" "ps2long45 ps2long4 ps2long5"; do
  set -- $j
  if [ -f "$PS2_TRACES/$2/watch.txt" ] && [ -f "$PS2_TRACES/$3/watch.txt" ]; then
    mkdir -p "$PS2_TRACES/$1"; cat "$PS2_TRACES/$2/watch.txt" "$PS2_TRACES/$3/watch.txt" > "$PS2_TRACES/$1/watch.txt"
  fi
done
