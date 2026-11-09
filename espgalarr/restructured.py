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
Stage 6 boss damage chain (BOSS6_DAMAGE): Arrange's functions for the seven boss forms
(A0A5 0x70B7E0 .. 0x720E30), which add the parts' multiplier fields, ported whole from 0x1A3E10.
Stage clear (CLEAR_TEXT): Arrange shows no story text after a stage, so the clear sequence is 27
frames shorter (273 logic frames after stage 1, as on the PS2), and (CLEAR_ILLUSTRATION) it draws no
story illustration.
"""
from __future__ import annotations

import re
import struct

from .pgm import asm68k, m68k
from .stages import layout

# Arrange routine -> (68k routine it replaces, its first bytes there; "??": bytes the layout
# stage may change, record offsets)
ROOTS = {
    # stage 2 boss part run 0x192E3C (pointer table 0x14F712): a 5-way type 0x0B fan where the
    # arcade fires one shot
    ("A0A1", "A0AB"): {0x65D3C0: (0x1920AA, "@xx??xx??xx??xx??xxxxxx:d74481b735ba9cd3"),
                       0x6731B0: (0x192E3C, "@8:0627d4e5e7bf5367"),
                       # the stage 2 boss's damage handler (arcade 0x190B9A): before each form's
                       # kill reward it merges the other records' hit marks into record 0 (hit
                       # counts summed, capped at 0x40); the PCSX2 Black run's boss (value 3000)
                       # scores 26 x the value for the arcade code's 10 x
                       0x68F310: (0x190B9A, "@xx??xxxx:b073a95de6ae7f75")},
    # stage 1 sub-tasks 0x01 (types 0x01 / 0x0F, 6 calls for 4), 0x0B (type 0x0A fans, 12 for 6)
    # and 0x0D (A0A0 0x663BE0: the boss's 9-shot type 0x0F fan, the arcade's has 5)
    ("A0A0", "A0AA"): {0x6DAF70: (0x1B1996, "@6:ea458f9f39e53ec2"), 0x655400: (0x18D6EE, "@8:05d1ae687d72f3c3"),
                       0x663BE0: (0x18DD58, "@6:ad83ddce14e18bb6"),
                       0x651800: (0x18D4EC, "@8:97a03d2bd97a165d"), 0x661A70: (0x18DCA0, "@8:2103fd20efb2e0d0"),
                       # the stage 1 boss's first-visit phase 3 (phase table 0x18AA44): Arrange
                       # fires each type 6 shot as a 3-way spread (A0A9 0x8DB320) and the type 0x0E
                       # group through its windowed helper wrapper (porter.INLINED_WRAPPERS)
                       0x6A7E20: (0x18AEF6, "@8:18565fcaa6c01b0b"),
                       # the stage 1 boss's damage handler (arcade 0x18DEE4): before a form's kill
                       # reward it merges the hit marks of the boss's parts (its six part lists,
                       # A0A0 0x6C7BD0) into record 0's, capped at 0x40 (the PCSX2 Black run's
                       # boss kill: 26 x the value, the arcade code 10 x)
                       0x6C08D0: (0x18DEE4, "@8:45eb5f5f5971bfd3")},
    # stage 4 sub-tasks 0x0B (5 type 0x0F shots for 1) and 0x29 (a 10-shot type 0x06 fan for 2);
    # enemy type 0x74's run (A0A3 0x6C2790: a 9-shot type 0x0D fan, offsets from 0x15CD52, where
    # the arcade fires one shot)
    ("A0A3", "A0AD"): {0x665CD0: (0x19BD2C, "@8:8cd46d62c352499c"),
                       0x664CE0: (0x19BCAC, "@8:2de3a5a9a9d9694e"), 0x67CD70: (0x19CB1C, "@8:85716d149230f918"),
                       0x6C2790: (0x19D312, "@8:72d5b79f9c84838a"),
                       # sub-task 7 (table 0x19B284): the mid-boss's 6-type bursts fire each
                       # shot as a +/- 0x10 pair (A0A9 0x8D0370) where the arcade fires one
                       # (stage 4 play 13000: 48 bullets a burst for 24)
                       0x653A50: (0x19B566, "@8:09af28a20be5b2f5"),
                       # the stage 4 boss's damage handler (arcade 0x1993E4): before its kill
                       # reward it merges the records' hit marks into record 0 (capped at 0x40)
                       # and keeps them in 0x813A6A (PS2 0x642326, a long of the stage variable
                       # block 0x167CC0 clears), which its parts copy (score.PART_KILL_SITES)
                       0x6CB790: (0x1993E4, "@xx??xx:6ac3d427605f2204")},
    # stage 3 sub-tasks 0x03 / 0x07 (the boss's volleys; Arrange adds a type 0x18 fan to 0x07)
    ("A0A2", "A0AC"): {0x6981A0: (0x195922, "@8:9737ef56df6a8628"), 0x6713B0: (0x197996, "@8:bd88c5837fae03ec"),
                       0x651180: (0x1966C2, "@6:dd69dec56bf03ad5"), 0x652CA0: (0x196860, "@6:409b3b8b0046697d"),
                       # sub-task 0x05 of the table at 0x1B6D90: two type 0x15 side shots and a
                       # faster centre per offset (the arcade fires the centre only). Only its
                       # entry is overwritten: 0x1B738A's `bcc $1b7326` into the body still finds
                       # the arcade's rts there
                       0x6B08D0: (0x1B72A8, "@6:ea458f9f39e53ec2"),
                       # the stage 3 boss's sub-task 0x0A (run 0x1974E6 = A0A2 0x6617D0, the same
                       # code) fires through four state routines (table 0x197556 = PS2 0x6CABD0,
                       # init / fire pairs; the inits are the arcade's): Arrange's fire routines
                       # make 8 / 8 / 13 / 16 helper calls where the arcade's make 3 / 3 / 8 / 8
                       0x6629F0: (0x1975BE, "@8:b6a87adcb44e2619"), 0x6652B0: (0x19765C, "@8:060d8074108f2cda"),
                       0x6680D0: (0x1976FE, "@8:8bdd4466e734f89c"), 0x66C2B0: (0x197826, "@8:b6a87adcb44e2619"),
                       # the stage 3 boss's damage handler (arcade 0x193412): before each form's
                       # kill reward (3000 / 5000) it merges the records' hit marks into record 0
                       # (capped at 0x40), as stage 1's and stage 2's do
                       0x69D6E0: (0x193412, "@xx??xx:6ac3d427605f2204")},
    # stage 5 sub-task 0x0B (A0A4 0x667A40 @ 0x1BDAFC, the type 0x28 fans: Arrange's body 0x666930
    # calls its window blocks 0x6647A0 / 0x6625E0 twice each, two helpers a window, where the
    # arcade inlines four blocks with a d1 -/+ 0x20 of their own; the blocks alone, ported at the
    # arcade's mid-routine 0x1BDB16 / 0x1BDD86, tested the arcade's d1: porter.INLINED_WRAPPERS)
    # stage 5 boss's sub-task 3 run (A0A4 0x65CCC0 @ 0x1BD028): the boss's volleys through the
    # 5 x 3 spread wrapper 0x655A60. Its end routine (0x68A700 = 0x1BC980) spawns the boss parts
    # through a loop (0x68A0F0) the map had taken for the 0x179A82 call inside it. Its fire blocks
    # (0x652EE0 / 0x656D50 / 0x658040) were mapped to labels inside the arcade routine, whose
    # inline blocks go on to step the state's counters, which the Arrange caller steps again:
    # state 0 ran at twice the rate and the end routine came early, splitting stage 5 at play
    # 9457 (records 94 / 101 stopped). porter.INLINED_WRAPPERS, TEXT_FIXES "A0A4"
    ("A0A4", "A0AE"): {0x668CB0: (0x1BE0C2, "@6:ea458f9f39e53ec2"), 0x667A40: (0x1BDAFC, "@6:ea458f9f39e53ec2"),
                       0x65CCC0: (0x1BD028, "4a2e00cf66000804"),
                       # the stage 5 boss's damage handler (arcade 0x1BC532): before its kill
                       # rewards (7000, 800 for the second record pair, 50000) it merges the
                       # records' hit marks into record 0 (capped at 0x40)
                       0x6825E0: (0x1BC532, "@xx??xx:6ac3d427605f2204")},
    # (A0A5 also: the type 0x49 run 0x673AB0 = 0x1AB12A, its type 0x1A shot as three and its
    # type 0x0A / 0x0D fans through the conditional trampoline 0x66B6E0, specialised per helper)
    # (and 0x69D780 = 0x1A6DD4: a 3-shot type 0x14 spread every 16 frames on top of the
    # arcade's type 0x0D pair, its death paths kept the arcade's: TEXT_FIXES "A0A5")
    # (and the boss's type 0x7D object run 0x6AE9F0 = 0x1A74AA: it fires from all 16 parts where
    # the arcade loops 12, its second volley three shots a part, angle +- 0x78)
    ("A0A5", "A0AF"): {0x6AE9F0: (0x1A74AA, "@8:a5b78a0c8a61448b"), 0x673AB0: (0x1AB12A, "@8:fc53d95d55e55f72"), 0x69D780: (0x1A6DD4, "@8:e169eeea265e98f7"), 0x6D05A0: (0x1A63A4, "@8:10e697553994a2ff"), 0x6D4EB0: (0x1A6B54, "@8:ddb9f6034c966a66"),
                       0x6D35C0: (0x1A688E, "@6:ebbfa65fa12fa4d3"), 0x6C3FC0: (0x1A5E2C, "@6:96c41863f8767a1c"),
                       # sub-tasks 0x03 (14 calls for 3, 4 of them through the spread helper),
                       # 0x19 (a 15-shot type 0x0D spread for 5) and 0x21 (a 5-way type 0x0E fan for 1)
                       0x6B2EC0: (0x1A53FC, "@8:ad8201e5dc9b538b"), 0x6C5E20: (0x1A5EB2, "@8:5a996abd75f8a197"),
                       0x6CD630: (0x1A6274, "@8:762eeb35882edf0b"),
                       # the stage 6 mid-boss's sub-task 0x09 (table 0x1AC48E): pairs of type 0x19
                       # (speed -2) and 0x0F (+2) shots through the trampoline 0x6505A0
                       # (porter.is_trampoline) where the arcade fires single type 0x0F shots
                       0x65A920: (0x1ACF60, "@8:b09f2926a173b667"),
                       # and its sub-task 0x01 (type 0x0D fans of 5 x 3 through the trampoline;
                       # the arcade fires 2 or 3 shots a direction, faster outside 0x40..0xC0)
                       0x651720: (0x1AC724, "@xx??xxxx:9c3e4c94d4f6b06b")},
    # mode-core enemy runs Arrange rewrote (tools/core_audit.py found their constants unpaired):
    # A0A9 0xADEEF0 = 0x16778A (a 7-shot fan where the arcade fires 5, through Arrange's shot
    # helper), 0xAE50C0 = 0x167E7E, 0x9D64F0 = 0x150D7C (the audit's 0x161EB4 is a crosswise
    # pair: 0x161EB4's twin is A0A9 0x75D030, which keeps the arcade's `cmpi.w #$400`)
    ("A0A9", "A0A8"): {0x947FE0: (0x177692, "@8:49d0fbd06055877d"), 0xADEEF0: (0x16778A, "@10:f9eae9ac82166a27"), 0xAE50C0: (0x167E7E, "@10:f9eae9ac82166a27"),
                       0x9D64F0: (0x150D7C, "@6:cf8521b213a45336"),
                       # type 0x3E (its death routine A0A9 0xA484B0 adds Arrange's shots while
                       # 0x813A18 is 1; over the constant patches of 0x15734E)
                       0xA43BD0: (0x15734E, "@10:f9eae9ac82166a27"),
                       # type 0x8D (A0A9 0xA94DA0, stage 2): 5 shots through the shot helpers where the arcade fires 3
                       0xA94DA0: (0x1660F4, "@8:5012b53fea1a1fdd"),
                       # (and A0A9 0xA3AD00 = 0x155514, stage 3; its death path the arcade's 0x1554E2)
                       0xA3AD00: (0x155514, "@xxxx??xxxx:232cdc1b88cd132c"),
                       # (and A0A9 0x7529A0 = 0x1618BC, stage 5: its fans through the shot helper
                       # 0x8BF7F0, more shots than the arcade's ten calls; draw table the arcade's)
                       0x7529A0: (0x1618BC, "@10:f2c7f60ee447b771"),
                       # type 0x3D (stage 4 boss, 0x155D5A; over its constant patches): its
                       # fade script, draw table, sound and merge calls are the arcade's
                       # (TEXT_FIXES "A0A9")
                       0x9DC1F0: (0x155D5A, "@8:9f634f2a900d04b8"),
                       # runs whose shots the Kakusei sites never reached (the build kept the
                       # arcade's plain allocator calls): types 0x3B / 0x29 / 0x26 (two fans of a
                       # helper where the arcade fires one shot; their death routines are the
                       # arcade's, which score.py's MERGE_SITES hook, TEXT_FIXES), 0x30, 0x2C and
                       # 0x39 (0x161EB4's true twin)
                       0xA15D10: (0x154306, "@xxxx??xx:cf295eeec8e5fc2b"), 0xA054A0: (0x152A86, "@xxxx??xx:cf295eeec8e5fc2b"),
                       0xA0DFD0: (0x153CD6, "@xxxx??xx:cf295eeec8e5fc2b"), 0xAF8190: (0x159216, "@8:b0c18f1aac558a74"),
                       0xA40CD0: (0x1559D6, "@8:42fa2299f4881e20"), 0x75D030: (0x161EB4, "@8:42fa2299f4881e20"),
                       # and two the sites reached only in part: types 0x2F and 0xA1
                       0x74E5E0: (0x16137A, "@8:42fa2299f4881e20"), 0xAB7330: (0x16AC34, "@8:5012b53fea1a1fdd")},
}
# Routines whose Arrange shot-helper calls tools/kakusei_sites.py leaves out (a helper standing
# for several arcade calls: no 68k call is redirected, so the 68k fires the arcade's shots and no
# Kakusei extra shot; TODO.md item 3), reached in a PCSX2 fire-held run (work/scratch/fork5/reach):
# ported whole at their sub-task entries
ROOTS[("A0A1", "A0AB")].update({0x6A6740: (0x1B4ABE, "@8:bd88c5837fae03ec"),
                                # the stage 2 mid-boss's (type 0x90) sub-task 5, its last attack
                                # (table 0x1B47A2; started by 0x1B2CFC, phase 12's init, once a hit
                                # leaves the mid-boss's HP $16(a5) <= 0xA200, 0x1B458C): the first
                                # aimed shot through 0x8C6A10 and the others as +/-4 pairs
                                # (0x8CAE00) where the arcade fires single shots. Bullets match the
                                # PS2 with it started by a slot poke at play 0xE40 (TODO.md item 6)
                                0x6AA6E0: (0x1B4D94, "@8:bd88c5837fae03ec")})
ROOTS[("A0A2", "A0AC")].update({0x65AE50: (0x196DC2, "@8:7c4a7862422f1bcc"),
                                # (the volley block 0x19588C, where the 68k falls into 0x195922:
                                # Arrange's routine ends `jsr 0x195922 ; rts`; three helper fans,
                                # 0x8BFD70 and 0x8BEF50 at +/-8, for the arcade's two 0x170F36 shots)
                                0x697120: (0x19588C, "@8:0b8459bfadcc4948")})
ROOTS[("A0A3", "A0AD")].update({0x66E780: (0x19C224, "@8:e5db3f1f414e2b84"),
                                0x679370: (0x19C91E, "@8:b18449bf25c5dc3d"),
                                0x681510: (0x19CDC4, "@8:28f41b4b57326da9")})
ROOTS[("A0A9", "A0A8")].update({0xA23F10: (0x153536, "@8:ef028bd9276afe38"),
                                0xA24790: (0x153580, "@8:f8fd58b7300d068c")})
# Stage 6 object type 0x7F (68k init 0x1A7728, run 0x1A7778; A0A5 init / run 0x6E01F0): its
# spawners 0x1A6108 / 0x1A61D2 are ported Arrange units that fill the spawn entry in Arrange's
# layout (a long $1C: speed -4 and bullet type 0x24, the flag at $22, a word at $24); Arrange's
# handler reads it there, its timer moved from $1E/$1F to $20/$21 (68k address, the arcade's
# instruction, Arrange's as assembly text; ';' separates two)
TYPE_7F_LAYOUT = (
    (0x1A774C, "@6:1361bbc80f8279f3", "move.w #2,$20(a5)"),
    (0x1A7784, "@4:7cf2fb65ea119fbe", "subq.b #1,$20(a5)"),
    (0x1A778A, "@6:adaf7fb56e1a8a0a", "move.b $21(a5),$20(a5)"),
    (0x1A7790, "@4:8425939647542df4", "tst.b $22(a5)"),
    (0x1A77AC, "@6:132b7ec49f15479d", "move.l $1c(a5),d0 ; nop"),
    (0x1A77B2, "@4:7ebec15f066efdc4", "move.w $24(a5),d5"),
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
# Stage clear sequence, step 0's fade script (68k 0x180DE2, fade engine 0x11F19C: a count, then
# per palette bank, offset, colours - 1, speed index): Arrange's (A0A9 0xC01ED0) drops palette
# 0x0080 and fades six. The engine takes 8 logic frames at speed 0 for any count, on both
# machines (MAME trace, stage 1: play 0x40B4..0x40BB); the 68k's longer step 1 in frames (12) is
# lag from fading seven palettes at once, not logic (68k script, its count, overlay, Arrange's)
CLEAR_FADE = (0x180DE2, 7, "A0A9", 0xC01ED0)
# Stage clear draw (68k 0x180EA0, A0A9 0x876E60): the arcade's calls 0x180EDA, 0x180F08 and the
# story illustration 0x1812CC (pieces from the stage's table 0x181326, from step 4 while palettes
# 0x280 / 0x2C0 fade in); Arrange's calls the first two only (0x876F40, 0x877170), so the PS2 shows
# white between the emerald screen and the next stage (PCSX2, every stage): the `bsr.w $1812cc`
# becomes two nops (site, arcade bytes)
CLEAR_ILLUSTRATION = (0x180ED4, "@4:1f9d3593ce63f918")
# overlays whose roots call helpers with constant native arguments (A0A2 0x6981A0 calls A0A9
# 0x8E6C40, the bullet limit setter the arcade inlines, with the limit in $a0; A0A5 0x673AB0
# calls the conditional trampoline 0x66B6E0 with a helper in $a0)
SPECIALISE = {"A0A2", "A0A5"}
# calls of the ported routines the overlay's learned map does not know, checked by hand
# (porter.port_roots `calls`): the bosses' per-frame helpers A0A2 0x6A42F0 / A0A3 0x6D6AA0 (three
# calls of flag-only tests, as the arcade's 0x1937B8 / 0x199914) and Arrange's kill reward with a
# position, A0A9 0x81B660 (score.KILL_SITES: the 68k's 0x178956 runs the ported one)
CALLS = {"A0A2": {0x6A42F0: 0x1937B8}, "A0A3": {0x6D6AA0: 0x199914}, "A0A4": {0x81B660: 0x178956}}
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
    # (the same routine as the de-translator now recovers it: its callers take 0x11AA26 instead)
    ("0x1bb970", "address 0xb030d5 not mapped"), ("0x1bb970", "address 0xb030d4 not mapped"),
    ("0x1bb970", "ROM address 0x11a79c only guessed"),
    ("0xadeef0", "ROM address 0x167ca0 only guessed: lea.l\t$167ca0.l,a0"),
    ("0x153f80", "no recovered source"),
    ("0x9dc1f0", "ROM address 0x1410f8 only guessed"), ("0x9dc1f0", "ROM address 0x150ebc only guessed"),
    ("0x796fa0", "no recovered source"), ("0x9b8430", "no recovered source"),
    ("0xa15d10", "call 0x9b6810 passes a0=a5+30,a1=$c0cd30 natively"),
    ("0xa15d10", "call 0x9b6810 passes a0=a5+31,a1=$c0cd30 natively"),
    ("0xa0dfd0", "call 0x9b6810 passes a0=a5+30,a1=$c0cd30 natively"),
    ("0xa0dfd0", "call 0x9b6810 passes a0=a5+31,a1=$c0cd30 natively"),
    ("0x9b8400", "no recovered source"), ("0xaf8180", "no recovered source"),
    ("0xa3ab20", "ROM address 0x155b2e only guessed"),
    ("0x7529a0", "ROM address 0x160e22 only guessed"),
    ("0xa40b00", "ROM address 0x155bae only guessed: lea.l\t$155bae.l,a1")],
    # (the second word of the arcade's own `movem.w $813d08.l,d2-d3`, 0x192EE4: kept as it is)
    "A0A1": [("0x6731b0", "new variable 0x813d0a not yet assigned")],
    # (the stage 1 boss block's words: resolved by TEXT_FIXES "A0A0" below)
    "A0A0": [("0x6c08d0", "new variable 0x813d24 not yet assigned"),
             ("0x6c08d0", "new variable 0x813d20 not yet assigned")],
    "A0A2": [("0x652ca0", "call 0x64f450 passes a0=[a4+8].b natively"),
             ("0x652ca0", "call 0x64ece0 passes a0=[a4+24].b natively"),
             ("0x651180", "call 0x64ece0 passes a0=[a4+32].b natively"),
             ("0x64ece0", "uses a MIPS argument the routine is entered with: move.b\tARG_A0+3,d1"),
             ("0x64f450", "uses a MIPS argument the routine is entered with: move.b\tARG_A0+3,d1"),
             ("0x6b08d0", "ROM address 0x1b5d3e only guessed: lea.l\t$1b5d3e.l,a3")],
    # (dropped by TEXT_FIXES below)
    "A0A3": [("0x9b8400", "no recovered source")],
    # (the stage 5 boss's sub-task 3 run: resolved by TEXT_FIXES "A0A4" below)
    "A0A4": [("0x65ccc0", "call 0x655a60 passes a0=[a4+22].b natively"),
             ("0x655a60", "uses a MIPS argument the routine is entered with: move.b\tARG_A0+3,d1"),
             ("0x65ccc0", "call 0x656d50 passes a0=[a4+22].b natively"),
             ("0x656d50", "uses a MIPS argument the routine is entered with: move.b\tARG_A0+3,d1"),
             ("0x68a700", "call 0x68a0f0 passes a0=a6+178 natively"),
             ("0x68a0f0", "unknown: ??? move.l [arg0],2(a0)")],
    # (replaced by TEXT_FIXES below: the arcade's tables and its 0x1AC594)
    "A0A5": [("0x69d780", "new variable 0x80116a not yet assigned"),
             ("0x6ae9f0", "ROM address 0x1a5cd2 only guessed: lea.l\t$1a5cd2.l,a0"),
             ("0x69d780", "ROM address 0x1a7058 only guessed"),
             # (the unreferenced death callees, replaced by the arcade's above)
             ("0x9b5f40", "ROM address 0x16c018 only guessed"),
             ("0x796fe0", "unknown: ??? if eq(lo16(d0@4), $58)"), ("0x796fe0", "unknown: ??? if eq(lo16(d0@4), $48)"),
             ("0x796fe0", "unknown: ??? if eq(lo16(d0@4), $63)"), ("0x796fe0", "unknown: ??? if eq(lo16(d0@4), $64)"),
             ("0x153f80", "no recovered source"), ("0x69d3a0", "no recovered source"),
             ("0x64ffc0", "ROM address 0x1a8868 only guessed: lea.l\t$1a8868.l,a1"),
             ("0x64ffc0", "ROM address 0x1a8910 only guessed: lea.l\t$1a8910.l,a0"),
             ("0x64ee10", "unknown: ??? flags Z=eq0(and(or(shl(CCR{}, $8), CCRB{}), $fffe))"),
             ("0x64ee10", "unknown: ??? CCR = ccr_unknown(shr(and(or(shl(CCR{}, $8), CCRB{}), $fffe), $8))"),
             ("0x64ee10", "unknown: ??? CCR = ccr_unknown(and(or(shl(CCR{}, $8), CCRB{}), $fffe))"),
             ("0x64ee10", "unknown: ??? flags Z=eq0(or(lo16(or(shl(CCR{N=bit7([add(a0, $18)].b)"),
             ("0x64ee10", "unknown: ??? CCR = ccr_unknown(shr(or(lo16(or(shl(CCR{N=bit7([add(a0, $18)].b)"),
             ("0x64ee10", "unknown: ??? CCR = ccr_unknown(or(lo16(or(shl(CCR{N=bit7([add(a0, $18)].b)")]}
# Arrange's second score-variable pointer table (A0A9 0xADDD00 / 0xAE50C0, PS2 0xC12950, 16
# entries: the 0x813A4E.. variables three entries on), rebuilt in ROM from the arcade's first
# (0x167CE0 = PS2 0xC12910): (PS2 table, PS2 first table, ROM first table, entries)
CORE_MULT_PTRS = (0xC12950, 0xC12910, 0x167CE0, 16)
# Type 0x3E's aimed 3-way (A0A9 0xA43BD0) takes its spawn offset from the 32-entry table PS2
# 0xC103D0 (= ROM 0x157A7C) by the angle & 0xF8, so angles from 0x80 read past it: on the PS2
# the next table in its layout (0xC10450, an enemy init block; small offsets), in the ROM code
# (offsets that put the shots off-screen). The port reads a 64-entry copy, the ROM table and the
# PS2 bytes after it: (PS2 table, ROM table, entries in the ROM)
CORE_AIM_OFFSETS = (0xC103D0, 0x157A7C, 32)
# the stage 3 volley task's bullet-limit calls (A0A9 0x8E6C40 with 0x4F, specialised): the port
# keeps the arcade's group-count variables and values (0x81B55A / 0x81B562; 0x12 at the start,
# 0xE at the end, as in 0x195922: the walkers have their own count, layout.ARR_CAP, and other
# routines read the arcade's), where the map took the PS2 variable for ARR_CAP / ARR_DELTA
# (overlay: (old, new, occurrences in order))
TEXT_FIXES = {
    # the stage 1 boss's damage handler (0x6C08D0): its block's +0x16 / +0x17 / +0x18 are the
    # arcade's 0x813D1E / 0x813D1F / 0x813D20 (the learned pair put +0x16 at 0x81E116, another
    # routine's word); its new +0x1C long (merged part marks) gets its own word
    "A0A0": [("$81e116.l", "$813d1e.l", 6), ("$81e117.l", "$813d1f.l", 1),
             ("$813d24.l", f"${layout.BOSS1_MARKS:x}.l", 6)],
    "A0A9": [
                  # the type 0x20 spawner's run animates PS2 atlas sprites 0x32D0..0x32D3 = ROM
                  # 0x28D0E0 + 0x24 n (sprite_ids.located), as the arcade run 0x177692 does
                  ("addq.l\t#1,(a0)\n\tcmpi.l\t#$32d4,(a0)", "addi.l\t#$24,(a0)\n\tcmpi.l\t#$28d170,(a0)", 1),
                  ("move.l\t#$32d0,(a0)", "move.l\t#$28d0e0,(a0)", 1),
                       # tables the map only guessed, checked against the PS2 data: 0x16BF84 is
                       # the arcade's draw jump table (A0A9 0x9B4060 = its inline call), 0x167E24
                       # the RNG-picked position table (PS2 0xC129B0 -> sprites 0x3858.. = ROM
                       # 0x2E6C18..); 0x167CA0 / 0x167CE0 are the arcade's own (kept)
                       ("$16bf60.l", "$16bf84.l", 1), ("$167d80.l", "$167e24.l", 1),
                       # the PS2 sound request (ELF 0x153F80, native) where the arcade's 0x17D312
                       # calls 0x17D064 (A0A9 0x796FE0 = 0x17D312, one call per sound id)
                       ("jsr\tF_153f80", "jsr\t$17d064.l", 4),
                       # type 0x8D (A0A9 0xA94DA0): its hit-mark merge (0x9B8400, score.py mark_merge)
                       # inlined; its death path is the arcade's 0x166410 (the same spawns, the
                       # kill reward 0x178140)
                       ("\tor.b\td5,35(a6)\n\tjsr\tF_9b8400\n\tmoveq\t#0,d5\n\tadd.w\t32(a6),d5\n\tadd.w\t76(a6),d5\n\tsub.l\td5,70(a6)\n\tbmi\tL_a9b930",
                        "\tor.b\td5,35(a6)\n\tmove.l\t36(a6),d5\n\tor.w\t82(a6),d5\n\tswap\td5\n\tadd.w\t80(a6),d5\n\tswap\td5\n"
                        "\tmove.l\td5,36(a6)\n\tcmpi.w\t#$40,36(a6)\n\tbcs\tL_8d_mm\n\tmove.w\t#$40,36(a6)\nL_8d_mm:\n"
                        "\tmoveq\t#0,d5\n\tadd.w\t32(a6),d5\n\tadd.w\t76(a6),d5\n\tsub.l\td5,70(a6)\n\tbpl\tL_8d_alive\n"
                        "\tjmp\t$166410.l\nL_8d_alive:", 1),
                       # type 0x3E's aimed 3-way fires when the mod-24 frame counter 0x801170 is 0
                       # (PS2 0x358034: the ELF's copy table 0x26B378.. pairs 0x801164 + 2k with
                       # 0x35804C - 4k); the map had no sample there and took the game counter
                       ("lea.l\t$801164.l,a0\n\tcmpi.w\t#1,$813a18.l", "lea.l\t$801170.l,a0\n\tcmpi.w\t#1,$813a18.l", 1),
                       # and its offsets for angles from 0x80 are the PS2's (CORE_AIM_OFFSETS)
                       ("andi.w\t#$f8,d3\n\tlea.l\t$157a7c.l,a0", "andi.w\t#$f8,d3\n\tlea.l\tcore_aim_offsets,a0", 1),
                       # type 0x3D (A0A9 0x9DC1F0): its fade script PS2 0xC0E5C0 is the arcade's
                       # 0x155C64 in the PS2 layout (entries two bytes longer, palettes by PS2
                       # address), started through the fade engine (ELF 0x2718C0 = 0x11F012);
                       # its draw table PS2 0xC0EDC0 = 0x156D48 (PS2 sprite numbers for ROM
                       # addresses); its sound call A0A9 0x796FA0 = 0x17D2AA; and 0x9B8430, the
                       # hit-count half of the merge (score.py mark_merge), inlined
                       ("$1410f8.l", "$155c64.l", 1), ("jsr\tF_2718c0", "jsr\t$11f012.l", 1),
                       ("$150ebc.l", "$156d48.l", 1), ("jsr\tF_796fa0", "jsr\t$17d2aa.l", 1),
                       ("\tjsr\tF_9b8430", "\tmove.l\t36(a6),d5\n\tor.w\t82(a6),d5\n\tswap\td5\n\tadd.w\t80(a6),d5\n"
                        "\tswap\td5\n\tmove.l\td5,36(a6)\n\tcmpi.w\t#$40,36(a6)\n\tbcs\tL_3d_mm\n\tmove.w\t#$40,36(a6)\n"
                        "L_3d_mm:", 1),
                       # ELF copies of 68k routines: 0x1D0960 = 0x11911C (its 0x8139A2 mapped as
                       # 0x800EFC), 0x1BB970 = 0x11AA26 (RNG pick, mis-split by the de-translator)
                       ("$800efc.l", "$8139a2.l", 1), ("jsr\tF_1bb970", "jsr\t$11aa26.l", 2),
                       # Arrange's second pointer table, rebuilt in ROM (CORE_MULT_PTRS)
                       ("$167d20.l", "core_mult_ptrs", 3),
                       # A0A9 0x9B6810 takes its byte pointer and table natively (a0 = a5 + $1E,
                       # a1 = PS2 0xC0B2B0 = ROM 0x15C852, sprite_ids.located): inlined as the
                       # arcade does it (0x15100E)
                       # (0xA0DFD0 / 0xA15D10: a5 + $1E then $1F, a1 = PS2 0xC0CD30 = ROM 0x15E352,
                       # as the arcade's 0x1547AC / 0x1547EA; in text order)
                       ("jsr\tF_9b6810", [f"move.b\t{o}(a5),d2\n\tandi.w\t#$3e,d2\n\tadd.w\td2,d2\n\tlea.l\t${t:x}.l,a0\n"
                                          "\tmove.l\t(a0,d2.w),d2" for o, t in ((30, 0x15C852), (30, 0x15C852),
                                                                               (30, 0x15E352), (31, 0x15E352),
                                                                               (30, 0x15E352), (31, 0x15E352))], 6),
                       # the death routines of types 0x3B / 0x29 / 0x26 / 0x30: the arcade's (its
                       # tables are Arrange's to their 0xFFFF ends; the hit-mark merge is score.py's
                       # hook at 0x1542B4 / 0x152A34 / 0x153C84), entered by `bmi` from the run
                       ("jsr\tF_a159e0", "jsr\t$1542b4.l", 1), ("\tjsr\tF_a3ab20\n\trts", "\tjmp\t$1554e2.l", 1),
                       # (0x7529A0's draw table: PS2 sprite numbers for the arcade's 0x161BE8 ROM addresses)
                       # the frame index tables of the runs A0A9 0x74E5E0 /
                       # 0x7529A0: the PS2's 0xB337D0 steps 17 records and mirrors them (bit 6)
                       # = the arcade's 0x161574, which precedes their record tables 0x1615F4 /
                       # 0x161BE8; the map took 0x162236 (33 records), so frames 17.. read past
                       # the records (stage 4 garbled sprites, offsets 0x380 / 0x580 / 0xB40)
                       ("lea.l\t$162236.l,a0\n\tadda.w\td0,a0\n\tmove.w\t(a0)+,d0\n\tor.w\t(a0),d4\n\tlea.l\t$1615f4.l,a0",
                        "lea.l\t$161574.l,a0\n\tadda.w\td0,a0\n\tmove.w\t(a0)+,d0\n\tor.w\t(a0),d4\n\tlea.l\t$1615f4.l,a0", 1),
                       ("lea.l\t$162236.l,a0\n\tadda.w\td0,a0\n\tmove.w\t(a0)+,d0\n\tor.w\t(a0),d4\n\tlea.l\t$160e22.l,a0",
                        "lea.l\t$161574.l,a0\n\tadda.w\td0,a0\n\tmove.w\t(a0)+,d0\n\tor.w\t(a0),d4\n\tlea.l\t$160e22.l,a0", 1),
                       # the run A0A9 0xAE50C0: its per-angle sprite table PS2 0xC12A10 (sprite
                       # numbers 0x1919..0x1927 at entries 14-20, 42-44, 50-54) is the arcade's
                       # 0x16853A (as its twin 0x167D7A; 0xC12B10 = 0x16863A follows); the map took
                       # 0x1187DC, code (stage 2 garbled sprites, offset 0x102E)
                       ("lea.l\t$1187dc.l,a0\n\tmove.l\t(a0,d0.w),10(a6)", "lea.l\t$16853a.l,a0\n\tmove.l\t(a0,d0.w),10(a6)", 1),
                       ("lea.l\t$160e22.l,a0", "lea.l\t$161be8.l,a0", 1), ("jsr\tF_a05170", "jsr\t$152a34.l", 1),
                       ("jsr\tF_a0dc90", "jsr\t$153c84.l", 1), ("jsr\tF_af8180", "jsr\t$1591ca.l", 1),
                       # (their Arrange copies stay unreferenced; their merge call, native, is dropped)
                       ("\tjsr\tF_9b8400\n", "", 4),
                       # type 0x2C's death table (PS2 0xC0FFD0 = ROM 0x155BB0 to its 0xFFFF end)
                       ("$155bae.l", "$155bb0.l", 1)],
              "A0A2": [("$818352.l", "$81b55a.l", 9), ("$818354.l", "$81b562.l", 3),
                       # (0x652CA0 ends its volleys as the arcade's 0x196974 does, with 0xE)
                       ("#$4f,d0", ["#$e,d0", "#$12,d0", "#$e,d0"], 3),
                       ("#$4f,$81b55a.l", ["#$e,$81b55a.l", "#$12,$81b55a.l", "#$e,$81b55a.l"], 3),
                       # the shot offset wrappers (A0A2 0x64F450 / 0x64ECE0, porter.INLINED_WRAPPERS)
                       # take their angle byte natively: the callers load it into d1, as the
                       # arcade's inline code does (0x196896 / 0x1966EE / 0x19691A)
                       ("\tmoveq\t#0,d1\n\tmove.b\tARG_A0+3,d1\n\tmove.w\t#$80,d0", "\tandi.l\t#$ff,d1\n\tmove.w\t#$80,d0", 1),
                       ("\tmove.w\t#$80,d0\n\tmoveq\t#0,d1\n\tmove.b\tARG_A0+3,d1", "\tmove.w\t#$80,d0\n\tandi.l\t#$ff,d1", 1),
                       ("\tjsr\tF_64f450", "\tmove.b\t8(a4),d1\n\tjsr\tF_64f450", 1),
                       ("\tmove.b\td1,8(a4)\n\tjsr\tF_64ece0", "\tmove.b\td1,8(a4)\n\tmove.b\t32(a4),d1\n\tjsr\tF_64ece0", 1),
                       ("\tmove.b\td1,26(a4)\n\tjsr\tF_64ece0", "\tmove.b\td1,26(a4)\n\tmove.b\t24(a4),d1\n\tjsr\tF_64ece0", 1),
                       # 0x6B08D0's offset pointer table: PS2 0x6CBAC0 holds the same four
                       # pointers to the same 0x80-byte blocks as the arcade's 0x1B7398 (RAM dumps)
                       ("$1b5d3e.l", "$1b7398.l", 1)],
              # enemy type 0x74's death path calls A0A9 0x9B8400 (native, no recovered source:
              # three calls into 0x9B7E60..); the arcade's makes no such call, so the port leaves it
              # out (the death path stays the arcade's: 0x178694, then 0x19D2DC)
              "A0A3": [("\tjsr\tF_9b8400\n", "", 1)],
              # the stage 5 boss's sub-task 3 run (A0A4 0x65CCC0 @ 0x1BD028): its bullet-limit
              # setters on the arcade's 0x81B55A / 0x81B562 (0x12 entering state 1, 0xE at the
              # end, as 0x1BD0E4 / 0x1BD840); the 5 x 3 spread wrappers 0x655A60 / 0x656D50 take
              # their angle natively ($16(a4), the arcade's 0x1BD1B8): the caller loads d1; the
              # end routine's part spawn loop 0x68A0F0
              # (INLINED_WRAPPERS) stores the native $a0 = a6 + $B2's long, the arcade's
              # `move.l $92(a6),2(a0)` (0x1BC9B6)
              "A0A4": [("$818352.l", "$81b55a.l", 6), ("$818354.l", "$81b562.l", 2),
                       ("#$4f,d0", ["#$12,d0", "#$e,d0"], 2),
                       ("#$4f,$81b55a.l", ["#$12,$81b55a.l", "#$e,$81b55a.l"], 2),
                       ("\tjsr\tF_655a60", "\tmove.b\t22(a4),d1\n\tjsr\tF_655a60", 1),
                       ("\tjsr\tF_656d50", "\tmove.b\t22(a4),d1\n\tjsr\tF_656d50", 1),
                       ("\tmove.b\tARG_A0+3,d1\n", "", 2),
                       ("\tjsr\t$179a82.l\n\tmove.w\t(a1)+,30(a0)",
                        "\tjsr\t$179a82.l\n\tmove.l\t178(a6),2(a0)\n\tmove.w\t(a1)+,30(a0)", 1)],
              # the stage 6 mid-boss's sub-task 0x01: its pattern tables are the arcade's 0x1AC8C8
              # (record offsets) / 0x1AC8B8 (count, offset list: the PS2's lists are the first
              # entries of the arcade's, of which the loop reads 5); its end routine A0A5 0x64EE10
              # (free the part's object, carry set when it had none) is the arcade's 0x1AC594
              "A0A5": [("$1a8868.l", "$1ac8c8.l", 1), ("$1a8910.l", "$1ac8b8.l", 1),
                       ("\tjsr\tF_64ee10", "\tjsr\t$1ac594.l", 1),
                       # the type 0x49 run's speed-from-distance helper (A0A5 0x677720): the map
                       # took the sibling 0x1A7624 (table 0x1A7666, steps of 1); its table, PS2
                       # 0x72BCB0, is the arcade's own 0x1AB402 (steps of 2), so it is 0x1AB3C0
                       ("\tmoveq\t#36,d6\n\tjsr\t$1a7624.l", "\tmoveq\t#36,d6\n\tjsr\t$1ab3c0.l", 1),
                       # (the type 0x7D run's own call of 0x1A7624, with d6 = 14, is the arcade's)
                       # 0x69D780: its spread's gate reads the game counter (PS2 0x35804C =
                       # 0x801164); its death paths go to the arcade's own (0x1A6D4A when
                       # 0x813A44 is set, 0x1A6D48 = burst + death when killed) and the timer
                       # path to the burst 0x1A6CE0, as the automatic port of 0x1A6E28 has it
                       ("$80116a.l", "$801164.l", 1),
                       ("\tbeq\tL_69d840\n\tjsr\tF_69d460\n\trts", "\tbeq\tL_69d840\n\tjmp\t$1a6d4a.l", 1),
                       ("\tbpl\tL_69dd70\n\tjsr\tF_69d3b0\n\trts", "\tbpl\tL_69dd70\n\tjmp\t$1a6d48.l", 1),
                       ("\tbcc\tL_69e188\n\tjsr\tF_69d3a0\n\trts", "\tbcc\tL_69e188\n\tjsr\t$1a6ce0.l\n\trts", 1),
                       # (the unreferenced callees still call the two with no recovered source)
                       ("\tjsr\tF_69d3a0", "\tjsr\t$1a6ce0.l", 1), ("jsr\tF_153f80", "jsr\t$17d064.l", 4),
                       # the type 0x7D run's part sprite table PS2 0x72EA20 (sprites 0x3300 + 0, 1,
                       # 2, 3, 2, 1) is the arcade's 0x1A76C8 (ROM 0x28D620 + 0x24 n)
                       ("lea.l\t$1a5cd2.l,a0\n\tadda.w\t40(a5),a0", "lea.l\t$1a76c8.l,a0\n\tadda.w\t40(a5),a0", 1)]}
# Stage 6 boss damage chain (68k 0x1A3E10..0x1A47C4, one routine falling through a block per
# boss form): Arrange splits it into functions that tail-call the next (A0A5 0x70B7E0 ..
# 0x71D800; the address map pairs them one block off, so the porter refused them and maps their
# tail calls wrongly). Their HP and threshold constants are the arcade's; Arrange clears each
# part's new record words +0x24 / +0x26 while the form lives and, when it dies, merges its parts'
# +0x24 (sum, capped at 0x40) and +0x26 (or) into part 0's before the reward routine. Ported
# whole from 0x1A3E10, the final form (A0A5 0x720E30 = 0x1A4666) included: it clears part 0's
# +0x23..+0x27 while it lives, and its death routine (A0A5 0x727320 = 0x1A4CEC) calls 0x112F02
# first and sets 0x813A8E bit 7 / calls 0x119500 last and only while 0x813A40 is 0 (its two
# other calls are ELF / A0A9 copies, verify.FUNC_FIXES). (pair, entry root, 68k entry, its
# first bytes, other roots, {function: (68k tail call, Arrange function it is)})
BOSS6_DAMAGE = (("A0A5", "A0AF"), 0x70B7E0, 0x1A3E10, "@xx??xxxx:4326fdc34ccdf74c",
                (0x70D3B0, 0x70EF90, 0x710C20, 0x718200, 0x71D800, 0x720E30),
                {0x70B7E0: (0x1A402C, 0x70D3B0), 0x70D3B0: (0x1A3F1E, 0x70EF90),
                 0x710C20: (0x1A4142, 0x718200), 0x718200: (0x1A4362, 0x71D800),
                 0x71D800: (0x1A4666, 0x720E30)})
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
CORE_TURRET_CALL = "@6:5c7f8f66f7bb3ab6"          # the volley calls (jsr $1713ec) the replacement keeps
CORE_TURRET_VOLLEY = (0x177A50, 0x177A92, "@6:632b55e3f04fc352", 0x177A66)
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

# The core's second turret bullet (type 0x24, run 0x177B8E, A0A9 0x955140; fired by the stage 6
# boss's type 0x7F bombs): its shot at angle 0 is at speed $3E - 10 (the arcade's - 4), through
# the shot wrapper (A0A9 0x8E08B0: a second shot, speed + 6, when 0x813A18 is 2), and its wobble
# adds $3A >> 1 (`lsr`: 0, as the init sets $3A to 1) where the arcade adds $3A, so its shots
# keep one speed (PS2 stage 6 play 49000: 80 at speed 3 where the arcade's swept 9..17).
# (68k `move.w $3e(a6),d0 / subq.w #4,d0`, the wobble's end, the 0x1713EC call inside)
CORE_TURRET2_VOLLEY = (0x177BCE, 0x177C12, "@6:d05b495ee9b8be20", 0x177BE2)
CORE_TURRET2_ASM = """
core_turret2_volley:
        move.w  $3e(a6),d0
        subi.w  #$a,d0
        swap    d0
        move.w  #$d,d0
        moveq   #0,d1
        moveq   #0,d3
        move.l  $2(a6),d2
        cmpi.w  #2,$813a18.l
        bne     core_turret2_one
        swap    d0
        addq.w  #6,d0
        swap    d0
        jsr     $1713ec.l
        swap    d0
        subq.w  #6,d0
        swap    d0
core_turret2_one:
        jsr     $1713ec.l
        subq.b  #1,$38(a6)
        move.w  $3a(a6),d0
        lsr.w   #1,d0
        add.w   d0,$3e(a6)
        tst.w   d0
        bmi     core_turret2_neg
        tst.w   $3e(a6)
        blt     core_turret2_end
        neg.w   $3a(a6)
        bra     core_turret2_end
core_turret2_neg:
        cmpi.w  #-8,$3e(a6)
        bgt     core_turret2_end
        neg.w   $3a(a6)
core_turret2_end:
        jmp     ${end:x}.l
"""

# Stage 2 small enemy (68k run 0x192C58, A0A1 0x674E70): Arrange fires its type 0x0D pair at any
# angle and position (the arcade only for angles 0x20..0xE0 and x < 0x5A00) and from an offset
# along the shot (table 0x15CE52 by angle, as the arcade's stage 1 volleys do; the arcade fires
# from the enemy itself). The routine's other code is the arcade's, its death path calls
# unported score code, so only this block is replaced, in place: (block, end, its bytes)
STAGE2_SMALL_FIRE = (0x192D6E, 0x192D8E, "@32:1f781de4b5351ea8")
# Stage 1 type 0x66 enemy (68k run 0x18E59C, A0A0 0x6753B0): Arrange fires every shot of its
# 12-way / 6-way rings (A0A0 0x675C00: d1 to the offset table, no compare); the arcade skips the
# angles outside 0x10..0xF0. Its death path calls unported score code, so only the window test is
# removed, in place: (test, end, its bytes)
STAGE1_RING_WINDOW = (0x18E62E, 0x18E63A, "@12:12a2f1d808048c34")
# Stage 4 boss part ring (68k 0x19EAAE, A0A3 0x6BD760): Arrange fires both shots of every part
# (two helper calls, A0A9 0x8C7F30), the arcade only the angles 0x30..0xCF; the window tests are
# removed in place: (test, end, its bytes)
STAGE4_RING_WINDOWS = ((0x19EB48, 0x19EB56, "@14:83249597123c13f2"),
                       (0x19EB6A, 0x19EB78, "@14:83249597123c13f2"))
STAGE2_SMALL_FIRE_ASM = """
        move.l  #$fff8000d,d0
        move.l  $2(a6),d2
        moveq   #0,d3
        move.b  d1,d3
        addq.b  #2,d3
        andi.w  #$fc,d3
        lea     $15ce52.l,a0
        move.l  (a0,d3.w),d3
        nop
"""


def _matches(b: bytes, head: str) -> bool:
    return m68k.matches(b, head)


def _own_labels(text: str) -> str:
    """Give the de-translator's address-less labels (L_01f..., the exits of an inlined helper such
    as 0x14F0D8's off-screen test) their function's name: two routines of one unit may share
    them."""
    out, fn = [], ""
    for line in text.split("\n"):
        m = re.match(r"^(F_[0-9a-f_]+):", line)
        if m:
            fn = m.group(1)[1:]
        out.append(re.sub(r"\b(L_01f[0-9a-f]{7})\b", rf"\1{fn}", line))
    return "\n".join(out)


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


def _core_aim_offsets(state, p: bytes) -> str:
    """The aimed 3-way's offset table (CORE_AIM_OFFSETS) as assembler data, with the PS2's tail."""
    from .ps2 import overlay
    ps2, rom, n = CORE_AIM_OFFSETS
    core = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    b = core.read(ps2, 8 * n)
    assert b[:4 * n] == p[rom:rom + 4 * n], "aimed shot offset table"
    return "\ncore_aim_offsets:\n" + "".join(f"\tdc.l\t${int.from_bytes(b[k:k + 4], 'big'):x}\n" for k in range(0, 8 * n, 4))


def port_restructured(state, ps: m68k.PatchSet, p: bytes) -> str:
    from .detrans import porter
    from .ps2 import overlay
    texts = []
    bpair, broot, bsite, bhead, bmore, btails = BOSS6_DAMAGE
    for (rn, an), roots in ROOTS.items():
        extra = [broot, *bmore] if (rn, an) == bpair else []
        d = porter.port_roots(list(roots) + extra, state.work / "ps2" / "detrans" / f"restructured_{rn.lower()}.json",
                              r_name=rn, a_name=an, specialise=rn in SPECIALISE, calls=CALLS.get(rn))
        for ps2_, m68_ in ADDR_FIXES.get(rn, {}).items():
            d["text"] = d["text"].replace(f"${ps2_:x}.l", f"${m68_:x}.l")
            d["problems"] = {f: [x for x in pr if f"{ps2_:#x}" not in x] for f, pr in d["problems"].items()}
            d["problems"] = {f: pr for f, pr in d["problems"].items() if pr}
        # (an accepted problem may be gone: the de-translator now recovers some of them)
        for fn_, pr_ in ACCEPTED.get(rn, []):
            if fn_ in d["problems"]:
                d["problems"][fn_] = [x for x in d["problems"][fn_] if not x.startswith(pr_)]
        d["problems"] = {f: pr for f, pr in d["problems"].items() if pr}
        assert not d["problems"], f"problems in the ported {rn} routines: {d['problems']}"
        assert not any(f"${ps2_:x}" in d["text"] for ps2_ in ADDR_FIXES.get(rn, {})), f"{rn} address fixes"
        if rn == "A0A9":
            d["text"] += _core_mult_ptrs(state, p)
            d["text"] += _core_aim_offsets(state, p)
        for old_, new_, n_ in TEXT_FIXES.get(rn, []):
            assert d["text"].count(old_) == n_, f"{rn} text fix {old_!r}: {d['text'].count(old_)} occurrences"
            for k_ in range(n_):
                d["text"] = d["text"].replace(old_, new_[k_] if isinstance(new_, list) else new_, 1)
        d["text"] = _own_labels(d["text"])
        texts.append(_fix_tails(d["text"], btails) if extra else d["text"])
    at, old, new, ovn, mips = CORE_TURRET_PERIOD
    w = struct.unpack("<I", overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn).read(mips, 4))[0]
    assert w >> 26 == 9 and w & 0xFFFF == new, f"Arrange turret period at {mips:#x}"
    assert p[at] == old, f"unexpected code at {at:#x}"
    ps.add(at, bytes([new]), f"Arrange core turret period {old} -> {new:#x}")
    site, end, head, call = CORE_TURRET_VOLLEY
    assert _matches(p[site:site + m68k.sig_len(head)], head) and _matches(p[call:call + 6], CORE_TURRET_CALL), \
        f"core turret volley {site:#x}"
    site2, end2, head2, call2 = CORE_TURRET2_VOLLEY
    assert _matches(p[site2:site2 + m68k.sig_len(head2)], head2) and _matches(p[call2:call2 + 6], CORE_TURRET_CALL), \
        f"core turret 2 volley {site2:#x}"
    # each overlay's text is assembled on its own: the callees ported with its roots (the
    # bullet helpers, kakusei.py) may also be in another overlay's text
    total = n = 0
    chain = False
    for ((rn, an), roots), t in zip(ROOTS.items(), texts):
        code, _ = asm68k.assemble(t, 0x200000)
        addr = state.alloc_p(len(code), 2, f"Arrange restructured volleys ({rn})")
        code, syms = asm68k.assemble(t, addr)
        ps.add(addr, code, f"Arrange restructured volleys ({rn})")
        total += len(code)
        for fn, (site_, head_) in roots.items():
            assert _matches(p[site_:site_ + m68k.sig_len(head_)], head_), f"restructured routine {site_:#x}"
            ps.jmp(site_, syms[f"F_{fn:x}"], f"Arrange {fn:#x} replaces {site_:#x}")
            n += 1
        if (rn, an) == bpair:
            assert _matches(p[bsite:bsite + m68k.sig_len(bhead)], bhead), f"boss damage chain {bsite:#x}"
            ps.jmp(bsite, syms[f"F_{broot:x}"], f"Arrange stage 6 boss damage chain replaces {bsite:#x}")
            chain = True
    assert chain, "the stage 6 boss damage chain was not entered"
    t = CORE_TURRET_ASM.replace("{end:x}", f"{end:x}")
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 2, "Arrange core turret volley")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange core turret volley")
    total += len(code)
    ps.jmp(site, syms["core_turret_volley"], "Arrange core turret volley")
    t = CORE_TURRET2_ASM.replace("{end:x}", f"{end2:x}")
    code, _ = asm68k.assemble(t, 0x200000)
    addr = state.alloc_p(len(code), 2, "Arrange core turret 2 volley")
    code, syms = asm68k.assemble(t, addr)
    ps.add(addr, code, "Arrange core turret 2 volley")
    total += len(code)
    ps.jmp(site2, syms["core_turret2_volley"], "Arrange core turret 2 volley")
    fsite, fend, fhead = STAGE2_SMALL_FIRE
    assert _matches(p[fsite:fend], fhead), f"stage 2 small enemy fire {fsite:#x}"
    code, _ = asm68k.assemble(STAGE2_SMALL_FIRE_ASM, fsite)
    assert len(code) == fend - fsite, f"stage 2 small enemy fire: {len(code)} bytes"
    ps.add(fsite, code, "Arrange stage 2 small enemy fire (no angle/x limits, offset start)")
    wsite, wend, whead = STAGE1_RING_WINDOW
    assert _matches(p[wsite:wend], whead), f"stage 1 ring window {wsite:#x}"
    ps.add(wsite, bytes.fromhex("4e71") * ((wend - wsite) // 2), "Arrange stage 1 type 0x66 rings (no angle window)")
    for wsite, wend, whead in STAGE4_RING_WINDOWS:
        assert _matches(p[wsite:wend], whead), f"stage 4 ring window {wsite:#x}"
        ps.add(wsite, bytes.fromhex("4e71") * ((wend - wsite) // 2), "Arrange stage 4 boss part rings (no angle window)")
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
    at_, n_, ovn_, scr_ = CLEAR_FADE
    arc_ = bytes(p[at_:at_ + 2 + 8 * n_])
    an_ = struct.unpack(">H", overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn_).read(scr_, 2))[0]
    arr_ = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / ovn_).read(scr_, 2 + 8 * an_)
    ents_ = [arc_[2 + 8 * k:10 + 8 * k] for k in range(n_)]
    assert struct.unpack(">H", arc_[:2])[0] == n_ and an_ < n_, "stage clear fade script counts"
    assert [e for e in ents_ if e[2:4] != b"\x00\x80"] == [arr_[2 + 8 * k:10 + 8 * k] for k in range(an_)], \
        "Arrange stage clear fade script: the arcade's without palette 0x0080"
    ps.add(at_, arr_, f"Arrange stage clear fade script ({ovn_} {scr_:#x}: {an_} palettes)")
    at_, old_ = CLEAR_ILLUSTRATION
    assert _matches(p[at_:at_ + 4], old_), "stage clear illustration call"
    ps.add(at_, bytes.fromhex("4e714e71"), "Arrange stage clear: no story illustration (A0A9 0x876E60)")
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
        assert _matches(p[at_:at_ + m68k.sig_len(old_)], old_), f"type 0x7F handler at {at_:#x}"
        new_code, _ = asm68k.assemble("\n".join("\t" + x.strip() for x in new_.split(";")), at_)
        assert len(new_code) == m68k.sig_len(old_), f"type 0x7F handler at {at_:#x}: {new_}"
        ps.add(at_, new_code, f"Arrange type 0x7F spawn layout at {at_:#x}")
    return (f"Arrange restructured volleys: {n} routines, the core turret and the stage 6 boss damage "
            f"chain ({total} bytes)")
