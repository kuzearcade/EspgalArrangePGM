"""Allocator bursts (espgalarr/pgm/allocburst.py): which calls the assembler rewrites, and the
entries' fixed layout."""
import capstone
import pytest

from espgalarr.pgm import allocburst, asm68k

H, N = allocburst.head, allocburst.next_


@pytest.fixture
def enabled():
    allocburst.ENABLED = True
    yield
    allocburst.ENABLED = False


def _calls(src: str) -> list[int]:
    code, _ = asm68k.assemble(src, 0x200000)
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_000)
    return [int(i.op_str.lstrip("$").split(".")[0], 16) for i in md.disasm(code, 0x200000) if i.mnemonic == "jsr"]


def test_chains(enabled):
    # a helper with an optional extra shot (a label on the second call) and a volley loop
    # calling it three times an iteration: only the volley's first allocation scans from slot 0
    src = """
volley: moveq   #3,d7
loop:   move.b  $1b(a2),d1
        jsr     F_shot
        addi.w  #$78,d1
        jsr     F_shot
        subi.w  #$f0,d1
        bsr     F_shot
        lea     $2c(a2),a2
        dbra    d7,loop
        move.w  d0,(a0)
        jsr     F_shot
        rts
F_shot: cmpi.w  #2,$813a18.l
        bne     one
        swap    d0
        jsr     $1713ec.l
        swap    d0
one:    jsr     $17135e.l
        rts
"""
    lines = asm68k.Assembler()._preprocess(src)
    for kakusei in (False, True):
        kinds = []
        dec = [d for it in range(4) for d in [not kakusei] * 3 + [it < 3]] + [not kakusei]
        _run(allocburst.rewrite(lines), dec, entry="volley", kinds=kinds)
        per = 2 if kakusei else 1
        assert kinds == ["H"] + ["N"] * (12 * per - 1) + ["H"] + ["N"] * (per - 1)
    asm68k.assemble(src, 0x200000)


def test_off_by_default():
    assert _calls("        jsr $17135e.l\n        jsr $17135e.l\n") == [0x17135E, 0x17135E]


def test_pure():
    for line in ("swap d0", "tst.w (a0)", "addi.b #$10,d1", "movea.l $8(a7),a0", "lea -$c(a7),a7",
                 "move.w #$81,d1", "and.b $d(a5),d1", "btst.b #1,(a0)", "bne.w x", "dbra d7,x", "exg d0,d1",
                 "move.l d0,$0(a7)", "movem.l d0-d1,-(a7)", "move.w d0,$e0b6ec.l", "clr.w $813a18.l", "pea (a0)"):
        mnem, _, args = line.partition(" ")
        assert allocburst.pure(mnem, args.strip()), line
    for line in ("clr.w (a6)", "move.w d0,$d00040.l", "move.l d0,4(a0)", "addq.w #1,VAR",
                 "jsr (a0)", "bsr x", "rts", "bra x", "jmp $100.l", "dc.w $4e75", "st (a0)"):
        mnem, _, args = line.partition(" ")
        assert not allocburst.pure(mnem, args.strip()), line


def test_entries():
    code, syms = asm68k.assemble(allocburst.entry_asm(0xD00000, 0x818352), allocburst.ENTRY_BASE)
    assert len(code) <= allocburst.ENTRY_SIZE
    for a in allocburst.ALLOCATORS:
        assert syms[f"head_{a:x}"] == H(a) and syms[f"next_{a:x}"] == N(a)


# ---- the specialisation keeps every path's instructions and only resumes after a clean line

def _program(rng):
    """Random functions of pure / impure / allocator / branch / call lines (ids in immediates)."""
    nf, out, uid = 4, [], [0]

    def nid():
        uid[0] += 1
        return uid[0]
    for f in range(nf):
        body, labels = [], [f"L_{f}_{k}" for k in range(4)]
        for _ in range(rng.randint(4, 14)):
            r = rng.random()
            if r < 0.3:
                body.append(("moveq", f"#{nid() % 128},d7", None))      # pure (the id goes in the trace)
            elif r < 0.45:
                body.append(("jsr", rng.choice(["$17135e.l", "$1713ec.l", "$170f36.l", "$170fb8.l"]), None))
            elif r < 0.55:
                body.append(("move.w", f"#{nid()},(a0)", None))          # impure
            elif r < 0.65:
                body.append(("move.w", f"#{nid()},-(a7)", None))         # the stack: pure
            elif r < 0.75:
                body.append((rng.choice(["bne", "beq", "dbra d6,"]), rng.choice(labels), None))
            elif r < 0.8:
                body.append(("bra", rng.choice(labels), None))
            elif r < 0.9 and f + 1 < nf:
                body.append((rng.choice(["jsr", "bsr"]), f"F_{rng.randint(f + 1, nf - 1)}", None))
            else:
                body.append(("rts", "", None))
        body.append(("rts", "", None))
        lines = [(f"F_{f}", "", "")]
        placed = rng.sample(range(len(body)), len(labels))
        for k, (m, a, _) in enumerate(body):
            lab = labels[placed.index(k)] if k in placed else None
            if m.startswith("dbra"):
                m, a = "dbra", "d6," + a
            lines.append((lab, m, a))
        out += lines
    return out


def _run(lines, decisions, entry="F_0", limit=150, kinds=None):
    labels = {lab: i for i, (lab, m, _) in enumerate(lines) if lab}
    nexts = {N(a) for a in allocburst.ALLOCATORS}
    heads = {H(a) for a in allocburst.ALLOCATORS}
    trace, stack, i, d, clean = [], [], labels[entry], iter(decisions), False
    for _ in range(50 * limit):
        if i >= len(lines) or len(trace) >= limit:
            break
        lab, m, a = lines[i]
        base = m.split(".")[0]
        if not m:
            i += 1
        elif base == "jsr" and a.startswith("$"):
            t = int(a[1:].split(".")[0], 16)
            if t in nexts:
                assert clean, "a next call after a write or with no allocation before it"
            if kinds is not None:
                kinds.append("N" if t in nexts else "H" if t in heads else "P")
            trace.append("A")
            clean = True
            i += 1
        elif base in ("jsr", "bsr"):
            stack.append(i + 1)
            i = labels[a]
        elif base == "rts":
            if not stack:
                break
            i = stack.pop()
        elif base == "bra":
            i = labels[a]
        elif base in ("bne", "beq", "dbra"):
            i = labels[a.split(",")[-1]] if next(d) else i + 1
        else:
            trace.append(a)
            if a.endswith("(a0)"):
                clean = False
            i += 1
    return trace


def test_specialisation_paths(enabled):
    import random
    rng = random.Random(7)
    for _ in range(300):
        lines = _program(rng)
        new = allocburst.rewrite(lines)
        for _ in range(5):
            dec = [rng.random() < 0.5 for _ in range(400)]
            allocburst.ENABLED = False
            want = _run(lines, dec)
            allocburst.ENABLED = True
            assert _run(new, dec) == want
        asm68k.assemble("\n".join(f"{lab + ':' if lab else ''}\t{m}\t{a}" for lab, m, a in lines), 0x200000)
