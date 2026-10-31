; Arrange item pool clear (gems, gold items).
;
; The Arrange pool has 800 records of 30 bytes (the PS2 sets its item slot count to 800); on
; PGM it lives in enhanced RAM (layout.ITEM_POOL, the layout stage relocates the pool base
; references and the spawn walks). The arcade clear zeroed its 100-record pool together with
; the live count and the item variables that follow it (0x817780..0x818351); this clears the
; new pool and that same old range.
;
; replaces 0x16E696
; requires arrange_bullets
; entry item_pool_clear

        .equ    POOL, $e10000
        .equ    SLOTS, 800
        .equ    OLD, $817780            ; arcade pool, then the count 0x818338 and variables
        .equ    OLD_BYTES, $bd2

item_pool_clear:
        lea     POOL.l,a0
        move.w  #SLOTS*30/2-1,d0
        moveq   #0,d1
clear_pool:
        move.w  d1,(a0)+
        dbra    d0,clear_pool
        lea     OLD.l,a0
        move.w  #OLD_BYTES/2-1,d0
clear_old:
        move.w  d1,(a0)+
        dbra    d0,clear_old
        rts
