# PS2 Espgaluda disc formats

## A020 container (A020/A020..A033, also A000/A00E)

Implementation: `espgalarr/ps2/arika_pak.py`. Evidence and addresses:
`work/ps2/notes/loader.md` (git-ignored).

### Scrambling

The game's CD request queue reads each file verbatim and the completion
callback unscrambles it in place. The transform is a byte delta chained on
the scrambled stream plus a keystream from a 32-bit linear congruential
generator. The only key is the file length in bytes:

```
state  = (size + 0x012D86A1) mod 2^32
out[0] = in[0]
for i in 1 .. size-1:
    out[i] = (in[i] - in[i-1] - ((state >> 8) & 0xFF)) mod 256
    state  = (state * 17 + 1) mod 2^32
```

Encoding is the inverse (`enc[i] = plain[i] + enc[i-1] + key`). Files are not
compressed as a whole. `decode_file(data)` / `encode_file(plain)` implement
this; `size` defaults to `len(data)`, which is what the game passes.

### Plaintext by file number

| file | content |
|---|---|
| A020 | main sprite bank (arcade); A02B: title/menu sprite bank + trailing TIM2 |
| A021..A02A | per-stage data blobs (layout owned by the stage overlays; A021 ends with a TIM2) |
| A02C..A032 | background tile sets, one per stage |
| A033 | text-layer tiles (HUD / kana font), loaded at boot |
| A00E | one TIM2 picture, 224×448 8-bit indexed (ADX logo) |

**Sprite bank** (`parse_sprite_bank`, `sprite_pixels`): 16481 (0x4061)
little-endian u32 offsets, one per sprite number, padded to 0x10400. 0 = not
present (the game then looks the number up in the other loaded bank). Bit 31
set = LZSS-compressed sprite at `offset & 0x7FFFFFFF`; otherwise raw pixels.
A sprite is `width × height` bytes, one pixel per byte, values 0..31 with
0x1F transparent — the PGM 5-bit sprite pixels unpacked to bytes. Dimensions
are not stored in the bank; they come from the game's sprite list (width is
a multiple of 16).

**LZSS** (`lzss_decompress`): u32 LE decompressed size, then blocks headed
by a byte `c`. If `c & 0x80 == 0`, the next `c` bytes are literals. Otherwise
bits 0..6 of `c` (LSB first) flag seven tokens: 0 = one literal byte, 1 = a
u16 LE back-reference with distance `(tok & 0x7FF) + 1` and length
`((tok >> 11) & 0x1F) + 2`, copied byte-wise forward (overlap allowed).

**Background tiles** (`bg_tiles`): 32×32 pixels, 5 bpp, 640 bytes per tile;
each 20-byte row holds 32 pixels packed LSB-first in little-endian bit order.
Tile counts per file: 1536, 1408, 1792, 1856, 1216, 1408, 1024.

**Text tiles** (`text_tile`): 8×8 pixels, 4 bpp, 32 bytes per tile, two
pixels per byte, low nibble = left pixel; 8614 tiles in A033.

**TIM2** (`find_tim2`, `tim2_pictures`): standard Sony TIM2 (file header
0x10 bytes, picture headers 0x30 bytes, image then CLUT).
