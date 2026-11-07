; Arrange enemy bullet pool initialiser (game start).
;
; The Arrange pool has 800 slots of 0x40 bytes; on PGM it lives in the DDP2 cart's ARM
; shared RAM at 0xD00000 (the layout stage relocates every pool base reference). Like the
; arcade initialiser it clears the pool, clears the 0x24 bytes of bullet variables that
; follow the arcade pool, marks every slot free (word 0xFFFF at +2) and sets the arcade
; group count/delta. It also sets the Arrange walker variables: 80 groups of 10 slots and
; no removed groups. It runs at game start and at every stage load, and clears the Arrange-only
; variables (porter.NEWVAR_BASE) up to 0xE10000 except Arrange's score counters
; (layout.SCORE_VARS, PS2 0xC37670..0xC37683): those carry over between stages on the PS2 (the
; hit count kept into stage 6 pays a stage 6 kill 264 x (12 + 10)); the game-start player area
; clear (shot_pool.s) zeroes them.
;
; replaces 0x170D2E
; requires arrange_bullets
; entry bullet_pool_init

        .equ    POOL, $d00000
        .equ    SLOTS, 800
        .equ    VARS, $81b552           ; bullet variables after the arcade pool
        .equ    ARR_CAP, $818352        ; Arrange walker group count
        .equ    ARR_DELTA, $818354      ; Arrange walker removed-group count
        .equ    NEWVARS, $e09800        ; Arrange-only variables .. NEWVARS_END
        .equ    NEWVARS_END, $e10000
        .equ    SCORE_VARS, $e0b6ec     ; layout.SCORE_VARS: kept across stages
        .equ    SCORE_BYTES, $14

bullet_pool_init:
        lea     POOL.l,a0
        move.w  #SLOTS*$20-1,d0         ; SLOTS * 0x40 bytes as words
        moveq   #0,d1
clear_pool:
        move.w  d1,(a0)+
        dbra    d0,clear_pool
        lea     VARS.l,a0
        moveq   #$24/2-1,d0
clear_vars:
        move.w  d1,(a0)+
        dbra    d0,clear_vars
        lea     POOL+2.l,a0
        move.w  #SLOTS-1,d0
mark_free:
        move.w  #$ffff,(a0)
        lea     $40(a0),a0
        dbra    d0,mark_free
        move.w  #6,$81b55a.l
        move.w  #$ffff,$81b562.l
        move.w  #SLOTS/10-1,ARR_CAP.l
        move.w  #$ffff,ARR_DELTA.l
        lea     NEWVARS.l,a0            ; Arrange-only variables (enhanced RAM): start cleared
        move.w  #(SCORE_VARS-NEWVARS)/2-1,d0
clear_newvars:
        move.w  d1,(a0)+
        dbra    d0,clear_newvars
        lea     SCORE_VARS+SCORE_BYTES.l,a0     ; the score counters kept
        move.w  #(NEWVARS_END-SCORE_VARS-SCORE_BYTES)/2-1,d0
clear_rest:
        move.w  d1,(a0)+
        dbra    d0,clear_rest
        rts
