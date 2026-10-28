"""Build stage 'music': stream the Arrange BGM into the sample ROM.

Design (see spec/arrange-diff.md "Music"): every track becomes ONE looped 8-bit u-law
sample that lives entirely inside a single 1 MB ICS2115 bank, so the chip loops it in
hardware and the Z80 only has to start/stop a voice. Rates are chosen per tier (in-game
tracks first) so that everything fits the 12 MB cart window after the original SFX
samples (which stay where they are) and the arcade BGM instrument samples (dropped).
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from ..music import convert, loop as loop_mod
from ..pgm import ics2115, m68k, rom as rom_mod, sound, z80
from ..ps2 import adx, afs

# 68k BGM command index -> Arrange AFS stream (from spec/arrange-diff.md, Music table)
ARRANGE_TRACKS = {
    0x06: ("title", 28, 1),
    0x0A: ("stage1", 18, 0),
    0x0C: ("stage2", 19, 0),
    0x0D: ("stage3", 20, 0),
    0x0F: ("stage4", 21, 0),
    0x14: ("stage5", 22, 0),
    0x0B: ("boss", 24, 0),
    0x10: ("boss5_1", 27, 0),
    0x08: ("clear", 25, 0),
    0x12: ("ending", 26, 1),
    0x09: ("name_entry", 23, 1),
}
MIN_LEN = {0: 90.0, 1: 30.0}        # seconds per tier when capping
MIN_LEN_TEST = {0: 30.0, 1: 8.0}    # dev preset for unpatched MAME (4 MB sample ROM)
BANK_PAYLOAD = rom_mod.ICS_BANK - 0x100


@dataclass
class PlacedTrack:
    cmd: int
    name: str
    afs_index: int
    rate: int
    ics_start: int       # absolute ICS address of first byte
    loop_start: int      # absolute ICS address where the loop restarts
    ics_end: int         # absolute ICS address (exclusive)
    seconds: float
    note: str

    @property
    def bank(self) -> int:
        return self.ics_start >> 20


def run(state) -> None:
    work: Path = state.work
    p_rom = bytes(state.p_rom)
    usage = sound.sample_usage(p_rom)
    sfx_hi = max(b for _, b in usage.sfx_ranges)          # absolute ICS address after the last SFX sample
    # everything from the end of the SFX samples upward is ours; grow the M ROM to the window
    reserved = [(0x400000, sfx_hi)]
    m_end = rom_mod.M_CART_BASE + getattr(state, "sample_rom_max", rom_mod.M_CART_MAX_SIZE)
    packer = ics2115.BankPacker(end=m_end, reserved=reserved)
    a = afs.Afs(work / "ps2" / "disc" / "A980" / "A980")
    plans: dict[str, loop_mod.TrackPlan] = {}
    monos: dict[str, np.ndarray] = {}
    prio: dict[str, int] = {}
    for cmd, (name, idx, tier) in ARRANGE_TRACKS.items():
        data = a.read(idx)
        info = adx.parse_header(data[:0x100])
        pcm = adx.decode(data)
        mono = convert.to_mono(pcm)
        plans[name] = loop_mod.plan_track(name, pcm, info.sample_rate, (info.loop_start, info.loop_end) if info.loop else None)
        monos[name] = mono
        prio[name] = tier
    budget = m_end - sfx_hi - 0x100 * len(plans)
    quality = getattr(state, "music_quality", "full")
    min_len = MIN_LEN_TEST if quality == "test" else MIN_LEN
    rates = loop_mod.STANDARD_RATES + ([6000, 4000] if quality == "test" else [])
    rate, fitted = loop_mod.fit_budget(plans, monos, budget, prio, min_len, rates)
    # bank-residency: cap each track to one bank at the chosen rate
    placed: list[PlacedTrack] = []
    for cmd, (name, idx, tier) in ARRANGE_TRACKS.items():
        pl = fitted[name]
        max_s = BANK_PAYLOAD / rate
        if pl.kept_samples / pl.src_rate > max_s:
            pl = loop_mod.cap_plan(pl, monos[name], max_s)
        tr = convert.convert_track(name, monos[name], pl.src_rate, pl, rate)
        pos = packer.place(name, len(tr.data), align=16)
        placed.append(PlacedTrack(cmd, name, idx, rate, pos.offset, pos.offset + tr.loop_start, pos.end,
                                  len(tr.data) / rate, pl.note))
        # write into the M ROM image (grow as needed)
        end_file = pos.end - rom_mod.M_CART_BASE
        if len(state.m_rom) < end_file:
            state.m_rom.extend(b"\xff" * (end_file - len(state.m_rom)))
        state.m_rom[pos.offset - rom_mod.M_CART_BASE:end_file] = tr.data
    # fixed output size (= the cap) so the ROM image length is deterministic for MAME/hardware
    size = m_end - rom_mod.M_CART_BASE
    if len(state.m_rom) < size:
        state.m_rom.extend(b"\xff" * (size - len(state.m_rom)))
    # blank the dropped arcade BGM instrument samples (everything after the SFX up to the first stream)
    first = min(p.ics_start for p in placed) - rom_mod.M_CART_BASE
    state.m_rom[sfx_hi - rom_mod.M_CART_BASE:first] = b"\xff" * (first - (sfx_hi - rom_mod.M_CART_BASE))
    state.budget["m_rom"] = {"added": sum(p.ics_end - p.ics_start for p in placed), "rate": rate}
    state.music_tracks = placed
    (state.out / "music.json").parent.mkdir(parents=True, exist_ok=True)
    (state.out / "music.json").write_text(json.dumps([asdict(p) for p in placed], indent=2))
    for p in placed:
        state.note(f"music: cmd {p.cmd:#x} {p.name:10s} afs{p.afs_index:2d} {p.rate} Hz "
                   f"{p.seconds:6.1f}s bank {p.bank:#x} @ {p.ics_start:#x} loop@{p.loop_start:#x} ({p.note})")
    state.note(f"music: sample ROM now {len(state.m_rom)/1048576:.1f} MB, rate {rate} Hz")
    install_driver(state, placed)


# --- 68k / Z80 integration ----------------------------------------------------

BGM_REQUEST_ROUTINES = (0x17DF18, 0x17DF36)      # (type 0x12 / type 0x11 wrappers)
BLOB_ADDR_REFS = (0x17CB66 + 2, 0x17CB7E + 2, 0x17E1A8 + 2, 0x17E228 + 2, 0x17E246 + 2)
BLOB_COUNT_LONG_MINUS1 = (0x17CB6C + 2, 0x17CB84 + 2)
BLOB_COUNT_LONG = (0x17E1BC + 2, 0x17E25A + 2)
BLOB_COUNT_WORD = (0x17E224 + 2,)


def install_driver(state, placed: list[PlacedTrack]) -> None:
    """Relocate an extended Z80 driver into free program ROM and redirect the 68k."""
    p = bytes(state.p_rom)
    tracks = [z80.TrackEntry(t.cmd, t.ics_start, t.loop_start, t.ics_end, t.rate) for t in placed]
    image = z80.build_blob(p, tracks, Path(__file__).resolve().parents[2] / "asm" / "z80" / "stream.z80")
    words = len(image) // 2
    addr = state.alloc_p(len(image), align=0x100, note="z80 driver")
    ps = m68k.PatchSet()
    ps.add(addr, image, "extended Z80 driver image")
    for a in BLOB_ADDR_REFS:
        ps.add(a, struct.pack(">I", addr), "z80 blob address")
    for a in BLOB_COUNT_LONG_MINUS1:
        ps.add(a, struct.pack(">I", words - 1), "z80 blob word count-1")
    for a in BLOB_COUNT_LONG:
        ps.add(a, struct.pack(">I", words), "z80 blob word count")
    for a in BLOB_COUNT_WORD:
        ps.add(a, struct.pack(">H", words), "z80 blob word count")
    for r in BGM_REQUEST_ROUTINES:
        # lea table,a1 ; add d0,d0 ; add d0,d0 ; adda d0,a1 ; move.w (a1),d0 ; jsr upload ; move.w 2(a1),d0 ; move.w d7,d1 ; jmp send
        ps.add(r + 0x0A, b"\xe4\x40", "lsr.w #2,d0 -> d0 = BGM index")
        ps.add(r + 0x0C, b"\x4e\x71" * 3, "skip bank upload")
        ps.add(r + 0x12, b"\x4e\x71" * 2, "keep d0 = BGM index")
    ps.apply(state.p_rom)
    state.note(f"music: Z80 driver image {len(image):#x} bytes at P {addr:#x}; BGM requests redirected")
