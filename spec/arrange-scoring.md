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
| item pickups in 0x16E72E: ten gold items 10..90, three gems 1 / 1 / 1 | handlers 0x7C1620.. 0x7C9E70, 0x7CAD90 / 0x7CB8C0 / 0x7CC3F0 | gold items x10 plus 10 per count in item +0x14 (with a popup); gems 10 / 10 / 1 |
| 0x16E702 gem spawn | 0x7C0C90 | the PS2 walks 800 item slots (count PS2 0x29A310); the PGM keeps 100 |
| 0x11B190 / 0x1705FA Kakusei gold | 0x1BEFF0 / 0x8B5B00 (per bullet 0x8B4D80) | also stores the enemy's hit count (+0x24, at most 0x40) in the gold (+0x32) |
| (per-player HUD) | ELF 0x21AB80, from 0x8316A0 (player routine) | counter run-down and the multiplier display |
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
such sites). Seen in PCSX2 (watch hooks on the Arrange frame's callees): the marks of frame N
are gone inside the enemy update of frame N+1 (0x782880). The port clears every record's
marks once per frame between the enemy update and the collision (frame call 0x17C890), which
leaves each kill the same marks.

## Counter run-down (ELF 0x21AB80, logic only)

Every frame per player: when the counter runs it drops by 1, every 4 frames while Kakusei is
on (+0x5F), every 16 with the barrier up (0x812676 / 0x812678); not while 0x813A8E bit 7 or
0x81E252 is set. When it is 0 the slide word drops by 0x40 and clears the hit count at 0. The
port runs it from the player draw step 0x12397C (the PS2 runs it from the player routine's
states).

## Not ported

- The multiplier / counter display and the score popups (need the Arrange HUD graphics).
- The 800 item slots (the gem pool stays at 100).
- The gold items' hit-count bonus (item +0x14, stored by Arrange's item init from a native
  argument; the arcade item init leaves the word stale).
- Arrange-only score calls of new Arrange enemy routines (0x8140A0, 0x814210, 0x81C880,
  0x81CF10): the build runs the arcade routines there.
