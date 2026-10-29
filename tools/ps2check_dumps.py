"""Compare de-translated Arrange routines with the PS2 originals on PCSX2 hook dumps.

Usage: python3 tools/ps2check_dumps.py <dump dir>
Dumps come from PCSX2 built with patches/pcsx2/espgal-trace.diff, run with ESPGAL_HOOKS (lines
"pc first-16-code-bytes"), ESPGAL_HOOK_MAX, ESPGAL_DUMP_DIR and a pad script such as
tools/pcsx2_pad_arrange_stage1.txt (ESPGAL_PAD). Hook annotations ("pc bytes # overlay -> 68k")
are read from ~/pcsx2/hooks_units_annot.txt. Prints MATCH or the differences per dump.
"""
import sys, glob, re, json, traceback
sys.path.insert(0, '/home/vboxuser/EspgaludaArrangePGM')
from pathlib import Path
from espgalarr.ps2.overlay import Overlay
from espgalarr.diff import mips_funcs
from espgalarr.detrans import ps2check as P
from espgalarr.detrans.verify import ElfText
W = Path('/home/vboxuser/EspgaludaArrangePGM/work/ps2')
ann = {}
for l in open('/home/vboxuser/pcsx2/hooks_units_annot.txt'):
    fn, sig, _, ov, *_ = l.split()
    ann[int(fn, 16)] = ov
starts, sizes = set(), {}
for name in set(ann.values()) | {'A0A9'}:
    R = Overlay(W / 'disc/A0A0' / name)
    for f in mips_funcs.split_functions(R.image, R.image_base, R.header.text_end):
        starts.add(f.start)
        if ann.get(f.start) == name:
            sizes[f.start] = f.size
e = ElfText(W / 'disc/SLPS_253.52')
for f in mips_funcs.split_functions(e.image, e.image_base, e.image_base + len(e.image)):
    starts.add(f.start)
dumps = sorted(glob.glob(sys.argv[1] + '/hook_*.ee.zst'))
for d in dumps:
    prefix = d[:-len('.ee.zst')]
    fn = int(re.search(r'hook_([0-9a-f]+)_', d).group(1), 16)
    dd = P.Dump.load(Path(prefix))
    try:
        t = P.truth(dd, fn)
        text, callees = P.render(dd, fn, sizes[fn], starts)
        c = P.candidate(dd, fn, text, callees)
        a7 = P.regfile(P.Memory(dd.main, dd.scratch))['a7']
        sp = int.from_bytes(dd.regs[29 * 16:29 * 16 + 4], 'little')
        diffs = P.compare(t, c, sp, a7=a7)
        print(Path(prefix).name, 'MATCH' if not diffs else f'{len(diffs)} differences', diffs[:6])
    except Exception as ex:
        print(Path(prefix).name, 'ERROR', type(ex).__name__, str(ex)[:200])
