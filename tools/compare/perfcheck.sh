#!/bin/bash
# perfcheck.sh BUILD TAG [SCALE] [BASE_TAG]: the slowdown audit (spec/timing.md, "40 MHz audit").
# Replays the three played PS2 traces at a 68k clock scale (default 2 = the MiSTer core's 40 MHz)
# with tests/lua/busytap.lua, then per run:
#   lagmap.py  - lag per stage and window against the PS2, hot zones (TSV: lagmap_W.tsv)
#   busymap.py - 68k cycles per logic step and the clock each window needs (TSV: busymap_W.tsv,
#                steps_W.txt), valid for every clock since the cycle counts do not depend on it
# Runs: both (ps2both_full, stages 1-6, both shots held), kak (ps2kak7, Kakusei + barrier to
# stage 3), blk (ps2blk7, Black to stage 3). With BASE_TAG, each hot zone also shows the MAME
# frames of that earlier perfcheck run, so an optimisation can be checked zone by zone.
# BURSTCHECK=1 also loads tests/lua/bursttap.lua (the allocator bursts' invariant, per run in
# burst.txt; a build with them has jsr targets at 0x3FFC00).
# Outputs: $COMPARE_DIR/runs/TAG_{both,kak,blk}/ and $COMPARE_DIR/runs/TAG.txt (the report).
. "$(dirname "$0")/env.sh"
B=$(cd "$1" && pwd); T=$2; SC=${3:-2.0}; BT=$4; C=$COMPARE_DIR
cd "$REPO"
oc=$C/runs/${T}_oc.lua
printf 'manager.machine.devices[":maincpu"].clock_scale = %s\ndofile("%s/busytap.lua")\n' "$SC" "$LUA" > "$oc"
[ -n "$BURSTCHECK" ] && printf 'dofile("%s/bursttap.lua")\n' "$LUA" >> "$oc"
mkdir -p "$C/runs/${T}_both" "$C/runs/${T}_kak" "$C/runs/${T}_blk"
BUSY_LOG=$C/runs/${T}_both/busy.txt BURST_LOG=$C/runs/${T}_both/burst.txt EXTRA_LUA=$oc "$CMP/bothrun.sh" "$B" "${T}_both" > "$C/runs/${T}_both.log" 2>&1 &
BUSY_LOG=$C/runs/${T}_kak/busy.txt BURST_LOG=$C/runs/${T}_kak/burst.txt EXTRA_LUA=$oc "$CMP/longrun.sh" "$B" "${T}_kak" kak > "$C/runs/${T}_kak.log" 2>&1 &
BUSY_LOG=$C/runs/${T}_blk/busy.txt BURST_LOG=$C/runs/${T}_blk/burst.txt EXTRA_LUA=$oc BLK_SHIFT=100 "$CMP/longrun.sh" "$B" "${T}_blk" blk > "$C/runs/${T}_blk.log" 2>&1 &
wait
{
  echo "perfcheck $T: $B at clock scale $SC"
  for w in both kak blk; do
    O=$C/runs/${T}_$w
    case $w in both) L=long_both.txt; P=$C/ps2both_full ;; *) L=long_$w.txt; P=$PS2_TRACES/ps2${w}7 ;; esac
    X=(); [ -n "$BT" ] && X=(--base "$C/runs/${BT}_$w/lagmap_$w.tsv")
    [ -n "$BURSTCHECK" ] && { echo; echo "=== $w: allocator bursts: $(tail -1 "$O/burst.txt")"; }
    echo; echo "=== $w: lag against the PS2"
    python3 "$CMP/lagmap.py" "$O/$L" "$P" $w --quiet --hot 2 --tsv "$O/lagmap_$w.tsv" "${X[@]}"
    echo; echo "=== $w: 68k cycles per step"
    python3 "$CMP/busymap.py" "$O/busy.txt" "$SC" "$O/$L" "$P" $w --tsv "$O/busymap_$w.tsv" --steps "$O/steps_$w.txt" \
      | sed -n '1,/^windows/p' | head -n -1
  done
} > "$C/runs/$T.txt"
cat "$C/runs/$T.txt"
