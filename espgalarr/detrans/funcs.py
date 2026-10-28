"""Function boundaries for de-translation.

`diff.mips_funcs.split_functions` cuts at jal targets, stack prologues and after `jr $ra`;
for de-translation two more rules matter:
  * a tail call (`j <ELF helper or other function>` + delay slot) ends a routine, the code
    after it is a routine entered through a code pointer;
  * a routine reached through a jump table keeps its targets: fragments that are only
    reachable by branches / jump tables from a preceding function are joined to it.
It also provides the overlay-wide label set (every branch target), so that a fragment that
is branched into from elsewhere is not treated as dead code.
"""
from __future__ import annotations

import struct

from ..diff import mips_funcs
from .mips import COND_BRANCHES, decode_all
from . import symexec


def _jumptables(insns, base, end, read_word):
    ex = symexec.Executor(insns, read_word=read_word)
    ex.start, ex.end = base, end
    try:
        return ex._find_jumptables({})
    except Exception:
        return {}


def split(ov, read_word=None):
    """Functions of an overlay for de-translation: list of (start, end)."""
    text_end = ov.header.text_end
    code = ov.image[:text_end - ov.image_base]
    insns = decode_all(code, ov.image_base)
    index = {i.addr: k for k, i in enumerate(insns)}
    starts: set[int] = set()
    jal_targets: set[int] = set()
    jal_targets_pre = {i.target for i in insns if i.op == "jal"}
    for k, ins in enumerate(insns):
        if ins.op == "jal" and ov.image_base <= ins.target < text_end:
            jal_targets.add(ins.target)
        if ins.op in ("jr", "j") and (ins.op == "jr" and ins.rs == 31 or ins.op == "j"):
            if ins.op == "j" and ov.image_base <= ins.target < text_end:
                # intra-overlay j: a long branch, unless it is a tail call (target is a
                # jal target) or part of a table of `j X ; nop` stubs
                nxt = insns[k + 2] if k + 2 < len(insns) else None
                if not (ins.target in jal_targets_pre or (nxt is not None and nxt.op == "j")
                        or (k >= 2 and insns[k - 2].op == "j")):
                    continue
            k2 = k + 2
            while k2 < len(insns) and insns[k2].op == "nop":
                k2 += 1
            if k2 < len(insns):
                starts.add(insns[k2].addr)
    starts |= jal_targets
    for ins in insns:
        if mips_funcs.is_sp_prologue(ins.word):
            starts.add(ins.addr)
    starts.add(ov.image_base)
    # branch targets overlay-wide (labels)
    labels: dict[int, int] = {}      # target -> source address (first)
    sources: dict[int, list[int]] = {}
    for ins in insns:
        if ins.op in COND_BRANCHES or ins.op in ("b", "j"):
            if ov.image_base <= ins.target < text_end:
                labels.setdefault(ins.target, ins.addr)
                sources.setdefault(ins.target, []).append(ins.addr)
    ordered = sorted(starts)
    # jump tables: targets become labels, and all code up to the last target belongs to
    # the dispatching function
    tables = {}
    for k, s in enumerate(ordered):
        e = ordered[k + 1] if k + 1 < len(ordered) else text_end
        seg = insns[index[s]:index.get(e, len(insns))]
        if any(i.op == "jr" and i.rs != 31 for i in seg):
            for jr, tg in _jumptables(seg, s, e, read_word).items():
                tables[jr] = tg
                for t in tg:
                    labels.setdefault(t, jr)
                    sources.setdefault(t, []).append(jr)
    # join fragments: a start that is not a jal target and is branched into (or is a
    # jump-table target) from an earlier function is not a function start
    changed = True
    while changed:
        changed = False
        ordered = sorted(starts)
        bounds = {s: (ordered[k + 1] if k + 1 < len(ordered) else text_end) for k, s in enumerate(ordered)}
        for s in ordered:
            if s in jal_targets or s == ov.image_base:
                continue
            src = labels.get(s)
            if src is not None and src < s:
                # the branch comes from an earlier function: join
                starts.discard(s)
                changed = True
                continue
            # jump-table targets inside a later fragment
        if changed:
            continue
    ordered = sorted(starts)
    out = []
    for k, s in enumerate(ordered):
        e = ordered[k + 1] if k + 1 < len(ordered) else text_end
        out.append((s, e))
    return out, labels, tables, sources


class Split:
    def __init__(self, ov, read_word=None):
        self.ov = ov
        self.funcs, self.labels, self.tables, self.sources = split(ov, read_word)
        self.starts = [s for s, _ in self.funcs]
        self.start_set = set(self.starts)

    def external_labels(self, start: int, end: int) -> set[int]:
        """Branch/jump-table targets inside [start, end) whose source lies outside it."""
        return {t for t, srcs in self.sources.items() if start < t < end and any(not (start <= a < end) for a in srcs)}

    def find(self, addr: int):
        import bisect
        i = bisect.bisect_right(self.starts, addr) - 1
        if i >= 0 and self.funcs[i][0] <= addr < self.funcs[i][1]:
            return self.funcs[i]
        return None
