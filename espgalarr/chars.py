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


def pattern_set(ov, table: int) -> dict:
    """Levels (entry addresses per level) and the list each entry's two pointers get."""
    levels = []
    k = 0
    while True:
        p = struct.unpack("<I", ov.read(table + 4 * k, 4))[0]
        if not p:
            break
        n = _w(ov.read(p, 2))
        levels.append((p, [p + 2 + ENTRY * i for i in range(n)]))
        k += 1
    entries = sorted((e for _, es in levels for e in es), key=lambda e: _w(ov.read(e + 0x24, 2)))
    kind, last, n_kinds = {}, None, 0
    for e in entries:
        b = ov.read(e, ENTRY)
        assert b[0x0A:0x0E] == b"\xff" * 4 and b[0x1E:0x22] == b"\xff" * 4, f"entry {e:#x} pointers"
        key = (_w(b, 0x26), _w(b, 0x28))
        if key != last:
            n_kinds += 1
            last = key
        kind[e] = n_kinds - 1
    end = max(e + ENTRY for e in entries)
    base = (end + LIST - 1) & ~(LIST - 1)
    while not all(_is_list(ov, base + LIST * i) for i in range(2 * n_kinds)):
        base += LIST
        assert base < end + 0x1000, f"sprite lists after the patterns of {table:#x}"
    assert not _is_list(ov, base + LIST * 2 * n_kinds), f"more sprite lists than kinds at {table:#x}"
    return {"levels": levels, "kind": kind, "kinds": n_kinds, "lists": base}


def _emit_set(state, ov, ps: m68k.PatchSet, table: int, slots: list) -> int:
    s = pattern_set(ov, table)
    k = s["kinds"]
    lists = state.alloc_p(2 * k * LIST, 4, "Chihiro sprite lists")
    blob = bytearray()
    for i in range(2 * k):
        src = s["lists"] + LIST * i
        blob += ov.read(src, LIST)
        for j in range(3):
            slots.append((lists + LIST * i + 4 * j, struct.unpack(">I", ov.read(src + 4 * j, 4))[0]))
    ps.add(lists, bytes(blob), f"Chihiro shot sprite lists of {table:#x} (sprite numbers, gfx stage)")
    size = 4 * (len(s["levels"]) + 1) + sum(2 + ENTRY * len(es) for _, es in s["levels"])
    out = state.alloc_p(size, 4, "Chihiro shot patterns")
    blob = bytearray(4 * (len(s["levels"]) + 1))
    for n, (p, es) in enumerate(s["levels"]):
        struct.pack_into(">I", blob, 4 * n, out + len(blob))
        blob += ov.read(p, 2)
        for e in es:
            b = bytearray(ov.read(e, ENTRY))
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
    # volley counts of character 0 (two word arrays: +0x66 = 0 / 2)
    arc, arr = VOLLEY_TABLES
    for k in range(2):
        a = struct.unpack_from(">I", p, arc + 4 * k)[0]
        b = struct.unpack("<I", ov.read(arr + 4 * k, 4))[0]
        n = (struct.unpack_from(">I", p, arc + 4 * (k + 1))[0] - a) // 2
        assert 0 < n <= 8, f"volley array {a:#x}"
        ps.add(a, ov.read(b, 2 * n), f"Chihiro volley counts {k} ({n} levels)")
    hnote = chihiro_handlers(state, ps, p, slots)
    state.sprite_number_slots = getattr(state, "sprite_number_slots", []) + slots
    return (f"Chihiro normal shot at {addr:#x}, {hnote} ({len(slots)} sprite slots, "
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


def chihiro_handlers(state, ps: m68k.PatchSet, p: bytes, slots: list) -> str:
    from .detrans import porter
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    d = porter.port_roots(list(HANDLER_ROOTS), state.work / "ps2" / "detrans" / "chihiro_handlers.json")
    probs = {int(k, 16) for k in d["problems"]}
    assert probs <= EXPECTED_PROBLEMS, f"new problems in the ported handlers: {d['problems']}"
    s = pattern_set(ov, PATTERN_SETS[0])
    # flight records (16 bytes, no pointers) and hit records (the list pointer at +6 goes to +0x1E)
    flight = _ptr_table(ov, FLIGHT_TABLE)
    hit = _ptr_table(ov, HIT_TABLE)
    assert len(flight) == len(hit), "flight / hit tables"
    four = s["lists"] + 2 * LIST * s["kinds"]          # 4-sprite hit lists after the 2K lists
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
        blob += ov.read(src, LIST)
        for j in range(4):
            slots.append((lists4 + LIST * k + 4 * j, struct.unpack(">I", ov.read(src + 4 * j, 4))[0]))
    blob += ov.read(ZOOM_TABLE, 32)
    ps.add(base, bytes(blob), "Chihiro shot flight/hit records, hit lists, zoom table")
    text = d["text"]
    text = _fix(text, r"^\tjsr\tF_9832c0\b.*$", "\tjsr\tchihiro_draw", None)
    text = _fix(text, r"^\tjsr\tF_97e830\b.*$", "\tjsr\t$116226.l", None)
    text = _fix(text, r"^\tlea(?:\.l)?\t\$[0-9a-f]+(?:\.[wl])?,a0\s*(?:;.*)?\n(\tjsr\t\$12471e\.l)",
                f"\tlea.l\t${base:x}.l,a0\n\\1")
    text = _fix(text, r"^\tlea(?:\.l)?\t\$[0-9a-f]+(?:\.[wl])?,a0\s*(?:;.*)?\n(\tjsr\t\$12473c\.l)",
                f"\tlea.l\t${base + tab_size:x}.l,a0\n\\1")
    text = _fix(text, r"^(F_814080:\n\tlea\.l\t)\$80ef36\.l", "\\1$81b596.l")
    text += HANDLER_DRAW.format(zoom=f"${zoom:x}")
    code, _ = asm68k.assemble(text, 0x200000)
    addr = state.alloc_p(len(code), 4, "Chihiro shot handlers")
    code, syms = asm68k.assemble(text, addr)
    ps.add(addr, code, "Chihiro shot handlers (ported from A0A9)")
    for k, fn in enumerate(HANDLER_ROOTS):
        ps.add(HANDLER_TABLE + 4 * k, struct.pack(">I", syms[f"F_{fn:x}"]), f"shot handler {k} -> Arrange {fn:#x}")
    return f"handlers at {addr:#x}"
