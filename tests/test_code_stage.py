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
