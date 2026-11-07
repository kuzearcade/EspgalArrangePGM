"""bothcmp.py MAME_SHOOT PS2DIR [N]: the both-shots run (bothrun.sh, shoot.lua KEY_TIMER
KEY_UNWRAP) against ps2both_full, keyed on the unwrapped logic frame (PS2 0x642298 + 0x10000 per
wrap = MAME key - 0x3E5; the last MAME frame of a key). As longcmp.py (blk layout: 642298:10,
c27a10, 358048:8, 358028:2, c2b0d0, c310c0): the runs of keys where the enemy records' first
words, the score or P1's x / y differ, then the stage segments (keys held 150+ frames on the
PS2) with each one's first and last key."""
import sys

mame_f, ps2_d = sys.argv[1], sys.argv[2]
nshow = int(sys.argv[3]) if len(sys.argv) > 3 else 12

mame = {}
for l in open(mame_f):
    f, c, sc, p1, pool = l.split()
    mame[int(c, 16) - 0x3E5] = (int(f), int(sc, 16), p1, pool)
ps2, hi, last, held = {}, 0, None, []
for l in open(ps2_d + "/watch.txt"):
    x = l.split()
    if x[0] == "H":
        continue
    k = int(x[1][:4], 16)
    if last is not None and k < last and last - k > 0x8000:
        hi += 0x10000
    if k == last:
        held[-1][1] += 1
    else:
        held.append([hi + k, 1])
    last = k
    ps2.setdefault(hi + k, (int(x[0]), int(x[5][4:12], 16), x[6], x[2]))


def recdiff(p, m):
    return [r for r in range(120) if p[r * 88:r * 88 + 12] != m[r * 88:r * 88 + 12]
            and not (p[r * 88:r * 88 + 4] == "8000" and m[r * 88:r * 88 + 4] == "0000")]


ks = sorted(k for k in ps2 if k in mame and k > 0)
print("compared", len(ks), "keys", hex(ks[0]), "-", hex(ks[-1]))
print("stage changes (PS2 key held 150+ frames):", " ".join(hex(k) for k, n in held if n >= 150))
for name, fn in (("records", lambda k: recdiff(ps2[k][3], mame[k][3])),
                 ("score", lambda k: [] if ps2[k][1] == mame[k][1] else [ps2[k][1], mame[k][1]]),
                 ("p1 x/y", lambda k: [] if ps2[k][2][4:12] == mame[k][2][4:12] else [ps2[k][2][4:12], mame[k][2][4:12]])):
    runs, cur = [], None
    for k in ks:
        d = fn(k)
        if d:
            if cur and k <= cur[1] + 2:
                cur[1] = k
            else:
                cur = [k, k, d[:6], ps2[k][0], mame[k][0]]
                runs.append(cur)
    print(name, len(runs), "runs")
    for a, b, d, pf, mf in runs[:nshow]:
        print(f"  keys {a:#x}-{b:#x} ({b - a + 1}) PS2 frame {pf} MAME frame {mf}: {d}")
