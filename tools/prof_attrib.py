"""prof_attrib.py P_ALLOC THRESH TRACE...: per main-loop pass (prof_passes.py's), cycles by category;
summed over the passes above THRESH cycles and over all passes."""
import bisect, collections, re, sys
LINE = re.compile(r"^([0-9a-f]+) ([0-9A-F]+): ")
WAIT = {0x112C46, 0x112C4C}
rows = sorted((int(a, 16), int(n, 16), note.strip()) for a, n, note in (l.split(" ", 2) for l in open(sys.argv[1])))
starts = [r[0] for r in rows]
thr = float(sys.argv[2])
RANGES = [(0x112B8C, 0x112C50, "idle wait"), (0x17135E, 0x1714F6, "bullet allocators"),
          (0x3FFC00, 0x3FFE00, "bullet allocators"),   # the burst entries (pgm/allocburst.py)
          (0x1714F6, 0x17242A, "pool sweeps (cancel/convert)"), (0x17242A, 0x173100, "pool update loop"),
          (0x16F000, 0x180000, "bullet run handlers (arcade)"), (0x11F290, 0x11F4F0, "palette fade service")]
cache = {}
def cat(pc):
    c = cache.get(pc)
    if c: return c
    for a, b, n in RANGES:
        if a <= pc < b: c = n; break
    else:
        i = bisect.bisect_right(starts, pc) - 1
        if i >= 0 and pc < rows[i][0] + rows[i][1]:
            n = rows[i][2]
            c = "ours: " + ("ported Arrange routines" if n.startswith("arrange ") else n)
        else:
            c = "arcade other" if pc < 0x200000 else "ours: other"
    cache[pc] = c
    return c
passes = []
for tr in sys.argv[3:]:
    cur = None; prev_c = prev_pc = None
    for line in open(tr):
        m = LINE.match(line)
        if not m: continue
        c, pc = int(m.group(1), 16), int(m.group(2), 16)
        if prev_c is not None and cur is not None and prev_pc not in WAIT:
            cur[cat(prev_pc)] += c - prev_c
        if pc in WAIT:
            if cur is not None: passes.append(cur); cur = None
        elif cur is None and prev_pc in WAIT:
            cur = collections.Counter()
        prev_c, prev_pc = c, pc
for name, sel in (("passes over %d" % thr, [p for p in passes if sum(p.values()) > thr]), ("all passes", passes)):
    tot = collections.Counter()
    for p in sel: tot.update(p)
    T = sum(tot.values())
    print(f"{name}: {len(sel)} passes, mean {T / max(len(sel), 1):.0f} cycles")
    for k, v in tot.most_common(14): print(f"  {100 * v / T:5.1f}%  {v / max(len(sel), 1):8.0f}  {k}")
