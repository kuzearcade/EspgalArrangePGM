"""Combined PS2<->68k routine anchoring: ROM-table references (strong), game-state RAM
variables (medium) and immediate constants (weak), plus a semantic-constant extractor
that ignores address material (lui/addiu pairs, $gp/$sp/$fp offsets)."""
from __future__ import annotations

import bisect
import collections
import json
from dataclasses import dataclass
from pathlib import Path

from ..pgm import rom as rom_mod
from ..ps2 import overlay
from . import datablock as db, mips_funcs, rammap

IMM_OPS = {0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu", 0x0C: "andi", 0x0D: "ori", 0x0E: "xori"}


def semantic_consts(f: mips_funcs.Function) -> list[tuple[int, str, int]]:
    """(byte offset, op, value) for immediates that are not address material."""
    out = []
    w = f.words
    lui_reg: dict[int, int] = {}      # reg -> index of last lui
    for i, x in enumerate(w):
        op = x >> 26
        rt = (x >> 16) & 31
        rs = (x >> 21) & 31
        if op == 0x0F:
            lui_reg[rt] = i
            continue
        if op in IMM_OPS:
            imm = x & 0xFFFF
            v = imm if op in (0x0C, 0x0D, 0x0E) else (imm - 0x10000 if imm >= 0x8000 else imm)
            addr_like = rs in (28, 29, 30) or (rs in lui_reg and i - lui_reg[rs] <= 12)
            if not addr_like:
                out.append((i * 4, IMM_OPS[op], v))
        # any write to rt invalidates a pending lui
        if op in IMM_OPS or op in (0x20, 0x21, 0x23, 0x24, 0x25):
            if rt in lui_reg and not (op in (0x09, 0x0D) and rs == rt):
                lui_reg.pop(rt, None)
    return out


@dataclass
class Anchoring:
    funcs_a: list
    funcs_b: list
    diff: mips_funcs.DiffResult
    rom_refs_a: dict[int, set[int]]       # arcade fn start -> ROM offsets referenced
    ram_a: dict[int, set[int]]            # arcade fn start -> 68k RAM addrs
    ram_b: dict[int, set[int]]
    routines: dict[int, dict]


def build(work: Path) -> Anchoring:
    p = rom_mod.RomSet(work).p_rom_68k()[:0x200000]
    ovs = overlay.load_all(work / "ps2" / "disc")
    A, R = ovs["ov_arcade.bin"], ovs["ov_arrange.bin"]
    fa = mips_funcs.split_functions(A.image, A.image_base, A.load_addr + A.header.text_size)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.load_addr + R.header.text_size)
    diff = mips_funcs.match(fa, fb)
    sa = db.match_segments(p, A.image, 0, 0x200000, min_len=48)
    sa.sort(key=lambda x: x.ov_off)
    alo = [x.ov_off for x in sa]

    def rom_of(addr):
        o = addr - A.image_base
        i = bisect.bisect_right(alo, o) - 1
        if i >= 0 and sa[i].ov_off <= o < sa[i].ov_end:
            return sa[i].rom_off + (o - sa[i].ov_off)
        return None
    rom_refs = {f.start: {r for r in (rom_of(x) for x in f.refs) if r is not None} for f in fa}
    m68 = set(int(l[:6], 16) for l in open(work / "pgm" / "notes_ram_xref.txt"))
    ram = {}
    for key, ov, funcs in (("a", A, fa), ("b", R, fb)):
        refs = rammap.ps2_addresses(ov.image, ov.image_base)
        blocks = rammap.find_blocks(refs, m68)
        starts = sorted(f.start for f in funcs)
        per = collections.defaultdict(set)
        for addr, sites in refs.items():
            t = rammap.translate(addr, blocks)
            if t is None or not (0x800000 <= t < 0x820000):
                continue
            for s in sites:
                i = bisect.bisect_right(starts, s) - 1
                if i >= 0:
                    per[starts[i]].add(t)
        ram[key] = per
    routines = {int(k, 16): v for k, v in json.loads((work / "pgm" / "routines.json").read_text()).items()}
    return Anchoring(fa, fb, diff, rom_refs, ram["a"], ram["b"], routines)


def score_68k(anch: Anchoring, fn_a: mips_funcs.Function, consts: list[int] | None = None) -> list[tuple[float, int]]:
    """Rank 68k routines for an arcade PS2 function."""
    out = []
    roms = anch.rom_refs_a.get(fn_a.start, set())
    rams = anch.ram_a.get(fn_a.start, set())
    cs = collections.Counter(c for c in (consts or []) if 0x10 <= c < 0x8000)
    for s, v in anch.routines.items():
        sr = set(v["rom"])
        sv = set(v["ram"])
        sc = collections.Counter(x for x in v["imms"] if 0x10 <= x < 0x8000)
        score = 3.0 * len(roms & sr) + 1.0 * len(rams & sv) + 0.5 * sum(min(cs[k], sc[k]) for k in cs)
        if score > 0:
            out.append((score, s))
    out.sort(reverse=True)
    return out[:5]


def ps2_for_68k(anch: Anchoring, routine: int) -> list[tuple[float, mips_funcs.Function]]:
    """Rank arcade PS2 functions for a 68k routine (same signals, reversed)."""
    v = anch.routines[routine]
    sr, sv = set(v["rom"]), set(v["ram"])
    out = []
    for f in anch.funcs_a:
        score = 3.0 * len(anch.rom_refs_a.get(f.start, set()) & sr) + 1.0 * len(anch.ram_a.get(f.start, set()) & sv)
        if score > 0:
            out.append((score, f))
    out.sort(key=lambda x: -x[0])
    return out[:5]
