# Espgaluda PS2: Arrange mode vs Arcade mode

Findings about how the PS2 ARRANGE mode differs from ARCADE mode, from static analysis of
`SLPS_253.52` and the A0A0 overlays. Addresses only; no original bytes. Sections are appended by
different passes.

## Music

Both modes keep the arcade 68k music request path: the 68k stub `0x17df18` (table at 68k 0x17cee0,
24 entries of 2 x BE16: stream index, secondary command) is called with D0 = music command by the
per-command stubs at 0x17df54..0x17e0f2. On PS2 the Z80 send is replaced by a lookup of a 13-entry u32
stream table (arrange `0xbf6c20`, arcade `0xcdaa10`, see spec/ps2-formats.md "Streamed music path")
and a call to `snd_stream_request(0, id, loop=1, vol=0xdc)` (0x12d5b0). The 68k-side table is identical
in both overlays; only the stream table differs, so the mode decides which ADX of `A980/A980`
(29 streams, stereo 48 kHz ADX, all long tracks with header loop points) is played.

Loop behaviour: every request passes loop=1, which the stream service turns into `ADXT_SetLpFlg(1)`
before `ADXT_StartAfs`, so BGM and jingles loop at the loop points stored in their ADX headers
(e.g. stage-clear 11: 5.4 s -> end, 25: 7.9 s -> end). A new request fades the running stream
(flag 0x10) and starts the new one; cmd 14 maps to the 1-second non-looping clip 9 in both modes,
i.e. "silence". Requests are suppressed while the 68k word at PS2 0x358008 (arrange) / 0x358340
(arcade) is non-zero (attract/sound-off flag; 68k ~0x81dbc0 / ~0x81def8 under the provisional mapping).

| 68k cmd | stub (68k) | situation (evidence) | ARCADE A980 idx (len) | ARRANGE A980 idx (len) |
|---|---|---|---|---|
| 6 | 0x17df54 | title / attract init (0x13f81c clears 0x8135c0.. and loads palettes) | 16 (59 s) | 28 (57 s) |
| 10 | 0x17dfba | stage 1 BGM (stage table 0x18061c[0]; game-start routines 0x15f02a/0x160044/0x121fc6) | 0 (188 s) | 18 (206 s) |
| 12 | 0x17dfce | stage 2 BGM (table[1]; stage-2 event list 0x14b426) | 1 (231 s) | 19 (404 s) |
| 13 | 0x17dfe2 | stage 3 BGM (table[2]; 0x14b696) | 2 (188 s) | 20 (254 s) |
| 15 | 0x17dff6 | stage 4 BGM (table[3]; 0x14ba58) | 5 (214 s) | 21 (205 s) |
| 20 | 0x17e00a | stage 5 BGM, 5-1 and 5-2 (table[4],[5]; 0x14bcc6); PS2 handler only re-requests when stage index == 4 | 6 (216 s) | 22 (311 s) |
| 11 | 0x17dfa6 | boss BGM, stages 1-4 and 5-2 boss (event lists 0x14b1b8/0x14b43a/0x14b6aa/0x14ba6c/0x14be6e; boss object 0x1badb6) | 10 (188 s) | 24 (180 s) |
| 16 | 0x17e032 | stage 5-1 boss (0x14bcd2; object handler 0x1adcce) | 14 (155 s) | 27 (192 s) |
| 8 | 0x17df7e | stage clear jingle (0x180b80, sets flag 0x81e3cc) | 11 (9 s, loops) | 25 (15 s, loops) |
| 18 | 0x17e05c | ending / staff roll (0x183e22 after building 7x28 text entries from 0xd5230) | 13 (229 s) | 26 (270 s) |
| 9 | 0x17df92 | name entry (0x182662 after ranking check 0x178fe2, 33 s timer) | 8 (76 s) | 23 (299 s) |
| 14 | 0x17e070 | sound-test slot after name entry (game over?); never requested by code | 9 (1 s, silent) | 9 (1 s, silent) |
| 19 | (none) | alias of stage 3 (same stream as 13) | 2 | 20 |
| 0-5, 7, 17 | 0x17e084.., 0x17df6a, 0x17e048 | stream index 0 = stop; secondary command 1-7 only | - | - |

(The sound-test list at 68k 0x138a48 orders the commands 6,10,12,13,15,20,11,16,8,18,9,14, matching
the situations above. The stage-5-2 event list 0x14be62 requests cmd 10 with D7=0x55 before its boss;
the D7 value is the secondary parameter and does not affect the stream choice.)

A980 entries 3, 4, 7, 12, 15 (short non-looping stereo clips) and 17 (identical to 9) are not
referenced by either stream table. `A980/A981` (10 short mono clips) is registered as partition 0x981
by the ELF at start-up together with A980, but no code in the ELF or either overlay forms a 0x0981xxxx
stream id and the in-memory ADXT_StartMem*/StartSj entry points are unreferenced, so the A981 voices
are not played through the stream path in either mode.

### Music implementation (ported)

- The arcade sequencer is bypassed: the 68k BGM request routines (0x17DF18 / 0x17DF36) no longer
  upload instrument/song banks; they send the BGM index itself as the song id (`asr #2` of the
  table offset) with the original command types 0x11/0x12.
- The Z80 driver image is extended (our code at Z80 0x6900, the former BGM instrument window)
  and relocated into free program ROM; the driver's song-play / song-stop / song-fade C functions
  (Z80 0x2E47 / 0x2DAA / 0x2DC7) are redirected to a stream player that programs one ICS2115
  voice (voice 15, in the sequencer's range 0-15; the SFX allocator cycles 16-31) with a
  bank-resident looped 8-bit u-law sample. Fade is currently an immediate stop.
- Each Arrange track is converted to mono u-law at the highest rate at which every in-game track
  keeps >= 90 s (others >= 30 s) inside one 1 MB bank; with the 12 MB window this is 11025 Hz with
  76-95 s loops cut at the most self-similar point, the title (57 s) and stage-clear jingle intact.
  All original SFX samples stay in place; the arcade BGM instrument samples are blanked.
- Verified in MAME: stage-1 run of the built set shows the stream starting on the game's BGM
  request and persisting through shot effects, at a loudness close to the original mix.

## Gameplay rules (status)

Source of the rule list: Arika's Arrange page, ja.wikipedia, Impress (see the plan); exact
values come from the PS2 code. Port status: **ported** = applied by `espgalarr/stages/code.py`,
**pending** = waiting for the de-translated Arrange routines (espgalarr/detrans).

| Rule | PGM anchor (spec/pgm-gameplay.md) | Status |
|---|---|---|
| Extends at 80,000,000 and 280,000,000 | extend tables 0x178D7E / 0x178D8E (10-pt units) | ported (all dip settings except "none") |
| No continues / no mid-game join | continue flag tests 0x1791DE, 0x1795D6, 0x140DA8 | ported (bne->bra), not yet exercised in MAME |
| New characters Chihiro / Black (speeds, hitboxes, Normal/Power Shot) | mode records 0x12785A, shot routines 0x126960.., player update 0x12392E | pending |
| Guard barrier minimum cost 1/3, bullet->gem / gold conversion, auto-barrier full drain | barrier 0x122A02, gauge +0x24 (cost 0x400 of 0xFA0) | pending |
| More gems, cheaper Kakusei | kill reward 0x178140, drain 0x1252B6 | pending |
| New counter + simultaneous-multiplier counter | gold value 0x1705FA, score 0x178104.. | pending |
| Denser/faster barrages, rank | rank 0x1481A4, bullet speed adds 0x813B00.. ; stage scripts | pending (stage-level changes live in the per-stage PS2 overlays) |
| Shot / barrier button swap | input decode 0x113B58 (bits 4-6) | pending (depends on the new characters' button layout) |
| HUD counters | 0x17F37C per-player HUD, 0x177E44 score | pending |
| Redrawn player/effect art (78 sprites) | animation records 0x142910.., frame arrays 0x141C16.. | 29 ported (records), 49 pending (sizes in code) |
