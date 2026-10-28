"""espgalarr command line: inspect / build / verify."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import inputs as inputs_mod


def _default_dirs(args) -> tuple[Path, Path, Path]:
    root = Path(args.root).resolve()
    return root / "mame_roms", root / "ps2_iso", root / "work"


def cmd_inspect(args) -> int:
    from .ps2 import afs as afs_mod, adx as adx_mod, overlay as ov_mod
    from .pgm import rom as rom_mod

    roms_dir, ps2_dir, work = _default_dirs(args)
    inp = inputs_mod.prepare(roms_dir, ps2_dir, work, strict=not args.no_verify)
    disc = inp.disc_dir
    print("== PS2 overlays (MWo3)")
    for name, ov in ov_mod.load_all(disc).items():
        h = ov.header
        print(f"  {ov.path.name}: id={h.module_id:#04x} {name:16s} load={h.load_addr:#08x} "
              f"text={h.text_size:#x} data={h.data_size:#x} bss={h.bss_size:#x}")
    print("== AFS archives")
    for rel in ("A980/A980", "A980/A981"):
        a = afs_mod.Afs(disc / rel)
        total = 0.0
        print(f"  {rel}: {len(a)} entries")
        for e in a.entries:
            hdr = a.read(e.index)[:0x100]
            try:
                info = adx_mod.parse_header(hdr)
            except ValueError:
                print(f"    [{e.index:2d}] {e.size:9d} bytes (not ADX)")
                continue
            total += info.duration
            loop = f"loop {info.loop_start}-{info.loop_end}" if info.loop else "no loop"
            print(f"    [{e.index:2d}] {e.size:9d} B  {info.channels}ch {info.sample_rate}Hz "
                  f"{info.duration:6.1f}s  {loop}")
        print(f"    total {total/60:.1f} min")
        a.close()
    print("== PGM ROM free space (runs >= 64 KB of 0x00/0xFF)")
    rs = rom_mod.RomSet(work)
    for key in ("P", "T", "A0", "A1", "B", "M"):
        data = rs.read(key)
        runs = rom_mod.free_runs(data)
        free = sum(r.size for r in runs)
        desc = ", ".join(f"{r.start:#x}-{r.end:#x}" for r in runs)
        print(f"  {key:2s} {len(data)/1048576:4.1f} MB  free {free/1048576:5.2f} MB  {desc}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="espgalarr")
    ap.add_argument("--root", default=".", help="project root holding mame_roms/, ps2_iso/, work/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="verify inputs, extract into work/, print what was found")
    p.add_argument("--no-verify", action="store_true")
    p.set_defaults(func=cmd_inspect)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
