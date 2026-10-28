"""Minimal MIPS (R5900 subset) decoder for the translated overlay code.

Only the instruction forms the translator and its compiler emit are decoded; anything else
decodes as mnemonic "unknown" and the de-translator reports it instead of guessing.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

REGS = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5",
        "t6", "t7", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1",
        "gp", "sp", "fp", "ra"]
GP, SP, FP, RA, AT, ZERO = 28, 29, 30, 31, 1, 0

_SPECIAL = {
    0: "sll", 2: "srl", 3: "sra", 4: "sllv", 6: "srlv", 7: "srav", 8: "jr", 9: "jalr",
    10: "movz", 11: "movn", 12: "syscall", 13: "break", 15: "sync", 16: "mfhi", 17: "mthi",
    18: "mflo", 19: "mtlo", 20: "dsllv", 22: "dsrlv", 23: "dsrav", 24: "mult", 25: "multu",
    26: "div", 27: "divu", 32: "add", 33: "addu", 34: "sub", 35: "subu", 36: "and", 37: "or",
    38: "xor", 39: "nor", 40: "mfsa", 41: "mtsa", 42: "slt", 43: "sltu", 44: "dadd", 45: "daddu",
    46: "dsub", 47: "dsubu", 52: "teq", 56: "dsll", 58: "dsrl", 59: "dsra", 60: "dsll32",
    62: "dsrl32", 63: "dsra32",
}
_REGIMM = {0: "bltz", 1: "bgez", 2: "bltzl", 3: "bgezl", 16: "bltzal", 17: "bgezal"}
_MAIN = {
    2: "j", 3: "jal", 4: "beq", 5: "bne", 6: "blez", 7: "bgtz", 8: "addi", 9: "addiu", 10: "slti",
    11: "sltiu", 12: "andi", 13: "ori", 14: "xori", 15: "lui", 20: "beql", 21: "bnel", 22: "blezl",
    23: "bgtzl", 24: "daddi", 25: "daddiu", 32: "lb", 33: "lh", 34: "lwl", 35: "lw", 36: "lbu",
    37: "lhu", 38: "lwr", 39: "lwu", 40: "sb", 41: "sh", 42: "swl", 43: "sw", 44: "sdl", 45: "sdr",
    46: "swr", 55: "ld", 63: "sd", 30: "lq", 31: "sq",
}
LOADS = {"lb", "lh", "lw", "lbu", "lhu", "lwu", "ld", "lwl", "lwr", "lq"}
STORES = {"sb", "sh", "sw", "sd", "swl", "swr", "sdl", "sdr", "sq"}
LOAD_SIZE = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4, "lwu": 4, "ld": 8}
STORE_SIZE = {"sb": 1, "sh": 2, "sw": 4, "sd": 8}
COND_BRANCHES = {"beq", "bne", "blez", "bgtz", "bltz", "bgez", "beql", "bnel", "blezl", "bgtzl",
                 "bltzl", "bgezl", "bltzal", "bgezal"}
RTYPE3 = {"sllv", "srlv", "srav", "addu", "add", "subu", "sub", "and", "or", "xor", "nor", "slt",
          "sltu", "daddu", "dsubu", "dadd", "dsub", "movz", "movn", "dsllv", "dsrlv", "dsrav"}
SHIFTS = {"sll", "srl", "sra", "dsll", "dsrl", "dsra", "dsll32", "dsrl32", "dsra32"}


@dataclass(frozen=True)
class Insn:
    addr: int
    word: int
    op: str
    rs: int = 0
    rt: int = 0
    rd: int = 0
    sa: int = 0
    imm: int = 0        # sign-extended 16-bit immediate / offset
    target: int = 0     # absolute branch/jump target

    @property
    def uimm(self) -> int:
        return self.imm & 0xFFFF

    @property
    def is_branch(self) -> bool:
        return self.op in COND_BRANCHES or self.op in ("j", "jal", "jr", "jalr", "b")

    @property
    def is_cond_branch(self) -> bool:
        return self.op in COND_BRANCHES

    def text(self) -> str:
        r = REGS
        o = self.op
        if o in ("nop",):
            return "nop"
        if o == "lui":
            return f"lui ${r[self.rt]}, {self.uimm:#x}"
        if o in ("addi", "addiu", "slti", "sltiu", "daddi", "daddiu"):
            return f"{o} ${r[self.rt]}, ${r[self.rs]}, {self.imm:#x}"
        if o in ("andi", "ori", "xori"):
            return f"{o} ${r[self.rt]}, ${r[self.rs]}, {self.uimm:#x}"
        if o in LOADS or o in STORES:
            return f"{o} ${r[self.rt]}, {self.imm:#x}(${r[self.rs]})"
        if o in ("beq", "bne", "beql", "bnel"):
            return f"{o} ${r[self.rs]}, ${r[self.rt]}, {self.target:#x}"
        if o == "b":
            return f"b {self.target:#x}"
        if o in COND_BRANCHES:
            return f"{o} ${r[self.rs]}, {self.target:#x}"
        if o in ("j", "jal"):
            return f"{o} {self.target:#x}"
        if o == "jr":
            return f"jr ${r[self.rs]}"
        if o == "jalr":
            return f"jalr ${r[self.rd]}, ${r[self.rs]}"
        if o in SHIFTS:
            return f"{o} ${r[self.rd]}, ${r[self.rt]}, {self.sa}"
        if o in RTYPE3:
            return f"{o} ${r[self.rd]}, ${r[self.rs]}, ${r[self.rt]}"
        if o in ("mult", "multu", "div", "divu"):
            return f"{o} ${r[self.rs]}, ${r[self.rt]}"
        if o in ("mfhi", "mflo"):
            return f"{o} ${r[self.rd]}"
        if o in ("mthi", "mtlo"):
            return f"{o} ${r[self.rs]}"
        return f"{o} ({self.word:#010x})"


def decode(word: int, addr: int) -> Insn:
    op = word >> 26
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    sa = (word >> 6) & 31
    imm = word & 0xFFFF
    if imm & 0x8000:
        imm -= 0x10000
    if word == 0:
        return Insn(addr, word, "nop")
    if op == 0:
        name = _SPECIAL.get(word & 0x3F, "unknown")
        return Insn(addr, word, name, rs, rt, rd, sa, imm)
    if op == 1:
        name = _REGIMM.get(rt, "unknown")
        return Insn(addr, word, name, rs, rt, rd, sa, imm, (addr + 4 + imm * 4) & 0xFFFFFFFF)
    name = _MAIN.get(op, "unknown")
    if name in ("j", "jal"):
        return Insn(addr, word, name, target=((addr + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2))
    if name in COND_BRANCHES:
        if name == "beq" and rs == 0 and rt == 0:
            name = "b"
        return Insn(addr, word, name, rs, rt, rd, sa, imm, (addr + 4 + imm * 4) & 0xFFFFFFFF)
    return Insn(addr, word, name, rs, rt, rd, sa, imm)


def decode_all(code: bytes, base: int) -> list[Insn]:
    n = len(code) // 4
    words = struct.unpack_from(f"<{n}I", code, 0)
    return [decode(w, base + 4 * i) for i, w in enumerate(words)]
