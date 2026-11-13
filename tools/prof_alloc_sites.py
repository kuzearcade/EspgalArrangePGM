"""prof_alloc_sites.py TRACE...: cycles spent inside the allocators (and their burst entries at
0x3FFC00, pgm/allocburst.py) per call site
(the jsr before entry), with call counts and mean cycles per call."""
import collections, re, sys
LINE = re.compile(r"^([0-9a-f]+) ([0-9A-F]+): ")
ENTRY = {0x17135E, 0x1713EC, 0x170F36, 0x170FB8} | {0x3FFC00 + 12 * k for k in range(8)}   # + burst entries
cyc, calls = collections.Counter(), collections.Counter()
for tr in sys.argv[1:]:
    prev_c = prev_pc = None; site = None; t0 = None; depth = 0
    for line in open(tr):
        m = LINE.match(line)
        if not m: continue
        c, pc = int(m.group(1), 16), int(m.group(2), 16)
        if pc in ENTRY and site is None:
            site, t0 = prev_pc, c; calls[site] += 1
        elif site is not None and pc in (site + 2, site + 4, site + 6):
            cyc[site] += c - t0; site = None
        prev_c, prev_pc = c, pc
T = sum(cyc.values())
for s, v in cyc.most_common(15):
    print(f"  site {s:06x}: {v:9d} cycles {100 * v / T:5.1f}%  {calls[s]:5d} calls  {v // max(calls[s],1):6d}/call")
