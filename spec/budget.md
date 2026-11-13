# ROM budget (from espgalarr build's out/budget.md, 2026-11-12)

Enhanced (default) build (the soundtrack is the lossless stream `espgalarr_bgm.bin`, outside the
sample ROM):

| ROM | size | cap | used by Arrange additions |
|---|---|---|---|
| p_rom | 4.00 MB | 4 MB | 195 KB |
| t_rom | 8.00 MB | 8 MB | 0 KB |
| a_rom | 16.00 MB | 16 MB | 504 KB |
| b_rom | 8.00 MB | 8 MB | 393 KB |
| m_rom | 12.00 MB | 12 MB | 0 KB |

Plain PGM build (`--arcade-records --arcade-bullets --arcade-shots --arcade-sprites --bgm ulaw`;
the soundtrack resampled into the 12 MB sample ROM):

| ROM | size | cap | used by Arrange additions |
|---|---|---|---|
| p_rom | 4.00 MB | 4 MB | 83 KB |
| t_rom | 8.00 MB | 8 MB | 0 KB |
| a_rom | 16.00 MB | 16 MB | 199 KB |
| b_rom | 8.00 MB | 8 MB | 135 KB |
| m_rom | 12.00 MB | 12 MB | 9280 KB |
