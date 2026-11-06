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
from .. import voices
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
    m_end = rom_mod.M_CART_BASE + getattr(state, "sample_rom_max", rom_mod.M_CART_MAX_SIZE)
    # the Arrange character voices replace the arcade's (voices.py): the other SFX samples stay
    # where they are, the new voices go into the old voices' room where they fit, else above
    sfx = sound.parse_sfx_table(p_rom)
    old_voices = {sfx[i].start for i in voices.VOICE_SFX}
    reserved = sorted({(a, b) for a, b in usage.sfx_ranges if a not in old_voices})
    reserved.append((sfx_hi - 1, sfx_hi))                  # (the region below sfx_hi is never the music's)
    packer = ics2115.BankPacker(end=m_end, reserved=reserved)
    vlist = voices.arrange_voices(work / "ps2" / "disc", p_rom, bytes(state.m_rom))
    voice_above = 0
    vps = m68k.PatchSet()
    for v in vlist:
        pos = packer.place(f"voice {v.sfx:#x}", len(v.data), align=16)
        end_file = pos.end - rom_mod.M_CART_BASE
        if len(state.m_rom) < end_file:
            state.m_rom.extend(b"\xff" * (end_file - len(state.m_rom)))
        state.m_rom[pos.offset - rom_mod.M_CART_BASE:end_file] = v.data
        if pos.offset >= sfx_hi:
            voice_above += pos.end - pos.offset
        e = sound.SFX_TABLE_ADDR + 12 * v.sfx
        vps.add(e, voices.sfx_entry(p_rom[e:e + 12], pos.offset, pos.end), f"Arrange voice SFX {v.sfx:#x}")
    vps.apply(state.p_rom)
    voice_hi = max([sfx_hi] + [pl.end for pl in packer.placed])
    state.note(f"music: 24 Arrange character voices ({sum(len(v.data) for v in vlist)} bytes, "
               f"{voice_above} above the SFX samples)")
    a = afs.Afs(work / "ps2" / "disc" / "A980" / "A980")
    if getattr(state, "bgm", "adx") == "adx":
        run_adx(state, a, voice_hi, m_end)
        return
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
    budget = m_end - voice_hi - 0x100 * len(plans)
    quality = getattr(state, "music_quality", "full")
    min_len = MIN_LEN_TEST if quality == "test" else MIN_LEN
    rates = loop_mod.STANDARD_RATES + ([6000, 4000] if quality == "test" else [])
    rate, fitted = loop_mod.fit_budget(plans, monos, budget, prio, min_len, rates, max_track_bytes=BANK_PAYLOAD)
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
    state.m_rom[voice_hi - rom_mod.M_CART_BASE:first] = b"\xff" * (first - (voice_hi - rom_mod.M_CART_BASE))
    state.budget["m_rom"] = {"added": sum(p.ics_end - p.ics_start for p in placed), "rate": rate}
    state.music_tracks = placed
    (state.out / "music.json").parent.mkdir(parents=True, exist_ok=True)
    (state.out / "music.json").write_text(json.dumps([asdict(p) for p in placed], indent=2))
    for p in placed:
        state.note(f"music: cmd {p.cmd:#x} {p.name:10s} afs{p.afs_index:2d} {p.rate} Hz "
                   f"{p.seconds:6.1f}s bank {p.bank:#x} @ {p.ics_start:#x} loop@{p.loop_start:#x} ({p.note})")
    state.note(f"music: sample ROM now {len(state.m_rom)/1048576:.1f} MB, rate {rate} Hz")
    install_driver(state, placed)


# --- lossless BGM (--bgm adx, spec/lossless-bgm.md) ------------------------------

ADX_FIRST = 18          # the blob holds AFS entries 18..28 (the Arrange set), track = AFS index - 18
# BGM indices the PS2's stream table maps to the same streams (spec/arrange-diff.md, Music table):
# 0x13 = stage 3's. The others with no track (0x0E: a 1 s silent clip; stream index 0) fade out.
ADX_ALIASES = {0x13: 20}


def bgm_blob(a) -> bytes:
    """espgalarr_bgm.bin: "EBGM", track count, (offset, size) big-endian per track, then the
    Arrange ADX streams verbatim, 256-byte aligned (MAME espgalarr_bgm.cpp, core adx_stream.sv)."""
    idx = sorted({i for _, i, _ in ARRANGE_TRACKS.values()})
    assert idx == list(range(ADX_FIRST, ADX_FIRST + len(idx))), idx
    out = bytearray(0x100)
    ents = []
    for i in idx:
        data = a.read(i)
        info = adx.parse_header(data[:0x100])
        assert (info.encoding, info.block_size, info.bits, info.channels, info.sample_rate, info.version) == \
            (3, 18, 4, 2, 48000, 4), f"AFS {i}: unexpected ADX format"
        out += bytes(-len(out) % 0x100)
        ents.append((len(out), len(data)))
        out += data
    out[0:8] = b"EBGM" + struct.pack(">I", len(idx))
    for k, (o, n) in enumerate(ents):
        out[8 + 8 * k:16 + 8 * k] = struct.pack(">II", o, n)
    return bytes(out)


def run_adx(state, a, voice_hi: int, m_end: int) -> None:
    """The ADX streams go verbatim into espgalarr_bgm.bin; the sample ROM keeps the SFX and the
    voices only; the Z80 driver's song functions drive the stream player (asm/z80/adxstream.z80)."""
    state.bgm_blob = bgm_blob(a)
    size = m_end - rom_mod.M_CART_BASE
    if len(state.m_rom) < size:
        state.m_rom.extend(b"\xff" * (size - len(state.m_rom)))
    # blank the dropped arcade BGM instrument samples (everything after the SFX and the voices)
    state.m_rom[voice_hi - rom_mod.M_CART_BASE:size] = b"\xff" * (size - (voice_hi - rom_mod.M_CART_BASE))
    tracks = {cmd: i - ADX_FIRST for cmd, (name, i, tier) in ARRANGE_TRACKS.items()}
    tracks.update({c: i - ADX_FIRST for c, i in ADX_ALIASES.items()})
    p = bytes(state.p_rom)
    image = z80.build_blob_adx(p, tracks, Path(__file__).resolve().parents[2] / "asm" / "z80" / "adxstream.z80")
    _install_image(state, image)
    state.budget["bgm"] = {"bytes": len(state.bgm_blob), "tracks": len(tracks)}
    (state.out / "music.json").parent.mkdir(parents=True, exist_ok=True)
    (state.out / "music.json").write_text(json.dumps(
        [{"cmd": c, "name": ARRANGE_TRACKS[c][0] if c in ARRANGE_TRACKS else "alias", "afs_index": t + ADX_FIRST,
          "bgm_track": t} for c, t in tracks.items()], indent=2))
    state.note(f"music: lossless BGM, {len(set(tracks.values()))} ADX tracks ({len(tracks)} BGM commands) verbatim in espgalarr_bgm.bin "
               f"({len(state.bgm_blob)} bytes); sample ROM keeps the SFX and voices "
               f"({(voice_hi - rom_mod.M_CART_BASE) / 1048576:.1f} MB used)")


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
    _install_image(state, image)


def _install_image(state, image: bytes) -> None:
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
