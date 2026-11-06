"""Build orchestration: inputs -> work/ -> out/ (ROM images, zips, manifest, budget report).

Stages are deliberately explicit so each can be run/tested alone:
  1. ingest      verify + extract inputs (inputs.prepare)
  2. analyse     locate tables/overlays (cached in work/analysis.json)
  3. data        apply Arrange data transplants to the P ROM (diff/datablock)
  4. code        assemble new 68k code and hook it in (asm/m68k)
  5. gfx         encode new sprites/tiles into A/B/T free space
  6. music       stream Arrange BGM into the M ROM + new Z80 driver
  7. emit        write ROMs, zips, manifest.json, budget.md
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import inputs as inputs_mod
from .pgm import rom as rom_mod


@dataclass
class BuildState:
    work: Path
    out: Path
    p_rom: bytearray                       # 68k big-endian view
    t_rom: bytearray
    a_rom: bytearray                       # a04801 + a04802 concatenated (16 MB)
    b_rom: bytearray
    m_rom: bytearray                       # cart sample window (grows up to 12 MB)
    log: list[str] = field(default_factory=list)
    budget: dict[str, dict] = field(default_factory=dict)
    p_free: int = 0x200000                 # next free byte in the program ROM (grows upward)
    p_allocs: list[tuple[int, int, str]] = field(default_factory=list)   # (start, size, note)
    sprite_sites: list[int] = field(default_factory=list)   # added code: sprite-code longs (gfx stage)

    def alloc_p(self, size: int, align: int = 0x10, note: str = "") -> int:
        """Reserve `size` bytes of free program ROM; returns the 68k address."""
        start = (self.p_free + align - 1) & ~(align - 1)
        if start + size > rom_mod.P_SIZE:
            raise MemoryError(f"program ROM full ({note})")
        self.p_free = start + size
        self.p_allocs.append((start, size, note))
        self.budget.setdefault("p_rom", {}).setdefault("added", 0)
        self.budget["p_rom"]["added"] += size
        return start

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print(msg)


def load_state(work: Path, out: Path) -> BuildState:
    rs = rom_mod.RomSet(work)
    return BuildState(
        work=work, out=out,
        p_rom=bytearray(rs.read("P")),
        t_rom=bytearray(rs.read("T")),
        a_rom=bytearray(rs.read("A0") + rs.read("A1")),
        b_rom=bytearray(rs.read("B")),
        m_rom=bytearray(rs.read("M")),
    )


OUTPUT_FILES = {
    # name in the drop-in zip -> (attribute, slice)
    "espgaluda_u8.bin": ("p_rom", None),
    "cave_t04801w064.u19": ("t_rom", None),
    "cave_a04801w064.u7": ("a_rom", (0, rom_mod.A_SIZE)),
    "cave_a04802w064.u8": ("a_rom", (rom_mod.A_SIZE, 2 * rom_mod.A_SIZE)),
    "cave_b04801w064.u1": ("b_rom", None),
    "cave_w04801b032.u17": ("m_rom", None),
}


def emit(state: BuildState, extra_files: dict[str, bytes]) -> dict:
    """Write ROM images + zips + manifest. P ROM is written byte-swapped (file order)."""
    from .stages import layout
    state.note(f"walker variables: {layout.check_walker_writes(bytes(state.p_rom), state.p_allocs)} "
               "writes in added code, all as Arrange's")
    state.out.mkdir(parents=True, exist_ok=True)
    roms = state.out / "roms"
    roms.mkdir(exist_ok=True)
    files: dict[str, bytes] = {}
    for name, (attr, sl) in OUTPUT_FILES.items():
        data = bytes(getattr(state, attr))
        if sl:
            data = data[sl[0]:sl[1]]
        if attr == "p_rom":
            data = rom_mod.swap16(data)
        files[name] = data
    # the DDP2 cart's ARM ROMs belong to the espgalbl-shaped drop-in only (stock MAME's espgalbl
    # driver loads them); the espgalarr set (the patched MAME machine, the MiSTer MRA) has no ARM
    arm = {k: v for k, v in extra_files.items() if k in ARM_FILES}
    extra_files = {k: v for k, v in extra_files.items() if k not in ARM_FILES}
    files.update(extra_files)
    bgm_blob = getattr(state, "bgm_blob", None)
    if bgm_blob:
        files["espgalarr_bgm.bin"] = bgm_blob      # (espgalarr zips only: enhanced hardware)
    manifest = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": {}}
    for name, data in files.items():
        (roms / name).write_bytes(data)
        manifest["files"][name] = {"size": len(data), "sha1": hashlib.sha1(data).hexdigest(),
                                   "crc32": f"{__import__('zlib').crc32(data) & 0xffffffff:08x}"}
    # drop-in: espgalbl-shaped (program + donor ARM leftovers + BIOS) and espgal-shaped (gfx/samples)
    bl = {k: files[k] for k in ("espgaluda_u8.bin",)}
    bl.update(extra_files)
    bl.update(arm)
    # MAME's espgalbl definition names the graphics/sample ROMs without the "cave_" prefix;
    # since our hashes differ from the parent, ship them under those names too.
    for name, data in files.items():
        if name.startswith("cave_"):
            bl[name[len("cave_"):]] = data
    rom_mod.write_set_zip(state.out / "dropin" / "espgalbl.zip", bl)
    bios = state.work / "pgm" / "pgm"
    if bios.exists():
        rom_mod.write_set_zip(state.out / "dropin" / "pgm.zip", {p.name: p.read_bytes() for p in bios.iterdir()})
    parent = {k: v for k, v in files.items() if k not in ("espgaluda_u8.bin", "espgalarr_bgm.bin") and k not in extra_files}
    rom_mod.write_set_zip(state.out / "dropin" / "espgal.zip", parent)
    arr = dict(files)
    for name, data in files.items():          # both naming conventions (espgal vs espgalbl style)
        if name.startswith("cave_"):
            arr[name[len("cave_"):]] = data
    rom_mod.write_set_zip(state.out / "espgalarr.zip", arr)
    rom_mod.write_set_zip(state.out / "dropin" / "espgalarr.zip", arr)   # for the patched MAME
    (state.out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (state.out / "build.log").write_text("\n".join(state.log) + "\n")
    return manifest


def write_budget(state: BuildState, release: bool = True) -> None:
    lines = ["# ROM budget (generated by espgalarr build)", "",
             "| ROM | size | cap | used by Arrange additions |", "|---|---|---|---|"]
    caps = {"p_rom": rom_mod.P_SIZE, "t_rom": rom_mod.T_CART_SIZE, "a_rom": 2 * rom_mod.A_SIZE,
            "b_rom": rom_mod.B_SIZE, "m_rom": rom_mod.M_CART_MAX_SIZE}
    for key, cap in caps.items():
        size = len(getattr(state, key))
        used = state.budget.get(key, {}).get("added", 0)
        lines.append(f"| {key} | {size/1048576:.2f} MB | {cap/1048576:.0f} MB | {used/1024:.0f} KB |")
    (state.out / "budget.md").write_text("\n".join(lines) + "\n")
    # the added program-ROM blocks (for profiles and traces: tools/prof68k.py)
    (state.out / "p_alloc.txt").write_text("".join(f"{a:06x} {n:06x} {note}\n" for a, n, note in state.p_allocs))
    if release and Path("spec").exists():     # the repo copy only follows release builds
        (Path("spec") / "budget.md").write_text("\n".join(lines) + "\n")


# the DDP2 cart's IGS027A ROMs: unused by the program (espgalbl's protection is simulated in 68k
# code); only the espgalbl-shaped drop-in carries them, for stock MAME's espgalbl driver
ARM_FILES = ("ddp2_igs027a_japan.bin", "v100.u23")


def donor_files(work: Path) -> dict[str, bytes]:
    """Files carried over unchanged from the espgalbl donor set (BIOS, and the ARM ROMs for the
    espgalbl-shaped drop-in when the donor set has them)."""
    d = work / "pgm" / "espgalbl"
    out = {n: (d / n).read_bytes() for n in ("pgm_m01s.rom", "pgm_p01s.u20", "pgm_p02s.u20")}
    out.update({n: (d / n).read_bytes() for n in ARM_FILES if (d / n).exists()})
    t01 = work / "pgm" / "pgm" / "pgm_t01s.rom"
    if t01.exists():
        out["pgm_t01s.rom"] = t01.read_bytes()
    return out


def run_stages(state: BuildState, stages: list[str]) -> None:
    """Run the transformation stages in order. Each stage module exposes run(state)."""
    import importlib
    for name in stages:
        mod = importlib.import_module(f"espgalarr.stages.{name}")
        t = time.time()
        mod.run(state)
        state.note(f"stage {name}: {time.time() - t:.1f}s")
