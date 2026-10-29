"""Check de-translated routines against the original MIPS code on real PS2 states.

A PCSX2 hook dump (tests: pcsx2 EspgalTrace, ESPGAL_HOOKS) holds the EE RAM, scratchpad and
CPU registers at the entry of a translated function. From that state:

  truth      the original MIPS function runs in espgalarr.ps2.mipsint until it returns;
  candidate  the function is de-translated with PS2 addresses kept as they are (IdentityMap),
             assembled above the RAM image and run in a 68020 emulator (machine68k) over the
             same RAM bytes (the PS2 keeps the 68k RAM in 68k byte order). Any jump into PS2
             code (a callee) is bridged: memory and the emulated 68k registers are handed to
             the MIPS interpreter, the callee runs as MIPS, and the 68k code continues.

Both end states are compared: the 68k registers (from the PS2 register slots for the truth)
and every RAM byte, except the MIPS native stack and the register-slot block.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..pgm import asm68k
from ..ps2 import mipsint
from ..ps2.mipsint import CPU, MAIN_SIZE, Memory, regfile, set_regfile
from . import core
from .porter import fix_stack_saves
from .templates import AddrMap

CODE_BASE = 0x2100000          # candidate code, above the 32 MB RAM image
SENTINEL = 0x2200000           # return address of the candidate (bra.s * there)
SLOTS = (0x4810A0, 0x481120)   # emulated 68k register slots
CCR_ADDR = 0x35FCF0 - 0x7EE4   # $gp-0x7ee4


class IdentityMap(AddrMap):
    """Keep PS2 addresses: RAM/data constants print as themselves, code as F_<ps2>."""

    def __init__(self, func_starts: set[int]):
        self.func_starts = func_starts

    def map_const(self, v: int):
        if 0 <= v < MAIN_SIZE:
            return v, "id", 1.0
        return None, "", 0.0

    def const_text(self, v: int):
        return f"F_{v:x}" if v in self.func_starts else None


@dataclass
class Dump:
    main: bytes
    scratch: bytes
    regs: bytes
    pc: int

    @classmethod
    def load(cls, prefix: Path) -> "Dump":
        import zstandard as zstd   # noqa: PLC0415
        main = zstd.ZstdDecompressor().decompress(Path(f"{prefix}.ee.zst").read_bytes(),
                                                  max_output_size=MAIN_SIZE)
        regs = Path(f"{prefix}.regs").read_bytes()
        return cls(main, Path(f"{prefix}.spr").read_bytes(), regs,
                   struct.unpack_from("<I", regs, 680)[0])


@dataclass
class Result:
    regs: dict
    main: bytes
    io: list = field(default_factory=list)


STUBS: set[int] = set()   # native PS2 services treated as `return 0` on both sides
MEMSET = 0x118200          # ELF memset(dst, value, n): run natively (slow in the interpreter)


def _native_memset(cpu: CPU) -> bool:
    dst, val, n = cpu.g(4) & 0xFFFFFFFF, cpu.g(5) & 0xFF, cpu.g(6) & 0xFFFFFFFF
    for a in range(dst, dst + n):
        cpu.mem.write(a, 1, val) if n < 64 else None
    if n >= 64:
        buf, o = cpu.mem._where(dst, n)
        if buf is not None:
            buf[o:o + n] = bytes([val]) * n
        else:
            for a in range(dst, dst + n):
                cpu.mem.write(a, 1, val)
    cpu.s(2, dst)
    cpu.pc = cpu.g(31) & 0xFFFFFFFF
    if cpu.stack:
        cpu.stack.pop()
    return True


def _install_natives(cpu: CPU) -> None:
    cpu.hooks[MEMSET] = _native_memset


class CallError(Exception):
    """An interpreter failure inside a callee; `callee` = the routine's direct callee."""

    def __init__(self, msg: str, callee: int | None, side: str = "truth"):
        super().__init__(msg)
        self.callee = callee
        self.side = side


def _run(cpu: CPU, fn: int) -> None:
    try:
        cpu.call(fn)
    except Exception as ex:  # noqa: BLE001
        raise CallError(f"{type(ex).__name__}: {ex}", cpu.stack[0] if cpu.stack else None) from ex


def truth(d: Dump, fn: int) -> Result:
    mem = Memory(d.main, d.scratch)
    cpu = CPU(mem)
    cpu.load_pcsx2_regs(d.regs)
    cpu.stubs = set(STUBS)
    _install_natives(cpu)
    _run(cpu, fn)
    return Result(regfile(mem), bytes(mem.main), mem.io_writes)


def render(d: Dump, fn: int, size: int, func_starts: set[int]) -> tuple[str, set[int]]:
    """De-translate the function from the dump's own memory with PS2 addresses kept."""
    code = d.main[fn:fn + size]
    rd = lambda a: struct.unpack_from("<I", d.main, a & (MAIN_SIZE - 1))[0]  # noqa: E731
    res = core.detranslate(code, fn, IdentityMap(func_starts), read_word=rd)
    lines = fix_stack_saves(res.text(header=False).splitlines())
    text = "\n".join(lines) + "\n"
    import re
    callees = {int(x, 16) for x in re.findall(r"\bF_([0-9a-f]+)\b", text)} - {fn}
    return text, callees


def candidate(d: Dump, fn: int, text: str, callees: set[int]) -> Result:
    import machine68k as m68
    head = "".join(f"\t.equ\tF_{c:x}, ${c:x}\n" for c in sorted(callees))
    code, syms = asm68k.assemble(head + text, CODE_BASE)
    mc = m68.Machine(m68.CPUType.M68020, (CODE_BASE >> 10) + 2048)
    mem, cpu = mc.mem, mc.cpu
    mem.w_block(0, d.main)
    mem.w_block(CODE_BASE, code)
    mem.w16(SENTINEL, 0x60FE)
    pm = Memory(d.main, d.scratch)            # MIPS side for callees
    mips = CPU(pm)
    mips.load_pcsx2_regs(d.regs)
    mips.stubs = set(STUBS)
    _install_natives(mips)
    regs = regfile(pm)
    for n in range(8):
        cpu.w_reg(n, regs[f"d{n}"])
        cpu.w_reg(8 + n, regs[f"a{n}"])
    cpu.w_sr(0x2700 | (pm.read(CCR_ADDR, 2) & 0x1F))
    sp = regs["a7"] - 4
    mem.w32(sp, SENTINEL)
    cpu.w_reg(15, sp)
    cpu.w_pc(syms[f"F_{fn:x}"] if f"F_{fn:x}" in syms else CODE_BASE)
    bridged = []

    def hook(pc):
        if pc >= MAIN_SIZE:
            return
        # callee in PS2 code: run it as MIPS on the same state, then return to the 68k caller
        pm.main[:] = mem.r_block(0, MAIN_SIZE)
        set_regfile(pm, {f"d{n}": cpu.r_reg(n) for n in range(8)} |
                        {f"a{n}": cpu.r_reg(8 + n) for n in range(8)})
        pm.write(CCR_ADDR, 2, (pm.read(CCR_ADDR, 2) & ~0x1F) | (cpu.r_sr() & 0x1F))
        # the 68k jsr pushed a return address; the translated callee keeps its own 68k stack
        a7 = cpu.r_reg(15)
        ret = mem.r32(a7)
        set_regfile(pm, {"a7": a7 + 4})
        mips.stack.clear()
        if pc in STUBS:
            pm.write(0, 0, 0)
        else:
            try:
                mips.call(pc)
            except Exception as ex:  # noqa: BLE001
                raise CallError(f"{type(ex).__name__}: {ex}", pc, "candidate") from ex
        mem.w_block(0, bytes(pm.main))
        back = regfile(pm)
        for n in range(8):
            cpu.w_reg(n, back[f"d{n}"])
            cpu.w_reg(8 + n, back[f"a{n}"])
        cpu.w_sr((cpu.r_sr() & ~0x1F) | (pm.read(CCR_ADDR, 2) & 0x1F))
        cpu.w_pc(ret)
        bridged.append(pc)

    def invalid(mode, width, addr):
        raise CallError(f"candidate bus error: {mode}{width} at {addr:#x} (pc={cpu.r_pc():#x})",
                        None, "candidate")

    mem.set_invalid_func(invalid)
    cpu.set_instr_hook_callback(hook)
    for _ in range(2000):
        cpu.execute(50000)
        if cpu.r_pc() in (SENTINEL, SENTINEL + 2):
            break
    else:
        raise CallError(f"candidate did not return (pc={cpu.r_pc():#x})", None, "candidate")
    out = {f"d{n}": cpu.r_reg(n) for n in range(8)} | {f"a{n}": cpu.r_reg(8 + n) for n in range(7)}
    out["a7"] = cpu.r_reg(15)
    main = bytes(mem.r_block(0, MAIN_SIZE))
    mc.cleanup()
    return Result(out, main)


def compare(t: Result, c: Result, sp_floor: int, ignore: list[tuple[int, int]] = (),
            a7: int | None = None) -> list[str]:
    """Differences between truth and candidate (68k registers, RAM bytes). Translated calls do
    not push return addresses on the emulated 68k stack while real 68k jsr does, so the bytes
    just below the entry a7 (the candidate's frames) are not compared."""
    diffs = []
    for k in sorted(t.regs):
        if k == "a7":
            continue
        if (t.regs[k] & 0xFFFFFFFF) != (c.regs[k] & 0xFFFFFFFF):
            diffs.append(f"{k}: truth {t.regs[k]:#x} candidate {c.regs[k]:#x}")
    skip = [SLOTS, (sp_floor - 0x10000, sp_floor + 0x100), (CCR_ADDR, CCR_ADDR + 2)] + list(ignore)
    if a7 is not None:
        skip.append((a7 - 0x400, a7))
    a, b = t.main, c.main
    i = 0
    n = 0
    while True:
        j = next((x for x in range(i, MAIN_SIZE, 1 << 16) if a[x:x + (1 << 16)] != b[x:x + (1 << 16)]), None)
        if j is None:
            break
        for x in range(j, min(j + (1 << 16), MAIN_SIZE)):
            if a[x] != b[x] and not any(lo <= x < hi for lo, hi in skip):
                if n < 20:
                    diffs.append(f"mem {x:#x}: truth {a[x]:#04x} candidate {b[x]:#04x}")
                n += 1
        i = j + (1 << 16)
    if n > 20:
        diffs.append(f"... {n} differing bytes")
    return diffs
