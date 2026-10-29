"""Compare de-translated Arrange routines with the PS2 originals on PCSX2 hook dumps.

Usage: python3 tools/ps2check_dumps.py <dump dir> <annotated hook list> [-j N (default 8)] [--out results.json]
Each dump is checked in its own subprocess limited to 3 GB of address space and DUMP_SECONDS (an
earlier unbounded run exhausted RAM and triggered the system OOM killer; a process pool with worker
recycling later deadlocked when a worker vanished).

Dumps come from PCSX2 built with patches/pcsx2/espgal-trace.diff, run with ESPGAL_HOOKS (lines
"pc first-16-code-bytes"), ESPGAL_HOOK_MAX, ESPGAL_DUMP_DIR and a pad script such as
tools/pcsx2_pad_arrange_stage1.txt (ESPGAL_PAD). The annotated list has the same lines plus
"  # <overlay> ..." and gives each dump's overlay: dump hook_<pc>_<sig>_<n> belongs to the
<sig>-th line (0-based) for that pc. Per dump the result is MATCH, the differences, or an error
(e.g. the routine could not be de-translated or assembled), or TIMEOUT after 180 s.
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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


WORKER_MEM = 3 << 30   # address-space cap per worker: a runaway check fails alone (MemoryError)
DUMP_SECONDS = 180     # wall-clock cap per dump (a looping candidate otherwise blocks the run)


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def _limit_memory() -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEM, WORKER_MEM))


def check_one(args) -> dict:
    prefix, annot, stubs = args
    from espgalarr.detrans import ps2check as P
    P.STUBS = set(stubs)
    ctx = _context(annot)
    m = re.search(r"hook_([0-9a-f]+)_(\d+)_(\d+)$", prefix)
    fn, sig = int(m.group(1), 16), int(m.group(2))
    ov = ctx["order"][fn][sig]
    out = {"dump": Path(prefix).name, "fn": f"{fn:#x}", "overlay": ov}
    import signal
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(DUMP_SECONDS)
    try:
        return _check(P, ctx, prefix, fn, ov, out)
    except P.CallError as ex:
        stub = ex.native_frame()
        if "budget exceeded" not in str(ex) or stub is None or stub in P.STUBS:
            raise
        # a native service that never finishes here (IOP wait, long loader): stub it on both
        # sides and compare the rest
        P.STUBS.add(stub)
        out["stubbed"] = f"{stub:#x}"
        return _check(P, ctx, prefix, fn, ov, out)
    finally:
        signal.alarm(0)


def _check(P, ctx, prefix, fn, ov, out) -> dict:
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
    except _Timeout:
        out["result"] = "TIMEOUT"
    except P.CallError as ex:
        if "budget exceeded" in str(ex) and ex.native_frame() is not None and "stubbed" not in out:
            raise
        out["result"] = "CANDIDATE-CRASH" if ex.side == "candidate" else "ERROR"
        out["error"] = str(ex)[:300]
        out["callee"] = f"{ex.callee:#x}" if ex.callee is not None else None
    except Exception as ex:  # noqa: BLE001
        out["result"] = "ERROR"
        out["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
    return out


def _one(prefix: str, annot: str, stubs: list[int]) -> dict:
    """Check one dump in a fresh, memory-capped interpreter (a crash or hang loses only it)."""
    cmd = [sys.executable, __file__, "--one", prefix, annot, ",".join(f"{x:x}" for x in stubs)]
    name = Path(prefix).name
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=DUMP_SECONDS + 60,
                           preexec_fn=_limit_memory)
    except subprocess.TimeoutExpired:
        return {"dump": name, "overlay": "?", "result": "TIMEOUT"}
    line = next((x for x in reversed(p.stdout.splitlines()) if x.startswith("{")), None)
    if line is None:
        return {"dump": name, "overlay": "?", "result": "WORKER-DIED",
                "error": f"exit {p.returncode}: {p.stderr.strip()[-300:]}"}
    return json.loads(line)


def main():
    a = sys.argv[1:]
    if a[0] == "--one":
        stubs = [int(x, 16) for x in a[3].split(",") if x]
        print(json.dumps(check_one((a[1], a[2], stubs))), flush=True)
        return
    jobs = int(a[a.index("-j") + 1]) if "-j" in a else 8
    out = a[a.index("--out") + 1] if "--out" in a else None
    d, annot = a[0], a[1]
    stubs = [int(x, 16) for x in a[a.index("--stubs") + 1].split(",")] if "--stubs" in a else []
    prefixes = sorted(str(p)[:-len(".ee.zst")] for p in Path(d).glob("hook_*.ee.zst"))
    results = []
    with ThreadPoolExecutor(jobs) as ex:
        for f in as_completed([ex.submit(_one, p, annot, stubs) for p in prefixes]):
            r = f.result()
            results.append(r)
            print(f"[{len(results)}/{len(prefixes)}]", r["dump"], r["overlay"], r["result"],
                  r.get("error", "") or (r.get("diffs") or [""])[0], r.get("callee") or "", flush=True)
    results.sort(key=lambda r: r["dump"])
    if out:
        Path(out).write_text(json.dumps(results, indent=1))
    c = collections.Counter(r["result"] for r in results)
    print(dict(c))


if __name__ == "__main__":
    main()
