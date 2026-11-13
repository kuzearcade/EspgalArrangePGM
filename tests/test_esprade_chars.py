"""build --esprade-chars (espgalarr/esprade_chars.py): the state -> ESP Ra.De. animation mapping,
the frame picking, the colour quantiser and the records. Synthetic data; the ROM-backed checks run
only when mame_roms/esprade.zip (MAME's esprade) is present."""
from pathlib import Path
import struct

import numpy as np
import pytest

from espgalarr import anim, esprade_chars as ec

# Arrange's frame list lengths per table (end // 4 of the handlers, spec/arrange-characters.md)
LENGTHS = {ec.IDLE: 8, ec.LEAN: 36, ec.LEAN_L: 36, ec.UP: 19, ec.DOWN: 19, ec.RETURN: 4, ec.RETURN_L: 4,
           ec.UP_RET: 4, ec.DOWN_RET: 4, ec.S9: 4, ec.POWER1: 5, ec.POWER2: 13, ec.BAR18: 22, ec.BAR19: 4,
           ec.BAR20: 5, ec.BAR21: 4, ec.S22: 3, ec.ENTRY: 34}


def test_every_table_maps_to_known_animations():
    for name in ec.CHARACTERS.values():
        assert set(ec.ANIMS[name]) >= {a for t in LENGTHS for a, _ in ec._segments(t)}
    # the transition + hold loop tables split where Arrange's loops restart (28 and 11 records)
    assert ec._segments(ec.LEAN)[0] == ("lean_right", 28) and ec._segments(ec.UP)[0] == ("lean_up", 11)


def test_one_to_one_where_the_counts_match():
    for name in ec.CHARACTERS.values():
        a = ec.ANIMS[name]
        for anim_name, n in (("idle", 8), ("lean_right", 28), ("guard_start", 22), ("death_knockback_respawn", 34)):
            assert len(a[anim_name]) == n
            assert ec._pick(a[anim_name], n) == a[anim_name]


def test_pick_by_time():
    frames = [(i, 48, 48, -24, -24, d) for i, d in enumerate((1, 1, 2, 4))]     # 8 frames long
    assert [f[0] for f in ec._pick(frames, 4)] == [0, 1, 2, 3]          # counts match: one to one
    assert [f[0] for f in ec._pick(frames, 3)] == [0, 2, 3]
    assert [f[0] for f in ec._pick(frames, 2)] == [0, 3]


def test_quantise_keeps_transparency_and_32_colours():
    rng = np.random.default_rng(1)
    pal = rng.integers(0, 32, (256, 3))
    px = [rng.integers(0, 256, (48, 48)).astype(np.uint8) for _ in range(3)]
    px[0][:10] = 0
    out_pal, idx = ec.quantise(px, pal)
    assert out_pal.shape == (32, 3) and out_pal.max() <= 31
    for p, q in zip(px, idx):
        assert ((p == 0) == (q == 255)).all()
        assert q[p != 0].max() < 32
    # exact when no more than 32 colours are used
    few = [np.array([[0, 5, 9], [9, 200, 5]], np.uint8)]
    out_pal, idx = ec.quantise(few, pal)
    for v, i in ((5, idx[0][0, 1]), (9, idx[0][0, 2]), (200, idx[0][1, 1])):
        assert tuple(out_pal[i]) == tuple(pal[v])


def test_palette_words():
    words = struct.unpack(">3H", ec.palette_words(np.array([[31, 0, 0], [0, 31, 0], [0, 0, 31]])))
    assert words == (31 << 10, 31 << 5, 31)


def test_states_2_and_6_get_their_own_tables_without_the_mirror_bit():
    pf = ec.PlayerFrames.__new__(ec.PlayerFrames)
    row = [None] * anim.STATES
    row[2] = ("loop", ec.LEAN, dict(set6=True, reload=1, end=0x90, restart=0x70, with3a=True))
    row[6] = ("once", ec.RETURN, dict(set6=True, reload=2, end=0x10, next=0, clr3a=True))
    out = pf.adjust_row(row)
    assert out[2][1] == ec.LEAN_L and "set6" not in out[2][2] and out[2][2]["restart"] == 0x70
    assert out[6][1] == ec.RETURN_L and "set6" not in out[6][2]


TIMER, SPEED = 9, 13


class _Elf:
    """One Arrange frame list per table: records with an add word and Arrange's own fields."""
    def __init__(self):
        self.mem = {}

    def table(self, tab, n, size):
        lp, base = 0x100000 + tab % 0x10000 * 0x10, 0x200000 + tab % 0x10000 * 0x400
        self.mem[tab] = struct.pack("<4I", lp, lp, lp, lp)
        self.mem[lp] = struct.pack(f"<{n}I", *[base + i * size for i in range(n)])
        for i in range(n):
            rec = bytearray(size)
            struct.pack_into(">hh", rec, 4, 64, -128)            # +4: added to the offset
            struct.pack_into(">H", rec, 0xE, 0x1234)             # +0xE: Arrange's, kept
            if size == 20:
                rec[16:20] = struct.pack(">HH", TIMER, SPEED)        # timer / speed words, kept
            self.mem[base + i * size] = bytes(rec)

    def read(self, a, n):
        return self.mem[a][:n]


def test_records_place_each_frame_where_esp_draws_it():
    elf = _Elf()
    for tab, n in LENGTHS.items():
        if tab not in (ec.LEAN_L, ec.RETURN_L):
            elf.table(tab, n, 20 if tab == ec.ENTRY else 16)
    pf = ec.PlayerFrames.__new__(ec.PlayerFrames)
    pf.elf = elf
    for tab, n in LENGTHS.items():
        size = 20 if tab == ec.ENTRY else 16
        for form in range(4):
            recs = pf.records(tab, form, n, size)
            assert len(recs) == n
            for rec, key in recs:
                off, add = struct.unpack_from(">hh", rec, 0), struct.unpack_from(">hh", rec, 4)
                # frame top-left = dx + the draw shift, dy (pixels), after the +4 add
                assert ((off[0] + add[0]) / 64, (off[1] + add[1]) / 64) == (-24 + ec.DRAW_SHIFT_X, -24)
                assert struct.unpack_from(">I", rec, 8)[0] == 0
                assert struct.unpack_from(">H", rec, 0xC)[0] == (48 // 16) << 9 | 48
                assert struct.unpack_from(">H", rec, 0xE)[0] == 0x1234
                if size == 20:
                    assert struct.unpack_from(">HH", rec, 16) == (TIMER, SPEED)
                assert key.startswith("esp_" + ("irori" if form < 2 else "jb5"))


ZIP = Path(__file__).resolve().parents[1] / "mame_roms" / "esprade.zip"


@pytest.mark.skipif(not ZIP.exists(), reason="no mame_roms/esprade.zip")
def test_rom_frames_and_palette():
    from espgalarr import esprade
    rom = esprade.EspRade(ZIP)
    pal = rom.palette(ec.PALETTES["jb5"])
    assert pal.shape == (256, 3) and pal.max() <= 31
    for name in ec.CHARACTERS.values():
        for frames in ec.ANIMS[name].values():
            for c, w, h, _, _, _ in frames:
                assert rom.sprite(c, w, h).any(), f"{name} {c:#x} is empty"


def test_output_sets_are_kept_apart(tmp_path):
    """Each build's manifest names its set; folders from before that are told apart by the log."""
    import json
    from espgalarr import build
    assert build.set_in(tmp_path) is None
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {}}))
    (tmp_path / "build.log").write_text("code: Arrange player animation: ... (12 sprites)\n")
    assert build.set_in(tmp_path) == build.REGULAR_SET
    (tmp_path / "build.log").write_text("code: Arrange player animation: ... (ESP Ra.De. frames (...))\n")
    assert build.set_in(tmp_path) == build.ESPRADE_SET
    (tmp_path / "manifest.json").write_text(json.dumps({"set": build.REGULAR_SET, "files": {}}))
    assert build.set_in(tmp_path) == build.REGULAR_SET
    assert build.ESPRADE_SET == "espgalarr_esprade"
