"""firekeys.py WATCH OUT_DIR (N | KEY...): keys of the both-shots run for bullet samples under fire.
With N: N keys per stage, evenly spread inside each stage (0x100 keys clear of its changes); else the
listed keys (hex). Writes OUT_DIR/timers.txt (the MAME play timer, key + 0xB2, hex) and
OUT_DIR/frames.txt (each key's first sample in WATCH, the merged both-shots trace's watch.txt with the
play key 0x642298 first; its 16-bit wrap is unwrapped) for pooldump.sh / ps2pool.sh / firecmp.py."""
import sys
from pathlib import Path

STAGES = [0xA0, 0x1B6E, 0x44D2, 0x822C, 0xBA78, 0xE3F3, 0xF725]   # stage changes; stage 6 to its boss kill
watch, out = sys.argv[1], Path(sys.argv[2])
if len(sys.argv) == 4 and len(sys.argv[3]) < 3:
    n = int(sys.argv[3])
    keys = []
    for lo, hi in zip(STAGES, STAGES[1:]):
        lo, hi = lo + 0x100, hi - 0x100
        keys += [lo + (hi - lo) * i // (n - 1) for i in range(n)]
else:
    keys = sorted({int(k, 16) for k in sys.argv[3:]})
want, ps2, hi, last = set(keys), {}, 0, None
with open(watch) as f:
    for line in f:
        fr, rest = line.split(' ', 1)
        if fr == 'H':
            continue
        k = int(rest[:4], 16)
        if last is not None and k < last and last - k > 0x8000:
            hi += 0x10000
        last = k
        k += hi
        if k in want and k not in ps2:
            ps2[k] = int(fr)
keys = [k for k in keys if k in ps2]
out.mkdir(parents=True, exist_ok=True)
(out / 'timers.txt').write_text(','.join(f'{k + 0xB2:x}' for k in keys))
(out / 'frames.txt').write_text(','.join(str(ps2[k]) for k in keys))
for k in keys:
    print(f'key {k:#x} timer {k + 0xB2:#x} ps2 frame {ps2[k]}')
