"""PS2 sprite numbers <-> PGM sprite graphics addresses.

The 68k passes a sprite's graphics address to the sprite routines in d2
(`move.l #$1d8870,d2 ; ... ; jmp $114af0`); the PS2 converted the graphics into its own
atlas and the translator replaced each address by the sprite's number in it
(`move.l #$20e6,d2`). The atlas holds no PGM address, so the numbers are learned from the
arcade mode: every translated arcade routine aligned with its 68k original, immediates
abstracted, gives (number, address) pairs where the 68k loads a graphics address and the
twin a number. Numbers follow the graphics in address order: the table is checked to be
increasing, and a ported routine that loads a number the table does not hold is refused.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

ID_MAX = 0x8000                     # sprite numbers seen go up to ~0x3d00
GFX_MIN = 0x80000                   # graphics addresses (the program ROM's code starts at 0x110000)
_LOAD = re.compile(r"^\s+move\.l\s+#\$([0-9a-f]+),d2\s*(;.*)?$")
_CALL = re.compile(r"^\s+(jsr|jmp)\s+\$([0-9a-f]+)(\.l)?\s*(;.*)?$")
_STOP = re.compile(r"^\s+(rts|rte|bra|jmp|jsr|bsr)\b")


def sprite_routines(index, ids: dict[int, int]) -> set[int]:
    """68k routines whose callers load in d2 graphics addresses the table pairs with sprite
    numbers (two call sites at least; a score routine takes points there, never one of
    those)."""
    import collections
    known = set(ids.values())
    seen: collections.Counter = collections.Counter()
    ks = index.ks
    for n, k in enumerate(ks):
        c = k.canon
        if c.mnem == "move" and c.size == "l" and c.ops[1] == ("dn", "d2") and c.ops[0][0] == "imm" \
                and c.ops[0][1] in known:
            for k2 in ks[n + 1:n + 17]:
                c2 = k2.canon
                if c2.mnem in ("jsr", "jmp") and c2.ops[0][0] == "func" and isinstance(c2.ops[0][1], int):
                    seen[c2.ops[0][1]] += 1
                    break
                if (c2.ops and c2.ops[-1] == ("dn", "d2")) or c2.mnem in ("rts", "bra", "jmp", "rte"):
                    break
    return {f for f, n in seen.items() if n >= 2}


def _pairs(rec, ks) -> list[tuple[int, int]]:
    from .port import _shape_key
    a = [_shape_key(c) if c else "?" for c in rec]
    b = [_shape_key(k.canon) for k in ks]
    out = []
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for i in range(blk.size):
            c, k = rec[blk.a + i], ks[blk.b + i].canon
            if c.mnem == k.mnem == "move" and c.size == k.size == "l" and c.ops[1] == k.ops[1] \
                    and c.ops[0][0] == k.ops[0][0] == "imm" and c.ops[0][1] < ID_MAX <= GFX_MIN <= k.ops[0][1]:
                out.append((c.ops[0][1], k.ops[0][1]))
    return out


def learn(overlays, index, amaps) -> dict[int, int]:
    """{sprite number: graphics address} from every translated routine of the arcade
    overlays (amaps: overlay -> its learned address map); pairs that break the address
    order or disagree are dropped."""
    import collections
    from ..diff import mips_funcs
    from . import core
    from .apply import is_translated
    from .verify import _reader, compare_function, recovered_canon
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for ov in overlays:
        rd = _reader(ov)
        for f in mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end):
            code = ov.read(f.start, f.size)
            if not is_translated(code):
                continue
            try:
                res = core.detranslate(code, f.start, amaps[ov], read_word=rd)
            except Exception:  # noqa: BLE001
                continue
            if not any(_LOAD.match(i.text()) for i in res.insns if not i.is_label):
                continue
            rep = compare_function(index, res)
            if rep.anchor is None:
                continue
            rec = [recovered_canon(i) for i in res.insns if not i.is_label and not i.is_comment]
            pos = index.pos_of(rep.anchor)
            ks = index.ks[max(0, pos - 12):pos + rep.n_m68 + 12]
            for n, p in _pairs(rec, ks):
                votes[n][p] += 1
    table = {}
    for n, cnt in votes.items():
        p, v = cnt.most_common(1)[0]
        if len(cnt) == 1 or v >= 3 * cnt.most_common(2)[1][1]:
            table[n] = p
    # numbers follow the graphics in address order: drop the pairs that do not
    keys = sorted(table)
    bad = set()
    for x, y in zip(keys, keys[1:]):
        if table[y] <= table[x]:
            bad |= {x, y}
    return {n: p for n, p in table.items() if n not in bad}


# the files the learned table depends on (the de-translator and the alignment)
_SOURCES = ("canon.py", "core.py", "expr.py", "mips.py", "port.py", "sprite_ids.py", "symexec.py",
            "templates.py", "verify.py")


def table(cache: Path | None = None) -> dict[int, int]:
    """The learned table (cached in work/, keyed by the de-translator's source)."""
    from ..ps2.overlay import Overlay
    from .port import OUT, STAGE_PAIRS, WORK, learn_overlay_map
    from .verify import Index, LearnedMap, load_listing
    cache = cache or OUT / "sprite_ids.json"
    here = Path(__file__).parent
    key = hashlib.sha1("".join((here / f).read_text() for f in _SOURCES).encode()).hexdigest()
    if cache.exists():
        d = json.loads(cache.read_text())
        if d.get("key") == key:
            return {int(k, 16): int(v, 16) for k, v in d["ids"].items()}
    disc = WORK / "ps2" / "disc" / "A0A0"
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    lm = LearnedMap.load(OUT / "addrmap_arcade.json")
    ovs, amaps = [], {}
    for name in ["A0A8"] + [a for a, _ in STAGE_PAIRS]:
        ov = Overlay(disc / name)
        ovs.append(ov)
        amaps[ov] = lm if name == "A0A8" else learn_overlay_map(ov, index, lm)
    t = learn(ovs, index, amaps)
    cache.write_text(json.dumps({"key": key, "ids": {f"{k:#x}": f"{v:#x}" for k, v in sorted(t.items())}},
                                indent=0))
    return t


def replace(lines: list[str], ids: dict[int, int], routines: set[int], problems: list[str]) -> list[str]:
    """Sprite numbers loaded into d2 for a sprite routine -> their graphics addresses (the
    load reaches the call through conditional branches and labels, d2 not written again)."""
    out = list(lines)
    for n, line in enumerate(lines):
        m = _LOAD.match(line)
        if not m or int(m.group(1), 16) >= ID_MAX:
            continue
        for l2 in lines[n + 1:n + 17]:
            code = l2.split(";", 1)[0].rstrip()
            c = _CALL.match(code)
            if c:
                if int(c.group(2), 16) in routines:
                    v = int(m.group(1), 16)
                    if v in ids:
                        out[n] = line.replace(f"#${m.group(1)},", f"#${ids[v]:x},", 1)
                    else:
                        problems.append(f"PS2 sprite number ${v:x} with no known PGM graphics address")
                break
            if _STOP.match(code) or re.search(r",d2$", code):
                break
    return out
