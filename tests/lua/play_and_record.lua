-- Coin up, start a game, hold fire, exit after N frames (used with -wavwrite / snapshots).
local frames = tonumber(os.getenv("ESPGAL_FRAMES") or "1800")
local snap_every = tonumber(os.getenv("ESPGAL_SNAP") or "0")
local n = 0
local ioport = manager.machine.ioport
local function field(port_sub, name_sub)
  for pname, port in pairs(ioport.ports) do
    if string.find(pname, port_sub, 1, true) then
      for fname, f in pairs(port.fields) do
        if string.find(fname, name_sub, 1, true) then return f end
      end
    end
  end
  return nil
end
local coin = field("Service", "Coin 1")
local start = field("P1P2", "1 Player Start") or field("P1P2", "Start")
local btn1 = field("P1P2", "P1 Button 1")
emu.register_frame_done(function()
  n = n + 1
  if n == 120 then coin:set_value(1) end
  if n == 130 then coin:set_value(0) end
  if n == 300 then start:set_value(1) end
  if n == 330 then start:set_value(0) end
  if n > 400 and btn1 then btn1:set_value((n // 4) % 2) end
  if snap_every > 0 and n % snap_every == 0 then manager.machine.video:snapshot() end
  if n >= frames then manager.machine:exit() end
end)
