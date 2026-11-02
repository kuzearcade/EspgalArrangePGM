"""Per-stage PS2 timing tables for the no-input MAME comparisons, from a PCSX2 watch trace.

Usage: python3 tools/ps2_play_table.py <trace dir> [<out dir> (default: the trace dir) [MIN_STUCK (default 150)]]

The trace is a PCSX2 watch.txt (patches/pcsx2/espgal-trace.diff, ESPGAL_WATCH) that watches
642298:10 first, then two more ranges, then 358048:8 and 358028:2 (e.g.
642298:10,c27a10:14a0,358048:8,358028:2). 0x642298 is the play-time key, 0x358048 the frame
toggle (= 0x801166) and 0x35804C the game counter (= 0x801164), 0x358028 the RNG (= 0x801176).
A stage load holds the key for MIN_STUCK+ frames; the next stage's play frame k is
(key - held key) & 0xFFFF from the first frame after.

Writes <out dir>/play_table_<n>.txt per stage, lines "play toggle counter" (hex play frame,
toggle, hex counter + 0x23F: the MAME counter base), which the MAME scripts write before each
logic frame, and prints: stage index, first PS2 frame, base key, last PS2 frame, play frames,
RNG at play 0.

The PS2 counter and toggle step once per vsync, also through the frames its slowdown freezes.
A logic frame sees the values one vsync after the last sample of the frame before: normally
that is the first sample of its own key, but when the counter stalls at a heavy frame (a boss
leaving: the vsync after the last sample of key k-1 already shows key k with the counter and
toggle unchanged) the first sample is one step behind. The tables therefore take each key's
values from the last sample of the key before (counter + 1, toggle flipped); the stage's first
key, which has no sample before it, keeps its first sample.
"""
from __future__ import annotations

import sys

CTR_BASE = 0x23F            # MAME counter = PS2 counter + CTR_BASE at the same sample


def read_rows(path: str) -> list[tuple[int, int, int, int, int | None]]:
    """(frame, key, toggle, counter, rng) per trace line."""
    rows = []
    for line in open(path):
        x = line.split()
        if x[0] == "H":
            continue
        rows.append((int(x[0]), int(x[1][:4], 16), int(x[3][0:4], 16), int(x[3][8:12], 16),
                     int(x[4][:4], 16) if len(x) > 4 else None))
    return rows


def stages(rows, min_stuck: int) -> list[tuple[int, int]]:
    """(first frame, base key) of each stage: the frame after the key was held MIN_STUCK+ frames."""
    out = []
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] == rows[i][1]:
            j += 1
        if rows[j][0] - rows[i][0] >= min_stuck and j + 1 < len(rows):
            out.append((rows[j + 1][0], rows[i][1], rows[i][0]))
        i = j + 1
    return out


def table(rows, start: int, end: int, base: int) -> dict[int, tuple[int, int]]:
    """play frame -> (toggle, MAME counter) for the logic frame of that key."""
    tab: dict[int, tuple[int, int]] = {}
    prev = None
    for f, key, tg, ctr, _ in rows:
        if not start <= f <= end:
            continue
        k = (key - base) & 0xFFFF
        if k not in tab:
            if prev is None:
                tab[k] = (tg, (ctr + CTR_BASE) & 0xFFFF)
            else:
                tab[k] = (prev[0] ^ 1, (prev[1] + 1 + CTR_BASE) & 0xFFFF)
        prev = (tg, ctr)
    return tab


def main() -> None:
    d = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else d
    min_stuck = int(sys.argv[3]) if len(sys.argv) > 3 else 150
    rows = read_rows(d + "/watch.txt")
    st = stages(rows, min_stuck)
    for n, (start, base, _) in enumerate(st):
        end = st[n + 1][2] if n + 1 < len(st) else rows[-1][0]
        tab = table(rows, start, end, base)
        held = [r for r in rows if r[0] < start and r[1] == base]
        rng0 = held[-1][4] if held else None
        with open(f"{out}/play_table_{n}.txt", "w") as f:
            for k in sorted(tab):
                f.write(f"{k:x} {tab[k][0]} {tab[k][1]:x}\n")
        print(n, "from", start, "base", hex(base), "to", end, "plays", max(tab) if tab else 0,
              "rng0", rng0 is not None and hex(rng0))


if __name__ == "__main__":
    main()
