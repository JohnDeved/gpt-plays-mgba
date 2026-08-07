local PORT = 8765
local PROTOCOL = "mgba-rpc/0.2"
local MAX_RANGE_BYTES = 1024 * 1024
local MAX_EVENT_QUEUE = 4096
local RUNTIME_DIR = os.getenv("MGBA_RUNTIME_DIR") or "/tmp"
local READY_FILE = os.getenv("MGBA_RPC_READY_FILE") or (RUNTIME_DIR.."/mgba_rpc_ready.txt")

-- Optional text-printer inspection defaults for the verified Run & Bun profile.
-- Clients can override these for another Gen III ROM/profile.
local DEFAULT_TEXT_BUFFER_ADDRESS = 0x02021FC4
local DEFAULT_TEXT_BUFFER_LENGTH = 0x3E8
local DEFAULT_TEXT_PRINTERS_ADDRESS = 0x0202018C
local DEFAULT_TEXT_PRINTER_STRIDE = 0x24
local DEFAULT_TEXT_PRINTER_SLOTS = 16
-- Verified Gen III task scheduler layout for the Run & Bun v1.07 ROM.
-- A Task is: function (u32), active (u8), prev (u8), next (u8),
-- priority (u8), data[16] (u16), for a 0x28-byte stride.
local DEFAULT_TASKS_ADDRESS = 0x03005E10
local DEFAULT_TASK_STRIDE = 0x28
local DEFAULT_TASK_SLOTS = 16

local server = nil
local clients = {}
local buffers = {}
local nextClientId = 1
local nextActionId = 1
local actions = {}
local actionQueue = {}
local activeAction = nil
local activeKeys = {}
local snapshots = {}
local watches = {}
local watchEvents = {}
local nextEventId = 1
local waits = {}
local nextWaitId = 1

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

local function integer_param(value, label, minimum, maximum)
  local n=tonumber(value)
  if n==nil or n%1~=0 then error(label.." must be an integer") end
  if minimum and n<minimum then error(label.." must be >= "..tostring(minimum)) end
  if maximum and n>maximum then error(label.." must be <= "..tostring(maximum)) end
  return n
end

local function normalize_range(spec, index)
  if type(spec)~="table" then error("range must be an object") end
  local address=integer_param(spec.address,"address",0,nil)
  local length=integer_param(spec.length,"length",1,MAX_RANGE_BYTES)
  local name=spec.name or ("range"..tostring(index or 1))
  if type(name)~="string" or name=="" then error("range name must be a non-empty string") end
  return {name=name,address=address,length=length}
end

local function normalize_ranges(specs)
  if type(specs)~="table" then error("ranges must be an array") end
  if #specs==0 then error("ranges must not be empty") end
  local out={}
  local seen={}
  for i,spec in ipairs(specs) do
    local r=normalize_range(spec,i)
    if seen[r.name] then error("duplicate range name: "..r.name) end
    seen[r.name]=true
    out[#out+1]=r
  end
  return out
end

local function bytes_to_hex(bytes, first, last)
  local out={}
  first=first or 1
  last=last or #bytes
  for i=first,last do out[#out+1]=string.format("%02x",bytes[i]) end
  return table.concat(out)
end

local function read_range_data(address, length)
  local bytes={}
  local raw=emu:readRange(address,length)
  if type(raw)=="string" and #raw==length then
    for i=1,length do bytes[i]=string.byte(raw,i) end
  else
    -- Keep a conservative fallback for older development builds.
    for i=0,length-1 do bytes[i+1]=emu:read8(address+i) end
  end
  return bytes,bytes_to_hex(bytes)
end

local function read_range_public(r, include_data)
  local bytes,hex=read_range_data(r.address,r.length)
  local out={name=r.name,address=r.address,length=r.length,encoding="hex"}
  if include_data then out.data=hex end
  return out,bytes
end

local function text_printer_public(address)
  return {
    address=address,
    current_char=emu:read32(address + 0x00),
    window_id=emu:read8(address + 0x04),
    font_id=emu:read8(address + 0x05),
    x=emu:read8(address + 0x06),
    y=emu:read8(address + 0x07),
    current_x=emu:read8(address + 0x08),
    current_y=emu:read8(address + 0x09),
    letter_spacing=emu:read8(address + 0x0A),
    line_spacing=emu:read8(address + 0x0B),
    callback=emu:read32(address + 0x10),
    active=emu:read8(address + 0x1B),
    state=emu:read8(address + 0x1C),
    text_speed=emu:read8(address + 0x1D),
    delay_counter=emu:read8(address + 0x1E),
    scroll_distance=emu:read8(address + 0x1F),
    min_letter_spacing=emu:read8(address + 0x20),
    japanese=emu:read8(address + 0x21),
  }
end

local function inspect_text(p)
  local buffer_address=integer_param(p.address or DEFAULT_TEXT_BUFFER_ADDRESS,"address",0,nil)
  local buffer_length=integer_param(p.length or DEFAULT_TEXT_BUFFER_LENGTH,"length",1,MAX_RANGE_BYTES)
  local printers_address=integer_param(p.printers_address or DEFAULT_TEXT_PRINTERS_ADDRESS,"printers_address",0,nil)
  local printer_stride=integer_param(p.printer_stride or DEFAULT_TEXT_PRINTER_STRIDE,"printer_stride",1,nil)
  local printer_slots=integer_param(p.printer_slots or DEFAULT_TEXT_PRINTER_SLOTS,"printer_slots",1,64)
  local _,hex=read_range_data(buffer_address,buffer_length)
  local printers={}
  for i=0,printer_slots-1 do
    printers[#printers+1]=text_printer_public(printers_address+i*printer_stride)
  end
  return {
    buffer={address=buffer_address,length=buffer_length,encoding="hex",data=hex},
    printers_address=printers_address,
    printer_stride=printer_stride,
    printers=printers,
  }
end

local function task_public(address, index)
  local data={}
  for i=0,15 do data[#data+1]=emu:read16(address + 0x08 + i*2) end
  return {
    index=index,
    address=address,
    function_address=emu:read32(address + 0x00),
    active=emu:read8(address + 0x04),
    previous=emu:read8(address + 0x05),
    next=emu:read8(address + 0x06),
    priority=emu:read8(address + 0x07),
    data=data,
  }
end

local function inspect_tasks(p)
  local address=integer_param(p.address or DEFAULT_TASKS_ADDRESS,"address",0,nil)
  local stride=integer_param(p.stride or DEFAULT_TASK_STRIDE,"stride",1,nil)
  local slots=integer_param(p.slots or DEFAULT_TASK_SLOTS,"slots",1,32)
  local tasks={}
  for i=0,slots-1 do tasks[#tasks+1]=task_public(address+i*stride,i) end
  return {
    address=address,
    stride=stride,
    slots=slots,
    tasks=tasks,
  }
end

local function sorted_watch_names()
  local names={}
  for name,_ in pairs(watches) do names[#names+1]=name end
  table.sort(names)
  return names
end

local function read_watch_value(w)
  if w.width then return read_width(w.width,w.address) end
  local _,hex=read_range_data(w.address,w.length)
  return hex
end

local function watch_public(w, include_value)
  local out={name=w.name,address=w.address,last_changed_frame=w.last_changed_frame}
  if w.width then out.width=w.width else out.length=w.length; out.encoding="hex" end
  if include_value then out.value=w.last end
  return out
end

local function queue_watch_event(w, before, after)
  local event={id=nextEventId,frame=frame(),watch=w.name,before=before,after=after}
  nextEventId=nextEventId+1
  watchEvents[#watchEvents+1]=event
  while #watchEvents>MAX_EVENT_QUEUE do table.remove(watchEvents,1) end
end

local function update_watches()
  for _,name in ipairs(sorted_watch_names()) do
    local w=watches[name]
    local ok,current=pcall(read_watch_value,w)
    if ok then
      if w.last~=nil and current~=w.last then
        local before=w.last
        w.last_changed_frame=frame()
        queue_watch_event(w,before,current)
      end
      w.last=current
      w.error=nil
    else
      w.error=tostring(current)
    end
  end
end

local function capture_ranges(ranges)
  local data={}
  for _,r in ipairs(ranges) do
    local bytes=read_range_data(r.address,r.length)
    data[r.name]=bytes
  end
  return data
end

local function snapshot_public(s, include_data)
  local out={name=s.name,frame=s.frame,ranges={}}
  for _,r in ipairs(s.ranges) do
    local item={name=r.name,address=r.address,length=r.length,encoding="hex"}
    if include_data then item.data=bytes_to_hex(s.data[r.name]) end
    out.ranges[#out.ranges+1]=item
  end
  return out
end

local function diff_bytes(before, after, r)
  if #before~=#after then error("snapshot range length changed: "..r.name) end
  local changes={}
  local start=nil
  local function flush(last)
    if not start then return end
    changes[#changes+1]={
      offset=start-1,
      address=r.address+start-1,
      length=last-start+1,
      before=bytes_to_hex(before,start,last),
      after=bytes_to_hex(after,start,last),
      encoding="hex",
    }
    start=nil
  end
  for i=1,#before do
    if before[i]~=after[i] then
      if not start then start=i end
    else
      flush(i-1)
    end
  end
  flush(#before)
  return changes
end

local function wait_condition_satisfied(w)
  local c=w.condition
  local kind=c.type or c.kind
  if kind=="frame" then
    return frame()>=integer_param(c.at_frame or c.frame,"at_frame",0,nil)
  end
  if kind=="memory_equals" or kind=="memory_not_equals" then
    local address=integer_param(c.address,"address",0,nil)
    local width=tonumber(c.width or 8)
    local value=integer_param(c.value,"value",0,nil)
    local equal=read_width(width,address)==value
    return kind=="memory_equals" and equal or not equal
  end
  if kind=="memory_changed" then
    local address=integer_param(c.address,"address",0,nil)
    local width=tonumber(c.width or 8)
    return read_width(width,address)~=w.baseline
  end
  if kind=="watch_changed" then
    local watch=watches[c.name]
    if not watch then error("unknown watch: "..tostring(c.name)) end
    return (watch.last_changed_frame or -1)>w.started_frame
  end
  if kind=="watch_equals" then
    local watch=watches[c.name]
    if not watch then error("unknown watch: "..tostring(c.name)) end
    return read_watch_value(watch)==c.value
  end
  if kind=="keys_equals" then return emu:getKeys()==integer_param(c.value,"value",0,nil) end
  if kind=="all" or kind=="any" then
    if type(c.conditions)~="table" or #c.conditions==0 then error("conditions must be a non-empty array") end
    local matches=0
    for _,child in ipairs(c.conditions) do
      local nested={condition=child,started_frame=w.started_frame,baseline=w.baseline}
      if wait_condition_satisfied(nested) then matches=matches+1 end
    end
    return kind=="all" and matches==#c.conditions or matches>0
  end
  error("unknown wait condition: "..tostring(kind))
end

local function wait_public(w)
  return {
    id=w.id,state=w.state,condition=w.condition,started_frame=w.started_frame,
    deadline_frame=w.deadline_frame,finished_frame=w.finished_frame,error=w.error,
  }
end

local function update_waits()
  for _,w in pairs(waits) do
    if w.state=="waiting" then
      local ok,satisfied=pcall(wait_condition_satisfied,w)
      if not ok then
        w.state="error"; w.error=tostring(satisfied); w.finished_frame=frame()
      elseif satisfied then
        w.state="done"; w.finished_frame=frame()
      elseif frame()>=w.deadline_frame then
        w.state="timed_out"; w.finished_frame=frame()
      end
    end
  end
end

local function poll_watch_events(after, limit)
  after=tonumber(after or 0) or 0
  limit=integer_param(limit or 256,"limit",1,MAX_EVENT_QUEUE)
  local out={}
  for _,event in ipairs(watchEvents) do
    if event.id>after then
      out[#out+1]=event
      if #out>=limit then break end
    end
  end
  local cursor=after
  if #watchEvents>0 then cursor=watchEvents[#watchEvents].id end
  return {events=out,cursor=cursor}
end

local function capabilities()
  return {
    protocol=PROTOCOL,
    ops={"ping","info","observe","text.inspect","tasks.inspect","input.press","input.sequence","input.clear","action.status","memory.read","memory.read_batch","memory.read_range","memory.read_range_batch","memory.write","memory.snapshot","memory.diff","watch.add","watch.remove","watch.list","watch.read","events.poll","wait.until","wait.status","wait.cancel","screenshot","state.save","state.load","reset"},
    keys={"A","B","SELECT","START","RIGHT","LEFT","UP","DOWN","R","L"},
    memory_widths={8,16,32},
    max_range_bytes=MAX_RANGE_BYTES,
    frame_synchronized_input=true,
    frame_based_waits=true,
    memory_snapshots=true,
    memory_watches=true,
    text_printer_inspection=true,
    task_inspection=true,
    screenshot=true,
    savestate=true,
  }
end

local function dispatch(req)
  local op=req.op
  local p=req.params or {}
  if op=="ping" then return {pong=true, protocol=PROTOCOL} end
  if op=="info" then return {title=emu:getGameTitle() or "", code=emu:getGameCode() or "", frame=frame(), capabilities=capabilities()} end
  if op=="text.inspect" then return inspect_text(p) end
  if op=="tasks.inspect" then return inspect_tasks(p) end
  if op=="reset" then
    emu:reset(); clear_all_keys(); actionQueue={}
    if activeAction then activeAction.state="cancelled"; activeAction.finished_frame=frame(); activeAction=nil end
    for _,w in pairs(waits) do
      if w.state=="waiting" then w.state="cancelled"; w.finished_frame=frame() end
    end
    return {reset=true}
  end
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
  if op=="memory.read_range" then
    local r=normalize_range(p,1)
    local out=read_range_public(r,true)
    return out
  end
  if op=="memory.read_range_batch" then
    local ranges=normalize_ranges(p.ranges)
    local vals={}
    for _,r in ipairs(ranges) do vals[#vals+1]=read_range_public(r,true) end
    return {ranges=vals}
  end
  if op=="memory.write" then
    local address=assert(tonumber(p.address),"address required")
    local width=tonumber(p.width or 8); local value=assert(tonumber(p.value),"value required")
    write_width(width,address,value)
    return {address=address,width=width,value=read_width(width,address)}
  end
  if op=="memory.snapshot" then
    local name=p.name
    if type(name)~="string" or name=="" then error("snapshot name must be a non-empty string") end
    local ranges=normalize_ranges(p.ranges)
    local snapshot={name=name,frame=frame(),ranges=ranges,data=capture_ranges(ranges)}
    snapshots[name]=snapshot
    return {snapshot=snapshot_public(snapshot,p.include_data==true)}
  end
  if op=="memory.diff" then
    local name=p.name
    local snapshot=snapshots[name]
    if not snapshot then error("unknown snapshot: "..tostring(name)) end
    local result={name=name,snapshot_frame=snapshot.frame,frame=frame(),ranges={}}
    local total=0
    for _,r in ipairs(snapshot.ranges) do
      local before=snapshot.data[r.name]
      local after=read_range_data(r.address,r.length)
      local changes=diff_bytes(before,after,r)
      local changed=0
      for _,change in ipairs(changes) do changed=changed+change.length end
      total=total+changed
      result.ranges[#result.ranges+1]={name=r.name,address=r.address,length=r.length,changed_bytes=changed,changes=changes}
    end
    result.changed_bytes=total
    return {diff=result}
  end
  if op=="watch.add" then
    local name=p.name
    if type(name)~="string" or name=="" then error("watch name must be a non-empty string") end
    local w={name=name,address=integer_param(p.address,"address",0,nil),last_changed_frame=nil,error=nil}
    if p.length~=nil then
      w.length=integer_param(p.length,"length",1,MAX_RANGE_BYTES)
    else
      w.width=tonumber(p.width or 8)
    end
    local ok,current=pcall(read_watch_value,w)
    if not ok then error(tostring(current)) end
    w.last=current
    watches[name]=w
    return {watch=watch_public(w,true)}
  end
  if op=="watch.remove" then
    local name=p.name
    local removed=watches[name]~=nil
    watches[name]=nil
    return {name=name,removed=removed}
  end
  if op=="watch.list" or op=="watch.read" then
    local out={}
    if op=="watch.read" and p.names then
      if type(p.names)~="table" then error("names must be an array") end
      for _,name in ipairs(p.names) do
        local w=watches[name]
        if not w then error("unknown watch: "..tostring(name)) end
        local ok,current=pcall(read_watch_value,w)
        if not ok then error(tostring(current)) end
        w.last=current
        out[#out+1]=watch_public(w,true)
      end
    else
      for _,name in ipairs(sorted_watch_names()) do
        local w=watches[name]
        if op=="watch.read" then
          local ok,current=pcall(read_watch_value,w)
          if not ok then error(tostring(current)) end
          w.last=current
        end
        out[#out+1]=watch_public(w,op=="watch.read")
      end
    end
    return {watches=out}
  end
  if op=="events.poll" then return poll_watch_events(p.after,p.limit) end
  if op=="wait.until" then
    if type(p.condition)~="table" then error("condition must be an object") end
    local timeout=integer_param(p.timeout_frames or 300,"timeout_frames",1,nil)
    local w={id=nextWaitId,state="waiting",condition=p.condition,started_frame=frame(),deadline_frame=frame()+timeout}
    nextWaitId=nextWaitId+1
    local kind=p.condition.type or p.condition.kind
    if kind=="memory_changed" then
      local address=integer_param(p.condition.address,"address",0,nil)
      local width=tonumber(p.condition.width or 8)
      w.baseline=read_width(width,address)
    end
    waits[w.id]=w
    local ok,satisfied=pcall(wait_condition_satisfied,w)
    if not ok then waits[w.id]=nil; error(tostring(satisfied)) end
    if satisfied then w.state="done"; w.finished_frame=frame() end
    return {wait=wait_public(w)}
  end
  if op=="wait.status" then
    local w=waits[tonumber(p.id)]
    if not w then error("unknown wait") end
    return {wait=wait_public(w)}
  end
  if op=="wait.cancel" then
    local w=waits[tonumber(p.id)]
    if not w then error("unknown wait") end
    if w.state=="waiting" then w.state="cancelled"; w.finished_frame=frame() end
    return {wait=wait_public(w)}
  end
  if op=="screenshot" then
    local path=p.path or (RUNTIME_DIR.."/mgba-shot.png")
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
    if p.ranges then
      result.ranges={}
      for _,r in ipairs(normalize_ranges(p.ranges)) do result.ranges[#result.ranges+1]=read_range_public(r,true) end
    end
    if p.watches then
      result.watches={}
      for _,name in ipairs(sorted_watch_names()) do
        local w=watches[name]
        local ok,current=pcall(read_watch_value,w)
        if not ok then error(tostring(current)) end
        w.last=current
        result.watches[#result.watches+1]=watch_public(w,true)
      end
    end
    if p.events then
      result.events=poll_watch_events(p.after_event,p.event_limit)
    end
    if p.text then
      result.text=inspect_text(type(p.text)=="table" and p.text or {})
    end
    if p.tasks then
      result.tasks=inspect_tasks(type(p.tasks)=="table" and p.tasks or {})
    end
    if p.screenshot then
      local path=type(p.screenshot)=="string" and p.screenshot or string.format("%s/mgba-frame-%d.png",RUNTIME_DIR,frame())
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
  update_watches()
  update_waits()
end)

server=socket.bind("127.0.0.1",PORT)
if server then
  local ok=server:listen()
  if ok then server:add("received",accept_client) end
end

local f=io.open(READY_FILE,"w")
if f then f:write(PROTOCOL.." port="..tostring(PORT).."\n"); f:close() end
