"""De-translate the Arrange overlay.

For every function of ov_arrange.bin: functions that `mips_funcs.match` pairs *exactly* with
an arcade function whose 68k original is known are reported as unchanged; the others
(near matches and arrange-only code) are de-translated into
work/ps2/detrans/arrange_<addr>.s together with the recovered references and callees.

The arrange overlay lays its data out differently from the arcade one, so its address map
is derived from (a) the ROM-table copies found by datablock.match_segments, (b) the
arcade map (work/ps2/detrans/addrmap_arcade.json, learned by verify.py) transported
through the reference lists of matched function pairs, and (c) the callee map of matched
pairs.

Usage: python3 -m espgalarr.detrans.apply [--limit N]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import struct
from pathlib import Path

from ..diff import datablock as db, mips_funcs
from ..pgm import rom as rom_mod
from ..ps2.overlay import Overlay
from . import core, funcs as funcs_mod
import re
from .mips import decode_all
from .verify import ROOT, WORK, LearnedMap, _reader

OUT = WORK / "ps2" / "detrans"


def is_translated(code: bytes) -> bool:
    """Does the function touch the emulated 68k register file or the CCR?"""
    for ins in decode_all(code, 0):
        if ins.op in ("lw", "lhu", "lbu", "lh", "lb", "sw", "sh", "sb") and ins.rs == 1 and 0x10A0 <= ins.imm < 0x1120:
            return True
        if ins.op in ("lhu", "sh") and ins.rs == 28 and ins.imm == -0x7EE4:
            return True
    return False


class ArrangeMap(LearnedMap):
    def __init__(self, samples, funcs, segs, image_base, code_starts=()):
        super().__init__(samples, funcs)
        self.segs = sorted(segs, key=lambda s: s.ov_off)
        self.seg_lo = [s.ov_off for s in self.segs]
        self.image_base = image_base
        self.code_starts = set(code_starts)

    def const_text(self, v: int):
        if v in self.funcs:
            return f"${self.funcs[v]:x}"
        if v in self.code_starts:
            return f"F_{v:x}"
        return None

    def map_const(self, v: int):
        o = v - self.image_base
        i = bisect.bisect_right(self.seg_lo, o) - 1
        if i >= 0 and self.segs[i].ov_off <= o < self.segs[i].ov_end:
            return self.segs[i].rom_off + (o - self.segs[i].ov_off), "rom", 1.0
        return super().map_const(v)


def build(limit: int | None = None, verbose: bool = True, assemble: bool = False):
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    A = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A8")
    R = Overlay(WORK / "ps2" / "disc" / "A0A0" / "A0A9")
    fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
    diff = mips_funcs.match(fa, fb)
    lm = LearnedMap.load(OUT / "addrmap_arcade.json")
    segs = db.match_segments(p, R.image, 0, 0x200000, min_len=48)
    sp = funcs_mod.Split(R, _reader(R))
    # transport the arcade map through matched pairs
    samples: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    funcs: dict[int, int] = {}
    by_a = {}
    for m in diff.matches:
        by_a[m.a.start] = m
        if len(m.a.refs) == len(m.b.refs):
            for x, y in zip(m.a.refs, m.b.refs):
                t, kind, conf = lm.map_const(x)
                if t is not None and conf >= 0.9:
                    samples[y][t] += 1
        if m.a.start in lm.funcs:
            funcs[m.b.start] = lm.funcs[m.a.start]
    for k, v in lm.funcs.items():          # ELF-resident callees are shared
        if k < A.image_base:
            funcs.setdefault(k, v)
    flat = {y: c.most_common(1)[0][0] for y, c in samples.items() if len(c) == 1 or c.most_common(1)[0][1] >= 2}
    amap = ArrangeMap(flat, funcs, segs, R.image_base, sp.start_set)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("arrange_*.s"):       # no stale output from earlier splits
        old.unlink()
    amap.save(OUT / "addrmap_arrange.json")
    by_b = diff.by_b()
    summary = {"unchanged": [], "near": [], "new": [], "native": 0, "low_conf": [], "funcs": {}}
    n = 0
    for start, end in sp.funcs:
        size = end - start
        if size < 16:
            continue
        code = R.read(start, size)
        if not is_translated(code):
            summary["native"] += 1
            continue
        m = by_b.get(start)
        if m is not None and m.kind == "exact" and m.a.start in lm.funcs:
            summary["unchanged"].append((start, m.a.start, lm.funcs[m.a.start]))
            summary["funcs"][start] = {"kind": "unchanged", "m68k": lm.funcs[m.a.start]}
            continue
        res = core.detranslate(code, start, amap, read_word=_reader(R), extra_labels=sp.external_labels(start, end))
        f = type("F", (), {"start": start, "end": end, "size": size})()
        kind = "near" if m is not None else "new"
        known = [i for i in res.insns if i.mnem and not i.is_label]
        hi = sum(1 for i in known if i.conf >= 0.8)      # recognised (flags may be elided)
        unknown = res.n_unknown
        total = len(known) + unknown
        frac = hi / total if total else 0.0
        refs = sorted({int(o[1:-2], 16) for i in known for o in i.ops if o.startswith("$") and o.endswith(".l")} |
                      {int(o[1:], 16) for i in known for o in i.ops if o.startswith("$") and not o.endswith(".l") and o[1:].isalnum()})
        ram = [r for r in refs if 0x800000 <= r < 0x820000]
        rom = [r for r in refs if r < 0x200000]
        other = [r for r in refs if r not in ram and r not in rom]
        callees = sorted(set(res.calls))
        lines = [f"; arrange function {f.start:#x}-{f.end:#x} ({f.size} bytes): {kind}"]
        if m is not None:
            twin = lm.funcs.get(m.a.start)
            lines.append(f"; arcade twin {m.a.start:#x} ({m.kind}, ratio {m.ratio:.2f})" + (f", 68k original {twin:#x}" if twin else ""))
        lines.append(f"; instructions: {len(known)} recovered ({hi} with confidence >= 0.8), {unknown} unknown")
        lines.append("; 68k RAM references: " + ", ".join(f"${r:x}" for r in ram))
        lines.append("; ROM table references: " + ", ".join(f"${r:x}" for r in rom))
        if other:
            lines.append("; unmapped PS2 references: " + ", ".join(f"${r:x}" for r in other))
        lines.append("; callees: " + ", ".join(amap.code_label(c) + (f" (elf)" if c < R.image_base else "") for c in callees))
        lines.append("")
        body = res.text(header=False)
        lines.append(body)
        text = "\n".join(lines)
        (OUT / f"arrange_{f.start:x}.s").write_text(text)
        summary[kind].append((f.start, f.size, frac, len(known), unknown))
        if frac < 0.95:
            summary["low_conf"].append((f.start, f.size, frac, len(known), unknown, kind))
        refs_code = sorted({int(x, 16) for x in re.findall(r"\bF_([0-9a-f]+)\b", body)} - {f.start})
        entry = {"kind": kind, "unknown": unknown, "known": len(known), "refs": refs_code,
                 "unmapped_calls": sorted(c for c in callees if amap.code_label(c).startswith("F_") and c not in sp.start_set)}
        if assemble:
            ok, reason = try_assemble(text)
            entry["assembles"] = ok
            entry["asm_error"] = reason
        summary["funcs"][f.start] = entry
        n += 1
        if limit and n >= limit:
            break
    if not limit:
        _elf_pass(summary, lm, assemble)
    return summary


def _elf_pass(summary: dict, lm: LearnedMap, assemble: bool) -> None:
    """De-translate the ELF-resident helpers that recovered routines call but that have no
    68k original (ambiguous or new). They become recovered routines of kind 'elf', placed in
    free ROM like any new routine, so their callers no longer count them as unmapped."""
    from .verify import ElfText
    ov = ElfText(WORK / "ps2" / "disc" / "SLPS_253.52")
    starts = {f.start: f.end for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)}
    sorted_starts = sorted(starts)

    def span(addr: int) -> tuple[int, int] | None:
        """Function extent for a call target: a split start, or a mid-function entry point
        (ends where the enclosing split function ends)."""
        if addr in starts:
            return addr, starts[addr]
        i = bisect.bisect_right(sorted_starts, addr) - 1
        if i >= 0 and addr < starts[sorted_starts[i]]:
            return addr, starts[sorted_starts[i]]
        return None

    for old in OUT.glob("elf_*.s"):
        old.unlink()
    work = [c for e in summary["funcs"].values() for c in e.get("unmapped_calls", []) if span(c)]
    done: set[int] = set()
    while work:
        start = work.pop()
        if start in done or start in summary["funcs"]:
            continue
        done.add(start)
        end = span(start)[1]
        code = ov.read(start, end - start)
        # call targets are code even when the translator elided every flag update (tiny
        # test-and-branch stubs), so no is_translated() filter here
        try:
            res = core.detranslate(code, start, lm, read_word=_reader(ov))
        except Exception as e:  # noqa: BLE001
            summary["funcs"][start] = {"kind": "elf", "unknown": 1, "known": 0, "refs": [], "unmapped_calls": [],
                                       "assembles": False, "asm_error": f"detranslate: {type(e).__name__}"}
            continue
        known = [i for i in res.insns if i.mnem and not i.is_label]
        body = res.text(header=False)
        callees = sorted(set(res.calls))
        text = "\n".join([f"; ELF helper {start:#x}-{end:#x} ({end - start} bytes): no unique 68k original",
                          f"; instructions: {len(known)} recovered, {res.n_unknown} unknown",
                          "; callees: " + ", ".join(lm.code_label(c) for c in callees), "", body])
        (OUT / f"elf_{start:x}.s").write_text(text)
        refs_code = sorted({int(x, 16) for x in re.findall(r"\bF_([0-9a-f]+)\b", body)} - {start})
        unmapped = sorted(c for c in callees if lm.code_label(c).startswith("F_") and not span(c))
        entry = {"kind": "elf", "unknown": res.n_unknown, "known": len(known), "refs": refs_code, "unmapped_calls": unmapped}
        if assemble:
            ok, reason = try_assemble(text)
            entry["assembles"] = ok
            entry["asm_error"] = reason
        summary["funcs"][start] = entry
        work.extend(c for c in refs_code if span(c) and c not in done)
    # callers: ELF helpers that are now recovered routines are references, not unmapped calls
    for e in summary["funcs"].values():
        if e.get("unmapped_calls"):
            moved = [c for c in e["unmapped_calls"] if c in summary["funcs"]]
            e["unmapped_calls"] = [c for c in e["unmapped_calls"] if c not in summary["funcs"]]
            e["refs"] = sorted(set(e.get("refs", [])) | set(moved))
    summary["elf"] = len(done)


def try_assemble(text: str) -> tuple[bool, str]:
    """Assemble one recovered routine; references to other routines (F_xxx) and 68k
    addresses are satisfied with placeholder symbols."""
    from ..pgm import asm68k
    defined = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*):", text, re.M))
    used = set(re.findall(r"\bF_[0-9a-f]+\b", text))
    prelude = "".join(f".equ {sym}, $100000\n" for sym in sorted(used - defined))
    try:
        asm68k.assemble(prelude + text, 0x200000)
        return True, ""
    except asm68k.AsmError as e:
        return False, str(e)[:120]
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:100]}"


def closure(funcs: dict) -> tuple[set, set]:
    """clean = zero unknowns, assembles, no unmapped callees; portable = clean and every
    referenced recovered routine is portable (unchanged routines count as portable)."""
    clean = {a for a, e in funcs.items() if e["kind"] == "unchanged" or
             (e.get("unknown", 1) == 0 and e.get("assembles", False) and not e.get("unmapped_calls"))}
    portable = set(clean)
    changed = True
    while changed:
        changed = False
        for a in list(portable):
            e = funcs[a]
            if e["kind"] == "unchanged":
                continue
            if any(r not in portable for r in e["refs"]):
                portable.discard(a)
                changed = True
    return clean, portable


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--assemble", action="store_true", help="assemble every emitted .s and report")
    a = ap.parse_args(argv)
    s = build(a.limit, assemble=a.assemble)
    if a.assemble:
        fs = s["funcs"]
        clean, portable = closure(fs)
        recovered = {k: v for k, v in fs.items() if v["kind"] != "unchanged"}
        n_asm = sum(1 for v in recovered.values() if v.get("assembles"))
        print(f"recovered routines: {len(recovered)}, assemble: {n_asm}, zero unknowns: "
              f"{sum(1 for v in recovered.values() if v['unknown'] == 0)}, clean: "
              f"{sum(1 for k in clean if fs[k]['kind'] != 'unchanged')}, portable (closure): "
              f"{sum(1 for k in portable if fs[k]['kind'] != 'unchanged')}")
        for kind in ("new", "near", "elf"):
            tot = [k for k, v in recovered.items() if v["kind"] == kind]
            print(f"  {kind}: {len(tot)} routines, clean {sum(1 for k in tot if k in clean)}, portable {sum(1 for k in tot if k in portable)}")
        reasons = collections.Counter(re.sub(r"\$[0-9a-f]+|\d+", "N", v["asm_error"])[:60] for v in recovered.values() if not v.get("assembles"))
        print("assembly failures by reason:", reasons.most_common(12))
        with open(OUT / "assemble_report.txt", "w") as f:
            f.write("# function kind unknown assembles clean portable unmapped_calls asm_error\n")
            for k in sorted(recovered):
                v = recovered[k]
                f.write(f"{k:#x} {v['kind']} {v['unknown']} {int(bool(v.get('assembles')))} {int(k in clean)} {int(k in portable)} "
                        f"{','.join(f'{c:#x}' for c in v['unmapped_calls'])} {v.get('asm_error', '')}\n")
        s["clean"] = sorted(clean)
        s["portable"] = sorted(portable)
    print(f"unchanged (exact twin with known 68k original): {len(s['unchanged'])}")
    print(f"native (no register-file access): {s['native']}")
    for kind in ("near", "new"):
        lst = s[kind]
        tot = sum(x[3] + x[4] for x in lst)
        unk = sum(x[4] for x in lst)
        print(f"{kind}: {len(lst)} functions, {tot} instructions, {unk} unknown ({(tot - unk) / tot if tot else 0:.1%} recovered)")
    print(f"functions below 95% confidence: {len(s['low_conf'])}")
    for x in sorted(s["low_conf"], key=lambda x: x[2])[:40]:
        print(f"  {x[0]:#x} {x[1]:6d} B  {x[2]:5.1%}  {x[3]} known / {x[4]} unknown  {x[5]}")
    (OUT / "summary.json").write_text(json.dumps({k: (v if k != "funcs" else {f"{a:#x}": e for a, e in v.items()}) for k, v in s.items()}, indent=0, default=list))


if __name__ == "__main__":
    main()
