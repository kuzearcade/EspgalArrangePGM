"""De-translate the Arrange overlay.

For every function of ov_arrange.bin: functions that `mips_funcs.match` pairs *exactly* with
an arcade function whose 68k original is known are reported as unchanged; the others
(near matches and arrange-only code) are de-translated into
work/ps2/detrans/arrange_<addr>.s together with the recovered references and callees.

The arrange overlay lays its data out differently from the arcade one, so its address map
is derived from (a) the ROM-table copies found by datablock.match_segments, (b) the
arcade map (work/ps2/detrans/addrmap_arcade.json, learned by verify.py) transported
through the reference lists of matched function pairs, and (c) the callee map of matched
pairs.

Usage: python3 -m espgalarr.detrans.apply [--limit N]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import struct
from pathlib import Path

from ..diff import datablock as db, mips_funcs
from ..pgm import rom as rom_mod
from ..ps2.overlay import Overlay
from . import core
from .mips import decode_all
from .verify import ROOT, WORK, LearnedMap

OUT = WORK / "ps2" / "detrans"


def is_translated(code: bytes) -> bool:
    """Does the function touch the emulated 68k register file or the CCR?"""
    for ins in decode_all(code, 0):
        if ins.op in ("lw", "lhu", "lbu", "lh", "lb", "sw", "sh", "sb") and ins.rs == 1 and 0x10A0 <= ins.imm < 0x1120:
            return True
        if ins.op in ("lhu", "sh") and ins.rs == 28 and ins.imm == -0x7EE4:
            return True
    return False


class ArrangeMap(LearnedMap):
    def __init__(self, samples, funcs, segs, image_base):
        super().__init__(samples, funcs)
        self.segs = sorted(segs, key=lambda s: s.ov_off)
        self.seg_lo = [s.ov_off for s in self.segs]
        self.image_base = image_base

    def map_const(self, v: int):
        o = v - self.image_base
        i = bisect.bisect_right(self.seg_lo, o) - 1
        if i >= 0 and self.segs[i].ov_off <= o < self.segs[i].ov_end:
            return self.segs[i].rom_off + (o - self.segs[i].ov_off), "rom", 1.0
        return super().map_const(v)


def build(limit: int | None = None, verbose: bool = True):
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    A = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A8")
    R = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A9")
    fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
    diff = mips_funcs.match(fa, fb)
    lm = LearnedMap.load(OUT / "addrmap_arcade.json")
    segs = db.match_segments(p, R.image, 0, 0x200000, min_len=48)
    # transport the arcade map through matched pairs
    samples: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    funcs: dict[int, int] = {}
    by_a = {}
    for m in diff.matches:
        by_a[m.a.start] = m
        if len(m.a.refs) == len(m.b.refs):
            for x, y in zip(m.a.refs, m.b.refs):
                t, kind, conf = lm.map_const(x)
                if t is not None and conf >= 0.9:
                    samples[y][t] += 1
        if m.a.start in lm.funcs:
            funcs[m.b.start] = lm.funcs[m.a.start]
    for k, v in lm.funcs.items():          # ELF-resident callees are shared
        if k < A.image_base:
            funcs.setdefault(k, v)
    flat = {y: c.most_common(1)[0][0] for y, c in samples.items() if len(c) == 1 or c.most_common(1)[0][1] >= 2}
    amap = ArrangeMap(flat, funcs, segs, R.image_base)
    OUT.mkdir(parents=True, exist_ok=True)
    amap.save(OUT / "addrmap_arrange.json")
    by_b = diff.by_b()
    summary = {"unchanged": [], "near": [], "new": [], "native": 0, "low_conf": []}
    n = 0
    for f in fb:
        if f.size < 16:
            continue
        code = R.read(f.start, f.size)
        if not is_translated(code):
            summary["native"] += 1
            continue
        m = by_b.get(f.start)
        if m is not None and m.kind == "exact" and m.a.start in lm.funcs:
            summary["unchanged"].append((f.start, m.a.start, lm.funcs[m.a.start]))
            continue
        res = core.detranslate(code, f.start, amap)
        kind = "near" if m is not None else "new"
        known = [i for i in res.insns if i.mnem and not i.is_label]
        hi = sum(1 for i in known if i.conf >= 0.95)
        unknown = res.n_unknown
        total = len(known) + unknown
        frac = hi / total if total else 0.0
        refs = sorted({int(o[1:-2], 16) for i in known for o in i.ops if o.startswith("$") and o.endswith(".l")} |
                      {int(o[1:], 16) for i in known for o in i.ops if o.startswith("$") and not o.endswith(".l") and o[1:].isalnum()})
        ram = [r for r in refs if 0x800000 <= r < 0x820000]
        rom = [r for r in refs if r < 0x200000]
        other = [r for r in refs if r not in ram and r not in rom]
        callees = sorted(set(res.calls))
        lines = [f"; arrange function {f.start:#x}-{f.end:#x} ({f.size} bytes): {kind}"]
        if m is not None:
            twin = lm.funcs.get(m.a.start)
            lines.append(f"; arcade twin {m.a.start:#x} ({m.kind}, ratio {m.ratio:.2f})" + (f", 68k original {twin:#x}" if twin else ""))
        lines.append(f"; instructions: {len(known)} recovered ({hi} with confidence >= 0.95), {unknown} unknown")
        lines.append("; 68k RAM references: " + ", ".join(f"${r:x}" for r in ram))
        lines.append("; ROM table references: " + ", ".join(f"${r:x}" for r in rom))
        if other:
            lines.append("; unmapped PS2 references: " + ", ".join(f"${r:x}" for r in other))
        lines.append("; callees: " + ", ".join(amap.code_label(c) + (f" (elf)" if c < R.image_base else "") for c in callees))
        lines.append("")
        lines.append(res.text(header=False))
        (OUT / f"arrange_{f.start:x}.s").write_text("\n".join(lines))
        summary[kind].append((f.start, f.size, frac, len(known), unknown))
        if frac < 0.95:
            summary["low_conf"].append((f.start, f.size, frac, len(known), unknown, kind))
        n += 1
        if limit and n >= limit:
            break
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    s = build(a.limit)
    print(f"unchanged (exact twin with known 68k original): {len(s['unchanged'])}")
    print(f"native (no register-file access): {s['native']}")
    for kind in ("near", "new"):
        lst = s[kind]
        tot = sum(x[3] + x[4] for x in lst)
        unk = sum(x[4] for x in lst)
        print(f"{kind}: {len(lst)} functions, {tot} instructions, {unk} unknown ({(tot - unk) / tot if tot else 0:.1%} recovered)")
    print(f"functions below 95% confidence: {len(s['low_conf'])}")
    for x in sorted(s["low_conf"], key=lambda x: x[2])[:40]:
        print(f"  {x[0]:#x} {x[1]:6d} B  {x[2]:5.1%}  {x[3]} known / {x[4]} unknown  {x[5]}")
    (OUT / "summary.json").write_text(json.dumps({k: v for k, v in s.items()}, indent=0, default=list))


if __name__ == "__main__":
    main()
