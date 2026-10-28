"""A small two-pass Z80 assembler (the subset the streaming sound driver uses).

Syntax: Zilog mnemonics, one per line, ';' comments, 'label:' definitions,
.org / .equ / db / dw / ds.  Expressions as in asm68k ($hex, labels, + - * / & | << >>).
Supported: ld (all common forms incl. (ix+d)/(iy+d)), push/pop, ex, exx, inc/dec, add/adc/
sub/sbc/and/or/xor/cp, rlca/rrca/rla/rra/rlc/rrc/rl/rr/sla/sra/srl, bit/set/res, jp/jr/djnz/
call/ret (conditional), rst, in/out, ldir/lddr/ldi/ldd, di/ei/nop/halt/im, neg, cpl, ccf, scf.
"""
from __future__ import annotations

import re

from .asm68k import AsmError, _Expr

R8 = {"b": 0, "c": 1, "d": 2, "e": 3, "h": 4, "l": 5, "(hl)": 6, "a": 7}
R16 = {"bc": 0, "de": 1, "hl": 2, "sp": 3}
R16AF = {"bc": 0, "de": 1, "hl": 2, "af": 3}
CC = {"nz": 0, "z": 1, "nc": 2, "c": 3, "po": 4, "pe": 5, "p": 6, "m": 7}
ALU = {"add": 0, "adc": 1, "sub": 2, "sbc": 3, "and": 4, "xor": 5, "or": 6, "cp": 7}
ROT = {"rlc": 0, "rrc": 1, "rl": 2, "rr": 3, "sla": 4, "sra": 5, "sll": 6, "srl": 7}
IDX = {"ix": 0xDD, "iy": 0xFD}


class AssemblerZ80:
    def __init__(self):
        self.syms: dict[str, int] = {}

    def assemble(self, source: str, origin: int = 0) -> tuple[bytes, dict[str, int]]:
        lines = []
        for raw in source.splitlines():
            line = raw.split(";", 1)[0].rstrip()
            if not line.strip():
                continue
            label = None
            m = re.match(r"^([A-Za-z_.][A-Za-z0-9_.]*):\s*(.*)$", line)
            if m:
                label, line = m.group(1), m.group(2).strip()
            parts = line.strip().split(None, 1)
            mnem = parts[0].lower() if parts else ""
            args = parts[1].strip() if len(parts) > 1 else ""
            lines.append((label, mnem, args))
        self._pass(lines, origin, False)
        return self._pass(lines, origin, True), dict(self.syms)

    def _pass(self, lines, origin, strict) -> bytes:
        pc = origin
        out = bytearray()
        for label, mnem, args in lines:
            if label is not None and mnem != ".equ":
                if strict and self.syms.get(label, pc) != pc:
                    raise AsmError(f"label {label} moved between passes")
                self.syms[label] = pc
            if not mnem:
                continue
            if mnem == ".org":
                npc = _Expr(args).eval(self.syms, pc, strict)
                if out and npc > pc:
                    out += b"\x00" * (npc - pc)
                pc = npc
                continue
            if mnem == ".equ":
                name, expr = [x.strip() for x in args.split(",", 1)]
                self.syms[name] = _Expr(expr).eval(self.syms, pc, strict)
                continue
            code = self._encode(mnem, args, pc, strict)
            out += code
            pc += len(code)
        return bytes(out)

    def _ev(self, s, pc, strict) -> int:
        return _Expr(s).eval(self.syms, pc, strict)

    def _encode(self, mnem: str, args: str, pc: int, strict: bool) -> bytes:
        ops = [o.strip() for o in re.split(r",(?![^(]*\))", args)] if args else []
        lo = [o.lower() for o in ops]
        ev = lambda s: self._ev(s, pc, strict)  # noqa: E731

        def idx(op):   # (ix+d) / (iy+d) -> (prefix, disp) or None
            m = re.match(r"^\((ix|iy)\s*([+-].*)?\)$", op, re.I)
            if not m:
                return None
            d = ev(m.group(2)) if m.group(2) else 0
            return IDX[m.group(1).lower()], d & 0xFF

        if mnem == "db":
            out = bytearray()
            for o in ops:
                if o.startswith('"'):
                    out += o.strip('"').encode()
                else:
                    out.append(ev(o) & 0xFF)
            return bytes(out)
        if mnem == "dw":
            out = bytearray()
            for o in ops:
                v = ev(o)
                out += bytes((v & 0xFF, (v >> 8) & 0xFF))
            return bytes(out)
        if mnem == "ds":
            return b"\x00" * ev(ops[0])
        simple = {"nop": b"\x00", "halt": b"\x76", "di": b"\xf3", "ei": b"\xfb", "exx": b"\xd9",
                  "rlca": b"\x07", "rrca": b"\x0f", "rla": b"\x17", "rra": b"\x1f", "cpl": b"\x2f",
                  "scf": b"\x37", "ccf": b"\x3f", "ldir": b"\xed\xb0", "lddr": b"\xed\xb8",
                  "ldi": b"\xed\xa0", "ldd": b"\xed\xa8", "neg": b"\xed\x44", "reti": b"\xed\x4d",
                  "retn": b"\xed\x45", "rld": b"\xed\x6f", "rrd": b"\xed\x67", "otir": b"\xed\xb3", "inir": b"\xed\xb2"}
        if mnem in simple and not ops:
            return simple[mnem]
        if mnem == "im":
            return bytes((0xED, {0: 0x46, 1: 0x56, 2: 0x5E}[ev(ops[0])]))
        if mnem == "ret":
            return b"\xc9" if not ops else bytes((0xC0 | (CC[lo[0]] << 3),))
        if mnem == "rst":
            return bytes((0xC7 | (ev(ops[0]) & 0x38),))
        if mnem in ("jp", "call"):
            if mnem == "jp" and lo[0] in ("(hl)", "hl"):
                return b"\xe9"
            if mnem == "jp" and lo[0] in ("(ix)", "(iy)"):
                return bytes((IDX[lo[0][1:3]], 0xE9))
            if len(ops) == 1:
                v = ev(ops[0])
                return bytes((0xC3 if mnem == "jp" else 0xCD, v & 0xFF, v >> 8))
            v = ev(ops[1])
            return bytes(((0xC2 if mnem == "jp" else 0xC4) | (CC[lo[0]] << 3), v & 0xFF, v >> 8))
        if mnem in ("jr", "djnz"):
            target = ev(ops[-1])
            d = target - (pc + 2)
            if strict and not -128 <= d <= 127:
                raise AsmError(f"jr out of range at {pc:#x}")
            if mnem == "djnz":
                return bytes((0x10, d & 0xFF))
            if len(ops) == 1:
                return bytes((0x18, d & 0xFF))
            return bytes((0x20 | (CC[lo[0]] << 3), d & 0xFF))
        if mnem in ("push", "pop"):
            if lo[0] in IDX:
                return bytes((IDX[lo[0]], 0xE5 if mnem == "push" else 0xE1))
            return bytes(((0xC5 if mnem == "push" else 0xC1) | (R16AF[lo[0]] << 4),))
        if mnem == "ex":
            if lo == ["de", "hl"]:
                return b"\xeb"
            if lo == ["af", "af'"]:
                return b"\x08"
            if lo == ["(sp)", "hl"]:
                return b"\xe3"
            if lo[0] == "(sp)" and lo[1] in IDX:
                return bytes((IDX[lo[1]], 0xE3))
        if mnem in ("inc", "dec"):
            o = lo[0]
            if o in R16 or o in IDX:
                if o in IDX:
                    return bytes((IDX[o], 0x23 if mnem == "inc" else 0x2B))
                return bytes(((0x03 if mnem == "inc" else 0x0B) | (R16[o] << 4),))
            ix = idx(ops[0])
            if ix:
                return bytes((ix[0], 0x34 if mnem == "inc" else 0x35, ix[1]))
            return bytes(((0x04 if mnem == "inc" else 0x05) | (R8[o] << 3),))
        if mnem in ALU:
            # add hl,rr / adc hl,rr / sbc hl,rr / add ix,rr
            if len(ops) == 2 and lo[0] in ("hl", "ix", "iy") and mnem in ("add", "adc", "sbc"):
                if lo[0] == "hl":
                    if mnem == "add":
                        return bytes((0x09 | (R16[lo[1]] << 4),))
                    return bytes((0xED, (0x4A if mnem == "adc" else 0x42) | (R16[lo[1]] << 4)))
                rr = {"bc": 0, "de": 1, lo[0]: 2, "sp": 3}[lo[1]]
                return bytes((IDX[lo[0]], 0x09 | (rr << 4)))
            src = ops[-1]
            s = src.lower()
            if s in R8:
                return bytes((0x80 | (ALU[mnem] << 3) | R8[s],))
            ix = idx(src)
            if ix:
                return bytes((ix[0], 0x86 | (ALU[mnem] << 3), ix[1]))
            return bytes((0xC6 | (ALU[mnem] << 3), ev(src) & 0xFF))
        if mnem in ROT:
            ix = idx(ops[0])
            if ix:
                return bytes((ix[0], 0xCB, ix[1], (ROT[mnem] << 3) | 6))
            return bytes((0xCB, (ROT[mnem] << 3) | R8[lo[0]]))
        if mnem in ("bit", "set", "res"):
            n = ev(ops[0]) & 7
            base = {"bit": 0x40, "res": 0x80, "set": 0xC0}[mnem]
            ix = idx(ops[1])
            if ix:
                return bytes((ix[0], 0xCB, ix[1], base | (n << 3) | 6))
            return bytes((0xCB, base | (n << 3) | R8[lo[1]]))
        if mnem == "in":
            if lo[1] == "(c)":
                return bytes((0xED, 0x40 | (R8[lo[0]] << 3)))
            return bytes((0xDB, ev(ops[1].strip("()")) & 0xFF))
        if mnem == "out":
            if lo[0] == "(c)":
                return bytes((0xED, 0x41 | (R8[lo[1]] << 3)))
            return bytes((0xD3, ev(ops[0].strip("()")) & 0xFF))
        if mnem == "ld":
            d, s = ops
            dl, sl = lo
            # 16-bit loads
            if dl in R16 or dl in IDX:
                if sl.startswith("(") and sl.endswith(")") and sl not in ("(hl)",):
                    v = ev(s.strip("()"))
                    if dl == "hl":
                        return bytes((0x2A, v & 0xFF, v >> 8))
                    if dl in IDX:
                        return bytes((IDX[dl], 0x2A, v & 0xFF, v >> 8))
                    return bytes((0xED, 0x4B | (R16[dl] << 4), v & 0xFF, v >> 8))
                if dl == "sp" and sl in ("hl", "ix", "iy"):
                    return b"\xf9" if sl == "hl" else bytes((IDX[sl], 0xF9))
                v = ev(s)
                if dl in IDX:
                    return bytes((IDX[dl], 0x21, v & 0xFF, v >> 8))
                return bytes((0x01 | (R16[dl] << 4), v & 0xFF, v >> 8))
            if dl.startswith("(") and dl.endswith(")") and dl not in R8 and not idx(d):
                if dl in ("(bc)", "(de)") and sl == "a":
                    return bytes((0x02 if dl == "(bc)" else 0x12,))
                v = ev(d.strip("()"))
                if sl == "a":
                    return bytes((0x32, v & 0xFF, v >> 8))
                if sl == "hl":
                    return bytes((0x22, v & 0xFF, v >> 8))
                if sl in IDX:
                    return bytes((IDX[sl], 0x22, v & 0xFF, v >> 8))
                return bytes((0xED, 0x43 | (R16[sl] << 4), v & 0xFF, v >> 8))
            if dl == "a" and sl in ("(bc)", "(de)"):
                return bytes((0x0A if sl == "(bc)" else 0x1A,))
            if dl == "a" and sl in ("i", "r"):
                return bytes((0xED, 0x57 if sl == "i" else 0x5F))
            if dl in ("i", "r") and sl == "a":
                return bytes((0xED, 0x47 if dl == "i" else 0x4F))
            if dl == "a" and sl.startswith("(") and sl not in R8 and not idx(s):
                v = ev(s.strip("()"))
                return bytes((0x3A, v & 0xFF, v >> 8))
            ixd = idx(d)
            ixs = idx(s)
            if ixd:
                if sl in R8 and sl != "(hl)":
                    return bytes((ixd[0], 0x70 | R8[sl], ixd[1]))
                return bytes((ixd[0], 0x36, ixd[1], ev(s) & 0xFF))
            if ixs:
                return bytes((ixs[0], 0x46 | (R8[dl] << 3), ixs[1]))
            if dl in R8 and sl in R8:
                return bytes((0x40 | (R8[dl] << 3) | R8[sl],))
            if dl in R8:
                return bytes((0x06 | (R8[dl] << 3), ev(s) & 0xFF))
        raise AsmError(f"unsupported z80 instruction: {mnem} {args}")


def assemble(source: str, origin: int = 0) -> tuple[bytes, dict[str, int]]:
    return AssemblerZ80().assemble(source, origin)
