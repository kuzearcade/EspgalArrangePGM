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
    # (comments are informational: harmless constant flag stores, branches never taken)
    rec = [(ins, recovered_canon(ins)) for ins in res.insns if not ins.is_label and not ins.is_comment]
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

# Learned function anchors that are wrong: the 68k program has near-identical copies of these
# routines and the learning paired the PS2 function with the wrong copy. Checked against the
# MIPS code (the copies differ only in a constant):
#   sprite layers 1/3/4 (0x114960/0x1149BC/0x1149EA): each PS2 helper fills its own sprite
#     list, 0x63D830/0x63BB80/0x639ED0/0x638220/0x636570 in the order of the 68k lists
#     0x80120A/0x801DCE/0x802602/0x802E36/0x80366A;
#   0x118E28 (angle, `asl.l #5`): ELF 0x1CC8F0 shifts by 5, 0x1C71C0 (= 0x118B64) by 6;
#   0x118198 (sprite direction, starts `moveq #0,d0`): ELF 0x1BFBC0 starts by clearing d0,
#     0x113C5C is an input read.
# Found by comparing ported units' calls with the arcade calls at the same positions.
#   (tools/map_audit.py, checked by hand against the constants each copy uses)
#   0x13EECC/0x13EF28 (d1 = $D/$C; the map had both at 0x13EEF4, d1 = $F);
#   0x1792A4 (table 0x179286; 0x179208 uses 0x179276);
#   0x158E62 (table 0x1584EC, none of 0x158DD4's own tables).
FUNC_FIXES = {0x19E870: 0x114960, 0x19EB70: 0x1149BC, 0x19ECF0: 0x1149EA,
              0x1CC8F0: 0x118E28, 0x1BFBC0: 0x118198,
              0x23AB20: 0x13EECC, 0x23B4B0: 0x13EF28, 0x891FB0: 0x1792A4, 0xA621B0: 0x158E62}

# tools/map_audit.py suspects checked by hand and found right: 0x82AC40 is the P2 setup
# (0x80EF36, $25002E00) = 0x12271C; 0x87FDC0 uses none of 0x16DE80's own tables; 0x9AA900 is
# 0x13AB3C merged with the routine after it; 0x143B40/0x143BD0 are 0x137F9E with its first
# store dropped (an Arrange change, not another copy).
AUDIT_CLEARED = {0x82AC40, 0x87FDC0, 0x9AA900, 0x143B40, 0x143BD0}


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
        funcs = {int(k, 16): int(v, 16) for k, v in d["funcs"].items()}
        return cls({int(k, 16): int(v, 16) for k, v in d["samples"].items()}, {**funcs, **FUNC_FIXES})


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
    accepted = {}
    for ps2, cnt in votes.items():
        m68, v = cnt.most_common(1)[0]
        if v >= 2 and (len(cnt) == 1 or v >= 2 * cnt.most_common(2)[1][1]):
            accepted[ps2] = m68
    keys = sorted(accepted)
    for ps2, cnt in votes.items():
        if ps2 in accepted or len(cnt) != 1:
            continue
        m68 = cnt.most_common(1)[0][0]
        k = (m68 - ps2) & 0xFFFFFFFF
        i = bisect.bisect_left(keys, ps2)
        near = [keys[j] for j in (i - 1, i) if 0 <= j < len(keys) and abs(keys[j] - ps2) <= 0x1000]
        if any((accepted[q] - q) & 0xFFFFFFFF == k for q in near):
            accepted[ps2] = m68
    for ps2, m68 in accepted.items():
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


class ElfText:
    """Overlay-like view of the ELF's code segment (for anchoring ELF-resident routines)."""

    def __init__(self, path: Path):
        from ..ps2.elf import Elf
        e = Elf(path)
        seg = e.segments[0]
        self.image = e.read(seg.vaddr, seg.filesz)
        self.image_base = seg.vaddr
        self.header = type("H", (), {"text_end": seg.vaddr + seg.filesz})()

    def contains(self, addr: int) -> bool:
        return self.image_base <= addr < self.image_base + len(self.image)

    def read(self, addr: int, size: int) -> bytes:
        o = addr - self.image_base
        return self.image[o:o + size]


def unmapped_callees(report: Path) -> set[int]:
    """ELF callees that the assembly report lists as unmapped."""
    out: set[int] = set()
    for line in report.read_text().splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) > 6:
            out.update(int(x, 16) for x in parts[6].split(",") if x.startswith("0x"))
    return out


def fallback_anchor(index: Index, res, min_keys: int = 3, min_frac: float = 0.75,
                    routine_starts: set[int] | None = None) -> int | None:
    """Anchor a short recovered routine. Candidates are 68k routine starts (previous
    instruction rts/rte/jmp/bra, or a known call target) whose first instruction has the same
    shape. Score = 2 per exactly equal instruction text (immediates, mapped callee addresses)
    + 1 per equal shape, over a difflib alignment. Accept the best only if it is unique and
    covers >= min_frac of the recovered instructions by shape."""
    rec = [c for c in (recovered_canon(i) for i in res.insns if not i.is_label and i.mnem) if c is not None]
    if len(rec) < min_keys:
        return None
    keys = [c.key for c in rec]
    texts = [c.text for c in rec]
    scored = []
    for pos, k in enumerate(index.keys):
        if k != keys[0]:
            continue
        prev = index.ks[pos - 1].mnem.split(".")[0] if pos > 0 else "rts"
        is_start = prev in ("rts", "rte", "jmp", "bra") or (routine_starts is not None and index.ks[pos].addr in routine_starts)
        if not is_start:
            continue
        win = index.ks[pos:pos + len(keys) + 6]
        sm = difflib.SequenceMatcher(None, keys, [w.canon.key for w in win], autojunk=False)
        shape = exact = 0
        for b in sm.get_matching_blocks():
            for j in range(b.size):
                shape += 1
                if texts[b.a + j] == win[b.b + j].canon.text:
                    exact += 1
        scored.append((shape + exact, shape, pos))
    if not scored:
        return None
    scored.sort(reverse=True)
    score, shape, pos = scored[0]
    if shape < max(min_keys, min_frac * len(keys)):
        return None
    if len(scored) > 1 and scored[1][0] == score:
        return None
    return index.ks[pos].addr


def learn_elf_targets(lm: "LearnedMap", index: Index, targets: set[int], timeout_s: int = 60,
                      iters: int = 2, verbose: bool = True) -> tuple[int, list[int]]:
    """Anchor only the given ELF functions, each under a wall-clock limit. Returns the number
    of callee mappings added and the functions skipped for exceeding the limit."""
    import signal

    class _Timeout(Exception):
        pass

    def _alarm(signum, frame):
        raise _Timeout()

    ov = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
    fs = [f for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end) if f.start in targets]
    rj = WORK / "pgm" / "routines.json"
    routine_starts = {int(k, 16) for k in json.loads(rj.read_text())} if rj.exists() else None
    n0 = len(lm.funcs)
    skipped: list[int] = []
    old = signal.signal(signal.SIGALRM, _alarm)
    try:
        for it in range(iters):
            reports = []
            for k, f in enumerate(fs):
                if k % 200 == 0:
                    print(f"  iter {it}: {k}/{len(fs)} functions", flush=True)
                if f.start in skipped:
                    continue
                t0 = time.time()
                signal.alarm(timeout_s)
                try:
                    res = core.detranslate(ov.read(f.start, f.size), f.start, lm, read_word=_reader(ov))
                    reports.append(compare_function(index, res))
                except _Timeout:
                    skipped.append(f.start)
                    if verbose:
                        print(f"  {f.start:#x} ({f.size} B): skipped after {timeout_s}s")
                    continue
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print(f"  {f.start:#x}: {type(e).__name__}: {e}")
                    continue
                finally:
                    signal.alarm(0)
                if reports[-1].anchor is None and f.start not in lm.funcs:
                    fa = fallback_anchor(index, res, routine_starts=routine_starts)
                    if fa is not None:
                        lm.funcs[f.start] = fa
                        if verbose:
                            print(f"  {f.start:#x}: fallback anchor -> {fa:#x}")
                if verbose and it == 0:
                    r = reports[-1]
                    print(f"  {f.start:#x} ({f.size} B) {time.time()-t0:5.1f}s -> 68k "
                          f"{r.anchor and hex(r.anchor)} {r.coverage:.0%}")
            learn(reports, lm)
    finally:
        signal.signal(signal.SIGALRM, old)
    return len(lm.funcs) - n0, skipped


def learn_elf(lm: "LearnedMap", index: Index, iters: int = 2):
    """Anchor the ELF-resident translated routines (the arrange build calls them) and add
    their 68k originals to the callee map."""
    ov = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
    fs = mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)
    n0 = len(lm.funcs)
    for _ in range(iters):
        reports, _ = run(None, None, lm, funcs=fs, ov=ov, index=index)
        learn(reports, lm)
    return len(lm.funcs) - n0, summarise(reports)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--elf", action="store_true", help="also anchor ELF-resident routines (callee map)")
    ap.add_argument("--elf-targets", type=Path, help="anchor only the unmapped ELF callees listed in this assemble_report.txt")
    ap.add_argument("--timeout", type=int, default=60, help="per-function limit (seconds) for --elf-targets")
    ap.add_argument("--elf-all", action="store_true", help="anchor every ELF function (time-bounded) to learn the ELF address map")
    ap.add_argument("--addr", type=lambda s: int(s, 16))
    ap.add_argument("--map", type=Path)
    ap.add_argument("--learn", type=Path, help="write learned address map here")
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--show", type=int, default=0, help="print N worst functions")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args(argv)
    lm = LearnedMap.load(a.map) if a.map else LearnedMap()
    if a.elf_all:
        ov = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
        targets = {f.start for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)}
        print(f"anchoring all {len(targets)} ELF functions (limit {a.timeout}s each)", flush=True)
        index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
        n0 = len(lm.samples)
        n, skipped = learn_elf_targets(lm, index, targets, a.timeout, iters=a.iters, verbose=False)
        lm.save(a.learn or a.map)
        print(f"ELF all: {n} callee mappings, {len(lm.samples) - n0} address samples added, "
              f"{len(skipped)} skipped: {[hex(x) for x in skipped]}")
        return 0
    if a.elf_targets:
        targets = unmapped_callees(a.elf_targets)
        print(f"anchoring {len(targets)} unmapped ELF callees (limit {a.timeout}s each)")
        index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
        n, skipped = learn_elf_targets(lm, index, targets, a.timeout)
        out = a.learn or a.map
        lm.save(out)
        print(f"ELF targets: {n} callee mappings added, {len(skipped)} skipped; map saved to {out}")
        return 0
    reports = results = None
    for it in range(a.iters if a.learn else 1):
        reports, results = run(a.limit, a.addr, lm)
        if a.learn:
            n = learn(reports, lm)
            print(f"iteration {it}: learned {n} new address samples ({len(lm.samples)} total, {len(lm.funcs)} funcs)")
            lm.save(a.learn)
    if a.elf and a.learn:
        index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
        n, es = learn_elf(lm, index)
        lm.save(a.learn)
        print(f"ELF: {es['anchored']} routines anchored, {n} callee mappings added, "
              f"{es['exact']}/{es['m68_insns']} instructions exact")
    s = summarise(reports)
    print(json.dumps({k: v for k, v in s.items() if k != "mismatch_by_mnemonic"}, indent=1))
    out = WORK / "ps2" / "detrans" / "verify_arcade.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write("# arcade MIPS function -> anchored 68k routine: exact/aligned 68k instructions, unknown outputs\n")
        for r in sorted(reports, key=lambda r: r.start):
            if r.anchor is None:
                continue
            f.write(f"{r.start:#x}-{r.end:#x} 68k {r.anchor:#x} {r.exact}/{r.n_m68} {r.coverage:.0%} unknown {r.unknown}\n")
        f.write(f"# total {s['exact']}/{s['m68_insns']} = {s['coverage']:.1%} exact over {s['anchored']} anchored functions\n")
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
