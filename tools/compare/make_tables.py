"""make_tables.py TRACES OUT: the MAME replay tables from the PCSX2 traces (ps2_traces.sh) -> OUT.

  ctr_table.txt / tg_table.txt   shooting run (ps2shoot2 / ps2tg), keyed on the logic frame
                                 (PS2 play time 0x642298 + 0x3E5): game counter 0x35804C + 0x23F,
                                 and the frame toggle 0x358048 at the key's first sample
  ctr_table_long / tg_table_long the same for the no-input stage 1 run (ps2long2)
  play_table_s2..s6.txt          stages 2-6, keyed on play frames (tools/ps2_play_table.py,
                                 MIN_STUCK 10): ps2long34, ps2long45, ps2long6, ps2long7 (two stages)
  shoot_ctr_events.txt           MAME SHOOT_CTR_EVENTS of the shooting run (its pad, by counter)
  kakusei_ctr_events.txt         those plus the Kakusei Triangle press (MAME P1 Button 2)
  kak7_ctr/tg_table.txt, kak7_events.txt
                                 the long Kakusei run (ps2kak7, to frame 26000: stage 2 and the
                                 start of stage 3), when traced; replayed with shoot.lua KEY_TIMER
  blk7_ctr/tg_table.txt, blk7_events.txt
                                 the long Black run (ps2blk7), keyed K = PS2 key + 0x3E5 + BLK_SHIFT
                                 (env, default 101: MAME's stage starts that many keys before the
                                 PS2's since the sprite-builder speed-up), values as
                                 tools/ps2_play_table.py gives them

The tables hold PS2 timing values only (counters, toggles); they stay in the git-ignored OUT."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Triangle (Kakusei) of ps2kak4 (pad frames 6000..6006): counters 0xE1D..0xE23 in the MAME replay,
# two counters after the trace's, as tuned for the Kakusei bullet test (counter 0xF4A match)
KAKUSEI_PRESS = [(0xE1D, "P1 Button 2", 1), (0xE23, "P1 Button 2", 0),
                 # Cross (the barrier, MAME P1 Button 4) of ps2kak4 (pad frames 6300..6306):
                 # counters 0xF48..0xF4E; at 0xF49 the barrier is then in effect on both machines
                 # (P1 +0x24 / +0x2E / +0x56), one counter later fires a 4-shot Power Shot volley
                 # first, whose aim jitter draws the RNG 4 times
                 (0xF48, "P1 Button 4", 1), (0xF4E, "P1 Button 4", 0)]


def rows(trace):
    for l in open(trace / "watch.txt"):
        x = l.split()
        if x[0] != "H":
            yield x


def key_table(trace, value, out, fmt):
    """first sample of each logic-frame key: value(row) -> out ("key value" lines)."""
    d = {}
    for x in rows(trace):
        d.setdefault(int(x[1][:4], 16) + 0x3E5, value(x))
    out.write_text("".join(f"{k:x} {fmt(v)}\n" for k, v in sorted(d.items())))


def ctr_events(trace, extra=()):
    """the pad's C (Button 3) and A (Button 1) changes from frame 3500, by game counter (+ 0x3E5),
    one counter later (the PS2 pad reaches the game a frame after the trace's frame; C at the
    trace's counter put the rapid-fire toggle, P1 +1 bit 4, in the opposite phase):
    "ctr,field,value;..." (ctrevents.py)."""
    ctr = {int(x[0]): int(x[1][:4], 16) for x in rows(trace)}
    prev, out = 0, list(extra)
    for l in open(trace / "pad.txt"):
        x = l.split()
        if not x or int(x[0]) < 3500:
            continue
        f, v = int(x[0]), int(x[1], 16)
        for bit, fld, plus in ((0x20, "P1 Button 3", 1), (0x80, "P1 Button 1", 1)):
            if (v ^ prev) & bit:
                out.append((ctr[f] + 0x3E5 + plus, fld, 1 if v & bit else 0))
        prev = v
    # by counter only (a stable sort): the pad's order is kept for changes that land on the same
    # counter, as all those during a stage clear do (the PS2's play-time key stops there); sorting
    # whole tuples put a release before the press that preceded it
    return ";".join(f"{c:x},{fld},{v}" for c, fld, v in sorted(out, key=lambda e: e[0])) + "\n"


def main():
    tr, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    key_table(tr / "ps2shoot2", lambda x: (int(x[5][:4], 16) + 0x23F) & 0xFFFF, out / "ctr_table.txt", lambda v: f"{v:x}")
    key_table(tr / "ps2tg", lambda x: int(x[2][:4], 16), out / "tg_table.txt", str)
    key_table(tr / "ps2long2", lambda x: (int(x[3][8:12], 16) + 0x23F) & 0xFFFF, out / "ctr_table_long.txt", lambda v: f"{v:x}")
    key_table(tr / "ps2long2", lambda x: int(x[3][0:4], 16), out / "tg_table_long.txt", str)
    tmp = out / "play_tables"
    for src, picks in (("ps2long34", [(0, 2)]), ("ps2long45", [(0, 3)]), ("ps2long6", [(0, 4)]),
                       ("ps2long7", [(0, 5), (1, 6)])):
        d = tmp / src
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(HERE.parent / "ps2_play_table.py"), str(tr / src), str(d), "10"],
                       check=True, stdout=subprocess.DEVNULL)
        for n, s in picks:
            os.replace(d / f"play_table_{n}.txt", out / f"play_table_s{s}.txt")
    (out / "shoot_ctr_events.txt").write_text(ctr_events(tr / "ps2shoot2"))
    (out / "kakusei_ctr_events.txt").write_text(ctr_events(tr / "ps2shoot2", KAKUSEI_PRESS))
    if (tr / "ps2kak7" / "watch.txt").exists():
        k7 = tr / "ps2kak7"
        key_table(k7, lambda x: (int(x[5][:4], 16) + 0x23F) & 0xFFFF, out / "kak7_ctr_table.txt", lambda v: f"{v:x}")
        key_table(k7, lambda x: int(x[6][:4], 16), out / "kak7_tg_table.txt", str)
        (out / "kak7_events.txt").write_text(ctr_events(k7, KAKUSEI_PRESS))
    if (tr / "ps2blk7" / "watch.txt").exists():
        black_tables(tr / "ps2blk7", int(os.environ.get("BLK_SHIFT", "101")), out)
    print("tables ->", out)


def black_tables(trace, shift, out):
    """the Black run's counter / toggle per MAME key (PS2 key + 0x3E5 + shift) and its events."""
    sys.path.insert(0, str(HERE.parent))
    import ps2_play_table as pt
    rows = [r for r in pt.read_rows(str(trace / "watch.txt")) if r[1] > 0]
    tab = pt.table(rows, rows[0][0], rows[-1][0], 0)
    keys = sorted(tab)
    (out / "blk7_ctr_table.txt").write_text("".join(f"{k + 0x3E5 + shift:x} {tab[k][1]:x}\n" for k in keys))
    (out / "blk7_tg_table.txt").write_text("".join(f"{k + 0x3E5 + shift:x} {tab[k][0]}\n" for k in keys))
    ev = ctr_events(trace).strip().split(";")
    (out / "blk7_events.txt").write_text(";".join(f"{int(c, 16) + shift:x},{f},{v}" for c, f, v in (e.split(",") for e in ev)) + "\n")


if __name__ == "__main__":
    main()
