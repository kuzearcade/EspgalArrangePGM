#!/bin/bash
# mbul.sh BUILD TAG c|a : MAME no-input run dumping RAM and the bullet pool at the bullet samples
# c: stages 2..6 (tests/lua/timing_xl2.lua, DUMP_PLAYS, play tables); a: stage 1
# (tests/lua/timing_xl.lua, DUMP_KEYS, the counter replay of the stage 1 comparison). Both dump the
# last frame of each sample (DUMP_LAST=1: the finished logic step, as the PS2's samples are).
# Layout (tools/compare/env.sh): the tables made by tools/compare/make_tables.py in $COMPARE_DIR,
# the samples ($BULLET_DIR/bsamples.mame.txt, from bsamples.py) and the output
# $BULLET_DIR/mbul_<TAG>_<c|a> in $BULLET_DIR = $COMPARE_DIR/bullets.
R=$(cd "$(dirname "$0")/../.." && pwd)
. "$R/tools/compare/env.sh"
B=${BULLET_DIR:-$COMPARE_DIR/bullets}
D=$B/mbul_$2_$3
rm -rf "$D"; mkdir -p "$D/nv"
cd "$R"
if [ "$3" = c ]; then
  STAGES="S1:$COMPARE_DIR/play_table_s2.txt:0xf1;S2:$COMPARE_DIR/play_table_s3.txt:0x50;S3:$COMPARE_DIR/play_table_s4.txt:0x48;S4:$COMPARE_DIR/play_table_s5.txt:0x92;S5:$COMPARE_DIR/play_table_s6.txt:0xeb" \
  DUMP_PLAYS="$(sed -n 1p $B/bsamples.mame.txt)" DUMP_LAST=1 DUMP_DIR=$D END_FRAME=290000 TIMING_OUT=$D/t.txt \
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 20000 $MAME espgalarr -rompath $1/dropin \
    -video none -sound none -nothrottle -window -cfg_directory work/mame/cfg -nvram_directory $D/nv \
    -autoboot_script tests/lua/timing_xl2.lua > $D/run.log 2>&1
else
  CTR_TABLE=$COMPARE_DIR/ctr_table_long.txt TG_TABLE=$COMPARE_DIR/tg_table_long.txt \
  DUMP_KEYS="$(sed -n 2p $B/bsamples.mame.txt)" DUMP_LAST=1 DUMP_DIR=$D TIMING_OUT=$D/t.txt \
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 4000 $MAME espgalarr -rompath $1/dropin \
    -video none -sound none -nothrottle -window -cfg_directory work/mame/cfg -nvram_directory $D/nv \
    -autoboot_script tests/lua/timing_xl.lua > $D/run.log 2>&1
fi
ls $D | grep -c bin
