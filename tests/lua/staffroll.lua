-- staffroll.lua (appended to an input script such as work/scratch/play.lua, or used as an
-- autoboot script of a run that reaches the ending): from the frame the ending's staff script
-- starts (0x81B614 = the built script copy, the immediate of `move.l #script,(a3)` at 0x187776)
-- log per frame to SROLL_OUT: offset, logic counter 0x801164 (a repeat = a lag frame), the forced
-- wait 0x8011A4, its flag 0x816764, the vblank countdown 0x8011C0, and the script state
-- 0x81B614 (line pointer), +4 (delay count), +6 (scroll speed), +8. SROLL_SAVE=1: save state "sr"
-- at the start (profile from it with tests/lua/proftrace.lua, PROF_WINDOWS from the offset).
-- Ends the run SROLL_FRAMES (1800) frames after the start.
do
  local mt = manager.machine.devices[":maincpu"].spaces["program"]
  local out = io.open(os.getenv("SROLL_OUT"), "w")
  local frames = tonumber(os.getenv("SROLL_FRAMES") or "1800")
  local script = mt:read_u32(0x187778)
  local f, f0 = 0, nil
  emu.register_frame_done(function()
    f = f + 1
    if not f0 then
      if mt:read_u32(0x81B614) == script then
        f0 = f
        out:write(string.format("start %d\n", f))
        out:flush()
        if os.getenv("SROLL_SAVE") then manager.machine:save("sr") end
      end
      return
    end
    local d = f - f0
    if d <= frames then
      out:write(string.format("%d %04x %04x %04x %04x %08x %04x %04x %04x\n", d, mt:read_u16(0x801164),
        mt:read_u16(0x8011A4), mt:read_u16(0x816764), mt:read_u16(0x8011C0), mt:read_u32(0x81B614),
        mt:read_u16(0x81B618), mt:read_u16(0x81B61A), mt:read_u16(0x81B61C)))
    elseif d == frames + 1 then
      out:close()
      manager.machine:exit()
    end
  end)
end
