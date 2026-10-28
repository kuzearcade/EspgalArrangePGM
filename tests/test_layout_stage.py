import struct

from espgalarr.pgm import m68k
from espgalarr.stages import layout


def test_relocate_pools_rewrites_base_and_end_refs():
    p = bytearray(0x1C0000)
    pool = layout.Pool("t", 0x815680, 90, 0x24, 0x2C)
    # lea base.l,a6 at 0x110000 ; cmpa.l #end,a6 at 0x110010
    p[0x110000:0x110006] = bytes.fromhex("4df9") + struct.pack(">I", pool.base)
    p[0x110010:0x110016] = bytes.fromhex("bdfc") + struct.pack(">I", pool.end)
    ps = m68k.PatchSet()
    stats = layout.relocate_pools(bytes(p), ps, [], pools=[pool], free_base=0x80BCD6)
    ps.apply(p)
    assert stats["t"]["base_sites"] == 2
    assert struct.unpack_from(">I", p, 0x110002)[0] == 0x80BCD6
    assert struct.unpack_from(">I", p, 0x110012)[0] == 0x80BCD6 + 90 * 0x2C
