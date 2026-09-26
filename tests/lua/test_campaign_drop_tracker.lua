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
    local result = {sequence=sequence, kind=kind, a=a, b=b, c=c, d=d,
        resref=ref or "flpri1", charge1=0, charge2=0, charge3=0,
        expiration=0, flags=1}
    return result
end

local function closed(tracker, generation, count)
    return tracker:finish(generation,
        {count=count, stable=true, armed=false, overflow=false})
end

local function rejected(tracker, generation, event)
    local ok, reason = tracker:ingest(generation, event)
    eq(ok, nil)
    assert(type(reason) == "string" and reason ~= "")
end

function cases.real_pointer_reuse_after_constructor_copy(Drop)
    local watches = {
        watch("reward-1", "1610843200", "1631610144", 19, "flpri1"),
        watch("reward-2", "1610821808", "1631612256", 18, "flpri2"),
        watch("reward-3", "1610821808", "1631613312", 19, "flpri3"),
        watch("unrelated-x", "1610843200", "1631608912", 18, "flprx1"),
    }
    local tracker = assert(Drop.new("campaign/session-1", watches))
    local rows = {
        row(1, 1, "1610821808", "1631613312", "1700000001", 19, "flpri3"),
        row(2, 2, "1631613312", "220003312", "1700000001", 0, "flpri3"),
        row(3, 1, "1610843200", "1631610144", "1700000002", 19, "flpri1"),
        row(4, 2, "1631610144", "219996656", "1700000002", 0, "flpri1"),
        -- The newly allocated FLPRI1 item reuses FLPRI3's copied old address.
        row(5, 3, "219996656", "1631613312", "1700000002", 0, "flpri1"),
        row(6, 1, "1610821808", "1631612256", "1700000001", 18, "flpri2"),
        row(7, 2, "1631612256", "219997280", "1700000001", 0, "flpri2"),
        row(8, 3, "219997280", "1631614896", "1700000001", 0, "flpri2"),
        row(9, 3, "220003312", "1631610672", "1700000001", 0, "flpri3"),
        row(10, 1, "1610843200", "1631608912", "1700000002", 18, "flprx1"),
        row(11, 2, "1631608912", "220000192", "1700000002", 0, "flprx1"),
        row(12, 3, "220000192", "1631608208", "1700000002", 0, "flprx1"),
    }
    for _, event in ipairs(rows) do assert(tracker:ingest("campaign/session-1", event)) end
    local candidates = assert(closed(tracker, "campaign/session-1", 12))
    eq(#candidates, 4)
    eq(candidates[1].request_token, "reward-1")
    eq(candidates[1].new_item_pointer, "1631613312")
    eq(candidates[1].original_item_pointer, "1631610144")
    eq(candidates[1].container_pointer, "1700000002")
    eq(candidates[1].sequences[3], 5)
    eq(candidates[3].original_item_pointer, candidates[1].new_item_pointer)
    eq(candidates[3].new_item_pointer, "1631610672")
    eq(tracker:ingest("campaign/session-1", row(13, 1, "1", "2", "3", 1)), nil)
end

function cases.message_reuse_after_completed_chain(Drop)
    local tracker = assert(Drop.new("g", {
        watch("one", "11", "21", 18, "flpri1"),
        watch("two", "12", "22", 19, "flpri2"),
    }))
    local events = {
        row(1, 1, "11", "21", "31", 18, "flpri1"),
        row(2, 2, "21", "41", "31", 0, "flpri1"),
        row(3, 3, "41", "51", "31", 0, "flpri1"),
        row(4, 1, "12", "22", "32", 19, "flpri2"),
        row(5, 2, "22", "41", "32", 0, "flpri2"),
        row(6, 3, "41", "52", "32", 0, "flpri2"),
    }
    for _, event in ipairs(events) do assert(tracker:ingest("g", event)) end
    local result = assert(closed(tracker, "g", 6))
    eq(result[1].message_pointer, result[2].message_pointer)
    eq(result[2].new_item_pointer, "52")
end

function cases.unrelated_events_do_not_claim_watched_items(Drop)
    local tracker = assert(Drop.new("g", {watch("one", "11", "21", 18, "flpri1")}))
    local events = {
        row(1, 1, "90", "91", "92", 1, "flpri1"),
        row(2, 2, "91", "93", "92", 0, "flpri1"),
        row(3, 1, "11", "21", "31", 18, "flpri1"),
        row(4, 3, "93", "94", "92", 0, "flpri1"),
        row(5, 2, "21", "41", "31", 0, "flpri1"),
        row(6, 3, "41", "51", "31", 0, "flpri1"),
    }
    for _, event in ipairs(events) do assert(tracker:ingest("g", event)) end
    local result = assert(closed(tracker, "g", 6))
    eq(#result, 1)
    eq(result[1].new_item_pointer, "51")
end

function cases.rejects_mismatch_reorder_missing_and_sequence_gap(Drop)
    local function base()
        return assert(Drop.new("g", {watch("one", "11", "21", 18, "flpri1")}))
    end
    local first = row(1, 1, "11", "21", "31", 18)
    local second = row(2, 2, "21", "41", "31", 0)
    local third = row(3, 3, "41", "51", "31", 0)
    local tracker = base()
    rejected(tracker, "g", second) -- constructor before removal
    eq(closed(tracker, "g", 0), nil)

    tracker = base()
    first.charge2 = 7
    rejected(tracker, "g", first)
    first.charge2 = 0
    tracker = base()
    assert(tracker:ingest("g", first))
    second.c = "32"
    rejected(tracker, "g", second)
    second.c = "31"
    tracker = base()
    assert(tracker:ingest("g", first))
    assert(tracker:ingest("g", second))
    third.flags = 3
    rejected(tracker, "g", third)
    third.flags = 1
    tracker = base()
    assert(tracker:ingest("g", first))
    assert(tracker:ingest("g", second))
    eq(closed(tracker, "g", 2), nil) -- missing consumer
    tracker = base()
    first.sequence = 2
    rejected(tracker, "g", first)
end

function cases.rejects_live_pointer_overlap_and_ambiguous_message(Drop)
    local watches = {watch("one", "11", "21", 18, "flpri1"),
        watch("two", "12", "22", 19, "flpri2")}
    local tracker = assert(Drop.new("g", watches))
    assert(tracker:ingest("g", row(1, 1, "11", "21", "31", 18, "flpri1")))
    assert(tracker:ingest("g", row(2, 2, "21", "41", "31", 0, "flpri1")))
    rejected(tracker, "g", row(3, 3, "41", "22", "31", 0, "flpri1"))

    tracker = assert(Drop.new("g", watches))
    assert(tracker:ingest("g", row(1, 1, "11", "21", "31", 18, "flpri1")))
    assert(tracker:ingest("g", row(2, 2, "21", "41", "31", 0, "flpri1")))
    assert(tracker:ingest("g", row(3, 1, "12", "22", "32", 19, "flpri2")))
    rejected(tracker, "g", row(4, 2, "22", "41", "32", 0, "flpri2"))

    tracker = assert(Drop.new("g", {watches[1]}))
    assert(tracker:ingest("g", row(1, 1, "11", "21", "31", 18, "flpri1")))
    assert(tracker:ingest("g", row(2, 2, "21", "41", "31", 0, "flpri1")))
    assert(tracker:ingest("g", row(3, 3, "41", "51", "31", 0, "flpri1")))
    rejected(tracker, "g", row(4, 2, "51", "90", "91", 0, "other"))
end

function cases.snapshot_overflow_and_reset_reject_stale_requests(Drop)
    local watches = {watch("one", "11", "21", 18, "flpri1")}
    local tracker = assert(Drop.new("old", watches))
    assert(tracker:ingest("old", row(1, 1, "11", "21", "31", 18)))
    assert(tracker:reset("new", watches))
    local stale, reason = tracker:ingest("old", row(2, 2, "21", "41", "31", 0))
    eq(stale, nil)
    eq(reason, "stale-request")
    eq(tracker:finish("old", {count=1,stable=true,armed=false,overflow=false}), nil)
    assert(tracker:ingest("new", row(1, 1, "11", "21", "31", 18)))
    assert(tracker:ingest("new", row(2, 2, "21", "41", "31", 0)))
    assert(tracker:ingest("new", row(3, 3, "41", "51", "31", 0)))
    eq(tracker:finish("new", {count=3,stable=true,armed=false,overflow=true}), nil)
    eq(closed(tracker, "new", 3), nil) -- poisoned after overflow
    eq(tracker:reset("new", watches), nil)
end

function cases.rejected_snapshots_and_sparse_watch_lists(Drop)
    local watches = {watch("one", "11", "21", 18, "flpri1")}
    local sparse = {[1]=watches[1], [3]=watch("three", "13", "23", 20, "flpri3")}
    eq(Drop.new("g", sparse), nil)
    for _, meta in ipairs({
        {count=0, stable=false, armed=false, overflow=false},
        {count=0, stable=true, armed=true, overflow=false},
        {count=1, stable=true, armed=false, overflow=false},
    }) do
        local tracker = assert(Drop.new("g", watches))
        eq(tracker:finish("g", meta), nil)
    end
end

function cases.inputs_outputs_and_parallel_trackers_are_isolated(Drop)
    local watch_input = watch("one", "11", "21", 18, "flpri1")
    local session = {nonce=17}
    local token = {source_id="cre:source/item/0"}
    local request = {session=session, token=token}
    watch_input.request_token = request
    local first = assert(Drop.new("a", {watch_input}))
    local second = assert(Drop.new("b", {watch("other", "11", "21", 18, "flpri1")}))
    watch_input.payload.flags = 99
    watch_input.original_item_pointer = "99"
    local event = row(1, 1, "11", "21", "31", 18)
    assert(first:ingest("a", event))
    event.a, event.b, event.flags = "99", "99", 99
    assert(second:ingest("b", row(1, 1, "11", "21", "31", 18)))
    for _, tracker in ipairs({first, second}) do
        local generation = tracker == first and "a" or "b"
        assert(tracker:ingest(generation, row(2, 2, "21", "41", "31", 0)))
        assert(tracker:ingest(generation, row(3, 3, "41", "51", "31", 0)))
    end
    local a = assert(closed(first, "a", 3))
    local b = assert(closed(second, "b", 3))
    eq(a[1].payload.flags, 1)
    eq(a[1].request_token, request)
    eq(a[1].request_token.session, session)
    eq(a[1].request_token.token, token)
    eq(a[1].request_token.session.nonce, 17)
    eq(a[1].original_item_pointer, "21")
    a[1].payload.flags = 99
    eq(b[1].payload.flags, 1)
end

function cases.unrelated_consumer_cannot_reuse_a_live_candidate_or_watched_item(Drop)
    for _, address in ipairs({"21", "51"}) do
        local generation = {}
        local tracker = assert(Drop.new(generation, {watch("one", "11", "21", 18, "flpri1")}))
        if address == "51" then
            assert(tracker:ingest(generation, row(1, 1, "11", "21", "31", 18)))
            assert(tracker:ingest(generation, row(2, 2, "21", "41", "31", 0)))
            assert(tracker:ingest(generation, row(3, 3, "41", "51", "31", 0)))
        end
        local sequence = address == "51" and 4 or 1
        rejected(tracker, generation, row(sequence, 3, "99", address, "91", 0, "other"))
    end
end

return cases
