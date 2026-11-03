"""poolctr.py MAME_TIMING PS2DIR [MAME_FRAME...]: stage 1 of the no-input run, enemy hitbox pools
PS2 vs MAME keyed by each game's frame counter (MAME lag frames repeat a counter value: the last
frame of each value is kept). PS2DIR: the ps2long trace. Frames given: their differing records."""
import sys
ps2, mame, lag = {}, {}, 0
for l in open(sys.argv[2] + '/watch.txt'):
    x = l.split(); ps2[int(x[1][:4], 16)] = (int(x[0]), bytes.fromhex(x[2]))
prev = None
for l in open(sys.argv[1]):
    x = l.split(); c = int(x[4], 16)
    if c == prev: lag += 1
    prev = c; mame[c] = (int(x[0]), x[6])
off = 0x0b08 - 0x0723
cs = sorted(c for c in mame if c - off in ps2 and mame[c][0] >= 7200 and c - off < 0x400a)   # stage 1: the PS2 holds play time 0x400A through the clear
bad = []
for c in cs:
    p = ps2[c - off][1]; m = mame[c][1]
    if any(p[k*0x2c:k*0x2c+6].hex() != m[k*12:k*12+12] for k in range(120)): bad.append(c)
runs = []
for c in bad:
    if runs and c == runs[-1][1] + 1: runs[-1][1] = c
    else: runs.append([c, c])
print('mame lag frames', lag, 'compared', len(cs), 'counters; differing runs (mame frame):',
      [(mame[a][0], mame[b][0]) for a, b in runs[:12]])
byf = {v[0]: c for c, v in mame.items()}
for f in map(int, sys.argv[3:]):
    c = byf.get(f)
    if c is None or c - off not in ps2: print(f, 'no data'); continue
    p = ps2[c - off]; m = mame[c]
    print(f, 'ctr', hex(c), 'ps2 frame', p[0])
    for k in range(120):
        a = p[1][k*0x2c:k*0x2c+6].hex(); b = m[1][k*12:k*12+12]
        if a != b: print('  rec', k, 'ps2', a, 'mame', b)
