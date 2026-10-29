-- Enemy-record accesses with register context: up to 2 samples per (PC, record) of
-- "pc rw addr mask a0..a7 d0..d7" in $ESPGAL_OUTDIR/ea.txt (FT_BASE/FT_STRIDE/FT_COUNT as fieldtap).
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local cpu = manager.machine.devices[":maincpu"]
-- look state entries up once: per-access lookups allocate faster than Lua collects (MAME grew to 10+ GB)
local curpc = cpu.state["CURPC"]
local base = tonumber(os.getenv("FT_BASE") or "815680", 16)
local stride = tonumber(os.getenv("FT_STRIDE") or "24", 16)
local count = tonumber(os.getenv("FT_COUNT") or "121")
local outdir = os.getenv("ESPGAL_OUTDIR") or "."
local hi = base + stride * count - 1
local n_by = {}
local rows = {}
local regs = {}
for _, k in ipairs({"A0","A1","A2","A3","A4","A5","A6","SP","D0","D1","D2","D3","D4","D5","D6","D7"}) do
  regs[#regs + 1] = cpu.state[k]
end
local function note(rw, offset, mask)
  local pc = curpc.value
  local key = pc * 256 + (offset - base) // stride
  local c = n_by[key] or 0
  if c >= 2 then return end
  n_by[key] = c + 1
  local r = {}
  for _, e in ipairs(regs) do r[#r + 1] = string.format("%x", e.value) end
  rows[#rows + 1] = string.format("%06x %s %06x %04x %s", pc, rw, offset, mask, table.concat(r, " "))
end
EA_W = mem:install_write_tap(base, hi, "eaw", function(offset, data, mask) note("W", offset, mask) end)
EA_R = mem:install_read_tap(base, hi, "ear", function(offset, data, mask) note("R", offset, mask) end)
local n = 0
local endf = tonumber(os.getenv("ESPGAL_END") or "0")
emu.register_frame_done(function()
  n = n + 1
  if n % 60 == 0 then collectgarbage() end
  if n % 2000 == 0 or n >= endf - 1 then
    local f = io.open(outdir .. "/ea.txt", "w")
    f:write(table.concat(rows, "\n") .. "\n")
    f:close()
  end
end)
