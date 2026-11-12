"""bshift.py STAGE PLAY: how many PS2 bullets MAME has at position + k * velocity (same speed and
angle) for k = -4..4, to tell a sample-timing shift from a real difference."""
import json, os, struct, sys

# the PS2's enemy bullet pool (0xC39A20) has 808 records (its counter word and the walker variables
# 0xC46428 / 0xC46430 follow them) and does use the last 8; the port's has 800
PS2_SLOTS = 808

HERE = os.environ.get('BULLET_DIR', os.path.join(os.environ.get('COMPARE_DIR', os.path.join(os.path.dirname(__file__), '..', '..', 'work', 'compare')), 'bullets'))  # bsamples.json, ps2bul/, mbul_*/


def rows(b, base):
    out = []
    for n in range(PS2_SLOTS if base == 0xC39A20 else 800):
        r = b[base + 0x40 * n:base + 0x40 * n + 0x40]
        if r[0] & 0x80:
            x, y = struct.unpack('>HH', r[2:6]); vx, vy = struct.unpack('>hh', r[0x1e:0x22])
            out.append((x, y, r[0x1a], r[0x1b], vx, vy))
    return out


s = {(st, p): f for st, p, f in json.load(open(os.path.join(HERE, 'bsamples.json')))}
st, p = int(sys.argv[1]) - 1, int(sys.argv[2])
P = rows(open(os.path.join(HERE, f'ps2bul/ee_{s[(st, p)]}.bin'), 'rb').read(), 0xC39A20)
mf = f'mbul_b0_a/k_{p + 0x3e5:x}.bin' if st == 0 else f'mbul_b0_c/p_{st}_{p:x}.bin'
M = set((x, y, sp, a) for x, y, sp, a, _, _ in rows(open(os.path.join(HERE, mf), 'rb').read(), 0x30000))
for k in range(-4, 5):
    n = sum(((x + k * vx) & 0xffff, (y + k * vy) & 0xffff, sp, a) in M for x, y, sp, a, vx, vy in P)
    print(f'k={k:+d}: {n} of {len(P)}')
