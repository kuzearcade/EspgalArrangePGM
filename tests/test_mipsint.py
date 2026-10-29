"""R5900 interpreter on hand-assembled code (no game data)."""
import struct

from espgalarr.ps2.mipsint import CPU, MAIN_SIZE, Memory, regfile, set_regfile


def _asm(words):
    return b"".join(struct.pack("<I", w) for w in words)


def test_loop_store_and_call():
    mem = Memory(bytearray(MAIN_SIZE))
    # f(a0=n): v0 = sum 1..n ; store halfword v0 at 0x1000 ; jr ra
    code = [
        0x00001021,  # addu v0, zero, zero
        0x10800004,  # beqz a0, +4 (to sh)
        0x00000000,  # nop
        0x00441021,  # addu v0, v0, a0
        0x1000fffc,  # b -4 (to beqz) with delay slot decrementing a0
        0x2484ffff,  # addiu a0, a0, -1
        0xa4021000,  # sh v0, 0x1000(zero)
        0x03e00008,  # jr ra
        0x00000000,  # nop
    ]
    mem.main[0x100:0x100 + 4 * len(code)] = _asm(code)
    cpu = CPU(mem)
    cpu.s(4, 10)
    cpu.call(0x100)
    assert cpu.g(2) == 55 and mem.read(0x1000, 2) == 55


def test_regfile_slots_and_mirrors():
    mem = Memory(bytearray(MAIN_SIZE))
    set_regfile(mem, {"d0": 0x12345678, "a6": 0x815680})
    assert regfile(mem)["d0"] == 0x12345678 and regfile(mem)["a6"] == 0x815680
    mem.write(0x20001000, 2, 0xBEEF)           # uncached mirror
    assert mem.read(0x80001000, 2) == 0xBEEF   # kseg0 view of the same byte
    mem.write(0x12000000, 4, 1)                # GS register: I/O, recorded only
    assert mem.io_writes == [(0x12000000, 4, 1)]


def test_mmi_pcpyld_and_por():
    mem = Memory(bytearray(MAIN_SIZE))
    cpu = CPU(mem)
    cpu.s128(4, (0x1111 << 64) | 0x2222)
    cpu.s128(5, (0x3333 << 64) | 0x4444)
    pcpyld = (28 << 26) | (4 << 21) | (5 << 16) | (6 << 11) | (0x0E << 6) | 0x09   # rd6 = rs.lo:rt.lo
    por = (28 << 26) | (4 << 21) | (5 << 16) | (7 << 11) | (0x12 << 6) | 0x29
    mem.main[0x100:0x108] = _asm([pcpyld, por])
    cpu.pc = 0x100
    cpu.step()
    cpu.step()
    assert cpu.r[6] == (0x2222 << 64) | 0x4444
    assert cpu.r[7] == (0x3333 << 64) | 0x6666
