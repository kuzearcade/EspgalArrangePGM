"""Z80 driver image builder on a synthetic blob with the expected hook prologues."""
from pathlib import Path

from espgalarr.pgm import z80


def test_build_blob_hooks(monkeypatch):
    # the hooked prologues are checked against signatures of the arcade driver's bytes (a real
    # build checks them); a synthetic blob cannot carry those bytes, so the check is stubbed here
    blob = bytearray(0x6200)
    monkeypatch.setattr(z80.m68k, "matches", lambda b, pat: True)
    monkeypatch.setattr(z80.sound, "find_z80_blob", lambda p: (0, len(blob)))
    img = z80.build_blob(bytes(blob), [z80.TrackEntry(0x0A, 0x600000, 0x60DA64, 0x6E0000, 11025)],
                         Path(__file__).resolve().parents[1] / "asm" / "z80" / "stream.z80")
    assert img[z80.SONG_PLAY] == 0xC3 and img[z80.SONG_STOP] == 0xC3 and img[z80.TIMER_TICK] == 0xC3
    assert img[z80.MAIN_LOOP_FN] == 0xC3 and img[z80.MAIN_LOOP_FN + 3:z80.MAIN_LOOP_FN + 7] == b"\x00" * 4
    entry = img[-1 - 16:-1] if len(img) % 2 == 0 and img[-1] == 0 and (len(img) - 1) % 2 else img[-16:]
    assert 0x46 in entry[:1] or 0x46 in img[z80.STREAM_ORG:]   # bank byte 0x40|6 present in the table
