-- Dump the IGS023 sprite list and take a snapshot after N frames (headless helper).
local frames = tonumber(os.getenv("ESPGAL_FRAMES") or "900")
local out = os.getenv("ESPGAL_OUT") or "work/mame/sprites.txt"
local n = 0
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local done = false
emu.register_frame_done(function()
  n = n + 1
  if n == frames and not done then
    done = true
    local f = io.open(out, "w")
    for i = 0, 255 do
      local base = 0xB00000 + i * 10
      local w0 = mem:read_u16(base); local w1 = mem:read_u16(base + 2)
      local w2 = mem:read_u16(base + 4); local w3 = mem:read_u16(base + 6)
      local w4 = mem:read_u16(base + 8)
      if (w4 & 0x7fff) == 0 then break end
      f:write(string.format("%04x %04x %04x %04x %04x\n", w0, w1, w2, w3, w4))
    end
    f:close()
    manager.machine.video:snapshot()
    manager.machine:exit()
  end
end)
