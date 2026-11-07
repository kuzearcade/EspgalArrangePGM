-- EXTRA_LUA of shoot.lua: while the play timer 0x8139DC is one of POOL_TIMERS (hex, comma separated),
-- write the enemy bullet pool (0xD00000, 800 records of 0x40, as 16-bit words) to POOL_DIR/t_<timer>.bin
-- every frame, so the file holds the key's last frame (its finished logic step); POOL_TASKS=1 also
-- writes the task slots (0x812D8C, 64 x 0x40) to t_<timer>.tsk
local mp = manager.machine.devices[":maincpu"].spaces["program"]
local ts = {}
for x in (os.getenv("POOL_TIMERS") or ""):gmatch("%x+") do ts[tonumber(x, 16)] = true end
local dir = os.getenv("POOL_DIR")
local tasks = os.getenv("POOL_TASKS") == "1"
local function dump(name, lo, n)
  local parts = {}
  for a = lo, lo + n - 2, 2 do
    local v = mp:read_u16(a)
    parts[#parts + 1] = string.char(v >> 8, v & 0xff)
  end
  local f = io.open(name, "wb"); f:write(table.concat(parts)); f:close()
end
emu.register_frame_done(function()
  local t = mp:read_u16(0x8139DC)
  if ts[t] then
    dump(string.format("%s/t_%04x.bin", dir, t), 0xD00000, 800 * 0x40)
    if tasks then dump(string.format("%s/t_%04x.tsk", dir, t), 0x812D8C, 64 * 0x40) end
  end
end)
