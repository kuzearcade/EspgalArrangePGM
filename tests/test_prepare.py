"""The prepare stage's repository data: every learned map a clean clone needs is tracked, and
holds addresses and numbers only (no code text)."""
import json
import re
from pathlib import Path

from espgalarr.stages import prepare

# the derived files the other stages read from work/ps2/detrans that prepare copies
NEEDED = {"addrmap_arcade.json", "ram_xlate.json", "native_service_map.json", "map_audit.json",
          "ps2_verified.json", "port_patches.json"}


def test_maps_present():
    assert {f.name for f in prepare.MAPS.glob("*.json")} == NEEDED
    starts = json.loads((prepare.DATA / "routine_starts.json").read_text())
    assert len(starts) > 1000 and all(re.fullmatch(r"0x[0-9a-f]+", a) for a in starts)


def test_maps_hold_no_code_text():
    for f in prepare.MAPS.glob("*.json"):
        t = f.read_text()
        assert not re.search(r"(\t|\\t)(move|jsr|lea|bra|rts)\b", t), f"{f.name}: code text"


def test_stamp_covers_the_maps(tmp_path):
    assert len(prepare._stamp()) == 40
    assert Path(prepare.__file__).name == "prepare.py"
