-- splist.lua (EXTRA_LUA for timing_xl2.lua): a hash of the finished sprite lists (main list
-- 0x800000, 256 entries; continuation list 0xD0C840 up to its terminator) each time the builder
-- turns the sprite DMA back on (write to 0xB0E000 with bit 0 set), at every SPL_EVERY-th play
-- frame (first build per stage and play frame), into SPL_OUT: "stage play main ext hash".
local cpu = manager.machine.devices[":maincpu"]
local sp = cpu.spaces["program"]
local every = tonumber(os.getenv("SPL_EVERY") or "8")
local out = io.open(os.getenv("SPL_OUT"), "w")
local seen = {}
local function list_hash(base, max)
  local h, n = 0, 0
  for k = 0, max - 1 do
    local a = base + 10 * k
    local w0, w1, w2, w3, w4 = sp:read_u16(a), sp:read_u16(a + 2), sp:read_u16(a + 4), sp:read_u16(a + 6), sp:read_u16(a + 8)
    if w0 == 0 and w1 == 0 and w2 == 0 and w3 == 0 and w4 == 0 then break end
    h = (h * 31 + w0) % 4294967296; h = (h * 31 + w1) % 4294967296; h = (h * 31 + w2) % 4294967296
    h = (h * 31 + w3) % 4294967296; h = (h * 31 + w4) % 4294967296
    n = n + 1
  end
  return h, n
end
splist_tap = sp:install_write_tap(   -- (global: a collected tap is removed)
0xb0e000, 0xb0e001, "splist", function(offset, data, mask)
  if data & 1 == 1 then
    local st, play = sp:read_u16(0x81399E), sp:read_u16(0x813A1E)
    local key = st * 65536 + play
    if play % every == 0 and not seen[key] then
      seen[key] = true
      local h1, n1 = list_hash(0x800000, 256)
      local h2, n2 = list_hash(0xd0c840, 1401)
      out:write(string.format("%d %x %d %d %08x\n", st, play, n1, n2, (h1 * 7 + h2) % 4294967296))
      out:flush()
    end
  end
  return data
end)
