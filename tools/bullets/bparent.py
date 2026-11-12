"""bparent.py STAGE PLAY TYPE...: for bullets of the given types (typemap.json), their emitters ($2A,
set by the fire routine): a parent bullet's type, or an enemy record (index and its task handler),
PS2 against MAME."""
import collections, json, os, struct, sys

# the PS2's enemy bullet pool (0xC39A20) has 808 records (its counter word and the walker variables
# 0xC46428 / 0xC46430 follow them) and does use the last 8; the port's has 800
PS2_SLOTS = 808

HERE = os.environ.get('BULLET_DIR', os.path.join(os.environ.get('COMPARE_DIR', os.path.join(os.path.dirname(__file__), '..', '..', 'work', 'compare')), 'bullets'))  # bsamples.json, ps2bul/, mbul_*/
TM = json.load(open(os.path.join(HERE, 'typemap.json')))


def emitters(b, base, recs, recsize, m, want, poolbase_addr):
    c = collections.Counter()
    for n in range(PS2_SLOTS if base == 0xC39A20 else 800):
        r = b[base + 0x40 * n:base + 0x40 * n + 0x40]
        if not r[0] & 0x80:
            continue
        h = f'{struct.unpack(">I", r[0x22:0x26])[0]:x}'
        t = m.get(h)
        if t not in want:
            continue
        e = struct.unpack('>I', r[0x2a:0x2e])[0]
        if poolbase_addr <= e < poolbase_addr + 800 * 0x40:
            pr = b[base + (e - poolbase_addr):base + (e - poolbase_addr) + 0x40]
            ph = f'{struct.unpack(">I", pr[0x22:0x26])[0]:x}'
            c[(t, 'bullet', m.get(ph, '?' + ph))] += 1
        elif recs <= e < recs + 120 * recsize:
            c[(t, 'record', (e - recs) // recsize)] += 1
        else:
            c[(t, 'other', f'{e:x}')] += 1
    return c


s = {(st, p): f for st, p, f in json.load(open(os.path.join(HERE, 'bsamples.json')))}
st, p = int(sys.argv[1]) - 1, int(sys.argv[2])
want = {int(x, 16) for x in sys.argv[3:]}
P = emitters(open(os.path.join(HERE, f'ps2bul/ee_{s[(st, p)]}.bin'), 'rb').read(), 0xC39A20, 0xC27A10, 0x58,
             TM['ps2'], want, 0xC39A20)
mf = f'mbul_b0_a/k_{p + 0x3e5:x}.bin' if st == 0 else f'mbul_b0_c/p_{st}_{p:x}.bin'
M = emitters(open(os.path.join(HERE, mf), 'rb').read(), 0x30000, 0xE08200, 0x2C, TM['m68k'], want, 0xD00000)
print('PS2 ', sorted(P.items()))
print('MAME', sorted(M.items()))
