"""bsamples.py: sample points for the per-stage bullet comparison: (stage index, play, PS2 frame)."""
import json
specs = [  # stage index (0x81399E), trace, base key, PS2 from, PS2 to, plays
    (0, 'ps2long', 0, 9000, 19879, range(6000, 16394, 1000)),
    (1, 'ps2long34', 0x400a, 20322, 50500, range(1000, 29667, 2500)),
    (2, 'ps2long45', 0x400a + 29667, 50729, 84778, range(1000, 33878, 3000)),
    (3, 'ps2long6', 0x3843, 85252, 117538, range(1000, 31718, 3000)),
    (4, 'ps2long7', 0xb429, 117945, 135765, range(1000, 17633, 2000)),
    (5, 'ps2long7', 0xf90a, 135935, 200000, range(1000, 63466, 4000)),
]
out = []
for st, d, base, pf, pt, plays in specs:
    want = set(plays); got = {}
    for l in open(d + '/watch.txt'):
        x = l.split()
        if x[0] == 'H':
            continue
        f = int(x[0])
        if f < pf or f > pt:
            continue
        k = (int(x[1][:4], 16) - base) & 0xffff
        if k in want and k not in got:
            got[k] = f
    for k in sorted(got):
        out.append((st, k, got[k]))
    print(st, len(got), 'of', len(want), sorted(want - set(got))[:5])
json.dump(out, open('bsamples.json', 'w'))
print(len(out), 'samples; PS2 frames', ','.join(str(f) for _, _, f in out))
