-- Pure diagnostic for an explicitly bounded lifetime trace. No native access,
-- world mutation, saved receipt, or reconstruction of a pre-existing lifetime.
-- Both CItem destructor entry kinds must be captured from before these watches
-- are armed until the stopped snapshot. Ownership changes and SetResRef are
-- separate evidence gaps; this is not a general campaign delivery adapter.
local Life = {}
local Tracker = {}
Tracker.__index = Tracker
local fields = {"resref", "charge1", "charge2", "charge3", "expiration", "flags"}

local function integer(value, maximum)
    return type(value) == "number" and value >= 0 and value <= maximum and value % 1 == 0
end

local function pointer(value)
    return type(value) == "string" and value:match("^[1-9][0-9]*$") ~= nil
end

local function payload(value, empty_allowed)
    if type(value.resref) ~= "string" or #value.resref > 8
        or (value.resref == "" and not empty_allowed)
        or (value.resref ~= "" and not value.resref:match("^[a-z0-9#_-]+$")) then return nil end
    for _, key in ipairs({"charge1", "charge2", "charge3", "expiration"}) do
        if not integer(value[key], 65535) then return nil end
    end
    if not integer(value.flags, 4294967295) then return nil end
    local copy = {}
    for _, key in ipairs(fields) do copy[key] = value[key] end
    return copy
end

local function same(left, right)
    for _, key in ipairs(fields) do if left[key] ~= right[key] then return false end end
    return true
end

local function fail(self, reason)
    self.state, self.failure = "failed", reason
    return nil, reason
end

function Life.new(Drop, generation, watches)
    if type(Drop) ~= "table" or type(Drop.new) ~= "function" then return nil, "bad-drop-module" end
    local inner, reason = Drop.new(generation, watches)
    if not inner then return nil, reason end
    local self = setmetatable({Drop=Drop,inner=inner,generation=generation,state="open",
        next_sequence=1,death_count=0,death_sequences={},live={},messages={},originals={}}, Tracker)
    for _, watch in ipairs(watches) do
        local original = {request_token=watch.request_token}
        self.originals[watch.request_token] = original
        self.live[watch.original_item_pointer] = {
            kind="old",watch=original,payload=payload(watch.payload,false),started=false,copied=false}
    end
    return self
end

function Tracker:reset(generation, watches)
    if generation == self.generation then return nil, "same-generation" end
    local next_tracker, reason = Life.new(self.Drop, generation, watches)
    if not next_tracker then return nil, reason end
    for key in pairs(self) do self[key] = nil end
    for key, value in pairs(next_tracker) do self[key] = value end
    return true
end

function Tracker:ingest(generation, row)
    if generation ~= self.generation then return nil, "stale-request" end
    if self.state ~= "open" then return nil, self.failure or "closed" end
    if type(row) ~= "table" or not integer(row.sequence,4096) or row.sequence == 0
        or not integer(row.kind,5) or row.kind == 0
        or not pointer(row.a) or not pointer(row.b) then return fail(self,"bad-event") end
    if row.sequence ~= self.next_sequence then return fail(self,"sequence-gap") end
    local destruction = row.kind >= 4
    local copied = payload(row, destruction)
    if not copied then return fail(self,"bad-event-payload") end
    if destruction then
        if row.c ~= "0" or not integer(row.d,4294967295)
            or (row.kind == 4 and row.d ~= 0) then return fail(self,"bad-destructor-event") end
    elseif not pointer(row.c) or not integer(row.d,65535) then
        return fail(self,"bad-event")
    end
    self.next_sequence = self.next_sequence + 1

    if destruction then
        local live = self.live[row.a]
        if live and live.watch then
            if live.kind == "ground" then return fail(self,"watched-ground-item-destroyed") end
            if not live.copied then return fail(self,"watched-item-destroyed-before-copy") end
            if not same(live.payload,copied) then return fail(self,"destroyed-original-payload-mismatch") end
            live.watch.destruction_sequence = row.sequence
        end
        -- An embedded message CItem has a different address from the new ground
        -- CItem. No userdata is dereferenced or inferred from this copied row.
        self.live[row.a] = nil
        return true
    end

    local old
    if row.kind == 1 then
        old = self.live[row.b]
        if old and (old.kind ~= "old" or old.started or old.copied) then
            return fail(self,"old-pointer-reused-without-destruction")
        end
    elseif row.kind == 2 then
        old = self.live[row.a]
        if old and (old.kind ~= "old" or old.copied) then
            return fail(self,"copied-item-lifetime-mismatch")
        end
        if self.live[row.b] then return fail(self,"message-overlaps-live-item") end
    elseif self.live[row.b] then
        -- Copying an original into a message alone does not end its lifetime.
        return fail(self,"new-item-overlaps-live-item")
    end

    local event = {}
    for key, value in pairs(row) do event[key] = value end
    event.sequence = self.death_count + 1
    local accepted, reason = self.inner:ingest(generation,event)
    if not accepted then return fail(self,reason) end
    self.death_count = event.sequence
    self.death_sequences[event.sequence] = row.sequence

    if row.kind == 1 then
        old = old or {kind="old",payload=copied,copied=false}
        old.started = true
        self.live[row.b] = old
    elseif row.kind == 2 then
        old = old or {kind="old",payload=copied,started=false}
        old.copied = true
        self.live[row.a] = old
        self.messages[row.b] = old.watch or false
    else
        local watch = self.messages[row.a]
        self.messages[row.a] = nil
        self.live[row.b] = {kind="ground",watch=watch or nil,payload=copied}
    end
    return true
end

function Tracker:finish(generation, snapshot)
    if generation ~= self.generation then return nil,"stale-request" end
    if self.state ~= "open" then return nil,self.failure or "closed" end
    if type(snapshot) ~= "table" or snapshot.schema ~= "flpr-drop-lifetime-trace-v1"
        or snapshot.installed ~= true or snapshot.stable ~= true or snapshot.armed ~= false
        or snapshot.overflow ~= false or not integer(snapshot.count,4096)
        or snapshot.count ~= self.next_sequence-1 then return fail(self,"unstable-or-incomplete-trace") end
    local candidates, reason = self.inner:finish(generation,
        {count=self.death_count,stable=true,armed=false,overflow=false})
    if not candidates then return fail(self,reason) end
    for _, candidate in ipairs(candidates) do
        for index, sequence in ipairs(candidate.sequences) do
            candidate.sequences[index] = self.death_sequences[sequence]
        end
        local original = self.originals[candidate.request_token]
        candidate.original_destruction_sequence = original.destruction_sequence
    end
    self.state = "finished"
    return candidates
end

return Life
