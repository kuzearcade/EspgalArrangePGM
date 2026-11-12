local mt = manager.machine.devices[":maincpu"].spaces["program"]
local pt = manager.machine.ioport.ports
local ft = tonumber(os.getenv("FT0") or "0")
local rf, play0 = ft, nil
local ctr_set = false
-- SAVE_AT: save state "s6" at that frame (load it with -state s6 and FT0 = SAVE_AT)
local save_at = tonumber(os.getenv("SAVE_AT") or "-1")
local ev = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
            [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
            [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0}}
local out = io.open(os.getenv("TIMING_OUT"), "w")
-- W_ADDR / W_FROM / W_TO / WLOG: log writes to a word (pc, value, play counter, stack) and exit
local wlog = os.getenv("WLOG") and io.open(os.getenv("WLOG"), "w")
local curpc = manager.machine.devices[":maincpu"].state["CURPC"]
local sp = manager.machine.devices[":maincpu"].state["SP"]
local a6r = manager.machine.devices[":maincpu"].state[os.getenv("W_REG") or "A6"]
local wfrom, wto = tonumber(os.getenv("W_FROM") or "-1"), tonumber(os.getenv("W_TO") or "-1")
local function onw(offset, data, mask)
  local a = sp.value
  wlog:write(string.format("%d %06x %06x %04x %04x play %04x a6 %06x st %08x %08x %08x %08x\n", ft, curpc.value, offset, data, mask,
    mt:read_u16(0x813A1E), a6r.value, mt:read_u32(a), mt:read_u32(a+4), mt:read_u32(a+8), mt:read_u32(a+12)))
  return data
end
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
-- the mod-24 frame counter 0x801170 (PS2 0x358034, read by Arrange's type 0x3E run) steps with
-- the game counter from their common clear: the written counter, unwrapped (65536 frames = 16 mod
-- 24), mod 24 (the tables' counter is the PS2's + 0x23F, the value the PS2's logic sees)
local glast, gwraps = nil, 0
-- STAGES "from:table:rng;..." (or PLAY_TABLE / PLAY_FROM / STAGE_RNG for one stage): from MAME
-- frame `from` (the stage's first frame) the toggle and counter follow the PS2's per play frame
-- (table lines "play toggle counter", play = 0x813A1E), and the RNG index is set to `rng` (the
-- PS2's at the stage's first play frame) while the play counter is still 0
local stages = {}
local function load_table(name)
  local t = {}
  for l in io.lines(name) do
    local k, g, c = l:match("(%x+) (%d) (%x+)"); t[tonumber(k, 16)] = {tonumber(g), tonumber(c, 16)}
  end
  return t
end
if os.getenv("STAGES") then
  -- (an entry "S<n>:table:rng" applies while the stage number 0x81399E is n)
  for f, n, r in os.getenv("STAGES"):gmatch("(S?%d+):([^:;]+):([^;]*)") do
    if f:sub(1, 1) == "S" then
      stages[#stages + 1] = {stage = tonumber(f:sub(2)), tab = load_table(n), rng = tonumber(r)}
    else
      stages[#stages + 1] = {from = tonumber(f), tab = load_table(n), rng = tonumber(r)}
    end
  end
elseif os.getenv("PLAY_TABLE") then
  stages[1] = {from = tonumber(os.getenv("PLAY_FROM") or "18660"), tab = load_table(os.getenv("PLAY_TABLE")),
               rng = os.getenv("STAGE_RNG") and tonumber(os.getenv("STAGE_RNG"))}
end
local plast = nil
local end_stage = os.getenv("END_STAGE") and tonumber(os.getenv("END_STAGE"))
local end_armed, end_prev = false, nil
emu.register_frame_done(function()
  rf = rf + 1
  -- frames count as before the select-to-stage change: from the frame stage 1's play starts (the
  -- play timer 0x8139DC leaves 0) ft resumes at 1461, where play used to start (the white
  -- screen and the how-to-play demo took that long), so every frame constant below keeps its
  -- meaning in the game
  if not play0 and rf > 520 and mt:read_u16(0x8139DC) > 0 then play0 = rf end
  ft = play0 and rf + 1461 - play0 or rf
  if ft == save_at then manager.machine:save("s6") end
  local e = ev[ft]; if e then pt[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  if ft >= 700 then mt:write_u8(0x80EE96 + 0x39, 0xff) end
  -- the PS2's RNG at the game start (0x21), set through stage 1's first play step (play 1), before
  -- the step's draws: it used to be set at MAME frame 1463, which is play 2 when the boot lags less
  if ft < 3000 and mt:read_u16(0x81399E) == 0 and mt:read_u16(0x813A1E) == 1 then mt:write_u16(0x801176, 0x0021) end
  -- and the game counter the old select-to-stage flow left there (CTR_START, 0x335: the how-to-play
  -- set-up cleared it, 0x112684), which the replays were aligned on until the PS2 tables take over;
  -- the select screen's count runs on now, as the PS2's does
  if ft < 3000 and mt:read_u16(0x81399E) == 0 and mt:read_u16(0x813A1E) == 1 and not ctr_set then
    local c = tonumber(os.getenv("CTR_START") or "0335", 16)
    mt:write_u16(0x801164, c); mt:write_u16(0x801170, c % 24); ctr_set = true
  end
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
  if wlog and ft == wfrom then wtap = mt:install_write_tap(tonumber(os.getenv("W_ADDR")), tonumber(os.getenv("W_ADDR")) + tonumber(os.getenv("W_LEN") or "2") - 1, "w", onw) end
  if wlog and ft == wto then wlog:close(); manager.machine:exit() end
  if os.getenv("DUMPS") and string.find("," .. os.getenv("DUMPS") .. ",", "," .. ft .. ",", 1, true) then
    local d = io.open(string.format("%s/d_%d.bin", os.getenv("DUMP_DIR"), ft), "wb")
    local t = {} for a = 0x800000, 0x81ffff do t[#t+1] = string.char(mt:read_u8(a)) end
    for a = 0xe00000, 0xe0ffff do t[#t+1] = string.char(mt:read_u8(a)) end
    for a = 0xd00000, 0xd0c7ff do t[#t+1] = string.char(mt:read_u8(a)) end
    d:write(table.concat(t)) d:close()
  end
  if os.getenv("REC_LOG") and ft >= tonumber(os.getenv("REC_FROM")) and ft <= tonumber(os.getenv("REC_TO")) then
    if not reclog then reclog = io.open(os.getenv("REC_LOG"), "w") end
    local a = 0xE08200 + 0x2C * tonumber(os.getenv("REC_N"))
    local t = {} for i = 0, 0x2a, 2 do t[#t+1] = string.format("%04x", mt:read_u16(a + i)) end
    reclog:write(string.format("%d %04x %s\n", ft, mt:read_u16(0x813A1E), table.concat(t)))
    if ft == tonumber(os.getenv("REC_TO")) then reclog:close(); manager.machine:exit() end
  end
  if os.getenv("MEM_LOG") and ft >= tonumber(os.getenv("MEM_FROM")) and ft <= tonumber(os.getenv("MEM_TO")) then
    if not memlog then memlog = io.open(os.getenv("MEM_LOG"), "w") end
    local a0, n = tonumber(os.getenv("MEM_ADDR")), tonumber(os.getenv("MEM_LEN"))
    local t = {} for a = a0, a0 + n - 1, 2 do t[#t+1] = string.format("%04x", mt:read_u16(a)) end
    memlog:write(string.format("%d %04x %s\n", ft, mt:read_u16(0x813A1E), table.concat(t)))
    if ft == tonumber(os.getenv("MEM_TO")) then memlog:close(); manager.machine:exit() end
  end
  local st = nil
  local sn = mt:read_u16(0x81399E)
  for _, x in ipairs(stages) do
    if (x.from and ft >= x.from) or (x.stage and ft >= 18000 and sn == x.stage) then st = x end
  end
  if st then
    local v = mt:read_u16(0x813A1E)
    local e = st.tab[v + 1]
    if v ~= plast and e then
      mt:write_u16(0x801166, e[1]); mt:write_u16(0x801164, (e[2] - 1) & 0xffff)
      if glast and e[2] < glast - 0x8000 then gwraps = gwraps + 1 end
      glast = e[2]; mt:write_u16(0x801170, (e[2] - 1 + 16 * gwraps) % 24)
    end
    plast = v
    if st.rng and v == 0 then mt:write_u16(0x801176, st.rng) end
  end
  if ft >= 7200 then
    local t = {}
    for k = 0, 119 do local a = 0xE08200 + 0x2C * k; t[#t+1] = string.format("%04x%04x%04x", mt:read_u16(a), mt:read_u16(a+2), mt:read_u16(a+4)) end
    out:write(string.format("%d %04x %04x %04x %04x %08x %s %04x\n", ft, mt:read_u16(0x8139E0), mt:read_u16(0x8139E2), mt:read_u16(0x813B1E), key or mt:read_u16(0x801164), mt:read_u16(0x813A1E), table.concat(t), mt:read_u16(0x801176)))
  end
  -- END_STAGE n: also stop once stage n (0x81399E, from frame 18000) has started (its play counter
  -- seen below 0x100) and its play counter then drops (the reset after the stage, as fullcmp.sh
  -- finds stage starts; the drop into the stage itself comes first); END_FRAME stays a cap
  if end_stage and ft >= 18000 then
    local v = mt:read_u16(0x813A1E)
    if end_armed and end_prev and v < end_prev - 100 then out:close(); manager.machine:exit() end
    if sn == end_stage and v < 0x100 then end_armed = true end
    end_prev = v
  end
  if ft == tonumber(os.getenv("END_FRAME") or "48250") then out:close(); manager.machine:exit() end
end)
if os.getenv("EXTRA_LUA") then dofile(os.getenv("EXTRA_LUA")) end
-- DUMP_PLAYS "stage:play,..." (hex play): dump work RAM, the enhanced RAM and the bullet pool when
-- 0x81399E = stage and the play counter first reaches play (DUMP_DIR/p_<stage>_<play>.bin)
if os.getenv("DUMP_PLAYS") then
  local want = {}
  for s, v in os.getenv("DUMP_PLAYS"):gmatch("(%d+):(%x+)") do want[#want+1] = {tonumber(s), tonumber(v, 16)} end
  local done = {}
  -- DUMP_LAST=1: dump the last frame with that play value instead of the first. When the 68k lags,
  -- a logic step spans two frames and the first frame of a play value can be mid-step (a volley
  -- half fired); the last one is the finished step, as the PS2's samples are
  local last, pend = os.getenv("DUMP_LAST") == "1", {}
  local function snap()
    local t = {}
    for a = 0x800000, 0x81ffff do t[#t+1] = string.char(mt:read_u8(a)) end
    for a = 0xe00000, 0xe0ffff do t[#t+1] = string.char(mt:read_u8(a)) end
    for a = 0xd00000, 0xd0c7ff do t[#t+1] = string.char(mt:read_u8(a)) end
    return table.concat(t)
  end
  local function write(w, data)
    local d = io.open(string.format("%s/p_%d_%x.bin", os.getenv("DUMP_DIR"), w[1], w[2]), "wb")
    d:write(data) d:close()
  end
  emu.register_frame_done(function()
    local sn, v = mt:read_u16(0x81399E), mt:read_u16(0x813A1E)
    for i, w in ipairs(want) do
      if not done[i] then
        if sn == w[1] and v == w[2] then
          if last then pend[i] = snap() else done[i] = true; write(w, snap()) end
        elseif pend[i] then
          done[i] = true; write(w, pend[i]); pend[i] = nil; collectgarbage()
        end
      end
    end
  end)
end
