"""Arrange's bullet helpers and their Kakusei extra shots (spec/arrange-diff.md, "Kakusei extra
shots").

Arrange fires enemy volleys through helpers in its mode core (A0A9 0x8B7EE0..0x8E0E30,
porter.KAKUSEI_HELPERS): each fires its shots through the bullet allocators (0x1713EC /
0x17135E / 0x170FB8 / 0x170F36) and, while 0x813A18 is 2 (Kakusei with gems), one more at
speed + 6. Arrange calls them where the arcade calls an allocator directly, so some volleys
also fire more shots outside Kakusei (A0A9 0x8C7520: a second shot at speed + 3).

Ported units port the helpers they call (porter.py never maps them). For the kept arcade
routines the helpers are ported once (porter.port_roots) and the 68k calls where Arrange calls
a helper (data/kakusei_sites.json, tools/kakusei_sites.py) are pointed at them; each site must
still hold its `jsr allocator.l` and no other patch may touch it.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from .pgm import asm68k, m68k

SITES = Path(__file__).parent / "data" / "kakusei_sites.json"
# Calls tools/kakusei_sites.py leaves out (TODO.md item 3), placed by hand: the 68k shot whose
# register set-up is the one the Arrange helper call gets ((site, helper, target), like SITES).
# A0A9 0xAD2120 (fan of the routine A0A9 0xAD6490 = 0x164B8E, entered with a direction in $a0;
# the 68k inlines both directions): a +/-4 pair a step (0x8CAE00) for the arcade's one shot.
# A0A9 0xAA9400 (two inlined copies at 0x169B6C / 0x16A22E, Arrange's one routine picks the
# copy by its $a0): a +/-2 pair (0x8CD040) for each of the arcade's ten shots.
EXTRA_SITES = [(0x164CDA, 0x8CAE00, 0x1713EC), (0x164D30, 0x8CAE00, 0x1713EC)] + [
    (a, 0x8CD040, 0x1713EC) for a in (0x169B86, 0x169B90, 0x169BA4, 0x169BAE, 0x169BBE, 0x169BCE,
                                      0x169BE2, 0x169BEC, 0x169C00, 0x169C0A, 0x16A248, 0x16A252,
                                      0x16A266, 0x16A270, 0x16A280, 0x16A290, 0x16A2A4, 0x16A2AE,
                                      0x16A2C2, 0x16A2CC)] + [
    # Twins the function matcher missed or paired crosswise (TODO item 9, the anchor audit):
    # A0A9 0xA659B0 = 0x150128 (the matcher paired it with the arcade's 0xA56430 = 0x161058, whose
    # Arrange twins are the ported 0x74E5E0 / 0x7529A0 / 0x75D030): its shot through 0x8BF7F0
    (0x15026C, 0x8BF7F0, 0x170FB8),
    # A0A5 0x6E01F0 = the stage 6 type 0x7F run 0x1A777C (offsets: restructured.TYPE_7F_LAYOUT,
    # which leaves these calls): its two shots through 0x8E0E30
    (0x1A77BA, 0x8E0E30, 0x17135E), (0x1A77C4, 0x8E0E30, 0x17135E)]
# Arrange's death bursts the arcade does not have (A0A9 0xA9C280, enemy type 0x96, stage 4: after
# its kill reward 0x3C6 and unless its task's $3D is set, 9 shots of type 0x0B aimed at the
# player (0x118E16) from -0x28 in steps of 0xA, each through the helper 0x8C8940: speeds s, s + 3,
# s + 6, and one more in Kakusei). The site's call (`jsr $17D242.l`, after the reward) becomes a
# call of a stub that makes it, then fires the burst. (site, its bytes, helper)
DEATH_BURSTS = [(0x168F82, "000000000000", 0x8C8940)]
# Arrange's spreads where the 68k inlines one shot (the call's site, its bytes, the helper, the
# angle steps before each helper call). A0A5 0x6BD550 (stage 6 boss sub-task, called 3 times by
# 0x6BEA10; the 68k's inlined copy at 0x1A59F4 fires one shot): 4 shots at +9 / +0x1B / -9 / -0x1B
# through 0x8E08B0 (a trial port of the routine from 0x1A59F4, not an entry, split the boss)
SPREADS = [(0x1A5A26, "000000000000", 0x8E08B0, (9, 0x12, -0x24, -0x12))]


def _spread_asm(helper_addr: int, steps, k: int) -> str:
    t = f"\nspread_{k}:\n"
    for s in steps:
        t += f"        addi.w  #${s & 0xFFFF:x},d1\n        jsr     ${helper_addr:x}.l\n"
    return t + "        rts\n"


def _death_burst_asm(helper_addr: int, k: int) -> str:
    return f"""
death_burst_{k}:
        jsr     $17d242.l
        movem.l d0-d3/d7/a0,-(a7)
        tst.b   $3d(a5)
        bne     db_done_{k}
        jsr     $118e16.l
        bcs     db_done_{k}
        move.l  #$1000b,d0
        move.l  2(a6),d2
        moveq   #0,d3
        addi.w  #$ffd8,d1
        moveq   #8,d7
db_loop_{k}:
        jsr     ${helper_addr:x}.l
        addi.w  #$a,d1
        dbra    d7,db_loop_{k}
db_done_{k}:
        movem.l (a7)+,d0-d3/d7/a0
        rts
"""

# The burst when Kakusei ends (the P1 options' type 0x14 object, 0x1318C8 = A0A9 0x9A0DE0; set up
# by 0x124F2E = ELF 0x20C380 from the layout table 0x12F722 = PS2 0xC07E70): each step of its
# size table (0xC bytes: sprite, +0x06 long, +0x0E word, the option hitbox size written to the
# options entry's +0x0A / +0x0C) grows the options' bullet-cancelling box. Arrange's tables for
# option levels 0..3 have a zero hitbox in every step (level 4's, 0x130AB0, is the arcade's): the
# burst cancels no bullets. (68k table, Arrange's in A0A9, steps)
KAKUSEI_END_SIZES = [(0x12FE50, 0xC085D0, 25), (0x130090, 0xC08810, 25)]


def kakusei_end_burst(state, ps: m68k.PatchSet, p: bytes) -> str:
    from .ps2.overlay import Overlay
    ov = Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    n = 0
    for at, ps2, steps in KAKUSEI_END_SIZES:
        for k in range(steps):
            a, b = at + 0xC * k, ov.read(ps2 + 0xC * k, 0xC)
            # the step's other fields (all but its sprite) must be the arcade's
            assert p[a + 4:a + 10] == b[4:10], f"Kakusei end burst step {a:#x}"
            if p[a + 10:a + 12] != b[10:12]:
                ps.add(a + 10, b[10:12], f"Arrange Kakusei end burst hitbox {a:#x}")
                n += 1
    return f"Arrange Kakusei end burst: {n} hitbox sizes from A0A9 (no bullet cancel at option levels 0..3)"


def port_kakusei(state, ps: m68k.PatchSet, p: bytes) -> str:
    from .detrans import porter
    d = porter.port_roots(sorted(porter.KAKUSEI_HELPERS), state.work / "ps2" / "detrans" / "kakusei_helpers.json",
                          r_name="A0A9", a_name="A0A8")
    assert not d["problems"], f"problems in the ported bullet helpers: {d['problems']}"
    code, _ = asm68k.assemble(d["text"], 0x200000)
    addr = state.alloc_p(len(code), 2, "Arrange bullet helpers")
    code, syms = asm68k.assemble(d["text"], addr)
    ps.add(addr, code, "Arrange bullet helpers (A0A9, Kakusei extra shots)")
    taken = [(x.offset, x.offset + len(x.data)) for x in ps.patches]
    n = skipped = 0
    if DEATH_BURSTS:
        t = "".join(_death_burst_asm(syms[f"F_{h:x}"], k) for k, (_, _, h) in enumerate(DEATH_BURSTS))
        bcode, _ = asm68k.assemble(t, 0x200000)
        baddr = state.alloc_p(len(bcode), 2, "Arrange death bursts")
        bcode, bsyms = asm68k.assemble(t, baddr)
        ps.add(baddr, bcode, "Arrange death bursts (A0A9 0xA9C280)")
        for k, (site, head, _) in enumerate(DEATH_BURSTS):
            assert p[site:site + 6] == bytes.fromhex(head) \
                and not any(a < site + 6 and site < b for a, b in taken), f"death burst site {site:#x}"
            ps.jsr(site, bsyms[f"death_burst_{k}"], f"Arrange death burst at {site:#x}")
        taken = [(x.offset, x.offset + len(x.data)) for x in ps.patches]
    if SPREADS:
        t = "".join(_spread_asm(syms[f"F_{h:x}"], st, k) for k, (_, _, h, st) in enumerate(SPREADS))
        scode, _ = asm68k.assemble(t, 0x200000)
        saddr = state.alloc_p(len(scode), 2, "Arrange spreads")
        scode, ssyms = asm68k.assemble(t, saddr)
        ps.add(saddr, scode, "Arrange spreads for inlined single shots")
        for k, (site, head, _, _) in enumerate(SPREADS):
            assert p[site:site + 6] == bytes.fromhex(head) \
                and not any(a < site + 6 and site < b for a, b in taken), f"spread site {site:#x}"
            ps.jsr(site, ssyms[f"spread_{k}"], f"Arrange spread at {site:#x}")
        taken = [(x.offset, x.offset + len(x.data)) for x in ps.patches]
    for site, helper, target in EXTRA_SITES:
        assert p[site:site + 6] == b"\x4e\xb9" + struct.pack(">I", target) \
            and not any(a < site + 6 and site < b for a, b in taken), f"hand-placed helper site {site:#x}"
    for site, helper, target in json.loads(SITES.read_text()) + [list(x) for x in EXTRA_SITES]:
        if p[site:site + 6] != b"\x4e\xb9" + struct.pack(">I", target) \
                or any(a < site + 6 and site < b for a, b in taken):
            skipped += 1
            continue
        ps.jsr(site, syms[f"F_{helper:x}"], f"Arrange bullet helper {helper:#x} at {site:#x}")
        n += 1
    return (f"Arrange bullet helpers: {len(code)} bytes, {n} kept-routine calls redirected ({skipped} replaced or patched), "
            f"{len(DEATH_BURSTS)} death bursts, {len(SPREADS)} spreads")
