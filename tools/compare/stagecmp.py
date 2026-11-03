"""stagecmp.py MAME_TIMING PS2DIR BASE MAME_FROM [MAME_TO]: one stage of the no-input run keyed on
play frames: MAME 0x813A1E (column 6) from MAME_FROM (the stage's first frame) = PS2 0x642298 -
BASE (0x642298 is total play time: stage 2 BASE 0x400A, stage 3 0x400A + 29667)."""
import sys
base, mfrom = int(sys.argv[3], 0), int(sys.argv[4])
mto = int(sys.argv[5]) if len(sys.argv) > 5 else 10**9
import os
PS2_FROM, PS2_TO = int(os.getenv('PS2_FROM', '0')), int(os.getenv('PS2_TO', '0'))
M = {}
for l in open(sys.argv[1]):
    x = l.split(); f = int(x[0])
    if mfrom <= f <= mto: M[int(x[5], 16)] = (f, x[6])   # last frame of each value: lag frames end mid logic frame
P = {}
for l in open(sys.argv[2] + '/watch.txt'):
    x = l.split()
    if x[0] == 'H': continue
    if PS2_TO and int(x[0]) > PS2_TO: break
    k = (int(x[1][:4], 16) - base) & 0xffff   # 16-bit play time wraps
    if int(x[0]) >= PS2_FROM: P.setdefault(k, (int(x[0]), x[2]))
ks = sorted(k for k in P if k in M)
first = None; nd = 0
for k in ks:
    p, m = P[k][1], M[k][1]
    d = [r for r in range(120) if (p[r*0x58:r*0x58+4] != '0000' or m[r*12:r*12+4] != '0000') and p[r*0x58:r*0x58+12] != m[r*12:r*12+12]
         and not (p[r*0x58:r*0x58+4] == '8000' and m[r*12:r*12+4] == '0000' and p[r*0x58+4:r*0x58+12] == m[r*12+4:r*12+12])]
    if d:
        nd += 1
        if first is None: first = (k, P[k][0], M[k][0], d[:6], [p[r*0x58:r*0x58+12] for r in d[:3]], [m[r*12:r*12+12] for r in d[:3]])
print('compared', len(ks), 'play frames', ks[0] if ks else None, '-', ks[-1] if ks else None, '; differing', nd)
print('first', first)
