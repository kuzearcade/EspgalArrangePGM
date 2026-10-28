"""IGS023 graphics formats (as rendered by the PGM video chip; see MAME igs023_video.cpp).

Sprites: the B ROM holds, per sprite, a 32-bit little-endian word giving the A-ROM start
(value >> 2 = index of the first 16-bit A word), followed by `width*16/16 = width` mask
words per line (bit set = transparent). Non-transparent pixels are stored consecutively in
the A ROM as 5-bit values, three per 16-bit word (15 bits used), in mask order.
Tiles: text layer = 8x8, 4 bpp packed LSB-first; background = 32x32, 5 bpp, bit-reversed
planes (MAME `pgm32_charlayout` with GFXDECODE_DEVICE_REVERSEBITS).
Palette: 15-bit xRGB? (MAME uses xRRRRRGGGGGBBBBB -> see pgm.cpp palette_device), 32 colours
per sprite palette.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Sprite:
    width_words: int          # width in 16-pixel units
    height: int               # in pixels
    pixels: np.ndarray        # (height, width_words*16) uint8, 0..31; 255 = transparent


def _u16le(data: bytes | memoryview, word_index: int) -> int:
    o = word_index * 2
    return data[o] | (data[o + 1] << 8)


def decode_sprite(a_rom: bytes, b_rom: bytes, b_offset_words: int, width_words: int, height: int) -> Sprite:
    """Decode one sprite given its B-ROM offset (in 16-bit words, as in the sprite list)."""
    a_off = ((_u16le(b_rom, b_offset_words + 1) << 16) | _u16le(b_rom, b_offset_words)) >> 2
    abit = 0
    bpos = b_offset_words + 2
    w = width_words * 16
    out = np.full((height, w), 255, dtype=np.uint8)
    for y in range(height):
        for xw in range(width_words):
            msk = _u16le(b_rom, bpos)
            bpos += 1
            for x in range(16):
                if not (msk & 1):
                    word = _u16le(a_rom, a_off)
                    out[y, xw * 16 + x] = (word >> abit) & 0x1F
                    abit += 5
                    if abit >= 15:
                        abit = 0
                        a_off += 1
                msk >>= 1
    return Sprite(width_words, height, out)


def encode_sprite(pixels: np.ndarray) -> tuple[bytes, bytes]:
    """Inverse of decode_sprite: returns (a_words_bytes, b_mask_bytes_without_header).
    `pixels` is (h, w) with w multiple of 16; 255 = transparent, else 0..31.
    The 4-byte A-offset header is written by the packer once the A position is known."""
    h, w = pixels.shape
    assert w % 16 == 0
    masks = bytearray()
    avals: list[int] = []
    for y in range(h):
        for xw in range(w // 16):
            m = 0
            for x in range(16):
                v = int(pixels[y, xw * 16 + x])
                if v == 255:
                    m |= 1 << x
                else:
                    avals.append(v & 0x1F)
            masks += bytes((m & 0xFF, m >> 8))
    # pack 3 x 5-bit per word
    awords = bytearray()
    for i in range(0, len(avals), 3):
        chunk = avals[i:i + 3]
        word = 0
        for j, v in enumerate(chunk):
            word |= v << (5 * j)
        awords += bytes((word & 0xFF, word >> 8))
    return bytes(awords), bytes(masks)


def b_header(a_word_index: int) -> bytes:
    v = a_word_index << 2
    return bytes((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF))


# --- tiles -------------------------------------------------------------------

def decode_tx_tiles(t_rom: bytes, start_byte: int, count: int) -> np.ndarray:
    """8x8x4 packed LSB-first tiles -> (count, 8, 8) uint8."""
    out = np.zeros((count, 8, 8), dtype=np.uint8)
    for n in range(count):
        base = start_byte + n * 32
        for y in range(8):
            for x in range(8):
                b = t_rom[base + y * 4 + x // 2]
                out[n, y, x] = (b & 0xF) if (x % 2 == 0) else (b >> 4)
    return out


def decode_bg_tiles(t_rom: bytes, start_byte: int, count: int) -> np.ndarray:
    """32x32x5 tiles, 5 bits per pixel consecutive, bit-reversed within each byte."""
    out = np.zeros((count, 32, 32), dtype=np.uint8)
    rev = np.array([int(f"{i:08b}"[::-1], 2) for i in range(256)], dtype=np.uint8)
    for n in range(count):
        base = start_byte + n * (32 * 32 * 5 // 8)
        data = rev[np.frombuffer(t_rom, dtype=np.uint8, count=32 * 32 * 5 // 8, offset=base)]
        bits = np.unpackbits(data)  # MSB first after reversal == LSB-first original
        px = bits.reshape(-1, 5)
        vals = (px[:, 0] << 4) | (px[:, 1] << 3) | (px[:, 2] << 2) | (px[:, 3] << 1) | px[:, 4]
        out[n] = vals.reshape(32, 32)
    return out


# --- palette ------------------------------------------------------------------

def rgb15_to_rgb888(v: int) -> tuple[int, int, int]:
    r = (v >> 10) & 0x1F
    g = (v >> 5) & 0x1F
    b = v & 0x1F
    return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2)


def rgb888_to_rgb15(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)


def palette_from_rom(p_rom: bytes, offset: int, n: int = 32) -> list[tuple[int, int, int]]:
    out = []
    for i in range(n):
        v = (p_rom[offset + 2 * i] << 8) | p_rom[offset + 2 * i + 1]
        out.append(rgb15_to_rgb888(v))
    return out
