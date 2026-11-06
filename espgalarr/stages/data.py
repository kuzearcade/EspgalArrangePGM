"""Build stage 'data': transplant Arrange data tables into the program ROM.

Sprite palettes whose Arrange copy (anchored through matched PS2 functions) differs from the
ROM, and the stage overlays' embedded tables Arrange changed (diff/stagetables.py: enemy task
timers and bullet counts, speeds, positions).
"""
from __future__ import annotations

from ..diff import palettes as pal_mod
from ..diff import stagetables
from ..pgm import m68k


# Character select palette loads (68k format: a count word, then per entry `colour.w slot.l
# source.l count.w mode.w`, or `slot.l source.l count.w mode.w` for the fade-to-white groups).
# Arrange rebuilt the ten groups (highlight P1 A/B, P2 A/B; white P1/P2; confirm P1 A/B, P2 A/B:
# the confirm groups load only the glow palette of the confirm picture's second record) and keeps
# them at ELF 0x2D6650.., padded to 16 / 4 bytes. Its source pointers are 0xFFFFFFFF on disc and
# filled by the loader; the values (all palettes in the ELF image) were read from PCSX2 RAM.
SELECT_PAL_GROUPS_68K = (0x142E54, 0x142E72, 0x142E90, 0x142EAE, 0x142ECC, 0x142EFE,
                         0x142F30, 0x142F5C, 0x142F88, 0x142FB4)
SELECT_PAL_AREA_END = 0x142FE0          # the next table
SELECT_PAL_GROUPS_ELF = (0x2D6650, 0x2D6670, 0x2D6690, 0x2D66C0, 0x2D66F0, 0x2D6730,
                         0x2D6770, 0x2D6780, 0x2D6790, 0x2D67A0)
SELECT_PAL_SOURCES = (                  # per Arrange group, the loader's source pointers
    (0x351220, 0x351260), (0x3512A0, 0x3512E0), (0x3532A0, 0x351220, 0x351260),
    (0x3532E0, 0x3512A0, 0x353320), (0x2DF070,) * 4, (0x2DF070,) * 4,
    (0x3532A0,), (0x3532E0,), (0x3532A0,), (0x3532E0,))
SELECT_PAL_WHITE = (4, 5)               # groups whose entries have no colour word


def select_palettes(state) -> int:
    """Replace the select screen's palette-load groups with Arrange's (and its palettes)."""
    import struct
    from ..ps2 import elf as elfm
    p = state.p_rom
    e = elfm.Elf(state.work / "ps2" / "disc" / "SLPS_253.52")
    base = 0x100000
    img = e.read(base, e.segments[0].filesz)
    copies: dict[int, int] = {}
    groups = []
    for k, (g, srcs) in enumerate(zip(SELECT_PAL_GROUPS_ELF, SELECT_PAL_SOURCES)):
        n = struct.unpack_from(">H", img, g - base)[0]
        esz = 12 if k in SELECT_PAL_WHITE else 14
        if n != len(srcs):
            raise ValueError(f"select palette group {g:#x}: {n} entries, expected {len(srcs)}")
        out = bytearray(img[g - base:g - base + 2 + esz * n])
        for i, src in enumerate(srcs):
            at = 2 + esz * i + (esz - 8)            # the source long precedes count.w mode.w
            if out[at:at + 4] != b"\xff\xff\xff\xff":
                raise ValueError(f"select palette group {g:#x}: entry {i} is not a loader slot")
            count = struct.unpack_from(">H", out, at + 4)[0] + 1
            if src not in copies:
                data = img[src - base:src - base + 2 * count]
                if any(struct.unpack_from(">H", data, j)[0] > 0x7FFF for j in range(0, len(data), 2)):
                    raise ValueError(f"select palette {src:#x} is not a palette")
                copies[src] = state.alloc_p(len(data), 2, f"select palette {src:#x}")
                p[copies[src]:copies[src] + len(data)] = data
            struct.pack_into(">I", out, at, copies[src])
        groups.append(out)
    if sum(len(g) for g in groups) > SELECT_PAL_AREA_END - SELECT_PAL_GROUPS_68K[0]:
        raise ValueError("select palette groups do not fit the arcade area")
    # pack the groups into the arcade area and repoint each `lea d16(pc),aN` of a group
    new = {}
    at = SELECT_PAL_GROUPS_68K[0]
    for old, g in zip(SELECT_PAL_GROUPS_68K, groups):
        new[old] = at
        p[at:at + len(g)] = g
        at += len(g)
    p[at:SELECT_PAL_AREA_END] = bytes(SELECT_PAL_AREA_END - at)
    nlea = 0
    for i in range(0x140000, 0x144000, 2):
        if (struct.unpack_from(">H", p, i)[0] & 0xF1FF) == 0x41FA:
            t = i + 2 + struct.unpack_from(">h", p, i + 2)[0]
            if t in new:
                struct.pack_into(">h", p, i + 2, new[t] - (i + 2))
                nlea += 1
    if nlea != len(SELECT_PAL_GROUPS_68K):
        raise ValueError(f"select palette groups: {nlea} lea sites, expected {len(SELECT_PAL_GROUPS_68K)}")
    return len(copies)


def run(state) -> None:
    changes, same = pal_mod.analyse(state.work)
    ps = m68k.PatchSet()
    for c in changes:
        ps.add(c.rom_off, c.data, f"palette {c.rom_off:#x} from {c.arrange_addr:#x}")
    ps.apply(state.p_rom)
    state.note(f"data: {len(changes)} palettes replaced ({same} unchanged): "
               + ", ".join(f"{c.rom_off:#x}" for c in changes))
    npal = select_palettes(state)
    state.note(f"data: character select palette loads from Arrange ({npal} palettes)")
    tables, skipped = stagetables.analyse(state.work)
    ps = m68k.PatchSet()
    for t in tables:
        ps.add(t.rom_off, t.arrange, f"stage table {t.rom_off:#x} from {t.overlay}:{t.arrange_addr:#x}")
    ps.apply(state.p_rom)
    state.note(f"data: {len(tables)} stage tables replaced ({len(skipped)} skipped): "
               + ", ".join(f"{t.rom_off:#x}" for t in tables))
