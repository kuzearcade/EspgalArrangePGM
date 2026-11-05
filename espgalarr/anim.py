"""Arrange player animation (spec/arrange-characters.md, "Player animation").

The 68k (like the arcade mode) runs the player's animation state +0x58 through one handler table
(0x131C5A) for both characters, each handler stepping a frame list of arcade sprites. Arrange
gives each character its own 24-entry handler table (ELF 0x2D7730 Chihiro, 0x2D7790 Black; the
loader puts the pointer in the character record +0x10, the player setup copies it to +0x9A),
and its handlers step Arrange's own frame tables (ELF data, records holding bank A020 sprite
numbers). Most handlers are stubs that jump to one of four generic animators with constant
arguments (MIPS a0 = frame table, a1 = timer reload, a2 = end index, a3 = restart index or next
state, t0 = the alternative next state); the others are full handlers of the same patterns.
All of them do what the arcade's handlers do, with other tables and constants:

  loop     every reload+1 frames copy the record at +0x5C (x/y offset, +0x0A sprite, +0x0E size
           and maybe +0x3A) and step +0x5C; at `end` restart at `restart` (A0A9 0x83FC60)
  once     the same until `end`, then clear +0x5A/+0x5C (and +0x3A, +0x5F) and switch to state
           `next` (or `alt` when +0x40 is set) through the table at +0x9A (A0A9 0x840C90,
           0x841D90 with `alt`)
  reset    clear the animation, call 0x124668 and switch to state 0 or 0x24 (A0A9 0x846690)
  special  state 23: records also set the timer +0x5A and a speed +0x6C, a flagged record
           calls the character's voice (ELF 0x2161C0, table A0A9 0xBFCD40: the same bank indices as
           the arcade's 0x1266CE, whose SFX now hold the Arrange voices, voices.py), and +0x6C moves the
           player up to y 0x700 (A0A9 0x842F80; the arcade's 0x12879E ends at 0x80, Arrange 0x88)

The port writes the handlers as new 68k code, copies the frame tables to program ROM (their sprite
numbers go to the graphics stage, state.sprite_number_slots), and replaces the 68k's 17
`lea $131c5a.l,a0` (three dispatchers, the rest in the arcade handlers it no longer reaches) with a
call that picks the character's table, keeps it at +0x9A and returns it in a0. States 10..13 have
no Arrange handler (never set); they keep the arcade's. The sprites (379, bank A020) fit the free
A/B ROM space (about 150 KB A, 180 KB B more than before).
"""
from __future__ import annotations

import struct

from .pgm import asm68k, m68k

HANDLER_TABLES = {0: 0x2D7730, 2: 0x2D7790}      # +0x4A -> ELF table (24 handler addresses)
ARCADE_TABLE = 0x131C5A
STATES = 24
# the 68k's `lea $131c5a.l,a0`: three dispatchers, and the arcade handlers' own state switches
# (0x127DB8..0x12884E, no longer reached: patched too)
DISPATCH_SITES = (0x1233B8, 0x123528, 0x12395E)
ARCADE_HANDLERS = (0x127DB8, 0x12884E)
ANIMATORS = {0x83FC60: "loop", 0x840C90: "once", 0x841D90: "once"}
SPECIAL = 0x842F80
# full handlers (hand-checked against their de-translations; the table address is re-checked
# in the MIPS at build time): kind, frame table, parameters
FULL = {
    0x844590: ("loop", 0x2D7A80, dict(set6=True, reload=1, end=0x90, restart=0x70, with3a=True)),
    0x845570: ("once", 0x2D91F0, dict(set6=True, reload=2, end=0x10, next=0, clr3a=True)),
    0x846690: ("reset", None, {}),
    0x846950: ("reset", None, {}),
    0x846C10: ("once", 0x2DAE10, dict(reload=1, end=0x58, next=0x4C)),
    0x847C30: ("loop", 0x2DB520, dict(reload=1, end=0x10, restart=0)),
    0x848B30: ("once", 0x2DB670, dict(reload=1, end=0x14, next=0x54)),
    0x849B50: ("loop", 0x2DB840, dict(reload=1, end=0x10, restart=0)),
    0x84AA50: ("once", 0x2DB990, dict(reload=1, end=0x0C, next=0, alt=0x24, clr3a=True, clr5f=True)),
    0x84BB90: ("once", 0x2DB990, dict(reload=1, end=0x10, next=0, alt=0x24, clr3a=True, clr5f=True)),
}
SPECIAL_TABLE, SPECIAL_END = 0x2DBAC0, 0x88
VOICE = 0x1266CE                 # voice call (Arrange: ELF 0x2161C0; same SFX indices, voices.py)
RESET_CALL = 0x124668            # ELF 0x2035E0 (address map)
RECORD, SPECIAL_RECORD = 16, 20
SPRITE_FIELD = 8


def _stub(ov, md, h: int):
    """(animator, a0..a3, t0) of a stub handler, or None for a full one."""
    regs = {"$zero": 0}
    for i in md.disasm(ov.read(h, 0x40), h):
        o = i.op_str.split(", ")
        if i.mnemonic == "lui":
            regs[o[0]] = int(o[1], 0) << 16
        elif i.mnemonic in ("addiu", "daddiu") and len(o) == 3 and o[1] in regs:
            regs[o[0]] = (regs[o[1]] + int(o[2], 0)) & 0xFFFFFFFF
        elif i.mnemonic == "move" and o[1] in regs:
            regs[o[0]] = regs[o[1]]
        elif i.mnemonic == "j":
            target = int(o[0], 0)
            # the delay slot
            d = next(md.disasm(ov.read(i.address + 4, 4), i.address + 4))
            od = d.op_str.split(", ")
            if d.mnemonic in ("addiu", "daddiu") and len(od) == 3 and od[1] in regs:
                regs[od[0]] = (regs[od[1]] + int(od[2], 0)) & 0xFFFFFFFF
            elif d.mnemonic == "move" and od[1] in regs:
                regs[od[0]] = regs[od[1]]
            if i.address - h >= 0x20:
                return None
            return target, [regs.get(r) for r in ("$a0", "$a1", "$a2", "$a3", "$t0")]
        elif i.mnemonic in ("jal", "jr", "beqz", "bnez", "beq", "bne") or i.address - h >= 0x20:
            return None
    return None


def _table_in(ov, md, h: int, size: int) -> set[int]:
    regs, out = {}, set()
    for i in md.disasm(ov.read(h, size), h):
        o = i.op_str.split(", ")
        if i.mnemonic == "lui":
            regs[o[0]] = int(o[1], 0) << 16
        elif i.mnemonic in ("addiu", "daddiu") and len(o) == 3 and o[1] in regs and o[1] != "$zero":
            out.add((regs[o[1]] + int(o[2], 0)) & 0xFFFFFFFF)
    return out


def _copy(n: int) -> str:
    return "\tmove.w\t$66(a6),d1\n\tadd.w\td1,d1\n\tmove.w\t$4a(a6),d0\n\tadd.w\td0,d0\n\tadd.w\td0,d0\n" \
           "\tadd.w\td1,d0\n" \
           f"\tlea\t${n:x}.l,a0\n\tmovea.l\t(a0,d0.w),a0\n\tmove.w\t$5c(a6),d0\n\tmovea.l\t(a0,d0.w),a0\n" \
           "\tmove.l\t(a0)+,$6(a6)\n\tmove.l\t(a0)+,d0\n\tadd.l\td0,$6(a6)\n\tmove.l\t(a0)+,$a(a6)\n" \
           "\tmove.w\t(a0)+,$e(a6)\n"


def _switch(next_: int, alt: int | None, lab: str) -> str:
    s = f"\tmove.w\t#${next_:x},d0\n"
    if alt is not None:
        s += f"\ttst.w\t$40(a6)\n\tbeq\t{lab}_n\n\tmove.w\t#${alt:x},d0\n{lab}_n:\n"
    return s + "\tmove.w\td0,$58(a6)\n\tmovea.l\t$9a(a6),a0\n\tmovea.l\t(a0,d0.w),a0\n\tjsr\t(a0)\n\trts\n"


def _handler(lab: str, kind: str, tab: int | None, k: dict) -> str:
    bit = "bset" if k.get("set6") else "bclr"
    if kind == "loop":
        return (f"{lab}:\n\t{bit}\t#6,$1c(a6)\n\tsubq.w\t#1,$5a(a6)\n\tbcc\t{lab}_x\n"
                f"\tmove.w\t#${k['reload']:x},$5a(a6)\n" + _copy(tab)
                + ("\tmove.w\t(a0)+,$3a(a6)\n" if k.get("with3a") else "")
                + f"\taddq.w\t#4,$5c(a6)\n\tcmpi.w\t#${k['end']:x},$5c(a6)\n\tbcs\t{lab}_x\n"
                f"\tmove.w\t#${k['restart']:x},$5c(a6)\n{lab}_x:\n\trts\n")
    if kind == "once":
        return (f"{lab}:\n\t{bit}\t#6,$1c(a6)\n\tsubq.w\t#1,$5a(a6)\n\tbcc\t{lab}_x\n"
                f"\tmove.w\t#${k['reload']:x},$5a(a6)\n\tcmpi.w\t#${k['end']:x},$5c(a6)\n\tbcs\t{lab}_p\n"
                "\tclr.l\t$5a(a6)\n" + ("\tclr.w\t$3a(a6)\n" if k.get("clr3a") else "")
                + ("\tclr.b\t$5f(a6)\n" if k.get("clr5f") else "")
                + _switch(k["next"], k.get("alt"), lab)
                + f"{lab}_p:\n" + _copy(tab) + f"\taddq.w\t#4,$5c(a6)\n{lab}_x:\n\trts\n")
    if kind == "reset":
        return (f"{lab}:\n\tbclr\t#6,$1c(a6)\n\tclr.b\t$5e(a6)\n\tclr.l\t$5a(a6)\n\tclr.w\t$3a(a6)\n"
                f"\tjsr\t${RESET_CALL:x}.l\n" + _switch(0, 0x24, lab))
    if kind == "special":
        return (f"{lab}:\n\tbclr\t#6,$1c(a6)\n\tsubq.w\t#1,$5a(a6)\n\tbcc\t{lab}_m\n"
                f"\tcmpi.w\t#${k['end']:x},$5c(a6)\n\tbcs\t{lab}_p\n"
                "\tclr.l\t$5a(a6)\n\tclr.w\t$3a(a6)\n\tclr.b\t$5f(a6)\n" + _switch(0, 0x24, lab)
                + f"{lab}_p:\n" + _copy(tab)
                + f"\tmove.w\t(a0)+,$5a(a6)\n\tmove.w\t(a0)+,$6c(a6)\n\ttst.w\t(a0)\n\tbeq\t{lab}_v\n"
                f"\tjsr\t${VOICE:x}.l\n{lab}_v:\n\taddq.w\t#4,$5c(a6)\n"
                f"{lab}_m:\n\tmove.w\t$6c(a6),d0\n\tsub.w\td0,$2(a6)\n\tcmpi.w\t#$700,$2(a6)\n\tbcc\t{lab}_x\n"
                f"\tmove.w\t#$700,$2(a6)\n{lab}_x:\n\trts\n")
    raise ValueError(kind)


TABLE_ASM = """
anim_table:
        lea     ${c:x}.l,a0
        tst.w   $4a(a6)
        beq     anim_table_set
        lea     ${b:x}.l,a0
anim_table_set:
        move.l  a0,$9a(a6)
        rts
"""


def port_animation(state, ps: m68k.PatchSet, p: bytes) -> str:
    import capstone

    from .ps2 import elf as elfm, overlay
    ov = overlay.Overlay(state.work / "ps2" / "disc" / "A0A0" / "A0A9")
    elf = elfm.Elf(state.work / "ps2" / "disc" / "SLPS_253.52")
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_LITTLE_ENDIAN)
    # 1. each character's handlers: (kind, frame table, parameters) per state
    plan: dict[int, list] = {}
    tables: dict[int, int] = {}            # frame table -> records per list (the largest end used)
    for ch, t in HANDLER_TABLES.items():
        hs = struct.unpack("<24I", elf.read(t, 4 * STATES))
        row = []
        for s, h in enumerate(hs):
            if not h:
                row.append(None)
                continue
            st = _stub(ov, md, h)
            if st is not None:
                target, (a0, a1, a2, a3, t0) = st
                if target == SPECIAL:
                    row.append(("special", SPECIAL_TABLE, dict(end=SPECIAL_END)))
                    continue
                kind = ANIMATORS[target]
                if kind == "loop":
                    k = dict(reload=a1, end=a2, restart=a3, with3a=True)
                else:
                    k = dict(reload=a1, end=a2, next=a3, clr3a=True)
                    if target == 0x841D90:
                        k["alt"] = t0
                row.append((kind, a0, k))
            else:
                kind, tab, k = FULL[h]
                if tab is not None:
                    assert tab in _table_in(ov, md, h, 0x1140), f"animation handler {h:#x}: table {tab:#x}"
                row.append((kind, tab, k))
        plan[ch] = row
        for e in row:
            if e and e[1] is not None:
                tables[e[1]] = max(tables.get(e[1], 0), e[2]["end"] // 4)
    assert tables[SPECIAL_TABLE] == SPECIAL_END // 4
    # 2. frame tables to program ROM: pointer table (4 longs: character x form) -> frame lists
    #    (longs) -> records (16 bytes, the special one 20), sprite numbers to the gfx stage
    blob = bytearray()
    base = state.alloc_p(0, 4, "Arrange player animation tables")     # current position
    rec_at: dict[tuple[int, int], int] = {}
    slots: list[tuple[int, int]] = []
    lists: dict[int, int] = {}
    pointers: dict[int, int] = {}

    def at() -> int:
        return base + len(blob)
    plans = []
    for tab, n in sorted(tables.items()):
        size = SPECIAL_RECORD if tab == SPECIAL_TABLE else RECORD
        ptrs = struct.unpack("<4I", elf.read(tab, 16))
        plans.append((tab, n, size, ptrs))
    # records first
    for tab, n, size, ptrs in plans:
        for lp in ptrs:
            if not lp or lp in lists:
                continue
            for r in struct.unpack(f"<{n}I", elf.read(lp, 4 * n)):
                if r and (r, size) not in rec_at:
                    rec = elf.read(r, size)
                    sid = struct.unpack_from(">I", rec, SPRITE_FIELD)[0]
                    assert 0 < sid < 0x2000, f"frame record {r:#x}: sprite {sid:#x}"
                    rec_at[(r, size)] = at()
                    slots.append((at() + SPRITE_FIELD, sid))
                    blob += rec
            lists[lp] = -1
    # frame lists
    for tab, n, size, ptrs in plans:
        for lp in ptrs:
            if not lp or lists[lp] != -1:
                continue
            lists[lp] = at()
            for r in struct.unpack(f"<{n}I", elf.read(lp, 4 * n)):
                blob += struct.pack(">I", rec_at[(r, size)] if r else 0)
    # pointer tables
    for tab, n, size, ptrs in plans:
        pointers[tab] = at()
        for lp in ptrs:
            blob += struct.pack(">I", lists[lp] if lp else 0)
    got = state.alloc_p(len(blob), 4, "Arrange player animation tables")
    assert got == base, "animation tables: allocation moved"
    ps.add(base, bytes(blob), f"Arrange player animation frame tables ({len(rec_at)} records, sprite numbers, gfx stage)")
    state.sprite_number_slots = getattr(state, "sprite_number_slots", []) + slots
    # 3. handlers and the two handler tables
    text = []
    labels: dict[int, list[str]] = {}
    done: dict[tuple, str] = {}
    for ch, row in plan.items():
        labels[ch] = []
        for s, e in enumerate(row):
            if e is None:
                labels[ch].append(None)
                continue
            key = (e[0], e[1], tuple(sorted(e[2].items())))
            if key not in done:
                lab = f"anim_{len(done)}"
                done[key] = lab
                text.append(_handler(lab, e[0], pointers.get(e[1]), e[2]))
            labels[ch].append(done[key])
    arcade = [struct.unpack_from(">I", p, ARCADE_TABLE + 4 * s)[0] for s in range(STATES)]
    src = "\n".join(text)
    code, syms = asm68k.assemble(src + TABLE_ASM.format(c=0, b=0), 0x200000)
    tabs_n = 2 * 4 * STATES
    addr = state.alloc_p(len(code) + tabs_n, 4, "Arrange player animation handlers")
    tc, tb = addr + len(code), addr + len(code) + 4 * STATES
    code, syms = asm68k.assemble(src + TABLE_ASM.format(c=tc, b=tb), addr)
    assert len(code) + tabs_n == tb + 4 * STATES - addr
    blob2 = bytearray(code)
    for ch in (0, 2):
        for s in range(STATES):
            lab = labels[ch][s]
            blob2 += struct.pack(">I", syms[lab] if lab else arcade[s])
    ps.add(addr, bytes(blob2), "Arrange player animation handlers and handler tables")
    # 4. the 68k's handler-table loads
    site = bytes.fromhex("41f9") + struct.pack(">I", ARCADE_TABLE)
    sites = [i for i in range(0x100000, 0x200000, 2) if p[i:i + 6] == site]
    outside = [i for i in sites if not ARCADE_HANDLERS[0] <= i < ARCADE_HANDLERS[1]]
    assert outside == list(DISPATCH_SITES), f"handler table loads: {[hex(x) for x in sites]}"
    for i in sites:
        ps.add(i, b"\x4e\xb9" + struct.pack(">I", syms["anim_table"]), f"player animation table by character at {i:#x}")
    return (f"Arrange player animation: {len(done)} handlers, {len(tables)} frame tables, {len(rec_at)} records "
            f"({len(set(n for _, n in slots))} sprites), {len(sites)} table loads")
