-- Per-instruction field offsets of enemy-record accesses (records = FT_COUNT x FT_STRIDE at FT_BASE).
-- Writes "pc field rw count" lines to $ESPGAL_OUTDIR/fields.txt.
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local cpu = manager.machine.devices[":maincpu"]
local base = tonumber(os.getenv("FT_BASE") or "815680", 16)
local stride = tonumber(os.getenv("FT_STRIDE") or "24", 16)
local count = tonumber(os.getenv("FT_COUNT") or "120")
local outdir = os.getenv("ESPGAL_OUTDIR") or "."
local hi = base + stride * count - 1
local pcst = cpu.state["PC"]
local seen = {}
local function note(rw, offset, mask)
  local pc = pcst.value
  local f = (offset - base) % stride
  -- byte accesses on a 16-bit bus: mask tells which byte
  if mask == 0x00ff then f = f + 1 end
  local key = pc * 256 + f * 2 + rw
  seen[key] = (seen[key] or 0) + 1
end
local function wr(offset, data, mask) note(1, offset, mask) end
local function rd(offset, data, mask) note(0, offset, mask) end
FT_W = mem:install_write_tap(base, hi, "ftw", wr)
FT_R = mem:install_read_tap(base, hi, "ftr", rd)
local n = 0
local endf = tonumber(os.getenv("ESPGAL_END") or "0")
emu.register_frame_done(function()
  n = n + 1
  if n % 60 == 0 then collectgarbage() end
  if n % 2000 == 0 or n >= endf - 1 then
    local f = io.open(outdir .. "/fields.txt", "w")
    for k, c in pairs(seen) do
      local pc = k // 256
      local r = k % 256
      f:write(string.format("%06x %d %s %d\n", pc, r // 2, (r % 2 == 1) and "W" or "R", c))
    end
    f:close()
  end
end)
