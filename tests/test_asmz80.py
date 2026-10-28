from espgalarr.pgm import asmz80


def test_z80_encodings():
    src = """
    .org $100
start:
    di
    ld sp,$ffff
    ld a,($8200)
    ld ($8100),a
    ld hl,data
    ld b,(hl)
    ld (hl),$55
    ld bc,$1234
    ld (ix+2),a
    ld a,(iy-1)
    ld de,(table)
    ld (table),hl
    add a,b
    adc a,$10
    sub (hl)
    and $0f
    or c
    xor a
    cp $80
    inc hl
    dec b
    inc (ix+4)
    add hl,de
    sbc hl,bc
    rlca
    srl a
    rr (hl)
    bit 7,a
    set 0,(ix+1)
    res 3,b
    jp start
    jp nz,start
    jr loop
    jr z,loop
    djnz loop
    call start
    call c,start
    ret
    ret nz
    push hl
    pop af
    push ix
    ex de,hl
    exx
    ldir
    in a,($80)
    out ($81),a
    im 1
    ei
loop:
    halt
data:
    db 1,2,3
table:
    dw $abcd
"""
    code, syms = asmz80.assemble(src)
    expect = bytes.fromhex(
        "f3" "31ffff" "3a0082" "320081" "21" + f"{syms['data']&0xff:02x}{syms['data']>>8:02x}" +
        "46" "3655" "013412" "dd7702" "fd7eff" "ed5b" + f"{syms['table']&0xff:02x}{syms['table']>>8:02x}" +
        "22" + f"{syms['table']&0xff:02x}{syms['table']>>8:02x}" +
        "80" "ce10" "96" "e60f" "b1" "af" "fe80" "23" "05" "dd3404" "19" "ed42" "07" "cb3f" "cb1e"
        "cb7f" "ddcb01c6" "cb98" "c30001" "c20001")
    assert code.startswith(expect)
    assert code.endswith(b"\x76\x01\x02\x03\xcd\xab")
    # jr loop / jr z,loop / djnz loop relative offsets must land on 'loop'
    i = code.index(b"\xc2\x00\x01") + 3
    jr_off = code[i + 1]
    assert (syms["start"] + i + 2 + (jr_off - 256 if jr_off > 127 else jr_off)) == syms["loop"]
