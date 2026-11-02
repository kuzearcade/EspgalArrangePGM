"""Arrange routines whose code changed shape (more shots per volley), so their
constants place nowhere in the 68k routine (spec/arrange-diff.md, "Restructured volleys").

Ported whole from the Arrange code at build time (porter.port_roots) and entered at the 68k run
routine's start:

  * A0A1 0x65D3C0 = 68k 0x1920AA, the stage 2 ring task's run (init 0x192048 falls into it):
    Arrange adds shots to both volleys (the second one fires five per side, not two), speed
    step +9 (arcade +6) between the halves of the first.
  * A0A5 0x6D35C0 = 68k 0x1A688E, a stage 6 task's run (init 0x1A685E falls into it): speed
    0xFFF9 (arcade 0xFFFB), two shots at -4 / +4 around the angle, then seven more on the
    positive side (+0x4B, +6, +6, +0x43, +8, +8, +8); the negative side keeps the arcade's five.
    Its shot helper (A0A5 0x6D2C20) is the 68k's inlined `$298(a6)` gated 0x1713EC call.
  * A0A5 0x6C3FC0 = 68k 0x1A5E2C, the stage 6 boss's spread task run (its init 0x1A5E02 is the
    ported A0A5 0x6C3B30, which jumps here): Arrange fires fixed fans where the arcade fires
    one shot at an angle and speed drawn from the RNG (two 0x11A0BC calls a volley; play 41305
    of the no-input run).

The mode core's item-carried turret (68k init 0x17795A, run 0x1779E2; A0A9 0x951230 /
0x952100) changed by hand-placed patches (CORE_TURRET): fire period 0xE (arcade 6, the low
byte of `move.w #$106,$36(a6)`), and its volley (A0A9 0x8CF270) is two shots at -0xC / +0xC
of angle 0x80 at speed $3E - 6 (the arcade: one at $3E - 1, and $3E swung by $3A between 0 and
-0xC every shot; Arrange no longer moves it), plus a centre shot at speed + 6 while 0x813A18
(awakened world) is 2.

Stage 6 object type 0x7F (TYPE_7F_LAYOUT): its spawners are ported Arrange units that write
Arrange's spawn layout, so the arcade handler's offsets follow Arrange's handler (A0A5 0x6E01F0).
Stage 6 piece type 0x4B (PIECE_TABLE): Arrange's ported spawner makes 20 pieces whose targets
come from a 24-entry table (the arcade's has 16); the table is moved to free ROM.
Stage clear (CLEAR_TEXT): Arrange shows no story text after a stage, so the clear sequence is 27
frames shorter (273 logic frames after stage 1, as on the PS2).
"""
from __future__ import annotations

import struct

from .pgm import asm68k, m68k

# Arrange routine -> (68k routine it replaces, its first bytes there; "??": bytes the layout
# stage may change, record offsets)
ROOTS = {
    ("A0A1", "A0AB"): {0x65D3C0: (0x1920AA, "522e????532e????522e????532e????000000000000")},
    ("A0A5", "A0AF"): {0x6D35C0: (0x1A688E, "000000000000"), 0x6C3FC0: (0x1A5E2C, "000000000000")},
}
# Stage 6 object type 0x7F (68k init 0x1A7728, run 0x1A7778; A0A5 init / run 0x6E01F0): its
# spawners 0x1A6108 / 0x1A61D2 are ported Arrange units that fill the spawn entry in Arrange's
# layout (a long $1C: speed -4 and bullet type 0x24, the flag at $22, a word at $24); Arrange's
# handler reads it there, its timer moved from $1E/$1F to $20/$21 (68k address, old, new)
TYPE_7F_LAYOUT = (
    (0x1A774C, "000000000000", "000000000000"),       # init: move.w #2,$20(a5)
    (0x1A7784, "532d001e", "532d0020"),               # run: subq.b #1,$20(a5)
    (0x1A778A, "000000000000", "000000000000"),       # move.b $21(a5),$20(a5)
    (0x1A7790, "4a2d0020", "4a2d0022"),               # tst.b $22(a5)
    (0x1A77AC, "000000000000", "202d001c4e71"),       # move.l $1c(a5),d0 (moveq #$24 / move.w)
    (0x1A77B2, "3a2d0022", "3a2d0024"),               # move.w $24(a5),d5
)
# Stage 6 piece type 0x4B (run 0x1A78AC): Arrange's spawner (ported, 20 pieces, index $1C)
# reads targets from a 24-entry table (A0A5 0x72FE90, 0x60 bytes) where the arcade's 0x1A7A3C has
# 16, so the data stage's in-place copy stops at 16 and the last pieces read code: the table is
# copied to free ROM and its two `lea $1a7a3c(pc),a0 / nop` readers point there
PIECE_TABLE = (0x1A7A3C, 0x40, "A0A5", 0x72FE90, 0x60, (0x1A78D8, 0x1A78FC))
# Stage clear sequence (68k 0x180C4E, A0A9 0x874EE0), step 5: the arcade types the stage's
# story text (0x18162A, table 0x181718) one character a frame, 28 frames for line 0; Arrange's
# table (A0A9 0xC024F0) has no text for any stage (pointers -1), so its routine (A0A9 0x87A0C0)
# sets up and ends on its first call (PCSX2: step 4 to 6 in one frame). 0x18162A is the ported
# Arrange routine, which reads the 68k table (stage * 8: text pointer, last line): stages 1..4
# point to a one-character blank line instead, which it ends on at once (68k table, stages,
# overlay, Arrange table)
CLEAR_TEXT = (0x181718, 4, "A0A9", 0xC024F0)
# the turret's period (TASK_BYTES form: ROM byte, old, new, overlay, MIPS addiu) and volley
# (68k `move.w $3e(a6),d0` .. the wobble's end, the 0x1713EC call inside)
CORE_TURRET_PERIOD = (0x1779CB, 6, 0xE, "A0A9", 0x952034)
CORE_TURRET_VOLLEY = (0x177A50, 0x177A92, "000000000000", 0x177A66)
CORE_TURRET_ASM = """
core_turret_volley:
        move.w  $3e(a6),d0
        subq.w  #6,d0
        swap    d0
        move.w  #$d,d0
        move.b  #$80,d1
        moveq   #0,d3
        move.l  $2(a6),d2
        subi.b  #$c,d1
        jsr     $1713ec.l
        addi.b  #$18,d1
        jsr     $1713ec.l
        subi.b  #$c,d1
        cmpi.w  #2,$813a18.l
        bne     core_turret_end
        swap    d0
        addq.w  #6,d0
        swap    d0
        jsr     $1713ec.l
core_turret_end:
        jmp     ${end:x}.l
"""


def _matches(b: bytes, head: str) -> bool:
    return all(h == "??" or b[k] == int(h, 16) for k, h in enumerate(head[i:i + 2] for i in range(0, len(head), 2)))


def port_restructured(state, ps: m68k.PatchSet, p: bytes) -> str:
    from .detrans import porter
    from .ps2 import overlay
    texts = []
    for (rn, an), roots in ROOTS.items():
        d = porter.port_roots(list(roots), state.work / "ps2" / "detrans" / f"restructured_{rn.lower()}.json",
                              r_name=rn, a_name=an)
        assert not d["problems"], f"problems in the ported {rn} routines: {d['problems']}"
        texts.append(d["text"])
    at, old, new, ovn, mips = CORE_TURRET_PERIOD
    w = struct.unpack("<I", overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn).read(mips, 4))[0]
    assert w >> 26 == 9 and w & 0xFFFF == new, f"Arrange turret period at {mips:#x}"
    assert p[at] == old, f"unexpected code at {at:#x}"
    ps.add(at, bytes([new]), f"Arrange core turret period {old} -> {new:#x}")
    site, end, head, call = CORE_TURRET_VOLLEY
    assert _matches(p[site:site + len(head) // 2], head) and p[call:call + 6] == bytes.fromhex("000000000000"), \
        f"core turret volley {site:#x}"
    t = "\n".join(texts) + CORE_TURRET_ASM.replace("{end:x}", f"{end:x}")
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 2, "Arrange restructured volleys")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange restructured volleys (A0A1 0x65D3C0; A0A5 0x6D35C0; core turret)")
    n = 0
    for roots in ROOTS.values():
        for fn, (site_, head_) in roots.items():
            assert _matches(p[site_:site_ + len(head_) // 2], head_), f"restructured routine {site_:#x}"
            ps.jmp(site_, syms[f"F_{fn:x}"], f"Arrange {fn:#x} replaces {site_:#x}")
            n += 1
    ps.jmp(site, syms["core_turret_volley"], "Arrange core turret volley")
    at_, n_, ovn_, tab_ = CLEAR_TEXT
    t_ = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn_).read(tab_ + 8, n_ * 8)
    assert all(t_[k:k + 4] == b"\xff" * 4 for k in range(0, n_ * 8, 8)), "Arrange stage clear text table"
    blank = state.alloc_p(2, 2, "stage clear blank text")
    ps.add(blank, b" \0", "stage clear blank text")
    for k in range(1, n_ + 1):
        ptr_ = struct.unpack(">I", p[at_ + 8 * k:at_ + 8 * k + 4])[0]
        assert 0x181718 < ptr_ < 0x182000 and p[ptr_:ptr_ + 7] == b" " * 7, f"stage {k} clear text"
        ps.add(at_ + 8 * k, struct.pack(">IH", blank, 0), f"Arrange stage {k} clear: no story text")
    rom_at, rom_n, ovn_, arr_at, arr_n, leas = PIECE_TABLE
    tab = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn_).read(arr_at, arr_n)
    assert bytes(state.p_rom[rom_at:rom_at + rom_n]) == tab[:rom_n], "piece table: the data stage's copy"
    tab_at = state.alloc_p(arr_n, 2, "Arrange stage 6 piece targets")
    ps.add(tab_at, tab, f"Arrange stage 6 piece targets ({ovn_} {arr_at:#x})")
    for at_ in leas:
        disp = struct.unpack(">h", p[at_ + 2:at_ + 4])[0]
        assert p[at_:at_ + 2] == bytes.fromhex("41fa") and at_ + 2 + disp == rom_at and p[at_ + 4:at_ + 6] == bytes.fromhex("4e71"), \
            f"piece table reader {at_:#x}"
        ps.add(at_, bytes.fromhex("41f9") + struct.pack(">I", tab_at), f"piece table reader {at_:#x}")
    for at_, old_, new_ in TYPE_7F_LAYOUT:
        assert p[at_:at_ + len(old_) // 2] == bytes.fromhex(old_), f"type 0x7F handler at {at_:#x}"
        ps.add(at_, bytes.fromhex(new_), f"Arrange type 0x7F spawn layout at {at_:#x}")
    return f"Arrange restructured volleys: {n} routines and the core turret ({len(code)} bytes)"
