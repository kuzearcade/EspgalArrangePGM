# Sourced by the tools/compare scripts: where the inputs, tools and outputs are. Override any of
# these in the environment.
REPO=${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
WORK=${WORK:-$REPO/work}                          # git-ignored inputs (ISO, MAME config)
COMPARE_DIR=${COMPARE_DIR:-$WORK/compare}         # tables and MAME run outputs (git-ignored)
PS2_TRACES=${PS2_TRACES:-$COMPARE_DIR/ps2}        # PCSX2 trace directories (watch.txt, pad.txt)
MAME=${MAME:-$HOME/Arcade-NMK16_MiSTer/mame/mame} # MAME with the espgalarr set (patches/mame)
MAME_CFG=${MAME_CFG:-$WORK/mame/cfg}
PCSX2=${PCSX2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pcsx2_instance.sh}   # PCSX2 (espgal-trace.diff) via a run slot + private data dir
PS2_ISO=${PS2_ISO:-$WORK/ps2/Espgaluda (Japan).iso}
LUA=$REPO/tests/lua
CMP=$REPO/tools/compare
MEM=${MEM:-8G}                                    # memory limit of each emulator run

# mame_run NVRAM_DIR TIMEOUT LUA_SCRIPT ROMPATH: one memory-bounded headless MAME run of espgalarr.
# MAMEARGS adds options (e.g. "-debug -debugger none -log" for breakpoints in an EXTRA_LUA; the
# run then starts in NVRAM_DIR, where error.log lands)
mame_run() {
  rm -rf "$1"; mkdir -p "$1"
  ( cd "$1" && systemd-run --user --scope -q -p MemoryMax=$MEM -p MemorySwapMax=0 timeout "$2" "$MAME" espgalarr \
    -rompath "$4" -video none -sound none -nothrottle -window -cfg_directory "$MAME_CFG" \
    -nvram_directory "$1" -autoboot_script "$3" $MAMEARGS > /dev/null 2>&1 )
}
