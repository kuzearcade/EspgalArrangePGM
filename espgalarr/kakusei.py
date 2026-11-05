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
    for site, helper, target in json.loads(SITES.read_text()):
        if p[site:site + 6] != b"\x4e\xb9" + struct.pack(">I", target) \
                or any(a < site + 6 and site < b for a, b in taken):
            skipped += 1
            continue
        ps.jsr(site, syms[f"F_{helper:x}"], f"Arrange bullet helper {helper:#x} at {site:#x}")
        n += 1
    return f"Arrange bullet helpers: {len(code)} bytes, {n} kept-routine calls redirected ({skipped} replaced or patched)"
