"""Stage 0: the derived files the other stages read from work/, made from the inputs and the
repository when they are missing or out of date.

* Learned maps (addresses and numbers only, espgalarr/data/detrans/ and data/routine_starts.json):
  copied into work/ when missing. They come from development runs (the arcade verifier's
  anchoring, the RAM map, the native-service map, tools/map_audit.py, the PCSX2 RAM-dump check,
  the near-routine constant patches) and are kept in the repository so a build does not need
  them again.
* The program ROM listing (work/pgm/dis/p_rom.lst, espgalarr.pgm.disasm).
* The de-translated Arrange and ELF routines (work/ps2/detrans/arrange_*.s, elf_*.s, with
  addrmap_arrange.json and summary.json; espgalarr.detrans.apply), re-made whenever the
  de-translator's code or the learned maps change (a stamp of both); the port caches keyed on
  the code alone (every work/ps2/detrans/*.json with a "key") go with them.

The de-translator modules read work/ relative to the current folder: run the build from the
repository folder (README).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
DATA = PKG / "data"
MAPS = DATA / "detrans"
STAMP = "listings.stamp"


def _stamp() -> str:
    h = hashlib.sha1()
    for d in ("detrans", "diff", "ps2"):
        for f in sorted((PKG / d).glob("*.py")):
            h.update(f.name.encode() + f.read_bytes())
    for f in sorted(MAPS.glob("*.json")):
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


def run(state) -> None:
    work = state.work
    assert work.resolve() == Path("work").resolve(), \
        f"run the build from the folder holding work/ ({work.parent}): the de-translator reads ./work"
    det = work / "ps2" / "detrans"
    det.mkdir(parents=True, exist_ok=True)
    copied = [f.name for f in sorted(MAPS.glob("*.json")) if not (det / f.name).exists()]
    for name in copied:
        shutil.copyfile(MAPS / name, det / name)
    routines = work / "pgm" / "routines.json"
    if not routines.exists():
        starts = json.loads((DATA / "routine_starts.json").read_text())
        routines.write_text(json.dumps({a: {} for a in starts}))
        copied.append("routines.json")
    if copied:
        state.note(f"prepare: learned maps from the repository: {', '.join(copied)}")
    lst = work / "pgm" / "dis" / "p_rom.lst"
    if not lst.exists():
        from ..pgm import disasm
        disasm.main([])
        state.note("prepare: program ROM listing written")
    stamp = _stamp()
    sf = det / STAMP
    if not (sf.exists() and sf.read_text() == stamp and any(det.glob("arrange_*.s"))):
        for f in list(det.glob("arrange_*.s")) + list(det.glob("elf_*.s")):
            f.unlink()
        dropped = 0
        for f in det.glob("*.json"):
            try:
                keyed = isinstance(json.loads(f.read_text()), dict) and "key" in json.loads(f.read_text())
            except ValueError:
                keyed = False
            if keyed:
                f.unlink()
                dropped += 1
        from ..detrans import apply
        apply.main([])
        sf.write_text(stamp)
        state.note(f"prepare: Arrange routines de-translated ({len(list(det.glob('arrange_*.s')))} listings; "
                   f"{dropped} port caches dropped)")
