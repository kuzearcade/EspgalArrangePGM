"""Arika "MWo3" code-overlay container used by the PS2 Espgaluda disc (A0A0/*)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"MWo3"
HEADER_SIZE = 0x40


@dataclass(frozen=True)
class OverlayHeader:
    module_id: int
    load_addr: int
    text_size: int
    data_size: int
    bss_size: int
    end_addr: int      # load_addr + text + data (two copies in the header)
    name: str          # e.g. "ov_arrange.bin"

    @property
    def text_end(self) -> int:
        return self.load_addr + self.text_size

    @property
    def data_start(self) -> int:
        return self.text_end

    @property
    def data_end(self) -> int:
        return self.text_end + self.data_size


def parse_header(data: bytes | memoryview) -> OverlayHeader:
    if bytes(data[:4]) != MAGIC:
        raise ValueError("not an MWo3 overlay")
    w = struct.unpack_from("<7I", data, 4)
    name_raw = bytes(data[0x20:0x40]).split(b"\0", 1)[0]
    return OverlayHeader(
        module_id=w[0], load_addr=w[1], text_size=w[2], data_size=w[3], bss_size=w[4],
        end_addr=w[5], name=name_raw.decode("ascii", "replace"),
    )


class Overlay:
    """An overlay file: header + image. The whole file (header included) is loaded at
    load_addr, so `image` (the bytes after the header) sits at image_base = load_addr+0x40."""

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.header = parse_header(raw)
        self.image = raw[HEADER_SIZE:]
        self.image_base = self.header.load_addr + HEADER_SIZE

    @property
    def name(self) -> str:
        return self.header.name

    @property
    def load_addr(self) -> int:
        return self.header.load_addr

    def contains(self, addr: int) -> bool:
        return self.image_base <= addr < self.image_base + len(self.image)

    def read(self, addr: int, size: int) -> bytes:
        o = addr - self.image_base
        return self.image[o:o + size]

    def offset_of(self, addr: int) -> int:
        return addr - self.image_base


OVERLAY_FILES = [f"A0A0/A0{n:02X}" for n in range(0xA0, 0xB1)]


def load_all(disc: Path) -> dict[str, Overlay]:
    out: dict[str, Overlay] = {}
    for rel in OVERLAY_FILES:
        p = disc / rel
        if p.exists():
            ov = Overlay(p)
            out[ov.name] = ov
    return out
