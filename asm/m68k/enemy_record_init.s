; Enemy record initialisation from a stage data list, for the Arrange 0x2C-byte enemy records.
;
; Same behaviour as the routine it replaces (d7+1 records from 4(a5), destination a6, source
; a0): each record starts with a word; a negative word is followed by 15 words copied from
; offset 6, otherwise the record is flagged active (bit 7), gets three fields from the list,
; three cleared longs and 4 more words at offset 0x1C. The original advanced exactly one
; 0x24-byte record per entry; here each record also gets the 8 bytes Arrange appends, as
; Arrange's version (0x786AA0) writes them: the hit marks (+0x24) cleared, so a reused record
; drops the previous enemy's, and the owner (+0x28) = the enemy object a5.
;
; replaces 0x14F118
; requires relocate_pools
; entry enemy_record_init

enemy_record_init:
        move.w  4(a5),d7
        movea.l a6,a1
rec_loop:
        move.w  (a0)+,(a1)
        bpl.s   rec_active
        addq.w  #6,a1
        moveq   #14,d6
copy_rest:
        move.w  (a0)+,(a1)+
        dbra    d6,copy_rest
        clr.l   (a1)+
        move.l  a5,(a1)+
        dbra    d7,rec_loop
        rts
rec_active:
        bset    #7,(a1)
        move.l  (a0)+,6(a1)
        move.l  (a0)+,10(a1)
        move.w  (a0)+,14(a1)
        moveq   #0,d0
        move.l  d0,16(a1)
        move.l  d0,20(a1)
        move.l  d0,24(a1)
        lea     28(a1),a1
        moveq   #3,d6
copy_tail:
        move.w  (a0)+,(a1)+
        dbra    d6,copy_tail
        clr.l   (a1)+
        move.l  a5,(a1)+
        dbra    d7,rec_loop
        rts
