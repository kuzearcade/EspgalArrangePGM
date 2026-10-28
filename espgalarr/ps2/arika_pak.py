"""Arika's obfuscated data files on the PS2 Espgaluda disc (A020/A020..A033, A000/A00E).

Reverse-engineered from SLPS_253.52 (see work/ps2/notes/loader.md and
spec/ps2-formats.md).  The on-disc bytes are an obfuscated image of the data
the game wants in RAM: the CD request queue reads the file verbatim and the
completion callback runs ``unscramble(buf, size)`` (ELF 0x11dbf0) in place.

Scheme (ELF 0x11dbf0)::

    state = (size + 0x012D86A1) & 0xffffffff        # size = exact file length
    out[0] = in[0]
    for i in 1 .. size-1:
        out[i] = (in[i] - in[i-1] - ((state >> 8) & 0xff)) & 0xff
        state  = (state * 17 + 1) & 0xffffffff

i.e. a byte delta against the *scrambled* predecessor plus a keystream from a
32-bit LCG.  The only key material is the file length.

After unscrambling, the plaintext depends on the file number:

* A020, A02B      PGM-style sprite bank: 0x4061 little-endian u32 offsets
                  (0 = no sprite, bit 31 = LZSS-compressed), then 8bpp
                  5-bit-index pixel data (0x1F = transparent).
* A021..A02A      stage data blobs for stages 1..n (A021 is stage 1); layout
                  owned by the stage code; A021 ends with a TIM2 picture.
* A02C..A032      PGM-style background tiles: 32x32 pixels, 5 bpp packed
                  (640 bytes per tile, 5-bit values LSB-first, little-endian).
* A033            8x8 4bpp text-layer tiles (32 bytes each).
* A000/A00E       one TIM2 picture (224x448 PSMT8), unscrambled with the same
                  scheme.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

KEY_SEED = 0x012D86A1
LCG_MUL = 17
LCG_INC = 1
MASK32 = 0xFFFFFFFF

SPRITE_TABLE_ENTRIES = 0x4061        # ELF 0x120a40: sprite number < 0x4061
SPRITE_TABLE_BYTES = 0x10400         # first sprite data in both banks
SPRITE_LZ_FLAG = 0x80000000
SPRITE_TRANSPARENT = 0x1F

BG_TILE_BYTES = 640                  # ELF 0x121c54: tile index * 640; 32*32*5/8
TEXT_TILE_BYTES = 32                 # ELF 0x123160: tile code << 5

TIM2_MAGIC = b"TIM2"

FILE_SPRITE_BANKS = (0x20, 0x2B)
FILE_STAGE_DATA = range(0x21, 0x2B)
FILE_BACKGROUNDS = range(0x2C, 0x33)
FILE_TEXT_TILES = 0x33
FILE_TIM2_ONLY = (0x0E,)


# ---------------------------------------------------------------- keystream

def keystream(n: int, size: int | None = None) -> np.ndarray:
    """Key bytes k[0..n-1]; byte i (i >= 1) of a file of `size` bytes uses k[i-1]."""
    if size is None:
        size = n
    if n <= 0:
        return np.zeros(0, dtype=np.uint8)
    block = 4096
    pows = np.empty(block, dtype=np.uint64)
    incs = np.empty(block, dtype=np.uint64)
    p, c = 1, 0
    for j in range(block):
        pows[j] = p
        incs[j] = c
        p = (p * LCG_MUL) & MASK32
        c = (c * LCG_MUL + LCG_INC) & MASK32
    p_block, c_block = p, c
    out = np.empty(n, dtype=np.uint32)
    state = (size + KEY_SEED) & MASK32
    for off in range(0, n, block):
        m = min(block, n - off)
        out[off:off + m] = ((pows[:m] * np.uint64(state) + incs[:m]) & np.uint64(MASK32)).astype(np.uint32)
        state = (p_block * state + c_block) & MASK32
    return ((out >> 8) & 0xFF).astype(np.uint8)


def decode_file(data: bytes, size: int | None = None) -> bytes:
    """Unscramble one on-disc file.  `size` defaults to len(data), which is what
    the game passes (the request queue always uses the exact file length)."""
    n = len(data)
    if n < 2:
        return bytes(data)
    if size is None:
        size = n
    enc = np.frombuffer(data, dtype=np.uint8)
    key = keystream(n - 1, size)
    out = enc.copy()
    out[1:] = enc[1:] - enc[:-1] - key
    return out.tobytes()


def encode_file(plain: bytes, size: int | None = None) -> bytes:
    """Inverse of decode_file (for rebuilding discs)."""
    n = len(plain)
    if n < 2:
        return bytes(plain)
    if size is None:
        size = n
    key = keystream(n - 1, size)
    src = np.frombuffer(plain, dtype=np.uint8)
    out = np.empty(n, dtype=np.uint8)
    out[0] = src[0]
    prev = int(src[0])
    # enc[i] = plain[i] + enc[i-1] + key[i-1]; sequential dependency -> plain loop
    s = src.tolist()
    k = key.tolist()
    o = out  # local alias
    for i in range(1, n):
        prev = (s[i] + prev + k[i - 1]) & 0xFF
        o[i] = prev
    return out.tobytes()


# ---------------------------------------------------------------- LZSS (sprites)

def lzss_size(src: bytes | memoryview, pos: int = 0) -> int:
    """Decompressed size prefix (ELF 0x124270)."""
    return struct.unpack_from("<I", src, pos)[0]


def lzss_decompress(src: bytes | memoryview, pos: int = 0) -> bytes:
    """ELF 0x1242a0.  Stream = u32 size, then blocks.  Block header byte c:
    c & 0x80 == 0 -> next c bytes are literals; otherwise bits 0..6 of c are
    7 token flags (LSB first): 0 = one literal byte, 1 = u16 LE back-reference
    with distance = (tok & 0x7FF) + 1 and length = ((tok >> 11) & 0x1F) + 2."""
    src = memoryview(src)
    size = lzss_size(src, pos)
    pos += 4
    out = bytearray()
    while len(out) < size:
        c = src[pos]
        pos += 1
        if not c & 0x80:
            out += src[pos:pos + c]
            pos += c
            continue
        for bit in range(7):
            if len(out) >= size:
                break
            if c & (1 << bit):
                tok = src[pos] | (src[pos + 1] << 8)
                pos += 2
                dist = (tok & 0x7FF) + 1
                length = ((tok >> 11) & 0x1F) + 2
                start = len(out) - dist
                for j in range(length):          # byte-wise forward copy (overlap = RLE)
                    out.append(out[start + j])
            else:
                out.append(src[pos])
                pos += 1
    return bytes(out[:size])


# ---------------------------------------------------------------- containers

@dataclass(frozen=True)
class Entry:
    index: int          # sprite number / picture index / row-block index
    offset: int         # byte offset in the decoded file
    size: int           # stored size in the file
    kind: str           # 'sprite', 'sprite-lz', 'tim2', 'tiles5', 'tiles4', 'raw'
    decoded_size: int = 0   # for 'sprite-lz': size after LZSS; else == size


def sprite_table(data: bytes) -> list[int]:
    return list(struct.unpack_from(f"<{SPRITE_TABLE_ENTRIES}I", data, 0))


def parse_sprite_bank(data: bytes) -> list[Entry]:
    """Entries of a sprite bank (A020 / A02B), in sprite-number order.  Stored
    size of raw sprites = distance to the next stored item (the bank does not
    record sprite dimensions; those come from the sprite list in the game)."""
    table = sprite_table(data)
    items = [(i, v & ~SPRITE_LZ_FLAG, bool(v & SPRITE_LZ_FLAG)) for i, v in enumerate(table) if v]
    # a bank may carry a trailing TIM2 picture that is not in the table (A02B)
    stops = sorted({o for _, o, _ in items} | {t.offset for t in find_tim2(data)} | {len(data)})
    by_off = sorted(o for _, o, _ in items)
    bounds = {o: next(s for s in stops if s > o) for o in by_off}
    out = []
    for i, off, lz in items:
        if lz:
            dec = lzss_size(data, off)
            out.append(Entry(i, off, bounds[off] - off, "sprite-lz", dec))
        else:
            out.append(Entry(i, off, bounds[off] - off, "sprite", bounds[off] - off))
    return out


def sprite_pixels(data: bytes, e: Entry) -> bytes:
    """8bpp pixel bytes (values 0..31, 0x1F transparent) of one sprite."""
    if e.kind == "sprite-lz":
        return lzss_decompress(data, e.offset)
    return bytes(data[e.offset:e.offset + e.size])


def find_tim2(data: bytes) -> list[Entry]:
    """TIM2 pictures embedded in a decoded blob (scan for the magic)."""
    out, pos = [], 0
    while True:
        pos = data.find(TIM2_MAGIC, pos)
        if pos < 0:
            return out
        version, fmt, npics = struct.unpack_from("<BBH", data, pos + 4)
        hdr = 0x80 if fmt == 1 else 0x10
        total, p = hdr, pos + hdr
        ok = True
        for _ in range(npics):
            if p + 0x30 > len(data):
                ok = False
                break
            tsize = struct.unpack_from("<I", data, p)[0]
            if tsize < 0x30:
                ok = False
                break
            total += tsize
            p += tsize
        if ok and npics:
            out.append(Entry(len(out), pos, total, "tim2", total))
            pos += total
        else:
            pos += 4


def tim2_pictures(data: bytes, pos: int) -> list[dict]:
    """Picture headers of the TIM2 at `pos` (fields per Sony's TIM2 spec)."""
    version, fmt, npics = struct.unpack_from("<BBH", data, pos + 4)
    p = pos + (0x80 if fmt == 1 else 0x10)
    pics = []
    for _ in range(npics):
        (total, clut_size, img_size, hdr_size, clut_colors, pict_fmt, mipmaps,
         clut_type, img_type, w, h) = struct.unpack_from("<IIIHHBBBBHH", data, p)
        pics.append(dict(offset=p, total=total, clut_size=clut_size, image_size=img_size,
                         header_size=hdr_size, clut_colors=clut_colors, pict_format=pict_fmt,
                         mipmaps=mipmaps, clut_type=clut_type, image_type=img_type,
                         width=w, height=h, image_offset=p + hdr_size,
                         clut_offset=p + hdr_size + img_size))
        p += total
    return pics


def parse(data: bytes, file_index: int) -> list[Entry]:
    """Describe a *decoded* file by its disc file number (0x20..0x33, 0x0E)."""
    if file_index in FILE_SPRITE_BANKS:
        return parse_sprite_bank(data) + find_tim2(data)
    if file_index in FILE_BACKGROUNDS:
        return [Entry(0, 0, len(data), "tiles5", len(data) // BG_TILE_BYTES)]
    if file_index == FILE_TEXT_TILES:
        return [Entry(0, 0, len(data), "tiles4", len(data) // TEXT_TILE_BYTES)]
    tims = find_tim2(data)
    if file_index in FILE_TIM2_ONLY and tims:
        return tims
    out: list[Entry] = []
    pos = 0
    for t in tims:
        if t.offset > pos:
            out.append(Entry(len(out), pos, t.offset - pos, "raw", t.offset - pos))
        out.append(Entry(len(out), t.offset, t.size, "tim2", t.size))
        pos = t.offset + t.size
    if pos < len(data):
        out.append(Entry(len(out), pos, len(data) - pos, "raw", len(data) - pos))
    return out


def bg_tiles(data: bytes, start: int = 0, count: int | None = None) -> np.ndarray:
    """(count, 32, 32) uint8 of 5-bit indices from a decoded A02C..A032 file.
    Each tile is 640 bytes: 32 rows of 20 bytes, 32 pixels of 5 bits packed
    LSB-first (pixel k of a row = bits 5k..5k+4 of the row read little-endian),
    which is how ELF 0x121c20 unpacks them from 16-bit words."""
    total = len(data) // BG_TILE_BYTES
    if count is None:
        count = total - start
    raw = np.frombuffer(data, dtype=np.uint8, count=count * BG_TILE_BYTES, offset=start * BG_TILE_BYTES)
    bits = np.unpackbits(raw.reshape(count * 32, 20), axis=1, bitorder="little")   # (rows, 160)
    px = bits.reshape(count * 32, 32, 5) << np.arange(5, dtype=np.uint8)
    return px.sum(axis=2, dtype=np.uint8).reshape(count, 32, 32)


def text_tile(data: bytes, code: int) -> np.ndarray:
    """8x8 array of 4-bit indices for text tile `code` (A033)."""
    raw = np.frombuffer(data, dtype=np.uint8, count=TEXT_TILE_BYTES, offset=code * TEXT_TILE_BYTES)
    px = np.empty(64, dtype=np.uint8)
    px[0::2] = raw & 0x0F
    px[1::2] = raw >> 4
    return px.reshape(8, 8)
