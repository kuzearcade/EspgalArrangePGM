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

The mode core's item-carried turret (68k init 0x17795A, run 0x1779E2; A0A9 0x951230 /
0x952100) changed by hand-placed patches (CORE_TURRET): fire period 0xE (arcade 6, the low
byte of `move.w #$106,$36(a6)`), and its volley (A0A9 0x8CF270) is two shots at -0xC / +0xC
of angle 0x80 at speed $3E - 6 (the arcade: one at $3E - 1, and $3E swung by $3A between 0 and
-0xC every shot; Arrange no longer moves it), plus a centre shot at speed + 6 while 0x813A18
(awakened world) is 2.
"""
from __future__ import annotations

import struct

from .pgm import asm68k, m68k

# Arrange routine -> (68k routine it replaces, its first bytes there; "??": bytes the layout
# stage may change, record offsets)
ROOTS = {
    ("A0A1", "A0AB"): {0x65D3C0: (0x1920AA, "522e????532e????522e????532e????000000000000")},
    ("A0A5", "A0AF"): {0x6D35C0: (0x1A688E, "000000000000")},
}
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
    return f"Arrange restructured volleys: {n} routines and the core turret ({len(code)} bytes)"
