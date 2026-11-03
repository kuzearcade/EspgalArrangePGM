"""prof68k.py TRACE [P_ALLOC] [--top N]: cycle profile of a MAME 68k instruction trace.

The trace comes from the debugger's `trace FILE,maincpu,noloop,{tracelog "%x ",totalcycles}`
(work/scratch/proftrace.lua): every instruction line starts with the total cycle count, so an
instruction costs the difference to the next line (interrupt entry included). Routines are the
trace's own call targets: a jsr / bsr target, a jmp target (tail calls, the build's hooks) and an
interrupt handler label the code until its rts / rte. Prints, over the whole trace:
  - cycles per frame (FRAME markers from proftrace.lua);
  - exclusive cycles per routine (time in its own code) and inclusive (with its callees);
  - per owner: the build's added blocks (P_ALLOC = OUT/p_alloc.txt: start size note) and
    "arcade" for the original ROM (< 0x200000).
Addresses only; nothing from the ROM is printed but mnemonics of the hottest instructions.
"""
import bisect
import collections
import re
import sys

LINE = re.compile(r"^([0-9a-f]+) ([0-9A-F]+): (\S+)\s*(.*)$")


def owners(path):
    rows = []
    if path:
        for l in open(path):
            a, n, note = l.split(" ", 2)
            rows.append((int(a, 16), int(n, 16), note.strip()))
    rows.sort()
    starts = [r[0] for r in rows]

    def owner(pc):
        if pc < 0x200000:
            return "arcade"
        i = bisect.bisect_right(starts, pc) - 1
        if i >= 0 and pc < rows[i][0] + rows[i][1]:
            return rows[i][2]
        return "added (unlisted)"
    return owner


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = 40
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
        args = [a for a in args if a != str(top)]
    owner = owners(args[1] if len(args) > 1 else None)
    excl = collections.Counter()      # routine -> cycles in its own code
    incl = collections.Counter()      # routine -> cycles with callees
    calls = collections.Counter()
    insn = collections.Counter()      # pc -> cycles
    mnem = {}
    by_owner = collections.Counter()
    frames = []
    stack = ["?"]
    prev = None                       # (cycles, pc, mnemonic)
    pending = None                    # how the next instruction's pc changes the stack
    total = 0
    fstart = None
    for l in open(args[0], errors="replace"):
        if l.startswith("FRAME"):
            if prev is not None:
                if fstart is not None:
                    frames.append(prev[0] - fstart)
                fstart = prev[0]
            continue
        if "interrupted at" in l:
            pending = "irq"
            continue
        m = LINE.match(l.strip())
        if not m:
            continue
        cyc, pc, mn = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
        if prev is not None:
            dc = cyc - prev[0]
            if 0 <= dc < 100000:
                total += dc
                excl[stack[-1]] += dc
                for r in set(stack):
                    incl[r] += dc
                insn[prev[1]] += dc
                by_owner[owner(prev[1])] += dc
        # stack update for this instruction's arrival
        if pending == "call" or pending == "irq":
            stack.append(pc); calls[pc] += 1
        elif pending == "jump":
            stack[-1] = pc; calls[pc] += 1
        elif pending == "ret":
            if len(stack) > 1:
                stack.pop()
        pending = None
        if mn.startswith(("jsr", "bsr")):
            pending = "call"
        elif mn.startswith("jmp"):
            pending = "jump"
        elif mn.startswith(("rts", "rte", "rtr")):
            pending = "ret"
        if len(stack) > 200:
            stack = stack[-50:]
        mnem[pc] = mn + " " + m.group(4)[:40]
        prev = (cyc, pc, mn)
    nf = len(frames) + 2 if frames else 1      # (the trace runs from one frame start to another)
    print(f"{total} cycles, {nf} frames, {total / nf:.0f} cycles per frame"
          f" (frames min {min(frames, default=0)} max {max(frames, default=0)})")
    print("\nper owner (cycles per frame, share):")
    for o, c in by_owner.most_common(30):
        print(f"  {c / nf:9.0f} {100 * c / total:5.1f}%  {o}")
    print(f"\nexclusive, top {top} routines (cycles per frame, share, calls per frame, owner):")
    for r, c in excl.most_common(top):
        lab = f"{r:06x}" if isinstance(r, int) else r
        print(f"  {lab}  {c / nf:9.0f} {100 * c / total:5.1f}%  {calls[r] / nf:7.1f}  "
              f"{owner(r) if isinstance(r, int) else ''}")
    print(f"\ninclusive, top {top} routines:")
    for r, c in incl.most_common(top):
        lab = f"{r:06x}" if isinstance(r, int) else r
        print(f"  {lab}  {c / nf:9.0f} {100 * c / total:5.1f}%  {calls[r] / nf:7.1f}  "
              f"{owner(r) if isinstance(r, int) else ''}")
    print("\nhottest instructions:")
    for pc, c in insn.most_common(25):
        print(f"  {pc:06x}  {c / nf:8.0f} {100 * c / total:5.1f}%  {mnem.get(pc, '')}")


if __name__ == "__main__":
    main()
