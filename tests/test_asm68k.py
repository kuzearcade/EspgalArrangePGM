"""Cross-check the mini 68k assembler against capstone's disassembler."""
import pytest

capstone = pytest.importorskip("capstone")
from espgalarr.pgm import asm68k, m68k  # noqa: E402

SRC = """
    .org $200000
start:
    move.w  d0,d1
    move.l  #$12345678,d2
    move.b  (a0)+,d3
    move.w  d4,-(sp)
    move.l  $8139e0.l,d5
    move.w  #1,$801100.l
    movea.l #$800000,a1
    move.w  4(a0),d2
    move.w  (a3,d0.w),d1
    move.b  2(a6,d1.l),d0
    lea     $c3a78.l,a0
    lea     tbl(pc),a2
    moveq   #-1,d0
    add.w   d0,d1
    add.l   d1,d2
    sub.w   #3,d0
    addq.w  #1,$80106a.l
    subq.l  #4,a7
    cmp.w   d1,d0
    cmpi.w  #$3f,d1
    and.w   #$3f,d1
    or.b    d0,(a1)
    eor.w   d2,d3
    lsl.w   #4,d1
    asr.l   #4,d2
    lsr.w   d1,d0
    ror.w   #1,d0
    btst    #6,d0
    bset    #7,d1
    bclr    #0,(a0)
    tst.w   $8139f4.l
    tas     $642308.l
    tas     3(a5)
    clr.w   $801204.l
    neg.w   d2
    not.w   d3
    ext.l   d0
    swap    d1
    mulu    d1,d2
    divu    #10,d3
    jsr     $118176.l
    jmp     (a3,d1.w)
    jsr     (a0)
    bsr     sub1
    bra     loop
    bne.s   loop
    beq     sub1
    dbra    d0,loop
    movem.l d0-d7/a0-a6,-(sp)
    movem.l (sp)+,d0-d7/a0-a6
    movem.w d0/d2/a1,(a0)
    link    a6,#-8
    unlk    a6
    exg     d0,a0
    pea     $e37d4.l
    sne     d0
    rts
loop:
    nop
sub1:
    rte
tbl:
    dc.w    8,$10,$18
    dc.l    $11223344
    dc.b    1,2,3,4
"""


def test_assembler_matches_capstone():
    code, syms = asm68k.assemble(SRC)
    assert syms["start"] == 0x200000
    listing = list(m68k.disassemble(code, 0x200000))
    text = "\n".join(f"{m} {o}" for _, _, m, o in listing)
    # spot checks of exact encodings
    assert code[:2] == b"\x32\x00"                    # move.w d0,d1
    # (encodings as numbers: opcode word, then the extension words)
    assert (0x243C12345678).to_bytes(6, "big") in code     # move.l #$12345678,d2
    assert (0x41F9000C3A78).to_bytes(6, "big") in code     # lea $c3a78.l,a0
    assert (0x48E7FFFE).to_bytes(4, "big") in code         # movem.l d0-d7/a0-a6,-(sp)
    assert (0x4CDF7FFF).to_bytes(4, "big") in code         # movem.l (sp)+,d0-d7/a0-a6
    assert b"\x4e\x75" in code and b"\x4e\x73" in code
    assert "move.l #$12345678, d2" in text
    assert "lea.l $c3a78.l, a0" in text
    assert "btst.b #$6, d0" in text or "btst.l #$6, d0" in text
    assert "dbra d0," in text
    assert "jsr $118176.l" in text
    # every source instruction must disassemble to a real mnemonic (no .byte fallbacks) up to tbl
    end = syms["tbl"] - 0x200000
    for addr, raw, mnem, _ in listing:
        if addr - 0x200000 >= end:
            break
        assert mnem != ".byte", f"bad encoding at {addr:#x}: {raw.hex()}"
