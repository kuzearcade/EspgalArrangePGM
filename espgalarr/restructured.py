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
  * A0A9 0x947FE0 = 68k 0x177692, the run of bullet type 0x20 (the spawner, run-init 0x177604
    kept): every tick Arrange fires three type 0x0D children (at the angle, then -0x40, -0x80)
    through its shot helper 0x8E0E30 where the arcade fires one (about 3x the type 0x0D bullets
    in the PS2's no-input run, stages 2, 4, 5 and 6).
  * Volley routines whose Arrange shot-helper calls do not line up with the arcade's (item 3 of
    TODO.md; work/scratch/ksites_left.py), ported whole: A0A0 0x655400 = 0x18D6EE, A0A2 0x6713B0
    = 0x197996, A0A3 0x665CD0 = 0x19BD2C, A0A4 0x6625E0 = 0x1BDD86 and 0x6647A0 = 0x1BDB16,
    stage 6 sub-tasks 0x11 / 0x12 (A0A5 0x6D05A0 = run 0x1A63A4: two
    calls of its fan helper 0x6CEFC0 for the 68k's ten shots) and 0x16 (A0A5 0x6D4EB0 = run
    0x1A6B54, through the shot wrapper 0x8E08B0).
  * A0A0 0x6DAF70 = 68k 0x1B1996, a stage 1 volley task's run: per fan angle Arrange fires
    three shots at speed s, s - 3, s - 6 through its shot wrapper (A0A9 0x8E08B0) where the
    arcade fires one (its fan helper 0x1B19CA; PS2 Kakusei test, counter 0xED2).
  * A0A2 0x6981A0 = 68k 0x195922, a stage 3 volley task: Arrange fires five bullets a side
    (sprites 0x18 / 0x26 / 0x27 / 0x26 / 0x18) where the arcade fires two, and sweeps 0xB0.
  * A0A4 0x668CB0 = 68k 0x1BE0C2, a stage 5 fan task: four fans (19, 15, 11, 9 bullets) where
    the arcade fires three, with other shot helpers.
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
Stage 6 boss damage chain (BOSS6_DAMAGE): Arrange's functions for the first six boss forms
(A0A5 0x70B7E0 ..), which add the parts' multiplier fields, ported whole from 0x1A3E10.
Stage clear (CLEAR_TEXT): Arrange shows no story text after a stage, so the clear sequence is 27
frames shorter (273 logic frames after stage 1, as on the PS2).
"""
from __future__ import annotations

import re
import struct

from .pgm import asm68k, m68k

# Arrange routine -> (68k routine it replaces, its first bytes there; "??": bytes the layout
# stage may change, record offsets)
ROOTS = {
    ("A0A1", "A0AB"): {0x65D3C0: (0x1920AA, "522e????532e????522e????532e????000000000000")},
    ("A0A0", "A0AA"): {0x6DAF70: (0x1B1996, "000000000000"), 0x655400: (0x18D6EE, "0000000000000000")},
    ("A0A3", "A0AD"): {0x665CD0: (0x19BD2C, "0000000000000000")},
    ("A0A2", "A0AC"): {0x6981A0: (0x195922, "0000000000000000"), 0x6713B0: (0x197996, "0000000000000000")},
    ("A0A4", "A0AE"): {0x668CB0: (0x1BE0C2, "000000000000"), 0x6625E0: (0x1BDD86, "0000000000000000"),
                       0x6647A0: (0x1BDB16, "0000000000000000")},
    ("A0A5", "A0AF"): {0x6D05A0: (0x1A63A4, "0000000000000000"), 0x6D4EB0: (0x1A6B54, "00000000000000c0"),
                       0x6D35C0: (0x1A688E, "000000000000"), 0x6C3FC0: (0x1A5E2C, "000000000000")},
    # mode-core enemy runs Arrange rewrote (tools/core_audit.py found their constants unpaired):
    # A0A9 0xADEEF0 = 0x16778A (a 7-shot fan where the arcade fires 5, through Arrange's shot
    # helper), 0xAE50C0 = 0x167E7E, 0x9D64F0 = 0x150D7C (the audit's 0x161EB4 is a crosswise
    # pair: 0x161EB4's twin is A0A9 0x75D030, which keeps the arcade's `cmpi.w #$400`)
    ("A0A9", "A0A8"): {0x947FE0: (0x177692, "0000000000000000"), 0xADEEF0: (0x16778A, "00000000000000000000"), 0xAE50C0: (0x167E7E, "00000000000000000000"),
                       0x9D64F0: (0x150D7C, "000000000000")},
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
# overlays whose roots call helpers with constant native arguments (A0A2 0x6981A0 calls A0A9
# 0x8E6C40, the bullet limit setter the arcade inlines, with the limit in $a0)
SPECIALISE = {"A0A2"}
# PS2 addresses a root reads that its overlay's learned map lacks (the player objects, 68k
# 0x80EE96 / 0x80EF36, in stage 5's fan task): replaced in the ported text
ADDR_FIXES = {"A0A4": {0xC310C0: 0x80EE96, 0xC31160: 0x80EF36}}
# problems the TEXT_FIXES below resolve (or harmless: flags left for a callee that does not read
# X, 0x178140), dropped from the porter's report: (overlay: [(function, problem prefix)]); each
# must occur
ACCEPTED = {"A0A9": [
    ("0xae50c0", "ROM address 0x167ca0 only guessed: lea.l\t$167ca0.l,a0"),
    ("0xae50c0", "ROM address 0x167d20 only guessed: lea.l\t$167d20.l,a1"),
    ("0xae50c0", "ROM address 0x167d80 only guessed: lea.l\t$167d80.l,a0"),
    ("0xae50c0", "ROM address 0x167ce0 only guessed: lea.l\t$167ce0.l,a0"),
    ("0xae50c0", "unknown: ??? flags C=0, V=0, Z=0, N=0, X="),
    ("0xaddd00", "ROM address 0x167ce0 only guessed: lea.l\t$167ce0.l,a0"),
    ("0xaddd00", "ROM address 0x167d20 only guessed: lea.l\t$167d20.l,a1"),
    ("0x9b6810", "unknown: ??? move.b [arg0],d2"), ("0x9b6810", "unknown: ??? a0 = arg1"),
    ("0x9b4060", "ROM address 0x16bf60 only guessed: lea.l\t$16bf60.l,a0"),
    ("0x1d0960", "new variable 0x800efc not yet assigned"),
    ("0x9d64f0", "call 0x9b6810 passes a0=a5+30,a1=$c0b2b0 natively"),
    ("0x1bb970", "unknown: ??? if ne0(sext8(add(lo16([$358029].bs), $1))) goto L_1bb9a8"),
    ("0xadeef0", "ROM address 0x167ca0 only guessed: lea.l\t$167ca0.l,a0")]}
# Arrange's second score-variable pointer table (A0A9 0xADDD00 / 0xAE50C0, PS2 0xC12950, 16
# entries: the 0x813A4E.. variables three entries on), rebuilt in ROM from the arcade's first
# (0x167CE0 = PS2 0xC12910): (PS2 table, PS2 first table, ROM first table, entries)
CORE_MULT_PTRS = (0xC12950, 0xC12910, 0x167CE0, 16)
# the stage 3 volley task's bullet-limit calls (A0A9 0x8E6C40 with 0x4F, specialised): the port
# keeps the arcade's group-count variables and values (0x81B55A / 0x81B562; 0x12 at the start,
# 0xE at the end, as in 0x195922: the walkers have their own count, layout.ARR_CAP, and other
# routines read the arcade's), where the map took the PS2 variable for ARR_CAP / ARR_DELTA
# (overlay: (old, new, occurrences in order))
TEXT_FIXES = {"A0A9": [
                  # the type 0x20 spawner's run animates PS2 atlas sprites 0x32D0..0x32D3 = ROM
                  # 0x28D0E0 + 0x24 n (sprite_ids.located), as the arcade run 0x177692 does
                  ("addq.l\t#1,(a0)\n\tcmpi.l\t#$32d4,(a0)", "addi.l\t#$24,(a0)\n\tcmpi.l\t#$28d170,(a0)", 1),
                  ("move.l\t#$32d0,(a0)", "move.l\t#$28d0e0,(a0)", 1),
                       # tables the map only guessed, checked against the PS2 data: 0x16BF84 is
                       # the arcade's draw jump table (A0A9 0x9B4060 = its inline call), 0x167E24
                       # the RNG-picked position table (PS2 0xC129B0 -> sprites 0x3858.. = ROM
                       # 0x2E6C18..); 0x167CA0 / 0x167CE0 are the arcade's own (kept)
                       ("$16bf60.l", "$16bf84.l", 1), ("$167d80.l", "$167e24.l", 1),
                       # ELF copies of 68k routines: 0x1D0960 = 0x11911C (its 0x8139A2 mapped as
                       # 0x800EFC), 0x1BB970 = 0x11AA26 (RNG pick, mis-split by the de-translator)
                       ("$800efc.l", "$8139a2.l", 1), ("jsr\tF_1bb970", "jsr\t$11aa26.l", 2),
                       # Arrange's second pointer table, rebuilt in ROM (CORE_MULT_PTRS)
                       ("$167d20.l", "core_mult_ptrs", 3),
                       # A0A9 0x9B6810 takes its byte pointer and table natively (a0 = a5 + $1E,
                       # a1 = PS2 0xC0B2B0 = ROM 0x15C852, sprite_ids.located): inlined as the
                       # arcade does it (0x15100E)
                       ("jsr\tF_9b6810", "move.b\t30(a5),d2\n\tandi.w\t#$3e,d2\n\tadd.w\td2,d2\n\tlea.l\t$15c852.l,a0\n"
                        "\tmove.l\t(a0,d2.w),d2", 2)],
              "A0A2": [("$818352.l", "$81b55a.l", 6), ("$818354.l", "$81b562.l", 2),
                       ("#$4f,d0", ["#$12,d0", "#$e,d0"], 2), ("#$4f,$81b55a.l", ["#$12,$81b55a.l", "#$e,$81b55a.l"], 2),
                       # the last volley's longer delay: A0A2 0x69B248 computes $36 - 1 without
                       # storing it (`cmpi.w #1,54(a5)`), which the de-translation drops
                       ("\tbeq\tL_69b360\n\tbne\tL_69b390\n\tmove.b\t#$1c,44(a5)",
                        "\tbeq\tL_69b360\n\tcmpi.w\t#1,54(a5)\n\tbne\tL_69b390\n\tmove.b\t#$1c,44(a5)", 1)]}
# Stage 6 boss damage chain (68k 0x1A3E10..0x1A47C4, one routine falling through a block per
# boss form): Arrange splits it into functions that tail-call the next (A0A5 0x70B7E0 ..
# 0x71D800; the address map pairs them one block off, so the porter refused them and maps their
# tail calls wrongly). Their HP and threshold constants are the arcade's; Arrange clears each
# part's new record words +0x24 / +0x26 while the form lives and, when it dies, merges its parts'
# +0x24 (sum, capped at 0x40) and +0x26 (or) into part 0's before the reward routine. Ported
# whole from 0x1A3E10; the last block (final form, A0A5 0x720E30, with PS2-only calls in its
# death routine) stays the arcade's 0x1A4666. (pair, entry root, 68k entry, its first bytes,
# other roots, {function: (68k tail call, Arrange function it is)})
BOSS6_DAMAGE = (("A0A5", "A0AF"), 0x70B7E0, 0x1A3E10, "4a6e????67024e75",
                (0x70D3B0, 0x70EF90, 0x710C20, 0x718200, 0x71D800),
                {0x70B7E0: (0x1A402C, 0x70D3B0), 0x70D3B0: (0x1A3F1E, 0x70EF90),
                 0x710C20: (0x1A4142, 0x718200), 0x718200: (0x1A4362, 0x71D800)})
# Bullet types Arrange adds (A0A9 table 0xC05000, 41 descriptors; the 68k's 0x173F86 has 38 and
# clamps the type to 0x25 at 0x17401E, Arrange to its variable 0x801039 = 0x28): types 0x26..0x28
# (the stage 3 volley's sprites 0x26 / 0x27) have only a run-init handler, A0A9 0x95B070 /
# 0x95E010 / 0x960FB0 (the PS2 fills the descriptors' handler fields at run time: PCSX2 dump).
# The three handlers are ported, the table is copied to free ROM with their descriptors (41
# entries), its two readers point there and the clamp becomes 0x28. Type 0x28 is fired in
# stage 5 (PCSX2 dumps at PS2 frame 129791: 8 at a time, up to 417 in an earlier run); its
# run-init is the arcade's type 0x0F / 0x22 one (0x175808 / 0x176896) and its 27 sprites
# (atlas 0x33C1..0x33DB, frame table A0A9 0xC057C0) are the ROM's (sprite_ids.located: codes
# 0x28EF94.., frame table 0x175984). Its run handler draws the bullet zoomed: `ori.l
# #$50005000` (IGS023 sprite words: zoom index 0xA, no grow) where the arcade types `or` d5
# (0x80008000, unzoomed). (68k table, entries, clamp word, readers, Arrange table, {type:
# run-init handler})
BULLET_TYPES = (0x173F86, 38, 0x17401E, (0x171056, 0x17148A), 0xC05000,
                {0x26: 0x95B070, 0x27: 0x95E010, 0x28: 0x960FB0})
# their run handlers (stored by the run-inits as $22 code pointers, PS2 addresses the porter
# leaves as numbers) and fixes to the ported text: those pointers, the first sprite (PS2 atlas
# numbers 0x32F4 / 0x3300 written bytewise to $A = ROM sprites 0x28D470 / 0x28D620,
# sprite_ids.located) and the frame tables the map only guessed (PS2 0xC05950: 0x3300..0x3303 =
# ROM 0x176694; PS2 0xC057C0: 9 frames of 0x33C1..0x33DB = ROM 0x175984)
BULLET_RUNS = (0x95BF20, 0x95EEC0, 0x961E60)
BULLET_FIXES = (("move.l\t#$95bf20,34(a6)", "move.l\t#F_95bf20,34(a6)"),
                ("move.l\t#$95eec0,34(a6)", "move.l\t#F_95eec0,34(a6)"),
                ("move.l\t#$961e60,34(a6)", "move.l\t#F_961e60,34(a6)"),
                ("clr.w\t10(a6)\n\tmove.b\t#$32,12(a6)\n\tmove.b\t#-$c,13(a6)", "move.l\t#$28d470,10(a6)"),
                ("clr.w\t10(a6)\n\tmove.b\t#$33,12(a6)\n\tclr.b\t13(a6)", "move.l\t#$28d620,10(a6)"),
                ("lea.l\t$17a796.l,a1", "lea.l\t$176694.l,a1"),
                ("lea.l\t$17a606.l,a0", "lea.l\t$175984.l,a0"))
# the guessed tables, checked against the PS2's: (ROM table, PS2 table, frames, sprites a frame)
BULLET_FRAME_TABLES = ((0x175984, 0xC057C0, 9, 3),)
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


def _fix_tails(text: str, tails: dict) -> str:
    """Point each listed function's tail calls (`jsr $68k.l`) at the ported next function."""
    out, fn, seen = [], None, set()
    for line in text.split("\n"):
        if re.match(r"^F_[0-9a-f]+:", line):
            fn = int(line[2:-1], 16)
        if fn in tails and line.strip() == f"jsr\t${tails[fn][0]:x}.l":
            line = f"\tjsr\tF_{tails[fn][1]:x}"
            seen.add(fn)
        out.append(line)
    assert seen == set(tails), f"boss damage chain tail calls not found: {set(tails) - seen}"
    return "\n".join(out)


def _core_mult_ptrs(state, p: bytes) -> str:
    """The rebuilt pointer table (CORE_MULT_PTRS) as assembler data: the PS2 first table's
    entries map onto the ROM's 0x167CE0 by one offset, which then maps the second table."""
    from .ps2 import overlay
    tab, first, rom_first, n = CORE_MULT_PTRS
    core = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    a = struct.unpack(f"<{n}I", core.read(first, 4 * n))
    r = struct.unpack(f">{n}I", p[rom_first:rom_first + 4 * n])
    off = r[0] - a[0]
    assert all(y - x == off for x, y in zip(a, r)), "core score-variable table offset"
    b = struct.unpack(f"<{n}I", core.read(tab, 4 * n))
    assert all(a[0] <= v <= a[-1] for v in b), "Arrange score-variable table"
    return "\ncore_mult_ptrs:\n" + "".join(f"\tdc.l\t${v + off:x}\n" for v in b)


def port_restructured(state, ps: m68k.PatchSet, p: bytes) -> str:
    from .detrans import porter
    from .ps2 import overlay
    texts = []
    bpair, broot, bsite, bhead, bmore, btails = BOSS6_DAMAGE
    for (rn, an), roots in ROOTS.items():
        extra = [broot, *bmore] if (rn, an) == bpair else []
        d = porter.port_roots(list(roots) + extra, state.work / "ps2" / "detrans" / f"restructured_{rn.lower()}.json",
                              r_name=rn, a_name=an, specialise=rn in SPECIALISE)
        for ps2_, m68_ in ADDR_FIXES.get(rn, {}).items():
            d["text"] = d["text"].replace(f"${ps2_:x}.l", f"${m68_:x}.l")
            d["problems"] = {f: [x for x in pr if f"{ps2_:#x}" not in x] for f, pr in d["problems"].items()}
            d["problems"] = {f: pr for f, pr in d["problems"].items() if pr}
        for fn_, pr_ in ACCEPTED.get(rn, []):
            assert any(x.startswith(pr_) for x in d["problems"].get(fn_, [])), \
                f"{rn} accepted problem not reported: {fn_} {pr_!r}"
            d["problems"][fn_] = [x for x in d["problems"][fn_] if not x.startswith(pr_)]
        d["problems"] = {f: pr for f, pr in d["problems"].items() if pr}
        assert not d["problems"], f"problems in the ported {rn} routines: {d['problems']}"
        assert not any(f"${ps2_:x}" in d["text"] for ps2_ in ADDR_FIXES.get(rn, {})), f"{rn} address fixes"
        if rn == "A0A9":
            d["text"] += _core_mult_ptrs(state, p)
        for old_, new_, n_ in TEXT_FIXES.get(rn, []):
            assert d["text"].count(old_) == n_, f"{rn} text fix {old_!r}: {d['text'].count(old_)} occurrences"
            for k_ in range(n_):
                d["text"] = d["text"].replace(old_, new_[k_] if isinstance(new_, list) else new_, 1)
        texts.append(_fix_tails(d["text"], btails) if extra else d["text"])
    at, old, new, ovn, mips = CORE_TURRET_PERIOD
    w = struct.unpack("<I", overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn).read(mips, 4))[0]
    assert w >> 26 == 9 and w & 0xFFFF == new, f"Arrange turret period at {mips:#x}"
    assert p[at] == old, f"unexpected code at {at:#x}"
    ps.add(at, bytes([new]), f"Arrange core turret period {old} -> {new:#x}")
    site, end, head, call = CORE_TURRET_VOLLEY
    assert _matches(p[site:site + len(head) // 2], head) and p[call:call + 6] == bytes.fromhex("000000000000"), \
        f"core turret volley {site:#x}"
    # each overlay's text is assembled on its own: the callees ported with its roots (the
    # bullet helpers, kakusei.py) may also be in another overlay's text
    total = n = 0
    for ((rn, an), roots), t in zip(ROOTS.items(), texts):
        code, _ = asm68k.assemble(t, 0x200000)
        addr = state.alloc_p(len(code), 2, f"Arrange restructured volleys ({rn})")
        code, syms = asm68k.assemble(t, addr)
        ps.add(addr, code, f"Arrange restructured volleys ({rn})")
        total += len(code)
        for fn, (site_, head_) in roots.items():
            assert _matches(p[site_:site_ + len(head_) // 2], head_), f"restructured routine {site_:#x}"
            ps.jmp(site_, syms[f"F_{fn:x}"], f"Arrange {fn:#x} replaces {site_:#x}")
            n += 1
        if (rn, an) == bpair:
            assert _matches(p[bsite:bsite + len(bhead) // 2], bhead), f"boss damage chain {bsite:#x}"
            ps.jmp(bsite, syms[f"F_{broot:x}"], f"Arrange stage 6 boss damage chain replaces {bsite:#x}")
    t = CORE_TURRET_ASM.replace("{end:x}", f"{end:x}")
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 2, "Arrange core turret volley")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange core turret volley")
    total += len(code)
    ps.jmp(site, syms["core_turret_volley"], "Arrange core turret volley")
    btab, bn, bclamp, breads, atab, bhandlers = BULLET_TYPES
    d = porter.port_roots(list(bhandlers.values()) + list(BULLET_RUNS),
                          state.work / "ps2" / "detrans" / "restructured_bullet_types.json", r_name="A0A9", a_name="A0A8")
    assert d["problems"] == {"0x95eec0": ["ROM address 0x17a796 only guessed: lea.l\t$17a796.l,a1"],
                             "0x960fb0": ["ROM address 0x17a606 only guessed: lea.l\t$17a606.l,a0"]}, \
        f"problems in the ported bullet type handlers: {d['problems']}"
    for old_, new_ in BULLET_FIXES:
        assert d["text"].count(old_) == 1, f"bullet type handler text: {old_!r}"
        d["text"] = d["text"].replace(old_, new_)
    assert not re.search(r"#\$[89][0-9a-f]{5}\b", d["text"]), "PS2 code pointer left in the bullet type handlers"
    core = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    from .detrans import sprite_ids
    located = sprite_ids.located()
    for rtab, ptab, frames, per in BULLET_FRAME_TABLES:
        for k in range(frames):
            pf = struct.unpack("<I", core.read(ptab + 4 * k, 4))[0]
            rf = struct.unpack(">I", p[rtab + 4 * k:rtab + 4 * k + 4])[0]
            want = [located[n] for n in struct.unpack(f">{per}I", core.read(pf, 4 * per))]
            assert list(struct.unpack(f">{per}I", p[rf:rf + 4 * per])) == want, f"bullet frame table {rtab:#x} frame {k}"
    assert struct.unpack(">H", p[bclamp:bclamp + 2])[0] == bn - 1 and bclamp == btab + 4 * bn, "bullet type clamp"
    lines = [d["text"]]
    for typ, fn in bhandlers.items():
        desc = core.read(struct.unpack("<I", core.read(atab + 4 * typ, 4))[0], 0x1A)
        assert desc[0x12:0x16] == b"\0" * 4 and desc[0x16:0x1A] == b"\xff" * 4, f"Arrange bullet type {typ:#x}"
        lines.append(f"bullet_desc_{typ:x}:\n" + "".join(f"\tdc.w\t${w:04x}\n" for w in struct.unpack(">9H", desc[:0x12]))
                     + f"\tdc.l\t0\n\tdc.l\tF_{fn:x}\n")
    lines.append("bullet_types:\n" + "".join(f"\tdc.l\t${struct.unpack('>I', p[btab + 4 * k:btab + 4 * k + 4])[0]:x}\n"
                                              for k in range(bn))
                 + "".join(f"\tdc.l\tbullet_desc_{typ:x}\n" for typ in sorted(bhandlers)))
    bt = "\n".join(lines)
    code, _ = asm68k.assemble(bt, 0x200000)
    baddr = state.alloc_p(len(code), 2, "Arrange bullet types 0x26..0x28")
    code, bsyms = asm68k.assemble(bt, baddr)
    ps.add(baddr, code, "Arrange bullet types 0x26..0x28 (A0A9 0x95B070, 0x95E010, 0x960FB0)")
    total += len(code)
    for at_ in breads:
        assert p[at_:at_ + 6] == bytes.fromhex("43f9") + struct.pack(">I", btab), f"bullet type table reader {at_:#x}"
        ps.add(at_ + 2, struct.pack(">I", bsyms["bullet_types"]), f"bullet type table reader {at_:#x}")
    ps.add(bclamp, struct.pack(">H", max(bhandlers)), f"Arrange bullet type clamp {max(bhandlers):#x}")
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
    return (f"Arrange restructured volleys: {n} routines, the core turret and the stage 6 boss damage "
            f"chain ({total} bytes)")
