-- Arrange rule checks: one scenario per run (ESPGAL_RULE), results as "RULE key=value" lines on
-- stdout. Driven by tools/rules_check.py. Addresses: spec/pgm-gameplay.md.
--   start     lives, gauge and gems at game start and after the first respawn (no invincibility)
--   extend    extend thresholds of the P1 score record, then a score poked just below each
--             threshold: does a life get added (invincible P1, fire held)
--   continue  P1 left with no spare life and no invincibility: game over, then coin + start
--             pressed repeatedly; does play resume
--   barrier   gauge set to ESPGAL_GAUGE (default full), the barrier button (ESPGAL_BUTTON) held
--             for ESPGAL_HOLD frames: gauge, barrier flag and gems as they change
--   kakusei   gems ESPGAL_GEMS and gauge ESPGAL_GAUGE, the Kakusei button tapped, no
--             invincibility from then on: gems, gauge, Kakusei transition and barrier flags
-- Series print "frame:value" pairs only where the value changes (frame relative to the press).
local rule = os.getenv("ESPGAL_RULE") or "extend"
local mem = manager.machine.devices[":maincpu"].spaces["program"]
local ports = manager.machine.ioport.ports
local P1, SCORE, LIVES = 0x80EE96, 0x81B57C, 0x8139D6
local GAUGE, BARRIER, GEMS, KAKUSEI = P1 + 0x24, P1 + 0x5F, P1 + 0x6E, P1 + 0x5E
local START = 2000                  -- in play by then (stage 1, score and lives initialised)
local PRESS = START + 10
local n = 0

local function press(port, field, v)
  local f = ports[":" .. port].fields[field]
  if f then f:set_value(v) else print("RULE error=no-field:" .. field) end
end
local function out(k, v) print(string.format("RULE %s=%s", k, tostring(v))) end
local function hex(v) return string.format("%#x", v) end

local menu = {[120] = {"Service", "Coin 1", 1}, [130] = {"Service", "Coin 1", 0},
              [300] = {"P1P2", "1 Player Start", 1}, [320] = {"P1P2", "1 Player Start", 0},
              [500] = {"P1P2", "P1 Button 1", 1}, [520] = {"P1P2", "P1 Button 1", 0},
              [1400] = {"P1P2", "P1 Button 1", 1}}
local button = os.getenv("ESPGAL_BUTTON")
local hold = tonumber(os.getenv("ESPGAL_HOLD") or "6")
local gauge0 = tonumber(os.getenv("ESPGAL_GAUGE") or "fa0", 16)
local gems0 = tonumber(os.getenv("ESPGAL_GEMS") or "300")
local last_lives, gone, back = nil, nil, nil
local t1, t2
local watch = {}                    -- name -> {read, last, list}

local function track(name, read)
  watch[#watch + 1] = {name, read, nil, {}}
end
local function sample(rel)
  for _, w in ipairs(watch) do
    local v = w[2]()
    if v ~= w[3] then table.insert(w[4], string.format("%d:%x", rel, v)); w[3] = v end
  end
end
local function report()
  for _, w in ipairs(watch) do out(w[1], table.concat(w[4], ",")) end
end

if rule == "barrier" then
  track("gauge", function() return mem:read_u16(GAUGE) end)
  track("barrier", function() return mem:read_u8(BARRIER) end)
  track("gems", function() return mem:read_u16(GEMS) end)
  track("state", function() return mem:read_u8(P1 + 0x58) end)
  track("scroll", function() return mem:read_u16(0x8139E0) end)
elseif rule == "kakusei" then
  track("gems", function() return mem:read_u16(GEMS) end)
  track("gauge", function() return mem:read_u16(GAUGE) end)
  track("kakusei", function() return mem:read_u8(KAKUSEI) end)
  track("barrier", function() return mem:read_u8(BARRIER) end)
  track("lives", function() return mem:read_u16(LIVES) end)
end

emu.register_frame_done(function()
  n = n + 1
  local m = menu[n]
  if m then press(m[1], m[2], m[3]) end
  local invincible = (rule == "extend" or rule == "barrier") or (rule == "kakusei" and n < PRESS)
  if invincible and n >= 700 then mem:write_u8(P1 + 0x39, 0xff) end
  if rule == "start" then
    local l = mem:read_u16(LIVES)
    if n >= 600 and n < START and mem:read_u8(P1) >= 0x80 and not t1 then
      t1 = n; out("first_gems", mem:read_u16(GEMS)); out("first_gauge", hex(mem:read_u16(GAUGE)))
    end
    if n == START then
      out("lives", l); out("gauge", hex(mem:read_u16(GAUGE))); out("gems", mem:read_u16(GEMS))
      last_lives = l
    elseif n > START and l ~= last_lives and not gone then
      gone = n                      -- respawn values are set while the life is taken
    elseif gone and n == gone + 120 then
      out("respawn_gauge", hex(mem:read_u16(GAUGE))); out("respawn_gems", mem:read_u16(GEMS))
      manager.machine:exit()
    elseif n >= START + 20000 then
      out("respawn", "none"); manager.machine:exit()
    end
  elseif rule == "extend" then
    if n == START then
      t1, t2 = mem:read_u32(SCORE + 0x10), mem:read_u32(SCORE + 0x14)
      out("threshold1", t1 * 10); out("threshold2", t2 * 10)
      out("lives0", mem:read_u16(LIVES))
      mem:write_u32(SCORE + 2, t1 - 50)
    elseif n == START + 1500 then
      out("score1", mem:read_u32(SCORE + 2) * 10); out("lives1", mem:read_u16(LIVES))
      mem:write_u32(SCORE + 2, t2 - 50)     -- (the record's thresholds move on after an extend)
    elseif n == START + 3000 then
      out("score2", mem:read_u32(SCORE + 2) * 10); out("lives2", mem:read_u16(LIVES))
      manager.machine:exit()
    end
  elseif rule == "continue" then
    if n == START then mem:write_u16(LIVES, 0) end
    if n > START then
      local l = mem:read_u16(LIVES)
      if l ~= last_lives then out("lives@" .. n, hex(l)); last_lives = l end
      if l == 0xffff and not gone then gone = n end
      if gone and n > gone + 60 and n < gone + 1800 then
        local k = (n - gone) % 120
        if k == 0 then press("Service", "Coin 1", 1) elseif k == 10 then press("Service", "Coin 1", 0)
        elseif k == 30 then press("P1P2", "1 Player Start", 1) elseif k == 40 then press("P1P2", "1 Player Start", 0) end
      end
      if gone and l ~= 0xffff and not back then back = n end
      if (gone and n >= gone + 2400) or n >= START + 30000 then
        out("game_over", gone or "none"); out("continued", back and ("yes@" .. back) or "no")
        manager.machine:exit()
      end
    end
  elseif rule == "barrier" or rule == "kakusei" then
    if n == START then
      mem:write_u16(GAUGE, gauge0)
      if rule == "kakusei" then mem:write_u16(GEMS, gems0) end
    end
    if n == PRESS then
      press("P1P2", button, 1)
      if rule == "kakusei" then mem:write_u8(P1 + 0x39, 0) end   -- (0xff stays until cleared)
    elseif n == PRESS + hold then press("P1P2", button, 0) end
    if n >= PRESS - 5 then sample(n - PRESS) end
    if n == PRESS + 600 then report(); manager.machine:exit() end
  end
end)
