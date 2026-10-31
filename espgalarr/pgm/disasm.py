"""Linear 68k listing of the program ROM (work/pgm/dis/p_rom.lst).

One line per instruction: `aaaaaa  <bytes hex, 24 wide>  <mnemonic operands>` (capstone),
`.byte 0xhh, 0xhh` for a word capstone does not decode. A plain linear sweep runs out of step
after data inside the code (pointer tables, sprite records) and can swallow the first
instructions of the routine behind it into a long bogus instruction; `entries` (known routine
starts and branch/call targets hidden that way) resynchronise it: an instruction that would run over an entry
is printed as `.byte` words up to the entry instead.

    python3 -m espgalarr.pgm.disasm [--plain]     writes work/pgm/dis/p_rom.lst
"""
from __future__ import annotations

import bisect
import json
import re
import sys
from pathlib import Path

WORK = Path("work")


def p_rom(work: Path = WORK) -> bytes:
    raw = (work / "pgm" / "espgalbl" / "espgaluda_u8.bin").read_bytes()
    b = bytearray(raw)
    b[0::2], b[1::2] = raw[1::2], raw[0::2]     # stored byte-swapped
    return bytes(b)


def _byte_line(pc: int, b: bytes) -> str:
    return f"{pc:06x}  {b[pc:pc + 2].hex():<24}  .byte 0x{b[pc]:02x}, 0x{b[pc + 1]:02x}"


def listing(b: bytes, entries: set[int] | frozenset = frozenset(), end: int | None = None):
    """Yield the listing lines of `b` from 0 to `end`."""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
    end = len(b) if end is None else end
    ent = sorted(e for e in entries if e % 2 == 0)
    pc = 0
    while pc < end:
        ins = next(md.disasm(b[pc:pc + 24], pc, 1), None)
        if ins is not None:
            k = bisect.bisect_right(ent, pc)
            if k < len(ent) and ent[k] < pc + ins.size:
                ins = None          # would run over an entry: words up to it
        if ins is None:
            yield _byte_line(pc, b)
            pc += 2
        else:
            yield f"{pc:06x}  {ins.bytes.hex():<24}  {ins.mnemonic} {ins.op_str}"
            pc += ins.size


_BRANCH = re.compile(r"^([0-9a-f]{6})\s+\S+\s+(?:bsr|jsr|jmp|bra|b[a-z]{2}|db[a-z]+)(?:\.[bw])?\s+.*?\$([0-9a-f]+)\s*$")
_TERM = ("rts", "rte", "jmp", "bra", "bra.w", "bra.b")


def _runs_to_terminator(b: bytes, pc: int, n: int = 40) -> bool:
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
    for _ in range(n):
        ins = next(md.disasm(b[pc:pc + 24], pc, 1), None)
        if ins is None:
            return False
        if ins.mnemonic in _TERM:
            return True
        pc += ins.size
    return True


def _data_like(text: str) -> bool:
    """A listing instruction that is data read as code (an `ori #imm` is opcode $00xx)."""
    return text.startswith(("ori", ".byte", "dc.w")) or "invalid" in text


def entries(b: bytes, work: Path = WORK, lo: int = 0x100000, hi: int = 0x200000, max_rounds: int = 6) -> set[int]:
    """Resync points: routine starts (work/pgm/routines.json) and branch/call targets that the
    listing hides inside a data-like instruction and from which the code decodes to a
    terminator. Iterated, since a resynced region shows further branches."""
    known = {int(k, 16) for k in json.loads((work / "pgm" / "routines.json").read_text())}
    ent: set[int] = set()
    for _ in range(max_rounds):
        lines = list(listing(b, ent, end=hi))
        addrs = [int(x[:6], 16) for x in lines]
        starts = set(addrs)
        cand = {t for t in known if lo <= t < hi and t % 2 == 0}
        for x in lines:
            m = _BRANCH.match(x)
            if m:
                t = int(m.group(2), 16)
                if lo <= t < hi and t % 2 == 0:
                    cand.add(t)
        new = set()
        for t in cand - starts - ent:
            i = bisect.bisect_right(addrs, t) - 1
            if i >= 0 and _data_like(lines[i][34:]) and _runs_to_terminator(b, t):
                new.add(t)
        if not new:
            break
        ent |= new
    return ent


def main(argv: list[str]) -> int:
    b = p_rom()
    ent = set() if "--plain" in argv else entries(b)
    out = WORK / "pgm" / "dis" / "p_rom.lst"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for line in listing(b, ent, end=0x200000):
            f.write(line + "\n")
    print(f"{out}: {len(ent)} resync entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
