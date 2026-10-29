"""Compare de-translated Arrange routines with the PS2 originals on PCSX2 hook dumps.

Usage: python3 tools/ps2check_dumps.py <dump dir> <annotated hook list> [-j N] [--out results.json]

Dumps come from PCSX2 built with patches/pcsx2/espgal-trace.diff, run with ESPGAL_HOOKS (lines
"pc first-16-code-bytes"), ESPGAL_HOOK_MAX, ESPGAL_DUMP_DIR and a pad script such as
tools/pcsx2_pad_arrange_stage1.txt (ESPGAL_PAD). The annotated list has the same lines plus
"  # <overlay> ..." and gives each dump's overlay: dump hook_<pc>_<sig>_<n> belongs to the
<sig>-th line (0-based) for that pc. Per dump the result is MATCH, the differences, or an error
(e.g. the routine could not be de-translated or assembled).
"""
from __future__ import annotations

import collections
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WORK = Path(__file__).resolve().parents[1] / "work" / "ps2"
_CTX: dict = {}


def _context(annot: str):
    if _CTX:
        return _CTX
    from espgalarr.detrans.verify import ElfText
    from espgalarr.diff import mips_funcs
    from espgalarr.ps2.overlay import Overlay
    order: dict = collections.defaultdict(list)
    for line in Path(annot).read_text().splitlines():
        if not line.strip():
            continue
        pc = int(line.split()[0], 16)
        ov = line.split("#", 1)[1].split()[0]
        order[pc].append(ov)
    starts, sizes = set(), {}
    for name in {o for v in order.values() for o in v} | {"A0A9"}:
        R = Overlay(WORK / "disc" / "A0A0" / name)
        for f in mips_funcs.split_functions(R.image, R.image_base, R.header.text_end):
            starts.add(f.start)
            sizes[(name, f.start)] = f.size
    e = ElfText(WORK / "disc" / "SLPS_253.52")
    for f in mips_funcs.split_functions(e.image, e.image_base, e.image_base + len(e.image)):
        starts.add(f.start)
    _CTX.update(order=order, starts=starts, sizes=sizes)
    return _CTX


def check_one(args) -> dict:
    prefix, annot = args
    from espgalarr.detrans import ps2check as P
    ctx = _context(annot)
    m = re.search(r"hook_([0-9a-f]+)_(\d+)_(\d+)$", prefix)
    fn, sig = int(m.group(1), 16), int(m.group(2))
    ov = ctx["order"][fn][sig]
    out = {"dump": Path(prefix).name, "fn": f"{fn:#x}", "overlay": ov}
    try:
        dd = P.Dump.load(Path(prefix))
        t = P.truth(dd, fn)
        start = P.regfile(P.Memory(dd.main, dd.scratch))
        out["truth_effect"] = any(t.regs[k] != start[k] for k in t.regs if k != "a7") or t.main != dd.main
        text, callees = P.render(dd, fn, ctx["sizes"][(ov, fn)], ctx["starts"])
        if "???" in text:
            out["result"] = "unknown-ops"
            return out
        c = P.candidate(dd, fn, text, callees)
        sp = int.from_bytes(dd.regs[29 * 16:29 * 16 + 4], "little")
        diffs = P.compare(t, c, sp, a7=start["a7"])
        out["result"] = "MATCH" if not diffs else "DIFF"
        out["diffs"] = diffs[:12]
    except Exception as ex:  # noqa: BLE001
        out["result"] = "ERROR"
        out["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
    return out


def main():
    a = sys.argv[1:]
    jobs = int(a[a.index("-j") + 1]) if "-j" in a else 1
    out = a[a.index("--out") + 1] if "--out" in a else None
    d, annot = a[0], a[1]
    prefixes = sorted(str(p)[:-len(".ee.zst")] for p in Path(d).glob("hook_*.ee.zst"))
    results = []
    with ProcessPoolExecutor(jobs) as ex:
        for r in ex.map(check_one, [(p, annot) for p in prefixes]):
            results.append(r)
            print(r["dump"], r["overlay"], r["result"], r.get("error", "") or (r.get("diffs") or [""])[0], flush=True)
    if out:
        Path(out).write_text(json.dumps(results, indent=1))
    c = collections.Counter(r["result"] for r in results)
    print(dict(c))


if __name__ == "__main__":
    main()
