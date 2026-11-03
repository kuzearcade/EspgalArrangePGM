-- proftrace.lua (EXTRA_LUA for timing_xl2.lua / shoot_k.lua, MAME -debug -debugger none):
-- instruction traces of the 68k over frame windows into PROF_DIR/tr_<name>.txt, every line
-- prefixed with the total cycle count (for cycle-weighted profiles), and
-- PROF_DIR/frames_<name>.txt: per frame its number, play 0x813A1E, logic counter 0x801164, wait
-- count 0x8011A4, 0x816764 and Kakusei 0x813A18.
-- Windows: PROF_WINDOWS "from:count,..." (MAME frames; name = from) and/or PROF_PLAYS
-- "stage:play:count,..." (play hex, from the first frame the stage 0x81399E has that play; name =
-- s<stage>_<play>), so a window stays on the same game moment when lag changes.
local dbg = manager.machine.debugger
local cpu = manager.machine.devices[":maincpu"]
local mt2 = cpu.spaces["program"]
local dir = os.getenv("PROF_DIR")
local wins, plays = {}, {}
for f, n in (os.getenv("PROF_WINDOWS") or ""):gmatch("(%d+):(%d+)") do wins[#wins + 1] = {tonumber(f), tonumber(n)} end
for s, p, n in (os.getenv("PROF_PLAYS") or ""):gmatch("(%d+):(%x+):(%d+)") do
  plays[#plays + 1] = {tonumber(s), tonumber(p, 16), tonumber(n), false}
end
local fc = tonumber(os.getenv("FT0") or "0")
local cur_end, flog = nil, nil
local function start(name, count)
  cur_end = fc + count
  flog = io.open(string.format("%s/frames_%s.txt", dir, name), "w")
  dbg:command(string.format('trace %s/tr_%s.txt,maincpu,noloop,{tracelog "%%x ",totalcycles}', dir, name))
end
emu.register_frame_done(function()
  fc = fc + 1
  if cur_end and fc == cur_end then
    dbg:command("trace off,maincpu")
    flog:close(); cur_end, flog = nil, nil
  end
  if flog then
    flog:write(string.format("%d %04x %04x %04x %04x %04x\n", fc, mt2:read_u16(0x813A1E), mt2:read_u16(0x801164),
      mt2:read_u16(0x8011A4), mt2:read_u16(0x816764), mt2:read_u16(0x813A18)))
    dbg:command('tracelog "FRAME %d\n",' .. string.format("%x", fc))   -- (debugger numbers are hex)
  end
  if not cur_end then
    for _, w in ipairs(wins) do
      if fc == w[1] then start(tostring(w[1]), w[2]) end
    end
    local st, pl = mt2:read_u16(0x81399E), mt2:read_u16(0x813A1E)
    for _, w in ipairs(plays) do
      if not w[4] and st == w[1] and pl >= w[2] and pl < w[2] + 16 then
        w[4] = true
        start(string.format("s%d_%x", w[1], w[2]), w[3])
        break
      end
    end
  end
end)
