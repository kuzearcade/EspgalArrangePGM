"""The default-ranking conversion (stages/code.py ranking_entries), on synthetic ELF records."""
import struct

from espgalarr.stages.code import ranking_entries


class FakeElf:
    def __init__(self, base, blob):
        self.base, self.blob = base, blob

    def read(self, vaddr, size):
        return self.blob[vaddr - self.base:vaddr - self.base + size]


def record(char, flags, name, per_stage):
    r = bytearray(0x30)
    r[5], r[6] = char, flags
    struct.pack_into("<3I", r, 8, *map(ord, name))
    struct.pack_into("<6I", r, 0x18, *per_stage)
    return bytes(r)


def test_ranking_entries():
    recs = [record(0, 0x04, "ABC", [10] * 6), record(1, 0x13, "XYZ", [1, 2, 3, 4, 5, 6]),
            record(2, 0x00, "AAA", [7, 0, 0, 0, 0, 0]), record(0, 0x05, "ZZZ", [1] * 6),
            record(1, 0x02, "MNO", [5, 5, 5, 9, 9, 9])]
    e = ranking_entries(FakeElf(0x1000, b"".join(recs)), 0x1000)
    assert e[0] == (50, [0, 4, 8], 4, 0)            # stages 1..5 summed
    assert e[1] == (10, [92, 96, 100], 6, 2)        # all clear: stage word 6
    assert e[2] == (7, [0, 0, 0], 0, 4)
    assert e[3] == (6, [100, 100, 100], 4, 0)       # stage word at most 4
    assert e[4] == (15, [48, 52, 56], 2, 2)
