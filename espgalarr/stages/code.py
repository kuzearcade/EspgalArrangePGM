"""Build stage 'code': 68k gameplay changes for Arrange mode.

Rules with addresses established in spec/pgm-gameplay.md are patched directly here; the
rules that need the recovered Arrange routines (player characters, shots, barrier, economy,
scoring) are added as the de-translator delivers them (see spec/arrange-diff.md).
"""
from __future__ import annotations

import re
import struct

import capstone
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


# Guard barrier start in the player update (arcade 0x1227DC, Arrange 0x82B970, which has no
# exact twin so the porter cannot install it). Arrange starts the barrier on D (bit 7 of the
# held input +0x18) instead of C and moves the rapid-fire toggle from D to C (Arrange 0x830B20);
# a barrier costs 0x540 instead of 0x400, an auto-barrier (hit while in Kakusei) the whole
# gauge 0xFA0 instead of 0x7D0. The barrier-held test 0x1316FE (C -> D) is a recovered constant.
BARRIER_PATCHES = (
    (0x1229BE, b"\x00\x00\x00\x00\x00\x00", b"\x00\x00\x00\x00\x00\x00", "rapid-fire toggle on C (was D)"),
    (0x122A02, b"\x00\x00\x00\x00\x00\x00", b"\x00\x00\x00\x00\x00\x00", "barrier start on D (was C)"),
    (0x122A42, b"\x30\x3c\x04\x00", b"\x30\x3c\x05\x40", "barrier cost 0x540 (was 0x400)"),
    (0x122A54, b"\x30\x3c\x07\xd0", b"\x30\x3c\x0f\xa0", "auto-barrier cost 0xFA0 (was 0x7D0)"),
)


def barrier_patches(ps: m68k.PatchSet, p: bytes) -> None:
    for a, old, new, what in BARRIER_PATCHES:
        assert p[a:a + len(old)] == old, f"unexpected code at {a:#x}"
        ps.add(a, new, f"Arrange barrier: {what}")


# Rank base by difficulty and stage (0x1481A4: rank = base[difficulty][stage] + play time +
# power / Kakusei / gems terms; spec/pgm-gameplay.md). The arcade's four 6-byte tables
# (pointers 0x148128 -> 0x148110..) run 16..132; Arrange's (A0A9 pointers 0xBF85D0, same
# routine 0x7D1E90 / 0x7D25C0) are 240 throughout (stage 6 of the lowest difficulty 0, as in
# the arcade), so the rank starts near its 0xF8 cap. The other tables of the rank setup
# (0x148138 / 0x148140 / 0x148148, Arrange 0xBF85E0..) are the same.
RANK_POINTERS = (0x148128, 0xBF85D0, 4)
RANK_TABLE = 6


def rank_patches(state, ps: m68k.PatchSet, p: bytes) -> int:
    """Copy Arrange's rank base tables over the arcade's. Returns the number changed."""
    from ..ps2 import overlay
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    arc, ps2, count = RANK_POINTERS
    assert ov.read(0xBF85E0, 0x18) == p[0x148138:0x148150], "rank class tables"
    n = 0
    for k in range(count):
        a = struct.unpack_from(">I", p, arc + 4 * k)[0]
        b = struct.unpack_from("<I", ov.read(ps2 + 4 * k, 4))[0]
        new = ov.read(b, RANK_TABLE)
        if new != p[a:a + RANK_TABLE]:
            ps.add(a, new, f"Arrange rank base, difficulty {k}")
            n += 1
    return n


# Player template copied into the player record at (re)spawn (0x12275A: 0xA0 bytes from
# 0x12263A). Arrange's (A0A9 0xBFC630, copied by 0x82AFC0) differs at +0x50..+0x57 and
# +0x8F; its copy routine then sets the level +0x76 to 6 (ELF 0x1F71D0 with 3, both players:
# asm/m68k/player_level.s).
PLAYER_TEMPLATE = (0x12263A, 0xBFC630, 0xA0)
TEMPLATE_DIFFS = {0x50, 0x52, 0x54, 0x56, 0x8F}


# Stage 1 turret volley timer (0x16C79C: reload 0x60 - rank term). Arrange's routine (A0A0
# 0x671F50) subtracts the byte pair at 0x8139CE (rank/4) where the arcade's (0x16C7AC and its
# PS2 twin A0AA 0x66FC00) subtracts 0x8139CC (rank/4 + rank/16), so its volleys come less often.
# The MIPS function matcher masks lui-loaded addresses, so it pairs the two as identical and
# the porter never sees the change (the only such reference change among the exact pairs:
# lui-loaded RAM addresses mapped through the learned maps). (68k instruction, arcade address,
# Arrange address, overlay, MIPS lui, PS2 address)
TURRET_RELOAD = (0x16C7AC, 0x8139CC, 0x8139CE, "A0A0", 0x67326C, 0x64228A)


def turret_patches(state, ps: m68k.PatchSet, p: bytes) -> None:
    from ..ps2 import overlay
    at, old, new, ovn, lui, ps2 = TURRET_RELOAD
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
    hi, lo = struct.unpack("<2I", ov.read(lui, 8))
    assert hi == 0x3C010000 | (ps2 + 0x8000) >> 16 and lo & 0xFFFF == ps2 & 0xFFFF, "Arrange turret reload"
    assert p[at:at + 6] == struct.pack(">HI", 0x9079, old), f"unexpected code at {at:#x}"
    ps.add(at + 2, struct.pack(">I", new), "Arrange turret volley timer")


# Stage 1 boss arms, volley mode 8 (0x18AE54: `move.w #7,$54(a6)`, the shots per burst and
# their reload): Arrange's routine (A0A0 0x6A6DD0) fires 5 per burst, so its arms return to
# idle (mode 0) sooner; the porter refuses that routine (its bullet callee's flags). (68k
# instruction, arcade count, Arrange count, overlay, MIPS `addiu $a0,$zero,n`)
BOSS_ARM_SHOTS = (0x18AE68, 7, 5, "A0A0", 0x6A6F08)


# Arrange immediates in routines the porter has no twin for: (68k immediate word, arcade
# value, Arrange value, overlay, MIPS `addiu $r,$zero,n` of the Arrange twin). Stage 2 task 3
# init (0x191958, A0A1 0x652510; its run 0x1919D6 fires 32-shot rings): reload 16 frames
# instead of 8 (+2) and 7 rings instead of 5 (+0xA).
TASK_IMMEDIATES = ((0x19195A, 8, 0x10, "A0A1", 0x65251C), (0x19196C, 5, 7, "A0A1", 0x65252C),
                   # stage 4 enemy states 1 and 3 (runs 0x1B94F6 = A0A3 0x6FDC30, 0x1B9628 =
                   # 0x700600): the y bands of the random target angle are 0x4300 / 0x4500 and
                   # 0x42C0 / 0x4540 on the PS2 (both modes; the PGM ROM has 0x3C00 / 0x4C00 and
                   # 0x3A00 / 0x4E00), so the porter, comparing the two PS2 modes, kept the ROM's
                   (0x1B953E, 0x3C00, 0x4300, "A0A3", 0x6FE4D0), (0x1B9542, 0x4C00, 0x4500, "A0A3", 0x6FE520),
                   (0x1B964C, 0x3A00, 0x42C0, "A0A3", 0x700870), (0x1B9650, 0x4E00, 0x4540, "A0A3", 0x7008C0),
                   # task inits too short to anchor (they fall into their run routine, which the
                   # porter anchors; it left these "not aligned to ROM"): found before the run's
                   # 68k address, the Arrange twin loading the new value. Stage 3 (A0A2 0x651050)
                   # and stage 4 (A0A3 0x6792F0: the volley task's reload, 0x67E580, 0x67FBA0)
                   (0x1966A6, 6, 1, "A0A2", 0x65105C), (0x19C914, 0x10, 0xA, "A0A3", 0x6792F4),
                   (0x19CBC8, 0x10, 0x15, "A0A3", 0x67E598), (0x19CC8C, 0x1C, 0x14, "A0A3", 0x67FBA4),
                   (0x19CC98, 6, 0xB, "A0A3", 0x67FC18))
# moveq counts (the value is the opcode's low byte): (68k instruction, arcade, Arrange, overlay,
# MIPS `addiu $r,$zero,n`). Stage 5 task 4's run (0x1BD8D8, A0A4 0x65FFA0, refused for its
# operands): 0x18 + 1 shots per volley instead of 9.
TASK_MOVEQ = ((0x1BD93C, 8, 0x18, "A0A4", 0x6607F0),)
# The same init's adjustment of 0x81B55A / 0x81B562 (to 0xE) is gone in Arrange: branch from
# its first instruction to the `jsr` after it. (68k instruction, target, first instruction)
TASK_SKIPS = ((0x19198E, 0x1919AE, bytes.fromhex("000000000000")),)
# cmpi.w bounds found by the immediate audit (spec/arrange-diff.md), both PS2 modes differing
# from the ROM: (68k instruction, arcade, Arrange, overlay, MIPS `addiu $r,$rs,-n`). Stage 6
# run 0x1A5D58 (A0A5 0x6C2E20) steps $20(a4) by 4 through all 12 longs at 0x1A5DD2, not 6.
TASK_COMPARES = ((0x1A5DC2, 0x18, 0x30, "A0A5", 0x6C3A28),)
# The same, where the Arrange twin computes the bound as a label distance (MIPS: lui/addiu,
# addiu k, lui/addiu, subu). Stage 5 run 0x1BC33E (A0A4 0x64AA50) ends its $132(a6) sweep at
# 0xB4, the end of its 0x60..0xB4 loop, instead of running on to 0x114.
TASK_COMPARE_SPANS = ((0x1BC358, 0x114, 0xB4, "A0A4", 0x64AD94),)


def _span(ov, at: int) -> int:
    w = struct.unpack("<6I", ov.read(at, 24))
    assert [x >> 26 for x in w[:5]] == [0xF, 9, 9, 0xF, 9] and w[5] & 0xFC0007FF == 0x23, f"label span at {at:#x}"
    s = lambda x: (x & 0xFFFF) - ((x & 0x8000) << 1)
    return ((w[0] & 0xFFFF) << 16) + s(w[1]) + s(w[2]) - ((w[3] & 0xFFFF) << 16) - s(w[4])


def task_patches(state, ps: m68k.PatchSet, p: bytes) -> None:
    from ..ps2 import overlay
    for at, old, new, ovn, mips in TASK_IMMEDIATES:
        ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
        w = struct.unpack("<I", ov.read(mips, 4))[0]
        assert w & 0xFFE0FFFF == 0x24000000 | new, f"Arrange immediate at {mips:#x}"
        assert struct.unpack_from(">H", p, at)[0] == old, f"unexpected code at {at:#x}"
        ps.add(at, struct.pack(">H", new), f"Arrange task immediate {old} -> {new} at {at:#x}")
    for at, old, new, ovn, mips in TASK_MOVEQ:
        ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
        assert struct.unpack("<I", ov.read(mips, 4))[0] & 0xFFE0FFFF == 0x24000000 | new, f"Arrange count at {mips:#x}"
        assert p[at] & 0xF1 == 0x70 and p[at + 1] == old, f"unexpected code at {at:#x}"
        ps.add(at + 1, bytes([new]), f"Arrange moveq {old} -> {new} at {at:#x}")
    for at, to, ins in TASK_SKIPS:
        assert p[at:at + len(ins)] == ins and 2 <= to - at - 2 < 0x80, f"unexpected code at {at:#x}"
        ps.add(at, struct.pack(">BB", 0x60, to - at - 2), f"Arrange drops {at:#x}..{to:#x}")
    for (at, old, new, ovn, mips), span in [(e, False) for e in TASK_COMPARES] + [(e, True) for e in TASK_COMPARE_SPANS]:
        ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
        if span:
            assert _span(ov, mips) == new, f"Arrange bound at {mips:#x}"
        else:
            assert struct.unpack("<I", ov.read(mips, 4))[0] & 0xFC00FFFF == 0x24000000 | (-new & 0xFFFF), f"Arrange bound at {mips:#x}"
        assert p[at] == 0x0C and p[at + 1] & 0xC0 == 0x40 and struct.unpack_from(">H", p, at + 2)[0] == old, f"unexpected code at {at:#x}"
        ps.add(at + 2, struct.pack(">H", new), f"Arrange compare {old:#x} -> {new:#x} at {at:#x}")


def boss_arm_patches(state, ps: m68k.PatchSet, p: bytes) -> None:
    from ..ps2 import overlay
    at, old, new, ovn, mips = BOSS_ARM_SHOTS
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
    assert struct.unpack("<I", ov.read(mips, 4))[0] == 0x24040000 | new, "Arrange boss arm shots"
    assert p[at:at + 4] == struct.pack(">HH", 0x3D7C, old), f"unexpected code at {at:#x}"
    ps.add(at + 2, struct.pack(">H", new), "Arrange stage 1 boss arm shots per burst")


# Hitbox setups that overrun their table (`move.w #n,4(a5); rts`, then `lea table(pc),a0; nop`
# and 0x14F118, which copies n+1 entries): n is the number of entries, so the arcade builds one
# more hitbox from the code after the table (an active box of garbage size; in stage 2 it also
# lands in an enemy record's owner/hit-mark bytes). The PS2 copies of these tables (stage and
# arcade-stage overlays) are followed by zeros, an empty box: each table moves to a copy with
# an empty extra entry (an active entry reads 0x14 bytes). (lea site, table, entries of 0x20
# bytes, stage overlay, PS2 copy)
HITBOX_TABLES = (
    (0x1AF3D4, 0x1AF498, 12, "A0A0", 0x6FE090),  # stage 1 mid-boss
    (0x1B1C52, 0x1B1D50, 10, "A0A1", 0x6DDAE0),  # stage 2
    (0x1B54DA, 0x1B55C0, 8, "A0A2", 0x6CC4D0),   # stage 3
    (0x1B7DC4, 0x1B7EC2, 11, "A0A3", 0x7093F0),  # stage 4
)
HITBOX_EMPTY = 0x14


def hitbox_table_patches(state, ps: m68k.PatchSet, p: bytes) -> None:
    from ..ps2 import overlay
    for at, table, n, ovn, ps2 in HITBOX_TABLES:
        size = n * 0x20
        assert p[at - 8:at] == struct.pack(">HHHH", 0x3B7C, n, 4, 0x4E75), f"hitbox count at {at:#x}"
        assert p[at:at + 6] == b"\x41\xfa" + struct.pack(">h", table - at - 2) + b"\x4e\x71", f"code at {at:#x}"
        ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
        data = p[table:table + size] + bytes(HITBOX_EMPTY)
        assert ov.read(ps2, len(data)) == data, f"PS2 hitbox table {ps2:#x}"
        addr = state.alloc_p(len(data), align=2, note=f"hitbox table {table:#x}")
        ps.add(addr, data, f"hitbox table {table:#x} with an empty entry {n + 1}")
        ps.add(at, b"\x41\xf9" + struct.pack(">I", addr), f"hitbox table {table:#x}")


# Small enemy with a 12-byte animation table (init 0x1654F4: hitbox entry, then 8 object words
# whose long at +4 is the animation pointer; 0x165584 steps one entry (sprite, size, word) per
# reload and releases the enemy at the 0xFFFF end). The PS2 data (A0A9, both modes) starts the
# record with the first frame (the hitbox entry's size, sprite and word) and the animation at
# the second entry: 31 steps instead of 32, so the enemy leaves 2 frames sooner. (hitbox entry,
# object words, animation table, entries; PS2 entry, words, table)
ANIM_START = (0x165564, 0x165554, 0x16561E, 32, 0xC13400, 0xC133F0, 0xC13420)


def anim_start_patches(state, ps: m68k.PatchSet, p: bytes) -> None:
    from ..ps2 import overlay
    ent, words, table, n, pe, pw, pt = ANIM_START
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    first = p[table:table + 12]
    assert struct.unpack_from(">I", p, words + 4)[0] == table and p[table + 12 * n:table + 12 * n + 2] == b"\xff\xff"
    # the PS2 words carry a loader-filled pointer (0xFFFFFFFF in the file)
    assert ov.read(pw, 16) == p[words:words + 4] + b"\xff" * 4 + p[words + 8:words + 16], "PS2 object words"
    e = ov.read(pe, 0x20)
    assert e[:2] + e[2:6] + e[12:] == p[ent:ent + 2] + first[4:8] + p[ent + 12:ent + 0x20] and e[10:12] == first[10:12], \
        "PS2 hitbox entry"
    t = ov.read(pt, 12 * (n - 1) + 2)
    assert all(t[12 * k + 4:12 * k + 12] == p[table + 12 * (k + 1) + 4:table + 12 * (k + 2)] for k in range(n - 1)) \
        and t[-2:] == b"\xff\xff", "PS2 animation table"
    ps.add(ent + 2, first[4:8] + first[0:4] + first[10:12], "small enemy hitbox entry: first animation frame")
    ps.add(words + 4, struct.pack(">I", table + 12), "small enemy animation from its second frame")


def template_patches(state, ps: m68k.PatchSet, p: bytes) -> int:
    from ..ps2 import overlay
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    arc, ps2, n = PLAYER_TEMPLATE
    new = ov.read(ps2, n)
    diff = {k for k in range(n) if new[k] != p[arc + k]}
    assert diff <= TEMPLATE_DIFFS, f"player template differs at {sorted(hex(k) for k in diff)}"
    ps.add(arc, new, "Arrange player template")
    return len(diff)


# Player character records (read by 0x123BAA and 0x124668: +0x10/+0x14 longs, normal and
# focused speed, +0x2C..+0x2F, +0x66). Arrange's characters take the arcade slots by their
# +0x4A value: Chihiro 0 (Ageha's), Black 2 (Tateha's). The Arrange overlay (A0A9) holds the
# same two tables (pointers as PS2 addresses, records of 0x20 bytes whose first 16 are the
# 68k record): (arcade table, PS2 table, entries).
CHARACTER_TABLES = ((0x127832, 0xBFC738, 2), (0x12785A, 0xBFC7C0, 4))
CHARACTER_RECORD = 16


def character_patches(state, ps: m68k.PatchSet, p: bytes) -> int:
    """Copy Chihiro's and Black's records over Ageha's and Tateha's (movement speeds; the
    other fields are the same in both games). Returns the number of records."""
    from ..ps2 import overlay
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    n = 0
    for arc, ps2, count in CHARACTER_TABLES:
        for k in range(count):
            a = struct.unpack_from(">I", p, arc + 4 * k)[0]
            b = struct.unpack_from("<I", ov.read(ps2 + 4 * k, 4))[0]
            rec = ov.read(b, CHARACTER_RECORD)
            # same layout: identical position/hitbox longs and flags, other speeds
            assert rec[:8] == p[a:a + 8] and rec[10:] == p[a + 10:a + CHARACTER_RECORD], f"record {a:#x}"
            if rec != p[a:a + CHARACTER_RECORD]:
                ps.add(a, rec, f"Arrange character record {k} of table {arc:#x} (speeds)")
                n += 1
    return n


def layer_table_lines(layers) -> str:
    return "".join(f"        dc.l    ${b:x}, ${c:x}\n        dc.w    {n}\n" for b, c, n in layers)


def expand_asm(text: str, state=None) -> str:
    """Fill generated sections of an asm source (`; @layer_table`: sprite builder layers)."""
    if "; @layer_table" in text:
        from .layout import ORIG_LAYERS
        layers = getattr(state, "sprite_layers", None) or ORIG_LAYERS
        text = text.replace("; @layer_table\n", layer_table_lines(layers))
    return text


def install_routines(state, ps: m68k.PatchSet) -> int:
    """Assemble every asm/m68k/*.s into free program ROM and hook it in.

    A source file declares what it replaces with header comments:
        ; replaces 0x1234AB      -> `jmp new.l` written over the first 6 bytes of that routine
        ; hook 0x1234AB jsr      -> `jsr new.l` written at that address (6 bytes)
        ; entry name             -> label used as the hook target (default: first label)
        ; requires flag          -> only installed when the build state has that flag set
    Labels of the form `orig_XXXXXX` resolve to that 68k address so new code can call the
    original routines.
    """
    n = 0
    for src in sorted(ASM_DIR.glob("*.s")):
        text = src.read_text()
        replaces = [int(x, 16) for x in re.findall(r"^;\s*replaces\s+(0x[0-9a-fA-F]+)", text, re.M)]
        hooks = [(int(x, 16), kind) for x, kind in re.findall(r"^;\s*hook\s+(0x[0-9a-fA-F]+)\s+(jsr|jmp)", text, re.M)]
        entry = re.findall(r"^;\s*entry\s+(\w+)", text, re.M)
        requires = re.findall(r"^;\s*requires\s+(\w+)", text, re.M)
        if not all(getattr(state, r, False) for r in requires):
            continue
        text = expand_asm(text, state)
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
    held = []
    seen: dict[tuple[int, int], int] = {}
    conflicts = elf = 0
    import os
    subset = os.environ.get("ESPGAL_PORT_SUBSET")       # "lo:hi" index range (bisecting aid)
    lo, hi = (int(x) for x in subset.split(":")) if subset else (0, 1 << 30)
    for idx, pt in enumerate(json.loads(cache.read_text())):
        if not (lo <= idx < hi):
            continue
        # Record-stride / layout changes (e.g. a 0x24-byte object record becoming 0x2C bytes)
        # must be applied to every pool walk at once, not per routine: keep them aside.
        if pt["kind"] == "poolcount" or pt["mnem"].split(".")[0] in ("lea", "adda", "suba") \
                or (pt["old"] & 0xFFFF, pt["new"] & 0xFFFF) in LAYOUT_CHANGES or _record_multiple(pt["old"], pt["new"]):
            layout.append(pt)
            continue
        if pt["rom_addr"] in HELD_BACK:
            held.append(pt)
            continue
        drop = os.environ.get("ESPGAL_ELF_DROP")       # bisecting: only these addresses (hex, comma)
        if pt["arrange_fn"] < ELF_END and not os.environ.get("ESPGAL_KEEP_ELF_CONSTANTS") \
                and (drop is None or f"{pt['rom_addr']:x}" in drop.split(",")):
            # recovered from an ELF function: the ELF serves both PS2 modes, so the difference
            # is the PS2 port's, not Arrange's (0x1473D6 set the one-player flag 0x813998 to
            # 0 where both PS2 modes have 1)
            elf += 1
            continue
        key = (pt["rom_addr"], pt["offset"])
        if key in seen:
            if seen[key] != pt["new"]:
                conflicts += 1
            continue
        seen[key] = pt["new"]
        ps.add(pt["rom_addr"], _encode_imm(pt, p), f"arrange constant {pt['old']:#x}->{pt['new']:#x} in {pt['mnem']} (routine {pt['routine']:#x})")
        n += 1
    if conflicts:
        state.note(f"code: WARNING {conflicts} conflicting recovered constants ignored (first value kept)")
    if elf:
        state.note(f"code: {elf} constants recovered from ELF functions (shared by both PS2 modes) left out")
    if held:
        state.note(f"code: {len(held)} constants held back until their Arrange routines are ported: "
                   + ", ".join(f"{x['rom_addr']:#x} ({HELD_BACK[x['rom_addr']]})" for x in held))
    if layout:
        state.note(f"code: {len(layout)} layout (record stride) changes held back: "
                   + ", ".join(f"{x['rom_addr']:#x}:{x['old']:#x}->{x['new']:#x}" for x in layout))
    return n


ELF_END = 0x300000           # PS2 ELF code below this; the mode overlays load above it
LAYOUT_CHANGES = {(0x24, 0x2C), (0xFFDC, 0xFFD4), (0x22, 0x2A), (0xFFDE, 0xFFD6)}


def _record_multiple(old: int, new: int) -> bool:
    """k * 0x24 -> k * 0x2C (an offset/count over enemy records): part of the record layout."""
    o, n = _s16(old), _s16(new)
    return o != 0 and o % 0x24 == 0 and n == o // 0x24 * 0x2C


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v

# Recovered constants that refer to Arrange-only code or data and break the arcade routines on
# their own; they land together with the ported Arrange routines (found by bisecting with
# ESPGAL_PORT_SUBSET and long scripted MAME runs).
HELD_BACK = {
    # stage 1 boss sequence spawns object type 4 instead of 1 (0x13AF48); arcade type 4 is a
    # different object, the boss sequence never finishes and the stage freezes at the boss.
    0x1B01A8: "Arrange object type in the stage 1 boss sequence",
}


def port_arrange(state, ps: m68k.PatchSet) -> int:
    """Install the recovered Arrange routines (detrans/porter.py) over their 68k originals.

    Only units whose arcade twin de-translates exactly to a self-contained 68k region are used.
    ESPGAL_UNIT_SUBSET="lo:hi" limits the (ordered) unit list for bisecting;
    ESPGAL_VERIFIED_SUBSET="lo:hi[,lo:hi]" keeps only those PS2-verified units (sorted by address);
    ESPGAL_UNIT_EXCLUDE="ov:fn,..." drops the named units."""
    import os
    from ..detrans import porter
    from ..detrans.port import STAGE_PAIRS
    sel = getattr(state, "port_overlays", None)
    if not sel:
        return 0
    pairs = [("A0A8", "A0A9")] + list(STAGE_PAIRS)
    pairs = [x for x in pairs if sel == ["all"] or x[1] in sel]
    rec = bool(getattr(state, "relocate_pools", False))
    d = porter.port_all(pairs, state.work / "ps2" / "detrans" / ("ported_units_rec.json" if rec else "ported_units.json"),
                        arrange_records=rec)
    units = d["units"]
    from . import layout
    assert all(int(v, 16) + 4 <= layout.POPUP_LIST for v in d["newvars"].values()), \
        "Arrange-only variables reach the score popup list (layout.POPUP_LIST)"
    subset = os.environ.get("ESPGAL_UNIT_SUBSET")
    if subset:
        lo, hi = (int(x) for x in subset.split(":"))
        units = units[lo:hi]
    drop = {x for x in os.environ.get("ESPGAL_UNIT_EXCLUDE", "").split(",") if x}   # bisecting by name
    if drop:
        units = [u for u in units if f"{u['overlay']}:{u['fn']:#x}" not in drop]
    vsub = os.environ.get("ESPGAL_VERIFIED_SUBSET")      # bisecting the PS2-verified units
    if vsub is not None:
        ver = sorted((u for u in units if u.get("trust") == "PS2-verified"), key=lambda u: u["orig"])
        keep = {id(u) for k in vsub.split(",") if k for u in ver[int(k.split(":")[0]):int(k.split(":")[1])]}
        units = [u for u in units if u.get("trust") != "PS2-verified" or id(u) in keep]
    import re

    def norm(t: str) -> str:            # the same code with other label names is the same unit
        names = {}
        return re.sub(r"\b(?:L|F)_[0-9a-f]+\b", lambda m: names.setdefault(m.group(0), f"L{len(names)}"), t)
    by_orig: dict[int, set] = {}
    for u in units:
        by_orig.setdefault(u["orig"], set()).add(norm(u["text"]))
    conflicting = {o for o, ts in by_orig.items() if len(ts) > 1}
    for o in sorted(conflicting):
        names = [f"{u['overlay']}:{u['fn']:#x}" for u in units if u["orig"] == o]
        state.note(f"code: WARNING {', '.join(names)} port {o:#x} differently (per-stage variants of a "
                   f"shared routine); original kept")
    done: dict[int, str] = {}
    n = 0
    listed = _listed(state.work)
    for u in units:
        if u["orig"] in done or u["orig"] in conflicting:
            continue
        text = _mark_sprites(u["text"])
        early = _hidden_start(bytes(state.p_rom), u, text, listed)
        if early:
            text = early[1]
        if getattr(state, "arrange_bullets", False):
            # the item pool moved with the 800 item slots (layout.arrange_items); the layout
            # stage only relocates the original code's references
            text = re.sub(r"\$([0-9a-f]{6})(\.l\b)", lambda m: _item_pool_ref(int(m.group(1), 16)) + m.group(2), text)
        code, _ = asm68k.assemble(text, 0x200000)
        addr = state.alloc_p(len(code), align=2, note=f"arrange {u['overlay']}:{u['fn']:#x}")
        code, syms = asm68k.assemble(text, addr)
        # converted sprite loads (`move.l #code,d2`): the gfx stage redirects redrawn sprites there
        state.sprite_sites += [v + (0 if k.startswith("S_sprd") else 2) for k, v in syms.items()
                               if k.startswith("S_spr")]
        ps.add(addr, code, f"ported Arrange {u['overlay']}:{u['fn']:#x}")
        # (the unit holds the routine and the callees ported with it, in address order: enter the
        # routine's own label, not the start of the text)
        if early:
            # the routine starts one instruction before the anchor (the listing hid its start):
            # enter the port there, and past its first instruction from the anchor
            ps.jmp(u["orig"] - early[0], syms[f"F_{u['fn']:x}"],
                   f"ported Arrange {u['overlay']}:{u['fn']:#x} replaces {u['orig'] - early[0]:#x}")
            ps.jmp(u["orig"], syms["S_after_first"], f"ported Arrange {u['overlay']}:{u['fn']:#x} entry {u['orig']:#x}")
        else:
            ps.jmp(u["orig"], syms[f"F_{u['fn']:x}"],
                   f"ported Arrange {u['overlay']}:{u['fn']:#x} replaces {u['orig']:#x}")
        for e, label in u.get("entries", []):
            # other code enters the replaced region here: continue in the ported code
            ps.jmp(e, syms[label], f"ported Arrange {u['overlay']}:{u['fn']:#x} entry {e:#x}")
        done[u["orig"]] = u["text"]
        n += 1
    state.note(f"code: {n} recovered Arrange routines ported ({len(d['failed'])} not portable yet, "
               f"{len(d['newvars'])} Arrange-only variables at {porter.NEWVAR_BASE:#x})")
    if getattr(state, "relocate_pools", False):
        replaced = [(u["orig"], u["end"]) for u in units if u["orig"] in done]
        from ..pgm import rom as rom_mod
        orig = bytes(rom_mod.RomSet(state.work).p_rom_68k())
        k, probs = kept_record_sites(bytes(state.p_rom), ps, d.get("kept_shifts", []), replaced,
                                     _extra_record_sites(state), orig)
        state.note(f"code: {k} record displacements of kept arcade routines converted by their Arrange twins"
                   + (f" ({len(probs)} problems: {probs[:3]})" if probs else ""))
    return n


# Record displacements of kept arcade routines the porter cannot pair (no anchor for the twin):
# 68k instruction -> (displacement, register, 0x2C value, overlay, MIPS address of the Arrange
# store, its two words). 0x1B16C2 `move.w #1,$e0(a6)` (stage 1 mid-boss, called on its timed
# exit from 0x1B0CA6) = A0A0 0x6FA0C0, which stores the word at a6+0x110 as two bytes.
EXTRA_RECORD_SITES = {0x1B16C2: (0xE0, "a6", 0x110, "A0A0", 0x6FA0CC, (0xA0800110, 0xA0830111))}


def _extra_record_sites(state) -> dict:
    from ..ps2 import overlay
    out = {}
    for pc, (dd, reg, nd, ovn, at, words) in EXTRA_RECORD_SITES.items():
        ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn)
        assert struct.unpack("<2I", ov.read(at, 8)) == words, f"Arrange store for {pc:#x}"
        out[f"{pc:#x}"] = [[dd, reg, nd]]
    return out


def kept_record_sites(p: bytes, ps: m68k.PatchSet, kept: list[dict], replaced: list[tuple[int, int]],
                      extra: dict | None = None, orig: bytes | None = None):
    """Convert the arcade record displacements (0x24 records) left in routines that stay arcade
    code, where the routine's Arrange twin uses the 0x2C offset for the same register and
    displacement (porter shift pairs; the layout stage only converts the sites its logged runs
    and static pass reach). Sites inside replaced regions are skipped (dead code), and so are
    instructions under another patch of this stage (a hook jump at a replaced routine's start)
    and displacements the layout stage already changed (`orig`, the input ROM: one it converted
    may now equal another field's arcade offset)."""
    from .. import records
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
    taken = [(q.offset, q.offset + len(q.data)) for q in ps.patches]
    sites: dict[str, list] = {}
    for e in kept:
        pairs = {(reg, va): vb for reg, va, vb in e["pairs"]}
        for i in md.disasm(p[e["lo"]:e["hi"]], e["lo"]):
            if any(lo <= i.address < hi for lo, hi in replaced) \
                    or any(lo < i.address + i.size and i.address < hi for lo, hi in taken):
                continue
            for off, reg, dd in records.displacement_offsets(p[i.address:i.address + 10], i.address):
                if orig is not None and orig[i.address + off:i.address + off + 2] != p[i.address + off:i.address + off + 2]:
                    continue
                vb = pairs.get((reg, dd))
                if vb is not None and [dd, reg, vb] not in sites.setdefault(f"{i.address:#x}", []):
                    sites[f"{i.address:#x}"].append([dd, reg, vb])
    sites.update(extra or {})
    return records.patch_sites(p, ps, {k: v for k, v in sites.items() if v})


def _listed(work: Path) -> set[int]:
    """Instruction addresses of the 68k listing (code range)."""
    from ..detrans.verify import load_listing
    return {k.addr for k in load_listing(work / "pgm" / "dis" / "p_rom.lst")}


# Units whose Arrange routine starts several 68k instructions before the anchor (checked by
# hand; the alignment paired none of them): (overlay, routine) -> (68k start, the mnemonics of
# the 68k instructions there, as many as the routine's first instructions they stand for).
# A0A5 0x69A600 = 0x1ABB84: the four byte stores are `move.l #-1,$1e(a5)`, its F_699860 is
# 0x1ABAC6 with the enlarged record offsets.
LEAD_STARTS = {("A0A5", 0x69A600): (0x1ABB84, ("move.l", "jsr", "bsr.w"), 6)}


def _hidden_start(p: bytes, u: dict, text: str, listed: set[int]):
    """(length, text with `S_after_first` after the routine's first instructions) when the 68k
    instructions just before the unit's anchor are the unit's first instructions: the routine
    starts there and the alignment did not pair them (the listing hid the start behind
    misdecoded data, or the encodings differ), so hooking at the anchor would run them twice.
    One instruction: the same encoding; a call of the same routine (`jsr` for the 68k's
    `bsr`); or a first call to another routine where the 68k has a `jsr` the listing hid (the
    Arrange routine calls its own version of the callee, e.g. A0A0 0x6F1930 at 0x1AF61E:
    0x1B11C2 with the enlarged record offsets). Longer leads: LEAD_STARTS."""
    lines = text.splitlines()
    # the routine's first instruction (its label may follow callees ported with it)
    f = next((i for i, l in enumerate(lines) if l == f"F_{u['fn']:x}:"), None)
    k = None if f is None else next((i for i in range(f + 1, len(lines)) if lines[i].startswith("\t")), None)
    if k is None or not all(l.endswith(":") for l in lines[f:k]):
        return None
    o = u["orig"]

    def after(n: int) -> str:
        body = [i for i in range(k, len(lines)) if lines[i].startswith("\t")]
        cut = body[n - 1] + 1
        return "\n".join(lines[:cut] + ["S_after_first:"] + lines[cut:]) + "\n"
    lead = LEAD_STARTS.get((u["overlay"], u["fn"]))
    if lead:
        start, mnems, n = lead
        md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
        ins = list(md.disasm(p[start:o], start))
        assert [i.mnemonic for i in ins] == list(mnems) and ins[-1].address + ins[-1].size == o, \
            f"68k before {o:#x}"
        return o - start, after(n)
    first = lines[k].split(";")[0]
    m = re.match(r"\s*jsr\s+\$([0-9a-f]+)\.l\s*$", first)
    if m:
        # the 68k's `bsr.w X` / `bsr.b X` right before the anchor
        x = int(m.group(1), 16)
        if p[o - 4:o - 2] == b"\x61\x00" and o - 2 + struct.unpack(">h", p[o - 2:o])[0] == x:
            return 4, after(1)
        if p[o - 2] == 0x61 and p[o - 1] not in (0, 0xFF) and o + struct.unpack("b", p[o - 1:o])[0] == x:
            return 2, after(1)
    if re.match(r"\s*jsr\s", first) and p[o - 6:o - 4] == b"\x4e\xb9" and o - 6 not in listed \
            and p[o:o + 6] != p[o - 6:o]:
        return 6, after(1)
    if re.search(r"\b[LF]_[0-9a-f]+\b|\bS_", first):
        return None
    try:
        code, _ = asm68k.assemble(first + "\n", 0x200000)
    except Exception:  # noqa: BLE001
        return None
    if not code or p[o - len(code):o] != code or p[o:o + len(code)] == code:
        return None
    return len(code), after(1)


def _item_pool_ref(v: int) -> str:
    from . import layout
    lo = layout.ITEM_POOL_OLD
    if lo <= v < lo + layout.ARCADE_ITEMS * layout.ITEM_RECORD:
        v = layout.ITEM_POOL + (v - lo)
    return f"${v:x}"


def _mark_sprites(text: str) -> str:
    """A label before every sprite code the porter wrote: `S_sprN` on a converted load
    (sprite_ids.replace), `S_sprdN` on a word of a table it emits."""
    from ..detrans.sprite_ids import SPRITE_MARK
    out, n = [], 0
    for line in text.splitlines():
        if line.startswith("\t") and f"; {SPRITE_MARK} $" in line:
            if line.lstrip().startswith("move.l\t#$"):
                out.append(f"S_spr{n}:")        # (the long is the instruction's immediate)
                n += 1
            elif line.lstrip().startswith("dc.l\t$"):
                out.append(f"S_sprd{n}:")       # (a table word)
                n += 1
        out.append(line)
    return "\n".join(out) + "\n"


def test_hooks(state, ps: m68k.PatchSet, p: bytes) -> None:
    """Test-only build hooks (environment variables, never set for a release build).
    ESPGAL_FORCE_STAGE=n: every stage start (0x147298, stage index in d0) uses stage n."""
    import os
    st = os.environ.get("ESPGAL_FORCE_STAGE")
    if st is None:
        return
    assert p[0x147298:0x14729a] == b"\x2f\x00", "unexpected stage-set routine"
    src = f"force_stage:\n\tmoveq\t#{int(st)},d0\n\tmove.l\td0,-(a7)\n\tjmp\t$14729a.l\n"
    code, _ = asm68k.assemble(src, 0x200000)
    addr = state.alloc_p(len(code), align=2, note="test: force stage")
    code, _ = asm68k.assemble(src, addr)
    ps.add(addr, code, "test hook: force stage")
    ps.jmp(0x147298, addr, "test hook: force stage")
    state.note(f"code: TEST BUILD - every stage start forced to stage {st}")


def run(state) -> None:
    ps = m68k.PatchSet()
    p = bytes(state.p_rom)
    test_hooks(state, ps, p)
    extend_patches(ps, p)
    continue_patches(ps, p)
    barrier_patches(ps, p)
    nch = character_patches(state, ps, p)
    state.note(f"code: Arrange rank base tables ({rank_patches(state, ps, p)} of 4 changed)")
    state.note(f"code: Arrange player template ({template_patches(state, ps, p)} bytes changed)")
    turret_patches(state, ps, p)
    boss_arm_patches(state, ps, p)
    task_patches(state, ps, p)
    hitbox_table_patches(state, ps, p)
    anim_start_patches(state, ps, p)
    if getattr(state, "arrange_shots", False):
        from .. import chars
        state.note("code: " + chars.chihiro_shots(state, ps, p))
        if getattr(state, "relocate_pools", False) and getattr(state, "arrange_bullets", False):
            from .. import collision
            state.note("code: " + collision.port_collision(state, ps, p))
            from .. import score
            state.note("code: " + score.port_score(state, ps, p))
            from .. import midboss
            state.note("code: " + midboss.port_midboss(state, ps, p))
    nr = install_routines(state, ps)
    nc = detrans_patches(state, ps, p)
    na = port_arrange(state, ps)
    ps.apply(state.p_rom)
    state.note(f"code: {len(ps.patches)} patches (extends 80M/280M, continues disabled, Arrange barrier start, {nch} character records, {nr} new routines, "
               f"{nc} recovered Arrange constants, {na} ported Arrange routines)")
