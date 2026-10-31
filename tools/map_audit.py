#!/usr/bin/env python3
"""Audit the learned PS2 -> 68k function map for near-identical 68k copies.

The learning anchors a PS2 function by instruction shapes, so where the 68k program has
copies of a routine that differ only in constants (sprite layers, per-player routines, two
angle routines) it can pick the wrong copy (see detrans/verify.FUNC_FIXES). For every mapped
PS2 function (ELF and the Arrange main overlay) whose 68k routine has same-shape copies, the
de-translated function is scored against each copy (exact instruction matches, constants
included, as a similarity ratio); an entry whose copy scores lower than another is reported.
Copies that differ only in PS2-native constants (the sprite layers' list buffers) score the
same and are not told apart here.

  tools/map_audit.py [--json out.json]
"""
from __future__ import annotations

import collections
import difflib
import json
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from espgalarr.detrans import core                                    # noqa: E402
from espgalarr.detrans.port import _shape_key                         # noqa: E402
from espgalarr.detrans.verify import (WORK, ElfText, Index, LearnedMap, _reader,  # noqa: E402
                                      load_listing, recovered_canon)
from espgalarr.ps2.overlay import Overlay                             # noqa: E402

TERM = ("rts", "rte", "jmp", "bra")


def region(index: Index, pos: int, limit: int = 200) -> int:
    """Instructions from pos up to and including the first terminator."""
    for k in range(pos, min(pos + limit, len(index.ks))):
        if index.ks[k].canon.mnem in TERM:
            return k - pos + 1
    return min(limit, len(index.ks) - pos)


def main(argv: list[str]) -> int:
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    lm = LearnedMap.load(WORK / "ps2" / "detrans" / "addrmap_arrange.json")
    elf = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
    main_ov = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A9")
    from espgalarr.diff import mips_funcs
    sizes = {f.start: f.size for f in mips_funcs.split_functions(elf.image, elf.image_base, elf.header.text_end)}
    msizes = {f.start: f.size for f in mips_funcs.split_functions(main_ov.image, main_ov.image_base,
                                                                  main_ov.header.text_end)}
    sk = [_shape_key(k.canon) for k in index.ks]
    grams: dict = collections.defaultdict(list)
    for i in range(len(sk) - 3):
        grams[tuple(sk[i:i + 4])].append(i)

    def alarm(*_):
        raise TimeoutError
    signal.signal(signal.SIGALRM, alarm)
    out = []
    checked = 0
    claims = collections.Counter(lm.funcs.values())
    for f, r in sorted(lm.funcs.items()):
        src, size = (elf, sizes.get(f)) if f in sizes else (main_ov, msizes.get(f))
        if size is None:
            continue
        try:
            pos = index.pos_of(r)
        except Exception:  # noqa: BLE001
            continue
        if pos is None or index.ks[pos].addr != r:
            continue
        n = region(index, pos)
        if n < 3:
            continue
        k = min(n, 8)               # copies may end differently: the same first shapes
        seq = sk[pos:pos + k]
        cands = [q for q in grams.get(tuple(seq[:4]), ()) if sk[q:q + k] == seq] if k >= 4 else []
        if len(cands) < 2:
            continue
        signal.alarm(10)
        try:
            res = core.detranslate(src.read(f, size), f, lm, read_word=_reader(src))
        except Exception:  # noqa: BLE001
            continue
        finally:
            signal.alarm(0)
        checked += 1
        keys = [c.key for c in (recovered_canon(i) for i in res.insns if not i.is_label and not i.is_comment)
                if c is not None]

        def score(q):
            b = [x.canon.key for x in index.ks[q:q + region(index, q)]]
            return round(difflib.SequenceMatcher(None, keys, b, autojunk=False).ratio(), 3)
        scores = {index.ks[q].addr: score(q) for q in cands}
        best = max(scores.values())
        winners = [a for a, s in scores.items() if s == best]
        if scores[r] < best:
            out.append({"ps2": f"{f:#x}", "mapped": f"{r:#x}", "best": [f"{a:#x}" for a in winners],
                        "scores": {f"{a:#x}": s for a, s in sorted(scores.items())}, "n": n,
                        "claims": claims[r]})
            print(f"{f:#x} -> {r:#x} ({claims[r]} claims) scores {scores[r]}; better: "
                  + ", ".join(f"{a:#x} ({best})" for a in winners), flush=True)
    print(f"{checked} mapped functions with same-shape 68k copies checked, {len(out)} better matched elsewhere")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
