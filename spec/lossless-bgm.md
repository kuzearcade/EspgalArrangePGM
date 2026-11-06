# Lossless Arrange BGM (MiSTer core + MAME): plan

Status: plan, 2026-11-06. Spike 1 done (below): the blob loads on the current core.

## Spike 1 result (2026-11-06, board, core ddbc05d, ROM out_m49)

A second MRA element `<rom index="2" address="0x34000000" zip="espgalarr_bgm.zip">` holding the
blob (an "EBGM" directory: count, then (offset, size) big-endian per track, then AFS 18-28
verbatim, 256-byte aligned; 129,296,640 bytes; zip -9 117.1 MB) works on the unmodified core:
the game boots and runs the attract demo, and DDR3 0x3400_0000.. reads back byte-exact through
/dev/mem (md5 of all 129,296,640 bytes). Load time from `load_core` to data in DDR3: main stream
5.1 s (as without the BGM), BGM 16.0 s: the blob adds about 11 s per boot. HPS memory is freed
after the load (46 MB used). Tools: work/scratch/audiospike (mkblob.py, loadtime.py: zero a
window, launch the MRA through /dev/MiSTer_cmd, poll its md5; the zeroed window must not be all
zeros in the expected data).

## Goal

Play the PS2 Arrange soundtrack exactly as the PS2 decodes it: 48 kHz stereo, full length, the
tracks' own loop points (intro once, loop body forever), no trimming or resampling. The current
build (`music/convert.py`, `asm/z80/stream.z80`) plays 11 kHz mono u-law cut to 57-95 s with one
loop on an ICS2115 voice; it stays as the plain-PGM-hardware option.

## Decoder (done, 2026-11-06)

The PS2 decodes on the EE with CRI's ADXB (ADXRT Ver.2700; inner loop ELF 0x27CD3C):
`s = clamp16(((c1 * h1 + c2 * h2) >> 12) + nibble * scale)`, `scale = ((word ^ key) & 0x1FFF) + 1`
(the key is CRI's stream-encryption LCG, 0 here), one arithmetic shift of the summed products,
high nibble first; a frame whose scale word has bit 15 set ends the stream. Coefficients 7400 /
-3342 (vgmstream's formula, int(c * 8192) / int(c * c * -4096); the pair sits at EE 0x2ECE90 in
the PCSX2 dumps). `espgalarr/ps2/adx.py decode_exact()` is this decode (the older float
`decode()` is off by up to 191). All 11 headers are version 4 (loop fields at 0x24).

Core: `rtl/adx_stream.sv` (core commit ef0eb0a, not wired in), testbench `sim/adx_tb` (random
memory latency 1..40 clocks, sometimes 300): all 11 tracks bit-exact against decode_exact from
the start through their loop seams (1-2 passes; ~232 M stereo samples a mode), both loop modes,
no underruns. Compare script: work/scratch/audiospike/adxcmp.py TRACK [LOOPS] [RESTORE].

Loop seam (PCSX2, no-input invincible run to frame 60000, hook on the stereo decoder ELF 0x27D294
in watch mode logging the decoder state 0x2ECE70..: channels +4, input pointer +8, frames +0xC,
outputs +0x10/+0x14, L h1/h2 +0x18, R h1/h2 +0x1C; work/scratch/audiospike/ps2loop, seam.py /
seam2.py): the boss track (AFS 24) plays 359 s in one go from frame 28186; the call at 38962 decodes
the last two frames to the loop end and the call at 38963 starts with the history the first pass
had just before the loop start (sample 151,584). The CRI player restores the loop start's history
(vgmstream's behaviour): `decode_exact(loop_hist="restore")`, adx_stream `LOOP_RESTORE = 1`.
(0x27CCE8 is the mono decoder: boot voice only. The menus play no ADX.)

Still open: whether the loop end sample itself plays (`LOOP_END_INCL`; every track but stage 4 has
samples = loop end + 1; stage 4 has 7 more). One sample per loop; needs the player's output
(SPU2 feed) on PCSX2.

adx_stream keeps the loop start's frame in its own register (fetched at the track start), so the
jump never waits on memory: with an inclusive end, stage 4's last frame is used for one sample.
`adx_stream_ddr` = adx_stream + `adx_ddr_port` (single-beat reads on a `ddr_if`, `acquire` held per
read). Testbench (sim/adx_tb): all 11 tracks x {restore, continue} x {exclusive, inclusive}
bit-exact through the seams with no underruns (tick every 64 clocks, ~20x the real rate); the
ddr_if top (`make ddr`, random busy, 5-60 clock reads) bit-exact on stage clear, stage 4, title.

## Build and MAME (2026-11-06)

`espgalarr build --bgm adx` (default stays `ulaw` until the core plays the stream): music.py
`run_adx` writes `espgalarr_bgm.bin` (AFS 18-28, track = AFS index - 18; the same bytes as the
spike's blob, CRC 8d4dbf32) into the espgalarr zips only, keeps the SFX and the Arrange voices in
the sample ROM (1.8 MB used of the 12 MB window; the rest blank) and installs
`asm/z80/adxstream.z80`: the driver's song play / stop / fade write the stream port (track table:
68k BGM index -> blob track; fade = volume 0x80 -> 0 one step per Z80 timer tick, then stop, the
u-law driver's 128-tick shape; the PS2's fade timing is still to check). mamepatch emits the
optional `bgm_stream` region. MAME: `espgalarr_bgm` device (patches/mame/enhanced-sprites.diff,
src/mame/igs/espgalarr_bgm.cpp; Z80 I/O 0x8800-0x880F, stereo speaker `bgm_speaker` at 0.5).
out_bgm1 in MAME (-wavwrite, coin / start, 100 s): the title track from 8.42 s, stage 1 from
26.43 s; 74.9 s of stage 1 equal to decode_exact x 0.5 within 0.5 LSB on both channels.

## Core integration and board (2026-11-06)

Core (branch espgalarr, "espgalarr lossless BGM in the core"): PGM.sv decodes
Z80 I/O 0x88xx (z80_addr[11:8] = 8; the status byte reads back through igs026_x), ticks the
player at clk_sys / 1250 = 48 kHz, puts adx_stream_ddr on a new last leaf of the DDR mux
(ddr_mux_lo5 after the share caches) and mixes in stereo: each side = the ICS2115's (L + R) / 4
(as before) + BGM x volume / 0x100, saturated (provisional balance). Top: AUDIO_R, Stereo Mix
OSD (status[52:51]). The single-cycle decode failed timing (-2.4 ns: pos compare -> frame mux ->
multiplies -> clamp); adx_stream now decodes in a 4-cycle pipeline (tick -> jump; bytes /
history / frame swap; products; sum and clamp). Fit: timing met (setup +0.207 ns, hold
+0.175), 553/553 RAM blocks (none added), 53/112 DSP blocks.

Board (core installed as IGSPGMPlus_20261105.rbf, copy espgalarr_bak/IGSPGMPlus_20261106_bgm.rbf;
the ICS-fix core before it in espgalarr_bak/IGSPGMPlus_20261105_40mhz_icsfix.rbf; MRA
"Espgaluda Arrange (lossless BGM test)" with espgalarr_bgm1.zip = out_bgm1): no BGM in the
attract before a coin (as in MAME); after a coin the title track (blob track 10) plays. HDMI
capture (arecord hw:1,0, 48 kHz): L - R (the mono ICS mix cancels) against decode_exact x 0.5:
correlation 0.992, alignment constant (-2 samples) over 33 s, so sample-locked with no drift;
gain 0.955 of the 0.5 and a residual of ~10%: MiSTer's default output filter (no afilter set in
MiSTer.ini: flat to ~10 kHz, -1.5 dB at 20 kHz) and the capture path. A bit-exact board check
needs a digital tap of the core's AUDIO_L/R.

Still open: the stream's state in savestates; the PS2's BGM:SFX balance and fades; whether the
loop end sample plays; the full command -> track map (stage 6 / final boss / game over).

## PS2 volume, fades, loop end and mix (2026-11-06, PCSX2 SPU2 dump)

PCSX2 patch (patches/pcsx2/espgal-trace.diff to update): `ESPGAL_SPU2DUMP=file` writes 16 x int16
per 48 kHz SPU2 sample: core 0 / 1 raw input, after InpVol, core 0 / 1 dry voice mix, final
output. No-input invincible run to frame 40700 (work/scratch/audiospike/ps2loop/spu2.raw): the
BGM is core 1's input (InpVol unity).

- Stream volume: requests pass 0xDC (220 of 256); the stream manager (ELF 0x12D8B0, slots at
  0x5505D0) sets ADXT_SetOutVol(trunc(-20 log10(256 / v)) * 10) (conversion ELF 0x1249C0; the
  stored ADXT volume reads -10 in the PCSX2 dumps). The CRI player applies it as
  `(s * k) >> 12`, k = int(4096 * 10^(dB / 20)) & ~15 (-1 dB: 3648), bit-exact on the dump.
- Fade: a new request (or a fade) takes 4 off v every manager tick (one 60 Hz frame), the gain
  following the dB table (measured k: 3648, 3248, 2896, 2576, 2288, 2048, 1824, 1616, 1440,
  1280, 1152, 1024, 912, 816, 640, 512, 400, 320, 192, then 0 over the stage 1 -> boss
  change); at 0 the stream stops and a queued one starts at 220. The player models one step per
  800 samples (the PS2 frame is 800.8 samples at 59.94 Hz: 44 samples longer over a whole fade).
- Loop: the boss track (AFS 24) through its seam equals decode_exact(restore, loop end exclusive)
  x 3648 >> 12 bit for bit over 8.70 M samples (60,000 past the seam); "continue" differs for 701
  samples after it, an inclusive end for all of them. Both questions settled.
- Command map: the documented table (spec/arrange-diff.md, Music) plus cmd 0x13 = stage 3's
  stream; cmd 0x0E (a 1 s silent clip) and the stream-index-0 commands fade the running stream
  (the driver sends command 5).
- Mix: final output = BGM (core 1 input) x 1.000 + core 0 dry voices x ~1.0 + a residual (reverb);
  stage 1 no input: BGM rms ~4960, SFX ~600; stage 2: BGM ~4250, SFX ~2030.

adx_stream / espgalarr_bgm apply the same gain table and fades (gout_l / gout_r); testbench:
fade and crossfade bit-exact against the model with this table; savestate save / restore exact
at three points of the stage clear track.

## Board (2026-11-06, core 0de2c38 + the mix gain)

Balance: the PS2 output over track 15..90 s of stage 1 (no input, invincible) has BGM 4722 / SFX
1028 rms (4.59); the board, same stretch (invincible lossless test build out_bgm2i, HDMI
capture, the BGM separated by fitting a 48-tap filter on L - R and applying it to the reference's
mid), had 4.11 at volume / 0x100 and has 4.53 at volume x 286 / 0x10000. Fade: the title fades
out over 0.9 s in whole-dB steps when a game starts, then stage 1's track. Savestates: a restore
resumes the stream at the saved position. The Music DIP no longer silences the BGM (TODO 4a).

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
