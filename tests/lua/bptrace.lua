-- Register trace at chosen instructions via debugger breakpoints (run MAME with -debug -debugger
-- none -log). BP_FILE: hex addresses. Each breakpoint logs "T pc a0..a6 d0..d7" to error.log and
-- disables itself; all are re-armed every BP_REARM frames (default 500).
local cpu = manager.machine.devices[":maincpu"]
local dbg = cpu.debug
local rearm = tonumber(os.getenv("BP_REARM") or "500")
local n, expect = 0, 1
local function action(id)
  return string.format('logerror "T %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x %%x\\n",pc,a0,a1,a2,a3,a4,a5,a6,d0,d1,d2,d3,d4,d5,d6,d7; bpdisable %d; go', id)
end
for w in io.open(os.getenv("BP_FILE")):read("*a"):gmatch("%x+") do
  local a = tonumber(w, 16)
  local id = dbg:bpset(a, "", action(expect))
  if id ~= expect then
    -- ids are not always consecutive: recreate it with its own id
    dbg:bpclear(id)
    expect = id + 1
    id = dbg:bpset(a, "", action(expect))
  end
  if id == expect then n = n + 1 end
  expect = id + 1
end
print("breakpoints", n)
local f = 0
emu.register_frame_done(function()
  f = f + 1
  if f % rearm == 0 then dbg:bpenable() end
end)
