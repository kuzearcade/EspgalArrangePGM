# Arrange scoring (ported: espgalarr/score.py)

## Routines

| arcade | Arrange (A0A9) | what |
|---|---|---|
| 0x178140 / 0x1783EE / 0x178694 / 0x178956 kill reward entries | 0x817E40 / 0x819140 / 0x81A360 / 0x81B660 | value x10 into a new long (PS2 0xC37680), then the body for the killer (d1 bit 4 = P1, bit 3 = P2) |
| (P1 / P2 copies inside each entry) | 0x80CE40 (first two entries), 0x810540 (last two) | one routine for both players, player number in MIPS $a0 |
| (none) | 0x80A360 | the kill's score (below), called the same way |
| 0x178104 / 0x178122 score add | 0x814060 / 0x814080 (via 0x817E20 / 0x817E30) | unchanged (clamp 0x3B9AC9FF) |
| 0x1780BA item score (gems 0x16DE4A.., gold 0x1700DA / 0x170146) | 0x814AD0 / 0x815C60 | value x10; nothing while Kakusei runs with no gems |
| 0x178C10 pickup score (bits 12 / 11 of d1) | 0x81C880 | nothing while Kakusei runs with no gems |
| item pickups in 0x16E72E: ten gold items 10..90, three gems 1 / 1 / 1 | handlers 0x7C1620.. 0x7C9E70, 0x7CAD90 / 0x7CB8C0 / 0x7CC3F0 | gold items: a popup of the count in item +0x14, the value x10, the value again per count; gems 10 / 10 / 1 |
| 0x16FD6E item spawn from a gold bullet (0x170166) | 0x7CDFE0 | item +0x14 = the bullet's hit count (+0x32) instead of its +0x26 |
| 0x170166 gold bullet end | 0x8B37B0 -> 0x8B1B90 | a popup of the bullet's hit count (half its velocity); its score call goes to an empty stub |
| 0x177F52 a point to a hit's owner (0x162AF2's hit flash) | 0x8140A0 | also clears +0 bit 2 |
| 0x123F86 / 0x124016 end-of-game bonus (lives + 1, the HUD counter 0x81E134 / 0x81E198, gauge, 0x81293A / 0x81293C) | ELF 0x1FBF40 / 0x1FCxxx | constants x10 (1,000,000 / 500,000 per unit, gauge x 400, the counter added 500 times) and every add x10 (0x815E80 / 0x816E50) |
| 0x125260 gem-gauge charge: +0x78 points | ELF 0x210210 | no score |
| 0x16E702 / 0x16FD6E item spawns | 0x7C0C90 / 0x7CDFE0 | 800 item slots (PS2 count 0x29A310); ported as the 800-slot pool (layout.arrange_items, enhanced RAM 0xE10000) |
| 0x11B190 / 0x1705FA Kakusei gold | 0x1BEFF0 / 0x8B5B00 (per bullet 0x8B4D80) | also stores the enemy's hit count (+0x24, at most 0x40) in the gold (+0x32) |
| (per-player HUD) | ELF 0x21AB80, from 0x8316A0 (player routine) | counter run-down and the "xN" / counter display |
| (none) | ELF 0x1F3F30 spawn / 0x1F4FC0 update (80 records of 30 bytes, PS2 0x6409C0) | score popups; the Arrange frame calls 0x1F4FC0 where the arcade frame calls 0x17B0A0 |

Arika merged the P1 / P2 copies of the 68k kill-reward body into one PS2 function taking the
player as a native argument; the porter de-translates a copy per player
(`port_roots(specialise=True)`: calls with constant MIPS arguments port the callee with the
argument bound, tests of it folded).

## New words (PS2 0xC37670.., PGM layout.SCORE_VARS)

| PS2 | P1 / P2 | use |
|---|---|---|
| 0xC37674 / 0xC37678 | counter | starts at 70 (0x46), runs down |
| 0xC37676 / 0xC3767A | hit count | the multiplier kept for later kills |
| 0xC3767C / 0xC3767E | display slide | 0x0680 when set, -0x40 per frame after the counter ends; hit count cleared at 0 |
| 0xC37680 | (shared) | value x10 of the last kill or item |

## Kill score (0x80A360)

With v = the enemy's value (d0 of the entry), hits = enemy +0x24 (Power Shot hits, clamped to
0x40), both = enemy +0x26 and +0x27 set (hit by the normal shot and the Power Shot):
- Kakusei (player +0x66) with no gems (+0x6E = 0): 0, nothing more;
- both, hit in Kakusei (enemy +0x23 != 0, written by the collision): hit count = hits when
  not lower, counter at least 16;
- both, otherwise: counter 0 -> counter 70, hit count = hits; hits equal to the kept hit
  count -> counter = max(70, counter) + hits + 16, higher -> max(70, counter) + 2 * hits + 16,
  hit count = hits; while Kakusei is on (+0x5F) counter += 8 instead of that; lower -> the
  enemy's hits become the kept hit count;
- score: v * hits + v * 10 when both (hits from the enemy), v * kept + v * 10 when not both and
  the counter runs, else v * 10.

Checked against PCSX2 (work/scratch/ps2score.sh, tests/lua/scoretrace.lua): value 48 kills
give 528 / 576 / 624 with the counter running at hit count 1 / 2 / 3, gems 10, a first
multiplied kill (value 40, 1 hit) 440 and the counter starts at 70 and drops 1 per frame.

## Gems and gold (0x80CE40 / 0x810540)

As the arcade (0x178140 ..) except: extra gems hits >> 1 (+1 when the enemy's +0 bit 1) when
both, kept hit count >> 2 while the counter runs; gem kinds by the gem count (0x810540: three
steps at 50 and 250); Kakusei costs 3 + 0x8139A6 (bit 1) or 13 + 0x8139A0 gems per kill
(arcade 5 / 25; 0x810540: 0x81399E-based); a kill while Kakusei runs with no gems drops nothing
and scores nothing (the arcade drops level-scaled gems).

## Hit marks

Enemy record +0x24 (Power Shot hits), +0x26 (normal-shot hit), +0x27 (Power Shot hit) are
set by the collision and read by the kill reward, which the enemy's routine calls in its next
update. A surviving enemy's routine clears them with +0x23 after the hit flash (Arrange
0xB009B0: `move.b d4,35(a6) ; move.l d4,36(a6)`, arcade 0x159B60 `clr.b $23(a6)`; about 100
such sites). The clear runs only on the path that clears the hit flash: an enemy off the top
of the screen (`tst.w $18(a6) ; bmi`) keeps its marks, so its hit count grows over several
frames (PCSX2 shooting run: records keep +0x24/+0x26 across frames). Multi-record enemies clear
every record's (`moveq #0,d4 ; move.b d4,$23(a6) ; move.b d4,$47(a6)..`, Arrange 0xA2D2D0 =
0x154B22). The bosses clear theirs from a zeroed d0 (`moveq #0,d0 ; move.b d0,$47(a6) ;
move.b d0,$6b(a6) ; move.b d0,$23(a6)`, stage 1 boss 0x18DF98; 104 stores in stages 1-6, up to
15 records in one group): without them a boss's records kept their marks for the whole fight
and its parts' Power Shot hits reached the kill reward (the PCSX2 Black run's stage 1 mid-boss,
value 5000: 0x683 hits, 8,385,000 for the PS2's 50,000). The port turns each of these arcade
stores (51 `clr.b $23(a6)`, 49 `move.b d4,` stores in the multi-record groups, 104 `move.b d0,`
after `moveq #0,d0`; score.mark_sites) into `trap #15` / `trap #14` plus the displacement word
(the d0 stores take trap #15, as d0 is 0); the handlers (score.MARKS_ASM, vectors 0xBC / 0xB8, unused by the game:
their arcade handlers are crash dumps) do the store, clear the long after it and set the
store's flags. Arrange's record init (0x786AA0) clears the marks of every record it sets up
(and writes the owner), so a reused record drops the previous enemy's
(asm/m68k/enemy_record_init.s).

Two-record enemies (a hitbox in each record) score their kill from record 0, so Arrange merges
record 1's marks into it every update, right after the arcade's hit-flash merge
(`move.b $47(a6),Dn ; or.b Dn,$23(a6)`): A0A9 0x9B8400 = 0x9B7E60 (d5 = record 0's +0x24 long),
0x9B8060 with the record number in $a0 (+0x26 word or-ed into d5, +0x24 hit count added to its
high word; record n at n * 0x2C), 0x9B7EE0 (stored back, hit count capped at 0x40). A kill
with Power Shot hits on one hitbox and a normal-shot hit on the other thus scores as hit by
both (v * hits + v * 10 and the counter) where the arcade code scores v * 10. Callers: 15
mode-core routines (A0A9 0x743CE0 = 0x160A52; the death routines 0x9EA9C0 .. 0xA3AB20 =
0x151516 .. 0x1554E2; the runs 0xA8A750 .. 0xAD3BD0 = 0x164216 .. 0x16B314, among them the type
0x88 run 0x1649F2) and stage 4's A0A3 0x6C2160 (0x19D2CC). The port (score.MERGE_SITES) turns
each pair into a jsr to the pair plus the merge, which leaves d5 the merged long as Arrange's
does. The callers A0A1 0x6B4CB0 and A0A2 0x6B5D70 have no hit-flash merge pair in their arcade
twins (stage 2's 0x1B51F8, stage 3's 0x1B7A48): Arrange adds the pair and the merge before
stage 2's HP minimum and the merge alone before stage 3's damage sum; score.MERGE_INSERTS calls
a helper there that redoes the replaced instructions. The three- and four-record merges 0x9B8430 / 0x9B8470 have
no callers. The stage 6 boss damage chain merges its parts itself (restructured.BOSS6_DAMAGE).
Checked: Black's stage 1 shooting run (PCSX2 ps2blk) matches the PS2 in score, P1 and enemy
records to the end of the trace (94558 on both; before, the first difference was this kind of
kill at key 0x7C6 and the run ended at 91960).

## Counter run-down and display (ELF 0x21AB80)

Every frame per player: when the counter runs it drops by 1, every 4 frames while Kakusei is
on (+0x5F), every 16 with the barrier up (0x812676 / 0x812678); not while 0x813A8E bit 7 or
0x81E252 is set. When it is 0 the slide word drops by 0x40 and clears the hit count at 0.
While the counter or the slide runs (and 0x812932 is clear) it draws, in the HUD layer
0x80647E (0x1168B4, palette 0x17): the "xN" sprite for the kept hit count (table A0A9
0xC04950: the arcade's gold-value sprites, also in the arcade table 0x170212) at x 0x5100,
y -0x580 (P1) / 0x3800 (P2) shifted by the slide word, then the counter in decimal (0x11A046,
leading zeros skipped, digit sprites 0x30..0x39 of ELF table 0x2DD7C0) 0x400 lower, 0x240
apart. The PS2 subtracts a word (0x35869C) from x that is 0 in every RAM dump (it is only
cleared and saved), so the port leaves it out. The port runs it from the player draw step
0x12397C (the PS2 runs it from the player routine's states).

## Score popups (ELF 0x1F3F30 spawn, 0x1F4FC0 update)

A multiplied kill (0x80A360) spawns a popup at the enemy (d2) for the hit count d3: 80 records
of 30 bytes (PS2 0x6409C0, PGM layout.POPUP_LIST, cleared at game start): +0 flags (bit 0:
ground enemy, enemy object +0xD bit 0), +2 position, +6 offset / +0xC frame index / +0xE size
(ELF table 0x2D6DD0 by hit count), +0x10 palette (0x17, 0x19 one frame in four), +0x13 hold
(0x14), +0x16 velocity (0), +0x1A zoom. Every frame (the Arrange frame calls 0x1F4FC0 where
the arcade frame calls 0x17B0A0; the port calls both): moves by the velocity minus the
scroll (0x813B1E), ground popups follow the ground scroll (0x807040) unless 0x8139F4 or
0x813A8E bit 2; removed off screen; zooms in over 16 frames (+0x1A += 0x08000800 up to
0x80008000), holds 20 frames, then shrinks its height by 2 per frame. Drawn in the third
Arrange-only sprite layer (the PS2 list 0x62BD40 by the sprite builder's order), sprite =
ELF table 0x2D7060 (four frames per hit count, chosen by frame counter bits 1-2). The 260
popup sprites are Arrange art: the graphics stage encodes them from bank A020.

Checked in MAME against PCSX2 screenshots: the "xN" and counter under the gem counter, the
popups at the kills, yellow with a red flash every fourth frame.

## Score calls in other routines

Checked across the main and stage overlays: every Arrange routine that calls a score routine has an arcade twin (matched,
or the routine between the same matched neighbours) calling the corresponding arcade one;
the differences are in the score routines themselves (above). 0x814210 (shot hits) is
ported with the shot handlers (chars.py, black.py); 0x81CC90 / 0x81CF10 are unchanged twins
of 0x17BD02 / 0x17BD16.

## Item handlers and the gauge charge

Arrange's item handlers (0x7C1620.. 0x7CC3F0) differ from the arcade mode's (0x758680..
0x761C10) in the scores and popups above, in the draw thinning of the third gem kind and the 90
gold item (every other frame near the player from 780 items instead of 80: Arrange 0x7BD5F0,
ported in layout.arrange_items) and in their draw call: a helper (0x7BE300 -> ELF 0x1B5100 / 0x1B4F00 /
0x1B4EC0) whose return value deletes the item when 0. Its v0 is the translated stack pointer
(none of its callees writes v0) unless the PS2-only flag 0x550C20 is set, so in play it never
deletes an item; not ported.

The gem-gauge charge (ELF 0x210210) matches the arcade's 0x125260 step for step (including the
pause on 0x813A8E bit 7) apart from the score it no longer adds.
