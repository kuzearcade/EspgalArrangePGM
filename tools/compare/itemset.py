"""itemset.py MAME_ITEMS PS2DIR: gems / gold items as multisets (flag, x, y, +0x14..+0x1D) per game
counter: MAME ITEM_LOG (shoot.lua) against a PCSX2 watch of 0x3586CC (count) and 0xC2B240 (pool)."""
import sys, collections
M = {}
for l in open(sys.argv[1]):
    x = l.split(); M[int(x[1], 16)] = (int(x[0]), int(x[3], 16), bytes.fromhex(x[4]))
P = {}
for l in open(sys.argv[2] + '/watch.txt'):
    x = l.split()
    if x[0] == 'H': continue
    P[(int(x[3][:4], 16) + 0x23F) & 0xFFFF] = (int(x[0]), int(x[4][:4], 16), bytes.fromhex(x[5]))
def bag(b):
    c = collections.Counter()
    for n in range(len(b) // 30):
        r = b[n * 30:n * 30 + 30]
        if r[0] & 0x80: c[(r[2:4].hex(), r[4:6].hex(), r[0x14:0x1e].hex())] += 1
    return c
ks = sorted(k for k in P if k in M)
bad = [k for k in ks if bag(P[k][2]) != bag(M[k][2]) or P[k][1] != M[k][1]]
live = [k for k in ks if P[k][1]]
print('compared', len(ks), 'counters', hex(ks[0]), '-', hex(ks[-1]), '; with items', len(live),
      '; max count ps2', max(P[k][1] for k in ks), 'mame', max(M[k][1] for k in ks), '; differing', len(bad))
for k in bad[:3]:
    p, m = bag(P[k][2]), bag(M[k][2])
    print(hex(k), 'count', P[k][1], M[k][1], 'ps2 only', list((p - m).elements())[:3], 'mame only', list((m - p).elements())[:3])
