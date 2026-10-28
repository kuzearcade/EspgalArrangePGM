"""Turn de-translated Arrange routines into program-ROM patches.

Near-matched routines (same structure as an arcade routine, constants changed): the arcade
twin is de-translated and aligned to the real 68k routine by the verifier, the Arrange twin is
de-translated and aligned to the arcade one instruction by instruction, and every immediate
that differs is rewritten inside the original 68k instruction's encoding. Routines whose
structure differs are reported for replacement (reassembly of the recovered source).
"""
from __future__ import annotations

import bisect
import collections
import difflib
import json
import struct
from dataclasses import dataclass, field, asdict
from pathlib import Path

from ..diff import datablock as db, mips_funcs
from ..pgm import rom as rom_mod
from ..ps2.overlay import Overlay
from . import core
from .apply import ArrangeMap, is_translated, _reader
from .verify import Index, LearnedMap, compare_function, load_listing, recovered_canon

WORK = Path("work")
OUT = WORK / "ps2" / "detrans"


@dataclass
class ImmPatch:
    rom_addr: int        # 68k instruction address
    nbytes: int
    offset: int          # byte offset of the immediate inside the instruction
    width: int           # 1 (opcode-embedded), 2 or 4
    old: int
    new: int
    mnem: str
    routine: int         # 68k routine (arcade twin's original)
    arrange_fn: int
    kind: str = "ext"    # ext | moveq | quick


@dataclass
class RoutineReport:
    arrange_fn: int
    arcade_fn: int
    m68k: int | None
    coverage: float
    imm_patches: int = 0
    pending: list[str] = field(default_factory=list)
    structural: int = 0


def _arrange_map(p: bytes, A: Overlay, R: Overlay, diff, lm: LearnedMap) -> ArrangeMap:
    segs = db.match_segments(p, R.image, 0, 0x200000, min_len=48)
    samples: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    funcs: dict[int, int] = {}
    for m in diff.matches:
        if len(m.a.refs) == len(m.b.refs):
            for x, y in zip(m.a.refs, m.b.refs):
                t, kind, conf = lm.map_const(x)
                if t is not None and conf >= 0.9:
                    samples[y][t] += 1
        if m.a.start in lm.funcs:
            funcs[m.b.start] = lm.funcs[m.a.start]
    for k, v in lm.funcs.items():
        if k < A.image_base:
            funcs.setdefault(k, v)
    flat = {y: c.most_common(1)[0][0] for y, c in samples.items() if len(c) == 1 or c.most_common(1)[0][1] >= 2}
    return ArrangeMap(flat, funcs, segs, R.image_base)


def _shape_key(c) -> str:
    """Alignment key: mnemonic/size plus operand kinds, with immediates (and addresses)
    abstracted so that constant changes still align."""
    parts = [c.mnem + "." + c.size]
    for op in c.ops:
        parts.append(op[0] if op[0] in ("imm", "abs", "label", "func", "pc") else "|".join(str(x) for x in op))
    return " ".join(parts)


def _imm_values(c) -> list[tuple[int, int, str]]:
    """(operand index, value, kind) of immediate and d16-displacement operands of a Canon."""
    out = []
    for i, op in enumerate(c.ops):
        if op[0] == "imm":
            out.append((i, op[1], "imm"))
        elif op[0] == "d16":
            out.append((i, op[1], "d16"))
    return out


def _encode_patch(p: bytes, k68, size: str, old: int, new: int, disp: bool = False) -> ImmPatch | None:
    raw = p[k68.addr:k68.addr + k68.nbytes]
    op = struct.unpack_from(">H", raw, 0)[0]
    mnem = k68.mnem.split(".")[0]
    if disp:
        if not (-32768 <= new <= 32767):
            return None
        enc_old = struct.pack(">h", old if old < 0x8000 else old - 0x10000)
        enc_new = struct.pack(">h", new)
        hits = [o for o in range(2, k68.nbytes - 1, 2) if raw[o:o + 2] == enc_old]
        if len(hits) != 1:
            return None
        return ImmPatch(k68.addr, k68.nbytes, hits[0], 2, old, new, k68.mnem, 0, 0, "d16")
    # moveq: 8-bit immediate in the opcode
    if (op & 0xF100) == 0x7000:
        if -128 <= new <= 127 and (raw[1] == (old & 0xFF)):
            return ImmPatch(k68.addr, k68.nbytes, 1, 1, old, new, k68.mnem, 0, 0, "moveq")
        return None
    # addq/subq: 3-bit count in bits 9-11
    if (op & 0xF100) == 0x5000 and (op & 0xC0) != 0xC0:
        if 1 <= new <= 8 and ((op >> 9) & 7 or 8) == old:
            return ImmPatch(k68.addr, k68.nbytes, 0, 1, old, new, k68.mnem, 0, 0, "quick")
        return None
    if new > 0x7FFFFFFF:
        new -= 0x100000000
    if old > 0x7FFFFFFF:
        old -= 0x100000000
    first = {"b": 2, "w": 2, "l": 4}.get(size, 2)
    for width in ([first] + [w for w in (2, 4) if w != first]):
        mask = (1 << (8 * width)) - 1
        if width == 2:
            if not (-32768 <= new <= 65535):
                continue
            enc_old = ((old & 0xFF) if size == "b" else (old & 0xFFFF)).to_bytes(2, "big")
            enc_new = ((new & 0xFF) if size == "b" else (new & 0xFFFF)).to_bytes(2, "big")
        else:
            enc_old = (old & mask).to_bytes(4, "big")
            enc_new = (new & mask).to_bytes(4, "big")
        hits = [o for o in range(2, k68.nbytes - width + 1, 2) if raw[o:o + width] == enc_old]
        if len(hits) == 1:
            return ImmPatch(k68.addr, k68.nbytes, hits[0], width, old, new, k68.mnem, 0, 0, "ext")
    return None


def port_near(limit: int | None = None, verbose: bool = False):
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    A = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A8")
    R = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A9")
    fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
    diff = mips_funcs.match(fa, fb)
    lm = LearnedMap.load(OUT / "addrmap_arcade.json")
    amap = _arrange_map(p, A, R, diff, lm)
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    patches: list[ImmPatch] = []
    reports: list[RoutineReport] = []
    n = 0
    for m in diff.matches:
        if m.kind != "near":
            continue
        code_a = A.read(m.a.start, m.a.size)
        code_b = R.read(m.b.start, m.b.size)
        if not is_translated(code_a) or not is_translated(code_b):
            continue
        try:
            ra = core.detranslate(code_a, m.a.start, lm, read_word=_reader(A))
            rb = core.detranslate(code_b, m.b.start, amap, read_word=_reader(R))
        except Exception as e:  # noqa: BLE001
            reports.append(RoutineReport(m.b.start, m.a.start, None, 0.0, pending=[f"detranslate failed: {type(e).__name__}"]))
            continue
        rep = compare_function(index, ra)
        rr = RoutineReport(m.b.start, m.a.start, rep.anchor, rep.coverage)
        k68_of = {id(ins): k for ins, k in rep.pairs}
        ia = [(ins, recovered_canon(ins)) for ins in ra.insns if not ins.is_label and ins.mnem]
        ib = [(ins, recovered_canon(ins)) for ins in rb.insns if not ins.is_label and ins.mnem]
        ka = [_shape_key(c) if c else "?" for _, c in ia]
        kb = [_shape_key(c) if c else "?" for _, c in ib]
        sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks())
        rr.structural = (len(ka) - matched) + (len(kb) - matched)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                (ins_a, ca), (ins_b, cb) = ia[blk.a + k], ib[blk.b + k]
                if ca is None or cb is None or ca.text == cb.text:
                    continue
                va, vb = _imm_values(ca), _imm_values(cb)
                if len(va) != len(vb):
                    continue
                for (i1, old, kind), (i2, new, kind2) in zip(va, vb):
                    if old == new or kind != kind2:
                        continue
                    k68 = k68_of.get(id(ins_a))
                    if k68 is None:
                        rr.pending.append(f"{ins_a.text().strip()} -> {new:#x}: not aligned to ROM")
                        continue
                    pt = _encode_patch(p, k68, "w" if kind == "d16" else ins_a.size, old, new, disp=(kind == "d16"))
                    if pt is None:
                        rr.pending.append(f"{k68.mnem} {k68.ops} @ {k68.addr:#x}: {old:#x}->{new:#x} not encodable")
                        continue
                    pt.routine = rep.anchor or 0
                    pt.arrange_fn = m.b.start
                    patches.append(pt)
                    rr.imm_patches += 1
        reports.append(rr)
        n += 1
        if verbose and (rr.imm_patches or rr.structural):
            print(f"{m.b.start:#x}<-{m.a.start:#x} rom {rep.anchor and hex(rep.anchor)} cov {rep.coverage:.2f} "
                  f"imm {rr.imm_patches} structural {rr.structural} pending {len(rr.pending)}")
        if limit and n >= limit:
            break
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "port_patches.json").write_text(json.dumps([asdict(x) for x in patches], indent=0))
    (OUT / "port_report.json").write_text(json.dumps([asdict(x) for x in reports], indent=0))
    return patches, reports


if __name__ == "__main__":
    import sys
    ps, rs = port_near(verbose="-v" in sys.argv)
    print(f"routines {len(rs)}, immediate patches {len(ps)}, structural-change routines "
          f"{sum(1 for r in rs if r.structural)}, pending items {sum(len(r.pending) for r in rs)}")
