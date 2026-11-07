#!/bin/bash
# pcsx2_instance.sh [pcsx2 args...]: run PCSX2 from a private data directory, holding one of
# PCSX2_SLOTS (default 4) machine-wide run slots; drop-in for the pcsx2-qt binary in scripts.
#
# PCSX2 in portable mode keeps its settings (inis/PCSX2.ini, rewritten on every exit), memory cards,
# save states, logs and shader cache next to the executable (EmuFolders::AppRoot = the executable's
# directory), so runs sharing ~/pcsx2/build/bin share that state. Every run here gets
# ~/pcsx2/inst/NAME (PCSX2_NAME, default a unique run<PID>_* one, removed by a later launch once that run has ended) with a hard link of the
# binary, links to the read-only resources, a fresh copy of the settings and of the (empty) memory
# cards, and a fixed real-time clock, so each run starts from the same state. Four such runs at once
# gave EE RAM, registers and scratchpad identical to a run alone (40000 frames, 8 dumps).
#
# Env: PCSX2_NAME (instance name, kept after the run), PCSX2_SLOTS (4), PCSX2_NOLOCK=1 (no slot),
# PCSX2_RTC ("YY MM DD hh mm ss", default "26 11 6 12 0 0"; "host" = the host clock).
# The ESPGAL_* trace environment passes through unchanged; dumps still go to ESPGAL_DUMP_DIR / cwd.
set -e
SRC=${PCSX2_HOME:-$HOME/pcsx2/build/bin}
ROOT=${PCSX2_INST:-$HOME/pcsx2/inst}
LOCKS=${PCSX2_LOCKS:-/home/vboxuser/EspgaludaArrangePGM/work/scratch}
NAME=${PCSX2_NAME:-}
[ -z "$NAME" ] && { NAME=run$$_$(date +%s%N); }
INST=$ROOT/$NAME
# PCSX2 is exec'd (same PID, so a caller's timeout / kill reaches it); unnamed instances of runs that
# have ended (their PID is gone) are removed here, by the next launch
for d in "$ROOT"/run[0-9]*_*; do
  [ -d "$d" ] || continue
  pid=${d##*/run}; pid=${pid%%_*}
  kill -0 "$pid" 2>/dev/null || rm -rf "$d"
done

# a run slot: the first free one of pcsx2.slot0..N-1 (polled), held on fd 9 until PCSX2 exits
if [ "${PCSX2_NOLOCK:-}" != 1 ]; then
  N=${PCSX2_SLOTS:-4}
  while :; do
    for ((i = 0; i < N; i++)); do
      exec 9> "$LOCKS/pcsx2.slot$i"
      flock -n 9 && break 2
      exec 9>&-
    done
    sleep 5
  done
fi

# settings + memory cards come from a template taken once from the main install
TPL=$ROOT/_template
if [ ! -f "$TPL/inis/PCSX2.ini" ]; then
  mkdir -p "$TPL/inis" "$TPL/memcards"
  cp "$SRC/inis/PCSX2.ini" "$TPL/inis/"; cp "$SRC"/memcards/*.ps2 "$TPL/memcards/"
fi
mkdir -p "$INST"/{inis,memcards,sstates,logs,cache,snaps}
ln -f "$SRC/pcsx2-qt" "$INST/pcsx2-qt"                 # re-linked every launch: follows a rebuild
for d in resources translations patches cheats gamesettings inputprofiles textures covers; do
  [ -e "$SRC/$d" ] && ln -sfn "$SRC/$d" "$INST/$d"
done
cp "$SRC/portable.txt" "$INST/portable.txt"
cp "$TPL/inis/PCSX2.ini" "$INST/inis/PCSX2.ini"
cp "$TPL"/memcards/*.ps2 "$INST/memcards/"
rm -f "$INST"/cache/* "$INST"/logs/*
# the game writes the boot time into RAM (0x60E308 and copies): the only bytes that differed between
# otherwise identical runs started at different times
if [ "${PCSX2_RTC:-}" != host ]; then
  read -r Y MO D H MI S <<< "${PCSX2_RTC:-26 11 6 12 0 0}"
  sed -i "/^\[EmuCore\]/a ManuallySetRealTimeClock = true\nRtcYear = $Y\nRtcMonth = $MO\nRtcDay = $D\nRtcHour = $H\nRtcMinute = $MI\nRtcSecond = $S" "$INST/inis/PCSX2.ini"
fi

exec "$INST/pcsx2-qt" "$@"
