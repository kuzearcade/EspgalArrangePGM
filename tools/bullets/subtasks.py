"""subtasks.py STAGE PLAY: the stage task table pointer (68k 0x812D88 / PS2 0xC475B4) and the active
sub-task slots (0x812D8C / 0xC475B8, 0x40 each, 20 slots), PS2 against MAME (first 0x18 bytes)."""
import json, os, struct, sys

HERE = os.environ.get('BULLET_DIR', os.getcwd())     # working directory: bsamples.json, ps2bul/, mbul_*/
s = {(st, p): f for st, p, f in json.load(open(os.path.join(HERE, 'bsamples.json')))}
st, p = int(sys.argv[1]) - 1, int(sys.argv[2])
pd = open(os.path.join(HERE, f'ps2bul/ee_{s[(st, p)]}.bin'), 'rb').read()
mf = f'mbul_b0_a/k_{p + 0x3e5:x}.bin' if st == 0 else f'mbul_b0_c/p_{st}_{p:x}.bin'
md = open(os.path.join(HERE, mf), 'rb').read()
print('table PS2', pd[0xC475B4:0xC475B8].hex(), 'MAME', md[0x12D88:0x12D8C].hex())
for n in range(20):
    a, b = pd[0xC475B8 + 0x40 * n:0xC475B8 + 0x40 * n + 0x18], md[0x12D8C + 0x40 * n:0x12D8C + 0x40 * n + 0x18]
    if a[0] or b[0]:
        print(f'slot {n:2}: PS2 {a.hex(" ", 2)}\n          MAME {b.hex(" ", 2)}')
