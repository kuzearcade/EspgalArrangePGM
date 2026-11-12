-- Movement test: Right for 60 frames from START, then shot held (laser) and Left; prints the
-- player's horizontal position (+4), speed (+0x1A), input, laser (+0x40), Kakusei and +0x66 every
-- 4 frames. Compared against PCSX2 Arrange in spec/arrange-characters.md.
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local ports = manager.machine.ioport.ports
local P1, START = 0x80EE96, 2400
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0},
            [START] = {"P1P2", "P1 Right", 1}, [START + 60] = {"P1P2", "P1 Right", 0},
            [START + 100] = {"P1P2", "P1 Button 1", 1}, [START + 160] = {"P1P2", "P1 Left", 1},
            [START + 220] = {"P1P2", "P1 Left", 0}, [START + 230] = {"P1P2", "P1 Button 1", 0}}
local n = 0
emu.register_frame_done(function()
  n = n + 1
  local e = ev[n]
  if e then ports[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if n >= 700 then mem:write_u8(P1 + 0x39, 0xff) end
  if (n >= START - 4 and n <= START + 68) or (n >= START + 156 and n <= START + 228) then
    if n % 4 == 0 then
      print(string.format("MV %d x=%04x spd=%d in=%02x laser=%d kak=%d f66=%d", n - START, mem:read_u16(P1 + 4), mem:read_u8(P1 + 0x1A), mem:read_u8(P1 + 0x18), mem:read_u16(P1 + 0x40), mem:read_u8(P1 + 0x5E), mem:read_u16(P1 + 0x66)))
    end
  end
  if n == START + 240 then manager.machine:exit() end
end)
