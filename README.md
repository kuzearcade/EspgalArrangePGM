# espgalarr — Espgaluda PS2 Arrange mode on IGS PGM hardware

`espgalarr` is a build tool. Given the PlayStation 2 *Espgaluda* disc image and the MAME
`espgal`, `espgalbl` and `pgm` ROM sets, it generates a PGM cartridge ROM set that plays the
PS2 **Arrange mode** on original PGM hardware (and in MAME): Arrange gameplay rules, the
changed/new graphics, and the arranged soundtrack streamed through the ICS2115.

This repository contains **only** the tool, our own new 68000/Z80 source, format
documentation and tests. No original game code or assets are stored here; every byte of the
output is derived at build time from the inputs you supply.

## Inputs

```
mame_roms/espgal.zip      parent set (graphics, samples)
mame_roms/espgalbl.zip    bootleg cartridge conversion (plain 68k program + DDP2 donor ARM)
mame_roms/pgm.zip         PGM BIOS
ps2_iso/<Espgaluda (Japan)>.iso | .7z    SLPS-25352
```

## Usage

```
python3 -m espgalarr.cli inspect          # verify inputs, extract into work/, print findings
python3 -m espgalarr.cli build --out out  # generate out/roms, out/dropin/*.zip, manifest.json
#   --sample-rom-max 0x400000 --music-quality test   # 4 MB sample ROM variant for unpatched MAME
python3 -m espgalarr.cli verify out       # re-hash outputs against the manifest
python3 -m pytest tests                   # codec/parser tests (synthetic data only)
```

Dependencies: Python ≥ 3.10, `capstone`, `numpy`, `pillow`, the `7z` CLI, and for the code
stages `m68k-linux-gnu-as` (binutils-m68k) and `pasmo` (Z80). MAME ≥ 0.285 for testing.

## Layout

See `spec/` for the reverse-engineering facts the build relies on, `asm/` for the new 68k
and Z80 code, `patches/mame/` for the MAME driver patch adding the `espgalarr` set, and
`tests/lua/` for the MAME automation scripts. Everything under `work/` and `out/` is
git-ignored.

## Hardware notes

The output is shaped like the `espgalbl` set: it expects a DoDonPachi 2 donor cartridge
(its IGS027A and external ARM data are kept as they are). The program ROM is 4 MB, T/A/B
ROMs keep their original sizes, and the sample ROM may grow up to the 12 MB window the
ICS2115 can address at 0x400000–0xFFFFFF (a larger chip or adapter than the donor's 4 MB).

## MAME

Stock MAME refuses the 12 MB sample ROM (its `espgalbl` definition declares 4 MB), so the build
also writes `out/espgalarr-mame.patch`: a ROM definition and GAME line to add to
`src/mame/igs/pgm.cpp` (plus `espgalarr` in `src/mame/mame.lst`). Rebuild MAME and run
`mame espgalarr -rompath out/dropin`. For quick checks in unpatched MAME use the 4 MB test build
above with `mame espgalbl -rompath out/dropin` (checksum warnings are expected).
