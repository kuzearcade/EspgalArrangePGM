"""btypes.py MAMEDIR_A MAMEDIR_C: bullets per type at each sample (typemap.json maps run handlers to
types), PS2 against MAME; prints the types whose counts differ ("?h" = an unmapped handler h)."""
import collections, json, os, struct, sys

# the PS2's enemy bullet pool: 800 records at 0xC39C20 (its counter word 0xC46420 and the walker
# variables 0xC46428 / 0xC46430 follow them), as the port's at 0xD00000
PS2_SLOTS = 800

HERE = os.environ.get('BULLET_DIR', os.path.join(os.environ.get('COMPARE_DIR', os.path.join(os.path.dirname(__file__), '..', '..', 'work', 'compare')), 'bullets'))  # bsamples.json, ps2bul/, mbul_*/
TM = json.load(open(os.path.join(HERE, 'typemap.json')))


def types(b, base, m):
    c = collections.Counter()
    for n in range(PS2_SLOTS if base == 0xC39C20 else 800):
        r = b[base + 0x40 * n:base + 0x40 * n + 0x40]
        if r[0] & 0x80:
            h = f'{struct.unpack(">I", r[0x22:0x26])[0]:x}'
            c[f'{m[h]:02x}' if h in m else '?' + h] += 1
    return c


ma, mc = sys.argv[1:3]
tot = collections.Counter()
for st, play, frame in json.load(open(os.path.join(HERE, 'bsamples.json'))):
    mf = os.path.join(ma, f'k_{play + 0x3e5:x}.bin') if st == 0 else os.path.join(mc, f'p_{st}_{play:x}.bin')
    pf = os.path.join(HERE, 'ps2bul', f'ee_{frame}.bin')
    if not (os.path.exists(mf) and os.path.exists(pf)):
        continue
    p = types(open(pf, 'rb').read(), 0xC39C20, TM['ps2'])
    m = types(open(mf, 'rb').read(), 0x30000, TM['m68k'])
    d = {t: (p[t], m[t]) for t in sorted(set(p) | set(m)) if p[t] != m[t]}
    for t, (a, b) in d.items():
        tot[t] += a - b
    print(f'stage {st + 1} play {play:5}: ' + (' '.join(f'{t}:{a}/{b}' for t, (a, b) in d.items()) or 'same per type'))
print('PS2 minus MAME, summed over samples:', dict(sorted(tot.items(), key=lambda x: -abs(x[1]))))
