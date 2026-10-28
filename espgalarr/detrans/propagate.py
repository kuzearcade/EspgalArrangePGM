"""Call-graph propagation of PS2 -> 68k function anchors.

If a translated PS2 function F is anchored to 68k routine R and F's calls (jal targets in
address order) and R's calls (jsr/bsr targets in address order, up to the next routine start)
have the same count, the i-th callees correspond. Pairs that agree with an existing anchor
confirm it; new pairs are added only when every vote for that PS2 callee agrees. Iterated to
a fixed point.
"""
from __future__ import annotations

import bisect
import collections
import difflib
import json
import re
from pathlib import Path

from ..diff import mips_funcs
from ..ps2.overlay import Overlay
from .mips import decode_all
from .verify import WORK, ElfText, LearnedMap

LISTING = WORK / "pgm" / "dis" / "p_rom.lst"


def m68k_calls() -> dict[int, list[int]]:
    """68k routine start -> ordered call targets (jsr/bsr with absolute or pc targets)."""
    starts = sorted(int(k, 16) for k in json.loads((WORK / "pgm" / "routines.json").read_text()))
    calls: dict[int, list[int]] = collections.defaultdict(list)
    pat = re.compile(r"^([0-9a-f]{6})\s+\S+\s+(jsr|bsr\.[bw]|bsr)\s+\$([0-9a-f]+)")
    with open(LISTING) as f:
        for line in f:
            m = pat.match(line)
            if not m:
                continue
            a = int(m.group(1), 16)
            i = bisect.bisect_right(starts, a) - 1
            if i >= 0:
                calls[starts[i]].append(int(m.group(3), 16))
    return calls


def ps2_calls(images) -> dict[int, list[int]]:
    out = {}
    for ov in images:
        for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end):
            out[f.start] = [i.target for i in decode_all(ov.read(f.start, f.size), f.start) if i.op == "jal"]
    return out


def propagate(lm: LearnedMap, images, max_iters: int = 6, verbose: bool = True) -> int:
    k_calls = m68k_calls()
    p_calls = ps2_calls(images)
    added_total = 0
    for it in range(max_iters):
        votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        for f, r in list(lm.funcs.items()):
            pc = p_calls.get(f)
            kc = k_calls.get(r)
            if not pc or not kc:
                continue
            if len(pc) == len(kc):
                for x, y in zip(pc, kc):
                    votes[x][y] += 1
                continue
            # align on callees already mapped on both sides; pair unknowns in equal-length gaps
            a_tok = [lm.funcs.get(x, ("?", x)) for x in pc]
            sm = difflib.SequenceMatcher(None, a_tok, kc, autojunk=False)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op == "replace" and (i2 - i1) == (j2 - j1):
                    for x, y in zip(pc[i1:i2], kc[j1:j2]):
                        if x not in lm.funcs:
                            votes[x][y] += 1
        added = 0
        conflicts = 0
        for x, c in votes.items():
            if len(c) != 1:
                conflicts += 1
                continue
            y = next(iter(c))
            if x in lm.funcs:
                continue
            lm.funcs[x] = y
            added += 1
        added_total += added
        if verbose:
            print(f"iteration {it}: {added} new anchors ({conflicts} ambiguous callees), {len(lm.funcs)} total", flush=True)
        if not added:
            break
    return added_total


def code_pointer_pass(lm: LearnedMap, ov, index, timeout_s: int = 10, verbose: bool = True) -> int:
    """Pair immediate operands that are code pointers (PS2 function start <-> 68k routine
    start) on instruction pairs aligned by the verifier, e.g. `move.l #handler,$22(a6)`."""
    import signal
    from . import core
    from .verify import _reader, compare_function, recovered_canon

    starts68 = {int(k, 16) for k in json.loads((WORK / "pgm" / "routines.json").read_text())}
    funcs = mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)
    elf = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
    ps2_starts = {f.start for f in funcs} | {f.start for f in mips_funcs.split_functions(elf.image, elf.image_base, elf.header.text_end)}
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    anchors: dict[int, int] = {}

    class _T(Exception):
        pass

    def _alarm(*_):
        raise _T()
    old = signal.signal(signal.SIGALRM, _alarm)
    try:
        for k, f in enumerate(funcs):
            if verbose and k % 500 == 0:
                print(f"  code pointers: {k}/{len(funcs)}", flush=True)
            if f.size < 16:
                continue
            signal.alarm(timeout_s)
            try:
                res = core.detranslate(ov.read(f.start, f.size), f.start, lm, read_word=_reader(ov))
                rep = compare_function(index, res)
            except Exception:  # noqa: BLE001  (timeouts and decode failures are skipped)
                continue
            finally:
                signal.alarm(0)
            if (rep.anchor is not None and f.start not in lm.funcs and rep.pairs
                    and rep.pairs[0][0].addr == rep.first_insn_addr and rep.coverage >= 0.5):
                anchors[f.start] = rep.pairs[0][1].addr
            for ins, k68 in rep.pairs:
                c = recovered_canon(ins)
                if c is None:
                    continue
                for oa, ob in zip(c.ops, k68.canon.ops):
                    if oa[0] in ("imm", "immf") and ob[0] == "imm":
                        x, y = oa[1], ob[1]
                        if x in ps2_starts and y in starts68 and x not in lm.funcs:
                            votes[x][y] += 1
    finally:
        signal.signal(signal.SIGALRM, old)
    added = 0
    for x, a in anchors.items():
        lm.funcs[x] = a
        added += 1
    for x, c in votes.items():
        if len(c) == 1 and x not in lm.funcs:
            lm.funcs[x] = next(iter(c))
            added += 1
    if verbose:
        print(f"  direct anchors {len(anchors)}, code-pointer anchors {added - len(anchors)}", flush=True)
    return added


def pointer_table_pass(lm: LearnedMap, ov, verbose: bool = True) -> int:
    """Pair 68k code-pointer tables with their relocated copies in the overlay's data.

    A 68k pointer table is a run of >= 2 consecutive longs that are routine starts. Its copy
    in the overlay sits between the verbatim-matched data segments around it (same gaps on
    both sides); entries there are little-endian PS2 addresses. Each slot votes
    PS2 function -> 68k routine; only unanimous pairs are kept."""
    import struct
    from ..diff import datablock as db
    from ..pgm import rom as rom_mod

    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    starts68 = {int(k, 16) for k in json.loads((WORK / "pgm" / "routines.json").read_text())}
    elf = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
    ps2_starts = {f.start for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)}
    ps2_starts |= {f.start for f in mips_funcs.split_functions(elf.image, elf.image_base, elf.header.text_end)}
    segs = sorted(db.match_segments(p, ov.image, 0, 0x200000, min_len=16), key=lambda x: x.rom_off)
    seg_lo = [x.rom_off for x in segs]
    # 68k pointer tables
    tables = []
    i = 0
    while i < 0x200000 - 8:
        n = 0
        while i + 4 * (n + 1) <= 0x200000 and struct.unpack_from(">I", p, i + 4 * n)[0] in starts68:
            n += 1
        if n >= 2:
            tables.append((i, n))
            i += 4 * n
        else:
            i += 2
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    placed = 0
    for t, n in tables:
        k = bisect.bisect_right(seg_lo, t) - 1
        if k < 0 or k + 1 >= len(segs):
            continue
        before, after = segs[k], segs[k + 1]
        if before.rom_end > t or after.rom_off < t + 4 * n:
            continue
        gap_rom = after.rom_off - before.rom_end
        gap_ov = after.ov_off - before.ov_end
        if gap_rom != gap_ov or gap_rom > 0x4000:
            continue
        cand = before.ov_end + (t - before.rom_end)
        placed += 1
        for j in range(n):
            v = struct.unpack_from("<I", ov.image, cand + 4 * j)[0]
            if v in ps2_starts:
                votes[v][struct.unpack_from(">I", p, t + 4 * j)[0]] += 1
    added = 0
    for x, c in votes.items():
        if len(c) == 1 and x not in lm.funcs:
            lm.funcs[x] = next(iter(c))
            added += 1
    if verbose:
        print(f"  pointer tables: {len(tables)} in ROM, {placed} placed in overlay, {added} anchors", flush=True)
    return added


def main() -> int:
    path = Path(WORK / "ps2" / "detrans" / "addrmap_arcade.json")
    lm = LearnedMap.load(path)
    images = [Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A8"), ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")]
    from .verify import Index, load_listing
    index = Index(load_listing(LISTING))
    total = 0
    for rnd in range(3):
        n_cp = pointer_table_pass(lm, images[0])
        n_cp += code_pointer_pass(lm, images[0], index) if rnd == 0 else 0
        n_pr = propagate(lm, images, verbose=False)
        lm.save(path)
        print(f"round {rnd}: {n_cp} code-pointer anchors, {n_pr} call-graph anchors, {len(lm.funcs)} total", flush=True)
        total += n_cp + n_pr
        if not (n_cp + n_pr):
            break
    print(f"propagation added {total} anchors; map saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
