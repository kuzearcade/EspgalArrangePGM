-- For candidate PCs (CAND_FILE: hex list), log up to 4 accesses to work RAM outside the enemy
-- records (stack excluded) with registers: "pc rw addr mask a0..a7 d0..d7" -> outside.txt.
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local cpu = manager.machine.devices[":maincpu"]
local cand = {}
for w in io.open(os.getenv("CAND_FILE")):read("*a"):gmatch("%x+") do cand[tonumber(w, 16)] = true end
local outdir = os.getenv("ESPGAL_OUTDIR") or "."
local lo, hi = 0x815680, 0x815680 + 0x24 * 120 - 1
local n_by, rows = {}, {}
local regs = {"A0","A1","A2","A3","A4","A5","A6","SP","D0","D1","D2","D3","D4","D5","D6","D7"}
local function chk(rw, offset, mask)
  local pc = cpu.state["CURPC"].value
  if not cand[pc] then return end
  local c = n_by[pc] or 0
  if c >= 4 then return end
  n_by[pc] = c + 1
  local r = {}
  for _, k in ipairs(regs) do r[#r + 1] = string.format("%x", cpu.state[k].value) end
  rows[#rows + 1] = string.format("%06x %s %06x %04x %s", pc, rw, offset, mask, table.concat(r, " "))
end
OT1 = mem:install_read_tap(0x800000, lo - 1, "o1r", function(o, d, m) chk("R", o, m) end)
OT2 = mem:install_write_tap(0x800000, lo - 1, "o1w", function(o, d, m) chk("W", o, m) end)
OT3 = mem:install_read_tap(hi + 1, 0x81efff, "o2r", function(o, d, m) chk("R", o, m) end)
OT4 = mem:install_write_tap(hi + 1, 0x81efff, "o2w", function(o, d, m) chk("W", o, m) end)
local n = 0
local endf = tonumber(os.getenv("ESPGAL_END") or "0")
emu.register_frame_done(function()
  n = n + 1
  if n % 2000 == 0 or n >= endf - 1 then
    local f = io.open(outdir .. "/outside.txt", "w")
    f:write(table.concat(rows, "\n") .. "\n")
    f:close()
  end
end)
