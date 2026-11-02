"""shotcalls.py: the calls in each volley task's run routine, 68k (jsr/bsr targets up to the routine's
rts) against the PS2 twin (jal targets up to its jr $ra; A0A9 0x8B7EE0..0x8E0E30 are Arrange's
shot helpers)."""
import struct, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capstone
from espgalarr.pgm import rom as rom_mod
from espgalarr.ps2 import overlay

p = bytes(rom_mod.RomSet(Path(__file__).resolve().parents[2] / 'work').p_rom_68k()[:0x200000])
md68 = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_000)
mdm = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 | capstone.CS_MODE_LITTLE_ENDIAN)
mdm.skipdata = True
JOBS = (('stage 2 task 0xA', 0x192356, 'A0A1', 0x665DC0), ('stage 3 task 0xB', 0x197996, 'A0A2', 0x6713B0),
        ('stage 6 tasks 0x11/0x12', 0x1A63A4, 'A0A5', 0x6D05A0), ('stage 6 task 0x16', 0x1A6B54, 'A0A5', 0x6D4EB0))
if len(sys.argv) > 1:      # NAME 68K OVERLAY PS2 ...
    a = sys.argv[1:]
    JOBS = [(a[i], int(a[i + 1], 16), a[i + 2], int(a[i + 3], 16)) for i in range(0, len(a), 4)]
for name, a68, ov, aps in JOBS:
    c68 = []
    for i in md68.disasm(p[a68:a68 + 0x300], a68):
        if i.mnemonic in ('jsr', 'bsr', 'bsr.w', 'bsr.b', 'jmp'):
            c68.append(i.op_str)
        if i.mnemonic == 'rts':
            break
    R = overlay.Overlay(Path(__file__).resolve().parents[2] / 'work' / 'ps2' / 'disc' / 'A0A0' / ov)
    cps = []
    for i in mdm.disasm(R.read(aps, 0x6000), aps):
        if i.mnemonic in ('jal', 'j'):
            cps.append(i.op_str)
        if i.mnemonic == 'jr' and i.op_str == '$ra':
            break
    print(name, f'68k {a68:#x}:', c68)
    print(' ' * len(name), f'{ov} {aps:#x}:', cps)
