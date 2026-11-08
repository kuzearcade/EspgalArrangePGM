"""ISO 9660 extraction (espgalarr.ps2.iso.extract) on a synthetic image."""
import struct

from espgalarr.ps2 import iso


def _rec(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    n = 33 + len(name) + (len(name) + 1) % 2
    r = bytearray(n)
    r[0] = n
    struct.pack_into("<I", r, 2, lba); struct.pack_into(">I", r, 6, lba)
    struct.pack_into("<I", r, 10, size); struct.pack_into(">I", r, 14, size)
    r[25] = 2 if is_dir else 0
    r[32] = len(name)
    r[33:33 + len(name)] = name
    return bytes(r)


def _image() -> bytes:
    S = iso.SECTOR
    img = bytearray(S * 24)
    pvd = bytearray(S)
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    pvd[156:156 + 34] = _rec(b"\x00", 18, S, True)
    img[16 * S:17 * S] = pvd
    root = _rec(b"\x00", 18, S, True) + _rec(b"\x01", 18, S, True) + _rec(b"SUB", 19, S, True) \
        + _rec(b"A.BIN;1", 20, 5, False) + _rec(b"DUMMY.BIN;1", 21, 4, False)
    img[18 * S:18 * S + len(root)] = root
    sub = _rec(b"\x00", 19, S, True) + _rec(b"\x01", 18, S, True) + _rec(b"B.;1", 22, S + 3, False)
    img[19 * S:19 * S + len(sub)] = sub
    img[20 * S:20 * S + 5] = b"hello"
    img[21 * S:21 * S + 4] = b"pad!"
    img[22 * S:22 * S + S + 3] = bytes(range(256)) * 8 + b"end"
    return bytes(img)


def test_extract(tmp_path):
    (tmp_path / "t.iso").write_bytes(_image())
    n = iso.extract(tmp_path / "t.iso", tmp_path / "out")
    assert n == 2
    assert (tmp_path / "out" / "A.BIN").read_bytes() == b"hello"
    assert not (tmp_path / "out" / "DUMMY.BIN").exists()
    assert (tmp_path / "out" / "SUB" / "B").read_bytes() == bytes(range(256)) * 8 + b"end"
