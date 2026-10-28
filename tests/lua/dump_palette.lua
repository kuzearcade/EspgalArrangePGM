-- Dump palette RAM (0xA00000-0xA013FF) after N frames.
local frames = tonumber(os.getenv("ESPGAL_FRAMES") or "900")
local out = os.getenv("ESPGAL_OUT") or "work/mame/palette.bin"
local n = 0
local mem = manager.machine.devices[":maincpu"].spaces["program"]
emu.register_frame_done(function()
  n = n + 1
  if n == frames then
    local f = io.open(out, "wb")
    for a = 0xA00000, 0xA013FE, 2 do
      local v = mem:read_u16(a)
      f:write(string.char(v >> 8, v & 0xff))
    end
    f:close()
    manager.machine:exit()
  end
end)
