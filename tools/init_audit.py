"""init_audit.py: constant byte stores that differ between paired arcade-mode / Arrange stage
functions, placed in the ROM by matching the arcade function's store sequence against the ROM's
immediate stores (move.b/w/l #imm,d(An)). Catches byte constants hidden in word / long
immediates (`move.w #$204e,$6(a4)`) that the porter cannot align."""
import sys, re, struct, difflib, collections
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path
import capstone
from espgalarr.ps2 import overlay
from espgalarr.diff import mips_funcs, stagetables
from espgalarr.pgm import rom as rom_mod

W = Path(__file__).resolve().parents[1] / "work"
p = bytes(rom_mod.RomSet(W).p_rom_68k()[:0x200000])
md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_LITTLE_ENDIAN)
STORE = re.compile(r'(-?0x[0-9a-f]+|\d+)\((\$\w+)\)')


def mips_stores(ov, f):
    """[(offset, byte, mips address)] of constant byte stores, in order."""
    const, out = {}, []
    for i in md.disasm(ov.read(f.start, f.size), f.start):
        ops = i.op_str.split(', ')
        if i.mnemonic == 'sb':
            m = STORE.match(ops[1])
            if m and m.group(2) not in ('$at', '$gp', '$sp'):
                v = (0, None) if ops[0] == '$zero' else const.get(ops[0])
                if v is not None:
                    out.append((int(m.group(1), 0), v[0] & 0xFF, v[1]))
            continue
        if i.mnemonic in ('addiu', 'daddiu') and len(ops) == 3 and ops[1] == '$zero':
            const[ops[0]] = (int(ops[2], 0), i.address)
            continue
        if i.mnemonic.startswith(('s', 'b', 'j')) and i.mnemonic not in ('sll', 'srl', 'sra', 'subu', 'slt', 'sltu', 'slti', 'sltiu', 'sllv', 'srlv', 'srav', 'dsll', 'dsrl', 'dsra'):
            continue
        if ops and ops[0].startswith('$'):
            const.pop(ops[0], None)
    return out


# ROM immediate stores: (displacement, byte, rom byte address)
rom = []
OPS = {0x1: 1, 0x3: 2, 0x2: 4}           # move size bits 13-12
for a in range(0x100000, 0x1C0000, 2):
    w = struct.unpack_from('>H', p, a)[0]
    if w >> 14 == 0 and (w >> 12) & 3 in OPS and (w >> 6) & 7 == 5 and w & 0x3F == 0x3C:   # move.s #imm,d(An)
        n = OPS[(w >> 12) & 3]
        immat = a + 2 + (1 if n == 1 else 0)
        imm = p[immat:immat + n]
        disp = struct.unpack_from('>h', p, a + 2 + (2 if n < 4 else 4))[0]
        for k in range(n):
            rom.append((disp + k, imm[k], immat + k, a))
seq = [(d, b) for d, b, _, _ in rom]
index = collections.defaultdict(list)
for i in range(len(seq) - 3):
    index[tuple(seq[i:i + 4])].append(i)

found = []
for an, rn in stagetables.PAIRS[1:]:
    A = overlay.Overlay(W / 'ps2' / 'disc' / 'A0A0' / an)
    R = overlay.Overlay(W / 'ps2' / 'disc' / 'A0A0' / rn)
    fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
    fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
    for m in mips_funcs.match(fa, fb).matches:
        sa, sb = mips_stores(A, m.a), mips_stores(R, m.b)
        if len(sa) < 4 or [x[:2] for x in sa] == [x[:2] for x in sb]:
            continue
        ka = [x[:2] for x in sa]
        hits = [i for i in index.get(tuple(ka[:4]), []) if seq[i:i + len(ka)] == ka]
        if len(hits) != 1:
            print(f'{rn} {m.b.start:#x} (arcade {m.a.start:#x}): {len(hits)} ROM places for {len(ka)} stores')
            continue
        base = hits[0]
        conv = lambda d: d if d < 0x24 else d // 0x24 * 0x2C + d % 0x24
        best = None
        for f in (lambda d: d, conv):
            sm = difflib.SequenceMatcher(a=[f(x[0]) for x in sa], b=[x[0] for x in sb], autojunk=False)
            if best is None or sm.ratio() > best.ratio():
                best = sm
        sm = best
        ops = sm.get_opcodes()
        if any(t != 'equal' for t, *_ in ops):
            print(f'  {rn} {m.b.start:#x}: structural (ROM {rom[base][3]:#x}), not placed')
            continue
        for tag, i1, i2, j1, j2 in ops:
            if tag == 'equal':
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    if sa[i][1] != sb[j][1]:
                        d, b, at, ins = rom[base + i]
                        found.append((at, b, sb[j][1], rn, sb[j][2], ins, d))
            else:
                print(f'  {rn} {m.b.start:#x}: {tag} arcade stores {[(hex(o), hex(v)) for o, v, _ in sa[i1:i2]]} arrange {[(hex(o), hex(v)) for o, v, _ in sb[j1:j2]]} (ROM {rom[base + min(i1, len(sa) - 1)][3]:#x})')
for at, old, new, rn, mips, ins, d in sorted(found):
    print(f'ROM {at:#x} (instr {ins:#x}, ${d:x}): {old:#x} -> {new:#x}   {rn} {mips:#x}' if mips else f'ROM {at:#x}: {old:#x} -> 0 (no addiu)')
    if mips: print(f'TB ({at:#X}, {old:#X}, {new:#X}, "{rn}", {mips:#X}),'.replace('0X', '0x'))
