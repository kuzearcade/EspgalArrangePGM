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


# --- original-byte signatures ------------------------------------------------
# The repository holds no original ROM bytes. A site the build expects to find before patching it is
# named by a signature string "@<shape>:<digest>": shape is the byte count, or one character per byte
# ('x' checked, '?' not); digest is the first 16 hex digits of the sha1 of the bytes, the unchecked ones
# read as 0. Signatures are generated from the input ROM (sign()), never typed in. Plain hex patterns
# (our own bytes, '??' a wildcard byte) are still accepted by matches().

def _sig_parts(pat: str) -> tuple[str, str]:
    shape, digest = pat[1:].split(":")
    return (shape if not shape.isdigit() else "x" * int(shape)), digest


def sig_len(pat: str) -> int:
    """The byte length a pattern (signature or hex) covers."""
    return len(_sig_parts(pat)[0]) if pat.startswith("@") else len(pat) // 2


def _digest(b: bytes, mask: str) -> str:
    import hashlib
    return hashlib.sha1(bytes(0 if m == "?" else c for c, m in zip(b, mask))).hexdigest()[:16]


def sign(b: bytes, mask: str | None = None) -> str:
    """The signature of bytes b (mask: 'x' / '?' per byte, None = all checked)."""
    mask = mask or "x" * len(b)
    assert len(mask) == len(b)
    return f"@{len(b) if '?' not in mask else mask}:{_digest(b, mask)}"


def matches(b: bytes, pat: str) -> bool:
    """b starts with what the pattern describes (signature, or hex with '??' wildcard bytes)."""
    if pat.startswith("@"):
        mask, digest = _sig_parts(pat)
        return len(b) >= len(mask) and _digest(b[:len(mask)], mask) == digest
    return len(b) >= len(pat) // 2 and all(
        h == "??" or b[k] == int(h, 16) for k, h in enumerate(pat[i:i + 2] for i in range(0, len(pat), 2)))


def expect(p: bytes, at: int, pat: str, what: str = "") -> None:
    """Stop the build unless the ROM at `at` is what the pattern describes."""
    if not matches(p[at:at + sig_len(pat)], pat):
        raise AssertionError(f"unexpected code at {at:#x}" + (f" ({what})" if what else ""))


def sig_mask(pat: str) -> str:
    """The pattern's mask, 'x' checked / '?' wildcard per byte."""
    if pat.startswith("@"):
        return _sig_parts(pat)[0]
    return "".join("?" if pat[i:i + 2] == "??" else "x" for i in range(0, len(pat), 2))
