"""De-translator tests on synthetic MIPS code (no game bytes).

The MIPS snippets are hand-assembled with the helpers below following the conventions the
translator uses (register slots at 0x4810a0.., CCR at $gp-0x7ee4)."""
import re
import struct

import pytest

from espgalarr.detrans import canon, core, expr as X, mips, symexec, templates as T

GP = 28
AT = 1
R = {n: i for i, n in enumerate(mips.REGS)}


def lui(rt, imm):
    return (15 << 26) | (R[rt] << 16) | (imm & 0xFFFF)


def itype(op, rt, rs, imm):
    return (op << 26) | (R[rs] << 21) | (R[rt] << 16) | (imm & 0xFFFF)


def addiu(rt, rs, imm):
    return itype(9, rt, rs, imm)


def ori(rt, rs, imm):
    return itype(13, rt, rs, imm)


def andi(rt, rs, imm):
    return itype(12, rt, rs, imm)


def lhu(rt, off, rs):
    return itype(37, rt, rs, off)


def lbu(rt, off, rs):
    return itype(36, rt, rs, off)


def lw(rt, off, rs):
    return itype(35, rt, rs, off)


def sh(rt, off, rs):
    return itype(41, rt, rs, off)


def sw(rt, off, rs):
    return itype(43, rt, rs, off)


def sb(rt, off, rs):
    return itype(40, rt, rs, off)


def rtype(funct, rd, rs, rt, sa=0):
    return (R[rs] << 21) | (R[rt] << 16) | (R[rd] << 11) | (sa << 6) | funct


def and_(rd, rs, rt):
    return rtype(36, rd, rs, rt)


def or_(rd, rs, rt):
    return rtype(37, rd, rs, rt)


def addu(rd, rs, rt):
    return rtype(33, rd, rs, rt)


def sll(rd, rt, sa):
    return rtype(0, rd, "zero", rt, sa)


def srl(rd, rt, sa):
    return rtype(2, rd, "zero", rt, sa)


def beqz(rs, off):
    return itype(4, "zero", rs, off)


def bnez(rs, off):
    return itype(5, "zero", rs, off)


def b(off):
    return itype(4, "zero", "zero", off)


JR_RA = rtype(8, "zero", "ra", "zero")
NOP = 0
CCR = -0x7EE4
SLOT = symexec.OFF_OF


def assemble(words):
    return struct.pack(f"<{len(words)}I", *words)


def ccr_clear(mask):
    """lhu a0,CCR ; addiu v1,zero,~mask ; and v1,a0,v1 ; sh v1,CCR"""
    return [lhu("a0", CCR, "gp"), addiu("v1", "zero", ~mask), and_("v1", "a0", "v1"), sh("v1", CCR, "gp")]


def ccr_set(mask):
    return [lhu("v1", CCR, "gp"), ori("v1", "v1", mask), sh("v1", CCR, "gp")]


def nz_flags(reg):
    """Z/N diamonds on the 16-bit value in `reg` (as the translator emits them)."""
    return ([bnez(reg, 5)] + [NOP] + ccr_set(0x400) + [b(4), NOP] + ccr_clear(0x400)
            + [andi("a1", reg, 0x8000), beqz("a1", 5), NOP] + ccr_set(0x800) + [b(4), NOP] + ccr_clear(0x800))


def detrans_text(words):
    r = core.detranslate(assemble(words), 0x800000)
    return [i.text().strip() for i in r.insns if not i.is_label]


def test_decoder_roundtrip():
    i = mips.decode(lhu("a0", CCR, "gp"), 0x1000)
    assert i.op == "lhu" and i.rt == R["a0"] and i.rs == GP and i.imm == CCR
    j = mips.decode(bnez("a1", 6), 0x1000)
    assert j.op == "bne" and j.target == 0x1000 + 4 + 6 * 4


def test_andi_word_with_flags():
    # andi.w #$80,d2 : lhu slot; andi; sh slot; clear V,C; Z/N diamonds
    w = [lui("at", 0x48), lhu("v1", SLOT["d2"], "at"), andi("v1", "v1", 0x80), lui("at", 0x48),
         sh("v1", SLOT["d2"], "at"), andi("a1", "v1", 0xFFFF)] + ccr_clear(0x200) + ccr_clear(0x100) \
        + nz_flags("a1") + [JR_RA, NOP]
    out = detrans_text(w)
    assert out[0].startswith("andi.w\t#$80,d2")
    assert "conf" not in out[0]
    assert out[-1] == "rts"


def test_move_word_memory_to_register_and_lea():
    # lea $xxxx,a0 ; move.w (a0),d0
    w = [lui("v1", 0x61), addiu("v1", "v1", -0x14B0), lui("at", 0x48), sw("v1", SLOT["a0"], "at"),
         lui("at", 0x48), lw("a0", SLOT["a0"], "at"), lbu("a1", 0, "a0"), lbu("a0", 1, "a0"),
         sll("a1", "a1", 8), or_("a0", "a1", "a0"), lui("at", 0x48), sh("a0", SLOT["d0"], "at"),
         andi("a0", "a0", 0xFFFF)] + [lhu("v1", CCR, "gp"), andi("v1", "v1", 0xF0FF), sh("v1", CCR, "gp")] \
        + nz_flags("a0") + [JR_RA, NOP]
    out = detrans_text(w)
    assert out[0].startswith("lea.l\t$60eb50.l,a0")
    assert out[1] == "move.w\t(a0),d0"


def test_branch_on_flags_and_jsr():
    # tst-like: lhu CCR; andi 0x400; bnez -> beq L ; jal ; jr
    w = [lhu("v1", CCR, "gp"), andi("v1", "v1", 0x400), bnez("v1", 3), NOP,
         (3 << 26) | ((0x900000) >> 2), NOP, JR_RA, NOP, JR_RA, NOP]
    out = detrans_text(w)
    assert out[0].startswith("beq\tL_")
    assert out[1].startswith("jsr\tF_900000")


def test_canon_equivalences():
    c1 = canon.canon("addq.w", "#$1, $80106a.l")
    c2 = canon.canon("addi.w", "#1,$80106a.l")
    assert c1.text == c2.text == "add.w #$1,$80106a"
    assert canon.canon("moveq", "#$ff, d0").text == canon.canon("move.l", "#-1,d0").text
    assert canon.canon("tst.w", "d0").text == canon.canon("cmpi.w", "#0,d0").text
    assert canon.canon("subq.l", "#$4, a7").text == canon.canon("lea.l", "-4(a7),a7").text
    assert canon.canon("bne.b", "$113be4").key == "bne L"
    assert canon.canon("lea.l", "$13f08e(pc), a0").text == canon.canon("lea.l", "$13f08e.l,a0").text


def test_expr_simplifier():
    m = X.OR(X.SHL(("mem", X.C(0x100), 1, False), 8), ("mem", X.C(0x101), 1, False))
    assert m == ("mem", X.C(0x100), 2, False)
    assert X.SAR(X.SHL(("r68", "d0", 0), 16), 16) == ("sext16", ("r68", "d0", 0))
    ccr = ("ccr", X.entry_snap())
    cleared = X.AND(ccr, X.C(0xF0FF))
    assert dict(cleared[1])["Z"] == X.FALSE and dict(cleared[1])["X"] == ("flag", "X")
    assert X.truth(X.AND(ccr, X.C(0x400))) == ("flag", "Z")


def test_cond_codes():
    assert T.cond_code(("flag", "Z")) == "eq"
    assert T.cond_code(X.bnot(("flag", "C"))) == "cc"
    assert T.cond_code(X.bor(("flag", "C"), ("flag", "Z"))) == "ls"
    assert T.cond_code(("ne", ("flagtest", X.entry_snap(), 0x800), ("flagtest", X.entry_snap(), 0x200))) == "lt"


def test_movem_peephole():
    regs = ["d0", "d1", "a0"]
    ins = [T.M68("move", "l", [r, f"{-4 * (len(regs) - k)}(a7)"]) for k, r in enumerate(regs)]
    ins.append(T.M68("lea", "", ["-12(a7)", "a7"]))
    core._peepholes(ins)
    assert [i.text().strip() for i in ins] == ["movem.l\td0-d1/a0,-(a7)"]


def test_branch_on_value_gets_a_test():
    # lw v1,$60eb08 ; beqz v1 -> the translator dropped the dead `tst.l` flag update
    w = [lui("at", 0x61), lw("v1", -0x14F8, "at"), beqz("v1", 3), NOP,
         JR_RA, NOP, JR_RA, NOP]
    out = detrans_text(w)
    assert out[0].startswith("tst.l\t$60eb08.l")
    assert out[1].startswith("beq\tL_")


def test_code_after_jump_is_another_entry():
    # `b` over nothing any branch reaches: the rest is a second entry, not raw MIPS
    w = [b(3), NOP, JR_RA, NOP, lui("at", 0x48), lhu("v1", SLOT["d1"], "at"),
         lui("at", 0x48), sh("v1", SLOT["d0"], "at"), JR_RA, NOP]
    assert not any("???" in t for t in detrans_text(w))


def test_compiled_ext_is_sign_extension():
    d0 = ("r68", "d0", 1)
    lo = X.AND(d0, X.C(0xFFFF))
    assert X.ITE(("bit", d0, 15), X.OR(lo, X.C(0xFFFF0000)), lo) == X.SEXT16(lo)
    lo8 = X.AND(d0, X.C(0xFF))
    assert X.ITE(("bit", d0, 7), X.OR(lo8, X.C(0xFF00)), lo8) == X.SEXT8(lo8)


def test_tas_peephole():
    ins = [T.M68("tst", "b", ["$642308.l"]), T.M68("ori", "b", ["#-$80", "$642308.l"])]
    core._peepholes(ins)
    assert [i.text().strip() for i in ins] == ["tas\t$642308.l"]


def test_reads_arg_regs():
    code = {0x900000: sb("a0", SLOT["d1"], "at"), 0x900004: JR_RA,
            0x900008: sb("a2", -0x7D80, "gp")}       # read in the jr delay slot
    assert symexec.reads_arg_regs(0x900000, code.get) == {4, 6}
    # an argument register written first is not an argument
    code2 = {0x900000: addiu("a0", "zero", 1), 0x900004: sb("a0", 0, "at"), 0x900008: JR_RA, 0x90000c: NOP}
    assert symexec.reads_arg_regs(0x900000, code2.get) == set()


def test_overlay_text_end_counts_from_the_image():
    from espgalarr.ps2.overlay import OverlayHeader, HEADER_SIZE
    h = OverlayHeader(1, 0x649800, 0x1000, 0x100, 0, 0x649800 + HEADER_SIZE + 0x1100, "x")
    assert h.text_end == 0x649800 + HEADER_SIZE + 0x1000 and h.data_end == h.end_addr


def test_r5900_three_operand_mult():
    # lh t1,d2 ; lh t0,d6 ; mult t1,t1,t0 ; sw t1,d2  ==  muls.w d6,d2
    lh = lambda rt, off, rs: itype(33, rt, rs, off)          # noqa: E731
    mult = rtype(24, "t1", "t1", "t0")
    w = [lui("at", 0x48), lh("t1", SLOT["d2"], "at"), lui("at", 0x48), lh("t0", SLOT["d6"], "at"),
         mult, lui("at", 0x48), sw("t1", SLOT["d2"], "at"), JR_RA, NOP]
    out = detrans_text(w)
    assert out[0].startswith("muls.w\td6,d2")


def test_helper_inlining():
    # a native-convention helper: returns 1 (after calling F_900100) when $a0 != 0, else 0
    from espgalarr.detrans import helpers
    jal = lambda t: (3 << 26) | (t >> 2)                      # noqa: E731
    code = assemble([bnez("a0", 3), NOP, JR_RA, addiu("v0", "zero", 0),
                     jal(0x900100), NOP, JR_RA, addiu("v0", "zero", 1)])
    words = {0x900000 + 4 * i: struct.unpack_from("<I", code, 4 * i)[0] for i in range(len(code) // 4)}
    saved = dict(helpers.HELPERS)
    helpers.HELPERS[0x900000] = (len(code), "test helper: x", "A0A9")
    try:
        text = ("F_800000:\n\tjsr\tF_900000                           ; mips-args a0=$1\n"
                "\ttst.l\tRET_V0\n\tbne\tL_800040\n\tmoveq\t#1,d0\nL_800040:\n\trts\n")
        out = helpers.inline(text, lambda a, n: code[a - 0x900000:a - 0x900000 + n], words.get, T.AddrMap())
        assert "F_900000" not in out and "RET_V0" not in out
        assert "\tjmp\tF_900100" in out                         # removed: return like the arcade
        out0 = helpers.inline(text.replace("a0=$1", "a0=$0"), lambda a, n: code[a - 0x900000:a - 0x900000 + n],
                              words.get, T.AddrMap())
        body0 = [l for l in out0.splitlines() if l.startswith("\t") and not l.startswith("\t;")]
        assert body0[0].startswith("\tbra\tL_") and body0[0].endswith("fffffe")   # not removed: continue
        assert "\tmoveq\t#1,d0" in out0
    finally:
        helpers.HELPERS.clear()
        helpers.HELPERS.update(saved)


def test_translator_entries():
    # a function entered at +0x30 by a code pointer: `j +0x30` over the nop padding before it;
    # a 16-aligned branch target without the padding jump is not an entry
    from espgalarr.detrans.porter import _translator_entries
    j = lambda t: (2 << 26) | ((t >> 2) & 0x3FFFFFF)          # noqa: E731
    base = 0x649800
    w = [lui("at", 0x48), addiu("v0", "zero", 1), j(base + 0x30), sb("v0", 3, "at")] + [NOP] * 8 \
        + [lui("at", 0x48), sb("v0", 2, "at"), JR_RA, NOP]
    img = struct.pack("<%dI" % len(w), *w)

    class Ov:
        def read(self, a, n):
            return img[a - base:a - base + n]
    assert _translator_entries(Ov(), base, len(img)) == [base + 0x30]
    w2 = w[:2] + [NOP, NOP] + w[4:]
    img2 = struct.pack("<%dI" % len(w2), *w2)
    Ov.read = lambda self, a, n: img2[a - base:a - base + n]
    assert _translator_entries(Ov(), base, len(img2)) == []


def test_byte_stores_merge_only_at_even_addresses():
    # the translator's big-endian word store (high byte = value >> 8): at offset 3 it must stay
    # two byte stores (a word store at an odd address is a 68000 address error), at 2 it merges
    def word_store(off):
        return [lui("at", 0x48), lw("v1", SLOT["a0"], "at"), addiu("v0", "zero", 0x1234), srl("a1", "v0", 8),
                sb("a1", off, "v1"), sb("v0", off + 1, "v1"), JR_RA, NOP]
    out = detrans_text(word_store(3))
    assert not any(re.match(r"^move\.[wl]\t", x) for x in out), out
    assert detrans_text(word_store(2))[0] == "move.w\t#$1234,2(a0)"


def test_branch_on_a_memory_bit_is_btst():
    from espgalarr.detrans import templates
    m, cc = templates.direct_test(("bnot", ("bit", ("mem", X.ADD(("r68", "a5"), X.C(24)), 1), 6)),
                                  templates.AddrMap(), 0)
    assert (m.text().strip(), cc) == ("btst\t#6,24(a5)", "eq")


def test_twin_gap_rules():
    from espgalarr.detrans import twin_gaps
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.templates import M68
    from espgalarr.detrans.verify import K68
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    t = lambda m, s, ops: M68(m, s, ops, 1.0, 0)                          # noqa: E731

    class Res:
        insns = []
    nxt, prv = k(0x10, "move.w", "d0,d1"), k(0, "move.w", "d1,d0")
    rule = lambda tw, k68: twin_gaps.rule(tw, k68, nxt, prv, t("move", "w", ["d0", "d1"]),  # noqa: E731
                                          Res, None, {}, frozenset())
    assert rule([t("tst", "l", ["d0"])], []) == "flag-only"
    assert rule([t("move", "b", ["#$50", "2(a0)"]), t("clr", "b", ["3(a0)"])],
                [k(4, "move.w", "#$5000,2(a0)")]) == "split-store"
    assert rule([t("move", "b", ["#$50", "2(a0)"]), t("clr", "b", ["3(a0)"])],
                [k(4, "move.w", "#$5001,2(a0)")]) is None
    assert rule([t("move", "l", ["d1", "-4(a7)"]), t("move", "l", ["d0", "(a7)"]), t("adda", "l", ["#-8", "a7"])],
                [k(4, "movem.w", "d0-d1,-(a7)")]) == "stack-save"
    assert rule([t("move", "w", ["d0", "4(a0)"])], []) is None         # real behaviour is never a gap


def test_twin_gap_effects_branches_and_fragments():
    from types import SimpleNamespace
    from espgalarr.detrans import twin_gaps
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.templates import M68
    from espgalarr.detrans.verify import K68, Index
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    t = lambda m, s, ops: M68(m, s, ops, 1.0, 0)                          # noqa: E731

    def lab(name):
        x = M68("", "", [], 1.0, 0, is_label=True)
        x.label_text = name
        return x
    nxt, prv = k(0x10, "move.w", "d0,d1"), k(0, "move.w", "d1,d0")
    res = SimpleNamespace(insns=[])
    ctx = twin_gaps.Ctx(SimpleNamespace(pairs=[]), res, None, {})
    rule = lambda tw, k68, n=nxt, p=prv, c=ctx, idx=None: twin_gaps.rule(  # noqa: E731
        tw, k68, n, p, t("move", "w", ["d0", "d1"]), res, idx, {}, frozenset(), c)
    # the same effect: `-(a0)` as a subtraction, `lea` hoisted over a store through another base
    assert rule([t("subq", "l", ["#4", "a0"]), t("move", "l", ["-(a1)", "(a0)"])],
                [k(4, "move.l", "-(a1),-(a0)")]) == "same-effect"
    assert twin_gaps.same_effect([t("lea", "l", ["30(a5)", "a0"]), t("add", "l", ["d0", "(a0)"]), t("moveq", "", ["#0", "d1"])],
                                 [k(4, "add.l", "d0,30(a5)"), k(8, "moveq", "#0,d1"), k(10, "lea", "30(a5),a0")],
                                 nxt, {}, ctx)
    assert not twin_gaps.same_effect([t("lea", "l", ["30(a5)", "a0"]), t("add", "l", ["d0", "(a0)"])],
                                     [k(4, "add.l", "d0,34(a5)"), k(10, "lea", "30(a5),a0")], nxt, {}, ctx)
    # asl/lsl differ in V only
    assert rule([t("lsl", "w", ["#2", "d0"])], [k(4, "asl.w", "#2,d0")]) == "same-effect"
    assert rule([t("lsl", "w", ["#2", "d0"])], [k(4, "asl.w", "#2,d0")], n=k(0x10, "bvs", "$40")) is None
    # without the context only the plain rules apply
    assert rule([t("subq", "l", ["#4", "a0"]), t("move", "l", ["-(a1)", "(a0)"])],
                [k(4, "move.l", "-(a1),-(a0)")], c=None) is None

    # `bhi L` as `bls M ; bra X` (M: the next instruction, X: the instruction aligned with L)
    tn, x = t("move", "w", ["d0", "d1"]), t("rts", "", [])
    b1, b2 = t("bls", "", ["L_m"]), t("bra", "", ["L_x"])
    res2 = SimpleNamespace(insns=[b1, b2, lab("L_m"), tn, lab("L_x"), x])
    target = k(0x40, "rts", "")
    ctx2 = twin_gaps.Ctx(SimpleNamespace(pairs=[(x, target)]), res2, None, {})
    br = lambda tw, k68: twin_gaps.rule(tw, k68, nxt, prv, tn, res2, None, {}, frozenset(), ctx2)  # noqa: E731
    assert br([b1, b2], [k(4, "bhi", "$40")]) == "branch-tree"
    assert br([b1, b2], [k(4, "bcs", "$40")]) is None

    # the decrement does not borrow right after `move.w <ea>,d7 ; beq`
    ks = [k(0, "move.w", "$818338,d7"), k(6, "beq", "$0"), k(8, "subq.w", "#1,d7"), k(10, "lea", "$817780,a6")]
    idx = Index(ks)
    assert rule([t("subq", "l", ["#1", "d7"])], [ks[2]], n=ks[3], p=ks[1], idx=idx) == "no-borrow"
    assert rule([t("subq", "l", ["#1", "d7"])], [ks[2]], n=ks[3], p=k(6, "bne", "$0"), idx=idx) is None

    # an outlined fragment: `jsr F` <-> the 68k run F de-translates to, its rts left out
    frag = [t("move", "w", ["30(a6)", "d0"]), t("add", "w", ["d0", "d0"]), t("rts", "", [])]
    bodies = {0xa85d30: (frag, {}), 0xa85d40: ([t("move", "w", ["4(a7)", "d0"]), t("rts", "", [])], {})}
    ctx3 = twin_gaps.Ctx(SimpleNamespace(pairs=[]), res, None, {}, body=bodies.get)
    run = [k(4, "move.w", "30(a6),d0"), k(8, "add.w", "d0,d0")]
    assert rule([t("jsr", "", ["F_a85d30"])], run, c=ctx3) == "outlined"
    assert rule([t("jsr", "", ["F_a85d30"])], run[:1], c=ctx3) is None
    assert rule([t("jsr", "", ["F_a85d40"])], [k(4, "move.w", "4(a7),d0")], c=ctx3) is None   # sees the call's push

    # a graphics address <-> its PS2 sprite number (as the learned table has it)
    ctx4 = twin_gaps.Ctx(SimpleNamespace(pairs=[]), res, None, {}, ids={0x20e6: 0x1d9068})
    assert rule([t("move", "l", ["#$20e6", "d2"])], [k(4, "move.l", "#$1d9068,d2")], c=ctx4) == "sprite-number"
    assert rule([t("move", "l", ["#$20e7", "d2"])], [k(4, "move.l", "#$1d9068,d2")], c=ctx4) is None


def test_sprite_numbers_become_graphics_addresses():
    from espgalarr.detrans import sprite_ids
    lines = ["\tmove.l\t#$20e6,d2", "\tmove.l\t2(a6),d1", "\tjsr\t$114af0.l",
             "\tmove.l\t#$20e7,d2", "\tjmp\t$114af0.l",
             "\tmove.l\t#$1000,d2", "\tjsr\t$123456.l"]
    probs = []
    out = sprite_ids.replace(lines, {0x20e6: 0x1d9068}, {0x114af0}, probs)
    assert out[0] == "\tmove.l\t#$1d9068,d2\t; sprite $20e6"
    assert out[3] == lines[3] and probs == ["PS2 sprite number $20e7 with no known PGM graphics address"]
    assert out[5] == lines[5]                       # not a sprite routine: a plain number


def test_tail_call_past_a_dead_address_load():
    from espgalarr.detrans import twin_gaps
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.verify import K68, Index
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    ks = [k(0x10, "lea", "$1593de(pc),a0"), k(0x14, "nop", ""), k(0x16, "andi.w", "#$3e,d1"),
          k(0x1a, "lea", "$1595ee(pc),a0"), k(0x1e, "move.l", "(a0,d1.w),$2c(a5)")]
    idx = Index(ks)
    assert twin_gaps._skips_to(idx, 0x10, 0x16)
    ks[2] = k(0x16, "move.l", "(a0),d1")                                  # a0 read: the load is live
    assert not twin_gaps._skips_to(Index(ks), 0x10, 0x16)


def test_sprite_number_reaches_its_call_past_a_branch():
    from espgalarr.detrans import sprite_ids
    lines = ["\tmove.l\t#$21c9,d2", "\tmove.w\t#$628,d3", "\ttst.w\t428(a6)", "\tbeq\tL_6b2460",
             "\tjsr\t$114a84.l", "L_6b2460:"]
    probs = []
    assert sprite_ids.replace(lines, {0x21c9: 0x1ef76c}, {0x114a84}, probs)[0].startswith("\tmove.l\t#$1ef76c,d2\t;")
    lines[1] = "\tmove.l\t(a0),d2"                         # d2 written again: not a sprite number
    assert sprite_ids.replace(lines, {0x21c9: 0x1ef76c}, {0x114a84}, probs)[0] == lines[0]


def test_outlined_helper_with_constant_arguments_is_inlined():
    # a translated helper storing $a0 into d1's slot: `jsr F ; mips-args a0=$12` -> move.w #$12,d1
    from espgalarr.detrans import helpers
    code = assemble([lui("at", 0x48), sh("a0", SLOT["d1"], "at"), JR_RA, NOP])
    words = {0x900000 + 4 * i: struct.unpack_from("<I", code, 4 * i)[0] for i in range(len(code) // 4)}
    rd = lambda a, n: code[a - 0x900000:a - 0x900000 + n]                # noqa: E731
    outlined = lambda a, m: (len(code), m) if a == 0x900000 else None    # noqa: E731
    text = "F_800000:\n\tjsr\tF_900000                           ; mips-args a0=$12\n\tmove.w\td1,d0\n\trts\n"
    out = helpers.inline(text, rd, words.get, T.AddrMap(), outlined)
    assert "F_900000" not in out and "\tmove.w\t#$12,d1" in out and out.rstrip().endswith("rts")
    # an argument that is not a constant: the call stays (the porter refuses it)
    reg = text.replace("a0=$12", "a0=d3")
    assert helpers.inline(reg, rd, words.get, T.AddrMap(), outlined) == reg
    assert helpers.inline(text, rd, words.get, T.AddrMap()) == text     # only the known helpers by default


def test_region_exit_returns_of_the_routine_only():
    # the region falls through at $100: the routine's own return continues there, a ported
    # callee's return goes back to its caller; a call of the ported continuation may return
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.porter import _region_exit
    from espgalarr.detrans.verify import K68, Index
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    idx = Index([k(0x100, "move.w", "d0,d1"), k(0x102, "rts", "")])
    text = "F_10:\n\tjsr\tF_20\n\trts\n\nF_20:\n\tmoveq\t#0,d0\n\trts\n"
    out = _region_exit(text, 0x100, idx, False, root=0x10)
    assert out.splitlines()[2].startswith("\tjmp\t$100.l") and out.splitlines()[6] == "\trts"
    out = _region_exit(text, 0x100, idx, False, root=0x10, continues=lambda f, a: f == 0x20 and 0x100 in a)
    assert out == text
    assert _region_exit(text, 0x100, idx, True, root=0x10) is None


def test_twin_tail_calls_and_calls():
    from types import SimpleNamespace
    from espgalarr.detrans import twin_gaps
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.templates import M68
    from espgalarr.detrans.verify import K68, Index
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    t = lambda m, s, ops: M68(m, s, ops, 1.0, 0)                          # noqa: E731
    res = SimpleNamespace(insns=[])
    ks = [k(0x30, "move.w", "d0,d1"), k(0x40, "move.w", "d1,d2"), k(0x42, "rts", "")]
    idx = Index(ks)
    bodies = {0xa10: ([t("move", "w", ["d1", "d2"]), t("rts", "", [])], {})}
    ctx = twin_gaps.Ctx(SimpleNamespace(pairs=[]), res, idx, {}, body=bodies.get)
    rule = lambda tw, k68, n: twin_gaps.rule(tw, k68, n, ks[0], t("rts", "", []), res, idx, {}, frozenset(), ctx)  # noqa: E731
    # the twin calls the translation of the code the 68k runs into, then returns
    assert rule([t("jsr", "", ["F_a10"]), t("rts", "", [])], [], ks[1]) == "fallthrough-tail"
    assert rule([t("jsr", "", ["F_a10"]), t("rts", "", [])], [], ks[2]) is None
    # calls: the same routine, or one the map does not resolve; not two different routines
    assert rule([t("jsr", "", ["$1000.l"])], [k(4, "jsr", "$1000")], ks[1]) == "call"
    assert rule([t("jsr", "", ["F_a20"])], [k(4, "jsr", "$1000")], ks[1]) == "call"
    assert rule([t("jsr", "", ["$1000.l"])], [k(4, "jsr", "$2000")], ks[1]) is None


def test_anchor_region_without_a_same_shaped_copy():
    # a copy of the region elsewhere (other addresses/constants, same shapes) makes it ambiguous
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.porter import _shape_siblings
    from espgalarr.detrans.verify import K68, Index
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    run = lambda a, v: [k(a, "move.w", f"#${v:x},d0"), k(a + 4, "add.w", "d0,d0"),       # noqa: E731
                        k(a + 6, "lea", f"${v:x}00,a0"), k(a + 12, "movea.l", "0(a0,d0.w),a0"),
                        k(a + 16, "jsr", "(a0)"), k(a + 18, "rts", "")]
    other = [k(0x80, "moveq", "#1,d1"), k(0x82, "rts", "")]
    idx = Index(run(0x10, 4) + other + run(0x100, 8))
    assert _shape_siblings(idx, 0, 6) == 1
    idx = Index(run(0x10, 4) + other)
    assert _shape_siblings(idx, 0, 6) == 0


def test_tables_found_by_their_contents():
    # a PS2 pointer table (MIPS functions, little-endian) with two entries more than the 68k's
    from espgalarr.detrans import tables
    rom = bytearray(0x200)
    struct.pack_into(">6I", rom, 0x100, 0x1149bc, 0x114a18, 0x114a4e, 0x114a84, 0, 0x40008)
    funcs = {0x948f80: 0x1149bc, 0x9495d0: 0x114a18, 0x949920: 0x114a4e, 0x949c70: 0x114a84}
    ps2 = struct.pack("<8I", 0x948f80, 0, 0x9495d0, 0x949920, 0x949c70, 0, 0x08000400, 0x12345678)
    rd = lambda a: struct.unpack_from("<I", ps2, a - 0x800) if 0x800 <= a < 0x800 + len(ps2) else None  # noqa: E731
    rd1 = lambda a: rd(a)[0] if rd(a) else None                         # noqa: E731
    assert tables.find(0x808, rd1, funcs, bytes(rom)) == 0x104        # after the added entry
    assert tables.find(0x800, rd1, funcs, bytes(rom)) is None          # too little evidence there
    # sprite numbers where the 68k has graphics addresses, increasing with the numbers
    struct.pack_into(">5I", rom, 0x40, 0x2e0000, 0x2e0100, 0x2e0200, 0x42800900, 0x0042800f)
    ps2 = struct.pack(">5I", 0x3d04, 0x3d05, 0x3d06, 0x42800900, 0x0042800f)
    rd2 = lambda a: struct.unpack_from("<I", ps2, a - 0x800)[0] if 0x800 <= a < 0x800 + len(ps2) else None  # noqa: E731
    assert tables.find(0x800, rd2, {}, bytes(rom), {0x3000: 0x200000}) == 0x40
    assert tables.find(0x800, rd2, {}, bytes(rom), {0x3d05: 0x2e0200}) is None   # contradicts the table


def test_sprite_number_reaches_a_call_through_an_address_register():
    from espgalarr.detrans import sprite_ids
    lines = ["\tmove.l\t#$18f8,d2", "\tlea.l\t$114960,a0", "\tcmpi.w\t#3,$81399e.l", "\tbne\tL_adc9a0",
             "\tlea.l\t$11498e,a0", "L_adc9a0:", "\tjsr\t(a0)"]
    probs = []
    assert sprite_ids.replace(lines, {0x18f8: 0x2170fc}, {0x114960, 0x11498e}, probs)[0].startswith("\tmove.l\t#$2170fc,d2\t;")
    out = sprite_ids.replace(lines, {}, {0x114960, 0x11498e}, probs)
    assert out[0] == lines[0] and probs == ["PS2 sprite number $18f8 with no known PGM graphics address"]


def test_sprite_number_reaches_a_ported_dispatch_wrapper():
    # A0A9 0x9B4BD0: the arcade's inline `lea $16bfe4,a0 / jsr (a0)` dispatch as a PS2 function;
    # the stage 2 train's parts call it with sprite numbers in d2
    from espgalarr.detrans import sprite_ids
    wrapper = ("F_9b4bd0:\n\tmoveq\t#0,d0\n\tmove.b\t31(a6),d0\n\tadd.w\td0,d0\n\tadd.w\td0,d0\n"
               "\tlea.l\t$16bfe4.l,a0                     ; conf 0.60\n\tmovea.l\t(a0,d0.w),a0\n\tjsr\t(a0)\n\trts\n")
    assert sprite_ids.dispatch_table(wrapper) == 0x16bfe4
    assert sprite_ids.dispatch_table(wrapper.replace("movea.l\t(a0,d0.w),a0", "move.w\t(a0,d0.w),d1")) is None
    rom = bytearray(0x200)
    for i, a in enumerate((0x114a18, 0x114a4e, 0x114a84)):
        rom[0x100 + 4 * i:0x104 + 4 * i] = a.to_bytes(4, "big")
    assert sprite_ids.table_targets(bytes(rom), 0x100) == [0x114a18, 0x114a4e, 0x114a84]
    lines = ["\tmove.l\t#$17cc,d2", "\tmove.w\t#$c18,d3", "\tjsr\tF_9b4bd0"]
    probs = []
    out = sprite_ids.replace(lines, {0x17cc: 0x3b2e40}, {0x114a84}, probs, wrapper=lambda f: f == 0x9b4bd0)
    assert out[0].startswith("\tmove.l\t#$3b2e40,d2\t;") and probs == []
    assert sprite_ids.replace(lines, {0x17cc: 0x3b2e40}, {0x114a84}, probs)[0] == lines[0]
    lines[2] = "\tjsr\tF_9b5520"                          # not a dispatch wrapper: a plain number
    assert sprite_ids.replace(lines, {0x17cc: 0x3b2e40}, {0x114a84}, probs,
                              wrapper=lambda f: f == 0x9b4bd0)[0] == lines[0]


def test_converted_sprite_loads_are_labelled_for_the_gfx_stage():
    from espgalarr.pgm import asm68k
    from espgalarr.stages.code import _mark_sprites
    text = _mark_sprites("F_1:\n\tmove.l\t#$1d9068,d2\t; sprite $20e6\n\tjsr\t$114af0.l\n\trts\n")
    code, syms = asm68k.assemble(text, 0x200000)
    assert syms["S_spr0"] == 0x200000 and code[2:6] == struct.pack(">I", 0x1D9068)


def test_arrange_table_copied_with_the_arcade_layout():
    # arcade PS2 copy = the 68k table (halfwords verbatim, a sprite number, a pointer); the
    # Arrange copy changes one value: the port emits Arrange's own table in 68k form
    from espgalarr.detrans import tables
    rom = bytearray(0x100)
    struct.pack_into(">HHII", rom, 0x40, 0x0108, 0xfffc, 0x2e0000, 0x114932)
    struct.pack_into(">I", rom, 0x4c, 0x001b1b18)                 # (the next table: not copied)
    arc = struct.pack(">HHI", 0x0108, 0xfffc, 0x3d04) + struct.pack("<I", 0x948b00) + b"\0" * 4
    arr = struct.pack(">HHI", 0x0108, 0xfffa, 0x3d04) + struct.pack("<I", 0x19c7d0) + b"\0" * 4
    rd = lambda img: (lambda a: struct.unpack_from("<I", img, a - 0x800) if 0x800 <= a <= 0x800 + len(img) - 4 else None)  # noqa: E731
    ra = lambda a: (rd(arc)(a) or [None])[0]                      # noqa: E731
    rr = lambda a: (rd(arr)(a) or [None])[0]                      # noqa: E731
    ids = {0x3d04: 0x2e0000}
    kinds = tables.layout(0x800, 0x40, ra, {0x948b00: 0x114932}, bytes(rom), ids)
    assert [k[1] for k in kinds] == ["word", "word", "sprite", "ptr"]
    kind, words = tables.convert(kinds, 0x800, 0x800, 0x40, ra, rr, bytes(rom), ids)
    assert kind == "new" and words[:4] == [(2, 0x0108, None), (2, 0xfffa, None), (4, 0x2e0000, 0x3d04), (4, 0x114932, None)]
    assert words[4] == (2, 0x001b, None)                            # then the 68k's own bytes
    assert tables.convert(kinds, 0x800, 0x800, 0x40, ra, ra, bytes(rom), ids) == ("same", [])


def test_ps2_only_flag_tests_fold():
    from espgalarr.detrans.porter import _fold_ps2_flags
    text = "F_1:\n\ttst.l\t$60eb08.l\n\tbeq\tL_2\n\tmove.w\t#1,4(a5)\n\trts\nL_2:\n\trts\n"
    # set where the 68k runs it: `beq` (taken when clear) falls through, `bne` is taken
    assert _fold_ps2_flags(text).splitlines()[1] == "\tmove.w\t#1,4(a5)"
    out = _fold_ps2_flags(text.replace("beq", "bne"))
    assert "60eb08" in out.splitlines()[1] and out.splitlines()[1].startswith("\tbra\tL_2")
    assert _fold_ps2_flags(text, root=False).splitlines()[1].startswith("\tbra\tL_2")   # (a callee: clear)
    other = text.replace("$60eb08", "$60eb0c")
    assert _fold_ps2_flags(other) == other


def test_flag_merged_routine_gets_an_entry_for_its_init():
    from espgalarr.detrans.canon import canon
    from espgalarr.detrans.porter import _flag_split_entry
    from espgalarr.detrans.verify import K68, Index
    k = lambda a, m, o: K68(a, 2, m, o, canon(m, o))                     # noqa: E731
    idx = Index([k(0x155bca, "move.w", "#$3,$4(a5)"), k(0x155bd0, "rts", ""), k(0x155bd2, "lea", "$155c98(pc),a0"),
                 k(0x155bd6, "nop", ""), k(0x155bd8, "jsr", "$14f118")])
    text = "F_9dba10:\n\ttst.l\t$60eb08.l\n\tbeq\tL_9dba48\n\tmove.w\t#3,4(a5)\n\trts\nL_9dba48:\n\tlea.l\t$155c98.l,a0\n"
    assert _flag_split_entry(text, 0x155bca, 0x155c5e, idx) == (0x155bd2, 0x9dba48)
    assert _flag_split_entry(text.replace("#3,", "#1,"), 0x155bca, 0x155c5e, idx) is False
    assert _flag_split_entry("F_1:\n\tmoveq\t#0,d0\n\trts\n", 0x155bca, 0x155c5e, idx) is None


def test_compare_after_a_branch_on_the_stores_flags():
    # subq.w #1,54(a5) ; beq L1 ; cmpi.w #1,54(a5) ; bne L2 ; rts ; L1: rts ; L2: rts.  The cmp
    # re-reads the stored word and subtracts 1 without a store: its flags look like the subq's
    # own, but the beq has read Z, so they are a new instruction's (not dropped into the subq)
    def word_minus_1(dst):          # dst = [a5+$36].w - 1 (a2 = the word)
        return [lui("at", 0x48), lw("v0", SLOT["a5"], "at"), lbu("a2", 0x36, "v0"), lbu("a1", 0x37, "v0"),
                sll("a2", "a2", 8), or_("a1", "a2", "a1"), andi("a2", "a1", 0xFFFF),
                addiu(dst, "a2", -1), andi(dst, dst, 0xFFFF)]

    def branch_on_z(taken_if_set, skip):
        return [lhu("v1", CCR, "gp"), andi("v1", "v1", 0x400),
                (bnez if taken_if_set else beqz)("v1", skip), NOP]

    subq = word_minus_1("a3") + [srl("v1", "a3", 8), sb("v1", 0x36, "v0"), sb("a3", 0x37, "v0")] \
        + nz_flags("a3")
    cmp = word_minus_1("a3") + nz_flags("a3")
    tail = [JR_RA, NOP]
    # offsets: from the delay slot of each branch to its target
    after_beq = cmp + branch_on_z(False, 0) + tail
    w = subq + branch_on_z(True, len(after_beq) - 1 + 1)
    after_bne = tail + tail
    w += cmp + branch_on_z(False, len(tail) + len(tail) - 1) + tail + tail + tail
    out = detrans_text(w)
    ops = [o.split("\t")[0] for o in out]
    i_beq, i_bne = ops.index("beq"), ops.index("bne")
    assert out[0] == "subq.w\t#1,54(a5)"
    assert i_beq < i_bne and "cmpi.w\t#1,54(a5)" in out[i_beq + 1:i_bne]


def test_se_stub_pins_by_effect_number():
    """Sound-effect stubs pair with the 68k stub that loads the same effect number, not by position."""
    import struct
    from types import SimpleNamespace
    from espgalarr.detrans import apply
    from espgalarr.pgm import asm68k
    p = bytearray(0x180000)
    for a, nn in ((0x17D800, 0x28), (0x17D81A, 0x29), (0x17D834, 0x2A), (0x17D84E, 0x2A)):
        p[a:a + 8] = asm68k.assemble(f"\tmovem.l\td0-d7/a0-a6,-(a7)\n\tmove.w\t#{nn},d0\n", a)[0]

    def stub(code, service=0x153F80, reg=2):
        return struct.pack("<4I", 0x3C008000 | reg << 16, 0x08000000 | service >> 2,
                           0x34040000 | reg << 21 | code, 0)
    base = 0x79AD00
    img = stub(0x2900) + stub(0x2800) + stub(0x2A00) + stub(0x0E01) + stub(0x0D01) + stub(0x2800, 0x100000)
    R = SimpleNamespace(image=img, image_base=base, header=SimpleNamespace(text_end=base + len(img)))
    pins = apply.se_stub_pins(R, bytes(p))
    # 0x2A: two 68k stubs (ambiguous); 0x0D01: unknown list entry; last: not a sound service
    assert pins == {base: 0x17D81A, base + 0x10: 0x17D800, base + 0x30: 0x17D96E}
