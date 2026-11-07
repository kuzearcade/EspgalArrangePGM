"""firecmp.py DIR MAMEDIR [-v]: bullet samples under fire: per key, the PS2's enemy bullets (DIR/ps2/
ee_<frame>.bin, pool 0xC39A20) against MAME's (MAMEDIR/t_<timer>.bin from firepool.sh, pool at 0), as
multisets of (x, y, speed $1A, angle $1B) as bstage.py compares them. The PS2's slots 0..3 can hold
records that are no bullets: all four when slot 0 starts 0x8314 (the static ones common.py drops), and
any of them at y 0 with speed and angle 0 (left at stage 5's boss and stage 6's end)."""
import collections
import os
import sys


def bag(b, base, ps2):
    c = collections.Counter()
    static = ps2 and b[base:base + 2] == b'\x83\x14'
    for n in range(800):
        r = b[base + 0x40 * n:base + 0x40 * n + 0x40]
        if len(r) < 0x40 or not r[0] & 0x80:
            continue
        if ps2 and n < 4 and (static or (r[4:6] == b'\0\0' and r[0x1a] == 0 and r[0x1b] == 0)):
            continue
        c[(r[2:4].hex(), r[4:6].hex(), r[0x1a], r[0x1b])] += 1
    return c


def main():
    d, mamed = sys.argv[1], sys.argv[2]
    timers = [int(x, 16) for x in open(f'{d}/timers.txt').read().split(',')]
    frames = [int(x) for x in open(f'{d}/frames.txt').read().split(',')]
    v = '-v' in sys.argv
    same = tot = 0
    for t, f in zip(timers, frames):
        mf, pf = f'{mamed}/t_{t:04x}.bin', f'{d}/ps2/ee_{f}.bin'
        if not (os.path.exists(mf) and os.path.exists(pf)):
            print(f'key {t - 0xB2:#x}: missing {"MAME" if not os.path.exists(mf) else "PS2"} dump')
            continue
        p = bag(open(pf, 'rb').read(), 0xC39A20, True)
        m = bag(open(mf, 'rb').read(), 0, False)
        op, om = p - m, m - p
        tot += 1
        same += p == m
        print(f'key {t - 0xB2:#x} (ps2 frame {f}): ps2 {sum(p.values())} mame {sum(m.values())}'
              + ('  SAME' if p == m else f'  ps2-only {sum(op.values())} mame-only {sum(om.values())}'))
        if v and p != m:
            print('   ps2 only :', sorted(op.elements())[:12])
            print('   mame only:', sorted(om.elements())[:12])
    print('identical', same, 'of', tot)


if __name__ == '__main__':
    main()
