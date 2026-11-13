"""The collision's shot loops over the live-shot table (collision.shot_table): the rewrite finds
both ported loops, keeps their hit paths, builds the table once per hitbox list and assembles.
(Exactness in play: perfcheck's PS2 reports and tools/compare/statecmp.py, spec/timing.md.)"""
import re

from espgalarr import collision
from espgalarr.pgm import asm68k


def _loop(n):
    """A ported shot loop as the porter emits it (labels L_<n>x)."""
    return (f"\tmove.w\t#-1,d5\n"
            f"\tmovea.l\ta0,a6\n\tmove.w\t$81291e.l,d6\n\tsubq.w\t#1,d6\n\tbra\tL_{n}1\n"
            f"L_{n}2:\n\tlea.l\t48(a6),a6\n\tdbra\td6,L_{n}1\n\tbra\tL_{n}9\n"
            f"L_{n}3:\n\tlea.l\t50(a6),a6\n"
            f"L_{n}1:\n\ttst.w\t(a6)+\n\tbpl\tL_{n}3\n\ttst.w\t$e0aff0.l\n\tbeq\tL_{n}4\n"
            f"\tbclr\t#1,-1(a6)\n"
            f"L_{n}4:\n\tmove.w\t(a6)+,d4\n\tadd.w\td7,d4\n\tadd.w\t12(a6),d4\n\tcmp.w\td1,d4\n"
            f"\tbcs\tL_{n}2\n\tsub.w\t14(a6),d4\n\tcmp.w\td4,d0\n\tbcs\tL_{n}2\n"
            f"\tmove.w\t(a6),d4\n\tadd.w\td7,d4\n\tadd.w\t16(a6),d4\n\tcmp.w\td3,d4\n\tbcs\tL_{n}2\n"
            f"\tsub.w\t18(a6),d4\n\tsub.w\t18(a6),d4\n\tcmp.w\td4,d2\n\tbcs\tL_{n}2\n"
            f"\ttst.w\t20(a6)\n\tbmi\tL_{n}8\n\ttst.w\t$e0aff0.l\n\tbne\tL_{n}5\n"
            f"\tbtst\t#5,-3(a6)\n\tbeq\tL_{n}5\n\tbtst\t#1,-3(a6)\n\tbne\tL_{n}8\n"
            f"L_{n}5:\n\tori.w\t#$2082,-4(a6)\n\taddq.w\t#1,34(a5)\n"
            f"L_{n}8:\n\tlea.l\t48(a6),a6\n\tdbra\td6,L_{n}1\n"
            f"L_{n}9:\n\trts\n")


def test_shot_table_rewrite():
    src = _loop(1) + _loop(2)
    t = collision.shot_table(src)
    # the old loops are gone (one group-word test per hitbox left), the hit paths are kept once
    # each and return into all four copies
    left = "$%x.l" % collision.layout_left()
    assert t.count("tst.w\t$e0aff0.l") == 2 * 4
    assert t.count("ori.w\t#$2082,-4(a6)") == 2
    assert t.count(f"\tmove.w\t#{collision.TABLE_AFTER + 1},{left}\n\tmove.w\t#-1,d5\n") == 2
    for k in (1, 2):
        body = t[t.index(f"cst{k}_wge:"):t.index(f"L_{k}9:")]
        assert body.count("bclr\t#1,-1(a6)") == 1 and body.count("bclr\t#1,-3(a6)") == 1
        assert body.count("\tjsr\tcoll_table\n") == 1
        for cp in ("g", "s", "wg", "ws"):
            assert f"\tbra\tcst{k}_{cp}m\n" in body or f"\tbne\tcst{k}_{cp}m\n" in body
        # the four bound tests in each copy: the loop's d4 tests mirrored onto table words
        for cp in "gs":
            seg = body[body.index(f"cst{k}_{cp}:"):body.index(f"cst{k}_{cp}m:")]
            assert re.findall(r"cmp\.w\t(\d+)\(a1\),(d\d)\n\t(b\w+)", seg) == [
                ("4", "d1", "bhi"), ("6", "d0", "bcs"), ("8", "d3", "bhi"), ("10", "d2", "bcs")]
    code, syms = asm68k.assemble(t, 0x200000)
    assert "coll_table" in syms and len(code) > 0


def test_table_fits():
    from espgalarr.stages import layout
    assert layout.COLLISION_TABLE_SIZE == 2 * layout.ARRANGE_SHOTS * 12
    assert layout.SHOT_LIST + 0x40 <= layout.COLLISION_TABLE
    assert layout.COLLISION_TABLE + layout.COLLISION_TABLE_SIZE <= layout.ITEM_POOL
