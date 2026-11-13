"""ESP Ra.De. (Cave 1998, MAME set `esprade`) as an optional input: its program and sprite ROMs,
read from the user's esprade.zip for `build --esprade-chars` (espgalarr/esprade_chars.py).

Nothing here embeds original data: file names, sizes and SHA1 digests (as published in MAME's
src/mame/atlus/cave.cpp), and the layouts MAME documents:
  * program: u42.int (even bytes) + u41.int (odd bytes), 1 MB of 68000 code at 0;
  * sprites: esp_u63/u64 then esp_u65/u66, ROM_LOAD32_WORD_SWAP (the two chips of each pair
    alternate 16-bit words, each word byte-swapped), 8 bits per pixel (layout_sprites_8bpp:
    planes 0-3 and 16-19 of each 128-bit 16-pixel element, pixels in nibble pairs);
  * a sprite (sprite013, `code`) is w x h pixels from pixel offset code * 256, row by row;
    pixel 0 is transparent; its colour field picks a 256-colour block of the palette;
  * palette words are xGRB 555 (palette_device::xGRB_555).
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import numpy as np

SETNAME = "esprade"
FILES: dict[str, tuple[int, str]] = {
    "u42.int": (0x80000, "ab1666eb826cb4a71588d86831dd18a2ef1c2a33"),
    "u41.int": (0x80000, "37a56b7b9662219a356aee3f4b5cbb774ac4950e"),
    "esp_u63.u63": (0x400000, "9519e365248bcec8419786eabb16fe4aae299af5"),
    "esp_u64.u64": (0x400000, "53549a2bd3edc7b5e73fb46e1421b156bb0c190f"),
    "esp_u65.u65": (0x400000, "94e72da1f542b4e0525b4b43994242816b43dbdc"),
    "esp_u66.u66": (0x400000, "e77d0ed7a11b5abca1df8a0eb20ac9360cf79e76"),
}


class EspRade:
    """The verified ROMs of one esprade.zip."""

    def __init__(self, zip_path: Path):
        if not zip_path.exists():
            raise SystemExit(f"--esprade-chars needs {zip_path} (MAME set esprade)")
        self._rom: dict[str, bytes] = {}
        problems = []
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            for fn, (size, sha) in FILES.items():
                if fn not in names:
                    problems.append(f"missing {fn}")
                    continue
                d = z.read(fn)
                if len(d) != size or hashlib.sha1(d).hexdigest() != sha:
                    problems.append(f"{fn}: size or SHA1 differs from MAME's esprade")
                self._rom[fn] = d
        if problems:
            raise SystemExit(f"{zip_path}: " + "; ".join(problems))
        self._px: np.ndarray | None = None

    def program(self) -> bytes:
        e, o = self._rom["u42.int"], self._rom["u41.int"]
        out = bytearray(len(e) * 2)
        out[0::2], out[1::2] = e, o
        return bytes(out)

    def pixels(self) -> np.ndarray:
        """The sprite ROMs as one 8-bit pixel per byte (16M pixels)."""
        if self._px is None:
            reg = np.zeros(0x1000000, np.uint8)
            for base, a, b in ((0, "esp_u63.u63", "esp_u64.u64"), (0x800000, "esp_u65.u65", "esp_u66.u66")):
                for off, name in ((0, a), (2, b)):
                    words = np.frombuffer(self._rom[name], np.uint8).reshape(-1, 2)[:, ::-1]
                    reg[base:base + 0x800000].reshape(-1, 4)[:, off:off + 2] = words
            bits = np.unpackbits(reg.reshape(-1, 16), axis=1)          # 128 bits per element, MSB first
            planes = (0, 1, 2, 3, 16, 17, 18, 19)
            xoff = (12, 8, 4, 0, 44, 40, 36, 32, 76, 72, 68, 64, 108, 104, 100, 96)
            px = np.zeros((bits.shape[0], 16), np.uint8)
            for p, po in enumerate(planes):
                for x, xo in enumerate(xoff):
                    px[:, x] |= bits[:, po + xo] << (7 - p)
            self._px = px.reshape(-1)
        return self._px

    def sprite(self, code: int, w: int, h: int) -> np.ndarray:
        """(h, w) pixel values; 0 is transparent."""
        o = code << 8
        return self.pixels()[o:o + w * h].reshape(h, w)

    def palette(self, addr: int, n: int = 256) -> np.ndarray:
        """n palette words from the program ROM at addr, as (n, 3) RGB 0..31."""
        p = self.program()
        w = np.frombuffer(p[addr:addr + 2 * n], ">u2").astype(np.int32)
        return np.stack([(w >> 5) & 31, (w >> 10) & 31, w & 31], -1)
