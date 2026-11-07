-- timing.lua with extra P1 inputs (SHOOT_EVENTS "frame,field,value;...", SHOOT_CTR_EVENTS) and full hitbox records,
-- the score long and the P1 record per frame: "ft ctr score p1(0xa0 hex) pool(120 x 0x2c hex)"
local mt = manager.machine.devices[":maincpu"].spaces["program"]
local pt = manager.machine.ioport.ports
local ft = 0
local ev = {[120] = {{"Service", "Coin 1", 1}}, [130] = {{"Service", "Coin 1", 0}},
            [300] = {{"P1P2", "1 Player Start", 1}}, [320] = {{"P1P2", "1 Player Start", 0}},
            [500] = {{"P1P2", "P1 Button 1", 1}}, [520] = {{"P1P2", "P1 Button 1", 0}},
            [1400] = {{"P1P2", "P1 Button 1", 1}}, [1410] = {{"P1P2", "P1 Button 1", 0}}}
for f, fld, v in (os.getenv("SHOOT_EVENTS") or ""):gmatch("(%d+),([^,;]+),(%d)") do
  f = tonumber(f); ev[f] = ev[f] or {}; table.insert(ev[f], {"P1P2", fld, tonumber(v)})
end
-- SHOOT_CTR_EVENTS "ctr(hex),field,value;...": applied at the first frame from 1700 on whose game
-- counter 0x801164 has reached ctr (MAME lag frames repeat a counter, so frame numbers drift)
local cev = {}
for c, fld, v in (os.getenv("SHOOT_CTR_EVENTS") or ""):gmatch("(%x+),([^,;]+),(%d)") do
  table.insert(cev, {tonumber(c, 16), fld, tonumber(v)})
end
local ci = 1
-- CTR_TABLE "key value" (hex) lines: from frame 1800 the game counter 0x801164 follows the PS2's
-- (0x35804C + 0x23F per logic frame: the PS2's slowdown freezes logic frames but counts them),
-- and the output and SHOOT_CTR_EVENTS use the logic-frame key (0x642298 + 0x3E5) instead
local ctab, key, written = nil, nil, nil
-- The replay's key starts at stage 1's first play frame as the play counter 0x813A1E +
-- KEY_PLAY_OFF (default 0x333, the PS2's logic-frame key there), then steps with each logic
-- frame; until the PS2 tables start (key 0x19D3 / 0x485) the counter, its mod-24 copy and the
-- toggle are replayed as the identity (counter = key, toggle = key & 1). Both used to come from
-- MAME's own counter 0x801164, which also counts boot and select steps, so a lag frame fewer
-- before the stage (the sprite builder speed-up, an overclock) shifted the whole replay a step.
local key_play_off = tonumber(os.getenv("KEY_PLAY_OFF") or "0x333")
-- KEY_TIMER=1 (the long traces): from frame 1800 the key is the play-time timer 0x8139DC (PS2
-- 0x642298, the key's own source) + KEY_PLAY_OFF instead of a count of logic frames; it stops
-- through stage clears on both machines, where counting logic frames would run ahead of the PS2's
-- key. (It used to start as MAME's counter at frame 1800, which also counts boot and select
-- steps: out_m38's one lag frame fewer in the Kakusei run's boot shifted that replay a step.)
local keytimer = os.getenv("KEY_TIMER") == "1"
-- KEY_UNWRAP=1 (traces into stage 6, where the 16-bit play time passes 0xFFFF): the key keeps
-- counting past 0xFFFF (the tables are then keyed the same way, f8 / tools/compare/both_tables.py)
local unwrap, tlast, thi = os.getenv("KEY_UNWRAP") == "1", nil, 0
if os.getenv("CTR_TABLE") then
  ctab = {}
  for l in io.lines(os.getenv("CTR_TABLE")) do
    local k, v = l:match("(%x+) (%x+)"); ctab[tonumber(k, 16)] = tonumber(v, 16)
  end
end
local function counter()
  if key then return key end
  return mt:read_u16(0x801164)
end
-- TG_TABLE "key toggle" lines (PS2 0x358048 at the end of each logic frame): the frame toggle
-- 0x801166 (which player's collision runs) follows the PS2's, which keeps toggling through the
-- PS2's frozen slowdown frames; normally MAME's end-of-frame value is the opposite of the PS2's
local tgt, tglast = nil, nil
if os.getenv("TG_TABLE") then
  tgt = {}
  for l in io.lines(os.getenv("TG_TABLE")) do
    local k, v = l:match("(%x+) (%d)"); tgt[tonumber(k, 16)] = tonumber(v)
  end
end
local function first_key(t) local k = nil if t then for x in pairs(t) do if not k or x < k then k = x end end end return k end
local ctab_first, tg_first = first_key(ctab), first_key(tgt)
local last = tonumber(os.getenv("SHOOT_END") or "7260")
local out = io.open(os.getenv("TIMING_OUT"), "w")
emu.register_frame_done(function()
  ft = ft + 1
  -- BTAP_FROM / BTAP_TO / BTAP_LOG: new bullets (flag word writes at a 0x40 slot start of 0xD00000) with pc and stack
  if os.getenv("BTAP_LOG") and ft == tonumber(os.getenv("BTAP_FROM")) then
    btlog = io.open(os.getenv("BTAP_LOG"), "w")
    local cpc = manager.machine.devices[":maincpu"].state["CURPC"]
    local csp = manager.machine.devices[":maincpu"].state["SP"]
    btap = mt:install_write_tap(0xD00000, 0xD031FF, "btap", function(offset, data, mask)
      if offset % 0x40 == 0 and data >= 0x8000 then
        local a = csp.value
        btlog:write(string.format("%d %04x %06x %06x %04x st %08x %08x %08x %08x %08x %08x %08x\n", ft, mt:read_u16(0x801164), cpc.value, offset, data,
          mt:read_u32(a), mt:read_u32(a + 4), mt:read_u32(a + 8), mt:read_u32(a + 12), mt:read_u32(a + 16), mt:read_u32(a + 20), mt:read_u32(a + 24)))
      end
      return data
    end)
  end
  if btap and ft == tonumber(os.getenv("BTAP_TO")) then btap:remove(); btap = nil; btlog:close() end
  for _, e in ipairs(ev[ft] or {}) do pt[":" .. e[1]].fields[e[2]]:set_value(e[3]) end
  -- BUL_LOG / BUL_FROM / BUL_TO: per frame "frame counter 0x813A18 gems bullet-area hex" (0xD00000, 0x3200)
  if os.getenv("BUL_LOG") and ft >= tonumber(os.getenv("BUL_FROM")) and ft <= tonumber(os.getenv("BUL_TO")) then
    if not bullog then bullog = io.open(os.getenv("BUL_LOG"), "w") end
    local t = {} for a = 0xD00000, tonumber(os.getenv("BUL_END") or "0xD031FF"), 2 do t[#t+1] = string.format("%04x", mt:read_u16(a)) end
    bullog:write(string.format("%d %04x %04x %04x %s\n", ft, mt:read_u16(0x801164), mt:read_u16(0x813A18), mt:read_u16(0x813A02), table.concat(t)))
    if ft == tonumber(os.getenv("BUL_TO")) then bullog:close() end
  end
  -- ITEM_LOG (same frames): per frame "frame counter 0x813A18 item-count item-pool hex" (gems /
  -- gold items: 800 records of 30 bytes at 0xE10000, live count 0x818338)
  if os.getenv("ITEM_LOG") and ft >= tonumber(os.getenv("BUL_FROM")) and ft <= tonumber(os.getenv("BUL_TO")) then
    if not itemlog then itemlog = io.open(os.getenv("ITEM_LOG"), "w") end
    local t = {} for a = 0xE10000, 0xE15DBF, 2 do t[#t+1] = string.format("%04x", mt:read_u16(a)) end
    itemlog:write(string.format("%d %04x %04x %04x %s\n", ft, mt:read_u16(0x801164), mt:read_u16(0x813A18), mt:read_u16(0x818338), table.concat(t)))
    if ft == tonumber(os.getenv("BUL_TO")) then itemlog:close() end
  end
  if ctab and keytimer and ft >= 1800 then
    -- KEY_TIMER (the long traces' tables, tools/compare/longrun.sh): the play-time timer + KEY_PLAY_OFF
    local r, t = mt:read_u16(0x801164), mt:read_u16(0x8139DC)
    if unwrap then
      if tlast and t < tlast and tlast - t > 0x8000 then thi = thi + 0x10000 end
      tlast = t; key = thi + t + key_play_off
    else
      key = (t + key_play_off) & 0xffff
    end
    local nxt = ctab[key + 1]
    if nxt then written = (nxt - 1) & 0xffff; mt:write_u16(0x801164, written); mt:write_u16(0x801170, written % 24) else written = r end
  elseif ctab and not keytimer and ft >= 1400 then
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
  if tgt and (key or not ctab) and ft >= (keytimer and 1800 or 1400) then
    local r = ctab and key or mt:read_u16(0x801164)
    local v = tgt[r + 1]
    if not v and ctab and not keytimer and r < tg_first then v = r & 1 end   -- before the PS2 table: toggle = key & 1
    if r ~= tglast and v then mt:write_u16(0x801166, v) end
    tglast = r
  end
  if ft >= 1700 then
    local c = counter()
    -- (P1 Button 4 = D, the barrier, is in the Service port on the PGM)
    while cev[ci] and c >= cev[ci][1] do
      pt[cev[ci][2] == "P1 Button 4" and ":Service" or ":P1P2"].fields[cev[ci][2]]:set_value(cev[ci][3]); ci = ci + 1
    end
  end
  if ft >= 700 then mt:write_u8(0x80EE96 + 0x39, 0xff) end
  -- the PS2's RNG at the game start (RNG_VAL, 0x21), set through stage 1's first play step (play 1), before
  -- the step's draws: it used to be set at MAME frame 1463, which is play 2 when the boot lags less
  if ft < 3000 and mt:read_u16(0x81399E) == 0 and mt:read_u16(0x813A1E) == 1 then mt:write_u16(0x801176, tonumber(os.getenv("RNG_VAL") or "0021", 16)) end
  if ft % 60 == 0 then collectgarbage() end
  if ft >= 1800 then
    local p = {} for k = 0, 0x9e, 2 do p[#p+1] = string.format("%04x", mt:read_u16(0x80EE96 + k)) end
    local t = {} for k = 0, 120 * 0x2c - 2, 2 do t[#t+1] = string.format("%04x", mt:read_u16(0xE08200 + k)) end
    out:write(string.format("%d %04x %08x %s %s\n", ft, counter(), mt:read_u32(0x81B57E), table.concat(p), table.concat(t)))
  end
  if ft == last then out:close(); manager.machine:exit() end
end)
if os.getenv("EXTRA_LUA") then dofile(os.getenv("EXTRA_LUA")) end
