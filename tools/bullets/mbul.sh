#!/bin/bash
# mbul.sh BUILD TAG c|a : MAME no-input run dumping RAM and the bullet pool at the bullet samples
# c: stages 2..6 (timing_xl2.lua, DUMP_PLAYS, play tables); a: stage 1 (timing_xl.lua, DUMP_KEYS,
# the counter replay of the stage 1 comparison)
# (run from the repository; the scratch directory S holds the play tables, timing_xl*.lua and
# bsamples.mame.txt; MAME is $MAME)
R=$(cd "$(dirname "$0")/../.." && pwd)
S=${BULLET_SCRATCH:-work/scratch}; cd $R
D=$R/$S/mbul_$2_$3
rm -rf "$D"; mkdir -p "$D/nv"
M=${MAME:-mame}
if [ "$3" = c ]; then
  STAGES="S1:$S/play_table_s2.txt:;S2:$S/play_table_s3.txt:0x50;S3:$S/play_table_s4.txt:0x48;S4:$S/play_table_s5.txt:0x92;S5:$S/play_table_s6.txt:0xeb" \
  DUMP_PLAYS="$(sed -n 1p $S/bsamples.mame.txt)" DUMP_DIR=$D END_FRAME=290000 TIMING_OUT=$D/t.txt \
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 20000 $M espgalarr -rompath $1/dropin \
    -video none -sound none -nothrottle -window -cfg_directory work/mame/cfg -nvram_directory $D/nv \
    -autoboot_script $S/timing_xl2.lua > $D/run.log 2>&1
else
  CTR_TABLE=$S/ctr_table_long.txt TG_TABLE=$S/tg_table_long.txt DUMP_KEYS="$(sed -n 2p $S/bsamples.mame.txt)" \
  DUMP_DIR=$D TIMING_OUT=$D/t.txt \
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 4000 $M espgalarr -rompath $1/dropin \
    -video none -sound none -nothrottle -window -cfg_directory work/mame/cfg -nvram_directory $D/nv \
    -autoboot_script $S/timing_xl.lua > $D/run.log 2>&1
fi
ls $D | grep -c bin
