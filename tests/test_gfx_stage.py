"""Graphics stage helpers on synthetic data."""
import struct

import numpy as np

from espgalarr.pgm import gfx
from espgalarr.stages import gfx as stage


def test_locate_sprite_roundtrip():
    rng = np.random.RandomState(5)
    px = rng.randint(0, 31, (24, 32)).astype(np.uint8)
    px[rng.rand(24, 32) < 0.3] = 255
    a, b = gfx.encode_sprite(px)
    a_rom = b"\x00" * 64 + a
    b_rom = b"\x11" * 100 + gfx.b_header(32) + b + b"\x22" * 50
    flat = np.where(px == 255, 0x1F, px).astype(np.uint8).reshape(-1)
    off = stage.locate_sprite(b_rom, a_rom, flat)
    assert off == 50


def test_scan_records_collects_variants():
    rec = lambda sid, size, flags: struct.pack(">IIIHH", sid, 0x00010002, 0x00030004, size, flags)  # noqa: E731
    img = b"\x00" * 16 + rec(9034, (26 << 9) | 160, 0xE) + rec(9035, (9 << 9) | 104, 0xE) + rec(9034, (26 << 9) | 160, 0x58)
    recs = stage._scan_records(img, 0, len(img))
    assert [r[-4:] for r in recs[9034]] == [struct.pack(">HH", (26 << 9) | 160, 0xE), struct.pack(">HH", (26 << 9) | 160, 0x58)]
    assert len(recs[9035]) == 1
