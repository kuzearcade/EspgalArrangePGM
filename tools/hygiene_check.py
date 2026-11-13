"""hygiene_check.py [PATH...]: tracked files (or the given paths) must hold no original game bytes or text.

Looks for, in each file, against the inputs (the MAME zips espgal / espgalbl / pgm, esprade when present, the PS2 executable,
overlays and modules in work/ps2/disc, and the decoded archives in work/ps2/decoded):
- raw byte runs of 16+ bytes found in an input;
- hex literals (bare hex strings, `\\xNN` sequences) with 3+ distinct values that occur in an input:
  6+ bytes anywhere, 4..5 bytes on a line that compares or searches (assert, ==, !=, expect, want,
  startswith, find, index: where a literal is an original-byte check by design; shorter values elsewhere
  match common instruction words and our own test programs by chance);
- ASCII runs of 12+ characters with a capital letter or a digit (the game's text is upper case and
  numbers; lower-case English phrases match SDK strings by chance) found in the game's own files (the
  Sony system modules in work/ps2/disc/MODULES are left out of this rule).
Strings listed in tools/hygiene_allow.txt (identifiers such as middleware API names, with a reason each)
are not reported.
The ROMs are searched as stored and 16-bit byte-swapped (the 68k view of the program ROM). Runs with fewer
than 4 distinct byte values (fill, NOPs) and the hex digit string are ignored. Exit status 1 with a report
when anything is found, 2 when the inputs are missing.
"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
import zipfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_K = 16
ASCII_K = 12
LIT_MIN = 4
LIT_ANY = 6
CMP_RE = re.compile(rb"assert|==|!=|expect|want|startswith|endswith|\.find\(|\.index\(|\.count\(")
CHUNK = 1 << 24
TRIVIAL = {b"0123456789abcdef", b"0123456789ABCDEF"}
HEX_RE = re.compile(rb"(?<![0-9A-Fa-fxX$])((?:[0-9A-Fa-f]{2}){4,})(?![0-9A-Fa-f])")   # 0x.. numbers are numbers
X_RE = re.compile(rb"((?:\\x[0-9A-Fa-f]{2}){4,})")


def window_hashes(a: np.ndarray, k: int) -> np.ndarray:
    if len(a) < k:
        return np.zeros(0, np.uint64)
    w = np.lib.stride_tricks.sliding_window_view(a, k)
    h = np.zeros(len(w), np.uint64)
    for j in range(k):
        h = h * np.uint64(1099511628211) + w[:, j].astype(np.uint64)
    return h


def inputs(root: str):
    """(name, bytes, is_rom) for every input; None if they are missing."""
    out = []
    for z in ("espgal", "espgalbl", "pgm", "esprade"):
        p = os.path.join(root, "mame_roms", z + ".zip")
        if not os.path.exists(p):
            if z == "esprade":          # optional (build --esprade-chars): checked when present
                continue
            return None
        with zipfile.ZipFile(p) as zf:
            for n in zf.namelist():
                out.append((f"{z}.zip:{n}", zf.read(n), True))
    for base in ("work/ps2/disc", "work/ps2/decoded"):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            return None
        for dp, _, fs in os.walk(d):
            for f in sorted(fs):
                p = os.path.join(dp, f)
                if os.path.getsize(p) < 64 << 20:          # the AFS sound archives are skipped
                    with open(p, "rb") as fh:
                        out.append((os.path.relpath(p, root), fh.read(), False))
    return out


def literals(b: bytes):
    for m in HEX_RE.finditer(b):
        yield m.start(), bytes.fromhex(m.group(1).decode())
    for m in X_RE.finditer(b):
        yield m.start(), bytes(int(x, 16) for x in re.findall(rb"\\x([0-9A-Fa-f]{2})", m.group(1)))


def scan(files: dict[str, bytes], ins) -> list[str]:
    raw = collections.defaultdict(set)           # window hash -> files
    asc = collections.defaultdict(set)
    lits = collections.defaultdict(set)          # literal bytes -> (file, line)
    for name, b in files.items():
        a = np.frombuffer(b, np.uint8)
        for h in np.unique(window_hashes(a, RAW_K)):
            raw[int(h)].add(name)
        if b"\0" in b[:8192]:
            continue
        for h in np.unique(window_hashes(a, ASCII_K)):
            asc[int(h)].add(name)
        for pos, v in literals(b):
            if len(v) < LIT_MIN or len(set(v)) < 3:
                continue
            if len(v) < LIT_ANY:
                ls, le = b.rfind(b"\n", 0, pos) + 1, b.find(b"\n", pos)
                if not CMP_RE.search(b[ls:le if le >= 0 else len(b)]):
                    continue
            lits[v].add((name, b.count(b"\n", 0, pos) + 1))
    pre = collections.defaultdict(set)           # 4-byte prefix hash -> literals
    for v in lits:
        pre[int(window_hashes(np.frombuffer(v[:LIT_MIN], np.uint8), LIT_MIN)[0])].add(v)
    keys = {k: np.array(sorted(d), dtype=np.uint64) for k, d in ((RAW_K, raw), (ASCII_K, asc), (LIT_MIN, pre))}
    found_raw = collections.defaultdict(set)     # file -> runs
    found_lit = collections.defaultdict(set)
    for iname, data, rom in ins:
        views = [("", data)]
        if rom:
            views.append(("(swapped)", np.frombuffer(data[:len(data) // 2 * 2], np.uint16).byteswap().tobytes()))
        for tag, src in views:
            a = np.frombuffer(src, np.uint8)
            for off in range(0, len(a), CHUNK):
                seg = a[off:off + CHUNK + 64]
                for k, tab in ((RAW_K, raw), (ASCII_K, asc)):
                    if k == ASCII_K and "/MODULES/" in iname:
                        continue
                    h = window_hashes(seg, k)
                    for x in np.nonzero(np.isin(h, keys[k]))[0]:
                        w = bytes(seg[x:x + k])
                        if len(set(w)) < 4 or w in TRIVIAL or (k == ASCII_K and not all(32 <= c < 127 for c in w)):
                            continue
                        if k == ASCII_K and not re.search(rb"[A-Z0-9]", w):
                            continue
                        for f in tab[int(h[x])]:
                            if w in files[f]:
                                found_raw[f].add((w, iname + tag))
                h = window_hashes(seg, LIT_MIN)
                for x in np.nonzero(np.isin(h, keys[LIT_MIN]))[0]:
                    for v in pre[int(h[x])]:
                        if bytes(seg[x:x + len(v)]) == v:
                            for f, line in lits[v]:
                                found_lit[f].add((line, v.hex(), iname + tag))
    allow = _allowed()
    report = []
    for f in sorted(set(found_raw) | set(found_lit)):
        runs = [r for r in _merge([w for w, _ in found_raw.get(f, ())], files[f])
                if not any(a in r and len(r) - len(a) < 4 for a in allow)]
        srcs = sorted({s for _, s in found_raw.get(f, ())})
        for r in runs:
            if r not in TRIVIAL:
                report.append(f"{f}: run {r[:60]!r} ({len(r)} bytes; {', '.join(srcs[:3])})")
        for line, hx, s in sorted(found_lit.get(f, ())):
            report.append(f"{f}:{line}: literal {hx[:48]} ({len(hx) // 2} bytes; {s})")
    return report


def _allowed() -> list[bytes]:
    """tools/hygiene_allow.txt: one allowed string per line (Python bytes-literal syntax not needed: the
    text up to '  #', which starts the reason)."""
    p = os.path.join(REPO, "tools", "hygiene_allow.txt")
    if not os.path.exists(p):
        return []
    out = []
    with open(p, "rb") as fh:
        for line in fh:
            s = line.rstrip(b"\n").split(b"  #", 1)[0]
            if s and not s.startswith(b"#"):
                out.append(s)
    return out


def _merge(windows, text: bytes) -> list[bytes]:
    """The maximal runs of the file covered by the found windows."""
    cover = np.zeros(len(text) + 1, bool)
    for w in set(windows):
        i = text.find(w)
        while i >= 0:
            cover[i:i + len(w)] = True
            i = text.find(w, i + 1)
    runs, i = [], 0
    while i < len(text):
        if cover[i]:
            j = i
            while j < len(text) and cover[j]:
                j += 1
            runs.append(text[i:j])
            i = j
        else:
            i += 1
    return runs


def tracked(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p and os.path.isfile(os.path.join(root, p))]


def main(argv: list[str]) -> int:
    paths = argv or tracked(REPO)
    files = {}
    for p in paths:
        with open(os.path.join(REPO, p), "rb") as fh:
            files[p] = fh.read()
    ins = inputs(REPO)
    if ins is None:
        print("hygiene_check: inputs not found (mame_roms/, work/ps2/disc, work/ps2/decoded)", file=sys.stderr)
        return 2
    report = scan(files, ins)
    for line in report:
        print(line)
    print(f"hygiene_check: {len(files)} files, {len(ins)} inputs: "
          f"{'clean' if not report else str(len(report)) + ' findings'}", file=sys.stderr)
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
