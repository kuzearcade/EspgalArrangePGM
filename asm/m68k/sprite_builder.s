; Sprite list builder for the enhanced sprite engine (espgalarr).
;
; Replaces the frame-end builder, which merged every sprite layer into one work list,
; capped it at 251 sprites (plus a dummy every 50) and copied it to the 256-entry IGS023
; list at 0x800000. This version is table driven and has no 256 limit:
;   * layers are emitted in the original priority order straight from their own lists
;     (12-byte work entries -> 10-byte hardware entries, same field transform and global
;     scroll offset as the original);
;   * the first 256 hardware entries fill the main list at 0x800000 (no terminator when
;     full, exactly what the IGS023 DMA reads);
;   * further entries go to the continuation list in cart ARM RAM at 0xD0C840, read by the
;     enhanced sprite engine (MAME `espgalarr` machine config); stock hardware shows the
;     first 256 as before;
;   * the dummy entry cadence (after 51 sprites, then every 50) is kept across both lists.
; Layers are added or moved by editing layer_table (base, byte-counter address pairs).
;
; replaces 0x113D7A
; entry sprite_builder

        .equ    MAIN_LIST, $800000
        .equ    MAIN_SLOTS, 256
        .equ    EXT_LIST, $d0c840
        .equ    EXT_SLOTS, 1400         ; continuation entries before its terminator (ends 0xD0FFFA)
        .equ    SCROLL_OFS, $807058
        .equ    COUNTERS, $806fca       ; 28 words of per-layer byte counters (+ spare)

sprite_builder:
        jsr     orig_112ad8             ; sprite DMA off while the list is rewritten
        move.l  SCROLL_OFS.l,d2
        lea     MAIN_LIST.l,a0
        move.w  #MAIN_SLOTS,d6          ; free slots in the current list
        moveq   #0,d7                   ; 0 = main list, 1 = continuation
        moveq   #$33,d4                 ; dummy cadence counter
        lea     layer_table(pc),a3
next_layer:
        move.l  (a3)+,d0
        beq.w   finish
        movea.l d0,a1
        movea.l (a3)+,a2
        move.w  (a2),d3                 ; layer size in bytes (12 per entry)
        beq.s   next_layer
entry_loop:
        subq.w  #1,d4
        bcc.s   real_entry
        moveq   #$32,d4
        bsr.w   take_slot
        move.l  #$fc003800,(a0)+        ; off-screen dummy entry
        clr.l   (a0)+
        move.w  #$201,(a0)+
        subq.w  #1,d4
real_entry:
        bsr.w   take_slot
        move.l  (a1)+,d0
        move.l  d0,d1
        andi.l  #$f800f800,d0
        andi.l  #$07ff3fff,d1
        add.l   d2,d1
        andi.l  #$07ff3fff,d1
        or.l    d0,d1
        move.l  d1,(a0)+
        move.l  (a1)+,(a0)+
        move.w  (a1)+,(a0)+
        move.b  (a1)+,d0
        or.b    (a1)+,d0
        move.b  d0,-6(a0)
        subi.w  #12,d3
        bne.s   entry_loop
        bra.s   next_layer

; take_slot: reserve one entry in the current list; switches to the continuation list
; when the main list is full, and ends the build when both are full.
take_slot:
        tst.w   d6
        bne.s   slot_ok
        tst.w   d7
        bne.s   lists_full
        moveq   #1,d7
        lea     EXT_LIST.l,a0
        move.w  #EXT_SLOTS,d6
slot_ok:
        subq.w  #1,d6
        rts
lists_full:
        addq.l  #4,a7                   ; drop the return address
finish:
        tst.w   d7
        bne.s   terminate               ; in the continuation list: terminate it
        tst.w   d6
        beq.s   main_full
        bsr.s   write_terminator        ; main list ends early
main_full:
        lea     EXT_LIST.l,a0           ; continuation list is empty
terminate:
        bsr.s   write_terminator
        lea     COUNTERS.l,a0
        moveq   #0,d1
        move.w  #$1b,d0
clear_counters:
        move.w  d1,(a0)+
        dbra    d0,clear_counters
        jmp     orig_112aca             ; sprite DMA back on

write_terminator:
        clr.l   (a0)+
        clr.l   (a0)+
        clr.w   (a0)+
        rts

        .align  4
; (layer list base, byte counter) in emit order; first = highest priority.
layer_table:
        dc.l    $80120a, $806fca
        dc.l    $804486, $806fd8
        dc.l    $801dce, $806fcc
        dc.l    $804576, $806fda
        dc.l    $802602, $806fce
        dc.l    $804666, $806fdc
        dc.l    $802e36, $806fd0
        dc.l    $804756, $806fde
        dc.l    $80366a, $806fd2
        dc.l    $804846, $806fe0
        dc.l    $803e9e, $806fd4
        dc.l    $806b92, $806ffe
        dc.l    $806aa2, $806ffa
        dc.l    $8066a6, $806ff4
        dc.l    $8066e2, $806ff6
        dc.l    $806a66, $806ff8
        dc.l    $804936, $806fe2
        dc.l    $804bd6, $806fe4
        dc.l    $804c36, $806fe6
        dc.l    $8043ea, $806fd6
        dc.l    $804f7e, $806fe8
        dc.l    $805086, $806fea
        dc.l    $8063be, $806ff0
        dc.l    $8051ee, $806fec
        dc.l    $805a5e, $806fee
        dc.l    $80647e, $806ff2
        dc.l    $806b1a, $806ffc
        dc.l    0
