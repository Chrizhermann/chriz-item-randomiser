-- Spoiler-safe aggregate protocol shared by the isolated EEex probes.
--
-- The aggregate never trusts source counters.  It requires each probe's exact
-- identity, phase vocabulary, and code set; missing, duplicate, unexpected, or
-- malformed entries are converted to failures while the returned set remains
-- exactly S01-S06, T01-T08, and L01-L09.

FLDLVProbe = FLDLVProbe or {}
FLDLVProbe.results = FLDLVProbe.results or {}

local SCHEMA = "flir-eeex-probe-v1"
local SPECS = {
    {
        name = "surface",
        prefix = "S",
        count = 6,
        phases = { complete = true, failed = true },
    },
    {
        name = "transport",
        prefix = "T",
        count = 8,
        phases = {
            start = true,
            container_queued = true,
            creature_queued = true,
            pile_queued = true,
            settle = true,
            done = true,
            failed = true,
        },
    },
    {
        name = "lifecycle",
        prefix = "L",
        count = 9,
        phases = {
            start = true,
            await_save = true,
            await_load = true,
            done = true,
            failed = true,
        },
    },
}

local result = {
    schema = SCHEMA,
    probe = "aggregate",
    phase = "complete",
    valid = true,
    passed = 0,
    failed = 0,
    skipped = 0,
    pending = 0,
    checks = {},
}

local function expected_codes(spec)
    local codes = {}
    for index = 1, spec.count do
        codes[#codes + 1] = string.format("%s%02d", spec.prefix, index)
    end
    return codes
end

local function exact_counter(value)
    return type(value) == "number" and value >= 0
        and value == math.floor(value)
end

local function normalize_probe(spec, probe)
    local expected = expected_codes(spec)
    local expected_set = {}
    for _, code in ipairs(expected) do
        expected_set[code] = true
    end

    local statuses = {}
    local structurally_valid = type(probe) == "table"
        and probe.schema == SCHEMA
        and probe.probe == spec.name
        and type(probe.phase) == "string"
        and spec.phases[probe.phase] == true
        and type(probe.checks) == "table"

    local source_counts = { P = 0, F = 0, K = 0, S = 0 }
    if structurally_valid then
        local entry_count = 0
        for key, check in pairs(probe.checks) do
            entry_count = entry_count + 1
            if type(key) ~= "number" or key ~= math.floor(key)
                or key < 1 or key > spec.count then
                structurally_valid = false
            end
            local code, status
            if type(check) == "string" then
                code, status = string.match(check,
                    "^([STL][0-9][0-9]):([PFKS])$")
            end
            if not code or not expected_set[code] then
                structurally_valid = false
            elseif statuses[code] ~= nil then
                statuses[code] = "F"
                structurally_valid = false
            else
                statuses[code] = status
                source_counts[status] = source_counts[status] + 1
            end
        end
        if entry_count ~= spec.count then
            structurally_valid = false
        end
    end

    for _, code in ipairs(expected) do
        if statuses[code] == nil then
            statuses[code] = "F"
            structurally_valid = false
        end
    end

    if structurally_valid then
        if not exact_counter(probe.passed)
            or not exact_counter(probe.failed)
            or not exact_counter(probe.skipped)
            or not exact_counter(probe.pending)
            or probe.passed ~= source_counts.P
            or probe.failed ~= source_counts.F
            or probe.skipped ~= source_counts.K
            or probe.pending ~= source_counts.S then
            structurally_valid = false
        end
    end

    if structurally_valid then
        local terminal = probe.phase == "complete" or probe.phase == "done"
        if terminal and source_counts.S ~= 0 then
            structurally_valid = false
        elseif probe.phase == "failed" and source_counts.F == 0 then
            structurally_valid = false
        end
    end

    if not structurally_valid then
        -- A malformed source cannot hide behind otherwise passing codes.
        statuses[expected[1]] = "F"
    end
    return expected, statuses, structurally_valid
end

for _, spec in ipairs(SPECS) do
    local codes, statuses, source_valid = normalize_probe(
        spec, FLDLVProbe.results[spec.name])
    if not source_valid then
        result.valid = false
    end
    for _, code in ipairs(codes) do
        local status = statuses[code]
        result.checks[#result.checks + 1] = code .. ":" .. status
        if status == "P" then
            result.passed = result.passed + 1
        elseif status == "F" then
            result.failed = result.failed + 1
        elseif status == "K" then
            result.skipped = result.skipped + 1
        else
            result.pending = result.pending + 1
        end
    end
end

if result.failed > 0 then
    result.phase = "failed"
elseif result.pending > 0 then
    result.phase = "pending"
end

FLDLVProbe.results.aggregate = result
return result
