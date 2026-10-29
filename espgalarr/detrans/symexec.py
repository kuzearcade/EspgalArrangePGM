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
from .mips import (AT, FP, GP, RA, SP, ZERO, COND_BRANCHES, LOAD_SIZE, STORE_SIZE, Insn,
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
    pending: dict = field(default_factory=dict)   # slot name -> {byte offset: (size, value, addr)}
    stack: dict = field(default_factory=dict)     # (base reg, offset) -> spilled value

    @staticmethod
    def fresh(gp: int = GP_DEFAULT) -> "State":
        s = State()
        s.ccr = ("ccr", X.entry_snap())
        s.regs[GP] = C(gp)
        s.regs[ZERO] = C(0)
        return s

    def copy(self) -> "State":
        s = State(dict(self.regs), dict(self.slots), self.ccr, dict(self.mem), self.hi, self.lo,
                  self.ccr_assigned, {k: dict(v) for k, v in self.pending.items()}, dict(self.stack))
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
    if cond == TRUE:
        return a.copy()
    if cond == FALSE:
        return b.copy()
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
    for k in set(a.stack) & set(b.stack):
        if a.stack[k] == b.stack[k]:
            s.stack[k] = a.stack[k]
    for k in set(a.pending) & set(b.pending):
        if a.pending[k] == b.pending[k]:
            s.pending[k] = dict(a.pending[k])
    s.hi, s.lo = a.hi, a.lo
    return s


class Executor:
    """Executes one function (list of decoded instructions)."""

    def __init__(self, insns: list[Insn], gp: int = GP_DEFAULT, slot_base: int = SLOT_BASE,
                 read_word=None, extra_labels=None):
        self.insns = insns
        self.read_word = read_word          # callback: address -> 32-bit word or None
        self.extra_labels = extra_labels or ()   # overlay-wide branch targets
        self.gp = gp
        self.slot_base = slot_base
        self.index = {i.addr: k for k, i in enumerate(insns)}
        self.effects: list = []
        self.labels: set[int] = set()
        self.branch_states: dict[int, list[State]] = {}
        self.diamonds: set[int] = set()
        self.start = insns[0].addr if insns else 0
        self.end = insns[-1].addr + 4 if insns else 0
        self.cur = 0

    # ------------------------------------------------------------ classification
    def _is_ccr_only(self, ins: Insn, join: int) -> bool:
        o = ins.op
        if o == "nop":
            return True
        if o == "lhu" and ins.rs == GP and ins.imm == CCR_GP_OFF:
            return True
        if o == "sh":
            return ins.rs == GP and ins.imm == CCR_GP_OFF
        if o in ("ori", "andi", "xori", "slti", "sltiu", "addiu", "daddiu", "lui"):
            return True
        if o in ("and", "or", "xor", "nor", "slt", "sltu", "sll", "srl", "sra", "addu", "subu"):
            return True
        if o in COND_BRANCHES and ins.target == join:
            return True
        return False

    def find_diamond(self, i: int):
        """Classify the conditional branch at index i.

        Returns None for a real 68k branch, or (kind, t, j):
          'region'  - [i, j) is a single-entry CCR-only region (flag computation compiled
                      to short-circuit branches); execution resumes at j,
          'onejump' - the fall-through arm is CCR-only and ends with `b T` to the
                      (possibly far, tail-merged) branch target T; resume after the jump.
        """
        ins = self.insns[i]
        if ins.target <= ins.addr or ins.target >= self.end:
            return None
        t = self.index.get(ins.target)
        if t is None or t <= i + 1:
            return None
        n = len(self.insns)
        # --- general region: scan forward, extending the end to the farthest target
        j = t
        k = i + 2
        ok = True
        has_sh = False
        d0 = self.insns[i + 1] if i + 1 < n else None
        if d0 is not None and d0.op in ("j", "jal", "jr", "jalr", "b") or d0 is not None and d0.op in COND_BRANCHES:
            ok = False
        if d0 is not None and d0.op == "sh" and self._is_ccr_only(d0, ins.target):
            has_sh = True               # e.g. ext.l: CCR store in the delay slot, `or` in the arm
        while k < j:
            x = self.insns[k]
            if x.op in COND_BRANCHES or x.op == "b":
                tg = self.index.get(x.target)
                if tg is None or tg <= k or tg > j + 24 or j - i > 80:
                    ok = False
                    break
                j = max(j, tg)
                d = self.insns[k + 1] if k + 1 < n else None
                if d is not None and not self._is_ccr_only(d, x.target):
                    ok = False
                    break
                if d is not None and d.op == "sh":
                    has_sh = True
                k += 2
                continue
            if x.op in ("j", "jal", "jr", "jalr"):
                ok = False
                break
            if not self._is_ccr_only(x, ins.target):
                ok = False
                break
            if x.op == "sh":
                has_sh = True
            k += 1
        if ok and has_sh and j < n and not self._entered_from_outside(i, j):
            return ("region", t, j)
        # --- fall-through arm ending in `b T`
        arm = []
        k = i + 2
        while k < n and k - i <= 26:
            x = self.insns[k]
            if x.op == "b":
                if x.target != ins.target:
                    return None
                delay = self.insns[k + 1] if k + 1 < n else None
                body = arm + ([delay] if delay is not None and delay.op != "nop" else [])
                if body and any(y.op == "sh" for y in body) and all(self._is_ccr_only(y, ins.target) for y in body):
                    return ("onejump", t, k + 2)
                return None
            if x.op in ("j", "jal", "jr", "jalr") or x.op in COND_BRANCHES or k == t:
                return None
            if not self._is_ccr_only(x, ins.target):
                return None
            arm.append(x)
            k += 1
        return None

    def _entered_from_outside(self, i: int, j: int) -> bool:
        """Does any branch outside [i, j) target the inside of (i, j)?"""
        lo, hi = self.insns[i].addr, self.insns[j].addr if j < len(self.insns) else self.end
        for k, x in enumerate(self.insns):
            if i <= k < j:
                continue
            if (x.op in COND_BRANCHES or x.op in ("b", "j")) and lo < x.target < hi:
                return True
        return False

    def _run_region(self, st: State, i: int, j: int, entry_cond=None) -> State:
        """Evaluate the CCR-only region [i, j) (forward branches only) by forward dataflow
        over its basic blocks with path conditions; returns the state at j."""
        insns = self.insns
        # block starts inside the region
        starts = {i}
        for k in range(i, j):
            x = insns[k]
            if x.op in COND_BRANCHES or x.op == "b":
                tg = self.index.get(x.target)
                if tg is not None and i < tg <= j:
                    starts.add(tg)
                if k + 2 <= j:
                    starts.add(k + 2)
        order = sorted(starts)
        incoming: dict[int, list] = {k: [] for k in order}
        incoming[j] = []
        incoming[i].append((TRUE, st))
        for bi, b0 in enumerate(order):
            b1 = order[bi + 1] if bi + 1 < len(order) else j
            edges = incoming.get(b0, [])
            if not edges:
                continue
            # merge incoming states
            reach, cur = edges[0]
            for g, s2 in edges[1:]:
                cur = merge_states(g, s2, cur)
                reach = X.bor(reach, g)
            cur = cur.copy()
            k = b0
            fall = True
            while k < b1:
                x = insns[k]
                self.cur = k
                if x.op in COND_BRANCHES or x.op == "b":
                    if x.op == "b":
                        c = TRUE
                    elif k == i and entry_cond is not None:
                        c = entry_cond
                    else:
                        c = self.cond_of(cur, x)    # (before this branch's delay slot runs)
                    d = insns[k + 1] if k + 1 < len(insns) else None
                    if d is not None and d.op != "nop" and k != i:     # entry delay slot already done
                        self.exec_insn(cur, d, None)
                    tg = self.index.get(x.target)
                    tg = tg if tg is not None and tg <= j else j
                    incoming.setdefault(tg, []).append((X.band(reach, c), cur.copy()))
                    if x.op == "b":
                        fall = False
                    else:
                        reach = X.band(reach, X.bnot(c))
                    k += 2
                    continue
                self.exec_insn(cur, x, None)
                k += 1
            if fall:
                incoming.setdefault(b1, []).append((reach, cur))
        edges = incoming.get(j, [])
        if not edges:
            return st.copy()
        g0, cur = edges[0]
        for g, s2 in edges[1:]:
            cur = merge_states(g, s2, cur)
        return cur

    # ------------------------------------------------------------ evaluation
    def _ea(self, st: State, ins: Insn):
        base = st.reg(ins.rs)
        return X.ADD(base, C(ins.imm)) if ins.imm else base

    def _const_function(self, addr: int) -> int | None:
        """Value of a function that only returns a constant (`jr $ra ; addiu/ori $v0,$zero,k`
        or `lui $v0,k`), else None."""
        if self.read_word is None:
            return None
        from .mips import decode
        w0, w1 = self.read_word(addr), self.read_word(addr + 4)
        if w0 != 0x03E00008 or w1 is None:
            return None
        d = decode(w1, addr + 4)
        if d.rt != 2:
            return None
        if d.op == "addiu" and d.rs == ZERO:
            return d.imm & M32
        if d.op == "ori" and d.rs == ZERO:
            return d.uimm
        if d.op == "lui":
            return (d.uimm << 16) & M32
        return None

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
        if a == CCR_ADDR + 1 and ins.op in ("lbu", "lb", "sb"):
            return ("CCRB", 0)          # the flags byte (C=1 V=2 Z=4 N=8 X=16)
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
        elif o in ("dsll", "dsra", "dsrl"):
            # 64-bit shifts by < 32 of a sign-extended 32-bit value: identity on the low word
            st.set(ins.rd, st.reg(ins.rt) if ins.sa < 32 else ("d" + o, st.reg(ins.rt), C(ins.sa)))
        elif o == "dsll32":
            st.set(ins.rd, ("dshl", st.reg(ins.rt), C(32 + ins.sa)))
        elif o in ("dsra32", "dsrl32"):
            v = st.reg(ins.rt)
            if v[0] == "dshl" and v[2] == C(32 + ins.sa):
                bits = 32 - ins.sa
                inner = v[1]
                if bits == 8:
                    st.set(ins.rd, X.SEXT8(inner) if o == "dsra32" else X.LO8(inner))
                elif bits == 16:
                    st.set(ins.rd, X.SEXT16(inner) if o == "dsra32" else X.LO16(inner))
                elif bits == 32:
                    st.set(ins.rd, inner)
                else:
                    st.set(ins.rd, ("d" + o, v, C(ins.sa)))
            else:
                st.set(ins.rd, ("d" + o, v, C(ins.sa)))
        elif o in ("dsllv", "dsrlv", "dsrav"):
            st.set(ins.rd, ("d" + o, st.reg(ins.rt), st.reg(ins.rs)))
        elif o in ("mult", "multu"):
            x, y = st.reg(ins.rs), st.reg(ins.rt)
            st.lo = ("mul" if o == "mult" else "mulu", x, y)
            st.hi = ("mulhi" if o == "mult" else "mulhiu", x, y)
            if ins.rd:
                st.set(ins.rd, st.lo)       # R5900 three-operand form: rd = LO
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
        elif o in LOAD_SIZE or o in STORE_SIZE or o in ("lq", "sq"):
            if ins.rs == SP or (ins.rs == FP and st.reg(FP)[0] == "in"):
                # the MIPS stack frame: callee-saved registers and spills of 68k values.
                # Stores are remembered and forwarded to the matching loads.
                key = (ins.rs, ins.imm)
                if o in LOAD_SIZE:
                    size = LOAD_SIZE[o]
                    v = None
                    for (b_, off), (ssize, sv) in st.stack.items():
                        if b_ == ins.rs and off <= ins.imm and ins.imm + size <= off + ssize:
                            v = X.SHR(sv, 8 * (ins.imm - off)) if ins.imm != off else sv
                            break
                    if v is None:
                        v = ("stack", ins.imm)
                    elif size < 4:
                        v = X.AND(v, C(0xFF if size == 1 else 0xFFFF))
                        if o in ("lb", "lh"):
                            v = X.SEXT8(v) if o == "lb" else X.SEXT16(v)
                    st.set(ins.rt, v)
                elif o in STORE_SIZE:
                    for k_ in [k_ for k_ in st.stack if k_[0] == ins.rs and k_[1] < ins.imm + STORE_SIZE[o] and ins.imm < k_[1] + st.stack[k_][0]]:
                        del st.stack[k_]
                    st.stack[key] = (STORE_SIZE[o], st.reg(ins.rt))
                elif o == "lq":
                    st.set(ins.rt, ("stack", ins.imm))
                return
            if o in ("lq", "sq"):
                if out is not None:
                    out.append(("mips", ins, a))
                return
            self._ldst(st, ins, out)
        elif o in ("jr", "jalr", "j", "jal", "b") or o in COND_BRANCHES:
            raise AssertionError("control flow handled by run()")
        else:
            if out is not None:
                out.append(("mips", ins, a))
            if o in ("mfhi", "mflo"):
                st.set(ins.rd, ("in", ins.rd))

    def _ldst(self, st: State, ins: Insn, out) -> None:
        o = ins.op
        a = ins.addr
        if o in LOAD_SIZE:
            size = LOAD_SIZE[o]
            sgn = o in ("lb", "lh")
            sl = self._slot_of(st, ins)
            if sl is not None:
                name, off = sl
                if name == "CCR":
                    st.set(ins.rt, st.ccr)
                elif name == "CCRB":
                    st.set(ins.rt, ("ccrb", st.ccr[1], frozenset()))
                elif off == 0:
                    v = st.slot_value(name, size)
                    st.set(ins.rt, X.SEXT8(v) if (sgn and size == 1) else X.SEXT16(v) if (sgn and size == 2) else v)
                else:
                    leaf = ("r68", name, st.slots.get(name, 0))
                    v = X.AND(X.SHR(leaf, 8 * off), C(0xFF if size == 1 else 0xFFFF))
                    st.set(ins.rt, X.SEXT8(v) if (sgn and size == 1) else X.SEXT16(v) if (sgn and size == 2) else v)
            else:
                ea = self._ea(st, ins)
                st.set(ins.rt, ("mem", ea, size, sgn))
        elif o in STORE_SIZE:
            size = STORE_SIZE[o]
            v = st.reg(ins.rt)
            sl = self._slot_of(st, ins)
            if sl is not None:
                name, off = sl
                if name == "CCRB":
                    v = X.ccrb_to_ccr(v)
                    name = "CCR"
                if name == "CCR":
                    if v[0] != "ccr":
                        # CCR loaded before a label (hoisted) and masked after it: the
                        # unknown register can only have held the CCR
                        for r_ in sorted({x[1] for x in _leaves_of(v, "in")}):
                            v = X.subst(v, ("in", r_), ("ccr", X.entry_snap()))
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
                elif off == 0 and size == 4:
                    st.pending.pop(name, None)
                    st.slot_store(name, size, v)
                    if out is not None:
                        out.append(("store", ("slot", name, size), v, a))
                elif size == 2 and off == 2 and self._slot_half(st, name, off, v, a, out):
                    pass
                else:
                    if off == 0 and size == 2:
                        st.pending.pop(name, None)
                    self._slot_piece(st, name, off, size, v, a, out)
            else:
                ea = self._ea(st, ins)
                if out is not None:
                    self._emit_store(out, ea, size, v, a)

    def _slot_half(self, st: State, name: str, off: int, v, a, out) -> bool:
        """`swap dN` is translated as two halfword moves between the slot halves."""
        if off != 2 or out is None:
            return False
        for j in range(len(out) - 1, max(-1, len(out) - 6), -1):
            e = out[j]
            if e[0] == "store" and e[1] == ("slot", name, 2):
                hv = X.strip(e[2])
                lv = X.strip(v)
                if hv[0] == "shr" and hv[2] == C(16) and X.strip(hv[1])[0] == "r68" and X.strip(hv[1])[1] == name \
                        and lv[0] == "r68" and lv[1] == name:
                    leaf = X.strip(hv[1])
                    del out[j]
                    st.slot_store(name, 4, None)
                    out.append(("store", ("slot", name, 4), ("swap", leaf), a))
                    return True
            if e[0] in ("label", "branch", "jump", "call", "rts"):
                break
        return False

    def _slot_piece(self, st: State, name: str, off: int, size: int, v, a, out) -> None:
        """A byte store into a register slot.  Offset 0 is emitted at once as a .b store
        (a plain move.b); when bytes 1..3 follow and the four assemble into a big-endian
        memory load (or a register's bytes), the .b effect is replaced by the .l store
        (the translator moves 32-bit operands through the slots byte by byte)."""
        pend = st.pending.setdefault(name, {})
        eff = None
        if off == 0 and size == 1:
            if not all(k in pend and pend[k][0] == 1 for k in (1, 2, 3)):
                # a fresh byte move, unless bytes 3..1 (or byte 1 of a word) came first
                hi = pend.get(1)
                fresh_hi = hi is not None and hi[0] == 1 and (0 not in pend or pend[0][2] < hi[2])
                pend.clear()
                if fresh_hi:
                    pend[1] = hi
            st.slot_store(name, 1, v)
            eff = ("store", ("slot", name, 1), v, a)
            if out is not None:
                out.append(eff)
        pend[off] = (size, v, a, eff)
        if size == 2 and 0 in pend and 2 in pend and pend[0][0] == 2 and pend[2][0] == 2:
            lo_, hi_ = pend[0][1], pend[2][1]
            merged = None
            lo_s, hi_s = X.strip(lo_), X.strip(hi_)
            if lo_s[0] == "mem" and lo_s[2] == 2 and hi_s[0] == "mem" and hi_s[2] == 2 and X.ADD(hi_s[1], C(2)) == lo_s[1]:
                merged = ("mem", hi_s[1], 4, False)
            else:
                full = _unmask(lo_, 2)
                if X.strip(hi_) == X.strip(X.SHR(full, 16)) or X.AND(hi_s, C(0xFFFF)) == X.AND(X.SHR(full, 16), C(0xFFFF)):
                    merged = full
                elif lo_s[0] == "c" and hi_s[0] == "c":
                    merged = C((lo_s[1] & 0xFFFF) | (hi_s[1] << 16))
            if merged is not None:
                e0 = pend[0][3]
                st.pending.pop(name, None)
                st.slot_store(name, 4, merged)
                if out is not None:
                    for j in range(len(out) - 1, -1, -1):
                        if out[j] is e0:
                            del out[j]
                            break
                    out.append(("store", ("slot", name, 4), merged, a))
                return
        if size == 2:
            if off == 0:
                st.slot_store(name, 2, v)
                eff = ("store", ("slot", name, 2), v, a)
                pend[0] = (2, v, a, eff)
                if out is not None:
                    out.append(eff)
            return
        sizes = (4,) if self._more_pieces_ahead(name, st) else (4, 2)
        for n in sizes:
            if all(k in pend for k in range(n)):
                vals = [pend[k][1] for k in range(n)]
                merged = _assemble_pieces(vals, n)
                if merged is not None:
                    e0 = pend[0][3]
                    st.pending.pop(name, None)
                    st.slot_store(name, n, merged)
                    if out is not None:
                        for j in range(len(out) - 1, -1, -1):
                            if out[j] is e0:
                                del out[j]
                                break
                        out.append(("store", ("slot", name, n), merged, a))
                    return
                if n == 4:
                    if pend[0][2] < min(pend[k][2] for k in (1, 2, 3)):
                        # byte 0 is a stale piece of an earlier byte move: keep waiting
                        del pend[0]
                        return
                    st.pending.pop(name, None)
                    if out is not None:
                        for k in (1, 2, 3):
                            sz, vv, aa, _ = pend[k]
                            out.append(("store", ("slotpart", name, k, sz), vv, aa))
                    return

    def _more_pieces_ahead(self, name: str, st: State | None = None) -> bool:
        """Will the next few instructions store bytes 2/3 of this slot?"""
        off = OFF_OF[name]
        for ins in self.insns[self.cur + 1:self.cur + 14]:
            if ins.op != "sb":
                continue
            if ins.rs == AT and ins.imm - off in (2, 3):
                return True
            if ins.rs != AT and ins.imm in (2, 3):
                # through a pointer: only if it holds this slot's address
                # (only constant pointers reach the slots, see _slot_of)
                base = st.reg(ins.rs) if st is not None else None
                if base is None or base[0] == "c" and (base[1] - self.slot_base) & M32 == off:
                    return True
            if ins.op in ("jr", "jalr", "j", "jal"):
                break
        return False

    def _emit_store(self, out, ea, size, v, a):
        """Emit a memory store, merging big-endian byte/halfword pieces (the other half
        may have been emitted a few effects earlier: the compiler interleaves them)."""
        if size < 4:
            for j in range(len(out) - 1, max(-1, len(out) - 10), -1):
                e = out[j]
                if e[0] in ("label", "branch", "jump", "call", "rts", "jumpreg", "callreg"):
                    break
                if e[0] != "store" or e[1][0] != "mem" or e[1][2] != size:
                    continue
                _, (_, pea, psize), pv, pa = e
                mask = C((1 << (8 * size)) - 1)
                if X.ADD(pea, C(size)) == ea and _hi_part(pv, v, size):
                    del out[j]
                    self._emit_store(out, pea, 2 * size, _unmask(v, size), a)
                    return
                if X.ADD(ea, C(size)) == pea and _hi_part(v, pv, size):
                    del out[j]
                    self._emit_store(out, ea, 2 * size, _unmask(pv, size), a)
                    return
        out.append(("store", ("mem", ea, size), v, a))

    # ------------------------------------------------------------ driver
    def cond_of(self, st: State, ins: Insn, names: bool = False):
        o = ins.op
        rs = st.reg(ins.rs)
        truth = X.truth_names if names else X.truth
        if o in ("beq", "beql", "bne", "bnel"):
            rt = st.reg(ins.rt)
            eq = o in ("beq", "beql")
            if ins.rt == ZERO:
                return X.bnot(truth(rs)) if eq else truth(rs)
            # x == 1 / x == 0 on a boolean-valued x (sltu $0,x ; slt ; bool)
            for x, c in ((rs, rt), (rt, rs)):
                if c[0] == "c" and c[1] in (0, 1):
                    if x[0] == "sltu" and x[1] == C(0):
                        bx = truth(x[2])
                    elif x[0] in ("slt", "sltu", "bool"):
                        bx = truth(x)
                    else:
                        continue
                    if c[1] == 0:
                        bx = X.bnot(bx)
                    return bx if eq else X.bnot(bx)
            return ("eq", rs, rt) if eq else ("ne", rs, rt)
        if o in ("blez", "blezl"):
            return ("le0", rs)
        if o in ("bgtz", "bgtzl"):
            return ("gt0", rs)
        if o in ("bltz", "bltzl", "bltzal"):
            return X.sign_test(rs)
        if o in ("bgez", "bgezl", "bgezal"):
            return X.sign_test(rs, True)
        raise ValueError(o)

    def _run_arm(self, st: State, insns: list[Insn], join: int, out: list) -> State:
        """Execute a diamond arm (CCR-only code with optional early exits to `join`)."""
        st = st.copy()
        k = 0
        exits = []      # (cond, state at exit)
        while k < len(insns):
            ins = insns[k]
            self.cur = self.index.get(ins.addr, self.cur)
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

    def _find_jumptables(self, diamonds) -> dict[int, list[int]]:
        """`sltiu v,idx,N ; beqz v,default ; sll ; lui/addiu ; addu ; lw t ; jr t`: the
        translation of a 68k jump table.  Returns {jr address: [targets]}."""
        out = {}
        if self.read_word is None:
            return out
        insns = self.insns
        for i, ins in enumerate(insns):
            if ins.op != "jr" or ins.rs == RA:
                continue
            # evaluate the last ~12 instructions symbolically to get the jr operand
            st = State.fresh(self.gp)
            lo = max(0, i - 14)
            n_entries = None
            for k in range(lo, i):
                x = insns[k]
                if x.op in COND_BRANCHES or x.op in ("j", "jal", "jr", "jalr", "b"):
                    continue
                if x.op == "sltiu":
                    n_entries = x.imm
                try:
                    self.exec_insn(st, x, None)
                except Exception:
                    pass
            v = st.reg(ins.rs)
            if v[0] != "mem" or v[2] != 4:
                continue
            addr = v[1]
            base = None
            if addr[0] == "add":
                for p, q in ((addr[1], addr[2]), (addr[2], addr[1])):
                    if p[0] == "c":
                        base = p[1]
            if base is None:
                continue
            n = n_entries if n_entries and n_entries <= 512 else 64
            tgts = []
            for e in range(n):
                w = self.read_word(base + 4 * e)
                if w is None or not (self.start <= w < self.end) or w & 3:
                    if n_entries is None:
                        break
                    continue
                tgts.append(w)
            if tgts:
                out[ins.addr] = tgts
        return out

    def _label_state(self, prev: State | None, addr: int) -> State:
        """State at a real label: fresh, except that register values the compiler may have
        hoisted above the branch (CCR copies, 68k register loads, address constants) are
        kept when every known predecessor (fall-through, branches seen so far) agrees."""
        st = State.fresh(self.gp)
        preds = ([prev] if prev is not None else []) + self.branch_states.get(addr, [])
        if not preds:
            return self._prior_facts(st, addr, [])
        if all(p.ccr == preds[0].ccr for p in preds[1:]) and preds[0].ccr[0] == "ccr":
            st.ccr = ("ccr", preds[0].ccr[1])
        for r, v in preds[0].regs.items():
            if r in (ZERO, GP, SP):
                continue
            if any(p.regs.get(r) != v for p in preds[1:]):
                continue
            if v[0] == "ccr":
                # a CCR copy made before the branch: re-base it on this label's flags,
                # keeping only the explicit assignments made to it
                asg = X.ccr_assigned(v)
                snap = dict(v[1])
                st.regs[r] = ("ccr", X.snap_set(st.ccr[1], **{f: snap[f] for f in asg}), asg)
            elif v[0] == "c" or _leaves_of(v, "r68") and not _leaves_of(v, "in") and not _leaves_of(v, "ret"):
                st.regs[r] = v
        for k in set().union(*(p.slots for p in preds)):
            st.slots[k] = max(p.slots.get(k, 0) for p in preds)
        for k, v in preds[0].stack.items():
            if all(p.stack.get(k) == v for p in preds[1:]):
                st.stack[k] = v
        return self._prior_facts(st, addr, preds)

    def _prior_facts(self, st: State, addr: int, preds: list) -> State:
        """Registers that every incoming branch (seen in the first pass) and every known
        predecessor leave as the same constant / the same 68k register's current value."""
        prior = getattr(self, "prior", {}).get(addr)
        if prior:
            now = [_reg_facts(p) for p in preds]
            for r in set().union(*(set(f) for f in prior)):
                if r in st.regs or r in (ZERO, GP, SP):
                    continue
                fact = prior[0].get(r)
                if fact is None or any(f.get(r) != fact for f in prior + now):
                    continue
                st.regs[r] = C(fact[1]) if fact[0] == "c" else ("r68", fact[1], st.slots.get(fact[1], 0))
        return st

    def run(self) -> list:
        """Two passes: the first learns, for every label, which MIPS registers hold a constant
        or the current value of a 68k register on each incoming branch; the second uses that
        at labels reached by later (backward) branches, where the compiler keeps e.g. a6
        cached in a MIPS register across a loop."""
        self._run_once()
        self.prior = {a: [_reg_facts(s) for s in states] for a, states in self.branch_states.items()}
        self.effects, self.branch_states, self.labels = [], {}, set()
        return self._run_once()

    def _run_once(self) -> list:
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
            if kind == "onejump":
                self.labels.add(insns[t].addr)
        for i, ins in enumerate(insns):
            if i in inside:
                continue
            if ins.op in COND_BRANCHES and i not in diamonds:
                self.labels.add(ins.target)
            elif ins.op in ("b", "j"):
                self.labels.add(ins.target)
        self.diamonds = {insns[i].addr for i in diamonds}
        for a in self.extra_labels:
            if self.start < a < self.end and a not in {insns[k].addr for k in inside}:
                self.labels.add(a)
        self.jumptables = self._find_jumptables(diamonds)
        for tgts in self.jumptables.values():
            self.labels.update(t for t in tgts if self.start < t < self.end)
        out = self.effects
        st = State.fresh(self.gp)
        i = 0
        dead = False
        while i < n:
            ins = insns[i]
            self.cur = i
            if dead and ins.op != "nop" and ins.addr not in self.labels:
                # code after a jump that no branch reaches: another entry point that shares
                # this function body (reached from elsewhere); de-translate it from scratch
                self.labels.add(ins.addr)
            if ins.addr in self.labels:
                st = self._label_state(st if not dead else None, ins.addr)
                out.append(("label", ins.addr, st.ccr[1], ins.addr))
                dead = False
            if dead:
                if ins.op != "nop":
                    out.append(("mips", ins, ins.addr))
                i += 1
                continue
            o = ins.op
            if o in COND_BRANCHES:
                d = diamonds.get(i)
                if d is not None and d[0] == "region":
                    delay = insns[i + 1] if i + 1 < n else None
                    # a delay slot that overwrites the tested register: take the condition
                    # before it (otherwise after it, where a CCR store reads as plain flags)
                    clobbers = delay is not None and _srcs_dst(delay)[1] in (ins.rs, ins.rt) \
                        and _srcs_dst(delay)[1] not in (None, ZERO)
                    c0 = self.cond_of(st, ins) if clobbers else None
                    if delay is not None and delay.op != "nop":
                        self.exec_insn(st, delay, out)
                    st = self._run_region(st, i, d[2], entry_cond=c0)
                    out.append(("ccr", ("ccr", st.ccr[1], st.ccr_assigned), ins.addr))
                    st.ccr_assigned = frozenset()
                    i = d[2]
                    continue
                c = self.cond_of(st, ins)
                cn = self.cond_of(st, ins, names=True)
                snap_before = st.ccr[1]
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                if d is None:
                    out.append(("branch", flagify(cn, snap_before), ins.target, snap_before, ins.addr))
                    self.branch_states.setdefault(ins.target, []).append(st.copy())
                    i += 2
                    continue
                kind, t, j = d
                if True:
                    arm = []
                    for x in insns[i + 2:j - 2]:
                        arm.append(x)
                    if insns[j - 1].op != "nop":
                        arm.append(insns[j - 1])
                    s_a = self._run_arm(st, arm, ins.target, out)
                    st = merge_states(c, st, s_a)
                out.append(("ccr", ("ccr", st.ccr[1], st.ccr_assigned), ins.addr))
                st.ccr_assigned = frozenset()
                if kind == "onejump":
                    out.append(("jump", ins.target, insns[j - 2].addr))
                    dead = True
                i = j
                continue
            if o in ("b", "j"):
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                if o == "j" and not (self.start <= ins.target < self.end):
                    # tail jump to another routine: keep the MIPS argument registers (a native
                    # service, or a helper given a code pointer in $a0)
                    out.append(("jump", ins.target, {r: st.reg(r) for r in (4, 5, 6, 7)}, ins.addr))
                else:
                    out.append(("jump", ins.target, ins.addr))
                self.branch_states.setdefault(ins.target, []).append(st.copy())
                dead = True
                i += 2
                continue
            if o == "jal":
                delay = insns[i + 1] if i + 1 < n else None
                if delay is not None and delay.op != "nop":
                    self.exec_insn(st, delay, out)
                k = self._const_function(ins.target)
                if k is not None:
                    # `jr $ra ; li $v0,k`: a native constant (per-stage parameter); no call
                    st.set(2, C(k))
                    i += 2
                    continue
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
                elif ins.addr in self.jumptables:
                    out.append(("jumptable", tgt, self.jumptables[ins.addr], ins.addr))
                else:
                    out.append(("jumpreg", tgt, ins.addr))
                dead = True
                i += 2
                continue
            self.exec_insn(st, ins, out)
            i += 1
        return out


def _leaves_of(e, kind, acc=None):
    if acc is None:
        acc = []
    if not isinstance(e, tuple):
        return acc
    if e[0] == kind:
        acc.append(e)
        return acc
    for x in e[1:]:
        if isinstance(x, tuple):
            _leaves_of(x, kind, acc)
    return acc


def _srcs_dst(ins):
    """(source registers, destination register or None) of a MIPS instruction."""
    from .mips import COND_BRANCHES, LOADS, RTYPE3, SHIFTS, STORES
    o = ins.op
    if o in STORES:
        return (ins.rs, ins.rt), None
    if o in LOADS or o in ("addiu", "addi", "daddiu", "daddi", "andi", "ori", "xori", "slti", "sltiu"):
        return (ins.rs,), ins.rt
    if o == "lui":
        return (), ins.rt
    if o in RTYPE3:
        return (ins.rs, ins.rt), ins.rd
    if o in SHIFTS:
        return (ins.rt,), ins.rd
    if o in ("beq", "bne", "beql", "bnel"):
        return (ins.rs, ins.rt), None
    if o in COND_BRANCHES or o in ("jr", "jalr"):
        return (ins.rs,), None
    return (), None


def _reg_facts(st: State) -> dict:
    """MIPS registers holding a constant or a 68k register's current (unmodified) value."""
    out = {}
    for r, v in st.regs.items():
        if v[0] == "c":
            out[r] = ("c", v[1])
        elif v[0] == "r68" and len(v) == 3 and v[2] == st.slots.get(v[1], 0):
            out[r] = ("cache", v[1])
    return out


def reads_arg_regs(addr: int, read_word, limit: int = 2000, depth: int = 3) -> set[int]:
    """MIPS argument registers ($a0-$a3) that the code at addr reads before writing them,
    in a straight-line scan up to its return (branches are followed as fall-through: a
    read on any path counts; inner calls are followed `depth` levels deep)."""
    from .mips import decode
    if read_word is None:
        return set()
    args = {4, 5, 6, 7}
    written, read = set(), set()

    def step(ins):
        srcs, dst = _srcs_dst(ins)
        read.update(r for r in srcs if r in args and r not in written)
        if dst is not None:
            written.add(dst)

    k = 0
    while k < limit:
        w = read_word(addr + 4 * k)
        if w is None:
            break
        ins = decode(w, addr + 4 * k)
        o = ins.op
        if o in ("jal", "j", "jalr", "jr"):
            src, _ = _srcs_dst(ins)
            read.update(r for r in src if r in args and r not in written)
            d = read_word(addr + 4 * k + 4)
            if d is not None:
                step(decode(d, addr + 4 * k + 4))       # the delay slot runs first
            live = args - written
            if o == "jr":
                break
            if o in ("jal", "j") and depth > 0:
                read.update(reads_arg_regs(ins.target, read_word, limit, depth - 1) & live)
            else:
                read.update(live)           # an unknown target (jalr) may take them all
            if o != "jal":
                break
            written.update(args)            # the callee clobbers them; scan on after it
            k += 2
            continue
        step(ins)
        if written >= args:
            break
        k += 1
    return read


def _assemble_pieces(vals, n: int):
    """vals[k] = byte k of a little-endian slot (k=0 is the LSB).  Returns the 32/16-bit
    value if the bytes are a big-endian memory word or a register's bytes."""
    # memory: byte k <- [p + (n-1-k)]
    m0 = X.strip(vals[0])
    if m0[0] == "mem" and m0[2] == 1:
        base = X.ADD(m0[1], C(-(n - 1)))
        ok = True
        for k in range(n):
            mk = X.strip(vals[k])
            if not (mk[0] == "mem" and mk[2] == 1 and mk[1] == X.ADD(base, C(n - 1 - k))):
                ok = False
                break
        if ok:
            return ("mem", base, n, False)
    # constants
    if all(X.strip(v)[0] == "c" for v in vals):
        return C(sum((X.strip(v)[1] & 0xFF) << (8 * k) for k, v in enumerate(vals)))
    # register / expression bytes: byte k == shr(x, 8k) (or sra for the top byte)
    x = X.strip(vals[0])
    if x[0] in ("lo8",):
        x = x[1]
    ok = True
    for k in range(1, n):
        want = X.SHR(x, 8 * k)
        want2 = X.SAR(x, 8 * k)
        got = X.strip(vals[k])
        if got[0] in ("shr", "sar") and got[2] == C(8 * k) and X.strip(got[1]) == x:
            continue                    # shr(lo16(x), 8): byte 1 of a word
        m8 = C(0xFF)
        if got != want and X.AND(got, m8) != X.AND(want, m8) and got != want2 and X.AND(got, m8) != X.AND(want2, m8):
            ok = False
            break
    if ok:
        return x
    return None


def _unmask(e, size: int):
    """Drop the store-size mask the compiler applied to the low piece."""
    if size == 2 and e[0] == "lo16":
        return e[1]
    if size == 1 and e[0] == "lo8":
        return e[1]
    if e[0] == "and" and e[2] == C((1 << (8 * size)) - 1):
        return e[1]
    return e


def _hi_part(hi, lo, size: int) -> bool:
    """hi == lo >> (8*size) (modulo masking)?"""
    lo = _unmask(lo, size)
    want = X.SHR(lo, 8 * size)
    m = C((1 << (8 * size)) - 1)
    return X.strip(hi) == X.strip(want) or hi == want or X.AND(hi, m) == X.AND(want, m)


def flagify(cond, snap):
    """Express a branch condition through CCR flag names when it equals the current
    value of a flag (the compiler forwards the flag computation into the branch)."""
    d = dict(snap)
    for f, v in d.items():
        if v[0] in ("true", "false", "flag"):
            continue
        if cond == v:
            return ("flag", f)
        if cond == X.bnot(v):
            return X.bnot(("flag", f))
    k = cond[0]
    if k == "bnot":
        return X.bnot(flagify(cond[1], snap))
    if k in ("band", "bor"):
        a, b = flagify(cond[1], snap), flagify(cond[2], snap)
        return X.band(a, b) if k == "band" else X.bor(a, b)
    if k == "bite":
        return X.bite(flagify(cond[1], snap), flagify(cond[2], snap), flagify(cond[3], snap))
    return cond


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
    if k == "jumptable":
        return f"{a:08x}  jumptable {X.fmt(e[1])} -> {', '.join(f'L_{t:x}' for t in e[2])}"
    if k == "callreg":
        return f"{a:08x}  callreg {X.fmt(e[1])}"
    if k == "mips":
        return f"{a:08x}  ; mips {e[1].text()}"
    return repr(e)
