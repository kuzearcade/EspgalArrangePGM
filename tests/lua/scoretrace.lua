-- Score trace for comparison with a PCSX2 run (work/scratch/ps2score.sh, ESPGAL_WATCH of the
-- score records, Arrange's score words and the P1 record). Start sequence and START as in
-- shottrace.lua (the frame the stage scroll reaches 12 = PCSX2 frame 3600). Environment:
--   ESPGAL_CHAR=2       pick the second character
--   ESPGAL_EVENTS       "rel,field,value;..." P1P2 inputs relative to START
--   ESPGAL_SCORE_VARS   hex address of the Arrange score words (layout.SCORE_VARS)
--   ESPGAL_LAST         last frame relative to START (default 2400)
--   ESPGAL_SNAPS        "rel,..." frames to save a snapshot
-- Output per frame: "SC <rel> <score long> <score words hex> <+0x5F> <+0x66> <+0x6E> <+0x40>".
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local ports = manager.machine.ioport.ports
local P1 = 0x80EE96
local VARS = tonumber(os.getenv("ESPGAL_SCORE_VARS") or "e0b6ec", 16)
local LAST = tonumber(os.getenv("ESPGAL_LAST") or "2400")
local START = 100000
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0},
            [1400] = {"P1P2", "P1 Button 1", 1}, [1410] = {"P1P2", "P1 Button 1", 0}}
if os.getenv("ESPGAL_CHAR") == "2" then
  ev[440] = {"P1P2", "P1 Right", 1}; ev[450] = {"P1P2", "P1 Right", 0}
end
local rel = {}
for r, f, v in (os.getenv("ESPGAL_EVENTS") or ""):gmatch("(%-?%d+),([^,;]+),(%d)") do
  rel[tonumber(r)] = rel[tonumber(r)] or {}
  table.insert(rel[tonumber(r)], {f, tonumber(v)})
end
local snaps = {}
for x in (os.getenv("ESPGAL_SNAPS") or ""):gmatch("%-?%d+") do snaps[tonumber(x)] = true end
local n = 0
emu.register_frame_done(function()
  n = n + 1
  if START == 100000 and n > 1420 and mem:read_u16(0x8139E0) >= 12 then START = n end
  local e = ev[n]
  if e then ports[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if n >= 700 then mem:write_u8(P1 + 0x39, 0xff) end
  if START == 100000 then return end
  local r = n - START
  for _, x in ipairs(rel[r] or {}) do ports[":P1P2"].fields[x[1]]:set_value(x[2]) end
  local w = {}
  for k = 0, 0x13 do w[#w + 1] = string.format("%02x", mem:read_u8(VARS + k)) end
  print(string.format("SC %d %d %s %02x %04x %04x %04x", r, mem:read_u32(0x81B57E), table.concat(w),
    mem:read_u8(P1 + 0x5F), mem:read_u16(P1 + 0x66), mem:read_u16(P1 + 0x6E), mem:read_u16(P1 + 0x40)))
  if snaps[r] then manager.machine.video:snapshot() end
  if r == LAST then manager.machine:exit() end
end)
