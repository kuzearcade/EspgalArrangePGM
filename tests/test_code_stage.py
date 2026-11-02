import struct

from espgalarr.pgm import m68k
from espgalarr.stages import code as stage


def test_extend_and_continue_patches():
    p = bytearray(0x200000)
    for i, v in enumerate((400000, 800000, 200000, 0xFFFFFFFF)):
        struct.pack_into(">I", p, stage.EXTEND_TABLE_1 + 4 * i, v)
        struct.pack_into(">I", p, stage.EXTEND_TABLE_2 + 4 * i, v + 1_000_000 if v != 0xFFFFFFFF else v)
    for a in stage.CONTINUE_BNE_SITES:
        p[a:a + 2] = b"\x66\x00"
    ps = m68k.PatchSet()
    stage.extend_patches(ps, bytes(p))
    stage.continue_patches(ps, bytes(p))
    ps.apply(p)
    assert struct.unpack_from(">I", p, stage.EXTEND_TABLE_1)[0] == 8_000_000
    assert struct.unpack_from(">I", p, stage.EXTEND_TABLE_2 + 8)[0] == 28_000_000
    assert struct.unpack_from(">I", p, stage.EXTEND_TABLE_1 + 12)[0] == 0xFFFFFFFF
    assert all(p[a:a + 2] == b"\x60\x00" for a in stage.CONTINUE_BNE_SITES)


def test_foreign_twin_guard():
    # the scroll accumulator clear (two longs and a word of RAM) vs the routine the matcher
    # once paired with it (other variables): a wrong twin; the same routine, and one using a
    # variable the porter moves on purpose (ARRANGE_OVERRIDES), are not
    rom = bytearray(0x100)
    rom[0x10:0x2a] = bytes.fromhex("00000000000000000000000000000000000000000000000000000000")
    u = {"orig": 0x10, "end": 0x2a, "text": "F_1:\n\tclr.w\t14(a6)\n\tclr.w\t$813b76.l\n\trts\n"}
    assert stage._foreign_twin(u, bytes(rom))
    u["text"] = "F_1:\n\tclr.l\t$80703c.l\n\tclr.w\t$80704e.l\n\trts\n"
    assert not stage._foreign_twin(u, bytes(rom))
    rom[0x30:0x38] = bytes.fromhex("0000000000000000")
    v = {"orig": 0x30, "end": 0x38, "text": "F_2:\n\tmove.w\t#$4f,$818352.l\n\trts\n"}
    assert not stage._foreign_twin(v, bytes(rom))


def test_ending_task_assembles():
    # the Arrange staff-roll task: entries for the init / run hooks, the logo script after it
    from espgalarr.pgm import asm68k
    src = stage.ENDING_TASK.replace("SPEED", "$20") + "end_script:\n\tdc.w\t0\nend_text:\n\tdc.b\t$41, 0\n"
    code, syms = asm68k.assemble(src, 0x300000)
    assert syms["end_init"] == 0x300000 and syms["end_run"] > syms["end_init"]
    # the button test reads both players' new presses of A..D
    assert bytes.fromhex("000000000000") in code and bytes.fromhex("807900801206") in code
    assert bytes.fromhex("00000000") in code


def test_player_animation_handlers_assemble():
    # the four handler patterns of the Arrange player animation (anim.py) assemble, and the
    # loop handler is the arcade's pattern (0x127DB8): timer reload, end and restart immediates
    from espgalarr import anim
    from espgalarr.pgm import asm68k
    src = "\n".join([
        anim._handler("h_loop", "loop", 0x300000, dict(reload=2, end=0x20, restart=0, with3a=True)),
        anim._handler("h_once", "once", 0x300010, dict(reload=1, end=0x34, next=0, alt=0x38, clr3a=True)),
        anim._handler("h_reset", "reset", None, {}),
        anim._handler("h_special", "special", 0x300020, dict(end=0x88)),
    ]) + anim.TABLE_ASM.format(c=0x300100, b=0x300200)
    code, syms = asm68k.assemble(src, 0x200000)
    assert set(syms) >= {"h_loop", "h_once", "h_reset", "h_special", "anim_table"}
    loop = code[:syms["h_once"] - 0x200000]
    assert loop.startswith(bytes.fromhex("00000000000000000000"))   # bclr #6,$1c(a6); subq.w #1,$5a(a6)
    assert bytes.fromhex("3d7c0002005a") in loop                     # move.w #2,$5a(a6)
    assert bytes.fromhex("000000000000") in loop                     # cmpi.w #$20,$5c(a6)
    assert bytes.fromhex("00000000") in loop                         # move.w (a0)+,$3a(a6)
    once = code[syms["h_once"] - 0x200000:syms["h_reset"] - 0x200000]
    assert bytes.fromhex("206e009a") in once                         # movea.l $9a(a6),a0
    assert bytes.fromhex("00000000") not in once


def test_restructured_volley_asm():
    from espgalarr import restructured
    from espgalarr.pgm import asm68k
    assert restructured._matches(bytes.fromhex("522e009e"), "522e????")
    assert not restructured._matches(bytes.fromhex("00000000"), "522e????")
    site, end, head, call = restructured.CORE_TURRET_VOLLEY
    assert site < call < end
    code, syms = asm68k.assemble(restructured.CORE_TURRET_ASM.replace("{end:x}", f"{end:x}"), 0x200000)
    # three 0x1713EC calls, then back to the run routine
    assert code.count(bytes.fromhex("000000000000")) == 3
    assert code.endswith(bytes.fromhex("4ef9") + struct.pack(">I", end))


def test_bound_spread():
    # a spread entry's source: the native argument calls bound to its allocator, labels renamed
    from espgalarr.detrans import porter
    src = ("F_8b64e0:\n\tbtst\t#1,(a0)\n\tbeq\tL_8b7688\n\t; ??? if ltu(a0@2, $100000) goto L_8b6840\n"
           "L_8b6840:\n\t; ??? jsr arg0\nL_8b7688:\n\t; ??? jsr $16?\n\trts\n")
    t = porter._bound_spread(0x8E7000, src)
    assert "F_8e7000:" in t and "F_8b64e0" not in t and "ltu(" not in t
    assert t.count("\tjsr\tF_8e7010") == 2 and "L_8e70008b6840:" in t and "beq\tL_8e70008b7688" in t
    assert porter.SPREAD_ENTRIES.keys() <= porter.KAKUSEI_HELPERS
    assert not set(porter.BULLET_ALLOCATORS) & porter.KAKUSEI_HELPERS
