"""PS2 disc access: ISO 9660 extraction (no external tools), post-extraction file access, CD.LST."""
from __future__ import annotations

import struct
from pathlib import Path

SECTOR = 2048

ELF_NAME = "SLPS_253.52"


def cd_list(disc: Path) -> list[str]:
    """Return the disc's load list (CD.LST) as relative POSIX paths."""
    txt = (disc / "CD.LST").read_text(errors="replace")
    out = []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        # entries look like  A000\A001.;1
        line = line.split(";")[0].rstrip(".")
        out.append(line.replace("\\", "/"))
    return out


def disc_file(disc: Path, rel: str) -> Path:
    p = disc / rel
    if not p.exists():
        raise FileNotFoundError(p)
    return p


def elf_path(disc: Path) -> Path:
    return disc_file(disc, ELF_NAME)


def _records(f, lba: int, size: int):
    """The directory records of the directory at sector `lba` (`size` bytes): (name, lba, size, is_dir)."""
    f.seek(lba * SECTOR)
    data = f.read(size)
    pos = 0
    while pos < len(data):
        n = data[pos]
        if n == 0:                                   # records never cross a sector: skip to the next
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        rec = data[pos:pos + n]
        ext, length = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
        name = rec[33:33 + rec[32]]
        if name not in (b"\x00", b"\x01"):            # "." and ".."
            yield name.decode("ascii").split(";")[0].rstrip("."), ext, length, bool(rec[25] & 2)
        pos += n


def extract(iso: Path, dest: Path, skip_prefix: str = "DUMMY") -> int:
    """Extract every file of an ISO 9660 image into `dest` (directory structure kept), leaving out
    files whose name starts with `skip_prefix` (the PS2 disc's padding files). Returns the file count."""
    count = 0
    with open(iso, "rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        if pvd[1:6] != b"CD001":
            raise ValueError(f"{iso}: not an ISO 9660 image")
        root = pvd[156:156 + 34]
        todo = [(dest, struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0])]
        while todo:
            d, lba, size = todo.pop()
            d.mkdir(parents=True, exist_ok=True)
            for name, ext, length, is_dir in _records(f, lba, size):
                if is_dir:
                    todo.append((d / name, ext, length))
                    continue
                if skip_prefix and name.upper().startswith(skip_prefix):
                    continue
                out = d / name
                if out.exists() and out.stat().st_size == length:
                    count += 1
                    continue
                f.seek(ext * SECTOR)
                with open(out, "wb") as o:
                    left = length
                    while left:
                        chunk = f.read(min(left, 1 << 22))
                        o.write(chunk)
                        left -= len(chunk)
                count += 1
    return count
