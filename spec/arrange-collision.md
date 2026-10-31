# Arrange collision (ported: espgalarr/collision.py)

The frame (0x17C8A8..0x17C9A0) calls the collision routine once per player with a0 = the
player's shot pool, a2 = barrier records, a3 = laser records, a4 = player record, d0 = the
player bit (0x1000 / 0x800), $81291E = live shot count. Arcade 0x11B1D0; Arrange 0x8982F0
(twin of the arcade code, arcade mode's PS2 copy at A0A8 0xA048F0).

| part | arcade | Arrange |
|---|---|---|
| player against bullets (sets $80B0FE) | 0x11C216, unrolled over 200 slots | 0x8AA860: loop over the live slots of the 800-slot pool, per-bullet check 0x8951D0 |
| player against items (0x817440, 0x817780) and enemy hitboxes (bump) | 0x11B202..0x11B346 | 0x8982F0 / 0x899D60 (same code) |
| shots against hitboxes (0x815680 / 0x816328 lists) | 0x11B348..0x11B662 | 0x89C600 (changed, below) |
| laser against enemies | 0x11B664.. | removed |
| barrier against bullets / enemies | 0x11B8D8.. | 0x8A4060 + 0x895F20 (bullets of the 800-slot pool become gems or gold), 0x8A4720 / 0x8A7D40 |

Shots against hitboxes (0x89C600), per hitbox list:
- the screen-rectangle tests ($80B0F4) are gone (only the 0x9600 / 0x7800 bounds remain; the
  second list skips its bound when $813A8E bit 2 is set);
- hitbox record +0x28 (Arrange's 0x2C records) holds the owning enemy object; when it differs
  from the previous hitbox's, a new group starts (Arrange-only word, PS2 gp-0x74D4, port
  layout.COLLISION_VARS) and every live shot's +1 bit 1 is cleared;
- a shot with +1 bit 5 (the Power Shots: types 0x20/0x28/0x30/0x38) hits one hitbox per
  group: it skips a hitbox of the same group once its +1 bit 1 is set;
- a hit ORs 0x2082 into the shot type (arcade: bit 7 of the low byte only), counts the hit in
  the hitbox (+0x24 word for Power Shots, +0x26 byte for other shots, +0x27 byte for Power
  Shots), then the arcade damage step (power +0x14 reduced by 1/4 in 2P and when $8139FC,
  hitbox points -= power, damage sum +0x20, Kakusei byte +0x23).

The hitbox allocator (0x14EF0A, the loop at 0x14EF40) stores the enemy object (a5) at +0x28 of
each record it hands out (the PS2 records hold the owner's address there).

Port notes: the de-translator now carries constants of loop preheaders the compiler placed
after the loop (symexec fixpoint), and de-translates a function the splitter cut at its frame
setup as one piece (porter OverlaySource.text). Fixed by hand in collision.py: the hitbox
extents (`add.w (a1)+,d0/d2`), the shot pointer and count before each shot scan, the RAM words
the map gets wrong ($80B0F2, $81777E, $80B0F4), the bullet handlers 0x170160 / 0x16FFEE, the
PS2 mode flags (gp-0x7E70 = 2, 0x299ED8 = 1 in Arrange play) and the player-bullet loop.

Checked: PCSX2 vs MAME with Black (work/scratch/ps2blk, tests/lua/shottrace.lua): shots are
identical up to the first hits; hits now carry 0x2000 as on the PS2 and a Power Shot chain
hits together, but a few frames later, because the enemies are already in other places at
that point (the same hitboxes 0x580-0x850 further along: a stage-timing difference, not the
collision). 100000-frame runs (both characters, invincible, shot held): no freeze.

Cost: the player-bullet check covers the 800-slot pool (empty slots skipped four at a time,
x range tested inline); the shot check has no screen-rectangle prefilter in Arrange. Lag
frames in the first 20000 frames of the long run: 584 before, 972 with the Arrange collision
(854 of them without any player-bullet check).
