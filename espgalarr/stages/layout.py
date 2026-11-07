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
# On the PS2 the walker group count is 0x4F and the delta 0xFFFF at every captured state
# (0xC46428 / 0xC46430, all 70 bullet samples); Arrange's inlined limit setter (A0A9 0x8E6C40,
# which the de-translator prints as writes of ARR_CAP / ARR_DELTA) only ever raises the count to
# 0x4F. Added code may write ARR_CAP only with 0x4F and ARR_DELTA only with 0xFFFF or the
# setter's difference (a data register): anything else would cap the 800-slot pool.
WALKER_WRITES = {ARR_CAP: ("#$4f",), ARR_DELTA: ("#$ffff", "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7")}


def check_walker_writes(p: bytes, allocs: list[tuple[int, int, str]]) -> int:
    """Assert that no added P ROM block writes the walker variables other than as Arrange does
    (WALKER_WRITES); returns the number of writes checked."""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_000)
    n = 0
    for start, size, note in allocs:
        blob = p[start:start + size]
        for var, ok in WALKER_WRITES.items():
            needle, k = struct.pack(">I", var), 0
            while (k := blob.find(needle, k)) >= 0:
                a = start + k
                k += 2
                for back in (2, 4):            # the opcode, or the opcode and an immediate
                    try:
                        i = next(md.disasm(p[a - back:a - back + 10], a - back), None)
                        if i is not None and i.address + i.size == a + 4 and i.op_str.endswith(f"${var:x}.l"):
                            break
                    except capstone.CsError:   # (data the disassembler will not decode)
                        continue
                else:
                    continue                   # a read (source operand) or not an instruction
                src = i.op_str.split(",")[0].strip()
                assert i.mnemonic.startswith("move") and src in ok, \
                    f"{note}: {i.mnemonic} {i.op_str} at {i.address:#x} writes the walker variable {var:#x}"
                n += 1
    return n


# Item pool (gems, gold items): arcade 100 records of 30 bytes at 0x817780 with the live count
# 0x818338; Arrange 800 (the PS2 sets its slot count, 0x29A310, to 800). Every walker but the
# two spawns (0x16E702, 0x16FD6E: `move.w #$63,d7`) is bounded by the live count; the clear
# 0x16E696 also clears the count and the item variables after the pool, so it is replaced
# (asm/m68k/item_pool.s). The pool goes to the upper half of the enhanced RAM.
ITEM_POOL_OLD, ITEM_RECORD, ARCADE_ITEMS, ARRANGE_ITEMS = 0x817780, 30, 100, 800
ITEM_POOL = 0xE10000
ITEM_SPAWN_COUNTS = (0x16E70C, 0x16FD78)
# The third gem kind and the 90 gold item (handlers 0x16FB5C, 0x16F636) draw only every other
# frame when close to the player while 80 items are alive; Arrange (0x7BD5F0) does so from 780
# (0x30C) items on.
ITEM_THIN_SITES = (0x16F76A, 0x16FCA2)     # cmpi.w #$50,$818338.l
# Arrange clears the whole item pool at every stage start (ELF 0x22F138: A0A9 0x7C07D0 after the
# stage set-up 0x147298's twin; PCSX2 Black run: all records zero from the stage 2 start, key
# 0x1F51). The arcade clears its pool only at the game start (0x147BB2); its stage change (state
# 1, table 0x180424: 0x1802B2 `jsr $147298.l`, MAME frame 10500 of the Black long run) clears
# nothing. The items spawn without clearing their +0x16 / +0x18 (0x16FD54 / 0x16FDD8), which the
# handlers add on a first frame with the toggle set, so a record left from stage 1 moved a stage
# 2 gold item off (the Black long run's 4-key score blips: the item homed in and was collected 2
# logic steps early or late). The call becomes `jsr $147298.l ; jmp $16e696.l` in free ROM
# (0x16E696 = asm/m68k/item_pool.s, which also clears the item variables).
STAGE_ITEM_CLEAR = 0x1802B2


def arrange_items(p: bytes, ps: m68k.PatchSet) -> dict:
    """800-slot item pool in enhanced RAM."""
    n = relocate_range(p, ps, ITEM_POOL_OLD, ITEM_POOL_OLD + ARCADE_ITEMS * ITEM_RECORD, ITEM_POOL, "item pool")
    for a in ITEM_SPAWN_COUNTS:
        assert p[a:a + 4] == b"\x00\x00\x00\x00", f"item spawn count at {a:#x}"
        ps.add(a + 2, struct.pack(">H", ARRANGE_ITEMS - 1), f"item spawn walk {ARCADE_ITEMS} -> {ARRANGE_ITEMS} slots")
    for a in ITEM_THIN_SITES:
        assert p[a:a + 8] == bytes.fromhex("00000000") + struct.pack(">I", 0x818338), f"item draw thinning {a:#x}"
        ps.add(a + 2, struct.pack(">H", 0x30C), "item draw thinning from 780 items (Arrange 0x7BD5F0)")
    end = ITEM_POOL + ARRANGE_ITEMS * ITEM_RECORD
    assert sprite_layout(True).end <= ITEM_POOL and end <= ENH_RAM + ENH_RAM_SIZE
    clear = (Path(__file__).parents[2] / "asm" / "m68k" / "item_pool.s").read_text()
    assert f"POOL, ${ITEM_POOL:x}\n" in clear and f"SLOTS, {ARRANGE_ITEMS}\n" in clear, "item_pool.s out of date"
    return {"base_sites": n, "end": end}


def stage_item_clear(state, p: bytes, ps: m68k.PatchSet) -> None:
    """The item pool cleared at every stage start, after the stage set-up (STAGE_ITEM_CLEAR)."""
    assert p[STAGE_ITEM_CLEAR:STAGE_ITEM_CLEAR + 6] == bytes.fromhex("000000000000"), "stage change set-up call"
    code = bytes.fromhex("4eb9") + struct.pack(">I", 0x147298) + bytes.fromhex("4ef9") + struct.pack(">I", 0x16E696)
    addr = state.alloc_p(len(code), align=2, note="stage start item pool clear")
    ps.add(addr, code, "stage set-up, then the item pool clear (Arrange 0x7C07D0)")
    ps.add(STAGE_ITEM_CLEAR + 2, struct.pack(">I", addr), "the item pool cleared at every stage start")


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
# Arrange clears both player-shot pools at every stage start, in the first run of a stage task
# (A0A9 0x7EB8D0, d7 = 1 -> 0x7E5560: memsets of 0xC31200 / 0xC3261C, 0x141C bytes each, in ELF
# 0x26AFE0), which comes after the player task in play step 1. A volley fired on that step (the
# Power Shot's cadence put one there at the stage 6 start of the both-shots run) is gone again
# before the pool walker runs. The arcade clears its pools only at game start (0x123B2E). The
# port's pools are cleared with the Arrange-only variables at the stage load (bullet_pool.s),
# before step 1, so that volley stayed in flight, held a record more until the volley cap and
# moved every later volley (PCSX2 hooks at the stage 6 start, TODO 8a). The per-frame pass
# (0x17C824, d7 = 0: its first call at 0x17C830, before the pool walker 0x126960) now clears
# both pools first on play step 1 (play counter 0x813A1E = 1).
# The same ELF 0x26AFE0 then calls Arrange's stage variable clear A0A9 0x7CE760, the twin of the
# arcade's 0x17FF0C (which clears the block 0x81E1F0..0x81E265, then calls 0x112B22): Arrange
# runs it at the stage load as the arcade does and again here, at the resume. In the stall
# between, the stage task sets 0x81E254 / 0x81E256 (0x17FFA4) and the clear text setup
# (0x18162A = A0A9 0x87A0C0) sets 0x81E252, the gate of the score run-down (score.py; the only
# block words written in the stall, MAME write tap at the stage 3 start). On the PS2 these three
# (0x3586D8 / 0x3586DC / 0x3586E0) are 1 at the resume and 0 after the clear (PCSX2 hooks on
# 0x7CE760, stages 2 and 3), so the run-down starts with play; in the port the gate stayed set
# until the stage start typer (0x181D28 = A0A9 0x87B9F0) ended, 44 frames later, and the kept hit
# count paid the stage 3 kill at key 0x4535 48 x 26 for the PS2's 48 x 10 (TODO 5a). Step 1 now
# also clears those three words and calls 0x112B22.
STAGE_RESUME_CLEARS = (0x81E252, 0x81E254, 0x81E256)
STAGE_SHOT_CLEAR = 0x17C830
STAGE_SHOT_CLEAR_ASM = """
        cmpi.w  #1,$813a1e.l
        bne     sc_done
        movem.l d0-d1/a0,-(a7)
        lea     ${pool:x}.l,a0
        move.w  #{longs}-1,d0
        moveq   #0,d1
sc_clr: move.l  d1,(a0)+
        dbra    d0,sc_clr
{resume}        jsr     $112b22.l
        movem.l (a7)+,d0-d1/a0
sc_done: jmp    $17a898.l
"""
# Arrange's Power Shot trail pools (64 records of 0x22 bytes per player, PS2 0xC33A70 /
# 0xC342F0; chars.py), right below the shot pools so one loop clears both (shot_pool.s).
TRAIL_RECORD, TRAILS = 0x22, 64
TRAIL_POOL = SHOT_POOL - 2 * TRAILS * TRAIL_RECORD
# Arrange-only words of hand-ported code (collision.py), at the top of the porter's variable area
COLLISION_VARS = 0xE0AFF0
# Arrange's stage 1 boss block long at +0x1C (PS2 0xC29BAC: the merged hit marks of the boss's
# parts, restructured.py 0x6C08D0), right below the collision words (the printed 0x813D24 is
# the arcade's arm entries)
BOSS1_MARKS = 0xE0AFE8
# Arrange's score popups (80 records of 30 bytes, PS2 0x6409C0, score.py) in the porter's
# variable area above what port_all assigns (stages/code.py checks), cleared at game start
# (shot_pool.s)
POPUPS, POPUP_RECORD = 80, 30
POPUP_LIST = 0xE0A000
# Arrange's score counters (PS2 0xC37670..0xC37683, score.py), right below the trail pools so
# the game-start clear covers them (shot_pool.s)
SCORE_VARS_SIZE = 0x14
SCORE_VARS = TRAIL_POOL - SCORE_VARS_SIZE


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
    assert SHOT_LIST + 0x40 <= ENH_RAM + ENH_RAM_SIZE
    assert NEWVAR_BASE + 0x100 <= COLLISION_VARS < NEWVAR_BASE + NEWVAR_SIZE
    clear = (Path(__file__).parents[2] / "asm" / "m68k" / "shot_pool.s").read_text()
    assert f"CLEAR_FROM, ${SCORE_VARS:x}\n" in clear and \
        f"${SCORE_VARS_SIZE:x}+2*{TRAILS}*${TRAIL_RECORD:x}+2*{ARRANGE_SHOTS}*${SHOT_RECORD:x}\n" in clear, \
        "shot_pool.s clear range out of date"
    assert NEWVAR_BASE + NEWVAR_SIZE <= SCORE_VARS
    assert NEWVAR_BASE + 0x400 <= POPUP_LIST and POPUP_LIST + POPUPS * POPUP_RECORD <= COLLISION_VARS
    assert f"POPUP_LIST, ${POPUP_LIST:x}\n" in clear and f"POPUP_BYTES, {POPUPS}*{POPUP_RECORD}\n" in clear, \
        "shot_pool.s popup clear out of date"
    # the stage-load clear of the Arrange-only variables keeps the score counters (bullet_pool.s)
    stage = (Path(__file__).parents[2] / "asm" / "m68k" / "bullet_pool.s").read_text()
    assert f"NEWVARS, ${NEWVAR_BASE:x} " in stage and f"SCORE_VARS, ${SCORE_VARS:x} " in stage and \
        f"SCORE_BYTES, ${SCORE_VARS_SIZE:x}\n" in stage, "bullet_pool.s score-counter gap out of date"
    return {"base_sites": n, "end": SHOT_LIST}


def stage_shot_clear(state, p: bytes, ps: m68k.PatchSet) -> None:
    """Both player-shot pools cleared on each stage's play step 1, before the walker, and Arrange's
    resume clear of the stage variables (STAGE_SHOT_CLEAR, STAGE_RESUME_CLEARS)."""
    from ..pgm import asm68k
    assert p[STAGE_SHOT_CLEAR:STAGE_SHOT_CLEAR + 6] == bytes.fromhex("000000000000"), "per-frame pass first call"
    assert p[0x17FF0C:0x17FF1E] == bytes.fromhex("000000000000000000000000000000000000"), "arcade stage variable clear"
    assert p[0x17FF1E:0x17FF24] == bytes.fromhex("000000000000"), "arcade stage variable clear's 0x112B22 call"
    assert all(0x81E1F0 <= a < 0x81E266 for a in STAGE_RESUME_CLEARS)
    resume = "".join(f"        clr.w   ${a:x}.l\n" for a in STAGE_RESUME_CLEARS)
    text = STAGE_SHOT_CLEAR_ASM.format(pool=SHOT_POOL, longs=2 * ARRANGE_SHOTS * SHOT_RECORD // 4, resume=resume)
    code, _ = asm68k.assemble(text, 0)
    addr = state.alloc_p(len(code), align=2, note="stage step 1 player-shot pool clear")
    code, _ = asm68k.assemble(text, addr)
    ps.add(addr, code, "player-shot pools and the stage variables cleared on play step 1 (ELF 0x26AFE0: "
           "Arrange 0x7E5560, 0x7CE760), then 0x17A898")
    ps.add(STAGE_SHOT_CLEAR + 2, struct.pack(">I", addr), "per-frame pass: the stage step 1 shot pool clear first")


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

# Enhanced RAM window of the `espgalarr` machine (patches/mame/enhanced-sprites.diff): 128 KB at
# 0xE00000, unmapped on a stock PGM. It holds the Arrange-sized sprite lists that do not fit in
# the PGM's 128 KB work RAM next to everything else.
ENH_RAM, ENH_RAM_SIZE = 0xE00000, 0x20000
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
        stage_shot_clear(state, bytes(state.p_rom), ps)
        ps.apply(state.p_rom)
        state.note(f"layout: Arrange player-shot pools, 2 x {ARRANGE_SHOTS} records in enhanced RAM "
                   f"{SHOT_POOL:#x}-{st['end'] - 1:#x} ({st['base_sites']} base sites, walker counts)")
    if getattr(state, "arrange_bullets", False):
        ps = m68k.PatchSet()
        st = arrange_bullets(bytes(state.p_rom), ps)
        ps.apply(state.p_rom)
        state.note(f"layout: Arrange bullet pool, {ARRANGE_BULLETS} slots in ARM RAM {ARM_RAM:#x}-{st['end'] - 1:#x} "
                   f"({st['base_sites']} base sites, {st['cap_reads']} group-count reads, {st['delta_sites']} delta sites)")
        ps = m68k.PatchSet()
        st = arrange_items(bytes(state.p_rom), ps)
        stage_item_clear(state, bytes(state.p_rom), ps)
        ps.apply(state.p_rom)
        state.note(f"layout: Arrange item pool, {ARRANGE_ITEMS} slots in enhanced RAM {ITEM_POOL:#x}-{st['end'] - 1:#x} "
                   f"({st['base_sites']} base sites, spawn walks)")
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
