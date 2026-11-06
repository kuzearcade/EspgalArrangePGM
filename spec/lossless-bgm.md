# Lossless Arrange BGM (MiSTer core + MAME): plan

Status: plan, 2026-11-06. Spike 1 (MRA size / load time) in progress.

## Goal

Play the PS2 Arrange soundtrack exactly as the PS2 decodes it: 48 kHz stereo, full length, the
tracks' own loop points (intro once, loop body forever), no trimming or resampling. The current
build (`music/convert.py`, `asm/z80/stream.z80`) plays 11 kHz mono u-law cut to 57-95 s with one
loop on an ICS2115 voice; it stays as the plain-PGM-hardware option.

## Source

AFS `A980/A980`, entries 18-28 (the Arrange set; `out_*/music.json` maps the 68k BGM commands):
CRI ADX, encoding 3, 4-bit, 18-byte frames, 2 channels, 48000 Hz, cutoff 500 (one coefficient
pair for all), loop flag set. Every loop start is a multiple of 32 samples (a frame boundary) and
every loop end is the track's last sample.

| AFS | Use | Length | Loop start | Bytes |
|---|---|---|---|---|
| 18 | stage 1 | 206.2 s | 3.38 s | 11,137,024 |
| 19 | stage 2 | 403.8 s | 100.66 s | 21,809,152 |
| 20 | stage 3 | 254.1 s | 3.14 s | 13,723,648 |
| 21 | stage 4 | 204.8 s | 25.60 s | 11,063,296 |
| 22 | stage 5 | 310.8 s | 69.17 s | 16,787,456 |
| 23 | name entry | 299.4 s | 77.88 s | 16,171,008 |
| 24 | boss | 180.0 s | 3.16 s | 9,721,856 |
| 25 | stage clear | 15.4 s | 7.85 s | 831,488 |
| 26 | ending | 270.0 s | 39.64 s | 14,583,808 |
| 27 | stage 5 boss | 192.1 s | 21.46 s | 10,375,168 |
| 28 | title | 57.2 s | 2.36 s | 3,092,480 |

Total 39.9 min, 129,296,384 bytes of ADX (129,284,756 up to the loop ends). As 16-bit PCM it
would be 460 MB, so the ADX bytes are stored verbatim and decoded in hardware: bit-exact with the
PS2's decode and smaller than any lossless re-encoding.

## Core constraints (Arcade-IGSPGMPlus_MiSTer, branch espgalarr)

- SDRAM 128 MB: ROMs use up to ~0x0500_0000; ~48 MB free. Too small.
- DDR3 core window 0x3000_0000-0x3FFF_FFFF: 0x3000_0000 is the MRA staging area (the `<rom
  address="0x30000000">` stream, ~51 MB, replayed by `ddr_rom_loader_adaptor`); 0x3C00_0000+ holds
  the protection/xram (0xE00000 RAM at 0x3CC0_0000) and savestates (0x3E00_0000). 0x3400_0000-
  0x3BFF_FFFF (128 MiB) is free: the BGM blob goes to 0x3400_0000 (ends ~0x3BB5_0000).
- Block RAM 553/553: the streamer uses flip-flops / MLAB only (~100 bytes of buffers).
- `hps_io` keeps 27 bits of the download length (`ioctl_addr[26:16] <= io_din[10:0]`), and the
  staging adaptor takes its length from it: the main stream must stay < 128 MiB. The BGM is a
  second `<rom index="2" address="0x34000000">` element instead: Main_MiSTer
  (support/arcade/mra_loader.cpp `rom_finish`) buffers it whole in HPS RAM and `shmem_put`s it
  straight into DDR3; the core only ignores index 2 (it never streams it).
- Audio: `AUDIO_R = AUDIO_L`, `AUDIO_MIX = 3`; the ICS2115 L+R is summed to mono (PGM.sv).
- Z80 I/O: the ICS2115 decodes when `z80_addr[11:8] == 0` (igs026_x.sv), latches at 0x81xx /
  0x82xx / 0x84xx (MAME pgm_z80_io): 0x88xx is free in both.
- clk_sys 60 MHz / 1250 = 48 kHz exactly; the MiSTer audio clock (24.576 MHz / 512) comes from
  the same 50 MHz reference, so each sample is output once with no drift.

## Hardware (core)

1. `rtl/adx_stream.sv`, on clk_sys:
   - DDR client at the lowest priority of the `ddr_mux` chain; prefetches the next stereo frame
     pair (2 x 18 bytes) into registers. 54 KB/s average, one 36-byte fetch per 0.67 ms.
   - Header parse from DDR at track start (data offset, sample count, loop start/end); directory
     at the blob start (track -> offset, length).
   - Decoder: per sample `s = nib * scale + ((c1 * h1 + c2 * h2) >> 12)`, clamped to 16 bits;
     c1/c2 constants (cutoff 500 @ 48 kHz). One multiplier time-shared over the 1250 clocks per
     output sample.
   - Loop: at loop end, seek to the loop-start frame and set the predictor history the way the
     PS2 does (to verify, PS2 RE item 3).
   - State: track, byte position, sample in frame, h1/h2 per channel, saved loop history, volume,
     play/stop/pause.
2. Register block, Z80 I/O 0x8800-0x880F (next to the latches in igs026_x.sv): track, command
   (play / stop / pause / resume), volume L/R (fades driven by the Z80, as on the PS2), status
   (playing, looped) readable.
3. Stereo mixer: BGM L/R at the PS2's gain plus the ICS2115 SFX (mono to both sides), clamped like
   the SPU2. `AUDIO_MIX` becomes an OSD option (default stereo); a BGM volume OSD option. MiSTer's
   audio filter must be flat for a bit-exact result.
4. Savestates: the streamer's ~40 bytes of state get an `ssb` slot (the ADX data is static).
5. Size: a few hundred LUT/FF, no block RAM, at most one DSP; clk_sys only, away from the CPU
   island.

## Software

- Build: extract AFS 18-28 at build time into `espgalarr_bgm.bin` (directory + verbatim ADX; no
  disc bytes in git), MRA `<rom index="2" address="0x34000000">`, manifest hashes, MAME patch.
  The M ROM goes back to SFX only (the 12 MB sample-ROM extension is no longer needed).
- Z80 driver: `stream_play` / `stream_stop` / `stream_fade` write port 0x88xx instead of ICS
  voice 15 (freeing it for SFX); fades and pause follow the PS2's.
- MAME: an `espgalarr` stream device (same registers and decoder, region "bgm") in
  patches/mame, so game-side work and tests stay in MAME.
- Plain PGM hardware keeps the u-law path (build option).

## PS2 reverse engineering

1. The full BGM command -> track map: attract / title, stage 6 and the final boss, game over,
   extends; whether Arrange uses the short AFS stingers (3, 4, 7, 9, 12, 15, 17) or the A981
   voices (10 mono ADX: a second, mono stream channel would cover them).
2. The CRI player calls in the ELF (start / stop / output volume): fade lengths and curves, volume
   levels, start latency; matched frame for frame on a PCSX2 trace of the BGM events.
3. Loop seam: the predictor history at the loop restart (PCSX2 hook on the CRI decode buffer over
   a loop of the 15 s stage clear track, compared with `espgalarr/ps2/adx.py`).
4. Mix balance: BGM:SFX ratio and SPU2 master levels (PCSX2 audio dump).
5. Whether Arrange's SFX are the arcade samples or new PS2 bank sounds (the latter would need a
   VAG / SPU2 voice engine: a later phase).

## Verification

- Decoder: Verilator unit testbench of `adx_stream.sv` alone, bit-exact against `adx.py` for all
  11 tracks including two passes over each loop seam. `adx.py` itself against the PCSX2 capture.
- Board: HDMI capture cross-correlated with the reference PCM (SFX muted, unity gain), 10-minute
  runs (no drift / dropped samples), savestate save / load while playing, DDR contention (xram
  0xE00000 miss latency, no CPU island stalls).
- Game: MAME and board replays of the long traces with BGM start / stop / fade frames against the
  PCSX2 events.

## Order

1. Spike: the BGM blob as a second MRA `<rom>` element on the current core: does it load, how
   long it takes, and is DDR3 at 0x3400_0000 byte-exact afterwards (read back through /dev/mem on
   the board).
2. Decoder module + testbench; MAME device (game-side work can start there).
3. PS2 RE items 1-4; Z80 driver.
4. Core register block, mixer, savestates, timing closure; board checks.

Risks: load time (ADX barely compresses: the zip grows by ~125 MB; Main_MiSTer buffers each
`<rom>` whole in HPS RAM), the loop-seam predictor behaviour, timing closure (small: clk_sys only).
