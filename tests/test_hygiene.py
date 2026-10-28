"""No original game data may be tracked in git. Skipped when inputs are absent."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INPUTS = [ROOT / "work" / "pgm" / "espgalbl" / "espgaluda_u8.bin",
          ROOT / "work" / "ps2" / "disc" / "SLPS_253.52",
          ROOT / "work" / "ps2" / "disc" / "A0A0" / "A0A9"]


@pytest.mark.skipif(not all(p.exists() for p in INPUTS), reason="inputs not extracted")
def test_tracked_files_contain_no_input_bytes():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    idx = set()
    for p in INPUTS:
        d = p.read_bytes()
        for o in range(0, len(d) - 32, 16):
            b = d[o:o + 32]
            if len(set(b)) > 6:
                idx.add(b)
    for f in tracked:
        data = (ROOT / f).read_bytes()
        for o in range(0, max(0, len(data) - 32)):
            assert data[o:o + 32] not in idx, f"{f} contains a 32-byte run from an input at {o:#x}"
