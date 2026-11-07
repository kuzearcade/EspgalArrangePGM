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
local function first_key(t) local k = nil if t then for x in pairs(t) do if not k or x < k then k = x end end end return k end
local ctab_first, tg_first = first_key(ctab), first_key(tgt)
-- The replay's key starts at stage 1's first play frame as the play counter 0x813A1E +
-- KEY_PLAY_OFF (default 0x333, the PS2's logic-frame key there), then steps with each logic
-- frame; until the PS2 tables start (key 0x19D3 / 0x485) the counter, its mod-24 copy and the
-- toggle are replayed as the identity (counter = key, toggle = key & 1). Both used to come from
-- MAME's own counter 0x801164, which also counts boot and select steps, so a lag frame fewer
-- before the stage (the sprite builder speed-up, an overclock) shifted the whole replay a step.
local key_play_off = tonumber(os.getenv("KEY_PLAY_OFF") or "0x333")
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
  -- the PS2's RNG at the game start (0x21), set through stage 1's first play step (play 1), before
  -- the step's draws: it used to be set at MAME frame 1463, which is play 2 when the boot lags less
  if ft < 3000 and mt:read_u16(0x81399E) == 0 and mt:read_u16(0x813A1E) == 1 then mt:write_u16(0x801176, 0x0021) end
  if ctab and ft >= 1400 then
    local r = mt:read_u16(0x801164)
    if not key then
      local pl = mt:read_u16(0x813A1E)
      if pl > 0 and mt:read_u16(0x81399E) == 0 then key = pl + key_play_off end
    elseif r ~= written then key = key + 1 end
    if key then
      local nxt = ctab[key + 1]
      -- before the PS2 table starts: the identity (counter = key), as a boot without lag leaves it
      if not nxt and key < ctab_first then nxt = key + 1 end
      if nxt then written = (nxt - 1) & 0xffff; mt:write_u16(0x801164, written); mt:write_u16(0x801170, written % 24) else written = r end
    end
  end
  if tgt and (key or not ctab) and ft >= 1400 then
    local r = ctab and key or mt:read_u16(0x801164)
    local v = tgt[r + 1]
    if not v and ctab and r < tg_first then v = r & 1 end   -- before the PS2 table: toggle = key & 1
    if r ~= tglast and v then mt:write_u16(0x801166, v) end
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
-- DUMP_KEYS "k,..." (hex replay keys, = PS2 0x642298 + 0x3E5 in stage 1): dump work RAM, the
-- enhanced RAM and the bullet pool on the first frame with that key (DUMP_DIR/k_<key>.bin);
-- DUMP_LAST=1: on the last frame with that key instead (the finished logic step, as in
-- timing_xl2.lua's DUMP_PLAYS: when the 68k lags the first frame can be mid-step)
if os.getenv("DUMP_KEYS") then
  local want, done, pend = {}, {}, {}
  for k in os.getenv("DUMP_KEYS"):gmatch("(%x+)") do want[#want+1] = tonumber(k, 16) end
  local last = os.getenv("DUMP_LAST") == "1"
  local function snap()
    local t = {}
    for a = 0x800000, 0x81ffff do t[#t+1] = string.char(mt:read_u8(a)) end
    for a = 0xe00000, 0xe0ffff do t[#t+1] = string.char(mt:read_u8(a)) end
    for a = 0xd00000, 0xd0c7ff do t[#t+1] = string.char(mt:read_u8(a)) end
    return table.concat(t)
  end
  local function write(k, data)
    local d = io.open(string.format("%s/k_%x.bin", os.getenv("DUMP_DIR"), k), "wb")
    d:write(data) d:close()
  end
  emu.register_frame_done(function()
    for i, k in ipairs(want) do
      if not done[i] then
        if key == k then
          if last then pend[i] = snap() else done[i] = true; write(k, snap()) end
        elseif pend[i] then
          done[i] = true; write(k, pend[i]); pend[i] = nil; collectgarbage()
        end
      end
    end
  end)
end
if os.getenv("EXTRA_LUA") then dofile(os.getenv("EXTRA_LUA")) end
