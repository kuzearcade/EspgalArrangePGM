"""ICS2115 sample encoding helpers: 8-bit u-law (MIL-STD-188-113 variant used by the
chip, mirroring MAME's decode table), 1 MB bank packing, and sample-table records."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rom import ICS_BANK, M_CART_BASE, M_CART_MAX_END


def ulaw_decode_table() -> np.ndarray:
    """256-entry int16 table: byte -> sample, as the ICS2115 (and MAME) decode it."""
    lut_initial = 33 << 2
    lut = [(lut_initial << i) - lut_initial for i in range(8)]
    out = np.zeros(256, dtype=np.int32)
    for i in range(256):
        exponent = ((~i) >> 4) & 7
        mantissa = (~i) & 0xF
        value = lut[exponent] + (mantissa << (exponent + 3))
        out[i] = -value if (i & 0x80) else value
    return out.astype(np.int16)


_DEC = ulaw_decode_table()
# Build an encoder by nearest-neighbour search over the positive half (symmetric code).
_POS_CODES = np.array([i for i in range(256) if not (i & 0x80)])
_POS_VALS = _DEC[_POS_CODES].astype(np.int32)
_ORDER = np.argsort(_POS_VALS)
_POS_VALS_SORTED = _POS_VALS[_ORDER]
_POS_CODES_SORTED = _POS_CODES[_ORDER]


def ulaw_encode(pcm: np.ndarray) -> np.ndarray:
    """int16 mono samples -> uint8 u-law bytes (nearest representable value)."""
    x = pcm.astype(np.int32)
    mag = np.abs(x)
    idx = np.searchsorted(_POS_VALS_SORTED, mag)
    idx = np.clip(idx, 1, len(_POS_VALS_SORTED) - 1)
    lo = _POS_VALS_SORTED[idx - 1]
    hi = _POS_VALS_SORTED[idx]
    pick = np.where((mag - lo) <= (hi - mag), idx - 1, idx)
    codes = _POS_CODES_SORTED[pick].astype(np.uint8)
    codes = np.where(x < 0, codes | 0x80, codes).astype(np.uint8)
    return codes


def ulaw_decode(codes: np.ndarray) -> np.ndarray:
    return _DEC[codes.astype(np.uint8)]


def pcm8_encode(pcm: np.ndarray) -> np.ndarray:
    """int16 -> signed 8-bit linear (for comparison / SFX)."""
    return (np.clip(pcm.astype(np.int32), -32768, 32767) >> 8).astype(np.int8).view(np.uint8)


@dataclass
class Placement:
    name: str
    offset: int      # ICS address (0x400000-based cart window)
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


class BankPacker:
    """First-fit packer that never lets a chunk cross a 1 MB ICS bank boundary."""

    def __init__(self, start: int = M_CART_BASE, end: int = M_CART_MAX_END, reserved: list[tuple[int, int]] | None = None):
        self.start, self.end = start, end
        self.used: list[tuple[int, int]] = sorted(reserved or [])
        self.placed: list[Placement] = []

    def _free(self, a: int, b: int) -> bool:
        return all(b <= u0 or a >= u1 for u0, u1 in self.used)

    def place(self, name: str, size: int, align: int = 16) -> Placement:
        if size > ICS_BANK:
            raise ValueError(f"{name}: {size:#x} exceeds one bank; split it first")
        pos = self.start
        while pos + size <= self.end:
            pos = (pos + align - 1) & ~(align - 1)
            bank_end = (pos // ICS_BANK + 1) * ICS_BANK
            if pos + size > bank_end:
                pos = bank_end
                continue
            if self._free(pos, pos + size):
                p = Placement(name, pos, size)
                self.used.append((pos, pos + size))
                self.used.sort()
                self.placed.append(p)
                return p
            # jump past the blocking allocation
            for u0, u1 in self.used:
                if u0 < pos + size and u1 > pos:
                    pos = u1
                    break
        raise MemoryError(f"no room for {name} ({size:#x} bytes)")

    def total_end(self) -> int:
        return max((u1 for _, u1 in self.used), default=self.start)


def split_banks(size: int, limit: int = ICS_BANK - 16) -> list[int]:
    """Chunk sizes for a stream of `size` bytes so each chunk fits a bank."""
    out = []
    while size > 0:
        n = min(size, limit)
        out.append(n)
        size -= n
    return out
