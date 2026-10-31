#!/usr/bin/env python3
"""Arrange rule checks in MAME (tests/lua/rules.lua) and in PCSX2 (the PS2 Arrange reference).

  tools/rules_check.py mame <machine> <rompath> [scenario ...]
  tools/rules_check.py ps2 <run dir>         (PCSX2 run with the probe pad script, then readout)
  tools/rules_check.py ps2-tap <run dir>     (barrier tap only, RAM every 4 frames)
  tools/rules_check.py ps2-read <run dir>    (readout of an existing run)

MAME prints one line per scenario result; the PS2 readout prints the same quantities from EE RAM
dumps (the Arrange player record of P1, lives and score, all in 68k byte order).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAME = Path(os.environ.get("ESPGAL_MAME", Path.home() / "Arcade-NMK16_MiSTer/mame/mame"))
PCSX2 = Path(os.environ.get("ESPGAL_PCSX2", Path.home() / "pcsx2/build/bin/pcsx2-qt"))
ISO = ROOT / "work/ps2/Espgaluda (Japan).iso"

# name: (rule, environment); buttons are the arcade layout (2 = B Kakusei, 3 = C barrier)
SCENARIOS = {
    "start": ("start", {}),
    "extend": ("extend", {}),
    "continue": ("continue", {}),
    # the barrier is on D in Arrange (Button 4, in the Service port) and on C in the arcade
    "barrier-tap": ("barrier", {"ESPGAL_BUTTON": "Service:P1 Button 4", "ESPGAL_HOLD": "6", "ESPGAL_GAUGE": "fa0"}),
    "barrier-hold": ("barrier", {"ESPGAL_BUTTON": "Service:P1 Button 4", "ESPGAL_HOLD": "120", "ESPGAL_GAUGE": "a60"}),
    "barrier-tap-c": ("barrier", {"ESPGAL_BUTTON": "P1 Button 3", "ESPGAL_HOLD": "6", "ESPGAL_GAUGE": "fa0"}),
    "barrier-hold-c": ("barrier", {"ESPGAL_BUTTON": "P1 Button 3", "ESPGAL_HOLD": "120", "ESPGAL_GAUGE": "a60"}),
    "kakusei": ("kakusei", {"ESPGAL_BUTTON": "P1 Button 2", "ESPGAL_GEMS": "299", "ESPGAL_GAUGE": "418"}),
}


def mame(machine: str, rompath: str, names: list[str]) -> None:
    for name in names or list(SCENARIOS):
        rule, env = SCENARIOS[name]
        nvram = tempfile.mkdtemp(prefix="nvram_", dir=ROOT / "work")   # work RAM is battery-backed
        cmd = [str(MAME), machine, "-rompath", rompath, "-video", "none", "-sound", "none",
               "-nothrottle", "-window", "-cfg_directory", str(ROOT / "work/mame/cfg"),
               "-nvram_directory", nvram,
               "-autoboot_script", str(ROOT / "tests/lua/rules.lua")]
        r = subprocess.run(cmd, env={**os.environ, "ESPGAL_RULE": rule, **env}, cwd=ROOT,
                           capture_output=True, text=True, timeout=1800)
        shutil.rmtree(nvram, ignore_errors=True)
        res = [l[5:] for l in r.stdout.splitlines() if l.startswith("RULE ")]
        print(f"{name}: " + "  ".join(res), flush=True)


# PS2 probe: menu path to Arrange stage 1 with Chihiro (tools/pcsx2_pad_arrange_stage1.txt
# without the held shot), then the barrier (Cross: tapped at 4300, held 4500-4620) and Kakusei
# (Triangle at 4800). Pad bits as in patches/pcsx2/espgal-trace.diff.
PAD_TAIL = [(4300, 0x40), (4306, 0), (4500, 0x40), (4620, 0), (4800, 0x10), (4806, 0)]
DUMPS = [3495, 4295, 4302, 4310, 4400, 4495, 4502, 4520, 4540, 4560, 4580, 4600, 4619, 4640,
         4700, 4795, 4810, 4830, 4850, 4870, 4890, 4910, 4950, 5000]
P1, LIVES, SCORE = 0xC310C0, 0x642292, 0xC2B0D0


# Tap-only probe for the barrier duration: Cross tapped at 4300, RAM every 4 frames after.
TAP_TAIL = [(4300, 0x40), (4306, 0)]
TAP_DUMPS = [4295] + list(range(4302, 4560, 4))


def ps2(run: Path, tail=PAD_TAIL, dumps=DUMPS) -> None:
    run = run.resolve()                 # PCSX2 changes directory; dump paths must be absolute
    run.mkdir(parents=True, exist_ok=True)
    pad = [l for l in (ROOT / "tools/pcsx2_pad_arrange_stage1.txt").read_text().splitlines()
           if l.strip() and int(l.split()[0]) < 3400]
    pad += [f"{f} {m:x}" for f, m in tail]
    (run / "pad.txt").write_text("\n".join(pad) + "\n")
    env = {**os.environ, "ESPGAL_PAD": str(run / "pad.txt"), "ESPGAL_DUMP_DIR": str(run),
           "ESPGAL_DUMP": ",".join(map(str, dumps)), "ESPGAL_QUIT": str(dumps[-1] + 10),
           "QT_QPA_PLATFORM": "wayland"}
    with open(run / "run.log", "w") as log:
        subprocess.run([str(PCSX2), "-batch", "-nogui", "-fastboot", "--", str(ISO)], env=env,
                       cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    ps2_read(run)


def ps2_read(run: Path) -> None:
    for f in sorted(run.glob("ee_*.bin"), key=lambda p: int(re.findall(r"\d+", p.name)[0])):
        b = f.read_bytes()
        w = lambda a: int.from_bytes(b[a:a + 2], "big")         # noqa: E731
        print(f"{f.stem[3:]:>5}: lives {w(LIVES)} score {int.from_bytes(b[SCORE + 2:SCORE + 6], 'big') * 10}"
              f" gauge {w(P1 + 0x24):#x} barrier {b[P1 + 0x5F]} kakusei {b[P1 + 0x5E]}"
              f" gems {w(P1 + 0x6E)} input {b[P1 + 0x18]:#x}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "mame" and len(a) >= 3:
        mame(a[1], a[2], a[3:])
    elif a and a[0] == "ps2" and len(a) == 2:
        ps2(Path(a[1]))
    elif a and a[0] == "ps2-tap" and len(a) == 2:
        ps2(Path(a[1]), TAP_TAIL, TAP_DUMPS)
    elif a and a[0] == "ps2-read" and len(a) == 2:
        ps2_read(Path(a[1]))
    else:
        sys.exit(__doc__)
