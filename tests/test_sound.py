"""Sanity checks for espgalarr.pgm.sound against the real ROM (skipped if absent)."""
from pathlib import Path

import pytest

from espgalarr.pgm import sound
from espgalarr.pgm.rom import RomSet

WORK = Path(__file__).resolve().parents[1] / "work"


@pytest.fixture(scope="module")
def p_rom():
    rs = RomSet(WORK)
    if not rs.p_file.exists():
        pytest.skip("espgalbl P ROM not present")
    return rs.p_rom_68k()


def test_z80_blob(p_rom):
    off, size = sound.find_z80_blob(p_rom)
    assert (off, size) == (0x1BE9D0, 0x61A2)
    hdr = sound.parse_driver(p_rom[off:off + size])
    assert hdr["sfx_table_ptr"] == 0x76C0 and hdr["bgm_table_ptr"] == 0x6900
    assert hdr["song_table_ptr"] == 0x0070 and hdr["bgm_count"] == 160


def test_tables(p_rom):
    sfx = sound.parse_sfx_table(p_rom)
    assert len(sfx) == 102
    assert sfx[82].start == 0x557018 and sfx[82].end == 0x55E0ED   # coin SFX (MAME trace)
    songs = sound.parse_song_banks(p_rom)
    assert len(songs) == 13 and all(s.n_channels == 16 for s in songs)
    u = sound.sample_usage(p_rom)
    assert not u.shared
    assert u.referenced_extent() == (0x400000, 0x7161FA)
    free = sound.free_m_rom_ranges(u, 0x400000)
    assert free[-1] == (0x1BD167, 0x400000)
