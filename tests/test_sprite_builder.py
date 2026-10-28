"""Differential test of asm/m68k/sprite_builder.s against the original frame-end builder.

Runs both in a 68000 emulator (machine68k) on random sprite-layer contents. Needs the
original program ROM in work/ (skipped otherwise); nothing from it is stored in the repo.
"""
import random
import struct
from pathlib import Path

import pytest

machine68k = pytest.importorskip("machine68k")

from espgalarr.pgm import asm68k  # noqa: E402
from espgalarr.stages.code import expand_asm  # noqa: E402
from espgalarr.stages.layout import ORIG_LAYERS, sprite_layout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROM = ROOT / "work/pgm/espgalbl/espgaluda_u8.bin"
SRC = ROOT / "asm/m68k/sprite_builder.s"
BUILDER = 0x113D7A
NEW_AT = 0x200000
SENTINEL = 0x3FFF00
MAIN, EXT = 0x800000, 0xD0C840
DUMMY = bytes.fromhex("fc003800000000000201")

pytestmark = pytest.mark.skipif(not PROM.exists(), reason="original program ROM not extracted")


class _State:
    sprite_layers = None


def _source(layers=None):
    st = _State()
    st.sprite_layers = layers
    return expand_asm(SRC.read_text(), st)


def _layers():
    return [(b, c) for b, c, _ in ORIG_LAYERS]


def _machine(prom: bytes, with_new: bool, layers=None):
    m = machine68k.Machine(machine68k.CPUType.M68000, 0xE10000 // 1024)
    mem = m.mem
    swapped = bytearray(prom)
    swapped[0::2], swapped[1::2] = prom[1::2], prom[0::2]
    mem.w_block(0, bytes(swapped[:0x400000]))
    if with_new:
        code, _ = asm68k.assemble(_source(layers), NEW_AT)
        mem.w_block(NEW_AT, code)
        mem.w_block(BUILDER, bytes([0x4E, 0xF9]) + struct.pack(">I", NEW_AT))
    mem.w16(SENTINEL, 0x60FE)  # bra.s *
    return m


def _run(m, ram: dict[int, bytes]):
    for a, data in ram.items():
        m.mem.w_block(a, data)
    cpu = m.cpu
    cpu.w_sr(0x2700)
    sp = 0x81F000
    m.mem.w32(sp, SENTINEL)
    cpu.w_sp(sp)
    cpu.w_pc(BUILDER)
    for _ in range(400):
        cpu.execute(20000)
        if cpu.r_pc() in (SENTINEL, SENTINEL + 2):
            return
    raise AssertionError(f"builder did not return (pc={cpu.r_pc():#x})")


def _entries(mem, base, n):
    return [bytes(mem.r_block(base + 10 * i, 10)) for i in range(n)]


def _expected(layers, counts, data, ofs):
    """Python model: every entry in layer order, dummy after 51 then every 50."""
    out, d4 = [], 0x33
    for (base, _), cnt in zip(layers, counts):
        for i in range(cnt):
            d4 -= 1
            if d4 < 0:
                out.append(DUMMY)
                d4 = 0x31
            w = data[base][12 * i:12 * i + 12]
            l0 = struct.unpack(">I", w[:4])[0]
            l1 = (((l0 & 0x07FF3FFF) + ofs) & 0x07FF3FFF) | (l0 & 0xF800F800)
            e = bytearray(struct.pack(">I", l1) + w[4:10])
            e[4] = w[10] | w[11]
            out.append(bytes(e))
    return out


def _capacity(layers):
    """Entries each layer list can hold before it runs into the next RAM block."""
    bases = sorted(b for b, _ in layers) + [0x806FCA]
    cap = [(bases[bases.index(b) + 1] - b) // 12 for b, _ in layers]
    assert cap == [n for _, _, n in ORIG_LAYERS]
    return cap


def _scenario(rng, layers, total):
    cap = _capacity(layers)
    assert total <= sum(cap)
    counts = [0] * len(layers)
    for _ in range(total):
        i = rng.choice([k for k in range(len(layers)) if counts[k] < cap[k]])
        counts[i] += 1
    data, ram = {}, {}
    for (base, ctr), cnt in zip(layers, counts):
        blob = bytes(rng.randrange(256) for _ in range(12 * cnt))
        # keep the size word non-zero so entries never look like terminators
        blob = bytearray(blob)
        for i in range(cnt):
            if blob[12 * i + 8] & 0x7F == 0 and blob[12 * i + 9] == 0:
                blob[12 * i + 9] = 1
        data[base] = bytes(blob)
        ram[base] = bytes(blob)
        ram[ctr] = struct.pack(">H", 12 * cnt)
    ofs = rng.randrange(1 << 32)
    ram[0x807058] = struct.pack(">I", ofs)
    return counts, data, ram, ofs


@pytest.fixture(scope="module")
def prom():
    return PROM.read_bytes()


@pytest.mark.parametrize("total", [0, 1, 50, 51, 52, 120, 200, 250])
def test_matches_original_below_cap(prom, total):
    """With room to spare both builders produce the identical hardware list."""
    layers = _layers()
    rng = random.Random(total)
    counts, data, ram, ofs = _scenario(rng, layers, total)
    n = len(_expected(layers, counts, data, ofs)) + 1  # + terminator
    # machine68k drives one global emulator: run the two builders one after the other
    old = _machine(prom, False)
    _run(old, ram)
    want = _entries(old.mem, MAIN, n)
    old.cleanup()
    new = _machine(prom, True)
    _run(new, ram)
    assert _entries(new.mem, MAIN, n) == want
    assert _entries(new.mem, EXT, 1)[0][8:] == b"\0\0"
    for _, ctr in layers:
        assert new.mem.r16(ctr) == 0
    new.cleanup()


@pytest.mark.parametrize("total", [251, 252, 300, 800, 1300, 1600, 2000])
def test_spills_into_continuation(prom, total):
    layers = _layers()
    rng = random.Random(1000 + total)
    counts, data, ram, ofs = _scenario(rng, layers, total)
    new = _machine(prom, True)
    _run(new, ram)
    want = _expected(layers, counts, data, ofs)[:256 + 1400]
    got = _entries(new.mem, MAIN, 256) + _entries(new.mem, EXT, 1401)
    assert got[:len(want)] == want
    assert got[len(want)][8:] == b"\0\0"  # terminator right after the last sprite
    new.cleanup()


def test_arrange_layout(prom):
    """Arrange lists in enhanced RAM + the three new layers; counters above capacity clamp."""
    lay = sprite_layout(True)
    layers = [(b, c) for b, c, _ in lay.layers]
    caps = [n for _, _, n in lay.layers]
    assert len(layers) == 30 and lay.layers[19][0] == lay.new_layers[0][0]
    rng = random.Random(7)
    counts = [rng.randrange(0, 40) for _ in layers]
    counts[layers.index((lay.moved[0x805A5E], 0x806FEE))] = 400       # bullets
    over = layers.index((lay.new_layers[1][0], lay.new_layers[1][1]))
    data, ram = {}, {}
    for (base, ctr), cnt, cap in zip(layers, counts, caps):
        blob = bytearray(rng.randrange(256) for _ in range(12 * min(cnt, cap)))
        for i in range(len(blob) // 12):
            blob[12 * i + 9] |= 1
        data[base] = bytes(blob)
        ram[base] = bytes(blob)
        ram[ctr] = struct.pack(">H", 12 * cnt)
    ram[lay.new_layers[1][1]] = struct.pack(">H", 0xFFFC)                 # garbage counter
    data[lay.new_layers[1][0]] = bytes(blob := bytearray(rng.randrange(256) for _ in range(12 * caps[over])))
    for i in range(caps[over]):
        blob[12 * i + 9] |= 1
    data[lay.new_layers[1][0]] = ram[lay.new_layers[1][0]] = bytes(blob)
    counts[over] = caps[over]
    ofs = 0
    ram[0x807058] = struct.pack(">I", ofs)
    new = _machine(prom, True, lay.layers)
    _run(new, ram)
    want = _expected(layers, [min(c, n) for c, n in zip(counts, caps)], data, ofs)
    got = _entries(new.mem, MAIN, 256) + _entries(new.mem, EXT, 1401)
    assert got[:len(want)] == want
    assert got[len(want)][8:] == b"\0\0"
    for _, ctr in layers:
        assert new.mem.r16(ctr) == 0
    new.cleanup()
