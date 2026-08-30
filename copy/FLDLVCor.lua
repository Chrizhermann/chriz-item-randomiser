-- Pure, dependency-injected delivery transaction core.
--
-- This file deliberately contains no EEex / Infinity globals and performs no
-- file I/O.  M_FLDLV.lua supplies the verified engine adapter in game; the
-- Lua 5.3 unit suite supplies an in-memory fake.

FLDLV = FLDLV or {}

local Core = {}
local Controller = {}
Controller.__index = Controller

Core.MANIFEST_SCHEMA = "flir-delivery-manifest-v1"
Core.MANIFEST_BACKEND = "eeex-manifest-v1"
Core.MAX_GLOBAL = 2147483646

Core.PHASE = {
    NONE = 0,
    PREPARED = 1,
    EXECUTING = 2,
    VERIFIED = 3,
    QUARANTINED = 4,
}

Core.REASON = {
    NONE = 0,
    BAD_SLOT = 1,
    ITEM_MISSING = 2,
    ENDPOINT_UNAVAILABLE = 3,
    LOCKED_UNOBSERVABLE = 4,
    FINGERPRINT_MISMATCH = 5,
    TRANSACTION_INVALID = 6,
    EXECUTION_FAILURE = 7,
    ASSIGNMENT_CHANGED = 8,
    ENGINE_FAILURE = 9,
    LOCKED_UNSAFE = 10,
    VARIANT_UNSUPPORTED = 11,
}

Core.GLOBALS = {
    phase = "FLDLVTxPhase",
    token = "FLDLVTxToken",
    unit = "FLDLVTxUnit",
    slot = "FLDLVTxSlot",
    endpoint = "FLDLVTxEnd",
    baseline = "FLDLVTxBase",
    quantity = "FLDLVTxQty",
    variant = "FLDLVTxVar",
    charge1 = "FLDLVTxCh1",
    charge2 = "FLDLVTxCh2",
    charge3 = "FLDLVTxCh3",
    fingerprint1 = "FLDLVTxFp1",
    fingerprint2 = "FLDLVTxFp2",
    fingerprint3 = "FLDLVTxFp3",
    fingerprint4 = "FLDLVTxFp4",
    nonce = "FLDLVTxNonce",
    reason = "FLDLVTxReason",
    sequence = "FLDLVTxSeq",
}

local unpack_values = unpack or table.unpack

local function is_integer(value)
    return type(value) == "number" and value == math.floor(value)
end

function Core.quarantine_global(global_name)
    assert(type(global_name) == "string" and #global_name > 0,
        "assignment global must be a nonempty string")
    local hash = 17
    for index = 1, #global_name do
        hash = (hash * 131 + string.byte(global_name, index)) % 2147483629
    end
    return "FLDLVQ" .. tostring(hash)
end

local function is_flag(value)
    return value == 0 or value == 1
end

local function is_nonempty_string(value)
    return type(value) == "string" and #value > 0
end

local function same_text(first, second)
    return type(first) == "string" and type(second) == "string" and
        string.lower(first) == string.lower(second)
end

local function pack_values(...)
    return { n = select("#", ...), ... }
end

local function sorted_keys(map)
    local keys = {}
    for key in pairs(map) do
        keys[#keys + 1] = key
    end
    table.sort(keys, function(first, second)
        return tostring(first) < tostring(second)
    end)
    return keys
end

local function has_exact_array_keys(value, count)
    if type(value) ~= "table" then
        return false
    end
    local seen = 0
    for key in pairs(value) do
        if not is_integer(key) or key < 1 or key > count then
            return false
        end
        seen = seen + 1
    end
    return seen == count
end

local function validate_fingerprint(fingerprint)
    if not has_exact_array_keys(fingerprint, 4) then
        return false
    end
    local seen = {}
    for index = 1, 4 do
        local word = fingerprint[index]
        if not is_integer(word) or word < 1 or word > Core.MAX_GLOBAL or seen[word] then
            return false
        end
        seen[word] = true
    end
    return true
end

local TOP_LEVEL_KEYS = {
    schema = true,
    backend = true,
    fingerprint = true,
    tokens_by_global = true,
    slots_by_tier_value = true,
    endpoints_by_id = true,
    unit_overrides = true,
    sparse_overrides = true,
}

local ENDPOINT_KINDS = {
    creature = true,
    container = true,
    group = true,
    ground = true,
    legacy_adapter = true,
}

local GROUND_STATIC_POLICIES = {
    ["derived-unique"] = true,
    authored = true,
    ["authored-entrance"] = true,
    ["authored-nearest"] = true,
    ["authored-static"] = true,
}

local function validate_manifest(manifest)
    if type(manifest) ~= "table" then
        return nil, "MANIFEST_SHAPE"
    end
    for key in pairs(manifest) do
        if not TOP_LEVEL_KEYS[key] then
            return nil, "MANIFEST_SHAPE"
        end
    end
    for key in pairs(TOP_LEVEL_KEYS) do
        if manifest[key] == nil then
            return nil, "MANIFEST_SHAPE"
        end
    end
    if manifest.schema ~= Core.MANIFEST_SCHEMA then
        return nil, "MANIFEST_SCHEMA"
    end
    if manifest.backend ~= Core.MANIFEST_BACKEND then
        return nil, "MANIFEST_BACKEND"
    end
    if not validate_fingerprint(manifest.fingerprint) then
        return nil, "MANIFEST_FINGERPRINT"
    end
    if type(manifest.tokens_by_global) ~= "table" or
        type(manifest.slots_by_tier_value) ~= "table" or
        type(manifest.endpoints_by_id) ~= "table" or
        type(manifest.unit_overrides) ~= "table" or
        type(manifest.sparse_overrides) ~= "table" then
        return nil, "MANIFEST_SHAPE"
    end

    local unit_ids = {}
    local item_tiers = {}
    local active_units = {}
    local quarantine_names = {}
    for global_name, token in pairs(manifest.tokens_by_global) do
        if type(global_name) ~= "string" or #global_name < 1 or #global_name > 32 or
            not string.match(global_name, "^[A-Za-z0-9_]+$") or type(token) ~= "table" or
            token.global ~= global_name or not is_nonempty_string(token.unit_id) or
            not is_nonempty_string(token.item_id) or not is_nonempty_string(token.tier) or
            not is_nonempty_string(token.compact_token) or
            not is_nonempty_string(token.item_resref) or #token.item_resref > 8 or
            string.find(token.item_resref, string.char(0), 1, true) ~= nil or
            not is_nonempty_string(token.variant_policy) or not is_flag(token.enabled) or
            token.slot_value ~= nil or token.endpoint_id ~= nil or token.location_id ~= nil then
            return nil, "MANIFEST_TOKENS"
        end
        if token.global ~= "fl" .. token.tier .. "t" .. token.compact_token then
            return nil, "MANIFEST_TOKENS"
        end
        if unit_ids[token.unit_id] then
            return nil, "MANIFEST_TOKENS"
        end
        local quarantine_name = Core.quarantine_global(global_name)
        if quarantine_names[quarantine_name] and
            quarantine_names[quarantine_name] ~= global_name then
            return nil, "MANIFEST_TOKENS"
        end
        quarantine_names[quarantine_name] = global_name
        unit_ids[token.unit_id] = true
        if token.enabled == 1 then
            active_units[token.unit_id] = token.tier
        end
        if item_tiers[token.item_id] and item_tiers[token.item_id] ~= token.tier then
            return nil, "MANIFEST_TOKENS"
        end
        item_tiers[token.item_id] = token.tier
        if not has_exact_array_keys(token.charges, 3) then
            return nil, "MANIFEST_TOKENS"
        end
        for index = 1, 3 do
            local charge = token.charges[index]
            if not is_integer(charge) or charge < 0 or charge > 65535 then
                return nil, "MANIFEST_TOKENS"
            end
        end
    end

    local endpoint_ids = {}
    for endpoint_id, endpoint in pairs(manifest.endpoints_by_id) do
        if not is_nonempty_string(endpoint_id) or type(endpoint) ~= "table" or
            not is_nonempty_string(endpoint.area) or not ENDPOINT_KINDS[endpoint.target_kind] or
            type(endpoint.target_identity) ~= "string" or not is_integer(endpoint.x) or
            endpoint.x < 0 or endpoint.x > 32767 or not is_integer(endpoint.y) or
            endpoint.y < 0 or endpoint.y > 32767 or not is_integer(endpoint.capacity) or
            endpoint.capacity < 1 or endpoint.capacity > 32767 or
            not is_nonempty_string(endpoint.static_policy) or
            not is_nonempty_string(endpoint.fallback_id) or type(endpoint.adapter) ~= "string" or
            not is_flag(endpoint.external_delivery) or not is_flag(endpoint.enabled) then
            return nil, "MANIFEST_ENDPOINTS"
        end
        if endpoint.target_kind == "legacy_adapter" then
            if endpoint.capacity ~= 1 or endpoint.static_policy ~= "legacy-external" or
                not is_nonempty_string(endpoint.target_identity) or
                endpoint.adapter ~= endpoint.target_identity or
                endpoint.external_delivery ~= 1 or endpoint.fallback_id ~= "-" then
                return nil, "MANIFEST_ENDPOINTS"
            end
        elseif endpoint.target_kind == "ground" then
            if endpoint.target_identity ~= "-" or endpoint.fallback_id ~= "-" or
                not GROUND_STATIC_POLICIES[endpoint.static_policy] or
                endpoint.external_delivery ~= 0 then
                return nil, "MANIFEST_ENDPOINTS"
            end
        else
            if not is_nonempty_string(endpoint.target_identity) or
                endpoint.static_policy ~= "runtime-resolve" or
                endpoint.fallback_id == "-" or endpoint.fallback_id == "" or
                endpoint.external_delivery ~= 0 then
                return nil, "MANIFEST_ENDPOINTS"
            end
        end
        endpoint_ids[endpoint_id] = true
    end

    local slot_ids = {}
    for tier, slots in pairs(manifest.slots_by_tier_value) do
        if not is_nonempty_string(tier) or type(slots) ~= "table" then
            return nil, "MANIFEST_SLOTS"
        end
        for slot_value, slot in pairs(slots) do
            if not is_integer(slot_value) or slot_value < 1 or type(slot) ~= "table" or
                not is_nonempty_string(slot.slot_id) or slot.tier ~= tier or
                slot.slot_value ~= slot_value or not is_nonempty_string(slot.endpoint_id) or
                not is_integer(slot.weight) or slot.weight < 1 or
                not is_nonempty_string(slot.progression_band) or not is_flag(slot.enabled) or
                slot.item_id ~= nil or slot.item_resref ~= nil or slot.global ~= nil or
                slot.unit_id ~= nil or slot_ids[slot.slot_id] then
                return nil, "MANIFEST_SLOTS"
            end
            if not endpoint_ids[slot.endpoint_id] then
                return nil, "MANIFEST_SLOTS"
            end
            if slot.enabled == 1 and manifest.endpoints_by_id[slot.endpoint_id].enabled ~= 1 then
                return nil, "MANIFEST_SLOTS"
            end
            slot_ids[slot.slot_id] = slot
        end
    end

    for endpoint_id, endpoint in pairs(manifest.endpoints_by_id) do
        if endpoint.enabled == 1 and endpoint.target_kind ~= "ground" and
            endpoint.target_kind ~= "legacy_adapter" then
            local visited = {}
            local current_id = endpoint_id
            local expected_area = endpoint.area
            local reached_ground = false
            while not reached_ground do
                if visited[current_id] then
                    return nil, "MANIFEST_ENDPOINTS"
                end
                visited[current_id] = true
                local current = manifest.endpoints_by_id[current_id]
                if not current or current.enabled ~= 1 or
                    not same_text(current.area, expected_area) then
                    return nil, "MANIFEST_ENDPOINTS"
                end
                if current.target_kind == "ground" then
                    reached_ground = true
                elseif current.target_kind == "legacy_adapter" or current.fallback_id == "-" then
                    return nil, "MANIFEST_ENDPOINTS"
                else
                    current_id = current.fallback_id
                end
            end
        end
    end

    local override_targets = {}
    for item_id, by_slot in pairs(manifest.sparse_overrides) do
        if not is_nonempty_string(item_id) or type(by_slot) ~= "table" or
            not item_tiers[item_id] then
            return nil, "MANIFEST_OVERRIDES"
        end
        override_targets[item_id] = {}
        for slot_id, endpoint_id in pairs(by_slot) do
            local slot = slot_ids[slot_id]
            local endpoint = manifest.endpoints_by_id[endpoint_id]
            if not slot or slot.enabled ~= 1 or not endpoint or endpoint.enabled ~= 1 or
                item_tiers[item_id] ~= slot.tier or endpoint.target_kind == "group" or
                endpoint.target_kind == "legacy_adapter" or endpoint.external_delivery ~= 0 or
                not same_text(endpoint.area,
                    manifest.endpoints_by_id[slot.endpoint_id].area) then
                return nil, "MANIFEST_OVERRIDES"
            end
            override_targets[item_id][slot_id] = endpoint_id
        end
    end

    local unit_override_targets = {}
    for unit_id, by_slot in pairs(manifest.unit_overrides) do
        if not is_nonempty_string(unit_id) or type(by_slot) ~= "table" or
            not active_units[unit_id] then
            return nil, "MANIFEST_UNIT_OVERRIDES"
        end
        unit_override_targets[unit_id] = {}
        local unit_tier = active_units[unit_id]
        for slot_id, endpoint_id in pairs(by_slot) do
            local slot = slot_ids[slot_id]
            local endpoint = manifest.endpoints_by_id[endpoint_id]
            if not slot or slot.enabled ~= 1 or not endpoint or endpoint.enabled ~= 1 or
                unit_tier ~= slot.tier or endpoint.target_kind == "group" or
                endpoint.target_kind == "legacy_adapter" or endpoint.external_delivery ~= 0 or
                not same_text(endpoint.area,
                    manifest.endpoints_by_id[slot.endpoint_id].area) then
                return nil, "MANIFEST_UNIT_OVERRIDES"
            end
            unit_override_targets[unit_id][slot_id] = endpoint_id
        end
    end

    -- Group files are installer metadata, not a runtime target identity.  The
    -- generated manifest must lower every active unit/group-slot pairing to a
    -- concrete endpoint through unit_overrides.  This also prevents runtime
    -- recovery from selecting a different group member after a save/load.
    for _, slot in pairs(slot_ids) do
        local base_endpoint = manifest.endpoints_by_id[slot.endpoint_id]
        if slot.enabled == 1 and base_endpoint.target_kind == "group" then
            for unit_id, tier in pairs(active_units) do
                if tier == slot.tier and
                    (not unit_override_targets[unit_id] or
                        not unit_override_targets[unit_id][slot.slot_id]) then
                    return nil, "MANIFEST_GROUPS"
                end
            end
        end
    end

    return true
end

Core.validate_manifest = validate_manifest

local REQUIRED_ENGINE_METHODS = {
    "get_global",
    "set_global",
    "get_current_area",
    "is_item_available",
    "choose_variant",
    "observe_endpoint",
    "list_items",
    "execute_delivery",
    "report_error",
}

local function build_indices(manifest)
    local token_globals = sorted_keys(manifest.tokens_by_global)
    local token_selector_by_global = {}
    for selector, global_name in ipairs(token_globals) do
        token_selector_by_global[global_name] = selector
    end

    local unique_units = {}
    for _, token in pairs(manifest.tokens_by_global) do
        unique_units[token.unit_id] = true
    end
    local unit_ids = sorted_keys(unique_units)
    local unit_selector_by_id = {}
    for selector, unit_id in ipairs(unit_ids) do
        unit_selector_by_id[unit_id] = selector
    end

    local endpoint_ids = sorted_keys(manifest.endpoints_by_id)
    local endpoint_selector_by_id = {}
    for selector, endpoint_id in ipairs(endpoint_ids) do
        endpoint_selector_by_id[endpoint_id] = selector
    end

    return {
        token_globals = token_globals,
        token_selector_by_global = token_selector_by_global,
        unit_ids = unit_ids,
        unit_selector_by_id = unit_selector_by_id,
        endpoint_ids = endpoint_ids,
        endpoint_selector_by_id = endpoint_selector_by_id,
    }
end

function Core.new(engine, manifest)
    local valid, code = validate_manifest(manifest)
    if not valid then
        return nil, code
    end
    if type(engine) ~= "table" then
        return nil, "ENGINE_SHAPE"
    end
    for _, method_name in ipairs(REQUIRED_ENGINE_METHODS) do
        if type(engine[method_name]) ~= "function" then
            return nil, "ENGINE_SHAPE"
        end
    end

    local self = setmetatable({}, Controller)
    self.engine = engine
    self.manifest = manifest
    self.indices = build_indices(manifest)
    self.reported_errors = {}
    self.retry_blocked = {}
    return self
end

function Controller:_get_global(name)
    local value = self.engine:get_global(name)
    if not is_integer(value) then
        return 0
    end
    return value
end

function Controller:_set_global(name, value)
    self.engine:set_global(name, value)
end

function Controller:_report_once(code, detail)
    detail = tostring(detail or "")
    local key = tostring(code) .. "\0" .. detail
    if self.reported_errors[key] then
        return
    end
    self.reported_errors[key] = true
    pcall(self.engine.report_error, self.engine, code, detail)
end

function Controller:guarded_callback(label, callback, ...)
    local arguments = pack_values(...)
    local results
    local ok, failure = xpcall(function()
        results = pack_values(callback(unpack_values(arguments, 1, arguments.n)))
    end, debug.traceback)
    if not ok then
        local callback_key = "callback\0" .. tostring(label)
        if not self.reported_errors[callback_key] then
            self.reported_errors[callback_key] = true
            pcall(self.engine.report_error, self.engine, "CALLBACK_ERROR",
                tostring(label) .. ":" .. tostring(failure))
        end
        return false, failure
    end
    return true, unpack_values(results, 1, results.n)
end

function Controller:_fingerprint_matches_globals()
    for index = 1, 4 do
        if self:_get_global(Core.GLOBALS["fingerprint" .. index]) ~=
            self.manifest.fingerprint[index] then
            return false
        end
    end
    return true
end

function Controller:_read_transaction()
    local phase = self:_get_global(Core.GLOBALS.phase)
    if phase == Core.PHASE.NONE then
        return nil
    end
    return {
        phase = phase,
        token_selector = self:_get_global(Core.GLOBALS.token),
        unit_selector = self:_get_global(Core.GLOBALS.unit),
        slot_value = self:_get_global(Core.GLOBALS.slot),
        endpoint_selector = self:_get_global(Core.GLOBALS.endpoint),
        baseline = self:_get_global(Core.GLOBALS.baseline),
        quantity = self:_get_global(Core.GLOBALS.quantity),
        variant_selector = self:_get_global(Core.GLOBALS.variant),
        charges = {
            self:_get_global(Core.GLOBALS.charge1),
            self:_get_global(Core.GLOBALS.charge2),
            self:_get_global(Core.GLOBALS.charge3),
        },
        nonce = self:_get_global(Core.GLOBALS.nonce),
        reason = self:_get_global(Core.GLOBALS.reason),
    }
end

function Controller:_slot_and_base_endpoint(token, slot_value)
    local slots = self.manifest.slots_by_tier_value[token.tier]
    local slot = slots and slots[slot_value] or nil
    if not slot or slot.enabled ~= 1 then
        return nil, nil
    end
    local endpoint_id = slot.endpoint_id
    local unit_overrides = self.manifest.unit_overrides[token.unit_id]
    if unit_overrides and unit_overrides[slot.slot_id] then
        endpoint_id = unit_overrides[slot.slot_id]
    else
        local item_overrides = self.manifest.sparse_overrides[token.item_id]
        if item_overrides and item_overrides[slot.slot_id] then
            endpoint_id = item_overrides[slot.slot_id]
        end
    end
    return slot, endpoint_id
end

function Controller:_endpoint_is_authorized(token, slot_value, endpoint_id)
    local _, cursor = self:_slot_and_base_endpoint(token, slot_value)
    local visited = {}
    while cursor and cursor ~= "-" do
        if visited[cursor] then
            return false
        end
        visited[cursor] = true
        if cursor == endpoint_id then
            return true
        end
        local endpoint = self.manifest.endpoints_by_id[cursor]
        if not endpoint then
            return false
        end
        cursor = endpoint.fallback_id
    end
    return false
end

function Controller:_transaction_identity(transaction)
    if transaction.phase < Core.PHASE.PREPARED or
        transaction.phase > Core.PHASE.QUARANTINED then
        return nil, nil, nil, nil, "phase"
    end
    local global_name = self.indices.token_globals[transaction.token_selector]
    local unit_id = self.indices.unit_ids[transaction.unit_selector]
    local endpoint_id = self.indices.endpoint_ids[transaction.endpoint_selector]
    local token = global_name and self.manifest.tokens_by_global[global_name] or nil
    if not token or token.enabled ~= 1 or token.unit_id ~= unit_id or
        transaction.slot_value < 1 or transaction.baseline < 0 or
        transaction.baseline > Core.MAX_GLOBAL or transaction.quantity ~= 1 or
        transaction.nonce < 1 or transaction.nonce > Core.MAX_GLOBAL or
        transaction.endpoint_selector < 1 then
        return nil, nil, nil, nil, "identity"
    end
    local signature, signature_error = self:_variant_signature(
        token, transaction.variant_selector, false)
    if not signature then
        return nil, nil, nil, nil, signature_error or "variant"
    end
    for index = 1, 3 do
        if transaction.charges[index] ~= signature.charges[index] then
            return nil, nil, nil, nil, "signature"
        end
    end
    local endpoint = endpoint_id and self.manifest.endpoints_by_id[endpoint_id] or nil
    if not endpoint or endpoint.enabled ~= 1 or endpoint.external_delivery ~= 0 or
        not self:_endpoint_is_authorized(token, transaction.slot_value, endpoint_id) then
        return nil, nil, nil, nil, "endpoint"
    end
    if transaction.phase == Core.PHASE.QUARANTINED then
        if transaction.reason == Core.REASON.NONE then
            return nil, nil, nil, nil, "reason"
        end
    elseif transaction.reason ~= Core.REASON.NONE then
        return nil, nil, nil, nil, "reason"
    end
    return global_name, token, endpoint_id, signature
end

function Controller:_set_phase(phase, reason)
    if reason ~= nil then
        self:_set_global(Core.GLOBALS.reason, reason)
    end
    self:_set_global(Core.GLOBALS.phase, phase)
end

function Controller:_clear_transaction()
    -- NONE is the clear commit marker.  A torn scrub is harmless because the
    -- next PREPARED record overwrites every field before publishing its phase.
    self:_set_global(Core.GLOBALS.phase, Core.PHASE.NONE)
    for _, field in ipairs({
        "token",
        "unit",
        "slot",
        "endpoint",
        "baseline",
        "quantity",
        "variant",
        "charge1",
        "charge2",
        "charge3",
        "fingerprint1",
        "fingerprint2",
        "fingerprint3",
        "fingerprint4",
        "nonce",
        "reason",
    }) do
        self:_set_global(Core.GLOBALS[field], 0)
    end
end

function Controller:_next_nonce()
    local current = self:_get_global(Core.GLOBALS.sequence)
    if current < 0 or current >= Core.MAX_GLOBAL then
        current = 0
    end
    local nonce = current + 1
    self:_set_global(Core.GLOBALS.sequence, nonce)
    return nonce
end

function Controller:_persist_transaction(token, slot_value, endpoint_id, baseline,
        variant_selector, reason)
    local token_selector = self.indices.token_selector_by_global[token.global]
    local unit_selector = self.indices.unit_selector_by_id[token.unit_id]
    local endpoint_selector = endpoint_id and
        self.indices.endpoint_selector_by_id[endpoint_id] or 0
    local nonce = self:_next_nonce()

    self:_set_global(Core.GLOBALS.token, token_selector)
    self:_set_global(Core.GLOBALS.unit, unit_selector)
    self:_set_global(Core.GLOBALS.slot, slot_value)
    self:_set_global(Core.GLOBALS.endpoint, endpoint_selector)
    self:_set_global(Core.GLOBALS.baseline, baseline or 0)
    self:_set_global(Core.GLOBALS.quantity, 1)
    self:_set_global(Core.GLOBALS.variant, variant_selector)
    self:_set_global(Core.GLOBALS.charge1, token.charges[1])
    self:_set_global(Core.GLOBALS.charge2, token.charges[2])
    self:_set_global(Core.GLOBALS.charge3, token.charges[3])
    for index = 1, 4 do
        self:_set_global(Core.GLOBALS["fingerprint" .. index],
            self.manifest.fingerprint[index])
    end
    self:_set_global(Core.GLOBALS.nonce, nonce)
    self:_set_global(Core.GLOBALS.reason, reason or Core.REASON.NONE)
    self:_set_global(Core.GLOBALS.phase,
        reason and Core.PHASE.QUARANTINED or Core.PHASE.PREPARED)
    return nonce
end

function Controller:_quarantine_new(token, reason, detail)
    self:_set_global(Core.quarantine_global(token.global), reason)
    self:_report_once("DELIVERY_QUARANTINED", detail)
end

function Controller:_clear_token_quarantine(token)
    local global_name = Core.quarantine_global(token.global)
    if self:_get_global(global_name) ~= 0 then
        self:_set_global(global_name, 0)
    end
end

function Controller:_variant_signature(token, selector, choose)
    if token.variant_policy == "exact" then
        if selector ~= nil and selector ~= 0 then
            return nil, "variant-selector"
        end
        return {
            selector = 0,
            resref = token.item_resref,
            charges = { token.charges[1], token.charges[2], token.charges[3] },
        }
    end
    if token.variant_policy ~= "legacy-random-book" then
        return nil, "variant-policy"
    end

    local lower_resref = string.lower(token.item_resref)
    if #lower_resref < 6 or string.sub(lower_resref, -6) ~= "book08" then
        return nil, "variant-family"
    end
    if choose then
        local ok, chosen = pcall(self.engine.choose_variant, self.engine, token, 6)
        if not ok then
            return nil, "variant-choice"
        end
        selector = chosen
    end
    if not is_integer(selector) or selector < 1 or selector > 6 then
        return nil, "variant-selector"
    end
    local stem = string.sub(token.item_resref, 1, #token.item_resref - 2)
    return {
        selector = selector,
        resref = stem .. string.format("%02d", selector + 2),
        charges = { token.charges[1], token.charges[2], token.charges[3] },
    }
end

function Controller:_count_matching(observation, signature)
    local ok, items = pcall(self.engine.list_items, self.engine, observation)
    if not ok or type(items) ~= "table" then
        return nil, "list-items"
    end
    local count = 0
    for _, item in pairs(items) do
        if type(item) == "table" and same_text(item.resref, signature.resref) and
            type(item.charges) == "table" and
            item.charges[1] == signature.charges[1] and
            item.charges[2] == signature.charges[2] and
            item.charges[3] == signature.charges[3] then
            count = count + 1
        end
    end
    return count
end

function Controller:_observe(endpoint_id, token)
    local endpoint = self.manifest.endpoints_by_id[endpoint_id]
    local ok, observation = pcall(self.engine.observe_endpoint, self.engine,
        endpoint_id, endpoint, token)
    if not ok or type(observation) ~= "table" or
        type(observation.observable) ~= "boolean" or
        type(observation.eligible) ~= "boolean" or
        type(observation.settled) ~= "boolean" or
        type(observation.verifiable) ~= "boolean" then
        return nil, "observe-error"
    end
    return observation
end

function Controller:_current_area_matches(endpoint)
    local ok, current_area = pcall(self.engine.get_current_area, self.engine)
    if not ok or not is_nonempty_string(current_area) then
        return false
    end
    return same_text(current_area, endpoint.area)
end

function Controller:_select_endpoint(base_endpoint_id, token)
    local endpoint_id = base_endpoint_id
    local visited = {}
    while endpoint_id and endpoint_id ~= "-" do
        if visited[endpoint_id] then
            return nil, nil, "cycle"
        end
        visited[endpoint_id] = true
        local endpoint = self.manifest.endpoints_by_id[endpoint_id]
        if not endpoint or endpoint.enabled ~= 1 or endpoint.external_delivery ~= 0 then
            return nil, nil, "invalid"
        end
        if not self:_current_area_matches(endpoint) then
            return nil, nil, "wrong-area"
        end
        local observation, observe_error = self:_observe(endpoint_id, token)
        if not observation then
            return nil, nil, observe_error
        end
        if observation.observable and observation.eligible then
            return endpoint_id, observation
        end
        local reason = string.lower(tostring(observation.reason or ""))
        if not observation.observable then
            if reason == "missing" then
                return nil, nil, "deferred"
            end
            if not observation.settled then
                return nil, nil, "settling"
            end
            return nil, nil, "unobservable"
        end
        if reason ~= "dead" and reason ~= "full" then
            return nil, nil, "unavailable"
        end
        endpoint_id = endpoint.fallback_id
    end
    return nil, nil, "unavailable"
end

function Controller:_submit(transaction, global_name, token, endpoint_id, signature)
    local endpoint = self.manifest.endpoints_by_id[endpoint_id]
    self:_set_phase(Core.PHASE.EXECUTING, Core.REASON.NONE)
    local item = {
        resref = signature.resref,
        charges = {
            signature.charges[1],
            signature.charges[2],
            signature.charges[3],
        },
        quantity = transaction.quantity,
        variant_policy = token.variant_policy,
        variant_selector = signature.selector,
        global = global_name,
        unit_id = token.unit_id,
    }
    local ok, accepted = pcall(self.engine.execute_delivery, self.engine,
        endpoint_id, endpoint, item)
    local execution_failure
    if not ok then
        execution_failure = tostring(accepted)
    elseif accepted ~= true then
        execution_failure = "instant executor rejected delivery"
    end

    -- The executor may throw after the engine has already applied the action.
    -- The only authoritative result is the locked endpoint's exact count.
    self:_continue_transaction(self:_read_transaction(), execution_failure)
    return "executed"
end

function Controller:_start_transaction(global_name, token, slot_value)
    local slot, endpoint_id = self:_slot_and_base_endpoint(token, slot_value)
    if not slot then
        self:_quarantine_new(token, Core.REASON.BAD_SLOT,
            "active slot missing for " .. global_name)
        return "quarantined"
    end
    local endpoint = self.manifest.endpoints_by_id[endpoint_id]
    if endpoint.external_delivery == 1 then
        self:_clear_token_quarantine(token)
        return "external"
    end
    if not self:_current_area_matches(endpoint) then
        return "deferred"
    end

    local selected_id, observation, select_error = self:_select_endpoint(endpoint_id, token)
    if not selected_id then
        if select_error == "wrong-area" or select_error == "settling" or
            select_error == "deferred" then
            return "deferred"
        end
        self:_quarantine_new(token, Core.REASON.ENDPOINT_UNAVAILABLE,
            "endpoint unavailable for " .. global_name)
        return "quarantined"
    end
    local signature, signature_error = self:_variant_signature(token, nil, true)
    if not signature then
        self:_quarantine_new(token, Core.REASON.VARIANT_UNSUPPORTED,
            "variant unavailable: " .. tostring(signature_error))
        return "quarantined"
    end
    local item_ok, item_available = pcall(self.engine.is_item_available,
        self.engine, signature.resref)
    if not item_ok or item_available ~= true then
        self:_quarantine_new(token, Core.REASON.ITEM_MISSING,
            "effective item missing for " .. global_name)
        return "quarantined"
    end
    local baseline, count_error = self:_count_matching(observation, signature)
    if baseline == nil then
        self:_quarantine_new(token, Core.REASON.ENGINE_FAILURE,
            "baseline unavailable: " .. tostring(count_error))
        return "quarantined"
    end

    self:_clear_token_quarantine(token)
    local nonce = self:_persist_transaction(token, slot_value, selected_id, baseline,
        signature.selector, nil)
    local transaction = self:_read_transaction()
    transaction.nonce = nonce
    self:_submit(transaction, global_name, token, selected_id, signature)
    return "started"
end

function Controller:_commit(global_name)
    self:_set_global(global_name, -1)
    self:_clear_transaction()
end

function Controller:_continue_transaction(transaction, execution_failure)
    if not self:_fingerprint_matches_globals() then
        self:_set_phase(Core.PHASE.QUARANTINED, Core.REASON.FINGERPRINT_MISMATCH)
        self:_report_once("FINGERPRINT_MISMATCH", "persisted transaction")
        return
    end

    local global_name, token, endpoint_id, signature, identity_error =
        self:_transaction_identity(transaction)
    if not global_name then
        self:_set_phase(Core.PHASE.QUARANTINED, Core.REASON.TRANSACTION_INVALID)
        self:_report_once("TRANSACTION_INVALID", identity_error)
        return
    end

    -- PREPARED is durable before EXECUTING.  Recovering it proves that the
    -- instant action was never invoked, so it can be cleared and replanned.
    if transaction.phase == Core.PHASE.PREPARED then
        self:_clear_transaction()
        return
    end

    local assignment = self:_get_global(global_name)
    if assignment == -1 then
        self:_clear_transaction()
        return
    end
    if assignment ~= transaction.slot_value then
        self:_set_phase(Core.PHASE.QUARANTINED, Core.REASON.ASSIGNMENT_CHANGED)
        self:_report_once("ASSIGNMENT_CHANGED", global_name)
        return
    end

    -- VERIFIED is written only after an exact baseline delta was observed.
    -- If a save/load lands in this tiny window, the durable proof is enough to
    -- finish the assignment commit without executing or inserting again.
    if transaction.phase == Core.PHASE.VERIFIED then
        self:_commit(global_name)
        return
    end

    if transaction.phase == Core.PHASE.QUARANTINED and
        transaction.reason ~= Core.REASON.LOCKED_UNOBSERVABLE and
        transaction.reason ~= Core.REASON.LOCKED_UNSAFE then
        return
    end

    if not endpoint_id then
        return
    end
    local endpoint = self.manifest.endpoints_by_id[endpoint_id]
    if not self:_current_area_matches(endpoint) then
        return
    end
    local observation, observe_error = self:_observe(endpoint_id, token)
    if not observation or not observation.observable then
        self:_set_phase(Core.PHASE.QUARANTINED,
            Core.REASON.LOCKED_UNOBSERVABLE)
        self:_report_once("LOCKED_ENDPOINT", observe_error or endpoint_id)
        return
    end

    -- A corpse can remain observable and even contain the created item, but
    -- the handover evidence proves that post-death inventory never reaches
    -- the loot pile.  Visibility and exact count are therefore insufficient
    -- unless the adapter also proves the locked endpoint remains lootable.
    if not observation.verifiable then
        self:_set_phase(Core.PHASE.QUARANTINED, Core.REASON.LOCKED_UNSAFE)
        self:_report_once("LOCKED_UNSAFE", endpoint_id)
        return
    end

    local count, count_error = self:_count_matching(observation, signature)
    if count == nil then
        self:_set_phase(Core.PHASE.QUARANTINED, Core.REASON.ENGINE_FAILURE)
        self:_report_once("COUNT_FAILURE", count_error)
        return
    end
    if count >= transaction.baseline + transaction.quantity then
        self:_set_phase(Core.PHASE.VERIFIED, Core.REASON.NONE)
        self:_commit(global_name)
        return
    end

    -- A locked ambiguous transaction is observation-only.  It may later prove
    -- the exact delta, but it must never execute again or switch endpoints.
    if transaction.phase == Core.PHASE.QUARANTINED then
        return
    end

    self.retry_blocked[global_name] = true
    self:_quarantine_new(token, Core.REASON.EXECUTION_FAILURE,
        execution_failure or ("no exact delivery delta at " .. endpoint_id))
    self:_report_once("EXECUTION_FAILURE", execution_failure or endpoint_id)
    self:_clear_transaction()
end

function Controller:poll()
    local transaction = self:_read_transaction()
    if transaction then
        self:_continue_transaction(transaction)
        return
    end

    for _, global_name in ipairs(self.indices.token_globals) do
        local token = self.manifest.tokens_by_global[global_name]
        if token.enabled == 1 then
            local slot_value = self:_get_global(global_name)
            if slot_value > 0 and not self.retry_blocked[global_name] then
                local outcome = self:_start_transaction(global_name, token, slot_value)
                if outcome == "started" then
                    return
                end
            else
                if slot_value <= 0 then
                    self.retry_blocked[global_name] = nil
                    self:_clear_token_quarantine(token)
                end
            end
        end
    end
end

function Controller:on_game_state_destroyed()
    self.retry_blocked = {}
end

FLDLV.Core = Core
return Core
