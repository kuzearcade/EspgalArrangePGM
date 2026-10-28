"""Symbolic 32-bit expressions used by the de-translator.

Expressions are nested tuples; the first element is the node kind:
    ('c', v)                  constant (unsigned 32-bit)
    ('r68', name)             68k register value at block entry ('d0'..'d7','a0'..'a7')
    ('in', mipsreg)           MIPS register value at block entry (unknown)
    ('mem', addr, size, sgn)  memory load (68k big-endian view), sgn: sign-extended
    ('ccr', snap)             CCR value: snap = ((flag, boolexpr), ...) for X N Z V C
    ('flagtest', snap, mask)  CCR & mask (non-zero iff one of the masked flags is set)
    ('and'|'or'|'xor'|'add'|'sub'|'mul'|'mulu'|'div'|'divu'|'mod'|'modu'|'shl'|'shr'|'sar'|
     'slt'|'sltu', a, b)      binary ops;  ('not', a); ('neg', a)
    ('sext8', a) ('sext16', a) ('lo8', a) ('lo16', a)    size casts
    ('ite', cond, a, b)       cond is a boolean expression
    ('eq0', a) ('ne0', a) ('bit', a, n) ('lt0', a) ('ge0', a) ('le0', a) ('gt0', a)
    ('true',) ('false',) ('bnot', b) ('band', b, c) ('bor', b, c)   boolean algebra
"""
from __future__ import annotations

M32 = 0xFFFFFFFF
FLAGS = ("X", "N", "Z", "V", "C")
FLAG_BIT = {"C": 0x100, "V": 0x200, "Z": 0x400, "N": 0x800, "X": 0x1000}
BIT_FLAG = {v: k for k, v in FLAG_BIT.items()}
TRUE = ("true",)
FALSE = ("false",)


def C(v: int):
    return ("c", v & M32)


def is_const(e) -> bool:
    return e[0] == "c"


def const(e) -> int | None:
    return e[1] if e[0] == "c" else None


def signed(v: int) -> int:
    v &= M32
    return v - 0x100000000 if v & 0x80000000 else v


def ccr_assigned(e) -> frozenset:
    """Flags explicitly assigned on the way from the CCR load to this value."""
    return e[2] if len(e) > 2 else frozenset()


def entry_snap():
    return tuple((f, ("flag", f)) for f in FLAGS)


def snap_get(snap, f):
    for k, v in snap:
        if k == f:
            return v
    return ("flag", f)


def snap_set(snap, **kw):
    return tuple((k, kw.get(k, v)) for k, v in snap)


# ------------------------------------------------------------------ boolean algebra

def bnot(b):
    k = b[0]
    if k == "true":
        return FALSE
    if k == "false":
        return TRUE
    if k == "bnot":
        return b[1]
    if k == "eq0":
        return ("ne0", b[1])
    if k == "ne0":
        return ("eq0", b[1])
    if k == "lt0":
        return ("ge0", b[1])
    if k == "ge0":
        return ("lt0", b[1])
    if k == "le0":
        return ("gt0", b[1])
    if k == "gt0":
        return ("le0", b[1])
    return ("bnot", b)


def band(a, b):
    if a == FALSE or b == FALSE:
        return FALSE
    if a == TRUE:
        return b
    if b == TRUE:
        return a
    if a == b:
        return a
    return ("band", a, b)


def bor(a, b):
    if a == TRUE or b == TRUE:
        return TRUE
    if a == FALSE:
        return b
    if b == FALSE:
        return a
    if a == b:
        return a
    return ("bor", a, b)


def bite(c, a, b):
    """Boolean if-then-else."""
    if a == b:
        return a
    if c == TRUE:
        return a
    if c == FALSE:
        return b
    if a == TRUE and b == FALSE:
        return c
    if a == FALSE and b == TRUE:
        return bnot(c)
    if a == TRUE:
        return bor(c, b)
    if b == FALSE:
        return band(c, a)
    if a == FALSE:
        return band(bnot(c), b)
    if b == TRUE:
        return bor(bnot(c), a)
    return ("bite", c, a, b)


def truth_names(e):
    """Like truth() but CCR tests are expressed through flag *names* (for 68k Bcc/Scc,
    whose condition must survive constant folding of the flag values)."""
    k = e[0]
    if k == "flagtest":
        r = FALSE
        for f in FLAGS:
            if e[2] & FLAG_BIT[f]:
                r = bor(r, ("flag", f))
        return r
    if k == "ite":
        return bite(e[1], truth_names(e[2]), truth_names(e[3]))
    if k == "and" and e[2][0] == "c" and e[1][0] == "flagtest":
        return truth_names(("flagtest", e[1][1], e[1][2] & e[2][1]))
    if k in ("xor", "sub") and e[1][0] == "flagtest" and e[2][0] == "flagtest":
        return ("ne", e[1], e[2])
    if k == "sltu" and e[2] == C(1):
        return bnot(truth_names(e[1]))
    if k == "bool":
        return e[1]
    return truth(e)


def truth(e):
    """Boolean view of a value expression (non-zero test)."""
    k = e[0]
    if k == "c":
        return TRUE if e[1] else FALSE
    if k == "flagtest":
        snap, mask = e[1], e[2]
        r = FALSE
        for f in FLAGS:
            if mask & FLAG_BIT[f]:
                r = bor(r, snap_get(snap, f))
        return r
    if k == "ite":
        return bite(e[1], truth(e[2]), truth(e[3]))
    if k == "and" and e[2][0] == "c":
        m = e[2][1]
        if m and (m & (m - 1)) == 0:        # single bit test
            n = m.bit_length() - 1
            return ("bit", e[1], n)
    if k == "sltu" and e[2] == C(1):
        return ("eq0", e[1])
    if k == "slt" and e[2] == C(0):
        return ("lt0", e[1])
    if k == "slt" and e[1] == C(0):
        return ("gt0", e[2])
    if k == "xor":
        return ("ne", e[1], e[2]) if not (is_const(e[2]) and e[2][1] == 0) else truth(e[1])
    if k == "sub":
        return ("ne", e[1], e[2])
    if k == "bool":
        return e[1]
    return ("ne0", e)


def bool_val(b):
    """Value (0/1) view of a boolean expression."""
    if b == TRUE:
        return C(1)
    if b == FALSE:
        return C(0)
    return ("bool", b)


# ------------------------------------------------------------------ value algebra

def _mask_of(e):
    """Known-zero-above mask: returns a bit mask M with value e & ~M == 0, or None."""
    k = e[0]
    if k == "c":
        return e[1]
    if k in ("lo8",):
        return 0xFF
    if k in ("lo16",):
        return 0xFFFF
    if k == "mem":
        return {1: 0xFF, 2: 0xFFFF, 4: M32}[e[2]] if not e[3] else M32
    if k == "and":
        a, b = _mask_of(e[1]), _mask_of(e[2])
        if a is None:
            return b
        if b is None:
            return a
        return a & b
    if k in ("or", "xor"):
        a, b = _mask_of(e[1]), _mask_of(e[2])
        if a is None or b is None:
            return None
        return a | b
    if k == "shl" and is_const(e[2]):
        a = _mask_of(e[1])
        return None if a is None else (a << e[2][1]) & M32
    if k == "shr" and is_const(e[2]):
        a = _mask_of(e[1])
        return (M32 if a is None else a) >> e[2][1]
    if k == "bool":
        return 1
    if k in ("sltu", "slt"):
        return 1
    if k == "ite":
        a, b = _mask_of(e[2]), _mask_of(e[3])
        if a is None or b is None:
            return None
        return a | b
    return None


def AND(a, b):
    if is_const(a) and not is_const(b):
        a, b = b, a
    if is_const(a) and is_const(b):
        return C(a[1] & b[1])
    if is_const(b):
        m = b[1]
        if m == 0:
            return C(0)
        km = _mask_of(a)
        if km is not None and km & ~m == 0:
            return a                      # mask is a no-op
        if a[0] == "ccr":
            snap = a[1]
            if (m & 0xE0FF) == 0xE0FF:            # keeps the non-flag bits: a flag clear
                kw = {f: FALSE for f in FLAGS if not (m & FLAG_BIT[f])}
                return ("ccr", snap_set(snap, **kw), ccr_assigned(a) | frozenset(kw)) if kw else a
            if m & ~0x1F00 == 0:                  # flag test
                return ("flagtest", snap, m)
        if m == 0xFF:
            if a[0] == "sext8":
                return ("lo8", a[1])
            return ("lo8", a) if km is None or km & ~0xFF else a
        if m == 0xFFFF:
            if a[0] == "sext16":
                return ("lo16", a[1])
            if a[0] == "lo16":
                return a
            if a[0] in ("and", "or", "xor", "add", "sub", "shl", "not", "neg", "mul") :
                return ("lo16", a)
            return ("lo16", a)
        if a[0] == "and" and is_const(a[2]):
            return AND(a[1], C(a[2][1] & m))
        if a[0] == "lo16" and m & ~0xFFFF == 0:
            return AND(a[1], b) if m != 0xFFFF else a
        if a[0] == "lo8" and m & ~0xFF == 0:
            return AND(a[1], b) if m != 0xFF else a
    if a == b:
        return a
    return ("and", a, b)


def OR(a, b):
    if is_const(a) and not is_const(b):
        a, b = b, a
    if is_const(a) and is_const(b):
        return C(a[1] | b[1])
    if is_const(b):
        if b[1] == 0:
            return a
        if a[0] == "ccr":
            m = b[1]
            kw = {f: TRUE for f in FLAGS if m & FLAG_BIT[f]}
            return ("ccr", snap_set(a[1], **kw), ccr_assigned(a) | frozenset(kw))
    # byte/halfword assembly: (x << 8) | y  with disjoint known masks
    r = _merge_loads(a, b)
    if r is not None:
        return r
    r = _assemble(a, b)
    if r is not None:
        return r
    if a == b:
        return a
    return ("or", a, b)


def _merge_loads(a, b):
    """or(shl(mem(p,n),8n), mem(p+n,n)) -> mem(p,2n) (big-endian assembly)."""
    for x, y in ((a, b), (b, a)):
        if x[0] == "shl" and is_const(x[2]) and x[1][0] == "mem" and y[0] == "mem":
            hi, lo = x[1], y
            n = hi[2]
            if x[2][1] == 8 * n and lo[2] == n and not lo[3] and not hi[3]:
                if ADD(hi[1], C(n)) == lo[1]:
                    return ("mem", hi[1], 2 * n, False)
        if x[0] == "shl" and is_const(x[2]) and x[1][0] == "sext8" and x[1][1][0] == "mem" and y[0] == "mem":
            hi, lo = x[1][1], y
            if x[2][1] == 8 and hi[2] == 1 and lo[2] == 1 and not lo[3] and ADD(hi[1], C(1)) == lo[1]:
                return ("sext16", ("mem", hi[1], 2, False))
        if x[0] == "shl" and is_const(x[2]) and x[1][0] == "mem" and y[0] == "mem" and x[1][3] and not y[3]:
            hi, lo = x[1], y
            n = hi[2]
            if x[2][1] == 8 * n and lo[2] == n and ADD(hi[1], C(n)) == lo[1]:
                return ("sext16", ("mem", hi[1], 2 * n, False)) if n == 1 else ("mem", hi[1], 2 * n, True)
    return None


def _or_terms(e, acc):
    if e[0] == "or":
        _or_terms(e[1], acc)
        _or_terms(e[2], acc)
    else:
        acc.append(e)


def _assemble(a, b):
    """General big-endian assembly: or-tree of shifted byte/halfword loads at consecutive
    addresses -> one wider load."""
    terms = []
    _or_terms(a, terms)
    _or_terms(b, terms)
    if len(terms) < 2:
        return None
    pieces = []     # (shift, size, addr, signed)
    slot = []       # (shift, size, off, leaf)
    for t in terms:
        sh = 0
        if t[0] == "shl" and is_const(t[2]):
            sh, t = t[2][1], t[1]
        sgn = False
        if t[0] in ("sext8", "sext16"):
            sgn, t = True, t[1]
        if sh % 8:
            return None
        if t[0] == "slotpart":
            slot.append((sh, t[3], t[2], ("r68", t[1], t[4])))
            continue
        if t[0] == "lo8" and t[1][0] == "r68":
            slot.append((sh, 1, 0, t[1]))
            continue
        if t[0] == "lo16" and t[1][0] == "r68":
            slot.append((sh, 2, 0, t[1]))
            continue
        if t[0] != "mem":
            return None
        if t[3]:
            sgn = True
        pieces.append((sh, t[2], t[1], sgn))
    if slot and not pieces:
        leaf = slot[0][3]
        if any(x[3] != leaf for x in slot):
            return None
        cover = 0
        for sh, size, off, _ in slot:
            if sh != 8 * off:
                return None
            cover |= ((1 << (8 * size)) - 1) << sh
        if cover == 0xFFFF:
            return ("lo16", leaf)
        if cover == M32:
            return leaf
        return None
    if slot:
        return None
    pieces.sort(key=lambda p: -p[0])
    n = (pieces[0][0] // 8) + pieces[0][1]
    if n not in (2, 4) or len(pieces) < 2:
        return None
    base = pieces[0][2]
    cover = 0
    for sh, size, addr, sgn in pieces:
        k = n - size - sh // 8          # byte offset of this piece inside the word
        if k < 0 or ADD(base, C(k)) != addr:
            return None
        if sgn and k != 0:
            return None
        cover += size
    if cover != n:
        return None
    sgn0 = pieces[0][3]
    if sgn0 and pieces[0][1] * 8 + pieces[0][0] < 32:
        return ("sext16", ("mem", base, n, False)) if n == 2 else ("mem", base, n, True)
    return ("mem", base, n, sgn0)


def XOR(a, b):
    if is_const(a) and not is_const(b):
        a, b = b, a
    if is_const(a) and is_const(b):
        return C(a[1] ^ b[1])
    if is_const(b) and b[1] == 0:
        return a
    if a == b:
        return C(0)
    return ("xor", a, b)


def NOT(a):
    if is_const(a):
        return C(~a[1])
    if a[0] == "not":
        return a[1]
    return ("not", a)


def ADD(a, b):
    if is_const(a) and not is_const(b):
        a, b = b, a
    if is_const(a) and is_const(b):
        return C(a[1] + b[1])
    if is_const(b):
        if b[1] == 0:
            return a
        if a[0] == "add" and is_const(a[2]):
            return ADD(a[1], C(a[2][1] + b[1]))
        if a[0] == "sub" and is_const(a[2]):
            return ADD(a[1], C(b[1] - a[2][1]))
    return ("add", a, b)


def SUB(a, b):
    if is_const(a) and is_const(b):
        return C(a[1] - b[1])
    if is_const(b):
        if b[1] == 0:
            return a
        return ADD(a, C(-b[1]))
    if a == b:
        return C(0)
    if is_const(a) and a[1] == 0:
        return NEG(b)
    return ("sub", a, b)


def NEG(a):
    if is_const(a):
        return C(-a[1])
    if a[0] == "neg":
        return a[1]
    return ("neg", a)


def SHL(a, n: int):
    n &= 31
    if n == 0:
        return a
    if is_const(a):
        return C(a[1] << n)
    if a[0] == "shl" and is_const(a[2]):
        return SHL(a[1], n + a[2][1])
    if a[0] == "shr" and is_const(a[2]) and a[2][1] == n:
        return AND(a[1], C((M32 << n) & M32))
    return ("shl", a, C(n))


def SHR(a, n: int):
    n &= 31
    if n == 0:
        return a
    if is_const(a):
        return C(a[1] >> n)
    km = _mask_of(a)
    if km is not None and (km >> n) == 0:
        return C(0)
    if a[0] == "shr" and is_const(a[2]):
        return SHR(a[1], n + a[2][1]) if n + a[2][1] < 32 else C(0)
    if a[0] == "sar" and is_const(a[2]):
        return SAR(a[1], min(31, n + a[2][1]))
    if a[0] == "shl" and is_const(a[2]):
        s = a[2][1]
        if s == n:
            return AND(a[1], C(M32 >> n))
        if s > n:
            return AND(SHL(a[1], s - n), C(M32 >> n))
        return AND(SHR(a[1], n - s), C(M32 >> n))
    return ("shr", a, C(n))


def SAR(a, n: int):
    n &= 31
    if n == 0:
        return a
    if is_const(a):
        return C(signed(a[1]) >> n)
    km = _mask_of(a)
    if km is not None and km & 0x80000000 == 0:
        return SHR(a, n)
    if a[0] == "sar" and is_const(a[2]):
        return SAR(a[1], min(31, n + a[2][1]))
    if a[0] == "shl" and is_const(a[2]):
        s = a[2][1]
        if s == 16 and n == 16:
            return ("sext16", a[1])
        if s == 24 and n == 24:
            return ("sext8", a[1])
        if s == n:
            return ("sext16", a[1]) if n == 16 else ("sext8", a[1]) if n == 24 else ("sar", a, C(n))
        if s > n:
            inner = SHL(a[1], s - n)
            if n == 16:
                return ("sext16", inner)
            if n == 24:
                return ("sext8", inner)
            return ("sar", a, C(n))
        # s < n: (x << s) >>a n  == sext(x) >> (n-s)
        if s == 16:
            return SAR(("sext16", a[1]), n - s)
        if s == 24:
            return SAR(("sext8", a[1]), n - s)
    return ("sar", a, C(n))


def SEXT16(a):
    if is_const(a):
        v = a[1] & 0xFFFF
        return C(v - 0x10000 if v & 0x8000 else v)
    if a[0] == "lo16":
        return ("sext16", a[1])
    if a[0] == "sext16":
        return a
    if a[0] == "mem" and a[2] == 2:
        return ("mem", a[1], 2, True)
    if a[0] == "sext8":
        return a
    return ("sext16", a)


def SEXT8(a):
    if is_const(a):
        v = a[1] & 0xFF
        return C(v - 0x100 if v & 0x80 else v)
    if a[0] == "lo8":
        return ("sext8", a[1])
    if a[0] == "sext8":
        return a
    if a[0] == "mem" and a[2] == 1:
        return ("mem", a[1], 1, True)
    return ("sext8", a)


def LO16(a):
    return AND(a, C(0xFFFF))


def LO8(a):
    return AND(a, C(0xFF))


def ITE(c, a, b):
    if a == b:
        return a
    if c == TRUE:
        return a
    if c == FALSE:
        return b
    if a[0] == "ccr" and b[0] == "ccr":
        sa, sb = dict(a[1]), dict(b[1])
        return ("ccr", tuple((f, bite(c, sa[f], sb[f])) for f in FLAGS), ccr_assigned(a) | ccr_assigned(b))
    return ("ite", c, a, b)


def SLT(a, b, unsigned=False):
    if is_const(a) and is_const(b):
        if unsigned:
            return C(1 if a[1] < b[1] else 0)
        return C(1 if signed(a[1]) < signed(b[1]) else 0)
    return ("sltu" if unsigned else "slt", a, b)


def strip(e):
    """Remove size casts that do not change a 16/8-bit interpretation (for matching)."""
    while e[0] in ("lo16", "lo8", "sext16", "sext8"):
        e = e[1]
    return e


def refs(e, pred) -> bool:
    """True if some sub-expression satisfies pred."""
    if pred(e):
        return True
    if isinstance(e, tuple):
        for x in e[1:]:
            if isinstance(x, tuple) and refs(x, pred):
                return True
    return False


def fmt(e) -> str:
    k = e[0]
    if k == "c":
        v = e[1]
        return f"${v:x}" if v < 0x80000000 else f"-${(-v) & M32:x}"
    if k == "r68":
        return f"{e[1]}@{e[2]}" if len(e) > 2 and e[2] else e[1]
    if k == "in":
        return f"${e[1]}?"
    if k == "mem":
        return f"[{fmt(e[1])}].{ {1: 'b', 2: 'w', 4: 'l'}[e[2]] }{'s' if e[3] else ''}"
    if k == "ccr":
        return "CCR{" + ",".join(f"{f}={fmtb(v)}" for f, v in e[1] if v != ('flag', f)) + "}"
    if k == "flagtest":
        return f"CCR&{e[2]:#x}"
    if k in ("true", "false", "flag", "eq0", "ne0", "bit", "lt0", "ge0", "le0", "gt0", "bnot", "band", "bor", "bite", "ne", "eq"):
        return fmtb(e)
    if k == "ite":
        return f"({fmtb(e[1])} ? {fmt(e[2])} : {fmt(e[3])})"
    if k == "bool":
        return f"bool({fmtb(e[1])})"
    return f"{k}({', '.join(fmt(x) if isinstance(x, tuple) else str(x) for x in e[1:])})"


def fmtb(b) -> str:
    if not isinstance(b, tuple):
        return str(b)
    k = b[0]
    if k == "true":
        return "1"
    if k == "false":
        return "0"
    if k == "flag":
        return b[1]
    if k in ("eq0", "ne0", "lt0", "ge0", "le0", "gt0"):
        return f"{k}({fmt(b[1])})"
    if k == "bit":
        return f"bit{b[2]}({fmt(b[1])})"
    if k == "bnot":
        return f"!{fmtb(b[1])}"
    if k in ("band", "bor"):
        return f"({fmtb(b[1])} {'&&' if k == 'band' else '||'} {fmtb(b[2])})"
    if k == "bite":
        return f"({fmtb(b[1])} ? {fmtb(b[2])} : {fmtb(b[3])})"
    if k in ("ne", "eq"):
        return f"{k}({fmt(b[1])}, {fmt(b[2])})"
    return fmt(b)


def subst(e, old, new):
    """Replace sub-expression `old` by `new` and re-simplify."""
    if e == old:
        return new
    if not isinstance(e, tuple) or len(e) < 2:
        return e
    k = e[0]
    args = [subst(x, old, new) if isinstance(x, tuple) else x for x in e[1:]]
    if args == list(e[1:]):
        return e
    if k == "and":
        return AND(args[0], args[1])
    if k == "or":
        return OR(args[0], args[1])
    if k == "xor":
        return XOR(args[0], args[1])
    if k == "add":
        return ADD(args[0], args[1])
    if k == "sub":
        return SUB(args[0], args[1])
    if k == "not":
        return NOT(args[0])
    if k == "ite":
        return ITE(args[0], args[1], args[2])
    if k == "shl" and is_const(args[1]):
        return SHL(args[0], args[1][1])
    if k == "shr" and is_const(args[1]):
        return SHR(args[0], args[1][1])
    if k == "sar" and is_const(args[1]):
        return SAR(args[0], args[1][1])
    return (k, *args)
