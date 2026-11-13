"""busymap.py BUSY_LOG SCALE MAME_SHOOT PS2DIR both|kak|blk [--shift N] [--win N] [--tsv OUT]

68k cycles per logic step of a replay run with tests/lua/busytap.lua (any clock_scale SCALE), and
the clock each part of the run would need to keep the PS2's pace.

A step starts at the vblank IRQ's write of 0 to 0x8011C0 and ends at the main loop's wait
(writes from 0x112B8C / 0x112C40). Its cost, IRQ handlers included, is
    n * P - 26 * r
where P is the vblank period in 68k cycles at SCALE (20 MHz * SCALE / 59.17 Hz), n the frames
from the step's start to the IRQ that ends its wait (the IRQ writes the byte only while a wait is
pending, so an overrun shows as n = 2) and r the spin reads before that IRQ (26 cycles a spin).
These cycle counts do not depend on the clock: one run predicts the lag at every clock (a step
lags when its cost exceeds one period at that clock; the forced waits of 0x8011A4 come on top).

Steps are keyed as lagmap.py (shoot.lua key - 0x3E5 = play timer 0x8139DC - 0xB2, unwrapped for
`both`; blk: - BLK_SHIFT) and grouped into the same windows (stages from the PS2 trace).
Per window: steps, mean / 90th percentile / max cycles per step, the share of steps over one
frame at 20 / 40 / 50 / 60 MHz, and "need": the 68k clock (MHz) at which no more steps would
overrun than the PS2 lags in that window.
"""
import argparse
import collections

ap = argparse.ArgumentParser()
ap.add_argument("busy")
ap.add_argument("scale", type=float)
ap.add_argument("mame")
ap.add_argument("ps2")
ap.add_argument("layout", choices=("both", "kak", "blk"))
ap.add_argument("--shift", type=int, default=None)
ap.add_argument("--win", type=int, default=300)
ap.add_argument("--tsv")
ap.add_argument("--steps", help="write 'key cycles' per step")
a = ap.parse_args()
shift = a.shift if a.shift is not None else (100 if a.layout == "blk" else 0)
BASE = 20e6 / 59.17
P = BASE * a.scale
SPIN = 26
WAIT_PCS = {0x112B8C, 0x112C40, 0x112B94}    # write pcs of the main loop's wait (pc after the write)

# --- steps from the busy log
cost, forced = {}, collections.Counter()
started, f0, pending, end_t = False, None, None, None
thi, tlast = 0, None
for l in open(a.busy):
    ft, pc, v, r, t = l.split()
    ft, pc, v, r, t = int(ft), int(pc, 16), int(v, 16), int(r), int(t, 16)
    irq = pc not in WAIT_PCS
    if irq:
        if pending is not None:
            # the IRQ only writes while a wait is pending: the frames from the step's start to
            # this IRQ are the periods it used, less the spin after it
            c = (ft - pending) * P - SPIN * r
            cost.setdefault(end_t, c)          # first step with that key (the stage ones)
            pending = None
        if v == 0:
            started, f0 = True, ft             # the wait is over: a step starts
        # (v != 0: a forced wait's countdown)
    else:
        if started:
            if a.layout == "both":
                if tlast is not None and t < tlast and tlast - t > 0x8000:
                    thi += 0x10000
                tlast = t
                key = thi + t - 0xB2
            else:
                key = (t + 0x333 - 0x3E5 - shift) & 0xFFFF
            pending, end_t = f0, key
        elif pc == 0x112C40 and end_t is not None:
            forced[end_t] += v - 1             # a forced wait of v vblanks (0x8011A4)
        started = False

# --- PS2 frames per key, stage starts (as lagmap.py)
pf, starts = collections.Counter(), []
hi, last, raw, run = 0, None, None, 0
for l in open(a.ps2 + "/watch.txt"):
    x = l.split()
    if x[0] == "H":
        continue
    k = int(x[1][:4], 16)
    if a.layout == "both" and raw is not None and k < raw and raw - k > 0x8000:
        hi += 0x10000
    raw = k
    k += hi if a.layout == "both" else 0
    if k == last:
        run += 1
        if run == 150 and k > 0x100:
            starts.append(k + 1)
    else:
        run = 0
    last = k
    pf[k] += 1

keys = sorted(k for k in cost if k in pf and k > 0 and pf[k] < 30 and cost[k] < 30 * BASE)


def stage_of(k):
    return 1 + sum(1 for s in starts if k >= s)


def mhz_needed(cs, allowed):
    """lowest clock (MHz) at which at most `allowed` of the steps exceed one frame"""
    s = sorted(cs, reverse=True)
    i = min(int(allowed), len(s) - 1)
    return 20.0 * s[i] / BASE


def over(cs, mhz):
    p = BASE * mhz / 20.0
    return 100.0 * sum(1 for c in cs if c > p) / len(cs)


by_stage = collections.defaultdict(list)
for k in keys:
    by_stage[stage_of(k)].append(k)

if a.steps:
    with open(a.steps, "w") as o:
        for k in keys:
            o.write(f"{k:#x} {int(cost[k])}\n")

print(f"{len(keys)} steps with a cost; P = {P:.0f} cycles at x{a.scale:g}")
hdr = "fwait  mean k  p90 k  max k  >1fr@20  @40   @50   @60 | need MHz"
print("\n(PS2lag: its lag beyond the forced waits fwait, which both machines take)\nper stage:\nstage steps PS2lag% " + hdr)
for st, ks in sorted(by_stage.items()):
    cs = [cost[k] for k in ks]
    plag = sum(1 for k in ks if 1 < pf[k] < 30)
    s = sorted(cs)
    fw = sum(forced[k] for k in ks)
    plag = max(0, plag - fw)       # the PS2's lag beyond the forced waits it shares
    print(f"{st:5d} {len(ks):5d} {100.0 * plag / len(ks):6.1f} {fw:5d}  {sum(cs) / len(cs) / 1000:6.0f} "
          f"{s[int(len(s) * .9)] / 1000:6.0f} {s[-1] / 1000:6.0f} {over(cs, 20):7.1f} {over(cs, 40):5.1f} "
          f"{over(cs, 50):5.1f} {over(cs, 60):5.1f} | {mhz_needed(cs, plag):6.1f}")

rows = []
print(f"\nwindows of {a.win} steps:\nstage keys            PS2lag% " + hdr)
for st, ks in sorted(by_stage.items()):
    for i in range(0, len(ks), a.win):
        w = ks[i:i + a.win]
        cs = [cost[k] for k in w]
        plag = max(0, sum(1 for k in w if 1 < pf[k] < 30) - sum(forced[k] for k in w))
        s = sorted(cs)
        row = (st, w[0], w[-1], len(w), 100.0 * plag / len(w), sum(cs) / len(cs), s[int(len(s) * .9)],
               s[-1], over(cs, 20), over(cs, 40), over(cs, 50), over(cs, 60), mhz_needed(cs, plag))
        rows.append(row)
        print(f"{st:5d} {w[0]:#07x}-{w[-1]:#07x} {row[4]:6.1f} {sum(forced[k] for k in w):5d}  {row[5] / 1000:6.0f} {row[6] / 1000:6.0f} "
              f"{row[7] / 1000:6.0f} {row[8]:7.1f} {row[9]:5.1f} {row[10]:5.1f} {row[11]:5.1f} | {row[12]:6.1f}"
              + ("  *" if row[9] - row[4] >= 3 else ""))
if a.tsv:
    with open(a.tsv, "w") as o:
        o.write("#stage\tkey0\tkey1\tsteps\tps2lag\tmean\tp90\tmax\tover20\tover40\tover50\tover60\tneed_mhz\n")
        for r in rows:
            o.write("\t".join([str(r[0]), hex(r[1]), hex(r[2]), str(r[3])]
                              + [f"{x:.1f}" for x in r[4:]]) + "\n")
