"""Function-level alignment of two MIPS (R5900) code images loaded at the same
address (PS2 Espgaluda: ov_arcade.bin vs ov_arrange.bin, both at 0x730800).

Works on raw 32-bit words (no capstone needed) so EE-specific opcodes that the
disassembler cannot decode are still handled uniformly.

Pipeline:
  1. split_functions(): function starts = `jal` targets inside the image plus
     stack-frame prologues (`addiu/daddiu $sp,$sp,-N`); a function ends where
     the next one starts.
  2. Function.strict_sig / loose_sig: strict keeps everything except
     relocatable material (j/jal targets, `lui` immediates and the low half /
     load-store offset that completes a `lui` address);
     loose keeps only an opcode class (registers + immediates masked).
  3. match(): in-order alignment of strict signatures (difflib), then exact
     out-of-order matches for signatures unique on both sides, then near
     matches (loose-signature similarity) inside the remaining gaps.
"""
from __future__ import annotations

import difflib
import struct
from dataclasses import dataclass, field
from typing import Iterable

SP = 29
RA = 31

OPC_NAMES = {
    0: "special", 1: "regimm", 2: "j", 3: "jal", 4: "beq", 5: "bne", 6: "blez", 7: "bgtz",
    8: "addi", 9: "addiu", 10: "slti", 11: "sltiu", 12: "andi", 13: "ori", 14: "xori", 15: "lui",
    16: "cop0", 17: "cop1", 18: "cop2", 20: "beql", 21: "bnel", 22: "blezl", 23: "bgtzl",
    24: "daddi", 25: "daddiu", 26: "ldl", 27: "ldr", 28: "mmi", 30: "lq", 31: "sq",
    32: "lb", 33: "lh", 34: "lwl", 35: "lw", 36: "lbu", 37: "lhu", 38: "lwr", 39: "lwu",
    40: "sb", 41: "sh", 42: "swl", 43: "sw", 44: "sdl", 45: "sdr", 46: "swr", 47: "cache",
    48: "ll", 49: "lwc1", 50: "lwc2", 51: "pref", 52: "lld", 53: "ldc1", 54: "ldc2", 55: "ld",
    56: "sc", 57: "swc1", 58: "swc2", 60: "scd", 61: "sdc1", 62: "sdc2", 63: "sd",
}
LOADSTORE = set(range(32, 48)) | {26, 27, 30, 31, 48, 49, 50, 52, 53, 54, 55, 56, 57, 58, 60, 61, 62, 63}
BRANCH_IMM = {4, 5, 6, 7, 20, 21, 22, 23}


def opc(w: int) -> int:
    return w >> 26


def rs(w: int) -> int:
    return (w >> 21) & 31


def rt(w: int) -> int:
    return (w >> 16) & 31


def rd(w: int) -> int:
    return (w >> 11) & 31


def imm16(w: int) -> int:
    v = w & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def funct(w: int) -> int:
    return w & 0x3F


def is_jal(w: int) -> bool:
    return opc(w) == 3


def jal_target(w: int, pc: int) -> int:
    return ((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)


def is_jr_ra(w: int) -> bool:
    return opc(w) == 0 and funct(w) == 8 and rs(w) == RA


def is_sp_prologue(w: int) -> bool:
    """addiu/daddiu $sp,$sp,-N"""
    return opc(w) in (9, 25) and rs(w) == SP and rt(w) == SP and imm16(w) < 0


def loose_class(w: int) -> int:
    """Opcode class with registers and immediates masked."""
    o = opc(w)
    if o == 0:
        return (o << 8) | funct(w)
    if o == 1:
        return (o << 8) | rt(w)
    if o in (16, 17, 18):
        return (o << 16) | (rs(w) << 8) | funct(w)
    if o == 28:  # MMI: funct + sub-funct in rd/sa fields
        return (o << 16) | (funct(w) << 8) | ((w >> 6) & 31)
    return o << 8


@dataclass
class Function:
    start: int
    end: int
    words: list[int]
    is_prologue: bool
    strict_sig: bytes = b""
    loose_sig: bytes = b""
    calls: list[int] = field(default_factory=list)       # jal targets
    refs: list[int] = field(default_factory=list)        # lui/addiu|ori absolute addresses
    callers: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.end - self.start

    def __repr__(self) -> str:
        return f"Function({self.start:#x}-{self.end:#x}, {self.size} bytes)"


def iter_words(code: bytes) -> list[int]:
    n = len(code) // 4
    return list(struct.unpack_from(f"<{n}I", code, 0))


def split_functions(code: bytes, base: int, text_end: int | None = None,
                    extra_starts: Iterable[int] = ()) -> list[Function]:
    """Split `code` (loaded at `base`) into functions; text ends at `text_end`."""
    words = iter_words(code)
    text_end = base + len(words) * 4 if text_end is None else text_end
    nwords = (text_end - base) // 4
    starts: set[int] = set()
    prologues: set[int] = set()
    for i in range(nwords):
        w = words[i]
        pc = base + i * 4
        if is_jal(w):
            t = jal_target(w, pc)
            if base <= t < text_end:
                starts.add(t)
        if is_sp_prologue(w):
            # Prologue may be preceded by nothing we can detect; treat as start.
            starts.add(pc)
            prologues.add(pc)
    for s in extra_starts:
        starts.add(s)
    # A prologue immediately after `jr ra; <delay>` may belong to the same
    # function (rare: tail-sharing); we keep the simple rule.
    ordered = sorted(s for s in starts if base <= s < text_end)
    if not ordered or ordered[0] != base:
        ordered.insert(0, base)
    funcs: list[Function] = []
    for k, s in enumerate(ordered):
        e = ordered[k + 1] if k + 1 < len(ordered) else text_end
        fw = words[(s - base) // 4:(e - base) // 4]
        funcs.append(Function(s, e, fw, s in prologues))
    for f in funcs:
        _analyse(f, base, text_end)
    by_start = {f.start: f for f in funcs}
    for f in funcs:
        for t in f.calls:
            g = by_start.get(t)
            if g is not None:
                g.callers.append(f.start)
    return funcs


def _analyse(f: Function, base: int, text_end: int) -> None:
    """Fill strict/loose signatures, calls and address refs."""
    strict: list[int] = []
    loose: list[int] = []
    calls: list[int] = []
    refs: list[int] = []
    hi: dict[int, int] = {}  # reg -> lui value (<<16)
    clear_after = False      # a jal/jr was seen: taint dies after its delay slot
    for i, w in enumerate(f.words):
        pc = f.start + i * 4
        o = opc(w)
        masked = w
        if clear_after:
            # the delay slot may still complete a lui (lui a0; jal x; addiu a0)
            clear_now, clear_after = True, False
        else:
            clear_now = False
        if o == 3:
            t = jal_target(w, pc)
            calls.append(t)
            # mask every call target: the arrange build calls ELF-resident copies
            # of routines the arcade overlay carries itself, so keeping targets
            # would turn identical code into "near" matches
            masked = w & 0xFC000000
        elif o == 2:
            masked = w & 0xFC000000
        elif o == 15:  # lui
            hi[rt(w)] = (w & 0xFFFF) << 16
            masked = w & 0xFFFF0000
        elif o in (9, 13, 25) and rs(w) in hi:  # addiu/ori/daddiu completing a lui
            a = hi[rs(w)] + (imm16(w) if o != 13 else (w & 0xFFFF))
            refs.append(a & 0xFFFFFFFF)
            masked = w & 0xFFFF0000
        elif o in LOADSTORE and rs(w) in hi:
            a = hi[rs(w)] + imm16(w)
            refs.append(a & 0xFFFFFFFF)
            masked = w & 0xFFFF0000
        # register write invalidates lui taint
        if o == 0:
            if rd(w) in hi and funct(w) not in (8, 9):
                hi.pop(rd(w), None)
        elif o in (8, 9, 10, 11, 12, 13, 14, 24, 25) and o != 15:
            if rt(w) in hi and not (o in (9, 13, 25) and rs(w) == rt(w)):
                hi.pop(rt(w), None)
        elif o in LOADSTORE and o not in (31, 41, 42, 43, 44, 45, 46, 40, 56, 57, 58, 60, 61, 62, 63):
            hi.pop(rt(w), None)
        if o == 3 or (o == 0 and funct(w) in (8, 9)):
            clear_after = True
        if clear_now:
            hi.clear()
        strict.append(masked)
        loose.append(loose_class(w))
    f.strict_sig = struct.pack(f"<{len(strict)}I", *strict)
    f.loose_sig = struct.pack(f"<{len(loose)}I", *loose)
    f.calls = calls
    f.refs = refs


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------

@dataclass
class Match:
    kind: str            # "exact" | "near"
    a: Function          # arcade
    b: Function          # arrange
    ratio: float


@dataclass
class DiffResult:
    matches: list[Match]
    unmatched_a: list[Function]
    unmatched_b: list[Function]

    def by_b(self) -> dict[int, Match]:
        return {m.b.start: m for m in self.matches}

    def by_a(self) -> dict[int, Match]:
        return {m.a.start: m for m in self.matches}


def _bigrams(f: Function) -> "Counter[tuple[int, int]]":
    from collections import Counter
    toks = struct.unpack(f"<{len(f.loose_sig) // 4}I", f.loose_sig)
    return Counter(zip(toks, toks[1:]))


def _similarity(x: Function, y: Function) -> float:
    """Multiset Jaccard similarity of opcode-class bigrams (cheap, order-aware
    enough for near-match detection; exact matches are decided on strict_sig)."""
    if x.loose_sig == y.loose_sig:
        return 1.0
    bx = x.__dict__.setdefault("_bg", _bigrams(x))
    by = y.__dict__.setdefault("_bg", _bigrams(y))
    inter = sum(min(c, by.get(k, 0)) for k, c in bx.items())
    union = sum(bx.values()) + sum(by.values()) - inter
    return inter / union if union else 0.0


def match(fa: list[Function], fb: list[Function], near_threshold: float = 0.70,
          max_gap: int = 400) -> DiffResult:
    sa = [f.strict_sig for f in fa]
    sb = [f.strict_sig for f in fb]
    ha = [hash(s) for s in sa]
    hb = [hash(s) for s in sb]
    matched_a: dict[int, Match] = {}
    matched_b: dict[int, Match] = {}

    def add(kind: str, i: int, j: int, ratio: float) -> None:
        m = Match(kind, fa[i], fb[j], ratio)
        matched_a[i] = m
        matched_b[j] = m

    # 1. in-order exact alignment
    sm = difflib.SequenceMatcher(None, ha, hb, autojunk=False)
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            if sa[i + k] == sb[j + k]:
                add("exact", i + k, j + k, 1.0)

    # 2. out-of-order exact matches for signatures unique on both sides
    from collections import Counter
    ca, cb = Counter(sa), Counter(sb)
    idx_b = {s: j for j, s in enumerate(sb) if cb[s] == 1 and j not in matched_b}
    for i, s in enumerate(sa):
        if i in matched_a or ca[s] != 1:
            continue
        j = idx_b.get(s)
        if j is not None and j not in matched_b:
            add("exact", i, j, 1.0)

    # 3. near matches inside gaps between consecutive in-order anchors
    anchors = sorted((m.a.start, i, j) for i, m in matched_a.items() for j in [fb.index(m.b)])
    # anchors as (i, j) index pairs sorted by i; only use monotonic ones
    pairs = []
    last_j = -1
    for _, i, j in anchors:
        if j > last_j:
            pairs.append((i, j))
            last_j = j
    bounds = [(-1, -1)] + pairs + [(len(fa), len(fb))]
    for (i0, j0), (i1, j1) in zip(bounds, bounds[1:]):
        ga = [i for i in range(i0 + 1, i1) if i not in matched_a]
        gb = [j for j in range(j0 + 1, j1) if j not in matched_b]
        if not ga or not gb or len(ga) * len(gb) > max_gap * max_gap:
            continue
        cands = []
        for i in ga:
            for j in gb:
                x, y = fa[i], fb[j]
                if min(x.size, y.size) * 2 < max(x.size, y.size) and max(x.size, y.size) > 64:
                    continue
                r = _similarity(x, y)
                if r >= near_threshold:
                    cands.append((r, i, j))
        cands.sort(reverse=True)
        for r, i, j in cands:
            if i in matched_a or j in matched_b:
                continue
            add("near", i, j, r)

    # 4. global near pass for leftovers that share a referenced string set or
    #    identical loose signature (moved functions)
    loose_b: dict[bytes, list[int]] = {}
    for j, g in enumerate(fb):
        if j not in matched_b:
            loose_b.setdefault(g.loose_sig, []).append(j)
    for i, f in enumerate(fa):
        if i in matched_a:
            continue
        lst = loose_b.get(f.loose_sig)
        if lst:
            while lst and lst[0] in matched_b:
                lst.pop(0)
            if lst:
                add("near", i, lst.pop(0), 1.0)

    matches = sorted(matched_b.values(), key=lambda m: m.b.start)
    ua = [f for i, f in enumerate(fa) if i not in matched_a]
    ub = [f for j, f in enumerate(fb) if j not in matched_b]
    return DiffResult(matches, ua, ub)


def find_function(funcs: list[Function], addr: int) -> Function | None:
    lo, hi = 0, len(funcs) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        f = funcs[mid]
        if addr < f.start:
            hi = mid - 1
        elif addr >= f.end:
            lo = mid + 1
        else:
            return f
    return None
