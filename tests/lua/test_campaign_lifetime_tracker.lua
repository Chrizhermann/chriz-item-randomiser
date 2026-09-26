local cases = {}

local function eq(actual, expected)
    assert(actual == expected, tostring(actual) .. " ~= " .. tostring(expected))
end

local function payload(ref)
    return {resref=ref or "flpri1", charge1=0, charge2=0, charge3=0,
        expiration=0, flags=1}
end

local function watch(token, owner, item, slot, ref)
    return {request_token=token, owner_pointer=owner, original_item_pointer=item,
        slot=slot, payload=payload(ref)}
end

local function row(sequence, kind, a, b, c, d, ref)
    return {sequence=sequence, kind=kind, a=a, b=b, c=c, d=d,
        resref=ref or "flpri1", charge1=0, charge2=0, charge3=0,
        expiration=0, flags=1}
end

local function destructor(sequence, kind, item, ref)
    local event = row(sequence, kind, item, "4294967295", "0",
        kind == 4 and 0 or 4294967295, ref)
    if ref == "" then event.resref = "" end
    return event
end

local function snapshot(count)
    return {schema="flpr-drop-lifetime-trace-v1", installed=true,
        count=count, stable=true, armed=false, overflow=false}
end

local function closed(tracker, generation, count)
    return tracker:finish(generation, snapshot(count))
end

local function ingest_all(tracker, generation, events)
    for _, event in ipairs(events) do assert(tracker:ingest(generation, event)) end
end

local function rejected(tracker, generation, event)
    local ok, reason = tracker:ingest(generation, event)
    eq(ok, nil)
    assert(type(reason) == "string" and reason ~= "")
end

local function one_chain(first, owner, old, container, message, ground, slot, ref)
    return {
        row(first, 1, owner, old, container, slot, ref),
        row(first+1, 2, old, message, container, 0, ref),
        row(first+2, 3, message, ground, container, 0, ref),
    }
end

function cases.destructors_between_death_rows_keep_original_sequences(Life, Drop)
    local token = {source_id="cre:source/item/0"}
    local watches = {watch(token, "11", "21", 18, "flpri1")}
    local tracker = assert(Life.new(Drop, "g", watches))
    local events = {
        destructor(1, 4, "90", ""),
        row(2, 1, "11", "21", "31", 18),
        destructor(3, 5, "91", ""),
        row(4, 2, "21", "41", "31", 0),
        destructor(5, 4, "21", "flpri1"),
        row(6, 3, "41", "51", "31", 0),
        destructor(7, 5, "92", ""),
    }
    ingest_all(tracker, "g", events)
    local result = assert(closed(tracker, "g", 7))
    eq(#result, 1)
    eq(result[1].request_token, token)
    eq(result[1].owner_pointer, "11")
    eq(result[1].original_item_pointer, "21")
    eq(result[1].container_pointer, "31")
    eq(result[1].message_pointer, "41")
    eq(result[1].new_item_pointer, "51")
    eq(result[1].sequences[1], 2)
    eq(result[1].sequences[2], 4)
    eq(result[1].sequences[3], 6)
    eq(tracker:ingest("g", destructor(8, 4, "93", "")), nil)
end

function cases.copy_before_original_destruction_is_required_and_sticky(Life, Drop)
    local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
    assert(tracker:ingest("g", row(1, 1, "11", "21", "31", 18)))
    rejected(tracker, "g", destructor(2, 4, "21", "flpri1"))
    rejected(tracker, "g", row(2, 2, "21", "41", "31", 0))
    eq(closed(tracker, "g", 1), nil)

    tracker = assert(Life.new(Drop, "h", {watch("one", "11", "21", 18)}))
    rejected(tracker, "h", destructor(1, 5, "21", "flpri1"))
    eq(closed(tracker, "h", 0), nil)
end

function cases.ground_item_destruction_rejects_even_identical_replacement(Life, Drop)
    for _, kind in ipairs({4, 5}) do
        local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
        ingest_all(tracker, "g", one_chain(1, "11", "21", "31", "41", "51", 18))
        rejected(tracker, "g", destructor(4, kind, "51", "flpri1"))
        rejected(tracker, "g", row(4, 3, "99", "51", "31", 0))
        eq(closed(tracker, "g", 3), nil)
    end
end

function cases.old_pointer_reuse_needs_explicit_destruction(Life, Drop)
    local watches = {watch("one", "11", "21", 18)}
    local tracker = assert(Life.new(Drop, "g", watches))
    ingest_all(tracker, "g", one_chain(1, "11", "21", "31", "41", "51", 18))
    rejected(tracker, "g", row(4, 1, "12", "21", "32", 19, "other"))

    tracker = assert(Life.new(Drop, "h", watches))
    ingest_all(tracker, "h", {
        row(1, 1, "11", "21", "31", 18),
        row(2, 2, "21", "41", "31", 0),
        destructor(3, 5, "21", "flpri1"),
        row(4, 3, "41", "51", "31", 0),
        row(5, 1, "12", "21", "32", 19, "other"),
        row(6, 2, "21", "42", "32", 0, "other"),
        row(7, 3, "42", "52", "32", 0, "other"),
    })
    local result = assert(closed(tracker, "h", 7))
    eq(result[1].new_item_pointer, "51")
    eq(result[1].sequences[3], 4)
end

function cases.copied_watched_old_pointer_reuse_needs_its_destructor(Life, Drop)
    local watches = {watch("one", "11", "21", 18, "flpri1"),
        watch("two", "12", "22", 19, "flpri2")}
    local copied = {
        row(1, 1, "11", "21", "31", 18, "flpri1"),
        row(2, 2, "21", "41", "31", 0, "flpri1"),
        row(3, 1, "12", "22", "32", 19, "flpri2"),
        row(4, 2, "22", "42", "32", 0, "flpri2"),
    }
    local tracker = assert(Life.new(Drop, "without-destruction", watches))
    ingest_all(tracker, "without-destruction", copied)
    rejected(tracker, "without-destruction",
        row(5, 3, "41", "22", "31", 0, "flpri1"))
    eq(closed(tracker, "without-destruction", 4), nil)

    tracker = assert(Life.new(Drop, "with-destruction", watches))
    ingest_all(tracker, "with-destruction", copied)
    ingest_all(tracker, "with-destruction", {
        destructor(5, 5, "22", "flpri2"),
        row(6, 3, "41", "22", "31", 0, "flpri1"),
        row(7, 3, "42", "52", "32", 0, "flpri2"),
    })
    local result = assert(closed(tracker, "with-destruction", 7))
    eq(#result, 2)
    eq(result[1].new_item_pointer, "22")
    eq(result[1].sequences[1], 1)
    eq(result[1].sequences[2], 2)
    eq(result[1].sequences[3], 6)
    eq(result[2].new_item_pointer, "52")
    eq(result[2].sequences[1], 3)
    eq(result[2].sequences[2], 4)
    eq(result[2].sequences[3], 7)
    eq(result[2].original_destruction_sequence, 5)
end

function cases.message_reuse_and_unrelated_temporary_destruction(Life, Drop)
    local watches = {watch("one", "11", "21", 18, "flpri1"),
        watch("two", "12", "22", 19, "flpri2")}
    local tracker = assert(Life.new(Drop, "g", watches))
    ingest_all(tracker, "g", {
        row(1, 1, "11", "21", "31", 18, "flpri1"),
        row(2, 2, "21", "41", "31", 0, "flpri1"),
        row(3, 3, "41", "51", "31", 0, "flpri1"),
        destructor(4, 4, "91", ""),
        row(5, 1, "12", "22", "32", 19, "flpri2"),
        row(6, 2, "22", "41", "32", 0, "flpri2"),
        destructor(7, 5, "92", ""),
        row(8, 3, "41", "52", "32", 0, "flpri2"),
    })
    local result = assert(closed(tracker, "g", 8))
    eq(#result, 2)
    eq(result[1].message_pointer, result[2].message_pointer)
    eq(result[2].new_item_pointer, "52")
    eq(result[2].sequences[1], 5)
    eq(result[2].sequences[3], 8)
end

function cases.malformed_destructors_and_gaps_poison_trace(Life, Drop)
    local good = destructor(1, 4, "90", "")
    local changes = {
        {key="a", value="0"}, {key="b", value="0"},
        {key="c", value="1"}, {key="d", value=1},
        {key="kind", value=6}, {key="charge1", value=-1},
    }
    for _, change in ipairs(changes) do
        local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
        local bad = destructor(1, 4, "90", "")
        bad[change.key] = change.value
        rejected(tracker, "g", bad)
        eq(closed(tracker, "g", 0), nil)
    end
    for _, bad in ipairs({
        row(2, 4, "90", "91", "0", 0),
        row(0, 4, "90", "91", "0", 0),
    }) do
        local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
        rejected(tracker, "g", bad)
    end
    local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
    assert(tracker:ingest("g", good))
    rejected(tracker, "g", destructor(3, 5, "91", ""))
    eq(closed(tracker, "g", 1), nil)
end

function cases.snapshot_schema_install_count_and_stability_are_required(Life, Drop)
    local invalid = {
        {key="schema", value="other"}, {key="installed", value=false},
        {key="count", value=2}, {key="stable", value=false},
        {key="armed", value=true}, {key="overflow", value=true},
    }
    for _, change in ipairs(invalid) do
        local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
        ingest_all(tracker, "g", one_chain(1, "11", "21", "31", "41", "51", 18))
        local meta = snapshot(3)
        meta[change.key] = change.value
        eq(tracker:finish("g", meta), nil)
        eq(closed(tracker, "g", 3), nil)
    end
    local tracker = assert(Life.new(Drop, "g", {watch("one", "11", "21", 18)}))
    eq(closed(tracker, "g", 0), nil) -- incomplete watched chain
end

function cases.session_reset_sparse_watches_and_parallel_state(Life, Drop)
    local watches = {watch("one", "11", "21", 18)}
    local sparse = {[1]=watches[1], [3]=watch("three", "13", "23", 20)}
    eq(Life.new(Drop, "g", sparse), nil)
    local generation = {nonce=1}
    local next_generation = {nonce=2}
    local token = {source_id="one"}
    local request = watch(token, "11", "21", 18)
    local tracker = assert(Life.new(Drop, generation, {request}))
    local parallel = assert(Life.new(Drop, "parallel", watches))
    assert(tracker:ingest(generation, row(1, 1, "11", "21", "31", 18)))
    assert(tracker:reset(next_generation, {request}))
    local stale, reason = tracker:ingest(generation, row(2, 2, "21", "41", "31", 0))
    eq(stale, nil)
    eq(reason, "stale-request")
    eq(tracker:finish(generation, snapshot(1)), nil)
    eq(tracker:reset(next_generation, {request}), nil)
    request.original_item_pointer = "99"
    request.payload.flags = 99
    local event = row(1, 1, "11", "21", "31", 18)
    assert(tracker:ingest(next_generation, event))
    event.a, event.b, event.flags = "99", "99", 99
    assert(tracker:ingest(next_generation, row(2, 2, "21", "41", "31", 0)))
    assert(tracker:ingest(next_generation, row(3, 3, "41", "51", "31", 0)))
    local result = assert(closed(tracker, next_generation, 3))
    eq(result[1].request_token, token)
    eq(result[1].payload.flags, 1)
    eq(result[1].original_item_pointer, "21")
    ingest_all(parallel, "parallel", one_chain(1, "11", "21", "31", "41", "51", 18))
    eq(assert(closed(parallel, "parallel", 3))[1].new_item_pointer, "51")
end

function cases.total_and_death_row_bounds_are_independent(Life, Drop)
    local watches = {watch("one", "11", "21", 18)}
    local tracker = assert(Life.new(Drop, "total", watches))
    ingest_all(tracker, "total", one_chain(1, "11", "21", "31", "41", "51", 18))
    for sequence = 4, 4096 do
        assert(tracker:ingest("total", destructor(sequence, 4, tostring(100000+sequence), "")))
    end
    eq(#assert(closed(tracker, "total", 4096)), 1)

    tracker = assert(Life.new(Drop, "too-many", watches))
    ingest_all(tracker, "too-many", one_chain(1, "11", "21", "31", "41", "51", 18))
    for sequence = 4, 4096 do
        assert(tracker:ingest("too-many", destructor(sequence, 4, tostring(100000+sequence), "")))
    end
    rejected(tracker, "too-many", destructor(4097, 4, "200000", ""))
    eq(closed(tracker, "too-many", 4096), nil)

    tracker = assert(Life.new(Drop, "death-limit", watches))
    ingest_all(tracker, "death-limit", one_chain(1, "11", "21", "31", "41", "51", 18))
    for sequence = 4, 256 do
        assert(tracker:ingest("death-limit",
            row(sequence, 3, tostring(300000+sequence), tostring(400000+sequence),
                "600000", 0, "other")))
    end
    eq(#assert(closed(tracker, "death-limit", 256)), 1)

    tracker = assert(Life.new(Drop, "death-over", watches))
    ingest_all(tracker, "death-over", one_chain(1, "11", "21", "31", "41", "51", 18))
    for sequence = 4, 256 do
        assert(tracker:ingest("death-over",
            row(sequence, 3, tostring(300000+sequence), tostring(400000+sequence),
                "600000", 0, "other")))
    end
    rejected(tracker, "death-over", row(257, 3, "300257", "400257", "600000", 0, "other"))
    eq(closed(tracker, "death-over", 256), nil)
end

return cases
