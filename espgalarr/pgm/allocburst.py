"""Allocator bursts: back-to-back bullet allocations resume the slot scan (spec/timing.md, "40 MHz
audit", candidate 1).

The four arcade allocators (0x17135E / 0x1713EC / 0x170F36 / 0x170FB8) each scan the enemy
bullet pool from slot 0 for the first free slot (status word 0). Our generated code often calls
them several times in a row with only register arithmetic in between: the Kakusei extra-shot
helpers, the restructured volleys. Nothing can free a slot between two such calls (the
allocators' own set-up and init hooks write only the new slot; the IRQ handlers do not touch the
pool), so after a call that took slot k (or found none), every slot below k is still occupied
when the next call starts: scanning from slot k gives the same slot, the same record, the same
flags and registers, at a fraction of the cost.

- The assembler (asm68k) rewrites every source text it assembles that calls an allocator
  (`rewrite`). Each line has two states: N (as written) and C (an allocator call ran since the
  last instruction that is not `pure`). Pure instructions write only registers, the stack or
  memory outside the pool and do not call out of the text; conditional branches, branches and
  calls within the text keep the state. The text is emitted twice: the original lines (state N;
  allocator calls become heads, followed by a branch into the C copy) and, appended, a copy of
  every line reached in state C (allocator calls become next calls; in-text calls go to the
  callee's C copy; the first impure instruction branches back to the N lines). A call to an
  in-text function continues in C when every return of the function, entered in that state, is
  in C (summaries to a fixed point). So a helper whose second call has a label (the Kakusei
  extra shot skipped) and a volley loop calling it again and again run on one cursor: only the
  volley's first allocation scans from slot 0. Original labels stay on the N lines, so other
  texts, tables and computed jumps enter as before. tests/test_allocburst.py runs random
  programs both ways and checks the paths and the condition of every next call.
- A head scans from slot 0 as the allocator does; a next call scans from the cursor. Both leave
  the cursor at the slot they took (not after it: a slot is rescanned rather than trusted) or at
  the pool's end when it is full, then finish through the allocator's own tails. Both run the
  allocator's gate (0x8139F6 + 0x8139F4) first, which leaves the cursor alone.
- The entries sit at a fixed address (ENTRY_BASE) so any text can be rewritten without knowing
  where the build placed things: per allocator, head = ENTRY_BASE + 24 * i, next = + 12.
- The cursor is a long in the arcade's old pool RAM (CURSOR): the pool moved to the cart's
  shared RAM (layout.py) and nothing references the old range any more (install checks).
"""
from __future__ import annotations

import re
import struct

ALLOCATORS = (0x17135E, 0x1713EC, 0x170F36, 0x170FB8)
ENTRY_BASE = 0x3FFC00                 # top of the program ROM, below nothing the build places
ENTRY_SIZE = 0x200
CURSOR = 0x818358                     # (0x818352 / 0x818354 hold the Arrange walker counts)
# per allocator: (gate tail, fail tail, found path); the gate tail returns with the gate's flags,
# the fail tail with carry set, the found path takes a0 = the slot and the movem frame on the stack
TAILS = {0x17135E: (0x1713DA, 0x1713D0, 0x1713E0), 0x1713EC: (0x171468, 0x17145E, 0x17146E),
         0x170F36: (0x170FB2, 0x170FA8, 0x170F2A), 0x170FB8: (0x171034, 0x17102A, 0x17103A)}
# the allocators' entry and scan (pool base and group count addresses masked) and found paths
# (the ROM after the layout stage)
ENTRY_SIG = "x" * 0x14 + "?" * 4 + "xx" + "?" * 4 + "x" * 0x62
SIGS = {0x17135E: "745ac970576afb9e", 0x1713EC: "745ac970576afb9e",
        0x170F36: "4c9d44215714b79b", 0x170FB8: "745ac970576afb9e"}
FOUND_SIGS = {0x1713E0: "@10:43ae08d62ebf9157", 0x17146E: "@6:55fa79f5d15158d1",
              0x170F2A: "@10:43ae08d62ebf9157", 0x17103A: "@6:55fa79f5d15158d1"}
TAIL_SIG = "@4:b2418f86cc72dcaf"      # the gate and fail tails start by restoring d7/a0/a1
# The rewrite is on while a build with the relocated pool runs its code stage (stages/code.py
# sets it; ESPGAL_NO_ALLOC_BURSTS=1 keeps it off, for A/B runs).
ENABLED = False


def head(alloc: int) -> int:
    return ENTRY_BASE + 24 * ALLOCATORS.index(alloc)


def next_(alloc: int) -> int:
    return head(alloc) + 12


# ------------------------------------------------------------------ source rewriting

_JSR_TARGET = re.compile(r"^(?:\$([0-9a-fA-F]+)|orig_([0-9a-fA-F]{6}))(?:\.l)?$")
_REG = re.compile(r"^(?:[da][0-7]|sp)$", re.I)
# instructions that only read their operands
_READ_ONLY = {"tst", "cmp", "cmpi", "cmpa", "cmpm", "btst", "chk", "nop"}
# instructions whose last operand is the only thing written (pure when it is a register)
_WRITES_LAST = {"move", "movea", "moveq", "lea", "add", "adda", "addi", "addq", "addx", "sub", "suba",
                "subi", "subq", "subx", "and", "andi", "or", "ori", "eor", "eori", "neg", "negx",
                "not", "clr", "ext", "swap", "lsl", "lsr", "asl", "asr", "rol", "ror", "roxl", "roxr",
                "mulu", "muls", "divu", "divs", "bset", "bclr", "bchg", "exg", "movem", "abcd", "sbcd"}
_CC = ("cc", "cs", "eq", "ne", "ge", "gt", "le", "lt", "hi", "ls", "mi", "pl", "vc", "vs", "hs", "lo")


def _operands(args: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in args:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _is_reglist(op: str) -> bool:
    return all(re.fullmatch(r"[da][0-7](?:-[da][0-7])?", r.strip(), re.I) for r in op.split("/"))


def alloc_target(mnem: str, args: str) -> int | None:
    """The allocator a `jsr` line calls, or None."""
    if mnem not in ("jsr", "jsr.l"):
        return None
    m = _JSR_TARGET.match(args.strip())
    if not m:
        return None
    a = int(m.group(1) or m.group(2), 16)
    return a if a in ALLOCATORS else None


POOL_RAM = (0xD00000, 0xD10000)       # the cart's shared RAM, where the relocated pool lives


def _safe_dest(op: str) -> bool:
    """A written operand that cannot be a pool slot: a register, the stack, or an absolute
    address outside the shared RAM."""
    op = op.strip()
    if _REG.match(op):
        return True
    if re.fullmatch(r"(?:-?\(\s*(?:a7|sp)\s*\)\+?|[$\w+\-*]*\(\s*(?:a7|sp)\s*(?:,\s*[da][0-7](?:\.[wl])?)?\))", op, re.I):
        return True
    m = re.fullmatch(r"\$([0-9a-fA-F]+)(\.[wl])?", op)
    if m:
        a = int(m.group(1), 16)
        if m.group(2) == ".w" or (m.group(2) is None and a < 0x8000):
            a = a - 0x10000 if a & 0x8000 else a
            a &= 0xFFFFFF
        return not (POOL_RAM[0] <= a < POOL_RAM[1])
    return False


def pure(mnem: str, args: str) -> bool:
    """True when the instruction cannot free a pool slot (it writes only registers, the stack or
    memory outside the pool) and keeps control on the line or branches off it conditionally."""
    base = mnem.split(".")[0]
    if base in _READ_ONLY:
        return True
    if (base.startswith("b") and base[1:] in _CC) or (base.startswith("db") and (base[2:] in _CC or base == "dbra")):
        return True                                    # conditional branches, dbcc
    ops = _operands(args)
    if base in ("pea", "link", "unlk"):
        return True                                    # the stack
    if base.startswith("s") and base[1:] in _CC + ("t", "f"):
        return len(ops) == 1 and _safe_dest(ops[0])
    if base not in _WRITES_LAST or not ops:
        return False
    if base == "exg":
        return True
    if base == "movem" and _is_reglist(ops[-1]):
        return True
    return _safe_dest(ops[-1])


# Two copies of a text: as written (state N: no chain) and, appended, the lines reached right
# after an allocation with only pure instructions since (state C: the cursor is good). See the
# module docstring.
N, C = 0, 1
_LABEL = re.compile(r"^[A-Za-z_.][A-Za-z0-9_.]*$")
_ENDS = {"rts", "rte", "rtr", "jmp", "trap", "stop", "illegal", "reset"}


def _branch(base: str) -> str | None:
    if base == "bra":
        return "jump"
    if base == "bsr":
        return "call"
    if (base.startswith("b") and base[1:] in _CC) or (base.startswith("db") and (base[2:] in _CC or base == "dbra")):
        return "cond"
    return None


def _classify(mnem: str, args: str, labels: dict) -> tuple[str, int | None, bool]:
    """(kind, in-text target line, copyable) of one line."""
    if not mnem:
        return "nop", None, True
    if mnem == ".equ":
        return "nop", None, True
    if mnem.startswith(".") or mnem.split(".")[0] in ("dc", "ds", "dcb"):
        return "barrier", None, False
    base = mnem.split(".")[0]
    ops = _operands(args)
    copyable = not (re.search(r"\bpc\b", args, re.I) and not all(
        re.fullmatch(r"[A-Za-z_.][\w.]*\(pc(?:,[^)]*)?\)", o, re.I) for o in ops if re.search(r"\bpc\b", o, re.I)))
    tgt = labels.get(ops[-1]) if ops and _LABEL.match(ops[-1]) else None
    if base == "jsr":
        if alloc_target(mnem, args) is not None:
            return "alloc", None, True
        return ("call", tgt, copyable) if tgt is not None else ("impure", None, copyable)
    kind = _branch(base)
    if kind == "call":
        return ("call", tgt, copyable) if tgt is not None else ("impure", None, copyable)
    if kind == "jump":
        return ("jump", tgt, copyable) if tgt is not None else ("end", None, copyable)
    if kind == "cond":
        return "cond", tgt, copyable
    if base in _ENDS:
        return "end", None, copyable
    return ("pure" if pure(mnem, args) else "impure"), None, copyable


def rewrite(lines: list[tuple]) -> list[tuple]:
    """The assembler's (label, mnemonic, args) lines with the allocator calls specialised: the
    original lines (state N, allocator calls as heads) and a copy of every line reached in
    state C (allocator calls as next calls, in-text calls to their C copies)."""
    if not ENABLED or not any(alloc_target(m, a) is not None for _, m, a in lines):
        return lines
    if any(m == ".org" for _, m, _ in lines):
        return lines
    n = len(lines)
    labels = {lab: i for i, (lab, m, _) in enumerate(lines) if lab is not None and m != ".equ"}
    K = [_classify(m, a, labels) for _, m, a in lines]
    funcs = sorted({t for k, t, _ in K if k == "call"})
    exits = {(f, s): N for f in funcs for s in (N, C)}

    def succ(i, s):
        k, t, cp = K[i]
        nx = i + 1 if i + 1 < n else None
        if s == C and not cp:
            return [(i, N)]
        if k in ("nop", "pure"):
            return [(nx, s)]
        if k == "alloc":
            return [(nx, C)]
        if k == "impure":
            return [(nx, N)]
        if k == "cond":
            return ([(t, s)] if t is not None else []) + [(nx, s)]
        if k == "jump":
            return [(t, s)]
        if k == "call":
            return [(nx, exits[(t, s)])]
        return []

    def walk(seeds, into_calls):
        seen, stack = set(), list(seeds)
        while stack:
            x = stack.pop()
            if x[0] is None or x in seen:
                continue
            seen.add(x)
            i, s = x
            if into_calls and K[i][0] == "call" and not (s == C and not K[i][2]):
                stack.append((K[i][1], s))
            stack.extend(succ(i, s))
        return seen

    def exit_state(f, s):
        ends = set()
        for i, st in walk([(f, s)], False):
            k, t, cp = K[i]
            if st == C and not cp:
                continue
            if k == "end":
                ends.add(st if lines[i][1].split(".")[0] == "rts" else N)
            elif k == "barrier" or (k == "cond" and t is None):
                ends.add(N)
            elif k in ("nop", "pure", "alloc", "impure", "cond", "call") and i + 1 >= n:
                ends.add(N)                            # (falls off the text)
        return C if ends == {C} else N

    changed = True
    while changed:
        changed = False
        for key in exits:
            if exits[key] == N and exit_state(*key) == C:
                exits[key] = C
                changed = True
    R = walk([(0, N)] + [(i, N) for i in labels.values()], True)

    def cl(i):
        return f"__abc{i}"

    def nl(i):
        return f"__abn{i}"

    def retarget(args, lab):
        ops = _operands(args)
        return ",".join(ops[:-1] + [lab])

    def unsized(mnem):
        return re.sub(r"\.[sbw]$", "", mnem)

    need_n = set()
    for i, s in R:
        if s != C:
            continue
        k, t, cp = K[i]
        if not cp:
            need_n.add(i)
        elif i + 1 < n and (k == "impure" or (k == "call" and exits[(t, C)] == N)):
            need_n.add(i + 1)
    out = []
    for i, (lab, m, a) in enumerate(lines):
        if i in need_n:
            out.append((nl(i), "", ""))
        k, t, _ = K[i]
        if (i, N) in R and k == "alloc":
            out.append((lab, m, f"${head(alloc_target(m, a)):x}.l"))
            if i + 1 < n:
                out.append((None, "bra.w", cl(i + 1)))
        elif (i, N) in R and k == "call" and exits[(t, N)] == C:
            out.append((lab, m, a))
            if i + 1 < n:
                out.append((None, "bra.w", cl(i + 1)))
        else:
            out.append((lab, m, a))
    for i, (lab, m, a) in enumerate(lines):
        if (i, C) not in R:
            continue
        out.append((cl(i), "", ""))
        k, t, cp = K[i]
        if not cp:
            out.append((None, "bra.w", nl(i)))
        elif k == "alloc":
            out.append((None, m, f"${next_(alloc_target(m, a)):x}.l"))
        elif k in ("cond", "jump") and t is not None:
            out.append((None, unsized(m), retarget(a, cl(t))))
        elif k == "call":
            out.append((None, unsized(m), retarget(a, cl(t))))
            if exits[(t, C)] == N and i + 1 < n:
                out.append((None, "bra.w", nl(i + 1)))
        elif k == "impure":
            out.append((None, m, a))
            if i + 1 < n:
                out.append((None, "bra.w", nl(i + 1)))
        elif k != "nop":
            out.append((None, m, a))
    return out


# ------------------------------------------------------------------ the entries

ENTRY_ASM = """
        .equ    POOL, ${pool:x}
        .equ    CAP, ${cap:x}             ; group count - 1 (10 slots a group)
        .equ    CURSOR, ${cursor:x}
{entries}
; a1 -> (gate tail, fail tail, found path) of the allocator; d7/a0/a1 saved as it does
burst_head:
        move.l  #POOL,CURSOR.l          ; (also when the gate holds: a later next call scans all)
        move.w  $8139f6.l,d7
        add.w   $8139f4.l,d7
        bne     burst_gate
        lea     POOL.l,a0
        move.w  CAP.l,d7
bh_loop:
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        dbra    d7,bh_loop
burst_full:
        move.l  a0,CURSOR.l
        movea.l 4(a1),a1
        jmp     (a1)
burst_found:
        move.l  a0,CURSOR.l
        movea.l 8(a1),a1
        jmp     (a1)
burst_gate:
        movea.l (a1),a1                 ; (keeps the gate's flags)
        jmp     (a1)
burst_next:
        move.w  $8139f6.l,d7
        add.w   $8139f4.l,d7
        bne     burst_gate
        movea.l CURSOR.l,a0
        move.w  CAP.l,d7                ; slots left: (CAP + 1) * 10 - (cursor - POOL) / $40
        addq.w  #1,d7
        mulu    #10*$40,d7
        add.l   #POOL,d7
        sub.l   a0,d7
        ble     burst_full
        lsr.l   #6,d7
        subq.w  #1,d7
bn_loop:
        tst.w   (a0)
        beq.s   burst_found
        lea     $40(a0),a0
        dbra    d7,bn_loop
        bra     burst_full
"""


def entry_asm(pool: int, cap: int) -> str:
    ent = []
    for k, a in enumerate(ALLOCATORS):
        g, f, fd = TAILS[a]
        for kind in ("head", "next"):
            ent.append(f"{kind}_{a:x}:\n        movem.l d7/a0-a1,-(a7)\n        lea     tails_{a:x}(pc),a1\n"
                       f"        bra.w   burst_{kind}\n")
    tails = "".join(f"tails_{a:x}:\n        dc.l    ${TAILS[a][0]:x},${TAILS[a][1]:x},${TAILS[a][2]:x}\n"
                    for a in ALLOCATORS)
    return ENTRY_ASM.format(pool=pool, cap=cap, cursor=CURSOR, entries="".join(ent) + tails)


def install(state, ps, p: bytes) -> str:
    """Place the entries at ENTRY_BASE (after checking the allocators they finish through)."""
    from . import asm68k, m68k
    pools, caps = set(), set()
    for a in ALLOCATORS:
        assert m68k.matches(p[a:a + len(ENTRY_SIG)], f"@{ENTRY_SIG}:{SIGS[a]}"), f"allocator {a:#x}"
        pools.add(struct.unpack_from(">I", p, a + 0x14)[0])
        caps.add(struct.unpack_from(">I", p, a + 0x1A)[0])
        assert struct.unpack_from(">H", p, a + 0x12)[0] == 0x41F9 and struct.unpack_from(">H", p, a + 0x18)[0] == 0x3E39
        fd = TAILS[a][2]
        assert m68k.matches(p[fd:], FOUND_SIGS[fd]), f"allocator {a:#x} found path {fd:#x}"
        for t in TAILS[a][:2]:
            assert m68k.matches(p[t:t + 4], TAIL_SIG), f"allocator {a:#x} tail {t:#x}"
    assert len(pools) == 1 and len(caps) == 1, "allocators disagree on the pool"
    pool, cap = pools.pop(), caps.pop()
    # the cursor's RAM: no absolute reference left in the old arcade pool range
    if pool != 0x818352:
        for i in range(0, len(p) - 4, 2):
            v = struct.unpack_from(">I", p, i)[0]
            assert not (CURSOR <= v < CURSOR + 4), f"cursor RAM referenced at {i:#x}"
    else:
        raise AssertionError("allocator bursts need the relocated bullet pool (the cursor lives in the old one)")
    code, syms = asm68k.assemble(entry_asm(pool, cap), ENTRY_BASE)
    assert len(code) <= ENTRY_SIZE and all(b == 0xFF for b in p[ENTRY_BASE:ENTRY_BASE + ENTRY_SIZE])
    for a in ALLOCATORS:
        assert syms[f"head_{a:x}"] == head(a) and syms[f"next_{a:x}"] == next_(a)
    ps.add(ENTRY_BASE, code, "allocator burst entries (head / next per allocator)")
    return f"allocator bursts: entries at {ENTRY_BASE:#x} ({len(code)} bytes), pool {pool:#x}, cursor {CURSOR:#x}"


def report(p: bytes) -> str:
    """Count the rewritten calls in the finished program ROM."""
    heads = {head(a) for a in ALLOCATORS}
    nexts = {next_(a) for a in ALLOCATORS}
    nh = nn = 0
    for i in range(0x200000, ENTRY_BASE, 2):
        if p[i:i + 2] == b"\x4e\xb9":
            t = struct.unpack_from(">I", p, i + 2)[0]
            nh += t in heads
            nn += t in nexts
    return f"allocator bursts: {nh} head calls, {nn} next calls resume the scan"
