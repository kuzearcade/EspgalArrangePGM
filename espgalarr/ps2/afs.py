"""CRI AFS archive index reader (the container used for ADX streams)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"AFS\0"


@dataclass(frozen=True)
class AfsEntry:
    index: int
    offset: int
    size: int


def read_index(data: bytes | memoryview) -> list[AfsEntry]:
    if bytes(data[:4]) != MAGIC:
        raise ValueError("not an AFS archive")
    n = struct.unpack_from("<I", data, 4)[0]
    out = []
    for i in range(n):
        off, size = struct.unpack_from("<II", data, 8 + 8 * i)
        out.append(AfsEntry(i, off, size))
    return out


class Afs:
    def __init__(self, path: Path):
        self.path = path
        self._f = open(path, "rb")
        head = self._f.read(8)
        n = struct.unpack_from("<I", head, 4)[0]
        self._f.seek(0)
        self.entries = read_index(self._f.read(8 + 8 * n))

    def __len__(self) -> int:
        return len(self.entries)

    def read(self, index: int) -> bytes:
        e = self.entries[index]
        self._f.seek(e.offset)
        return self._f.read(e.size)

    def close(self) -> None:
        self._f.close()
