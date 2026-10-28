"""De-translator tests on synthetic MIPS code (no game bytes).

The MIPS snippets are hand-assembled with the helpers below following the conventions the
translator uses (register slots at 0x4810a0.., CCR at $gp-0x7ee4)."""
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
