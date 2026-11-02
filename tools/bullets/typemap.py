"""typemap.py: bullet type -> run handler on the PS2 (runtime descriptors in a PCSX2 dump, the
run-init's installed $22) and on the 68k (descriptor +0x16 run-init, its `move.l #h,$22(a6)`).
Writes typemap.json {"ps2": {handler: type}, "m68k": {handler: type}}."""
import json, re, struct, sys, zipfile

DIS = open(os.environ['A0A9_DIS']).read().splitlines()
IDX = {int(l.split()[0], 16): i for i, l in enumerate(DIS) if l and l[0] in '0123456789abcdef'}


def ps2_installs(fn):
    """handler addresses a MIPS run-init loads with lui + addiu (code range 0x8xxxxx..0x9xxxxx)."""
    out = []
    i = IDX.get(fn)
    if i is None:
        return out
    for j in range(i, min(i + 1500, len(DIS))):
        m = re.match(r'[0-9a-f]+ lui \$(\w+), (0x[0-9a-f]+)$', DIS[j])
        if m:
            hi = int(m.group(2), 16)
            for k in range(j + 1, min(j + 8, len(DIS))):
                m2 = re.match(rf'[0-9a-f]+ addiu \$\w+, \${m.group(1)}, (-?0x[0-9a-f]+|\d+)$', DIS[k])
                if m2:
                    a = (hi << 16) + int(m2.group(1), 0)
                    if 0x800000 <= a < 0xA00000:
                        out.append(a)
                    break
        if j > i and re.search(r' jr \$ra$', DIS[j]):
            break
    return out


ee = open(sys.argv[1], 'rb').read()          # a PCSX2 EE dump
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pathlib import Path
from espgalarr.ps2 import overlay
tab = struct.unpack('<41I', overlay.Overlay(Path(__file__).resolve().parents[2] / 'work' / 'ps2' / 'disc' / 'A0A0' / 'A0A9').read(0xC05000, 41 * 4))
ps2 = {}
for t, d in enumerate(tab):
    init = struct.unpack_from('>I', ee, d + 0x12)[0]
    run = struct.unpack_from('>I', ee, d + 0x16)[0]
    for h in [run] + ps2_installs(run) + (ps2_installs(init) if init else []):
        ps2.setdefault(f'{h:x}', t)
z = zipfile.ZipFile(sys.argv[2]).read('espgaluda_u8.bin')
p = bytes(x for i in range(0, len(z), 2) for x in (z[i + 1], z[i]))
btab = struct.unpack_from('>I', p, 0x171056 + 2)[0]       # the bullet type table the readers use
m68 = {}
for t in range(40):
    d = struct.unpack_from('>I', p, btab + 4 * t)[0]
    init, run = struct.unpack_from('>II', p, d + 0x12)
    m68[f'{run:x}'] = t
    m = re.search(rb'\x2d\x7c(....)\x00\x22', p[run:run + 0x200], re.S)    # the run-init's own $22
    if m:
        m68[f'{struct.unpack(">I", m.group(1))[0]:x}'] = t
json.dump({'ps2': ps2, 'm68k': m68}, open('typemap.json', 'w'), indent=0)
print('ps2 handlers', len(ps2), 'm68k handlers', len(m68))
for h in ('909200', '928580', '961e60', '9276d0', '94a400'):
    print(' ps2', h, '-> type', hex(ps2[h]) if h in ps2 else None)
