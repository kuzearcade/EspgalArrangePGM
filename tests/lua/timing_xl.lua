local mt = manager.machine.devices[":maincpu"].spaces["program"]
local pt = manager.machine.ioport.ports
local ft = 0
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0},
            [1400] = {"P1P2", "P1 Button 1", 1}, [1410] = {"P1P2", "P1 Button 1", 0}}
local out = io.open(os.getenv("TIMING_OUT"), "w")
-- CTR_TABLE / TG_TABLE (see shoot.lua): the game counter 0x801164 and the frame toggle 0x801166
-- follow the PS2's 0x35804C / 0x358048 per logic frame; the output keys on the logic frame
local function load(name, pat)
  if not os.getenv(name) then return nil end
  local t = {}
  for l in io.lines(os.getenv(name)) do local k, v = l:match(pat); t[tonumber(k, 16)] = tonumber(v, 16) end
  return t
end
local ctab, tgt = load("CTR_TABLE", "(%x+) (%x+)"), load("TG_TABLE", "(%x+) (%x+)")
local key, written, tglast = nil, nil, nil
-- PLAY_TABLE "play toggle counter" lines (stage 2 on, keyed on play frames 0x813A1E, which the
-- PS2's 0x642298 - 0x400A matches): from frame PLAY_FROM the toggle and counter follow the PS2's
local ptab, plast = nil, nil
local pfrom = tonumber(os.getenv("PLAY_FROM") or "18660")
if os.getenv("PLAY_TABLE") then
  ptab = {}
  for l in io.lines(os.getenv("PLAY_TABLE")) do
    local k, t, c = l:match("(%x+) (%d) (%x+)"); ptab[tonumber(k, 16)] = {tonumber(t), tonumber(c, 16)}
  end
end
emu.register_frame_done(function()
  ft = ft + 1
  local e = ev[ft]; if e then pt[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if ft >= 700 then mt:write_u8(0x80EE96 + 0x39, 0xff) end
  if ft == 1463 then mt:write_u16(0x801176, 0x0021) end
  if ctab and ft >= 1800 then
    local r = mt:read_u16(0x801164)
    if not key then key = r elseif r ~= written then key = key + 1 end
    local nxt = ctab[key + 1]
    if nxt then written = (nxt - 1) & 0xffff; mt:write_u16(0x801164, written) else written = r end
  end
  if tgt and ft >= 1800 then
    local r = ctab and key or mt:read_u16(0x801164)
    if r ~= tglast and tgt[r + 1] then mt:write_u16(0x801166, tgt[r + 1]) end
    tglast = r
  end
  if ptab and ft >= pfrom then
    local v = mt:read_u16(0x813A1E)
    if v ~= plast and ptab[v + 1] then
      mt:write_u16(0x801166, ptab[v + 1][1]); mt:write_u16(0x801164, (ptab[v + 1][2] - 1) & 0xffff)
    end
    plast = v
  end
  if ft >= 7200 then
    local t = {}
    for k = 0, 119 do local a = 0xE08200 + 0x2C * k; t[#t+1] = string.format("%04x%04x%04x", mt:read_u16(a), mt:read_u16(a+2), mt:read_u16(a+4)) end
    out:write(string.format("%d %04x %04x %04x %04x %08x %s\n", ft, mt:read_u16(0x8139E0), mt:read_u16(0x8139E2), mt:read_u16(0x813B1E), key or mt:read_u16(0x801164), mt:read_u16(0x813A1E), table.concat(t)))
  end
  if ft == 48250 then out:close(); manager.machine:exit() end
end)
