"""dis68.py BUILD ADDR [N]: disassemble N (default 40) instructions of a build's P ROM
(BUILD/roms/espgaluda_u8.bin, byte-swapped to the 68k view) from ADDR (hex): what a port placed."""
import sys

import capstone

p = open(sys.argv[1] + '/roms/espgaluda_u8.bin', 'rb').read()
p = bytes(p[i ^ 1] for i in range(len(p)))
md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_000)
a = int(sys.argv[2], 16); n = int(sys.argv[3]) if len(sys.argv) > 3 else 40
for k, i in enumerate(md.disasm(p[a:a + 0x800], a)):
    if k >= n:
        break
    print(f'{i.address:06x}  {i.mnemonic} {i.op_str}')
