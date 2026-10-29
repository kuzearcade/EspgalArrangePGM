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

import re
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
STOP = 0x2200000               # a nop: where the candidate is parked when it has returned
RET_V0 = 0x2200010             # $v0 of the last bridged call (rendered `RET_V0`)
SLOTS = (0x4810A0, 0x481120)   # emulated 68k register slots
CCR_ADDR = 0x35FCF0 - 0x7EE4   # $gp-0x7ee4: halfword, flags in bits 8-12 (C V Z N X)


def _ps2_ccr(mem: Memory) -> int:
    return (mem.read(CCR_ADDR, 2) >> 8) & 0x1F


def _set_ps2_ccr(mem: Memory, ccr: int) -> None:
    mem.write(CCR_ADDR, 2, (mem.read(CCR_ADDR, 2) & ~0x1F00) | ((ccr & 0x1F) << 8))


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


def _arg_value(txt: str, cpu, mem) -> int | None:
    """Value of a rendered MIPS argument (`$k`, `dN`/`aN`, `aN+k`, `[aN+k].s`) on the 68k side."""
    if txt.startswith("$"):
        return int(txt[1:], 16)
    regno = {f"d{n}": n for n in range(8)} | {f"a{n}": 8 + n for n in range(8)}
    if txt in regno:
        return cpu.r_reg(regno[txt])
    m = re.fullmatch(r"([ad][0-7])([+-]\d+)", txt)
    if m:
        return (cpu.r_reg(regno[m.group(1)]) + int(m.group(2))) & 0xFFFFFFFF
    m = re.fullmatch(r"\[([ad][0-7])([+-]\d+)\]\.([bwl])", txt)
    if m:
        a = (cpu.r_reg(regno[m.group(1)]) + int(m.group(2))) & 0xFFFFFFFF
        return {"b": mem.r8, "w": mem.r16, "l": mem.r32}[m.group(3)](a)
    return None


def _install_natives(cpu: CPU) -> None:
    cpu.hooks[MEMSET] = _native_memset


ELF_TEXT = (0x100000, 0x649800)   # ELF code (native services and the ELF's translated part)


class CallError(Exception):
    """An interpreter failure inside a callee; `callee` = the routine's direct callee,
    `frames` = the MIPS call stack at the failure (outermost first)."""

    def __init__(self, msg: str, callee: int | None, side: str = "truth", frames=()):
        super().__init__(msg)
        self.callee = callee
        self.side = side
        self.frames = list(frames)

    def native_frame(self) -> int | None:
        """Outermost ELF function on the failing call path (the service to stub)."""
        return next((f for f in self.frames if ELF_TEXT[0] <= f < ELF_TEXT[1]), None)


def _run(cpu: CPU, fn: int) -> None:
    try:
        cpu.call(fn)
    except Exception as ex:  # noqa: BLE001
        raise CallError(f"{type(ex).__name__}: {ex}", cpu.stack[0] if cpu.stack else None,
                        frames=cpu.stack) from ex


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
    core.MOVEM_W_LOADS = False      # model the translator's word loads (no sign extension)
    core.DBRA_FOLD = False          # and its dbra (loop while the count is >= 0)
    code = d.main[fn:fn + size]
    rd = lambda a: struct.unpack_from("<I", d.main, a & (MAIN_SIZE - 1))[0]  # noqa: E731
    res = core.detranslate(code, fn, IdentityMap(func_starts), read_word=rd)
    lines = fix_stack_saves(res.text(header=False).splitlines())
    text = "\n".join(lines) + "\n"
    callees = {int(x, 16) for x in re.findall(r"\bF_([0-9a-f]+)\b", text)} - {fn}
    return text, callees


def candidate(d: Dump, fn: int, text: str, callees: set[int], trace=None) -> Result:
    """`trace`: optional deque; gets (pc, d0-d7/a0-a7) for every candidate instruction."""
    import machine68k as m68
    head = "".join(f"\t.equ\tF_{c:x}, ${c:x}\n" for c in sorted(callees)) + f"\t.equ\tRET_V0, ${RET_V0:x}\n"
    # calls/jumps passing MIPS arguments (native services, code pointers): label each one
    lines, call_args = [], {}
    for line in text.splitlines():
        m = re.search(r"; .*mips-args (\S+)", line)
        if m:
            lines.append(f"A_args_{len(call_args)}:")
            call_args[f"A_args_{len(call_args)}"] = {
                int(k[1]) + 4: v for k, v in (x.split("=") for x in m.group(1).split(","))}
        lines.append(line)
    code, syms = asm68k.assemble(head + "\n".join(lines) + "\n", CODE_BASE)
    args_at = {syms[k]: v for k, v in call_args.items()}
    mc = m68.Machine(m68.CPUType.M68020, (CODE_BASE >> 10) + 2048)
    mem, cpu = mc.mem, mc.cpu
    mem.w_block(0, d.main)
    mem.w_block(CODE_BASE, code)
    mem.w16(STOP, 0x4E71)
    pm = Memory(d.main, d.scratch)            # MIPS side for callees
    mips = CPU(pm)
    mips.load_pcsx2_regs(d.regs)
    mips.stubs = set(STUBS)
    _install_natives(mips)
    regs = regfile(pm)
    for n in range(8):
        cpu.w_reg(n, regs[f"d{n}"])
        cpu.w_reg(8 + n, regs[f"a{n}"])
    cpu.w_sr(0x2700 | _ps2_ccr(pm))
    # the PS2 stack layout exactly: translated calls push no return address, so neither does
    # the candidate's entry; it ends at the `rts` executed with the entry a7
    entry_a7 = regs["a7"]
    cpu.w_reg(15, entry_a7)
    cpu.w_pc(syms[f"F_{fn:x}"] if f"F_{fn:x}" in syms else CODE_BASE)
    bridged = []
    last = [0]

    class Done(Exception):
        pass

    code_end = CODE_BASE + len(code)

    def finish_if_returning(a7):
        # a translated rts is a MIPS return: it pops nothing, so any rts that does not return
        # into the candidate (an internal bsr/jsr) ends the routine, wherever a7 is
        if a7 == entry_a7 or not CODE_BASE <= mem.r32(a7) < code_end:
            # the instruction at the pc still executes before the exception surfaces
            cpu.w_pc(STOP)
            raise Done()

    def hook(pc):
        if pc >= MAIN_SIZE:
            if trace is not None:
                trace.append((pc, [cpu.r_reg(i) for i in range(16)]))
            if mem.r16(pc) == 0x4E75:               # rts
                finish_if_returning(cpu.r_reg(15))
            last[0] = pc
            return
        # callee in PS2 code: run it as MIPS on the same state, then return to the 68k caller
        op = mem.r16(last[0])
        pushed = 0x4E80 <= op <= 0x4EBF or (op >> 8) == 0x61     # jsr / bsr (jmp/bra: tail)
        a7 = cpu.r_reg(15)
        ret = mem.r32(a7) if pushed else None
        ps2_a7 = a7 + 4 if pushed else a7
        pm.main[:] = mem.r_block(0, MAIN_SIZE)
        set_regfile(pm, {f"d{n}": cpu.r_reg(n) for n in range(8)} |
                        {f"a{n}": cpu.r_reg(8 + n) for n in range(7)} | {"a7": ps2_a7})
        _set_ps2_ccr(pm, cpu.r_sr())
        mips.stack.clear()
        for r, txt in args_at.get(last[0], {}).items():
            v = mips.g(2) & 0xFFFFFFFF if txt == "v0" else _arg_value(txt, cpu, mem)
            if v is not None:
                mips.s(r, v - (1 << 32) if v & 0x80000000 else v)   # sign-extended like lui/addiu
        if pc not in STUBS:
            try:
                mips.call(pc)
            except Exception as ex:  # noqa: BLE001
                raise CallError(f"{type(ex).__name__}: {ex}", pc, "candidate", [pc] + mips.stack) from ex
        mem.w_block(0, bytes(pm.main))
        mem.w32(RET_V0, (0 if pc in STUBS else mips.g(2)) & 0xFFFFFFFF)
        back = regfile(pm)
        for n in range(8):
            cpu.w_reg(n, back[f"d{n}"])
            cpu.w_reg(8 + n, back[f"a{n}"])
        cpu.w_sr((cpu.r_sr() & ~0x1F) | _ps2_ccr(pm))
        bridged.append(pc)
        if not pushed:
            # tail jump: the callee returned for this routine (or for a 68k bsr inside it)
            if back["a7"] == entry_a7 or not CODE_BASE <= mem.r32(back["a7"]) < code_end:
                cpu.w_pc(STOP)
                raise Done()
            ret = mem.r32(back["a7"])
            cpu.w_reg(15, back["a7"] + 4)
        cpu.w_pc(ret)
        last[0] = ret
        if mem.r16(ret) == 0x4E75:      # the hook does not run again for the resumed instruction
            finish_if_returning(cpu.r_reg(15))

    def invalid(mode, width, addr):
        raise CallError(f"candidate bus error: {mode}{width} at {addr:#x} (pc={cpu.r_pc():#x})",
                        None, "candidate")

    mem.set_invalid_func(invalid)
    cpu.set_instr_hook_callback(hook)
    try:
        for _ in range(2000):
            cpu.execute(50000)
        raise CallError(f"candidate did not return (pc={cpu.r_pc():#x})", None, "candidate")
    except Done:
        pass
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
