"""twin_of.py RN AN 68K...: the matched function pair (arcade AN / Arrange RN) whose arcade twin
aligns with each 68k address, with both twins' call lists."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from espgalarr.detrans import core, helpers, porter  # noqa: E402
from espgalarr.detrans.port import learn_overlay_map  # noqa: E402
from espgalarr.detrans.verify import WORK, Index, LearnedMap, compare_function, load_listing  # noqa: E402
from espgalarr.diff import mips_funcs  # noqa: E402
from espgalarr.ps2.overlay import Overlay  # noqa: E402

rn, an = sys.argv[1], sys.argv[2]
want = [int(x, 16) for x in sys.argv[3:]]
index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
lm = LearnedMap.load(WORK / "ps2" / "detrans" / "addrmap_arcade.json")
disc = WORK / "ps2" / "disc" / "A0A0"
A, R = Overlay(disc / an), Overlay(disc / rn)
alm = lm if an == "A0A8" else learn_overlay_map(A, index, lm)
rda, rdr = helpers.reader(A, helpers.main_overlay("A0A8")), helpers.reader(R, helpers.main_overlay("A0A9"))
fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
twin = {m.a.start: m.b for m in mips_funcs.match(fa, fb).matches}
for f in fa:
    try:
        ra = core.detranslate(A.read(f.start, f.size), f.start, alm, read_word=rda)
    except Exception:  # noqa: BLE001
        continue
    ks = [k.addr for _, k in compare_function(index, ra).pairs]
    if not ks:
        continue
    hit = [w for w in want if min(ks) <= w <= max(ks)]
    if not hit:
        continue
    b = twin.get(f.start)
    print("68k", [hex(w) for w in hit], "range", hex(min(ks)), hex(max(ks)), "arc", hex(f.start),
          "arr", b and hex(b.start))
    print("  arc calls", [i.text().split()[-1] for i in ra.insns if i.mnem in ("jsr", "jmp", "bsr")])
    if b:
        try:
            rr = core.detranslate(R.read(b.start, b.size), b.start, alm, read_word=rdr)
            calls = [i.text().split()[-1] for i in rr.insns if i.mnem in ("jsr", "jmp", "bsr")]
            print("  arr calls", calls)
            print("  arr helpers", [c for c in calls if c.startswith("F_") and int(c[2:], 16) in porter.KAKUSEI_HELPERS])
        except Exception as e:  # noqa: BLE001
            print("  arr detranslate failed", e)
