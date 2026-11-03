"""stages.py MAME_TIMING PS2_TRACES: stages 3-6 of the no-input run (stagecmp.py per stage).
The stage starts are the MAME frames (after 40000) where the play counter 0x813A1E drops; each
stage compares PS2 play time 0x642298 - base over the PS2 frames of its trace."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# (trace, PS2 play-time base, first PS2 frame, last PS2 frame): stages 3, 4, 5, 6
SPECS = [('ps2long45', 0x400a + 29667, 50729, 84778), ('ps2long6', 0x3843, 85252, 117538),
         ('ps2long7', 0xb429, 117945, 135765), ('ps2long7', 0xf90a, 135935, 199999)]


def main():
    tf, traces = sys.argv[1], Path(sys.argv[2])
    prev, starts = None, []
    for l in open(tf):
        x = l.split(); f = int(x[0]); v = int(x[5], 16)
        if f > 40000 and prev is not None and v < prev - 100:
            starts.append(f)
        prev = v
    print('stage starts', starts)
    for i, (d, base, pf, pt) in enumerate(SPECS):
        if i >= len(starts):
            print('stage', i + 3, 'not reached'); continue
        mfrom = starts[i]; mto = starts[i + 1] - 1 if i + 1 < len(starts) else 10**9
        env = dict(os.environ, PS2_FROM=str(pf), PS2_TO=str(pt))
        r = subprocess.run([sys.executable, str(HERE / 'stagecmp.py'), tf, str(traces / d), hex(base), str(mfrom), str(mto)],
                           env=env, capture_output=True, text=True)
        print('stage', i + 3, r.stdout.strip())


if __name__ == '__main__':
    main()
