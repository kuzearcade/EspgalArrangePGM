"""68k tables found by their contents (where the learned address map only guesses).

The PS2 overlays keep their own copies of the 68k ROM tables, not always at the offsets the
map interpolates between its samples (a pointer table with entries added, a table moved), so
a guessed ROM address may be a few bytes off or land on code. The words of the PS2 copy say
where the 68k table is:

  * a MIPS function address (little-endian) is the 68k routine it translates (big-endian);
  * a sprite number (the PS2 atlas index the translator put where the 68k has a graphics
    address, detrans/sprite_ids.py) is a graphics address: the learned one when the table
    has it, else an address in the A ROM range, increasing with the numbers;
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


def _kinds(words_le: list[int], funcs: dict, ids: dict | None) -> list[tuple]:
    out = []
    for w in words_le:
        be = struct.unpack(">I", struct.pack("<I", w))[0]
        if w in funcs:
            out.append(("ptr", funcs[w]))
        elif ids is not None and be in ids:
            out.append(("sprite", be, ids[be]))
        elif ids is not None and SPRITE_LO <= be < SPRITE_HI:
            out.append(("sprite?", be))
        else:
            out.append(("word", be))
    return out


def find(addr: int, read_word, funcs: dict, rom: bytes, ids: dict | None = None,
         lo: int = 0, hi: int | None = None) -> int | None:
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
    kinds = _kinds(words, funcs, ids)
    # the shortest prefix with enough evidence gives the candidates; the table is the one
    # whose match runs furthest (the PS2 copy may differ further on: entries added, the next
    # table moved), and only if no other candidate matches that far
    n0, solid, strong, wild = None, 0, 0, 0
    for n, k in enumerate(kinds):
        solid += k[0] in ("ptr", "sprite") or k[0] == "word" and bool(k[1])
        strong += k[0] in ("ptr", "sprite")
        wild += k[0] == "sprite?"
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
        return w == k[2]
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
                   else re.escape(struct.pack(">I", k[2])) if k[0] == "sprite" else b"(.{4})"
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

    def __init__(self, base, read_word, funcs: dict, rom: bytes, ids: dict | None = None):
        self._base, self._rd, self._funcs, self._rom, self._ids = base, read_word, funcs, rom, ids
        self._cache: dict[int, int | None] = {}

    def __getattr__(self, name):
        return getattr(self._base, name)

    def map_const(self, v: int):
        r = self._base.map_const(v)
        if r[1] not in ("interp", "near", "") or (r[0] is not None and r[0] >= 0x200000) or self._rd(v) is None:
            return r
        if v not in self._cache:
            self._cache[v] = find(v, self._rd, self._funcs, self._rom, self._ids, 0, 0x200000)
        t = self._cache[v]
        return (t, "table", 1.0) if t is not None else r
