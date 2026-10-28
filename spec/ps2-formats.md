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

### Relationship to the PGM sprite set (verified)

Every sprite drawn on the PGM title screen (35 entries from the live sprite list, decoded
with `espgalarr/pgm/gfx.py`) exists pixel-for-pixel in the PS2 banks A020/A02B (transparent
= 0x1F). The PS2 sprite id space is the 0x4061-entry offset table shared by both banks
(A02B holds large/backdrop sprites). So the PS2 port uses the PGM sprite art converted 1:1,
and **"new Arrange graphics" = PS2 sprite ids (and stage-blob sprites) that have no PGM
counterpart** — found by matching decoded pixel data against the PGM sprite directory.

### Stage data files A021..A02A

Every A021..A02A file is a sprite bank with exactly the A020 layout
(offset table, raw/LZSS 8-bit sprites, optional trailing TIM2); nothing
else is stored in them. The game's sprite renderer looks a sprite number up
in A020 first and, when that entry is 0, in whichever bank is currently
loaded in the second slot. Mapping (ELF tables used by the stage-start
routine; see `arika_pak.STAGE_FILES` / `file_role`):

| stage | sprites | background tiles | overlay (Arrange / Arcade) |
|---|---|---|---|
| 1..6 | A025..A02A | A02C..A031 | ov_st1..6 / ov_arc_st1..6 |
| ending | A021 (+TIM2 ending picture) | A032 | ov_end / ov_arc_end |

A023 (Arrange) and A024 (Arcade) hold the common in-game sprite set loaded at
game start (same 3845 sprite numbers; 78 large sprites are redrawn in
Arrange). A022 is a 16-sprite set for a results-type screen; A02B is the
title/menu/gallery set with a trailing TIM2.

Consequence for the port: stage and bullet-pattern differences between
Arrange and Arcade are not data in these files (both modes load the same
stage files); they live in the per-mode overlay code and the data tables
embedded in those overlays.

### Graphics that actually change in Arrange (measured)

Stage sprite sets A025–A02A and the background tile sets are shared by both modes. The only
mode-specific bank pair is A023 (Arrange) vs A024 (Arcade): same 3845 sprite ids, of which
**78 sprites are redrawn** (ids 9034–9062, 9071–9077, 9081–9087, 9091–9108, 9255, 9257,
9280–9295: the player characters and their effects). Their cost in PGM format is about
243 KB of A-ROM (5-bit packed, opaque pixels only) and 162 KB of B-ROM (masks), which fits
the free space in a04802 (~1 MB) and b04801 (~0.48 MB) without reclaiming anything.

## PS2 code structure

Facts recovered from `work/ps2/disc/SLPS_253.52` and the A0A0 overlays (addresses only; tooling in
`espgalarr/diff/mips_funcs.py`, results in `work/ps2/notes/func_diff.md`).

### Executable and overlays

- ELF: one PT_LOAD at 0x100000 (file offset 0x800, filesz 0x257e00, memsz 0x549800, so .bss runs to
  0x649800), entry 0x100008. crt0 sets `$gp = 0x35fcf0` (0x1001c0) and the main stack via syscall.
- MWo3 overlays: the loader reads the **whole file, 0x40-byte header included, to `load_addr`**; the
  header's `end_addr` equals `load_addr + 0x40 + text + data`. The code image therefore starts at
  `load_addr + 0x40` (mode cores 0x730840, stage overlays 0x649840). The listings in
  `work/ps2/dis/ov_*.lst` were made with the header stripped and are 0x40 too low; all addresses in
  this section are real load addresses. Intra-overlay `jal` targets only line up with prologues under
  this +0x40 rule (arcade 5601/7409, arrange 3512/4475; with +0 only ~1%).
- ov_arcade.bin: text 0x730840-0xbf4300, data to 0xd02300, bss 0x2e100. ov_arrange.bin: text
  0x730840-0xb33080, data to 0xc26780, bss 0x21700. The "text" region is code followed by rodata
  (copies of the 68k ROM tables, see spec/pgm-data-tables.md); the data region holds the mode's
  own tables (sound table, pointer tables with MIPS code addresses).

### What the code is

Both mode cores are a **static translation of the 68k program** to MIPS. The emulated register file
lives at 0x4810a0 (A7), 0x4810a8..0x4810d8 = A6..A0, 0x4810e0..0x481118 = D7..D0 (32-bit each, 8-byte
stride); the CCR is the halfword at `$gp-0x7ee4` (0x357e0c) with C=0x100, V=0x200, Z=0x400, N=0x800,
X=0x1000. 68k big-endian memory is read byte-wise (`lbu`/`sll`/`or`), ROM addresses are rewritten to
the overlay's copies (e.g. 68k 0x17cee0 -> 0xbf6bb0 in arrange, 0xcda9a0 in arcade), and 68k work RAM
is reached through `$gp` or `lui/addiu` (68k address = PS2 address + 0x4c5bb8, anchored on the
stage-clear flag 0x81e3cc = `$gp-0x74dc`; one anchor, provisional). `movem.l d0-d7/a0-a6,-(sp)`
becomes a 15-register byte-store sequence (handlers of 0x580 bytes).

ELF layout (approximate boundaries): 0x100000-~0x152000 native runtime (CD request queue
`0x111700(kind, flags, fileno, arg)` which registers A980/A981 and AA80/AA81 at 0x153130; stream
manager, GS wrapper "wrapgpu.c" at 0x139b40); ~0x152ab0-~0x27d000 translated 68k routines shared
with the **arrange** core (the arrange overlay calls 299 distinct ELF entry points, the arcade
overlay only 75 and instead carries its own copies: 677 arcade-only functions have an opcode-identical
twin in the ELF); 0x27d000+ CRI ADX library (ADXT_* at 0x284350-0x285e90) and sce libraries.

### Streamed music path

- Channel blocks: 2 x 16 bytes at 0x5505d0 (byte0 flags: 0x80 active, 0x40 start requested,
  0x10 fading, 0x04 playing, 0x02 pause req, 0x01 resume req; byte1 loop; +2 volume; +4 current id;
  +8 requested id). id = `(file number << 16) | AFS index`, e.g. 0x09800012 = A980 entry 18.
- API: `0x12d5b0 snd_stream_request(ch, id, loop, vol)` (fades the running stream and queues the new
  id via 0x5505a8/0x5505b0/0x5505c0), 0x12d710 pause, 0x12d770 resume, 0x12d7a0 fade, 0x12d7e0
  fade-all; per-frame service 0x12d830 -> 0x124a40 -> `ADXT_SetLpFlg` (0x2853b0, 1 when loop) and
  `ADXT_StartAfs` (0x285ab0, a0=handle, a1=partition id = file number, a2=AFS index). Other ADXT
  entry points: Stop 0x284470, SetOutVol 0x284df0, StartFname 0x285ba0 (unreferenced), StartMem2
  0x285c50 / StartMemIdx 0x285d28 (unreferenced).
- Hook: the translated 68k music stub 0x17df18 (arrange 0x79b0d0, arcade 0xa2d910 and 0xa2dd30 for the
  0x17df36 entry) keeps the 68k table lookup (BE16 at table[cmd*4]) and replaces the Z80 send with a
  lookup in a 13-entry u32 stream table (arrange 0xbf6c20, arcade 0xcdaa10) followed by
  `snd_stream_request(0, id, 1, 0xdc)`; the request is skipped unless the 68k word at PS2 0x358008
  (arrange) / 0x358340 (arcade) is zero. Only channel 0 is ever requested.
