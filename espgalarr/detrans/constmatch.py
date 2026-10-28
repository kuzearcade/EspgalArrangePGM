"""Anchor near-identical Arrange routines by their constant sequence and derive constant patches.

For an Arrange routine whose Arcade twin has the same structure but different semantic
constants (not addresses), and which the verifier could not anchor, find the 68k routine whose
immediate operands contain the Arcade twin's ordered constant sequence (difflib match), accept
it only if unique and strong, then locate each changed constant's occurrence inside the 68k
routine (k-th occurrence of the old value, counting in order) and emit a patch.
"""
from __future__ import annotations

import bisect
import collections
import difflib
import json
import re
from dataclasses import asdict

from ..diff import anchor, mips_funcs
from ..pgm import rom as rom_mod
from ..ps2.overlay import Overlay
from .port import ImmPatch, OUT, WORK, _encode_patch
from .verify import Index, load_listing

IMM_RE = re.compile(r"#(-?)\$([0-9a-f]+)")


def routine_imms(index: Index, starts: list[int]) -> dict[int, list[tuple[int, object]]]:
    """68k routine start -> ordered (value, K68) for every immediate operand."""
    out: dict[int, list] = collections.defaultdict(list)
    for k in index.ks:
        i = bisect.bisect_right(starts, k.addr) - 1
        if i < 0:
            continue
        for sign, h in IMM_RE.findall(k.ops):
            v = int(h, 16)
            out[starts[i]].append((-v if sign else v, k))
    return out


def _norm(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def match_routines(min_frac: float = 0.7, min_consts: int = 2, verbose: bool = True):
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    A = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A8")
    R = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A9")
    fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
    diff = mips_funcs.match(fa, fb)
    starts = sorted(int(k, 16) for k in json.loads((WORK / "pgm" / "routines.json").read_text()))
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    imms = routine_imms(index, starts)
    done = {x["arrange_fn"] for x in json.loads((OUT / "port_patches.json").read_text())}
    patches: list[ImmPatch] = []
    report = []
    for m in diff.matches:
        if m.kind != "near" or m.b.start in done:
            continue
        ca = [_norm(v) for _, op, v in anchor.semantic_consts(m.a)]
        cb = [_norm(v) for _, op, v in anchor.semantic_consts(m.b)]
        if len(ca) != len(cb) or ca == cb or len(ca) < min_consts:
            continue
        changed = [(j, x, y) for j, (x, y) in enumerate(zip(ca, cb)) if x != y]
        scored = []
        for r, lst in imms.items():
            vals = [v for v, _ in lst]
            if not all(x in vals or (x & 0xFFFF) in [w & 0xFFFF for w in vals] for _, x, _ in changed):
                continue
            sm = difflib.SequenceMatcher(None, ca, vals, autojunk=False)
            m_ = sum(b.size for b in sm.get_matching_blocks())
            scored.append((m_, r, sm))
        scored.sort(key=lambda t: -t[0])
        if not scored or scored[0][0] < max(min_consts, min_frac * len(ca)):
            report.append((m.b.start, m.a.start, None, "no strong match"))
            continue
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            report.append((m.b.start, m.a.start, None, f"ambiguous ({scored[0][1]:#x}, {scored[1][1]:#x})"))
            continue
        score, r, sm = scored[0]
        lst = imms[r]
        # map arcade const index -> 68k (value, K68) through the alignment blocks
        jmap = {}
        for b in sm.get_matching_blocks():
            for t in range(b.size):
                jmap[b.a + t] = lst[b.b + t]
        ok = 0
        for j, x, y in changed:
            hit = jmap.get(j)
            if hit is None:
                # unaligned slot: use the routine's k-th occurrence of x (k = occurrence index in ca)
                k = sum(1 for q in ca[:j] if q == x)
                occ = [e for e in lst if e[0] == x or (e[0] & 0xFFFF) == (x & 0xFFFF)]
                if k >= len(occ):
                    continue
                hit = occ[k]
            k68 = hit[1]
            size = k68.mnem.split(".")[1] if "." in k68.mnem else "w"
            pt = _encode_patch(p, k68, size, x, y)
            if pt is None:
                continue
            pt.routine = r
            pt.arrange_fn = m.b.start
            pt.kind = pt.kind
            patches.append(pt)
            ok += 1
        report.append((m.b.start, m.a.start, r, f"{ok}/{len(changed)} constants patched (score {score}/{len(ca)})"))
    if verbose:
        for a, t, r, msg in report:
            print(f"arrange {a:#x} (arcade {t:#x}) -> {r and hex(r)}: {msg}")
    (OUT / "constmatch_patches.json").write_text(json.dumps([asdict(x) for x in patches], indent=0))
    return patches, report


if __name__ == "__main__":
    ps, rs = match_routines()
    print(f"{len(ps)} constant patches from {sum(1 for r in rs if r[2])} constant-matched routines "
          f"({sum(1 for r in rs if not r[2])} unmatched)")
