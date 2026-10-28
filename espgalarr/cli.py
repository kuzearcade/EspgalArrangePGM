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


def cmd_build(args) -> int:
    from . import build as build_mod
    roms_dir, ps2_dir, work = _default_dirs(args)
    inputs_mod.prepare(roms_dir, ps2_dir, work, strict=not args.no_verify)
    out = Path(args.out).resolve()
    state = build_mod.load_state(work, out)
    state.note(f"loaded inputs from {work}")
    stages = [s for s in ("data", "code", "gfx", "music") if s not in (args.skip or [])]
    build_mod.run_stages(state, stages)
    manifest = build_mod.emit(state, build_mod.donor_files(work))
    build_mod.write_budget(state)
    from . import mamepatch
    mamepatch.write_patch(out / "manifest.json", out / "espgalarr-mame.patch")
    mamepatch.write_patch(out / "manifest.json", Path("patches/mame/espgalarr.patch")) if Path("patches/mame").exists() else None
    state.note(f"wrote {len(manifest['files'])} files to {out}")
    return 0


def cmd_rip_gfx(args) -> int:
    """Decode the PS2 sprite/tile banks into PNG sheets under work/ps2/gfx (QA aid)."""
    import numpy as np
    from PIL import Image
    from .ps2 import arika_pak as ap
    _, _, work = _default_dirs(args)
    disc = work / "ps2" / "disc"
    outdir = work / "ps2" / "gfx"
    outdir.mkdir(parents=True, exist_ok=True)
    for fn, idx in (("A020", 0x20), ("A02B", 0x2B)):
        dec = ap.decode_file((disc / "A020" / fn).read_bytes())
        ents = ap.parse(dec, idx)
        cells = []
        for e in ents:
            px = ap.sprite_pixels(dec, e)
            n = len(px)
            w = 16 if n % 16 == 0 and n <= 16 * 64 else 32
            if n % w:
                continue
            cells.append(np.asarray(px, dtype=np.uint8).reshape(-1, w))
        H = sum(c.shape[0] + 1 for c in cells)
        sheet = np.full((H, 32), 0, dtype=np.uint8)
        y = 0
        for c in cells:
            sheet[y:y + c.shape[0], :c.shape[1]] = c * 8
            y += c.shape[0] + 1
        Image.fromarray(sheet).save(outdir / f"{fn}_sprites.png")
        print(f"  {fn}: {len(ents)} sprites -> {outdir / (fn + '_sprites.png')}")
    return 0


def cmd_verify(args) -> int:
    import hashlib, json
    out = Path(args.out).resolve()
    manifest = json.loads((out / "manifest.json").read_text())
    bad = 0
    for name, info in manifest["files"].items():
        data = (out / "roms" / name).read_bytes()
        ok = hashlib.sha1(data).hexdigest() == info["sha1"] and len(data) == info["size"]
        print(f"  {'OK ' if ok else 'BAD'} {name} {info['size']:#x} {info['sha1']}")
        bad += not ok
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="espgalarr")
    ap.add_argument("--root", default=".", help="project root holding mame_roms/, ps2_iso/, work/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="verify inputs, extract into work/, print what was found")
    p.add_argument("--no-verify", action="store_true")
    p.set_defaults(func=cmd_inspect)
    b = sub.add_parser("build", help="generate the Arrange ROM set into --out")
    b.add_argument("--out", default="out")
    b.add_argument("--no-verify", action="store_true")
    b.add_argument("--skip", nargs="*", choices=["data", "code", "gfx", "music"], help="skip stages (dev only)")
    b.set_defaults(func=cmd_build)
    g = sub.add_parser("rip-gfx", help="decode PS2 sprite banks to PNG sheets in work/ps2/gfx")
    g.set_defaults(func=cmd_rip_gfx)
    v = sub.add_parser("verify", help="re-hash an output directory against its manifest")
    v.add_argument("out", nargs="?", default="out")
    v.set_defaults(func=cmd_verify)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
