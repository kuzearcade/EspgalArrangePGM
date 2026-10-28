"""Espgaluda (espgalbl) sound system: Z80 driver blob, instrument/song tables,
M-ROM sample usage.

Everything here was derived from the 68k program (work/pgm/dis/p_rom.lst) and
the Z80 driver blob; see work/pgm/notes/sound.md for the evidence.

Layout summary (68k addresses == ROM offsets, ROM mapped at 0):

* Z80 driver blob: ROM 0x1BE9D0, 0x30D1 words = 0x61A2 bytes, uploaded to Z80
  RAM 0x0000 by 0x17E1E0 (boot) / 0x17CB4A (test menu).
* SFX instrument table: ROM 0xE37D4, 0x67 entries x 12 bytes, uploaded to Z80
  RAM 0x76C0 by 0x17E622 (count written to Z80 0x0042).  Entry 0x66 is a
  terminator (not a sample).
* Song banks: 13-entry pointer table at 0x17CA18 -> 12-byte records
  {bgm_instr_table, 1, song_struct} at 0x17CA4C.  0x17CAE8(d0=bank) uploads
  the song (0x17E3F4) to Z80 RAM 0xA6C0 (pointer at 0x0070, count 0x0052) and
  0xDC0 bytes of 22-byte BGM instrument entries to Z80 RAM 0x6900.
* BGM index table at 0x17CEE0: 21 x (bank.w, song_id.w); 0x17DF18/0x17DF36
  (d0 = index, d7 = volume) upload the bank then queue command 0x12 / 0x11.

Sample address encoding (both 12- and 22-byte entries): byte 0 is the value
written to ICS2115 register 0x11 (wavesample static address 27..20); its low
nibble is the 1 MB bank, so 0x44 -> 0x400000 and 0x45 -> 0x500000.  The 32-bit
little-endian start/end/loop fields are ICS oscillator addresses with 12
fraction bits (MAME: curaddr = (saddr << 20) | (acc >> 12)), i.e. byte offset
inside the bank = value >> 12.  The cart M ROM (cave_w04801b032.u17) sits at
ICS 0x400000.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

M_CART_BASE = 0x400000

# Known locations (verified against instruction patterns in find_* helpers).
Z80_UPLOAD_SITE = 0x17CB60          # lea $c10000,a0 ; lea $1be9d0,a1 ; move.l #$30d0,d0
Z80_BLOB_ADDR = 0x1BE9D0
Z80_BLOB_WORDS = 0x30D1
SFX_TABLE_ADDR = 0xE37D4
SFX_TABLE_COUNT = 0x67              # last entry is a terminator
SFX_UPLOAD_SITE = 0x17CB22          # move.l #$67,-(a7) ; pea $e37d4 ; jsr $17e622
SONG_BANK_PTRS = 0x17CA18           # 13 longs -> 12-byte records
SONG_BANK_COUNT = 13
BGM_INDEX_TABLE = 0x17CEE0          # 21 x (bank.w, id.w)
BGM_INDEX_COUNT = 21
BGM_INSTR_BLOB_BYTES = 0xDC0        # 160 x 22 uploaded to Z80 0x6900
SONG_UPLOAD_Z80 = 0xA6C0
BGM_INSTR_Z80 = 0x6900
SFX_INSTR_Z80 = 0x76C0

# Z80 driver header cells (Z80 RAM addresses, filled by the 68k uploads).
Z80_CMD_MAILBOX = 0x0006            # 8-byte command block (68k 0xC10006)
Z80_SFX_TABLE_PTR = 0x0040          # -> 0x76C0
Z80_SFX_COUNT = 0x0042
Z80_SONG_TABLE_PTR = 0x0050         # -> 0x0070 (song pointer list)
Z80_SONG_COUNT = 0x0052
Z80_BGM_TABLE_PTR = 0x0060          # -> 0x6900
Z80_BGM_COUNT = 0x0062              # 0xA0 in the blob
Z80_SONG_PTRS = 0x0070

# Sound command bytes (byte 0 of the 8-byte block; Z80 switch at 0x0916).
CMD_SFX_PLAY = 0x00
CMD_SFX_PLAY_1 = 0x01
CMD_SFX_PLAY_2 = 0x02
CMD_SFX_PLAY_PARAM = 0x03
CMD_SFX_STOP = 0x0F
CMD_SFX_STOP_ALL = 0x10
CMD_BGM_PLAY = 0x11
CMD_BGM_PLAY_ALT = 0x12
CMD_BGM_STOP = 0x15
CMD_BGM_FADE = 0x16
CMD_STOP_EVERYTHING = 0x1D
CMD_MISC_20 = 0x20


@dataclass(frozen=True)
class Sample:
    """One ICS2115 wavesample; addresses are ICS addresses (M ROM at 0x400000)."""
    index: int
    bank_byte: int
    flags: int
    freq: int
    start: int
    end: int
    volume: int | None = None
    loop_start: int | None = None
    loop_end: int | None = None

    @property
    def size(self) -> int:
        return self.end - self.start

    @property
    def loop(self) -> bool:
        return bool(self.flags & 0x08)

    @property
    def rom_range(self) -> tuple[int, int]:
        """(start, end) as offsets into the cart M ROM file."""
        return self.start - M_CART_BASE, self.end - M_CART_BASE


@dataclass(frozen=True)
class Song:
    bank: int
    struct_addr: int
    instr_table_addr: int
    n_instruments: int
    n_channels: int
    n_patterns: int
    byte3: int
    map_addr: int
    size_table_addr: int
    pattern_data_addr: int
    pattern_bytes: int
    instrument_map: tuple[int, ...]     # b0 bytes at map_addr; NOT the set of used
                                        # instruments (see parse_song_banks)
    window: tuple[Sample, ...]          # the uploaded 160-entry instrument window

    @property
    def samples(self) -> set[tuple[int, int]]:
        """Conservative sample set: every valid entry of the uploaded window.
        An ICS trace in MAME (work/pgm/notes/sound.md) shows the title song
        (bank 4, map = 0..6) starting voices on window entries 39-45, so the
        patterns address the window directly and only a pattern-data parser
        could narrow this further."""
        return {(s.start, s.end) for s in self.window if s.size > 0}


def _ics_addr(bank_byte: int, raw: int) -> int:
    return ((bank_byte & 0xF) << 20) + (raw >> 12)


def _parse12(index: int, e: bytes) -> Sample:
    bank = e[0]
    freq = struct.unpack_from("<H", e, 2)[0]
    s, en = struct.unpack_from("<II", e, 4)
    return Sample(index, bank, e[1], freq, _ics_addr(bank, s), _ics_addr(bank, en))


def _parse22(index: int, e: bytes) -> Sample:
    bank = e[0]
    freq = struct.unpack_from("<H", e, 2)[0]
    s, en = struct.unpack_from("<II", e, 4)
    ls, le = struct.unpack_from("<II", e, 14)
    return Sample(index, bank, e[1], freq, _ics_addr(bank, s), _ics_addr(bank, en),
                  e[12], _ics_addr(bank, ls), _ics_addr(bank, le))


# --- Z80 driver blob --------------------------------------------------------

_UPLOAD_RE = re.compile(
    rb"\x00\x00\x00\x00\x00\x00"          # lea.l $c10000.l, a0
    rb"\x43\xf9(?P<src>[\x00-\x1f][\x00-\xff]{3})"   # lea.l $src.l, a1
    rb"\x20\x3c(?P<cnt>[\x00-\xff]{4})",  # move.l #cnt, d0   (dbra -> cnt+1 words)
    re.DOTALL)


def find_z80_blob(p_rom: bytes) -> tuple[int, int]:
    """Locate the Z80 program uploaded to 0xC10000: returns (rom_offset, size)."""
    m = _UPLOAD_RE.search(p_rom)
    if not m:
        raise ValueError("Z80 upload loop not found")
    src = struct.unpack(">I", m.group("src"))[0]
    words = struct.unpack(">I", m.group("cnt"))[0] + 1
    if src + 2 * words > len(p_rom) or p_rom[src:src + 3] != b"\xed\x56\xc3":   # im 1 ; jp
        raise ValueError(f"implausible Z80 blob at {src:#x}")
    return src, 2 * words


def parse_driver(blob: bytes) -> dict:
    """Header cells of the Z80 driver image (the tables themselves are uploaded
    separately by the 68k, see parse_sfx_table / parse_song_banks)."""
    if blob[:3] != b"\xed\x56\xc3":
        raise ValueError("not the PGM Z80 driver (expected IM 1 ; JP)")
    u16 = lambda off: struct.unpack_from("<H", blob, off)[0]
    return {
        "entry": u16(3),
        "irq_vector": u16(0x39) if blob[0x38] == 0xC3 else None,
        "nmi_vector": u16(0x67) if blob[0x66] == 0xC3 else None,
        "sfx_table_ptr": u16(Z80_SFX_TABLE_PTR),
        "sfx_count": u16(Z80_SFX_COUNT),           # 0 in the blob, written at boot
        "song_table_ptr": u16(Z80_SONG_TABLE_PTR),
        "song_count": u16(Z80_SONG_COUNT),         # 0 in the blob, written per upload
        "bgm_table_ptr": u16(Z80_BGM_TABLE_PTR),
        "bgm_count": u16(Z80_BGM_COUNT),
        "sfx_entry_size": 12,
        "bgm_entry_size": 22,
        "size": len(blob),
    }


# --- 68k-side tables ----------------------------------------------------------

def parse_sfx_table(p_rom: bytes, addr: int = SFX_TABLE_ADDR,
                    count: int = SFX_TABLE_COUNT) -> list[Sample]:
    """The 12-byte SFX instrument table (last entry is a terminator, dropped)."""
    out = []
    for n in range(count):
        e = p_rom[addr + 12 * n: addr + 12 * n + 12]
        if (e[0] & 0xF0) != 0x40:          # terminator / garbage
            break
        out.append(_parse12(n, e))
    return out


def parse_song_banks(p_rom: bytes, table: int = SONG_BANK_PTRS,
                     count: int = SONG_BANK_COUNT) -> list[Song]:
    songs = []
    for n in range(count):
        rec = struct.unpack_from(">I", p_rom, table + 4 * n)[0]
        instr_tbl, nsongs, song = struct.unpack_from(">III", p_rom, rec)
        if nsongs != 1:
            raise ValueError(f"bank {n}: unexpected song count {nsongs}")
        h = p_rom[song:song + 18]
        b0, b1, b2, b3 = h[0], h[1], h[2], h[3]
        map_addr, size_tbl, pat = struct.unpack_from(">III", h, 4)
        size = struct.unpack_from(">H", h, 16)[0]
        imap = tuple(p_rom[map_addr:map_addr + b0])
        songs.append(Song(n, song, instr_tbl, b0, b1, b2, b3, map_addr, size_tbl, pat, size,
                          imap, tuple(parse_bgm_window(p_rom, instr_tbl))))
    return songs


def parse_bgm_window(p_rom: bytes, addr: int, count: int = BGM_INSTR_BLOB_BYTES // 22) -> list[Sample]:
    """The 22-byte BGM instrument entries uploaded from `addr` to Z80 0x6900.
    The six windows used by the 13 banks are slices (at 22-byte boundaries) of
    one master table starting at 0xE91A6 (651 entries); the last window is
    shorter than 160, so stop at the first implausible entry."""
    out = []
    for i in range(count):
        e = p_rom[addr + 22 * i: addr + 22 * i + 22]
        if (e[0] & 0xF0) != 0x40 or e[1] not in (0x00, 0x08):
            break
        s = _parse22(i, e)
        if not (s.start <= s.end <= 0x800000):
            break
        out.append(s)
    return out


def parse_bgm_index(p_rom: bytes, table: int = BGM_INDEX_TABLE,
                    count: int = BGM_INDEX_COUNT) -> list[tuple[int, int]]:
    """BGM index -> (bank, song id) as used by 0x17DF18/0x17DF36."""
    return [struct.unpack_from(">HH", p_rom, table + 4 * i) for i in range(count)]


# --- usage / classification -------------------------------------------------

@dataclass
class Usage:
    sfx: list[Sample]
    songs: list[Song]
    sfx_ranges: set[tuple[int, int]] = field(default_factory=set)
    bgm_ranges: set[tuple[int, int]] = field(default_factory=set)
    bgm_only: set[tuple[int, int]] = field(default_factory=set)
    sfx_only: set[tuple[int, int]] = field(default_factory=set)
    shared: set[tuple[int, int]] = field(default_factory=set)

    @staticmethod
    def _bytes(rs) -> int:
        return sum(b - a for a, b in rs)

    @property
    def sfx_bytes(self) -> int:
        return self._bytes(self.sfx_ranges)

    @property
    def bgm_bytes(self) -> int:
        return self._bytes(self.bgm_ranges)

    @property
    def bgm_only_bytes(self) -> int:
        return self._bytes(self.bgm_only)

    def referenced_extent(self) -> tuple[int, int]:
        rs = self.sfx_ranges | self.bgm_ranges
        return min(a for a, _ in rs), max(b for _, b in rs)


def _overlaps(r, others) -> bool:
    a, b = r
    return any(a < d and c < b for c, d in others)


def sample_usage(p_rom: bytes) -> Usage:
    """Classify M-ROM sample ranges: used by SFX commands (must keep) vs only by
    BGM songs (reclaimable when the music is replaced)."""
    sfx = parse_sfx_table(p_rom)
    songs = parse_song_banks(p_rom)
    u = Usage(sfx, songs)
    u.sfx_ranges = {(s.start, s.end) for s in sfx if s.size > 0}
    for song in songs:
        u.bgm_ranges |= song.samples
    for r in u.bgm_ranges:
        (u.shared if _overlaps(r, u.sfx_ranges) else u.bgm_only).add(r)
    for r in u.sfx_ranges:
        if not _overlaps(r, u.bgm_ranges):
            u.sfx_only.add(r)
    return u


def free_m_rom_ranges(usage: Usage, m_rom_size: int, keep_bgm: bool = False) -> list[tuple[int, int]]:
    """M-ROM file offset ranges not referenced by any kept sample (sorted)."""
    keep = set(usage.sfx_ranges)
    if keep_bgm:
        keep |= usage.bgm_ranges
    rs = sorted((a - M_CART_BASE, b - M_CART_BASE) for a, b in keep)
    out = []
    pos = 0
    for a, b in rs:
        if a > pos:
            out.append((pos, a))
        pos = max(pos, b)
    if pos < m_rom_size:
        out.append((pos, m_rom_size))
    return out
