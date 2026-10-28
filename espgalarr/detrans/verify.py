"""Verification harness: de-translate arcade overlay functions and compare the result
with the original 68k program.

Anchoring: every recovered function is canonicalised (canon.py) and its abstract 3-grams
(addresses replaced by placeholders) are looked up in an index of the 68k listing; the
offset with most votes wins.  The two instruction streams are then aligned with difflib
and compared instruction by instruction.

Usage: python3 -m espgalarr.detrans.verify [--limit N] [--addr 0x...] [--map addrmap.json]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import difflib
import json
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..diff import mips_funcs
from ..ps2.overlay import Overlay
from . import core
from .canon import Canon, canon
from .templates import AddrMap, M68

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
NGRAM = 3
TRIVIAL = {"jsr", "bsr", "rts", "bra", "nop", "jmp"}


@dataclass
class K68:
    addr: int
    nbytes: int
    mnem: str
    ops: str
    canon: Canon


def load_listing(path: Path, lo: int = 0x110000, hi: int = 0x1C0000) -> list[K68]:
    out = []
    with open(path) as f:
        for line in f:
            if len(line) < 10:
                continue
            try:
                a = int(line[:6], 16)
            except ValueError:
                continue
            if a < lo or a >= hi:
                continue
            parts = line.rstrip("\n").split("  ")
            parts = [p for p in parts if p != ""]
            if len(parts) < 3:
                continue
            hexb = parts[1].strip()
            rest = parts[2].strip()
            if rest.startswith(".byte") or rest.startswith("dc"):
                continue
            mnem, _, ops = rest.partition(" ")
            if mnem == "nop":
                continue          # 68k nops have no translation
            try:
                c = canon(mnem, ops)
            except ValueError:
                continue
            out.append(K68(a, len(hexb) // 2, mnem, ops, c))
    return out


class Index:
    def __init__(self, ks: list[K68]):
        self.ks = ks
        self.keys = [k.canon.key for k in ks]
        self.grams: dict[tuple, list[int]] = collections.defaultdict(list)
        for i in range(len(self.keys) - NGRAM + 1):
            self.grams[tuple(self.keys[i:i + NGRAM])].append(i)
        self.addr_index = [k.addr for k in ks]

    def pos_of(self, addr: int) -> int:
        return bisect.bisect_left(self.addr_index, addr)


@dataclass
class FnReport:
    start: int
    end: int
    anchor: int | None            # 68k address of the first aligned instruction
    n_m68: int = 0                # 68k instructions in the aligned region
    n_out: int = 0                # recovered instructions (incl. unknowns)
    exact: int = 0
    abstract: int = 0             # equal ignoring addresses
    unknown: int = 0
    mismatches: list = field(default_factory=list)   # (ours, theirs)
    pairs: list = field(default_factory=list)        # (M68, K68) aligned pairs
    votes: int = 0
    first_insn_addr: int = -1     # MIPS address of the first recovered instruction

    @property
    def coverage(self) -> float:
        return self.exact / self.n_m68 if self.n_m68 else 0.0


def _reader(ov: Overlay):
    def read_word(addr: int):
        if not ov.contains(addr) or not ov.contains(addr + 3):
            return None
        return struct.unpack("<I", ov.read(addr, 4))[0]
    return read_word


def recovered_canon(ins: M68) -> Canon | None:
    if ins.is_label or not ins.mnem:
        return None
    m = ins.mnem + ("." + ins.size if ins.size else "")
    try:
        return canon(m, ",".join(ins.ops))
    except ValueError:
        return None


def anchor(index: Index, cs: list[Canon], min_votes: int = 3):
    """Best 68k listing position for a recovered instruction sequence."""
    keys = [c.key for c in cs]
    votes: collections.Counter = collections.Counter()
    for i in range(len(keys) - NGRAM + 1):
        g = tuple(keys[i:i + NGRAM])
        if all(k.split(" ")[0] in TRIVIAL for k in g):
            continue
        pos = index.grams.get(g)
        if not pos or len(pos) > 400:
            continue
        w = 1.0 / len(pos)
        for p in pos:
            votes[p - i] += w
    if not votes:
        return None, 0
    off, v = votes.most_common(1)[0]
    if v < 1.5:
        return None, 0
    # also count raw hits for the chosen offset
    raw = 0
    for i in range(len(keys) - NGRAM + 1):
        g = tuple(keys[i:i + NGRAM])
        if off + i in index.grams.get(g, ()):
            raw += 1
    if raw < min_votes or raw < 0.15 * max(1, len(keys) - NGRAM + 1):
        return None, raw
    return off, raw


def compare_function(index: Index, res: core.Result) -> FnReport:
    rep = FnReport(res.start, res.end, None)
    rec = [(ins, recovered_canon(ins)) for ins in res.insns if not ins.is_label]
    if rec:
        rep.first_insn_addr = rec[0][0].addr
    rep.n_out = len(rec)
    rep.unknown = sum(1 for ins, c in rec if c is None)
    cs = [c for ins, c in rec if c is not None]
    if len(cs) < NGRAM:
        return rep
    off, votes = anchor(index, cs)
    rep.votes = votes
    if off is None:
        return rep
    lo = max(0, off - 8)
    hi = min(len(index.ks), off + len(rec) + 6)
    slice_ = index.ks[lo:hi]
    a_keys = [c.key if c is not None else f"?{i}" for i, (ins, c) in enumerate(rec)]
    b_keys = [k.canon.key for k in slice_]
    sm = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    # single trivial matches (rts/bra/jsr) at the edges would drag the region into the
    # neighbouring routine
    while blocks and blocks[-1].size == 1 and b_keys[blocks[-1].b].split(" ")[0] in TRIVIAL:
        blocks.pop()
    while blocks and blocks[0].size == 1 and b_keys[blocks[0].b].split(" ")[0] in TRIVIAL:
        blocks.pop(0)
    if not blocks:
        return rep
    first_b = blocks[0].b
    last_b = blocks[-1].b + blocks[-1].size - 1
    rep.anchor = slice_[first_b].addr
    rep.n_m68 = last_b - first_b + 1
    matched_a = set()
    for blk in blocks:
        for k in range(blk.size):
            ins, c = rec[blk.a + k]
            k68 = slice_[blk.b + k]
            matched_a.add(blk.a + k)
            rep.pairs.append((ins, k68))
            if c.text == k68.canon.text:
                rep.exact += 1
            else:
                rep.abstract += 1
                rep.mismatches.append((ins, k68))
    # unmatched 68k instructions inside the region: record what they are
    covered_b = set()
    for blk in blocks:
        covered_b.update(range(blk.b, blk.b + blk.size))
    for j in range(first_b, last_b + 1):
        if j not in covered_b:
            rep.mismatches.append((None, slice_[j]))
    # unmatched recovered instructions (between first and last aligned)
    if blocks:
        for i in range(blocks[0].a, blocks[-1].a + blocks[-1].size):
            if i not in matched_a:
                rep.mismatches.append((rec[i][0], None))
    return rep


# ---------------------------------------------------------------- address map learning

class LearnedMap(AddrMap):
    """PS2 constant -> 68k address map learned from aligned instruction pairs."""

    def __init__(self, samples: dict[int, int] | None = None, funcs: dict[int, int] | None = None):
        self.samples = dict(samples or {})
        self.funcs = dict(funcs or {})
        self._keys = sorted(self.samples)

    def rebuild(self):
        self._keys = sorted(self.samples)

    def map_const(self, v: int):
        if v in self.samples:
            return self.samples[v], "sample", 1.0
        if v in self.funcs:
            return self.funcs[v], "func", 1.0
        if not self._keys:
            return None, "", 0.0
        i = bisect.bisect_left(self._keys, v)
        lo = self._keys[i - 1] if i > 0 else None
        hi = self._keys[i] if i < len(self._keys) else None
        cands = []
        for k in (lo, hi):
            if k is not None and abs(k - v) <= 0x4000:
                cands.append((abs(k - v), self.samples[k] - k))
        if not cands:
            return None, "", 0.0
        cands.sort()
        if len(cands) == 2 and cands[0][1] == cands[1][1]:
            return (v + cands[0][1]) & 0xFFFFFFFF, "interp", 0.9
        return (v + cands[0][1]) & 0xFFFFFFFF, "near", 0.6

    def code_label(self, ps2: int) -> str:
        t = self.funcs.get(ps2)
        return f"${t:x}.l" if t is not None else f"F_{ps2:x}"

    def save(self, path: Path):
        path.write_text(json.dumps({"samples": {f"{k:#x}": f"{v:#x}" for k, v in sorted(self.samples.items())},
                                    "funcs": {f"{k:#x}": f"{v:#x}" for k, v in sorted(self.funcs.items())}}, indent=0))

    @classmethod
    def load(cls, path: Path) -> "LearnedMap":
        d = json.loads(path.read_text())
        return cls({int(k, 16): int(v, 16) for k, v in d["samples"].items()},
                   {int(k, 16): int(v, 16) for k, v in d["funcs"].items()})


def learn(reports: list[FnReport], lm: LearnedMap) -> int:
    """Collect (ps2 const, 68k addr) pairs from aligned instructions whose abstract forms
    agree and whose only difference is an address operand."""
    n = 0
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    fvotes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for rep in reports:
        for ins, k68 in rep.pairs:
            c = recovered_canon(ins)
            if c is None or c.key != k68.canon.key:
                continue
            for oa, ob in zip(c.ops, k68.canon.ops):
                if oa[0] in ("abs", "absx") and ob[0] in ("abs", "absx", "pc", "pcx"):
                    votes[oa[1]][ob[1]] += 1
                if oa[0] == "func" and ob[0] == "func" and isinstance(oa[1], str) and oa[1].startswith("f_"):
                    fvotes[int(oa[1][2:], 16)][ob[1]] += 1
    for ps2, cnt in votes.items():
        m68, v = cnt.most_common(1)[0]
        if v >= 1 and (len(cnt) == 1 or v >= 2 * cnt.most_common(2)[1][1]):
            if lm.samples.get(ps2) != m68:
                n += 1
            lm.samples[ps2] = m68
    anchored_funcs = {}
    for rep in reports:
        if rep.anchor is not None and rep.pairs and rep.pairs[0][0].addr == rep.first_insn_addr:
            anchored_funcs[rep.start] = rep.pairs[0][1].addr
    for ps2, cnt in fvotes.items():
        m68, v = cnt.most_common(1)[0]
        if ps2 in anchored_funcs:
            continue
        if v >= 2 and (len(cnt) == 1 or v >= 2 * cnt.most_common(2)[1][1]):
            lm.funcs[ps2] = m68
    lm.funcs.update(anchored_funcs)
    lm.rebuild()
    return n


# ---------------------------------------------------------------- driver

def run(limit: int | None = None, only: int | None = None, amap: AddrMap | None = None,
        funcs: list | None = None, ov: Overlay | None = None, index: Index | None = None,
        verbose: bool = False):
    ov = ov or Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A8")
    index = index or Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    funcs = funcs or mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)
    reports = []
    results = {}
    t0 = time.time()
    for f in funcs:
        if only is not None and f.start != only:
            continue
        if f.size < 16:
            continue
        try:
            res = core.detranslate(ov.read(f.start, f.size), f.start, amap, read_word=_reader(ov))
        except Exception as e:      # keep going: report the failure as a function with no output
            print(f"{f.start:#x}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        rep = compare_function(index, res)
        reports.append(rep)
        results[f.start] = res
        if limit and len(reports) >= limit:
            break
    return reports, results


def summarise(reports: list[FnReport]) -> dict:
    anchored = [r for r in reports if r.anchor is not None]
    tot = sum(r.n_m68 for r in anchored)
    ex = sum(r.exact for r in anchored)
    ab = sum(r.abstract for r in anchored)
    unk = sum(r.unknown for r in anchored)
    out_n = sum(r.n_out for r in anchored)
    mism: collections.Counter = collections.Counter()
    for r in anchored:
        for ours, theirs in r.mismatches:
            key = theirs.canon.mnem + ("." + theirs.canon.size if theirs.canon.size else "") if theirs else "(extra)"
            mism[key] += 1
    return {"functions": len(reports), "anchored": len(anchored), "m68_insns": tot, "exact": ex,
            "abstract_only": ab, "unknown_out": unk, "recovered_out": out_n,
            "coverage": ex / tot if tot else 0.0, "mismatch_by_mnemonic": mism}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--addr", type=lambda s: int(s, 16))
    ap.add_argument("--map", type=Path)
    ap.add_argument("--learn", type=Path, help="write learned address map here")
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--show", type=int, default=0, help="print N worst functions")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args(argv)
    lm = LearnedMap.load(a.map) if a.map else LearnedMap()
    reports = results = None
    for it in range(a.iters if a.learn else 1):
        reports, results = run(a.limit, a.addr, lm)
        if a.learn:
            n = learn(reports, lm)
            print(f"iteration {it}: learned {n} new address samples ({len(lm.samples)} total, {len(lm.funcs)} funcs)")
            lm.save(a.learn)
    s = summarise(reports)
    print(json.dumps({k: v for k, v in s.items() if k != "mismatch_by_mnemonic"}, indent=1))
    print("mismatches by 68k mnemonic:", s["mismatch_by_mnemonic"].most_common(40))
    if a.dump and results:
        for r in results.values():
            print(r.text())
    if a.show:
        worst = sorted((r for r in reports if r.anchor is not None), key=lambda r: (r.coverage, -r.n_m68))
        for r in worst[:a.show]:
            print(f"\n== {r.start:#x} anchor {r.anchor:#x} {r.exact}/{r.n_m68} ({r.coverage:.0%}) unknown {r.unknown}")
            for ours, theirs in r.mismatches[:12]:
                print(f"   ours: {ours.text().strip() if ours else '-':40s} | 68k: {theirs.canon.text if theirs else '-'}")


if __name__ == "__main__":
    main()
