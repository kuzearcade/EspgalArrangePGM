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
