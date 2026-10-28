"""PS2 disc file access (post-extraction) and CD.LST parsing."""
from __future__ import annotations

from pathlib import Path

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
