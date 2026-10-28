"""Discover how the PS2 port lays out the 68k game's global variables.

The PS2 code is a source-level port that keeps the 68k globals' relative layout within
blocks, but relocates blocks. For every PS2 global address referenced by the code
(lui/addiu|load/store pairs and $gp-relative accesses) and every 68k RAM address referenced
by the 68k ROM, candidate offsets K = m68k - ps2 are voted; offsets supported by many
distinct addresses inside a contiguous PS2 range define a block mapping.
"""
from __future__ import annotations

import collections
import struct
from dataclasses import dataclass

GP_DEFAULT = 0x35FCF0
LOADSTORE = {0x09, 0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2B}


def ps2_addresses(img: bytes, base: int, gp: int = GP_DEFAULT) -> dict[int, list[int]]:
    """addr -> list of code addresses referencing it."""
    n = len(img) // 4 * 4
    w = struct.unpack("<%dI" % (n // 4), img[:n])
    out: dict[int, list[int]] = collections.defaultdict(list)
    for i in range(len(w) - 1):
        op = w[i] >> 26
        rs = (w[i] >> 21) & 31
        imm = w[i] & 0xFFFF
        if imm >= 0x8000:
            imm -= 0x10000
        if rs == 28 and op in LOADSTORE and op != 0x09:
            out[gp + imm].append(base + 4 * i)
        if op == 0x0F:
            hi = w[i] & 0xFFFF
            rt = (w[i] >> 16) & 31
            for j in range(i + 1, min(i + 6, len(w))):
                op2 = w[j] >> 26
                rs2 = (w[j] >> 21) & 31
                imm2 = w[j] & 0xFFFF
                if imm2 >= 0x8000:
                    imm2 -= 0x10000
                if rs2 == rt and op2 in LOADSTORE:
                    out[((hi << 16) + imm2) & 0xFFFFFFFF].append(base + 4 * i)
                    break
    return out


@dataclass
class Block:
    ps2_lo: int
    ps2_hi: int
    k: int               # m68k = ps2 + k
    support: int         # distinct addresses agreeing

    @property
    def m68k_lo(self) -> int:
        return (self.ps2_lo + self.k) & 0xFFFFFFFF

    @property
    def m68k_hi(self) -> int:
        return (self.ps2_hi + self.k) & 0xFFFFFFFF


def find_blocks(ps2: dict[int, list[int]], m68k: set[int], min_support: int = 6, window: int = 0x400) -> list[Block]:
    """Greedy: vote K over (ps2 addr, 68k addr) pairs restricted to plausible ranges, then
    for the best K's, grow contiguous PS2 ranges whose addresses map into the 68k set."""
    m68k_sorted = sorted(m68k)
    votes: collections.Counter = collections.Counter()
    ps2_sorted = sorted(a for a in ps2 if 0x100000 <= a < 0x1000000)
    for a in ps2_sorted:
        for b in m68k_sorted:
            votes[(b - a) & 0xFFFFFFFF] += 1
    blocks: list[Block] = []
    used: set[int] = set()
    for k, _ in votes.most_common(400):
        hits = [a for a in ps2_sorted if ((a + k) & 0xFFFFFFFF) in m68k and a not in used]
        if len(hits) < min_support:
            continue
        hits.sort()
        start = hits[0]
        prev = hits[0]
        run = [hits[0]]
        for a in hits[1:] + [None]:
            if a is not None and a - prev <= window:
                run.append(a)
                prev = a
                continue
            if len(run) >= min_support:
                blocks.append(Block(run[0], run[-1] + 1, k, len(run)))
                used.update(run)
            if a is None:
                break
            start = prev = a
            run = [a]
    blocks.sort(key=lambda b: b.ps2_lo)
    return blocks


def translate(addr: int, blocks: list[Block]) -> int | None:
    for b in blocks:
        if b.ps2_lo - 0x100 <= addr < b.ps2_hi + 0x100:
            return (addr + b.k) & 0xFFFFFFFF
    return None
