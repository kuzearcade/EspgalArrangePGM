PCSX2 reference runs (PS2 Arrange as ground truth)

Build: PCSX2 v2.9.148 + patches/pcsx2/espgal-trace.diff (environment-driven automation: scripted
pad, frame RAM/register dumps, screenshots, function-entry hook dumps, quit at frame). On a
Wayland-only Qt build, pcsx2-qt/QtUtils.cpp's X11 compositing probe must also be guarded (done
locally in ~/pcsx2/src). BIOS: a Japanese image (ps2-0200j-20040614 works; the 0210j dump in the
supplied set does not boot). Settings: portable mode, inis/PCSX2.ini with [UI] SettingsVersion=1.

Menu path (pcsx2_pad_arrange_stage1.txt, frames at 60 fps with fast boot): Start at the title
(2150), Down x2 + Circle = Arrange Mode (2300), Circle = Game Start (2400), Circle = Chihiro
(3000), Cross held from 3400 (shot); stage 1 boss by ~6000.

Check: tools/ps2check_dumps.py <dump dir>  (MIPS interpreter truth vs de-translated 68k code).
