local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local z80 = machine.devices[":soundcpu"]
local mem = cpu.spaces["program"]
local zio = z80.spaces["io"]
LOG = io.open(os.getenv("SNDLOG") or "/tmp/sndlog.txt", "w")
local buf = {}
LASTBANK = -1
local snapn = 0
local function fr() return machine.screens[":screen"]:frame_number() end
TAP1 = mem:install_write_tap(0xc10006, 0xc1000d, "cmd", function(offset, data, mask)
  buf[offset] = data
  if offset == 0xc1000c then
    local w0, w1, w2, w3 = buf[0xc10006] or 0, buf[0xc10008] or 0, buf[0xc1000a] or 0, buf[0xc1000c] or 0
    local t = (w0 >> 8) & 0xff
    LOG:write(string.format("F%06d CMD %02x vol=%02x id=%02x b3=%02x ext=%04x%04x bank=%d\n", fr(), t, w0 & 0xff, w1 >> 8, w1 & 0xff, w2, w3, LASTBANK))
    if t == 0x11 or t == 0x12 then
      snapn = snapn + 1
      local ok, err = pcall(function() machine.video:snapshot() end)
      LOG:write(string.format("  snapshot %d %s\n", snapn, tostring(ok and "" or err)))
    end
    LOG:flush()
  end
end)
TAP2 = mem:install_read_tap(0x17ca4c, 0x17cae7, "bank", function(offset, data, mask)
  local b = (offset - 0x17ca4c) // 12
  if b ~= LASTBANK then
    LASTBANK = b
    LOG:write(string.format("F%06d BANKUPLOAD bank=%d\n", fr(), b))
    LOG:flush()
  end
end)
-- ICS2115 register writes from the Z80: 0x8000 osc select? 0x8001 reg, 0x8002 lo, 0x8003 hi
local creg, lo = -1, 0
local regs = {}
SEEN = {}
TAP3 = zio:install_write_tap(0x8000, 0x8003, "ics", function(offset, data, mask)
  if offset == 0x8001 then creg = data & 0xff
  elseif offset == 0x8002 then lo = data & 0xff
  elseif offset == 0x8003 then
    local v = ((data & 0xff) << 8) | lo
    regs[creg] = v
    if creg == 0x0b then  -- address low follows address high (0x0a)
      local addr = (((regs[0x0a] or 0) << 16) | v) >> 12
      local bank = ((regs[0x11] or 0) >> 8) & 0x0f
      local key = (bank << 20) + addr
      if not SEEN[key] then
        SEEN[key] = fr()
        LOG:write(string.format("F%06d VOICESTART cfg=%04x f=%04x vs=%04x ve=%04x va=%04x vi=%04x vc=%04x pan=%04x key=%s (saddr=%04x) bank=%d\n", fr(), regs[0] or 0, regs[1] or 0, regs[7] or 0, regs[8] or 0, regs[9] or 0, regs[6] or 0, regs[0xd] or 0, regs[0xc] or 0, key, regs[0x11] or 0, LASTBANK))
        LOG:flush()
      end
    end
  end
end)
local coin = machine.ioport.ports[":Service"]
local p1 = machine.ioport.ports[":P1P2"]
local function field(port, name) if not port then return nil end for n, f in pairs(port.fields) do if n == name then return f end end return nil end
local fcoin = field(coin, "Coin 1")
local fstart = field(p1, "1 Player Start")
local fshot = field(p1, "P1 Button 1")
LOG:write(string.format("fields coin=%s start=%s shot=%s\n", tostring(fcoin ~= nil), tostring(fstart ~= nil), tostring(fshot ~= nil)))
emu.register_frame_done(function()
  local f = fr()
  if f == 5400 and fcoin then fcoin:set_value(1) end
  if f == 5410 and fcoin then fcoin:set_value(0) end
  if f == 5420 and fcoin then fcoin:set_value(1) end
  if f == 5430 and fcoin then fcoin:set_value(0) end
  if f == 5500 and fstart then fstart:set_value(1) end
  if f == 5520 and fstart then fstart:set_value(0) end
  if f > 5520 and f < 12000 and fshot then fshot:set_value((f // 4) % 2) end
  if f == 20000 then LOG:write("END\n"); LOG:close(); machine:exit() end
end)
