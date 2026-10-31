"""Porter building blocks on synthetic input (no original code)."""
from espgalarr.detrans.porter import NewVars, Porter, fix_stack_saves
from espgalarr.detrans.ramxlate import RamXlate, regions
import collections


def test_fix_stack_saves_moves_adjustment_first():
    out = fix_stack_saves(["\tmove.l\ta6,-4(a7)", "\tmove.l\td0,(a7)", "\tlea.l\t-8(a7),a7", "\trts"])
    assert out == ["\tlea\t-8(a7),a7", "\tmove.l\ta6,4(a7)", "\tmove.l\td0,0(a7)", "\trts"]
    # the other ways the de-translator writes the adjustment (subq/suba #K, adda #-K)
    for adj in ("\tsubq.l\t#8,a7", "\tsuba.l\t#$8,a7", "\tadda.l\t#$fffffff8,a7", "\tadda.w\t#-8,a7"):
        assert fix_stack_saves(["\tmove.l\ta6,-4(a7)", "\tmove.l\td0,(a7)", adj]) == out[:3]
    # raising a7 is not an adjustment for saves
    assert fix_stack_saves(["\tmove.l\td0,-4(a7)", "\taddq.l\t#4,a7"])[0] == "\tmove.l\td0,-4(a7)"


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


def test_odd_word_access_is_refused():
    from espgalarr.detrans.porter import _odd_access
    assert _odd_access("\tmove.w\t#2,3(a0)") and _odd_access("\tcmpi.l\t#1,$81e117.l")
    assert not _odd_access("\tmove.b\t#2,3(a0)") and not _odd_access("\tmove.w\t#3,4(a0)")
    assert not _odd_access("\tlea.l\t3(a0),a1") and not _odd_access("\tbne\tL_3")


def test_call_mismatch_refuses_a_different_callee_at_the_same_position():
    from espgalarr.detrans.porter import call_mismatch
    # arcade region 0x1000..0x1020: bsr helper (0x1100), jsr 0x2000, bra 0x3000 (all outside)
    calls = [(0x1004, 0x1100), (0x1010, 0x2000), (0x101c, 0x3000), (0x1030, 0x4000)]
    unit = ("F_10:\n\trts\n"                                   # a ported callee's own code
            "F_20:\n\tjsr\tF_10\n\tjsr\t$2000.l\n\tjsr\t$3000.l\n\trts\n")
    assert call_mismatch(unit, 0x20, 0x1000, 0x1020, calls) is None
    bad = unit.replace("$2000.l", "$2004.l")                   # a near-identical copy
    assert call_mismatch(bad, 0x20, 0x1000, 0x1020, calls) == "calls $2004 where the arcade calls $2000"
    fewer = unit.replace("\tjsr\t$3000.l\n", "")               # different count: no verdict
    assert call_mismatch(fewer.replace("$2000", "$2004"), 0x20, 0x1000, 0x1020, calls) is None


def test_listing_resyncs_at_an_entry_inside_a_bogus_instruction():
    from espgalarr.pgm.disasm import listing
    b = bytes.fromhex("008000004e754e71")      # data word $0080 reads as ori.l #$4e75,d0
    plain = list(listing(b))
    assert plain[0].endswith("ori.l #$4e75, d0") and plain[1].startswith("000006")
    synced = list(listing(b, {4}))
    assert [x[:6] for x in synced] == ["000000", "000002", "000004", "000006"]
    assert synced[0].endswith(".byte 0x00, 0x80") and synced[2].endswith("rts ")
