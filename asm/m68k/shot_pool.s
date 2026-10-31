; Player area clear with the Arrange player-shot pools (layout stage; default, --arcade-shots
; disables).
;
; The original clears 0x1D52 words from the P1 record 0x80EE96 (player records, both arcade
; shot pools of 35 records, the shot slot lists). The Arrange pools have 99 records of 0x34
; bytes per player (98 shots and a terminator, PS2 0xC31200 / 0xC3261C); on PGM they live in
; enhanced RAM (layout.SHOT_POOL), so they are cleared here as well, with the Power Shot trail
; pools right below them (layout.TRAIL_POOL, 2 x 64 records of 0x22 bytes) and Arrange's score
; counters below those (layout.SCORE_VARS, score.py), and the score popup list
; (layout.POPUP_LIST).
;
; replaces 0x123B2E
; requires arrange_shots
; entry player_area_clear

        .equ    CLEAR_FROM, $e0b6ec
        .equ    POOL_BYTES, $14+2*64*$22+2*99*$34
        .equ    POPUP_LIST, $e0a000
        .equ    POPUP_BYTES, 80*30

player_area_clear:
        move.w  #$1d51,d0
        moveq   #0,d1
        lea     $80ee96.l,a0
clear_old:
        move.w  d1,(a0)+
        dbra    d0,clear_old
        lea     CLEAR_FROM.l,a0
        move.w  #POOL_BYTES/2-1,d0
clear_pools:
        move.w  d1,(a0)+
        dbra    d0,clear_pools
        lea     POPUP_LIST.l,a0
        move.w  #POPUP_BYTES/2-1,d0
clear_popups:
        move.w  d1,(a0)+
        dbra    d0,clear_popups
        rts
