"""No tracked file holds original game bytes or text (tools/hygiene_check.py; about 3 minutes).
Skipped when the inputs are absent, or with ESPGALARR_SKIP_HYGIENE=1."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


@pytest.mark.skipif(os.environ.get("ESPGALARR_SKIP_HYGIENE") == "1", reason="ESPGALARR_SKIP_HYGIENE=1")
def test_tracked_files_hold_no_original_bytes(capsys):
    import hygiene_check
    if hygiene_check.inputs(str(ROOT)) is None:
        pytest.skip("inputs not present (mame_roms/, work/ps2/disc, work/ps2/decoded)")
    assert hygiene_check.main([]) == 0, capsys.readouterr().out
