"""Map PS2 (ported C) functions to the 68k routines they were ported from, by comparing the
sets of game-state variables they touch (PS2 globals translated through rammap blocks).
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass
from pathlib import Path

from . import mips_funcs, rammap


@dataclass
class Candidate:
    m68k_start: int
    score: float
    shared: int
    shared_vars: list[int]


def ps2_function_vars(img: bytes, base: int, funcs: list[mips_funcs.Function], blocks: list[rammap.Block]) -> dict[int, set[int]]:
    refs = rammap.ps2_addresses(img, base)
    per_func: dict[int, set[int]] = collections.defaultdict(set)
    # bucket reference sites by function
    starts = sorted(f.start for f in funcs)
    import bisect
    for addr, sites in refs.items():
        m = rammap.translate(addr, blocks)
        if m is None or not (0x800000 <= m < 0x820000):
            continue
        for site in sites:
            i = bisect.bisect_right(starts, site) - 1
            if i >= 0:
                per_func[starts[i]].add(m)
    return per_func


def load_68k_routines(path: Path) -> dict[int, dict]:
    d = json.loads(path.read_text())
    return {int(k, 16): v for k, v in d.items()}


def match_functions(ps2_vars: dict[int, set[int]], routines: dict[int, dict], min_shared: int = 3, top: int = 3) -> dict[int, list[Candidate]]:
    rsets = {s: set(v["ram"]) for s, v in routines.items()}
    inv: dict[int, list[int]] = collections.defaultdict(list)
    for s, vs in rsets.items():
        for v in vs:
            inv[v].append(s)
    out: dict[int, list[Candidate]] = {}
    for f, vs in ps2_vars.items():
        if len(vs) < min_shared:
            continue
        counts: collections.Counter = collections.Counter()
        for v in vs:
            for s in inv.get(v, ()):
                counts[s] += 1
        cands = []
        for s, n in counts.items():
            if n < min_shared:
                continue
            j = n / len(vs | rsets[s])
            cands.append(Candidate(s, j, n, sorted(vs & rsets[s])))
        cands.sort(key=lambda c: (-c.score, -c.shared))
        if cands:
            out[f] = cands[:top]
    return out
