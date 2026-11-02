; Stage 6 boss volley speed (PS2 behaviour).
;
; The boss's sub-task 0x1A5B1C fires the four bullets (speed class $8(a4), type 0x21) that turn
; into small enemies when they reach the bottom of the screen (0x1777FC). Arrange (A0A5
; 0x6B2440) fires them through a PS2-only wrapper (0x6B1330) as type 0x20, and they fly one
; speed step faster: a PCSX2 dump at play 34436 shows the same bullets at the same positions
; and angles with speed 0x15 (+0x1A) where the 68k has 0x14, and the edge-converting handler
; (0x94B210, the twin of 0x1777FC). The arcade's type 0x20 is a spawner bullet, so the 68k keeps
; type 0x21 and adds the step to the speed class.
;
; hook 0x1A5B28 jmp
; entry boss6_volley

boss6_volley:
        move.w  $8(a4),d0
        addq.w  #1,d0
        swap    d0
        jmp     $1a5b2e
