"""PS2 sprite numbers <-> PGM sprite graphics addresses.

The 68k passes a sprite's graphics address to the sprite routines in d2
(`move.l #$1d8870,d2 ; ... ; jmp $114af0`); the PS2 converted the graphics into its own
atlas and the translator replaced each address by the sprite's number in it
(`move.l #$20e6,d2`). The atlas holds no PGM address, but a number is the sprite's index
in the PS2 banks: located() finds each bank sprite's pixels in the PGM ROMs (the address is
its B-ROM word offset), and table() learns the pairs from the arcade mode (every translated
arcade routine aligned with its 68k original, immediates abstracted, gives (number, address)
pairs where the 68k loads a graphics address and the twin a number). Numbers follow the
graphics in address order: the learned table is checked to be increasing. A ported routine
that loads a number neither holds is refused.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

ID_MAX = 0x8000                     # sprite numbers seen go up to ~0x3d00
GFX_MIN = 0x80000                   # graphics addresses (the program ROM's code starts at 0x110000)
LOCATE_VERSION = "1"                # bump when located() changes
SPRITE_MARK = "sprite"              # comment on a converted load (stages/gfx.py redirects redrawn art)
_LOAD = re.compile(r"^\s+move\.l\s+#\$([0-9a-f]+),d2\s*(;.*)?$")
_CALL = re.compile(r"^\s+(jsr|jmp)\s+\$([0-9a-f]+)(\.l)?\s*(;.*)?$")
_STOP = re.compile(r"^\s+(rts|rte|bra|jmp|jsr|bsr)\b")
_LEA = re.compile(r"^\s+lea(?:\.l)?\s+\$([0-9a-f]+)(?:\.l)?,(a[0-6])\s*$")
_CALL_IND = re.compile(r"^\s+(jsr|jmp)\s+\((a[0-6])\)\s*$")
_CALL_F = re.compile(r"^\s+(jsr|jmp)\s+F_([0-9a-f]+)\s*(;.*)?$")
# a PS2 function that only dispatches through a jump table: the arcade inlines these
# (`lea $16bfe4,a0 / movea.l (a0,d0.w),a0 / jsr (a0)`), the PS2 calls them (A0A9 0x9B4BD0)
_DISPATCH = re.compile(r"^(?:moveq #0,d0\|)?move\.[bw] \d+\(a6\),d0\|(?:add\.w d0,d0\|){1,2}"
                       r"lea\.l \$([0-9a-f]+)\.l,a0\|movea\.l \(a0,d0\.w\),a0\|(?:jsr \(a0\)\|rts|jmp \(a0\))$")


def dispatch_table(text: str) -> int | None:
    """The jump table of a ported function that is only a table dispatch (index from the
    object, `jsr (a0)`/`jmp (a0)`), else None."""
    body = []
    for line in text.splitlines():
        code = line.split(";", 1)[0].strip()
        if not code or code.endswith(":"):
            continue
        body.append(re.sub(r"\s+", " ", code))
    m = _DISPATCH.match("|".join(body))
    return int(m.group(1), 16) if m else None


def table_targets(rom: bytes, table: int, n_max: int = 64) -> list[int]:
    """The code addresses of a jump table in the program ROM (68k byte order), up to the
    first entry that is not one."""
    out = []
    for i in range(n_max):
        a = table + 4 * i
        if a + 4 > len(rom):
            break
        v = int.from_bytes(rom[a:a + 4], "big")
        if not 0x100000 <= v < 0x200000:
            break
        out.append(v)
    return out


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


def _learn_pairs(ov, index, amap, part: int = 0, parts: int = 1) -> list[tuple[int, list]]:
    """learn()'s (function number, its (sprite number, graphics address) pairs) for the
    translated routines part, part + parts, ... of one overlay."""
    from ..diff import mips_funcs
    from . import core
    from .apply import is_translated
    from .verify import _reader, compare_function, recovered_canon
    rd = _reader(ov)
    out = []
    for fi, f in enumerate(mips_funcs.split_functions(ov.image, ov.image_base, ov.header.text_end)):
        if fi % parts != part:
            continue
        code = ov.read(f.start, f.size)
        if not is_translated(code):
            continue
        try:
            res = core.detranslate(code, f.start, amap, read_word=rd)
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
        out.append((fi, _pairs(rec, ks)))
    return out


def learn(overlays, index, amaps, found=None) -> dict[int, int]:
    """{sprite number: graphics address} from every translated routine of the arcade
    overlays (amaps: overlay -> its learned address map); pairs that break the address
    order or disagree are dropped. `found`: the pairs of each overlay (_learn_pairs, all
    parts), when already made."""
    import collections
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for k, ov in enumerate(overlays):
        # (votes counted in overlay and function order: ties go to the first seen)
        for _, pairs in sorted(found[k] if found is not None else _learn_pairs(ov, index, amaps[ov])):
            for n, p in pairs:
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
    from .port import OUT, STAGE_PAIRS
    cache = cache or OUT / "sprite_ids.json"
    here = Path(__file__).parent
    key = hashlib.sha1("".join((here / f).read_text() for f in _SOURCES).encode()).hexdigest()
    if cache.exists():
        d = json.loads(cache.read_text())
        if d.get("key") == key:
            return {int(k, 16): int(v, 16) for k, v in d["ids"].items()}
    from .. import parallel
    names = ["A0A8"] + [a for a, _ in STAGE_PAIRS]
    parts = max(1, 3 * parallel.workers() // len(names))
    got = parallel.map_ordered(_table_part, [(name, k, parts) for name in names for k in range(parts)])
    found = [sum(got[i * parts:(i + 1) * parts], []) for i in range(len(names))]
    t = learn([None] * len(names), None, None, found)
    cache.write_text(json.dumps({"key": key, "ids": {f"{k:#x}": f"{v:#x}" for k, v in sorted(t.items())}},
                                indent=0))
    return t


_INDEX = None


def _table_part(job: tuple[str, int, int]) -> list[tuple[int, list]]:
    """_learn_pairs of one part of an arcade overlay (table()), in a worker process."""
    from ..ps2.overlay import Overlay
    from .port import OUT, WORK, learn_overlay_map
    from .verify import Index, LearnedMap, load_listing
    global _INDEX
    if _INDEX is None:
        _INDEX = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    name, part, parts = job
    lm = LearnedMap.load(OUT / "addrmap_arcade.json")
    ov = Overlay(WORK / "ps2" / "disc" / "A0A0" / name)
    amap = lm if name == "A0A8" else learn_overlay_map(ov, _INDEX, lm)
    return _learn_pairs(ov, _INDEX, amap, part, parts)


def located(cache: Path | None = None) -> dict[int, int]:
    """{sprite number: graphics address} for every PS2 bank sprite whose pixels are a PGM
    sprite: the number is the sprite's index in the PS2 banks (A020..A02B; the same index in
    several banks is the same sprite, A023 holding the Arrange art of A024's), the address
    its B-ROM word offset (stages/gfx.py locate_sprite). A number whose banks locate to
    different places is left out. Cached in work/, keyed by the bank files and the code."""
    from ..ps2 import arika_pak as ap
    from .port import OUT, WORK
    cache = cache or OUT / "sprite_located.json"
    disc = WORK / "ps2" / "disc" / "A020"
    names = [f"A0{x:02X}" for x in sorted(ap.FILE_SPRITE_BANKS) if (disc / f"A0{x:02X}").exists()]
    here = Path(__file__).parent
    h = hashlib.sha1()
    h.update(LOCATE_VERSION.encode())
    import inspect
    from ..stages import gfx as _gfx
    # the locating code only (gfx.py also holds the build stage, which must not invalidate
    # this cache)
    h.update("".join(inspect.getsource(f) for f in (_gfx.locate_sprite, _gfx._mask_bytes)).encode())
    h.update((here.parent / "ps2" / "arika_pak.py").read_bytes())
    for n in names:
        h.update(str((disc / n).stat().st_size).encode())
    key = h.hexdigest()
    if cache.exists():
        d = json.loads(cache.read_text())
        if d.get("key") == key:
            return {int(k, 16): int(v, 16) for k, v in d["ids"].items()}
    from .. import parallel
    # (each bank's sprites dealt round-robin into parts, a few per worker; a sprite number's
    # set of places is the same whatever the split)
    parts = max(1, 4 * parallel.workers() // max(1, len(names)))
    found: dict[int, set] = {}
    for got in parallel.map_ordered(_locate_part, [(str(disc / n), k, parts) for n in names for k in range(parts)]):
        for i, off in got:
            found.setdefault(i, set()).add(off)
    t = {n: next(iter(v)) for n, v in found.items() if len(v) == 1}
    cache.write_text(json.dumps({"key": key, "ids": {f"{k:#x}": f"{v:#x}" for k, v in sorted(t.items())}},
                                indent=0))
    return t


_ROMS: tuple | None = None


def _locate_part(job: tuple[str, int, int]) -> list[tuple[int, int]]:
    """[(sprite number, B-ROM word offset)] of the bank sprites k, k + parts, ... located in the
    PGM ROMs (located())."""
    import numpy as np
    from ..pgm import rom as rom_mod
    from ..ps2 import arika_pak as ap
    from ..stages import gfx
    from .port import WORK
    global _ROMS
    if _ROMS is None:
        rs = rom_mod.RomSet(WORK)
        _ROMS = (rs.read("A0") + rs.read("A1"), rs.read("B"))
    a, b = _ROMS
    path, k, parts = job
    dec = ap.decode_file(Path(path).read_bytes())
    sprites = [e for e in ap.parse(dec, int(Path(path).name[2:], 16)) if e.kind in ("sprite", "sprite-lz")]
    out = []
    for e in sprites[k::parts]:
        px = np.frombuffer(ap.sprite_pixels(dec, e), np.uint8)
        if len(px) < 16 or len(px) % 16:
            continue
        try:
            off = gfx.locate_sprite(b, a, px)
        except IndexError:
            off = None                     # (a mask match whose A data runs off the ROM)
        if off is not None:
            out.append((e.index, off))
    return out


def replace(lines: list[str], ids: dict[int, int], routines: set[int], problems: list[str],
            wrapper=None) -> list[str]:
    """Sprite numbers loaded into d2 for a sprite routine -> their graphics addresses (the
    load reaches the call through conditional branches and labels, d2 not written again; a
    call through aN counts when aN was loaded with a sprite routine on the way; a call of a
    ported function counts when wrapper(function) says it dispatches to sprite routines)."""
    out = list(lines)
    for n, line in enumerate(lines):
        m = _LOAD.match(line)
        if not m or int(m.group(1), 16) >= ID_MAX:
            continue
        via = {}                                # aN -> the routine loaded into it
        for l2 in lines[n + 1:n + 17]:
            code = l2.split(";", 1)[0].rstrip()
            ld = _LEA.match(code)
            if ld:
                via[ld.group(2)] = int(ld.group(1), 16)
                continue
            ind = _CALL_IND.match(code)
            if ind and via.get(ind.group(2)) in routines:
                c = ind                         # (a sprite routine through aN)
                target = via[ind.group(2)]
            else:
                c = _CALL.match(code)
                target = int(c.group(2), 16) if c else None
                cf = _CALL_F.match(code) if not c and wrapper is not None else None
                if cf:
                    c, target = cf, (-1 if wrapper(int(cf.group(2), 16)) else None)
            if c:
                if target in routines or target == -1:
                    v = int(m.group(1), 16)
                    if v in ids:
                        out[n] = line.replace(f"#${m.group(1)},", f"#${ids[v]:x},", 1).split(";", 1)[0] \
                            + f"\t; {SPRITE_MARK} ${v:x}"
                    else:
                        problems.append(f"PS2 sprite number ${v:x} with no known PGM graphics address")
                break
            if _STOP.match(code) or re.search(r",d2$", code):
                break
    return out
