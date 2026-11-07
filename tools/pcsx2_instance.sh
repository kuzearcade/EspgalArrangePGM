#!/bin/bash
# pcsx2_instance.sh NAME [pcsx2 args...]: run PCSX2 from its own data directory ~/pcsx2/inst/NAME, so
# several copies can run at once without sharing state. PCSX2 in portable mode keeps its settings
# (inis/PCSX2.ini, rewritten on every exit), memory cards, save states, logs and shader cache next
# to the executable (EmuFolders::AppRoot = the executable's directory), so every instance gets a
# hard link of the binary, links to the read-only resources, and on every launch a fresh copy of the
# settings and of the (empty) memory cards from the main install: each run starts from the same state.
# The ESPGAL_* trace environment passes through unchanged; dumps still go to ESPGAL_DUMP_DIR / cwd.
set -e
NAME=${1:?usage: pcsx2_instance.sh NAME [pcsx2 args...]}; shift
SRC=${PCSX2_HOME:-$HOME/pcsx2/build/bin}
INST=${PCSX2_INST:-$HOME/pcsx2/inst}/$NAME
mkdir -p "$INST"/{inis,memcards,sstates,logs,cache,snaps}
ln -f "$SRC/pcsx2-qt" "$INST/pcsx2-qt"                 # re-linked every launch: follows a rebuild
for d in resources translations patches cheats gamesettings inputprofiles textures covers; do
  [ -e "$SRC/$d" ] && ln -sfn "$SRC/$d" "$INST/$d"
done
# settings + memory cards come from a template taken once from the main install (the main copies
# are rewritten by every run there, so reading them while one runs could catch a partial file)
TPL=${PCSX2_INST:-$HOME/pcsx2/inst}/_template
if [ ! -f "$TPL/inis/PCSX2.ini" ]; then
  mkdir -p "$TPL/inis" "$TPL/memcards"
  cp "$SRC/inis/PCSX2.ini" "$TPL/inis/"; cp "$SRC"/memcards/*.ps2 "$TPL/memcards/"
fi
cp "$SRC/portable.txt" "$INST/portable.txt"
cp "$TPL/inis/PCSX2.ini" "$INST/inis/PCSX2.ini"
# a fixed real-time clock (PCSX2_RTC="YY MM DD hh mm ss", PCSX2_RTC=host for the host clock): the
# game writes the boot time into RAM (0x60E308 and copies), the only bytes that differed between
# otherwise identical runs started at different times
if [ "${PCSX2_RTC:-}" != host ]; then
  read -r Y MO D H MI S <<< "${PCSX2_RTC:-26 11 6 12 0 0}"
  sed -i "/^\[EmuCore\]/a ManuallySetRealTimeClock = true\nRtcYear = $Y\nRtcMonth = $MO\nRtcDay = $D\nRtcHour = $H\nRtcMinute = $MI\nRtcSecond = $S" "$INST/inis/PCSX2.ini"
fi
cp "$TPL"/memcards/*.ps2 "$INST/memcards/"
rm -f "$INST"/cache/* "$INST"/logs/*
exec "$INST/pcsx2-qt" "$@"
