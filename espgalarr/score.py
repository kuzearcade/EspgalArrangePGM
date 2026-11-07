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
The counter runs down and is drawn in Arrange's per-player HUD helper (ELF 0x21AB80, called
from the player routine's tail every frame; hooked here into the same tail, TAIL_SITE): the
"xN" multiplier (the gold-value sprites the arcade has, table A0A9 0xC04950) and the counter
digits. A multiplied kill spawns a score popup (ELF 0x1F3F30: 80 records, PS2 0x6409C0,
layout.POPUP_LIST) that zooms in, holds and squashes out (ELF 0x1F4FC0, which the Arrange
frame calls where the arcade frame calls 0x17B0A0; hooked there); its 260 sprites (an "xN"
in four frames for 1..65, ELF table 0x2D7060) are Arrange art, encoded by the graphics stage
from bank A020 (state.sprite_number_slots).
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from .chars import _fix
from .pgm import asm68k, m68k

ROOTS = (0x817E40, 0x819140, 0x81A360, 0x81B660, 0x814AD0, 0x81C880, 0x21AB80, 0x1F4FC0, 0x8B1B90,
         0x8140A0, 0x815E80)
KILL_SITES = {0x178140: 0x817E40, 0x1783EE: 0x819140, 0x178694: 0x81A360, 0x178956: 0x81B660}
ITEM_SITE = 0x1780BA              # score by the player bits in d1 (gems, gold)
PICKUP_SITE = 0x178C10            # score by the player bits 12 / 11 of d1 (item pickups)
# Item pickups in the item list update 0x16E72E (`moveq #v,d0 ; jsr $178C10`), in the order of
# Arrange's item handlers (A0A9 0x7C1620.. 0x7CC3F0, arcade-mode PS2 0x758680.. 0x761C10):
# ten gold items 10..90 (Arrange: a popup of the count in item +0x14, the value x10, then the
# value per count), three gems of 1 (Arrange: 10, 10, 1). Gold items come from Kakusei gold bullets
# (0x170166 -> item spawn 0x16FD6E); Arrange's spawn (0x7CDFE0) stores the bullet's hit count
# (+0x32, set by the gold conversion 0x8B4D80) in item +0x14 where the arcade stores +0x26.
GOLD_ITEMS = {0x16E7A0: 10, 0x16E93E: 15, 0x16EAD6: 20, 0x16EC6E: 30, 0x16EE06: 35, 0x16EF9E: 40,
              0x16F136: 60, 0x16F2CE: 65, 0x16F466: 70, 0x16F5FE: 90}
GEM_ITEMS = {0x16F7DA: 10, 0x16F976: 10, 0x16FB12: 1}
GOLD_COUNT_SITE = 0x16FDD4        # item spawn 0x16FD6E: move.w $26(a6),(a1)+ (item +0x14)
ITEM_COUNT_SITE = 0x16FD4E        # item init 0x16FD26: move.l a0,(a1)+ ; move.w #0,(a1)+ (item +0x10, +0x14);
                                  # Arrange 0x7CD080 stores the spawner's +0x24 word (the hit count of a
                                  # killed enemy's record) as +0x14
CANCEL_SITE = 0x170020            # cancelled-bullet state (Arrange 0x8B0850): `move.w $36(a6),d1 ;
                                  # beq ; moveq #1,d0 ; jsr $1780BA` (10 points to the owner every
                                  # frame while the bullet fades): Arrange scores nothing there
GOLD_END_SITE = 0x17019E          # gold bullet: subq.b #2,$f(a6) ; beq.b $17020E (removed)
# Score calls Arrange changed in other routines (the score module 0x813D50.. replaced the
# arcade's 0x177F52.. as a whole; the shot handlers that call it are ported in chars.py /
# black.py, the item and kill entries above):
#   * 0x162AF2's hit flash: 0x177F52 (a point to the hit's owner unless +0 bit 2) -> Arrange
#     0x8140A0, which also clears the bit;
#   * the stage-clear bonus 0x123F86 / 0x124016 (Arrange ELF 0x1FBF40 / 0x1FCxxx): ten times
#     the constants (per item 1,000,000 / 500,000, gauge x 400, the counter added 500 times)
#     and every add through the x10 score add (Arrange 0x815E80 / 0x816E50);
#   * the gem-gauge charge 0x125260 (Arrange ELF 0x210210) no longer scores +0x78 (0x125350..).
FLASH_SITE = 0x162B7E
BONUS_CONSTS = {0x123FB6: ("000000000000", "203c000f4240"), 0x12403E: ("000000000000", "203c000f4240"),
                0x123FD2: ("000000000000", "203c0007a120"), 0x12405A: ("000000000000", "203c0007a120"),
                0x123FEC: ("c0fc0028", "c0fc0190"), 0x124074: ("c0fc0028", "c0fc0190"),
                0x123FFE: ("7e645347", "3e3c01f3"), 0x124086: ("7e645347", "3e3c01f3")}
BONUS_ADDS = {0x123FBC: 1, 0x123FD8: 1, 0x123FF0: 1, 0x12400A: 1, 0x124044: 2, 0x124060: 2, 0x124078: 2, 0x124092: 2}
GAUGE_SCORE = 0x125350            # btst #0,$813A8E.l ; ... jsr $178104 / $178122 ; rts
# The player routine's common tail (a6 = player), `jsr $1268a0.l ; jsr $12522a.l`: Arrange's tail
# (A0A9 0x8316A0, 20 callers) runs ELF 0x21A220 (= 0x1268A0), the HUD helper 0x21AB80, then 0x20FC70.
# The helper used to be hooked into the player draw step 0x12397C, which does not run for the first
# ~0x1E frames of a stage: with the counters kept across stages (bullet_pool.s) the run-down then
# started 0x1E keys late and a stage 3 kill was paid the kept hit count (x 26 for the PS2's x 10).
TAIL_SITE = 0x1233B2              # `jsr $12522a.l` after `jsr $1268a0.l`
POPUP_SITE = 0x17C866             # frame: jsr $17B0A0 (Arrange calls the popup update there)
HUD_HITS = (0xC04950, 66)         # A0A9: per hit count {offset long, sprite number, size word}
HUD_DIGITS = (0x2DD7C0, 16)       # ELF: digit sprite numbers (longs)
POPUP_SPAWN = (0x2D6DD0, 65)      # ELF: per hit count 10 bytes {offset long, frame index word, size word, word}
POPUP_FRAMES = (0x2D7060, 260)    # ELF: popup sprite numbers (longs, 4 frames per hit count)
EXPECTED = {0x7C0C90, 0x7CDEE0, 0x1BEFF0, 0x817E30, 0x1F3F30, 0x21AB80, 0x1F4FC0,
            "F_80a360_1", "F_80a360_2"}

HUD_ASM = """
score_hud:
        movem.l d0-d6/a0-a2,-(a7)
        jsr     F_21ab80
        movem.l (a7)+,d0-d6/a0-a2
        jmp     $12522a
popup_tick:
        jsr     F_1f4fc0
        jmp     $17b0a0
"""

# Hit marks (enemy record +0x24..+0x27, set by the collision, read by the kill reward in the
# enemy's next update): Arrange's enemy routines clear them together with +0x23 when a
# surviving enemy's update clears its hit flash (e.g. 0xB009B0: `move.b d4,35(a6) ; move.l
# d4,36(a6)`, arcade 0x159B60 `clr.b $23(a6)`; the multi-record enemies clear every record's,
# e.g. 0xA2D2D0 = 0x154B22); an enemy off the screen top skips that and keeps its marks (its
# hit count grows over the frames). Every such arcade store (`clr.b d(a6)` or, after `moveq
# #0,d4`, `move.b d4,d(a6)` with d = a record's +0x23) becomes `trap #15` / `trap #14` and the
# displacement word; the handler does the store and clears the long after it.
MARK_TRAPS = {"clr.b": (15, "4e4f"), "move.b d4,": (14, "4e4e")}
# The stage 1 boss's two side parts (two-record enemies; arcade 0x18AB18 and its twin before it,
# Arrange A0A0 0x6A3140): on a hit the arcade clears record 0's flash only (`clr.b $23(a6)`), where
# Arrange clears both records' flash and hit marks (`move.b d4,35(a6) ; move.b d4,79(a6) ; move.l
# d4,36(a6) ; move.l d4,80(a6)`). Without it record 1's hit count only grew (0x39A by the boss's
# end in a both-shots run) and the boss's merged marks (layout.BOSS1_MARKS, capped at 0x40) paid
# the kill 0x40 x its value: 370,200 against the PS2's 95,200 (PCSX2 both-shots run, TODO 8a).
# These sites take `trap #13` (both records) instead of trap #15.
PAIR_CLEAR_SITES = (0x189EC6, 0x18AB64)
PAIR_TRAP = (13, "4e4d")
MARKS_ASM = """
mark_clr2:
        movem.l d0-d1/a0,-(a7)
        movea.l 14(a7),a0
        move.w  (a0)+,d0
        move.l  a0,14(a7)
        clr.l   $2d(a6,d0.w)
        clr.b   $2c(a6,d0.w)
        clr.l   1(a6,d0.w)
        clr.b   0(a6,d0.w)
        bra     mark_ccr
mark_clr:
        movem.l d0-d1/a0,-(a7)
        movea.l 14(a7),a0
        move.w  (a0)+,d0
        move.l  a0,14(a7)
        clr.l   1(a6,d0.w)
        clr.b   0(a6,d0.w)
        bra     mark_ccr
mark_mov:
        movem.l d0-d1/a0,-(a7)
        movea.l 14(a7),a0
        move.w  (a0)+,d0
        move.l  a0,14(a7)
        move.l  d4,1(a6,d0.w)
        move.b  d4,0(a6,d0.w)
mark_ccr:
        dc.w    $40c1                  ; move.w sr,d1 (the store's flags)
        andi.w  #$f,d1
        andi.w  #$fff0,12(a7)
        or.w    d1,12(a7)
        movem.l (a7)+,d0-d1/a0
        rte
"""


# Two-record enemies (a hitbox in record 0, one in record 1): Arrange merges record 1's hit marks
# into record 0's (A0A9 0x9B8400: +0x26 word or-ed, +0x24 hit counts added, capped at 0x40) right
# after the hit-flash merge `move.b $47(a6),Dn ; or.b Dn,$23(a6)` (0x2C layout: $4f), so the kill
# reward (which reads record 0) sees both shot kinds. Its callers: 15 mode-core runs and death
# routines (A0A9 0x743CE0, 0x9EA9C0 .. 0xAD3BD0) and stage 4's A0A3 0x6C2160; the stage 6 boss
# damage chain does its own merge (restructured.BOSS6_DAMAGE). The 8 bytes become a jsr to the
# pair plus the merge, which leaves d5 the merged long as Arrange's does.
MERGE_SITES = (0x151516, 0x151C52, 0x152332, 0x152A34, 0x1530A8, 0x1536EA, 0x153C84, 0x1542B4, 0x1554E2,
               0x160A52, 0x164216, 0x164AB2, 0x165968, 0x166184, 0x168BFE, 0x16B314, 0x19D2CC)
MERGE_ASM = """
merge_d0:
        move.b  $4f(a6),d0
        or.b    d0,$23(a6)
        bra     mark_merge
merge_d5:
        move.b  $4f(a6),d5
        or.b    d5,$23(a6)
mark_merge:
        move.l  $24(a6),d5
        or.w    $52(a6),d5
        swap    d5
        add.w   $50(a6),d5
        swap    d5
        move.l  d5,$24(a6)
        cmpi.w  #$40,$24(a6)
        bcs     mm_end
        move.w  #$40,$24(a6)
mm_end: rts
merge_s2:
        move.b  $4f(a6),d5
        or.b    d5,$23(a6)
        bsr     mark_merge
        move.w  $18(a6),d4
        cmp.w   $44(a6),d4
        rts
merge_s3:
        bsr     mark_merge
        moveq   #0,d5
        add.w   $20(a6),d5
        rts
clear_s2:
        move.w  d4,$44(a6)
        bmi     cs2_end
        clr.b   $23(a6)
        clr.b   $4f(a6)
        clr.l   $24(a6)
        clr.l   $50(a6)
cs2_end:    tst.w   $18(a6)
        rts
clear_s3:
        move.w  d4,$20(a6)
        move.w  d4,$4c(a6)
        move.b  d4,$23(a6)
        move.l  d4,$24(a6)
        rts
"""
# The two Arrange-only callers, whose arcade twins have no hit-flash merge pair to hook: stage 2's
# two-record enemy (A0A1 0x6B4CB0, arcade 0x1B51F8) adds the pair and the merge before its HP
# minimum (`move.w $18(a6),d4 ; cmp.w $3c(a6),d4`, 8 bytes), stage 3's (A0A2 0x6B5D70, arcade
# 0x1B7A48) the merge alone before summing both records' damage (`moveq #0,d5 ; add.w
# $20(a6),d5`, 6 bytes). Each becomes a jsr to a helper that redoes the replaced instructions:
# (site, its bytes (?? = a record offset the layout stage converts), helper)
MERGE_INSERTS = ((0x1B5216, "00000000000000??", "merge_s2"), (0x1B7A7E, "000000000000", "merge_s3"))
# The same two enemies' hit-mark clears, which their arcade twins (that never clear +0x23 there)
# lack: stage 2's clears both records' flash and marks once it survives the HP minimum (A0A1
# 0x6B4CB0: `moveq #0,d4 ; move.b d4,35(a6) ; move.b d4,79(a6) ; move.l d4,36(a6) ; move.l
# d4,80(a6)`; arcade `move.w d4,$3c(a6) ; tst.w $18(a6)` at 0x1B5228 before its `bmi`), stage 3's
# record 0's alone after the damage sum (A0A2 0x6B5D70: `move.b d4,35(a6) ; move.l d4,36(a6)`;
# arcade `move.w d4,$20(a6) ; move.w d4,$44(a6)` at 0x1B7A9E, d4 = 0). Without the first record 1's
# hit count only grew (0x59 in 0x3F frames of the both-shots run) and the merge passed it on: the
# gold it dropped paid 15 x 0x40 where the PS2's paid 15 x 2 (TODO 8a).
CLEAR_INSERTS = ((0x1B5228, "3d4400??4a6e0018", "clear_s2"), (0x1B7A9E, "00000000000000??", "clear_s3"))
# Enemies of three and four records: Arrange merges the hit marks of records 1..2 (A0A9 0x9B8430)
# or 1..3 (0x9B8470) into record 0's after their hit-flash merge, as 0x9B8400 does for record 1.
# Its four callers: stage 4's four-record enemy's two kills (A0A9 0xA2C4A8 / 0xA2D1D8, arcade
# 0x15497E / 0x154A22, kill values 0x22A / 0x364), a three-record enemy's kill (0x9E9F08, arcade
# 0x1565CA, 0xED2) and a three-record enemy's damage step (0xAB0930, arcade 0x16A098 and its
# mirror copy 0x1699D6, which the PS2 runs through the same function). Without it the four-record
# enemy's kill paid its value x (2 + 10) for the PS2's x (16 + 10), and the counter carried the
# short count on to later kills (the both-shots run's stage 4, TODO 5a). The site is the chain's
# last two instructions, `or.b d(a6),Dn ; or.b Dn,$23(a6)`, 8 bytes; d = record +0x23, arcade.
# (site, records merged, Dn, d)
MULTI_MERGE_SITES = ((0x154994, 3, 0, 0x8F), (0x154A30, 3, 0, 0x8F), (0x1565CE, 2, 0, 0x6B),
                     (0x1699DA, 2, 5, 0x47), (0x16A09C, 2, 5, 0x47))


def _multi_merge_asm() -> str:
    from . import records
    t = ""
    for n in sorted({k for _, k, _, _ in MULTI_MERGE_SITES}):
        ors = "".join(f"\tor.w\t${0x2C * r + 0x26:x}(a6),d5\n" for r in range(1, n + 1))
        adds = "".join(f"\tadd.w\t${0x2C * r + 0x24:x}(a6),d5\n" for r in range(1, n + 1))
        t += (f"mark_merge{n}:\n\tmove.l\t$24(a6),d5\n{ors}\tswap\td5\n{adds}\tswap\td5\n\tmove.l\td5,$24(a6)\n"
              f"\tcmpi.w\t#$40,$24(a6)\n\tbcs\tmm{n}_end\n\tmove.w\t#$40,$24(a6)\nmm{n}_end:\trts\n")
    for a, n, r, d in MULTI_MERGE_SITES:
        t += f"merge_{a:x}:\n\tor.b\t${records.convert(d):x}(a6),d{r}\n\tor.b\td{r},$23(a6)\n\tbra\tmark_merge{n}\n"
    return t


# The stage 1 boss's parts (A0A0 0x6B6930 / 0x6A3140, arcade 0x189E84 / its twin at 0x18AB2A):
# a part killed with the boss copies the boss's merged hit marks (its block's +0x1C long,
# layout.BOSS1_MARKS, set by the ported damage handler A0A0 0x6C08D0) into its own +0x24 before
# the kill reward, so its drops carry the boss's hit count (the PCSX2 Black run: four gold items
# worth 260 each for the arcade code's 100). The stage 4 boss's parts (A0A3 0x6C5EF0, arcade
# 0x19D552) do the same with the long its ported damage handler (A0A3 0x6CB790) keeps in 0x813A6A.
# (site of `jsr $178694.l`, its bytes, the boss's merged marks: None = layout.BOSS1_MARKS)
PART_KILL_SITES = ((0x189EEE, "000000000000", None), (0x18AB8C, "000000000000", None),
                   (0x19D606, "000000000000", 0x813A6A))
# The stage 3 boss (arcade 0x1B6A88, Arrange A0A2 0x6C5360): its two kill rewards take the
# parts' flash (records +4..+6, `move.b $b3(a6),d3 ; or.b .. ; move.b d3,$23(a6)`), and Arrange
# also their hit marks: the +0x26 words or-ed, the +0x24 hit counts added, at most 0x40. Without it
# the kill paid 2849 x 10 where the PS2's paid 2849 x (15 + 10) (both-shots run, TODO 8a).
# The stage 4 boss's form (arcade 0x1B972E, Arrange A0A3 0x702970) does the same with records
# +6..+8 in both its kills (0xE2F, 0x16BA; A0A3 0x704E78 / 0x705DC8): the kills paid x 10 where
# the PS2's paid x (15 + 10) (the both-shots run's stage 4, TODO 5a).
# (site of `move.b d3,$23(a6) ; jsr $1783ee.l`, its bytes, the part records whose marks merge
# (the first one's long is the base))
BOSS_KILL_SITES = ((0x1B6B80, "00000000000000000000", (4, 5, 6)), (0x1B6BBC, "00000000000000000000", (4, 5, 6)),
                   (0x1B980C, "00000000000000000000", (6, 7, 8)), (0x1B9848, "00000000000000000000", (6, 7, 8)))


def _boss_kill_label(parts) -> str:
    return "boss_kill_" + "".join(str(r) for r in parts)


def _boss_kill_asm() -> str:
    t = ""
    for parts in sorted({x for _, _, x in BOSS_KILL_SITES}):
        n = _boss_kill_label(parts)
        ors = "".join(f"\tor.w\t${0x2C * r + 0x26:x}(a6),d3\n" for r in parts[1:])
        adds = "".join(f"\tadd.w\t${0x2C * r + 0x24:x}(a6),d3\n" for r in parts[1:])
        t += (f"{n}:\n\tmove.b\td3,$23(a6)\n\tmove.l\t${0x2C * parts[0] + 0x24:x}(a6),d3\n{ors}\tswap\td3\n{adds}"
              f"\tswap\td3\n\tmove.l\td3,$24(a6)\n\tcmpi.w\t#$40,$24(a6)\n\tbcs\t{n}_go\n\tmove.w\t#$40,$24(a6)\n"
              f"{n}_go:\tjmp\t$1783ee.l\n")
    return t


def _part_kill_asm() -> str:
    from .stages import layout
    t = ""
    for marks in sorted({m or layout.BOSS1_MARKS for _, _, m in PART_KILL_SITES}):
        t += f"""
part_kill_{marks:x}:
        move.l  ${marks:x}.l,$24(a6)
        jmp     $178694.l
"""
    return t


def mark_sites(listing: Path) -> list[tuple[int, str, int]]:
    """(address, form, displacement) of the arcade stores that clear a record's +0x23."""
    import re as _re
    lines = []
    for line in listing.read_text().splitlines():
        m = _re.match(r"^([0-9a-f]{6})\s+\S+\s+(.*)$", line)
        if m:
            lines.append((int(m.group(1), 16), m.group(2).strip()))
    out = []
    for i, (a, u) in enumerate(lines):
        if u == "clr.b $23(a6)":
            out.append((a, "clr.b", 0x23))
        elif u == "moveq #$0, d0":
            # a zeroed d0 stored to the records' +0x23 (the multi-record bosses' flash end, e.g.
            # stage 1's 0x18DF98: $47, $6b, $23) clears them as clr.b does
            j = i + 1
            while j < len(lines):
                m = _re.fullmatch(r"move\.b d0, \$([0-9a-f]+)\(a6\)", lines[j][1])
                if not m:
                    break
                if (int(m.group(1), 16) - 0x23) % 0x24 == 0:
                    out.append((lines[j][0], "move.b d0,", int(m.group(1), 16)))
                j += 1
        elif u == "move.b d4, $23(a6)" and lines[i - 1][1] in ("moveq #$0, d4",) + tuple(
                f"move.w d4, ${x:x}(a6)" for x in (0x20, 0x44, 0x68)) or (
                lines[i - 1][1] == "moveq #$0, d4" and (m := _re.fullmatch(r"move\.b d4, \$([0-9a-f]+)\(a6\)", u))
                and (int(m.group(1), 16) - 0x23) % 0x24 == 0):
            # (a run may start at a later record's: the multi-record bosses' parts, e.g. stage 3's
            # 0x1B6BCA `moveq #0,d4 ; move.b d4,$b3(a6) ..`, Arrange 0x6C89E8 clearing the marks too)
            j = i
            while True:
                m = _re.fullmatch(r"move\.b d4, \$([0-9a-f]+)\(a6\)", lines[j][1])
                if not m or (int(m.group(1), 16) - 0x23) % 0x24:
                    break
                out.append((lines[j][0], "move.b d4,", int(m.group(1), 16)))
                j += 1
    return out


# Gold item pickup (Arrange 0x7C1620..: popup of the count, value x10, the value per count;
# the stubs below enter with d0 = value x10, d4 = value, d4 / d5 saved) and the
# gold bullet's end (Arrange 0x8B37B0 calls 0x8B1B90 there: a popup of the bullet's count)
# Item init (jsr from 0x16FD4E, a1 = item +0x10): the spawner's hit count as Arrange's init
# stores it, when the spawner is an enemy hitbox record; the gold popup indexes its 65-entry
# table with it (at most 0x40, the kill reward's cap). Spawners that are not records (the
# arcade code also spawns items from objects, whose +0x24 is no count) store 0 as the arcade.
ITEM_ASM = """
item_count:
        move.l  a0,(a1)+
        move.l  d0,-(a7)
        moveq   #0,d0
        cmpa.l  #$e08200,a6
        bcs     ic_store
        cmpa.l  #$e09620,a6
        bcc     ic_store
        move.w  $24(a6),d0
        cmpi.w  #$40,d0
        bls     ic_store
        moveq   #$40,d0
ic_store:   move.w  d0,(a1)+
        move.l  (a7)+,d0
        rts
"""

# Arrange's gold pickup (A0A9 0x7C1C70..) counts the value loop down in d5 and leaves it there
# (0xFFFF after the loop, 0 for a zero count): the item walker (0x7C1070 = 0x21CBB8) keeps the
# scroll step in d5 and subtracts it from every later item of the frame, so on the PS2 those
# items move one unit after a pickup (PCSX2 hooks, both-shots run key 0xFF9: walker d5 = 0xFFFF
# after slot 1's pickup, slot 2's y one unit off). Restoring d5 drifted the later items: a gold
# item homed in and was collected 4 logic steps early or late (the both-shots run's 4-key score
# blips). gold_score keeps d4 and leaves d5 as Arrange does.
GOLD_ASM = """
gold_score:
        movem.l d0/d2-d5,-(a7)
        move.l  2(a6),d2
        move.w  20(a6),d3
        moveq   #0,d4
        jsr     F_1f3f30
        movem.l (a7)+,d0/d2-d5
        jsr     $178c10
        moveq   #0,d0
        move.w  20(a6),d5
        beq     gs_done
        subq.w  #1,d5
gs_loop:    add.l   d4,d0
        subq.w  #1,d5
        bpl     gs_loop
        jsr     $178c10
gs_done:    move.l  (a7)+,d4
        addq.l  #4,a7
        rts
gold_end:
        subq.b  #2,$f(a6)
        beq     ge_out
        jmp     $1701a4
ge_out:     jsr     F_8b1b90
        jmp     $17020e
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
    # callees: the score adds (0x817E20 / 0x817E30 jump to them), the gem spawn (Arrange's
    # walks the PS2's 800 item slots, the PGM has the arcade's 100), P2's score record, the
    # bullet-to-gold parameters (0x11B190)
    t = _fix(t, r"\tjsr\t\$170fb8\.l\n", "\tjsr\t$178104.l\n", 4)
    t = _fix(t, r"\tjsr\tF_817e30\n", "\tjsr\t$178122.l\n", 4)
    t = _fix(t, r"\tjsr\tF_7c0c90\n", "\tjsr\t$16e702.l\n", None)
    t = _fix(t, r"^(F_814080:\n\tlea\.l\t)\$80ef36\.l", r"\1$81b596.l")
    t = _fix(t, r"\$c46424\.l", "$81b556.l")
    t = _fix(t, r"\$c46426\.l", "$81b558.l")
    # counter run-down and display (ELF 0x21AB80): the player from a6 (the PS2 tests the HUD
    # record's player byte), the pause tests (0x813A8E bit 7, 0x81E252), the x position (a
    # PS2 word, 0x35869C, that is 0 in play), the tables (generated below), the BCD helper
    t = _fix(t, r"^(\tmove\.w\t#\$5100,d1\n)\tsub\.w\t\$81e06c\.l,d1\n", r"\1")
    t = _fix(t, r"^\ttst\.b\t7\(a5\)\n", "\tcmpa.l\t#$80ee96,a6\n", 2)    # (Z: P1)
    t = _fix(t, r"^\tbne\tL_21b6e8\n\ttst\.w\t\$81e0b0\.l\n", "\tbtst\t#7,$813a8e.l\n\tbne\tL_21b6e8\n\ttst.w\t$81e252.l\n")
    t = _fix(t, r"\tlea\.l\t\$129500\.l,a1\n", "\tlea.l\thud_hits,a1\n")
    # (the table offset is added as a long: the PS2's d0 has a clear upper word there)
    t = _fix(t, r"^(\tmove\.w\t2\(a0\),d0\n\tsubq\.w\t#1,d0\n)", r"\tmoveq\t#0,d0\n\1")
    t = _fix(t, r"\tlea\.l\t\$15e944\.l,a2\n", "\tlea.l\thud_digits,a2\n")
    t = _fix(t, r"\tjsr\tF_1b7bb0\n", "\tjsr\t$11a046.l\n")
    # score popups (ELF 0x1F3F30 spawn, 0x1F4FC0 update): the list, the tables, the PS2
    # variables (frame counter 0x801164, flash 0x80116A, scroll 0x813B1E / 0x807040, pause
    # 0x8139F4 / 0x813A8E bit 2) and the steps the porter could not render
    t = _fix(t, r"\$7ff65e\.l", f"${layout.POPUP_LIST:x}.l", 2)
    t = _fix(t, r"\tlea\.l\t\$140a0a\.l,a1\n", "\tlea.l\tpopup_spawn,a1\n")
    # (the count's offset into it is a long: the MIPS computes it from the whole register)
    t = _fix(t, r"^(L_1f4230:\n\ttst\.w\td3\n\tbeq\tL_1f4128\n)\tmove\.w\td3,d5\n",
             "\\1\tmoveq\t#0,d5\n\tmove.w\td3,d5\n")
    t = _fix(t, r"\tlea\.l\t\$140c9a\.l,a4\n", "\tlea.l\tpopup_frames,a4\n")
    t = _fix(t, r"^\ttst\.l\td4\n\tbne\t(L_1f4ea8)\n\tbeq\tL_1f4ea8\n", r"\ttst.l\td4\n\tbne\t\1\n\tbtst\t#0,13(a5)\n\tbeq\t\1\n")
    t = _fix(t, r"^\tori\.b\t#1,-30\(a0\)\n", "\tbset\t#0,-30(a0)\n")
    t = _fix(t, r"\$b030f8\.l", "$801164.l", None)
    t = _fix(t, r"\$b030ec\.l", "$80116a.l")
    t = _fix(t, r"^(\tsub\.w\t)\$81e154\.l(,d0\n)", r"\1$813b1e.l\2\tadd.l\td0,2(a6)\n")
    t = _fix(t, r"^(\tbcs\tL_1f6880\n)\tbeq\t(L_1f6090)\n\ttst\.w\t\$800f4e\.l\n\tbne\tL_1f6090\n",
             r"\1\tbtst\t#0,(a6)\n\tbeq\t\2\n\ttst.w\t$8139f4.l\n\tbne\t\2\n\tbtst\t#2,$813a8e.l\n\tbne\t\2\n")
    t = _fix(t, r"^(L_1f6238:\n)", r"\1\tbset\t#2,(a6)\n")
    t = _fix(t, r"^(L_1f63c8:\n)(\tbne\tL_1f6238\n)", r"\1\tcmpi.l\t#$80008000,26(a6)\n\2")
    # the popup's sprite slot (ELF 0x1985F0: the next entry of PS2 list 0x62BD40, the third
    # Arrange-only layer by the sprite builder's order; the map took the arcade's 0x117328,
    # which clears a layer-0 entry and returns past it)
    lay = layout.sprite_layout(True)
    base3, ctr3, cap3 = lay.new_layers[2]
    assert layout.POPUPS <= cap3
    t = _fix(t, r"^\tjsr\t\$117328\.l\n", f"\tlea.l\t${base3:x}.l,a0\n\tadda.w\t${ctr3:x}.l,a0\n\taddi.w\t#$c,${ctr3:x}.l\n")
    t = _drop_unused(t)
    for bad in ("ARG_", "RET_", "F_7c0c90", "$b030", "$c4", "$7ff65e", "$81e0", "$81e1", "$800f4e"):
        assert bad not in t, f"left in the ported scoring: {bad}"
    t += "".join(f"gold_{a:x}:\n\tmovem.l\td4-d5,-(a7)\n\tmoveq\t#{v},d4\n\tmove.l\t#{v * 10},d0\n\tbra\tgold_score\n"
                 for a, v in GOLD_ITEMS.items())
    t += GOLD_ASM + ITEM_ASM
    n = sum(q.count for q in layout.POOLS)
    assert all(q.new_stride == 0x2C for q in layout.POOLS)
    return (t + HUD_ASM + MARKS_ASM + MERGE_ASM + _multi_merge_asm() + _boss_kill_asm() + _part_kill_asm()
            + _tables(work))


# Sprite numbers in the tables (PS2 bank A020): (label, byte offset of the number in the
# table, entry size, count); the graphics stage writes the PGM codes there
SPRITE_FIELDS = (("hud_hits", 4, 10, HUD_HITS[1]), ("hud_digits", 0, 4, HUD_DIGITS[1]),
                 ("popup_frames", 0, 4, POPUP_FRAMES[1]))


def _tables(work) -> str:
    """The HUD and popup tables, copied from the PS2 at build time (the sprite numbers are
    replaced by the graphics stage, port_score)."""
    from .ps2.elf import Elf
    from .ps2.overlay import Overlay
    disc = work / "ps2" / "disc"
    ov, elf = Overlay(disc / "A0A0" / "A0A9"), Elf(disc / "SLPS_253.52")

    def words(data: bytes) -> str:
        return "".join(f"\tdc.w\t${int.from_bytes(data[i:i + 2], 'big'):x}\n" for i in range(0, len(data), 2))
    out = "\t.align\t2\n"
    for label, (src, n), size, rd in (("hud_hits", HUD_HITS, 10, ov), ("hud_digits", HUD_DIGITS, 4, elf),
                                      ("popup_spawn", POPUP_SPAWN, 10, elf), ("popup_frames", POPUP_FRAMES, 4, elf)):
        out += f"{label}:\n" + words(rd.read(src, size * n))
    return out


def sprite_slots(syms: dict, code: bytes, addr: int) -> list[tuple[int, int]]:
    """(P-ROM address, PS2 sprite number) of every sprite number in the tables."""
    slots = []
    for label, off, size, n in SPRITE_FIELDS:
        for k in range(n):
            a = syms[label] + k * size + off
            num = int.from_bytes(code[a - addr:a - addr + 4], "big")
            if label == "hud_digits" and k >= 10:
                continue                    # (only the digits 0-9 are drawn)
            slots.append((a, num))
    return slots


def port_score(state, ps: m68k.PatchSet, p: bytes) -> str:
    t = score_text(state.work)
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 4, "Arrange scoring")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange scoring (ported from A0A9 0x817E40..)")
    slots = sprite_slots(syms, code, addr)
    state.sprite_number_slots = getattr(state, "sprite_number_slots", []) + slots
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
    assert p[GOLD_COUNT_SITE:GOLD_COUNT_SITE + 4] == bytes.fromhex("00000000"), "item spawn count word"
    ps.add(GOLD_COUNT_SITE + 2, bytes.fromhex("0032"), "gold item +0x14 = the bullet's hit count (Arrange 0x7CDFE0)")
    assert p[ITEM_COUNT_SITE:ITEM_COUNT_SITE + 6] == bytes.fromhex("000000000000"), "item init count word"
    ps.jsr(ITEM_COUNT_SITE, syms["item_count"], "item +0x14 = the enemy's hit count (Arrange 0x7CD080)")
    assert p[GOLD_END_SITE:GOLD_END_SITE + 6] == bytes.fromhex("000000000000"), "gold bullet end"
    ps.jmp(GOLD_END_SITE, syms["gold_end"], "gold bullet end: Arrange popup (0x8B1B90)")
    assert p[FLASH_SITE:FLASH_SITE + 6] == bytes.fromhex("000000000000"), "hit flash score call"
    ps.add(FLASH_SITE + 2, syms["F_8140a0"].to_bytes(4, "big"), "hit flash point: Arrange 0x8140A0")
    for a, (old, new) in BONUS_CONSTS.items():
        assert p[a:a + len(old) // 2] == bytes.fromhex(old), f"stage bonus constant {a:#x}"
        ps.add(a, bytes.fromhex(new), "stage-clear bonus x10 (Arrange)")
    for a, k in BONUS_ADDS.items():
        assert p[a:a + 6] == bytes.fromhex("000000000000" if k == 1 else "000000000000"), f"stage bonus add {a:#x}"
        ps.add(a + 2, syms["F_815e80" if k == 1 else "L_816e50"].to_bytes(4, "big"), "stage-clear bonus: x10 score add")
    assert p[GAUGE_SCORE:GAUGE_SCORE + 4] == bytes.fromhex("00000000"), "gauge charge score"
    ps.add(GAUGE_SCORE, bytes.fromhex("4e75"), "gauge charge: no score (Arrange)")
    assert p[TAIL_SITE - 6:TAIL_SITE + 6] == bytes.fromhex("000000000000000000000000"), "player routine tail"
    ps.jsr(TAIL_SITE, syms["score_hud"], "Arrange counter run-down in the player routine's tail (A0A9 0x8316A0)")
    from . import records
    ps.add(0x80 + 4 * 15, syms["mark_clr"].to_bytes(4, "big"), "trap #15: hit-flash clear with the hit marks")
    ps.add(0x80 + 4 * 14, syms["mark_mov"].to_bytes(4, "big"), "trap #14: hit-flash store with the hit marks")
    ps.add(0x80 + 4 * PAIR_TRAP[0], syms["mark_clr2"].to_bytes(4, "big"), "trap #13: both records' flash and hit marks")
    for a in PAIR_CLEAR_SITES:
        assert struct.unpack_from(">HH", p, a) == (0x422E, 0x23), f"two-record part flash clear at {a:#x}"
        ps.add(a, bytes.fromhex(PAIR_TRAP[1]) + struct.pack(">H", 0x23), f"both records' hit marks cleared at {a:#x}")
    nm = 0
    for a, form, d in mark_sites(state.work / "pgm" / "dis" / "p_rom.lst"):
        if a in PAIR_CLEAR_SITES:
            continue
        op = {"clr.b": 0x422E, "move.b d4,": 0x1D44, "move.b d0,": 0x1D40}[form]
        nd = records.convert(d)
        cur = struct.unpack_from(">HH", p, a)
        assert cur[0] == op and cur[1] in (d, nd), f"hit-flash store at {a:#x}: {cur}"
        trap = MARK_TRAPS["clr.b" if form == "move.b d0," else form][1]   # d0 is 0 there
        ps.add(a, bytes.fromhex(trap) + struct.pack(">H", nd), f"hit marks cleared with +0x23 at {a:#x}")
        nm += 1
    for a in MERGE_SITES:
        n = (p[a] >> 1) & 7
        assert n in (0, 5) and struct.unpack_from(">HHHH", p, a) in (
            (0x102E | n << 9, d, 0x812E | n << 9, 0x23) for d in (0x47, 0x4F)), f"two-record hit-flash merge at {a:#x}"
        ps.add(a, bytes.fromhex("4eb9") + syms[f"merge_d{n}"].to_bytes(4, "big") + bytes.fromhex("4e71"),
               f"Arrange two-record hit-mark merge (A0A9 0x9B8400) at {a:#x}")
    for a, n, r, d in MULTI_MERGE_SITES:
        assert struct.unpack_from(">HHHH", p, a) in (
            (0x802E | r << 9, x, 0x812E | r << 9, 0x23) for x in (d, records.convert(d))), f"multi-record hit-flash merge at {a:#x}"
        ps.add(a, bytes.fromhex("4eb9") + syms[f"merge_{a:x}"].to_bytes(4, "big") + bytes.fromhex("4e71"),
               f"Arrange {n + 1}-record hit-mark merge (A0A9 {0x9B8430 if n == 2 else 0x9B8470:#x}) at {a:#x}")
    for a, head, label in MERGE_INSERTS + CLEAR_INSERTS:
        n = len(head) // 2
        # (?? = a record 1 offset, arcade or converted: $3c / $44, $44 / $4c)
        assert all(p[a + k] in ((0x3C, 0x44, 0x4C) if h == "??" else (int(h, 16),))
                   for k, h in enumerate(head[i:i + 2] for i in range(0, len(head), 2))), f"Arrange-only hit-mark site {a:#x}"
        ps.add(a, bytes.fromhex("4eb9") + syms[label].to_bytes(4, "big") + bytes.fromhex("4e71") * ((n - 6) // 2),
               f"Arrange two-record hit-mark {'merge (A0A9 0x9B8400)' if label.startswith('merge') else 'clear'} inserted at {a:#x}")
    assert p[CANCEL_SITE:CANCEL_SITE + 14] == bytes.fromhex("0000000000000000" "000000000000"), "cancelled-bullet score"
    ps.add(CANCEL_SITE, bytes.fromhex("6000000c") + bytes.fromhex("4e71") * 5,
           "cancelled bullets score nothing (Arrange 0x8B0850)")
    from .stages import layout
    for a, head, marks in PART_KILL_SITES:
        assert p[a:a + 6] == bytes.fromhex(head), f"boss part kill reward call {a:#x}"
        ps.add(a, bytes.fromhex("4eb9") + syms[f"part_kill_{marks or layout.BOSS1_MARKS:x}"].to_bytes(4, "big"),
               f"boss part: the boss's merged hit marks before its kill reward ({a:#x})")
    for a, head, parts in BOSS_KILL_SITES:
        assert p[a:a + 10] == bytes.fromhex(head), f"boss kill reward {a:#x}"
        ps.add(a, bytes.fromhex("4eb9") + syms[_boss_kill_label(parts)].to_bytes(4, "big") + bytes.fromhex("4e714e71"),
               f"boss: the parts' hit marks (records +{parts[0]}..+{parts[-1]}) merged before its kill reward ({a:#x})")
    assert p[POPUP_SITE:POPUP_SITE + 6] == bytes.fromhex("000000000000"), "frame call of 0x17B0A0"
    ps.add(POPUP_SITE + 2, syms["popup_tick"].to_bytes(4, "big"), "Arrange score popups updated with 0x17B0A0")
    return (f"scoring at {addr:#x} ({len(slots)} sprite slots, {nm} hit-flash stores clear the hit marks, "
            f"{len(MERGE_SITES) + len(MERGE_INSERTS)} two-record and {len(MULTI_MERGE_SITES)} multi-record hit-mark merges)")
