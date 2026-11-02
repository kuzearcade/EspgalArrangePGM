; Enemy bullet speed (PS2 behaviour): one speed step faster than the arcade.
;
; The bullet set-up of the allocators (0x1713EC / 0x17135E: 0x171472; 0x170FB8 / 0x170F36:
; 0x17103A) takes the speed class as the type's base + the caller's d0 high word + the rank
; 0x813B00. Arrange's set-ups (A0A9 0x8E7B90 and its three siblings) then call 0x8AFE40,
; `addq.w #1,d7`; the arcade mode's (A0A8) do not. PCSX2 bullet dump at stage 6 play 34436:
; speed 0x15 where the 68k had 0x14, at the same positions and angles.
;
; hook 0x1714BE jsr
; hook 0x171084 jsr
; entry bullet_speed

bullet_speed:
        add.w   $813b00,d7
        addq.w  #1,d7
        rts
