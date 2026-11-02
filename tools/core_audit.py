"""core_audit.py BUILT_ZIP: Arrange constant changes in the mode cores (A0A8 arcade mode, A0A9
Arrange) and their state in a built program ROM (spec/arrange-diff.md, "Mode-core constants").

tools/imm_audit.py places each arcade-core function in the 68k listing by its own shape and
leaves most short ones unplaced; but only functions whose Arrange twin differs can carry an
Arrange change, so this audit starts from the twins instead:
  1. pairs from mips_funcs.match whose de-translations differ in a non-address immediate;
  2. functions the matcher leaves unpaired on both sides, paired by de-translated shape (difflib
     ratio >= 0.75, mutual best among the unpaired, at least 3 instructions);
then anchors each arcade twin in the ROM listing (verify.compare_function) and prints every
changed immediate with its 68k instruction and a status against the built ROM:
  ok / unit (a ported unit replaces it) / MISSING / WRONG (changed to another value) /
  rom-mismatch (the arcade twin's value is not the ROM's) / unplaced (no 68k instruction).
Shape pairs can be look-alike siblings (one function's twin is the other's): check each hit's
context (the twins must agree on everything else) before patching (code.py TASK_* tables)."""
import collections
import difflib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import capstone  # noqa: E402

from espgalarr.detrans import core, helpers  # noqa: E402
from espgalarr.detrans.apply import is_translated  # noqa: E402
from espgalarr.detrans.verify import WORK, Index, LearnedMap, compare_function, load_listing  # noqa: E402
from espgalarr.diff import mips_funcs  # noqa: E402
from espgalarr.pgm import rom as rom_mod  # noqa: E402
from espgalarr.ps2.overlay import Overlay  # noqa: E402
from twin_audit import BITS, imms, shape, val  # noqa: E402


def detrans(ov, f, lm):
    code = ov.read(f.start, f.size)
    if not is_translated(code):
        return None
    try:
        r = core.detranslate(code, f.start, lm, read_word=helpers.reader(ov))
    except Exception:  # noqa: BLE001
        return None
    return r, [i for i in r.insns if not i.is_label and not i.is_comment]


def imm_diffs(ia, ib):
    """(i, j) of shape-aligned instructions whose immediates differ (non-address values)."""
    sm = difflib.SequenceMatcher(a=[shape(i.text()) for i in ia], b=[shape(i.text()) for i in ib], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                a, b = imms(ia[i].text()), imms(ib[j].text())
                if a and a != b and len(a) == len(b) and all(val(x, "l") < 0x100000 for x in a + b):
                    out.append((i, j))
    return out


def main() -> None:
    o = bytes(rom_mod.RomSet(WORK).p_rom_68k())
    built = zipfile.ZipFile(sys.argv[1]).read("espgaluda_u8.bin")
    if built[0x100000:0x100100] != o[0x100000:0x100100]:
        built = bytes(x for i in range(0, len(built), 2) for x in (built[i + 1], built[i]))
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_000)
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    lma = LearnedMap.load(WORK / "ps2" / "detrans" / "addrmap_arcade.json")
    lmr = LearnedMap.load(WORK / "ps2" / "detrans" / "addrmap_arrange.json")
    disc = WORK / "ps2" / "disc" / "A0A0"
    A, R = Overlay(disc / "A0A8"), Overlay(disc / "A0A9")
    fa = sorted(mips_funcs.split_functions(A.image, A.image_base, A.header.text_end), key=lambda f: f.start)
    fb = sorted(mips_funcs.split_functions(R.image, R.image_base, R.header.text_end), key=lambda f: f.start)
    units = json.loads((WORK / "ps2" / "detrans" / "ported_units_rec.json").read_text())["units"]
    matches = mips_funcs.match(fa, fb).matches
    da = {f.start: detrans(A, f, lma) for f in fa}
    db = {f.start: detrans(R, f, lmr) for f in fb}
    pairs = [(m.a.start, m.b.start, "matched") for m in matches
             if A.read(m.a.start, m.a.size) != R.read(m.b.start, m.b.size)]
    # the unpaired, by shape
    ma, mb = {m.a.start for m in matches}, {m.b.start for m in matches}
    ua = {k: [shape(i.text()) for i in v[1]] for k, v in da.items() if v and k not in ma and len(v[1]) >= 3}
    ub = {k: [shape(i.text()) for i in v[1]] for k, v in db.items() if v and k not in mb and len(v[1]) >= 3}
    byn = collections.defaultdict(list)
    for k, s in ub.items():
        byn[len(s)].append(k)
    best_a, best_b = {}, {}
    for ka, sa in ua.items():
        n = len(sa)
        for m in range(int(n * 0.7), int(n * 1.4) + 2):
            for kb in byn.get(m, ()):
                sm = difflib.SequenceMatcher(None, sa, ub[kb], autojunk=False)
                if sm.real_quick_ratio() < 0.75 or sm.quick_ratio() < 0.75:
                    continue
                r = sm.ratio()
                if r >= 0.75:
                    if r > best_a.get(ka, (0,))[0]:
                        best_a[ka] = (r, kb)
                    if r > best_b.get(kb, (0,))[0]:
                        best_b[kb] = (r, ka)
    pairs += [(ka, kb, f"shape {r:.2f}") for ka, (r, kb) in sorted(best_a.items()) if best_b.get(kb, (0, None))[1] == ka]
    stats = collections.Counter()
    for a, b, how in pairs:
        if not da.get(a) or not db.get(b):
            stats["not de-translated"] += 1
            continue
        (ra, ia), (_, ib) = da[a], db[b]
        diffs = imm_diffs(ia, ib)
        if not diffs:
            stats["no constant change"] += 1
            continue
        rep = compare_function(index, ra)
        k68 = {id(ins): k for ins, k in rep.pairs}
        print(f"== A0A8 {a:#x} / A0A9 {b:#x} ({how}) anchor {rep.anchor if rep.anchor is None else hex(rep.anchor)}")
        for i, j in diffs:
            ta, tb = ia[i].text().strip(), ib[j].text().strip()
            k = k68.get(id(ia[i]))
            if k is None:
                st, where = "unplaced", ""
            else:
                size = ia[i].size
                mask = (1 << BITS.get(size, 32)) - 1
                av = [val(x, size) for x in imms(ta)]
                rv = [val(x, size) for x in imms(f"{k.mnem} {k.ops}")]
                bv = [val(x, size) for x in imms(next(md.disasm(built[k.addr:k.addr + 10], k.addr)).op_str)]
                wv = [val(x, size) for x in imms(tb)]
                if k.mnem[:3] in ("add", "sub") and ta.split()[0][:3] != k.mnem[:3]:     # subi #-n = addi #n
                    rv, bv = [-x & mask for x in rv], [-x & mask for x in bv]
                st = "rom-mismatch" if av != rv else "unit" if any(u["orig"] <= k.addr < u["end"] for u in units) \
                    else "ok" if bv == wv else "WRONG" if built[k.addr:k.addr + k.nbytes] != o[k.addr:k.addr + k.nbytes] \
                    else "MISSING"
                where = f"{k.addr:#x} {k.mnem} {k.ops}"
            stats[st] += 1
            print(f"   {st:12} {where:40} {ta} -> {tb}   (MIPS {ib[j].addr:#x})", flush=True)
    print(dict(stats), f"(pairs: {len(pairs)}, of which by shape: {sum(1 for p in pairs if p[2] != 'matched')})")


if __name__ == "__main__":
    main()
