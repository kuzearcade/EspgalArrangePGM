"""Porter building blocks on synthetic input (no original code)."""
from espgalarr.detrans.porter import NewVars, Porter, fix_stack_saves
from espgalarr.detrans.ramxlate import RamXlate, regions
import collections


def test_fix_stack_saves_moves_adjustment_first():
    out = fix_stack_saves(["\tmove.l\ta6,-4(a7)", "\tmove.l\td0,(a7)", "\tlea.l\t-8(a7),a7", "\trts"])
    assert out == ["\tlea\t-8(a7),a7", "\tmove.l\ta6,4(a7)", "\tmove.l\td0,0(a7)", "\trts"]


def test_newvars_keep_cluster_layout():
    nv = NewVars(0xE09000, 0x100)
    for a in (0x812946, 0x81294A, 0x830000):
        nv.want(a)
    nv.assign()
    assert nv.map[0x81294A] - nv.map[0x812946] == 4
    assert nv.map[0x830000] >= nv.map[0x81294A] + 2


def test_regions_and_xlate():
    votes = collections.defaultdict(collections.Counter)
    for b in range(0x801244, 0x801256, 2):
        votes[b][b + 0x5D94] += 3
    regs = regions(votes)
    assert regs[0][2] == 0x5D94
    x = RamXlate({"regions": [[hex(lo), hex(hi), d, v] for lo, hi, d, v in regs], "pairs": {}})
    assert x(0x801248) == 0x801248 + 0x5D94 and x(0x900000) is None


def test_port_translates_only_extrapolated(tmp_path):
    (tmp_path / "arrange_100.s").write_text(
        "F_100:\n"
        "\tmove.w\t$81d4ce.l,d7                    ; conf 0.60\n"
        "\tmove.w\t$813b1e.l,d3\n"
        "\tjsr\tF_200\n"
        "\tjsr\tF_300\n"
        "\trts\n")
    pt = Porter(RamXlate({"regions": [], "pairs": {}}, {0x81D4CE: 0x818352}), {0x200: 0x11A0BC},
                src_dir=tmp_path)
    r = pt.port(0x100)
    assert "$818352.l" in r.text and "$813b1e.l" in r.text and "$11a0bc.l" in r.text
    assert r.callees == {0x300}
