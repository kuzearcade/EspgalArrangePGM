"""Turn recovered Arrange routines (work/ps2/detrans/arrange_*.s) into PGM 68k source.

A recovered routine prints RAM operands as Arrange addresses and calls as `F_<ps2 address>`.
Porting it means:
  * RAM operands -> PGM addresses: the region map learned by ramxlate.py, plus overrides for
    areas the port placed itself (bullet pool, walker variables, sprite lists);
  * calls -> the 68k routine the PS2 function translates (address map), another ported routine
    (emitted in the same unit), or a known 68k equivalent of a native PS2 service;
  * nothing unknown left (`; ???` lines, unmapped calls or addresses) -- otherwise the routine is
    reported with the reasons and not ported.
The output is plain asm68k source (no original bytes), assembled into free program ROM by the
build like asm/m68k/*.s.
"""
from __future__ import annotations

import bisect
import collections
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ramxlate import RamXlate

OUT = Path("work") / "ps2" / "detrans"
ROM_HI = 0x200000
_EMPTY = bytes.fromhex("0000000000000000")     # jr $ra ; nop
# what a ported absolute address may be on the PGM (+ the enhanced machine): program ROM and
# BIOS, work RAM, video RAM, palette, video registers, sound/inputs/Z80, cart ARM RAM, the
# enhanced RAM; anything else is a PS2 address the map did not translate
PGM_SPACE = ((0x000000, 0x400004), (0x800000, 0x830000), (0x900000, 0x910000), (0xA00000, 0xA10000),
             (0xB00000, 0xB10000), (0xC00000, 0xC20000), (0xD00000, 0xD20000), (0xE00000, 0xE20000))
_ABS = re.compile(r"(?<![#\w])\$([0-9a-f]+)(?:\.[wl])?")


# Arrange-only RAM placed by the port (layout stage), keyed by the de-translator's printed
# (extrapolated) Arrange address. Bullet variables: Arrange block 0x81D4C6.. = arcade 0x81B552..
# except the walker group count/delta, which the port keeps in their own words.
ARRANGE_OVERRIDES = {
    0x81D4C6: 0x81B552, 0x81D4C8: 0x81B554, 0x81D4CC: 0x81B558, 0x81D4CD: 0x81B559,
    0x81D4CE: 0x818352, 0x81D4D6: 0x818354, 0x81D4D7: 0x818355, 0x81D4D8: 0x81B564,
    0x81E496: 0xD00000, 0x81E498: 0xD00002,
}


# PS2-only flags with a fixed value where the 68k program runs the code testing them.
# 0x60eb08: the PS2 enemy spawner (Arrange 0x783f10, arcade 0x81bb..) calls a new enemy's
# routine with it set, then again with it clear. The PS2 merged two 68k routines there: with
# the flag set the routine only sets its state (`move.w #k,4(a5) ; rts`, the 68k routine at
# the region start), with it clear it runs the init (the 68k code right after that rts,
# reached through its own pointer): the port folds the flag as set and enters the init from
# there (overlay_units). Anywhere else (a ported callee, run in normal play) it is clear.
PS2_FLAGS = {0x60EB08: 1}            # (its value at the start of a unit's own routine; 0 elsewhere)
_FLAG_TST = re.compile(r"^\s+tst\.[bwl]\s+\$([0-9a-f]+)(?:\.l)?\s*(;.*)?$")
_FLAG_BR = re.compile(r"^\s+(beq|bne)\s+(L_[0-9a-f]+)\s*(;.*)?$")


def _fold_ps2_flags(text: str, root: bool = True) -> str:
    """`tst <PS2 flag> ; beq/bne L` with the flag's fixed value (PS2_FLAGS in a unit's own
    routine, 0 in a callee): the branch taken (bra) or not (nothing). Any other use stays
    (and is refused as outside the PGM's address space)."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        t = _FLAG_TST.match(lines[i])
        b = _FLAG_BR.match(lines[i + 1]) if t and i + 1 < len(lines) else None
        if b and int(t.group(1), 16) in PS2_FLAGS:
            value = PS2_FLAGS[int(t.group(1), 16)] if root else 0
            if (b.group(1) == "beq") == (value == 0):
                out.append(f"\tbra\t{b.group(2)}\t; PS2-only flag ${t.group(1)} is {value}")
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


@dataclass
class Ported:
    fn: int
    text: str = ""
    callees: set = field(default_factory=set)      # PS2 functions that must be ported too
    problems: list = field(default_factory=list)


_SAVE = re.compile(r"^\s+move\.l\s+([da][0-7]),(-(\d+))?\(a7\)\s*(;.*)?$")
_ADJ = re.compile(r"^\s+(lea|subq|suba|subi|adda)(?:\.[wl])?\s+(-?\d+\(a7\)|#-?\$?[0-9a-f]+),a7\s*(;.*)?$")


def _stack_adjust(line: str) -> int | None:
    """K for an instruction that lowers a7 by K (lea -K(a7) / subq|suba #K / adda #-K)."""
    m = _ADJ.match(line)
    if not m:
        return None
    mn, op = m.group(1), m.group(2)
    if mn == "lea":
        k = -int(op.split("(")[0])
    else:
        neg = op.startswith("#-")
        digits = op.lstrip("#-")
        v = int(digits[1:], 16) if digits.startswith("$") else int(digits)
        if v >= 0x80000000:
            v -= 1 << 32
        if neg:
            v = -v
        k = -v if mn == "adda" else v
    return k if k > 0 else None


_NO_ACCESS = ("lea", "pea", "jsr", "jmp", "bsr", "bra", "movem")


def _odd_access(code: str) -> bool:
    """Does a .w/.l instruction read or write memory at an odd displacement/address?"""
    m = re.match(r"^\s+(\w+)\.([wl])\s+(.*)$", code)
    if not m or m.group(1) in _NO_ACCESS or m.group(1).startswith(("b", "db")) and m.group(1) not in ("btst", "bset", "bclr", "bchg"):
        return False
    for op in _split_ops(m.group(3)):
        d = re.match(r"^(-?\d+)\(a[0-7]\)$", op)
        if d and int(d.group(1)) & 1:
            return True
        a = re.match(r"^\$([0-9a-f]+)\.[wl]$", op)
        if a and int(a.group(1), 16) & 1:
            return True
    return False


def fix_stack_saves(lines: list[str]) -> list[str]:
    """`move.l rN,-k(a7)` ... `lea -K(a7),a7` (or subq/suba #K / adda #-K) stores below the
    stack pointer before moving it,
    which an interrupt would clobber. Move the adjustment first and rebase the stores
    (a bare `(a7)` store in such a run is the lowest slot, -K)."""
    out, i = [], 0
    while i < len(lines):
        j = i
        while j < len(lines) and _SAVE.match(lines[j]):
            j += 1
        k_total = _stack_adjust(lines[j]) if j < len(lines) and j > i else None
        if k_total:
            out.append(f"\tlea\t-{k_total}(a7),a7")
            for line in lines[i:j]:
                mm = _SAVE.match(line)
                off = -int(mm.group(3)) if mm.group(2) else -k_total
                out.append(f"\tmove.l\t{mm.group(1)},{off + k_total}(a7)")
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    return out


class NewVars:
    """Fresh storage for Arrange-only RAM (extrapolated addresses with no arcade counterpart).
    Addresses within `gap` bytes form a cluster that keeps its internal layout."""

    def __init__(self, base: int, size: int, gap: int = 0x40):
        self.base, self.size, self.gap = base, size, gap
        self.addrs: set[int] = set()
        self.map: dict[int, int] = {}

    def want(self, a: int) -> None:
        self.addrs.add(a)

    def assign(self) -> None:
        nxt = self.base
        cl: list[int] = []
        for a in sorted(self.addrs) + [None]:
            if cl and (a is None or a - cl[-1] > self.gap):
                lo = cl[0] & ~1
                for x in cl:
                    self.map[x] = nxt + (x - lo)
                nxt += ((cl[-1] - lo + 4) + 3) & ~3
                cl = []
            if a is not None:
                cl.append(a)
        assert nxt <= self.base + self.size, "Arrange-only variables overflow their area"
        self.end = nxt


class Porter:
    def __init__(self, ram: RamXlate, func_map: dict[int, int], native: dict[int, int] | None = None,
                 src_dir: Path = OUT):
        self.ram = ram
        self.func_map = func_map            # PS2 function -> 68k routine
        self.native = native or {}          # PS2 native service -> 68k routine
        self.src_dir = src_dir
        self.newvars: NewVars | None = None
        self.sprites: tuple | None = None   # (sprite number table, 68k sprite routines)
        self.is_empty = None                # PS2 function -> is it an empty stub (`jr $ra`)?

    @classmethod
    def load(cls, overrides: dict[int, int] | None = None) -> "Porter":
        if overrides is None:
            overrides = ARRANGE_OVERRIDES
        amap = json.loads((OUT / "addrmap_arrange.json").read_text())
        from .verify import FUNC_FIXES
        funcs = {**{int(k, 16): int(v, 16) for k, v in amap["funcs"].items()}, **FUNC_FIXES}
        summ = OUT / "summary.json"
        if summ.exists():           # unchanged Arrange functions = their 68k original
            for k, v in json.loads(summ.read_text())["funcs"].items():
                if v.get("m68k") is not None:
                    funcs.setdefault(int(k, 16), int(v["m68k"]))
        from .helpers import CALLEES
        for k, v in CALLEES.items():        # what Arika's helpers call, as 68k routines
            funcs.setdefault(k, v)
        native = {}
        nsm = OUT / "native_service_map.json"
        if nsm.exists():
            for k, v in json.loads(nsm.read_text()).items():
                if v.get("m68k_helper"):
                    native[int(k, 16)] = int(v["m68k_helper"], 16)
        return cls(RamXlate.load(overrides=overrides), funcs, native)

    def source(self, fn: int) -> str | None:
        for name in (f"arrange_{fn:x}.s", f"elf_{fn:x}.s"):
            p = self.src_dir / name
            if p.exists():
                return p.read_text()
        return None

    def _addr(self, v: int, problems: list, extrapolated: bool) -> int:
        if v < ROM_HI:
            return v
        if not extrapolated:
            return v            # exact/interpolated arcade sample: same variable in both builds
        t = self.ram(v)
        if t is not None:
            return t
        if self.newvars is not None and (0x800000 <= v < 0x830000 or v >= BSS_KEYS):
            if v in self.newvars.map:
                return self.newvars.map[v]
            self.newvars.want(v)
            problems.append(f"new variable {v:#x} not yet assigned")
            return v
        if 0x800000 <= v < 0x820000:
            problems.append(f"RAM {v:#x} not mapped")
        else:
            # (an extrapolated address in the hardware range is a PS2 variable the map does
            # not know, not hardware: the translated code reaches the hardware through services)
            problems.append(f"address {v:#x} not mapped")
        return v

    def port(self, fn: int, local: set[int] | None = None, root: bool = False) -> Ported:
        """Port one function. `local` = PS2 functions emitted in the same unit (called by label)."""
        local = local or set()
        res = Ported(fn)
        src = self.source(fn)
        if src is None:
            res.problems.append("no recovered source")
            return res
        out = []
        src = _fold_ps2_flags(src, root)
        for line in src.splitlines():
            code = line.split(";", 1)[0].rstrip()
            note = line.split(";", 1)[1] if ";" in line else ""
            if not code.strip():
                if "???" in note:
                    res.problems.append("unknown: " + note.strip()[:80])
                continue
            m = re.match(r"^\s+(jsr|jmp|bsr|bra)\s+F_([0-9a-f]+)\s*$", code)
            if m and m.group(1) in ("jsr", "bsr") and self.is_empty is not None and self.is_empty(int(m.group(2), 16)):
                continue                        # a call of an empty stub does nothing
            if m:
                tgt = int(m.group(2), 16)
                if "mips-args" in note and tgt not in self.native:
                    # the callee reads values passed in MIPS registers (a code pointer for a
                    # helper, a service argument): the 68k call does not carry them
                    res.problems.append(f"call {tgt:#x} passes {note.split('mips-args', 1)[1].strip()} natively")
                op = "jsr" if m.group(1) in ("jsr", "bsr") else "jmp"
                if tgt in local or tgt == fn:
                    out.append(f"\t{op}\tF_{tgt:x}")
                    res.callees.add(tgt)
                elif tgt in self.func_map:
                    out.append(f"\t{op}\t${self.func_map[tgt]:x}.l")
                elif tgt in self.native:
                    out.append(f"\t{op}\t${self.native[tgt]:x}.l")
                else:
                    out.append(f"\t{op}\tF_{tgt:x}")
                    res.callees.add(tgt)
                continue

            if _odd_access(code):
                # the 68000 faults on a word/long access at an odd address (the PS2 does not)
                res.problems.append("word/long access at an odd address: " + code.strip()[:60])
            if "ARG_A" in code:
                res.problems.append("uses a MIPS argument the routine is entered with: " + code.strip()[:60])
            if "RET_V0" in code:
                res.problems.append("uses a native routine's return value ($v0): " + code.strip()[:60])
            extrap = "conf 0.60" in note or "conf 0.30" in note
            guessed = extrap or "conf 0.90" in note

            def sub(mm):
                v = int(mm.group(2), 16)
                if v < ROM_HI and guessed and not mm.group(1):
                    # a program-ROM address the map only interpolated: a table the PS2 laid
                    # out differently (the translation of a pointer table) is off
                    res.problems.append(f"ROM address {v:#x} only guessed: " + code.strip()[:60])
                return f"{mm.group(1)}${self._addr(v, res.problems, extrap):x}{mm.group(3)}"
            code = re.sub(r"(#?)\$([0-9a-f]{6,8})(\.l|\b)", sub, code)
            if code.split()[0].startswith("dc."):
                out.append(code + (f"\t;{note}" if "sprite $" in note else ""))
                continue                        # (a table the port emits: data, not addresses)
            for mm in _ABS.finditer(code):
                v = int(mm.group(1), 16)
                if not any(lo <= v < hi for lo, hi in PGM_SPACE):
                    res.problems.append(f"address {v:#x} outside the PGM's: " + code.strip()[:60])
            out.append(code)
        if self.sprites is not None:
            # PS2 sprite numbers back to the PGM graphics addresses (detrans/sprite_ids.py)
            from . import sprite_ids
            out = sprite_ids.replace(out, *self.sprites, res.problems)
        res.text = "\n".join(fix_stack_saves(out)) + "\n"
        return res

    def port_unit(self, roots: list[int], max_funcs: int = 64) -> tuple[str, dict[int, Ported]]:
        """Port roots plus every unmapped PS2 callee they reach (one asm unit)."""
        todo, done = list(roots), {}
        while todo and len(done) < max_funcs:
            f = todo.pop()
            if f in done:
                continue
            done[f] = None
            r = self.port(f, local=set(done) | set(todo), root=f in roots)
            done[f] = r
            for c in r.callees:
                if c not in done:
                    todo.append(c)
        units = set(done)
        # re-port with the final local set so every intra-unit call uses its label
        res = {f: self.port(f, local=units, root=f in roots) for f in units}
        return "\n".join(res[f].text for f in sorted(res)), res


if __name__ == "__main__" and "--plan" not in __import__("sys").argv:
    import collections
    import sys
    pt = Porter.load()
    summ = json.loads((OUT / "summary.json").read_text())["funcs"]
    reasons = collections.Counter()
    ok = []
    for k, v in summ.items():
        if v.get("kind") not in ("near", "new"):
            continue
        fn = int(k, 16)
        r = pt.port(fn)
        if r.problems:
            reasons[r.problems[0].split(" ")[0]] += 1
        else:
            ok.append((fn, v["kind"], len(r.callees)))
    print(f"translatable on their own: {len(ok)} "
          f"(near {sum(1 for x in ok if x[1] == 'near')}, new {sum(1 for x in ok if x[1] == 'new')}); "
          f"first blocking reason: {dict(reasons)}")
    if "-v" in sys.argv:
        for fn, kind, nc in ok:
            print(f"  {fn:#x} {kind} callees-to-port {nc}")


def plan(newvar_base: int = 0xE09800, newvar_size: int = 0x6800, max_funcs: int = 64):
    """Port every changed Arrange routine that has a 68k original, with its callee closure.
    Returns (units, porter): units = {68k original: (asm text, ported dict)} for the clean ones,
    and the reasons for the rest."""
    pt = Porter.load()
    pt.newvars = NewVars(newvar_base, newvar_size)
    rep = json.loads((OUT / "port_report.json").read_text())
    roots = {}
    for r in rep:
        if r["m68k"] and r["structural"]:
            roots.setdefault(r["arrange_fn"], r["m68k"])
    # pass 1: collect Arrange-only variables over every candidate unit
    for fn in roots:
        pt.port_unit([fn], max_funcs)
    pt.newvars.assign()
    units, failed = {}, {}
    for fn, orig in roots.items():
        text, res = pt.port_unit([fn], max_funcs)
        probs = [f"{f:#x}: {p}" for f, r in res.items() for p in r.problems]
        if probs or len(res) >= max_funcs:
            failed[fn] = probs[:3] or ["closure too large"]
        else:
            units[orig] = (fn, text, res)
    return units, failed, pt


def _main_plan():
    import collections
    units, failed, pt = plan()
    print(f"portable units: {len(units)}, failed: {len(failed)}, Arrange-only variables: "
          f"{len(pt.newvars.map)} ({pt.newvars.end - pt.newvars.base:#x} bytes)")
    c = collections.Counter(v[0].split(": ", 1)[1].split(" ")[0] for v in failed.values())
    print("first failure kinds:", dict(c))

if __name__ == "__main__" and "--plan" in __import__("sys").argv:
    _main_plan()


_ENTRY_CACHE: dict = {}


def _code_refs():
    """(sorted branch/call targets with their sources, set of code-pointer constants) of the ROM."""
    if "refs" in _ENTRY_CACHE:
        return _ENTRY_CACHE["refs"]
    import bisect
    import struct
    from ..pgm import rom as rom_mod
    from .port import WORK
    tg, starts, insns = [], set(), {}
    for line in (WORK / "pgm" / "dis" / "p_rom.lst").read_text().splitlines():
        mm = re.match(r"^([0-9a-f]{6})\s+(\S+)\s+(.*)$", line)
        if mm:
            starts.add(int(mm.group(1), 16))
            insns[int(mm.group(1), 16)] = (len(mm.group(2)) // 2, mm.group(3))
        m = re.match(r"^([0-9a-f]{6})\s+\S+\s+(b\w+|jsr|jmp|db\w+)\s+.*?\$([0-9a-f]+)(\.[wl])?\s*$", line)
        if m:
            tg.append((int(m.group(3), 16), int(m.group(1), 16)))
    tg.sort()
    p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
    cand = {}
    for i in range(0, len(p) - 3, 2):
        v = struct.unpack_from(">I", p, i)[0]
        if 0x100000 <= v < 0x200000 and v % 2 == 0 and v in starts:
            cand[i] = v
    # a code pointer is an even instruction start that is an instruction's operand (lea, pea,
    # move.l #) or one of a table of pointers (a neighbouring long is one too: jump tables in
    # the code, handler tables in the data). Other such values are instruction fragments (an
    # `ori.b #k,(aN)` opcode is $001x) or halves of (word, word) records in the data tables
    ordered = sorted(starts)
    ptrs = set()
    for i, v in cand.items():
        if i - 4 in cand or i + 4 in cand:
            ptrs.add(v)
            continue
        k = bisect.bisect_right(ordered, i) - 1
        if k >= 0 and ordered[k] < i < ordered[k] + insns[ordered[k]][0] and f"${v:x}" in insns[ordered[k]][1]:
            ptrs.add(v)
    _ENTRY_CACHE["refs"] = (tg, [t for t, _ in tg], ptrs)
    _ENTRY_CACHE["values"] = set(cand.values())
    return _ENTRY_CACHE["refs"]


def _branch_targets() -> frozenset:
    """68k addresses reached by a branch/call or a code pointer."""
    if "targets" not in _ENTRY_CACHE:
        tg, keys, ptrs = _code_refs()
        _ENTRY_CACHE["targets"] = frozenset(keys) | frozenset(ptrs)
    return _ENTRY_CACHE["targets"]


def _region_exit(text: str, end: int, index, start_bounded: bool, root: int | None = None,
                 continues=None) -> str | None:
    """The 68k region does not end in a terminator (the 68k continues at `end`). An `rts` in
    the ported routine is right when it follows a call of the continuation (`jsr $end`, or
    of X when the 68k has `jmp X`/`bra X` at `end`, or of a ported function F with
    continues(F, those addresses): F stands for the 68k code there). Otherwise the PS2
    routine is the translator's outlined piece of a longer 68k routine (its caller continues
    after the call): when the region is entered by falling through, its returns continue at
    `end`; a called region that returns there is refused (None). Only the returns of the routine `root` (and of what it jumps
    to) leave the region: a ported callee returns to its caller."""
    import re as _re
    # where the 68k goes from `end`: falling into the next instructions, or a jmp/bra target
    # within a few instructions (the alignment may stop just short of the routine's tail)
    ok_targets = {end}
    pos = index.pos_of(end)
    if pos is not None and index.ks[pos].addr == end:
        for k in index.ks[pos:pos + 4]:
            ok_targets.add(k.addr)
            mm_ = _re.fullmatch(r"\$([0-9a-f]+)(\.l)?", k.ops.strip()) if k.canon.mnem in ("jmp", "bra") else None
            if mm_:
                ok_targets.add(int(mm_.group(1), 16))
            if k.canon.mnem in ("jmp", "bra", "rts", "rte"):
                break
    lines, out, prev = text.splitlines(), [], ""
    exits = None
    if root is not None:
        # the functions whose returns leave the region: root and those it jumps to
        body: dict[int, list] = {}
        cur = None
        for line in lines:
            m = _re.match(r"^F_([0-9a-f]+):", line)
            if m:
                cur = int(m.group(1), 16)
            body.setdefault(cur, []).append(line)
        exits, todo = set(), [root]
        while todo:
            f = todo.pop()
            if f in exits:
                continue
            exits.add(f)
            for line in body.get(f, ()):
                m = _re.match(r"^\s+(?:jmp|bra)\s+F_([0-9a-f]+)\s*(;.*)?$", line)
                if m:
                    todo.append(int(m.group(1), 16))
    cur = None
    for line in lines:
        m = _re.match(r"^F_([0-9a-f]+):", line)
        if m:
            cur = int(m.group(1), 16)
        code = line.split(";", 1)[0].strip()
        if code == "rts" and (exits is None or cur in exits):
            mm = _re.match(r"(?:jsr|bsr)\s+\$([0-9a-f]+)", prev)
            mt = _re.match(r"(?:jsr|bsr)\s+F_([0-9a-f]+)$", prev)
            if not (mm and int(mm.group(1), 16) in ok_targets
                    or mt and continues is not None and continues(int(mt.group(1), 16), ok_targets)):
                if start_bounded:
                    return None
                out.append(f"\tjmp\t${end:x}.l\t; outlined: returns continue at the region end")
                prev = ""
                continue
        if code:
            prev = code
        out.append(line)
    return "\n".join(out) + "\n"


def _only_immediates_differ(rec, k68) -> bool:
    """Do an aligned arcade-twin instruction and its 68k original differ only in immediate
    constants?  (A differing address or displacement means the address/record mapping
    disagrees there, and the Arrange text would be mapped the same wrong way.)"""
    from .verify import recovered_canon
    a, b = recovered_canon(rec), k68.canon
    if a is None:
        return False
    ma, _, oa = a.text.partition(" ")
    mb, _, ob = b.text.partition(" ")
    if ma != mb:
        return False
    xa, xb = _split_ops(oa), _split_ops(ob)
    return len(xa) == len(xb) and all(x == y or x.startswith("#") and y.startswith("#") for x, y in zip(xa, xb))


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
    return out + ([cur.strip()] if cur.strip() else [])


class _FixedMap:
    """An address map with some PS2 addresses pinned to their 68k address (twin evidence).
    Records the work-RAM addresses it only interpolated between samples (taken as the same
    variable as the arcade's; extrapolated ones become Arrange-only variables instead)."""

    def __init__(self, base, fixes: dict[int, int]):
        self._base, self._fixes = base, fixes
        self.guessed: set[int] = set()
        self.extrapolated: set[int] = set()

    def map_const(self, v: int):
        if v in self._fixes:
            return self._fixes[v], "twin", 1.0
        # an address in the same block (within 16 bytes) as one the twin pins: same offset
        near = [q for q in self._fixes if 0 < abs(v - q) < 16 and 0x800000 <= self._fixes[q] < 0x830000]
        if near:
            q = min(near, key=lambda q: abs(v - q))
            return self._fixes[q] + (v - q), "twin", 1.0
        r = self._base.map_const(v)
        if r[0] is not None and 0x800000 <= r[0] < 0x830000:
            if r[1] == "interp":
                self.guessed.add(r[0])
            elif r[1] == "near":
                self.extrapolated.add(r[0])
        return r

    def __getattr__(self, name):
        return getattr(self._base, name)


VERIFIED = OUT / "ps2_verified.json"     # tools/ps2check_dumps.py --verified


def ps2_verified() -> dict[str, set[int]]:
    """Arrange routines (per overlay) that matched the PS2 on every captured state."""
    if not VERIFIED.exists():
        return {}
    return {ov: {int(x, 16) for x in fns} for ov, fns in json.loads(VERIFIED.read_text())["verified"].items()}


def _entry_points(lo: int, hi: int) -> tuple[list[int], set[int]]:
    """(addresses strictly inside (lo, hi) reached from outside [lo, hi) or by a code pointer,
    the subset reached by branches/calls)."""
    import bisect as _b
    tg, keys, ptrs = _code_refs()
    out = set()
    i = _b.bisect_right(keys, lo)
    while i < len(tg) and tg[i][0] < hi:
        if not (lo <= tg[i][1] < hi):
            out.add(tg[i][0])
        i += 1
    return sorted(out | {v for v in ptrs if lo < v < hi}), out


def _translator_entries(ov, start: int, size: int) -> list[int]:
    """Entries the translator made inside a MIPS function (for 68k code pointers into the
    middle of a routine: the PS2 tables hold their MIPS addresses): a 16-aligned address
    reached by a `j` over the nop padding before it."""
    import struct
    w = lambda a: (lambda b: struct.unpack("<I", b)[0] if len(b) == 4 else None)(ov.read(a, 4))  # noqa: E731
    out = []
    for m in range(start + 16, start + size, 16):
        k = m - 4
        while k > start and w(k) == 0:
            k -= 4
        for a in (k - 4, k):
            x = w(a)
            if x is not None and x >> 26 == 2 and ((a + 4) & 0xF0000000) | ((x & 0x3FFFFFF) << 2) == m:
                out.append(m)
                break
    return out


_MAIN_FUNCS: dict = {}
_MAIN_MAPS: dict = {}
_ELF: list = []


def _read_or_none(read_code, a: int):
    try:
        return read_code(a, 8)
    except Exception:  # noqa: BLE001
        return None


def _elf_funcs() -> set[int]:
    """Function starts of the PS2 ELF (cached)."""
    if len(_ELF) < 2:
        from ..diff import mips_funcs
        e = _elf_text()
        _ELF.append({f.start for f in mips_funcs.split_functions(e.image, e.image_base, e.header.text_end)})
    return _ELF[1]


TABLE_PLACEHOLDER = 0x3FF000  # printed for a table the port emits (top of the empty program ROM)


def _with_tables(text: str, data: dict) -> str:
    """The routine's text with its own copies of Arrange tables (placeholder -> label)."""
    if not data:
        return text
    for ph, (q, _) in data.items():
        text = re.sub(rf"\${ph:x}(\.l)?\b", f"T_{q:x}", text)
    out = [text.rstrip("\n")]
    for ph, (q, words) in sorted(data.items()):
        out.append(f"T_{q:x}:")
        for sz, v, n in words:
            out.append(f"\tdc.{'l' if sz == 4 else 'w'}\t${v:0{2 * sz}x}"
                       + (f"\t; sprite ${n:x}" if n is not None else ""))
    return "\n".join(out) + "\n"


BSS_KEYS = 0x40000000       # printed keys of overlay BSS variables (Arrange-only storage)


class _BssMap:
    """An address in a stage overlay's BSS (zeroed when the overlay loads) that the map pairs
    with no variable (it would only guess a ROM address) is an Arrange-only variable: printed
    as a key above BSS_KEYS (conf 0.30), which the porter gives fresh storage like an
    extrapolated RAM address. (The main overlay's BSS holds the game's own variables.)"""

    def __init__(self, base, ov):
        self._base = base
        self._lo = ov.image_base + len(ov.image)
        self._hi = self._lo + ov.header.bss_size
        self._key = BSS_KEYS | (ov.header.module_id << 20)

    def __getattr__(self, name):
        return getattr(self._base, name)

    def map_const(self, v: int):
        r = self._base.map_const(v)
        if self._lo <= v < self._hi and (r[0] is None or r[0] < ROM_HI):
            return self._key + (v - self._lo), "near", 0.3     # (the map knows no variable there)
        return r


def _elf_text():
    """The PS2 ELF's loaded image, overlay-like (cached)."""
    if not _ELF:
        from .port import WORK
        from .verify import ElfText
        _ELF.append(ElfText(WORK / "ps2" / "disc" / "SLPS_253.52"))
    return _ELF[0]
_SHAPES: dict = {}


def _shape_siblings(index, pos: int, n: int) -> int:
    """How many other places of the 68k program have the n instructions from pos with the
    same shapes (mnemonic, sizes, registers; constants and addresses abstracted)?"""
    from .port import _shape_key
    if id(index) not in _SHAPES:
        sk = [_shape_key(k.canon) for k in index.ks]
        grams: dict = collections.defaultdict(list)
        for i in range(len(sk) - 3):
            grams[tuple(sk[i:i + 4])].append(i)
        _SHAPES[id(index)] = (sk, grams)
    sk, grams = _SHAPES[id(index)]
    seq = sk[pos:pos + n]
    return sum(1 for q in grams.get(tuple(seq[:4]), ()) if q != pos and sk[q:q + n] == seq)


MAP_AUDIT = OUT / "map_audit.json"       # tools/map_audit.py --json


def map_suspects() -> set[int]:
    """PS2 functions whose learned 68k routine has a near-identical copy that matches the
    PS2 code better (tools/map_audit.py): not used for Arrange calls until checked by hand
    (corrections go to verify.FUNC_FIXES)."""
    from .verify import FUNC_FIXES
    if not MAP_AUDIT.exists():
        return set()
    return {int(x["ps2"], 16) for x in json.loads(MAP_AUDIT.read_text())} - set(FUNC_FIXES)


def helpers_chain(m, main: str):
    """m, then the learned map of the main overlay (A0A8 arcade / A0A9 Arrange) for what m
    does not know (a bare AddrMap: raw PS2 addresses, kept)."""
    from . import helpers
    from .templates import AddrMap
    from .verify import LearnedMap
    if main not in _MAIN_MAPS:
        _MAIN_MAPS[main] = LearnedMap.load(OUT / ("addrmap_arcade.json" if main == "A0A8" else "addrmap_arrange.json"))
        if main != "A0A8":
            for k in map_suspects():
                _MAIN_MAPS[main].funcs.pop(k, None)
    if isinstance(m, _FixedMap):
        c = _FixedMap(helpers.ChainMap(m._base, _MAIN_MAPS[main]), m._fixes)
        c.guessed, c.extrapolated = m.guessed, m.extrapolated     # (recorded for the routine)
        return c
    if type(m) is AddrMap:
        return m
    return helpers.ChainMap(m, _MAIN_MAPS[main])


def _main_funcs(name: str) -> dict:
    """{start: Function} of a main overlay (cached)."""
    if name not in _MAIN_FUNCS:
        from ..diff import mips_funcs
        from . import helpers
        ov = helpers.main_overlay(name)
        _MAIN_FUNCS[name] = {f.start: f for f in mips_funcs.split_functions(ov.image, ov.image_base,
                                                                            ov.header.text_end)}
    return _MAIN_FUNCS[name]


def _entries_into(lo: int, hi: int) -> bool:
    """Is any address strictly inside (lo, hi) reached from outside [lo, hi) or by a pointer?"""
    import bisect as _b
    tg, keys, ptrs = _code_refs()
    i = _b.bisect_right(keys, lo)
    while i < len(tg) and tg[i][0] < hi:
        if not (lo <= tg[i][1] < hi):
            return True
        i += 1
    return any(lo < v < hi for v in ptrs) if hi - lo < 0x4000 else True


def _entered_before(ks, pos: int, max_back: int = 8) -> bool:
    """Is ks[pos] a code-pointer target (a jump-table entry, e.g. a task's run entry) with
    another one among the (up to `max_back`) instructions that fall straight into it (an
    init that runs on into the run entry)? Entered there, code before the anchor in the port
    would run on every entry."""
    _, _, targets = _code_refs()
    if ks[pos].addr not in targets:
        return False
    for k in range(pos - 1, max(pos - 1 - max_back, -1), -1):
        if ks[k].canon.mnem in ("rts", "jmp", "bra", "rte"):
            return False
        if ks[k].addr in targets:
            return True
    return False


class OverlaySource:
    """Recovered Arrange routines of one overlay pair, de-translated on demand, with their
    68k originals (anchors) for the changed ones."""

    def __init__(self, a_name: str, r_name: str, index=None, main_funcs: dict | None = None,
                 arrange_records: bool = False):
        self.arrange_records = arrange_records
        from ..diff import mips_funcs
        from ..pgm import rom as rom_mod
        from ..ps2.overlay import Overlay
        from .port import (STAGE_PAIRS, WORK, _arrange_map, _fallback_pairs, learn_overlay_map)
        from .verify import Index, LearnedMap, compare_function, load_listing
        from . import core, twin_gaps
        from .apply import is_translated, _reader
        self.core, self._reader, self.is_translated = core, _reader, is_translated
        disc = WORK / "ps2" / "disc" / "A0A0"
        self.A, self.R = Overlay(disc / a_name), Overlay(disc / r_name)
        self.index = index or Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
        lm = LearnedMap.load(OUT / "addrmap_arcade.json")
        self.alm = learn_overlay_map(self.A, self.index, lm) if (a_name, r_name) in STAGE_PAIRS else lm
        fa = mips_funcs.split_functions(self.A.image, self.A.image_base, self.A.header.text_end)
        fb = mips_funcs.split_functions(self.R.image, self.R.image_base, self.R.header.text_end)
        self.fa = {f.start: f for f in fa}
        self.fb = {f.start: f for f in fb}
        diff = mips_funcs.match(fa, fb)
        p = rom_mod.RomSet(WORK).p_rom_68k()[:0x200000]
        self.amap = _arrange_map(p, self.A, self.R, diff, self.alm)
        if main_funcs:
            for k, v in main_funcs.items():
                self.amap.funcs.setdefault(k, v)
        for k in map_suspects():          # (an Arrange call to one stays unmapped: refused)
            self.amap.funcs.pop(k, None)
        routine_starts = {int(k, 16) for k in json.loads((WORK / "pgm" / "routines.json").read_text())}
        self.roots: dict[int, int] = {}          # arrange fn -> 68k original (changed routines)
        self.call_votes: dict = collections.defaultdict(collections.Counter)
        self.trust: dict[int, bool] = {}
        self.trust_info: dict[int, tuple] = {}
        self.unshifted: dict[int, int] = {}
        self.needs_layout: dict[int, str] = {}
        self.layout_only: dict[int, bool] = {}
        self.region: dict[int, tuple] = {}       # arrange fn -> (68k start, end, ends with rts/jmp/bra)
        self._text: dict[int, str] = {}
        self.twin: dict[int, tuple] = {}      # arrange fn -> (arcade twin, aligned 68k start)
        self.trust_why: dict[int, str | None] = {}
        self.distrust: dict[int, str] = {}       # arrange fn -> why it is not trusted
        self.guessed: dict[int, set] = {}        # arrange fn -> RAM addresses the map only guessed
        self.extrapolated: dict[int, set] = {}   # arrange fn -> RAM addresses extrapolated from a sample
        self.addr_fix: dict[int, dict[int, int]] = {}   # arrange fn -> {PS2 address: 68k address}
        self.entries: dict[int, list] = {}      # arrange fn -> [(68k entry, Arrange MIPS address)]
        self.verified = ps2_verified().get(r_name, set())
        from . import sprite_ids
        # sprite numbers: the PS2 banks located in the PGM ROMs, the pairs learned from the
        # twins winning (the same art may be stored twice); the sprite routines are those the
        # learned pairs show
        self.sprite_learned_table = sprite_ids.table()
        self.sprite_ids = {**sprite_ids.located(), **self.sprite_learned_table}
        self.sprite_learned: dict[int, int | None] = {}     # from this overlay's twins (twin_gaps)
        self._bodies: dict[int, tuple | None] = {}
        from . import helpers
        # the stage overlays call helpers in their mode's main overlay: read through both
        main_a, main_r = helpers.main_overlay("A0A8"), helpers.main_overlay("A0A9")
        self._rd_a, self._rd_r = helpers.reader(self.A, main_a), helpers.reader(self.R, main_r)
        self._code_a = lambda a, n: (self.A if self.A.contains(a) else main_a).read(a, n)
        self._code_r = lambda a, n: (self.R if self.R.contains(a) else main_r).read(a, n)
        # guessed program-ROM addresses placed by the table found there (detrans/tables.py)
        # an empty MIPS routine (`jr $ra ; nop`) is not the translation of a 68k routine with a
        # body (the arcade mode's stubs were paired with $1149bc by the learned map)
        self.empty = set()
        for fm, rd in ((self.alm.funcs, self._code_a), (self.amap.funcs, self._code_r)):
            for k in [k for k in fm if self._is_empty(rd, k, fm[k])]:
                self.empty.add(k)
                del fm[k]
        # (tables in the overlays, the main overlay or the ELF)
        from . import tables
        elf = _elf_text()
        self._rom = p
        self._rd_ta, self._rd_tr = helpers.reader(self.A, main_a, elf), helpers.reader(self.R, main_r, elf)
        # (a code pointer in a table is a function start)
        starts_a = set(self.fa) | set(_main_funcs("A0A8")) | _elf_funcs()
        starts_r = set(self.fb) | set(_main_funcs("A0A9")) | _elf_funcs()
        self._is_code_a = starts_a.__contains__
        self._is_code_r = starts_r.__contains__
        self.alm = tables.TableMap(self.alm, self._rd_ta, self.alm.funcs, p, self.sprite_ids, self._is_code_a)
        bss = _BssMap(self.amap, self.R) if (a_name, r_name) in STAGE_PAIRS else self.amap
        self.amap = tables.TableMap(bss, self._rd_tr, self.amap.funcs, p, self.sprite_ids, self._is_code_r)
        # helpers the translator outlined with constant arguments are inlined (helpers.inline)
        self._outl_a = self._outlined(lambda: self.fa, "A0A8", self._code_a)
        self._outl_r = self._outlined(lambda: self.fb, "A0A9", self._code_r)
        self._b2a = {m.b.start: m.a.start for m in diff.matches}
        self.tail_map: dict[int, int] = {}       # Arrange callee -> the 68k code it continues into
        self.trail: dict[int, tuple] = {}        # arrange fn -> (68k instructions, rule) after the alignment
        self._bare_ctx = None
        self._tables: dict[int, tuple] = {}
        for m in diff.matches:
            if m.kind != "near":
                continue
            ca = self.A.read(m.a.start, m.a.size)
            if not is_translated(ca):
                continue
            try:
                ra = core.detranslate(ca, m.a.start, self.alm, read_word=self._rd_a)
            except Exception:  # noqa: BLE001
                continue
            # the arcade mode calls Arika's helpers where the 68k has the fragment inline
            ra_inl = helpers.inline_result(ra, self._code_a, self._rd_a, self.alm, self._outl_a)
            rep = compare_function(self.index, ra_inl)
            rep0_anchor = rep.anchor
            if rep.anchor is None:
                _fallback_pairs(self.index, ra, rep, routine_starts)
            if rep.anchor is not None:
                self.roots[m.b.start] = rep.anchor
                # trusted: the arcade twin de-translates exactly to a self-contained 68k region
                pos = self.index.pos_of(rep.anchor)
                end_pos = pos + rep.n_m68
                ks = self.index.ks
                region_end = ks[end_pos].addr if end_pos < len(ks) else rep.anchor
                start = rep.anchor
                # the twin's last instructions not aligned: the translator's tail call for the
                # 68k's final jump, or a call of the code the 68k runs into (split off as its
                # own function): the region extends over the 68k they stand for
                trail = None
                if not (rep.unknown == 0 and rep.exact == rep.n_m68):
                    trail = twin_gaps.trailing(rep, ra_inl, self.index, {**self.alm.funcs, **helpers.CALLEES},
                                               end_pos, _branch_targets(), body=self._arcade_body,
                                               ids=self.sprite_ids, learned=self.sprite_learned)
                if trail is not None and trail[1].endswith("fallthrough-tail") \
                        and not self._tail_callee(m.b.start, ra_inl, ks[end_pos + trail[0]].addr):
                    trail = None
                if trail is not None:
                    self.trail[m.b.start] = trail
                    end_pos += trail[0]
                    region_end = ks[end_pos].addr if end_pos < len(ks) else region_end
                # the de-translator writes movem saves/restores as plain moves, so alignment
                # starts after `movem.l regs,-(a7)` and stops before `movem.l (a7)+,regs ; rts`
                ported_text = self.text(m.b.start) or ""
                saves = bool(re.search(r"^\s+move\.l\s+[ad][0-7],-\d+\(a7\)", ported_text, re.M))
                saved = saves and pos > 0 and ks[pos - 1].canon.mnem == "movem" and "-(a7)" in ks[pos - 1].canon.text
                if saved:
                    start = ks[pos - 1].addr
                    pos -= 1
                # twin code before its first aligned instruction (other than those saves) where
                # the anchor is an entry with another entry falling into it: the 68k routine
                # starts earlier, e.g. an init that falls into the anchored run entry
                # (`move.w #$1001,2(a4)` written as two byte moves); anchored at the run entry the
                # port would redo the init every frame
                recs0 = [i for i in ra_inl.insns if not i.is_label and i.mnem]
                paired0 = {id(a) for a, _ in rep.pairs}
                lead = next((k for k, i in enumerate(recs0) if id(i) in paired0), 0)
                lead_unpaired = lead > 0 and not saved and _entered_before(ks, pos)
                if end_pos + 1 < len(ks) and ks[end_pos].canon.mnem == "movem" and "(a7)+" in ks[end_pos].canon.text \
                        and ks[end_pos + 1].canon.mnem == "rts":
                    end_pos += 2
                    region_end = ks[end_pos].addr if end_pos < len(ks) else region_end
                prev_term = pos > 0 and ks[pos - 1].canon.mnem in ("rts", "jmp", "bra", "rte")
                rep.anchor = start
                self.roots[m.b.start] = start
                last_term = end_pos > pos and ks[end_pos - 1].canon.mnem in ("rts", "jmp", "bra", "rte")
                entered = (_entries_into(rep.anchor, region_end)
                           or bool(_translator_entries(self.R, m.b.start, m.b.size))
                           or bool(_translator_entries(self.A, m.a.start, m.a.size)))
                if entered:
                    # entries from outside: redirect each to the same point of the ported code
                    ents = self._resolve_entries(m.b.start, m.a.start, rep, ra_inl,
                                                 _entry_points(rep.anchor, region_end), rep.anchor, region_end)
                    if ents is not None:
                        self.entries[m.b.start] = ents
                        self._text.pop(m.b.start, None)
                        entered = False
                self.twin[m.b.start] = (m.a.start, rep0_anchor)
                self.trust_info[m.b.start] = (rep.anchor, region_end, rep.unknown, rep.exact, rep.n_m68,
                                              prev_term or rep.anchor in routine_starts, last_term, entered)
                exact = (rep.unknown == 0 and rep.exact == rep.n_m68 and rep.n_m68 > 0
                         and region_end - rep.anchor >= 6 and not entered and not lead_unpaired)
                # PS2-verified: the Arrange routine matched the PS2 on every captured state
                # (tools/ps2check_dumps.py --verified), the twin aligns with no inserted or
                # missing instruction (operands may differ: they are replaced anyway), and the
                # learned call map confirms the anchor (no near-identical sibling routine)
                # (gaps in the alignment are allowed where they are the translator's own rewrites
                # of the same code: detrans/twin_gaps.py)
                gaps_ok = not any(a is None or b is None for a, b in rep.mismatches) or not twin_gaps.unexplained(
                    rep, ra_inl, self.index, {**self.alm.funcs, **helpers.CALLEES}, _branch_targets(),
                    body=self._arcade_body, ids=self.sprite_ids, learned=self.sprite_learned)
                structural = (rep.unknown == 0 and rep.n_m68 > 0 and region_end - rep.anchor >= 6
                              and not entered and gaps_ok and not lead_unpaired)
                if structural and not last_term:
                    # the 68k falls through at the region end: the twin must end there as well
                    # (a twin that returns there was split off a longer 68k routine: the port
                    # would return early and skip the rest)
                    recs = [i for i in ra_inl.insns if not i.is_label and i.mnem]
                    paired = {id(a) for a, _ in rep.pairs}
                    structural = bool(recs) and (id(recs[-1]) in paired or trail is not None)
                # (an anchor from the alignment itself, not a fallback pairing that leaves no
                # instruction pairs to check; a call map that knows the twin)
                mapped = self.alm.funcs.get(m.a.start)
                confirmed = rep0_anchor is not None and mapped is not None and mapped in (rep.anchor, rep0_anchor)
                if not confirmed and rep0_anchor is not None and mapped is None and structural:
                    # the call map does not know the twin: no other 68k code of the same
                    # instruction shapes (an identical or near-identical copy) can be the anchor
                    p0, p1 = self.index.pos_of(rep.anchor), self.index.pos_of(region_end)
                    confirmed = p1 - p0 >= 6 and _shape_siblings(self.index, p0, p1 - p0) == 0
                verified = m.b.start in self.verified and structural and confirmed and not exact
                if verified:
                    # the twin differs from the 68k only in immediates and absolute addresses the
                    # learned map got wrong: port the Arrange routine with those addresses fixed
                    fixes = self._twin_address_fixes(m.b.start, m.a.start, ca, ra_inl, rep)
                    verified = fixes is not None
                    if verified:
                        self.addr_fix[m.b.start] = fixes
                        self._text.pop(m.b.start, None)
                self.trust[m.b.start] = exact or verified
                self.trust_why[m.b.start] = "twin exact" if exact else "PS2-verified" if verified else None
                if not (exact or verified):
                    self.distrust[m.b.start] = (
                        "region entered from outside" if entered
                        else "twin has code before its anchor (the 68k routine starts earlier)" if lead_unpaired
                        else "twin has unrecognised instructions" if rep.unknown
                        else "region too short" if not (rep.n_m68 > 0 and region_end - rep.anchor >= 6)
                        else "arcade twin not exact, not PS2-verified" if m.b.start not in self.verified
                        else "twin has inserted/missing instructions" if not gaps_ok
                        else "twin returns where the 68k falls through" if not structural
                        else "anchor not confirmed by the call map" if not confirmed
                        else "twin operands differ beyond immediates/addresses")
                self.region[m.b.start] = (rep.anchor, region_end, last_term)
            # learn call targets: the n-th call of the Arrange routine <-> the n-th call of its
            # arcade twin (aligned), where only the arcade side is resolved
            try:
                bmap = _FixedMap(self.amap, {**self._tables_for(m.b.start)[0],
                                             **self.addr_fix.get(m.b.start, {})})
                rb = core.detranslate(self.R.read(m.b.start, m.b.size), m.b.start, bmap, read_word=self._rd_r,
                                      extra_labels=[a for _, a in self.entries.get(m.b.start, ())])
            except Exception:  # noqa: BLE001
                continue
            self.guessed[m.b.start] = bmap.guessed
            self.extrapolated[m.b.start] = bmap.extrapolated
            self._unshift(m.b.start, ra, rb)
            ca_calls = [i.text().split() for i in ra.insns if i.mnem in ("jsr", "jmp", "bsr")]
            cb_calls = [i.text().split() for i in rb.insns if i.mnem in ("jsr", "jmp", "bsr")]
            ka = ["F" if t[-1].startswith("F_") else ("R" if t[-1].startswith("$") else "?") for t in ca_calls]
            kb = ["F" if t[-1].startswith("F_") else ("R" if t[-1].startswith("$") else "?") for t in cb_calls]
            sm = difflib.SequenceMatcher(None, [t[-1] if t[-1].startswith("$") else "x" for t in ca_calls],
                                         [t[-1] if t[-1].startswith("$") else "x" for t in cb_calls], autojunk=False)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op in ("equal", "replace") and i2 - i1 == j2 - j1:
                    for x, y in zip(ca_calls[i1:i2], cb_calls[j1:j2]):
                        if x[-1].startswith("$") and y[-1].startswith("F_"):
                            self.call_votes[int(y[-1][2:], 16)][int(x[-1][1:].split(".")[0], 16)] += 1

        # an anchor claimed by several Arrange routines is ambiguous (near-identical sibling
        # routines, e.g. per player): only an exact twin can be trusted there
        claims = collections.Counter(self.roots.values())
        for fn, why in self.trust_why.items():
            if why == "PS2-verified" and claims[self.roots[fn]] > 1:
                self.trust[fn], self.trust_why[fn] = False, None
                self.distrust[fn] = "anchor claimed by several routines"
        self.funcs = dict(self.amap.funcs)
        for b, c in self.call_votes.items():
            a, v = c.most_common(1)[0]
            if b not in self.funcs and v >= 0.75 * sum(c.values()):
                self.funcs[b] = a

    def _unshift(self, fn: int, ra, rb) -> None:
        """Undo the Arrange enemy-record growth (0x24 -> 0x2C bytes) in a changed routine, guided
        by its arcade twin: aligned displacements/immediates that equal the arcade value shifted by
        8 bytes per record take the arcade value. Records whether anything Arrange-only still
        depends on the new layout, and whether any real change is left at all."""
        from .verify import recovered_canon

        def key(c):
            parts = [c.mnem + "." + c.size]
            for op in c.ops:
                if op[0] in ("imm", "abs", "label", "func", "pc"):
                    parts.append(op[0])
                elif op[0] == "d16":
                    parts.append("d16|" + str(op[1]))
                else:
                    parts.append("|".join(str(x) for x in op))
            return " ".join(parts)

        def shifted(v: int) -> int:
            return v + 8 * (v // 0x24) if v >= 0 else -((-v) + 8 * ((-v) // 0x24))
        ia = [(i, recovered_canon(i)) for i in ra.insns if not i.is_label and i.mnem]
        ib = [(i, recovered_canon(i)) for i in rb.insns if not i.is_label and i.mnem]
        sm = difflib.SequenceMatcher(None, [key(c) if c else "?" for _, c in ia],
                                     [key(c) if c else "?" for _, c in ib], autojunk=False)
        aligned_b = set()
        changed = False
        undo = []
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                (xa, ca), (xb, cb) = ia[blk.a + k], ib[blk.b + k]
                aligned_b.add(id(xb))
                if ca is None or cb is None:
                    continue
                for n, (oa, ob) in enumerate(zip(ca.ops, cb.ops)):
                    va = oa[2] if oa[0] == "d16" else (oa[1] if oa[0] == "imm" else None)
                    vb = ob[2] if ob[0] == "d16" else (ob[1] if ob[0] == "imm" else None)
                    if not isinstance(va, int) or not isinstance(vb, int) or va == vb:
                        continue
                    if abs(va) >= 0x24 and vb == shifted(va) and n < len(xb.ops):
                        undo.append((xb, n, xb.ops[n]))
                        old = str(vb) if oa[0] == "d16" else None
                        if oa[0] == "d16":
                            xb.ops[n] = re.sub(rf"^(-?\$?){old}\(", lambda mm: f"{va}(", xb.ops[n]) \
                                if re.match(rf"^-?{old}\(", xb.ops[n]) else xb.ops[n].replace(f"{vb}(", f"{va}(")
                        else:
                            xb.ops[n] = f"#{va}" if xb.ops[n].startswith("#") else xb.ops[n]
                        self.unshifted[fn] = self.unshifted.get(fn, 0) + 1
                c2 = recovered_canon(xb)
                if c2 is None or c2.text != ca.text:
                    changed = True
        extra_b = [(x, c) for x, c in ib if id(x) not in aligned_b]
        if len(extra_b) or len(ia) != len(aligned_b):
            changed = True
        for x, c in extra_b:
            if c is None:
                continue
            for op in c.ops:
                if op[0] == "d16" and str(op[1]).startswith("a") and abs(op[2]) >= 0x24:
                    self.needs_layout[fn] = f"Arrange-only {x.text().strip()}"
        self.layout_only[fn] = not changed
        if self.arrange_records:
            # the build uses the Arrange 0x2C enemy records: keep Arrange's own offsets
            for xb, n, op in undo:
                xb.ops[n] = op
            self.needs_layout.pop(fn, None)
        tfix, tdata = self._tables_for(fn)
        fm = _FixedMap(self.amap, {**tfix, **self.addr_fix.get(fn, {})})
        fm.guessed, fm.extrapolated = self.guessed.setdefault(fn, set()), self.extrapolated.setdefault(fn, set())
        self._text[fn] = _with_tables(self._inline_helpers(rb.text(header=False), fm), tdata)

    def _resolve_entries(self, fn: int, twin: int, rep, ra_inl, points: tuple, anchor: int,
                         end: int) -> list | None:
        """[(68k entry E, Arrange MIPS address)] for entries into the region from outside, or
        None if an entry cannot be placed, or its jmp (6 bytes) would overlap the one at the
        region start or another entry's.

        Code pointers: the PS2 reaches a pointer target in the middle of a routine only through
        an entry the translator made there (_translator_entries); a 68k "pointer" into a
        routine whose Arrange and twin functions have none is not used as one (a value in a
        numeric table, a coincidence). The k-th twin entry is the k-th Arrange entry; E is the
        68k instruction aligned with the twin's first instruction there.
        Branches: E is the 68k instruction aligned with a twin instruction, which aligns with an
        Arrange instruction (same shape) right after a label (a block start)."""
        import difflib
        from . import helpers
        from .port import _shape_key
        from .verify import recovered_canon
        points, branched = points
        twin_of = {k.addr: x for x, k in rep.pairs}
        k68_of = {id(x): k.addr for x, k in rep.pairs}
        code = self.R.read(fn, self.fb[fn].size)
        arr = helpers.inline_result(self.core.detranslate(code, fn, self.amap, read_word=self._rd_r),
                                    self._code_r, self._rd_r, self.amap, self._outl_r)
        ai = [i for i in arr.insns if not i.is_label and i.mnem]
        bi = [i for i in ra_inl.insns if not i.is_label and i.mnem]
        out, spots = [], [anchor]
        ents_r = _translator_entries(self.R, fn, self.fb[fn].size)
        ents_a = _translator_entries(self.A, twin, self.fa[twin].size)
        if not ents_a and not ents_r and not branched:
            return []
        if any(i.addr == 0 for i in ai + bi) or len(ents_r) != len(ents_a):
            return None                         # re-parsed (helper inlined): no MIPS addresses
        labels_r = {i.addr for i in arr.insns if i.is_label}
        _code_refs()
        cands = _ENTRY_CACHE["values"] | branched   # some 32-bit value of the ROM or a branch
        for t, r in zip(ents_a, ents_r):
            k = next((n for n, i in enumerate(ra_inl.insns) if i.is_label and i.addr == t), None)
            x = next((i for i in ra_inl.insns[k + 1:] if not i.is_label and i.mnem), None) if k is not None else None
            e = k68_of.get(id(x)) if x is not None else None
            if e == anchor:
                continue                        # only the jump over the padding precedes it
            if e is None or r not in labels_r or e not in cands or not anchor < e:
                return None
            out.append((e, r))
            spots.append(e)
        # branches into the region: an entry can only go to a block start (the label right
        # before the instruction)
        label_before, prev = {}, None
        for i in arr.insns:
            if i.is_label:
                prev = i
            elif i.mnem:
                if prev is not None:
                    label_before[id(i)] = prev.addr
                prev = None
        key = lambda i: (lambda c: _shape_key(c) if c else "?")(recovered_canon(i))  # noqa: E731
        sm = difflib.SequenceMatcher(None, [key(i) for i in ai], [key(i) for i in bi], autojunk=False)
        b2a = {}
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                b2a[id(bi[blk.b + k])] = ai[blk.a + k]
        for e in sorted(branched - {e for e, _ in out}):
            tw = twin_of.get(e)
            a = b2a.get(id(tw)) if tw is not None else None
            if a is None or id(a) not in label_before:
                return None
            out.append((e, label_before[id(a)]))
            spots.append(e)
        # an entry at an rts needs no jmp (the original rts stays); every jmp must fit in the
        # region (the bytes after it are another routine)
        def is_rts(e):
            k = self.index.ks[self.index.pos_of(e)]
            return k.addr == e and k.canon.mnem == "rts"
        out = [(e, a) for e, a in out if not is_rts(e)]
        spots = sorted([anchor] + [e for e, _ in out])
        if any(y - x < 6 for x, y in zip(spots, spots[1:])) or spots[-1] + 6 > end:
            return None
        return sorted(out)

    def _twin_address_fixes(self, fn: int, tw: int, twin_code: bytes, ra_inl, rep) -> dict | None:
        """{Arrange PS2 address: 68k address} correcting the learned address map, from the
        twin/68k alignment (twin address P <-> 68k Y where they disagree) and the Arrange/twin
        alignment (Arrange address Q <-> P at the same place). None when an operand differs
        in anything but an immediate or an absolute address, or the evidence is inconsistent."""
        import difflib
        from . import helpers
        from .port import _shape_key
        from .templates import AddrMap
        from .verify import recovered_canon
        raw = helpers.inline_result(self.core.detranslate(twin_code, tw, AddrMap(), read_word=self._rd_a),
                                    self._code_a, self._rd_a, AddrMap(), self._outl_a)
        mi = [i for i in ra_inl.insns if not i.is_label and i.mnem]
        ri = [i for i in raw.insns if not i.is_label and i.mnem]
        if len(mi) != len(ri) or any(a.mnem != b.mnem for a, b in zip(mi, ri)):
            return None
        pos = {id(x): k for k, x in enumerate(mi)}
        p2y: dict[int, int] = {}
        mism = {id(x) for x, k68 in rep.mismatches if x is not None and k68 is not None}
        for x, k68 in rep.pairs:
            cx, craw = recovered_canon(x), recovered_canon(ri[pos[id(x)]])
            if cx is None or craw is None or cx.mnem != k68.canon.mnem or len(cx.ops) != len(k68.canon.ops):
                if id(x) in mism:
                    return None
                continue
            for om, orw, ok in zip(cx.ops, craw.ops, k68.canon.ops):
                if om[0] == orw[0] == ok[0] == "abs":
                    # every aligned absolute address: twin PS2 address -> its 68k address
                    if p2y.setdefault(orw[1], ok[1]) != ok[1]:
                        return None
                elif id(x) in mism and not (om == ok or om[0] == ok[0] == "imm"):
                    return None
        if not p2y:
            return {}
        code = self.R.read(fn, self.fb[fn].size)
        arr = helpers.inline_result(self.core.detranslate(code, fn, AddrMap(), read_word=self._rd_r),
                                    self._code_r, self._rd_r, AddrMap(), self._outl_r)
        ai = [recovered_canon(i) for i in arr.insns if not i.is_label and i.mnem]
        bi = [recovered_canon(i) for i in ri]
        sm = difflib.SequenceMatcher(None, [_shape_key(c) if c else "?" for c in ai],
                                     [_shape_key(c) if c else "?" for c in bi], autojunk=False)
        fixes: dict[int, int] = {}
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                ca_, cb_ = ai[blk.a + k], bi[blk.b + k]
                for oa, ob in zip(ca_.ops, cb_.ops):
                    if oa[0] == ob[0] == "abs" and ob[1] in p2y:
                        # an address the Arrange map knows from its own samples stays (the
                        # Arrange routine may use another variable there); guessed ones follow
                        # the twin
                        if self.amap.map_const(oa[1])[1] not in ("interp", "near", ""):
                            continue
                        if helpers_chain(self.amap, "A0A9").map_const(oa[1])[0] in ARRANGE_OVERRIDES:
                            continue    # an Arrange variable the port places itself (layout stage)
                        if fixes.setdefault(oa[1], p2y[ob[1]]) != p2y[ob[1]]:
                            return None
        return fixes

    def _is_empty(self, read_code, k: int, m68: int) -> bool:
        try:
            code = read_code(k, 8)
        except Exception:  # noqa: BLE001
            return False
        if code != _EMPTY:
            return False
        try:
            k68 = self.index.ks[self.index.pos_of(m68)]
        except (IndexError, TypeError):
            return False
        return not (k68.addr == m68 and k68.canon.mnem == "rts")

    def continues(self, x: int, addrs) -> bool:
        """Is the Arrange function x the counterpart of 68k code at one of addrs (its arcade
        twin de-translates to that code)?"""
        y = self._b2a.get(x)
        if y is None:
            return False
        if self._bare_ctx is None:
            from . import helpers, twin_gaps
            self._bare_ctx = twin_gaps.Ctx.bare(self.index, {**self.alm.funcs, **helpers.CALLEES},
                                                self._arcade_body)
        return any(self.tail_map.get(x) == a or self._bare_ctx.same_code(f"f_{y:x}", a) for a in sorted(addrs))

    def _tail_callee(self, fn: int, ra_inl, addr: int) -> bool:
        """The twin ends calling F (the translation of the 68k code at addr): the Arrange
        routine must end calling F's own Arrange counterpart, which then stands for addr
        (the port continues into the 68k code there instead of running a copy of it)."""
        def last_call(lines):
            code = [l.split(";", 1)[0].split() for l in lines]
            code = [c for c in code if c and not c[0].endswith(":")]
            if len(code) >= 2 and code[-1] == ["rts"] and code[-2][0] == "jsr" and code[-2][1].startswith("F_"):
                return int(code[-2][1][2:], 16)
            return None
        y = last_call(i.text() for i in ra_inl.insns if not i.is_label)
        x = last_call((self.text(fn) or "").splitlines())
        if x is None or y is None or self._b2a.get(x) != y or self.tail_map.get(x, addr) != addr:
            return False
        self.tail_map[x] = addr
        return True

    def _outlined(self, funcs, main: str, read_code):
        """outlined(addr, amap) for helpers.inline: (size, map) of a translated routine of
        this overlay (funcs()) or of the mode's main overlay (its addresses also through the
        main overlay's learned map), else None."""
        def outlined(a: int, amap):
            fn = funcs().get(a)
            if fn is None:
                fn = _main_funcs(main).get(a)
                amap = helpers_chain(amap, main)
            if fn is None or not self.is_translated(read_code(a, fn.size)):
                return None
            return fn.size, amap
        return outlined

    def _arcade_body(self, f: int):
        """(instructions, {label: index}) of the arcade routine f de-translated with the
        arcade map, or None (not a routine of this overlay, not translated, unrecognised)."""
        if f not in self._bodies:
            self._bodies[f] = None
            fn = self.fa.get(f)
            if fn is None:
                # a routine of the mode's main overlay (the stage overlays call into it)
                fn = _main_funcs("A0A8").get(f)
            code = self._code_a(f, fn.size) if fn is not None else b""
            if code[:8] == _EMPTY:
                from .templates import M68
                self._bodies[f] = ([M68("rts", "", [], 1.0, 0)], {})     # a stub: does nothing
            elif fn is not None and self.is_translated(code):
                try:
                    res = self.core.detranslate(code, f, self.alm, read_word=self._rd_a)
                except Exception:  # noqa: BLE001
                    res = None
                if res is not None:
                    insns, labels = [], {}
                    for i in res.insns:
                        if i.is_label:
                            if i.addr != f:
                                labels[i.text().rstrip(":").strip()] = len(insns)
                        elif not i.is_comment:
                            if not i.mnem:
                                break
                            insns.append(i)
                    else:
                        self._bodies[f] = (insns, labels)
        return self._bodies[f]

    def _inline_helpers(self, text: str, amap=None) -> str:
        """Arika's native-convention helpers (in the main overlay) back to inline 68k."""
        from . import helpers
        return helpers.inline(text, self._code_r, self._rd_r, amap or self.amap, self._outl_r)

    def text(self, fn: int) -> str | None:
        if fn in self._text:
            return self._text[fn]
        f = self.fb.get(fn)
        if f is None:
            return None
        code = self.R.read(f.start, f.size)
        if not self.is_translated(code):
            return None
        tfix, tdata = self._tables_for(fn)
        amap = _FixedMap(self.amap, {**tfix, **self.addr_fix.get(fn, {})})
        try:
            res = self.core.detranslate(code, f.start, amap, read_word=self._rd_r,
                                        extra_labels=[a for _, a in self.entries.get(fn, ())])
        except Exception:  # noqa: BLE001
            return None
        self.guessed[fn] = amap.guessed
        self.extrapolated[fn] = amap.extrapolated
        t = _with_tables(self._inline_helpers(res.text(header=False), amap), tdata)
        self._text[fn] = t
        return t

    def _tables_for(self, fn: int) -> tuple[dict, dict]:
        """Arrange tables the map only guesses, placed through the arcade twin's table at
        the same operand: ({PS2 address: 68k address or placeholder}, {placeholder: the
        table's 68k words}) - the 68k table when Arrange's holds the same, else a converted
        copy of Arrange's own (detrans/tables.py layout/convert)."""
        if fn in self._tables:
            return self._tables[fn]
        self._tables[fn] = ({}, {})
        y = self._b2a.get(fn)
        fb, fa = self.fb.get(fn), self.fa.get(y) if y is not None else None
        if fb is None or fa is None:
            return self._tables[fn]
        from . import tables
        from .templates import AddrMap
        from .verify import recovered_canon
        try:
            rb = self.core.detranslate(self.R.read(fn, fb.size), fn, AddrMap(), read_word=self._rd_r)
            ra = self.core.detranslate(self.A.read(y, fa.size), y, AddrMap(), read_word=self._rd_a)
        except Exception:  # noqa: BLE001
            return self._tables[fn]
        cb = [recovered_canon(i) for i in rb.insns if not i.is_label and i.mnem]
        ca = [recovered_canon(i) for i in ra.insns if not i.is_label and i.mnem]
        if len(ca) != len(cb) or any(a is None or b is None or a.mnem != b.mnem for a, b in zip(ca, cb)):
            return self._tables[fn]
        fixes, data = {}, {}
        for a, b in zip(ca, cb):
            for oa, ob in zip(a.ops, b.ops):
                if not (oa[0] == ob[0] == "abs") or ob[1] in fixes:
                    continue
                g = self.amap.map_const(ob[1])
                if g[0] is None or g[0] >= ROM_HI or g[1] not in ("interp", "near", ""):
                    continue                    # (not a guessed program-ROM address)
                yv = self.alm.map_const(oa[1])
                if yv[0] is None or yv[0] >= ROM_HI or yv[2] < 0.95:
                    continue
                kinds = tables.layout(oa[1], yv[0], self._rd_ta, self.alm.funcs, self._rom, self.sprite_ids,
                                      self._is_code_a)
                if len(kinds) < 2:
                    continue
                got = tables.convert(kinds, oa[1], ob[1], yv[0], self._rd_ta, self._rd_tr, self._rom,
                                     self.sprite_ids)
                if got is None:
                    continue
                if got[0] == "same":
                    fixes[ob[1]] = yv[0]
                else:
                    ph = TABLE_PLACEHOLDER + 4 * len(data)
                    fixes[ob[1]] = ph
                    data[ph] = (ob[1], got[1])
        self._tables[fn] = (fixes, data)
        return self._tables[fn]


def _flag_split_entry(text: str, start: int, end: int, index):
    """(68k entry, Arrange label address) of the init of a routine the PS2 merged with the
    one before it behind a PS2_FLAGS test (`tst <flag> ; beq L ; move.w #k,4(a5) ; rts`, the
    68k region starting with the same `move.w #k,4(a5) ; rts`): the 68k code after that rts
    is the Arrange code at L. None when the routine does not start with such a test, False
    when it does but the 68k start differs (the flag-set part is not the 68k routine)."""
    code = [l.split(";", 1)[0].rstrip() for l in text.splitlines()]
    code = [c for c in code if c.strip() and not c.startswith("F_")]
    t = _FLAG_TST.match(code[0]) if code else None
    if not (t and int(t.group(1), 16) in PS2_FLAGS):
        return None
    if len(code) < 5:
        return False
    b = _FLAG_BR.match(code[1])
    mv = re.match(r"^\s+move\.w\s+#(\$?[0-9a-f]+),4\(a5\)$", code[2])
    clr = re.match(r"^\s+clr\.w\s+4\(a5\)$", code[2])
    if not (b and (mv or clr) and code[3].strip() == "rts"):
        return False
    if (b.group(1) == "beq") != (PS2_FLAGS[int(t.group(1), 16)] != 0):
        return False                    # (the branch must leave for the init when the flag is clear)
    k = 0 if clr else int(mv.group(1)[1:], 16) if mv.group(1).startswith("$") else int(mv.group(1))
    pos = index.pos_of(start)
    ks = index.ks[pos:pos + 3]
    if len(ks) < 3 or ks[0].addr != start or ks[0].canon.mnem != "move" or ks[1].canon.mnem != "rts" \
            or ks[0].canon.ops != (("imm", k), ("d16", "a5", 4)) or not ks[2].addr + 6 <= end:
        return False
    return ks[2].addr, int(b.group(2)[2:], 16)


def overlay_porter(src: OverlaySource, newvars: NewVars | None = None) -> Porter:
    base = Porter.load()
    fm = {**base.func_map, **src.funcs}
    for k in src.empty:
        fm.pop(k, None)
    pt = Porter(base.ram, fm, base.native)
    pt.is_empty = lambda f: f not in fm and _read_or_none(src._code_r, f) == _EMPTY
    pt.newvars = newvars
    pt.source = lambda fn: src.text(fn) or base.source(fn)
    from . import sprite_ids
    ids = {**{k: v for k, v in src.sprite_learned.items() if v is not None}, **src.sprite_ids}
    pt.sprites = (ids, sprite_ids.sprite_routines(src.index, src.sprite_learned_table))
    return pt


_ARC_CALLS: list[tuple[int, int]] | None = None
_ARC_PAT = re.compile(r"^([0-9a-f]{6})\s+\S+\s+(?:jsr|jmp|bsr(?:\.[bw])?|bra(?:\.[bw])?)\s+\$([0-9a-f]+)")
_UNIT_PAT = re.compile(r"^\t(?:jsr|jmp|bra)\t(?:\$([0-9a-f]+)(?:\.l)?|(F_[0-9a-f]+))\s*(?:;.*)?$")


def _arcade_calls() -> list[tuple[int, int]]:
    global _ARC_CALLS
    if _ARC_CALLS is None:
        from .port import WORK
        _ARC_CALLS = []
        with open(WORK / "pgm" / "dis" / "p_rom.lst") as f:
            for line in f:
                m = _ARC_PAT.match(line)
                if m:
                    _ARC_CALLS.append((int(m.group(1), 16), int(m.group(2), 16)))
    return _ARC_CALLS


def call_mismatch(text: str, fn: int, start: int, end: int, calls: list[tuple[int, int]] | None = None) -> str | None:
    """The unit's own calls/jumps out of its 68k region against the arcade region's, in order
    (a ported callee `F_` stands for whatever the arcade calls there). With the same count, a
    different address at one position is a wrong learned anchor (near-identical 68k copies,
    see verify.FUNC_FIXES) or a real Arrange change the port cannot tell apart: refused.
    `calls`: (address, target) of the arcade calls/jumps in address order."""
    calls = _arcade_calls() if calls is None else calls
    keys = [a for a, _ in calls]
    i, j = bisect.bisect_left(keys, start), bisect.bisect_left(keys, end)
    arc = [t for _, t in calls[i:j] if not start <= t < end]
    own, inside = [], False
    for line in text.splitlines():
        if re.match(r"^F_[0-9a-f]+:", line):
            inside = line == f"F_{fn:x}:"
            continue
        m = _UNIT_PAT.match(line) if inside else None
        if m and (m.group(2) or not start <= int(m.group(1), 16) < end):
            own.append(None if m.group(2) else int(m.group(1), 16))
    if len(own) != len(arc):
        return None
    for x, y in zip(own, arc):
        if x is not None and x != y:
            return f"calls ${x:x} where the arcade calls ${y:x}"
    return None


def overlay_units(src: "OverlaySource", newvars: NewVars, trusted_only: bool = True) -> tuple[list[dict], dict]:
    """Ported, assemblable units of one overlay pair: [{"orig", "end", "fn", "text"}], failures.
    Call twice with the same NewVars: first to collect Arrange-only variables (before
    newvars.assign()), then to produce the final text."""
    from ..pgm import asm68k
    pt = overlay_porter(src, newvars)
    units, failed = [], {}
    for fn, orig in sorted(src.roots.items(), key=lambda kv: kv[1]):
        if trusted_only and not src.trust.get(fn):
            failed[fn] = src.distrust.get(fn, "not trusted")
            continue
        if src.layout_only.get(fn):
            failed[fn] = "no change beyond relocation/record layout (original kept)"
            continue
        if fn in src.needs_layout:
            failed[fn] = "needs the Arrange record layout: " + src.needs_layout[fn]
            continue
        src.text(fn)
        if src.trust_why.get(fn) == "PS2-verified" and src.guessed.get(fn):
            # the Arrange RAM layout is not the arcade's everywhere: an address interpolated
            # between samples may be another variable (the PS2 check does not see addresses)
            failed[fn] = "RAM address only interpolated: " + ", ".join(f"{v:#x}" for v in sorted(src.guessed[fn]))
            continue
        text, res = pt.port_unit([fn])
        probs = [f"{f:#x}: {p}" for f, r in res.items() for p in r.problems]
        if src.trust_why.get(fn) == "PS2-verified":
            probs += [f"{f:#x}: RAM address only interpolated: " + ", ".join(f"{v:#x}" for v in sorted(src.guessed[f]))
                      for f in res if f != fn and src.guessed.get(f)]
        if probs:
            failed[fn] = probs[0]
            continue
        start, end, last_term = src.region[fn]
        why = call_mismatch(text, fn, start, end)
        if why:
            failed[fn] = why
            continue
        if not last_term:
            text = _region_exit(text, end, src.index, src.trust_info[fn][5], root=fn, continues=src.continues)
            if text is None:
                failed[fn] = "returns where its 68k region continues (routine split differently)"
                continue
            text += f"\tjmp\t${end:x}.l\n"      # the 68k code falls through to the next fragment
        try:
            asm68k.assemble(text, 0x200000)
        except Exception as e:  # noqa: BLE001
            failed[fn] = f"asm: {e}"
            continue
        ents = list(src.entries.get(fn, []))
        split = _flag_split_entry(src.text(fn) or "", start, end, src.index)
        if split is False:
            failed[fn] = "PS2 flag-merged routine whose flag-set part is not its 68k start"
            continue
        if split is not None:
            ents.append(split)
        if any(f"L_{a:x}:" not in text for _, a in ents):
            failed[fn] = "an entry point has no label in the ported text"
            continue
        units.append({"orig": orig, "end": end, "fn": fn, "text": text, "trust": src.trust_why.get(fn),
                      "entries": [[e, f"L_{a:x}"] for e, a in ents]})
    return units, failed


NEWVAR_BASE, NEWVAR_SIZE = 0xE09800, 0x6800     # enhanced RAM for Arrange-only variables


def port_all(pairs: list[tuple[str, str]], cache: Path | None = None, arrange_records: bool = False) -> dict:
    """Port every trusted unit of the given overlay pairs (shared Arrange-only variables).
    Returns {"units": [...], "failed": {...}, "newvars": {arr: pgm}}; cached in `cache`."""
    import hashlib
    from .verify import Index, load_listing
    from .port import WORK
    here = Path(__file__).parent
    key = hashlib.sha1(("".join(sorted(f.read_text() for f in here.glob("*.py"))) + repr(pairs)
                        + repr(arrange_records)
                        + (VERIFIED.read_text() if VERIFIED.exists() else "")
                        + (MAP_AUDIT.read_text() if MAP_AUDIT.exists() else "")).encode()).hexdigest()
    if cache and cache.exists():
        d = json.loads(cache.read_text())
        if d.get("key") == key:
            return d
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    srcs = [(a, r, OverlaySource(a, r, index=index, arrange_records=arrange_records)) for a, r in pairs]
    nv = NewVars(NEWVAR_BASE, NEWVAR_SIZE)
    for _, _, src in srcs:
        overlay_units(src, nv)
    nv.assign()
    units, failed = [], {}
    for a, r, src in srcs:
        u, f = overlay_units(src, nv)
        for x in u:
            x["overlay"] = r
        units += u
        failed.update({f"{r}:{k:#x}": v for k, v in f.items()})
    d = {"key": key, "units": units, "failed": failed,
         "newvars": {f"{k:#x}": f"{v:#x}" for k, v in sorted(nv.map.items())}}
    if cache:
        cache.write_text(json.dumps(d, indent=0))
    return d
