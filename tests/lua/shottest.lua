-- Shot test: after the start sequence, hold ESPGAL_SHOT_HOLD ("port:field", e.g.
-- "P1P2:P1 Button 3" = C, Chihiro's normal shot) for 160 frames, or without it tap the shot
-- (Button 1) for 2 of every 8 frames; print the number of active player-1 shots (records with a nonzero type word in
-- the pool at ESPGAL_POOL, 99 x 0x34 by default = the Arrange pool) every 4 frames, and the
-- first two shots' type, position and sprite fields at START+40.
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local ports = manager.machine.ioport.ports
local P1 = 0x80EE96
-- START: the frame the stage scroll (0x8139E0) reaches ESPGAL_SCROLL (default 12 = where the
-- PCSX2 reference test starts), after the start sequence
local SCROLL = tonumber(os.getenv("ESPGAL_SCROLL") or "12")
local START = 100000
local POOL = tonumber(os.getenv("ESPGAL_POOL") or "e0c800", 16)
local COUNT = tonumber(os.getenv("ESPGAL_POOL_COUNT") or "99")
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0}}
local hold = os.getenv("ESPGAL_SHOT_HOLD")
local hport, hfield
if hold then hport, hfield = hold:match("^(.-):(.*)$") end
local n = 0
local function active()
  local c = 0
  for k = 0, COUNT - 1 do if mem:read_u16(POOL + k * 0x34) ~= 0 then c = c + 1 end end
  return c
end
emu.register_frame_done(function()
  n = n + 1
  if START == 100000 and n > 1420 and mem:read_u16(0x8139E0) >= SCROLL then START = n end
  local e = ev[n]
  if e then ports[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if n >= 700 then mem:write_u8(P1 + 0x39, 0xff) end
  if n >= START and n <= START + 160 then
    if hold then ports[":" .. hport].fields[hfield]:set_value(n < START + 160 and 1 or 0)
    else ports[":P1P2"].fields["P1 Button 1"]:set_value((n < START + 160 and ((n - START) % 8) < 2) and 1 or 0) end
  end
  if n >= START and n <= START + 168 and n % 4 == 0 then
    print(string.format("SH %d active=%d laser=%d power=%d", n - START, active(), mem:read_u16(P1 + 0x40), mem:read_u16(P1 + 0x20)))
  end
  if n == START + 8 or n == START + 40 then
    local k, shown = 0, 0
    while k < COUNT and shown < 2 do
      local a = POOL + k * 0x34
      if mem:read_u16(a) ~= 0 then
        print(string.format("SO type=%04x x=%04x y=%04x spr=%08x f1e=%08x", mem:read_u16(a), mem:read_u16(a + 2),
          mem:read_u16(a + 4), mem:read_u32(a + 0xA), mem:read_u32(a + 0x1E)))
        shown = shown + 1
      end
      k = k + 1
    end
    manager.machine.video:snapshot()
  end
  if n == START + 170 then manager.machine:exit() end
end)
