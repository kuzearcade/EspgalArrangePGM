PCSX2 reference runs (PS2 Arrange as ground truth)

Build: PCSX2 v2.9.148 + patches/pcsx2/espgal-trace.diff (environment-driven automation: scripted
pad, frame RAM/register dumps, screenshots, function-entry hook dumps, quit at frame). On a
Wayland-only Qt build, pcsx2-qt/QtUtils.cpp's X11 compositing probe must also be guarded (done
locally in ~/pcsx2/src). BIOS: a Japanese image (ps2-0200j-20040614 works; the 0210j dump in the
supplied set does not boot). Settings: portable mode, inis/PCSX2.ini with [UI] SettingsVersion=1.

Menu path (pcsx2_pad_arrange_stage1.txt, frames at 60 fps with fast boot): Start at the title
(2150), Down x2 + Circle = Arrange Mode (2300), Circle = Game Start (2400), Circle = Chihiro
(3000), Cross held from 3400 (shot); stage 1 boss by ~6000.

Check: tools/ps2check_dumps.py <dump dir>  (MIPS interpreter truth vs de-translated 68k code).

Full-game capture: ESPGAL_HOOKS=hooks_all.txt (every changed/new Arrange routine of A0A9 and the
stage overlays A0A0-A0A6, built from mips_funcs.match), ESPGAL_HOOK_MAX=2, lives held with
ESPGAL_POKE=642292:0002 ESPGAL_POKE_FROM=3600 (P1 lives word in 68k byte order; P2 at 0x642294,
found from the Arrange death routine 0x796570), [Framerate] NominalScalar=10, 130000 frames.
Several stage overlays share addresses: dumps are named hook_<pc>_<sig>_<n>, <sig> = which line
for that pc matched. Check: tools/ps2check_dumps.py <dir> <annotated list> -j 8 --out res.json,
inside `systemd-run --user --scope -p MemoryMax=16G -p MemorySwapMax=0` (about 40 min for the
2135 dumps of the full capture). Each dump runs in its own subprocess (3 GB address space, 180 s).

How the check models the PS2 (espgalarr/detrans/ps2check.py):
  * the candidate runs on the PS2 stack layout: translated calls push no return address, so the
    candidate's entry pushes none either; it ends at the `rts` executed with the entry a7;
  * bridged callees get the 68k registers, the CCR (PS2 CCR word: flags in bits 8-12) and the
    MIPS argument registers the callee reads (rendered as `; mips-args a0=$k|dN|[aN+k].s` on the
    call or tail jump: native service arguments, code pointers passed to helpers, loads the
    translator moved from a callee into its callers); the porter refuses such calls unless the
    target is a mapped native service;
  * movem.w loads are not folded (the translator writes only the low words, no sign extension);
  * a native service that never finishes in the interpreter (IOP waits, long loaders) is stubbed
    on both sides and the dump is retried once (result field "stubbed").

Porter trust from the PS2 check: `ps2check_dumps.py ... --verified work/ps2/detrans/ps2_verified.json`
lists the routines that matched on every captured state; the porter (detrans/porter.py) then also
trusts a changed routine whose arcade twin is not exact when: it is PS2-verified; the twin aligns
with its 68k region with no inserted/missing instruction other than the translator's own rewrites
of the same code (detrans/twin_gaps.py: its flag computations, split immediate stores, conditional
returns and tail calls written differently, register saves, dead code; straight-line runs with the
same effect on registers, stores, calls and live flags, also over neighbouring gaps (a hoisted
`lea`); branch runs that go to the same places for every N/Z/V/C; outlined fragments, where the
twin calls an arcade routine that de-translates to the inline 68k run (no a7 access, it is called);
tail calls to the translation of the branch target, past nops and dead address loads; a decrement
written .l where Dn.w was just found non-zero; sprite numbers, below; and gaps that split into
pieces each explained by one of these); the operands that differ
are immediates or absolute addresses (the latter are corrected in the port from the twin evidence);
the anchor
comes from the alignment and the learned call map confirms it; no other routine claims the anchor;
the region is not entered from outside; and if the 68k falls through at the region end, the twin
ends there too (a twin returning there was split off a longer 68k routine). Units say which rule
trusted them ("twin exact" / "PS2-verified"). Bisect them with ESPGAL_VERIFIED_SUBSET="lo:hi".

What the PS2 check does not see, the port must get right by itself:
  * sprite numbers (detrans/sprite_ids.py): the 68k passes a sprite's graphics address in d2,
    the PS2 its number in its own atlas (numbered in graphics-address order, no PGM address kept
    anywhere on the PS2). The table is learned from every translated arcade routine aligned with
    its 68k original (immediates abstracted) and checked increasing; a `move.l #n,d2` that reaches
    a call of a 68k sprite routine is replaced by the address, or the routine is refused when the
    table does not hold n (an Arrange routine starting before its 68k region loads one outside the
    aligned part, so "twin exact" does not cover it);
  * addresses: the Arrange RAM layout is not the arcade's everywhere (blocks moved, words
    reordered), so a work-RAM address the map only interpolates may be another variable. Twin
    evidence pins every aligned absolute address the Arrange map only guessed (an address in the
    same block, within 16 bytes, takes the same offset); a PS2-verified unit that still has an
    interpolated RAM address is not ported;
  * alignment: the PS2 writes 68k memory bytewise, the 68000 faults on a word/long access at an
    odd address. The de-translator merges byte stores only at even addresses, and the porter
    refuses any .w/.l access at an odd displacement or address;
  * the stack: translated calls push no return address, so the translator's register saves write
    at and below a7 before lowering it (`move.l rN,-k(a7) ... subq.l #K,a7`); the port moves the
    adjustment first (fix_stack_saves), or the save overwrites the 68k return address.

Region exits and entries (detrans/porter.py): when the 68k region does not end in a terminator, an
`rts` of the ported routine is kept only where it follows a call of the 68k continuation; in a
region entered by falling through (the translator outlined it as a MIPS function, its caller goes
on after the call) the returns become `jmp <region end>`; a called region returning early is not
ported. Entries into a region from outside:
  * branches/calls from 68k code outside it are redirected to the same point of the ported code
    when that point is a block start (a label) there, with a `jmp` patched at the entry;
  * code pointers: the PS2 tables and constants hold MIPS addresses, and the translator gives a
    pointer target in the middle of a routine its own entry (16-aligned, reached by a `j` over
    the nop padding before it). A 68k value pointing into a routine whose Arrange and twin
    functions have no such entry is not used as a code pointer (numeric tables: 16.16 values,
    (word, word) records). The k-th twin entry pairs with the k-th Arrange entry; its 68k
    address is the instruction aligned with the twin's first instruction there. ROM values only
    count as pointers when they are an instruction's operand or part of a table of pointers.
An entry at an rts needs no jmp (the original rts stays); every jmp must fit inside the region (the
bytes after it are the next routine). Otherwise (or when the jmps would overlap) the routine is not
ported. Units not ported say which
trust condition failed ("failed" in work/ps2/detrans/ported_units*.json).
