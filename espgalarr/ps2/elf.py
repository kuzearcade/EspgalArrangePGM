"""PS2 ELF access + MIPS disassembly helpers (capstone)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

try:
    import capstone as cs
except ImportError:  # pragma: no cover
    cs = None


@dataclass(frozen=True)
class Segment:
    vaddr: int
    offset: int
    filesz: int
    memsz: int
    flags: int


class Elf:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        d = self.data
        if d[:4] != b"\x7fELF":
            raise ValueError("not an ELF")
        self.entry = struct.unpack_from("<I", d, 0x18)[0]
        phoff = struct.unpack_from("<I", d, 0x1c)[0]
        phentsize, phnum = struct.unpack_from("<HH", d, 0x2a)
        self.segments: list[Segment] = []
        for i in range(phnum):
            p_type, p_off, p_vaddr, _p_paddr, p_filesz, p_memsz, p_flags, _al = struct.unpack_from(
                "<8I", d, phoff + i * phentsize)
            if p_type == 1:
                self.segments.append(Segment(p_vaddr, p_off, p_filesz, p_memsz, p_flags))

    def read(self, vaddr: int, size: int) -> bytes:
        for s in self.segments:
            if s.vaddr <= vaddr < s.vaddr + s.filesz:
                o = s.offset + (vaddr - s.vaddr)
                return self.data[o:o + size]
        raise ValueError(f"vaddr {vaddr:#x} not in file")

    def contains(self, vaddr: int) -> bool:
        return any(s.vaddr <= vaddr < s.vaddr + s.memsz for s in self.segments)


class Memory:
    """A flat view of ELF + loaded overlays, for cross-module disassembly."""

    def __init__(self, elf: Elf, overlays: list | None = None):
        self.regions: list[tuple[int, bytes]] = [(s.vaddr, elf.read(s.vaddr, s.filesz)) for s in elf.segments]
        for ov in overlays or []:
            self.regions.append((ov.load_addr, ov.image))

    def read(self, addr: int, size: int) -> bytes:
        for base, img in self.regions:
            if base <= addr < base + len(img):
                return img[addr - base: addr - base + size]
        raise ValueError(f"addr {addr:#x} unmapped")

    def u32(self, addr: int) -> int:
        return struct.unpack("<I", self.read(addr, 4))[0]

    def contains(self, addr: int) -> bool:
        return any(base <= addr < base + len(img) for base, img in self.regions)


def mips_disassembler():
    if cs is None:
        raise RuntimeError("capstone not installed")
    md = cs.Cs(cs.CS_ARCH_MIPS, cs.CS_MODE_MIPS64 | cs.CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    md.skipdata = True
    return md


def disassemble(code: bytes, addr: int):
    """Yield (address, bytes, mnemonic, op_str)."""
    md = mips_disassembler()
    for insn in md.disasm(code, addr):
        yield insn.address, insn.bytes, insn.mnemonic, insn.op_str


def write_listing(code: bytes, addr: int, out: Path) -> None:
    with open(out, "w") as f:
        for a, b, m, o in disassemble(code, addr):
            f.write(f"{a:08x}  {b.hex():8s}  {m} {o}\n")
