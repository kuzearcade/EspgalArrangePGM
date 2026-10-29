; Enemy area clear with the Arrange enemy record layout (layout stage, --relocate-pools).
;
; The original clears the enemy script/record area at 0x814410 and then calls 0x14ECAC. The 120
; enemy records (Arrange: 0x2C bytes each instead of 0x24) and the counter word after them now
; live in enhanced RAM at 0xE08200, so they are cleared here as well.
;
; replaces 0x14EC34
; requires relocate_pools
; entry enemy_area_clear

        .equ    POOLS, $e08200
        .equ    POOLS_BYTES, 120*$2c+2

enemy_area_clear:
        lea     $814410.l,a0
        move.w  #$1816,d0
clear_old:
        move.w  #0,(a0)+
        dbra    d0,clear_old
        lea     POOLS.l,a0
        move.w  #POOLS_BYTES/2-1,d0
clear_pools:
        move.w  #0,(a0)+
        dbra    d0,clear_pools
        jmp     orig_14ecac
