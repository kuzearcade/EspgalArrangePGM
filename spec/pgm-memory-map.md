# PGM (espgalbl) memory map notes

68k program ROM espgaluda_u8.bin is mapped at 0x000000 (4 MB, 0x200000+ empty);
RAM 0x800000-0x81FFFF; the cart replaces the BIOS.  Details and evidence live
in work/pgm/notes/*.md.

## Protection

* The original game only used the IGS027A from the service-menu "Asic27 Test"
  module (ROM 0x11F67E-0x1219D6, dispatcher 0x121A2C).  Gameplay never reads
  0x400000-0x400005.
* ASIC I/O (original): value word -> 0x400000, command word -> 0x400002,
  reply polled from 0x400002/0x400000, 0x400004 cleared after cmd 0x99.
  Key/command/value shadows at RAM 0x801070/0x801072/0x801074; slots at
  0x81F008 (32 x 32 bit), slot pointer 0x81F000, result 0x81F004, error flag
  0x80106C.
* Bootleg: the three poll routines were replaced by 68k simulations of
  commands 0x99 (0x11FDCE), 0x8E (0x11FE74) and 0x67/0xE5/0x40 (0x11FF2E);
  all other command bytes hang (`bra *` at 0x11FEE8 / 0x120020).  The 129
  `move.w d0,$400000/$400002` writes remain and are harmless; the test screen
  is broken but unreachable in play.
* 0xD00000-0xD0FFFF (DDP2 ARM shared RAM) and 0xD10000 (latch) are never
  accessed.  MAME's init_ddp2 decrypts only the ARM data ROM (region user1)
  and installs a 68k read hook at 0x80EE54 active only at PC 0x149DCE/0x149CFE.
* Build rule: do not read 0x400000-0x4FFFFF, do not touch 0xD00000-0xD10001,
  do not place code that reads 0x80EE54 at PC 0x149CFE/0x149DCE.  The ASIC test
  module can be dropped.
* The espgalarr build uses 0xD00000-0xD0FFFF as plain RAM (bullet pool, extended sprite list:
  enhanced hardware), never the latch. Its MAME machine (`espgalarr`, pgm.cpp) is a plain PGM
  with that RAM, init_pgm, no IGS027A and no ARM ROMs (2026-11-06); only the espgalbl-shaped
  drop-in keeps the ARM ROMs, for stock MAME's DDP2 driver.
* With the pool relocated, the arcade pool's work RAM (0x818352-0x81B551) is unreferenced except
  for the Arrange walker counts at 0x818352 / 0x818354. 0x818358 (long) holds the allocator burst
  cursor (pgm/allocburst.py), and program ROM 0x3FFC00-0x3FFDFF holds its entries; the build's
  allocations stop below 0x3FFC00 (build.P_TOP).
* Bootleg hook stubs at ROM 0x200 (from 0x13A792) and 0x220 (from 0x177E16)
  call 0x178C72 (config table copy into 0x80107E) and write 0x5A20 to
  0x81EFFE; not protection related.

## Sound

* Latches: 68k 0xC00003 (-> Z80 port 0x8200, write raises Z80 NMI),
  0xC00005 (<- Z80 port 0x8100), 0xC0000D (-> Z80 port 0x8400).  Z80 reset
  0xC00008 (0xA659 assert, 0x5050 run), Z80 control 0xC0000A (0x45D3 halt /
  0x0A0A run).  Z80 RAM window 0xC10000-0xC1FFFF (word writes, byte-swapped
  for 16-bit cells).
* Z80 driver blob: ROM 0x1BE9D0, 0x61A2 bytes -> Z80 0x0000 (0x17E1E0).
* SFX instrument table: ROM 0xE37D4, 0x67 x 12 -> Z80 0x76C0, count at Z80
  0x0042.  BGM instrument windows: 160 x 22 from the master table 0xE91A6
  (651 entries) -> Z80 0x6900, count 0x0062 = 160.  Songs -> Z80 0xA6C0,
  pointer at 0x0070, count 0x0052 = 1 (one song per bank upload).
* Bank table 0x17CA18 (13 banks), BGM index table 0x17CEE0 (21 entries),
  BGM request 0x17DF18/0x17DF36 (d0 index, d7 volume), SFX request 0x17CFA6
  (type 0, d0 id, d1 volume, d2 channel).  Command block = 8 bytes written to
  Z80 0x0006 by the per-frame sender 0x17D12A from the 50-entry queue at
  0x81DE90.  Command types: 0-3 SFX play, 0xD-0xF SFX stop, 0x10 stop SFX,
  0x11/0x12 song play, 0x15 song stop, 0x16 fade, 0x1D stop all, 0x20 misc.
* BGM banks: 4 title, 1 stage 1, 6 stage 2, 7 stage 3, 9 stage 4, 0xA stage 5,
  2 boss, 0xC stage-5 second part / last boss, 5/0xB/3 game-flow screens
  (unverified), 8 sound-test only, 0 unused.
* M ROM (4 MB at ICS 0x400000): SFX samples 0x400000-0x5BD167 (1.74 MB,
  keep); BGM samples 0x5BD167-0x7161FA (1.35 MB); 0x7161FA-0x7FFFFF unused.
  Replacing the music frees file offsets 0x1BD167-0x3FFFFF (2.26 MB).
  Sample address = (bank_byte & 0xF) << 20 | (u32 >> 12).
