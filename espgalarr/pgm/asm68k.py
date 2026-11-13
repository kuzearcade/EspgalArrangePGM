"""A small two-pass 68000 assembler (the subset our new code uses).

Syntax (Motorola-style, one instruction per line, ';' comments):
    label:            label definition (also 'label' alone at column 0)
    .org $200000      set origin
    .equ NAME, expr   constant
    dc.b/dc.w/dc.l    data;  ds.b n  reserve;  .align n
    move.w d0,(a0)+   etc.
Operands: dN aN (aN) (aN)+ -(aN) d16(aN) d8(aN,xN.w|.l) $abs.w $abs.l d16(pc) d8(pc,xN.w|.l) #imm label
Sizes: .b .w .l (default .w).  Branches: bra/bsr/bcc with .s/.b or .w; dbCC.
Expressions: integers ($hex, %bin), labels, + - * / & | << >> and parentheses.
Verified against capstone in tests/test_asm68k.py.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field


class AsmError(Exception):
    pass


# ---------------------------------------------------------------- expressions

class _Expr:
    def __init__(self, text: str):
        self.text = text.strip()

    def eval(self, syms: dict[str, int], pc: int, strict: bool) -> int:
        t = self.text
        t = re.sub(r"\$([0-9a-fA-F]+)", lambda m: str(int(m.group(1), 16)), t)
        t = re.sub(r"%([01]+)", lambda m: str(int(m.group(1), 2)), t)

        def sub(m):
            name = m.group(0)
            if name == "pc" or name == "PC":
                return str(pc)
            if name in syms:
                return str(syms[name])
            m = re.fullmatch(r"orig_([0-9a-fA-F]{6})", name)
            if m:  # original routine at that 68k address
                return str(int(m.group(1), 16))
            if strict:
                raise AsmError(f"undefined symbol {name!r}")
            return "0"
        t = re.sub(r"[A-Za-z_.][A-Za-z0-9_.]*", sub, t)
        if not re.fullmatch(r"[0-9+\-*/&|<>() ]+", t):
            raise AsmError(f"bad expression {self.text!r}")
        return int(eval(t, {"__builtins__": {}}, {}))  # noqa: S307 - sanitised arithmetic only


# ------------------------------------------------------------------- operands

@dataclass
class Op:
    mode: int             # EA mode field
    reg: int              # EA register field
    ext: list[int] = field(default_factory=list)   # extension words
    kind: str = ""        # 'dn','an','ind','post','pre','d16','d8','absw','absl','pcd16','imm'
    value: int = 0        # immediate / displacement / absolute (resolved)
    expr: _Expr | None = None
    xreg: int = 0
    xlong: bool = False


_REG_D = re.compile(r"^d([0-7])$", re.I)
_REG_A = re.compile(r"^(a[0-7]|sp)$", re.I)


def _areg(s: str) -> int:
    s = s.lower()
    return 7 if s == "sp" else int(s[1])


def parse_operand(s: str, size: str, syms, pc, strict) -> Op:
    s = s.strip()
    if _REG_D.match(s):
        return Op(0, int(s[1]), kind="dn")
    if _REG_A.match(s):
        return Op(1, _areg(s), kind="an")
    m = re.match(r"^\((a[0-7]|sp)\)\+$", s, re.I)
    if m:
        return Op(3, _areg(m.group(1)), kind="post")
    m = re.match(r"^-\((a[0-7]|sp)\)$", s, re.I)
    if m:
        return Op(4, _areg(m.group(1)), kind="pre")
    m = re.match(r"^\((a[0-7]|sp)\)$", s, re.I)
    if m:
        return Op(2, _areg(m.group(1)), kind="ind")
    m = re.match(r"^(.*)\(pc,\s*([da][0-7])(\.[wl])?\)$", s, re.I)
    if m:
        target = _Expr(m.group(1)).eval(syms, pc, strict)
        d = target - (pc + 2)
        xr = m.group(2).lower()
        xlong = (m.group(3) or ".w").lower() == ".l"
        xfield = (8 if xr[0] == "a" else 0) | int(xr[1])
        if strict and not -128 <= d <= 127:
            raise AsmError(f"pc-relative d8 out of range: {s}")
        return Op(7, 3, [(xfield << 12) | (0x800 if xlong else 0) | (d & 0xFF)], kind="pcd8", value=d)
    m = re.match(r"^(.*)\((a[0-7]|sp),\s*([da][0-7])(\.[wl])?\)$", s, re.I)
    if m:
        d = _Expr(m.group(1) or "0").eval(syms, pc, strict)
        xr = m.group(3).lower()
        xlong = (m.group(4) or ".w").lower() == ".l"
        xfield = (8 if xr[0] == "a" else 0) | int(xr[1])
        if not -128 <= d <= 127:
            raise AsmError(f"d8 out of range: {s}")
        return Op(6, _areg(m.group(2)), [(xfield << 12) | (0x800 if xlong else 0) | (d & 0xFF)], kind="d8", value=d)
    m = re.match(r"^(.*)\((a[0-7]|sp)\)$", s, re.I)
    if m:
        d = _Expr(m.group(1) or "0").eval(syms, pc, strict)
        if not -32768 <= d <= 32767:
            raise AsmError(f"d16 out of range: {s}")
        return Op(5, _areg(m.group(2)), [d & 0xFFFF], kind="d16", value=d)
    m = re.match(r"^(.*)\(pc\)$", s, re.I)
    if m:
        target = _Expr(m.group(1)).eval(syms, pc, strict)
        d = target - (pc + 2)
        if strict and not -32768 <= d <= 32767:
            raise AsmError(f"pc displacement out of range: {s}")
        return Op(7, 2, [d & 0xFFFF], kind="pcd16", value=d)
    if s.startswith("#"):
        v = _Expr(s[1:]).eval(syms, pc, strict)
        if size == "l":
            return Op(7, 4, [(v >> 16) & 0xFFFF, v & 0xFFFF], kind="imm", value=v)
        if size == "b":
            return Op(7, 4, [v & 0xFF], kind="imm", value=v)
        return Op(7, 4, [v & 0xFFFF], kind="imm", value=v)
    m = re.match(r"^(.*)\.(w|l)$", s, re.I)
    forced = m.group(2).lower() if m else None
    v = _Expr(m.group(1) if m else s).eval(syms, pc, strict)
    if forced == "w" or (forced is None and -32768 <= v <= 32767 and strict and False):
        return Op(7, 0, [v & 0xFFFF], kind="absw", value=v)
    return Op(7, 1, [(v >> 16) & 0xFFFF, v & 0xFFFF], kind="absl", value=v)


# --------------------------------------------------------------- instructions

SIZES = {"b": 0, "w": 1, "l": 2}
CC = {"t": 0, "f": 1, "hi": 2, "ls": 3, "cc": 4, "hs": 4, "cs": 5, "lo": 5, "ne": 6, "eq": 7,
      "vc": 8, "vs": 9, "pl": 10, "mi": 11, "ge": 12, "lt": 13, "gt": 14, "le": 15}


def _ea(op: Op) -> int:
    return (op.mode << 3) | op.reg


def _w(words: list[int]) -> bytes:
    return b"".join(struct.pack(">H", w & 0xFFFF) for w in words)


class Assembler:
    def __init__(self):
        self.syms: dict[str, int] = {}

    # pass driver -------------------------------------------------------------
    def assemble(self, source: str, origin: int = 0) -> tuple[bytes, dict[str, int]]:
        lines = self._preprocess(source)
        from . import allocburst              # back-to-back allocator calls resume the scan
        lines = allocburst.rewrite(lines)
        self._run_pass(lines, origin, strict=False)
        out, _ = self._run_pass(lines, origin, strict=True)
        return out, dict(self.syms)

    def _preprocess(self, src: str) -> list[tuple[str | None, str, str]]:
        out = []
        for raw in src.splitlines():
            line = raw.split(";", 1)[0].rstrip()
            if not line.strip():
                continue
            label = None
            m = re.match(r"^([A-Za-z_.][A-Za-z0-9_.]*):\s*(.*)$", line)
            if m:
                label, line = m.group(1), m.group(2)
            elif not line[0].isspace() and not line.startswith("."):
                parts = line.split(None, 1)
                label, line = parts[0], (parts[1] if len(parts) > 1 else "")
            line = line.strip()
            if not line:
                out.append((label, "", ""))
                continue
            parts = line.split(None, 1)
            out.append((label, parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""))
        return out

    def _run_pass(self, lines, origin, strict) -> tuple[bytes, int]:
        pc = origin
        out = bytearray()
        start = origin
        for label, mnem, args in lines:
            if label is not None and mnem != ".equ":
                if strict and label in self.syms and self.syms[label] != pc:
                    raise AsmError(f"label {label} moved between passes ({self.syms[label]:#x} -> {pc:#x})")
                self.syms[label] = pc
            if not mnem:
                continue
            if mnem == ".org":
                pc = _Expr(args).eval(self.syms, pc, strict)
                if not out:
                    start = pc
                else:
                    if pc < start + len(out):
                        raise AsmError(".org backwards")
                    out += b"\xff" * (pc - start - len(out))
                continue
            if mnem == ".equ":
                name, expr = [x.strip() for x in args.split(",", 1)]
                self.syms[name] = _Expr(expr).eval(self.syms, pc, strict)
                continue
            if mnem == ".align":
                n = _Expr(args).eval(self.syms, pc, strict)
                while pc % n:
                    out += b"\xff"
                    pc += 1
                continue
            code = self._encode(mnem, args, pc, strict)
            out += code
            pc += len(code)
        return bytes(out), start

    # encoding --------------------------------------------------------------
    def _split_args(self, args: str) -> list[str]:
        res, depth, cur = [], 0, ""
        for ch in args:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                res.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            res.append(cur.strip())
        return res

    def _encode(self, mnem: str, args: str, pc: int, strict: bool) -> bytes:
        base, _, sz = mnem.partition(".")
        ops = self._split_args(args) if args else []
        syms = self.syms
        P = lambda s, size="w": parse_operand(s, size, syms, pc, strict)  # noqa: E731

        if base in ("dc",):
            vals = [_Expr(x).eval(syms, pc, strict) for x in ops]
            if sz == "b":
                return bytes(v & 0xFF for v in vals)
            if sz == "l":
                return b"".join(struct.pack(">I", v & 0xFFFFFFFF) for v in vals)
            return _w(vals)
        if base == "ds":
            n = _Expr(ops[0]).eval(syms, pc, strict)
            return b"\x00" * (n * {"b": 1, "w": 2, "l": 4}[sz or "w"])
        if base in ("rts", "nop", "rte", "illegal", "trapv", "reset"):
            return _w([{"rts": 0x4E75, "nop": 0x4E71, "rte": 0x4E73, "illegal": 0x4AFC, "trapv": 0x4E76, "reset": 0x4E70}[base]])

        size = sz or "w"
        S = SIZES.get(size)

        if base in ("bra", "bsr") or (base[0] == "b" and base[1:] in CC):
            target = _Expr(ops[0]).eval(syms, pc, strict)
            cond = {"bra": 0, "bsr": 1}.get(base, CC.get(base[1:]))
            disp = target - (pc + 2)
            if sz in ("s", "b"):
                if strict and not (-128 <= disp <= 127) or disp == 0:
                    raise AsmError(f"short branch out of range at {pc:#x}")
                return _w([0x6000 | (cond << 8) | (disp & 0xFF)])
            if strict and not -32768 <= disp <= 32767:
                raise AsmError("branch out of range")
            return _w([0x6000 | (cond << 8), disp & 0xFFFF])
        if base.startswith("db") and (base[2:] in CC or base == "dbra"):
            cond = 1 if base == "dbra" else CC[base[2:]]
            dn = int(ops[0][1])
            target = _Expr(ops[1]).eval(syms, pc, strict)
            disp = target - (pc + 2)
            return _w([0x50C8 | (cond << 8) | dn, disp & 0xFFFF])

        if base in ("jmp", "jsr"):
            op = P(ops[0], "l")
            return _w([(0x4EC0 if base == "jmp" else 0x4E80) | _ea(op)] + op.ext)
        if base == "lea":
            src, dst = P(ops[0], "l"), P(ops[1])
            return _w([0x41C0 | (dst.reg << 9) | _ea(src)] + src.ext)
        if base == "pea":
            op = P(ops[0], "l")
            return _w([0x4840 | _ea(op)] + op.ext)
        if base == "moveq":
            v = _Expr(ops[0][1:]).eval(syms, pc, strict)
            return _w([0x7000 | (int(ops[1][1]) << 9) | (v & 0xFF)])
        if base in ("move", "movea"):
            src, dst = P(ops[0], size), P(ops[1], size)
            if dst.kind == "an" and size == "b":
                raise AsmError("movea.b")
            if ops[1].lower() in ("sr", "ccr"):
                return _w([(0x46C0 if ops[1].lower() == "sr" else 0x44C0) | _ea(src)] + src.ext)
            if ops[0].lower() == "sr":
                return _w([0x40C0 | _ea(dst)] + dst.ext)
            if ops[0].lower() == "usp":
                return _w([0x4E68 | dst.reg])
            if ops[1].lower() == "usp":
                return _w([0x4E60 | src.reg])
            szbits = {"b": 1, "w": 3, "l": 2}[size]
            return _w([(szbits << 12) | (dst.reg << 9) | (dst.mode << 6) | _ea(src)] + src.ext + dst.ext)
        if base == "movem":
            # movem.w/l reglist,-(an) | reglist,ea | ea,reglist
            def reglist(s: str) -> int:
                mask = 0
                for part in s.split("/"):
                    if "-" in part:
                        a, b = part.split("-")
                        ra, rb = _regnum(a), _regnum(b)
                        for r in range(ra, rb + 1):
                            mask |= 1 << r
                    else:
                        mask |= 1 << _regnum(part)
                return mask
            if re.match(r"^[da]", ops[0].lower()) and not _REG_A.match(ops[0]) and "/" in ops[0] or "-" in ops[0] and not ops[0].startswith("-("):
                mask = reglist(ops[0])
                dst = P(ops[1], size)
                if dst.kind == "pre":
                    mask = int(f"{mask:016b}"[::-1], 2)
                return _w([0x4880 | ((S - 1) << 6) | _ea(dst), mask] + dst.ext)
            src = P(ops[0], size)
            mask = reglist(ops[1])
            return _w([0x4C80 | ((S - 1) << 6) | _ea(src), mask] + src.ext)
        if base in ("link",):
            return _w([0x4E50 | _areg(ops[0]), _Expr(ops[1][1:]).eval(syms, pc, strict) & 0xFFFF])
        if base == "unlk":
            return _w([0x4E58 | _areg(ops[0])])
        if base == "exg":
            a, b = P(ops[0]), P(ops[1])
            if a.kind == b.kind == "dn":
                return _w([0xC140 | (a.reg << 9) | b.reg])
            if a.kind == b.kind == "an":
                return _w([0xC148 | (a.reg << 9) | b.reg])
            d, an = (a, b) if a.kind == "dn" else (b, a)
            return _w([0xC188 | (d.reg << 9) | an.reg])
        if base in ("clr", "tst", "neg", "not", "negx"):
            op = P(ops[0], size)
            code = {"clr": 0x4200, "tst": 0x4A00, "neg": 0x4400, "not": 0x4600, "negx": 0x4000}[base]
            return _w([code | (S << 6) | _ea(op)] + op.ext)
        if base == "tas":
            op = P(ops[0], "b")
            return _w([0x4AC0 | _ea(op)] + op.ext)
        if base == "swap":
            return _w([0x4840 | int(ops[0][1])])
        if base == "ext":
            return _w([(0x4880 if size == "w" else 0x48C0) | int(ops[0][1])])
        if base in ("addq", "subq"):
            v = _Expr(ops[0][1:]).eval(syms, pc, strict)
            dst = P(ops[1], size)
            return _w([0x5000 | ((v & 7) << 9) | (0x100 if base == "subq" else 0) | (S << 6) | _ea(dst)] + dst.ext)
        if base in ("addi", "subi", "cmpi", "andi", "ori", "eori") or (
                base in ("add", "sub", "cmp", "and", "or", "eor") and ops[0].startswith("#") and not _REG_A.match(ops[1])):
            code = {"ori": 0x0000, "andi": 0x0200, "subi": 0x0400, "addi": 0x0600, "eori": 0x0A00, "cmpi": 0x0C00,
                    "or": 0x0000, "and": 0x0200, "sub": 0x0400, "add": 0x0600, "eor": 0x0A00, "cmp": 0x0C00}[base]
            imm = P(ops[0], size)
            if ops[1].lower() in ("sr", "ccr"):
                return _w([code | (0x7C if ops[1].lower() == "sr" else 0x3C)] + imm.ext)
            dst = P(ops[1], size)
            return _w([code | (S << 6) | _ea(dst)] + imm.ext + dst.ext)
        if base in ("add", "sub", "cmp", "and", "or", "eor", "adda", "suba", "cmpa", "mulu", "muls", "divu", "divs"):
            src, dst = P(ops[0], size), P(ops[1], size)
            if base in ("mulu", "muls", "divu", "divs"):
                code = {"mulu": 0xC0C0, "muls": 0xC1C0, "divu": 0x80C0, "divs": 0x81C0}[base]
                return _w([code | (dst.reg << 9) | _ea(src)] + src.ext)
            if dst.kind == "an" or base in ("adda", "suba", "cmpa"):
                code = {"add": 0xD0C0, "adda": 0xD0C0, "sub": 0x90C0, "suba": 0x90C0, "cmp": 0xB0C0, "cmpa": 0xB0C0}[base]
                return _w([code | (dst.reg << 9) | (0x100 if size == "l" else 0) | _ea(src)] + src.ext)
            if base == "eor":
                return _w([0xB100 | (src.reg << 9) | (S << 6) | _ea(dst)] + dst.ext)
            if base == "cmp":
                if src.kind == "post" and dst.kind == "post":
                    return _w([0xB108 | (dst.reg << 9) | (S << 6) | src.reg])
                return _w([0xB000 | (dst.reg << 9) | (S << 6) | _ea(src)] + src.ext)
            code = {"add": 0xD000, "sub": 0x9000, "and": 0xC000, "or": 0x8000}[base]
            if dst.kind == "dn":
                return _w([code | (dst.reg << 9) | (S << 6) | _ea(src)] + src.ext)
            return _w([code | (src.reg << 9) | 0x100 | (S << 6) | _ea(dst)] + dst.ext)
        if base in ("lsl", "lsr", "asl", "asr", "rol", "ror", "roxl", "roxr"):
            kind = {"as": 0, "ls": 1, "rox": 2, "ro": 3}[base[:-1]]
            dr = 1 if base.endswith("l") else 0
            if len(ops) == 1:   # memory shift by 1
                op = P(ops[0], size)
                return _w([0xE0C0 | (kind << 9) | (dr << 8) | _ea(op)] + op.ext)
            dst = int(ops[1][1])
            if ops[0].startswith("#"):
                n = _Expr(ops[0][1:]).eval(syms, pc, strict) & 7
                return _w([0xE000 | (n << 9) | (dr << 8) | (S << 6) | (kind << 3) | dst])
            return _w([0xE000 | (int(ops[0][1]) << 9) | (dr << 8) | (S << 6) | 0x20 | (kind << 3) | dst])
        if base in ("btst", "bset", "bclr", "bchg"):
            t = {"btst": 0, "bchg": 1, "bclr": 2, "bset": 3}[base]
            dst = P(ops[1], "b")
            if ops[0].startswith("#"):
                n = _Expr(ops[0][1:]).eval(syms, pc, strict)
                return _w([0x0800 | (t << 6) | _ea(dst), n & 0xFF] + dst.ext)
            return _w([0x0100 | (int(ops[0][1]) << 9) | (t << 6) | _ea(dst)] + dst.ext)
        if base[0] == "s" and base[1:] in CC:
            op = P(ops[0], "b")
            return _w([0x50C0 | (CC[base[1:]] << 8) | _ea(op)] + op.ext)
        if base == "trap":
            return _w([0x4E40 | (_Expr(ops[0][1:]).eval(syms, pc, strict) & 0xF)])
        if base in ("stop",):
            return _w([0x4E72, _Expr(ops[0][1:]).eval(syms, pc, strict) & 0xFFFF])
        raise AsmError(f"unsupported instruction {mnem} {args}")


def _regnum(s: str) -> int:
    s = s.strip().lower()
    if s == "sp":
        return 15
    return (8 if s[0] == "a" else 0) + int(s[1])


def assemble(source: str, origin: int = 0) -> tuple[bytes, dict[str, int]]:
    return Assembler().assemble(source, origin)
