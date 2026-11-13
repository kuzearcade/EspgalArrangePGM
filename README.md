# espgalarr — Espgaluda PS2 Arrange mode on IGS PGM hardware

`espgalarr` is a build tool. Given the PlayStation 2 *Espgaluda* disc image and the MAME
`espgal`, `espgalbl` and `pgm` ROM sets, it generates a PGM ROM set that plays the PS2
**Arrange mode**: Arrange's gameplay (ported from the PS2 code), its two characters and redrawn
graphics, and the Arrange soundtrack. It runs on the MiSTer [`IGSPGMPlus` core](https://github.com/kuzearcade/Arcade-IGSPGMPlus_MiSTer/tree/espgalarr) and in a patched
MAME; a variant for plain PGM hardware can be built as well.

This repository contains **only** the tool, our own new 68000/Z80 code, documentation and
tests. No original game code, data or text is stored here (checked by `tools/hygiene_check.py`,
which the tests run); every byte of the output is derived at build time from your inputs.

## What you need

- **Python 3.10 or newer** with three packages: `capstone`, `numpy`, `pillow`.
- **The inputs** (below). Nothing else: the 68000 and Z80 assemblers and the PS2 disc reader are
  part of the tool.
- `7z` only if your disc image is packed as `.7z` / `.zip` (a plain `.iso` is read directly).
- Disk space for the extracted disc and the build caches under `work/` (a few GB), and about
  0.5 GB per output directory.

To play or test the result: the MiSTer [`IGSPGMPlus` core](https://github.com/kuzearcade/Arcade-IGSPGMPlus_MiSTer/tree/espgalarr), or MAME 0.289 built with this
repository's patches (see *Running it*).

Setting up Python (run everything from this repository's folder: the build reads `asm/` from it):

```
python3 -m venv .venv && . .venv/bin/activate     # Debian/Ubuntu: apt install python3-venv first
pip install -e .                                  # the three packages and the `espgalarr` command
```

or, without a virtual environment, the distribution's packages (Debian/Ubuntu:
`apt install python3-capstone python3-numpy python3-pil`) and `python3 -m espgalarr.cli` in place
of `espgalarr` below.

## Inputs

Put them in these folders of the repository (or pass `--root DIR` to use another folder holding
the same layout):

```
mame_roms/espgal.zip      parent set (graphics, sound samples)
mame_roms/espgalbl.zip    bootleg cartridge conversion (plain 68k program); its DDP2 donor ARM
                          ROMs (ddp2_igs027a_japan.bin, v100.u23) are optional
mame_roms/pgm.zip         PGM BIOS
ps2_iso/<any name>.iso    Espgaluda, SLPS-25352 (or a .7z / .zip containing the .iso)
mame_roms/esprade.zip     ESP Ra.De. (MAME set esprade): only for --esprade-chars
```

Every input is checked against its known hashes before anything is built.

## Building

```
espgalarr build                 # or: python3 -m espgalarr.cli build
```

That is all a normal build needs. It extracts the inputs into `work/` (once), makes the files
it derives from them there (the program listing and the de-translated Arrange routines, from the
learned address maps in `espgalarr/data/`), runs the build stages and writes `out/`:

| output | what |
|---|---|
| `out/espgalarr.zip` | the ROM set for the MiSTer (copy to `games/mame/`) |
| `out/dropin/` | `espgalarr.zip`, `espgal.zip`, `espgalbl.zip`, `pgm.zip` for MAME's `-rompath` |
| `out/roms/` | the individual ROM images |
| `out/espgalarr-mame.patch` | MAME's `espgalarr` ROM definition with this build's hashes |
| `out/manifest.json`, `out/budget.md`, `out/build.log` | hashes, ROM space used, build log |

The first build takes longest (it translates Arrange's routines from the PS2 code and caches the
results under `work/`): about four minutes on a 16-core machine, longer with fewer cores. Later
builds take under half a minute. The slow steps run in worker processes on 3/4 of the cores
(at least one); `ESPGAL_JOBS=n espgalarr build` sets the number instead. The output is the same
whatever the number. Deleting `work/` (or a clean clone) also gives the same output: `work/` is
only a cache.

The two sets the release MRAs load (see *Running it*):

```
espgalarr build                    # the PS2 characters: out/espgalarr.zip
espgalarr build --esprade-chars    # ESP Ra.De. characters (needs mame_roms/esprade.zip):
                                   # out_esprade/espgalarr_esprade.zip
```

The two are separate sets: the ESP Ra.De. build writes its own folder (`out_esprade` unless
`--out` says otherwise) and its own zip, and a build refuses an output folder that holds the
other set. (Its `out_esprade/dropin/` holds the same set as `espgalarr.zip`, the name MAME's
`espgalarr` set loads.)

### Build modes

| mode | command | runs on |
|---|---|---|
| **Enhanced** (default) | `espgalarr build` | MiSTer [`IGSPGMPlus` core](https://github.com/kuzearcade/Arcade-IGSPGMPlus_MiSTer/tree/espgalarr); MAME with the patches |
| **Plain PGM** | `espgalarr build --arcade-records --arcade-bullets --arcade-shots --arcade-sprites --bgm ulaw` | a PGM with a 12 MB sample ROM (see *Hardware notes*); MAME with the patches |

The **enhanced** build uses the extra hardware of the MiSTer core and the patched MAME machine:
RAM at 0xD00000 (Arrange's 800-slot bullet pool) and 0xE00000 (128 KB: Arrange's larger enemy
records, player-shot pools and sprite lists), and a stream player that plays the PS2's soundtrack
losslessly (`espgalarr_bgm.bin`, 129 MB, inside `espgalarr.zip`).

The **plain PGM** build keeps the arcade's pool and list sizes (so busy Arrange scenes can run
out of bullet / sprite slots that the PS2 has) and resamples the soundtrack into the ICS2115
sample ROM (8-bit µ-law, one loop per track). It runs in the patched MAME without touching the
enhanced RAM; it has not been run on a real PGM.

The mode flags can also be used one at a time:

| option | effect |
|---|---|
| `--arcade-records` | arcade 0x24-byte enemy records instead of Arrange's 0x2C records in enhanced RAM |
| `--arcade-bullets` | arcade 200-slot bullet pool instead of Arrange's 800 slots (cart RAM at 0xD00000) |
| `--arcade-shots` | arcade 35-record player-shot pools instead of Arrange's 99 |
| `--arcade-sprites` | arcade sprite list sizes instead of Arrange's (enhanced RAM) |
| `--bgm ulaw` | the soundtrack in the sample ROM instead of the lossless stream (enhanced hardware only) |
| `--esprade-chars` | ESP Ra.De.'s characters as the player sprites: J-B 5th as Black, Irori as Chihiro (below) |
| `--out DIR` | output directory (default `out`; `out_esprade` with `--esprade-chars`) |
| `--root DIR` | folder holding `mame_roms/`, `ps2_iso/` and `work/` (default: current folder; give it before `build`) |

**ESP Ra.De. characters** (`--esprade-chars`, needs `mame_roms/esprade.zip`): Black is drawn as
ESP Ra.De.'s J-B 5th and Chihiro as its Irori, with every in-game body animation of theirs: idle,
the leans left / right / up / down with their hold loops and returns, the laser pose for the
Power Shot, the guard barrier poses, and the hit tumble for the respawn. The game is unchanged
(Arrange's states, timing, hitboxes, shots and character art elsewhere: select screen,
portraits, ending); each character's frames get one 32-colour palette made from ESP Ra.De.'s.
Its checksums are listed below with the others. Details: `spec/arrange-characters.md`.

Development options (not needed for a normal build): `--skip STAGE...` (skip build stages
data / layout / code / gfx / music), `--port-routines OVERLAY...` (install only some of the
recovered Arrange routines) and `--no-port-routines` (none: not a complete Arrange game),
`--music-quality test` and `--sample-rom-max BYTES` (smaller / lower-rate soundtrack, for
experiments with the sample ROM size), `--no-verify` (skip the input hash check).

### Expected output

A build is reproducible: the same inputs give byte-identical ROM images. `out/roms/` (and the
same files inside the zips) should match these checksums; `espgalarr verify out` checks a build
against its own manifest. The zip files themselves differ from build to build (they record file
times), so compare the ROM images, not the zips. The `pgm_*` files are the unchanged PGM BIOS.
The checksums belong to this version of the tool; any change to the build changes them (update
them from a build's `out/manifest.json` when that happens).

Enhanced (default) build:

| file | bytes | CRC32 | SHA1 |
|---|---:|---|---|
| `cave_a04801w064.u7` | 8,388,608 | `26dd4932` | `9bbabb5a53cb5ba88397cc2c258980f3b70314ce` |
| `cave_a04802w064.u8` | 8,388,608 | `2c65e9d6` | `d98f2e56876887ebfed703c9e0b4ffb8cda65016` |
| `cave_b04801w064.u1` | 8,388,608 | `3a09e3c5` | `15e68fa2a6208b08011d57643be1b7673626a184` |
| `cave_t04801w064.u19` | 8,388,608 | `6021c79e` | `fbc340dafb18aa3094de29b881318a5a9794e4bc` |
| `cave_w04801b032.u17` | 12,582,912 | `b4385df5` | `6aad8accb6a34e2b2555a0e9bc5ae30767ecfec8` |
| `espgalarr_bgm.bin` | 129,296,640 | `8d4dbf32` | `866bd266924f52f22d70ef2a4207c09db3ca11a6` |
| `espgaluda_u8.bin` | 4,194,304 | `8d3927bc` | `40ca58484670eab7a5417838e40b7185f27fe7ac` |
| `pgm_m01s.rom` | 2,097,152 | `45ae7159` | `d3ed3ff3464557fd0df6b069b2e431528b0ebfa8` |
| `pgm_p01s.u20` | 131,072 | `e42b166e` | `2a9df9ec746b14b74fae48b1a438da14973702ea` |
| `pgm_p02s.u20` | 131,072 | `78c15fa2` | `885a6558e022602cc6f482ac9667ba9f61e75092` |
| `pgm_t01s.rom` | 2,097,152 | `1a7123a0` | `cc567f577bfbf45427b54d6695b11b74f2578af3` |

Enhanced build with the ESP Ra.De. characters (`--esprade-chars`; differs from the default in
the program ROM and the two sprite ROMs that take the new frames):

| file | bytes | CRC32 | SHA1 |
|---|---:|---|---|
| `cave_a04801w064.u7` | 8,388,608 | `26dd4932` | `9bbabb5a53cb5ba88397cc2c258980f3b70314ce` |
| `cave_a04802w064.u8` | 8,388,608 | `e32702cd` | `b2066df357b661c670179e9c0d88fbdbeee77ae6` |
| `cave_b04801w064.u1` | 8,388,608 | `a6b70de4` | `e8ca97789f39dfba40953458153894cd3721945c` |
| `cave_t04801w064.u19` | 8,388,608 | `6021c79e` | `fbc340dafb18aa3094de29b881318a5a9794e4bc` |
| `cave_w04801b032.u17` | 12,582,912 | `b4385df5` | `6aad8accb6a34e2b2555a0e9bc5ae30767ecfec8` |
| `espgalarr_bgm.bin` | 129,296,640 | `8d4dbf32` | `866bd266924f52f22d70ef2a4207c09db3ca11a6` |
| `espgaluda_u8.bin` | 4,194,304 | `34abbc48` | `c293b1b3191436830487ce7c2cb7870494688a62` |
| `pgm_m01s.rom` | 2,097,152 | `45ae7159` | `d3ed3ff3464557fd0df6b069b2e431528b0ebfa8` |
| `pgm_p01s.u20` | 131,072 | `e42b166e` | `2a9df9ec746b14b74fae48b1a438da14973702ea` |
| `pgm_p02s.u20` | 131,072 | `78c15fa2` | `885a6558e022602cc6f482ac9667ba9f61e75092` |
| `pgm_t01s.rom` | 2,097,152 | `1a7123a0` | `cc567f577bfbf45427b54d6695b11b74f2578af3` |

Plain PGM build (`--arcade-records --arcade-bullets --arcade-shots --arcade-sprites --bgm ulaw`):

| file | bytes | CRC32 | SHA1 |
|---|---:|---|---|
| `cave_a04801w064.u7` | 8,388,608 | `26dd4932` | `9bbabb5a53cb5ba88397cc2c258980f3b70314ce` |
| `cave_a04802w064.u8` | 8,388,608 | `e722257b` | `b4ba4fe08e911541295c2b8b445db1bf68837648` |
| `cave_b04801w064.u1` | 8,388,608 | `da47fe9b` | `10db594566c92750ad395ec86606ab54906850f7` |
| `cave_t04801w064.u19` | 8,388,608 | `6021c79e` | `fbc340dafb18aa3094de29b881318a5a9794e4bc` |
| `cave_w04801b032.u17` | 12,582,912 | `d5b046d2` | `ea259a4774506b5d3e1d7fc4ef197daa145913bf` |
| `espgaluda_u8.bin` | 4,194,304 | `419ebf9b` | `4fbbe0bd5e802c7268d9895f52cb4f4a6214d58c` |
| `pgm_m01s.rom` | 2,097,152 | `45ae7159` | `d3ed3ff3464557fd0df6b069b2e431528b0ebfa8` |
| `pgm_p01s.u20` | 131,072 | `e42b166e` | `2a9df9ec746b14b74fae48b1a438da14973702ea` |
| `pgm_p02s.u20` | 131,072 | `78c15fa2` | `885a6558e022602cc6f482ac9667ba9f61e75092` |
| `pgm_t01s.rom` | 2,097,152 | `1a7123a0` | `cc567f577bfbf45427b54d6695b11b74f2578af3` |

### Other commands

```
espgalarr inspect      # check and extract the inputs, print the disc's files and the ROMs' free space
espgalarr verify out   # re-hash an output directory against its manifest
espgalarr rip-gfx      # decode the PS2 sprite banks to PNG sheets in work/ps2/gfx
python3 -m pytest tests
```

## Running it

**MiSTer.** Use the `IGSPGMPlus` core from the core repository
([`Arcade-IGSPGMPlus_MiSTer`](https://github.com/kuzearcade/Arcade-IGSPGMPlus_MiSTer), branch `espgalarr`, folder
[`releases/espgalarr/`](https://github.com/kuzearcade/Arcade-IGSPGMPlus_MiSTer/tree/espgalarr/releases/espgalarr)): copy the `.rbf` to `_Arcade/cores/` and the
MRAs to `_Arcade/`, and the build's zip to `games/mame/` under the name its MRA loads:

| MRA | zip in `games/mame/` | built by |
|---|---|---|
| `Espgaluda Arrange (PS2 conversion).mra` | `espgalarr.zip` | `espgalarr build` (`out/espgalarr.zip`) |
| `Espgaluda Arrange (ESP Ra.De. characters).mra` | `espgalarr_esprade.zip` | `espgalarr build --esprade-chars` (`out_esprade/espgalarr_esprade.zip`) |

Each MRA carries the CRC32 of every file of its set (the tables above), so MiSTer reports a zip
from another build or version. The lossless soundtrack (`espgalarr_bgm.bin`) is inside the zip
and the MRA loads it from there: there is nothing else to copy (it makes loading the game about
11 seconds longer). The core never conflicts with the
stock PGM core (its file is named `IGSPGMPlus_<date>.rbf`).

**MAME.** In a MAME 0.289 source tree:

1. `git apply patches/mame/enhanced-sprites.diff` (the enhanced hardware: RAM at 0xE00000, the
   extended sprite list and the BGM stream player);
2. add the `espgalarr` set from your build's `out/espgalarr-mame.patch` to
   `src/mame/igs/pgm.cpp` as its header says (plus `espgalarr` in `src/mame/mame.lst`);
   `patches/mame/espgalarr.patch` is a copy from one reference build;
3. rebuild MAME and run `mame espgalarr -rompath out/dropin`. The soundtrack comes from
   `espgalarr_bgm.bin` inside `out/dropin/espgalarr.zip`; the set marks it optional, so a plain
   PGM build (which has none and keeps its music in the sample ROM) loads too.

Stock MAME cannot run the result: it has no `espgalarr` set, its `espgalbl` set expects a 4 MB
sample ROM, and the enhanced build needs the extra hardware.

## Hardware notes

The output is shaped like the `espgalbl` set: a DoDonPachi 2 donor cartridge, whose IGS027A the
program never uses (the bootleg simulates its protection in 68k code). The `espgalarr` set (the
MiSTer core and the patched MAME machine) carries no ARM ROMs; only the espgalbl-shaped drop-in
keeps them. The program ROM is 4 MB and the T/A/B ROMs keep their original sizes; the sample ROM
grows to the 12 MB window the ICS2115 can address at 0x400000–0xFFFFFF (a larger chip or an
adapter than the donor's 4 MB), in both modes.

Graphics headroom: the Arrange sprites take about 0.5 MB of the A ROMs and 0.38 MB of the B ROM,
inside the free space of the 16 MB A / 8 MB B images, so no graphics ROM grows. The format
allows more (PGM carts use up to 32 MB of A and 16 MB of B ROM). `out/budget.md` shows what is
left.

## Repository layout

`espgalarr/` the build tool, `asm/` the new 68k and Z80 code, `spec/` the reverse-engineering
facts the build relies on, `patches/mame/` the MAME patches, `patches/pcsx2/` the PCSX2 trace
patch used for the PS2 comparisons, `tools/` the comparison and audit tools
(`tools/compare/README.md`), `tests/` the tests and the MAME Lua scripts. The inputs, `work/`
(extracted data, caches, local notes) and every `out*/` directory are git-ignored, as are the
local working notes `TODO.md` and `STATUS.md`.

## Legal

This repository contains no copyrighted game code, data, graphics, sound or text: only the
build tool, new code written for this project, documentation and tests. Everything the tool
outputs is derived at build time from inputs you supply. *Espgaluda* and its PlayStation 2
Arrange mode are the property of their respective rights holders (CAVE, ARIKA and their
licensors); all trademarks and copyrights belong to their respective owners. This project is
not affiliated with or endorsed by any of them.

This tool is meant only for people who own both an original copy of the PlayStation 2
*Espgaluda* (SLPS-25352) and an official *Espgaluda* arcade PCB, and who make the inputs from
their own copies. Do not distribute the generated ROM set, the input ROMs or the disc image.
