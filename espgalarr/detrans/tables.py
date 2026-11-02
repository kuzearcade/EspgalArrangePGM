"""68k tables found by their contents (where the learned address map only guesses).

The PS2 overlays keep their own copies of the 68k ROM tables, not always at the offsets the
map interpolates between its samples (a pointer table with entries added, a table moved), so
a guessed ROM address may be a few bytes off or land on code. The words of the PS2 copy say
where the 68k table is:

  * a MIPS function address (little-endian) is the 68k routine it translates (big-endian);
  * a sprite number (the PS2 atlas index the translator put where the 68k has a graphics
    address, detrans/sprite_ids.py) is a graphics address: the known one when the table
    has it (or the word itself: a small value may be plain data), else an address in the A
    ROM range, increasing with the numbers;
  * a MIPS code address the map does not know is some 68k routine (an even address in the
    68k code);
  * any other word is the 68k word itself.

The 68k table is the one position in the program ROM where every word fits, with enough
evidence (pointers, known sprite numbers and non-zero verbatim words) to be unambiguous.
"""
from __future__ import annotations

import re
import struct

GFX_MIN, GFX_MAX = 0x80000, 0x1000000     # sprite graphics addresses (A ROM, 16 MB)
SPRITE_LO, SPRITE_HI = 0x1000, 0x8000     # where an unknown word is read as a sprite number
WORDS = 16                                # words of the PS2 copy compared
MIN_EVIDENCE = 3      # matched non-zero words (one not a sprite wildcard; one more without pointers/sprites)


def _kinds(words_le: list[int], funcs: dict, ids: dict | None, is_code=None) -> list[tuple]:
    out = []
    for w in words_le:
        be = struct.unpack(">I", struct.pack("<I", w))[0]
        if w in funcs:
            out.append(("ptr", funcs[w]))
        elif is_code is not None and is_code(w):
            out.append(("ptr?", w))              # a routine the map does not know
        elif ids is not None and be in ids:
            out.append(("sprite", be, ids[be]))
        elif ids is not None and SPRITE_LO <= be < SPRITE_HI:
            out.append(("sprite?", be))
        else:
            out.append(("word", be))
    return out


def find(addr: int, read_word, funcs: dict, rom: bytes, ids: dict | None = None,
         lo: int = 0, hi: int | None = None, is_code=None) -> int | None:
    """The 68k address (in rom[lo:hi], big-endian program ROM) of the table whose PS2 copy
    is at addr, or None (not found, ambiguous, or too little evidence)."""
    words = []
    for k in range(WORDS):
        w = read_word(addr + 4 * k)
        if w is None:
            break
        words.append(w)
    if len(words) < 2:
        return None
    kinds = _kinds(words, funcs, ids, is_code)
    # the shortest prefix with enough evidence gives the candidates; the table is the one
    # whose match runs furthest (the PS2 copy may differ further on: entries added, the next
    # table moved), and only if no other candidate matches that far
    n0, solid, strong, wild = None, 0, 0, 0
    for n, k in enumerate(kinds):
        solid += k[0] in ("ptr", "sprite") or k[0] == "word" and bool(k[1])
        strong += k[0] in ("ptr", "sprite")
        wild += k[0] == "sprite?"           # (an unknown code pointer only constrains)
        if (strong or wild) and solid >= 1 and solid + wild >= MIN_EVIDENCE or solid >= MIN_EVIDENCE + 1:
            n0 = n + 1
            break
    if n0 is None:
        return None
    cands = _search(kinds[:n0], rom, ids or {}, lo, hi, limit=64)
    if not cands or len(cands) >= 64:
        return None
    reach = {c: _reach(kinds, rom, c, ids or {}) for c in cands}
    best = max(reach.values())
    top = [c for c, r in reach.items() if r == best]
    return top[0] if len(top) == 1 else None


def _fits(k, w: int) -> bool:
    if k[0] in ("ptr", "word"):
        return w == k[1]
    if k[0] == "sprite":
        return w in (k[1], k[2])
    if k[0] == "ptr?":
        return CODE_LO <= w < CODE_HI and not w % 2
    return GFX_MIN <= w < GFX_MAX


def _reach(kinds: list[tuple], rom: bytes, at: int, ids: dict) -> int:
    """How many words of the PS2 copy the 68k words from `at` match."""
    pairs = []
    for n, k in enumerate(kinds):
        if at + 4 * n + 4 > len(rom):
            return n
        w = struct.unpack_from(">I", rom, at + 4 * n)[0]
        if not _fits(k, w):
            return n
        if k[0] == "sprite?":
            pairs.append((k[1], w))
            if not _consistent(pairs, ids):
                return n
    return len(kinds)


def _search(kinds: list[tuple], rom: bytes, ids: dict, lo: int, hi: int | None, limit: int = 2) -> list[int]:
    pat = b"".join(re.escape(struct.pack(">I", k[1])) if k[0] in ("ptr", "word")
                   else b"(?:" + re.escape(struct.pack(">I", k[2])) + b"|" + re.escape(struct.pack(">I", k[1])) + b")"
                   if k[0] == "sprite" else b"\\x00[\\x10-\\x1f].." if k[0] == "ptr?" else b"(.{4})"
                   for k in kinds)
    unknown = [k[1] for k in kinds if k[0] == "sprite?"]
    hits = []
    for m in re.finditer(pat, rom[lo:hi], re.S):
        if m.start() % 2:
            continue                       # (68k data is word-aligned)
        got = [struct.unpack(">I", g)[0] for g in m.groups()]
        if _consistent(list(zip(unknown, got)), ids):
            hits.append(lo + m.start())
            if len(hits) >= limit:
                break
    return hits


def _consistent(pairs: list[tuple[int, int]], ids: dict) -> bool:
    """Sprite numbers -> graphics addresses: >= GFX_MIN, one address per number, increasing
    with the numbers (also against the learned neighbours of each)."""
    seen: dict[int, int] = {}
    for n, a in pairs:
        if not GFX_MIN <= a < GFX_MAX or seen.get(n, a) != a:
            return False
        seen[n] = a
    known = sorted(ids.items())
    order = sorted(seen.items())
    if any(a1 >= a2 for (_, a1), (_, a2) in zip(order, order[1:])):
        return False
    import bisect
    keys = [k for k, _ in known]
    for n, a in order:
        i = bisect.bisect_left(keys, n)
        if i > 0 and known[i - 1][1] >= a or i < len(known) and known[i][1] <= a:
            return False
    return True


class TableMap:
    """An address map that places guessed program-ROM addresses by the table found there
    (find()); everything else as the base map."""

    def __init__(self, base, read_word, funcs: dict, rom: bytes, ids: dict | None = None, is_code=None):
        self._base, self._rd, self._funcs, self._rom, self._ids = base, read_word, funcs, rom, ids
        self._is_code = is_code
        self._cache: dict[int, int | None] = {}

    def __getattr__(self, name):
        return getattr(self._base, name)

    def map_const(self, v: int):
        r = self._base.map_const(v)
        if r[1] not in ("interp", "near", "") or (r[0] is not None and r[0] >= 0x200000) or self._rd(v) is None:
            if not self._contradicted(v, r[0]):
                return r
            # (a confident mapping whose ROM bytes are not the PS2's: the stage overlays share
            # their load address, so a sample learned from another stage can name another
            # stage's table, e.g. A0A3 0x708920 -> 0x1B2080, code, for the table at 0x199B7A)
        if v not in self._cache:
            self._cache[v] = find(v, self._rd, self._funcs, self._rom, self._ids, 0, 0x200000, self._is_code)
        t = self._cache[v]
        if t is None and self._contradicted(v, r[0]):
            # (too little evidence for find(): the PS2 bytes themselves, the longest prefix of
            # at least 0x10 bytes the ROM has; copies of the same table are interchangeable,
            # e.g. 0x199B7A = 0x199C62 up to their end)
            b = _bytes(self._rd, v, 0x40) or b""
            for n in range(len(b), 0xF, -2):
                k = self._rom.find(b[:n], CODE_LO, CODE_HI)
                if k >= 0:
                    t = self._cache[v] = k
                    break
        return (t, "table", 1.0) if t is not None else r

    def _contradicted(self, v: int, t) -> bool:
        """The PS2 data at v and the ROM at its mapped address t differ in their first 8 bytes
        (only for data addresses of the program ROM's code area, where the stage tables are)."""
        if t is None or not (CODE_LO <= t < CODE_HI) or (self._is_code and self._is_code(v)):
            return False
        b = _bytes(self._rd, v, 8)
        return b is not None and b != self._rom[t:t + 8]


CODE_LO, CODE_HI = 0x100000, 0x200000      # 68k program code (pointer fields of a table)
MAX_BYTES = 0x100
TAIL = 0x20                                 # 68k bytes kept after an emitted copy


def _bytes(read_word, a: int, n: int) -> bytes | None:
    out = b""
    while len(out) < n:
        w = read_word(a + len(out))
        if w is None:
            return None
        out += struct.pack("<I", w)
    return out[:n]


def layout(p_addr: int, y_addr: int, read_word, funcs: dict, rom: bytes, ids: dict | None,
           is_code=None) -> list[tuple]:
    """What the 68k table at y_addr holds, from its PS2 copy at p_addr (the arcade mode's), as
    (offset, kind, size): "word" the same 16-bit value, "sprite" a sprite number (32-bit) for
    its graphics address, "ptr" a MIPS function (32-bit, little-endian) for a 68k routine
    (`is_code(w)`: also one the map does not know). Stops at the first field that is none of
    these (where the PS2 copy is laid out differently, or the table ends)."""
    out, o = [], 0
    while o < MAX_BYTES and y_addr + o + 2 <= len(rom):
        b4 = _bytes(read_word, p_addr + o, 4)
        if b4 is None:
            break
        y4 = rom[y_addr + o:y_addr + o + 4]
        if b4[:2] == y4[:2] and (len(y4) < 4 or b4[2:] == y4[2:] or o % 4):
            out.append((o, "word", 2))
            o += 2
            continue
        if o % 4 == 0 and len(y4) == 4:
            y = struct.unpack(">I", y4)[0]
            be, le = struct.unpack(">I", b4)[0], struct.unpack("<I", b4)[0]
            if ids is not None and ids.get(be) == y:
                out.append((o, "sprite", 4))
                o += 4
                continue
            if funcs.get(le) == y or (is_code is not None and le not in funcs and is_code(le)
                                      and CODE_LO <= y < CODE_HI and not y % 2):
                out.append((o, "ptr", 4))
                o += 4
                continue
        if b4[:2] == y4[:2]:
            out.append((o, "word", 2))
            o += 2
            continue
        break
    return out


def convert(kinds: list[tuple], p_addr: int, q_addr: int, y_addr: int, read_a, read_r, rom: bytes,
            ids: dict | None) -> tuple[str, list] | None:
    """The other build's copy at q_addr of the table whose arcade copy is at p_addr (68k at
    y_addr, fields from layout()): ("same", []) when it holds what the arcade's does (pointers
    in the same pattern), ("new", [(size, value, sprite number or None)]) the 68k fields of its
    own contents followed by the 68k bytes after the matched part (TAIL), or None (a field
    that cannot be converted)."""
    if not kinds:
        return None
    n = kinds[-1][0] + kinds[-1][2]
    p, q = _bytes(read_a, p_addr, n), _bytes(read_r, q_addr, n)
    if p is None or q is None:
        return None
    ptrs = [(o, sz) for o, kd, sz in kinds if kd == "ptr"]
    # pointers: the same pattern of equal entries (a dispatch table of the other build's own
    # routines standing for the same 68k ones)
    for a, _ in ptrs:
        for b, _ in ptrs:
            if (p[a:a + 4] == p[b:b + 4]) != (q[a:a + 4] == q[b:b + 4]):
                return None
    if all(kd == "ptr" or p[o:o + sz] == q[o:o + sz] for o, kd, sz in kinds):
        return "same", []
    out = []
    for o, kd, sz in kinds:
        if kd == "ptr":
            out.append((4, struct.unpack_from(">I", rom, y_addr + o)[0], None))
        elif kd == "word":
            out.append((2, struct.unpack_from(">H", q, o)[0], None))
        else:
            be = struct.unpack_from(">I", q, o)[0]
            if ids is None or be not in ids:
                return None
            out.append((4, ids[be], be))
    for o in range(n, n + TAIL, 2):
        if y_addr + o + 2 <= len(rom):
            out.append((2, struct.unpack_from(">H", rom, y_addr + o)[0], None))
    return "new", out
