"""imm_audit.py [ARCADE:ARRANGE ...]: immediates where the PGM ROM differs from the PS2
arcade-mode twin of a routine, with the Arrange twin's value at the same place
(spec/arrange-diff.md, "PS2 constants the PGM ROM does not have").

Every arcade-overlay function is de-translated (helpers inlined, as the porter does) and placed
in the 68k listing by instruction SHAPE (immediates and addresses abstracted: 4-gram votes), so
a routine whose constants differ finds itself, not a near-identical sibling carrying the PS2's
constants. Ambiguous placements are resolved by look-alike groups (k functions sharing the same
k places pair by rank), overlay neighbours (within 0x300 bytes of an anchored neighbour), the
overlay's own ROM pages (>= 3 unique anchors), places not inside an anchored routine, and code
pointer targets. Output: JSON lines in work/imm_audit/<arrange overlay>.jsonl; every reported
site still needs a look at the code (branch structure or a different callee can misalign)."""
import sys, os, json, difflib, collections
sys.path.insert(0, '.')
from espgalarr.detrans import core, helpers
from espgalarr.detrans.port import STAGE_PAIRS, WORK, _shape_key, _imm_values
from espgalarr.detrans.verify import load_listing, recovered_canon
from espgalarr.detrans.porter import OverlaySource
from espgalarr.detrans.apply import is_translated
from espgalarr.diff import mips_funcs

pairs = [tuple(a.split(':')) for a in sys.argv[1:]] or [('A0A8', 'A0A9')] + STAGE_PAIRS[:-1]
ks = load_listing(WORK / 'pgm' / 'dis' / 'p_rom.lst', 0x100000, 0x200000)
SH = [_shape_key(k.canon) for k in ks]
# routine entries: targets of code pointers (state / task tables) in the program ROM
from espgalarr.pgm import rom as _rom
_P = bytes(_rom.RomSet(WORK).p_rom_68k())[:0x200000]
_LA = {k.addr for k in ks}
ENTRIES = {v for v in (int.from_bytes(_P[o:o + 4], 'big') for o in range(0, len(_P) - 3, 2)) if v in _LA}
G = collections.defaultdict(list)
for i in range(len(SH) - 3):
    G[tuple(SH[i:i + 4])].append(i)


def shapes(res):
    ins = [i for i in res.insns if not i.is_label and i.mnem]
    cs = [recovered_canon(i) for i in ins]
    return ins, cs, [_shape_key(c) if c else '?' for c in cs]


def candidates(seq):
    """Start offsets voted by the 4-grams of shapes (each gram votes pos - j); those with at
    least 50 % of the grams and within 80 % of the best."""
    n = len(seq)
    if n < 6:
        return []
    votes = collections.Counter()
    ng = 0
    for j in range(n - 3):
        key = tuple(seq[j:j + 4])
        if '?' in key:
            continue
        ng += 1
        hits = G.get(key, ())
        if len(hits) > 400:
            continue
        for p in hits:
            votes[p - j] += 1
    if not votes or ng == 0:
        return []
    best = votes.most_common(1)[0][1]
    if best < 0.5 * ng:
        return []
    return sorted((c, v) for c, v in votes.items() if v >= 0.8 * best)


for an, rn in pairs:
    src = OverlaySource(an, rn)
    fa = sorted(src.fa.values(), key=lambda f: f.start)
    diff = mips_funcs.match(fa, sorted(src.fb.values(), key=lambda f: f.start))
    twin = {m.a.start: m for m in diff.matches}
    rows = []
    for f in fa:
        code = src.A.read(f.start, f.size)
        if not is_translated(code):
            continue
        try:
            ra = helpers.inline_result(core.detranslate(code, f.start, src.alm, read_word=src._rd_a),
                                       src._code_a, src._rd_a, src.alm, src._outl_a)
        except Exception:
            continue
        ins, cs, seq = shapes(ra)
        rows.append((f, ins, cs, seq, candidates(seq)))
    # anchors: unique candidates first; then, repeatedly, a function next to an anchored one
    # (overlay neighbours are 68k neighbours) takes its candidate nearest to that anchor,
    # within 0x300 bytes and with no other candidate as near
    chosen = {k: r[4][0][0] for k, r in enumerate(rows) if len(r[4]) == 1}
    # groups of look-alike routines: k overlay functions sharing the same k candidates are
    # paired by rank (the PS2 copies keep the 68k order)
    groups = collections.defaultdict(list)
    for k, r in enumerate(rows):
        if len(r[4]) > 1:
            groups[tuple(c for c, _ in r[4])].append(k)
    for cands_, members in groups.items():
        if len(members) == len(cands_):
            for k, c in zip(sorted(members), cands_):
                chosen[k] = c
    changed = True
    while changed:
        changed = False
        for k, r in enumerate(rows):
            if k in chosen or len(r[4]) < 2:
                continue
            for nb in (k - 1, k + 1):
                if nb not in chosen:
                    continue
                a0 = ks[chosen[nb]].addr
                ds = sorted((abs(ks[c].addr - a0), c) for c, _ in r[4])
                if ds[0][0] <= 0x300 and (len(ds) == 1 or ds[1][0] > 0x300):
                    chosen[k] = ds[0][1]
                    changed = True
                    break
    # the overlay's own ROM pages: those holding at least 3 unique anchors (a stage overlay's
    # routine can match another stage's copy)
    pages = collections.Counter(ks[r[4][0][0]].addr >> 12 for r in rows if len(r[4]) == 1)
    own = {pg for pg, n in pages.items() if n >= 3}
    chosen = {k: c for k, c in chosen.items() if ks[c].addr >> 12 in own}
    # candidates inside a routine already anchored (its listing span) are taken
    for _ in range(3):
        taken = set()
        for k, c in chosen.items():
            for q in range(c, min(c + len(rows[k][3]), len(ks))):
                if q > c and ks[q].addr in ENTRIES:
                    break
                taken.add(q)
        for k, r in enumerate(rows):
            if k not in chosen and len(r[4]) > 1:
                mine = [c for c, _ in r[4] if ks[c].addr >> 12 in own and c not in taken]
                if len(mine) > 1:
                    ent = [c for c in mine if ks[c].addr in ENTRIES]
                    if len(ent) == 1:
                        mine = ent
                if len(mine) == 1:
                    chosen[k] = mine[0]
    os.makedirs(WORK / 'imm_audit', exist_ok=True)
    out = open(WORK / 'imm_audit' / f'{rn}.jsonl', 'w')
    stats = collections.Counter()
    for k, (f, ins, cs, seq, cands) in enumerate(rows):
        if not cands:
            stats['unanchored'] += 1
            continue
        if k not in chosen:
            stats['off-chain'] += 1
            continue
        pos = chosen[k]
        stats['anchored'] += 1
        diffs = []
        win = SH[pos:pos + len(seq) + 16]
        amap = {}
        for blk in difflib.SequenceMatcher(None, seq, win, autojunk=False).get_matching_blocks():
            for t in range(blk.size):
                amap[blk.a + t] = pos + blk.b + t
        for j, c in enumerate(cs):
            if j not in amap:
                continue
            k68 = ks[amap[j]]
            if c is None or c.text == k68.canon.text:
                continue
            va, vk = _imm_values(c), _imm_values(k68.canon)
            # the same operands but for immediates (a different address = misaligned)
            if len(va) != len(vk) or len(c.ops) != len(k68.canon.ops) or any(
                    a != b for a, b in zip(c.ops, k68.canon.ops) if a[0] != 'imm' or b[0] != 'imm'):
                continue
            for (o1, pv, kd), (o2, rv, kd2) in zip(va, vk):
                # (addresses and sprite numbers: the map's business, not constants)
                if kd == kd2 == 'imm' and pv != rv and rv < 0x100000 and pv < 0x100000:
                    diffs.append((j, k68.addr, k68.mnem, k68.ops, rv, pv))
        # a right anchor agrees on most immediates; a look-alike routine of another script not
        agree = sum(1 for j, c in enumerate(cs) if j in amap and c is not None and c.text == ks[amap[j]].canon.text
                    and any(kd == 'imm' for _, _, kd in _imm_values(c)))
        if not diffs or len(seq) < 10 or agree < 3 * len(diffs):
            continue
        m = twin.get(f.start)
        arr = {}
        if m is not None:
            try:
                rcode = src.R.read(m.b.start, m.b.size)
                rb = helpers.inline_result(core.detranslate(rcode, m.b.start, src.amap, read_word=src._rd_r),
                                           src._code_r, src._rd_r, src.amap, None)
                bins, bcs, bseq = shapes(rb)
                for blk in difflib.SequenceMatcher(None, seq, bseq, autojunk=False).get_matching_blocks():
                    for t in range(blk.size):
                        cb = bcs[blk.b + t]
                        if cb is not None:
                            arr[blk.a + t] = [v for _, v, kd in _imm_values(cb) if kd == 'imm']
            except Exception:
                arr = {}
        for j, addr, mnem, ops, rv, pv in diffs:
            ca = [v for _, v, kd in _imm_values(cs[j]) if kd == 'imm']
            av = arr.get(j)
            avv = av[ca.index(pv)] if av is not None and len(av) == len(ca) else None
            out.write(json.dumps({'pair': f'{an}:{rn}', 'arc_fn': f.start, 'arr_fn': m.b.start if m else None,
                                  'kind': m.kind if m else None, 'addr': addr, 'ins': f'{mnem} {ops}',
                                  'rom': rv, 'ps2': pv, 'arrange': avv, 'ambiguous': len(cands) > 1, 'votes': dict(cands)[pos], 'n': len(seq)}) + '\n')
            stats['diffs'] += 1
    out.close()
    print(an, rn, dict(stats), flush=True)
