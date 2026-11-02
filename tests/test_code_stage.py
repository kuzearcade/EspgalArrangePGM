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
