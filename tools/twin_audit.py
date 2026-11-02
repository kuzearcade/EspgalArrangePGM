"""twin_audit.py BUILT_P_ROM [OVERLAY ...]: immediates Arrange changes in paired stage functions.

For every changed pair of stage functions (arcade-mode overlay A0AA.., Arrange A0A0..; pairs from
mips_funcs.match), both twins are de-translated; the arcade one is aligned with the ROM listing
(verify.compare_function) and the two texts are aligned with each other on their shape (operands'
numbers and labels blanked). Each aligned instruction whose immediates differ is printed with its
ROM instruction and a status against a built program ROM (out/roms/espgaluda_u8.bin):
  ok           the built ROM holds the Arrange value
  unit         a ported unit (work/ps2/detrans/ported_units_rec.json) replaces the code there
  MISSING      the built ROM still holds the arcade value
  WRONG        the built ROM was changed there, to another value
  rom-mismatch the arcade twin's value is not the ROM's (a code pointer, or a misplacement)
Shape alignment pairs the wrong instructions where Arrange inserts or drops a block of the same
shape (stage 5 0x1BE0C2's fourth fan, stage 1 0x1B1596's new tail): check every hit against the
full de-translations before patching (code.py TASK_* tables).
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capstone  # noqa: E402

from espgalarr.detrans import core, helpers  # noqa: E402
from espgalarr.detrans.apply import is_translated  # noqa: E402
from espgalarr.detrans.port import STAGE_PAIRS, learn_overlay_map  # noqa: E402
from espgalarr.detrans.verify import WORK, Index, LearnedMap, compare_function, load_listing  # noqa: E402
from espgalarr.diff import mips_funcs  # noqa: E402
from espgalarr.pgm import rom as rom_mod  # noqa: E402
from espgalarr.pgm.rom import swap16  # noqa: E402
from espgalarr.ps2.overlay import Overlay  # noqa: E402

BITS = {"b": 8, "w": 16, "l": 32}


def imms(t: str) -> list[str]:
    return re.findall(r"#(-?\$?[0-9a-f]+)", t)


def shape(t: str) -> str:
    t = re.sub(r"#-?\$?[0-9a-f]+", "#", t.split(";")[0].strip())
    t = re.sub(r"\b[LF]_[0-9a-f]+", "L", t)
    return re.sub(r"-?\$?\b[0-9a-f]*\d[0-9a-f]*\b(?=\()|\$[0-9a-f]+", "N", t)


def val(s: str, size: str) -> int:
    neg = s.startswith("-")
    s = s.lstrip("-")
    v = int(s[1:], 16) if s.startswith("$") else int(s, 16) if s.startswith("0x") else int(s)
    return (-v if neg else v) & (1 << BITS.get(size, 32)) - 1


def main() -> None:
    built = swap16(Path(sys.argv[1]).read_bytes())
    only = sys.argv[2:]
    p = rom_mod.RomSet(WORK).p_rom_68k()
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_000)
    units = json.loads((WORK / "ps2" / "detrans" / "ported_units_rec.json").read_text())["units"]
    covered = lambda a: any(u["orig"] <= a < u["end"] for u in units)  # noqa: E731
    index = Index(load_listing(WORK / "pgm" / "dis" / "p_rom.lst"))
    lm = LearnedMap.load(WORK / "ps2" / "detrans" / "addrmap_arcade.json")
    disc = WORK / "ps2" / "disc" / "A0A0"
    for an, rn in STAGE_PAIRS:
        if rn == "A0A6" or only and rn not in only:
            continue
        A, R = Overlay(disc / an), Overlay(disc / rn)
        alm = learn_overlay_map(A, index, lm)
        rda = helpers.reader(A, helpers.main_overlay("A0A8"))
        rdr = helpers.reader(R, helpers.main_overlay("A0A9"))
        fa = mips_funcs.split_functions(A.image, A.image_base, A.header.text_end)
        fb = mips_funcs.split_functions(R.image, R.image_base, R.header.text_end)
        for m in mips_funcs.match(fa, fb).matches:
            ca, cb = A.read(m.a.start, m.a.size), R.read(m.b.start, m.b.size)
            if ca == cb or not is_translated(ca):
                continue
            try:
                ra = core.detranslate(ca, m.a.start, alm, read_word=rda)
                rr = core.detranslate(cb, m.b.start, alm, read_word=rdr)
            except Exception as e:  # noqa: BLE001
                print(f"{rn} {m.b.start:#x}: not de-translated ({e})")
                continue
            rep = compare_function(index, ra)
            if rep.anchor is None:
                continue
            k68 = {id(ins): k for ins, k in rep.pairs}
            ia = [i for i in ra.insns if not i.is_label and not i.is_comment]
            ib = [i for i in rr.insns if not i.is_label and not i.is_comment]
            sm = difflib.SequenceMatcher(a=[shape(i.text()) for i in ia], b=[shape(i.text()) for i in ib], autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "equal":
                    continue
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    ta, tb = ia[i].text(), ib[j].text()
                    if not imms(ta) or imms(ta) == imms(tb):
                        continue
                    k = k68.get(id(ia[i]))
                    if k is None:
                        print(f"{'unplaced':12} {rn} {m.b.start:#x} (anchor {rep.anchor:#x}): {ta.strip()} -> {tb.strip()}")
                        continue
                    size, mask = ia[i].size, (1 << BITS.get(ia[i].size, 32)) - 1
                    av = [val(x, size) for x in imms(ta)]
                    rv = [val(x, size) for x in imms(f"{k.mnem} {k.ops}")]
                    bv = [val(x, size) for x in imms(next(md.disasm(built[k.addr:k.addr + 10], k.addr)).op_str)]
                    wv = [val(x, size) for x in imms(tb)]
                    if k.mnem[:3] in ("add", "sub") and ta.split()[0][:3] != k.mnem[:3]:   # subi #-n = addi #n
                        rv, bv = [-x & mask for x in rv], [-x & mask for x in bv]
                    if av != rv:
                        st = "rom-mismatch"
                    elif covered(k.addr):
                        st = "unit"
                    elif bv == wv:
                        st = "ok"
                    elif built[k.addr:k.addr + k.nbytes] != p[k.addr:k.addr + k.nbytes]:
                        st = "WRONG"
                    else:
                        st = "MISSING"
                    print(f"{st:12} {k.addr:#x} {k.mnem} {k.ops}   {ta.strip()} -> {tb.strip()}   {rn} {m.b.start:#x} (MIPS {ib[j].addr:#x})", flush=True)


if __name__ == "__main__":
    main()
