; Removal exit for the stage 1 enemy routine 0x15F242 (PS2 behaviour).
;
; The routine's first call (0x15EC28) may remove the enemy by jumping to 0x14F0D8, which walks
; the enemy's records with a6 and returns into the routine; the 68k then runs the rest of the
; routine with a6 past the enemy's records and moves the next enemy's hitbox (record +0x2E).
; The PS2 (Arrange ELF 0x24B5C0, and its arcade mode) saves a6 around that call and returns
; at once when it changed. Same here; otherwise the routine continues with its second call.
;
; replaces 0x15F242
; entry gate_exit_a

gate_exit_a:
        move.l  a6,-(a7)
        jsr     $15ec28
        cmpa.l  (a7)+,a6
        beq     gea_alive
        rts
gea_alive:
        jsr     $15ea6e
        jmp     $15f24a
