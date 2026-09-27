-- Experimental campaign protocol. Never autoloaded or installed by the TP2.
-- Native lifecycle, exact removal, and lootable delivery adapters are unproven.
-- All world observations/receipts below must come from a separately verified host.
local Core = {}
Core.SCHEMA = "flir-campaign-prototype-v1"
Core.MAX_UNITS = 4096
-- Held means an exact, operation-bound instance was placed on a living
-- creature target. Its later lootability is not claimed; only deliveries
-- can be held, and only for creature-kind targets.
-- Protected is source-only: a live pre-access guard is bound to an unvisited
-- original. It is not evidence that the original has already been removed.
Core.PHASE = { pending = 0, in_flight = 1, confirmed = 2, quarantined = 3, held = 4, protected = 5 }
Core.CREATURE_TARGET_KINDS = { actor = true, ["cre-template"] = true }
Core.G = { state = "flpr_state", seed = "flpr_seed", units = "flpr_units",
    sources = "flpr_sources", targets = "flpr_targets", sequence = "flpr_sequence",
    journal = "flpr_jkind", index = "flpr_jindex", nonce = "flpr_jnonce",
    provenance = "flpr_prov", guard = "flpr_guard" }
local G = Core.G
local Controller = {}
Controller.__index = Controller

local function integer(value, minimum, maximum)
    return type(value) == "number" and value == math.floor(value)
        and value >= minimum and value <= maximum
end

local function digest(value)
    return type(value) == "string" and #value == 64 and not value:find("[^0-9a-f]")
end

local function array(value, maximum)
    if type(value) ~= "table" then return false end
    local n = #value
    if n > maximum then return false end
    for key in pairs(value) do
        if not integer(key, 1, n) then return false end
    end
    for index = 1, n do if value[index] == nil then return false end end
    return true
end

local function copy(value, seen, depth)
    local kind = type(value)
    if kind == "string" or kind == "boolean" or kind == "nil" then return value end
    if kind == "number" and value == value and value ~= math.huge and value ~= -math.huge then return value end
    if kind ~= "table" or getmetatable(value) ~= nil or (depth or 0) > 32 then error("INVALID_DATA") end
    seen = seen or {}
    if seen[value] then error("CYCLIC_DATA") end
    seen[value] = true
    local result = {}
    for key, item in pairs(value) do
        if type(key) ~= "string" and not integer(key, 1, Core.MAX_UNITS) then error("INVALID_KEY") end
        result[key] = copy(item, seen, (depth or 0) + 1)
    end
    seen[value] = nil
    return result
end

local function name(prefix, index)
    return string.format("flpr_%s%04d", prefix, index)
end

local function hash_name(prefix, index)
    return string.format("flpr_%s%02d", prefix, index)
end

local function payload(record)
    if type(record) ~= "table" or type(record.resref) ~= "string" or
        #record.resref < 1 or #record.resref > 8 or record.resref:find("[^!-~]") or
        not array(record.charges, 3) or #record.charges ~= 3 or
        not integer(record.expiration, 0, 65535) or not integer(record.flags, 0, 4294967295) then
        return false
    end
    for _, value in ipairs(record.charges) do
        if not integer(value, 0, 65535) then return false end
    end
    return true
end

local function same_payload(first, second)
    if not payload(first) or not payload(second) or first.resref ~= second.resref or
        first.expiration ~= second.expiration or first.flags ~= second.flags then return false end
    for i = 1, 3 do if first.charges[i] ~= second.charges[i] then return false end end
    return true
end

local function validate(manifest)
    if type(manifest) ~= "table" or manifest.schema ~= Core.SCHEMA or
        manifest.state ~= "prototype-not-playable" or not digest(manifest.revision) or
        not digest(manifest.bound_revision) or not digest(manifest.world_fingerprint) or
        type(manifest.profile_id) ~= "string" or manifest.profile_id == "" or
        not array(manifest.sources, Core.MAX_UNITS) or #manifest.sources == 0 or
        not array(manifest.targets, Core.MAX_UNITS) or #manifest.targets == 0 then return nil, "MANIFEST_INVALID" end
    local units, slots, prior = {}, {}, ""
    for index, source in ipairs(manifest.sources) do
        if type(source) ~= "table" or type(source.id) ~= "string" or source.id <= prior or
            not integer(source.quantity, 1, Core.MAX_UNITS) or not digest(source.resource_sha256) or
            type(source.record) ~= "table" or source.record.id ~= source.id or
            not payload(source.record.record) then return nil, "SOURCE_INVALID" end
        prior = source.id
        if #units + source.quantity > Core.MAX_UNITS then return nil, "UNIT_LIMIT" end
        for count = 1, source.quantity do units[#units + 1] = { source = index, copy = count } end
    end
    prior = ""
    for index, target in ipairs(manifest.targets) do
        if type(target) ~= "table" or type(target.id) ~= "string" or target.id <= prior or
            not integer(target.capacity, 1, Core.MAX_UNITS) or not digest(target.resource_sha256) or
            type(target.record) ~= "table" or target.record.id ~= target.id then return nil, "TARGET_INVALID" end
        prior = target.id
        if #slots + target.capacity > Core.MAX_UNITS then return nil, "CAPACITY_LIMIT" end
        for ordinal = 1, target.capacity do slots[#slots + 1] = { target = index, ordinal = ordinal } end
    end
    if #slots < #units then return nil, "CAPACITY_SHORTFALL" end
    return { units = units, slots = slots }
end

local function creature_target(manifest, shape, slot_index)
    local slot = shape.slots[slot_index]
    local target = slot and manifest.targets[slot.target]
    return target ~= nil and type(target.record) == "table" and
        Core.CREATURE_TARGET_KINDS[target.record.kind] == true
end

local function guard_source(source)
    return source ~= nil and type(source.record) == "table" and
        ((source.quantity == 1 and (source.record.kind == "container" or
          source.record.kind == "cre-template" or source.record.kind == "embedded-cre")) or
         source.record.kind == "store")
end

local function lease_active(lease)
    if type(lease) ~= "table" or lease.revoked or type(lease.active) ~= "function" then return false end
    local ok, active = pcall(lease.active)
    -- Protection cannot resume under an old lease after an observed gap. A
    -- fresh, exact adapter proof is needed, and never while delivery is pending.
    if not ok or active ~= true then lease.revoked = true; return false end
    return true
end

function Controller:_clear_guards()
    self.source_guards, self.guard_requests, self.guard_removal_requests = {}, {}, {}
end

function Core.new(storage, resolve_revision)
    if type(storage) ~= "table" or type(storage.get) ~= "function" or
        type(storage.set) ~= "function" or type(resolve_revision) ~= "function" then
        return nil, "ADAPTER_INVALID"
    end
    return setmetatable({ storage = storage, resolve_revision = resolve_revision, session = {},
        boundary = 0, issued_boundary = 0, held_requests = {},
        source_guards = {}, guard_requests = {}, guard_removal_requests = {} }, Controller)
end

function Controller:_get(key)
    local value = self.storage:get(key)
    if not integer(value, -2147483648, 2147483647) then error("GLOBAL_INVALID") end
    return value
end

function Controller:_set(key, value)
    self.storage:set(key, value)
    if self:_get(key) ~= value then error("GLOBAL_WRITE_FAILED") end
end

function Controller:_phase(prefix, index)
    -- EEex returns zero for an absent GLOBAL, which
    -- must never turn a lost delivered/suppressed marker into a fresh attempt.
    -- Sources have no held phase. Protected sources require the additive
    -- guard protocol version; legacy saves retain their old phase meanings.
    local stored = self:_get(name(prefix, index))
    if not integer(stored, 1, prefix == "d" and 5 or 6) or
        (prefix == "s" and (stored == 5 or (stored == 6 and self:_get(G.guard) ~= 1))) then
        error("PHASE_MISSING_OR_INVALID")
    end
    return stored - 1
end

function Controller:_set_phase(prefix, index, phase)
    self:_set(name(prefix, index), phase + 1)
end

function Controller:_write_hash(prefix, value)
    for index = 1, 16 do
        self:_set(hash_name(prefix, index), tonumber(value:sub((index - 1) * 4 + 1, index * 4), 16))
    end
end

function Controller:_read_hash(prefix)
    local pieces = {}
    for index = 1, 16 do
        local value = self:_get(hash_name(prefix, index))
        if not integer(value, 0, 65535) then return nil end
        pieces[index] = string.format("%04x", value)
    end
    return table.concat(pieces)
end

local function shuffled_slots(slots, seed)
    local result = {}
    for index = 1, #slots do result[index] = index end
    -- Park-Miller stays exact below 2^53 in LuaJIT/Lua 5.1 doubles. Rejection
    -- avoids modulo bias; this private generator does not alter math.random.
    local state, range = seed, 2147483646
    local function draw(maximum)
        local cutoff = range - range % maximum
        repeat state = (state * 16807) % 2147483647 until state - 1 < cutoff
        return (state - 1) % maximum + 1
    end
    for index = #result, 2, -1 do
        local selected = draw(index)
        result[index], result[selected] = result[selected], result[index]
    end
    return result
end

function Controller:capture(manifest, seed, handoff)
    -- This argument is an explicit trusted-host contract, not a game detector.
    if handoff ~= "confirmed-new-game" then return nil, "NEW_GAME_HANDOFF_REQUIRED" end
    if self:_get(G.state) ~= 0 then return nil, "ALREADY_MARKED" end
    if not integer(seed, 1, 2147483646) then return nil, "SEED_INVALID" end
    local ok, frozen = pcall(copy, manifest)
    if not ok then return nil, "MANIFEST_INVALID" end
    local shape, failure = validate(frozen)
    if not shape then return nil, failure end
    local assignments = shuffled_slots(shape.slots, seed)
    self.session, self.boundary, self.issued_boundary = {}, 0, 0
    self.held_requests = {}
    self:_clear_guards()
    self:_set(G.state, 1) -- incomplete initialization is never resumed/rerolled
    self:_write_hash("r", frozen.revision)
    self:_set(G.seed, seed)
    self:_set(G.units, #shape.units)
    self:_set(G.sources, #frozen.sources)
    self:_set(G.targets, #frozen.targets)
    self:_set(G.sequence, 1)
    self:_set(G.journal, 0)
    self:_set(G.index, 0)
    self:_set(G.nonce, 0)
    self:_set(G.provenance, 1)
    self:_set(G.guard, 1)
    for index = 1, #frozen.sources do self:_set_phase("s", index, 0) end
    for index = 1, #shape.units do
        self:_set(name("a", index), assignments[index])
        self:_set_phase("d", index, 0)
        self:_set(name("n", index), 0)
    end
    self:_set(G.state, 2) -- publish only after all campaign fields round-trip
    self.manifest, self.shape = frozen, shape
    return true
end

function Controller:restore()
    self.manifest, self.shape = nil, nil
    self.session = {}
    self.held_requests = {}
    self:_clear_guards()
    self.boundary, self.issued_boundary = 0, 0
    local state = self:_get(G.state)
    if state == 0 then return nil, "INACTIVE" end
    if state == 1 then return nil, "CAPTURE_INCOMPLETE" end
    if state ~= 2 then return nil, "STATE_INVALID" end
    local revision = self:_read_hash("r")
    if not revision then return nil, "REVISION_INVALID" end
    -- No current-profile fallback is accepted here, even if lookup fails.
    local manifest = self.resolve_revision(revision)
    if manifest == nil then return nil, "REVISION_UNAVAILABLE" end
    local ok, frozen = pcall(copy, manifest)
    if not ok or type(frozen) ~= "table" or frozen.revision ~= revision then return nil, "REVISION_MISMATCH" end
    local shape, failure = validate(frozen)
    if not shape then return nil, failure end
    if self:_get(G.units) ~= #shape.units or self:_get(G.sources) ~= #frozen.sources or
        self:_get(G.targets) ~= #frozen.targets or not integer(self:_get(G.seed), 1, 2147483646) or
        not integer(self:_get(G.sequence), 1, 2147483647) then
        return nil, "CAMPAIGN_SHAPE_MISMATCH"
    end
    local expected = shuffled_slots(shape.slots, self:_get(G.seed))
    local provenance = self:_get(G.provenance)
    if provenance ~= 0 and provenance ~= 1 then return nil, "PROVENANCE_VERSION_INVALID" end
    local guard = self:_get(G.guard)
    if guard ~= 0 and guard ~= 1 then return nil, "GUARD_VERSION_INVALID" end
    local held_nonces = {}
    local pending_sources, pending_rewards = 0, 0
    for index = 1, #frozen.sources do
        local phase = self:_phase("s", index)
        if not integer(phase, 0, 3) and not
            (phase == Core.PHASE.protected and guard == 1 and guard_source(frozen.sources[index])) then
            return nil, "SOURCE_STATE_INVALID"
        end
        if phase == 1 then pending_sources = pending_sources + 1 end
    end
    for index, unit in ipairs(shape.units) do
        local phase = self:_phase("d", index)
        if self:_get(name("a", index)) ~= expected[index] or not integer(phase, 0, 4) then
            return nil, "ASSIGNMENT_INVALID"
        end
        if phase == Core.PHASE.held and not creature_target(frozen, shape, expected[index]) then
            return nil, "ASSIGNMENT_INVALID"
        end
        local nonce = self:_get(name("n", index))
        if nonce ~= 0 then
            if provenance ~= 1 or not integer(nonce, 2, self:_get(G.sequence)) or held_nonces[nonce] or
                not creature_target(frozen, shape, expected[index]) or
                (phase ~= Core.PHASE.held and phase ~= Core.PHASE.confirmed and
                    not (phase == Core.PHASE.in_flight and self:_get(G.journal) == 2 and
                        self:_get(G.index) == index and self:_get(G.nonce) == nonce)) then
                return nil, "HELD_NONCE_INVALID"
            end
            held_nonces[nonce] = true
        elseif provenance == 1 and phase == Core.PHASE.held then
            return nil, "HELD_NONCE_MISSING"
        end
        local source_phase = self:_phase("s", unit.source)
        if phase > 0 and source_phase ~= Core.PHASE.confirmed and source_phase ~= Core.PHASE.protected then
            return nil, "UNSUPPRESSED_REWARD"
        end
        if phase == 1 then pending_rewards = pending_rewards + 1 end
    end
    local journal, index = self:_get(G.journal), self:_get(G.index)
    if journal == 0 then
        if pending_sources ~= 0 or pending_rewards ~= 0 then return nil, "JOURNAL_MISSING" end
    elseif journal == 1 or journal == 2 then
        local maximum = journal == 1 and #frozen.sources or #shape.units
        if not integer(index, 1, maximum) or not digest(self:_read_hash("w")) or
            not integer(self:_get(G.nonce), 1, 2147483647) or self:_get(G.nonce) ~= self:_get(G.sequence) then
            return nil, "JOURNAL_INVALID"
        end
        local phase = self:_phase(journal == 1 and "s" or "d", index)
        -- Phase 2 (or held, for deliveries) means the observation was committed
        -- just before a save cut off journal clearing. Restore only cleans that
        -- journal; never repeats.
        if phase ~= 1 and phase ~= 2 and phase ~= 3 and phase ~= Core.PHASE.held then
            return nil, "JOURNAL_INVALID"
        end
        if pending_sources + pending_rewards ~= (phase == 1 and 1 or 0) then return nil, "JOURNAL_INVALID" end
        if (journal == 1 and pending_rewards ~= 0) or (journal == 2 and pending_sources ~= 0) then return nil, "JOURNAL_INVALID" end
        -- A cut after publishing held but before clearing its journal must
        -- retain the same delivery identity; do not erase conflicting evidence.
        if journal == 2 and self:_get(name("n", index)) ~= 0 and
            self:_get(name("n", index)) ~= self:_get(G.nonce) then return nil, "HELD_NONCE_INVALID" end
        if phase ~= 1 then self:_set(G.journal, 0) end
    else
        return nil, "JOURNAL_INCOMPLETE"
    end
    self.manifest, self.shape = frozen, shape
    return true
end

function Controller:destroy()
    -- Called after the real game destruction boundary by a future host adapter.
    self.manifest, self.shape = nil, nil
    self.session = {}
    self.held_requests = {}
    self:_clear_guards()
    self.boundary, self.issued_boundary = 0, 0
end

function Controller:on_observation_boundary()
    self.boundary = self.boundary + 1
end

function Controller:_active()
    return self.manifest ~= nil and self:_get(G.state) == 2 and
        self:_read_hash("r") == self.manifest.revision
end

function Controller:assignments()
    if not self:_active() then return nil, "INACTIVE" end
    local result = {}
    for index, unit in ipairs(self.shape.units) do
        local slot = self.shape.slots[self:_get(name("a", index))]
        if not slot then return nil, "ASSIGNMENT_INVALID" end
        result[index] = { unit = index, source = unit.source, copy = unit.copy,
            source_id = self.manifest.sources[unit.source].id,
            target_id = self.manifest.targets[slot.target].id, ordinal = slot.ordinal,
            source_phase = self:_phase("s", unit.source), delivery_phase = self:_phase("d", index) }
    end
    return copy(result)
end

function Controller:_begin(kind, index, witness)
    if not self:_active() then return nil, "INACTIVE" end
    if self:_get(G.journal) ~= 0 then return nil, "MUTATION_PENDING" end
    local max = kind == 1 and #self.manifest.sources or #self.shape.units
    if not integer(index, 1, max) then return nil, "INDEX_INVALID" end
    local prefix = kind == 1 and "s" or "d"
    if self:_phase(prefix, index) ~= 0 then return nil, "ALREADY_ATTEMPTED" end
    local source_index = kind == 1 and index or self.shape.units[index].source
    local source = self.manifest.sources[source_index]
    if type(witness) ~= "table" or not digest(witness.identity) then return nil, "WITNESS_INVALID" end
    if kind == 1 then
        if witness.source_id ~= source.id or witness.exact_matches ~= 1 or
            not same_payload(witness.payload, source.record.record) or witness.quantity ~= source.quantity then
            return nil, "SOURCE_MISMATCH"
        end
    else
        local phase = self:_phase("s", source_index)
        if phase == Core.PHASE.protected then
            if not lease_active(self.source_guards[source_index]) then return nil, "SOURCE_GUARD_INACTIVE" end
        elseif phase ~= Core.PHASE.confirmed then return nil, "SOURCE_NOT_SUPPRESSED" end
        local slot = self.shape.slots[self:_get(name("a", index))]
        if not slot or witness.target_id ~= self.manifest.targets[slot.target].id or witness.accepts ~= 1 then
            return nil, "TARGET_UNAVAILABLE"
        end
    end
    local nonce = self:_get(G.sequence) + 1
    if not integer(nonce, 1, 2147483647) then return nil, "NONCE_EXHAUSTED" end
    self:_set(G.journal, -1)
    self:_set(G.sequence, nonce)
    self:_set(G.index, index)
    self:_set(G.nonce, nonce)
    self:_write_hash("w", witness.identity)
    self:_set_phase(prefix, index, 1)
    self:_set(G.journal, kind)
    self.issued_boundary = self.boundary
    local result = { kind = kind == 1 and "suppress" or "deliver", index = index,
        session = self.session, mode = "execute-once",
        nonce = nonce, revision = self.manifest.revision, identity = witness.identity,
        source_id = source.id, quantity = kind == 1 and source.quantity or 1,
        payload = copy(source.record.record) }
    if kind == 2 then
        local slot = self.shape.slots[self:_get(name("a", index))]
        result.target_id, result.ordinal = self.manifest.targets[slot.target].id, slot.ordinal
    end
    return result
end

function Controller:begin_source(index, witness) return self:_begin(1, index, witness) end
function Controller:begin_delivery(index, witness) return self:_begin(2, index, witness) end

function Controller:pending_observation()
    if not self:_active() then return nil, "INACTIVE" end
    local kind, index = self:_get(G.journal), self:_get(G.index)
    if kind ~= 1 and kind ~= 2 then return nil, "NO_PENDING_MUTATION" end
    local source_index = kind == 1 and index or self.shape.units[index].source
    local source = self.manifest.sources[source_index]
    -- This is an observation request, never permission to replay a mutation.
    -- The host must obtain fresh world evidence and retain this exact opaque
    -- Lua session table; serialized receipts and old callbacks cannot match it.
    return { kind = kind == 1 and "suppress" or "deliver", index = index,
        session = self.session, mode = "observe-only", nonce = self:_get(G.nonce),
        revision = self.manifest.revision, identity = self:_read_hash("w"),
        source_id = source.id, quantity = kind == 1 and source.quantity or 1,
        payload = copy(source.record.record) }
end

function Controller:observe(receipt)
    if not self:_active() then return nil, "INACTIVE" end
    if self.boundary <= self.issued_boundary then return nil, "LATER_OBSERVATION_REQUIRED" end
    local kind, index = self:_get(G.journal), self:_get(G.index)
    if kind ~= 1 and kind ~= 2 then return nil, "NO_PENDING_MUTATION" end
    if type(receipt) ~= "table" or receipt.session ~= self.session or receipt.revision ~= self.manifest.revision or
        receipt.nonce ~= self:_get(G.nonce) or receipt.index ~= index or
        receipt.kind ~= (kind == 1 and "suppress" or "deliver") or receipt.identity ~= self:_read_hash("w") then
        return nil, "RECEIPT_MISMATCH"
    end
    local source_index = kind == 1 and index or self.shape.units[index].source
    local source = self.manifest.sources[source_index]
    if kind == 2 and self:_phase("s", source_index) == Core.PHASE.protected and
        not lease_active(self.source_guards[source_index]) then
        -- A pending delivery remains observe-only; losing its source guard
        -- must never clear the journal and authorize a second creation.
        return nil, "SOURCE_GUARD_INACTIVE"
    end
    local valid = same_payload(receipt.payload, source.record.record)
    local outcome = Core.PHASE.quarantined
    if kind == 1 then
        if valid and receipt.removed == source.quantity and receipt.remaining == 0 then
            outcome = Core.PHASE.confirmed
        end
    elseif valid and receipt.created == 1 and receipt.nonce_bound_instance == 1 then
        -- The host must prove THIS nonce's new instance, not an old matching
        -- copy or just a count increase. Confirmation also needs lootability;
        -- a creature target may instead report the instance as held.
        if receipt.lootable == 1 and receipt.held == nil then
            outcome = Core.PHASE.confirmed
        elseif receipt.lootable == 0 and receipt.held == 1 and
            creature_target(self.manifest, self.shape, self:_get(name("a", index))) then
            outcome = Core.PHASE.held
        end
    end
    if outcome == Core.PHASE.held and self:_get(G.provenance) == 1 then
        -- Publish the persistent delivery identity before the held phase.
        -- A partial write cannot authorize another creation on restore.
        self:_set(name("n", index), receipt.nonce)
    end
    self:_set_phase(kind == 1 and "s" or "d", index, outcome)
    self:_set(G.journal, 0)
    if outcome == Core.PHASE.quarantined then return nil, "MUTATION_QUARANTINED" end
    return true
end

-- Guard observations do not mutate the world or use the mutation journal.
-- The trusted adapter must establish protection before source access, retain
-- exact campaign/source binding, and revoke its lease on any lifecycle change.
function Controller:source_guard_request(index)
    if not self:_active() then return nil, "INACTIVE" end
    if not integer(index, 1, #self.manifest.sources) then return nil, "INDEX_INVALID" end
    if self:_get(G.journal) ~= 0 then return nil, "MUTATION_PENDING" end
    local source = self.manifest.sources[index]
    if self:_get(G.guard) ~= 1 or not guard_source(source) then return nil, "SOURCE_GUARD_UNAVAILABLE" end
    local phase = self:_phase("s", index)
    if phase ~= Core.PHASE.pending and phase ~= Core.PHASE.protected then return nil, "SOURCE_NOT_GUARDABLE" end
    local pending = self.guard_requests[index]
    if not pending or pending.phase ~= phase then
        pending = { token = {}, boundary = self.boundary, phase = phase }
        self.guard_requests[index] = pending
    end
    return { kind = "source-guard", mode = "observe-only", index = index,
        session = self.session, token = pending.token, revision = self.manifest.revision,
        source_id = source.id, resource_sha256 = source.resource_sha256,
        quantity = source.quantity, payload = copy(source.record.record) }
end

function Controller:source_guard_active(index)
    if not self:_active() then return nil, "INACTIVE" end
    if not integer(index, 1, #self.manifest.sources) then return nil, "INDEX_INVALID" end
    if self:_phase("s", index) ~= Core.PHASE.protected then return false end
    return lease_active(self.source_guards[index])
end

local function matching_guard_receipt(receipt, expected)
    for _, field in ipairs({ "kind", "mode", "index", "session", "token", "revision",
        "source_id", "resource_sha256", "quantity", "guard_generation" }) do
        if receipt[field] ~= expected[field] then return false end
    end
    return same_payload(receipt.payload, expected.payload)
end

function Controller:confirm_source_guard(receipt)
    if not self:_active() then return nil, "INACTIVE" end
    if type(receipt) ~= "table" or not integer(receipt.index, 1, #self.manifest.sources) then
        return nil, "RECEIPT_MISMATCH"
    end
    local index, pending = receipt.index, self.guard_requests[receipt.index]
    if not pending or receipt.session ~= self.session or receipt.token ~= pending.token or
        self:_phase("s", index) ~= pending.phase then return nil, "RECEIPT_MISMATCH" end
    if self.boundary <= pending.boundary then return nil, "LATER_OBSERVATION_REQUIRED" end
    local expected, reason = self:source_guard_request(index)
    if not expected then return nil, reason end
    if not matching_guard_receipt(receipt, expected) then return nil, "RECEIPT_MISMATCH" end
    local lease = { active = receipt.active, generation = receipt.generation }
    if receipt.pre_access ~= 1 or receipt.exact_source_bound ~= 1 or
        not integer(receipt.generation, 1, 2147483647) or not lease_active(lease) then
        return nil, "SOURCE_GUARD_UNPROVEN"
    end
    -- A checked phase write is the only persistent commit. Publish the lease
    -- afterward: even a write that lands then throws cannot authorize reward
    -- creation without a new, successful observation. Old tokens are consumed.
    self.source_guards[index], self.guard_requests[index], self.guard_removal_requests[index] = nil, nil, nil
    self:_set_phase("s", index, Core.PHASE.protected)
    self.source_guards[index] = lease
    return true
end

function Controller:guarded_source_removal_request(index)
    if not self:_active() then return nil, "INACTIVE" end
    if not integer(index, 1, #self.manifest.sources) then return nil, "INDEX_INVALID" end
    if self:_get(G.journal) ~= 0 then return nil, "MUTATION_PENDING" end
    if self:_phase("s", index) ~= Core.PHASE.protected then return nil, "SOURCE_NOT_PROTECTED" end
    local lease = self.source_guards[index]
    if not lease_active(lease) then return nil, "SOURCE_GUARD_INACTIVE" end
    local source = self.manifest.sources[index]
    local pending = self.guard_removal_requests[index]
    if not pending or pending.lease ~= lease then
        pending = { token = {}, boundary = self.boundary, lease = lease }
        self.guard_removal_requests[index] = pending
    end
    return { kind = "guarded-source-removal", mode = "observe-only", index = index,
        session = self.session, token = pending.token, revision = self.manifest.revision,
        source_id = source.id, resource_sha256 = source.resource_sha256,
        quantity = source.quantity, payload = copy(source.record.record), guard_generation = lease.generation }
end

function Controller:confirm_guarded_source_removal(receipt)
    if not self:_active() then return nil, "INACTIVE" end
    if type(receipt) ~= "table" or not integer(receipt.index, 1, #self.manifest.sources) then
        return nil, "RECEIPT_MISMATCH"
    end
    local index, pending = receipt.index, self.guard_removal_requests[receipt.index]
    if not pending or receipt.session ~= self.session or receipt.token ~= pending.token or
        self.source_guards[index] ~= pending.lease then return nil, "RECEIPT_MISMATCH" end
    if self.boundary <= pending.boundary then return nil, "LATER_OBSERVATION_REQUIRED" end
    local expected, reason = self:guarded_source_removal_request(index)
    if not expected then return nil, reason end
    if not matching_guard_receipt(receipt, expected) then return nil, "RECEIPT_MISMATCH" end
    if receipt.exact_source_event ~= 1 or receipt.removed ~= expected.quantity or receipt.remaining ~= 0 then
        return nil, "GUARDED_REMOVAL_UNPROVEN"
    end
    self.source_guards[index], self.guard_requests[index], self.guard_removal_requests[index] = nil, nil, nil
    self:_set_phase("s", index, Core.PHASE.confirmed)
    return true
end

-- A held reward is already created. This request authorizes observation only;
-- it never changes the delivery phase or opens another mutation journal.
function Controller:held_request(index)
    if not self:_active() then return nil, "INACTIVE" end
    if not integer(index, 1, #self.shape.units) then return nil, "INDEX_INVALID" end
    if self:_phase("d", index) ~= Core.PHASE.held then return nil, "NOT_HELD" end
    if self:_get(G.provenance) ~= 1 then return nil, "HELD_PROVENANCE_UNAVAILABLE" end
    local nonce = self:_get(name("n", index))
    if not integer(nonce, 2, self:_get(G.sequence)) then return nil, "HELD_NONCE_MISSING" end
    local assignment = self:assignments()[index]
    local pending = self.held_requests[index]
    if not pending then
        pending = { token = {}, boundary = self.boundary }
        self.held_requests[index] = pending
    end
    return { kind = "verify-held-drop", mode = "observe-only", index = index,
        session = self.session, token = pending.token, nonce = nonce,
        revision = self.manifest.revision, source_id = assignment.source_id,
        target_id = assignment.target_id,
        payload = copy(self.manifest.sources[assignment.source].record.record) }
end

function Controller:confirm_held(receipt)
    if not self:_active() then return nil, "INACTIVE" end
    if type(receipt) ~= "table" or not integer(receipt.index, 1, #self.shape.units) then
        return nil, "RECEIPT_MISMATCH"
    end
    local index, pending = receipt.index, self.held_requests[receipt.index]
    if self:_phase("d", index) ~= Core.PHASE.held then return nil, "NOT_HELD" end
    if not pending or receipt.session ~= self.session or receipt.token ~= pending.token then
        return nil, "RECEIPT_MISMATCH"
    end
    if self.boundary <= pending.boundary then return nil, "LATER_OBSERVATION_REQUIRED" end
    -- Avoid interleaving completion with the creation/suppression journal.
    if self:_get(G.journal) ~= 0 then return nil, "MUTATION_PENDING" end
    local expected, reason = self:held_request(index)
    if not expected then return nil, reason end
    for _, field in ipairs({ "kind", "mode", "nonce", "revision", "source_id", "target_id" }) do
        if receipt[field] ~= expected[field] then return nil, "RECEIPT_MISMATCH" end
    end
    if not same_payload(receipt.payload, expected.payload) or receipt.exact_transferred_instance ~= 1 or
        receipt.lootable ~= 1 then return nil, "HELD_DROP_UNPROVEN" end
    -- Observation has no world side effect. One checked GLOBAL is the commit;
    -- a save on either side retains held or confirmed, never pending creation.
    self:_set_phase("d", index, Core.PHASE.confirmed)
    self.held_requests[index] = nil
    return true
end

return Core
