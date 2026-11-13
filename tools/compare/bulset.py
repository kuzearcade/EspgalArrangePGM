"""bulset.py MAME_BUL PS2DIR: bullets as multisets (flag, x, y, speed, angle) per game counter.
The PS2 pool is 800 records at 0xC39C20; traces of 0xC840 bytes start there (ps2_traces.sh), older ones
(0xC800 / 0xCA00 bytes) start 8 records early, at 0xC39A20."""
import sys, collections
M = {}
for l in open(sys.argv[1]):
    x = l.split(); M[int(x[1], 16)] = (int(x[0]), x[2], bytes.fromhex(x[4]))
P = {}
for l in open(sys.argv[2] + '/watch.txt'):
    x = l.split()
    if x[0] == 'H': continue
    pool = bytes.fromhex(x[6])
    pool = pool[:800 * 0x40] if len(pool) == 0xC840 else pool[8 * 0x40:]
    P[(int(x[5][:4], 16) + 0x23F) & 0xFFFF] = (int(x[0]), x[2], pool)
def bag(b):
    c = collections.Counter()
    for n in range(len(b) // 0x40):
        r = b[n*0x40:n*0x40+0x40]
        if r[0] & 0x80: c[(r[2:4].hex(), r[4:6].hex(), r[0x1a], r[0x1b])] += 1
    return c
ks = sorted(k for k in P if k in M)
bad = [k for k in ks if bag(P[k][2]) != bag(M[k][2])]
kak = [k for k in ks if P[k][1] == '0002']
print('compared', len(ks), 'counters', hex(ks[0]), '-', hex(ks[-1]), '; Kakusei from', kak and hex(kak[0]), '; differing', len(bad))
if bad:
    k = bad[0]; p, m = bag(P[k][2]), bag(M[k][2])
    print('first', hex(k), 'ps2 frame', P[k][0], 'mame frame', M[k][0], 'ps2 only', list((p - m).elements())[:4], 'mame only', list((m - p).elements())[:4])
    runs = []
    for k in bad:
        if runs and k == runs[-1][1] + 1: runs[-1][1] = k
        else: runs.append([k, k])
    print('runs', [(hex(a), hex(b)) for a, b in runs[:10]])
