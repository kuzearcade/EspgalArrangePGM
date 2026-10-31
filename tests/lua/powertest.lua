-- Power Shot trace: after the start sequence, hold the shot (Button 1) for ESPGAL_HOLD frames
-- (default 100) from the frame the stage scroll (0x8139E0) reaches ESPGAL_SCROLL (default 12),
-- and every 2 frames print P1's laser flag and the Power Shot records (pool records 35.. of
-- the Arrange pool at ESPGAL_POOL) and the number of live trail records (ESPGAL_TRAIL, 64 x
-- 0x22). Output lines: "PW <frame> laser=<+0x40> n=<records> trails=<n>" then
-- "PR <slot> type x y a1a a1b i26 i2a spr" per record (all hex).
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local ports = manager.machine.ioport.ports
local P1 = 0x80EE96
local SCROLL = tonumber(os.getenv("ESPGAL_SCROLL") or "12")
local HOLD = tonumber(os.getenv("ESPGAL_HOLD") or "100")
local POOL = tonumber(os.getenv("ESPGAL_POOL") or "e0c800", 16)
local TRAIL = tonumber(os.getenv("ESPGAL_TRAIL") or "e0b700", 16)
local START = 100000
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0},
            [1400] = {"P1P2", "P1 Button 1", 1}, [1410] = {"P1P2", "P1 Button 1", 0}}
local n = 0
emu.register_frame_done(function()
  n = n + 1
  if START == 100000 and n > 1420 and mem:read_u16(0x8139E0) >= SCROLL then START = n end
  local e = ev[n]
  if e then ports[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if n >= 700 then mem:write_u8(P1 + 0x39, 0xff) end
  if n >= START and n <= START + HOLD then
    ports[":P1P2"].fields["P1 Button 1"]:set_value(n < START + HOLD and 1 or 0)
  end
  if n >= START and n <= START + HOLD + 40 and (n - START) % 2 == 0 then
    local recs, trails = {}, 0
    for k = 35, 97 do
      local a = POOL + k * 0x34
      if mem:read_u16(a) ~= 0 then recs[#recs + 1] = {k, a} end
    end
    for k = 0, 63 do if mem:read_u16(TRAIL + k * 0x22) ~= 0 then trails = trails + 1 end end
    print(string.format("PW %d laser=%x n=%d trails=%d p99=%x", n - START, mem:read_u16(P1 + 0x40), #recs, trails,
      mem:read_u8(P1 + 0x99)))
    for i = 1, math.min(#recs, 6) do
      local k, a = recs[i][1], recs[i][2]
      print(string.format("PR %d %04x %04x %04x %02x %02x %04x %04x %08x", k, mem:read_u16(a), mem:read_u16(a + 2),
        mem:read_u16(a + 4), mem:read_u8(a + 0x1A), mem:read_u8(a + 0x1B), mem:read_u16(a + 0x26),
        mem:read_u16(a + 0x2A), mem:read_u32(a + 0xA)))
    end
  end
  if n == START + 30 or n == START + 60 then manager.machine.video:snapshot() end
  if n == START + HOLD + 42 then manager.machine:exit() end
end)
