local PORT = 8765
local server = nil
local clients = {}
local buffers = {}
local nextId = 1
local scriptFrame = 0
local releases = {}

local KEYS = {
  A = C.GBA_KEY.A, B = C.GBA_KEY.B,
  SELECT = C.GBA_KEY.SELECT, START = C.GBA_KEY.START,
  RIGHT = C.GBA_KEY.RIGHT, LEFT = C.GBA_KEY.LEFT,
  UP = C.GBA_KEY.UP, DOWN = C.GBA_KEY.DOWN,
  R = C.GBA_KEY.R, L = C.GBA_KEY.L,
}

local function send(sock, msg)
  if sock then sock:send(tostring(msg) .. "\n") end
end

local function num(s)
  if not s then return nil end
  s = s:match("^%s*(.-)%s*$")
  if s:match("^0[xX][0-9a-fA-F]+$") then return tonumber(s:sub(3), 16) end
  return tonumber(s)
end

local function parse2(rest)
  local a, b = rest:match("^(%S+)%s+(%S+)%s*$")
  return num(a), num(b)
end

local function handle(sock, line)
  local cmd, rest = line:match("^(%S+)%s*(.-)%s*$")
  if not cmd then return end
  cmd = cmd:upper()

  if cmd == "PING" then
    send(sock, "PONG " .. tostring(scriptFrame))
  elseif cmd == "FRAME" then
    send(sock, tostring(emu:currentFrame()))
  elseif cmd == "TITLE" then
    send(sock, emu:getGameTitle() or "")
  elseif cmd == "CODE" then
    send(sock, emu:getGameCode() or "")
  elseif cmd == "KEYS" then
    send(sock, string.format("0x%08X", emu:getKeys()))
  elseif cmd == "RESET" then
    emu:reset()
    send(sock, "OK")
  elseif cmd == "KEYDOWN" or cmd == "KEYUP" then
    local k = KEYS[rest:upper()]
    if k == nil then
      send(sock, "ERR bad key")
    else
      if cmd == "KEYDOWN" then emu:addKey(k) else emu:clearKey(k) end
      send(sock, "OK")
    end
  elseif cmd == "PRESS" then
    local name, n = rest:match("^(%S+)%s*(%d*)$")
    local k = name and KEYS[name:upper()] or nil
    n = tonumber(n) or 2
    if k == nil then
      send(sock, "ERR bad key")
    else
      emu:addKey(k)
      releases[k] = scriptFrame + math.max(1, n)
      send(sock, "OK")
    end
  elseif cmd == "READ8" or cmd == "READ16" or cmd == "READ32" then
    local a = num(rest)
    if not a then
      send(sock, "ERR bad address")
    else
      local v
      if cmd == "READ8" then v = emu:read8(a)
      elseif cmd == "READ16" then v = emu:read16(a)
      else v = emu:read32(a) end
      send(sock, string.format("0x%X", v))
    end
  elseif cmd == "WRITE8" or cmd == "WRITE16" or cmd == "WRITE32" then
    local a, v = parse2(rest)
    if not a or not v then
      send(sock, "ERR usage " .. cmd .. " ADDRESS VALUE")
    else
      if cmd == "WRITE8" then emu:write8(a, v)
      elseif cmd == "WRITE16" then emu:write16(a, v)
      else emu:write32(a, v) end
      send(sock, "OK")
    end
  elseif cmd == "SCREENSHOT" then
    local path = rest
    if path == "" then path = "/mnt/data/mgba-shot.png" end
    emu:screenshot(path)
    send(sock, "OK " .. path)
  else
    send(sock, "ERR unknown command")
  end
end

local function on_client_data(id)
  local sock = clients[id]
  if not sock then return end
  while true do
    local p, err = sock:receive(4096)
    if p then
      local data = (buffers[id] or "") .. p
      while true do
        local s, e = data:find("\n", 1, true)
        if not s then break end
        local line = data:sub(1, s - 1):gsub("\r$", "")
        data = data:sub(e + 1)
        if line ~= "" then handle(sock, line) end
      end
      buffers[id] = data
    else
      if err ~= socket.ERRORS.AGAIN then
        sock:close(); clients[id] = nil; buffers[id] = nil
      end
      return
    end
  end
end

local function accept_client()
  local sock, err = server:accept()
  if not sock then return end
  local id = nextId; nextId = nextId + 1
  clients[id] = sock
  buffers[id] = ""
  sock:add("received", function() on_client_data(id) end)
  sock:add("error", function() clients[id] = nil; buffers[id] = nil end)
  send(sock, "HELLO mgba-control-v2")
end

server = socket.bind("127.0.0.1", PORT)
if server then
  local ok, err = server:listen()
  if ok then server:add("received", accept_client) end
end

callbacks:add("frame", function()
  scriptFrame = scriptFrame + 1
  for k, due in pairs(releases) do
    if scriptFrame >= due then emu:clearKey(k); releases[k] = nil end
  end
end)

local f = io.open("/mnt/data/mgba_control_v2_ready.txt", "w")
if f then
  f:write("loaded port=" .. tostring(PORT) .. "\n")
  f:close()
end
