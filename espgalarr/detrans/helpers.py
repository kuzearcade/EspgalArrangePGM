"""Arika's native-convention helpers, mapped back to 68k code.

The Arrange build factors some repeated 68k fragments into MIPS helpers that take constants
in $a0-$a3 and return a flag in $v0 (the arcade code has the fragment inline). A call site

    jsr   F_<helper>        ; mips-args a0=$k0,a1=$k1,...
    tst.l RET_V0
    bne   L_x               (or beq)

is replaced by the helper de-translated with $a0-$a3 bound to the call's constants
(specialisation): exits returning 1 branch to L_x (bne) and exits returning 0 continue after
the call (the other way round for beq). Labels are renamed per call site.

Other helpers the translator outlined (any translated routine called with constant
$a0-$a3, e.g. `jsr F_x ; mips-args a0=$481108,a1=$6dc5c0`: a table lookup reading d2
through its register-file slot) are inlined the same way when the caller does not test
$v0: each exit continues after the call. The specialised body must be fully recognised,
not address memory through a7 and make no native call of its own.
"""
from __future__ import annotations

import re

from . import core

# helper -> (size, description, main overlay holding it)
_OFFSCREEN = ("off-screen check with margins $a0-$a3: removes the object and returns 1 when it "
              "has left the screen after having been on it")
HELPERS = {
    0x78C920: (0x990, _OFFSCREEN, "A0A9"),      # Arrange
    0x823FC0: (0x990, _OFFSCREEN, "A0A8"),      # the arcade mode's copy
}

# routines the helpers call -> their 68k original. 0x786690 (arcade copy 0x81df70) is the
# translation of $14f0d8 "remove the object" (movea.l 6(a5),a6 ; moveq #1,d0 ; move.w 4(a5),d1
# ; loop over the records) with the record loop moved out to 0x7867d0 for the 0x2C records; in
# the 0x2C build the loop's stride at $14f0e4 is patched (data/enemy_record_sites.json "steps")
CALLEES = {0x786690: 0x14F0D8, 0x81DF70: 0x14F0D8}


def reader(*overlays):
    """read_word over several overlays (a stage overlay and its main overlay)."""
    import struct

    def read_word(addr: int):
        for ov in overlays:
            if ov.contains(addr) and ov.contains(addr + 3):
                return struct.unpack("<I", ov.read(addr, 4))[0]
        return None
    return read_word


def main_overlay(name: str):
    """The (cached) main overlay that holds the helpers of one mode."""
    from ..ps2.overlay import Overlay
    from .port import WORK
    if name not in _MAIN:
        _MAIN[name] = Overlay(WORK / "ps2" / "disc" / "A0A0" / name)
    return _MAIN[name]


_MAIN: dict = {}

_CALL = re.compile(r"^\s+jsr\s+F_([0-9a-f]+)\s*;.*mips-args (\S+)")
_TST = re.compile(r"^\s+tst\.l\s+RET_V0\s*(;.*)?$")
_BR = re.compile(r"^\s+(bne|beq)\s+(L_[0-9a-f]+)\s*(;.*)?$")
_LABEL = re.compile(r"\bL_([0-9a-f]+)\b")


def specialise(helper: int, args: dict[int, int], read_code, read_word, amap, size: int | None = None,
               flag: bool = True) -> list | None:
    """The helper's instructions with $a0-$a3 bound, or None if something is not recognised
    (with `flag`: if an exit's $v0 is not a constant 0/1)."""
    size = HELPERS[helper][0] if size is None else size
    res = core.detranslate(read_code(helper, size), helper, amap, read_word=read_word, entry_regs=args)
    out = []
    for m in res.insns:
        if m.is_label and m.addr == helper:
            continue
        if not m.is_label and not m.mnem:
            if m.is_comment:
                continue
            return None                         # something the de-translator did not recognise
        if m.mnem == "rts" and flag:
            v0 = getattr(m, "ret_v0", None)
            if v0 is None or v0[0] != "c" or v0[1] not in (0, 1):
                return None
        if not flag and not m.is_label:
            t = m.text()
            if getattr(m, "args", None) or "RET_V0" in t or "ARG_A" in t or re.search(r"\ba7\b", t) \
                    or m.mnem in ("rte", "jmp", "bra") and "F_" in t \
                    or any(f"L_{helper:x}" == o for o in m.ops):
                return None                     # native calls, stack, tail calls, loops to entry
        out.append(m)
    return out


def const_args(args: dict | str | None) -> dict[int, int] | None:
    """{MIPS register: value} of a call's arguments when all are constants, else None
    (args: an instruction's `args` {"a0": "$k"} or the text `a0=$k,a1=$k`)."""
    if not args:
        return None
    if isinstance(args, str):
        args = dict(kv.split("=", 1) for kv in args.split(","))
    out = {}
    for k, v in args.items():
        if not (k[:1] == "a" and k[1:] in "0123" and v.startswith("$")):
            return None
        out[4 + int(k[1])] = int(v[1:], 16)
    return out


def inline(text: str, read_code, read_word, amap, outlined=None) -> str:
    """Replace mapped helper calls in de-translated text (see the module docstring).
    outlined(addr, amap) -> (size, address map for its body) of a translated routine to inline
    when called with constant arguments, or None (no `outlined`: only the HELPERS)."""
    lines = text.splitlines()
    out, i, site = [], 0, 0
    while i < len(lines):
        m = _CALL.match(lines[i])
        tgt = int(m.group(1), 16) if m else None
        how = None if tgt is None else (HELPERS[tgt][0], amap) if tgt in HELPERS else outlined(tgt, amap) if outlined else None
        if how is None:
            out.append(lines[i])
            i += 1
            continue
        args = const_args(m.group(2))
        flag = tgt in HELPERS
        if flag:
            tst = i + 2 < len(lines) and _TST.match(lines[i + 1])
            br = tst and _BR.match(lines[i + 2])
            body = specialise(tgt, args, read_code, read_word, amap) if args is not None and br else None
        else:
            body = specialise(tgt, args, read_code, read_word, how[1], how[0], flag=False) if args is not None else None
        if body is None:
            out.append(lines[i])
            i += 1
            continue
        site += 1
        pre = f"L_{site:02x}ff"                 # hex only (labels are compared as L_<hex>)
        cont = f"{pre}fffffe"
        if flag:
            on1, on0 = (br.group(2), cont) if br.group(1) == "bne" else (cont, br.group(2))
            on1_rts = _is_rts_block(lines, on1)
            out.append(f"\t; {HELPERS[tgt][1].split(':')[0]} ({m.group(2)}), helper {tgt:#x} inlined")
        else:
            out.append(f"\t; helper {tgt:#x} ({m.group(2)}) inlined")
        for k, b in enumerate(body):
            if b is None:
                continue
            t = _line(b)
            nxt = body[k + 1] if k + 1 < len(body) else None
            if flag and b.mnem == "jsr" and on1_rts and nxt is not None and nxt.mnem == "rts" and nxt.ret_v0[1] == 1:
                out.append(f"\tjmp\t{b.ops[0]}")     # the object is removed: return (as the arcade)
                body[k + 1] = None
            elif b.is_label:
                out.append(_LABEL.sub(lambda mm: pre + mm.group(1), t))
            elif b.mnem == "rts":
                out.append(f"\tbra\t{(on1 if b.ret_v0[1] == 1 else on0) if flag else cont}")
            else:
                out.append(_LABEL.sub(lambda mm: pre + mm.group(1), t))
        if out[-1] == f"\tbra\t{cont}":
            out.pop()                           # falls through to the label anyway
        out.append(f"{cont}:")
        i += 3 if flag else 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


class ChainMap:
    """An address map that asks a second map where the first knows nothing (a stage
    overlay's map, then the main overlay's for a helper living there)."""

    def __init__(self, first, second):
        self.first, self.second = first, second

    def __getattr__(self, name):
        return getattr(self.first, name)

    def map_const(self, v: int):
        t = self.first.map_const(v)
        return t if t[0] is not None else self.second.map_const(v)


def _line(m) -> str:
    """An instruction's text with its confidence note (as core.Result.text writes it: the
    porter tells exact from extrapolated addresses by it)."""
    t = m.text()
    if m.mnem and not m.is_label and m.conf < 0.95:
        t = f"{t:40s}; conf {m.conf:.2f}" + (f" {m.note}" if m.note else "")
    return t


def _is_rts_block(lines: list[str], label: str) -> bool:
    """Is `label:` in lines followed (after other labels) by a plain rts?"""
    for j, l in enumerate(lines):
        if l.strip() == f"{label}:":
            for l2 in lines[j + 1:]:
                c = l2.split(";", 1)[0].strip()
                if not c or c.endswith(":"):
                    continue
                return c == "rts"
    return False


def inline_result(res, read_code, read_word, amap, outlined=None):
    """inline() on a core.Result (for the arcade-twin comparison): the instructions of the
    inlined text, re-parsed."""
    import dataclasses
    from .templates import M68
    text = res.text(header=False)
    new = inline(text, read_code, read_word, amap, outlined)
    if new == text:
        return res
    for k, v in CALLEES.items():
        new = new.replace(f"F_{k:x}", f"${v:x}.l")
    insns = []
    for line in new.splitlines():
        code = line.split(";", 1)[0].rstrip()
        if not code.strip():
            continue
        if code.endswith(":") and not code[0].isspace():
            lab = M68("", "", [], 1.0, 0, is_label=True)
            lab.label_text = code[:-1]
            insns.append(lab)
            continue
        parts = code.split(None, 1)
        mn, ops = parts[0], _split_ops(parts[1]) if len(parts) > 1 else []
        mnem, _, size = mn.partition(".")
        insns.append(M68(mnem, size, ops, 1.0, 0))
    return dataclasses.replace(res, insns=insns)


def _split_ops(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out
