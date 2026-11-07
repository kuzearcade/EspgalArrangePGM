"""both_tables.py TRACE OUT PREFIX: replay tables of a long shooting trace that runs into stage 6
(ps2both_full: ps2both7 to frame 29899 + ps2both_s46), for shoot.lua KEY_TIMER=1 KEY_UNWRAP=1.

The PS2 play time 0x642298 is a 16-bit word and passes 0xFFFF in stage 6, so the keys are
unwrapped (+0x10000 at each wrap) before the tables are written; otherwise as
make_tables.black_tables with shift 0: PREFIX_ctr_table.txt / PREFIX_tg_table.txt ("key value",
key = unwrapped PS2 key + 0x3E5, values from the last sample of the key before, as
ps2_play_table.table) and PREFIX_events.txt (the pad's C / A changes by key)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import ps2_play_table as pt  # noqa: E402
import make_tables as mt  # noqa: E402


def unwrapped_rows(path):
    """(frame, unwrapped key, toggle, counter) per trace line, from the first nonzero key."""
    out, hi, last = [], 0, None
    for f, key, tg, ctr, _ in pt.read_rows(path):
        if last is not None and key < last and last - key > 0x8000:
            hi += 0x10000
        last = key
        if hi or key > 0:
            out.append((f, hi + key, tg, ctr))
    return out


def main():
    tr, out, pre = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    out.mkdir(parents=True, exist_ok=True)
    tab, prev = {}, None
    for f, k, tg, ctr in unwrapped_rows(str(tr / "watch.txt")):
        if k not in tab:
            tab[k] = (tg, (ctr + pt.CTR_BASE) & 0xFFFF) if prev is None else \
                     (prev[0] ^ 1, (prev[1] + 1 + pt.CTR_BASE) & 0xFFFF)
        prev = (tg, ctr)
    keys = sorted(tab)
    (out / f"{pre}_ctr_table.txt").write_text("".join(f"{k + 0x3E5:x} {tab[k][1]:x}\n" for k in keys))
    (out / f"{pre}_tg_table.txt").write_text("".join(f"{k + 0x3E5:x} {tab[k][0]}\n" for k in keys))
    (out / f"{pre}_events.txt").write_text(mt.ctr_events(tr))
    print("keys", hex(keys[0]), "-", hex(keys[-1]), len(keys), "->", out)


if __name__ == "__main__":
    main()
