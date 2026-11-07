"""Build stage 'gfx': port the redrawn Arrange sprites into the PGM A/B ROMs.

Facts used (spec/ps2-formats.md, spec/pgm-gameplay.md):
- PS2 banks A023 (Arrange) / A024 (Arcade) hold the same sprite ids; the ids whose pixels differ
  are the redrawn Arrange art. A024's pixels equal the PGM sprites, so each redrawn id's original
  B-ROM offset is found by searching the B ROM for the sprite's mask words (PGM sprite data is
  width-agnostic). The new art is encoded into free A/B space and every 68k reference to the old
  sprite code (bits 0-22 = B offset in words) is redirected.
- The Arrange player animation records live in the ELF (16-byte records: id, x/y offset, hitbox,
  size|flags) in the same layout as the 68k records; for redrawn ids their fields replace the
  68k record fields (size words change because 60 of the 78 sprites changed dimensions).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from ..pgm import gfx as gfxmod, m68k, rom as rom_mod
from ..ps2 import arika_pak as ap, elf as elfm

A_FREE_BYTES = 0x800000 + 0x6F6000      # start of free space in the 16 MB A image (a04802 tail)
B_FREE_BYTES = 0x785000                 # start of free space in the B ROM
RECORD_TABLE_SCAN = (0x1D6300, 0x1DA000)   # ELF file offsets to scan for arrange animation records


def _mask_bytes(px: np.ndarray) -> bytes:
    out = bytearray()
    flat = px.reshape(-1, 16)
    for row in flat:
        v = 0
        for x in range(16):
            if row[x] == 0x1F:
                v |= 1 << x
        out += bytes((v & 0xFF, v >> 8))
    return bytes(out)


def _blank(px: np.ndarray) -> bool:
    return bool((px == 0x1F).all())


def locate_sprite(b_rom: bytes, a_rom: bytes, px: np.ndarray) -> int | None:
    """B-ROM word offset of the sprite whose pixels are `px` (flat, len multiple of 16). A wholly
    transparent sprite matches any transparent stretch (9057 / 9108, the last frames of the select
    strips, matched inside the confirm effect's frames 0x63104 / 0x5F9D8, which the remap then
    pointed at the strips' Arrange art), so it is not located."""
    if _blank(px):
        return None
    m = _mask_bytes(px)
    if len(set(m)) < 3 and len(m) < 64:
        return None
    pos = b_rom.find(m)
    n16 = len(px) // 16
    while pos >= 0:
        if pos >= 4 and (pos - 4) % 2 == 0:
            off = (pos - 4) // 2
            dec = gfxmod.decode_sprite(a_rom, b_rom, off, 1, n16).pixels.reshape(-1)
            dec = np.where(dec == 255, 0x1F, dec).astype(np.uint8)
            if np.array_equal(dec, px):
                return off
        pos = b_rom.find(m, pos + 1)
    return None


def _coherence(px: np.ndarray, w: int) -> float:
    a = px.reshape(-1, w)
    if a.shape[0] < 2:
        return 0.0
    opaque = a != 0x1F
    same = float((a[1:] == a[:-1]).mean())
    vo = float((opaque[1:] & opaque[:-1]).sum()) / max(1, int(opaque.sum()))
    return same + vo


def estimate_dims(px: np.ndarray) -> tuple[int, int] | None:
    """Most plausible (width, height) of a flat sprite: the width (multiple of 16) that
    maximises row-to-row coherence. Validated 18/18 on sprites with known dimensions."""
    n = len(px)
    best = None
    for w in range(16, 1025, 16):
        if n % w == 0 and 1 <= n // w <= 511:
            c = _coherence(px, w)
            if best is None or c > best[0]:
                best = (c, w, n // w)
    return (best[1], best[2]) if best else None


def size_word(w: int, h: int) -> int:
    return ((w // 16) << 9) | h


def patch_size_immediates(p: bytes, ps: m68k.PatchSet, ref_sites: list[int], old_size: int, new_size: int,
                          window: int = 0x400) -> int:
    """Replace `move.w #old_size,dN` / `move.w #old_size,d16(aN)` immediates near the code
    sites that reference the sprite (frame arrays set the size in code)."""
    n = 0
    seen = set()
    for site in ref_sites:
        lo, hi = max(0x110000, site - window), min(0x1C0000, site + window)
        for i in range(lo, hi - 3, 2):
            op = struct.unpack_from(">H", p, i)[0]
            imm = struct.unpack_from(">H", p, i + 2)[0]
            if imm != old_size or i in seen:
                continue
            if (op & 0xF1FF) == 0x303C or (op & 0xF1FF) == 0x317C:   # move.w #imm,dN / move.w #imm,d16(aN)
                ps.add(i + 2, struct.pack(">H", new_size), f"size {old_size:#x}->{new_size:#x} at {i:#x}")
                seen.add(i)
                n += 1
    return n


def ref_sites_for_code(p: bytes, code: int) -> list[int]:
    """Code addresses that reach a sprite code: direct longs in the code region, or `lea`
    (pc-relative or absolute) of a data array containing it."""
    sites = []
    data_hits = []
    for i in range(0, 0x200000 - 3, 2):
        v = struct.unpack_from(">I", p, i)[0]
        if (v & 0x7FFFFF) == code and (v >> 29) == 0:
            (sites if 0x110000 <= i < 0x1C0000 else data_hits).append(i)
    for d in data_hits:
        # array start: walk back over consecutive sprite-code longs
        start = d
        while start - 4 >= 0 and (struct.unpack_from(">I", p, start - 4)[0] >> 29) == 0 and (struct.unpack_from(">I", p, start - 4)[0] & 0x7FFFFF) > 0x100:
            start -= 4
        for i in range(0x110000, 0x1C0000 - 5, 2):
            op = struct.unpack_from(">H", p, i)[0]
            if (op & 0xF1FF) == 0x41FA:                     # lea d16(pc),aN
                disp = struct.unpack_from(">h", p, i + 2)[0]
                if i + 2 + disp == start:
                    sites.append(i)
            elif (op & 0xF1FF) == 0x41F9:                   # lea abs.l,aN
                if struct.unpack_from(">I", p, i + 2)[0] == start:
                    sites.append(i)
    return sites


def redrawn_sprites(disc: Path) -> dict[int, tuple[bytes, bytes]]:
    """id -> (arcade pixels, arrange pixels) for every id whose art differs."""
    out = {}
    banks = {}
    for fn, idx in (("A024", 0x24), ("A023", 0x23)):
        dec = ap.decode_file((disc / "A020" / fn).read_bytes())
        banks[fn] = {e.index: bytes(ap.sprite_pixels(dec, e)) for e in ap.parse(dec, idx)}
    for n, arr in banks["A023"].items():
        arc = banks["A024"].get(n)
        if arc is not None and arc != arr:
            out[n] = (arc, arr)
    return out


def _scan_records(img: bytes, lo: int, hi: int) -> dict[int, list[bytes]]:
    """id -> every 12-byte record tail seen for that id, in scan order (tables repeat the
    same ids with different flags; variants line up by position between arcade/arrange)."""
    recs: dict[int, list[bytes]] = {}
    for off in range(lo, hi - 15, 4):
        sid = struct.unpack_from(">I", img, off)[0]
        if not (1 <= sid < 0x4061):
            continue
        sz = struct.unpack_from(">H", img, off + 12)[0]
        w, h = (sz >> 9) & 0x3F, sz & 0x1FF
        if w and h:
            recs.setdefault(sid, []).append(img[off + 4:off + 16])
    return recs


def arrange_records(elf_path: Path) -> dict[int, list[bytes]]:
    """id -> 12 trailing bytes (x/y, hitbox, size|flags) of its Arrange animation record."""
    e = elfm.Elf(elf_path)
    img = e.read(0x100000, e.segments[0].filesz)
    return _scan_records(img, *RECORD_TABLE_SCAN)


def arcade_records(disc: Path) -> dict[int, list[bytes]]:
    """Same records in the arcade overlay's copy of the 68k tables (used to validate that a
    68k long followed by 12 bytes really is an animation record and not a frame array)."""
    from ..ps2 import overlay
    ov = overlay.Overlay(disc / "A0A0" / "A0A8")
    return _scan_records(ov.image, 0x3F0000, len(ov.image))


SELECT_ROOT_68K = 0x1428DC     # character select: two pointers (P1 / P2 side) to five group pointers
SELECT_ROOT_ELF = 0x2D6328     # the Arrange copy of the same tables (little-endian pointers)
SELECT_GROUPS = 4              # groups 0/1 = the two characters, 2/3 = their confirm pictures


def _select_portraits(p: bytearray, disc: Path, offsets: dict[int, int], remap: dict[int, int]) -> int:
    """Rewrite the character select's portrait groups (a header long, 16-byte records, -1) with
    Arrange's. Arrange rebuilt them: Chihiro / Black are 5 / 6 parts (the arcade's 6 / 9, whose
    extra parts would draw arcade art over them) and each confirm picture is the whole portrait
    drawn twice in two palettes (the arcade's 13 / 9 part Ageha / Tateha composites, whose ids
    Arrange re-used for copies of the new portraits). Groups are rewritten in place (shorter)."""
    e = elfm.Elf(disc / "SLPS_253.52")
    base = 0x100000
    img = e.read(base, e.segments[0].filesz)
    le = lambda a: struct.unpack_from("<I", img, a - base)[0]
    located = None
    n = 0
    for side in range(2):
        tab68 = struct.unpack_from(">I", p, SELECT_ROOT_68K + 4 * side)[0]
        tabps = le(SELECT_ROOT_ELF + 4 * side)
        for k in range(SELECT_GROUPS):
            g68 = struct.unpack_from(">I", p, tab68 + 4 * k)[0]
            gps = le(tabps + 4 * k) - base
            size68 = 4
            while p[g68 + size68:g68 + size68 + 4] != b"\xff\xff\xff\xff":
                size68 += 16
            out = bytearray(img[gps:gps + 4])
            o = gps + 4
            while img[o:o + 4] != b"\xff\xff\xff\xff":
                sid = struct.unpack_from(">I", img, o)[0]
                if sid in offsets:
                    code = remap.get(offsets[sid], offsets[sid])
                else:
                    if located is None:
                        from ..detrans import sprite_ids
                        located = sprite_ids.located()
                    code = located[sid]
                out += struct.pack(">I", code) + img[o + 4:o + 16]
                o += 16
            if len(out) > size68:
                raise ValueError(f"gfx: select group {side}/{k} grew ({len(out)} > {size68} bytes)")
            p[g68:g68 + len(out) + 4] = out + b"\xff\xff\xff\xff"
            n += 1
    return n


# Name entry faces: the PS2's sprite renderer (ELF 0x120AA8) draws sprites 0x3CF9 / 0x3CFB
# (Chihiro) and 0x3CFF / 0x3CFD (Black) from 64x48 pixel blocks in the ELF when the mode is
# Arrange, in place of the bank's Ageha / Tateha faces; the name entry's face table (68k 0x182CAA:
# per character two longs) holds the arcade codes of those numbers. Palette 8 is Arrange's already.
NAME_ENTRY_FACES = {0x182CB2: (0x349AF4, 0x2FE0C0), 0x182CB6: (0x349C7C, 0x2FECC0),
                    0x182CBA: (0x349F8C, 0x2FF8C0), 0x182CBE: (0x349E04, 0x3004C0)}
FACE_W, FACE_H = 64, 48


def _name_entry_faces(p: bytes, disc: Path) -> list[tuple[int, np.ndarray]]:
    e = elfm.Elf(disc / "SLPS_253.52")
    out = []
    for addr, (code, src) in NAME_ENTRY_FACES.items():
        if struct.unpack_from(">I", p, addr)[0] != code:
            raise ValueError(f"gfx: name entry face table at {addr:#x} is not {code:#x}")
        px = np.frombuffer(e.read(src, FACE_W * FACE_H), np.uint8).reshape(FACE_H, FACE_W)
        if px.max() > 0x1F:
            raise ValueError(f"gfx: name entry face {src:#x} is not sprite pixels")
        out.append((addr, np.where(px == 0x1F, 255, px).astype(np.uint8)))
    return out


# Character select wings: the routine 0x1417D6 draws, per side and character, a frame of the
# arcade's wing animation (tables 0x141D16 / 0x141D9A / 0x141EA2 / ..; palette 0xA, zoomed through
# `jsr $114af0` / `jsr $114aba` by the sign of +0x16 / +0x48) around the small flying figure.
# Arrange's copy of the routine (A0A9 0x85EEC0, the arcade's A0A8 0x9A4AE0) is the same code
# without those four calls (the arcade's `jal 0x9A4270` at 0x9A59C0 / 0x9A6E90 / 0x9A8818 /
# 0x9A9C70); its figure draws (sprite 9071, the redrawn strips) stay. Each
# `move.w $nn(a6),d0 ; bgt ; jsr ; bra ; jsr` becomes a branch past both calls.
SELECT_WING_DRAWS = {0x141856: 0x16, 0x141904: 0x16, 0x141A24: 0x48, 0x141AD2: 0x48}


# Character select confirm flip (0x142500, the select object 0x813760): while the slot pictures
# shrink and the confirm picture grows, the arcade also draws a 21x224 effect frame (grown, table
# 0x143020 by the zoom step +0x16; the overlay's 0xC10700, sprites 0xD4A..0xD54: the green
# lightning). Arrange's select code has no such draw (no 0x2AE0 sprite in A0A9 but the flying
# figure's two, table 0xBFD240) and the PS2 shows none: the block becomes a branch past it.
SELECT_FLIP_EFFECT = (0x14258E, "00000000000000000000", 0x1425C8)


def _select_no_flip_effect(p: bytearray) -> None:
    at, want, end = SELECT_FLIP_EFFECT
    if p[at:at + len(want) // 2] != bytes.fromhex(want) or p[end:end + 4] != bytes.fromhex("00000000"):
        raise ValueError(f"gfx: select flip effect draw at {at:#x} is not the expected block")
    p[at:at + 4] = bytes.fromhex("6000") + struct.pack(">h", end - (at + 2))


def _select_no_wings(p: bytearray) -> None:
    for at, field in SELECT_WING_DRAWS.items():
        want = bytes.fromhex(f"302e{field:04x}00000000000000000000000000000000")
        if p[at:at + len(want)] != want:
            raise ValueError(f"gfx: select wing draw at {at:#x} is not the expected call pair")
        p[at:at + 4] = bytes.fromhex("60124e71")        # bra.b at+0x14 (past both calls) ; nop


# Ranking faces: the ranking rows draw a pair of 64x40 faces per entry from the table at 0x13E014
# (by the entry's character word: 0x13E024 character 0, 0x13E02C character 1), in palette 12.
# The PS2 has no Chihiro / Black art for them (its table, ELF 0x2DD818, is the arcade's and its
# renderer overrides only the name entry faces), so Arrange's table rows would show Ageha /
# Tateha. Derived here from the name entry faces above: rows dy..dy+40 (the crop that best fits
# the arcade face's outline), drawn inside the arcade face's outline; palette 12 is loaded from
# the name entry's face palette (0xC77B8, Arrange's) instead of 0xC7EB8 (only the faces use it).
RANKING_FACES = {0x13E024: (0x3606F0, 0x2FE0C0, 8), 0x13E028: (0x360838, 0x2FECC0, 0),
                 0x13E02C: (0x360AC8, 0x2FF8C0, 8), 0x13E030: (0x360980, 0x3004C0, 0)}
RANKING_FACE_H = 40
RANKING_FACE_PALETTE = (0xC7EB8, 0xC77B8, (0x13E412, 0x13E4F4))   # old, new, sites


def _ranking_faces(p: bytes, disc: Path, a_rom: bytes, b_rom: bytes,
                   ps: m68k.PatchSet) -> list[tuple[int, np.ndarray]]:
    e = elfm.Elf(disc / "SLPS_253.52")
    out = []
    for addr, (code, src, dy) in RANKING_FACES.items():
        if struct.unpack_from(">I", p, addr)[0] != code:
            raise ValueError(f"gfx: ranking face table at {addr:#x} is not {code:#x}")
        face = np.frombuffer(e.read(src, FACE_W * FACE_H), np.uint8).reshape(FACE_H, FACE_W)
        face = face[dy:dy + RANKING_FACE_H]
        shape = gfxmod.decode_sprite(a_rom, b_rom, code, FACE_W // 16, RANKING_FACE_H).pixels != 255
        out.append((addr, np.where(shape & (face != 0x1F), face, 255).astype(np.uint8)))
    old, new, sites = RANKING_FACE_PALETTE
    for at in sites:
        if struct.unpack_from(">I", p, at)[0] != old:
            raise ValueError(f"gfx: ranking palette site {at:#x} is not {old:#x}")
        ps.add(at, struct.pack(">I", new), f"ranking faces: palette 12 from {new:#x}")
    return out


# Attract publisher screen: the PS2 shows Arika's logo (blue on black, a 224x448 TIM2 picture in
# A02B, the same as A021's) after the Cave logo where the arcade shows AMI's. The AMI screen's
# draw (0x13F188) is the full-screen background sprite (palette 0) and four AMI sprites from
# 0x13F19E (palette 1); its palette loads (0x13F220: two entries, sources at +8 / +0x16) fade
# palette 1 in from 0xC8078 (AMI's colours) and palette 0 from 0x11F4EE (white). The four AMI draws become one draw of the logo (its colours reduced to 31),
# palette 0 loads black (the background), palette 1 the logo's colours; the timing is the
# arcade's (240 frames).
ARIKA_TIM2 = ("A02B", 0x1A5C00)
AMI_DRAWS = (0x13F19E, 0x13F21E)          # four `move.l / move.l / move.w / move.w / jsr|jmp`
AMI_PALETTE_SOURCES = ((0x13F228, 0xC8078), (0x13F236, 0x11F4EE))   # palette 1, palette 0
DRAW_HELPER = 0x114932


def _arika_screen(state, p: bytes, disc: Path, ps: m68k.PatchSet) -> list[tuple[int, np.ndarray]]:
    from PIL import Image
    want = bytes.fromhex("000000000000000000000000000000000000")
    lo, hi = AMI_DRAWS
    if p[lo:lo + len(want)] != want or p[hi - 6:hi] != bytes.fromhex("000000000000"):
        raise ValueError("gfx: the AMI screen draws are not the expected code")
    for at, src in AMI_PALETTE_SOURCES:
        if struct.unpack_from(">I", p, at)[0] != src:
            raise ValueError(f"gfx: AMI palette source at {at:#x} is not {src:#x}")
    dec = ap.decode_file((disc / "A020" / ARIKA_TIM2[0]).read_bytes())
    pic = ap.tim2_pictures(dec, ARIKA_TIM2[1])[0]
    w, h = pic["width"], pic["height"]
    if (w, h, pic["image_type"], pic["clut_colors"]) != (224, 448, 5, 256):
        raise ValueError("gfx: the Arika TIM2 is not a 224x448 8-bit picture")
    idx = np.frombuffer(dec[pic["image_offset"]:pic["image_offset"] + w * h], np.uint8).reshape(h, w)
    clut = np.frombuffer(dec[pic["clut_offset"]:pic["clut_offset"] + 1024], np.uint8).reshape(256, 4)
    csm1 = np.array([(i & 0xE7) | ((i & 8) << 1) | ((i & 16) >> 1) for i in range(256)])
    rgb5 = (clut[csm1][idx][:, :, :3] >> 3).astype(np.uint8)     # screen orientation, 5 bits a channel
    lit = rgb5.max(2) > 0
    ys, xs = np.nonzero(lit)
    sy0, sy1, sx0, sx1 = ys.min(), ys.max(), xs.min(), xs.max()
    # native orientation: native X = 447 - screen y, native Y = screen x; width padded to 16
    nw = (sy1 - sy0 + 1 + 15) & ~15
    nh = sx1 - sx0 + 1
    x0 = 447 - sy1 - (nw - (sy1 - sy0 + 1)) // 2
    native = np.zeros((nh, nw, 3), np.uint8)
    mask = np.zeros((nh, nw), bool)
    for y in range(nh):
        for x in range(nw):
            sy = 447 - (x0 + x)
            if sy0 <= sy <= sy1 and lit[sy, sx0 + y]:
                native[y, x] = rgb5[sy, sx0 + y]
                mask[y, x] = True
    q = Image.fromarray(native[mask].reshape(-1, 1, 3) * 8).quantize(31, method=Image.Quantize.MEDIANCUT)
    lut = np.array(q.getpalette()[:93], np.uint8).reshape(31, 3) >> 3
    px = np.full((nh, nw), 255, np.uint8)
    px[mask] = np.asarray(q).reshape(-1)
    words = [(int(r) << 10) | (int(g) << 5) | int(b) for r, g, b in lut] + [0]
    pal_logo = state.alloc_p(64, 2, "Arika logo palette")
    pal_black = state.alloc_p(64, 2, "black palette (publisher screen background)")
    ps.add(pal_logo, struct.pack(">32H", *words), "Arika logo palette")
    ps.add(pal_black, bytes(64), "black palette")
    ps.add(AMI_PALETTE_SOURCES[0][0], struct.pack(">I", pal_logo), "publisher screen: palette 1 Arika's")
    ps.add(AMI_PALETTE_SOURCES[1][0], struct.pack(">I", pal_black), "publisher screen: palette 0 black")
    draw = (b"\x22\x3c" + struct.pack(">I", ((x0 * 64) << 16) | (int(sx0) * 64))   # move.l #pos,d1
            + b"\x24\x3c" + b"\0\0\0\0"                                          # move.l #code,d2
            + b"\x36\x3c" + struct.pack(">H", size_word(nw, nh))                 # move.w #size,d3
            + b"\x38\x3c\x00\x01"                                                # move.w #1,d4
            + b"\x4e\xf9" + struct.pack(">I", DRAW_HELPER))                      # jmp helper
    draw += b"\x4e\x71" * ((hi - lo - len(draw)) // 2)
    ps.add(lo, draw, "publisher screen: the Arika logo instead of AMI's")
    state.note(f"gfx: publisher screen: Arika logo {nw}x{nh} at native ({x0}, {int(sx0)}), {len(words) - 1} colours")
    return [(lo + 8, px)]


def _place_sprite(state, px: np.ndarray, a_pos: int, b_pos: int) -> tuple[int, int, int]:
    """Encode `px` into free A / B space: B gets the A-offset header, the mask and the end marker
    the IGS023 needs for vertically flipped sprites (gfxmod.b_end_marker). Returns (sprite code =
    B word, new a_pos, new b_pos)."""
    a_data, b_mask = gfxmod.encode_sprite(px)
    a_pos = (a_pos + 1) & ~1
    b_pos = (b_pos + 1) & ~1
    if a_pos + len(a_data) > len(state.a_rom) or b_pos + 8 + len(b_mask) > len(state.b_rom):
        raise MemoryError("gfx: A/B ROM free space exhausted")
    opaque = int((px != 255).sum())
    state.a_rom[a_pos:a_pos + len(a_data)] = a_data
    state.b_rom[b_pos:b_pos + 4] = gfxmod.b_header(a_pos // 2)
    state.b_rom[b_pos + 4:b_pos + 4 + len(b_mask)] = b_mask
    state.b_rom[b_pos + 4 + len(b_mask):b_pos + 8 + len(b_mask)] = gfxmod.b_end_marker(a_pos // 2, opaque)
    return b_pos // 2, a_pos + len(a_data), b_pos + 8 + len(b_mask)


def run(state) -> None:
    disc = state.work / "ps2" / "disc"
    a_rom = bytes(state.a_rom)
    b_rom = bytes(state.b_rom)
    p = state.p_rom
    cache = state.work / "ps2" / "redrawn_boffsets_cache.json"
    changed = redrawn_sprites(disc)
    offsets: dict[int, int] = {}
    if cache.exists():
        offsets = {int(k): v for k, v in json.loads(cache.read_text()).items()
                   if int(k) in changed and not _blank(np.frombuffer(changed[int(k)][0], np.uint8))}
    for n, (arc, arr) in changed.items():
        if n not in offsets:
            off = locate_sprite(b_rom, a_rom, np.frombuffer(arc, np.uint8))
            if off is None:
                state.note(f"gfx: sprite {n}: original not found in B ROM, skipped")
                continue
            offsets[n] = off
    cache.write_text(json.dumps(offsets))
    recs = arrange_records(disc / "SLPS_253.52")
    arc_recs = arcade_records(disc)
    a_pos = A_FREE_BYTES
    b_pos = B_FREE_BYTES
    ps = m68k.PatchSet()
    remap: dict[int, int] = {}
    deferred = []
    sized: dict[tuple[int, int], tuple] = {}    # (old, new pixel count) -> old / new dims, sites
    for n, (arc, arr) in sorted(changed.items()):
        if n not in offsets:
            continue
        # A sprite whose pixel count changed needs its new display size; that comes from an
        # animation record (ported below) or from code immediates (needs the de-translated
        # Arrange code, not available yet) -> keep the arcade art for the latter for now.
        if len(arc) != len(arr) and n not in recs:
            old_dims = estimate_dims(np.frombuffer(arc, np.uint8))
            new_dims = estimate_dims(np.frombuffer(arr, np.uint8))
            if not old_dims or not new_dims:
                deferred.append(n)
                continue
            sites = ref_sites_for_code(p, offsets[n])
            if not sites:
                deferred.append(n)
                continue
            nsz = patch_size_immediates(bytes(p), ps, sites, size_word(*old_dims), size_word(*new_dims))
            if nsz == 0:
                # a strip of a picture (the ranking portraits) whose width estimate failed: its
                # neighbours in the same code array share the size immediate, already patched
                # for the same pixel counts
                shared = sized.get((len(arc), len(arr)))
                if not shared or not any(abs(s - t) <= 0x400 for s in sites for t in shared[2]):
                    deferred.append(n)
                    continue
                old_dims, new_dims = shared[0], shared[1]
            else:
                sized[(len(arc), len(arr))] = (old_dims, new_dims, sites)
            state.note(f"gfx: sprite {n}: {old_dims[0]}x{old_dims[1]} -> {new_dims[0]}x{new_dims[1]} ({nsz} size immediates)")
        px = np.frombuffer(arr, np.uint8).reshape(-1, 16)
        px = np.where(px == 0x1F, 255, px).astype(np.uint8)
        b_word, a_pos, b_pos = _place_sprite(state, px, a_pos, b_pos)
        remap[offsets[n]] = b_word
    # redirect every 68k reference to the old sprite codes (longs with the old offset in bits 0-22)
    nref = nrec = 0
    # (not the address operands of jumps, calls and leas the build wrote into the original code: a
    # hook's `jmp $2xxxxx` into added code can equal an old sprite code)
    orig = bytes(rom_mod.RomSet(state.work).p_rom_68k()[:0x200000])
    hooks = {0x4EF9, 0x4EB9, 0x41F9, 0x43F9, 0x45F9, 0x47F9, 0x49F9, 0x4BF9, 0x4DF9}
    for i in range(0, 0x200000 - 3, 2):
        v = struct.unpack_from(">I", p, i)[0]
        old = v & 0x7FFFFF
        if old in remap and (v >> 29) == 0:
            if i >= 2 and struct.unpack_from(">H", p, i - 2)[0] in hooks and p[i - 2:i + 4] != orig[i - 2:i + 4]:
                continue
            ps.add(i, struct.pack(">I", (v & ~0x7FFFFF) | remap[old]), f"sprite code {old:#x}->{remap[old]:#x}")
            nref += 1
            # animation record: copy the Arrange record's trailing fields when the id has one
            sid = next((k for k, o in offsets.items() if o == old), None)
            if sid in recs and sid in arc_recs and i + 16 <= len(p):
                tail = p[i + 4:i + 16]
                for k, variant in enumerate(arc_recs[sid]):
                    if tail == variant and k < len(recs[sid]):
                        if recs[sid][k] != variant:
                            ps.add(i + 4, recs[sid][k], f"record fields for sprite {sid} (variant {k})")
                            nrec += 1
                        break
    # sprite codes in the code added by the build (ported Arrange routines, code stage)
    for i in getattr(state, "sprite_sites", ()):
        v = struct.unpack_from(">I", p, i)[0]
        if (v & 0x7FFFFF) in remap and (v >> 29) == 0:
            ps.add(i, struct.pack(">I", (v & ~0x7FFFFF) | remap[v & 0x7FFFFF]), f"sprite code {v:#x} (added code)")
            nref += 1
    # sprite numbers in data added by the build (Arrange characters, chars.py): encode each
    # sprite of the common bank A020 that is not in the PGM ROMs and write its code
    slots = getattr(state, "sprite_number_slots", [])
    if slots:
        from ..detrans import sprite_ids
        located = sprite_ids.located()
        dec = ap.decode_file((disc / "A020" / "A020").read_bytes())
        bank = {e.index: e for e in ap.parse(dec, 0x20)}
        codes: dict[int, int] = {}
        for addr, n in slots:
            if n not in codes:
                if n in located:
                    codes[n] = located[n]
                else:
                    px = np.frombuffer(bytes(ap.sprite_pixels(dec, bank[n])), np.uint8).reshape(-1, 16)
                    px = np.where(px == 0x1F, 255, px).astype(np.uint8)
                    codes[n], a_pos, b_pos = _place_sprite(state, px, a_pos, b_pos)
            ps.add(addr, struct.pack(">I", codes[n]), f"sprite {n:#x} -> code {codes[n]:#x}")
        state.note(f"gfx: {len(codes)} Arrange character sprites ({sum(1 for n in codes if n not in located)} encoded) "
                   f"for {len(slots)} sprite slots")
    # whole pictures added by the code stage (the ending logo): (program ROM long for the code,
    # native-orientation pixels 0..31 / 255 transparent, width a multiple of 16)
    for addr, px in (getattr(state, "custom_sprites", []) + _name_entry_faces(p, disc)
                     + _ranking_faces(p, disc, a_rom, b_rom, ps) + _arika_screen(state, p, disc, ps)):
        code, a_pos, b_pos = _place_sprite(state, px, a_pos, b_pos)
        ps.add(addr, struct.pack(">I", code), f"picture {px.shape[1]}x{px.shape[0]} -> code {code:#x}")
    ps.apply(p)
    nsel = _select_portraits(p, disc, offsets, remap)
    state.note(f"gfx: character select portraits: {nsel} record groups taken from the Arrange table")
    _select_no_wings(p)
    _select_no_flip_effect(p)
    state.note(f"gfx: character select: no wing frames ({len(SELECT_WING_DRAWS)} draws skipped)")
    state.budget["a_rom"] = {"added": a_pos - A_FREE_BYTES}
    state.budget["b_rom"] = {"added": b_pos - B_FREE_BYTES}
    if deferred:
        state.note(f"gfx: {len(deferred)} redrawn sprites deferred (size only known from code): {deferred}")
    state.note(f"gfx: {len(remap)} redrawn sprites re-encoded ({(a_pos-A_FREE_BYTES)/1024:.0f} KB A, "
               f"{(b_pos-B_FREE_BYTES)/1024:.0f} KB B); {nref} code references redirected, {nrec} records updated")
