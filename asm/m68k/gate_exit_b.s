; Removal exit for the stage 1 enemy routine 0x1602CE (PS2 behaviour), as gate_exit_a.s for
; its variant (first call 0x15FC5E, Arrange stage 1 overlay 0x6D0960).
;
; replaces 0x1602CE
; entry gate_exit_b

gate_exit_b:
        move.l  a6,-(a7)
        jsr     $15fc5e
        cmpa.l  (a7)+,a6
        beq     geb_alive
        rts
geb_alive:
        jsr     $15fae4
        jmp     $1602d6
