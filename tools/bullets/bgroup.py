"""bgroup.py STAGE PLAY: bullets of one sample grouped by (speed, handler), with their angle sets, on
both sides, so a group whose speed differs between them shows as the same angles at another speed."""
import collections, json, os, struct, sys

HERE = os.environ.get('BULLET_DIR', os.getcwd())     # working directory: bsamples.json, ps2bul/, mbul_*/


def groups(b, base):
    g = collections.defaultdict(collections.Counter)
    for n in range(800):
        r = b[base + 0x40 * n:base + 0x40 * n + 0x40]
        if r[0] & 0x80:
            g[(r[0x1a], f'{struct.unpack(">I", r[0x22:0x26])[0]:x}', r[0x0a:0x0e].hex())][r[0x1b]] += 1
    return g


s = {(st, p): f for st, p, f in json.load(open(os.path.join(HERE, 'bsamples.json')))}
st, p = int(sys.argv[1]) - 1, int(sys.argv[2])
P = groups(open(os.path.join(HERE, f'ps2bul/ee_{s[(st, p)]}.bin'), 'rb').read(), 0xC39A20)
mf = f'mbul_b0_a/k_{p + 0x3e5:x}.bin' if st == 0 else f'mbul_b0_c/p_{st}_{p:x}.bin'
M = groups(open(os.path.join(HERE, mf), 'rb').read(), 0x30000)
for name, G in (('PS2', P), ('MAME', M)):
    print(name)
    for k in sorted(G):
        print('  speed %2d handler %6s sprite %s: %d bullets, angles %s' % (k[0], k[1], k[2], sum(G[k].values()),
              sorted(G[k])[:24]))
