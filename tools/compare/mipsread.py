"""mipsread.py FN [END [OVERLAY]]: an overlay's (default A0A9) MIPS listing from FN (to END, or
the function's end) with 68k register slots, gp/ccr and constants named."""
import sys, re, capstone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from espgalarr.ps2.overlay import Overlay
from espgalarr.detrans.verify import WORK
from espgalarr.detrans.symexec import SLOT_OF
ov = Overlay(WORK/"ps2"/"disc"/"A0A0"/(sys.argv[3] if len(sys.argv) > 3 else "A0A9"))
fn = int(sys.argv[1], 16); end = int(sys.argv[2], 16) if len(sys.argv) > 2 and sys.argv[2] else None
if end is None:
    from espgalarr.diff import mips_funcs
    for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end):
        if f.start == fn: end = f.start + f.size
md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_LITTLE_ENDIAN); md.skipdata = True
hi = {}
for i in md.disasm(ov.read(fn, end - fn), fn):
    ops = i.op_str.split(', ')
    note = ''
    m = re.match(r'(-?0x[0-9a-f]+|-?\d+)\((\$\w+)\)', ops[-1]) if ops else None
    if i.mnemonic == 'lui':
        hi[ops[0]] = int(ops[1], 0) << 16
    elif m and m.group(2) == '$at' and '$at' in hi:
        a = (hi['$at'] + int(m.group(1), 0)) & 0xffffffff
        off = a - 0x480000
        note = SLOT_OF.get(off, hex(a))
    elif m and m.group(2) == '$gp':
        g = int(m.group(1), 0)
        note = 'CCR' if g == -0x7ee4 else f'gp{g:+#x}'
    elif i.mnemonic in ('addiu', 'ori') and len(ops) == 3 and ops[1] in hi:
        note = hex((hi[ops[1]] + int(ops[2], 0)) & 0xffffffff)
    if i.mnemonic in ('jal', 'j', 'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez', 'b'):
        pass
    print(f"{i.address:x}  {i.mnemonic:8s} {i.op_str:30s} {('; ' + note) if note else ''}")
