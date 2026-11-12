"""The build's faster routines give the results of the plain versions they replaced (synthetic
data; the plain versions are kept here as the reference)."""
import random

import numpy as np
import pytest

from espgalarr import parallel
from espgalarr.detrans import core, symexec
from espgalarr.detrans.mips import Insn
from espgalarr.diff import stagetables
from espgalarr.pgm import gfx
from espgalarr.stages import gfx as stage


# ---- sprite search (stages/gfx.py)

def _mask_bytes_plain(px):
    out = bytearray()
    for row in px.reshape(-1, 16):
        v = 0
        for x in range(16):
            if row[x] == 0x1F:
                v |= 1 << x
        out += bytes((v & 0xFF, v >> 8))
    return bytes(out)


def _locate_plain(b_rom, a_rom, px):
    if stage._blank(px):
        return None
    m = _mask_bytes_plain(px)
    if len(set(m)) < 3 and len(m) < 64:
        return None
    pos = b_rom.find(m)
    n16 = len(px) // 16
    while pos >= 0:
        if pos >= 4 and (pos - 4) % 2 == 0:
            off = (pos - 4) // 2
            dec = gfx.decode_sprite(a_rom, b_rom, off, 1, n16).pixels.reshape(-1)
            dec = np.where(dec == 255, 0x1F, dec).astype(np.uint8)
            if np.array_equal(dec, px):
                return off
        pos = b_rom.find(m, pos + 1)
    return None


def _outcome(fn, *a):
    try:
        return fn(*a)
    except IndexError:
        return "IndexError"


def test_mask_bytes_matches_plain():
    rng = np.random.RandomState(1)
    for _ in range(50):
        px = rng.randint(0, 32, 16 * rng.randint(1, 40)).astype(np.uint8)
        assert stage._mask_bytes(px) == _mask_bytes_plain(px)


def test_locate_sprite_matches_plain():
    rng = np.random.RandomState(2)
    for trial in range(60):
        px = rng.randint(0, 31, (rng.randint(1, 12), 16)).astype(np.uint8)
        px[rng.rand(*px.shape) < 0.4] = 255
        a, b = gfx.encode_sprite(px)
        flat = np.where(px == 255, 0x1F, px).astype(np.uint8).reshape(-1)
        # the same mask twice: a decoy whose A data differs (or runs off the A ROM) first
        decoy_a = bytes(rng.randint(0, 256, len(a)).astype(np.uint8))
        a_rom = b"\x00" * 8 + decoy_a + a
        decoy_at = len(a_rom) // 2 if trial % 7 == 0 else 4       # (A ROM word offsets)
        b_rom = (b"\x11" * 6 + gfx.b_header(decoy_at) + b + b"\x33" * 2
                 + gfx.b_header((8 + len(decoy_a)) // 2) + b + b"\x22" * 10)
        if trial % 5 == 0:
            flat = flat.copy()
            k = np.flatnonzero(flat != 0x1F)
            if len(k):
                flat[k[0]] = (flat[k[0]] + 1) % 31     # no match anywhere (same mask)
        assert _outcome(stage.locate_sprite, b_rom, a_rom, flat) == _outcome(_locate_plain, b_rom, a_rom, flat)


# ---- branch scan (detrans/symexec.py)

def _entered_plain(ex, i, j):
    lo, hi = ex.insns[i].addr, ex.insns[j].addr if j < len(ex.insns) else ex.end
    for k, x in enumerate(ex.insns):
        if i <= k < j:
            continue
        if (x.op in symexec.COND_BRANCHES or x.op in ("b", "j")) and lo < x.target < hi:
            return True
    return False


def test_entered_from_outside_matches_plain():
    rnd = random.Random(3)
    for _ in range(30):
        n = rnd.randint(1, 60)
        base = 0x400000
        insns = []
        for k in range(n):
            op = rnd.choice(["beq", "bne", "b", "j", "addiu", "nop", "lw", "bgez"])
            insns.append(Insn(base + 4 * k, 0, op, target=base + 4 * rnd.randint(-2, n + 2)))
        ex = symexec.Executor(insns)
        for i in range(n):
            for j in range(i, n + 1):
                assert ex._entered_from_outside(i, j) == _entered_plain(ex, i, j)


# ---- register versions (detrans/core.py)

def _version_plain(effects, store):
    name, best = store[1][1], 0
    for e in effects:
        if e is store:
            break
        if e[0] == "store" and e[1][0] == "slot" and e[1][1] == name:
            best += 1
    return best + 1


def test_versions_match_plain():
    rnd = random.Random(4)
    for _ in range(30):
        effects = []
        for k in range(rnd.randint(1, 40)):
            kind = rnd.choice(["slot", "slot", "mem", "label"])
            if kind == "label":
                effects.append(("label", k))
            else:
                effects.append(("store", (kind, rnd.choice(["d0", "d1", "a0"]), 2), ("c", k), k))
        effects.append(effects[0])               # (an object listed twice: its first place counts)
        v = core._Versions(effects)
        for e in effects:
            if e[0] == "store":
                assert v(e) == _version_plain(effects, e)
                copy = tuple(e)[:-1] + (e[-1],)  # a rewritten store not in the list
                assert v(copy) == _version_plain(effects, copy)


# ---- worker pool

def _square(x):
    return x * x


def test_workers_three_quarters(monkeypatch):
    monkeypatch.delenv("ESPGAL_JOBS", raising=False)
    monkeypatch.setattr(parallel, "cores", lambda: 16)
    assert parallel.workers() == 12
    monkeypatch.setattr(parallel, "cores", lambda: 1)
    assert parallel.workers() == 1
    monkeypatch.setenv("ESPGAL_JOBS", "3")
    assert parallel.workers() == 3


@pytest.mark.parametrize("jobs", ["1", "4"])
def test_map_ordered_keeps_order(monkeypatch, jobs):
    monkeypatch.setenv("ESPGAL_JOBS", jobs)
    assert parallel.map_ordered(_square, range(40)) == [x * x for x in range(40)]


# ---- stage table prefixes (diff/stagetables.py)

def _unique_prefix_plain(p, a, n):
    while n >= stagetables.MIN and p.count(a[:n]) != 1:
        n -= 2
    return n


def test_unique_prefix_matches_plain():
    rnd = random.Random(5)
    for _ in range(400):
        alpha = rnd.choice([b"ab", b"abc", bytes(range(8))])
        p = bytes(rnd.choice(alpha) for _ in range(rnd.randint(0, 200)))
        if rnd.random() < 0.5 and len(p) > 20:
            k = rnd.randint(0, len(p) - 20)
            a = p[k:k + rnd.randint(0, 40)] + bytes(rnd.choice(alpha) for _ in range(rnd.randint(0, 6)))
        else:
            a = bytes(rnd.choice(alpha) for _ in range(rnd.randint(0, 40)))
        n = rnd.randint(0, len(a)) & ~1
        assert stagetables._unique_prefix(p, a, n) == _unique_prefix_plain(p, a, n), (p, a, n)


# ---- patch range checks (stages/code.py)

def test_spans_match_plain():
    from espgalarr.stages import code
    rnd = random.Random(6)
    for _ in range(200):
        spans = [(lo, lo + rnd.randint(0, 12)) for lo in (rnd.randint(0, 80) for _ in range(rnd.randint(0, 12)))]
        s = code._Spans(spans)
        for a in range(-2, 100):
            size = rnd.randint(1, 10)
            assert s.overlaps(a, a + size) == any(lo < a + size and a < hi for lo, hi in spans)
            assert s.overlaps(a, a + 1) == any(lo <= a < hi for lo, hi in spans)


# ---- sprite code references (stages/gfx.py)

def _ref_sites_plain(p, code):
    import struct
    sites, data_hits = [], []
    for i in range(0, 0x200000 - 3, 2):
        v = struct.unpack_from(">I", p, i)[0]
        if (v & 0x7FFFFF) == code and (v >> 29) == 0:
            (sites if 0x110000 <= i < 0x1C0000 else data_hits).append(i)
    for d in data_hits:
        start = d
        while start - 4 >= 0 and (struct.unpack_from(">I", p, start - 4)[0] >> 29) == 0 \
                and (struct.unpack_from(">I", p, start - 4)[0] & 0x7FFFFF) > 0x100:
            start -= 4
        for i in range(0x110000, 0x1C0000 - 5, 2):
            op = struct.unpack_from(">H", p, i)[0]
            if (op & 0xF1FF) == 0x41FA:
                if i + 2 + struct.unpack_from(">h", p, i + 2)[0] == start:
                    sites.append(i)
            elif (op & 0xF1FF) == 0x41F9:
                if struct.unpack_from(">I", p, i + 2)[0] == start:
                    sites.append(i)
    return sites


def test_ref_sites_match_plain():
    import struct
    rng = np.random.RandomState(7)
    p = bytearray(rng.randint(0, 256, 0x400000).astype(np.uint8).tobytes())
    code = 0x123456
    for at in (0x10, 0x110000, 0x150002, 0x1BFFFC, 0x1FFFFC, 0x80000, 0x80000):   # (one twice)
        struct.pack_into(">I", p, at, code)
    struct.pack_into(">III", p, 0x90000, 0x2000, 0x3000, code)        # an array ending in it
    struct.pack_into(">HI", p, 0x120000, 0x41F9, 0x90000)               # lea abs.l of its start
    struct.pack_into(">Hh", p, 0x130000, 0x43FA, 0x7FFE)                # lea d16(pc) (other target)
    struct.pack_into(">HI", p, 0x1BFFFA, 0x45F9, 0x80000)               # at the scan's last place
    struct.pack_into(">HI", p, 0x1BFFFC, 0x41F9, 0x80000)               # past it
    pcrel = 0x90000 - 0x10000                                           # (in range of a pc lea)
    struct.pack_into(">Hh", p, pcrel - 2, 0x41FA, 0x7FFE)
    struct.pack_into(">II", p, 0x1C000C, 0xE0000000, code)              # an array just past the code
    struct.pack_into(">Hh", p, 0x1BFF00, 0x41FA, 0x1C0010 - 0x1BFF02)    # lea d16(pc) of it
    for c in (code, 0x2000, 0x999999):
        assert stage.ref_sites_for_code(bytes(p), c) == _ref_sites_plain(bytes(p), c)
