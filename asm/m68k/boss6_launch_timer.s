; Stage 6 boss launched enemy (0x1A6C24, type 0x7E): lifetime timer (PS2 behaviour).
;
; When $26(a5) (0x3FC frames from the init) runs out, the 68k self-destructs the enemy
; (0x1A6D48: burst 0x1A6CE0, explosion effects, removal). Arrange (A0A5 0x69E16C) only fires the
; burst (0x69D3A0 = j 0x69C950, the twin of 0x1A6CE0) and returns: the enemy flies on, its
; timer counting down past zero (PCSX2 watch of the task slot, play 35481).
;
; hook 0x1A6E28 jmp
; entry boss6_launch_timer

boss6_launch_timer:
        subq.w  #1,$26(a5)
        bcs     blt_burst
        jmp     $1a6e30
blt_burst:
        jsr     $1a6ce0
        rts
