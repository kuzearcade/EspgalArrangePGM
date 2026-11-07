"""Build stage 'data': transplant Arrange data tables into the program ROM.

Sprite palettes whose Arrange copy (anchored through matched PS2 functions) differs from the
ROM, and the stage overlays' embedded tables Arrange changed (diff/stagetables.py: enemy task
timers and bullet counts, speeds, positions).
"""
from __future__ import annotations

from ..diff import palettes as pal_mod
from ..diff import stagetables
from ..pgm import asm68k, m68k


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


# Palettes the mode core recolours (pal_mod) that other code keeps in the arcade colours: the
# stage 1 intro (68k 0x15EA18, the ELF's common 0x256AB0) loads 0xC3E38 into palette 0x12 for
# its launch figure, whose pixels (stage bank A025) are the arcade's; the PS2 holds the original
# for it (RAM 0x60F400 at the launch) and gives the Arrange copy (ELF 0x34F4E0) only to the
# player code (A0A9 0x7D14A0). Those sites get a copy of the original.
KEEP_ARCADE_PALETTE = {0xC3E38: (0x15F09E, 0x1600B8)}
# Stage 1 intro figure (TODO 10f): the intro objects (variant A 0x15F010 / 0x15F242 for character
# 0, B 0x16002A / 0x1602CE for character 2; stage object table 0x14F43E) draw the player's
# pre-awakening figure, arcade art (A025), in palette 0, the player palette, which the arcade's
# player setup (0x117F72) fills with 0xC3D78 / 0xC3E38 and the data stage recolours for Chihiro /
# Black. The PS2 runs the same intro (ELF 0x24B5C0 / 0x260CB0, draws at 0x24F42C..) but in a
# played game fast-forwards it behind the loading screen (spec/arrange-diff.md "Stage start";
# PCSX2: figure drawn frames 3377..3387 only, play counter stalled), and shows it in the attract
# demo with the demo character's arcade colours; the build shows it in play, so the figure came
# out in Chihiro's / Black's colours (pink with a yellow shadow). The figure is drawn in palette 1
# (player 2's palette, unused until player 2's setup, after the figure is gone) holding the
# arcade palette of its variant, loaded with the intro's own palette setup. (variant: palette,
# its init's last palette load `jsr $117e02.l`, the figure's `move.w #0,d4` draws)
INTRO_FIGURE = {"A": (0xC3D78, 0x15F0B2, (0x15F4A0, 0x15F4C4)),
                "B": (0xC3E38, 0x1600CC, (0x160418, 0x16043C))}
INTRO_FIGURE_SLOT = 1


def intro_figure_palette(state, originals: dict[int, bytes]) -> None:
    """Hook the intro inits to load the arcade player palette into INTRO_FIGURE_SLOT and point
    the figure's draws at it (originals: palette address -> the unmodified ROM's 0x40 bytes)."""
    import struct
    p = state.p_rom
    for var, (pal, load, draws) in INTRO_FIGURE.items():
        if not m68k.matches(p[load:load + 6], "@6:14c1188a670be0eb"):
            raise ValueError(f"data: intro {var} palette load at {load:#x} is not jsr $117e02")
        copy = state.alloc_p(0x40, 2, f"intro figure arcade palette {pal:#x}")
        p[copy:copy + 0x40] = originals[pal]
        stub = state.alloc_p(32, 2, f"intro {var} figure palette load")
        code, _ = asm68k.assemble(f"""
        jsr     $117e02.l               ; the original load
        move.l  a0,-(a7)
        move.w  d0,-(a7)
        lea     ${copy:x}.l,a0
        moveq   #{INTRO_FIGURE_SLOT},d0
        jsr     $117e02.l
        move.w  (a7)+,d0
        move.l  (a7)+,a0
        rts
""", stub)
        p[stub:stub + len(code)] = code
        p[load:load + 6] = bytes.fromhex("4eb9") + struct.pack(">I", stub)
        for at in draws:
            if not m68k.matches(p[at:at + 6], "@6:f69c74e7e79d0c08"):
                raise ValueError(f"data: intro {var} figure draw at {at:#x} is not move.w #0,d4 ; jsr")
            struct.pack_into(">H", p, at + 2, INTRO_FIGURE_SLOT)


def run(state) -> None:
    import struct
    changes, same = pal_mod.analyse(state.work)
    intro_figure_palette(state, {pal: bytes(state.p_rom[pal:pal + 0x40]) for pal, _, _ in INTRO_FIGURE.values()})
    for c in changes:
        sites = KEEP_ARCADE_PALETTE.get(c.rom_off, ())
        if not sites:
            continue
        at = state.alloc_p(0x40, 2, f"arcade palette {c.rom_off:#x}")
        state.p_rom[at:at + 0x40] = state.p_rom[c.rom_off:c.rom_off + 0x40]
        for site in sites:
            if struct.unpack_from(">I", state.p_rom, site)[0] != c.rom_off:
                raise ValueError(f"data: palette site {site:#x} is not {c.rom_off:#x}")
            struct.pack_into(">I", state.p_rom, site, at)
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
