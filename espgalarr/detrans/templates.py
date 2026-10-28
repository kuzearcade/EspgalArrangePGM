"""Recognise 68000 instructions in the effect stream produced by symexec.

A 68k instruction is a *primary* effect (a register/memory store or a control transfer)
plus the flag assignments it owns, or a flags-only group (tst/cmp/btst...).  Ownership of
a flag assignment is decided semantically rather than by position, because the compiler
that produced the overlay interleaves neighbouring instructions:
  1. the flag expression contains the instruction's result expression (Z=eq0(res) ...),
  2. it references an operand leaf of an instruction whose flag depends on operands
     (C/X of shifts, C/V/X of add/sub),
  3. constant assignments go to the nearest instruction that expects the flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import expr as X
from .expr import C, FLAGS

SIZE_CH = {1: "b", 2: "w", 4: "l"}
SIGN_BIT = {1: 7, 2: 15, 4: 31}
MASK = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF}


@dataclass
class M68:
    """One recovered 68k instruction."""
    mnem: str                       # 'move', 'and', 'bne', ... ('' for comment-only)
    size: str = ""                  # 'b' 'w' 'l' or ''
    ops: list[str] = field(default_factory=list)
    conf: float = 1.0
    addr: int = 0                   # MIPS address of the primary effect
    note: str = ""
    flags_expected: str = ""        # flags the instruction sets
    flags_got: dict = field(default_factory=dict)
    operand_leaves: set = field(default_factory=set)
    result: tuple | None = None
    is_label: bool = False
    raw: tuple | None = None        # the effect (for diagnostics)

    def text(self) -> str:
        if self.is_label:
            return f"L_{self.addr:x}:"
        if not self.mnem:
            return f"\t; ??? {self.note}"
        m = self.mnem + ("." + self.size if self.size else "")
        return f"\t{m}\t{','.join(self.ops)}" if self.ops else f"\t{m}"


class AddrMap:
    """Maps PS2 constants back to 68k addresses; the default is the identity."""

    def map_const(self, v: int) -> tuple[int | None, str, float]:
        """-> (68k address or None, kind, confidence)."""
        return None, "", 0.0

    def code_label(self, ps2: int) -> str:
        return f"F_{ps2:x}"


# ---------------------------------------------------------------- operand text

def leaves(e, acc=None) -> set:
    """All r68 leaves (name, version) and mem addresses referenced."""
    if acc is None:
        acc = set()
    if not isinstance(e, tuple):
        return acc
    if e[0] == "r68":
        acc.add(e)
        return acc
    for x in e[1:]:
        if isinstance(x, tuple):
            leaves(x, acc)
    return acc


def contains(e, sub) -> bool:
    if e == sub:
        return True
    if not isinstance(e, tuple):
        return False
    return any(isinstance(x, tuple) and contains(x, sub) for x in e[1:])


def _reg(e):
    e = X.strip(e)
    return e[1] if e[0] == "r68" else None


def _index_text(e, amap: AddrMap) -> str | None:
    """Index register part of an EA: dN.w / dN.l / aN.l."""
    s = X.strip(e)
    if e[0] == "sext16" and s[0] == "r68":
        return f"{s[1]}.w"
    if e[0] == "r68":
        return f"{e[1]}.l"
    return None


def _addr_const(v: int, amap: AddrMap) -> tuple[str, float]:
    m, kind, conf = amap.map_const(v)
    if m is not None:
        if m < 0x8000 or m >= 0xFFFF8000:
            return f"${m & 0xFFFFFF:x}.w", conf
        return f"${m:x}.l", conf
    return f"${v:x}.l", 0.3


def ea_text(addr, size: int, amap: AddrMap) -> tuple[str, float]:
    """Effective-address text for a memory operand with address expression `addr`."""
    a = addr
    if a[0] == "c":
        return _addr_const(a[1], amap)
    if a[0] == "r68" and a[1][0] == "a":
        return f"({a[1]})", 1.0
    if a[0] == "add":
        b, c = a[1], a[2]
        if c[0] == "c" and b[0] == "r68" and b[1][0] == "a":
            d = X.signed(c[1])
            return f"{d}({b[1]})", 1.0
        if c[0] == "c" and b[0] == "add":
            # d8(aN,xN)
            b1, b2 = b[1], b[2]
            if b1[0] == "r68" and b1[1][0] == "a":
                ix = _index_text(b2, amap)
                if ix:
                    return f"{X.signed(c[1])}({b1[1]},{ix})", 1.0
            if b2[0] == "r68" and b2[1][0] == "a":
                ix = _index_text(b1, amap)
                if ix:
                    return f"{X.signed(c[1])}({b2[1]},{ix})", 1.0
        if b[0] == "r68" and b[1][0] == "a":
            ix = _index_text(c, amap)
            if ix:
                return f"({b[1]},{ix})", 1.0
        if c[0] == "r68" and c[1][0] == "a":
            ix = _index_text(b, amap)
            if ix:
                return f"({c[1]},{ix})", 1.0
        if c[0] == "c":
            # absolute + index: lea tbl,aN implied?  emit as absolute(index) pseudo-EA
            ix = _index_text(b, amap)
            t, cf = _addr_const(c[1], amap)
            if ix:
                return f"{t[:-2]}({ix})", min(cf, 0.5)
    if a[0] == "r68" and a[1][0] == "d":
        return f"({a[1]})", 0.4
    return f"[{X.fmt(a)}]", 0.0


def value_text(e, size: int, amap: AddrMap, dest_is_areg: bool = False) -> tuple[str, float, str]:
    """Operand text for a source value; -> (text, confidence, kind) kind in
    'dn','an','imm','mem','?'."""
    s = X.strip(e)
    if s[0] == "r68":
        return s[1], 1.0, "dn" if s[1][0] == "d" else "an"
    if s[0] == "c":
        v = s[1]
        if size == 4 or dest_is_areg:
            m, kind, conf = amap.map_const(v)
            if m is not None:
                return f"#${m:x}", conf, "imm"
            vv = v if v < 0x80000000 else v - 0x100000000
            return (f"#{vv}" if -256 < vv < 256 else f"#${v:x}"), 1.0, "imm"
        v &= MASK[size]
        if v >= (MASK[size] >> 1) + 1:
            return f"#-${(MASK[size] + 1 - v):x}" if MASK[size] + 1 - v > 9 else f"#-{MASK[size] + 1 - v}", 1.0, "imm"
        return (f"#{v}" if v < 10 else f"#${v:x}"), 1.0, "imm"
    if s[0] == "mem":
        t, cf = ea_text(s[1], s[2], amap)
        return t, cf, "mem"
    return X.fmt(e), 0.0, "?"


# ---------------------------------------------------------------- flag matching

def flag_val_matches(f: str, b, ins: M68) -> int:
    """0: not about this instruction, 1: constant, 3: references the result, 2: references an
    operand (only meaningful for the operand-derived C/V/X flags)."""
    if b[0] in ("true", "false"):
        return 1
    res = ins.result
    if res is not None:
        rs = X.strip(res)
        if rs[0] == "r68":
            # bare register copy: only a direct test of that value is about this move
            if len(b) >= 2 and isinstance(b[1], tuple) and X.strip(b[1]) == rs and b[0] in ("eq0", "ne0", "bit", "lt0", "ge0"):
                return 3
        elif contains(b, res) or contains(b, rs):
            return 3
        elif rs[0] == "and" and rs[2][0] == "c" and _bitnum(rs[2][1], 4) is not None:
            # eq0(and(x, 1<<k)) is simplified to !bit_k(x)
            k = _bitnum(rs[2][1], 4)
            inner = b[1] if b[0] == "bnot" else b
            if inner[0] == "bit" and inner[2] == k and X.strip(inner[1]) == X.strip(rs[1]):
                return 3
    if f not in "CVX":
        return 0
    lv = leaves(b)
    if lv & ins.operand_leaves:
        return 2
    # memory operand reference
    if ins.raw is not None and ins.raw[0] == "store":
        for l in _mem_leaves(b):
            if contains(ins.raw[2], l) or contains(ins.raw[1], l):
                return 3
    return 0


def _mem_leaves(e, acc=None):
    if acc is None:
        acc = []
    if not isinstance(e, tuple):
        return acc
    if e[0] == "mem":
        acc.append(e)
        return acc
    for x in e[1:]:
        if isinstance(x, tuple):
            _mem_leaves(x, acc)
    return acc


# ---------------------------------------------------------------- primaries

def _imm_mnem(base: str, v: int, size: int) -> tuple[str, str]:
    """add/sub immediates: addq for 1..8, else addi.  Returns (mnem, imm text)."""
    m = MASK[size]
    v &= m
    neg = (m + 1 - v) & m
    if 1 <= v <= 8:
        return base + "q", f"#{v}"
    if base == "add" and 1 <= neg <= 8:
        return "subq", f"#{neg}"
    if base == "sub" and 1 <= neg <= 8:
        return "addq", f"#{neg}"
    return base + "i", f"#${v:x}"


def recognise_store(eff, amap: AddrMap) -> M68:
    _, dest, val, addr = eff
    size = dest[2] if dest[0] in ("slot", "mem") else 0
    sz = SIZE_CH.get(size, "?")
    ins = M68("", sz, [], 0.0, addr, raw=eff, result=val)
    ins.operand_leaves = leaves(val)
    v = X.strip(val)
    if dest[0] == "slot":
        name = dest[1]
        if name[0] == "a":
            return _recognise_areg_store(ins, name, size, val, amap)
        return _recognise_dreg_store(ins, name, size, val, amap)
    if dest[0] == "mem":
        return _recognise_mem_store(ins, dest, val, amap)
    ins.note = f"store {dest} = {X.fmt(val)}"
    return ins


def _bitnum(c: int, size: int) -> int | None:
    m = MASK[size]
    c &= m
    if c and (c & (c - 1)) == 0:
        return c.bit_length() - 1
    return None


def _recognise_dreg_store(ins: M68, name: str, size: int, val, amap: AddrMap) -> M68:
    v = X.strip(val)
    k = v[0]
    me = ("r68", name)

    def is_me(e):
        e = X.strip(e)
        return e[0] == "r68" and e[1] == name

    if k == "r68" or k == "c" or k == "mem":
        t, cf, kind = value_text(v, size, amap)
        if k == "c" and size == 4 and -128 <= X.signed(v[1]) <= 127:
            ins.mnem, ins.ops = "moveq", [t, name]
            ins.size = ""
        elif k == "r68" and v[1] == name:
            # same register: a size change -> ext?  (handled by sext forms); treat as tst-like move
            ins.mnem, ins.ops = "move", [t, name]
        else:
            ins.mnem, ins.ops = "move", [t, name]
        ins.conf, ins.flags_expected = cf, "NZVC"
        return ins
    if k in ("and", "or", "xor", "add", "sub") and len(v) == 3:
        a, b = v[1], v[2]
        mn = {"and": "and", "or": "or", "xor": "eor", "add": "add", "sub": "sub"}[k]
        if is_me(a):
            other = b
        elif is_me(b) and k != "sub":
            other = a
        else:
            other = None
        if other is not None:
            os_ = X.strip(other)
            if os_[0] == "c":
                cv = os_[1]
                if k in ("add", "sub"):
                    mn2, it = _imm_mnem(mn, cv if k == "add" else -cv, size)
                    ins.mnem, ins.ops = mn2, [it, name]
                    ins.flags_expected = "XNZVC"
                else:
                    bit = _bitnum(cv, size) if k in ("or", "xor") else _bitnum(~cv, size) if k == "and" else None
                    t, cf, _ = value_text(os_, size, amap)
                    ins.mnem, ins.ops = mn + "i", [t, name]
                    ins.flags_expected = "NZVC"
                    if bit is not None:
                        ins.note = f"bit {bit}"   # may be bset/bclr/bchg: decided by flags later
                        ins.alt_bit = (bit, {"or": "bset", "and": "bclr", "xor": "bchg"}[k])
                ins.conf = 1.0
                return ins
            t, cf, kind = value_text(other, size, amap)
            if kind in ("dn", "mem", "an"):
                ins.mnem, ins.ops, ins.conf = mn, [t, name], cf
                ins.flags_expected = "XNZVC" if k in ("add", "sub") else "NZVC"
                return ins
    if k == "not" and is_me(v[1]):
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "not", [name], 1.0, "NZVC"
        return ins
    if k == "neg" and is_me(v[1]):
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "neg", [name], 1.0, "XNZVC"
        return ins
    if k == "sub" and X.strip(v[1]) == ("c", 0) and is_me(v[2]):
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "neg", [name], 1.0, "XNZVC"
        return ins
    if k == "sext8" and is_me(v[1]) and size == 2:
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "ext", [name], 1.0, "NZVC"
        return ins
    if k == "sext16" and is_me(v[1]) and size == 4:
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "ext", [name], 1.0, "NZVC"
        return ins
    if k in ("sext8", "sext16", "lo8", "lo16"):
        # move with sign extension of a smaller source (move.w then ext) - keep as move
        t, cf, kind = value_text(v[1], size, amap)
        if kind != "?":
            ins.mnem, ins.ops, ins.conf, ins.flags_expected = "move", [t, name], min(cf, 0.6), "NZVC"
            ins.note = f"sign/zero extended source {k}"
            return ins
    if k in ("shl", "shr", "sar") and len(v) == 3:
        src, cnt = v[1], v[2]
        if is_me(src):
            mn = {"shl": "lsl", "shr": "lsr", "sar": "asr"}[k]
            cs = X.strip(cnt)
            if cs[0] == "c" and 1 <= cs[1] <= 8:
                ins.mnem, ins.ops, ins.conf = mn, [f"#{cs[1]}", name], 1.0
                ins.flags_expected = "XNZVC"
                ins.shift_count = cs[1]
                return ins
            if cs[0] == "and" and X.strip(cs[1])[0] == "r68":
                ins.mnem, ins.ops, ins.conf = mn, [X.strip(cs[1])[1], name], 0.9
                ins.flags_expected = "XNZVC"
                return ins
            if cs[0] == "r68":
                ins.mnem, ins.ops, ins.conf = mn, [cs[1], name], 0.9
                ins.flags_expected = "XNZVC"
                return ins
    if k == "or" and len(v) == 3:
        r = _rotate(v, name, size)
        if r is not None:
            mn, cnt = r
            ins.mnem, ins.ops, ins.conf, ins.flags_expected = mn, [f"#{cnt}", name], 1.0, "NZVC"
            return ins
        sw = _swap(v, name)
        if sw:
            ins.mnem, ins.ops, ins.conf, ins.flags_expected = "swap", [name], 1.0, "NZVC"
            return ins
    if k in ("mul", "mulu"):
        a, b = v[1], v[2]
        mn = "muls" if k == "mul" else "mulu"
        if is_me(a) or is_me(b):
            other = b if is_me(a) else a
            t, cf, kind = value_text(other, 2, amap)
            ins.mnem, ins.size, ins.ops, ins.conf, ins.flags_expected = mn, "w", [t, name], cf, "NZVC"
            return ins
    if k in ("divu", "div", "divrem", "divremu"):
        pass
    if k == "ite":
        # scc: dN.b = cond ? 0xff : 0
        cnd, a, b = v[1], X.strip(v[2]), X.strip(v[3])
        if a[0] == "c" and b[0] == "c" and {a[1] & 0xFF, b[1] & 0xFF} == {0xFF, 0}:
            cc = cond_code(cnd if a[1] & 0xFF == 0xFF else X.bnot(cnd))
            if cc:
                ins.mnem, ins.size, ins.ops, ins.conf = "s" + cc, "", [name], 0.9
                return ins
    ins.note = f"{name}.{SIZE_CH.get(size, '?')} = {X.fmt(val)}"
    return ins


def _rotate(v, name: str, size: int):
    """or(shr(x,k), shl(and(x,mask),bits-k)) -> ror #k; or(shl(x,k), shr(x,bits-k)) -> rol #k."""
    bits = size * 8
    a, b = v[1], v[2]
    for p, q in ((a, b), (b, a)):
        ps, qs = X.strip(p), X.strip(q)
        if ps[0] == "shr" and qs[0] == "shl" and ps[2][0] == "c" and qs[2][0] == "c":
            k1, k2 = ps[2][1], qs[2][1]
            src1 = X.strip(ps[1])
            src2 = X.strip(qs[1])
            if src2[0] == "and":
                src2 = X.strip(src2[1])
            if src1 == src2 and src1[0] == "r68" and src1[1] == name and k1 + k2 == bits:
                if k1 <= 8 and k2 >= bits - 8:
                    return "ror", k1
                if k2 <= 8:
                    return "rol", k2
    return None


def _swap(v, name: str) -> bool:
    a, b = X.strip(v[1]), X.strip(v[2])
    for p, q in ((a, b), (b, a)):
        if p[0] == "shr" and q[0] == "shl" and p[2] == C(16) and q[2] == C(16):
            if _reg(p[1]) == name and _reg(q[1]) == name:
                return True
    return False


def _recognise_areg_store(ins: M68, name: str, size: int, val, amap: AddrMap) -> M68:
    v = X.strip(val)
    k = v[0]
    if size == 2:
        # the translator writes only the low word of the slot for movea.w (sic)
        t, cf, kind = value_text(v, 2, amap)
        if kind != "?":
            ins.mnem, ins.size, ins.ops, ins.conf = "movea", "w", [t, name], cf
            return ins
    if size != 4:
        ins.note = f"{name}.{SIZE_CH.get(size)} = {X.fmt(val)}"
        return ins
    if k == "c":
        t, cf = _addr_const(v[1], amap)
        ins.mnem, ins.ops, ins.conf = "lea", [t, name], cf
        return ins
    if k == "r68":
        ins.mnem, ins.ops, ins.conf = "movea", [v[1], name], 1.0
        return ins
    if k == "sext16":
        t, cf, kind = value_text(v[1], 2, amap)
        if kind != "?":
            ins.mnem, ins.size, ins.ops, ins.conf = "movea", "w", [t, name], cf
            return ins
    if k == "mem":
        t, cf = ea_text(v[1], v[2], amap)
        if v[2] == 4:
            ins.mnem, ins.ops, ins.conf = "movea", [t, name], cf
        else:
            ins.mnem, ins.size, ins.ops, ins.conf = "movea", "w", [t, name], cf
        return ins
    if k == "add" and len(v) == 3:
        a, b = v[1], v[2]
        if _reg(a) == name or _reg(b) == name:
            other = b if _reg(a) == name else a
            os_ = X.strip(other)
            if os_[0] == "c":
                d = X.signed(os_[1])
                if 1 <= d <= 8:
                    ins.mnem, ins.ops = "addq", [f"#{d}", name]
                elif -8 <= d <= -1:
                    ins.mnem, ins.ops = "subq", [f"#{-d}", name]
                else:
                    ins.mnem, ins.ops = "lea", [f"{d}({name})", name]
                ins.conf = 1.0
                return ins
            t, cf, kind = value_text(other, 4, amap)
            if other[0] == "sext16":
                t, cf, kind = value_text(other[1], 2, amap)
                ins.mnem, ins.size, ins.ops, ins.conf = "adda", "w", [t, name], cf
                return ins
            if kind != "?":
                ins.mnem, ins.ops, ins.conf = "adda", [t, name], cf
                return ins
        # lea d(aM,xN) / lea d(aM)
        t, cf = ea_text(v, 4, amap)
        if cf > 0:
            ins.mnem, ins.ops, ins.conf = "lea", [t, name], cf
            return ins
    if k == "sub" and len(v) == 3 and _reg(v[1]) == name:
        other = v[2]
        if other[0] == "sext16":
            t, cf, kind = value_text(other[1], 2, amap)
            ins.mnem, ins.size, ins.ops, ins.conf = "suba", "w", [t, name], cf
            return ins
        t, cf, kind = value_text(other, 4, amap)
        if kind != "?":
            ins.mnem, ins.ops, ins.conf = "suba", [t, name], cf
            return ins
    ins.note = f"{name} = {X.fmt(val)}"
    return ins


def _recognise_mem_store(ins: M68, dest, val, amap: AddrMap) -> M68:
    _, addr, size = dest
    if size not in MASK:
        ins.note = f"{size}-byte store {X.fmt(addr)} = {X.fmt(val)}"
        return ins
    et, ecf = ea_text(addr, size, amap)
    v = X.strip(val)
    k = v[0]
    same = ("mem", addr, size, False)

    def is_same_mem(e):
        e = X.strip(e)
        return e[0] == "mem" and e[1] == addr and e[2] == size

    if k == "c" and (v[1] & MASK[size]) == 0:
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "clr", [et], ecf, "NZVC"
        ins.alt = ("move", ["#0", et])
        return ins
    if k in ("r68", "c", "mem"):
        t, cf, kind = value_text(v, size, amap)
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "move", [t, et], min(cf, ecf), "NZVC"
        return ins
    if k in ("and", "or", "xor", "add", "sub") and len(v) == 3:
        a, b = v[1], v[2]
        mn = {"and": "and", "or": "or", "xor": "eor", "add": "add", "sub": "sub"}[k]
        other = b if is_same_mem(a) else a if (is_same_mem(b) and k != "sub") else None
        if other is not None:
            os_ = X.strip(other)
            if os_[0] == "c":
                cv = os_[1]
                if k in ("add", "sub"):
                    mn2, it = _imm_mnem(mn, cv if k == "add" else -cv, size)
                    ins.mnem, ins.ops, ins.flags_expected = mn2, [it, et], "XNZVC"
                else:
                    t, cf, _ = value_text(os_, size, amap)
                    ins.mnem, ins.ops, ins.flags_expected = mn + "i", [t, et], "NZVC"
                    bit = _bitnum(cv, size) if k in ("or", "xor") else _bitnum(~cv, size) if k == "and" else None
                    if bit is not None:
                        ins.alt_bit = (bit, {"or": "bset", "and": "bclr", "xor": "bchg"}[k])
                ins.conf = ecf
                return ins
            t, cf, kind = value_text(other, size, amap)
            if kind == "dn":
                ins.mnem, ins.ops, ins.conf = mn, [t, et], min(cf, ecf)
                ins.flags_expected = "XNZVC" if k in ("add", "sub") else "NZVC"
                return ins
    if k == "not" and is_same_mem(v[1]):
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "not", [et], ecf, "NZVC"
        return ins
    if k == "neg" and is_same_mem(v[1]):
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = "neg", [et], ecf, "XNZVC"
        return ins
    if k in ("shl", "shr", "sar") and is_same_mem(v[1]) and X.strip(v[2]) == C(1):
        ins.mnem, ins.ops, ins.conf, ins.flags_expected = {"shl": "lsl", "shr": "lsr", "sar": "asr"}[k], [et], ecf, "XNZVC"
        return ins
    if k == "ite":
        cnd, a, b = v[1], X.strip(v[2]), X.strip(v[3])
        if a[0] == "c" and b[0] == "c" and {a[1] & 0xFF, b[1] & 0xFF} == {0xFF, 0} and size == 1:
            cc = cond_code(cnd if a[1] & 0xFF == 0xFF else X.bnot(cnd))
            if cc:
                ins.mnem, ins.size, ins.ops, ins.conf = "s" + cc, "", [et], 0.9 * ecf
                return ins
    ins.note = f"[{et}].{SIZE_CH.get(size, '?')} = {X.fmt(val)}"
    return ins


# ---------------------------------------------------------------- conditions

def _flag(b):
    return b[1] if b[0] == "flag" else None


def cond_code(b) -> str | None:
    """68k condition code for a boolean expression over CCR flags."""
    k = b[0]
    if k == "flag":
        return {"Z": "eq", "N": "mi", "C": "cs", "V": "vs"}.get(b[1])
    if k == "bnot" and b[1][0] == "flag":
        return {"Z": "ne", "N": "pl", "C": "cc", "V": "vc"}.get(b[1][1])
    if k == "bor":
        fs = {_flag(b[1]), _flag(b[2])}
        if fs == {"C", "Z"}:
            return "ls"
        # Z || (N ^ V)
        parts = [b[1], b[2]]
        for p, q in (parts, parts[::-1]):
            if _flag(p) == "Z" and _nxorv(q) == 1:
                return "le"
    if k == "bnot" and b[1][0] == "bor":
        inner = b[1]
        fs = {_flag(inner[1]), _flag(inner[2])}
        if fs == {"C", "Z"}:
            return "hi"
        parts = [inner[1], inner[2]]
        for p, q in (parts, parts[::-1]):
            if _flag(p) == "Z" and _nxorv(q) == 1:
                return "gt"
    if k == "band":
        parts = [b[1], b[2]]
        for p, q in (parts, parts[::-1]):
            if p[0] == "bnot" and _flag(p[1]) == "Z" and _nxorv(q) == -1:
                return "gt"
            if p[0] == "bnot" and _flag(p[1]) == "Z" and q[0] == "bnot" and _flag(q[1]) == "C":
                return "hi"
    x = _nxorv(b)
    if x == 1:
        return "lt"
    if x == -1:
        return "ge"
    if k == "true":
        return "t"
    if k == "false":
        return "f"
    return None


def _nxorv(b) -> int:
    """1 if b == N xor V, -1 if b == not(N xor V), else 0."""
    k = b[0]
    if k == "ne" and len(b) == 3:
        f1, f2 = _flagtest_flag(b[1]), _flagtest_flag(b[2])
        if {f1, f2} == {"N", "V"}:
            return 1
    if k == "eq" and len(b) == 3:
        f1, f2 = _flagtest_flag(b[1]), _flagtest_flag(b[2])
        if {f1, f2} == {"N", "V"}:
            return -1
    if k == "bnot":
        return -_nxorv(b[1])
    if k == "ne0" and b[1][0] == "xor":
        f1, f2 = _flagtest_flag(b[1][1]), _flagtest_flag(b[1][2])
        if {f1, f2} == {"N", "V"}:
            return 1
    if k == "eq0" and b[1][0] == "xor":
        f1, f2 = _flagtest_flag(b[1][1]), _flagtest_flag(b[1][2])
        if {f1, f2} == {"N", "V"}:
            return -1
    if k in ("bor", "band"):
        # (N && !V) || (!N && V)  == N ^ V ;  (N && V) || (!N && !V) == !(N^V)
        p, q = b[1], b[2]
        if k == "bor" and p[0] == "band" and q[0] == "band":
            s1 = _flagpair(p)
            s2 = _flagpair(q)
            if s1 and s2:
                if s1 in ({("N", True), ("V", False)}, {("N", False), ("V", True)}) and s2 in ({("N", True), ("V", False)}, {("N", False), ("V", True)}) and s1 != s2:
                    return 1
                if s1 in ({("N", True), ("V", True)}, {("N", False), ("V", False)}) and s2 in ({("N", True), ("V", True)}, {("N", False), ("V", False)}) and s1 != s2:
                    return -1
    if k == "bite":
        # N ? !V : V  == N ^ V
        c, a, d = b[1], b[2], b[3]
        if _flag(c) == "N":
            if a[0] == "bnot" and _flag(a[1]) == "V" and _flag(d) == "V":
                return 1
            if _flag(a) == "V" and d[0] == "bnot" and _flag(d[1]) == "V":
                return -1
        if _flag(c) == "V":
            if a[0] == "bnot" and _flag(a[1]) == "N" and _flag(d) == "N":
                return 1
            if _flag(a) == "N" and d[0] == "bnot" and _flag(d[1]) == "N":
                return -1
    return 0


def _flagpair(b):
    out = set()
    for x in (b[1], b[2]):
        if x[0] == "flag":
            out.add((x[1], True))
        elif x[0] == "bnot" and x[1][0] == "flag":
            out.add((x[1][1], False))
        else:
            return None
    return out


def _flagtest_flag(e):
    """flag name if e is CCR&bit (value form) or ('flag', f)."""
    if e[0] == "flagtest":
        m = e[2]
        return X.BIT_FLAG.get(m)
    if e[0] == "flag":
        return e[1]
    if e[0] == "bool" and e[1][0] == "flag":
        return e[1][1]
    return None


# ---------------------------------------------------------------- flags-only

def recognise_flags_only(items: list, amap: AddrMap) -> list[M68]:
    """items: list of (flag, boolexpr, addr).  Returns recognised instructions."""
    d = {}
    for f, b, a in items:
        d.setdefault(f, []).append((b, a))
    out = []
    addr = items[0][2]
    z = d.get("Z", [(None, 0)])[-1][0]
    n = d.get("N", [(None, 0)])[-1][0]
    v = d.get("V", [(None, 0)])[-1][0]
    c = d.get("C", [(None, 0)])[-1][0]
    x = d.get("X", [(None, 0)])[-1][0]
    # btst: only Z assigned, Z = !bitK(e)  or eq0(and(e, mask))
    if z is not None and n is None and v is None and c is None:
        if z[0] == "bnot" and z[1][0] == "bit":
            e, k = z[1][1], z[1][2]
            t, cf, kind = value_text(e, 1 if X.strip(e)[0] == "mem" else 4, amap)
            if kind != "?":
                return [M68("btst", "", [f"#{k}", t], cf, addr)]
        if z[0] == "eq0" and z[1][0] == "and":
            e, m = z[1][1], z[1][2]
            ms = X.strip(m)
            if ms[0] == "shl" and X.strip(ms[1]) == C(1):
                cnt = X.strip(ms[2])
                creg = _reg(cnt[1]) if cnt[0] == "and" else _reg(cnt)
                t, cf, kind = value_text(e, 4, amap)
                if creg and kind != "?":
                    return [M68("btst", "", [creg, t], cf * 0.95, addr)]
            k = _bitnum(ms[1], 4) if ms[0] == "c" else None
            if k is not None:
                t, cf, kind = value_text(e, 1 if X.strip(e)[0] == "mem" else 4, amap)
                if kind != "?":
                    return [M68("btst", "", [f"#{k}", t], cf, addr)]
        if z[0] == "eq0":
            e = z[1]
            # btst dN,dM: eq0(shr(dM, dN&31) & 1)?  fall through to tst
    # tst: Z=eq0(e), N=bit(e), V=0, C=0
    if z is not None and z[0] == "eq0" and v == X.FALSE and c == X.FALSE:
        e = z[1]
        size = _size_of(e, n)
        t, cf, kind = value_text(e, size, amap)
        if kind in ("dn", "mem", "an"):
            return [M68("tst", SIZE_CH[size], [t], cf if n is not None else cf * 0.8, addr)]
    # cmp: Z=eq0(sub(a,b)) / eq(a,b), C=borrow, V=overflow
    cmp = _recognise_cmp(z, n, v, c, amap, addr)
    if cmp is not None:
        return [cmp]
    if z is not None and z[0] in ("eq", "ne"):
        pass
    txt = ", ".join(f"{f}={X.fmtb(b)}" for f, b, _ in items)
    return [M68("", "", [], 0.0, addr, note=f"flags {txt}")]


def _size_of(e, n) -> int:
    """Operand size from the N-flag bit number or from the expression's width."""
    if n is not None and n[0] == "bit":
        return {7: 1, 15: 2, 31: 4}.get(n[2], 2)
    s = e
    if s[0] == "lo8" or (s[0] == "mem" and s[2] == 1) or s[0] == "sext8":
        return 1
    if s[0] == "lo16" or (s[0] == "mem" and s[2] == 2) or s[0] == "sext16":
        return 2
    if s[0] == "and" and s[2][0] == "c":
        return 1 if s[2][1] == 0xFF else 2 if s[2][1] == 0xFFFF else 4
    return 4


def _recognise_cmp(z, n, v, c, amap: AddrMap, addr: int) -> M68 | None:
    if z is None:
        return None
    a = b = None
    zs = X.strip(z[1]) if z[0] in ("eq0", "ne0") else None
    if zs is not None and zs[0] == "sub":
        a, b = zs[1], zs[2]
    elif zs is not None and zs[0] == "add" and zs[2][0] == "c":
        a, b = zs[1], C(-zs[2][1])
    elif z[0] == "eq" and len(z) == 3:
        a, b = z[1], z[2]
    elif zs is not None and (v is not None and v[0] not in ("true", "false") or c is not None and c[0] not in ("true", "false")):
        a, b = zs, C(0)         # cmp #0 (the subtraction folded away)
    else:
        return None
    # 68k: cmp src,dst computes dst - src: a = dst, b = src
    size = 4
    if n is not None and n[0] == "bit":
        size = {7: 1, 15: 2, 31: 4}.get(n[2], 4)
    else:
        size = max(_size_of(a, None), _size_of(b, None)) if a[0] != "c" else _size_of(b, None)
    da = X.strip(a)
    db = X.strip(b)
    ta, cfa, ka = value_text(da, size, amap)
    tb, cfb, kb = value_text(db, size, amap)
    conf = min(cfa, cfb)
    if c is None and v is None:
        conf *= 0.7
    if ka == "an" or (da[0] == "r68" and da[1][0] == "a"):
        mn = "cmpa"
        if a[0] == "sext16" or b[0] == "sext16":
            size = 2
        return M68(mn, SIZE_CH[size], [tb, ta], conf, addr)
    if ka == "dn":
        mn = "cmpi" if kb == "imm" else "cmp"
        return M68(mn, SIZE_CH[size], [tb, ta], conf, addr)
    if ka == "mem":
        if kb == "imm":
            return M68("cmpi", SIZE_CH[size], [tb, ta], conf, addr)
        if kb == "mem":
            return M68("cmpm", SIZE_CH[size], [tb, ta], conf, addr)
    if ka == "imm" and kb in ("dn", "mem"):
        return M68("cmpi", SIZE_CH[size], [ta, tb], conf * 0.8, addr, note="operands swapped")
    return None


# ---------------------------------------------------------------- forwarded conditions

def _cmp_operands(snap):
    """(dst, src) of the last compare/subtract recorded in the CCR snapshot's Z."""
    z = dict(snap).get("Z")
    if z is None or z[0] not in ("eq0", "ne0"):
        return None
    e = X.strip(z[1])
    if e[0] == "sub":
        return e[1], e[2]
    if e[0] == "add" and e[2][0] == "c":
        return e[1], C(-e[2][1])
    return e, C(0)


def _same(a, b) -> bool:
    return X.strip(a) == X.strip(b) or a == b


def semantic_cc(cond, snap) -> str | None:
    """Condition code for a branch the compiler expressed on the compared values instead
    of the CCR (`sltu`/`slt`/`eq` on the operands of the preceding cmp/sub/tst)."""
    ops = _cmp_operands(snap)
    if ops is None:
        return None
    a, b = ops
    neg = False
    if cond[0] == "bnot":
        neg, cond = True, cond[1]
    k = cond[0]
    cc = None
    if k in ("ltu", "lt") and len(cond) == 3:
        x, y = cond[1], cond[2]
        if _same(x, a) and _same(y, b):
            cc = "cs" if k == "ltu" else "lt"
        elif _same(x, b) and _same(y, a):
            cc = "hi" if k == "ltu" else "gt"
    elif k in ("ne", "eq") and len(cond) == 3:
        x, y = cond[1], cond[2]
        if (_same(x, a) and _same(y, b)) or (_same(x, b) and _same(y, a)):
            cc = "ne" if k == "ne" else "eq"
    elif k in ("ne0", "eq0"):
        e = X.strip(cond[1])
        if (e[0] == "sub" and _same(e[1], a) and _same(e[2], b)) or (b == C(0) and _same(e, a)):
            cc = "ne" if k == "ne0" else "eq"
    elif k in ("lt0", "ge0"):
        e = X.strip(cond[1])
        if (e[0] == "sub" and _same(e[1], a) and _same(e[2], b)) or (b == C(0) and _same(e, a)):
            cc = "mi" if k == "lt0" else "pl"
    elif k in ("le0", "gt0"):
        e = X.strip(cond[1])
        if b == C(0) and _same(e, a):
            cc = "le" if k == "le0" else "gt"
    if cc is None:
        return None
    if neg:
        cc = {"cs": "cc", "cc": "cs", "lt": "ge", "ge": "lt", "hi": "ls", "ls": "hi", "gt": "le", "le": "gt",
              "ne": "eq", "eq": "ne", "mi": "pl", "pl": "mi"}[cc]
    return cc
