local manifest_path = assert(arg[1], "manifest path missing")
local mode = arg[2] or "default"
local changed = mode == "changed"
local book = mode == "book"
local synthesized = mode == "synthesized" or book
local variant = mode == "variant"
local adapter_changed = mode == "adapter"
local percent_policy = "policy%flir_manifest_probe%tail"

local environment = {}
setmetatable(environment, { __index = _G })
local chunk, load_error = loadfile(manifest_path, "t", environment)
assert(chunk, load_error)
chunk()

assert(type(environment.FLDLV) == "table", "FLDLV namespace missing")
local manifest = environment.FLDLV.Manifest
assert(type(manifest) == "table", "FLDLV.Manifest missing")

local expected_top_level = {
    schema = true,
    backend = true,
    fingerprint = true,
    tokens_by_global = true,
    slots_by_tier_value = true,
    endpoints_by_id = true,
    sparse_overrides = true,
}
for key in pairs(manifest) do
    assert(expected_top_level[key], "unexpected top-level manifest key: " .. tostring(key))
end
for key in pairs(expected_top_level) do
    assert(manifest[key] ~= nil, "missing top-level manifest key: " .. key)
end

assert(manifest.schema == "flir-delivery-manifest-v1", "wrong manifest schema")
assert(manifest.backend == "eeex-manifest-v1", "wrong manifest backend")

local fingerprint = manifest.fingerprint
assert(type(fingerprint) == "table", "fingerprint is not a table")
for index = 1, 4 do
    local word = fingerprint[index]
    assert(type(word) == "number" and word % 1 == 0, "fingerprint word is not an integer")
    assert(word >= 1 and word <= 2147483646, "fingerprint word outside positive GLOBAL range")
end
for key in pairs(fingerprint) do
    assert(type(key) == "number" and key >= 1 and key <= 4 and key % 1 == 0,
        "fingerprint contains an unexpected key")
end
for first = 1, 4 do
    for second = first + 1, 4 do
        assert(fingerprint[first] ~= fingerprint[second], "fingerprint words reused one unsalted hash")
    end
end

local function assert_numeric_flags(value, path, seen)
    if type(value) ~= "table" then
        assert(type(value) ~= "boolean", "boolean persisted in manifest at " .. path)
        return
    end
    seen = seen or {}
    assert(not seen[value], "cycle in manifest at " .. path)
    seen[value] = true
    for key, child in pairs(value) do
        if key == "enabled" or key == "external_delivery" then
            assert(type(child) == "number" and (child == 0 or child == 1),
                "persisted flag is not numeric at " .. path .. "." .. tostring(key))
        end
        assert_numeric_flags(child, path .. "." .. tostring(key), seen)
    end
    seen[value] = nil
end
assert_numeric_flags(manifest, "manifest")

local tokens = manifest.tokens_by_global
assert(type(tokens) == "table", "tokens_by_global is not a table")
local token_a = tokens.fl1t1
local token_b = tokens.fl1t2
assert(type(token_a) == "table" and type(token_b) == "table", "expected token globals missing")
assert(token_a.global == "fl1t1" and token_a.unit_id == "core:unit-a", "token A identity mismatch")
assert(token_a.item_resref == "synt0001", "token A resref mismatch")
assert(token_a.tier == "1" and token_a.compact_token == "1", "token A compact mismatch")
local expected_a_policy = book and "legacy-random-book" or (variant and percent_policy or "exact")
assert(token_a.variant_policy == expected_a_policy and token_a.enabled == 1,
    "token A policy mismatch")
assert(type(token_a.charges) == "table" and token_a.charges[1] == 3 and token_a.charges[2] == 2,
    "token A charges missing")
assert(token_a.charges[3] == (changed and 2 or 1), "token A changed charge mismatch")
assert(token_b.global == "fl1t2" and token_b.unit_id == "core:unit-b", "token B identity mismatch")
assert(token_b.item_resref == "synt0002", "token B resref mismatch")
assert(token_b.tier == "1" and token_b.compact_token == "2", "token B compact mismatch")
assert(token_b.charges[1] == 0 and token_b.charges[2] == 4 and token_b.charges[3] == 0,
    "token B charges mismatch")
if synthesized then
    assert(token_a.item_id:match("^flir:item%-%d+$"), "token A did not receive a generated item ID")
    assert(token_b.item_id:match("^flir:item%-%d+$"), "token B did not receive a generated item ID")
    assert(token_a.item_id ~= token_b.item_id, "distinct synthesized resources collapsed to one item")
    local token_x = assert(tokens.fl1tx0, "extra token X missing")
    local token_y = assert(tokens.fl1ty0, "extra token Y missing")
    assert(token_x.unit_id == "core:unit-x" and token_y.unit_id == "core:unit-y",
        "extra token stable unit identity mismatch")
    assert(token_x.item_id == token_a.item_id and token_y.item_id == token_a.item_id,
        "extra tokens did not reuse the base logical item")
    for _, extra in ipairs({ token_x, token_y }) do
        assert(extra.charges[1] == 3 and extra.charges[2] == 2 and extra.charges[3] == 1,
            "extra token did not inherit base-token charges")
        assert(extra.variant_policy == expected_a_policy,
            "extra token did not inherit the base logical item's variant policy")
    end
else
    assert(token_a.item_id == "core:item-a", "token A item ID mismatch")
    assert(token_b.item_id == "core:item-b", "token B item ID mismatch")
    assert(tokens.fl1tx0 == nil and tokens.fl1ty0 == nil, "unexpected extra token emitted")
end
assert(tokens.fl1t10 == nil, "unexpected token/global join emitted")
for _, token in pairs(tokens) do
    assert(token.slot_value == nil and token.endpoint_id == nil and token.location_id == nil,
        "token row contains a prejoined location")
end

local tier_slots = assert(manifest.slots_by_tier_value["1"], "tier slot map missing")
local slot_2 = assert(tier_slots[2], "numeric slot 2 missing")
local slot_10 = assert(tier_slots[10], "numeric slot 10 missing")
local slot_adapter = assert(tier_slots[77], "legacy adapter slot missing")
local slot_disabled = assert(tier_slots[99], "disabled numeric slot missing")
assert(slot_2.slot_id == "core:slot-b" and slot_2.endpoint_id == "core:container-b",
    "slot 2 identity mismatch")
assert(slot_10.slot_id == "core:slot-a" and slot_10.endpoint_id == "core:container-a",
    "slot 10 identity mismatch")
assert(slot_2.slot_value == 2 and slot_10.slot_value == 10, "slot numeric values mismatch")
assert(slot_2.enabled == 1 and slot_10.enabled == 1, "slot flags mismatch")
assert(slot_adapter.slot_id == "core:slot-adapter" and
    slot_adapter.endpoint_id == "core:adapter-a" and slot_adapter.enabled == 1,
    "legacy adapter slot mismatch")
assert(slot_disabled.slot_id == "core:slot-disabled" and
    slot_disabled.endpoint_id == "core:ground-disabled" and slot_disabled.enabled == 0,
    "disabled slot tombstone mismatch")
for _, slot in pairs(tier_slots) do
    assert(slot.item_id == nil and slot.item_resref == nil and slot.global == nil and slot.unit_id == nil,
        "slot row contains a prejoined item")
end

local endpoints = manifest.endpoints_by_id
local container_a = assert(endpoints["core:container-a"], "container endpoint missing")
local expected_identity = 'Container "A"\\B]]%flir_manifest_probe%' .. string.char(10, 9, 1)
local function hex_bytes(value)
    return (value:gsub(".", function(byte)
        return string.format("%02x", string.byte(byte))
    end))
end
assert(container_a.target_identity == expected_identity,
    "Lua string round-trip changed target identity: expected=" .. hex_bytes(expected_identity) ..
    " actual=" .. hex_bytes(container_a.target_identity))
assert(container_a.area == "area-a" and container_a.target_kind == "container",
    "container endpoint shape mismatch")
assert(container_a.fallback_id == "core:ground-a" and container_a.enabled == 1,
    "container fallback mismatch")
assert(container_a.adapter == "-" and container_a.external_delivery == 0,
    "ordinary adapter sentinel was misclassified as external delivery")
local ground_a = assert(endpoints["core:ground-a"], "ground fallback missing")
assert(ground_a.x == 100 and ground_a.y == 200 and ground_a.capacity == 2,
    "ground endpoint numeric fields mismatch")
assert(ground_a.adapter == "-" and ground_a.external_delivery == 0,
    "ordinary ground endpoint was misclassified as external delivery")
local container_b = assert(endpoints["core:container-b"], "container B endpoint missing")
assert(container_b.adapter == (adapter_changed and percent_policy or "-"),
    "endpoint adapter did not round-trip")
assert(container_b.external_delivery == 0,
    "ordinary endpoint with metadata adapter was misclassified as external delivery")
local disabled_endpoint = assert(endpoints["core:ground-disabled"], "disabled endpoint missing")
assert(disabled_endpoint.enabled == 0 and disabled_endpoint.external_delivery == 0,
    "disabled endpoint tombstone mismatch")
local adapter_endpoint = assert(endpoints["core:adapter-a"], "legacy adapter endpoint missing")
assert(adapter_endpoint.target_kind == "legacy_adapter" and
    adapter_endpoint.adapter == "legacy-hook" and adapter_endpoint.external_delivery == 1 and
    adapter_endpoint.enabled == 1, "legacy adapter endpoint semantics mismatch")

if synthesized then
    assert(next(manifest.sparse_overrides) == nil, "synthesized fixture unexpectedly emitted an override")
else
    local override_by_item = assert(manifest.sparse_overrides["core:item-a"], "override item map missing")
    assert(override_by_item["core:slot-a"] == "core:container-o", "sparse override mismatch")
    assert(override_by_item["core:slot-b"] == nil, "disabled sparse override was emitted")
end
assert(manifest.assignments == nil and manifest.assignments_by_token == nil and
    manifest.delivery_by_token == nil, "prejoined assignment map emitted")

io.write(string.format("FP %d %d %d %d\n", fingerprint[1], fingerprint[2], fingerprint[3], fingerprint[4]))
