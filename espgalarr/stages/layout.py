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
from pathlib import Path
from dataclasses import dataclass

from ..pgm import m68k

FREE_BASE = 0xE08200        # enhanced RAM, after the Arrange sprite lists (sprite_layout().end)
POOL_AREA_END = FREE_BASE + 120 * 0x2C + 2     # records + the counter word after them (0x816760)
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


STRIDE_CHANGES = {(0x22, 0x2A), (-0x22, -0x2A)}    # plus every k*0x24 -> k*0x2C (code._record_multiple)


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
            if v == pool.end and v != pool.base and not any(p2.base == v for p2 in pools):
                # the word after the last record is a counter (clr/addq/tst $816760): only rewrite
                # the end address where it is used as a pointer (lea/movea/cmpa #)
                op = struct.unpack_from(">H", p, i - 2)[0]
                if (op & 0xF1FF) not in (0x41F9, 0x207C, 0xB1FC):
                    continue
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
        from .code import _record_multiple
        if (_signed(pt["old"]), _signed(pt["new"])) in STRIDE_CHANGES or _record_multiple(pt["old"], pt["new"]):
            ps.add(pt["rom_addr"], _encode_imm(pt, p), f"stride {pt['old']:#x}->{pt['new']:#x} in {pt['mnem']} (routine {pt['routine']:#x})")
            n_stride += 1
    stats["stride_sites"] = n_stride
    return stats


# Enemy bullet pool: 200 slots x 0x40 bytes at 0x818352; referenced only through
# `lea $818352.l,aN` / `lea $818354.l,aN` (the following word at 0x81B552 is a counter).
BULLET_POOL = (0x818352, 0x81B552)
ARM_RAM = 0xD00000          # DDP2 cart ARM shared RAM (64 KB), unused by the bootleg

# Arrange bullet pool (recovered from the Arrange initialiser and pool walkers): 800 slots of
# the same 0x40-byte layout. The walkers scan the pool in groups of 10 slots up to a group
# count (arcade 0x81B55A, 6..0x13 = 70..200 bullets, lowered/raised by stage scripts, with
# 0x81B562 = groups just removed). Arrange gives the walkers their own two variables, set
# once by the initialiser (0x4F = 80 groups = 800 bullets, delta 0xFFFF = none) and never
# touched by the stage scripts. The port does the same: the 11 walker reads of the group
# count and the delta reads/resets move to two new words in the freed old pool area; the
# stage scripts keep writing the arcade variables, which other routines still read.
ARRANGE_BULLETS = 800
ARR_CAP, ARR_DELTA = 0x818352, 0x818354
CAP_READS = (0x11B8EC, 0x11E170, 0x170608, 0x170F4E, 0x170FD0, 0x171376, 0x171404,
             0x171514, 0x171946, 0x171EBC, 0x173078)          # move.w $81B55A.l,d6/d7
DELTA_READS = (0x170996, 0x171726, 0x171BF8, 0x17216E, 0x173242)  # move.w $81B562.l,d7
DELTA_RESETS = (0x173416,)                                     # move.w #$FFFF,$81B562.l
BULLET_INIT = 0x170D2E      # replaced by asm/m68k/bullet_pool.s (requires arrange_bullets)


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


def arrange_bullets(p: bytes, ps: m68k.PatchSet) -> dict:
    """800-slot Arrange bullet pool in cart ARM RAM with the Arrange walker variables."""
    n = relocate_range(p, ps, *BULLET_POOL, ARM_RAM, "bullet pool")
    for a in CAP_READS:
        op = struct.unpack_from(">H", p, a)[0]
        assert (op & 0xF1FF) == 0x3039 and struct.unpack_from(">I", p, a + 2)[0] == 0x81B55A, f"cap read {a:#x}"
        ps.add(a + 2, struct.pack(">I", ARR_CAP), f"walker group count read at {a:#x} -> Arrange variable")
    for a in DELTA_READS:
        op = struct.unpack_from(">H", p, a)[0]
        assert (op & 0xF1FF) == 0x3039 and struct.unpack_from(">I", p, a + 2)[0] == 0x81B562, f"delta read {a:#x}"
        ps.add(a + 2, struct.pack(">I", ARR_DELTA), f"walker delta read at {a:#x} -> Arrange variable")
    for a in DELTA_RESETS:
        assert p[a:a + 2] == b"\x33\xfc" and struct.unpack_from(">I", p, a + 4)[0] == 0x81B562, f"delta reset {a:#x}"
        ps.add(a + 4, struct.pack(">I", ARR_DELTA), f"walker delta reset at {a:#x} -> Arrange variable")
    end = ARM_RAM + ARRANGE_BULLETS * 0x40
    assert end <= 0xD0C840, "bullet pool would overlap the sprite continuation list"
    return {"base_sites": n, "cap_reads": len(CAP_READS), "delta_sites": len(DELTA_READS) + len(DELTA_RESETS), "end": end}


# Player-shot pools: arcade 35 records of 0x34 bytes per player (P1 0x80EFD6, P2 0x80F6F2,
# contiguous), Arrange 99 (98 shots and a terminator; PS2 0xC31200 / 0xC3261C). Walked by the
# shot routines (from a pool base, as many slots as a volley needs) and by the per-frame walker
# 0x126980 (P1 then P2, `move.w #$22,d7` each; Arrange 0x62). The new pools take the top of
# the enhanced RAM left by porter.NEWVAR_SIZE; asm/m68k/shot_pool.s clears them at game start.
SHOT_POOLS = (0x80EFD6, 0x80F6F2)
SHOT_RECORD, ARCADE_SHOTS, ARRANGE_SHOTS = 0x34, 35, 99
SHOT_POOL = 0xE0C800
SHOT_WALK_COUNTS = (0x1269A6, 0x1269F0)
SHOT_LIST = SHOT_POOL + 2 * ARRANGE_SHOTS * SHOT_RECORD        # after the pools: slot lists
# Arrange's Power Shot trail pools (64 records of 0x22 bytes per player, PS2 0xC33A70 /
# 0xC342F0; chars.py), right below the shot pools so one loop clears both (shot_pool.s).
TRAIL_RECORD, TRAILS = 0x22, 64
TRAIL_POOL = SHOT_POOL - 2 * TRAILS * TRAIL_RECORD


def arrange_shots(p: bytes, ps: m68k.PatchSet) -> dict:
    """Both player-shot pools at SHOT_POOL with the Arrange size."""
    n = 0
    for k, base in enumerate(SHOT_POOLS):
        new = SHOT_POOL + k * ARRANGE_SHOTS * SHOT_RECORD
        for i in range(CODE_LO, CODE_HI - 5, 2):
            op = struct.unpack_from(">H", p, i)[0]
            if (op & 0xF1FF) in (0x41F9, 0x207C) and struct.unpack_from(">I", p, i + 2)[0] == base:
                ps.add(i + 2, struct.pack(">I", new), f"player-shot pool {k + 1} {base:#x} -> {new:#x}")
                n += 1
    for a in SHOT_WALK_COUNTS:
        assert p[a:a + 4] == b"\x00\x00\x00\x00", f"shot walker count at {a:#x}"
        ps.add(a + 2, struct.pack(">H", ARRANGE_SHOTS - 1), f"shot walker count {ARCADE_SHOTS - 1:#x} -> {ARRANGE_SHOTS - 1:#x}")
    from ..detrans.porter import NEWVAR_BASE, NEWVAR_SIZE
    assert NEWVAR_BASE + NEWVAR_SIZE <= TRAIL_POOL and SHOT_LIST + 0x40 <= ENH_RAM + ENH_RAM_SIZE
    clear = (Path(__file__).parents[2] / "asm" / "m68k" / "shot_pool.s").read_text()
    assert f"TRAIL_POOL, ${TRAIL_POOL:x}\n" in clear and f"2*{TRAILS}*${TRAIL_RECORD:x}+2*{ARRANGE_SHOTS}*${SHOT_RECORD:x}\n" in clear, \
        "shot_pool.s clear range out of date"
    return {"base_sites": n, "end": SHOT_LIST}


# Sprite layers, in the builder's emit order (first = highest priority): (list base, byte
# counter, capacity in entries). Capacities of the original layers are the RAM gap to the next
# list (layer 0 doubled as the merge buffer, hence 251).
ORIG_LAYERS = [
    (0x80120A, 0x806FCA, 251), (0x804486, 0x806FD8, 20), (0x801DCE, 0x806FCC, 175),
    (0x804576, 0x806FDA, 20), (0x802602, 0x806FCE, 175), (0x804666, 0x806FDC, 20),
    (0x802E36, 0x806FD0, 175), (0x804756, 0x806FDE, 20), (0x80366A, 0x806FD2, 175),
    (0x804846, 0x806FE0, 20), (0x803E9E, 0x806FD4, 113), (0x806B92, 0x806FFE, 90),
    (0x806AA2, 0x806FFA, 10), (0x8066A6, 0x806FF4, 5), (0x8066E2, 0x806FF6, 75),
    (0x806A66, 0x806FF8, 5), (0x804936, 0x806FE2, 56), (0x804BD6, 0x806FE4, 8),
    (0x804C36, 0x806FE6, 70), (0x8043EA, 0x806FD6, 13), (0x804F7E, 0x806FE8, 22),
    (0x805086, 0x806FEA, 30), (0x8063BE, 0x806FF0, 16), (0x8051EE, 0x806FEC, 180),
    (0x805A5E, 0x806FEE, 200), (0x80647E, 0x806FF2, 46), (0x806B1A, 0x806FFC, 10),
]

# Enhanced RAM window of the `espgalarr` machine (patches/mame/enhanced-sprites.diff): 64 KB at
# 0xE00000, unmapped on a stock PGM. It holds the Arrange-sized sprite lists that do not fit in
# the PGM's 128 KB work RAM next to everything else.
ENH_RAM, ENH_RAM_SIZE = 0xE00000, 0x10000
# Arrange list sizes (from the PS2 sprite builder): enemy bullets 0x805A5E (filled from its base
# by the bullet update 0x17242A) and 0x8051EE (appended by it and other helpers), gems 0x803E9E.
ARRANGE_LISTS = [(0x805A5E, 800), (0x8051EE, 720), (0x803E9E, 800)]
# Three Arrange-only layers (160, 128, 160 entries) emitted right after layer 0x804C36.
NEW_LAYERS_AFTER = 0x804C36
NEW_LAYER_SIZES = (160, 128, 160)


@dataclass
class SpriteLayout:
    layers: list            # [(base, counter, capacity)] in emit order
    moved: dict             # old base -> new base
    new_layers: list        # [(base, counter, capacity)] Arrange-only
    end: int                # first byte after the enhanced-RAM allocation


def sprite_layout(arrange: bool = True) -> SpriteLayout:
    if not arrange:
        return SpriteLayout(list(ORIG_LAYERS), {}, [], ENH_RAM)
    a = ENH_RAM
    moved, caps = {}, {}
    for base, n in ARRANGE_LISTS:
        moved[base], caps[base] = a, n
        a += 12 * n
    new_layers = []
    counters = a + 12 * sum(NEW_LAYER_SIZES)
    for i, n in enumerate(NEW_LAYER_SIZES):
        new_layers.append((a, counters + 2 * i, n))
        a += 12 * n
    end = counters + 2 * len(NEW_LAYER_SIZES)
    assert end <= ENH_RAM + ENH_RAM_SIZE
    layers = []
    for base, ctr, cap in ORIG_LAYERS:
        layers.append((moved.get(base, base), ctr, caps.get(base, cap)))
        if base == NEW_LAYERS_AFTER:
            layers.extend(new_layers)
    return SpriteLayout(layers, moved, new_layers, end)


def relocate_sprite_lists(p: bytes, ps: m68k.PatchSet, lay: SpriteLayout) -> int:
    """Rewrite every lea/movea/suba/adda/cmpa long-immediate reference to a moved list base."""
    n = 0
    for i in range(CODE_LO, CODE_HI - 5, 2):
        op = struct.unpack_from(">H", p, i)[0]
        if (op & 0xF1FF) not in (0x41F9, 0x207C, 0x91FC, 0xD1FC, 0xB1FC):
            continue
        v = struct.unpack_from(">I", p, i + 2)[0]
        if v in lay.moved:
            ps.add(i + 2, struct.pack(">I", lay.moved[v]), f"sprite list {v:#x} -> {lay.moved[v]:#x}")
            n += 1
    return n


def run(state) -> None:
    if getattr(state, "arrange_shots", False):
        ps = m68k.PatchSet()
        st = arrange_shots(bytes(state.p_rom), ps)
        ps.apply(state.p_rom)
        state.note(f"layout: Arrange player-shot pools, 2 x {ARRANGE_SHOTS} records in enhanced RAM "
                   f"{SHOT_POOL:#x}-{st['end'] - 1:#x} ({st['base_sites']} base sites, walker counts)")
    if getattr(state, "arrange_bullets", False):
        ps = m68k.PatchSet()
        st = arrange_bullets(bytes(state.p_rom), ps)
        ps.apply(state.p_rom)
        state.note(f"layout: Arrange bullet pool, {ARRANGE_BULLETS} slots in ARM RAM {ARM_RAM:#x}-{st['end'] - 1:#x} "
                   f"({st['base_sites']} base sites, {st['cap_reads']} group-count reads, {st['delta_sites']} delta sites)")
    lay = sprite_layout(getattr(state, "arrange_sprites", False))
    state.sprite_layers = lay.layers
    if lay.moved:
        ps = m68k.PatchSet()
        n = relocate_sprite_lists(bytes(state.p_rom), ps, lay)
        ps.apply(state.p_rom)
        state.note(f"layout: Arrange sprite lists in enhanced RAM {ENH_RAM:#x}-{lay.end - 1:#x} "
                   f"({n} base sites; " + ", ".join(f"{o:#x}->{nb:#x}" for o, nb in lay.moved.items())
                   + f"; new layers at " + ", ".join(f"{b:#x}x{c}" for b, _, c in lay.new_layers) + ")")
    if not getattr(state, "relocate_pools", False):
        state.note("layout: arcade enemy records kept (--arcade-records)")
        return
    import json
    cache = state.work / "ps2" / "detrans" / "port_patches.json"
    if not cache.exists():
        from ..detrans import port as port_mod
        port_mod.port_near()
    ps = m68k.PatchSet()
    stats = relocate_pools(bytes(state.p_rom), ps, json.loads(cache.read_text()))
    from .. import records
    nrec, problems = records.patch_sites(bytes(state.p_rom), ps)
    nst, pr2 = records.patch_steps(bytes(state.p_rom), ps)
    problems += pr2
    nrec += nst + records.patch_tables(bytes(state.p_rom), ps) + records.patch_table_words(bytes(state.p_rom), ps) \
        + records.patch_table_longs(bytes(state.p_rom), ps) \
        + records.patch_immediates(bytes(state.p_rom), ps)
    stats["record_sites"] = nrec
    for pr in problems:
        state.note(f"layout: WARNING record site {pr}")
    ps.apply(state.p_rom)
    state.pool_layout = stats
    state.note("layout: " + "; ".join(f"{k}: base {v['new_base']:#x} ({v['base_sites']} sites)" for k, v in stats.items() if isinstance(v, dict))
               + f"; {stats['stride_sites']} recovered stride constants and {stats['record_sites']} cross-record "
               "displacements applied (0x2C enemy records)")
