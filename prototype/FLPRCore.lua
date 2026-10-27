-- Experimental campaign protocol. Never autoloaded or installed by the TP2.
-- Native lifecycle, removal, and delivery observations are supplied by the host.
-- All world observations/receipts below must come from its verified adapters.
local Core = {}
Core.SCHEMA = "flir-campaign-prototype-v1"
Core.MAX_UNITS = 4096
-- Held means an exact, operation-bound instance was placed on a living
-- creature target. Its later lootability is not claimed; only deliveries
-- can be held, and only for creature-kind targets.
-- Protected is source-only: a live pre-access guard is bound to an unvisited
-- original. It is not evidence that the original has already been removed.
-- Parked is delivery-only: a reward on its way whose observation could not go
-- on (the party left its place, or a save held it on its way). Its journal
-- closed with the counts before it (flpr_b<index>); the next visit of its
-- place tells whether it arrived (Controller:confirm_parked).
Core.PHASE = { pending = 0, in_flight = 1, confirmed = 2, quarantined = 3, held = 4, protected = 5,
    kept = 6, lost = 7, parked = 8 }
Core.CREATURE_TARGET_KINDS = { actor = true, ["cre-template"] = true,
    ["creature-target"] = true, group = true }
Core.G = { state = "flpr_state", seed = "flpr_seed", units = "flpr_units",
    sources = "flpr_sources", targets = "flpr_targets", sequence = "flpr_sequence",
    journal = "flpr_jkind", index = "flpr_jindex", nonce = "flpr_jnonce",
    provenance = "flpr_prov", guard = "flpr_guard", settle = "flpr_jsettle" }
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
    local rules = manifest.randomization
    if #slots < #units and not (rules and rules.overflow == true) then return nil, "CAPACITY_SHORTFALL" end
    if rules then
        if type(rules) ~= "table" or type(rules.sources) ~= "table" or type(rules.targets) ~= "table" or
            not integer(rules.loss_percent, 0, 100) or not integer(rules.unrandomized_percent, 0, 100) or
            (rules.overflow ~= nil and type(rules.overflow) ~= "boolean") then
            return nil, "RANDOMIZATION_INVALID"
        end
        local function tier(value)
            return type(value) == "string" and #value > 0 and #value <= 64 and not value:find("[^a-z0-9:_%-]")
        end
        local capacity, required, targets, keep_reserve, excluded_slots = {}, {}, {}, {}, {}
        for _, target in ipairs(manifest.targets) do
            local tiers = rules.targets[target.id]
            if not array(tiers, Core.MAX_UNITS) or #tiers ~= target.capacity then return nil, "TIER_TARGET_INVALID" end
            targets[target.id] = target
            for _, value in ipairs(tiers) do
                if not tier(value) then return nil, "TIER_TARGET_INVALID" end
                capacity[value] = (capacity[value] or 0) + 1
            end
        end
        for _, source in ipairs(manifest.sources) do
            local rule = rules.sources[source.id]
            if type(rule) ~= "table" or not tier(rule.tier) or type(rule.loss_exempt) ~= "boolean" or
                not array(rule.exclude_targets, Core.MAX_UNITS) then return nil, "TIER_SOURCE_INVALID" end
            if rule.reward_variants ~= nil then
                if not array(rule.reward_variants, Core.MAX_UNITS) or #rule.reward_variants == 0 then
                    return nil, "REWARD_VARIANTS_INVALID"
                end
                for _, variant in ipairs(rule.reward_variants) do
                    if type(variant) ~= "string" or #variant < 1 or #variant > 8 or
                        variant:find("[^a-z0-9#_%-]") then return nil, "REWARD_VARIANTS_INVALID" end
                end
            end
            required[rule.tier] = (required[rule.tier] or 0) + source.quantity
            local seen, excluded_tiers = {}, {}
            for _, excluded in ipairs(rule.exclude_targets) do
                local target = type(excluded) == "table" and targets[excluded.id]
                if not target or not integer(excluded.ordinal, 1, target.capacity) then return nil, "TIER_EXCLUSION_INVALID" end
                local key = excluded.id .. "/" .. excluded.ordinal
                if seen[key] then return nil, "TIER_EXCLUSION_INVALID" end
                seen[key] = true
                excluded_slots[key] = true
                local value = rules.targets[excluded.id][excluded.ordinal]
                excluded_tiers[value] = (excluded_tiers[value] or 0) + 1
            end
            -- Conservative capacity bound until original overflow allocation is
            -- connected. A keep decision must never make a new run undeliverable.
            for value, count in pairs(excluded_tiers) do
                local removed = value == rule.tier and source.quantity or 0
                keep_reserve[value] = (keep_reserve[value] or 0) + math.max(0, count - removed)
            end
        end
        if rules.row_filters ~= nil then
            local filters = rules.row_filters
            if type(filters) ~= "table" then return nil, "ROW_FILTER_INVALID" end
            for _, kind in ipairs({"sources", "targets"}) do
                if type(filters[kind]) ~= "table" then return nil, "ROW_FILTER_INVALID" end
                local known = {}
                for _, row in ipairs(manifest[kind]) do
                    known[row.id] = true
                    local rule = filters[kind][row.id]
                    if type(rule) ~= "table" or not integer(rule.chance, 0, 100) or
                        type(rule.group) ~= "string" or #rule.group > 64 or
                        (rule.group ~= "" and not tier(rule.group)) then return nil, "ROW_FILTER_INVALID" end
                end
                for id in pairs(filters[kind]) do if not known[id] then return nil, "ROW_FILTER_INVALID" end end
            end
        end
        for value, count in pairs(required) do
            if not rules.overflow and (capacity[value] or 0) < count then return nil, "TIER_CAPACITY_SHORTFALL" end
            if not rules.overflow and rules.unrandomized_percent > 0 and rules.unrandomized_percent < 100 and
                (capacity[value] or 0) < count + (keep_reserve[value] or 0) then
                return nil, "KEEP_CAPACITY_SHORTFALL"
            end
            if rules.overflow then
                local available = false
                for _, slot in ipairs(slots) do
                    local target = manifest.targets[slot.target]
                    local kind = target.record.kind
                    if rules.targets[target.id][slot.ordinal] == value and
                        (kind == "actor" or kind == "cre-template" or kind == "creature-target" or kind == "container" or kind == "store" or kind == "group") and
                        (rules.unrandomized_percent == 0 or not excluded_slots[target.id .. "/" .. slot.ordinal]) then
                        available = true; break
                    end
                end
                if not available then return nil, "OVERFLOW_TARGET_MISSING" end
            end
        end
    end
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
          source.record.kind == "cre-template" or source.record.kind == "embedded-cre" or
          source.record.kind == "scripted" or source.record.kind == "authored-source")) or
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

-- Only this controller writes its GLOBALs while a game runs, so every value it
-- has read or written is kept. Reading them all from the engine again for each
-- request cost more than a frame per tick in campaigns of a few hundred units
-- (2.7.3.0 looks a GLOBAL up by name, about 20 microseconds in a modded game).
-- Each observation boundary re-reads the state, the journal and a rotating
-- share of the kept values: one changed behind the controller's back still
-- stops the campaign, within seconds.
Core.CACHE_CHECKS = 8

function Controller:_clear_cache()
    self.cache, self.cache_order, self.cache_known, self.cache_cursor, self.hashes = {}, {}, {}, 0, {}
    self.version = (self.version or 0) + 1
end

-- Changes whenever a value this controller reports may have changed: on every
-- write, and when it restores or forgets a campaign.
function Controller:state_version()
    return self.version
end

function Core.new(storage, resolve_revision)
    if type(storage) ~= "table" or type(storage.get) ~= "function" or
        type(storage.set) ~= "function" or type(resolve_revision) ~= "function" then
        return nil, "ADAPTER_INVALID"
    end
    local controller = setmetatable({ storage = storage, resolve_revision = resolve_revision, session = {},
        boundary = 0, issued_boundary = 0, held_requests = {}, parked_requests = {},
        source_guards = {}, guard_requests = {}, guard_removal_requests = {} }, Controller)
    controller:_clear_cache()
    return controller
end

function Controller:_get(key)
    local value = self.cache[key]
    if value ~= nil then return value end
    value = self.storage:get(key)
    if not integer(value, -2147483648, 2147483647) then error("GLOBAL_INVALID") end
    if not self.cache_known[key] then
        self.cache_known[key] = true
        self.cache_order[#self.cache_order + 1] = key
    end
    self.cache[key] = value
    return value
end

function Controller:_set(key, value)
    -- A write that throws, or lands and then throws, leaves no kept value.
    self.cache[key], self.hashes, self.version = nil, {}, self.version + 1
    self.storage:set(key, value)
    if self:_get(key) ~= value then error("GLOBAL_WRITE_FAILED") end
end

function Controller:_check_cached(key)
    local kept = self.cache[key]
    if kept ~= nil and self.storage:get(key) ~= kept then error("GLOBAL_CHANGED") end
end

function Controller:_validate_cache()
    self:_check_cached(G.state)
    self:_check_cached(G.journal)
    local keys = self.cache_order
    local count = #keys
    for _ = 1, math.min(Core.CACHE_CHECKS, count) do
        self.cache_cursor = self.cache_cursor % count + 1
        self:_check_cached(keys[self.cache_cursor])
    end
end

function Controller:_phase(prefix, index)
    -- EEex returns zero for an absent GLOBAL, which
    -- must never turn a lost delivered/suppressed marker into a fresh attempt.
    -- Sources have no held or parked phase. Protected sources require the additive
    -- guard protocol version; legacy saves retain their old phase meanings.
    local stored = self:_get(name(prefix, index))
    if not integer(stored, 1, prefix == "d" and 9 or 7) or (prefix == "d" and stored == 6) or
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
    if self.hashes[prefix] then return self.hashes[prefix] end
    local pieces = {}
    for index = 1, 16 do
        local value = self:_get(hash_name(prefix, index))
        if not integer(value, 0, 65535) then return nil end
        pieces[index] = string.format("%04x", value)
    end
    self.hashes[prefix] = table.concat(pieces)
    return self.hashes[prefix]
end

local function random_draw(seed)
    -- Park-Miller stays exact below 2^53 in LuaJIT/Lua 5.1 doubles. Rejection
    -- avoids modulo bias; this private generator does not alter math.random.
    local state, range = seed, 2147483646
    return function(maximum)
        local cutoff = range - range % maximum
        repeat state = (state * 16807) % 2147483647 until state - 1 < cutoff
        return (state - 1) % maximum + 1
    end
end

local function shuffle(result, draw)
    for index = #result, 2, -1 do
        local selected = draw(index)
        result[index], result[selected] = result[selected], result[index]
    end
    return result
end

local function campaign_assignments(manifest, shape, seed)
    local draw, result, kept = random_draw(seed), {}, {}
    local rules = manifest.randomization
    if not rules then
        for index = 1, #shape.slots do result[index] = index end
        return shuffle(result, draw), kept
    end
    -- Preserve an excluded original and its ident-paired location together.
    -- Loss is a separate choice: remove the original but award no replacement.
    local excluded, pools, filtered_groups = {}, {}, {}
    local filters = rules.row_filters
    local rejected_sources, rejected_targets = {}, {}
    for index, source in ipairs(manifest.sources) do
        local rule = filters and filters.sources[source.id]
        local rejected = source.record.kind ~= "virtual" and rules.unrandomized_percent > 0 and
            draw(100) <= rules.unrandomized_percent
        if not rejected and rule and rule.chance < 100 then rejected = draw(100) > rule.chance end
        if rejected then
            rejected_sources[index] = true
            if rule and rule.group ~= "" then filtered_groups[rule.group] = true end
        end
    end
    if filters then
        for index, target in ipairs(manifest.targets) do
            local rule = filters.targets[target.id]
            if rule.chance < 100 and draw(100) > rule.chance then
                rejected_targets[index] = true
                if rule.group ~= "" then filtered_groups[rule.group] = true end
            end
        end
    end
    for index, source in ipairs(manifest.sources) do
        local rule = filters and filters.sources[source.id]
        if rejected_sources[index] or (rule and filtered_groups[rule.group]) then
            kept[index] = true
            for _, target in ipairs(rules.sources[source.id].exclude_targets) do
                excluded[target.id .. "/" .. target.ordinal] = true
            end
        end
    end
    for index, slot in ipairs(shape.slots) do
        local target = manifest.targets[slot.target]
        local rule = filters and filters.targets[target.id]
        if not excluded[target.id .. "/" .. slot.ordinal] and not rejected_targets[slot.target] and
            not (rule and filtered_groups[rule.group]) then
            local tier = rules.targets[target.id][slot.ordinal]
            pools[tier] = pools[tier] or {}
            pools[tier][#pools[tier] + 1] = index
        end
    end
    local tiers, required = {}, {}
    for index, source in ipairs(manifest.sources) do
        if not kept[index] then
            local tier = rules.sources[source.id].tier
            required[tier] = (required[tier] or 0) + source.quantity
        end
    end
    for tier in pairs(pools) do tiers[#tiers + 1] = tier end
    table.sort(tiers) -- pairs order differs across Lua versions.
    for _, tier in ipairs(tiers) do
        local pool = pools[tier]
        if rules.overflow and #pool < (required[tier] or 0) then
            local eligible = {}
            for _, slot_index in ipairs(pool) do
                local kind = manifest.targets[shape.slots[slot_index].target].record.kind
                if kind == "actor" or kind == "cre-template" or kind == "creature-target" or kind == "container" or kind == "store" or kind == "group" then
                    eligible[#eligible + 1] = slot_index
                end
            end
            if #eligible == 0 then return nil, "OVERFLOW_TARGET_MISSING" end
            -- Original fixes.tpa permits several rewards at an ordinary
            -- location. Each reward still owns its separate delivery state.
            while #pool < required[tier] do pool[#pool + 1] = eligible[draw(#eligible)] end
        end
        shuffle(pool, draw)
    end
    for index, unit in ipairs(shape.units) do
        local rule = rules.sources[manifest.sources[unit.source].id]
        if kept[unit.source] then result[index] = -1
        elseif not rule.loss_exempt and rules.loss_percent > 0 and draw(100) <= rules.loss_percent then
            result[index] = 0
        else
            local pool = pools[rule.tier]
            if not pool or #pool == 0 then return nil, "TIER_CAPACITY_SHORTFALL" end
            result[index] = table.remove(pool)
        end
    end
    local rewards = {}
    for index, unit in ipairs(shape.units) do
        local source = manifest.sources[unit.source]
        local variants = rules.sources[source.id].reward_variants
        if variants and result[index] > 0 then
            rewards[index] = copy(source.record.record)
            rewards[index].resref = variants[draw(#variants)]
        end
    end
    return result, kept, rewards
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
    local assignments, kept, rewards = campaign_assignments(frozen, shape, seed)
    if not assignments then return nil, kept end
    shape.rewards = rewards or {}
    self.session, self.boundary, self.issued_boundary = {}, 0, 0
    self.held_requests, self.parked_requests = {}, {}
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
    for index, source in ipairs(frozen.sources) do
        -- Original blank rows deliberately add stock; they have no original
        -- to suppress or to leave in place under the keep-originals option.
        self:_set_phase("s", index, kept[index] and Core.PHASE.kept or
            (source.record.kind == "virtual" and Core.PHASE.confirmed or Core.PHASE.pending))
    end
    for index = 1, #shape.units do
        self:_set(name("a", index), assignments[index])
        self:_set_phase("d", index, assignments[index] == -1 and Core.PHASE.kept or
            (assignments[index] == 0 and Core.PHASE.lost or 0))
        self:_set(name("n", index), 0)
    end
    self:_set(G.state, 2) -- publish only after all campaign fields round-trip
    self.manifest, self.shape = frozen, shape
    return true
end

function Controller:restore()
    self.manifest, self.shape = nil, nil
    self.session = {}
    self.held_requests, self.parked_requests = {}, {}
    self:_clear_guards()
    self:_clear_cache()
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
    local expected, kept, rewards = campaign_assignments(frozen, shape, self:_get(G.seed))
    if not expected then return nil, kept end
    shape.rewards = rewards or {}
    local provenance = self:_get(G.provenance)
    if provenance ~= 0 and provenance ~= 1 then return nil, "PROVENANCE_VERSION_INVALID" end
    local guard = self:_get(G.guard)
    if guard ~= 0 and guard ~= 1 then return nil, "GUARD_VERSION_INVALID" end
    local held_nonces = {}
    local pending_sources, pending_rewards = 0, 0
    for index = 1, #frozen.sources do
        local phase = self:_phase("s", index)
        if kept[index] then
            if phase ~= Core.PHASE.kept then return nil, "SOURCE_STATE_INVALID" end
        elseif not integer(phase, 0, 3) and not
            (phase == Core.PHASE.protected and guard == 1 and guard_source(frozen.sources[index])) then
            return nil, "SOURCE_STATE_INVALID"
        end
        if phase == 1 then pending_sources = pending_sources + 1 end
    end
    for index, unit in ipairs(shape.units) do
        local phase = self:_phase("d", index)
        local disposition = expected[index] == -1 and Core.PHASE.kept or
            (expected[index] == 0 and Core.PHASE.lost or nil)
        if self:_get(name("a", index)) ~= expected[index] or (disposition and phase ~= disposition) or
            (not disposition and not integer(phase, 0, 4) and phase ~= Core.PHASE.parked) then
            return nil, "ASSIGNMENT_INVALID"
        end
        if phase == Core.PHASE.held and not creature_target(frozen, shape, expected[index]) then
            return nil, "ASSIGNMENT_INVALID"
        end
        -- Other units' counts are never read: only a parked unit has them.
        if phase == Core.PHASE.parked and not integer(self:_get(name("b", index)), 1, 2147483647) then
            return nil, "PARKED_EVIDENCE_INVALID"
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
        if phase > 0 and not disposition and source_phase ~= Core.PHASE.confirmed and source_phase ~= Core.PHASE.protected then
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
        -- Phase 2 (or held or parked, for deliveries) means the observation was
        -- committed just before a save cut off journal clearing. Restore only
        -- cleans that journal; never repeats.
        if phase ~= 1 and phase ~= 2 and phase ~= 3 and phase ~= Core.PHASE.held and
            not (journal == 2 and phase == Core.PHASE.parked) then
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
    self.held_requests, self.parked_requests = {}, {}
    self:_clear_guards()
    self:_clear_cache()
    self.boundary, self.issued_boundary = 0, 0
end

function Controller:on_observation_boundary()
    self.boundary = self.boundary + 1
    self:_validate_cache()
end

function Controller:_active()
    return self.manifest ~= nil and self:_get(G.state) == 2 and
        self:_read_hash("r") == self.manifest.revision
end

function Controller:assignments()
    if not self:_active() then return nil, "INACTIVE" end
    local result = {}
    for index, unit in ipairs(self.shape.units) do
        local selected = self:_get(name("a", index))
        local slot = self.shape.slots[selected]
        if not slot and selected ~= 0 and selected ~= -1 then return nil, "ASSIGNMENT_INVALID" end
        result[index] = { unit = index, source = unit.source, copy = unit.copy,
            source_id = self.manifest.sources[unit.source].id,
            target_id = slot and self.manifest.targets[slot.target].id, ordinal = slot and slot.ordinal,
            source_phase = self:_phase("s", unit.source), delivery_phase = self:_phase("d", index) }
        if self.manifest.randomization then
            result[index].tier = self.manifest.randomization.sources[result[index].source_id].tier
            result[index].outcome = selected == -1 and "kept" or (selected == 0 and "lost" or "deliver")
        end
        if self.shape.rewards[index] then result[index].payload = copy(self.shape.rewards[index]) end
    end
    -- Every table in the result is new; the caller cannot reach campaign state.
    return result
end

function Controller:source_is_kept(index)
    return self:_active() and self:_phase("s", index) == Core.PHASE.kept
end

function Controller:reward_payload(index)
    if not self:_active() then return nil, "INACTIVE" end
    if not integer(index, 1, #self.shape.units) then return nil, "INDEX_INVALID" end
    local source = self.manifest.sources[self.shape.units[index].source]
    return copy(self.shape.rewards[index] or source.record.record)
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
        -- The adapter's opaque counts at its place before this reward, by which
        -- a later visit can tell whether it arrived (Controller:park). Absent,
        -- the delivery cannot be parked.
        if witness.settle ~= nil and not integer(witness.settle, 1, 2147483647) then
            return nil, "WITNESS_INVALID"
        end
    end
    local nonce = self:_get(G.sequence) + 1
    if not integer(nonce, 1, 2147483647) then return nil, "NONCE_EXHAUSTED" end
    self:_set(G.journal, -1)
    self:_set(G.sequence, nonce)
    self:_set(G.index, index)
    self:_set(G.nonce, nonce)
    if kind == 2 then self:_set(G.settle, witness.settle or 0) end
    self:_write_hash("w", witness.identity)
    self:_set_phase(prefix, index, 1)
    self:_set(G.journal, kind)
    self.issued_boundary = self.boundary
    local result = { kind = kind == 1 and "suppress" or "deliver", index = index,
        session = self.session, mode = "execute-once",
        nonce = nonce, revision = self.manifest.revision, identity = witness.identity,
        source_id = source.id, quantity = kind == 1 and source.quantity or 1,
        payload = kind == 2 and self:reward_payload(index) or copy(source.record.record) }
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
        payload = kind == 2 and self:reward_payload(index) or copy(source.record.record) }
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
    local valid = same_payload(receipt.payload, kind == 2 and self:reward_payload(index) or source.record.record)
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

-- The pending delivery's observation cannot go on: the party left the place
-- of its target while the reward was on its way, and the objects there leave
-- memory with their area (FLPRNat). Nothing is created or repeated. Its counts
-- before it are kept with it as it parks, and its journal closes, so other
-- rewards go on; the next visit of its place settles it (Controller:confirm_parked).
function Controller:park(receipt)
    if not self:_active() then return nil, "INACTIVE" end
    if self.boundary <= self.issued_boundary then return nil, "LATER_OBSERVATION_REQUIRED" end
    local index = self:_get(G.index)
    if self:_get(G.journal) ~= 2 then return nil, "NO_PENDING_DELIVERY" end
    if type(receipt) ~= "table" or receipt.parked ~= 1 or receipt.session ~= self.session or
        receipt.revision ~= self.manifest.revision or receipt.nonce ~= self:_get(G.nonce) or
        receipt.index ~= index or receipt.kind ~= "deliver" or receipt.identity ~= self:_read_hash("w") then
        return nil, "RECEIPT_MISMATCH"
    end
    return self:_park(index)
end

-- A save made while a reward was on its way (journal 2) holds neither its
-- message nor any item pointer: it parks, as for a party that left. Source
-- suppressions and deliveries begun without counts (CIR 1.0.2) stay unproven.
function Controller:park_unproven()
    if not self:_active() then return nil, "INACTIVE" end
    if self:_get(G.journal) ~= 2 then return nil, "PENDING_RELOAD_UNPROVEN" end
    local parked, reason = self:_park(self:_get(G.index))
    if not parked and reason == "PARK_EVIDENCE_MISSING" then return nil, "PENDING_RELOAD_UNPROVEN" end
    return parked, reason
end

-- Counts first, then the phase, then the journal: a cut before the phase
-- leaves the delivery pending in its journal, one after it a journal that
-- restore cleans. Neither repeats the delivery.
function Controller:_park(index)
    local settle = self:_get(G.settle)
    if not integer(settle, 1, 2147483647) then return nil, "PARK_EVIDENCE_MISSING" end
    if self:_phase("d", index) ~= Core.PHASE.in_flight then return nil, "JOURNAL_INVALID" end
    self:_set(name("b", index), settle)
    self:_set_phase("d", index, Core.PHASE.parked)
    self:_set(G.journal, 0)
    return true
end

-- A host whose reward was on its way when it stopped (FLPRHost:_disable): a
-- save made after that cannot settle it, since the world went on without CIR.
-- Its journal stays unproven, as before parking existed.
function Controller:forfeit_settle()
    if self:_active() and self:_get(G.journal) == 2 and self:_get(G.settle) ~= 0 then
        self:_set(G.settle, 0)
    end
end

-- A parked reward is to be settled by an observation only: the request never
-- opens a journal, and one request serves a session (restore forgets it).
function Controller:parked_request(index)
    if not self:_active() then return nil, "INACTIVE" end
    if not integer(index, 1, #self.shape.units) then return nil, "INDEX_INVALID" end
    if self:_phase("d", index) ~= Core.PHASE.parked then return nil, "NOT_PARKED" end
    local settle = self:_get(name("b", index))
    if not integer(settle, 1, 2147483647) then return nil, "PARKED_EVIDENCE_INVALID" end
    local slot = self.shape.slots[self:_get(name("a", index))]
    if not slot then return nil, "ASSIGNMENT_INVALID" end
    local pending = self.parked_requests[index]
    if not pending then
        pending = { token = {} }
        self.parked_requests[index] = pending
    end
    return { kind = "settle-parked", mode = "observe-only", index = index,
        session = self.session, token = pending.token, revision = self.manifest.revision,
        source_id = self.manifest.sources[self.shape.units[index].source].id,
        target_id = self.manifest.targets[slot.target].id, settle = settle,
        payload = self:reward_payload(index) }
end

-- The adapter tells from the counts whether the parked reward arrived, and
-- where it is: a creature target holding it is held, as an observed one is;
-- anywhere else it is confirmed. One that did not arrive is pending again,
-- for a new delivery. One checked phase write is the commit.
function Controller:confirm_parked(receipt)
    if not self:_active() then return nil, "INACTIVE" end
    if type(receipt) ~= "table" or not integer(receipt.index, 1, #self.shape.units) then
        return nil, "RECEIPT_MISMATCH"
    end
    local index, pending = receipt.index, self.parked_requests[receipt.index]
    if self:_phase("d", index) ~= Core.PHASE.parked then return nil, "NOT_PARKED" end
    if not pending or receipt.session ~= self.session or receipt.token ~= pending.token then
        return nil, "RECEIPT_MISMATCH"
    end
    -- Avoid interleaving the settle with the creation/suppression journal.
    if self:_get(G.journal) ~= 0 then return nil, "MUTATION_PENDING" end
    local expected, reason = self:parked_request(index)
    if not expected then return nil, reason end
    for _, field in ipairs({ "kind", "mode", "revision", "source_id", "target_id", "settle" }) do
        if receipt[field] ~= expected[field] then return nil, "RECEIPT_MISMATCH" end
    end
    local holder = receipt.holder
    if not same_payload(receipt.payload, expected.payload) or (receipt.arrived ~= 0 and receipt.arrived ~= 1) or
        (receipt.arrived == 1 and holder ~= "creature" and holder ~= "party" and holder ~= "ground" and
         holder ~= "container") then return nil, "PARKED_SETTLE_UNPROVEN" end
    local creature = creature_target(self.manifest, self.shape, self:_get(name("a", index)))
    if receipt.arrived == 1 and holder == "creature" and not creature then return nil, "PARKED_SETTLE_UNPROVEN" end
    local outcome = receipt.arrived == 0 and Core.PHASE.pending or
        (holder == "creature" and Core.PHASE.held or Core.PHASE.confirmed)
    self.parked_requests[index] = nil
    if outcome == Core.PHASE.held and self:_get(G.provenance) == 1 then
        -- A held reward keeps an identity of its own (restore); the delivery's
        -- nonce closed with its journal. This one is new, published first.
        local nonce = self:_get(G.sequence) + 1
        if not integer(nonce, 2, 2147483647) then return nil, "NONCE_EXHAUSTED" end
        self:_set(G.sequence, nonce)
        self:_set(name("n", index), nonce)
    end
    self:_set_phase("d", index, outcome)
    self:_set(name("b", index), 0)
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
        payload = self:reward_payload(index) }
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
