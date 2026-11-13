-- bursttap.lua (EXTRA_LUA of shoot.lua): checks the allocator bursts (espgalarr/pgm/allocburst.py)
-- over a whole replay. Every store of the scan cursor (0x818358) by a head or next entry must leave
-- every slot below it occupied (status word non-zero): a next call trusts exactly that.
--   BURST_LOG: one line per violation ("ft cursor slot pc", the first 50) and a running
--   "stores N, violations V" every 600 frames (the last line is the total: the runs end without
--   MAME's stop notifier).
-- The tap is kept in a global (MAME collects locals).
local cpu = manager.machine.devices[":maincpu"]
local mt = cpu.spaces["program"]
local cpc = cpu.state["CURPC"]
local POOL, CURSOR = 0xD00000, 0x818358
local log = io.open(os.getenv("BURST_LOG"), "w")
local hi, stores, bad, rf = 0, 0, 0, 0
bursttap_w = mt:install_write_tap(CURSOR, CURSOR + 3, "burst_w", function(offset, data, mask)
  if offset == CURSOR then hi = data; return end
  local v = (hi << 16) | data
  stores = stores + 1
  for a = POOL, v - 0x40, 0x40 do
    if mt:read_u16(a) == 0 then
      bad = bad + 1
      if bad <= 50 then
        log:write(string.format("%d %x %d %x\n", rf, v, (a - POOL) // 0x40, cpc.value))
      end
      break
    end
  end
end)
emu.register_frame_done(function()
  rf = rf + 1
  if rf % 600 == 0 then
    log:write(string.format("stores %d, violations %d\n", stores, bad))
    log:flush()
  end
end)
emu.add_machine_stop_notifier(function()
  log:write(string.format("stores %d, violations %d\n", stores, bad))
  log:close()
end)
