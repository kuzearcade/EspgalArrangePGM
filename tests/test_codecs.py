"""Codec round-trip tests on synthetic data only (no original game bytes)."""
import struct

import numpy as np

from espgalarr.pgm import gfx, ics2115, rom
from espgalarr.ps2 import afs, overlay
from espgalarr.diff import datablock as db


def test_sprite_roundtrip():
    rng = np.random.RandomState(7)
    px = rng.randint(0, 32, (40, 48)).astype(np.uint8)
    px[rng.rand(40, 48) < 0.5] = 255
    a, b = gfx.encode_sprite(px)
    sp = gfx.decode_sprite(a, gfx.b_header(0) + b, 0, 3, 40)
    assert np.array_equal(sp.pixels, px)


def test_sprite_a_offset_header():
    rng = np.random.RandomState(3)
    px = rng.randint(0, 32, (8, 16)).astype(np.uint8)
    a, b = gfx.encode_sprite(px)
    pad = b"\x00" * 10   # A data placed at word index 5
    brom = gfx.b_header(5) + b
    sp = gfx.decode_sprite(pad + a, brom, 0, 1, 8)
    assert np.array_equal(sp.pixels, px)


def test_ulaw_monotonic_and_symmetric():
    t = ics2115.ulaw_decode_table()
    assert t[0x7F] == 0 and t[0] == t.max() and t[0x80] == -t[0]
    x = np.array([-30000, -1000, -10, 0, 10, 1000, 30000], dtype=np.int16)
    y = ics2115.ulaw_decode(ics2115.ulaw_encode(x))
    assert np.all(np.diff(y.astype(int)) >= 0)
    assert abs(int(y[-1]) - 30000) < 1200 and abs(int(y[0]) + 30000) < 1200


def test_bank_packer_never_crosses_bank():
    p = ics2115.BankPacker(reserved=[(0x400000, 0x400100)])
    for i in range(12):
        pl = p.place(f"s{i}", 0x90000)
        assert pl.offset // rom.ICS_BANK == (pl.end - 1) // rom.ICS_BANK


def test_swap16():
    assert rom.swap16(b"\x00\x00\x00\x00") == b"\x00\x00\x00\x00"


def test_free_runs():
    data = b"\x12" * 0x2000 + b"\xff" * 0x20000 + b"\x34" * 0x1000 + b"\x00" * 0x10000
    runs = rom.free_runs(data, block=0x1000, min_size=0x10000)
    assert [(r.start, r.end, r.fill) for r in runs] == [(0x2000, 0x22000, 0xFF), (0x23000, 0x33000, 0)]


def test_afs_index():
    hdr = afs.MAGIC + struct.pack("<I", 2) + struct.pack("<II", 0x800, 10) + struct.pack("<II", 0x1000, 20)
    ents = afs.read_index(hdr)
    assert [(e.offset, e.size) for e in ents] == [(0x800, 10), (0x1000, 20)]


def test_overlay_header():
    h = overlay.MAGIC + struct.pack("<7I", 7, 0x649800, 0x100, 0x20, 0x8, 0x649920, 0x649920)
    h += b"ov_test.bin".ljust(0x20, b"\0")
    ph = overlay.parse_header(h)
    assert ph.module_id == 7 and ph.load_addr == 0x649800 and ph.name == "ov_test.bin"


def test_datablock_alignment():
    rng = np.random.RandomState(1)
    rom_img = rng.bytes(0x1000)
    ov = rng.bytes(0x100) + rom_img[0x200:0x600] + rng.bytes(0x80) + rom_img[0x800:0xA00]
    segs = db.match_segments(rom_img, ov, 0, len(rom_img), min_len=64)
    pairs = sorted((s.rom_off, s.ov_off, s.length) for s in segs)
    assert pairs == [(0x200, 0x100, 0x400), (0x800, 0x580, 0x200)]


def _adx_stream(frames, loop=None):
    """A synthetic version 4 ADX stream (2 channels, 48 kHz, cutoff 500) from
    [(scale word, 16 nibble bytes) per channel] per frame; loop = (start, end) samples."""
    from espgalarr.ps2 import adx  # noqa: F401
    coff = 0x3C
    h = bytearray(coff + 4)
    h[0:2] = b"\x80\x00"
    struct.pack_into(">H", h, 2, coff)
    h[4:8] = bytes([3, 18, 4, 2])
    struct.pack_into(">IIH", h, 8, 48000, 32 * len(frames), 500)
    h[0x12] = 4
    if loop:
        struct.pack_into(">IIIII", h, 0x24, 1, loop[0], 0, loop[1], 0)
    body = b"".join(struct.pack(">H", w) + nib for fr in frames for w, nib in fr)
    return bytes(h) + body


def test_adx_decode_exact():
    from espgalarr.ps2 import adx
    assert adx.coefficients_int(500, 48000) == (7400, -3342)
    # frame 0: left nibbles +7 then -8 at scale word 0x0100 (scale 0x101); right all 0
    nl = bytes([0x78] * 16)
    fr0 = [(0x0100, nl), (0x0000, bytes(16))]
    fr1 = [(0xE000 | 0x1FFF, bytes([0x11] * 16)), (0x0001, bytes([0xF0] * 16))]
    d = _adx_stream([fr0, fr1], loop=(32, 40))
    out = adx.decode_exact(d, 64)
    c1, c2 = 7400, -3342
    h1 = h2 = 0
    want = []
    saved = None
    for i in range(64):
        p = 32 + (i - 40) % 8 if i >= 40 else i      # loop 40 -> 32
        if p == 32:                                  # the PS2 restores the loop start's history
            if saved is None:
                saved = (h1, h2)
            else:
                h1, h2 = saved
        f, j = divmod(p, 32)
        if f == 0:
            nib, scale = (7 if j % 2 == 0 else -8), 0x101
        else:
            nib, scale = 1, 0x2000                     # (word & 0x1FFF) + 1, the top bits ignored
        s = max(-32768, min(32767, ((c1 * h1 + c2 * h2) >> 12) + nib * scale))
        want.append(s)
        h1, h2 = s, h1
    assert out[:, 0].tolist() == want
    assert out[:32, 1].tolist() == [0] * 32
    assert out[32, 1] == -2 and out[33, 1] < 0        # right frame 1: nibbles -1 / 0 at scale 2
