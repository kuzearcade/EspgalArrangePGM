"""shootcmp.py MAME PS2DIR [OFF]: shooting run, keyed on the game frame counter."""
import sys
off = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x3e5
mame = {}
for l in open(sys.argv[1]):
    f, c, sc, p1, pool = l.split(); mame[int(c, 16)] = (int(f), int(sc, 16), bytes.fromhex(p1), bytes.fromhex(pool))
ps2 = {}
for l in open(sys.argv[2] + '/watch.txt'):
    x = l.split()
    if x[0] == 'H': continue
    f = int(x[0]); ctr, pool, sc, p1 = x[1], x[2], x[3], x[4]
    ps2[int(ctr[:4], 16)] = (f, int(sc[4:12], 16), bytes.fromhex(p1), bytes.fromhex(pool))
# Enemy records +6 / +0xA hold pointers to their enemy-script slots: PS2 0xC267A0.. is MAME
# 0x814410.. (the same 0x50-byte slots), so PS2 pointers are translated before comparing
SLOT_PS2, SLOT_MAME, SLOT_LEN = 0xC267A0, 0x814410, 0x33EE
def rec_ps2(r):
    r = bytearray(r)
    for o in (6, 0xA):
        v = int.from_bytes(r[o:o + 4], 'big')
        if SLOT_PS2 <= v < SLOT_PS2 + SLOT_LEN: r[o:o + 4] = (v - SLOT_PS2 + SLOT_MAME).to_bytes(4, 'big')
    return bytes(r)
cs = sorted(c for c in mame if c - off in ps2 and mame[c][0] >= 1860)
first = {}
for c in cs:
    m, p = mame[c], ps2[c - off]
    if 'score' not in first and m[1] != p[1]: first['score'] = (m[0], m[1], p[1])
    pos = [k for k in range(120) if m[3][k*0x2c:k*0x2c+6] != p[3][k*0x2c:k*0x2c+6]]
    if 'pool' not in first and pos: first['pool'] = (m[0], pos[:8])
    mb = [k for k in range(91, 102) if m[3][k*0x2c:k*0x2c+0x28] != rec_ps2(p[3][k*0x2c:k*0x2c+0x28])]
    if 'midboss' not in first and mb: first['midboss'] = (m[0], mb)
    if 'p1' not in first and m[2][:0x40] != p[2][:0x40]:
        first['p1'] = (m[0], [hex(k) for k in range(0x40) if m[2][k] != p[2][k]][:10])
print('compared', len(cs), 'frames', mame[cs[0]][0], '-', mame[cs[-1]][0])
for k, v in first.items(): print(k, v)
if len(sys.argv) > 4:
    lo, hi = map(int, sys.argv[4].split('-'))
    byf = {v[0]: c for c, v in mame.items()}
    for f in range(lo, hi):
        c = byf.get(f)
        if c is None or c - off not in ps2: continue
        m, p = mame[c], ps2[c - off]
        print(f, 'score', m[1], p[1], 'p1 x,y', m[2][2:6].hex(), p[2][2:6].hex())
