"""Arrange character voices (spec/arrange-diff.md, "Character voices").

The PS2 plays the characters' voices from a 24-sample sound bank (bank 2: sound requests
0x8002nn03 through ELF 0x153F80, A0A9 stubs 0x79ADF0.. one per index; the entry animation's
table A0A9 0xBFCD40 asks for indices 5 / 0x0B / 0x17 / 0x11). The disc holds two such banks
(IECS header + VAG ADPCM data): A009 / A008, the arcade characters', and A00C / A00B, the Arrange
characters' (Chihiro, Black). The arcade bank's 24 voices are the PGM SFX 0x34..0x3F and
0x5A..0x65 in index order (checked at build time by duration), and the arcade code requests
them where Arrange requests the same index of its own bank (the entry voices: 0x39 / 0x3F / 0x65
/ 0x5F = indices 5 / 0x0B / 0x17 / 0x11). So the port replaces those 24 SFX samples with the
Arrange bank's, decoded, at the arcade voices' 16 kHz signed 8-bit format and loudness, placed in
the sample ROM before the streamed music (stages/music.py) and pointed at from the SFX table.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .music import loop as loop_mod
from .pgm import ics2115, sound

ARCADE_BANK = ("A009", "A008")      # (header, sample data) in A000
ARRANGE_BANK = ("A00C", "A00B")
VOICE_SFX = tuple(range(0x34, 0x40)) + tuple(range(0x5A, 0x66))   # bank index k -> SFX id
RATE = 16000
_COEF = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))


def vag_decode(data: bytes) -> np.ndarray:
    """Sony VAG (PS-ADPCM) -> int16 samples, to the block flagged as the end."""
    out, h1, h2 = [], 0, 0
    for o in range(0, len(data) - 15, 16):
        pred, shift, flags = data[o] >> 4, data[o] & 0xF, data[o + 1]
        f0, f1 = _COEF[pred] if pred < 5 else (0, 0)
        for byte in data[o + 2:o + 16]:
            for nib in (byte & 0xF, byte >> 4):
                s = ((nib - 16 if nib >= 8 else nib) << 12) >> shift
                s = max(-32768, min(32767, s + (h1 * f0 + h2 * f1) // 64))
                out.append(s)
                h2, h1 = h1, s
        if flags & 1 and o > 0:
            break
    return np.array(out, np.int16)


def read_bank(disc: Path, files: tuple[str, str]) -> list[tuple[int, np.ndarray]]:
    """[(rate, samples)] of an IECS bank (its 'Vagi' chunk: data offset + rate per sample)."""
    hd = (disc / "A000" / files[0]).read_bytes()
    bd = (disc / "A000" / files[1]).read_bytes()
    head = hd.find(b"IECSdaeH")
    vagi = struct.unpack_from("<I", hd, head + 0x20)[0]
    assert hd[vagi:vagi + 8] == b"IECSigaV", f"{files[0]}: no Vagi chunk"
    n = struct.unpack_from("<I", hd, vagi + 12)[0] + 1
    ents = [struct.unpack_from("<IH", hd, vagi + o) for o in struct.unpack_from(f"<{n}I", hd, vagi + 16)]
    out = []
    for k, (off, rate) in enumerate(ents):
        end = ents[k + 1][0] if k + 1 < n else len(bd)
        out.append((rate, vag_decode(bd[off:end])))
    return out


def _envelope(x: np.ndarray, rate: int, step: float = 0.01) -> np.ndarray:
    n = int(rate * step)
    e = np.array([np.abs(x[i:i + n]).mean() for i in range(0, len(x) - n, n)])
    return e / (e.max() or 1.0)


@dataclass
class Voice:
    sfx: int
    data: bytes          # signed 8-bit at RATE


def arrange_voices(disc: Path, p_rom: bytes, m_rom: bytes) -> list[Voice]:
    """The 24 Arrange voices for VOICE_SFX, at the arcade conversion's loudness."""
    arcade, arrange = read_bank(disc, ARCADE_BANK), read_bank(disc, ARRANGE_BANK)
    assert len(arcade) == len(arrange) == len(VOICE_SFX), "voice bank sizes"
    sfx = sound.parse_sfx_table(p_rom)
    gains = []
    for k, sid in enumerate(VOICE_SFX):
        s = sfx[sid]
        assert s.flags == 0 and s.freq == RATE, f"SFX {sid:#x} is not a 16 kHz 8-bit voice"
        rate, pcm = arcade[k]
        a, b = s.rom_range
        rom = np.frombuffer(m_rom[a:b], np.int8).astype(np.float64) * 256
        # the arcade conversion of bank entry k (trimmed to 0.8..1.15 of its length): its 10 ms
        # amplitude envelope follows the PS2 sample's (correlation 0.85..0.99 over the 24)
        ea, eb = _envelope(rom, RATE), _envelope(pcm.astype(np.float64), rate)
        n = min(len(ea), len(eb))
        assert np.corrcoef(ea[:n], eb[:n])[0, 1] > 0.75, f"SFX {sid:#x} is not voice {k} of {ARCADE_BANK}"
        gains.append(np.sqrt(np.mean(rom ** 2)) / max(1.0, np.sqrt(np.mean(pcm.astype(np.float64) ** 2))))
    gain = float(np.median(gains))
    out = []
    for k, sid in enumerate(VOICE_SFX):
        rate, pcm = arrange[k]
        res = loop_mod.resample(pcm.astype(np.float64), rate, RATE) * gain
        out.append(Voice(sid, ics2115.pcm8_encode(np.clip(res, -32768, 32767)).tobytes()))
    return out


def sfx_entry(old: bytes, start: int, end: int) -> bytes:
    """A 12-byte SFX table entry for an ICS sample [start, end) (one bank), the rest of `old` kept."""
    bank = start >> 20
    assert (end - 1) >> 20 == bank, "a sample may not cross a 1 MB bank"
    e = bytearray(old)
    e[0] = (old[0] & 0xF0) | (bank & 0xF)
    struct.pack_into("<II", e, 4, (start & 0xFFFFF) << 12, (end & 0xFFFFF) << 12)
    return bytes(e)
