"""bosstask.py STAGE PLAY [RECORD]: the enemy task slots (68k 0x814410 / PS2 0xC267A0, 0x50 each) whose
object ($6) is the given record (default 90 = PS2 45), with their run handler ($4C), and the stage
task slots (0x812D8C / 0xC475B8, 0x40 each) that are active, PS2 against MAME."""
import json, os, struct, sys

HERE = os.environ.get('BULLET_DIR', os.getcwd())     # working directory: bsamples.json, ps2bul/, mbul_*/
s = {(st, p): f for st, p, f in json.load(open(os.path.join(HERE, 'bsamples.json')))}
st, p = int(sys.argv[1]) - 1, int(sys.argv[2])
rec = int(sys.argv[3]) if len(sys.argv) > 3 else 90
pd = open(os.path.join(HERE, f'ps2bul/ee_{s[(st, p)]}.bin'), 'rb').read()
mf = f'mbul_b0_a/k_{p + 0x3e5:x}.bin' if st == 0 else f'mbul_b0_c/p_{st}_{p:x}.bin'
md = open(os.path.join(HERE, mf), 'rb').read()
for name, d, tasks, tbase, recs, rsize in (('PS2', pd, 0xC267A0, 0, 0xC27A10, 0x58), ('MAME', md, 0x814410, 0x800000, 0xE08200, 0x2C)):
    obj = recs + (rec // 2 if name == 'PS2' else rec) * rsize
    print(name, 'object', hex(obj))
    for n in range(0x3a):
        a = tasks - tbase + 0x50 * n
        slot = d[a:a + 0x50]
        if struct.unpack('>I', slot[6:10])[0] == obj and slot[0] & 0x80:
            print('  task', n, 'handler $4c', hex(struct.unpack('>I', slot[0x4c:0x50])[0]), 'type $c', hex(slot[0xc]))
