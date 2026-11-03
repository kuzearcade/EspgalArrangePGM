"""stage2cmp.py MAME_TIMING PS2DIR [PS2_BASE]: stage 2 of the no-input run keyed on play frames
(MAME 0x813A1E, column 6 of timing_xl output; PS2 0x642298 - PS2_BASE, its value at the stage 1
clear). Live records only (the PS2 zeroes the pool at the stage change, MAME leaves stale words)."""
import sys
base = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x400a
M = {}
for l in open(sys.argv[1]):
    x = l.split()
    if int(x[2], 16) == 0 and int(x[1], 16) < 0x100 and x[5] != '0000' and int(x[0]) > 18000:
        pass
    if int(x[0]) > 18000 and int(x[1], 16) < 0x400 and M.get('stage2') is None and int(x[5], 16) < 4:
        M['stage2'] = int(x[0])
    if M.get('stage2') and int(x[0]) >= M['stage2']:
        v = int(x[5], 16)
        if M.get("last", -1) > v + 100: M["done"] = 1
        if not M.get("done"): M[v] = (int(x[0]), x[6]); M["last"] = v
P = {}
started = False
for l in open(sys.argv[2] + '/watch.txt'):
    x = l.split()
    if x[0] == 'H': continue
    c = int(x[1][:4], 16)
    if c > base: started = True
    if started: P[c - base] = (int(x[0]), x[2])
ks = sorted(k for k in P if k in M and isinstance(k, int) and k >= 0)
first = None; ndiff = 0
for k in ks:
    p, m = P[k][1], M[k][1]
    # (a record the PS2 shows as 0x8000 with no position for one frame: freed, the blip at 2564)
    d = [r for r in range(120) if (p[r*0x58:r*0x58+4] != '0000' or m[r*12:r*12+4] != '0000')
         and p[r*0x58:r*0x58+12] != m[r*12:r*12+12] and not (p[r*0x58:r*0x58+4] == '8000' and m[r*12:r*12+4] == '0000')]
    if d:
        ndiff += 1
        if first is None: first = (k, P[k][0], M[k][0], d[:6], [p[r*0x58:r*0x58+12] for r in d[:3]], [m[r*12:r*12+12] for r in d[:3]])
print('compared', len(ks), 'play frames, stage 2 from MAME frame', M['stage2'], '; differing', ndiff)
print('first', first)
