"""Align the 68k program ROM against the copies of its data tables embedded in
the PS2 overlays, and diff the Arcade vs Arrange copies.

Algorithm (ours): index every 16-byte-aligned block of the ROM, walk the overlay
and greedily extend matches into maximal segments (rom_off, ov_off, length).
Unmatched overlay gaps between matched segments are "foreign" data (PS2-only or
Arrange-only tables).
"""
from __future__ import annotations

from dataclasses import dataclass

BLOCK = 16


@dataclass(frozen=True)
class Segment:
    rom_off: int
    ov_off: int
    length: int

    @property
    def rom_end(self) -> int:
        return self.rom_off + self.length

    @property
    def ov_end(self) -> int:
        return self.ov_off + self.length

    @property
    def shift(self) -> int:
        return self.ov_off - self.rom_off


def _low_entropy(b: bytes) -> bool:
    return len(set(b)) <= 3


def index_blocks(rom: bytes, start: int = 0, end: int | None = None) -> dict[bytes, list[int]]:
    end = len(rom) if end is None else end
    idx: dict[bytes, list[int]] = {}
    for o in range(start, end - BLOCK + 1, BLOCK):
        b = rom[o:o + BLOCK]
        if _low_entropy(b):
            continue
        idx.setdefault(b, []).append(o)
    return idx


def match_segments(rom: bytes, ov: bytes, rom_start: int = 0, rom_end: int | None = None,
                   min_len: int = 64) -> list[Segment]:
    """Maximal verbatim runs of `rom[rom_start:rom_end]` found inside `ov`."""
    idx = index_blocks(rom, rom_start, rom_end)
    segs: list[Segment] = []
    i = 0
    n = len(ov)
    while i + BLOCK <= n:
        cands = idx.get(ov[i:i + BLOCK])
        if not cands:
            i += 1
            continue
        best = None
        for ro in cands:
            # extend backwards (byte-wise) and forwards
            a, b = ro, i
            while a > 0 and b > 0 and rom[a - 1] == ov[b - 1]:
                a -= 1
                b -= 1
            e_r, e_o = ro + BLOCK, i + BLOCK
            while e_r < len(rom) and e_o < n and rom[e_r] == ov[e_o]:
                e_r += 1
                e_o += 1
            length = e_o - b
            if best is None or length > best.length:
                best = Segment(a, b, length)
        if best.length >= min_len:
            if segs and segs[-1].ov_end >= best.ov_off and segs[-1].shift == best.shift:
                last = segs[-1]
                segs[-1] = Segment(last.rom_off, last.ov_off, max(last.ov_end, best.ov_end) - last.ov_off)
            else:
                segs.append(best)
            i = best.ov_end
        else:
            i += BLOCK
    return segs


def coverage(segs: list[Segment], rom_lo: int, rom_hi: int) -> tuple[int, list[tuple[int, int]]]:
    """Bytes of rom[rom_lo:rom_hi] covered, and the uncovered holes."""
    covered = 0
    holes = []
    cur = rom_lo
    for s in sorted(segs, key=lambda s: s.rom_off):
        if s.rom_end <= rom_lo or s.rom_off >= rom_hi:
            continue
        lo, hi = max(s.rom_off, rom_lo), min(s.rom_end, rom_hi)
        if lo > cur:
            holes.append((cur, lo))
        covered += max(0, hi - max(lo, cur))
        cur = max(cur, hi)
    if cur < rom_hi:
        holes.append((cur, rom_hi))
    return covered, holes


def format_segments(segs: list[Segment]) -> str:
    lines = [f"{'rom_off':>8} {'rom_end':>8} {'ov_off':>8} {'ov_end':>8} {'len':>7} {'shift':>8}"]
    for s in sorted(segs, key=lambda s: s.ov_off):
        lines.append(f"{s.rom_off:08x} {s.rom_end:08x} {s.ov_off:08x} {s.ov_end:08x} {s.length:7x} {s.shift:+8x}")
    return "\n".join(lines)
