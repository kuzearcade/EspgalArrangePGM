"""Build stage 'code': 68k gameplay changes for Arrange mode.

Rules with addresses established in spec/pgm-gameplay.md are patched directly here; the
rules that need the recovered Arrange routines (player characters, shots, barrier, economy,
scoring) are added as the de-translator delivers them (see spec/arrange-diff.md).
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from ..pgm import asm68k, m68k

ASM_DIR = Path(__file__).resolve().parents[2] / "asm" / "m68k"

# Extend thresholds (in 10-point units) indexed by the dip-switch setting; the last entry
# (0xFFFFFFFF) is "no extend" and is kept. Arrange: 80,000,000 and 280,000,000 points.
EXTEND_TABLE_1 = 0x178D7E
EXTEND_TABLE_2 = 0x178D8E
ARRANGE_EXTEND_1 = 8_000_000
ARRANGE_EXTEND_2 = 28_000_000


def extend_patches(ps: m68k.PatchSet, p: bytes) -> None:
    for table, value in ((EXTEND_TABLE_1, ARRANGE_EXTEND_1), (EXTEND_TABLE_2, ARRANGE_EXTEND_2)):
        for i in range(4):
            cur = struct.unpack_from(">I", p, table + 4 * i)[0]
            if cur != 0xFFFFFFFF:
                ps.add(table + 4 * i, struct.pack(">I", value), f"extend threshold {i}")


# "No continues": the continue flow checks the continue-block flag 0x8139B2 with
# `tst.w $8139B2 ; bne.w <skip>` at these sites; turning the bne into bra always skips.
CONTINUE_BNE_SITES = (0x1791DE, 0x1795D6, 0x140DA8)


def continue_patches(ps: m68k.PatchSet, p: bytes) -> None:
    for a in CONTINUE_BNE_SITES:
        assert p[a:a + 2] == b"\x66\x00", f"expected bne.w at {a:#x}"
        ps.add(a, b"\x60\x00", "no continues: bne.w -> bra.w")


def install_routines(state, ps: m68k.PatchSet) -> int:
    """Assemble every asm/m68k/*.s into free program ROM and hook it in.

    A source file declares what it replaces with header comments:
        ; replaces 0x1234AB      -> `jmp new.l` written over the first 6 bytes of that routine
        ; hook 0x1234AB jsr      -> `jsr new.l` written at that address (6 bytes)
        ; entry name             -> label used as the hook target (default: first label)
    Labels of the form `orig_XXXXXX` resolve to that 68k address so new code can call the
    original routines.
    """
    n = 0
    for src in sorted(ASM_DIR.glob("*.s")):
        text = src.read_text()
        replaces = [int(x, 16) for x in re.findall(r"^;\s*replaces\s+(0x[0-9a-fA-F]+)", text, re.M)]
        hooks = [(int(x, 16), kind) for x, kind in re.findall(r"^;\s*hook\s+(0x[0-9a-fA-F]+)\s+(jsr|jmp)", text, re.M)]
        entry = re.findall(r"^;\s*entry\s+(\w+)", text, re.M)
        # pre-pass to learn the size, then place
        code, syms = asm68k.assemble(text, 0x200000)
        addr = state.alloc_p(len(code), align=4, note=src.name)
        code, syms = asm68k.assemble(text, addr)
        target = syms[entry[0]] if entry else min(v for v in syms.values() if v >= addr)
        ps.add(addr, code, f"routine {src.name}")
        for a in replaces:
            ps.jmp(a, target, f"{src.name} replaces {a:#x}")
        for a, kind in hooks:
            (ps.jsr if kind == "jsr" else ps.jmp)(a, target, f"{src.name} hook {a:#x}")
        n += 1
    return n


def _encode_imm(pt: dict, p: bytes) -> bytes:
    """Bytes of the instruction after rewriting one immediate (see detrans/port.py)."""
    raw = bytearray(p[pt["rom_addr"]:pt["rom_addr"] + pt["nbytes"]])
    if pt["kind"] == "moveq":
        raw[1] = pt["new"] & 0xFF
    elif pt["kind"] == "quick":
        op = struct.unpack_from(">H", raw, 0)[0] & ~0x0E00
        op |= ((pt["new"] & 7) << 9)
        struct.pack_into(">H", raw, 0, op)
    else:
        w = pt["width"]
        o = pt["offset"]
        if w == 2:
            v = pt["new"] & (0xFF if pt["mnem"].endswith(".b") else 0xFFFF)
            raw[o:o + 2] = v.to_bytes(2, "big")
        else:
            raw[o:o + 4] = (pt["new"] & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(raw)


def detrans_patches(state, ps: m68k.PatchSet, p: bytes) -> int:
    """Apply the constant changes recovered from the Arrange code (detrans/port.py)."""
    import json
    cache = state.work / "ps2" / "detrans" / "port_patches.json"
    if not cache.exists():
        from ..detrans import port as port_mod
        port_mod.port_near()
    n = 0
    layout = []
    for pt in json.loads(cache.read_text()):
        # Record-stride / layout changes (e.g. a 0x24-byte object record becoming 0x2C bytes)
        # must be applied to every pool walk at once, not per routine: keep them aside.
        if pt["mnem"].split(".")[0] in ("lea", "adda", "suba") or (pt["old"] & 0xFFFF, pt["new"] & 0xFFFF) in LAYOUT_CHANGES:
            layout.append(pt)
            continue
        ps.add(pt["rom_addr"], _encode_imm(pt, p), f"arrange constant {pt['old']:#x}->{pt['new']:#x} in {pt['mnem']} (routine {pt['routine']:#x})")
        n += 1
    if layout:
        state.note(f"code: {len(layout)} layout (record stride) changes held back: "
                   + ", ".join(f"{x['rom_addr']:#x}:{x['old']:#x}->{x['new']:#x}" for x in layout))
    return n


LAYOUT_CHANGES = {(0x24, 0x2C), (0xFFDC, 0xFFD4), (0x22, 0x2A), (0xFFDE, 0xFFD6)}


def run(state) -> None:
    ps = m68k.PatchSet()
    p = bytes(state.p_rom)
    extend_patches(ps, p)
    continue_patches(ps, p)
    nr = install_routines(state, ps)
    nc = detrans_patches(state, ps, p)
    ps.apply(state.p_rom)
    state.note(f"code: {len(ps.patches)} patches (extends 80M/280M, continues disabled, {nr} new routines, "
               f"{nc} recovered Arrange constants)")
