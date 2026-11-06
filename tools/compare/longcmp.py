"""longcmp.py MAME_SHOOT PS2DIR [kak|blk] [SHIFT] [N]: a long shooting run (shoot.lua KEY_TIMER)
against its PCSX2 trace, keyed on the logic frame (PS2 0x642298 = MAME key - 0x3E5 - SHIFT; the
last MAME frame of a key, as lag frames repeat it). Prints the runs of keys where the enemy
records differ (first 6 bytes of the 120 0x2C records at PS2 0xC27A10 / MAME 0xE08200; a freed
PS2 record 0x8000.. = MAME 0x0000), the score (PS2 0xC2B0D2 = MAME 0x81B57E) or P1's x / y.
Layouts: kak = ps2kak7 (642298:10, c27a10, c2b0d0, c310c0, ...), blk = ps2blk7 (642298:10,
c27a10, 358048:8, 358028:2, c2b0d0, c310c0). A single-key run at a stage change or a boss's
last frame is expected."""
import sys

mame_f, ps2_d = sys.argv[1], sys.argv[2]
lay = sys.argv[3] if len(sys.argv) > 3 else "kak"
shift = int(sys.argv[4]) if len(sys.argv) > 4 else 0
nshow = int(sys.argv[5]) if len(sys.argv) > 5 else 10
SC, P1 = (3, 4) if lay == "kak" else (5, 6)

mame = {}
for l in open(mame_f):
    f, c, sc, p1, pool = l.split()
    mame[(int(c, 16) - 0x3E5 - shift) & 0xFFFF] = (int(f), int(sc, 16), p1, pool)
ps2 = {}
for l in open(ps2_d + "/watch.txt"):
    x = l.split()
    if x[0] == "H":
        continue
    ps2.setdefault(int(x[1][:4], 16), (int(x[0]), int(x[SC][4:12], 16), x[P1], x[2]))


def recdiff(p, m):
    return [r for r in range(120) if p[r * 88:r * 88 + 12] != m[r * 88:r * 88 + 12]
            and not (p[r * 88:r * 88 + 4] == "8000" and m[r * 88:r * 88 + 4] == "0000")]


ks = sorted(k for k in ps2 if k in mame and k > 0)
print("compared", len(ks), "keys", hex(ks[0]), "-", hex(ks[-1]))
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
