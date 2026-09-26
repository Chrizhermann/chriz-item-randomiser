-- Pure, offline correlation of copied death-drop rows. No hooks, engine calls,
-- world writes, Core receipt, or claim about an item's earlier held lifetime.
--
-- Usage:
--   local tracker = assert(Drop.new(generation, watches))
--   -- Each watch: {request_token, owner_pointer, original_item_pointer, slot,
--   --              payload={resref,charge1,charge2,charge3,expiration,flags}}
--   assert(tracker:ingest(generation, row)) -- numbered probe row, in order
--   local candidates = assert(tracker:finish(generation,
--       {count=#rows, stable=true, armed=false, overflow=false}))
--   assert(tracker:reset(next_generation, next_watches))
-- The opaque request token is retained by reference for caller identity checks;
-- payload and event fields are copied. Only finish returns candidates.
-- A current-generation error closes the tracker; reset needs a different token.
-- Stale-generation calls are refused without altering the current generation.

local Drop = {}
local Tracker = {}
Tracker.__index = Tracker

local payload_fields = {"resref", "charge1", "charge2", "charge3", "expiration", "flags"}

local function pointer(value)
    return type(value) == "string" and value:match("^[1-9][0-9]*$") ~= nil
end

local function integer(value, maximum)
    return type(value) == "number" and value >= 0 and value <= maximum and value % 1 == 0
end

local function payload(value)
    if type(value) ~= "table" or type(value.resref) ~= "string"
        or value.resref == "" or #value.resref > 8
        or not value.resref:match("^[a-z0-9#_-]+$") then return nil end
    for _, key in ipairs({"charge1", "charge2", "charge3", "expiration"}) do
        if not integer(value[key], 65535) then return nil end
    end
    if not integer(value.flags, 4294967295) then return nil end
    local copy = {}
    for _, key in ipairs(payload_fields) do copy[key] = value[key] end
    return copy
end

local function same_payload(left, right)
    for _, key in ipairs(payload_fields) do
        if left[key] ~= right[key] then return false end
    end
    return true
end

local function clone(value, seen)
    if type(value) ~= "table" then return value end
    seen = seen or {}
    if seen[value] then return seen[value] end
    local copy = {}
    seen[value] = copy
    for key, item in pairs(value) do copy[key] = clone(item, seen) end
    return copy
end

local function prepare(generation, watches)
    if not (type(generation) == "table" or (type(generation) == "string" and generation ~= "")) or type(watches) ~= "table"
        then return nil, "bad-request" end
    local count, maximum = 0, 0
    for key in pairs(watches) do
        if not integer(key, 256) or key == 0 then return nil, "bad-request" end
        count = count + 1
        if key > maximum then maximum = key end
    end
    if count == 0 or count ~= maximum then return nil, "sparse-watches" end
    local prepared = {}
    local by_old, by_slot, by_token = {}, {}, {}
    for index = 1, count do
        local watch = watches[index]
        if type(watch) ~= "table" or not (type(watch.request_token) == "table"
            or (type(watch.request_token) == "string" and watch.request_token ~= ""))
            or not pointer(watch.owner_pointer)
            or not pointer(watch.original_item_pointer) or not integer(watch.slot, 65535)
            or by_old[watch.original_item_pointer] or by_token[watch.request_token] then
            return nil, "bad-watch"
        end
        local copied_payload = payload(watch.payload)
        if not copied_payload then return nil, "bad-watch-payload" end
        local slot_key = watch.owner_pointer .. "/" .. watch.slot
        if by_slot[slot_key] then return nil, "ambiguous-watch-slot" end
        local row = {request_token=watch.request_token, owner_pointer=watch.owner_pointer,
            original_item_pointer=watch.original_item_pointer, slot=watch.slot,
            payload=copied_payload, stage=0}
        prepared[index] = row
        by_old[row.original_item_pointer] = row
        by_slot[slot_key] = row
        by_token[watch.request_token] = row
    end
    return {generation=generation, watches=prepared, by_old=by_old, by_slot=by_slot,
        by_token=by_token, by_message={}, other_old={}, other_message={},
        candidate_items={}, completed_messages={}, next_sequence=1, state="open"}
end

local function fail(self, reason)
    self.state = "failed"
    self.failure = reason
    return nil, reason
end

function Drop.new(generation, watches)
    local state, reason = prepare(generation, watches)
    if not state then return nil, reason end
    return setmetatable(state, Tracker)
end

function Tracker:reset(generation, watches)
    if generation == self.generation then return nil, "same-generation" end
    local state, reason = prepare(generation, watches)
    if not state then return nil, reason end
    for key in pairs(self) do self[key] = nil end
    for key, value in pairs(state) do self[key] = value end
    return true
end

function Tracker:ingest(generation, event)
    if generation ~= self.generation then return nil, "stale-request" end
    if self.state ~= "open" then return nil, self.failure or "closed" end
    if type(event) ~= "table" or not integer(event.sequence, 256)
        or not integer(event.kind, 3) or event.kind < 1
        or not pointer(event.a) or not pointer(event.b) or not pointer(event.c)
        or not integer(event.d, 65535) then return fail(self, "bad-event") end
    local copied_payload = payload(event)
    if not copied_payload then return fail(self, "bad-event-payload") end
    if event.sequence ~= self.next_sequence then return fail(self, "sequence-gap") end
    self.next_sequence = self.next_sequence + 1
    local kind, a, b, c = event.kind, event.a, event.b, event.c
    if kind == 1 then
        local watch = self.by_old[b]
        local slot_watch = self.by_slot[a .. "/" .. event.d]
        if self.by_message[b] or self.other_message[b] or self.candidate_items[b] then
            return fail(self, "ambiguous-old-pointer") end
        if watch and watch.stage < 2 then
            if watch.stage ~= 0 or slot_watch ~= watch or not same_payload(watch.payload, copied_payload)
                then return fail(self, "watched-removal-mismatch") end
            watch.stage, watch.container, watch.first = 1, c, event.sequence
        elseif slot_watch and slot_watch.stage < 2 then
            return fail(self, "watched-slot-changed")
        else
            -- The old pointer may be reused only once its constructor copied it.
            if self.other_old[b] then return fail(self, "ambiguous-old-pointer") end
            self.other_old[b] = {container=c, payload=copied_payload}
        end
        return true
    end
    if event.d ~= 0 then return fail(self, "message-slot-mismatch") end
    if kind == 2 then
        if self.candidate_items[a] then return fail(self, "ambiguous-old-pointer") end
        local watch = self.by_old[a]
        local old_at_message = self.by_old[b]
        if (old_at_message and old_at_message.stage < 2) or self.candidate_items[b]
            or self.other_old[b] then return fail(self, "ambiguous-message-pointer") end
        if watch and watch.stage == 1 then
            if c ~= watch.container or not same_payload(watch.payload, copied_payload)
                then return fail(self, "watched-constructor-mismatch") end
            if self.by_message[b] or self.other_message[b] then
                return fail(self, "ambiguous-message-pointer") end
            watch.stage, watch.message, watch.second = 2, b, event.sequence
            self.by_message[b] = watch
        else
            if watch and watch.stage == 0 then return fail(self, "missing-watched-removal") end
            local other = self.other_old[a]
            if watch and not other then return fail(self, "ambiguous-old-pointer-reuse") end
            -- Other lifetimes do not need a valid transfer to prove a watched
            -- lifetime. Their first event only distinguishes pointer reuse.
            if self.by_message[b] or self.other_message[b] then
                return fail(self, "ambiguous-message-pointer") end
            self.other_old[a] = nil
            self.other_message[b] = {container=c, payload=copied_payload}
        end
        return true
    end
    local watch = self.by_message[a]
    if watch then
        if watch.stage ~= 2 or c ~= watch.container or not same_payload(watch.payload, copied_payload)
            then return fail(self, "watched-consumer-mismatch") end
        for _, other in ipairs(self.watches) do
            if other.original_item_pointer == b and other.stage < 2 then
                return fail(self, "new-item-overlaps-live-old-item")
            end
        end
        if self.candidate_items[b] or self.by_message[b] or self.other_message[b]
            or self.other_old[b] then return fail(self, "ambiguous-new-item-pointer") end
        watch.stage, watch.new_item, watch.third = 3, b, event.sequence
        self.by_message[a] = nil
        self.completed_messages[a] = true
        self.candidate_items[b] = true
        return true
    end
    -- An unrelated consumer still cannot allocate on top of a currently live
    -- watched item, message, or confirmed candidate. Ignoring its relationship
    -- must never hide evidence that a candidate address has another lifetime.
    if self.candidate_items[b] or self.by_message[b] or self.other_message[b] or self.other_old[b]
        or (self.by_old[b] and self.by_old[b].stage < 2) then
        return fail(self, "unrelated-new-item-collision")
    end
    local other = self.other_message[a]
    if other then
        self.other_message[a] = nil
        return true
    end
    if self.candidate_items[a] then return fail(self, "ambiguous-message-pointer") end
    if self.completed_messages[a] then return fail(self, "ambiguous-message-reuse") end
    -- An unrelated consumer may precede this request's observation window.
    return true
end

function Tracker:finish(generation, snapshot)
    if generation ~= self.generation then return nil, "stale-request" end
    if self.state ~= "open" then return nil, self.failure or "closed" end
    if type(snapshot) ~= "table" or snapshot.stable ~= true or snapshot.armed ~= false
        or snapshot.overflow ~= false or not integer(snapshot.count, 256)
        or snapshot.count ~= self.next_sequence - 1 then return fail(self, "unstable-or-overflow") end
    local result = {}
    for index, watch in ipairs(self.watches) do
        if watch.stage ~= 3 then return fail(self, "incomplete-watched-chain") end
        result[index] = {request_token=watch.request_token,
            owner_pointer=watch.owner_pointer, original_item_pointer=watch.original_item_pointer,
            slot=watch.slot, payload=clone(watch.payload), container_pointer=watch.container,
            message_pointer=watch.message, new_item_pointer=watch.new_item,
            sequences={watch.first, watch.second, watch.third}}
    end
    self.state = "finished"
    return result
end

return Drop
