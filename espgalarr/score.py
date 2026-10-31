"""Arrange scoring (spec/arrange-scoring.md): kill rewards with the hit-count multiplier and
its counter, item scores, Kakusei gold carrying the hit count.

Arrange's kill reward (A0A9 0x817E40 / 0x819140 / 0x81A360 / 0x81B660, the four arcade entries
0x178140 / 0x1783EE / 0x178694 / 0x178956) keeps the arcade's shape but calls one PS2 routine
for both players with the player number as a native argument (0x80CE40 / 0x810540, which call
the score routine 0x80A360 the same way): Arika merged the P1 / P2 copies of the 68k code.
The porter de-translates each with the argument bound (port_roots(specialise=True)).
Differences from the arcade:
  * every score is ten times the arcade's (the enemy's value is kept x10 in a new long);
  * the score of a kill is value * (hits + 10), hits = the Power Shot hits on the enemy
    (record +0x24, at most 0x40), when the enemy was hit by both shot kinds (+0x26 / +0x27);
    a per-player counter (Arrange-only words) keeps the last hit count for later kills while
    it runs down; nothing is scored while Kakusei runs with no gems left;
  * gem drops: hits / 2 (or the counter's hit count / 4) extra gems, the gem kinds by the
    gem count; Kakusei costs fewer gems per kill (3 + stage / 13 + 2 * stage);
  * Kakusei gold remembers the enemy's hit count (bullet +0x32, 0x8B5B00);
  * item scores (gems, gold, arcade 0x1780BA = Arrange 0x814AD0) are x10 and dropped while
    Kakusei runs with no gems.
The counter runs down in Arrange's per-player HUD helper (ELF 0x21AB80, called from the player
routine every frame): its logic part is ported and hooked into the player draw step 0x12397C;
the counter / multiplier display and the score popups (ELF 0x1F3F30 / 0x1F4FC0) need the
Arrange HUD graphics and are not ported yet.
"""
from __future__ import annotations

import re

from .chars import _fix
from .pgm import asm68k, m68k

ROOTS = (0x817E40, 0x819140, 0x81A360, 0x81B660, 0x814AD0, 0x81C880, 0x21AB80)
KILL_SITES = {0x178140: 0x817E40, 0x1783EE: 0x819140, 0x178694: 0x81A360, 0x178956: 0x81B660}
ITEM_SITE = 0x1780BA              # score by the player bits in d1 (gems, gold)
PICKUP_SITE = 0x178C10            # score by the player bits 12 / 11 of d1 (item pickups)
# Item pickups in the item list update 0x16E72E (`moveq #v,d0 ; jsr $178C10`), in the order of
# Arrange's item handlers (A0A9 0x7C1620.. 0x7CC3F0, arcade-mode PS2 0x758680.. 0x761C10):
# ten gold items 10..90 (Arrange: x10, then 10 per count in item +0x14 with a popup), three gems
# of 1 (Arrange: 10, 10, 1). The count in +0x14 is the enemy's hit count, which Arrange's item
# init receives natively (0x7CDEE0 -> 0x7CD080); the arcade item init does not store it, so
# the bonus is not ported (it would read a stale word).
GOLD_ITEMS = {0x16E7A0: 10, 0x16E93E: 15, 0x16EAD6: 20, 0x16EC6E: 30, 0x16EE06: 35, 0x16EF9E: 40,
              0x16F136: 60, 0x16F2CE: 65, 0x16F466: 70, 0x16F5FE: 90}
GEM_ITEMS = {0x16F7DA: 10, 0x16F976: 10, 0x16FB12: 1}
DRAW_SITE = 0x12397C              # player draw step (a6 = player): cmpi.w #1,$82(a6)
MARKS_SITE = 0x17C890             # frame: jsr $123E64, after the enemy update, before the collision
EXPECTED = {0x7C0C90, 0x7CDEE0, 0x1BEFF0, 0x817E30, 0x1F3F30, 0x21AB80,
            "F_80a360_1", "F_80a360_2"}

HUD_ASM = """
score_hud:
        movem.l d0-d2,-(a7)
        jsr     F_21ab80
        movem.l (a7)+,d0-d2
        cmpi.w  #1,$82(a6)
        jmp     $123982
"""

# Hit marks (enemy record +0x24..+0x27, set by the collision, read by the kill reward in the
# enemy's next update): Arrange's enemy routines clear them with +0x23 after the hit flash of
# a surviving enemy (e.g. 0xB009B0: `move.b d4,35(a6) ; move.l d4,36(a6)`, arcade 0x159B60
# `clr.b $23(a6)`, about 100 sites). The port clears every record's marks once per frame
# between the enemy update and the collision, which leaves the same marks to each kill.
MARKS_ASM = """
clear_marks:
        movem.l d0-d1/a0,-(a7)
        lea     ${first:x}.l,a0
        move.w  #{n},d0
        moveq   #0,d1
cm_loop:    move.l  d1,(a0)
        lea     $2c(a0),a0
        dbra    d0,cm_loop
        movem.l (a7)+,d0-d1/a0
        jmp     $123e64
"""


def _drop_unused(t: str) -> str:
    """Remove routines nothing in the text calls any more (F_ labels)."""
    while True:
        parts = re.split(r"(?m)^(?=F_\w+:$)", t)
        used = set(re.findall(r"\b(F_\w+)\b(?!:)", t))
        keep = [p for p in parts if not p.startswith("F_") or p.split(":", 1)[0] in used or p.startswith(ROOT_LABELS)]
        if len(keep) == len(parts):
            return t
        t = "".join(keep)


ROOT_LABELS = tuple(f"F_{r:x}:" for r in ROOTS)


def score_text(work) -> str:
    """The ported routines with the fixes below (68k text, labels F_<Arrange address>)."""
    from .detrans import porter
    from .stages import layout
    sv = layout.SCORE_VARS
    # Arrange-only words PS2 0xC37670..0xC37683 (printed by the de-translator as 0x812936.. in
    # the overlay, 0x813190.. in the ELF helper); the barrier timers PS2 0xC373B0 / 0xC373B2
    nv = {**{0x812936 + k: sv + k for k in range(0x14)}, **{0x813190 + k: sv + k for k in range(0x14)},
          0x812ED0: 0x812676, 0x812ED2: 0x812678}
    d = porter.port_roots(list(ROOTS), work / "ps2" / "detrans" / "score.json", specialise=True, newvars=nv)
    probs = {(int(k, 16) if k.startswith("0x") else k) for k in d["problems"]}
    assert probs <= EXPECTED, f"new problems in the ported scoring: {d['problems']}"
    t = d["text"]
    # the P1 / P2 tests the porter cannot render: Kakusei (player +0x5F) after the score
    # routine, the hit count against the counter's (unsigned, the flags of the cmp)
    t, n = re.subn(r"(\tjsr\tF_80a360_(\d)\n)(?:.*\n)*?L_[0-9a-f]+_\2:\n(?=\tbne\tL_)",
                   lambda m: m.group(1) + f"\ttst.b\t${0x80EEF5 if m.group(2) == '1' else 0x80EF95:x}.l\n", t)
    assert n == 4, f"Kakusei test after the score routine: {n}"
    t, n = re.subn(r"(\tcmp\.w\t\$[0-9a-f]+\.l,d5\n)\ttst\.w\t\$81293c\.l\n(?:.*\n)*?L_80b488_(\d):\n\tbra\tL_80b5d8_\2\n",
                   lambda m: m.group(1) + f"\tbra\tL_80b5d8_{m.group(2)}\n", t)
    assert n == 2, f"hit count compare: {n}"
    # score * hits: an add loop the porter renders as flag tests (PS2 dbra = loop while >= 0)
    t, n = re.subn(r"(L_80c9f0_(\d):\n)(?:.*\n)*?L_80cb18_\2:\n\tsubq\.w\t#1,d4\n\tadd\.l\td5,d0\n",
                   lambda m: m.group(1) + f"\tadd.l\td5,d0\n\tsubq.w\t#1,d4\n\tbpl\tL_80c9f0_{m.group(2)}\n", t)
    assert n == 2, f"multiply loop: {n}"
    # score popups (ELF 0x1F3F30): not ported yet (Arrange HUD graphics)
    t = _fix(t, r"^(\tmoveq\t#0,d4\n)\tjsr\tF_1f3f30\n", r"\1", 2)
    # callees: the score adds (0x817E20 / 0x817E30 jump to them), the gem spawn (Arrange's
    # walks the PS2's 800 item slots, the PGM has the arcade's 100), P2's score record, the
    # bullet-to-gold parameters (0x11B190)
    t = _fix(t, r"\tjsr\t\$170fb8\.l\n", "\tjsr\t$178104.l\n", 4)
    t = _fix(t, r"\tjsr\tF_817e30\n", "\tjsr\t$178122.l\n", 4)
    t = _fix(t, r"\tjsr\tF_7c0c90\n", "\tjsr\t$16e702.l\n", None)
    t = _fix(t, r"^(F_814080:\n\tlea\.l\t)\$80ef36\.l", r"\1$81b596.l")
    t = _fix(t, r"\$c46424\.l", "$81b556.l")
    t = _fix(t, r"\$c46426\.l", "$81b558.l")
    # counter run-down (ELF 0x21AB80): the logic only (the display needs the HUD graphics);
    # the player from a6 (the PS2 tests the HUD record's player byte), the frame counter,
    # the pause tests (0x813A8E bit 7, 0x81E252)
    t = _fix(t, r"^\tmove\.w\t#\$5100,d1\n(?:.*\n)*?\tmove\.w\t#-\$580,d1\n", "")
    t = _fix(t, r"^\ttst\.b\t7\(a5\)\n\tbeq\tL_21af60\n", "\tcmpa.l\t#$80ee96,a6\n\tbeq\tL_21af60\n")
    t = _fix(t, r"^\tmove\.w\t#\$3800,d1\n", "")
    t = _fix(t, r"^\tbne\tL_21b6e8\n\ttst\.w\t\$81e0b0\.l\n", "\tbtst\t#7,$813a8e.l\n\tbne\tL_21b6e8\n\ttst.w\t$81e252.l\n")
    t = _fix(t, r"\$b030f8\.l", "$801164.l")
    t = _fix(t, r"^L_21b6e8:\n(?:.*\n)*?L_21ce58:\n", "L_21b6e8:\nL_21ce58:\n")
    t = _drop_unused(t)
    for bad in ("ARG_", "RET_", "F_1f3f30", "F_7c0c90", "$b030", "$c4", "$7ff"):
        assert bad not in t, f"left in the ported scoring: {bad}"
    t += "".join(f"gold_{a:x}:\n\tmove.l\t#{v * 10},d0\n\tjmp\t$178c10\n" for a, v in GOLD_ITEMS.items())
    n = sum(q.count for q in layout.POOLS)
    assert all(q.new_stride == 0x2C for q in layout.POOLS)
    return t + HUD_ASM + MARKS_ASM.format(first=layout.FREE_BASE + 0x24, n=n - 1)


def port_score(state, ps: m68k.PatchSet, p: bytes) -> str:
    t = score_text(state.work)
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 4, "Arrange scoring")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange scoring (ported from A0A9 0x817E40..)")
    for site, fn in KILL_SITES.items():
        assert p[site:site + 6] == bytes.fromhex("000000000000"), f"kill reward entry {site:#x}"
        ps.jmp(site, syms[f"F_{fn:x}"], f"Arrange kill reward replaces {site:#x}")
    assert p[ITEM_SITE:ITEM_SITE + 6] == bytes.fromhex("000000000000"), "item score entry"
    ps.jmp(ITEM_SITE, syms["F_814ad0"], "Arrange item score replaces 0x1780BA")
    assert p[PICKUP_SITE:PICKUP_SITE + 6] == bytes.fromhex("000000000000"), "pickup score entry"
    ps.jmp(PICKUP_SITE, syms["F_81c880"], "Arrange pickup score replaces 0x178C10")
    for a, v in GOLD_ITEMS.items():
        assert p[a:a + 8] == bytes([0x70, v]) + bytes.fromhex("000000000000"), f"gold item score {a:#x}"
        ps.add(a, bytes.fromhex("4eb9") + syms[f"gold_{a:x}"].to_bytes(4, "big") + bytes.fromhex("4e71"),
               f"gold item {v} x10 (Arrange)")
    for a, v in GEM_ITEMS.items():
        assert p[a:a + 8] == bytes.fromhex("0000000000000000"), f"gem score {a:#x}"
        if v != 1:
            ps.add(a, bytes([0x70, v]), f"gem score 1 -> {v} (Arrange)")
    assert p[DRAW_SITE:DRAW_SITE + 6] == bytes.fromhex("000000000000"), "player draw step"
    ps.jmp(DRAW_SITE, syms["score_hud"], "Arrange counter run-down in the player draw step")
    assert p[MARKS_SITE:MARKS_SITE + 6] == bytes.fromhex("000000000000"), "frame call of 0x123E64"
    ps.add(MARKS_SITE + 2, syms["clear_marks"].to_bytes(4, "big"), "enemy hit marks cleared before the collision")
    return f"scoring at {addr:#x}"
