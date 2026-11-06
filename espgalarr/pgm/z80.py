"""Build the patched Z80 sound driver: original blob + streaming extension + redirections."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from . import asmz80, sound

STREAM_ORG = 0x6900           # former BGM instrument window (no longer uploaded)
SONG_PLAY = 0x2E47            # driver C function: play song (HL = id, DE = mode)
SONG_STOP = 0x2DAA            # driver C function: stop song
SONG_FADE = 0x2DC7            # driver C function: fade song
TIMER_TICK = 0x0FF9           # driver timer-IRQ tick routine (first 6 bytes = ld de / ld hl)
MAIN_LOOP_FN = 0x13D0         # routine called every main-loop iteration (first 7 bytes relocated)
ICS_CLOCK = 33_075            # output sample rate in 32-voice mode
OSC_CFG_STREAM = 0x0D         # ulaw | eightbit | loop (ICS2115 oscillator configuration bits)
VOLUME = 0xFFF0


@dataclass
class TrackEntry:
    cmd: int           # 68k BGM index
    ics_start: int     # absolute ICS address
    loop_start: int
    ics_end: int
    rate: int

    def pack(self) -> bytes:
        bank = self.ics_start >> 20
        assert bank == (self.ics_end - 1) >> 20, "stream must not cross a 1 MB bank"
        start = self.ics_start & 0xFFFFF
        loop = self.loop_start & 0xFFFFF
        end = (self.ics_end - 1) & 0xFFFFF
        fc = int(round(self.rate * 1024 / ICS_CLOCK)) & 0xFFFE

        def hi(x):
            return (x << 12) >> 16 & 0xFFFF

        def lo(x):
            return (x << 12) & 0xFFFF
        return struct.pack("<BBH HH HH HH", 0x40 | bank, OSC_CFG_STREAM, fc,
                           hi(start), lo(start), hi(loop), lo(loop), hi(end), lo(end))


def build_blob(p_rom: bytes, tracks: list[TrackEntry], asm_path: Path) -> bytes:
    """Return the new Z80 image (0x0000..end) to upload in place of the original blob."""
    off, size = sound.find_z80_blob(p_rom)
    blob = bytearray(p_rom[off:off + size])
    n = max(t.cmd for t in tracks) + 1
    table = bytearray(n * 16)
    for t in tracks:
        table[t.cmd * 16:(t.cmd + 1) * 16] = t.pack()
    src = asm_path.read_text()
    src = src.replace("STREAM_ORG", f"${STREAM_ORG:x}").replace("N_TRACKS", str(n)).replace("VOLUME", f"${VOLUME:x}")
    code, syms = asmz80.assemble(src)
    image = bytearray(blob)
    image += b"\x00" * (STREAM_ORG - len(image))
    image += code + bytes(table)
    for entry, target in ((SONG_PLAY, syms["stream_play"]), (SONG_STOP, syms["stream_stop"]), (SONG_FADE, syms["stream_fade"]),
                          (TIMER_TICK, syms["irq_hook"]), (MAIN_LOOP_FN, syms["loop_hook"])):
        image[entry:entry + 3] = bytes((0xC3, target & 0xFF, target >> 8))
    assert blob[TIMER_TICK:TIMER_TICK + 6] == bytes.fromhex("000000000000"), "unexpected timer tick prologue"
    assert blob[MAIN_LOOP_FN:MAIN_LOOP_FN + 7] == bytes.fromhex("00000000000000"), "unexpected main-loop prologue"
    image[MAIN_LOOP_FN + 3:MAIN_LOOP_FN + 7] = b"\x00" * 4
    if len(image) & 1:
        image += b"\x00"
    return bytes(image)


def build_blob_adx(p_rom: bytes, tracks: dict[int, int], asm_path: Path) -> bytes:
    """The Z80 image for the lossless BGM (asm/z80/adxstream.z80): `tracks` maps the 68k BGM index
    to its track in the BGM blob; the song functions drive the stream player at I/O 0x8800."""
    off, size = sound.find_z80_blob(p_rom)
    blob = bytearray(p_rom[off:off + size])
    n = max(tracks) + 1
    table = bytearray(b"\xff" * n)
    for cmd, idx in tracks.items():
        table[cmd] = idx
    src = asm_path.read_text().replace("STREAM_ORG", f"${STREAM_ORG:x}").replace("N_TRACKS", str(n))
    code, syms = asmz80.assemble(src)
    image = bytearray(blob)
    image += b"\x00" * (STREAM_ORG - len(image))
    image += code + bytes(table)
    # (the player times its own fades: no timer / main-loop hooks)
    for entry, target in ((SONG_PLAY, syms["stream_play"]), (SONG_STOP, syms["stream_stop"]), (SONG_FADE, syms["stream_fade"])):
        image[entry:entry + 3] = bytes((0xC3, target & 0xFF, target >> 8))
    if len(image) & 1:
        image += b"\x00"
    return bytes(image)
