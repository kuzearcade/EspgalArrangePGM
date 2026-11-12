-- Shot trace on a schedule, for comparison with a PCSX2 run of the same inputs.
-- START = the frame the stage scroll (0x8139E0) reaches ESPGAL_SCROLL (default 12) after the
-- start sequence (PCSX2 probe frame 3600). Environment:
--   ESPGAL_CHAR=2          pick the second character (Right on the select screen)
--   ESPGAL_EVENTS          "rel,field,value;..." P1P2 inputs relative to START
--   ESPGAL_GEMS_FROM=rel   gems 299 / gauge 0x418 written every frame from START+rel
--   ESPGAL_TRACE           "from-to;..." frame windows relative to START, printed every ESPGAL_EVERY (2) frames
--   ESPGAL_RECORDS         "first-last" P1 pool records (default 0-97)
--   ESPGAL_POOL            pool base (default the Arrange pool 0xE0C800)
--   ESPGAL_SNAPS           "rel,..." frames to save a snapshot
-- Output: "TR <rel> kak=<+0x66> laser=<+0x40> n=<records>" and "TS <slot> type x y a1a a1b i26 i2a spr".
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local ports = manager.machine.ioport.ports
local P1 = 0x80EE96
local SCROLL = tonumber(os.getenv("ESPGAL_SCROLL") or "12")
local POOL = tonumber(os.getenv("ESPGAL_POOL") or "e0c800", 16)
local FIRST, LAST = (os.getenv("ESPGAL_RECORDS") or "0-97"):match("^(%d+)-(%d+)$")
FIRST, LAST = tonumber(FIRST), tonumber(LAST)
local EVERY = tonumber(os.getenv("ESPGAL_EVERY") or "2")
local GEMS_FROM =tonumber(os.getenv("ESPGAL_GEMS_FROM") or "100000")
local START = 100000
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0}}
if os.getenv("ESPGAL_CHAR") == "2" then
  ev[440] = {"P1P2", "P1 Right", 1}; ev[450] = {"P1P2", "P1 Right", 0}
end
local rel = {}
for r, f, v in (os.getenv("ESPGAL_EVENTS") or ""):gmatch("(%-?%d+),([^,;]+),(%d)") do
  rel[tonumber(r)] = rel[tonumber(r)] or {}
  table.insert(rel[tonumber(r)], {f, tonumber(v)})
end
local windows, last = {}, 0
for a, b in (os.getenv("ESPGAL_TRACE") or "0-100"):gmatch("(%d+)-(%d+)") do
  windows[#windows + 1] = {tonumber(a), tonumber(b)}
  if tonumber(b) > last then last = tonumber(b) end
end
local snaps = {}
for x in (os.getenv("ESPGAL_SNAPS") or ""):gmatch("%-?%d+") do snaps[tonumber(x)] = true end
local n = 0
emu.register_frame_done(function()
  n = n + 1
  if START == 100000 and n > 1420 and mem:read_u16(0x8139E0) >= SCROLL then START = n end
  local e = ev[n]
  if e then ports[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if n >= 700 then mem:write_u8(P1 + 0x39, 0xff) end
  if START == 100000 then return end
  local r = n - START
  if r >= GEMS_FROM then mem:write_u16(P1 + 0x6E, 299); mem:write_u16(P1 + 0x24, 0x418) end
  for _, x in ipairs(rel[r] or {}) do ports[":P1P2"].fields[x[1]]:set_value(x[2]) end
  local show = false
  for _, w in ipairs(windows) do if r >= w[1] and r <= w[2] and r % EVERY == 0 then show = true end end
  if show then
    local recs = {}
    for k = FIRST, LAST do
      local a = POOL + k * 0x34
      if mem:read_u16(a) ~= 0 then recs[#recs + 1] = {k, a} end
    end
    print(string.format("TR %d kak=%x laser=%x n=%d", r, mem:read_u16(P1 + 0x66), mem:read_u16(P1 + 0x40), #recs))
    for i = 1, math.min(#recs, 6) do
      local k, a = recs[i][1], recs[i][2]
      print(string.format("TS %d %04x %04x %04x %02x %02x %04x %04x %08x", k, mem:read_u16(a), mem:read_u16(a + 2),
        mem:read_u16(a + 4), mem:read_u8(a + 0x1A), mem:read_u8(a + 0x1B), mem:read_u16(a + 0x26),
        mem:read_u16(a + 0x2A), mem:read_u32(a + 0xA)))
    end
  end
  if snaps[r] then manager.machine.video:snapshot() end
  if r == last + 2 then manager.machine:exit() end
end)
