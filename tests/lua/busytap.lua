-- busytap.lua (EXTRA_LUA of shoot.lua / timing_xl2.lua): the 68k's idle time per vblank period,
-- without the debugger, so a whole replay can be measured at any clock_scale.
--
-- The main loop's vblank wait (0x112B8C) sets 0x8011C0 and spins on `tst.b $8011c0.l / bne.b`
-- at 0x112C46 (26 cycles an iteration) until the vblank IRQ clears it. A read tap counts the
-- spin's reads of 0x8011C0; a write tap logs every write of the byte with its pc, value and the
-- reads since the previous write:
--   BUSY_LOG lines "ft pc value reads play" (ft = the replay script's frame numbering, play =
--   0x8139DC). Writes at 0x112B8C (value 1) end a logic step; the IRQ's writes start a vblank
--   period. tools/compare/busymap.py turns them into cycles per logic step.
-- Nothing is written to the machine. The taps are kept in globals (MAME collects locals).
local cpu = manager.machine.devices[":maincpu"]
local mt = cpu.spaces["program"]
local cpc = cpu.state["CURPC"]
local log = io.open(os.getenv("BUSY_LOG"), "w")
local reads, rf, play0 = 0, 0, nil
busytap_r = mt:install_read_tap(0x8011C0, 0x8011C1, "busy_r", function(offset, data, mask)
  if mask & 0xff00 ~= 0 then reads = reads + 1 end
end)
busytap_w = mt:install_write_tap(0x8011C0, 0x8011C1, "busy_w", function(offset, data, mask)
  if mask & 0xff00 ~= 0 then
    local ft = (play0 and rf + 1461 - play0 or rf) + 1   -- the frame in progress
    log:write(string.format("%d %x %x %d %x\n", ft, cpc.value, (data >> 8) & 0xff, reads, mt:read_u16(0x8139DC)))
    reads = 0
  end
end)
emu.register_frame_done(function()
  rf = rf + 1
  if not play0 and rf > 520 and mt:read_u16(0x8139DC) > 0 then play0 = rf end
  if rf % 600 == 0 then log:flush() end
end)
emu.add_machine_stop_notifier(function() log:close() end)
