local PORT = 8765
local PROTOCOL = "mgba-rpc/0.1"

local server = nil
local clients = {}
local buffers = {}
local nextClientId = 1
local nextActionId = 1
local actions = {}
local actionQueue = {}
local activeAction = nil
local activeKeys = {}

local KEYS = {
  A = C.GBA_KEY.A, B = C.GBA_KEY.B,
  SELECT = C.GBA_KEY.SELECT, START = C.GBA_KEY.START,
  RIGHT = C.GBA_KEY.RIGHT, LEFT = C.GBA_KEY.LEFT,
  UP = C.GBA_KEY.UP, DOWN = C.GBA_KEY.DOWN,
  R = C.GBA_KEY.R, L = C.GBA_KEY.L,
}

-- Minimal JSON codec for RPC values: null/bool/number/string/array/object.
local json = {}

local function json_escape(s)
  return s:gsub('[%z\1-\31\\"]', function(c)
    local m = { ['"']='\\"', ['\\']='\\\\', ['\b']='\\b', ['\f']='\\f', ['\n']='\\n', ['\r']='\\r', ['\t']='\\t' }
    return m[c] or string.format('\\u%04x', string.byte(c))
  end)
end

local function is_array(t)
  local n = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then return false end
    if k > n then n = k end
  end
  for i = 1, n do if t[i] == nil then return false end end
  return true, n
end

function json.encode(v)
  local tv = type(v)
  if v == nil then return "null" end
  if tv == "boolean" then return v and "true" or "false" end
  if tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return tostring(v)
  end
  if tv == "string" then return '"' .. json_escape(v) .. '"' end
  if tv == "table" then
    local arr, n = is_array(v)
    local out = {}
    if arr then
      for i = 1, n do out[#out+1] = json.encode(v[i]) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    for k, val in pairs(v) do
      out[#out+1] = json.encode(tostring(k)) .. ":" .. json.encode(val)
    end
    table.sort(out)
    return "{" .. table.concat(out, ",") .. "}"
  end
  error("unsupported json type: " .. tv)
end

function json.decode(s)
  local i, len = 1, #s
  local function ws() while i <= len and s:sub(i,i):match("%s") do i=i+1 end end
  local parse_value
  local function parse_string()
    if s:sub(i,i) ~= '"' then error("expected string") end
    i=i+1
    local out={}
    while i<=len do
      local c=s:sub(i,i)
      if c=='"' then i=i+1; return table.concat(out) end
      if c=='\\' then
        i=i+1; local e=s:sub(i,i)
        local m={ ['"']='"', ['\\']='\\', ['/']='/', b='\b', f='\f', n='\n', r='\r', t='\t' }
        if m[e] then out[#out+1]=m[e]; i=i+1
        elseif e=='u' then
          local h=s:sub(i+1,i+4); if not h:match('^%x%x%x%x$') then error('bad unicode escape') end
          local cp=tonumber(h,16)
          if cp < 128 then out[#out+1]=string.char(cp)
          elseif cp < 2048 then out[#out+1]=string.char(192+math.floor(cp/64),128+(cp%64))
          else out[#out+1]=string.char(224+math.floor(cp/4096),128+(math.floor(cp/64)%64),128+(cp%64)) end
          i=i+5
        else error('bad escape') end
      else out[#out+1]=c; i=i+1 end
    end
    error("unterminated string")
  end
  local function parse_number()
    local st=i
    while i<=len and s:sub(i,i):match('[%d%+%-%e%E%.]') do i=i+1 end
    local n=tonumber(s:sub(st,i-1)); if n==nil then error('bad number') end; return n
  end
  local function parse_array()
    i=i+1; ws(); local a={}
    if s:sub(i,i)==']' then i=i+1; return a end
    while true do
      a[#a+1]=parse_value(); ws()
      local c=s:sub(i,i)
      if c==']' then i=i+1; return a end
      if c~=',' then error('expected comma') end
      i=i+1; ws()
    end
  end
  local function parse_object()
    i=i+1; ws(); local o={}
    if s:sub(i,i)=='}' then i=i+1; return o end
    while true do
      local k=parse_string(); ws(); if s:sub(i,i)~=':' then error('expected colon') end
      i=i+1; ws(); o[k]=parse_value(); ws()
      local c=s:sub(i,i)
      if c=='}' then i=i+1; return o end
      if c~=',' then error('expected comma') end
      i=i+1; ws()
    end
  end
  function parse_value()
    ws(); local c=s:sub(i,i)
    if c=='"' then return parse_string() end
    if c=='{' then return parse_object() end
    if c=='[' then return parse_array() end
    if s:sub(i,i+3)=='true' then i=i+4; return true end
    if s:sub(i,i+4)=='false' then i=i+5; return false end
    if s:sub(i,i+3)=='null' then i=i+4; return nil end
    if c:match('[%d%-]') then return parse_number() end
    error('unexpected token at '..i)
  end
  local v=parse_value(); ws(); if i<=len then error('trailing json') end; return v
end

local function send(sock, obj)
  if sock then sock:send(json.encode(obj) .. "\n") end
end

local function frame()
  return emu:currentFrame()
end

local function clear_all_keys()
  for _, key in pairs(KEYS) do emu:clearKey(key) end
  activeKeys = {}
end

local function key_value(name)
  if type(name) ~= "string" then return nil end
  return KEYS[name:upper()]
end

local function set_step_keys(names)
  clear_all_keys()
  if not names then return end
  for _, name in ipairs(names) do
    local k=key_value(name)
    if k ~= nil then emu:addKey(k); activeKeys[k]=true end
  end
end

local function normalize_sequence(steps)
  local out={}
  if type(steps) ~= "table" then error("steps must be an array") end
  for _, st in ipairs(steps) do
    if type(st) ~= "table" then error("step must be object") end
    local frames=math.floor(tonumber(st.frames or st.wait or 1) or 1)
    if frames < 1 then frames=1 end
    local keys=st.keys
    if st.key then keys={st.key} end
    if keys ~= nil and type(keys) ~= "table" then error("keys must be array") end
    if keys then
      for _, name in ipairs(keys) do if not key_value(name) then error("bad key: "..tostring(name)) end end
    end
    out[#out+1]={keys=keys or {}, frames=frames}
  end
  if #out==0 then error("empty sequence") end
  return out
end

local function enqueue_action(steps)
  local id=nextActionId; nextActionId=nextActionId+1
  local a={id=id, state="queued", created_frame=frame(), steps=normalize_sequence(steps), step=0}
  actions[id]=a; actionQueue[#actionQueue+1]=a
  return a
end

local function action_public(a)
  if not a then return nil end
  return {id=a.id,state=a.state,created_frame=a.created_frame,started_frame=a.started_frame,finished_frame=a.finished_frame,step=math.min(a.step or 0,#a.steps),total_steps=#a.steps}
end

local function read_width(width, address)
  if width==8 then return emu:read8(address) end
  if width==16 then return emu:read16(address) end
  if width==32 then return emu:read32(address) end
  error("width must be 8, 16, or 32")
end

local function write_width(width, address, value)
  if width==8 then emu:write8(address,value); return end
  if width==16 then emu:write16(address,value); return end
  if width==32 then emu:write32(address,value); return end
  error("width must be 8, 16, or 32")
end

local function capabilities()
  return {
    protocol=PROTOCOL,
    ops={"ping","info","observe","input.press","input.sequence","input.clear","action.status","memory.read","memory.read_batch","memory.write","screenshot","state.save","state.load","reset"},
    keys={"A","B","SELECT","START","RIGHT","LEFT","UP","DOWN","R","L"},
    memory_widths={8,16,32},
    frame_synchronized_input=true,
    screenshot=true,
    savestate=true,
  }
end

local function dispatch(req)
  local op=req.op
  local p=req.params or {}
  if op=="ping" then return {pong=true, protocol=PROTOCOL} end
  if op=="info" then return {title=emu:getGameTitle() or "", code=emu:getGameCode() or "", frame=frame(), capabilities=capabilities()} end
  if op=="reset" then emu:reset(); clear_all_keys(); return {reset=true} end
  if op=="input.clear" then clear_all_keys(); return {keys=emu:getKeys()} end
  if op=="input.press" then
    local key=p.key; local frames=math.floor(tonumber(p.frames or 2) or 2)
    local a=enqueue_action({{keys={key},frames=frames},{keys={},frames=1}})
    return {action=action_public(a)}
  end
  if op=="input.sequence" then
    local a=enqueue_action(p.steps)
    return {action=action_public(a)}
  end
  if op=="action.status" then
    local a=actions[tonumber(p.id)]
    if not a then error("unknown action") end
    return {action=action_public(a)}
  end
  if op=="memory.read" then
    local address=assert(tonumber(p.address),"address required")
    local width=tonumber(p.width or 8)
    return {address=address,width=width,value=read_width(width,address)}
  end
  if op=="memory.read_batch" then
    local vals={}
    for idx,r in ipairs(p.reads or {}) do
      local address=assert(tonumber(r.address),"address required")
      local width=tonumber(r.width or 8)
      vals[idx]={address=address,width=width,value=read_width(width,address),name=r.name}
    end
    return {reads=vals}
  end
  if op=="memory.write" then
    local address=assert(tonumber(p.address),"address required")
    local width=tonumber(p.width or 8); local value=assert(tonumber(p.value),"value required")
    write_width(width,address,value)
    return {address=address,width=width,value=read_width(width,address)}
  end
  if op=="screenshot" then
    local path=p.path or "/mnt/data/mgba-shot.png"
    emu:screenshot(path)
    return {path=path}
  end
  if op=="state.save" then
    local path=assert(p.path,"path required")
    local ok
    if p.flags == nil then ok=emu:saveStateFile(path) else ok=emu:saveStateFile(path, tonumber(p.flags)) end
    return {path=path,saved=ok and true or false}
  end
  if op=="state.load" then
    local path=assert(p.path,"path required")
    local ok
    if p.flags == nil then ok=emu:loadStateFile(path) else ok=emu:loadStateFile(path, tonumber(p.flags)) end
    return {path=path,loaded=ok and true or false}
  end
  if op=="observe" then
    local result={frame=frame(),keys=emu:getKeys(),title=emu:getGameTitle() or "",code=emu:getGameCode() or ""}
    if p.reads then
      result.reads={}
      for idx,r in ipairs(p.reads) do
        local address=assert(tonumber(r.address),"address required")
        local width=tonumber(r.width or 8)
        result.reads[idx]={address=address,width=width,value=read_width(width,address),name=r.name}
      end
    end
    if p.screenshot then
      local path=type(p.screenshot)=="string" and p.screenshot or string.format("/mnt/data/mgba-frame-%d.png",frame())
      emu:screenshot(path); result.screenshot=path
    end
    if activeAction then result.action=action_public(activeAction) end
    return result
  end
  error("unknown op: "..tostring(op))
end

local function handle(sock,line)
  local ok,req=pcall(json.decode,line)
  if not ok or type(req)~="table" then send(sock,{id=nil,ok=false,frame=frame(),error="invalid json: "..tostring(req)}); return end
  local id=req.id
  local ok2,res=pcall(dispatch,req)
  if ok2 then send(sock,{id=id,ok=true,frame=frame(),result=res})
  else send(sock,{id=id,ok=false,frame=frame(),error=tostring(res)}) end
end

local function on_client_data(id)
  local sock=clients[id]; if not sock then return end
  while true do
    local p,err=sock:receive(8192)
    if p then
      local data=(buffers[id] or "")..p
      while true do
        local s,e=data:find("\n",1,true); if not s then break end
        local line=data:sub(1,s-1):gsub("\r$",""); data=data:sub(e+1)
        if line~="" then handle(sock,line) end
      end
      buffers[id]=data
    else
      if err~=socket.ERRORS.AGAIN then sock:close(); clients[id]=nil; buffers[id]=nil end
      return
    end
  end
end

local function accept_client()
  local sock=server:accept(); if not sock then return end
  local id=nextClientId; nextClientId=nextClientId+1
  clients[id]=sock; buffers[id]=""
  sock:add("received",function() on_client_data(id) end)
  sock:add("error",function() clients[id]=nil; buffers[id]=nil end)
  send(sock,{type="hello",protocol=PROTOCOL,frame=frame(),title=emu:getGameTitle() or "",code=emu:getGameCode() or "",capabilities=capabilities()})
end

callbacks:add("frame",function()
  if not activeAction and #actionQueue>0 then
    activeAction=table.remove(actionQueue,1); activeAction.state="running"; activeAction.started_frame=frame(); activeAction.step=0; activeAction.remaining=0
  end
  if activeAction then
    if activeAction.remaining<=0 then
      activeAction.step=activeAction.step+1
      local st=activeAction.steps[activeAction.step]
      if not st then
        clear_all_keys(); activeAction.state="done"; activeAction.finished_frame=frame(); activeAction=nil
      else
        set_step_keys(st.keys); activeAction.remaining=st.frames
      end
    end
    if activeAction then activeAction.remaining=activeAction.remaining-1 end
  end
end)

server=socket.bind("127.0.0.1",PORT)
if server then
  local ok=server:listen()
  if ok then server:add("received",accept_client) end
end

local f=io.open("/mnt/data/mgba_rpc_ready.txt","w")
if f then f:write(PROTOCOL.." port="..tostring(PORT).."\n"); f:close() end
