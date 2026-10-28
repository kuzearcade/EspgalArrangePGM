import struct
from types import SimpleNamespace

from espgalarr.detrans import port


def k(addr, raw, mnem):
    return SimpleNamespace(addr=addr, nbytes=len(raw), mnem=mnem, ops="")


def test_encode_ext_word():
    p = bytearray(0x200)
    p[0x100:0x104] = bytes.fromhex("3e3c0022")        # move.w #$22,d7
    pt = port._encode_patch(bytes(p), k(0x100, p[0x100:0x104], "move.w"), "w", 0x22, 0x62)
    assert pt and pt.offset == 2 and pt.width == 2 and pt.kind == "ext"


def test_encode_lea_displacement_negative():
    p = bytearray(0x200)
    p[0x100:0x104] = bytes.fromhex("4deeffdc")        # lea -$24(a6),a6
    pt = port._encode_patch(bytes(p), k(0x100, p[0x100:0x104], "lea.l"), "l", 0xFFFFFFDC, 0xFFFFFFD4)
    assert pt and pt.offset == 2 and pt.width == 2


def test_encode_moveq_and_quick():
    p = bytearray(0x200)
    p[0x100:0x102] = bytes.fromhex("7e22")            # moveq #$22,d7
    pt = port._encode_patch(bytes(p), k(0x100, p[0x100:0x102], "moveq"), "l", 0x22, 0x62)
    assert pt and pt.kind == "moveq"
    p[0x110:0x112] = bytes.fromhex("5440")            # addq.w #2,d0
    pt = port._encode_patch(bytes(p), k(0x110, p[0x110:0x112], "addq.w"), "w", 2, 6)
    assert pt and pt.kind == "quick"
    assert port._encode_patch(bytes(p), k(0x110, p[0x110:0x112], "addq.w"), "w", 2, 9) is None
