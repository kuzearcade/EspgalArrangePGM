"""Arrange routines the overlay porter refuses or has no twin for: stage 1 mid-boss and boss, power-up drop (spec/arrange-diff.md, enemy timing).

The mid-boss (68k 0x1AF3D4.., Arrange A0A0) is one core record and parts in the hitbox pool
(a6 = the first record). Two of its Arrange routines change the arcade's and are ported here
whole with port_roots; the porter's twin alignment refuses them (translator rewrites of the
arcade twin, an alignment that slips at the routine's first calls):

  * 0x6F1B50 = 68k 0x1B11CA, the per-frame damage routine. Arrange adds, at each of the
    three part kills (core 0x1B12BE, wing parts 0x1B1388 / 0x1B145A), the parts' hit counts
    to the core record's +0x24 (the sum of the parts' +0x24 words, at most 0x40; the parts'
    +0x26 flags ORed into +0x26) before the kill score (0x1783EE), and clears the parts' new
    fields when no kill happened. The ported kill score reads +0x24 for the hit bonus.
  * 0x6F8940 = 68k 0x1B1596, a wing part's death: Arrange also cancels part script 4 when
    it runs (0x13AD2C) and starts script 6.

  * 0x698B20 = 68k 0x18954A, the stage 1 boss's next target (its script 2): Arrange first
    calls 0x189722 (the target near the player, A0A0 0x69B170), whose RNG draws shift the
    random target the arcade code then picks.

Arrange core (A0A9) routines the porter has no twin for:

  * 0x8829E0 = 68k 0x16E14A, the power-up (pool A type 0) drop's motion: speed +0x1A = 9
    (the arcade's 0x0D), angle +0x1B = 0x18 + random / 4 (RNG entry 0x11A48C; the arcade's
    0x18 + random & 0xF from 0x11A548), the counters +0x18/+0x19 = 7, 0 as in the arcade.

Stage 1 turret death (0x16C89C, Arrange A0A0 0x674C00): after the kill score Arrange clears the
record's hit marks (+0x24 long) before marking it dead, so a later kill that sums the marks
(the item drops) does not count them again. Patched in place (TURRET_DEATH): the porter
would pull in the kill score, which score.py ports.

The 68k routines are entered only at their start (called from 0x1B11C2 / the routine
above). Their callees with no learned 68k counterpart are mapped in verify.FUNC_FIXES.
"""
from __future__ import annotations

from .pgm import asm68k, m68k

# Arrange routine -> the 68k routine it replaces and its first bytes there ("??": a byte the
# layout stage may have changed, record offsets)
ROOTS = {0x6F1B50: (0x1B11CA, "@xx??xx??:7321a5139244e291"), 0x6F8940: (0x1B1596, "@10:4539e00ea1707f7f"),
         0x698B20: (0x18954A, "@6:e72b8a2eb6928fe7")}
CORE_ROOTS = {0x8829E0: (0x16E14A, "@8:97f53f41b4fd3c11")}
# turret death tail: `move.w #$8080,(a6) ; move.b $1a(a5),$1d(a6) ; rts` after the kill score
TURRET_DEATH = (0x16C8AC, "@12:312abaaa0ee84b8a")
TURRET_DEATH_ASM = """
turret_death:
        clr.l   $24(a6)
        move.w  #$8080,(a6)
        move.b  $1a(a5),$1d(a6)
        rts
"""


def _matches(b: bytes, head: str) -> bool:
    return m68k.matches(b, head)


def port_midboss(state, ps: m68k.PatchSet, p: bytes) -> str:
    from .detrans import porter
    d = porter.port_roots(list(ROOTS), state.work / "ps2" / "detrans" / "midboss.json", r_name="A0A0", a_name="A0AA")
    assert not d["problems"], f"problems in the ported mid-boss routines: {d['problems']}"
    c = porter.port_roots(list(CORE_ROOTS), state.work / "ps2" / "detrans" / "core_roots.json")
    assert not c["problems"], f"problems in the ported core routines: {c['problems']}"
    t = d["text"] + "\n" + c["text"] + TURRET_DEATH_ASM
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 2, "Arrange mid-boss")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange stage 1 mid-boss and boss, power-up drop (A0A0 0x6F1B50, 0x6F8940, 0x698B20; A0A9 0x8829E0)")
    for fn, (site, head) in {**ROOTS, **CORE_ROOTS}.items():
        assert _matches(p[site:site + m68k.sig_len(head)], head), f"mid-boss routine {site:#x}"
        ps.jmp(site, syms[f"F_{fn:x}"], f"Arrange mid-boss replaces {site:#x}")
    site, head = TURRET_DEATH
    assert _matches(p[site:site + m68k.sig_len(head)], head), f"turret death {site:#x}"
    ps.jmp(site, syms["turret_death"], "Arrange turret death clears the hit marks")
    return f"Arrange mid-boss and core: {len(ROOTS) + len(CORE_ROOTS)} routines ({len(code)} bytes)"
