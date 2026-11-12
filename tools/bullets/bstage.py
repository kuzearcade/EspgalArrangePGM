"""bstage.py PS2DIR MAMEDIR_A MAMEDIR_C [-v]: enemy bullets per sample (bsamples.json), PS2 dump
ee_<frame>.bin (pool 0xC39A20) against the MAME dump at the same play frame (stage 1: k_<key>.bin,
key = play + 0x3E5; stages 2..6: p_<stage>_<play>.bin; pool at dump offset 0x30000). Bullets are
compared as multisets of (x, y, speed $1A, angle $1B), slot order ignored."""
import collections, json, os, sys

HERE = os.environ.get('BULLET_DIR', os.path.join(os.environ.get('COMPARE_DIR', os.path.join(os.path.dirname(__file__), '..', '..', 'work', 'compare')), 'bullets'))  # bsamples.json, ps2bul/, mbul_*/
SLOTS, SIZE = 800, 0x40
# the PS2's pool has 808 records (its counter word and the walker variables 0xC46428 / 0xC46430
# follow them) and does use the last 8; the port's has 800
PS2_SLOTS = 808


def bag(b, base):
    c = collections.Counter()
    for n in range(PS2_SLOTS if base == 0xC39A20 else SLOTS):
        r = b[base + SIZE * n:base + SIZE * n + SIZE]
        if len(r) == SIZE and r[0] & 0x80:
            c[(r[2:4].hex(), r[4:6].hex(), r[0x1a], r[0x1b])] += 1
    return c


def main():
    ps2, ma, mc = sys.argv[1:4]
    verbose = '-v' in sys.argv
    tot = collections.Counter()
    for st, play, frame in json.load(open(os.path.join(HERE, 'bsamples.json'))):
        pf = os.path.join(ps2, f'ee_{frame}.bin')
        mf = os.path.join(ma, f'k_{play + 0x3e5:x}.bin') if st == 0 else os.path.join(mc, f'p_{st}_{play:x}.bin')
        if not (os.path.exists(pf) and os.path.exists(mf)):
            print(f'stage {st + 1} play {play:5}: missing {"ps2" if not os.path.exists(pf) else "mame"} dump')
            continue
        p = bag(open(pf, 'rb').read(), 0xC39A20)
        m = bag(open(mf, 'rb').read(), 0x30000)
        common = sum((p & m).values())
        np_, nm = sum(p.values()), sum(m.values())
        same = p == m
        tot['samples'] += 1; tot['same'] += same
        sp = collections.Counter(k[2] for k in p.elements()); sm = collections.Counter(k[2] for k in m.elements())
        print(f'stage {st + 1} play {play:5}: ps2 {np_:4} mame {nm:4} common {common:4} {"SAME" if same else ""}'
              + ('' if same else f'  speeds ps2 {dict(sorted(sp.items()))} mame {dict(sorted(sm.items()))}' if verbose else ''))
    print('identical samples', tot['same'], 'of', tot['samples'])


if __name__ == '__main__':
    main()
