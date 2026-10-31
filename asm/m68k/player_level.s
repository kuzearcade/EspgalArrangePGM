; Player level at (re)spawn (Arrange).
;
; Arrange's player setup (A0A9 0x82AFC0, the twin of 0x12275A) copies the player template and
; then sets the level word +0x76 to 6 for either player (ELF 0x1F71D0 with 3: the word is
; twice the argument); the arcade starts at 0 and raises it by 2 per gem-gauge level-up up to
; 6. The hook replaces the instruction after the copy loop (move.b #$f0,$39(a6)).
;
; hook 0x12277A jmp
; entry player_level

player_level:
        move.w  #6,$76(a6)
        move.b  #$f0,$39(a6)
        jmp     $122780
