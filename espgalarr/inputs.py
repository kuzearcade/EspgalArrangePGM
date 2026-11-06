"""Locate, hash and extract the original inputs (PS2 disc + MAME ROM sets).

Nothing here embeds original data: only file names, sizes and SHA1 digests (the
same identifiers MAME publishes) so the tool can refuse unknown inputs.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# SHA1s as published in MAME src/mame/igs/pgm.cpp (espgal, espgalbl, pgm BIOS).
ROM_SETS: dict[str, dict[str, tuple[int, str]]] = {
    "espgal": {
        "espgaluda_v100.u38": (0x200000, "bce2e7fb9105ed51603d09cbd3a9eeb5b8f47ee2"),
        "t01s.u18": (0x200000, "cc567f577bfbf45427b54d6695b11b74f2578af3"),
        "cave_t04801w064.u19": (0x800000, "fbc340dafb18aa3094de29b881318a5a9794e4bc"),
        "cave_a04801w064.u7": (0x800000, "9bbabb5a53cb5ba88397cc2c258980f3b70314ce"),
        "cave_a04802w064.u8": (0x800000, "a7541e2b5a0df2bc62a5b347e54dbc2ed1922db2"),
        "cave_b04801w064.u1": (0x800000, "61d48b7117459f7babc022b68231f6928177a71d"),
        "cave_w04801b032.u17": (0x400000, "6b7333f16cce778c5725dbdf75a5446f0906397a"),
    },
    "espgalbl": {
        "espgaluda_u8.bin": (0x400000, "d4b694c88deaeebb7b1c0ddbf29a06380b06426f"),
        "ddp2_igs027a_japan.bin": (0x4000, "4491c08f3cefef2933ad5a741f4bb05cc2f3e1a0"),
        "v100.u23": (0x20000, "20c9479f158467fc2037dcf162b6c6be18c91d46"),
        "pgm_m01s.rom": (0x200000, "d3ed3ff3464557fd0df6b069b2e431528b0ebfa8"),
        "pgm_p01s.u20": (0x20000, "2a9df9ec746b14b74fae48b1a438da14973702ea"),
        "pgm_p02s.u20": (0x20000, "885a6558e022602cc6f482ac9667ba9f61e75092"),
    },
    "pgm": {
        "pgm_m01s.rom": (0x200000, "d3ed3ff3464557fd0df6b069b2e431528b0ebfa8"),
        "pgm_p01s.u20": (0x20000, "2a9df9ec746b14b74fae48b1a438da14973702ea"),
        "pgm_p02s.u20": (0x20000, "885a6558e022602cc6f482ac9667ba9f61e75092"),
        "pgm_t01s.rom": (0x200000, "cc567f577bfbf45427b54d6695b11b74f2578af3"),
    },
}

# The PS2 disc. ELF name and volume id are public identifiers.
PS2_ELF = "SLPS_253.52"
PS2_ISO_SIZE = 4073586688


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass
class Inputs:
    roms_dir: Path
    iso: Path | None
    work: Path
    problems: list[str] = field(default_factory=list)

    def rom_zip(self, setname: str) -> Path:
        return self.roms_dir / f"{setname}.zip"

    @property
    def disc_dir(self) -> Path:
        return self.work / "ps2" / "disc"

    def rom_path(self, setname: str, filename: str) -> Path:
        return self.work / "pgm" / setname / filename


# Files a set may lack: espgalbl's DDP2 cart ARM ROMs. The program never uses the IGS027A; they
# only go into the espgalbl-shaped drop-in for stock MAME's espgalbl driver (checked when present).
OPTIONAL_FILES = {("espgalbl", "ddp2_igs027a_japan.bin"), ("espgalbl", "v100.u23")}


def verify_rom_zip(zpath: Path, setname: str, problems: list[str]) -> None:
    expected = ROM_SETS[setname]
    if not zpath.exists():
        problems.append(f"missing {zpath}")
        return
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        for fn, (size, sha) in expected.items():
            if fn not in names:
                if (setname, fn) not in OPTIONAL_FILES:
                    problems.append(f"{zpath.name}: missing {fn}")
                continue
            data = z.read(fn)
            if len(data) != size:
                problems.append(f"{zpath.name}:{fn}: size {len(data):#x} != {size:#x}")
            if hashlib.sha1(data).hexdigest() != sha:
                problems.append(f"{zpath.name}:{fn}: SHA1 mismatch")


def extract_rom_zip(zpath: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        for fn in z.namelist():
            out = dest / Path(fn).name
            if not out.exists() or out.stat().st_size != z.getinfo(fn).file_size:
                out.write_bytes(z.read(fn))


def find_iso(ps2_dir: Path) -> Path | None:
    for p in sorted(ps2_dir.glob("*")):
        if p.suffix.lower() in (".iso", ".7z", ".zip"):
            return p
    return None


def extract_iso(iso_or_archive: Path, work: Path, keep_iso: bool = True) -> Path:
    """Extract the PS2 disc into work/ps2/disc using the 7z CLI. Returns the disc dir.

    Accepts a raw .iso or a .7z/.zip archive containing one. DUMMY*.BIN padding
    files are skipped. Idempotent: skips work already present.
    """
    ps2 = work / "ps2"
    ps2.mkdir(parents=True, exist_ok=True)
    disc = ps2 / "disc"
    if (disc / PS2_ELF).exists():
        return disc
    iso = iso_or_archive
    if iso.suffix.lower() != ".iso":
        subprocess.run(["7z", "x", "-y", f"-o{ps2}", str(iso), "-x!readme.html", "-x!*.txt"],
                       check=True, stdout=subprocess.DEVNULL)
        isos = list(ps2.glob("*.iso"))
        if not isos:
            raise RuntimeError(f"no .iso inside {iso}")
        iso = isos[0]
    disc.mkdir(exist_ok=True)
    subprocess.run(["7z", "x", "-y", f"-o{disc}", str(iso), "-x!DUMMY*"],
                   check=True, stdout=subprocess.DEVNULL)
    if not keep_iso and iso != iso_or_archive:
        iso.unlink()
    return disc


def prepare(roms_dir: Path, ps2_dir: Path, work: Path, strict: bool = True) -> Inputs:
    """Verify + extract everything into work/. Raises on hash problems when strict."""
    inp = Inputs(roms_dir=roms_dir, iso=find_iso(ps2_dir), work=work)
    for setname in ROM_SETS:
        verify_rom_zip(inp.rom_zip(setname), setname, inp.problems)
    if inp.iso is None:
        inp.problems.append(f"no PS2 disc image found in {ps2_dir}")
    if strict and inp.problems:
        raise SystemExit("input verification failed:\n  " + "\n  ".join(inp.problems))
    for setname in ROM_SETS:
        if inp.rom_zip(setname).exists():
            extract_rom_zip(inp.rom_zip(setname), work / "pgm" / setname)
    if inp.iso is not None:
        extract_iso(inp.iso, work)
    return inp
