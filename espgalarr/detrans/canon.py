"""Canonical form of 68k instructions (ours and capstone's) for comparison.

Semantically equivalent spellings map to one form: addq/addi/add #imm -> add, subq/.. ->
add of the negated immediate, moveq -> move.l, clr -> move #0, tst -> cmp #0, cmpi -> cmp,
andi/ori/eori -> and/or/eor, dbra -> dbf, lea.l -> lea, sizes dropped where implicit.
Absolute addresses, branch labels and call targets are kept as typed operands so that an
"abstract" key (addresses replaced by placeholders) can be used for n-gram anchoring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MASK = {"b": 0xFF, "w": 0xFFFF, "l": 0xFFFFFFFF, "": 0xFFFFFFFF}
BRANCHES = {"bra", "bsr", "bhi", "bls", "bcc", "bcs", "bne", "beq", "bvc", "bvs", "bpl", "bmi", "bge",
            "blt", "bgt", "ble", "bt", "bf"}
DBCC = {"dbra", "dbf", "dbt", "dbhi", "dbls", "dbcc", "dbcs", "dbne", "dbeq", "dbvc", "dbvs", "dbpl",
        "dbmi", "dbge", "dblt", "dbgt", "dble"}


@dataclass(frozen=True)
class Canon:
    mnem: str
    size: str
    ops: tuple            # each op: (kind, value) kinds: dn an ind post pre d16 d8 abs pc imm label func reglist special
    text: str             # canonical full text
    key: str              # abstracted text (addresses -> placeholders)


_num = re.compile(r"^(-?)\$?([0-9a-fA-F]+)$")


def _int(s: str) -> int:
    s = s.strip()
    m = _num.match(s)
    if not m:
        if s.lstrip("-").isdigit():
            return int(s)
        raise ValueError(s)
    sign, digits = m.groups()
    v = int(digits, 16) if "$" in s else int(digits)
    return -v if sign else v


def _areg(s: str) -> str:
    s = s.lower()
    return "a7" if s == "sp" else s


def parse_operand(s: str, mnem: str, size: str):
    s = s.strip()
    sl = s.lower()
    if re.fullmatch(r"d[0-7]", sl):
        return ("dn", sl)
    if re.fullmatch(r"a[0-7]|sp", sl):
        return ("an", _areg(sl))
    if sl in ("sr", "ccr", "usp"):
        return ("special", sl)
    m = re.fullmatch(r"\((a[0-7]|sp)\)\+", sl)
    if m:
        return ("post", _areg(m.group(1)))
    m = re.fullmatch(r"-\((a[0-7]|sp)\)", sl)
    if m:
        return ("pre", _areg(m.group(1)))
    m = re.fullmatch(r"\((a[0-7]|sp)\)", sl)
    if m:
        return ("ind", _areg(m.group(1)))
    m = re.fullmatch(r"(-?\$?[0-9a-f]*)\((a[0-7]|sp),\s*([da][0-7])\.([wl])\)", sl)
    if m:
        d = _int(m.group(1)) if m.group(1) else 0
        return ("d8", _areg(m.group(2)), m.group(3), m.group(4), d)
    m = re.fullmatch(r"(-?\$?[0-9a-f]*)\((a[0-7]|sp)\)", sl)
    if m:
        d = _int(m.group(1)) if m.group(1) else 0
        return ("d16", _areg(m.group(2)), d)
    m = re.fullmatch(r"(-?\$?[0-9a-f]+)\(pc(?:,\s*([da][0-7])\.([wl]))?\)", sl)
    if m:
        if m.group(2):
            return ("pcx", _int(m.group(1)), m.group(2), m.group(3))
        return ("pc", _int(m.group(1)))
    m = re.fullmatch(r"(-?\$?[0-9a-f]+)\(([da][0-7])\.([wl])\)", sl)
    if m:                       # our pseudo "abs(index)" form
        return ("absx", _int(m.group(1)) & 0xFFFFFFFF, m.group(2), m.group(3))
    if sl.startswith("#"):
        v = _int(sl[1:])
        if mnem == "moveq":
            v &= 0xFF
            if v & 0x80:
                v -= 0x100
            return ("imm", v & 0xFFFFFFFF)
        return ("imm", v & MASK.get(size, 0xFFFFFFFF))
    if re.fullmatch(r"l_[0-9a-f]+", sl):
        return ("label", sl)
    if re.fullmatch(r"f_[0-9a-f]+", sl):
        return ("func", sl)
    if re.fullmatch(r"[da][0-7](-[da][0-7])?(/[da][0-7](-[da][0-7])?)*", sl):
        return ("reglist", _reglist(sl))
    m = re.fullmatch(r"(-?\$?[0-9a-f]+)(\.[wl])?", sl)
    if m:
        v = _int(m.group(1))
        if m.group(2) == ".w":
            v = v & 0xFFFF
            if v & 0x8000:
                v |= 0xFFFF0000
        v &= 0xFFFFFFFF
        if mnem in BRANCHES or mnem in DBCC:
            return ("label", v)
        if mnem in ("jsr", "jmp", "bsr"):
            return ("func", v)
        return ("abs", v)
    return ("?", sl)


def _reglist(s: str) -> int:
    mask = 0
    for part in s.split("/"):
        if "-" in part:
            a, b = part.split("-")
            lo, hi = _regno(a), _regno(b)
        else:
            lo = hi = _regno(part)
        for i in range(lo, hi + 1):
            mask |= 1 << i
    return mask


def _regno(r: str) -> int:
    return int(r[1]) + (8 if r[0] == "a" else 0)


def _op_text(op) -> str:
    k = op[0]
    if k in ("dn", "an", "special", "label", "func"):
        return str(op[1]) if k not in ("label", "func") else (op[1] if isinstance(op[1], str) else f"{k[0]}{op[1]:x}")
    if k == "ind":
        return f"({op[1]})"
    if k == "post":
        return f"({op[1]})+"
    if k == "pre":
        return f"-({op[1]})"
    if k == "d16":
        return f"{op[2]}({op[1]})"
    if k == "d8":
        return f"{op[4]}({op[1]},{op[2]}.{op[3]})"
    if k == "pc":
        return f"${op[1]:x}(pc)"
    if k == "pcx":
        return f"${op[1]:x}(pc,{op[2]}.{op[3]})"
    if k == "absx":
        return f"${op[1]:x}({op[2]}.{op[3]})"
    if k == "abs":
        return f"${op[1]:x}"
    if k == "imm":
        return f"#${op[1]:x}"
    if k == "reglist":
        return f"#{op[1]:04x}"
    return str(op[1])


def _op_key(op) -> str:
    k = op[0]
    if k in ("abs", "pc"):
        return "A"
    if k == "label":
        return "L"
    if k == "func":
        return "F"
    if k == "pcx":
        return f"A({op[2]}.{op[3]})"
    if k == "absx":
        return f"A({op[2]}.{op[3]})"
    return _op_text(op)


def canon(mnem: str, ops_text: str) -> Canon:
    """mnem like 'move.w' / 'lea.l', ops_text like 'd0, $4(a0)'."""
    m = mnem.lower()
    size = ""
    if "." in m:
        m, size = m.split(".", 1)
    ops = [parse_operand(o, m, size) for o in _split(ops_text)] if ops_text.strip() else []
    # --- mnemonic normalisation
    if m in ("lea", "pea", "jsr", "jmp", "exg", "swap", "rts", "nop", "link", "unlk", "rte", "trap", "reset"):
        size = ""
    if m in BRANCHES or m in DBCC:
        size = ""
        if m in ("dbra", "dbf"):
            m = "dbf"
    if m in ("sne", "seq", "st", "sf", "shi", "sls", "scc", "scs", "svc", "svs", "spl", "smi", "sge", "slt", "sgt", "sle"):
        size = ""
    if m == "moveq":
        m, size = "move", "l"
    if m in ("addq", "addi", "subq", "subi", "add", "sub") and ops and ops[0][0] == "imm":
        dst = ops[1]
        v = ops[0][1]
        if dst[0] == "an":
            # address-register arithmetic: size-less, signed
            sv = v if v < 0x80000000 else v - 0x100000000
            if size == "w" and v & 0x8000:
                sv = v - 0x10000
            if m in ("subq", "subi", "sub"):
                sv = -sv
            m, size, ops = "adda", "", [("imm", sv & 0xFFFFFFFF), dst]
        else:
            msk = MASK[size]
            if m in ("subq", "subi", "sub"):
                v = (-v) & msk
            m, ops = "add", [("imm", v & msk), dst]
    elif m in ("adda", "suba") and ops and ops[0][0] == "imm":
        v = ops[0][1]
        sv = v if v < 0x80000000 else v - 0x100000000
        if size == "w" and v & 0x8000:
            sv = v - 0x10000
        if m == "suba":
            sv = -sv
        m, size, ops = "adda", "", [("imm", sv & 0xFFFFFFFF), ops[1]]
    elif m == "lea" and len(ops) == 2 and ops[0][0] == "d16" and ops[1][0] == "an" and ops[0][1] == ops[1][1]:
        m, size, ops = "adda", "", [("imm", ops[0][2] & 0xFFFFFFFF), ops[1]]
    if m in ("cmpi", "cmp") and ops and ops[0][0] == "imm":
        m = "cmp"
    if m in ("andi", "and") and ops and ops[0][0] == "imm":
        m = "and"
    if m in ("ori", "or") and ops and ops[0][0] == "imm":
        m = "or"
    if m in ("eori", "eor") and ops and ops[0][0] == "imm":
        m = "eor"
    if m == "clr":
        m, ops = "move", [("imm", 0), ops[0]]
    if m == "tst":
        m, ops = "cmp", [("imm", 0), ops[0]]
    if m == "movea" and size == "l":
        size = ""
    if m == "move" and size == "l" and len(ops) == 2 and ops[1][0] == "an":
        m, size = "movea", ""
    if m == "move" and size == "w" and len(ops) == 2 and ops[1][0] == "an":
        m = "movea"
    if m in ("cmpa",) and size == "l":
        size = ""
    if m == "cmp" and len(ops) == 2 and ops[1][0] == "an":
        m = "cmpa"
        if size == "l":
            size = ""
    if m in ("bset", "bclr", "bchg", "btst"):
        size = ""
    if m in ("muls", "mulu", "divs", "divu") and size == "w":
        size = ""
    if m == "ext" or m == "movem":
        pass
    text = f"{m}.{size}" if size else m
    key = text
    if ops:
        text += " " + ",".join(_op_text(o) for o in ops)
        key += " " + ",".join(_op_key(o) for o in ops)
    return Canon(m, size, tuple(ops), text, key)


def _split(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]
