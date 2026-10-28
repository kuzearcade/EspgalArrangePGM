"""68000 disassembly / patching helpers for the PGM program ROM (capstone)."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

try:
    import capstone as cs
except ImportError:  # pragma: no cover
    cs = None


def m68k_disassembler():
    if cs is None:
        raise RuntimeError("capstone not installed")
    md = cs.Cs(cs.CS_ARCH_M68K, cs.CS_MODE_M68K_000)
    md.detail = False
    md.skipdata = True
    return md


def disassemble(code: bytes, addr: int):
    md = m68k_disassembler()
    for insn in md.disasm(code, addr):
        yield insn.address, insn.bytes, insn.mnemonic, insn.op_str


def write_listing(code: bytes, addr: int, out: Path) -> None:
    with open(out, "w") as f:
        for a, b, m, o in disassemble(code, addr):
            f.write(f"{a:06x}  {b.hex():24s}  {m} {o}\n")


# --- patch engine -----------------------------------------------------------

@dataclass
class Patch:
    offset: int          # 68k address == ROM offset (ROM is mapped at 0)
    data: bytes
    note: str = ""


@dataclass
class PatchSet:
    patches: list[Patch] = field(default_factory=list)

    def add(self, offset: int, data: bytes, note: str = "") -> None:
        self.patches.append(Patch(offset, bytes(data), note))

    def jmp(self, at: int, target: int, note: str = "") -> None:
        """Overwrite 6 bytes at `at` with JMP target.L."""
        self.add(at, b"\x4e\xf9" + struct.pack(">I", target), note)

    def jsr(self, at: int, target: int, note: str = "") -> None:
        self.add(at, b"\x4e\xb9" + struct.pack(">I", target), note)

    def apply(self, rom: bytearray) -> None:
        for p in self.patches:
            end = p.offset + len(p.data)
            if end > len(rom):
                raise ValueError(f"patch {p.note!r} at {p.offset:#x} exceeds ROM")
            rom[p.offset:end] = p.data

    def overlaps(self) -> list[tuple[Patch, Patch]]:
        out = []
        ps = sorted(self.patches, key=lambda p: p.offset)
        for a, b in zip(ps, ps[1:]):
            if a.offset + len(a.data) > b.offset:
                out.append((a, b))
        return out
