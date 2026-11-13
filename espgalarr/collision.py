"""Arrange collision (spec/arrange-collision.md): player shots, barrier and player against
enemies and bullets.

The frame calls the arcade routine 0x11B1D0 once per player (0x17C8E0..0x17C9A0; a0 shot
pool, a2 barrier records, a4 player, d0 player bit). Arrange's version is 0x8982F0 with the
pieces 0x899D60 / 0x89C600 / 0x8A4060 / 0x8A4720 / 0x8A7D40 / 0x8AA860 / 0x895F20 / 0x8951D0;
it is ported at build time (porter.port_roots) and installed over 0x11B1D0. Differences from
the arcade:
  * shots against enemy hitboxes (0x89C600, both hitbox lists): the screen-rectangle tests are
    gone; a hitbox whose owner (record +0x28, the enemy object) differs from the previous
    hitbox's starts a new group (variable, PS2 gp-0x74D4), which clears every shot's +1 bit 1;
    a shot with +1 bit 5 (the Power Shots) hits one hitbox per group (bit 1 set by the hit);
    a hit ORs 0x2082 into the shot type (the arcade only sets the hit bit 0x80) and marks
    the hitbox (+0x24 count, +0x26 / +0x27 by shot kind);
  * the arcade laser part is gone; the barrier part walks the 800-slot bullet pool
    (0x8A4060 / 0x895F20: barriered bullets are turned into gems or gold) and the enemies
    (0x8A4720 / 0x8A7D40);
  * the player against bullets (0x8AA860) is a loop over the live bullet slots (the arcade's
    unrolled 200-slot check only covered the first 200 slots of the build's 800-slot pool).
The enemy-object allocator 0x14EF0A writes the owner into each hitbox record (+0x28) as
Arrange's does (the arcade records have no such field).
"""
from __future__ import annotations

import os
import re
import struct

from .chars import _fix
from .pgm import asm68k, m68k

ROOT = 0x8982F0
SITE = 0x11B1D0                   # arcade entry called by the frame
EXPECTED = {0x8AA860, 0x899D60, 0x89C600, 0x8982F0}
ALLOC_MARK = 0x14EF40             # hitbox allocator: lea -$2C(a6),a6 / move.w #$8000,(a6) / dbra

# Player against the live bullet slots (Arrange 0x8AA860: the PS2 loops over its live slot
# count, at most 800). Here: the slots from the start, empty ones (word 0 >= 0) skipped
# inline, up to the number of live bullets the bullet walker counted this frame (0x81B552);
# the per-bullet check 0x8951D0 (entered at +2) returns without advancing a6 on a hit.
# (a0 holds the shot pool for the shot loop; a5 is free: the routine reloads it.)
PLAYER_BULLETS = """\tlea.l\t$d00000.l,a6
\tmove.w\t$81b552.l,d5
\tbeq\tpb_done
\tsubq.w\t#1,d5
\tlea.l\t$d0c800.l,a5
pb_scan:\ttst.w\t(a6)
\tbmi\tpb_live
\ttst.w\t$40(a6)
\tbmi\tpb_live1
\ttst.w\t$80(a6)
\tbmi\tpb_live2
\ttst.w\t$c0(a6)
\tbmi\tpb_live3
\tlea.l\t$100(a6),a6
\tcmpa.l\ta5,a6
\tbcs\tpb_scan
pb_done:\trts
pb_live3:\tlea.l\t$40(a6),a6
pb_live2:\tlea.l\t$40(a6),a6
pb_live1:\tlea.l\t$40(a6),a6
pb_live:\tmove.w\t2(a6),d4
\tcmp.w\td4,d0
\tbcs\tpb_miss
\tcmp.w\td1,d4
\tbcs\tpb_miss
\taddq.l\t#2,a6
\tmove.l\ta6,-(a7)
\tjsr\tF_8951d0
\tcmpa.l\t(a7)+,a6
\tbeq\tpb_done
\tsubq.l\t#2,a6
\tdbra\td5,pb_more
\trts
pb_miss:\tlea.l\t$40(a6),a6
\tdbra\td5,pb_more
\trts
pb_more:\tcmpa.l\ta5,a6
\tbcs\tpb_scan
\trts
"""
# Shots against hitboxes, the inner loop (Arrange 0x89D980 / 0x8A1940): for each enemy hitbox it
# walked the shot pool from a0, skipping empty slots, over the 0x81291E live shots, testing the
# new-group word (COLLISION_VARS) twice per shot and re-adding the bias d7 to the shot's
# extents. Shots do not move during the collision (a hit writes only their +0, +1 and +0x18),
# and d7 holds for a whole hitbox list (a hit swaps it out and back), so SHOT_TABLE_ASM builds,
# at most once per list (see SHOT_LOOP_NEW), a table of the live shots in pool order: their record (+4, as the loop's a6)
# and the four biased extents the loop compared. The loop then runs over the table in two
# copies, one per value of the group word (constant for a hitbox): the clear of the shot's +1
# bit 1 and the one-hit-per-group test each stay in their copy. Same tests on the same values
# in the same order, same writes: the shots are the same. Only the dead scratch d4 ends
# differently (the following code writes it before reading it).
SHOT_TABLE_ASM = """
coll_table:
        movem.l d4/d6/a1/a6,-(a7)
        lea     ${table:x}.l,a1
        movea.l a0,a6
        move.w  $81291e.l,d6
        subq.w  #1,d6
ct_scan:
        tst.w   (a6)
        bmi.s   ct_live
        lea     52(a6),a6
        bra.s   ct_scan
ct_live:
        addq.l  #4,a6
        move.l  a6,(a1)+
        move.w  -2(a6),d4
        add.w   d7,d4
        add.w   12(a6),d4
        move.w  d4,(a1)+
        sub.w   14(a6),d4
        move.w  d4,(a1)+
        move.w  (a6),d4
        add.w   d7,d4
        add.w   16(a6),d4
        move.w  d4,(a1)+
        sub.w   18(a6),d4
        sub.w   18(a6),d4
        move.w  d4,(a1)+
        lea     48(a6),a6
        dbra    d6,ct_scan
        movem.l (a7)+,d4/d6/a1/a6
        rts
"""
# the arcade-shaped loop as ported (labels as groups), up to its hit path
SHOT_LOOP_RE = (
    r"\tmovea\.l\ta0,a6\n\tmove\.w\t\$81291e\.l,d6\n\tsubq\.w\t#1,d6\n\tbra\t(?P<loop>L_\w+)\n"
    r"(?P<miss>L_\w+):\n\tlea\.l\t48\(a6\),a6\n\tdbra\td6,(?P=loop)\n\tbra\t(?P<exit>L_\w+)\n"
    r"(?P<empty>L_\w+):\n\tlea\.l\t50\(a6\),a6\n"
    r"(?P=loop):\n\ttst\.w\t\(a6\)\+\n\tbpl\t(?P=empty)\n\ttst\.w\t\$e0aff0\.l\n\tbeq\t(?P<noclr>L_\w+)\n"
    r"\tbclr\t#1,-1\(a6\)\n"
    r"(?P=noclr):\n\tmove\.w\t\(a6\)\+,d4\n\tadd\.w\td7,d4\n\tadd\.w\t12\(a6\),d4\n\tcmp\.w\td1,d4\n"
    r"\tbcs\t(?P=miss)\n\tsub\.w\t14\(a6\),d4\n\tcmp\.w\td4,d0\n\tbcs\t(?P=miss)\n"
    r"\tmove\.w\t\(a6\),d4\n\tadd\.w\td7,d4\n\tadd\.w\t16\(a6\),d4\n\tcmp\.w\td3,d4\n\tbcs\t(?P=miss)\n"
    r"\tsub\.w\t18\(a6\),d4\n\tsub\.w\t18\(a6\),d4\n\tcmp\.w\td4,d2\n\tbcs\t(?P=miss)\n"
    r"\ttst\.w\t20\(a6\)\n\tbmi\t(?P<next>L_\w+)\n\ttst\.w\t\$e0aff0\.l\n\tbne\t(?P<hit>L_\w+)\n"
    r"\tbtst\t#5,-3\(a6\)\n\tbeq\t(?P=hit)\n\tbtst\t#1,-3\(a6\)\n\tbne\t(?P=next)\n(?P=hit):\n"
    r"(?P<body>(?:.*\n)*?)"
    r"(?P=next):\n\tlea\.l\t48\(a6\),a6\n\tdbra\td6,(?P=loop)\n(?P=exit):\n")
# The new loops. The word {left} counts down the hitboxes of the list that reach the shot loop
# (set to TABLE_AFTER + 1 at the list header): the first TABLE_AFTER walk the pool as the
# original did, with the group word's tests moved out into two copies; the next one builds the
# table (the build costs about 180 cycles a live shot, the table loop saves 30-60 a shot
# per hitbox: a boss with a hitbox or two never pays for it) and used by the rest.
# (a1 is free in the shot loop: the hitbox extents are read before it.)
SHOT_LOOP_NEW = """\tmove.w\t$81291e.l,d6
\tsubq.w\t#1,d6
\tsubq.w\t#1,${left:x}.l
\tbmi\t{k}t
\tbeq\t{k}b
\tmovea.l\ta0,a6
\ttst.w\t$e0aff0.l
\tbeq\t{k}ws
\tbra\t{k}wg
{k}wge:\tlea.l\t50(a6),a6
{k}wg:\ttst.w\t(a6)+
\tbpl\t{k}wge
\tbclr\t#1,-1(a6)
\tmove.w\t(a6)+,d4
\tadd.w\td7,d4
\tadd.w\t12(a6),d4
\tcmp.w\td1,d4
\tbcs\t{k}wgm
\tsub.w\t14(a6),d4
\tcmp.w\td4,d0
\tbcs\t{k}wgm
\tmove.w\t(a6),d4
\tadd.w\td7,d4
\tadd.w\t16(a6),d4
\tcmp.w\td3,d4
\tbcs\t{k}wgm
\tsub.w\t18(a6),d4
\tsub.w\t18(a6),d4
\tcmp.w\td4,d2
\tbcs\t{k}wgm
\ttst.w\t20(a6)
\tbpl\t{hit}
{k}wgm:\tlea.l\t48(a6),a6
\tdbra\td6,{k}wg
\tbra\t{exit}
{k}wse:\tlea.l\t50(a6),a6
{k}ws:\ttst.w\t(a6)+
\tbpl\t{k}wse
\tmove.w\t(a6)+,d4
\tadd.w\td7,d4
\tadd.w\t12(a6),d4
\tcmp.w\td1,d4
\tbcs\t{k}wsm
\tsub.w\t14(a6),d4
\tcmp.w\td4,d0
\tbcs\t{k}wsm
\tmove.w\t(a6),d4
\tadd.w\td7,d4
\tadd.w\t16(a6),d4
\tcmp.w\td3,d4
\tbcs\t{k}wsm
\tsub.w\t18(a6),d4
\tsub.w\t18(a6),d4
\tcmp.w\td4,d2
\tbcs\t{k}wsm
\ttst.w\t20(a6)
\tbmi\t{k}wsm
\tbtst\t#5,-3(a6)
\tbeq\t{hit}
\tbtst\t#1,-3(a6)
\tbeq\t{hit}
{k}wsm:\tlea.l\t48(a6),a6
\tdbra\td6,{k}ws
\tbra\t{exit}
{k}b:\tjsr\tcoll_table
{k}t:\tlea.l\t${table:x}.l,a1
\ttst.w\t$e0aff0.l
\tbeq\t{k}s
{k}g:\tmovea.l\t(a1),a6
\tbclr\t#1,-3(a6)
\tcmp.w\t4(a1),d1
\tbhi\t{k}gm
\tcmp.w\t6(a1),d0
\tbcs\t{k}gm
\tcmp.w\t8(a1),d3
\tbhi\t{k}gm
\tcmp.w\t10(a1),d2
\tbcs\t{k}gm
\ttst.w\t20(a6)
\tbpl\t{hit}
{k}gm:\tlea.l\t12(a1),a1
\tdbra\td6,{k}g
\tbra\t{exit}
{k}s:\tcmp.w\t4(a1),d1
\tbhi\t{k}sm
\tcmp.w\t6(a1),d0
\tbcs\t{k}sm
\tcmp.w\t8(a1),d3
\tbhi\t{k}sm
\tcmp.w\t10(a1),d2
\tbcs\t{k}sm
\tmovea.l\t(a1),a6
\ttst.w\t20(a6)
\tbmi\t{k}sm
\tbtst\t#5,-3(a6)
\tbeq\t{hit}
\tbtst\t#1,-3(a6)
\tbeq\t{hit}
{k}sm:\tlea.l\t12(a1),a1
\tdbra\td6,{k}s
\tbra\t{exit}
{hit}:
{body}{next}:
\ttst.w\t${left:x}.l
\tbgt\t{k}wn
\ttst.w\t$e0aff0.l
\tbne\t{k}gm
\tbra\t{k}sm
{k}wn:\ttst.w\t$e0aff0.l
\tbne\t{k}wgm
\tbra\t{k}wsm
{exit}:
"""
TABLE_AFTER = 3     # hitboxes of a list that walk the pool before the table is built
ALLOC_ASM = """
hitbox_owner:
        lea     -$2c(a6),a6
        move.w  #$8000,(a6)
        move.l  a5,$28(a6)
        dbra    d0,hitbox_owner
        rts
"""


def port_jobs(work) -> list[dict]:
    """The porter.port_roots calls of this module (keyword arguments; stages/code.py
    prefetches them in parallel)."""
    return [dict(roots=[ROOT], cache=work / "ps2" / "detrans" / "collision.json")]


def collision_text(work) -> str:
    """The ported routine with the fixes below (68k text, labels F_<Arrange address>)."""
    from .detrans import porter
    from .stages import layout
    d = porter.port_roots(**port_jobs(work)[0])
    probs = {int(k, 16) for k in d["problems"]}
    assert probs <= EXPECTED, f"new problems in the ported collision: {d['problems']}"
    t = d["text"]
    # RAM the porter maps to other words: the arcade's own variables
    t = _fix(t, r"\$80b102\.l", "$80b0f2.l", None)          # player bit ORed into hit hitboxes
    t = _fix(t, r"\$81cf30\.l", "$81777e.l")                 # count of the 0x817440 list
    t = _fix(t, r"\$81e3f6\.l", "$80b0f4.l")                 # range words (adjusted, no longer tested)
    assert layout.ARM_RAM + layout.ARRANGE_BULLETS * 0x40 == 0xD0C800, "bullet pool end (PLAYER_BULLETS)"
    newvar = layout.COLLISION_VARS
    t = _fix(t, r"\$813d62\.l", f"${newvar:x}.l", None)      # Arrange-only: new hitbox group
    # enemy hitbox lists (relocated, 0x2C records)
    bases = {pool.base: layout.FREE_BASE + sum(q.count * q.new_stride for q in layout.POOLS[:k])
             for k, pool in enumerate(layout.POOLS)}
    for old, new in bases.items():
        t = _fix(t, rf"\${old:x}\.l", f"${new:x}.l", None)
    # item pool (800 slots in enhanced RAM, layout.arrange_items; the walk is bounded by the
    # live count)
    t = _fix(t, rf"\${layout.ITEM_POOL_OLD:x}\.l", f"${layout.ITEM_POOL:x}.l")
    # shots against hitboxes: the hitbox extents the porter could not render (add.w (a1)+ into
    # d0 / d2), the shot pointer and the live shot count before the shot loop
    t, n = re.subn(r"\tlea\.l\t14\(a5\),a1\n\taddq\.l\t#2,a1\n\tsub\.w\t\(a1\)\+,d1\n\tadd\.w\td7,d2\n"
                   r"\tmove\.w\td2,d3\n\taddq\.l\t#2,a1\n\tsub\.w\t\(a1\)\+?,d3\n(?:\taddq\.l\t#2,a1\n)?",
                   "\tlea.l\t14(a5),a1\n\tadd.w\t(a1)+,d0\n\tsub.w\t(a1)+,d1\n\tadd.w\td7,d2\n"
                   "\tmove.w\td2,d3\n\tadd.w\t(a1)+,d2\n\tsub.w\t(a1)+,d3\n", t, flags=re.M)
    assert n == 2, f"hitbox extents: {n}"
    t, n = re.subn(r"^(\tsubq\.w\t#1,d6\n\tbra\tL_(?:89d980|8a1940)\n)",
                   "\tmovea.l\ta0,a6\n\tmove.w\t$81291e.l,d6\n\\1", t, flags=re.M)
    assert n == 2, f"shot loop setup: {n}"
    # bullet handlers (the map only interpolates two of them): 0x8B36D0 = 0x170160 (tst.w
    # $16(a6); bne 0x170146), 0x8B0500 = 0x16FFEE (the bullet-to-item setup)
    t = _fix(t, r"#\$1711ea,", "#$170160,", None)
    t = _fix(t, r"#\$16fcd0,", "#$16ffee,", None)
    # PS2 mode flags tested before the barrier conversion (gp-0x7E70 = 2, 0x299ED8 = 1 in
    # Arrange play): always the Arrange path
    t = _fix(t, r"^\tcmpi\.b\t#2,\$800ee1\.l\n\tbne\tL_\w+\n\tcmpi\.b\t#1,\$299ed8\.l\n\tbne\tL_\w+\n", "")
    # barrier against enemies: 0x8A4720 saves the barrier record's +0x10 on the 68k stack and
    # ends by calling 0x8A7D40 (the second hitbox list), which pops it before its rts. The MIPS
    # call leaves the emulated stack alone; as a jsr it put the return address over the saved
    # word, so the pop took half of it and the rts left for garbage (a barrier touching an
    # enemy in the second list crashed the game): a tail jump, as in the arcade's one routine
    t = _fix(t, r"^\tjsr\tF_8a7d40\n(L_8a7d28:\n)?\trts\n", "\tjmp\tF_8a7d40\n", 3)
    # player against bullets
    pb = "\trts\n" if os.environ.get("ESPGAL_TEST_NO_PLAYER_BULLETS") else PLAYER_BULLETS   # profiling only
    t = _fix(t, r"^\tlea\.l\t\$d00002\.l,a6\n(?:.*\n)*?L_8ab710:\n\trts\n", pb)
    assert "ARG_" not in t and "RET_" not in t, "native arguments left in the collision"
    if os.environ.get("ESPGAL_NO_SHOT_TABLE"):
        return t + ALLOC_ASM
    return shot_table(t) + ALLOC_ASM


def layout_left() -> int:
    from .stages import layout
    return layout.COLLISION_TABLE_LEFT


def shot_table(t: str) -> str:
    """Both shots-against-hitboxes loops over a live-shot table (SHOT_TABLE_ASM)."""
    from .stages import layout
    assert layout.SHOT_RECORD == 52, "shot record (SHOT_TABLE_ASM, SHOT_LOOP_RE)"
    table, left = layout.COLLISION_TABLE, layout.COLLISION_TABLE_LEFT
    k = 0

    def loop(m):
        nonlocal k
        k += 1
        return SHOT_LOOP_NEW.format(k=f"cst{k}_", table=table, left=left, hit=m["hit"],
                                    body=m["body"], next=m["next"], exit=m["exit"])
    t, n = re.subn(SHOT_LOOP_RE, loop, t)
    assert n == 2, f"shot loops: {n}"
    # each hitbox list starts the count (the list headers, after its bias d7 is set)
    t, n = re.subn(r"^(\tmove\.w\t#-1,d5\n)", f"\tmove.w\t#{TABLE_AFTER + 1},${left:x}.l\n\\1", t, flags=re.M)
    assert n == 2, f"hitbox list headers: {n}"
    return t + SHOT_TABLE_ASM.format(table=table)


def port_collision(state, ps: m68k.PatchSet, p: bytes) -> str:
    t = collision_text(state.work)
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 4, "Arrange collision")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange collision (ported from A0A9 0x8982F0)")
    assert m68k.matches(p[SITE:SITE + 6], "@6:be75e0d1d779ce80"), "arcade collision entry"
    ps.jmp(SITE, syms[f"F_{ROOT:x}"], "Arrange collision replaces 0x11B1D0")
    assert m68k.matches(p[ALLOC_MARK:ALLOC_MARK + 8], "@8:55376b2d3a1c18fe"), "hitbox allocator (0x2C records)"
    ps.jmp(ALLOC_MARK, syms["hitbox_owner"], "hitbox allocator writes the owner (+0x28)")
    return f"collision at {addr:#x}"
