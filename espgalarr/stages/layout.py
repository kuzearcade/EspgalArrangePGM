"""Build stage 'layout': relocate and enlarge object pools for the Arrange data layout.

The Arrange build enlarges the enemy object record from 0x24 to 0x2C bytes (recovered
constant changes, see detrans/port.py). PGM work RAM is full, so the enemy pools move to a
region that a 1P+2P MAME session never writes (0x80BCD6-0x80ED65, see STATUS.md). The
transform rewrites every `lea base.l,aN` / `movea.l #base,aN` site to the new base and, inside
the same routine, every `lea +/-stride(aN),aN` walk of that register to the new stride.
Record counts are unchanged, so a build with this stage but without new Arrange code must
behave exactly like the original (verified by scripted MAME runs).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ..pgm import m68k

FREE_BASE = 0x80BCD6
CODE_LO, CODE_HI = 0x110000, 0x1C0000


@dataclass
class Pool:
    name: str
    base: int
    count: int
    stride: int
    new_stride: int

    @property
    def end(self) -> int:
        return self.base + self.count * self.stride


POOLS = [Pool("enemyA", 0x815680, 90, 0x24, 0x2C), Pool("enemyB", 0x816328, 30, 0x24, 0x2C)]


def _routine_end(p: bytes, start: int) -> int:
    """Scan forward to the first rts / jmp abs / jmp (an) after `start` (coarse routine end)."""
    i = start
    while i < CODE_HI - 1:
        op = struct.unpack_from(">H", p, i)[0]
        if op == 0x4E75 or op == 0x4E74 or (op & 0xFFC0) == 0x4EC0:
            return i + 2
        i += 2
    return CODE_HI


STRIDE_CHANGES = {(0x24, 0x2C), (-0x24, -0x2C), (0x22, 0x2A), (0x48, 0x58), (0x438, 0x528), (0xCA8, 0xF78)}


def _signed(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def relocate_pools(p: bytes, ps: m68k.PatchSet, port_patches: list[dict], pools=POOLS, free_base: int = FREE_BASE) -> dict:
    """Relocate the pool bases (every `lea`/`movea.l #base` site) and apply the stride-related
    constant changes recovered from the Arrange code (held back by the code stage). Stride sites
    are taken ONLY from the recovered Arrange diff: routines that do not change in Arrange do not
    walk these pools, and routines rewritten in Arrange are replaced wholesale."""
    stats = {}
    nb = free_base
    for pool in pools:
        new_base = nb
        nb += pool.count * pool.new_stride
        n_base = 0
        new_end = new_base + pool.count * pool.new_stride
        for i in range(CODE_LO, CODE_HI - 3, 2):
            v = struct.unpack_from(">I", p, i)[0]
            if v == pool.base or v == pool.end:
                # any absolute long equal to the base or the end of the pool (lea/movea/cmpa/cmpi...)
                ps.add(i, struct.pack(">I", new_base if v == pool.base else new_end), f"{pool.name} {'base' if v == pool.base else 'end'} ref at {i:#x}")
                n_base += 1
        stats[pool.name] = {"new_base": new_base, "base_sites": n_base}
    n_stride = 0
    from .code import _encode_imm
    import os
    if os.environ.get("ESPGAL_LAYOUT_NO_STRIDE"):
        port_patches = []
    for pt in port_patches:
        if (_signed(pt["old"]), _signed(pt["new"])) in STRIDE_CHANGES:
            ps.add(pt["rom_addr"], _encode_imm(pt, p), f"stride {pt['old']:#x}->{pt['new']:#x} in {pt['mnem']} (routine {pt['routine']:#x})")
            n_stride += 1
    stats["stride_sites"] = n_stride
    return stats


# Enemy bullet pool: 200 slots x 0x40 bytes at 0x818352; referenced only through
# `lea $818352.l,aN` / `lea $818354.l,aN` (the following long at 0x81B552 is a counter).
BULLET_POOL = (0x818352, 0x81B552)
ARM_RAM = 0xD00000          # DDP2 cart ARM shared RAM (64 KB), unused by the bootleg


def relocate_range(p: bytes, ps: m68k.PatchSet, lo: int, hi: int, new_lo: int, name: str) -> int:
    """Rewrite `lea abs.l,aN` / `movea.l #abs,aN` operands in [lo, hi) to new_lo + offset."""
    n = 0
    for i in range(CODE_LO, CODE_HI - 5, 2):
        op = struct.unpack_from(">H", p, i)[0]
        if (op & 0xF1FF) not in (0x41F9, 0x207C):
            continue
        v = struct.unpack_from(">I", p, i + 2)[0]
        if lo <= v < hi:
            ps.add(i + 2, struct.pack(">I", new_lo + (v - lo)), f"{name} {v:#x} -> {new_lo + (v - lo):#x}")
            n += 1
    return n


def run(state) -> None:
    if getattr(state, "relocate_bullets", False):
        ps = m68k.PatchSet()
        n = relocate_range(bytes(state.p_rom), ps, *BULLET_POOL, ARM_RAM, "bullet pool")
        ps.apply(state.p_rom)
        state.note(f"layout: enemy bullet pool relocated to ARM RAM {ARM_RAM:#x} ({n} sites)")
    if not getattr(state, "relocate_pools", False):
        state.note("layout: pool relocation disabled (enable with --relocate-pools)")
        return
    import json
    cache = state.work / "ps2" / "detrans" / "port_patches.json"
    if not cache.exists():
        from ..detrans import port as port_mod
        port_mod.port_near()
    ps = m68k.PatchSet()
    stats = relocate_pools(bytes(state.p_rom), ps, json.loads(cache.read_text()))
    ps.apply(state.p_rom)
    state.pool_layout = stats
    state.note("layout: " + "; ".join(f"{k}: base {v['new_base']:#x} ({v['base_sites']} sites)" for k, v in stats.items() if isinstance(v, dict))
               + f"; {stats['stride_sites']} recovered stride constants applied")
