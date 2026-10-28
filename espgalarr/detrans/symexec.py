"""Symbolic execution of translated MIPS functions into an ordered stream of effects.

The translator emits, per 68k instruction, loads of the emulated register slots
(0x4810a0.. 8 bytes each), the operation, a store of the result and read-modify-write
updates of the 16-bit CCR word at $gp-0x7ee4.  Conditional flag updates are compiled to
short branch diamonds that only touch the CCR; those are folded here into `ite`
expressions so that the recogniser sees one flag assignment per flag.

Effects (tuples):
    ('label', addr)                         a real branch target (state is reset)
    ('store', ('slot', reg, size), value)   68k register write (size 1/2/4)
    ('store', ('mem', addr, size), value)   memory write (68k big-endian view)
    ('ccr', snap)                           CCR state after this update
    ('branch', boolexpr, target)            conditional branch (taken when true)
    ('jump', target) ('call', target) ('rts',) ('jumpreg', expr) ('callreg', expr)
    ('mips', insn)                          instruction without a symbolic model
Each effect is followed by the address of the MIPS instruction that produced it (as the
last tuple element) to keep the output traceable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import expr as X
from .expr import C, FALSE, TRUE, M32
from .mips import (AT, GP, RA, SP, ZERO, COND_BRANCHES, LOAD_SIZE, STORE_SIZE, Insn,
                   decode_all)

SLOT_BASE = 0x480000
CCR_GP_OFF = -0x7EE4
GP_DEFAULT = 0x35FCF0
CCR_ADDR = GP_DEFAULT + CCR_GP_OFF

# emulated 68k register file: 8 bytes per slot, d0 at 0x1118 descending to a7 at 0x10a0
SLOT_OF = {}
for _n in range(8):
    SLOT_OF[0x1118 - 8 * _n] = f"d{_n}"
    SLOT_OF[0x10D8 - 8 * _n] = f"a{_n}"
OFF_OF = {v: k for k, v in SLOT_OF.items()}
SLOT_LO, SLOT_HI = min(SLOT_OF), max(SLOT_OF) + 8

CALLER_SAVED = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 31]


@dataclass
class State:
    regs: dict = field(default_factory=dict)      # mips reg -> expr
    slots: dict = field(default_factory=dict)     # 68k reg name -> (size, value, old_full)
    ccr: tuple = None                             # ('ccr', snap)
    mem: dict = field(default_factory=dict)       # (addr_expr, size) -> value
    hi: tuple = None
    lo: tuple = None
    ccr_assigned: frozenset = frozenset()   # flags assigned since the last emitted CCR effect

    @staticmethod
    def fresh(gp: int = GP_DEFAULT) -> "State":
        s = State()
        s.ccr = ("ccr", X.entry_snap())
        s.regs[GP] = C(gp)
        s.regs[ZERO] = C(0)
        return s

    def copy(self) -> "State":
        s = State(dict(self.regs), dict(self.slots), self.ccr, dict(self.mem), self.hi, self.lo,
                  self.ccr_assigned)
        return s

    def reg(self, r: int):
        if r == ZERO:
            return C(0)
        v = self.regs.get(r)
        if v is None:
            v = ("in", r)
            self.regs[r] = v
        return v

    def set(self, r: int, v):
        if r != ZERO:
            self.regs[r] = v

    def slot_value(self, name: str, size: int):
        """Value of a 68k register slot as seen by a load of `size` bytes: an opaque,
        versioned leaf (the version changes at every store so flag expressions can be
        attributed to the instruction that produced the value)."""
        full = ("r68", name, self.slots.get(name, 0))
        if size == 4:
            return full
        return X.AND(full, C(0xFFFF if size == 2 else 0xFF))

    def slot_store(self, name: str, size: int, v):
        self.slots[name] = self.slots.get(name, 0) + 1

    def clobber_call(self):
        for r in CALLER_SAVED:
            self.regs[r] = ("ret", r)
        for k in list(self.slots):
            self.slots[k] += 1
        self.mem.clear()
        self.ccr = ("ccr", X.entry_snap())


def merge_states(cond, a: State, b: State) -> State:
    """State after `if cond then a else b`."""
    s = State()
    for r in set(a.regs) | set(b.regs):
        va, vb = a.regs.get(r, ("in", r)), b.regs.get(r, ("in", r))
        s.regs[r] = va if va == vb else X.ITE(cond, va, vb)
    s.ccr = X.ITE(cond, a.ccr, b.ccr)
    s.ccr_assigned = a.ccr_assigned | b.ccr_assigned | X.ccr_assigned(s.ccr)
    s.ccr = s.ccr[:2]
    for k in set(a.slots) | set(b.slots):
        s.slots[k] = max(a.slots.get(k, 0), b.slots.get(k, 0))
    for k in set(a.mem) & set(b.mem):
        if a.mem[k] == b.mem[k]:
            s.mem[k] = a.mem[k]
    s.hi, s.lo = a.hi, a.lo
    return s


class Executor:
    """Executes one function (list of decoded instructions)."""

    def __init__(self, insns: list[Insn], gp: int = GP_DEFAULT, slot_base: int = SLOT_BASE):
        self.insns = insns
        self.gp = gp
        self.slot_base = slot_base
        self.index = {i.addr: k for k, i in enumerate(insns)}
        self.effects: list = []
        self.labels: set[int] = set()
        self.diamonds: set[int] = set()
        self.start = insns[0].addr if insns else 0
        self.end = insns[-1].addr + 4 if insns else 0

    # ------------------------------------------------------------ classification
    def _is_ccr_only(self, ins: Insn, join: int) -> bool:
        o = ins.op
        if o == "nop":
            return True
        if o == "lhu" and ins.rs == GP and ins.imm == CCR_GP_OFF:
            return True
        if o == "sh" and ins.rs == GP and ins.imm == CCR_GP_OFF:
            return True
        if o in ("ori", "andi", "xori"):
            return True
        if o in ("and", "or", "xor", "nor"):
            return True
        if o in ("addiu", "daddiu") and ins.rs == ZERO:
            return True
        if o == "lui" and ins.rt == AT:
            return True
        if o in COND_BRANCHES and ins.target == join:
            return True
        return False

    def find_diamond(self, i: int):
        """Classify the conditional branch at index i.  Returns None (a real branch) or
        (kind, t, j) with t = index of the branch target, j = index of the join."""
        ins = self.insns[i]
        if ins.target <= ins.addr or ins.target >= self.end:
            return None
        t = self.index.get(ins.target)
        if t is None or t <= i + 2 or t - i > 48:
            return None
        arm_a = self.insns[i + 2:t]
        # two-armed: arm A ends with `b J` (+ delay slot, + nop padding)
        for k, x in enumerate(arm_a):
            if x.op == "b":
                j = self.index.get(x.target)
                if j is None or j <= t or j - t > 24:
                    return None
                body = arm_a[:k] + [arm_a[k + 1]] if k + 1 < len(arm_a) else arm_a[:k]
                pad = arm_a[k + 2:]
                if any(p.op != "nop" for p in pad):
                    return None
                if not all(self._is_ccr_only(y, x.target) for y in body):
                    return None
                if not any(y.op == "sh" for y in body):
                    return None
                arm_b = self.insns[t:j]
                if not all(self._is_ccr_only(y, x.target) for y in arm_b):
                    return None
                if any(y.op in COND_BRANCHES for y in arm_b) or any(y.op in COND_BRANCHES for y in body):
                    return None
                return ("two", t, j)
            if x.op in ("j", "jal", "jr", "jalr"):
                return None
        # one-armed: arm A falls through into the target
        if not all(self._is_ccr_only(y, ins.target) for y in arm_a):
            return None
        if not any(y.op == "sh" for y in arm_a):
            return None
        return ("one", t, t)

    # ------------------------------------------------------------ evaluation
    def _ea(self, st: State, ins: Insn):
        base = st.reg(ins.rs)
        return X.ADD(base, C(ins.imm)) if ins.imm else base

    def _slot_of(self, st: State, ins: Insn):
        """(name, byte offset inside the slot) if the access hits the register file."""
        base = st.reg(ins.rs)
        if base[0] == "c":
            a = (base[1] + ins.imm) & M32
        elif base == ("in", AT) or (base[0] == "ret" and base[1] == AT):
            a = (self.slot_base + ins.imm) & M32     # `lui $at,0x48` hoisted out of sight
        else:
            return None
        if a == CCR_ADDR:
            return ("CCR", 0)
        off = a - self.slot_base
        if SLOT_LO <= off < SLOT_HI:
            return (SLOT_OF[off & ~7], off & 7)
        return None

    def exec_insn(self, st: State, ins: Insn, out: list) -> None:
        o = ins.op
        a = ins.addr
        if o == "nop":
            return
        if o == "lui":
            st.set(ins.rt, C(ins.uimm << 16))
        elif o in ("addiu", "addi", "daddiu", "daddi"):
            st.set(ins.rt, X.ADD(st.reg(ins.rs), C(ins.imm)))
        elif o == "ori":
            st.set(ins.rt, X.OR(st.reg(ins.rs), C(ins.uimm)))
        elif o == "andi":
            st.set(ins.rt, X.AND(st.reg(ins.rs), C(ins.uimm)))
        elif o == "xori":
            st.set(ins.rt, X.XOR(st.reg(ins.rs), C(ins.uimm)))
        elif o == "slti":
            st.set(ins.rt, X.SLT(st.reg(ins.rs), C(ins.imm)))
        elif o == "sltiu":
            st.set(ins.rt, X.SLT(st.reg(ins.rs), C(ins.imm), True))
        elif o in ("addu", "add", "daddu", "dadd"):
            st.set(ins.rd, X.ADD(st.reg(ins.rs), st.reg(ins.rt)))
        elif o in ("subu", "sub", "dsubu", "dsub"):
            st.set(ins.rd, X.SUB(st.reg(ins.rs), st.reg(ins.rt)))
        elif o == "and":
            st.set(ins.rd, X.AND(st.reg(ins.rs), st.reg(ins.rt)))
        elif o == "or":
            st.set(ins.rd, X.OR(st.reg(ins.rs), st.reg(ins.rt)))
        elif o == "xor":
            st.set(ins.rd, X.XOR(st.reg(ins.rs), st.reg(ins.rt)))
        elif o == "nor":
            v = X.OR(st.reg(ins.rs), st.reg(ins.rt))
            st.set(ins.rd, X.NOT(v))
        elif o == "slt":
            st.set(ins.rd, X.SLT(st.reg(ins.rs), st.reg(ins.rt)))
        elif o == "sltu":
            st.set(ins.rd, X.SLT(st.reg(ins.rs), st.reg(ins.rt), True))
        elif o == "sll":
            st.set(ins.rd, X.SHL(st.reg(ins.rt), ins.sa))
        elif o == "srl":
            st.set(ins.rd, X.SHR(st.reg(ins.rt), ins.sa))
        elif o == "sra":
            st.set(ins.rd, X.SAR(st.reg(ins.rt), ins.sa))
        elif o == "sllv":
            st.set(ins.rd, ("shl", st.reg(ins.rt), X.AND(st.reg(ins.rs), C(31))))
        elif o == "srlv":
            st.set(ins.rd, ("shr", st.reg(ins.rt), X.AND(st.reg(ins.rs), C(31))))
        elif o == "srav":
            st.set(ins.rd, ("sar", st.reg(ins.rt), X.AND(st.reg(ins.rs), C(31))))
        elif o in ("dsll", "dsll32", "dsrl", "dsrl32", "dsra", "dsra32", "dsllv", "dsrlv", "dsrav"):
            st.set(ins.rd, ("d" + o, st.reg(ins.rt), C(ins.sa)))
        elif o in ("mult", "multu"):
            x, y = st.reg(ins.rs), st.reg(ins.rt)
            st.lo = ("mul" if o == "mult" else "mulu", x, y)
            st.hi = ("mulhi" if o == "mult" else "mulhiu", x, y)
        elif o in ("div", "divu"):
            x, y = st.reg(ins.rs), st.reg(ins.rt)
            st.lo = ("div" if o == "div" else "divu", x, y)
            st.hi = ("mod" if o == "div" else "modu", x, y)
        elif o == "mflo":
            st.set(ins.rd, st.lo if st.lo is not None else ("in", "lo"))
        elif o == "mfhi":
            st.set(ins.rd, st.hi if st.hi is not None else ("in", "hi"))
        elif o in ("movz", "movn"):
            c = X.truth(st.reg(ins.rt))
            if o == "movz":
                c = X.bnot(c)
            st.set(ins.rd, X.ITE(c, st.reg(ins.rs), st.reg(ins.rd)))
        elif o in LOAD_SIZE:
            size = LOAD_SIZE[o]
            sgn = o in ("lb", "lh")
            sl = self._slot_of(st, ins)
            if sl is not None:
                name, off = sl
                if name == "CCR":
                    st.set(ins.rt, st.ccr)
                elif off == 0:
                    v = st.slot_value(name, size)
                    st.set(ins.rt, X.SEXT8(v) if (sgn and size == 1) else X.SEXT16(v) if (sgn and size == 2) else v)
                else:
                    st.set(ins.rt, ("slotpart", name, off, size))
            else:
                ea = self._ea(st, ins)
                st.set(ins.rt, ("mem", ea, size, sgn))
        elif o in STORE_SIZE:
            size = STORE_SIZE[o]
            v = st.reg(ins.rt)
            sl = self._slot_of(st, ins)
            if sl is not None:
                name, off = sl
                if name == "CCR":
                    if v[0] == "ccr":
                        st.ccr = v[:2]
                        st.ccr_assigned |= X.ccr_assigned(v)
                        if out is not None:
                            out.append(("ccr", ("ccr", v[1], st.ccr_assigned), a))
                            st.ccr_assigned = frozenset()
                    else:
                        st.ccr = ("ccr", X.entry_snap())
                        if out is not None:
                            out.append(("ccr", ("ccr_unknown", v), a))
                elif off == 0:
                    st.slot_store(name, size, v)
                    if out is not None:
                        out.append(("store", ("slot", name, size), v, a))
                elif out is not None:
                    out.append(("store", ("slotpart", name, off, size), v, a))
            else:
                ea = self._ea(st, ins)
                if out is not None:
                    self._emit_store(out, ea, size, v, a)
        elif o in ("jr", "jalr", "j", "jal", "b") or o in COND_BRANCHES:
            raise AssertionError("control flow handled by run()")
        else:
            if out is not None:
                out.append(("mips", ins, a))
            if o in ("mfhi", "mflo"):
                st.set(ins.rd, ("in", ins.rd))

    def _emit_store(self, out, ea, size, v, a):
        """Emit a memory store, merging big-endian byte/halfword pieces."""
        if out and out[-1][0] == "store" and out[-1][1][0] == "mem":
            _, (_, pea, psize), pv, pa = out[-1]
            if psize == size:
                # previous = high part at ea - size, current = low part at ea
                if X.ADD(pea, C(size)) == ea and _hi_part(pv, v, size):
                    out[-1] = ("store", ("mem", pea, 2 * size), X.AND(v, C((1 << (16 * size)) - 1)) if size < 2 else v, pa)
                    return
                # previous = low part at ea + size, current = high part at ea
                if X.ADD(ea, C(size)) == pea and _hi_part(v, pv, size):
                    out[-1] = ("store", ("mem", ea, 2 * size), X.AND(pv, C((1 << (16 * size)) - 1)) if size < 2 else pv, pa)
                    return
        out.append(("store", ("mem", ea, size), v, a))

    # ------------------------------------------------------------ driver
    def cond_of(self, st: State, ins: Insn):
        o = ins.op
        rs = st.reg(ins.rs)
        if o in ("beq", "beql"):
            rt = st.reg(ins.rt)
            if ins.rt == ZERO:
                return X.bnot(X.truth(rs))
            return ("eq", rs, rt)
        if o in ("bne", "bnel"):
            rt = st.reg(ins.rt)
            if ins.rt == ZERO:
                return X.truth(rs)
            return ("ne", rs, rt)
        if o in ("blez", "blezl"):
            return ("le0", rs)
        if o in ("bgtz", "bgtzl"):
            return ("gt0", rs)
        if o in ("bltz", "bltzl", "bltzal"):
            return ("lt0", rs)
        if o in ("bgez", "bgezl", "bgezal"):
            return ("ge0", rs)
        raise ValueError(o)

    def _run_arm(self, st: State, insns: list[Insn], join: int, out: list) -> State:
        """Execute a diamond arm (CCR-only code with optional early exits to `join`)."""
        st = st.copy()
        k = 0
        exits = []      # (cond, state at exit)
        while k < len(insns):
            ins = insns[k]
            if ins.op in COND_BRANCHES:
                c = self.cond_of(st, ins)
                delay = insns[k + 1] if k + 1 < len(insns) else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, None)
                exits.append((c, st.copy()))
                k += 2
                continue
            self.exec_insn(st, ins, None)
            k += 1
        for c, s in reversed(exits):
            st = merge_states(c, s, st)
        return st

    def run(self) -> list:
        insns = self.insns
        n = len(insns)
        # real labels: targets of branches that are not diamonds; computed in a pre-pass
        diamonds = {}
        for i, ins in enumerate(insns):
            if ins.op in COND_BRANCHES:
                d = self.find_diamond(i)
                if d is not None:
                    diamonds[i] = d
        inside = set()
        for i, (kind, t, j) in diamonds.items():
            inside.update(range(i + 1, j))
        for i, ins in enumerate(insns):
            if i in inside:
                continue
            if ins.op in COND_BRANCHES and i not in diamonds:
                self.labels.add(ins.target)
            elif ins.op in ("b", "j"):
                self.labels.add(ins.target)
        self.diamonds = {insns[i].addr for i in diamonds}
        out = self.effects
        st = State.fresh(self.gp)
        i = 0
        dead = False
        while i < n:
            ins = insns[i]
            if ins.addr in self.labels:
                out.append(("label", ins.addr, ins.addr))
                st = State.fresh(self.gp)
                dead = False
            if dead:
                if ins.op != "nop":
                    out.append(("mips", ins, ins.addr))
                i += 1
                continue
            o = ins.op
            if o in COND_BRANCHES:
                d = diamonds.get(i)
                c = self.cond_of(st, ins)
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                if d is None:
                    out.append(("branch", c, ins.target, ins.addr))
                    i += 2
                    continue
                kind, t, j = d
                if kind == "one":
                    arm = insns[i + 2:t]
                    s_a = self._run_arm(st, arm, ins.target, out)
                    st = merge_states(c, st, s_a)
                else:
                    arm_a = insns[i + 2:t]
                    # strip the `b J` + delay + padding, keep the delay slot in the body
                    body = []
                    for k, x in enumerate(arm_a):
                        if x.op == "b":
                            if k + 1 < len(arm_a):
                                body.append(arm_a[k + 1])
                            break
                        body.append(x)
                    s_a = self._run_arm(st, body, insns[j].addr, out)
                    s_b = self._run_arm(st, insns[t:j], insns[j].addr, out)
                    st = merge_states(c, s_b, s_a)
                out.append(("ccr", ("ccr", st.ccr[1], st.ccr_assigned), ins.addr))
                st.ccr_assigned = frozenset()
                i = j
                continue
            if o in ("b", "j"):
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                out.append(("jump", ins.target, ins.addr))
                dead = True
                i += 2
                continue
            if o == "jal":
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                out.append(("call", ins.target, {r: st.reg(r) for r in (4, 5, 6, 7)}, ins.addr))
                st.clobber_call()
                i += 2
                continue
            if o == "jalr":
                tgt = st.reg(ins.rs)
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                out.append(("callreg", tgt, ins.addr))
                st.clobber_call()
                i += 2
                continue
            if o == "jr":
                tgt = st.reg(ins.rs)
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                if ins.rs == RA:
                    out.append(("rts", ins.addr))
                else:
                    out.append(("jumpreg", tgt, ins.addr))
                dead = True
                i += 2
                continue
            self.exec_insn(st, ins, out)
            i += 1
        return out


def _hi_part(hi, lo, size: int) -> bool:
    """hi == lo >> (8*size) (modulo masking)?"""
    want = X.SHR(lo, 8 * size)
    return X.strip(hi) == X.strip(want) or hi == want or X.AND(hi, C((1 << (8 * size)) - 1)) == X.AND(want, C((1 << (8 * size)) - 1))


def run_function(code: bytes, base: int, **kw) -> Executor:
    ex = Executor(decode_all(code, base), **kw)
    ex.run()
    return ex


def fmt_effect(e) -> str:
    k = e[0]
    a = e[-1]
    if k == "label":
        return f"L_{e[1]:x}:"
    if k == "store":
        d = e[1]
        if d[0] == "slot":
            dest = f"{d[1]}.{ {1: 'b', 2: 'w', 4: 'l', 8: 'q'}[d[2]] }"
        elif d[0] == "mem":
            dest = f"[{X.fmt(d[1])}].{ {1: 'b', 2: 'w', 4: 'l', 8: 'q'}[d[2]] }"
        else:
            dest = repr(d)
        return f"{a:08x}  {dest} = {X.fmt(e[2])}"
    if k == "ccr":
        return f"{a:08x}  {X.fmt(e[1]) if e[1][0] == 'ccr' else 'CCR = ' + X.fmt(e[1][1])}"
    if k == "branch":
        return f"{a:08x}  if {X.fmtb(e[1])} goto L_{e[2]:x}"
    if k == "jump":
        return f"{a:08x}  goto L_{e[1]:x}"
    if k == "call":
        return f"{a:08x}  call {e[1]:#x}"
    if k == "rts":
        return f"{a:08x}  rts"
    if k == "jumpreg":
        return f"{a:08x}  jump {X.fmt(e[1])}"
    if k == "callreg":
        return f"{a:08x}  callreg {X.fmt(e[1])}"
    if k == "mips":
        return f"{a:08x}  ; mips {e[1].text()}"
    return repr(e)
