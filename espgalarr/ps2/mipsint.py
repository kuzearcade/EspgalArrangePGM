"""Small R5900 (PS2 EE) interpreter for the translated game code.

Used as the ground truth when checking de-translated routines: a PCSX2 dump gives the machine
state at a function entry, this interpreter runs the original MIPS code from there, and the
result is compared with the de-translated 68k routine run on the same state.

Scope: the instruction forms the translator and the game's C code use (see
espgalarr/detrans/mips.py, which decodes them). Memory: 32 MB main RAM with its kseg/uncached
mirrors, the 16 KB scratchpad at 0x70000000; any other address is I/O and reads as 0 (writes are
recorded in `io_writes`). Unsupported instructions raise `Unsupported`, never guess.
"""
from __future__ import annotations

import struct

from ..detrans import mips as M

MAIN_SIZE = 32 << 20
IO_KEEP = 256          # I/O writes kept per run (count in io_count)
MAX_DEPTH = 512        # call depth limit (runaway recursion / unbalanced calls)
SCRATCH_BASE, SCRATCH_SIZE = 0x70000000, 0x4000
M64 = (1 << 64) - 1
M32 = 0xFFFFFFFF
M128 = (1 << 128) - 1


class Unsupported(Exception):
    pass


def _s32(v: int) -> int:
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def _s64(v: int) -> int:
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def _sext32(v: int) -> int:
    """32-bit result sign-extended into a 64-bit register (MIPS64 rule for 32-bit ops)."""
    return _s32(v) & M64


class Memory:
    def __init__(self, main: bytes | bytearray, scratch: bytes | bytearray | None = None):
        assert len(main) >= MAIN_SIZE
        self.main = bytearray(main[:MAIN_SIZE])
        self.scratch = bytearray(scratch[:SCRATCH_SIZE]) if scratch else bytearray(SCRATCH_SIZE)
        self.io_writes: list[tuple[int, int, int]] = []   # first IO_KEEP writes only
        self.io_count = 0

    def _where(self, a: int, n: int):
        a &= M32
        if SCRATCH_BASE <= a < SCRATCH_BASE + SCRATCH_SIZE:
            return self.scratch, a - SCRATCH_BASE
        p = a & 0x1FFFFFFF
        if a < 0x80000000 and a >= 0x20000000 and a < 0x30000000:
            p = a - 0x20000000           # uncached mirror
        elif 0x30000000 <= a < 0x40000000:
            p = a - 0x30000000           # uncached accelerated mirror
        if p + n <= MAIN_SIZE:
            return self.main, p
        return None, a

    def read(self, a: int, n: int) -> int:
        buf, o = self._where(a, n)
        if buf is None:
            return 0
        return int.from_bytes(buf[o:o + n], "little")

    def write(self, a: int, n: int, v: int) -> None:
        buf, o = self._where(a, n)
        if buf is None:
            self.io_count += 1
            if len(self.io_writes) < IO_KEEP:   # bounded: hardware wait loops write millions
                self.io_writes.append((a & M32, n, v))
            return
        buf[o:o + n] = (v & ((1 << (8 * n)) - 1)).to_bytes(n, "little")


class CPU:
    def __init__(self, mem: Memory):
        self.mem = mem
        self.r = [0] * 32          # up to 128-bit values (low 64 bits for normal ops)
        self.hi = self.lo = 0
        self.pc = 0
        self.hooks: dict = {}      # pc -> fn(cpu) -> True if it handled the instruction at pc
        self.steps = 0
        self.cp0 = [0] * 32
        self.fpr = [0] * 32
        self.syscalls: list[int] = []
        self.stack: list[int] = []      # call targets (jal/jalr) still active
        self.stubs: set[int] = set()    # functions that return at once (v0 = 0)
        self._dec: dict[int, M.Insn] = {}

    # -- registers
    def g(self, i: int) -> int:
        return self.r[i] & M64

    def s(self, i: int, v: int) -> None:
        if i:
            self.r[i] = (self.r[i] & ~M64 & M128) | (v & M64)

    def s128(self, i: int, v: int) -> None:
        if i:
            self.r[i] = v & M128

    def load_pcsx2_regs(self, raw: bytes) -> None:
        """cpuRegisters from PCSX2: 32 x 128-bit GPRs, then HI, LO (128-bit), then CP0 ..., pc
        after sa/IsDelaySlot/pc layout. Only GPRs, HI, LO and pc are used."""
        for i in range(32):
            self.r[i] = int.from_bytes(raw[16 * i:16 * i + 16], "little")
        self.hi = int.from_bytes(raw[512:520], "little")
        self.lo = int.from_bytes(raw[528:536], "little")
        self.pc = int.from_bytes(raw[680:684], "little")
        self.cp0 = [int.from_bytes(raw[544 + 4 * k:548 + 4 * k], "little") for k in range(32)]

    # -- execution
    def decode(self, pc: int) -> M.Insn:
        ins = self._dec.get(pc)
        if ins is None:
            ins = M.decode(self.mem.read(pc, 4), pc)
            self._dec[pc] = ins
        return ins

    def call(self, fn: int, ret: int = 0xDEAD0000, budget: int = 5_000_000) -> None:
        """Run a function: ra = ret, pc = fn, until the pc reaches ret."""
        self.s(M.RA, ret)
        self.pc = fn
        while self.pc != ret:
            self.step()
            if self.steps > budget:
                raise RuntimeError(f"instruction budget exceeded at {self.pc:#x}")

    def step(self) -> None:
        h = self.hooks.get(self.pc)
        if h is not None and h(self):
            return
        if self.pc in self.stubs:
            self.s(2, 0)
            self.pc = self.g(M.RA) & M32
            if self.stack:
                self.stack.pop()
            return
        ins = self.decode(self.pc)
        nxt = self.exec(ins, self.pc + 4)
        self.pc = nxt

    def _delay_then(self, slot_pc: int, target: int) -> int:
        ins = self.decode(slot_pc)
        if ins.is_branch:
            raise Unsupported(f"branch in delay slot at {slot_pc:#x}")
        self.exec(ins, slot_pc + 4)
        return target

    def exec(self, i: M.Insn, nxt: int) -> int:
        self.steps += 1
        op, rs, rt, rd = i.op, i.rs, i.rt, i.rd
        g, s = self.g, self.s
        pc = i.addr
        # ---- loads / stores
        if op in M.LOAD_SIZE:
            a = (g(rs) + i.imm) & M32
            n = M.LOAD_SIZE[op]
            v = self.mem.read(a, n)
            if op in ("lb", "lh", "lw"):
                bits = 8 * n
                if v >> (bits - 1):
                    v -= 1 << bits
                v &= M64
            s(rt, v)
            return nxt
        if op in M.STORE_SIZE:
            a = (g(rs) + i.imm) & M32
            self.mem.write(a, M.STORE_SIZE[op], g(rt))
            return nxt
        if op == "lq":
            a = (g(rs) + i.imm) & M32 & ~0xF
            self.s128(rt, self.mem.read(a, 16))
            return nxt
        if op == "sq":
            a = (g(rs) + i.imm) & M32 & ~0xF
            self.mem.write(a, 16, self.r[rt] & M128)
            return nxt
        if op in ("lwl", "lwr", "swl", "swr", "sdl", "sdr"):
            raise Unsupported(f"{op} at {pc:#x}")
        # ---- immediates
        if op == "addiu" or op == "addi":
            s(rt, _sext32(g(rs) + i.imm))
            return nxt
        if op == "daddiu" or op == "daddi":
            s(rt, g(rs) + i.imm)
            return nxt
        if op == "andi":
            s(rt, g(rs) & i.uimm)
            return nxt
        if op == "ori":
            s(rt, g(rs) | i.uimm)
            return nxt
        if op == "xori":
            s(rt, g(rs) ^ i.uimm)
            return nxt
        if op == "lui":
            s(rt, _sext32(i.uimm << 16))
            return nxt
        if op == "slti":
            s(rt, 1 if _s64(g(rs)) < i.imm else 0)
            return nxt
        if op == "sltiu":
            s(rt, 1 if g(rs) < (i.imm & M64) else 0)
            return nxt
        # ---- register ops
        if op == "addu" or op == "add":
            s(rd, _sext32(g(rs) + g(rt)))
            return nxt
        if op == "subu" or op == "sub":
            s(rd, _sext32(g(rs) - g(rt)))
            return nxt
        if op == "daddu" or op == "dadd":
            s(rd, g(rs) + g(rt))
            return nxt
        if op == "dsubu" or op == "dsub":
            s(rd, g(rs) - g(rt))
            return nxt
        if op == "and":
            s(rd, g(rs) & g(rt))
            return nxt
        if op == "or":
            s(rd, g(rs) | g(rt))
            return nxt
        if op == "xor":
            s(rd, g(rs) ^ g(rt))
            return nxt
        if op == "nor":
            s(rd, ~(g(rs) | g(rt)))
            return nxt
        if op == "slt":
            s(rd, 1 if _s64(g(rs)) < _s64(g(rt)) else 0)
            return nxt
        if op == "sltu":
            s(rd, 1 if g(rs) < g(rt) else 0)
            return nxt
        if op == "movz":
            if g(rt) == 0:
                s(rd, g(rs))
            return nxt
        if op == "movn":
            if g(rt) != 0:
                s(rd, g(rs))
            return nxt
        if op == "sll":
            s(rd, _sext32((g(rt) & M32) << i.sa))
            return nxt
        if op == "srl":
            s(rd, _sext32((g(rt) & M32) >> i.sa))
            return nxt
        if op == "sra":
            s(rd, _sext32(_s32(g(rt)) >> i.sa))
            return nxt
        if op == "sllv":
            s(rd, _sext32((g(rt) & M32) << (g(rs) & 31)))
            return nxt
        if op == "srlv":
            s(rd, _sext32((g(rt) & M32) >> (g(rs) & 31)))
            return nxt
        if op == "srav":
            s(rd, _sext32(_s32(g(rt)) >> (g(rs) & 31)))
            return nxt
        if op == "dsll":
            s(rd, g(rt) << i.sa)
            return nxt
        if op == "dsrl":
            s(rd, g(rt) >> i.sa)
            return nxt
        if op == "dsra":
            s(rd, _s64(g(rt)) >> i.sa)
            return nxt
        if op == "dsll32":
            s(rd, g(rt) << (i.sa + 32))
            return nxt
        if op == "dsrl32":
            s(rd, g(rt) >> (i.sa + 32))
            return nxt
        if op == "dsra32":
            s(rd, _s64(g(rt)) >> (i.sa + 32))
            return nxt
        if op == "dsllv":
            s(rd, g(rt) << (g(rs) & 63))
            return nxt
        if op == "dsrlv":
            s(rd, g(rt) >> (g(rs) & 63))
            return nxt
        if op == "dsrav":
            s(rd, _s64(g(rt)) >> (g(rs) & 63))
            return nxt
        if op in ("mult", "multu"):
            a, b = (_s32(g(rs)), _s32(g(rt))) if op == "mult" else (g(rs) & M32, g(rt) & M32)
            p = a * b
            self.lo, self.hi = _sext32(p), _sext32(p >> 32)
            if rd:
                s(rd, self.lo)
            return nxt
        if op in ("div", "divu"):
            a, b = (_s32(g(rs)), _s32(g(rt))) if op == "div" else (g(rs) & M32, g(rt) & M32)
            if b:
                q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                self.lo, self.hi = _sext32(q), _sext32(a - q * b)
            return nxt
        if op == "mfhi":
            s(rd, self.hi)
            return nxt
        if op == "mflo":
            s(rd, self.lo)
            return nxt
        if op == "mthi":
            self.hi = g(rs)
            return nxt
        if op == "mtlo":
            self.lo = g(rs)
            return nxt
        if op == "sync" or op == "nop":
            return nxt
        # ---- control flow
        if op == "j" or op == "b":
            return self._delay_then(nxt, i.target)
        if op == "jal":
            s(M.RA, pc + 8)
            self.stack.append(i.target)
            if len(self.stack) > MAX_DEPTH:
                raise RuntimeError(f"call depth over {MAX_DEPTH} at {pc:#x}")
            return self._delay_then(nxt, i.target)
        if op == "jr":
            t = g(rs) & M32
            if rs == M.RA and self.stack:
                self.stack.pop()
            return self._delay_then(nxt, t)
        if op == "jalr":
            t = g(rs) & M32
            self.stack.append(t)
            if len(self.stack) > MAX_DEPTH:
                raise RuntimeError(f"call depth over {MAX_DEPTH} at {pc:#x}")
            s(rd or M.RA, pc + 8)
            return self._delay_then(nxt, t)
        if op in M.COND_BRANCHES:
            a, b = _s64(g(rs)), _s64(g(rt))
            take = {"beq": a == b, "bne": a != b, "blez": a <= 0, "bgtz": a > 0, "bltz": a < 0,
                    "bgez": a >= 0, "beql": a == b, "bnel": a != b, "blezl": a <= 0,
                    "bgtzl": a > 0, "bltzl": a < 0, "bgezl": a >= 0, "bltzal": a < 0,
                    "bgezal": a >= 0}[op]
            if op in ("bltzal", "bgezal"):
                s(M.RA, pc + 8)
            if take:
                return self._delay_then(nxt, i.target)
            if op.endswith("l") and op not in ("bltzal", "bgezal"):
                return nxt + 4               # likely: delay slot annulled
            return self._delay_then(nxt, nxt + 4)
        if (i.word >> 26) == 28:
            return self._mmi(i, nxt)
        if (i.word >> 26) == 16:             # COP0: mfc0 (others: performance counters etc. -> 0)
            if rs == 0:
                if rt:
                    s(rt, _sext32(self.cp0[rd] if (i.word & 0x3F) == 0 else 0))
                return nxt
            if rs == 0x10 and (i.word & 0x3F) == 0x39:   # di: clear Status.EIE
                self.cp0[12] &= ~0x10000
                return nxt
            if rs == 0x10 and (i.word & 0x3F) == 0x38:   # ei: set Status.EIE
                self.cp0[12] |= 0x10000
                return nxt
            if rs == 4:                      # mtc0
                self.cp0[rd] = g(rt) & M32
                return nxt
            if rs == 0x10:                   # other privileged ops: no effect here
                return nxt
        top = i.word >> 26
        if top == 47:                        # cache: no architectural effect here
            return nxt
        if op == "syscall":                  # kernel services are not modelled: return 0
            if len(self.syscalls) < IO_KEEP:
                self.syscalls.append(g(3))
            s(2, 0)
            return nxt
        if top == 49:                        # lwc1
            self.fpr[rt] = self.mem.read((g(rs) + i.imm) & M32, 4)
            return nxt
        if top == 57:                        # swc1
            self.mem.write((g(rs) + i.imm) & M32, 4, self.fpr[rt])
            return nxt
        if top == 17 and rs in (0, 4):       # mfc1 / mtc1
            if rs == 0:
                s(rt, _sext32(self.fpr[rd]))
            else:
                self.fpr[rd] = g(rt) & M32
            return nxt
        if op == "mfsa":
            s(rd, 0)
            return nxt
        if op == "mtsa":
            return nxt
        if i.word >> 26 == 0 and (i.word & 0x3F) in (0x30, 0x31, 0x32, 0x33, 0x34, 0x36):
            return nxt                       # tge/tgeu/tlt/tltu/teq/tne: traps (assertions) not taken
        raise Unsupported(f"{op} ({i.word:#010x}) at {pc:#x}")

    def _mmi(self, i: M.Insn, nxt: int) -> int:
        """R5900 multimedia (opcode 28): the 128-bit moves/logic and pipeline-1 HI/LO forms."""
        w = i.word
        rs, rt, rd, sa, fn = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31, w & 0x3F
        R = self.r
        lo64 = lambda x: R[x] & M64          # noqa: E731
        hi64 = lambda x: (R[x] >> 64) & M64  # noqa: E731
        if fn == 0x09 and sa == 0x0E:        # pcpyld rd, rs, rt
            self.s128(rd, (lo64(rs) << 64) | lo64(rt))
        elif fn == 0x29 and sa == 0x0E:      # pcpyud rd, rs, rt
            self.s128(rd, (hi64(rt) << 64) | hi64(rs))
        elif fn == 0x09 and sa == 0x12:      # pand
            self.s128(rd, R[rs] & R[rt])
        elif fn == 0x09 and sa == 0x13:      # pxor
            self.s128(rd, R[rs] ^ R[rt])
        elif fn == 0x29 and sa == 0x12:      # por
            self.s128(rd, R[rs] | R[rt])
        elif fn == 0x29 and sa == 0x13:      # pnor
            self.s128(rd, ~(R[rs] | R[rt]) & M128)
        elif fn == 0x29 and sa == 0x1B:      # pcpyh rd, rt
            out = 0
            for d in range(2):
                h = (R[rt] >> (64 * d)) & 0xFFFF
                out |= (h * 0x0001000100010001) << (64 * d)
            self.s128(rd, out)
        elif fn == 0x09 and sa == 0x08:      # pmfhi
            self.s128(rd, self.hi)
        elif fn == 0x09 and sa == 0x09:      # pmflo
            self.s128(rd, self.lo)
        elif fn == 0x10:                     # mfhi1
            self.s(rd, self.hi >> 64)
        elif fn == 0x12:                     # mflo1
            self.s(rd, self.lo >> 64)
        elif fn == 0x11:                     # mthi1
            self.hi = (self.hi & M64) | (lo64(rs) << 64)
        elif fn == 0x13:                     # mtlo1
            self.lo = (self.lo & M64) | (lo64(rs) << 64)
        elif fn in (0x18, 0x19):             # mult1 / multu1
            a, b = (_s32(lo64(rs)), _s32(lo64(rt))) if fn == 0x18 else (lo64(rs) & M32, lo64(rt) & M32)
            p = a * b
            self.lo = (self.lo & M64) | (_sext32(p) << 64)
            self.hi = (self.hi & M64) | (_sext32(p >> 32) << 64)
            if rd:
                self.s(rd, _sext32(p))
        elif fn in (0x1A, 0x1B):             # div1 / divu1
            a, b = (_s32(lo64(rs)), _s32(lo64(rt))) if fn == 0x1A else (lo64(rs) & M32, lo64(rt) & M32)
            if b:
                q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                self.lo = (self.lo & M64) | (_sext32(q) << 64)
                self.hi = (self.hi & M64) | (_sext32(a - q * b) << 64)
        else:
            raise Unsupported(f"MMI {w:#010x} (fn {fn:#x} sa {sa:#x}) at {i.addr:#x}")
        return nxt


def regfile(mem: Memory) -> dict[str, int]:
    """The emulated 68k registers (slots at 0x4810a0, little-endian, 8 bytes each)."""
    out = {}
    for n in range(8):
        out[f"d{n}"] = mem.read(0x481118 - 8 * n, 4)
        out[f"a{n}"] = mem.read(0x4810D8 - 8 * n, 4)
    return out


def set_regfile(mem: Memory, regs: dict[str, int]) -> None:
    for k, v in regs.items():
        n = int(k[1])
        a = (0x481118 if k[0] == "d" else 0x4810D8) - 8 * n
        mem.write(a, 4, v)
