"""prof_passes.py TRACE: main-loop passes of a tests/lua/proftrace.lua trace (from leaving the
vblank wait loop 0x112C46 / 0x112C4C to entering it again): the frame each starts in and its
cycles. A pass longer than one frame (337920 cycles at 20 MHz, less the vblank IRQ's) misses the
next vblank, which is a lag frame. Then the longest pass's cycles per instruction address."""
import collections
import re
import sys

LINE = re.compile(r"^([0-9a-f]+) ([0-9A-F]+): (\S+)\s*(.*)$")
WAIT = {0x112C46, 0x112C4C}
frame = None
passes = []          # (frame, start cycle, end cycle, Counter of cycles per pc)
cur = None
prev_c = prev_pc = None
for line in open(sys.argv[1]):
    if line.startswith("FRAME"):
        frame = int(line.split()[1])
        continue
    m = LINE.match(line)
    if not m:
        continue
    c, pc = int(m.group(1), 16), int(m.group(2), 16)
    if prev_c is not None and cur is not None:
        cur[3][prev_pc] += c - prev_c
    if pc in WAIT:
        if cur is not None:
            cur[2] = c
            passes.append(cur)
            cur = None
    elif cur is None and prev_pc in WAIT:
        cur = [frame, c, None, collections.Counter()]
    prev_c, prev_pc = c, pc
for f, a, b, _ in passes:
    print(f"pass from frame {f}: {b - a} cycles")
long = max(passes, key=lambda p: p[2] - p[1])
print("longest pass from frame", long[0], long[2] - long[1], "cycles; top pcs:")
for pc, n in long[3].most_common(25):
    print(f"  {pc:06x} {n}")
