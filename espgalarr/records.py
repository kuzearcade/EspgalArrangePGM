"""Arrange enemy record layout: 120 records of 0x2C bytes instead of 0x24 (fields unchanged,
8 new bytes at the end of each record).

Code that reaches a *neighbouring* record through a displacement from a record pointer
(`$48(a6)` = record+2, field 0, bosses and multi-part enemies do this a lot) must use the new
stride. Those sites are found dynamically: tests/lua/eatap.lua logs every enemy-record access
with the CPU registers in the arcade layout; `derive_sites` turns each displacement access whose
base register points into another record than the target into (instruction, old displacement,
new displacement) with new = (target record - base record) * 0x2C + target field - base field.
The list (addresses and numbers only) is espgalarr/data/enemy_record_sites.json.
"""
from __future__ import annotations

import collections
import json
import re
import struct
from pathlib import Path

import capstone
from capstone import m68k as M

BASE, OLD, NEW, COUNT = 0x815680, 0x24, 0x2C, 120
SITES = Path(__file__).parent / "data" / "enemy_record_sites.json"
_md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
_md.detail = True
_EXT = {M.M68K_AM_REGI_ADDR_DISP: 2, M.M68K_AM_ABSOLUTE_DATA_SHORT: 2, M.M68K_AM_ABSOLUTE_DATA_LONG: 4,
        M.M68K_AM_AREGI_INDEX_8_BIT_DISP: 2, M.M68K_AM_PCI_DISP: 2, M.M68K_AM_PCI_INDEX_8_BIT_DISP: 2}


def _rec(a: int):
    return (a - BASE) // OLD if BASE <= a < BASE + OLD * COUNT else None


def derive_sites(ea_log: Path, listing: Path, base: int = BASE, stride: int = OLD,
                 skip: set | None = None) -> dict:
    """{pc: [[old disp, "aN", new disp], ...]} from an eatap.lua log. The log may come from the
    arcade layout (base 0x815680, stride 0x24) or from a 0x2C-layout build (then instructions
    already patched, `skip`, are ignored: their listed displacement is no longer the executed
    one). For a displacement d from a register pointing at field g of a record, the target is
    field g+d in the same record or a neighbour; new d = (g+d)//0x24 * 0x2C + (g+d)%0x24 - g."""
    skip = skip or set()
    lst = {}
    for line in listing.read_text().splitlines():
        m = re.match(r"^([0-9a-f]{6})\s+\S+\s+\S+\s*(.*)$", line)
        if m:
            lst[int(m.group(1), 16)] = m.group(2)
    top = base + stride * COUNT
    found = collections.defaultdict(lambda: collections.defaultdict(set))
    for line in ea_log.read_text().splitlines():
        f = line.split()
        if len(f) < 20:
            continue
        pc, addr, mask = int(f[0], 16), int(f[2], 16), int(f[3], 16)
        if pc < 0x113000 or pc not in lst or f"{pc:#x}" in skip:
            continue
        regs = [int(x, 16) for x in f[4:20]]
        eff = addr + 1 if mask == 0x00FF else addr         # byte lane of a byte access
        for sgn, dv, reg in re.findall(r"(-?)\$([0-9a-f]+)\((a[0-7])\)", lst[pc]):
            d = int(dv, 16) * (-1 if sgn else 1)
            v = regs[int(reg[1])]
            if v + d == eff or v + d == eff - 2:            # (second word of a long access)
                if not base <= v < top:
                    break
                g = (v - base) % stride
                if g >= OLD:
                    break                                   # base register inside an Arrange gap
                t = g + d
                if not 0 <= t < OLD:
                    rd, tf = divmod(t, OLD)
                    found[pc][(d, reg)].add(rd * NEW + tf - g)
                break
    out, conflicts = {}, []
    for pc, by in sorted(found.items()):
        for (d, reg), nds in sorted(by.items()):
            if len(nds) == 1:
                out.setdefault(f"{pc:#x}", []).append([d, reg, nds.pop()])
            else:
                conflicts.append((pc, d, reg, sorted(nds)))
    return {"sites": out, "conflicts": [[f"{pc:#x}", d, r, n] for pc, d, r, n in conflicts]}


def displacement_offsets(code: bytes, addr: int) -> list[tuple[int, str, int]]:
    """(byte offset of the extension word, base register, displacement) of each d16(An) operand."""
    ins = next(_md.disasm(code, addr), None)
    if ins is None:
        return []
    off, out = 2, []
    long_imm = ins.mnemonic.endswith(".l")
    for o in ins.operands:
        am = o.address_mode
        if am == M.M68K_AM_REGI_ADDR_DISP:
            out.append((off, ins.reg_name(o.mem.base_reg), o.mem.disp))
        if am == M.M68K_AM_IMMEDIATE:
            # quick forms (addq/subq/moveq) carry the value in the opcode word
            if not re.match(r"(addq|subq|moveq)", ins.mnemonic):
                off += 4 if long_imm else 2
        else:
            off += _EXT.get(am, 0)
    return out


def convert(v: int) -> int:
    """Record offset k*0x24+f -> k*0x2C+f (signed)."""
    s = -1 if v < 0 else 1
    k, f = divmod(abs(v), OLD)
    return s * (k * NEW + f)


def patch_tables(p: bytes, ps) -> int:
    data = json.loads(SITES.read_text())
    n = 0
    for a, t in data.get("tables", {}).items():
        a = int(a, 16)
        fmt = {2: ">h", 4: ">i"}[t["size"]]
        for i in range(t["count"]):
            v = struct.unpack_from(fmt, p, a + i * t["size"])[0]
            ps.add(a + i * t["size"], struct.pack(fmt, convert(v)), f"enemy record offset table {a:#x}[{i}]")
            n += 1
    return n


def patch_table_words(p: bytes, ps) -> int:
    data = json.loads(SITES.read_text())
    n = 0
    for a, (old, new) in data.get("table_words", {}).items():
        a = int(a, 16)
        assert struct.unpack_from(">h", p, a)[0] == old, f"table word {a:#x}"
        ps.add(a, struct.pack(">h", new), f"enemy record offset word {a:#x}: {old} -> {new}")
        n += 1
    return n


def patch_steps(p: bytes, ps) -> tuple[int, list]:
    """Pointer steps between records (`lea k(An),An`, `adda/addq/subq #k,An`)."""
    data = json.loads(SITES.read_text())
    n, problems = 0, []
    for a, (k, nk) in data.get("steps", {}).items():
        a = int(a, 16)
        ins = next(_md.disasm(p[a:a + 10], a), None)
        if ins is None:
            problems.append(f"{a:#x}: undecodable"); continue
        m = ins.mnemonic
        if m.startswith(("addq", "subq")):
            mag = abs(nk)
            if not 1 <= mag <= 8 or (nk < 0) != m.startswith("subq") and nk != 0:
                problems.append(f"{a:#x}: {m} cannot take {nk}"); continue
            op = struct.unpack_from(">H", p, a)[0]
            ps.add(a, struct.pack(">H", (op & ~0x0E00) | ((mag & 7) << 9)), f"enemy record step {k}->{nk} at {a:#x}")
        else:
            val = abs(k) if m.startswith(("adda", "suba")) and "#" in ins.op_str else k
            hit = [o for o in range(2, ins.size, 2) if struct.unpack_from(">h", p, a + o)[0] == val]
            if m.startswith("lea") and "pc" in ins.op_str:
                hit = []
            if m.endswith(".l") and "#" in ins.op_str:
                hit = [o for o in range(2, ins.size - 2, 2) if struct.unpack_from(">i", p, a + o)[0] == val]
                if len(hit) == 1:
                    ps.add(a + hit[0], struct.pack(">i", abs(nk) if m.startswith(("adda", "suba")) else nk),
                           f"enemy record step {k}->{nk} at {a:#x}")
                    n += 1
                    continue
            if len(hit) != 1:
                problems.append(f"{a:#x}: step {k} not found in {ins.mnemonic} {ins.op_str}"); continue
            ps.add(a + hit[0], struct.pack(">h", abs(nk) if m.startswith(("adda", "suba")) else nk),
                   f"enemy record step {k}->{nk} at {a:#x}")
        n += 1
    return n, problems


def patch_sites(p: bytes, ps, sites: dict | None = None) -> tuple[int, list]:
    """Rewrite cross-record displacements for the 0x2C layout. Returns (patched, problems)."""
    if sites is None:
        data = json.loads(SITES.read_text())
        sites = {k: v for k, v in data["sites"].items() if k not in data.get("exclude", {})}
    n, problems = 0, []
    for pcs, lst in sites.items():
        pc = int(pcs, 16)
        ops = displacement_offsets(p[pc:pc + 10], pc)
        ins = next(_md.disasm(p[pc:pc + 10], pc), None)
        size = ins.size if ins else 2
        for d, reg, nd in lst:
            # the extension word holding d: unique by value, else by operand layout
            hit = [o for o in range(2, size, 2) if struct.unpack_from(">h", p, pc + o)[0] == d]
            if len(hit) != 1:
                hit = [o for o, r, dd in ops if r == reg and dd == d]
            if len(hit) != 1:
                problems.append(f"{pc:#x}: {d}({reg}) not found uniquely in {ops}")
                continue
            if struct.unpack_from(">h", p, pc + hit[0])[0] != d:
                problems.append(f"{pc:#x}: extension word mismatch")
                continue
            ps.add(pc + hit[0], struct.pack(">h", nd), f"enemy record 0x2C: {d}({reg}) -> {nd}({reg}) at {pc:#x}")
            n += 1
    return n, problems


_STEP = re.compile(r"^(lea(?:\.l)?|adda\.[wl]|suba\.[wl]|addq\.[wl]|subq\.[wl])\s+(?:(-?)\$([0-9a-f]+)\((a[0-7])\), (a[0-7])|#\$([0-9a-f]+), (a[0-7]))$")


def derive_steps(ea_log: Path, listing: Path) -> dict:
    """Pointer steps between records: `lea k(An),An` / `adda #k,An` / `addq/subq #k,An` whose
    register points into the enemy records before and after (values from the next logged access
    through the same register, a few instructions later with no other write to it).
    Returns {pc: [old k (signed, as added), new k]}."""
    lst = []
    for line in listing.read_text().splitlines():
        m = re.match(r"^([0-9a-f]{6})\s+(\S+)\s+(.*)$", line)
        if m:
            lst.append((int(m.group(1), 16), m.group(3).strip()))
    idx = {a: i for i, (a, _) in enumerate(lst)}
    samples = collections.defaultdict(list)
    for line in ea_log.read_text().splitlines():
        f = line.split()
        if len(f) >= 20:
            samples[int(f[0], 16)].append([int(x, 16) for x in f[4:20]])
    out, conflicts = {}, []
    for i, (a, t) in enumerate(lst):
        m = _STEP.match(t)
        if not m or a < 0x113000:
            continue
        if m.group(4):
            if m.group(4) != m.group(5) and not m.group(1).startswith("lea"):
                continue
            k = int(m.group(3), 16) * (-1 if m.group(2) else 1)
            reg = m.group(5)          # lea k(An),Am: Am = An + k (An unchanged before the lea)
        else:
            k = int(m.group(6), 16) * (-1 if t.startswith("sub") else 1)
            reg = m.group(7)
        news = set()

        def scan(j0, hops):
            j = j0
            for _ in range(10):
                if j >= len(lst):
                    return
                b, u = lst[j]
                dst = u.split(",")[-1]
                if re.search(rf"\b{reg}\b", dst) and not re.search(rf"\({reg}\)", dst) \
                        and not u.startswith(("cmp", "tst", "btst")):
                    return      # register rewritten
                if re.search(rf"\({reg}\)", u) and b in samples:
                    for regs in samples[b]:
                        v1 = regs[int(reg[1])]
                        v0 = v1 - k
                        r0, r1 = _rec(v0), _rec(v1)
                        if r0 is not None and r1 is not None:
                            news.add((r1 - r0) * NEW + (v1 - BASE) % OLD - (v0 - BASE) % OLD)
                    return
                m2 = re.match(r"^(bra|b[a-z]{2}|db[a-z]+)(\.[bws])?\s+(?:d[0-7], )?\$([0-9a-f]+)$", u)
                if m2 and hops > 0:
                    t = int(m2.group(3), 16)
                    if t in idx:
                        scan(idx[t], hops - 1)
                    if m2.group(1) == "bra":
                        return
                elif re.match(r"(jmp|jsr|rts|rte|bsr)", u):
                    return
                j += 1
        scan(i + 1, 2)
        if news and news != {k}:
            if len(news) == 1:
                out[f"{a:#x}"] = [k, news.pop()]
            else:
                conflicts.append([f"{a:#x}", k, sorted(news)])
    return {"steps": out, "step_conflicts": conflicts}


def mixed_use(outside_log: Path) -> dict:
    """Sites whose own operand (register + displacement) also reaches memory other than the
    enemy records (log of tests/lua/outside.lua): {pc: reason}. Such shared code keeps its
    arcade displacement."""
    data = json.loads(SITES.read_text())
    out = {}
    for line in outside_log.read_text().splitlines():
        f = line.split()
        if len(f) < 20:
            continue
        pc, addr, mask = int(f[0], 16), int(f[2], 16), int(f[3], 16)
        eff = addr + 1 if mask == 0x00FF else addr
        regs = [int(x, 16) for x in f[4:20]]
        for d, reg, _ in data["sites"].get(f"{pc:#x}", []):
            v = regs[int(reg[1])]
            if v + d == eff or v + d == eff - 2:
                out[f"{pc:#x}"] = f"{d}({reg}) also reaches {eff:#x} (shared code)"
    return out


def add_excludes(found: dict) -> int:
    data = json.loads(SITES.read_text())
    ex = data.setdefault("exclude", {})
    n = 0
    for k, v in found.items():
        if k not in ex:
            ex[k] = v
            n += 1
    SITES.write_text(json.dumps(data, indent=0))
    return n


def _listing(listing: Path):
    lst = []
    for line in listing.read_text().splitlines():
        m = re.match(r"^([0-9a-f]{6})\s+(\S+)\s+(.*)$", line)
        if m:
            lst.append((int(m.group(1), 16), m.group(3).strip()))
    return lst


def _writes_reg(u: str, reg: str) -> bool:
    ops = u.split(None, 1)[1] if " " in u else ""
    if re.search(rf"\({reg}\)\+|-\({reg}\)", ops):
        return True
    dst = ops.split(", ")[-1] if ", " in ops else ""
    if u.startswith(("cmp", "tst", "btst")):
        return False
    return dst == reg or (u.startswith("movem") and "-" in dst and reg[0] in dst) or u.startswith("exg")


def propagate_sites(ea_logs: list[Path], listing: Path, known: dict, exclude: dict | None = None,
                    steps: dict | None = None) -> dict:
    """Unsampled cross-record displacements: the field a register points at, seen in sampled
    accesses, holds along straight-line code until the register is rewritten (forward up to the
    next rts/jmp, backward to the previous one)."""
    lst = _listing(listing)
    idx = {a: i for i, (a, _) in enumerate(lst)}
    seeds = collections.defaultdict(set)          # (list index, reg) -> fields
    for log in ea_logs:
        for line in log.read_text().splitlines():
            f = line.split()
            if len(f) < 20:
                continue
            pc = int(f[0], 16)
            if pc not in idx or pc < 0x113000 or f"{pc:#x}" in (exclude or {}):
                continue
            regs = [int(x, 16) for x in f[4:20]]
            for reg in set(re.findall(r"\((a[0-6])\)", lst[idx[pc]][1])):
                v = regs[int(reg[1])]
                if _rec(v) is not None:
                    seeds[(idx[pc], reg)].add((v - BASE) % OLD)
    field = collections.defaultdict(set)          # (list index, reg) -> propagated fields
    for (i, reg), gs in seeds.items():
        for step in (1, -1):
            j = i
            while 0 <= j < len(lst):
                u = lst[j][1]
                if step == -1 and j != i and (re.match(r"(rts|rte|jmp|bra)\b", u) or _writes_reg(u, reg)):
                    break
                field[(j, reg)] |= gs
                if step == 1 and j != i and (re.match(r"(rts|rte|jmp|bra)\b", u) or _writes_reg(u, reg)):
                    break
                j += step
    out = {}
    for (j, reg), gs in field.items():
        if len(gs) != 1:
            continue
        g = next(iter(gs))
        a, u = lst[j]
        if a < 0x113000 or f"{a:#x}" in (exclude or {}) or f"{a:#x}" in (steps or {}):
            continue
        for sgn, dv, r in re.findall(r"(-?)\$([0-9a-f]+)\((a[0-6])\)", u):
            if r != reg:
                continue
            d = int(dv, 16) * (-1 if sgn else 1)
            t = g + d
            if 0 <= t < OLD:
                continue
            rd, tf = divmod(t, OLD)
            nd = rd * NEW + tf - g
            k = f"{a:#x}"
            if any(x[0] == d and x[1] == reg for x in known.get(k, [])):
                continue
            out.setdefault(k, []).append([d, reg, nd])
    return out


def derive_table_words(ea_logs: list[Path], listing: Path, rom: bytes) -> dict:
    """`adda.w (aT),aM` / `adda.w (aT)+,aM` adding a ROM table word to a record pointer: the word
    (at aT, read from the next logged access through aM) is a record offset. {addr: [old, new]}"""
    lst = _listing(listing)
    idx = {a: i for i, (a, _) in enumerate(lst)}
    samples = collections.defaultdict(list)
    for log in ea_logs:
        for line in log.read_text().splitlines():
            f = line.split()
            if len(f) >= 20:
                samples[int(f[0], 16)].append([int(x, 16) for x in f[4:20]])
    out = {}
    for i, (a, u) in enumerate(lst):
        m = re.match(r"^adda\.w\s+\((a[0-6])\)(\+?), (a[0-6])$", u)
        if not m:
            continue
        rt, post, rm = m.group(1), m.group(2), m.group(3)
        for j in range(i + 1, min(i + 6, len(lst))):
            b, w = lst[j]
            if re.search(rf"\({rm}\)", w) and b in samples:
                for regs in samples[b]:
                    ta = regs[int(rt[1])] - (2 if post else 0)
                    if not 0 <= ta < 0x200000:
                        continue
                    off = struct.unpack_from(">h", rom, ta)[0]
                    v1 = regs[int(rm[1])]
                    v0 = v1 - off
                    r0, r1 = _rec(v0), _rec(v1)
                    if r0 is None or r1 is None or r0 == r1:
                        continue
                    nv = (r1 - r0) * NEW + (v1 - BASE) % OLD - (v0 - BASE) % OLD
                    out[f"{ta:#x}"] = [off, nv]
                break
            if _writes_reg(w, rm) or re.match(r"(b|j|rts)", w):
                break
    return out


if __name__ == "__main__":
    # python3 -m espgalarr.records old.txt ... [--new new.txt ...]   (eatap logs)
    import sys
    from .pgm import rom as rom_mod
    args = sys.argv[1:]
    olds = [Path(x) for x in args[:args.index("--new")]] if "--new" in args else [Path(x) for x in args]
    news = [Path(x) for x in args[args.index("--new") + 1:]] if "--new" in args else []
    lst = Path("work/pgm/dis/p_rom.lst")
    old = json.loads(SITES.read_text()) if SITES.exists() else {}
    exclude = old.get("exclude", {})
    sites = {k: v for k, v in old.get("sites", {}).items()} if news else {}
    conflicts, steps, sconf = [], dict(old.get("steps", {})) if news else {}, []
    patched = set(sites)

    def merge(d):
        for k, v in d.items():
            have = {(a, b) for a, b, _ in sites.get(k, [])}
            sites.setdefault(k, []).extend(x for x in v if (x[0], x[1]) not in have)
    for log in olds:
        d = derive_sites(log, lst)
        merge(d["sites"])
        conflicts += d["conflicts"]
        st = derive_steps(log, lst)
        steps.update(st["steps"])
        sconf += st["step_conflicts"]
    for log in news:
        d = derive_sites(log, lst, base=0xE08200, stride=NEW, skip=patched | set(steps))
        merge(d["sites"])
        conflicts += d["conflicts"]
    if olds:
        merge(propagate_sites(olds, lst, sites, exclude, steps))
    rom = bytes(rom_mod.RomSet(Path("work")).p_rom_68k())
    tw = dict(old.get("table_words", {}))
    if olds:
        tw.update(derive_table_words(olds, lst, rom))
    for k in exclude:
        sites.pop(k, None)
    SITES.parent.mkdir(exist_ok=True)
    SITES.write_text(json.dumps({"source": "tests/lua/eatap.lua over MAME runs (arcade and 0x2C layout); see records.py",
                                 "sites": dict(sorted(sites.items())), "conflicts": conflicts,
                                 "steps": dict(sorted(steps.items())), "step_conflicts": sconf,
                                 "tables": old.get("tables", {}), "table_words": tw, "exclude": exclude}, indent=0))
    print(f"{sum(len(v) for v in sites.values())} displacement sites, {len(steps)} steps, {len(tw)} table words, "
          f"{len(conflicts)}+{len(sconf)} conflicts")


def derive_trace(trace_log: Path, listing: Path) -> dict:
    """Sites and steps from a tests/lua/bptrace.lua log of an arcade-layout run (registers at
    the instruction itself, so stored-only pointers built by lea/adda are covered too)."""
    lst = {a: u for a, u in _listing(listing)}
    sites = collections.defaultdict(lambda: collections.defaultdict(set))
    steps = collections.defaultdict(set)
    for line in trace_log.read_text(errors="replace").splitlines():
        if not line.startswith("T "):
            continue
        f = line.split()
        if len(f) < 9:
            continue
        pc = int(f[1], 16)
        u = lst.get(pc)
        if u is None:
            continue
        regs = [int(x, 16) for x in f[2:9]]
        for sgn, dv, reg in re.findall(r"(-?)\$([0-9a-f]+)\((a[0-6])\)", u):
            if re.search(rf"\(pc|{sgn}\${dv}\(pc", u):
                pass
            d = int(dv, 16) * (-1 if sgn else 1)
            v = regs[int(reg[1])]
            if _rec(v) is None:
                continue
            g = (v - BASE) % OLD
            t = g + d
            if not 0 <= t < OLD:
                rd, tf = divmod(t, OLD)
                sites[pc][(d, reg)].add(rd * NEW + tf - g)
        m = re.match(r"^(adda|suba)\.[wl]\s+#\$([0-9a-f]+), (a[0-6])$", u)
        if m:
            k = int(m.group(2), 16) * (-1 if m.group(1) == "suba" else 1)
            v0 = regs[int(m.group(3)[1])]
            v1 = v0 + k
            r0, r1 = _rec(v0), _rec(v1)
            if r0 is not None and r1 is not None and r0 != r1:
                steps[pc].add((k, (r1 - r0) * NEW + (v1 - BASE) % OLD - (v0 - BASE) % OLD))
    out_s, out_t, conf = {}, {}, []
    for pc, by in sites.items():
        for (d, reg), nds in by.items():
            if len(nds) == 1:
                out_s.setdefault(f"{pc:#x}", []).append([d, reg, nds.pop()])
            else:
                conf.append([f"{pc:#x}", d, reg, sorted(nds)])
    for pc, ks in steps.items():
        if len(ks) == 1:
            out_t[f"{pc:#x}"] = list(ks.pop())
        else:
            conf.append([f"{pc:#x}", "step", sorted(ks)])
    return {"sites": out_s, "steps": out_t, "conflicts": conf}
