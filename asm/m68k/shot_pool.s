; Player area clear with the Arrange player-shot pools (layout stage; default, --arcade-shots
; disables).
;
; The original clears 0x1D52 words from the P1 record 0x80EE96 (player records, both arcade
; shot pools of 35 records, the shot slot lists). The Arrange pools have 99 records of 0x34
; bytes per player (98 shots and a terminator, PS2 0xC31200 / 0xC3261C); on PGM they live in
; enhanced RAM (layout.SHOT_POOL), so they are cleared here as well.
;
; replaces 0x123B2E
; requires arrange_shots
; entry player_area_clear

        .equ    SHOT_POOL, $e0c800
        .equ    SHOT_POOL_BYTES, 2*99*$34

player_area_clear:
        move.w  #$1d51,d0
        moveq   #0,d1
        lea     $80ee96.l,a0
clear_old:
        move.w  d1,(a0)+
        dbra    d0,clear_old
        lea     SHOT_POOL.l,a0
        move.w  #SHOT_POOL_BYTES/2-1,d0
clear_pools:
        move.w  d1,(a0)+
        dbra    d0,clear_pools
        rts
