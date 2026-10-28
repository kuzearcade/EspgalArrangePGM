"""PGM cartridge ROM images: layout, byte order, free-space scanning, set I/O.

Addresses follow MAME's pgm.cpp regions for the espgal/espgalbl sets.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

# Region layout (MAME) ------------------------------------------------------
P_SIZE = 0x400000                 # espgaluda_u8.bin, 68k view 0x000000-0x3FFFFF
T_BIOS_SIZE = 0x200000            # pgm_t01s.rom at igs023 0x000000
T_CART_BASE = 0x180000            # cave_t04801w064.u19 loads here
T_CART_SIZE = 0x800000
A_SIZE = 0x800000                 # each of cave_a04801/a04802
B_SIZE = 0x800000
M_BIOS_SIZE = 0x200000            # pgm_m01s.rom at ics 0x000000 (motherboard)
M_CART_BASE = 0x400000            # cave_w04801b032.u17 loads here
M_CART_MAX_END = 0x1000000        # ICS2115 24-bit address space
M_CART_MAX_SIZE = M_CART_MAX_END - M_CART_BASE   # 12 MB window
ICS_BANK = 0x100000               # a sample may not cross a 1 MB bank

ESPGAL_FILES = {
    "T": "cave_t04801w064.u19",
    "A0": "cave_a04801w064.u7",
    "A1": "cave_a04802w064.u8",
    "B": "cave_b04801w064.u1",
    "M": "cave_w04801b032.u17",
}
ESPGALBL_FILES = {
    "P": "espgaluda_u8.bin",
    "ARM": "ddp2_igs027a_japan.bin",
    "ARMDATA": "v100.u23",
}


def swap16(data: bytes) -> bytes:
    """Swap byte pairs (ROM_LOAD16_WORD_SWAP <-> 68k big-endian view)."""
    b = bytearray(data)
    b[0::2], b[1::2] = data[1::2], data[0::2]
    return bytes(b)


@dataclass(frozen=True)
class FreeRun:
    start: int
    end: int
    fill: int

    @property
    def size(self) -> int:
        return self.end - self.start


def free_runs(data: bytes, block: int = 0x1000, min_size: int = 0x10000) -> list[FreeRun]:
    """Runs of blocks entirely 0x00 or entirely 0xFF (candidate free space)."""
    runs: list[FreeRun] = []
    cur_start = None
    cur_fill = None
    zero = bytes(block)
    ones = b"\xff" * block
    for off in range(0, len(data), block):
        blk = data[off:off + block]
        fill = 0 if blk == zero[:len(blk)] else 0xFF if blk == ones[:len(blk)] else None
        if fill is not None and fill == cur_fill:
            continue
        if cur_start is not None and off - cur_start >= min_size:
            runs.append(FreeRun(cur_start, off, cur_fill))
        cur_start, cur_fill = (off, fill) if fill is not None else (None, None)
    if cur_start is not None and len(data) - cur_start >= min_size:
        runs.append(FreeRun(cur_start, len(data), cur_fill))
    return runs


class RomSet:
    """The espgal+espgalbl images as loaded from work/pgm/<set>/."""

    def __init__(self, work: Path):
        self.work = work
        self.p_file = work / "pgm" / "espgalbl" / ESPGALBL_FILES["P"]
        self.files = {k: work / "pgm" / "espgal" / v for k, v in ESPGAL_FILES.items()}

    def p_rom_68k(self) -> bytes:
        """Program ROM in 68k (big-endian) byte order."""
        return swap16(self.p_file.read_bytes())

    def read(self, key: str) -> bytes:
        if key == "P":
            return self.p_rom_68k()
        return self.files[key].read_bytes()


def write_set_zip(out_zip: Path, files: dict[str, bytes]) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            z.writestr(name, data)
