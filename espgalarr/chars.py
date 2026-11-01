"""Arrange characters: Chihiro's shots (spec/arrange-characters.md).

Chihiro takes the arcade character slot 0 (Ageha's). Her normal shot (Arrange 0x823BF0 with
the spawner 0x834450, entered through the stubs 0x824EF0 / 0x824F20 for +0x66 = 0 / 2) has
the arcade routine's structure (0x122D10): find as many free records in the player-shot
pool as the volley needs, then fill them from a pattern. The pattern format is Arrange's:
per level a shot count and 0x2C-byte entries holding the object fields inline (the arcade
goes through direction records). The routine is new 68k code; the patterns are copied from
the Arrange overlay A0A9.

Pattern pointers: in the overlay file the two pointer fields of an entry (+0x0A animation
list, +0x1E auxiliary list; 0xFFFFFFFF on disc) are filled by the loader, which hands out the
16-byte sprite lists that follow the patterns in order: entries sorted by their index word
(+0x24) take a new list whenever their shape (+0x26, +0x28) changes; mirrored entries (+0x2A
negated) share one. The K animation lists are followed by the K auxiliary lists. Checked
against PCSX2 RAM; the build re-checks the layout (stop on mismatch).

Sprite lists hold PS2 sprite numbers (bank A020); the graphics stage replaces them with PGM
sprite codes (state.sprite_number_slots).
"""
from __future__ import annotations

import struct

from .pgm import asm68k, m68k
from .ps2 import overlay

PATTERN_SETS = (0xBF7040, 0xBF78E0)        # +0x66 = 0 (stub argument 1) / 2 (argument 2)
ENTRY = 0x2C
LIST = 16
SHOT_DISPATCH = 0x122CF0                   # arcade table: +0x66*2 + +0x4A*4 + +0x40*4
VOLLEY_TABLES = (0x1278AA, 0xBFC810)        # arcade / Arrange per-character volley counts


def _w(b: bytes, a: int = 0) -> int:
    return struct.unpack_from(">H", b, a)[0]


def _is_list(ov, a: int) -> bool:
    v = struct.unpack(">4I", ov.read(a, LIST))
    return v[3] == 0 and all(0 < x < 0x8000 for x in v[:3])


def pattern_set(ov, table: int, entry: int = ENTRY, key_fields=((0x26, 2), (0x28, 2))) -> dict:
    """Levels (entry addresses per level) and the list each entry's two pointers get.
    key_fields: (offset, size) of the entry fields whose change starts a new list (Chihiro
    +0x26/+0x28 in 0x2C-byte entries, Black the x offset +2 in 0x28-byte entries)."""
    levels = []
    k = 0
    while True:
        p = struct.unpack("<I", ov.read(table + 4 * k, 4))[0]
        if not p:
            break
        n = _w(ov.read(p, 2))
        levels.append((p, [p + 2 + entry * i for i in range(n)]))
        k += 1
    entries = sorted((e for _, es in levels for e in es), key=lambda e: _w(ov.read(e + 0x24, 2)))
    kind, last, n_kinds = {}, None, 0
    for e in entries:
        b = ov.read(e, entry)
        assert b[0x0A:0x0E] == b"\xff" * 4 and b[0x1E:0x22] == b"\xff" * 4, f"entry {e:#x} pointers"
        key = tuple(b[o:o + n] for o, n in key_fields)
        if key != last:
            n_kinds += 1
            last = key
        kind[e] = n_kinds - 1
    end = max(e + entry for e in entries)
    base = (end + LIST - 1) & ~(LIST - 1)
    while not all(_is_list(ov, base + LIST * i) for i in range(2 * n_kinds)):
        base += LIST
        assert base < end + 0x1000, f"sprite lists after the patterns of {table:#x}"
    assert not _is_list(ov, base + LIST * 2 * n_kinds), f"more sprite lists than kinds at {table:#x}"
    return {"levels": levels, "kind": kind, "kinds": n_kinds, "lists": base}


def _emit_set(state, ov, ps: m68k.PatchSet, table: int, slots: list, entry: int = ENTRY,
              key_fields=((0x26, 2), (0x28, 2))) -> int:
    s = pattern_set(ov, table, entry, key_fields)
    k = s["kinds"]
    lists = state.alloc_p(2 * k * LIST, 4, "Chihiro sprite lists")
    blob = bytearray()
    for i in range(2 * k):
        src = s["lists"] + LIST * i
        blob += ov.read(src, LIST)
        for j in range(3):
            slots.append((lists + LIST * i + 4 * j, struct.unpack(">I", ov.read(src + 4 * j, 4))[0]))
    ps.add(lists, bytes(blob), f"Chihiro shot sprite lists of {table:#x} (sprite numbers, gfx stage)")
    size = 4 * (len(s["levels"]) + 1) + sum(2 + entry * len(es) for _, es in s["levels"])
    out = state.alloc_p(size, 4, "Chihiro shot patterns")
    blob = bytearray(4 * (len(s["levels"]) + 1))
    for n, (p, es) in enumerate(s["levels"]):
        struct.pack_into(">I", blob, 4 * n, out + len(blob))
        blob += ov.read(p, 2)
        for e in es:
            b = bytearray(ov.read(e, entry))
            struct.pack_into(">I", b, 0x0A, lists + LIST * s["kind"][e])
            struct.pack_into(">I", b, 0x1E, lists + LIST * (k + s["kind"][e]))
            blob += b
    ps.add(out, bytes(blob), f"Chihiro shot patterns of {table:#x}")
    return out


SHOT_ASM = """
; Chihiro's normal shot (Arrange 0x823BF0 + spawner 0x834450), dispatch slots of character 0
chihiro_shot_a:
        lea     {table_a}.l,a1
        bra.s   chihiro_shot
chihiro_shot_b:
        lea     {table_b}.l,a1
chihiro_shot:
        lea     {pool1}.l,a0
        movea.l $81268a.l,a2
        tst.b   7(a5)
        beq.s   cs_l1
        lea     {pool2}.l,a0
        movea.l $812692.l,a2
cs_l1:      move.w  $20(a6),d0
        add.w   d0,d0
        movea.l (a1,d0.w),a1
        move.w  (a2),d0
        tst.w   $66(a6)
        beq.s   cs_l2
        addq.w  #4,d0
cs_l2:      subq.w  #1,d0
        move.w  (a1)+,d6
        moveq   #0,d7
        tst.w   $813998.l
        bne.s   cs_l3
        lea     $127958.l,a2
        adda.w  $4a(a6),a2
        cmp.w   (a2),d0
        bls.s   cs_l3
        move.w  (a2),d0
cs_l3:      add.w   d6,d7
        dbra    d0,cs_l3
        subq.w  #1,d7
        lea     {list_end}.l,a2
        moveq   #0,d0
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
cs_l4:      tst.w   (a0)
        bmi.s   cs_l5
        move.l  a0,(a2)+
        subq.w  #1,d6
        beq.s   spawn
cs_l5:      lea     $34(a0),a0
        dbra    d7,cs_l4
        jmp     $1233ac.l               ; not enough free records: the common player tail
spawn:
        lea     {list}.l,a3
cs_l6:      movea.l (a3),a0
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
        move.w  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        addq.l  #4,a3
        cmpa.l  #{list_end},a3
        beq.s   cs_l7
        tst.l   (a3)
        bne.s   cs_l6
cs_l7:      subq.w  #4,$3c(a6)
        bcc.s   cs_l8
        move.w  #8,$3c(a6)
cs_l8:      subq.w  #4,$3e(a6)
        bcc.s   cs_l9
        move.w  #8,$3e(a6)
cs_l9:      jmp     $123372.l               ; shot sound, then the common player tail
"""


def chihiro_shots(state, ps: m68k.PatchSet, p: bytes) -> str:
    """Install Chihiro's normal shot: patterns, routine, dispatch slots 0/1, volley counts."""
    from .stages import layout
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    slots: list = []
    tables = [_emit_set(state, ov, ps, t, slots) for t in PATTERN_SETS]
    pool1 = layout.SHOT_POOL
    pool2 = layout.SHOT_POOL + layout.ARRANGE_SHOTS * layout.SHOT_RECORD
    lst = layout.SHOT_LIST
    text = SHOT_ASM.format(table_a=f"${tables[0]:x}", table_b=f"${tables[1]:x}", pool1=f"${pool1:x}",
                           pool2=f"${pool2:x}", list=f"${lst:x}", list_end=f"${lst + 7 * 4:x}")
    code, _ = asm68k.assemble(text, 0x200000)
    addr = state.alloc_p(len(code), 4, "Chihiro shot routine")
    code, syms = asm68k.assemble(text, addr)
    ps.add(addr, code, "Chihiro normal shot routine")
    for slot, entry in ((0, "chihiro_shot_a"), (1, "chihiro_shot_b")):
        ps.add(SHOT_DISPATCH + 4 * slot, struct.pack(">I", syms[entry]), f"shot dispatch slot {slot} -> {entry}")
    # volley counts (per character two word arrays, normal shot / Power Shot, indexed by
    # +0x4A * 4: arrays 0/1 Chihiro, 2/3 Black); the last array is as long as the levels
    arc, arr = VOLLEY_TABLES
    for k in range(4):
        a = struct.unpack_from(">I", p, arc + 4 * k)[0]
        b = struct.unpack("<I", ov.read(arr + 4 * k, 4))[0]
        n = (struct.unpack_from(">I", p, arc + 4 * (k + 1))[0] - a) // 2 if k < 3 else 5
        assert 0 < n <= 8, f"volley array {a:#x}"
        ps.add(a, ov.read(b, 2 * n), f"volley counts {k} ({n} levels)")
    hnote = chihiro_handlers(state, ps, p, slots)
    pnote = chihiro_power(state, ps, p, slots)
    from . import black
    bnote = black.black_shots(state, ps, p, slots)
    state.sprite_number_slots = getattr(state, "sprite_number_slots", []) + slots
    return (f"Chihiro normal shot at {addr:#x}, {hnote}, {pnote}, {bnote} ({len(slots)} sprite slots, "
            f"patterns {tables[0]:#x}/{tables[1]:#x})")


# Shot handlers. The per-frame walker 0x126980 calls a handler by the shot's type (type &
# 0x3C, table 0x126A22). Arrange rewrote all of them; Chihiro's normal shot spawns with type
# 0x40 (handler 0: muzzle flash from the +0x0A list, then flight setup) and continues as 0x44
# (handler 1: flight from the +0x1E list, speed from the angle bytes once the +0x2A timer
# runs out, hit: score, hit record, hit animation). Arcade types of character 0 use the same
# handlers (Ageha 0x8000/0x8008); Tateha's (0x8010/0x8018) do not. The handlers are ported
# from the Arrange code at build time (porter.port_roots) with these fixes:
HANDLER_ROOTS = (0x981C40, 0x9832D0)
HANDLER_TABLE = 0x126A22
FLIGHT_TABLE, HIT_TABLE, ZOOM_TABLE = 0xBF73D0, 0xBF7550, 0xC07940   # A0A9 data
EXPECTED_PROBLEMS = {0x9832C0, 0x97E830, 0x9825E0}
# 0x9832C0 (`j 0x97EB50`): zoomed draw, d6 = ZOOM_TABLE[+0x28 power level] -> 0x11626A;
# 0x97E830: plain draw (ELF 0x1B5A80) -> 0x116226; 0x9825E0: an unrecognised flag note only
# (the off-screen test `addi.w #$400 ; addi.w #$C000 ; bcc` is complete in the text).
# The flight / hit record tables are guessed addresses in the text: the `lea` before the
# arcade record routines 0x12471E (flight) / 0x12473C (hit) loads the copied tables; P2's
# score record is 0x81B596 (the map gives the P2 player record 0x80EF36).
HANDLER_DRAW = """
chihiro_draw:
        move.l  d6,-(a7)
        move.w  $28(a6),d6
        add.w   d6,d6
        lea     {zoom}.l,a0
        move.l  (a0,d6.w),d6
        jsr     $11626a.l
        move.l  (a7)+,d6
        rts
"""


def _ptr_table(ov, a: int) -> list[int]:
    out = []
    while True:
        v = struct.unpack("<I", ov.read(a + 4 * len(out), 4))[0]
        if not v:
            return out
        out.append(v)


def _fix(text: str, pattern: str, repl: str, count: int | None = 1) -> str:
    import re
    new, n = re.subn(pattern, repl, text, flags=re.M)
    assert n >= 1 if count is None else n == count, f"handler fix {pattern!r}: {n} matches"
    return new


def _shot_records(state, ov, ps: m68k.PatchSet, table: int, flight_t: int, hit_t: int, zoom_t: int,
                  slots: list) -> tuple[int, int, int]:
    """Flight records (16 bytes, no pointers), hit records (the list pointer at +6 goes to
    +0x1E: the 4-sprite hit list of the entry's kind; the lists follow the 2K lists of the
    pattern set) and the zoom table of one normal-shot pattern set. Returns the flight and
    hit pointer tables and the zoom table."""
    s = pattern_set(ov, table)
    flight = _ptr_table(ov, flight_t)
    hit = _ptr_table(ov, hit_t)
    assert len(flight) == len(hit), f"flight / hit tables of {table:#x}"
    four = s["lists"] + 2 * LIST * s["kinds"]
    kinds_by_idx = {_w(ov.read(e + 0x24, 2)) // 4: k for e, k in s["kind"].items()}
    tab_size = 4 * (len(flight) + 1)
    base = state.alloc_p(2 * tab_size + 2 * 16 * len(flight) + LIST * s["kinds"] + 32, 4, "Chihiro shot records")
    blob = bytearray(2 * tab_size)
    f_recs = base + 2 * tab_size
    h_recs = f_recs + 16 * len(flight)
    lists4 = h_recs + 16 * len(hit)
    zoom = lists4 + LIST * s["kinds"]
    for i, a in enumerate(flight):
        struct.pack_into(">I", blob, 4 * i, f_recs + 16 * i)
    for i, a in enumerate(hit):
        struct.pack_into(">I", blob, tab_size + 4 * i, h_recs + 16 * i)
    for a in flight:
        blob += ov.read(a, 16)
    for i, a in enumerate(hit):
        r = bytearray(ov.read(a, 16))
        assert r[6:10] == b"\xff" * 4, f"hit record {a:#x}"   # (long, word, list, word, word)
        struct.pack_into(">I", r, 6, lists4 + LIST * kinds_by_idx.get(i, 0))
        blob += r
    for k in range(s["kinds"]):
        src = four + LIST * k
        assert _sprite_list(ov, src), f"hit list at {src:#x}"
        blob += ov.read(src, LIST)
        for j in range(4):
            slots.append((lists4 + LIST * k + 4 * j, struct.unpack(">I", ov.read(src + 4 * j, 4))[0]))
    blob += ov.read(zoom_t, 32)
    ps.add(base, bytes(blob), f"Chihiro shot flight/hit records, hit lists, zoom table of {table:#x}")
    return base, base + tab_size, zoom


def _record_fixes(text: str, flight: int, hit: int) -> str:
    """The `lea` before the arcade record routines 0x12471E (flight) / 0x12473C (hit) loads the
    copied tables (the text has guessed addresses)."""
    text = _fix(text, r"^\tlea(?:\.l)?\t\$[0-9a-f]+(?:\.[wl])?,a0\s*(?:;.*)?\n(\tjsr\t\$12471e\.l)",
                f"\tlea.l\t${flight:x}.l,a0\n\\1")
    return _fix(text, r"^\tlea(?:\.l)?\t\$[0-9a-f]+(?:\.[wl])?,a0\s*(?:;.*)?\n(\tjsr\t\$12473c\.l)",
                f"\tlea.l\t${hit:x}.l,a0\n\\1")


def chihiro_handlers(state, ps: m68k.PatchSet, p: bytes, slots: list) -> str:
    from .detrans import porter
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    d = porter.port_roots(list(HANDLER_ROOTS), state.work / "ps2" / "detrans" / "chihiro_handlers.json")
    probs = {int(k, 16) for k in d["problems"]}
    assert probs <= EXPECTED_PROBLEMS, f"new problems in the ported handlers: {d['problems']}"
    flight, hit, zoom = _shot_records(state, ov, ps, PATTERN_SETS[0], FLIGHT_TABLE, HIT_TABLE, ZOOM_TABLE, slots)
    text = d["text"]
    text = _fix(text, r"^\tjsr\tF_9832c0\b.*$", "\tjsr\tchihiro_draw", None)
    text = _fix(text, r"^\tjsr\tF_97e830\b.*$", "\tjsr\t$116226.l", None)
    text = _record_fixes(text, flight, hit)
    text = _fix(text, r"^(F_814080:\n\tlea\.l\t)\$80ef36\.l", "\\1$81b596.l")
    text += HANDLER_DRAW.format(zoom=f"${zoom:x}")
    code, _ = asm68k.assemble(text, 0x200000)
    addr = state.alloc_p(len(code), 4, "Chihiro shot handlers")
    code, syms = asm68k.assemble(text, addr)
    ps.add(addr, code, "Chihiro shot handlers (ported from A0A9)")
    for k, fn in enumerate(HANDLER_ROOTS):
        ps.add(HANDLER_TABLE + 4 * k, struct.pack(">I", syms[f"F_{fn:x}"]), f"shot handler {k} -> Arrange {fn:#x}")
    return f"handlers at {addr:#x}, " + kakusei_handlers(state, ps, ov, slots)


# Kakusei-form normal shot (patterns 0xBF78E0, type 0x8008: Ageha's Kakusei shot slots).
# Handler 2 (0x983E70): muzzle (plain draw), then flight setup from the record table 0xBF7BD0
# and, while the +0x2A timer runs, straight flight before the angle speed; handler 3
# (0x985770): flight drawn zoomed by power level (0xC07970), hit: hit record 0xBF7D00 and the
# 4-sprite hit animation drawn at a fixed zoom (0x97EA40). Fixes: the zoomed draws (ELF
# 0x1B5AC0, the porter names helper 0x1162A2) go through the full zoom helper 0x11626A as in
# handler 1; the hit sound 0x7980F0 (native arguments lost) is the arcade's Kakusei-shot hit
# sound 0x17D3DC until Arrange's sounds are ported.
KAKUSEI_ROOTS = (0x983E70, 0x985770)
KAKUSEI_TABLES = (0xBF7BD0, 0xBF7D00, 0xC07970)
KAKUSEI_EXPECTED = {0x97EE80, 0x984260, 0x985830, 0x984810, 0x97E830}
# 0x984810: an unrecognised flag note only (`add.w d1,4(a6)` before `move.w 4(a6),d0`)


def kakusei_handlers(state, ps: m68k.PatchSet, ov, slots: list) -> str:
    from .detrans import porter
    d = porter.port_roots(list(KAKUSEI_ROOTS), state.work / "ps2" / "detrans" / "chihiro_kakusei.json")
    probs = {int(k, 16) for k in d["problems"]}
    assert probs <= KAKUSEI_EXPECTED, f"new problems in the ported Kakusei handlers: {d['problems']}"
    flight, hit, zoom = _shot_records(state, ov, ps, PATTERN_SETS[1], *KAKUSEI_TABLES, slots)
    text = d["text"]
    text = _fix(text, r"^(F_97ee80:\n(?:\t.*\n)*?)\tlea\.l\t\$[0-9a-f]+\.l,a0\n", f"\\1\tlea.l\t${zoom:x}.l,a0\n")
    text = _fix(text, r"^\tjsr\t\$1162a2\.l$", "\tjsr\t$11626a.l", 2)
    text = _fix(text, r"^\tjsr\tF_97e830\b.*$", "\tjsr\t$116226.l", None)
    text = _fix(text, r"^\tjsr\tF_7980f0$", "\tjsr\t$17d3dc.l")
    text = _record_fixes(text, flight, hit)
    code, _ = asm68k.assemble(text, 0x200000)
    addr = state.alloc_p(len(code), 4, "Chihiro Kakusei shot handlers")
    code, syms = asm68k.assemble(text, addr)
    ps.add(addr, code, "Chihiro Kakusei-form shot handlers (ported from A0A9)")
    for k, fn in enumerate(KAKUSEI_ROOTS):
        ps.add(HANDLER_TABLE + 4 * (2 + k), struct.pack(">I", syms[f"F_{fn:x}"]), f"shot handler {2 + k} -> Arrange {fn:#x}")
    return f"Kakusei handlers at {addr:#x}"


# Power Shot (Arrange 0x826610 + spawner 0x8383E0, stubs 0x827CC0 / 0x827CF0 for +0x66 = 0 / 2;
# dispatch slots 4/5, the arcade laser's: index +0x40 = 4). It is Ageha's laser routine
# (0x1230CA + 0x12376C) reworked: the records come from the pool from record 35 on (the normal
# shot keeps 0..34), as many as the volley count (+0x81268E, Chihiro 0x10) times the shots per
# volley, with the laser's volley cap (0x12795C, the same values as Arrange 0xBFC838). While
# scanning, every `shots per volley` records seen move the spread index on (d4 += 4, d5 += 8):
# d5 is added to the first angle byte, d4 is stored at +0x2A and picks the offsets added to
# record +0x10 / +0x14 (A0A9 0xBFCD90 / 0xBFCDD0) and, in the handlers, the zoom (0xC07880)
# and the trail. The second angle byte is the lever angle +0x57 (the arcade laser's), plus
# random/2 unless +1 bit 5 is set. Animation counters +0x62 (8..0) / +0x64 (0xC..0).
# Arrange defers the shot sound (+0x98 = 1, played by 0x831280); here it is the arcade's
# shot-sound step 0x123372 as for the normal shot.
POWER_SETS = (0xBF9E40, 0xC02520)            # +0x66 = 0 (1 shot per volley) / 2 (4)
POWER_ENTRY = 0x26                          # the laser's entry size (0x12B05A patterns)
POWER_SPREAD = (0xBFCD90, 0xBFCDD0)          # 16 longs each, by d4
POWER_DISPATCH = (4, 5)
LASER_CAP = 0x12795C
# Record tables of the handlers (index: record +0x26 = entry +0x24). Flight records (10 bytes,
# arcade 0x12471E) and hit records (24 bytes with a loader-filled list pointer at +0x10,
# arcade 0x12475A); per pattern set, from the `lea` before those calls in 0x98AAD0 / 0x98BAD0
# (+0x66 = 0) and 0x98D3B0 / 0x98E3B0 (+0x66 = 2).
POWER_RECORDS = ((0xBF9F50, 0xBFA060), (0xC02860, 0xC029F0))
POWER_ZOOM = 0xC07880                       # 16 longs by +0x2A (0x97E860)
TRAIL_LISTS, TRAIL_N = 0xC07A50, 20          # 4-sprite lists of the trail draws 0x995EA0..0x9986C0


def _anim_list(ov, a: int) -> bool:
    v = struct.unpack(">4I", ov.read(a, LIST))
    return v[3] == 0 and all(0 < x < 0x8000 for x in v[:3])


def _sprite_list(ov, a: int, n: int = 4) -> bool:
    return all(0 < x < 0x8000 for x in struct.unpack(f">{n}I", ov.read(a, 4 * n)))


def power_set(ov, table: int, records: tuple[int, int], key_fields=((0x1A, 1),), extra: int = 0) -> dict:
    """Power Shot patterns of one set and the lists the loader hands out (checked against
    PCSX2 RAM): entries sorted by their index word +0x24 take a new animation (+0x0A, 3
    sprites and 0) and auxiliary list (+0x1E, 4 sprites) whenever the first angle byte +0x1A
    changes (mirrored entries differ in +0x1B only). The K animation lists follow the hit
    records and their pointer table, the K auxiliary lists start at the next 16-byte boundary,
    the hit lists (from the hit records' +0x16 index, one per distinct record) follow them.
    Black's sets: the key is the x offset word +2 in his Kakusei form; `extra` entries per
    level beyond the count word (his Kakusei form counts 3 and spawns 4)."""
    levels = []
    for p in _ptr_table(ov, table):
        n = _w(ov.read(p, 2)) + extra
        levels.append((p, [p + 2 + POWER_ENTRY * i for i in range(n)]))
    entries = sorted((e for _, es in levels for e in es), key=lambda e: _w(ov.read(e + 0x24, 2)))
    kind, last, k = {}, None, 0
    for e in entries:
        b = ov.read(e, POWER_ENTRY)
        assert b[0x0A:0x0E] == b"\xff" * 4 and b[0x1E:0x22] == b"\xff" * 4, f"entry {e:#x} pointers"
        kv = tuple(b[o:o + n] for o, n in key_fields)
        if kv != last:
            k, last = k + 1, kv
        kind[e] = k - 1
    idx = sorted({_w(ov.read(e + 0x24, 2)) for e in entries})
    assert idx == list(range(0, 4 * len(idx), 4)), f"entry indexes of {table:#x}"
    flight, hit = (_ptr_table(ov, t)[:len(idx)] for t in records)
    assert len(flight) == len(hit) == len(idx), f"record tables of {table:#x}"
    anim = max(hit) + 0x18                     # after the hit records (and their table, if it follows)
    while not all(_anim_list(ov, anim + LIST * i) for i in range(k)):
        anim += 8
        assert anim < max(hit) + 0x60, f"animation lists of {table:#x}"
    aux = (anim + LIST * k + 15) & ~15
    assert all(_sprite_list(ov, aux + LIST * i) for i in range(k)), f"auxiliary lists of {table:#x}"
    hit_lists, a, prev = [], aux + LIST * k, None
    for r in hit:
        b = ov.read(r, 0x18)
        assert b[0x10:0x14] == b"\xff" * 4, f"hit record {r:#x}"
        n = _w(b, 0x16) // 4 + 1
        if b != prev:
            assert _sprite_list(ov, a, n), f"hit list at {a:#x}"
            hit_lists.append((a, n))
            a += 4 * n
            prev = b
        else:
            hit_lists.append(hit_lists[-1])
    return {"levels": levels, "kind": kind, "kinds": k, "anim": anim, "aux": aux,
            "flight": flight, "hit": hit, "hit_lists": hit_lists}


def _sprites(ov, ps, state, src: int, n: int, slots: list, note: str) -> int:
    """Copy n sprite numbers (big-endian longs) to program ROM; their slots go to the gfx stage."""
    out = state.alloc_p(4 * n, 4, note)
    ps.add(out, ov.read(src, 4 * n), f"{note} (sprite numbers, gfx stage)")
    for j in range(n):
        slots.append((out + 4 * j, struct.unpack(">I", ov.read(src + 4 * j, 4))[0]))
    return out


def _emit_power_set(state, ov, ps: m68k.PatchSet, table: int, records, slots: list,
                    key_fields=((0x1A, 1),), extra: int = 0) -> dict:
    s = power_set(ov, table, records, key_fields, extra)
    k = s["kinds"]
    anim = _sprites(ov, ps, state, s["anim"], 4 * k, slots, "Power Shot animation lists")
    aux = _sprites(ov, ps, state, s["aux"], 4 * k, slots, "Power Shot auxiliary lists")
    size = 4 * (len(s["levels"]) + 1) + sum(2 + POWER_ENTRY * len(es) for _, es in s["levels"])
    out = state.alloc_p(size, 4, "Power Shot patterns")
    blob = bytearray(4 * (len(s["levels"]) + 1))
    for n, (p, es) in enumerate(s["levels"]):
        struct.pack_into(">I", blob, 4 * n, out + len(blob))
        blob += ov.read(p, 2)
        for e in es:
            b = bytearray(ov.read(e, POWER_ENTRY))
            struct.pack_into(">I", b, 0x0A, anim + LIST * s["kind"][e])
            struct.pack_into(">I", b, 0x1E, aux + LIST * s["kind"][e])
            blob += b
    ps.add(out, bytes(blob), f"Power Shot patterns of {table:#x}")
    # flight / hit record tables (pointer tables then the records)
    n = len(s["flight"])
    lists = {}
    for a, cnt in dict.fromkeys(s["hit_lists"]):
        lists[a] = _sprites(ov, ps, state, a, cnt, slots, "Power Shot hit list")
    base = state.alloc_p(8 * n + 16 * n + 0x18 * n, 4, "Power Shot records")
    blob = bytearray(8 * n)
    for i in range(n):
        struct.pack_into(">I", blob, 4 * i, base + 8 * n + 16 * i)
        struct.pack_into(">I", blob, 4 * (n + i), base + 24 * n + 0x18 * i)
    for a in s["flight"]:
        blob += ov.read(a, 10) + bytes(6)
    for a, (la, _) in zip(s["hit"], s["hit_lists"]):
        r = bytearray(ov.read(a, 0x18))
        struct.pack_into(">I", r, 0x10, lists[la])
        blob += r
    ps.add(base, bytes(blob), f"Power Shot flight/hit records of {table:#x}")
    return {"patterns": out, "flight": base, "hit": base + 4 * n}


POWER_ASM = """
; Chihiro's Power Shot (Arrange 0x826610 + spawner 0x8383E0), dispatch slots 4/5
chihiro_power_a:
        lea     {table_a}.l,a1
        bra   chihiro_power
chihiro_power_b:
        lea     {table_b}.l,a1
chihiro_power:
        lea     {pool1}.l,a0
        movea.l $81268e.l,a2
        tst.b   7(a5)
        beq   cp_l1
        lea     {pool2}.l,a0
        movea.l $812696.l,a2
cp_l1:      move.w  $20(a6),d0
        add.w   d0,d0
        movea.l (a1,d0.w),a1
        move.w  (a2),d0
        subq.w  #1,d0
        move.w  (a1)+,d6
        moveq   #0,d7
        tst.w   $813998.l
        bne   cp_l2
        lea     {cap}.l,a2
        adda.w  $4a(a6),a2
        cmp.w   (a2),d0
        bls   cp_l2
        move.w  (a2),d0
cp_l2:      add.w   d6,d7
        dbra    d0,cp_l2
        subq.w  #1,d7
        lea     {list_end}.l,a2
        moveq   #0,d0
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.l  d0,-(a2)
        move.w  d6,d2
        move.w  d6,d3
        moveq   #0,d4
        moveq   #0,d5
cp_l3:      tst.w   (a0)
        bmi   cp_l4
        move.l  a0,(a2)+
        subq.w  #1,d6
        beq   cp_spawn
cp_l4:      subq.w  #1,d3
        bne   cp_l5
        move.w  d2,d3
        addq.w  #4,d4
        addq.w  #8,d5
cp_l5:      lea     $34(a0),a0
        dbra    d7,cp_l3
        bra     power_c_tail            ; not enough free records: C, then the common player tail
cp_spawn:
        lea     {list}.l,a4
cp_s1:      movea.l (a4)+,a0
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
        adda.w  $62(a6),a2
        move.l  (a2),(a0)+
        move.l  (a1)+,(a0)+
        move.l  (a1)+,(a0)+
        move.l  (a1)+,(a0)+
        move.b  (a1)+,d0
        add.b   d5,d0
        move.b  d0,(a0)+
        addq.l  #1,a1                   ; the entry's second angle byte gives way to the lever
        move.b  $57(a6),d0
        btst    #5,1(a6)
        bne   cp_s2
        jsr     $11a0d8.l               ; random byte in d1
        asr.b   #1,d1
        add.b   d1,d0
cp_s2:      move.b  d0,(a0)+
        move.w  (a1)+,(a0)+
        move.b  $48(a6),-1(a0)
        move.l  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.w  $64(a6),(a0)+
        move.w  (a1)+,(a0)+
        move.w  $20(a6),(a0)+
        move.w  d4,(a0)+
        lea     {spread_a}.l,a2
        move.l  (a2,d4.w),d0
        add.l   d0,-$1c(a0)
        lea     {spread_b}.l,a2
        move.l  (a2,d4.w),d0
        add.l   d0,-$18(a0)
        cmpa.l  #{list_end},a4
        beq   cp_s3
        tst.l   (a4)
        bne   cp_s1
cp_s3:      subq.w  #4,$62(a6)
        bcc   cp_s4
        move.w  #8,$62(a6)
cp_s4:      subq.w  #4,$64(a6)
        bcc   cp_s5
        move.w  #$c,$64(a6)
cp_s5:      bra     power_c_sound           ; C, then the shot sound and the common player tail
"""

# The Power Shot handlers' hit-record load (ELF 0x205E70, the twin the porter maps to the arcade
# 0x12475A) also sets the spark timer (+0x30 = spread index +0x2A / 4, reload +0x31 = 7: the hit
# handlers spawn a spark 0x17AE52 when it runs out) and widens the hit box +0x10 / +0x14 by the
# spread offsets.
POWER_HIT_ASM = """
power_hit:
        jsr     $12475a.l
        move.w  $2a(a6),d0
        move.w  d0,d1
        lsr.w   #2,d0
        move.b  d0,$30(a6)
        move.b  #7,$31(a6)
        lea     {spread_a}.l,a0
        move.l  (a0,d1.w),d0
        add.l   d0,$10(a6)
        lea     {spread_b}.l,a0
        move.l  (a0,d1.w),d0
        add.l   d0,$14(a6)
        rts
"""

# Sprite submission to Arrange's new layers (layout.NEW_LAYER_SIZES). The PS2 wrappers 0x198430
# (zoomed, list "new2") and 0x1985C0 (plain, "new1") are the forms of the arcade helpers
# 0x11626A / 0x1162E0 on another list: call those, then move the entry they appended to layer
# 0x804C36 over to the new layer. The PS2 does not check the capacity; a full layer drops the
# sprite here (Kakusei-form trails of both players can exceed new1's 160 entries).
NEW_LAYER_ASM = """
power_draw:
        move.l  d6,-(a7)
        move.w  $2a(a6),d6
        lea     {zoom}.l,a0
        move.l  (a0,d6.w),d6
        jsr     $11626a.l
        move.l  (a7)+,d6
        lea     {new2}.l,a1
        lea     {new2_ctr}.l,a0
        move.w  #{new2_cap},d1
        bra   to_layer
trail_draw:
        jsr     $1162e0.l
        lea     {new1}.l,a1
        lea     {new1_ctr}.l,a0
        move.w  #{new1_cap},d1
to_layer:
        move.w  (a0),d0                 ; byte counter of the new layer
        cmp.w   d1,d0
        bcc   tl_full
        adda.w  d0,a1
        addi.w  #12,(a0)
        bra   tl_move
tl_full:    suba.l  a1,a1
tl_move:    move.w  $806fe6.l,d0
        subi.w  #12,d0
        move.w  d0,$806fe6.l
        lea     $804c36.l,a0
        adda.w  d0,a0
        move.l  a1,d0
        beq   tl_done
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
tl_done:    rts
"""

# Trail walker: Arrange's shot walker 0x981510 walks both shot pools (as the arcade 0x126960),
# then both trail pools (0x9801C0: a 6-position history of the owner shot, scrolled with the
# shots, drawn by the routine of its type, table 0xC079D0). The walker is called where the
# arcade frame calls 0x126960.
TRAIL_WALK_SITE = 0x17C854
TRAIL_TABLE = 0xC079D0
TRAIL_ROOTS = (0x9801C0, 0x995EA0, 0x996120, 0x996630, 0x996DB0,   # Chihiro (trail types 0..0x3C)
               0x9977B0, 0x997A30, 0x997F40, 0x9986C0)             # Black (0x40..0x7C)
TRAIL_WALK_ASM = """
power_walk:
        jsr     $126960.l
        move.w  $813b20.l,d6            ; the shot walker's scroll step
        tst.w   $813998.l
        bne   pw_l1
        move.w  $801166.l,$81292a.l
pw_l1:      lea     {trail1}.l,a6
        jsr     F_9801c0
        tst.w   $813998.l
        bne   pw_l2
        bchg    #0,$81292b.l
pw_l2:      lea     {trail2}.l,a6
        jmp     F_9801c0
power_no_trail:
        rts
"""
# Buttons (Arrange player update 0x82B970, after the barrier and Kakusei checks, which stay the
# arcade's). The arcade (0x122B08..0x122CC8) fires the normal shot on A and turns the laser on
# after A is held 25 frames (+0x4E); C (bit 6, moved from D by code.BARRIER_PATCHES) simulates
# A presses (0x1229BE). Arrange: A held sets +0x40 = 4 at once (and the arcade's speed ramp to
# the focused speed); the first Power volley arms +0x99 = 0x10 tail volleys, counted down one
# per volley (+0x2E/+0x2F interval), so a short tap still fires them after release; when they
# have run out, releasing A ends the Power Shot (+0x52..+0x55 = 0, +0x56 = 1, +0x57 = 0 for
# character 0, as the shared ELF 0x1FD840; the start sets +0x56 = 1, +0x57 = 0, 0x1FD870). A
# never fires the normal shot: C fires it every other frame (0x830B20: +1 bit 4 toggled), also
# while the Power Shot is on (the Power Shot routine ends in that check). The arcade's laser
# beam helpers (0x124BD6 / 0x12480C) are not called; 0x124A50 (laser end: sound off, beam
# state cleared) still runs when the Power Shot ends.
# Kakusei-form switch (arcade 0x124668, Arrange: the ELF routine 0x2035E0, which the arcade
# mode's overlay does not use): the same record load (table 0x12785A, Arrange copy) and tail
# (0x1251C2, +0x7C = 0x1E, hitbox growth by +0x76), with Arrange's differences: the Power Shot
# is switched off (+0x40, +1 bit 7, +0x99), +0x8A = 0, +0x94 = 4; the normal form zeroes
# +0x52..+0x55 (ELF 0x1FD640; arcade 2, 0, 2, 0); the Kakusei form keeps the shot palette +0x48
# (the shots stay green) and sets +0x56 = 1, +0x57 = 0 (ELF 0x1FD790; arcade 4).
KAKUSEI_SWITCH = 0x124668
KAKUSEI_SWITCH_ASM = """
kakusei_switch:
        move.w  $66(a6),d1
        add.w   d1,d1
        move.w  $4a(a6),d0
        add.w   d0,d0
        add.w   d0,d0
        add.w   d1,d0
        lea     $12785a.l,a0
        movea.l (a0,d0.w),a0
        move.l  (a0)+,$10(a6)
        move.l  (a0)+,$14(a6)
        move.b  (a0)+,$35(a6)
        move.b  (a0)+,$34(a6)
        move.b  (a0)+,$2c(a6)
        move.b  (a0)+,$2d(a6)
        move.b  (a0)+,$2e(a6)
        move.b  (a0)+,$2f(a6)
        move.w  (a0)+,$66(a6)
        clr.w   $40(a6)
        bclr    #7,1(a6)
        clr.b   $99(a6)
        clr.w   $8a(a6)
        move.w  #4,$94(a6)
        tst.w   $66(a6)
        bne     ks_kakusei
        move.b  $46(a6),$1d(a6)
        move.b  $90(a6),$48(a6)
        move.b  $46(a6),$37(a6)
        moveq   #0,d0
        move.b  d0,$52(a6)
        move.b  d0,$54(a6)
        move.b  d0,$53(a6)
        move.b  d0,$55(a6)
        clr.w   $74(a6)
        rts
ks_kakusei:
        move.b  $47(a6),$1d(a6)
        move.b  $47(a6),$37(a6)
        move.b  #1,$56(a6)
        move.b  #0,$57(a6)
        jsr     $1251c2.l
        move.w  #$1e,$7c(a6)
        move.w  $76(a6),d0
        beq     ks_done
ks_grow:    move.l  #$100010,d1
        add.l   d1,$10(a6)
        add.l   d1,$14(a6)
        subq.w  #2,d0
        bgt     ks_grow
ks_done:    rts
"""
A_LOGIC = 0x122B08
RAPID_BLOCK, RAPID_BLOCK_END = 0x1229BE, 0x1229F2
BUTTON_ASM = """
arrange_a:
        btst    #4,$18(a6)
        beq     aa_up
        tst.w   $40(a6)
        bne     aa_ramp
        move.w  #4,$40(a6)
        cmpi.w  #$40,$58(a6)
        bcc     aa_fire
        move.w  #$38,$58(a6)
        moveq   #0,d0
        move.w  d0,$44(a6)
        move.w  d0,$5a(a6)
        move.w  d0,$5c(a6)
aa_ramp:    subq.b  #1,$50(a6)
        bcc     aa_fire
        move.b  #1,$50(a6)
        move.b  $1a(a6),d0
        cmp.b   $34(a6),d0
        beq     aa_fire
        bcs     aa_inc
        subq.b  #1,$1a(a6)
        bra     aa_fire
aa_inc:     addq.b  #1,$1a(a6)
        bra     aa_fire
aa_up:      tst.w   $40(a6)
        beq     aa_reset
        tst.b   $99(a6)
        beq     aa_end
        subq.b  #1,$2e(a6)
        bcs     aa_tail
        jmp     $1233ac.l
aa_tail:    move.b  $2f(a6),$2e(a6)
        subq.b  #1,$99(a6)
        jmp     $122cc8.l               ; dispatch (+0x40 = 4: the Power Shot)
aa_end:     clr.w   $40(a6)
        bclr    #7,1(a6)
        move.b  #4,$50(a6)
        jsr     $124a50.l
        cmpi.w  #$40,$58(a6)
        bcc     aa_reset
        cmpi.w  #$3c,$58(a6)
        beq     aa_end2
        move.w  #$3c,$58(a6)
        moveq   #0,d0
        move.w  d0,$44(a6)
        move.w  d0,$5a(a6)
        move.w  d0,$5c(a6)
aa_end2:    tst.w   $4a(a6)
        bne     aa_reset
        moveq   #0,d0
        move.b  d0,$52(a6)
        move.b  d0,$53(a6)
        move.b  d0,$54(a6)
        move.b  d0,$55(a6)
        move.b  #1,$56(a6)
        move.b  d0,$57(a6)
aa_reset:   move.b  #$19,$4e(a6)
        move.b  $35(a6),$1a(a6)
aa_fire:    tst.b   $5e(a6)
        bne     aa_out
        tst.w   $40(a6)
        beq     power_c_tail
        bset    #7,1(a6)
        bne     aa_on
        move.b  #1,$56(a6)
        clr.b   $57(a6)
        move.b  #$10,$99(a6)
        clr.b   $2e(a6)
aa_on:      subq.b  #1,$2e(a6)
        bcc     power_c_tail
        move.b  $2f(a6),$2e(a6)
        tst.b   $99(a6)
        beq     aa_disp
        subq.b  #1,$99(a6)
aa_disp:    jmp     $122cc8.l
aa_out:     jmp     $1233ac.l
; C rapid fire (Arrange 0x830B20): the normal shot every other frame while C is held
power_c_sound:
        lea     $123372.l,a0
        bra     power_c
power_c_tail:
        lea     $1233ac.l,a0
power_c:    btst    #6,$18(a6)
        beq     pc_out
        bchg    #4,1(a6)
        beq     pc_out
        move.w  $66(a6),d0
        add.w   d0,d0
        move.w  $4a(a6),d1
        add.w   d1,d1
        add.w   d1,d1
        add.w   d1,d0
        lea     $122cf0.l,a0
        movea.l (a0,d0.w),a0
pc_out:     jmp     (a0)
"""
# Black's Power Shot (Arrange 0x827D20 for the normal form, 0x8293C0 for the Kakusei form,
# spawner 0x83A140; stubs 0x82A850 / 0x82A880, dispatch slots 6/7; types 0x8030 / 0x8038:
# Tateha's laser handler slots 12-15). Normal form: Chihiro's routine with the spread angle
# step 5 (d5) and an exit when a scanned record does not complete a volley block. Kakusei
# form: blocks of 4 consecutive free records from record 35, all 4 taken at once (entries:
# the count word + 1), spread index per block. The spawner copies both angle bytes from the
# entry (no lever angle, no random). The handlers spawn trails of type 0x40 + spread index.
BLACK_POWER_SETS = (0xC03030, 0xC06580)
BLACK_POWER_RECORDS = ((0xC03140, 0xC031B0), (0xC068C0, 0xC06A50))
BLACK_POWER_KEYS = (((0x1A, 1),), ((2, 2),))
BLACK_POWER_DISPATCH = (6, 7)
BLACK_POWER_HANDLER_ROOTS = (0x98FA20, 0x990DE0, 0x9929B0, 0x993D70)
BLACK_POWER_HANDLER_SLOTS = (12, 13, 14, 15)
BLACK_POWER_ASM = """
; Black's Power Shot, normal form (Arrange 0x827D20) and Kakusei form (0x8293C0)
black_power_a:
        lea     {table_a}.l,a1
        bsr     bp_setup
        subq.w  #1,d0
        bsr     bp_cap
        moveq   #0,d7
bp_l2:      add.w   d6,d7
        dbra    d0,bp_l2
        subq.w  #1,d7
        bsr     bp_clear
bp_l3:      tst.w   (a0)
        bmi     bp_l4
        move.l  a0,(a2)+
        subq.w  #1,d6
        beq     black_spawn
bp_l4:      subq.w  #1,d3
        bne     power_c_tail
        move.w  d2,d3
        addq.w  #4,d4
        addq.w  #5,d5
        lea     $34(a0),a0
        dbra    d7,bp_l3
        bra     power_c_tail
black_power_b:
        lea     {table_b}.l,a1
        bsr     bp_setup
        bsr     bp_cap
        move.w  d0,d7
        subq.w  #1,d7
        bsr     bp_clear
bk_l1:      tst.w   (a0)
        bmi     bk_l2
        tst.b   $34(a0)
        bmi     bk_l2
        tst.b   $68(a0)
        bmi     bk_l2
        tst.b   $9c(a0)
        bmi     bk_l2
        move.l  a0,(a2)+
        lea     $34(a0),a0
        move.l  a0,(a2)+
        lea     $34(a0),a0
        move.l  a0,(a2)+
        lea     $34(a0),a0
        move.l  a0,(a2)+
        bra     black_spawn
bk_l2:      addq.w  #4,d4
        addq.w  #5,d5
        lea     $d0(a0),a0
        dbra    d7,bk_l1
        bra     power_c_tail
; pool part from record 35, level patterns in a1, d0 = volley count, d6 = count word
bp_setup:
        lea     {pool1}.l,a0
        movea.l $81268e.l,a2
        tst.b   7(a5)
        beq     bp_s1
        lea     {pool2}.l,a0
        movea.l $812696.l,a2
bp_s1:      move.w  $20(a6),d0
        add.w   d0,d0
        movea.l (a1,d0.w),a1
        move.w  (a2),d0
        move.w  (a1)+,d6
        rts
bp_cap:     tst.w   $813998.l
        bne     bp_s2
        lea     {cap}.l,a2
        adda.w  $4a(a6),a2
        cmp.w   (a2),d0
        bls     bp_s2
        move.w  (a2),d0
bp_s2:      rts
bp_clear:
        lea     {list_end}.l,a2
        moveq   #0,d1
        move.l  d1,-(a2)
        move.l  d1,-(a2)
        move.l  d1,-(a2)
        move.l  d1,-(a2)
        move.l  d1,-(a2)
        move.l  d1,-(a2)
        move.l  d1,-(a2)
        move.w  d6,d2
        move.w  d6,d3
        moveq   #0,d4
        moveq   #0,d5
        rts
black_spawn:
        lea     {list}.l,a4
bsp_1:      movea.l (a4)+,a0
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
        adda.w  $62(a6),a2
        move.l  (a2),(a0)+
        move.l  (a1)+,(a0)+
        move.l  (a1)+,(a0)+
        move.l  (a1)+,(a0)+
        move.b  (a1)+,d0
        add.b   d5,d0
        move.b  d0,(a0)+
        move.b  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.b  $48(a6),-1(a0)
        move.l  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.w  $64(a6),(a0)+
        move.w  (a1)+,(a0)+
        move.w  $20(a6),(a0)+
        move.w  d4,(a0)+
        lea     {spread_a}.l,a2
        move.l  (a2,d4.w),d0
        add.l   d0,-$1c(a0)
        lea     {spread_b}.l,a2
        move.l  (a2,d4.w),d0
        add.l   d0,-$18(a0)
        cmpa.l  #{list_end},a4
        beq     bsp_2
        tst.l   (a4)
        bne     bsp_1
bsp_2:      subq.w  #4,$62(a6)
        bcc     bsp_3
        move.w  #8,$62(a6)
bsp_3:      subq.w  #4,$64(a6)
        bcc     bsp_4
        move.w  #$c,$64(a6)
bsp_4:      bra     power_c_sound
"""
POWER_HANDLER_ROOTS = (0x98A650, 0x98BA10, 0x98CF30, 0x98E2F0)
POWER_HANDLER_SLOTS = (8, 9, 10, 11)        # types 0x20 / 0x24 / 0x28 / 0x2C: the arcade laser's
POWER_EXPECTED = {0x990EA0, 0x98FEA0, 0x993E30, 0x992E30, 0x9977B0, 0x997A30, 0x997F40, 0x9986C0,
                  0x9990C0, 0x98BAD0, 0x98AAD0, 0x98E3B0, 0x198430, 0x98D3B0, 0x9801C0, 0x1985C0,
                  0x995EA0, 0x996120, 0x996630, 0x996DB0}


def chihiro_power(state, ps: m68k.PatchSet, p: bytes, slots: list) -> str:
    from .detrans import porter
    from .stages import layout
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    sets = [_emit_power_set(state, ov, ps, t, r, slots) for t, r in zip(POWER_SETS, POWER_RECORDS)]
    spread = state.alloc_p(0x80 + 0x40, 4, "Power Shot spread and zoom tables")
    ps.add(spread, ov.read(POWER_SPREAD[0], 0x40) + ov.read(POWER_SPREAD[1], 0x40) + ov.read(POWER_ZOOM, 0x40),
           "Power Shot spread offsets (+0x10, +0x14) and zoom by spread index")
    pool1 = layout.SHOT_POOL + layout.ARCADE_SHOTS * layout.SHOT_RECORD
    pool2 = pool1 + layout.ARRANGE_SHOTS * layout.SHOT_RECORD
    lst = layout.SHOT_LIST
    trail1 = layout.TRAIL_POOL
    trail2 = trail1 + layout.TRAILS * layout.TRAIL_RECORD
    lay = layout.sprite_layout(True)
    (n1, c1, k1), (n2, c2, k2) = lay.new_layers[0], lay.new_layers[1]
    trail_lists = _sprites(ov, ps, state, TRAIL_LISTS, 4 * TRAIL_N, slots, "Power Shot trail lists")

    bsets = [_emit_power_set(state, ov, ps, t, r, slots, kf, ex)
             for t, r, kf, ex in zip(BLACK_POWER_SETS, BLACK_POWER_RECORDS, BLACK_POWER_KEYS, (0, 1))]
    d = porter.port_roots(list(POWER_HANDLER_ROOTS) + list(BLACK_POWER_HANDLER_ROOTS) + list(TRAIL_ROOTS),
                          state.work / "ps2" / "detrans" / "power.json")
    probs = {int(k, 16) for k in d["problems"]}
    assert probs <= POWER_EXPECTED, f"new problems in the ported Power Shot code: {d['problems']}"
    text = d["text"]
    # 0x97E860: zoom by spread index, then the new2 wrapper
    text = _fix(text, r"^F_97e860:\n(?:\t.*\n)+?\trts\n", "F_97e860:\n\tjmp\tpower_draw\n")
    # ELF 0x205E70, the hit-record load: the arcade 0x12475A plus the spark timer and the spread
    text = _fix(text, r"^\tjsr\t\$12475a\.l$", "\tjsr\tpower_hit", 4)
    for guess, (k, field) in {0x17F39A: (0, "flight"), 0x17F4AA: (0, "hit"),
                              0x181A88: (1, "flight"), 0x181C18: (1, "hit")}.items():
        text = _fix(text, rf"^\tlea\.l\t\${guess:x}\.l,a0", f"\tlea.l\t${sets[k][field]:x}.l,a0")
    for guess, (k, field) in {0x16D832: (0, "flight"), 0x16D8A2: (0, "hit"),
                              0x179500: (1, "flight"), 0x179690: (1, "hit")}.items():
        text = _fix(text, rf"^\tlea\.l\t\${guess:x}\.l,a0", f"\tlea.l\t${bsets[k][field]:x}.l,a0")
    # Black's explosion wobble (0x990EA0 / 0x993E30): the random byte added once more for every
    # 16 of spread index (the porter could not express the byte-add / borrow flags)
    import re
    loops = iter(range(2))
    text, n = re.subn(r"^(\tmove\.b\td0,d1\n\tmove\.w\t42\(a6\),d2\n)(?:.*\n)*?\tmove\.b\td0,27\(a6\)\n",
                      lambda m: (lambda i: f"{m.group(1)}bw_{i}:\tadd.b\td1,d0\n\tsubi.w\t#$10,d2\n\tbcc\tbw_{i}\n"
                                           "\tmove.b\td0,27(a6)\n")(next(loops)), text, flags=re.M)
    assert n == 2, f"Black explosion wobble loops: {n}"
    text = _fix(text, r"^\tlea\.l\t\$810b46\.l,a0", f"\tlea.l\t${trail1:x}.l,a0")
    text = _fix(text, r"^\tlea\.l\t\$80f5b6\.l,a0", f"\tlea.l\t${trail2:x}.l,a0")
    text = _fix(text, r"^\tlea\.l\t\$17ea2e\.l,a0", "\tlea.l\ttrail_table.l,a0")
    for i in range(TRAIL_N):
        text = _fix(text, rf"^\tlea\.l\t\${0x17EAAE + 16 * i:x}\.l,a0", f"\tlea.l\t${trail_lists + 16 * i:x}.l,a0")
    text = _fix(text, r"^\tj(sr|mp)\tF_1985c0$", r"\tj\1\ttrail_draw", None)
    # the trail draw table: Chihiro's types (+0x2A 0..0x3C) use the first four routines
    tt = struct.unpack("<32I", ov.read(TRAIL_TABLE, 4 * 32))
    names = {fn: f"F_{fn:x}" for fn in TRAIL_ROOTS[1:]}
    assert set(tt) == set(TRAIL_ROOTS[1:]), "trail draw table"
    text += "\ntrail_table:\n" + "".join(
        f"\tdc.l\t{names.get(fn, 'power_no_trail')}\n" for fn in tt)
    text += TRAIL_WALK_ASM.format(trail1=f"${trail1:x}", trail2=f"${trail2:x}")
    text += NEW_LAYER_ASM.format(zoom=f"${spread + 0x80:x}", new1=f"${n1:x}", new2=f"${n2:x}",
                                 new1_ctr=f"${c1:x}", new2_ctr=f"${c2:x}",
                                 new1_cap=12 * k1, new2_cap=12 * k2)
    text += POWER_ASM.format(table_a=f"${sets[0]['patterns']:x}", table_b=f"${sets[1]['patterns']:x}",
                             pool1=f"${pool1:x}", pool2=f"${pool2:x}", cap=f"${LASER_CAP:x}",
                             list=f"${lst:x}", list_end=f"${lst + 7 * 4:x}",
                             spread_a=f"${spread:x}", spread_b=f"${spread + 0x40:x}")
    text += BLACK_POWER_ASM.format(table_a=f"${bsets[0]['patterns']:x}", table_b=f"${bsets[1]['patterns']:x}",
                                   pool1=f"${pool1:x}", pool2=f"${pool2:x}", cap=f"${LASER_CAP:x}",
                                   list=f"${lst:x}", list_end=f"${lst + 7 * 4:x}",
                                   spread_a=f"${spread:x}", spread_b=f"${spread + 0x40:x}")
    text += POWER_HIT_ASM.format(spread_a=f"${spread:x}", spread_b=f"${spread + 0x40:x}")
    text += BUTTON_ASM + KAKUSEI_SWITCH_ASM
    code, _ = asm68k.assemble(text, 0x200000)
    addr = state.alloc_p(len(code), 4, "Chihiro Power Shot")
    code, syms = asm68k.assemble(text, addr)
    ps.add(addr, code, "Chihiro Power Shot routine, handlers, trail (ported from A0A9)")
    for slot, entry in zip(POWER_DISPATCH, ("chihiro_power_a", "chihiro_power_b")):
        ps.add(SHOT_DISPATCH + 4 * slot, struct.pack(">I", syms[entry]), f"shot dispatch slot {slot} -> {entry}")
    for slot, fn in zip(POWER_HANDLER_SLOTS + BLACK_POWER_HANDLER_SLOTS, POWER_HANDLER_ROOTS + BLACK_POWER_HANDLER_ROOTS):
        ps.add(HANDLER_TABLE + 4 * slot, struct.pack(">I", syms[f"F_{fn:x}"]), f"shot handler {slot} -> Arrange {fn:#x}")
    for slot, entry in zip(BLACK_POWER_DISPATCH, ("black_power_a", "black_power_b")):
        ps.add(SHOT_DISPATCH + 4 * slot, struct.pack(">I", syms[entry]), f"shot dispatch slot {slot} -> {entry}")
    assert p[TRAIL_WALK_SITE:TRAIL_WALK_SITE + 6] == b"\x00\x00\x00\x00\x00\x00", "frame call of the shot walker"
    ps.jsr(TRAIL_WALK_SITE, syms["power_walk"], "shot walker + Power Shot trails")
    assert p[A_LOGIC:A_LOGIC + 6] == b"\x00\x00\x00\x00\x00\x00", "A-button logic"
    ps.jmp(A_LOGIC, syms["arrange_a"], "Arrange A button: Power Shot at once, tail volleys, C rapid fire")
    assert p[RAPID_BLOCK:RAPID_BLOCK + 6] == b"\x00\x00\x00\x00\x00\x00", "rapid-fire block"
    ps.add(RAPID_BLOCK, bytes([0x60, RAPID_BLOCK_END - RAPID_BLOCK - 2]), "arcade rapid-fire block skipped (C is Arrange's 0x830B20)")
    assert p[KAKUSEI_SWITCH:KAKUSEI_SWITCH + 4] == b"\x00\x00\x00\x00", "Kakusei-form switch"
    ps.jmp(KAKUSEI_SWITCH, syms["kakusei_switch"], "Arrange Kakusei-form switch (ELF 0x2035E0)")
    return f"Power Shot at {addr:#x}, A logic at {syms['arrange_a']:#x}"
