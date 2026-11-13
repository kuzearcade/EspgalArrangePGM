"""lagmap.py MAME_SHOOT PS2DIR both|kak|blk [--shift N] [--win N] [--tsv OUT] [--base OLD.tsv]

Where a replayed run slows down more than the PS2: both sides are keyed on the same logic step
(the play timer key of bothcmp.py / longcmp.py), so the video frames each side spends per step can
be compared window by window, whatever the 68k clock.

- PS2 frames per key: frames of watch.txt whose key (0x642298) is that key; MAME frames: lines of
  the shoot.lua log with that key. A key held 30+ frames on either side (stage changes, disc loads,
  the start) counts as one frame there, so only slowdown remains.
- Windows of --win keys (default 300 logic steps, 5 s) per stage. Stages start where the PS2 holds
  a key for 150+ frames (bothcmp.py's stage changes).
- Real time: PS2 frames at 59.94 Hz, PGM frames at 59.17 Hz. "speed" = the PS2's seconds for the
  window over MAME's (1.00 = as fast as the PS2).
- Hot zones: consecutive windows where MAME's lag exceeds the PS2's by 3+ points.
- --tsv writes one line per window (stage, first key, last key, steps, PS2 frames, MAME frames,
  PS2 frame / MAME frame at the window's start) so a later build can be checked against the
  same windows: --base OLD.tsv prints each hot zone's MAME frames then and now.
"""
import argparse
import collections

PS2_HZ, PGM_HZ = 59.94, 59.17
HOLD = 30

ap = argparse.ArgumentParser()
ap.add_argument("mame")
ap.add_argument("ps2")
ap.add_argument("layout", choices=("both", "kak", "blk"))
ap.add_argument("--shift", type=int, default=None, help="blk key shift (BLK_SHIFT, default 100)")
ap.add_argument("--win", type=int, default=300)
ap.add_argument("--hot", type=float, default=3.0, help="excess lag points for a hot window")
ap.add_argument("--tsv")
ap.add_argument("--base")
ap.add_argument("--quiet", action="store_true", help="summary and hot zones only")
a = ap.parse_args()
shift = a.shift if a.shift is not None else (100 if a.layout == "blk" else 0)

# MAME: key -> [frames, first frame]
mf, mfirst, mkmax = collections.Counter(), {}, 0
for l in open(a.mame):
    x = l.split()
    if len(x) < 2:
        continue
    f, c = int(x[0]), int(x[1], 16)
    if a.layout == "both":
        k = c - 0x3E5          # shoot.lua KEY_UNWRAP already unwrapped it
    else:
        k = (c - 0x3E5 - shift) & 0xFFFF
    if mf and k < mkmax - 0x1000:
        break              # the run went on past the game's end (a new play timer): stop
    mkmax = max(mkmax, k)
    mf[k] += 1
    mfirst.setdefault(k, f)

# PS2: key -> [frames, first frame]; stage starts
pf, pfirst, starts = collections.Counter(), {}, []
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
    pfirst.setdefault(k, int(x[0]))

keys = sorted(k for k in pf if k in mf and k > 0)
if not keys:
    raise SystemExit("no common keys")
lo_k, hi_k = keys[0], keys[-1]


def stage_of(k):
    return 1 + sum(1 for s in starts if k >= s)


def eff(n):
    return 1 if n >= HOLD else n


wins = []          # (stage, k0, k1, steps, ps2 frames, mame frames, ps2 frame0, mame frame0)
by_stage = collections.defaultdict(list)
for k in keys:
    by_stage[stage_of(k)].append(k)
for st, ks in sorted(by_stage.items()):
    for i in range(0, len(ks), a.win):
        w = ks[i:i + a.win]
        if len(w) < a.win // 3:
            if wins and wins[-1][0] == st:       # fold a short tail into the previous window
                s, k0, _, n, p, m, p0, m0 = wins.pop()
                w2 = [k for k in ks if k0 <= k <= w[-1]]
                wins.append((s, k0, w[-1], len(w2), sum(eff(pf[k]) for k in w2),
                             sum(eff(mf[k]) for k in w2), p0, m0))
                continue
        wins.append((st, w[0], w[-1], len(w), sum(eff(pf[k]) for k in w),
                     sum(eff(mf[k]) for k in w), pfirst[w[0]], mfirst[w[0]]))


def lag(n, fr):
    return 100.0 * (fr - n) / fr if fr else 0.0


def speed(p, m):
    return (p / PS2_HZ) / (m / PGM_HZ) if m else 0.0


print(f"keys {lo_k:#x}-{hi_k:#x}, {len(keys)} steps compared; stage starts (PS2 keys): "
      + " ".join(hex(s) for s in starts))
print("\nper stage:")
print("stage  steps   PS2 frames lag%   steps/s | MAME frames lag%   steps/s | speed vs PS2")
for st, ks in sorted(by_stage.items()):
    n = len(ks)
    p = sum(eff(pf[k]) for k in ks)
    m = sum(eff(mf[k]) for k in ks)
    print(f"{st:5d} {n:6d}   {p:10d} {lag(n, p):5.1f} {n / (p / PS2_HZ):7.1f} | "
          f"{m:11d} {lag(n, m):5.1f} {n / (m / PGM_HZ):7.1f} | {speed(p, m):5.2f}")

base = {}
if a.base:
    for l in open(a.base):
        if l.startswith("#"):
            continue
        x = l.split("\t")
        base[(int(x[0]), int(x[1], 16))] = (int(x[2], 16), int(x[3]), int(x[5]))

hot = []
cur = None
for w in wins:
    st, k0, k1, n, p, m, p0, m0 = w
    ex = lag(n, m) - lag(n, p)
    bm = base.get((st, k0), (0, 0, 0))[2]
    if ex >= a.hot or (bm and lag(n, bm) - lag(n, p) >= a.hot):   # hot now or in the base run
        if cur and cur[0] == st and cur[-1][2] == wins[wins.index(w) - 1][2]:
            cur.append(w)
        else:
            cur = [st, w]
            hot.append(cur)
    else:
        cur = None

if not a.quiet:
    print(f"\nwindows of {a.win} steps (lag %; excess = MAME lag - PS2 lag; * = hot):")
    print("stage keys            PS2 frame  MAME frame  PS2 lag  MAME lag  excess  speed")
    for st, k0, k1, n, p, m, p0, m0 in wins:
        ex = lag(n, m) - lag(n, p)
        print(f"{st:5d} {k0:#07x}-{k1:#07x} {p0:9d} {m0:11d} {lag(n, p):8.1f} {lag(n, m):9.1f} "
              f"{ex:7.1f} {speed(p, m):6.2f}{' *' if ex >= a.hot else ''}")

print(f"\nhot zones (MAME lag {a.hot:g}+ points over the PS2's" + (", now or in the base run" if base else "") + "):")
print("stage keys            steps  PS2 frame  MAME frame  PS2 lag  MAME lag  extra frames"
      + ("  base MAME frames -> now" if base else ""))
tot = 0
for z in hot:
    st, ws = z[0], z[1:]
    k0, k1 = ws[0][1], ws[-1][2]
    n = sum(w[3] for w in ws)
    p = sum(w[4] for w in ws)
    m = sum(w[5] for w in ws)
    tot += m - p
    extra = ""
    if base:
        bm = sum(base.get((st, w[1]), (0, 0, 0))[2] for w in ws)
        extra = f"  {bm} -> {m}"
    print(f"{st:5d} {k0:#07x}-{k1:#07x} {n:6d} {ws[0][6]:9d} {ws[0][7]:11d} {lag(n, p):8.1f} "
          f"{lag(n, m):9.1f} {m - p:13d}{extra}")
print(f"total extra MAME frames in hot zones: {tot}; over the whole run: "
      f"{sum(w[5] for w in wins) - sum(w[4] for w in wins)}")

if a.tsv:
    with open(a.tsv, "w") as o:
        o.write("#stage\tkey0\tkey1\tsteps\tps2_frames\tmame_frames\tps2_frame0\tmame_frame0\n")
        for st, k0, k1, n, p, m, p0, m0 in wins:
            o.write(f"{st}\t{k0:#x}\t{k1:#x}\t{n}\t{p}\t{m}\t{p0}\t{m0}\n")
