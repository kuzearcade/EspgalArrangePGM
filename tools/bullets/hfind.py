"""hfind.py ADDR...: MIPS sites in A0A9 that load the code address ADDR (lui + addiu/ori), with the
enclosing function (nearest preceding `addiu $sp, $sp, -n`)."""
import re, sys

L = open(os.environ['A0A9_DIS']).read().splitlines()
for a in (int(x, 16) for x in sys.argv[1:]):
    hi, lo = (a + 0x8000) >> 16, a & 0xffff
    lo_s = lo - 0x10000 if lo >= 0x8000 else lo
    hits = []
    for i, l in enumerate(L):
        m = re.match(r'([0-9a-f]+) lui \$(\w+), (0x[0-9a-f]+)$', l)
        if not m or int(m.group(3), 16) != hi:
            continue
        reg = m.group(2)
        for j in range(i + 1, min(i + 8, len(L))):
            m2 = re.match(rf'([0-9a-f]+) (addiu|ori) \$\w+, \${reg}, (-?0x[0-9a-f]+|\d+)$', L[j])
            if m2 and int(m2.group(3), 0) in (lo, lo_s):
                fn = None
                for k in range(i, max(i - 4000, 0), -1):
                    if re.search(r'addiu \$sp, \$sp, -', L[k]) or L[k].split()[1:2] == ['jr']:
                        fn = L[k + 1 if 'jr' in L[k] else k].split()[0]
                        break
                hits.append((m.group(1), fn))
                break
    print(hex(a), hits[:8])
