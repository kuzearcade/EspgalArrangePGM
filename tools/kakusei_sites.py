"""kakusei_sites.py: the 68k bullet calls where Arrange calls one of its bullet helpers
(porter.KAKUSEI_HELPERS) -> espgalarr/data/kakusei_sites.json.

For every paired function (arcade-mode A0A8 / A0AA.. with Arrange A0A9 / A0A0..), both twins are
de-translated and their call lists aligned (all bullet calls count as equal); an aligned
Arrange call of a helper whose arcade twin calls 0x1713EC / 0x17135E / 0x170FB8 / 0x170F36 gives
the 68k call site (verify.compare_function's pairing of the arcade twin with the listing).
Calls in blocks that do not align one to one (a helper standing for several arcade calls) are
counted and left out. Writes [[site, helper, 68k target], ...] (addresses only).
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from espgalarr.detrans import core, helpers, porter  # noqa: E402
from espgalarr.detrans.apply import is_translated  # noqa: E402
from espgalarr.detrans.port import STAGE_PAIRS, learn_overlay_map  # noqa: E402
from espgalarr.detrans.verify import WORK, Index, LearnedMap, compare_function, load_listing  # noqa: E402
from espgalarr.diff import mips_funcs  # noqa: E402
from espgalarr.ps2.overlay import Overlay  # noqa: E402

TARGETS = ("$1713ec.l", "$17135e.l", "$170fb8.l", "$170f36.l")
OUT = Path(__file__).resolve().parents[1] / "espgalarr" / "data" / "kakusei_sites.json"


def helper_of(t: str) -> int | None:
    m = re.match(r"F_([0-9a-f]+)$", t)
    return int(m.group(1), 16) if m and int(m.group(1), 16) in porter.KAKUSEI_HELPERS else None


def main() -> None:
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    lm = LearnedMap.load(WORK / "ps2" / "detrans" / "addrmap_arcade.json")
    disc = WORK / "ps2" / "disc" / "A0A0"
    sites: dict[int, tuple[int, int]] = {}
    left = 0
    for an, rn in [("A0A8", "A0A9")] + [x for x in STAGE_PAIRS if x[1] != "A0A6"]:
        A, R = Overlay(disc / an), Overlay(disc / rn)
        alm = lm if an == "A0A8" else learn_overlay_map(A, index, lm)
        rda, rdr = helpers.reader(A, helpers.main_overlay("A0A8")), helpers.reader(R, helpers.main_overlay("A0A9"))
        fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
        fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
        for m in mips_funcs.match(fa, fb).matches:
            cb = R.read(m.b.start, m.b.size)
            if not is_translated(cb):
                continue
            try:
                rr = core.detranslate(cb, m.b.start, alm, read_word=rdr)
            except Exception:  # noqa: BLE001
                continue
            bcalls = [i for i in rr.insns if i.mnem in ("jsr", "jmp", "bsr")]
            tb = [i.text().split()[-1] for i in bcalls]
            if not any(helper_of(t) for t in tb):
                continue
            try:
                ra = core.detranslate(A.read(m.a.start, m.a.size), m.a.start, alm, read_word=rda)
            except Exception:  # noqa: BLE001
                continue
            k68 = {id(i): k for i, k in compare_function(index, ra).pairs}
            acalls = [i for i in ra.insns if i.mnem in ("jsr", "jmp", "bsr")]
            ta = [i.text().split()[-1] for i in acalls]
            key = lambda t: "B" if helper_of(t) or t in TARGETS or re.match(r"\$17[01][0-9a-f]{3}\.l", t) else t  # noqa: E731
            sm = difflib.SequenceMatcher(None, [key(t) for t in ta], [key(t) for t in tb], autojunk=False)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op not in ("equal", "replace") or i2 - i1 != j2 - j1:
                    left += sum(1 for t in tb[j1:j2] if helper_of(t))
                    continue
                for x, ty in zip(acalls[i1:i2], tb[j1:j2]):
                    h = helper_of(ty)
                    k = k68.get(id(x))
                    if h is None:
                        continue
                    if k is None or k.mnem != "jsr" or not any(k.ops.endswith(t) for t in TARGETS):
                        left += 1
                        continue
                    tgt = int(k.ops.split("$")[1].split(".")[0], 16)
                    if sites.setdefault(k.addr, (h, tgt)) != (h, tgt):
                        raise SystemExit(f"{k.addr:#x}: two helpers")
        print(rn, len(sites), "sites so far,", left, "left out", flush=True)
    OUT.write_text(json.dumps([[a, h, t] for a, (h, t) in sorted(sites.items())]) + "\n")
    print(len(sites), "sites ->", OUT)


if __name__ == "__main__":
    main()
