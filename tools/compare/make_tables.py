"""make_tables.py TRACES OUT: the MAME replay tables from the PCSX2 traces (ps2_traces.sh) -> OUT.

  ctr_table.txt / tg_table.txt   shooting run (ps2shoot2 / ps2tg), keyed on the logic frame
                                 (PS2 play time 0x642298 + 0x3E5): game counter 0x35804C + 0x23F,
                                 and the frame toggle 0x358048 at the key's first sample
  ctr_table_long / tg_table_long the same for the no-input stage 1 run (ps2long2)
  play_table_s2..s6.txt          stages 2-6, keyed on play frames (tools/ps2_play_table.py,
                                 MIN_STUCK 10): ps2long34, ps2long45, ps2long6, ps2long7 (two stages)
  shoot_ctr_events.txt           MAME SHOOT_CTR_EVENTS of the shooting run (its pad, by counter)
  kakusei_ctr_events.txt         those plus the Kakusei Triangle press (MAME P1 Button 2)

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
    """the pad's C (Button 3) and A (Button 1, one counter later) changes from frame 3500, by game
    counter (+ 0x3E5): "ctr,field,value;..." (ctrevents.py)."""
    ctr = {int(x[0]): int(x[1][:4], 16) for x in rows(trace)}
    prev, out = 0, list(extra)
    for l in open(trace / "pad.txt"):
        x = l.split()
        if not x or int(x[0]) < 3500:
            continue
        f, v = int(x[0]), int(x[1], 16)
        for bit, fld, plus in ((0x20, "P1 Button 3", 0), (0x80, "P1 Button 1", 1)):
            if (v ^ prev) & bit:
                out.append((ctr[f] + 0x3E5 + plus, fld, 1 if v & bit else 0))
        prev = v
    return ";".join(f"{c:x},{fld},{v}" for c, fld, v in sorted(out)) + "\n"


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
    print("tables ->", out)


if __name__ == "__main__":
    main()
