"""Arrange characters: Black's normal shot (spec/arrange-characters.md).

Black takes the arcade character slot 2 (Tateha's). His normal shot (Arrange 0x824F50 with the
spawners 0x835A40 / 0x836F10, which are the same code; stubs 0x8265B0 / 0x8265E0 for +0x66 =
0 / 2, dispatch slots 2/3) differs from Chihiro's: every shot of a volley owns a block of the
pool (5 records, 8 in the Kakusei form) and takes its first free record; the volley count is
the first volley array (+0x81268A, Black 5; 8 in the Kakusei form) capped by 0x127958, and the
slot list holds the record (7 longs) and the 0x28-byte entry (7 longs after them) of each shot.
Patterns 0xBFB350 / 0xBFBE40 (types 0x8010 / 0x8018: Tateha's handler slots 4-7); their
loader-filled lists follow the rule of chars.pattern_set with the x offset word +2 as the key.

Handlers 4/5 (0x986310 / 0x987570) and 6/7 (0x988580 / 0x989650) are ported at build time.
Their hit path uses the Arrange-only ELF routine 0x205830 (hit record: +6, +0xE, +0x22, +0x24,
then +0x1E = one of three overlapping 4-sprite explosion lists, table 0xBFBE10, picked by the
ELF random 0x1BB5E0: table 0x2D5100 of 0..2 indexed by the arcade random counter 0x801176 &
0x7F); both are new 68k code here (black_hit_list, black_rand) with the tables copied.
"""
from __future__ import annotations

import re
import struct

from . import chars
from .chars import LIST, _fix, _ptr_table, _sprites
from .pgm import asm68k, m68k
from .ps2 import overlay

PATTERN_SETS = (0xBFB350, 0xBFBE40)          # +0x66 = 0 / 2
ENTRY = 0x28
KEY_FIELDS = ((2, 2),)
DISPATCH = (2, 3)
HANDLER_ROOTS = (0x986310, 0x987570, 0x988580, 0x989650)
HANDLER_SLOTS = (4, 5, 6, 7)
# record tables per pattern set: flight (10 bytes, 0x12471E) / hit (10 bytes, ELF 0x205830),
# from the `lea` before those calls in 0x986700 / 0x987630 and 0x988970 / 0x989710; the text
# has guessed addresses for them
RECORDS = ((0xBFB7D0, 0xBFB9F0), (0xBFC1D0, 0xBFC360))
GUESSED = {0x1268F0: (0, 0), 0x126B10: (0, 1), 0x1272F0: (1, 0), 0x127480: (1, 1)}
HIT_WINDOWS = 0xBFBE10                     # 3 pointers into one 8-sprite run
RAND_TABLE = 0x2D5100                      # ELF data, 128 bytes of 0..2
EXPECTED_PROBLEMS = {0x1BC200, 0x986700, 0x989710, 0x205830, 0x987630, 0x97E830, 0x1BB5E0, 0x987560,
                     0x988970}
# 0x1BC200 / 0x1BB5E0: ELF randoms (0x1BC200 is the arcade 0x11ABE0, signed word in d1, as in
# Ageha's handler 3); 0x987560 (`j 0x97E830`) and 0x97E830: plain draw 0x116226; the zoomed
# draws (ELF 0x1B5AC0, helper named 0x1162A2) use the full zoom helper 0x11626A; the hit
# sound 0x798D00 (native arguments lost) is the arcade Tateha-shot hit sound 0x17D490.

SHOT_ASM = """
; Black's normal shot (Arrange 0x824F50 + spawner 0x835A40), dispatch slots 2/3
black_shot_a:
        lea     {table_a}.l,a1
        bra     black_shot
black_shot_b:
        lea     {table_b}.l,a1
black_shot:
        lea     {pool1}.l,a0
        movea.l $81268a.l,a2
        tst.b   7(a5)
        beq     bs_l1
        lea     {pool2}.l,a0
        movea.l $812692.l,a2
bs_l1:      move.w  $20(a6),d0
        add.w   d0,d0
        movea.l (a1,d0.w),a1
        move.w  (a2),d1
        tst.w   $66(a6)
        beq     bs_l2
        moveq   #8,d1
bs_l2:      move.w  (a1)+,d7
        subq.w  #1,d7
        tst.w   $813998.l
        bne     bs_l3
        lea     $127958.l,a2
        move.w  $4a(a6),d5
        move.w  (a2,d5.w),d5
        cmp.w   d5,d1
        bls     bs_l3
        move.w  d5,d1
bs_l3:      subq.w  #1,d1
        lea     {list_end}.l,a2
        moveq   #0,d0
        moveq   #13,d2
bs_clr:     move.l  d0,-(a2)
        dbra    d2,bs_clr
bs_l4:      move.w  d1,d0
        movea.l a0,a3
bs_l5:      tst.w   (a3)
        bne     bs_l6
        move.l  a1,28(a2)
        move.l  a3,(a2)+
        bra     bs_l7
bs_l6:      lea     $34(a3),a3
        dbra    d0,bs_l5
bs_l7:      lea     40(a1),a1
        tst.w   $66(a6)
        bne     bs_l8
        lea     260(a0),a0
        bra     bs_l9
bs_l8:      lea     416(a0),a0
bs_l9:      dbra    d7,bs_l4
        tst.l   {list}.l
        bne     bs_spawn
        jmp     $1233ac.l               ; no free record: the common player tail
bs_spawn:
        lea     {list}.l,a3
bs_s1:      movea.l (a3),a0
        movea.l 28(a3),a1
        move.w  (a1)+,d0
        or.b    $49(a6),d0
        move.w  d0,(a0)+
        move.w  (a1)+,d0
        add.w   2(a6),d0
        move.w  d0,(a0)+
        move.w  (a1)+,d0
        add.w   4(a6),d0
        move.w  d0,(a0)+
        move.l  (a1)+,(a0)+
        movea.l (a1)+,a2
        adda.w  $3c(a6),a2
        move.l  (a2),(a0)+
        move.l  (a1)+,(a0)+
        move.l  (a1)+,(a0)+
        move.l  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.b  $48(a6),-1(a0)
        move.l  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.w  $3e(a6),(a0)+
        move.w  (a1)+,(a0)+
        move.w  $20(a6),(a0)+
        move.w  (a1)+,(a0)+
        addq.l  #4,a3
        cmpa.l  #{list_mid},a3
        beq     bs_s2
        tst.l   (a3)
        bne     bs_s1
bs_s2:      subq.w  #4,$3c(a6)
        bcc     bs_s3
        move.w  #8,$3c(a6)
bs_s3:      subq.w  #4,$3e(a6)
        bcc     bs_s4
        move.w  #8,$3e(a6)
bs_s4:      jmp     $123372.l               ; shot sound, then the common player tail

; ELF 0x1BB5E0: random 0..2 (table) by the arcade random counter
black_rand:
        addq.b  #1,$801177.l
        moveq   #$7f,d0
        and.w   $801176.l,d0
        move.l  a0,-(a7)
        lea     {rand}.l,a0
        move.b  (a0,d0.w),d0
        movea.l (a7)+,a0
        rts
"""


def port_jobs(work) -> list[dict]:
    """The porter.port_roots calls of this module (keyword arguments; stages/code.py
    prefetches them in parallel)."""
    return [dict(roots=list(HANDLER_ROOTS), cache=work / "ps2" / "detrans" / "black_handlers.json")]


def black_shots(state, ps: m68k.PatchSet, p: bytes, slots: list) -> str:
    """Install Black's normal shot: patterns, routine, dispatch slots 2/3, handlers 4-7."""
    from .detrans import porter
    from .ps2.elf import Elf
    from .stages import layout
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    tables = [chars._emit_set(state, ov, ps, t, slots, ENTRY, KEY_FIELDS) for t in PATTERN_SETS]
    elf = Elf(state.work / "ps2" / "disc" / "SLPS_253.52")
    rand = elf.read(RAND_TABLE, 0x80)
    assert set(rand) <= {0, 1, 2}, "ELF random table"
    # explosion windows: 3 pointers into one run of sprite numbers
    win = _ptr_table(ov, HIT_WINDOWS)
    assert len(win) == 3 and win == [win[0], win[0] + 8, win[0] + 16], "hit list windows"
    run = _sprites(ov, ps, state, win[0], 8, slots, "Black hit list run")
    data = state.alloc_p(0x80 + 12, 4, "Black random table, hit list windows")
    ps.add(data, rand + b"".join(struct.pack(">I", run + w - win[0]) for w in win), "Black random table, hit list windows")
    # flight / hit record tables (10-byte records, no pointers)
    rec = {}
    for k, (ft, ht) in enumerate(RECORDS):
        # one record per entry index (the Kakusei tables have no terminator)
        idx = sorted({chars._w(ov.read(e + 0x24, 2)) for e in chars.pattern_set(ov, PATTERN_SETS[k], ENTRY, KEY_FIELDS)["kind"]})
        assert idx == list(range(0, 4 * len(idx), 4)), f"entry indexes of {PATTERN_SETS[k]:#x}"
        for j, t in enumerate((ft, ht)):
            ptrs = _ptr_table(ov, t)[:len(idx)]
            assert len(ptrs) == len(idx) and all(0xB00000 < a < 0xD00000 for a in ptrs), f"record table {t:#x}"
            base = state.alloc_p(4 * len(ptrs) + 16 * len(ptrs), 4, "Black shot records")
            blob = b"".join(struct.pack(">I", base + 4 * len(ptrs) + 16 * i) for i in range(len(ptrs)))
            blob += b"".join(ov.read(a, 10) + bytes(6) for a in ptrs)
            ps.add(base, blob, f"Black shot records {t:#x}")
            rec[(k, j)] = base
    pool1 = layout.SHOT_POOL
    pool2 = layout.SHOT_POOL + layout.ARRANGE_SHOTS * layout.SHOT_RECORD
    lst = layout.SHOT_LIST
    text = SHOT_ASM.format(table_a=f"${tables[0]:x}", table_b=f"${tables[1]:x}", pool1=f"${pool1:x}",
                           pool2=f"${pool2:x}", list=f"${lst:x}", list_mid=f"${lst + 28:x}",
                           list_end=f"${lst + 56:x}", rand=f"${data:x}")
    d = porter.port_roots(**port_jobs(state.work)[0])
    probs = {int(k, 16) for k in d["problems"]}
    assert probs <= EXPECTED_PROBLEMS, f"new problems in Black's ported handlers: {d['problems']}"
    h = d["text"]
    h = _fix(h, r"^\tjsr\tF_9(?:7e830|87560)\b.*$", "\tjsr\t$116226.l", None)
    h = _fix(h, r"^\tjsr\t\$1162a2\.l$", "\tjsr\t$11626a.l", 2)
    h = _fix(h, r"^\tjsr\tF_1bc200$", "\tjsr\t$11abe0.l", 2)
    h = _fix(h, r"^\tjsr\tF_1bb5e0$", "\tjsr\tblack_rand")
    h = _fix(h, r"^\tlea\.l\t\$[0-9a-f]+\.l,a0\n(\tmove\.l\t\(a0,d0\.w\),30\(a6\))", f"\tlea.l\t${data + 0x80:x}.l,a0\n\\1")
    h = _fix(h, r"^\tjsr\tF_798d00$", "\tjsr\t$17d490.l", 2)
    h = _fix(h, r"^(F_814080:\n\tlea\.l\t)\$80ef36\.l", "\\1$81b596.l")
    for guess, (k, j) in GUESSED.items():
        h = _fix(h, rf"^\tlea\.l\t\${guess:x}\.l,a0", f"\tlea.l\t${rec[(k, j)]:x}.l,a0")
    text += h
    code, _ = asm68k.assemble(text, 0x200000)
    addr = state.alloc_p(len(code), 4, "Black normal shot")
    code, syms = asm68k.assemble(text, addr)
    ps.add(addr, code, "Black normal shot routine and handlers (ported from A0A9)")
    for slot, entry in zip(DISPATCH, ("black_shot_a", "black_shot_b")):
        ps.add(chars.SHOT_DISPATCH + 4 * slot, struct.pack(">I", syms[entry]), f"shot dispatch slot {slot} -> {entry}")
    for slot, fn in zip(HANDLER_SLOTS, HANDLER_ROOTS):
        ps.add(chars.HANDLER_TABLE + 4 * slot, struct.pack(">I", syms[f"F_{fn:x}"]), f"shot handler {slot} -> Arrange {fn:#x}")
    return f"Black normal shot at {addr:#x}"
