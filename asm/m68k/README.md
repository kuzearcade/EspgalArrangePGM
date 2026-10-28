Our 68000 source files (Motorola syntax, assembled by espgalarr/pgm/asm68k.py). Each file is
placed in free program ROM by the code stage and hooked in through its header comments
(`; replaces 0x...`, `; hook 0x... jsr|jmp`, `; entry label`). No original code lives here.
