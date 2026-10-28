-- Write a pattern into the DDP2 cart's ARM shared RAM (0xD00000-0xD0FFFF) from the 68k side,
-- let the game run, and report any word that changed (i.e. the ARM firmware touches it).
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local n = 0
local out = io.open(os.getenv("ARMRAM_OUT") or "work/mame/armram.txt", "w")
local function pat(a) return (a * 0x9E37 + 0x1234) & 0xffff end
emu.register_frame_done(function()
  n = n + 1
  if n == 200 then
    for a = 0xD00000, 0xD0FFFE, 2 do mem:write_u16(a, pat(a)) end
    out:write("pattern written at frame 200\n"); out:flush()
  end
  if n == 200 + 1 or n == 1500 or n == 4000 then
    local bad, first = 0, nil
    for a = 0xD00000, 0xD0FFFE, 2 do
      if mem:read_u16(a) ~= pat(a) then bad = bad + 1; first = first or a end
    end
    out:write(string.format("frame %d: %d of 32768 words differ%s\n", n, bad, first and string.format(" (first %06x)", first) or "")); out:flush()
  end
  if n >= 4000 then out:close(); manager.machine:exit() end
end)
