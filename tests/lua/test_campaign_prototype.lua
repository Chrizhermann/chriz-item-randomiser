-- Logical host protocol tests. These do not prove native callback or mutation APIs.
local cases = {}
local function clone(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for key, item in pairs(value) do result[key] = clone(item) end
    return result
end
local function equal(actual, expected)
    assert(actual == expected, tostring(actual) .. " ~= " .. tostring(expected))
end
local function failed(expected, result, reason)
    equal(result, nil)
    equal(reason, expected)
end
local function fixture(Core, revision, quantity)
    local f = { values = {}, revisions = {}, writes = 0, lookups = 0 }
    f.manifest = { schema = Core.SCHEMA, state = "prototype-not-playable",
        revision = string.rep(revision or "a", 64), bound_revision = string.rep("b", 64),
        world_fingerprint = string.rep("c", 64), profile_id = "synthetic",
        sources = {
            { id = "cre:sample/item/0", quantity = quantity or 1, resource_sha256 = string.rep("1", 64),
              record = { id = "cre:sample/item/0", record = { resref = "sample", charges = { 3, 2, 1 }, expiration = 7, flags = 1 } } },
            { id = "cre:sample/item/1", quantity = 1, resource_sha256 = string.rep("2", 64),
              record = { id = "cre:sample/item/1", record = { resref = "sample", charges = { 9, 0, 0 }, expiration = 8, flags = 4 } } },
        },
        targets = {
            { id = "cre:targeta", capacity = 3, record = { id = "cre:targeta" }, resource_sha256 = string.rep("3", 64) },
            { id = "cre:targetb", capacity = 2, record = { id = "cre:targetb" }, resource_sha256 = string.rep("4", 64) },
        }, runtime_requirements = { "synthetic-host-only" } }
    f.revisions[f.manifest.revision] = f.manifest
    f.storage = {}
    function f.storage:get(key) return f.values[key] or 0 end
    function f.storage:set(key, value)
        f.writes = f.writes + 1
        if f.fail_before == f.writes then error("SIMULATED_WRITE_FAILURE") end
        f.values[key] = value
        if f.fail_after == f.writes then error("SIMULATED_AFTER_WRITE_FAILURE") end
    end
    function f:new_controller()
        return assert(Core.new(self.storage, function(revision)
            f.lookups = f.lookups + 1
            return f.revisions[revision]
        end))
    end
    f.controller = f:new_controller()
    function f:capture(seed)
        return self.controller:capture(self.manifest, seed or 17, "confirmed-new-game")
    end
    function f:source_witness(index)
        local source = self.manifest.sources[index]
        return { identity = string.rep(tostring(index), 64), source_id = source.id,
            exact_matches = 1, quantity = source.quantity, payload = clone(source.record.record) }
    end
    function f:source_operation(index)
        return assert(self.controller:begin_source(index, self:source_witness(index)))
    end
    function f:source_receipt(operation)
        local receipt = clone(operation)
        receipt.session = operation.session
        receipt.removed, receipt.remaining = receipt.quantity, 0
        return receipt
    end
    function f:suppress(index)
        local operation = self:source_operation(index)
        self.controller:on_observation_boundary()
        assert(self.controller:observe(self:source_receipt(operation)))
    end
    function f:delivery_witness(index)
        return { identity = string.rep("d", 64), target_id = self.controller:assignments()[index].target_id, accepts = 1 }
    end
    function f:delivery_receipt(operation)
        local receipt = clone(operation)
        receipt.session = operation.session
        receipt.created, receipt.nonce_bound_instance, receipt.lootable = 1, 1, 1
        return receipt
    end
    return f
end

local function tiered(f)
    local first, second = f.manifest.sources[1].id, f.manifest.sources[2].id
    local target_a, target_b = f.manifest.targets[1].id, f.manifest.targets[2].id
    f.manifest.randomization = {
        loss_percent = 0, unrandomized_percent = 0,
        sources = {
            [first] = {tier = "low", loss_exempt = false,
                exclude_targets = {{id = target_a, ordinal = 1}}},
            [second] = {tier = "high", loss_exempt = true,
                exclude_targets = {{id = target_a, ordinal = 2}}},
        },
        targets = {
            [target_a] = {"low", "high", "low"},
            [target_b] = {"high", "low"},
        },
    }
    return f.manifest.randomization
end

function cases.TieredCopiesUseOnlyMatchingSlotsIncludingMultipleTargetSlots(Core)
    local f = fixture(Core, nil, 3)
    local rules = tiered(f)
    assert(f:capture(17))
    local seen, at_first_target = {}, 0
    for _, assignment in ipairs(f.controller:assignments()) do
        equal(assignment.outcome, "deliver")
        equal(assignment.tier, rules.targets[assignment.target_id][assignment.ordinal])
        local key = assignment.target_id .. "/" .. assignment.ordinal
        assert(not seen[key]); seen[key] = true
        if assignment.target_id == f.manifest.targets[1].id then at_first_target = at_first_target + 1 end
    end
    equal(at_first_target >= 2, true)
    equal(#f.controller:assignments(), 4)
end

function cases.TieredSeedReproducesAcrossFreshCampaignsAndReload(Core)
    local first, second = fixture(Core, nil, 3), fixture(Core, nil, 3)
    tiered(first); tiered(second)
    assert(first:capture(29)); assert(second:capture(29))
    for index, assignment in ipairs(first.controller:assignments()) do
        local other = second.controller:assignments()[index]
        equal(assignment.target_id, other.target_id)
        equal(assignment.ordinal, other.ordinal)
        equal(assignment.outcome, other.outcome)
    end
    local first_saved, second_saved = clone(first.values), clone(second.values)
    first.values, second.values = first_saved, second_saved
    local first_loaded, second_loaded = first:new_controller(), second:new_controller()
    assert(first_loaded:restore()); assert(second_loaded:restore())
    for index, assignment in ipairs(first_loaded:assignments()) do
        local other = second_loaded:assignments()[index]
        equal(assignment.target_id, other.target_id)
        equal(assignment.ordinal, other.ordinal)
        equal(assignment.outcome, other.outcome)
    end
end

function cases.LossRemovesOriginalButOnlyExemptSourceGetsDelivery(Core)
    local f = fixture(Core, nil, 2)
    local rules = tiered(f); rules.loss_percent = 100
    assert(f:capture(17))
    local assignments = f.controller:assignments()
    for index = 1, 2 do
        equal(assignments[index].outcome, "lost")
        equal(assignments[index].target_id, nil)
        equal(assignments[index].delivery_phase, Core.PHASE.lost)
    end
    equal(assignments[3].outcome, "deliver")
    f:suppress(1); f:suppress(2)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1,
        {identity = string.rep("d", 64), target_id = f.manifest.targets[1].id, accepts = 1}))
    assert(f.controller:begin_delivery(3, f:delivery_witness(3)))
end

function cases.KeptOriginalsNeverRequestMutationAndExcludeLinkedSlots(Core)
    local f = fixture(Core)
    local rules = tiered(f); rules.unrandomized_percent = 100
    assert(f:capture(17))
    for index, assignment in ipairs(f.controller:assignments()) do
        equal(assignment.outcome, "kept")
        equal(assignment.source_phase, Core.PHASE.kept)
        equal(assignment.delivery_phase, Core.PHASE.kept)
        equal(assignment.target_id, nil)
        failed("ALREADY_ATTEMPTED", f.controller:begin_source(index, f:source_witness(index)))
        failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(index,
            {identity = string.rep("d", 64), target_id = f.manifest.targets[1].id, accepts = 1}))
    end
    assert(f:new_controller():restore())
    local selective = fixture(Core)
    local selective_rules = tiered(selective)
    selective_rules.unrandomized_percent = 50
    selective_rules.sources[selective.manifest.sources[2].id].tier = "low"
    selective_rules.sources[selective.manifest.sources[2].id].exclude_targets = {}
    local found = false
    for seed = 1, 40 do
        local candidate = fixture(Core)
        candidate.manifest.randomization = clone(selective_rules)
        assert(candidate:capture(seed))
        local rows = candidate.controller:assignments()
        if rows[1].outcome == "kept" and rows[2].outcome == "deliver" then
            assert(rows[2].target_id ~= candidate.manifest.targets[1].id or rows[2].ordinal ~= 1)
            found = true; break
        end
    end
    assert(found, "NO_SELECTIVE_KEEP_SEED")
end

function cases.VirtualStockStillDeliversWhenPhysicalOriginalsAreKept(Core)
    local f = fixture(Core)
    local rules = tiered(f)
    local virtual = f.manifest.sources[2]
    virtual.record.kind = "virtual"
    rules.unrandomized_percent = 100
    assert(f:capture(17))
    local assignments = f.controller:assignments()
    equal(assignments[1].outcome, "kept")
    equal(assignments[1].source_phase, Core.PHASE.kept)
    equal(assignments[2].outcome, "deliver")
    equal(assignments[2].source_phase, Core.PHASE.confirmed)
    assert(assignments[2].target_id ~= f.manifest.targets[1].id or assignments[2].ordinal ~= 1)
    failed("ALREADY_ATTEMPTED", f.controller:begin_source(1, f:source_witness(1)))
    failed("ALREADY_ATTEMPTED", f.controller:begin_source(2, f:source_witness(2)))
    assert(f.controller:begin_delivery(2, f:delivery_witness(2)))
    assert(f:new_controller():restore())
end

local function row_filters(f)
    local rules = tiered(f)
    rules.row_filters = {sources = {}, targets = {}}
    for _, kind in ipairs({"sources", "targets"}) do
        for _, row in ipairs(f.manifest[kind]) do
            rules.row_filters[kind][row.id] = {chance = 100, group = ""}
        end
    end
    return rules.row_filters
end

function cases.RejectedLocationKeepsEverySourceSharingItsOriginalIdent(Core)
    local f = fixture(Core)
    local filters = row_filters(f)
    for _, source in ipairs(f.manifest.sources) do
        filters.sources[source.id].group = "bg1:paired"
    end
    local target = filters.targets[f.manifest.targets[1].id]
    target.group, target.chance = "bg1:paired", 0
    assert(f:capture(19))
    for index, assignment in ipairs(f.controller:assignments()) do
        equal(assignment.outcome, "kept")
        equal(assignment.source_phase, Core.PHASE.kept)
        failed("ALREADY_ATTEMPTED", f.controller:begin_source(index, f:source_witness(index)))
    end
    assert(f:new_controller():restore())
end

function cases.RejectedUnpairedSourceDoesNotExcludeUnrelatedRows(Core)
    local f = fixture(Core)
    local filters = row_filters(f)
    filters.sources[f.manifest.sources[1].id].chance = 0
    assert(f:capture(19))
    equal(f.controller:assignments()[1].outcome, "kept")
    equal(f.controller:assignments()[2].outcome, "deliver")
end

function cases.RejectedVirtualStockIsNotCreatedOrSuppressed(Core)
    local f = fixture(Core)
    local filters = row_filters(f)
    f.manifest.sources[2].record.kind = "virtual"
    filters.sources[f.manifest.sources[2].id].chance = 0
    assert(f:capture(19))
    local assignment = f.controller:assignments()[2]
    equal(assignment.outcome, "kept")
    equal(assignment.source_phase, Core.PHASE.kept)
    equal(assignment.delivery_phase, Core.PHASE.kept)
    assert(f:new_controller():restore())
end

function cases.TargetChanceRollAppliesToWholeRowAndSurvivesRestore(Core)
    local found_included, found_excluded = false, false
    for seed = 1, 50 do
        local f = fixture(Core)
        local filters = row_filters(f)
        local target_a = f.manifest.targets[1].id
        filters.targets[target_a].chance = 50
        f.manifest.randomization.sources[f.manifest.sources[1].id].exclude_targets = {}
        f.manifest.randomization.sources[f.manifest.sources[2].id].exclude_targets = {}
        assert(f:capture(seed))
        local assignments = f.controller:assignments()
        if assignments[1].target_id == target_a or assignments[2].target_id == target_a then
            found_included = true
        else found_excluded = true end
        local restored = f:new_controller()
        assert(restored:restore())
        for index, assignment in ipairs(restored:assignments()) do
            equal(assignment.target_id, assignments[index].target_id)
            equal(assignment.ordinal, assignments[index].ordinal)
        end
    end
    assert(found_included and found_excluded, "ROW_CHANCE_NEVER_VARIED")
end

function cases.RejectedOnlyTierLocationFailsBeforeCampaignWrites(Core)
    local f = fixture(Core)
    local filters = row_filters(f)
    for _, target in ipairs(f.manifest.targets) do filters.targets[target.id].chance = 0 end
    failed("TIER_CAPACITY_SHORTFALL", f:capture(19))
    equal(f.writes, 0)
end

function cases.RowFiltersRejectMissingRowsAndInvalidChanceBeforeWrites(Core)
    local f = fixture(Core)
    local filters = row_filters(f)
    filters.sources[f.manifest.sources[1].id].chance = 101
    failed("ROW_FILTER_INVALID", f:capture(19))
    equal(f.writes, 0)
    filters.sources[f.manifest.sources[1].id] = nil
    failed("ROW_FILTER_INVALID", f:capture(19))
    equal(f.writes, 0)
end

function cases.TomeVariantKeepsSuppressionOriginalAndPersistsDeliveryProof(Core)
    local f = fixture(Core)
    local rules = tiered(f)
    local source = f.manifest.sources[1]
    source.record.record.resref = "book08"
    rules.sources[source.id].reward_variants = {"book03"}
    assert(f:capture(19))
    local assignment = f.controller:assignments()[1]
    equal(assignment.payload.resref, "book03")
    equal(source.record.record.resref, "book08")
    local suppression = f:source_operation(1)
    equal(suppression.payload.resref, "book08")
    f.controller:on_observation_boundary()
    assert(f.controller:observe(f:source_receipt(suppression)))
    local delivery = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
    equal(delivery.payload.resref, "book03")
    local restored = f:new_controller()
    assert(restored:restore())
    equal(restored:pending_observation().payload.resref, "book03")
    local request = restored:pending_observation()
    restored:on_observation_boundary()
    assert(restored:observe(f:delivery_receipt(request)))
    equal(restored:assignments()[1].delivery_phase, Core.PHASE.confirmed)
    equal(restored:reward_payload(1).charges[1], source.record.record.charges[1])
    assert(f:new_controller():restore())
end

function cases.TomeVariantsReachAllSixChoicesAndSurviveRestore(Core)
    local seen = {}
    for seed = 1, 90 do
        local f = fixture(Core)
        local rules = tiered(f)
        rules.sources[f.manifest.sources[1].id].reward_variants = {
            "book03", "book04", "book05", "book06", "book07", "book08"}
        assert(f:capture(seed))
        local chosen = f.controller:reward_payload(1).resref
        seen[chosen] = true
        local restored = f:new_controller()
        assert(restored:restore())
        equal(restored:reward_payload(1).resref, chosen)
    end
    local count = 0
    for _ in pairs(seen) do count = count + 1 end
    equal(count, 6)
end

local function overflow_fixture(Core)
    local f = fixture(Core, nil, 3)
    local first, second = f.manifest.sources[1].id, f.manifest.sources[2].id
    local target_a, target_b = f.manifest.targets[1].id, f.manifest.targets[2].id
    f.manifest.targets[1].capacity = 1
    f.manifest.targets[1].record.kind = "container"
    f.manifest.targets[2].capacity = 1
    f.manifest.targets[2].record.kind = "container"
    f.manifest.randomization = {
        overflow = true, loss_percent = 0, unrandomized_percent = 0,
        sources = {
            [first] = {tier = "low", loss_exempt = false, exclude_targets = {}},
            [second] = {tier = "low", loss_exempt = false, exclude_targets = {}},
        },
        targets = {[target_a] = {"low"}, [target_b] = {"high"}},
    }
    return f
end

function cases.OverflowSharesEligibleSlotButPersistsSeparateDeliveries(Core)
    local f = overflow_fixture(Core)
    assert(f:capture(23))
    local assignments = f.controller:assignments()
    equal(#assignments, 4)
    for _, assignment in ipairs(assignments) do
        equal(assignment.outcome, "deliver")
        equal(assignment.target_id, f.manifest.targets[1].id)
        equal(assignment.ordinal, 1)
    end
    f:suppress(1); f:suppress(2)
    local first = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller:on_observation_boundary()
    assert(f.controller:observe(f:delivery_receipt(first)))
    local restored = f:new_controller()
    assert(restored:restore())
    f.controller = restored
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.confirmed)
    equal(f.controller:assignments()[2].delivery_phase, Core.PHASE.pending)
    local second = assert(f.controller:begin_delivery(2, f:delivery_witness(2)))
    assert(second.index ~= first.index and second.nonce ~= first.nonce)
    f.controller:on_observation_boundary()
    assert(f.controller:observe(f:delivery_receipt(second)))
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.confirmed)
    equal(f.controller:assignments()[2].delivery_phase, Core.PHASE.confirmed)
end

function cases.OverflowRejectsWhenKeptOriginalExcludesLastEligibleSlot(Core)
    local f = overflow_fixture(Core)
    f.manifest.sources[1].quantity = 1
    f.manifest.sources[2].record.kind = "virtual"
    f.manifest.randomization.unrandomized_percent = 100
    f.manifest.randomization.sources[f.manifest.sources[1].id].exclude_targets = {
        {id = f.manifest.targets[1].id, ordinal = 1}}
    failed("OVERFLOW_TARGET_MISSING", f:capture(23))
    equal(f.writes, 0)
    assert(next(f.values) == nil)
end

function cases.MixedKeepExclusionsNeedSpareTierCapacityBeforeCapture(Core)
    local function configure(f, spare, percent)
        local rules = tiered(f)
        local first, second = f.manifest.sources[1].id, f.manifest.sources[2].id
        local target = f.manifest.targets[1].id
        rules.unrandomized_percent = percent
        rules.sources[first].exclude_targets = {
            {id = target, ordinal = 1}, {id = target, ordinal = 2}}
        rules.sources[second].tier = "low"
        rules.sources[second].exclude_targets = {}
        rules.targets[target] = spare and {"low", "low", "low"} or {"low", "low", "high"}
        rules.targets[f.manifest.targets[2].id] = {"high", "high"}
    end
    local unsafe = fixture(Core)
    configure(unsafe, false, 50)
    failed("KEEP_CAPACITY_SHORTFALL", unsafe:capture(2))
    equal(unsafe.writes, 0); assert(next(unsafe.values) == nil)

    local spare = fixture(Core)
    configure(spare, true, 50)
    assert(spare:capture(2))
    equal(spare.values.flpr_state, 2)
    assert(spare:new_controller():restore())

    local all_kept = fixture(Core)
    configure(all_kept, false, 100)
    assert(all_kept:capture(2))
    for _, assignment in ipairs(all_kept.controller:assignments()) do
        equal(assignment.outcome, "kept")
    end
end

function cases.ExplicitNewGameHandoffIsRequired(Core)
    local f = fixture(Core)
    failed("NEW_GAME_HANDOFF_REQUIRED", f.controller:capture(f.manifest, 17))
    equal(f.writes, 0)
    failed("INACTIVE", f.controller:restore())
    equal(f.lookups, 0)
    equal(f.writes, 0)
end

function cases.CaptureIsOnceAndUsesPrivateRandomGenerator(Core)
    local f = fixture(Core)
    local old_random, old_seed = math.random, math.randomseed
    math.random, math.randomseed = function() error("GLOBAL_RANDOM_USED") end, function() error("GLOBAL_SEED_USED") end
    local ok, err = pcall(function() assert(f:capture()) end)
    math.random, math.randomseed = old_random, old_seed
    assert(ok, err)
    failed("ALREADY_MARKED", f:capture(19))
    equal(f.values.flpr_seed, 17)
end

function cases.IdenticalSeedsReproduceAndFreshSeedsVary(Core)
    local fingerprints = {}
    for seed = 1, 24 do
        local first, second = fixture(Core), fixture(Core)
        assert(first:capture(seed)); assert(second:capture(seed))
        local signature = {}
        for i = 1, 2 do
            equal(first.values[string.format("flpr_a%04d", i)], second.values[string.format("flpr_a%04d", i)])
            signature[i] = tostring(first.values[string.format("flpr_a%04d", i)])
        end
        fingerprints[table.concat(signature, ",")] = true
    end
    local count = 0
    for _ in pairs(fingerprints) do count = count + 1 end
    assert(count > 1, "fresh seeds never varied assignment")
end

function cases.CapacityAndRepeatedCopiesRemainDistinct(Core)
    local f = fixture(Core, nil, 3)
    assert(f:capture())
    local assignments, slots = f.controller:assignments(), {}
    equal(#assignments, 4)
    for _, assignment in ipairs(assignments) do
        local key = assignment.target_id .. ":" .. assignment.ordinal
        assert(not slots[key]); slots[key] = true
    end
    equal(assignments[1].source, 1); equal(assignments[3].source, 1)
    equal(assignments[3].copy, 3); equal(assignments[4].source, 2)
end

function cases.OlderCampaignResolvesItsRevisionAfterNextProfileChanges(Core)
    local old, next_run = fixture(Core, "a"), fixture(Core, "e")
    assert(old:capture(17)); old:suppress(1)
    local saved = clone(old.values)
    assert(next_run:capture(23))
    old.revisions[next_run.manifest.revision] = next_run.manifest
    old.values = saved
    local restored = old:new_controller()
    assert(restored:restore())
    equal(restored.manifest.revision, old.manifest.revision)
    equal(restored:assignments()[1].source_phase, 2)
    equal(restored:assignments()[1].delivery_phase, 0)
    equal(next_run.controller:assignments()[1].source_phase, 0)
end

function cases.MissingRevisionNeverFallsBackToCurrent(Core)
    local f = fixture(Core)
    assert(f:capture())
    f.revisions[f.manifest.revision] = nil
    local before = f.writes
    failed("REVISION_UNAVAILABLE", f:new_controller():restore())
    equal(f.writes, before)
end

function cases.WrongRevisionOrMalformedResolverResultIsRejected(Core)
    local f = fixture(Core)
    assert(f:capture())
    f.revisions[f.manifest.revision] = true
    failed("REVISION_MISMATCH", f:new_controller():restore())
    f.revisions[f.manifest.revision] = { revision = string.rep("f", 64) }
    failed("REVISION_MISMATCH", f:new_controller():restore())
end

function cases.LiveProfileTablesCannotMutateCapturedSnapshot(Core)
    local f = fixture(Core)
    assert(f:capture())
    f.manifest.sources[1].record.record.charges[1] = 99
    equal(f.controller.manifest.sources[1].record.record.charges[1], 3)
    local view = f.controller:assignments()
    view[1].target_id = "modified"
    assert(f.controller:assignments()[1].target_id ~= "modified")
end

function cases.InterruptedCaptureNeverRerollsOrPublishesPartialCampaign(Core)
    local sample = fixture(Core)
    assert(sample:capture())
    for cut = 1, sample.writes do
        local f = fixture(Core)
        f.fail_before = cut
        assert(not pcall(function() f:capture() end))
        f.fail_before = nil
        local restored, reason = f:new_controller():restore()
        equal(restored, nil)
        equal(reason, cut == 1 and "INACTIVE" or "CAPTURE_INCOMPLETE")
        if cut > 1 then failed("ALREADY_MARKED", f:capture(43)) end
    end
end

function cases.MissingOrCorruptStatusesCannotBecomeFreshRewards(Core)
    for _, key in ipairs({ "flpr_s0001", "flpr_d0001" }) do
        local f = fixture(Core)
        assert(f:capture())
        f.values[key] = nil
        local before = f.writes
        assert(not pcall(function() f:new_controller():restore() end))
        equal(f.writes, before)
    end
end

function cases.ModifiedAssignmentsOrSeedAreRejectedOnLoad(Core)
    for _, key in ipairs({ "flpr_a0001", "flpr_seed" }) do
        local f = fixture(Core)
        assert(f:capture())
        f.values[key] = 0
        local result = f:new_controller():restore()
        equal(result, nil)
    end
end

function cases.RewardCannotStartBeforeExactSourceSuppression(Core)
    local f = fixture(Core)
    assert(f:capture())
    failed("SOURCE_NOT_SUPPRESSED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    local witness = f:source_witness(1)
    witness.payload.charges[1] = 99
    failed("SOURCE_MISMATCH", f.controller:begin_source(1, witness))
    witness = f:source_witness(1); witness.exact_matches = 2
    failed("SOURCE_MISMATCH", f.controller:begin_source(1, witness))
    witness = f:source_witness(1); witness.payload.flags = 0
    failed("SOURCE_MISMATCH", f.controller:begin_source(1, witness))
    witness = f:source_witness(1); witness.payload.expiration = 0
    failed("SOURCE_MISMATCH", f.controller:begin_source(1, witness))
end

function cases.OnlySelectedDuplicateRecordCanBeSuppressed(Core)
    local f = fixture(Core)
    assert(f:capture())
    failed("SOURCE_MISMATCH", f.controller:begin_source(1, f:source_witness(2)))
    f:suppress(1)
    equal(f.controller:assignments()[1].source_phase, 2)
    equal(f.controller:assignments()[2].source_phase, 0)
end

function cases.SuppressionNeedsALaterObservationAndOneAttempt(Core)
    local f = fixture(Core)
    assert(f:capture())
    local operation = f:source_operation(1)
    failed("LATER_OBSERVATION_REQUIRED", f.controller:observe(f:source_receipt(operation)))
    failed("MUTATION_PENDING", f.controller:begin_source(1, f:source_witness(1)))
    f.controller:on_observation_boundary()
    assert(f.controller:observe(f:source_receipt(operation)))
    failed("ALREADY_ATTEMPTED", f.controller:begin_source(1, f:source_witness(1)))
end

function cases.WrongReceiptsNeverCommitAnotherMutation(Core)
    local f = fixture(Core)
    assert(f:capture())
    local operation = f:source_operation(1)
    f.controller:on_observation_boundary()
    for _, key in ipairs({ "nonce", "index", "identity", "revision", "kind" }) do
        local receipt = f:source_receipt(operation)
        receipt[key] = type(receipt[key]) == "number" and 999 or "wrong"
        failed("RECEIPT_MISMATCH", f.controller:observe(receipt))
        equal(f.values.flpr_jkind, 1)
    end
    assert(f.controller:observe(f:source_receipt(operation)))
end

function cases.UncertainRemovalQuarantinesWithoutRewardOrRetry(Core)
    local f = fixture(Core)
    assert(f:capture())
    local operation = f:source_operation(1)
    f.controller:on_observation_boundary()
    local receipt = f:source_receipt(operation); receipt.remaining = 1
    failed("MUTATION_QUARANTINED", f.controller:observe(receipt))
    failed("ALREADY_ATTEMPTED", f.controller:begin_source(1, f:source_witness(1)))
    failed("SOURCE_NOT_SUPPRESSED", f.controller:begin_delivery(1, f:delivery_witness(1)))
end

function cases.PendingSuppressionSurvivesSaveAndRequiresObservation(Core)
    local f = fixture(Core)
    assert(f:capture())
    local operation = f:source_operation(1)
    f.controller = f:new_controller()
    assert(f.controller:restore())
    failed("MUTATION_PENDING", f.controller:begin_source(1, f:source_witness(1)))
    failed("LATER_OBSERVATION_REQUIRED", f.controller:observe(f:source_receipt(operation)))
    f.controller:on_observation_boundary()
    failed("RECEIPT_MISMATCH", f.controller:observe(f:source_receipt(operation)))
    local request = assert(f.controller:pending_observation())
    equal(request.mode, "observe-only")
    assert(f.controller:observe(f:source_receipt(request)))
end

function cases.JournalWritesFailClosedAtEveryCutBeforeOperationReturn(Core)
    local baseline = fixture(Core)
    assert(baseline:capture())
    local captured = baseline.writes
    baseline:source_operation(1)
    local count = baseline.writes - captured
    for cut = 1, count do
        local f = fixture(Core)
        assert(f:capture())
        f.fail_before = f.writes + cut
        assert(not pcall(function() f:source_operation(1) end))
        f.fail_before = nil
        local ok, result, reason = pcall(function() return f:new_controller():restore() end)
        assert(ok)
        if cut == 1 then assert(result) else failed("JOURNAL_INCOMPLETE", result, reason) end
    end
end

function cases.CommittedObservationCanRecoverInterruptedJournalClear(Core)
    local f = fixture(Core)
    assert(f:capture())
    local operation = f:source_operation(1)
    f.controller:on_observation_boundary()
    f.fail_before = f.writes + 2
    assert(not pcall(function() f.controller:observe(f:source_receipt(operation)) end))
    f.fail_before = nil
    f.controller = f:new_controller()
    assert(f.controller:restore())
    equal(f.values.flpr_jkind, 0)
    failed("ALREADY_ATTEMPTED", f.controller:begin_source(1, f:source_witness(1)))
end

function cases.DeliveryNeedsNonceBoundNewInstanceAndActualLootability(Core)
    for _, field in ipairs({ "created", "nonce_bound_instance", "lootable" }) do
        local f = fixture(Core)
        assert(f:capture()); f:suppress(1)
        local operation = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
        equal(operation.target_id, f:delivery_witness(1).target_id)
        local receipt = f:delivery_receipt(operation)
        failed("LATER_OBSERVATION_REQUIRED", f.controller:observe(receipt))
        f.controller:on_observation_boundary()
        receipt[field] = 0
        failed("MUTATION_QUARANTINED", f.controller:observe(receipt))
        failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    end
end

function cases.DeliveryAfterReloadVerifiesWithoutReissuing(Core)
    local f = fixture(Core)
    assert(f:capture()); f:suppress(1)
    local operation = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller = f:new_controller(); assert(f.controller:restore())
    failed("MUTATION_PENDING", f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller:on_observation_boundary()
    failed("RECEIPT_MISMATCH", f.controller:observe(f:delivery_receipt(operation)))
    assert(f.controller:observe(f:delivery_receipt(assert(f.controller:pending_observation()))))
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.controller:assignments()[1].delivery_phase, 2)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
end

function cases.OldSessionReceiptCannotConfirmSameRevisionNewCampaign(Core)
    local f = fixture(Core)
    assert(f:capture())
    local stale = f:source_receipt(f:source_operation(1))
    f.controller:destroy()
    f.values = {}
    assert(f:capture())
    local current = f:source_operation(1)
    equal(stale.nonce, current.nonce)
    f.controller:on_observation_boundary()
    failed("RECEIPT_MISMATCH", f.controller:observe(stale))
    assert(f.controller:observe(f:source_receipt(current)))
end

function cases.SparseArraysCannotSilentlyOmitSourcesOrTargets(Core)
    for _, field in ipairs({ "sources", "targets" }) do
        local f = fixture(Core)
        for i = 3, 4 do
            f.manifest[field][i] = clone(f.manifest[field][2])
            f.manifest[field][i].id = f.manifest[field][2].id .. i
            f.manifest[field][i].record.id = f.manifest[field][i].id
        end
        f.manifest[field][2] = nil
        local result = f:capture()
        equal(result, nil); equal(f.writes, 0)
    end
end

function cases.TargetUnavailabilityDoesNotChangeTheAssignment(Core)
    local f = fixture(Core)
    assert(f:capture()); f:suppress(1)
    local witness = f:delivery_witness(1); local expected = witness.target_id
    witness.accepts = 0
    failed("TARGET_UNAVAILABLE", f.controller:begin_delivery(1, witness))
    equal(f.controller:assignments()[1].target_id, expected)
    equal(f.controller:assignments()[1].delivery_phase, 0)
end

function cases.DestroyClearsTheControllerBeforeAnotherGame(Core)
    local f = fixture(Core)
    assert(f:capture())
    f.controller:destroy()
    failed("INACTIVE", f.controller:assignments())
    failed("INACTIVE", f.controller:begin_source(1, f:source_witness(1)))
    f.values = {}
    failed("INACTIVE", f.controller:restore())
end

function cases.GlobalAdapterRequiresCapabilitiesAndUsesOnlyNumericGlobals(Core, Adapter)
    failed("CAPABILITY", Adapter.new({}))
    local values = {}
    local adapter = assert(Adapter.new({
        EEex_GameState_GetGlobalInt = function(key) return values[key] or 0 end,
        EEex_GameState_SetGlobalInt = function(key, value)
            assert(type(value) == "number" and value == math.floor(value) and #key <= 32)
            values[key] = value
        end,
    }))
    local f = fixture(Core)
    local controller = assert(Core.new(adapter, function() return f.manifest end))
    assert(controller:capture(f.manifest, 17, "confirmed-new-game"))
    equal(values.flpr_state, 2)
    assert(controller:restore())
end

function cases.LimitsAndMalformedDataFailBeforeAnyWrites(Core)
    for _, change in ipairs({
        function(m) m.sources[1].quantity = 4097 end,
        function(m) m.targets[1].capacity = 4097 end,
        function(m) m.sources[1].record.record.charges[1] = 65536 end,
        function(m) m.sources[1].record.record.flags = 0/0 end,
        function(m) m.sources[1].record.record.cycle = m end,
    }) do
        local f = fixture(Core); change(f.manifest)
        local result = f:capture(); equal(result, nil); equal(f.writes, 0)
    end
end

function cases.GlobalWriteReadbackMustMatchBeforeOperationsArePublished(Core)
    local f = fixture(Core)
    function f.storage:set() end
    assert(not pcall(function() f:capture() end))
    equal(f.values.flpr_state, nil)
end

local function creature_targets(f, kind)
    for _, target in ipairs(f.manifest.targets) do target.record.kind = kind end
end

local function held_receipt(f, operation)
    local receipt = f:delivery_receipt(operation)
    receipt.lootable, receipt.held = 0, 1
    return receipt
end

function cases.CreatureTargetMayHoldExactInstanceWithoutClaimingLootability(Core)
    for _, kind in ipairs({ "actor", "cre-template" }) do
        local f = fixture(Core)
        creature_targets(f, kind)
        assert(f:capture()); f:suppress(1)
        local operation = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
        f.controller:on_observation_boundary()
        assert(f.controller:observe(held_receipt(f, operation)))
        equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
        equal(f.values.flpr_jkind, 0)
        failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
        f.controller = f:new_controller(); assert(f.controller:restore())
        equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
        failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    end
end

function cases.HeldReceiptQuarantinesForNonCreatureOrContradictoryEvidence(Core)
    for _, variant in ipairs({ "container", "untyped", "both", "missing", "malformed" }) do
        local f = fixture(Core)
        if variant == "container" then creature_targets(f, "container")
        elseif variant ~= "untyped" then creature_targets(f, "actor") end
        assert(f:capture()); f:suppress(1)
        local operation = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
        f.controller:on_observation_boundary()
        local receipt = held_receipt(f, operation)
        if variant == "both" then receipt.lootable = 1
        elseif variant == "missing" then receipt.held = nil
        elseif variant == "malformed" then receipt.held = 2 end
        failed("MUTATION_QUARANTINED", f.controller:observe(receipt))
        equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.quarantined)
    end
end

function cases.HeldPhaseIsNeverValidForSourcesOrNonCreatureTargetsOnRestore(Core)
    local f = fixture(Core)
    assert(f:capture()); f:suppress(1)
    f.values.flpr_d0001 = Core.PHASE.held + 1
    f.controller = f:new_controller()
    failed("ASSIGNMENT_INVALID", f.controller:restore())
    f.values.flpr_d0001 = Core.PHASE.pending + 1
    f.values.flpr_s0001 = Core.PHASE.held + 1
    f.controller = f:new_controller()
    local ok, reason = pcall(function() return f.controller:restore() end)
    assert(not ok and tostring(reason):find("PHASE_MISSING_OR_INVALID", 1, true))
end

function cases.HeldCommitRecoversInterruptedJournalClearWithoutRepeat(Core)
    local f = fixture(Core)
    creature_targets(f, "actor")
    assert(f:capture()); f:suppress(1)
    local operation = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller:on_observation_boundary()
    -- Persistent nonce, held phase, then journal clear.
    f.fail_before = f.writes + 3
    assert(not pcall(function() f.controller:observe(held_receipt(f, operation)) end))
    f.fail_before = nil
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.values.flpr_jkind, 0)
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
end

local function held_fixture(Core)
    local f = fixture(Core)
    creature_targets(f, "actor")
    assert(f:capture()); f:suppress(1)
    local operation = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller:on_observation_boundary()
    assert(f.controller:observe(held_receipt(f, operation)))
    return f, operation
end

local function drop_receipt(request)
    local receipt = clone(request)
    receipt.session, receipt.token = request.session, request.token
    receipt.exact_transferred_instance, receipt.lootable = 1, 1
    return receipt
end

function cases.HeldDropConfirmationIsObserveOnlyAndPersistsWithoutReplay(Core)
    local f, operation = held_fixture(Core)
    equal(f.values.flpr_n0001, operation.nonce)
    local before = f.writes
    local request = assert(f.controller:held_request(1))
    equal(request.mode, "observe-only"); equal(request.nonce, operation.nonce)
    equal(f.writes, before); equal(f.values.flpr_jkind, 0)
    local receipt = drop_receipt(request)
    failed("LATER_OBSERVATION_REQUIRED", f.controller:confirm_held(receipt))
    f.controller:on_observation_boundary()
    assert(f.controller:confirm_held(receipt))
    equal(f.writes, before + 1)
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.confirmed)
    failed("NOT_HELD", f.controller:confirm_held(receipt))
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.confirmed)
    equal(f.values.flpr_n0001, operation.nonce)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
end

function cases.HeldDropRejectsWrongBindingOrUnprovenPayloadWithoutWriting(Core)
    for _, field in ipairs({ "kind", "mode", "nonce", "revision", "source_id", "target_id", "session", "token" }) do
        local f = held_fixture(Core)
        local receipt = drop_receipt(assert(f.controller:held_request(1)))
        receipt[field] = "wrong"
        f.controller:on_observation_boundary()
        local before = f.writes
        failed("RECEIPT_MISMATCH", f.controller:confirm_held(receipt))
        equal(f.writes, before)
        equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
    end
    for _, alter in ipairs({
        function(r) r.payload.charges[2] = 9 end,
        function(r) r.payload.expiration = 0 end,
        function(r) r.payload.flags = 3 end,
        function(r) r.payload.resref = "another" end,
        function(r) r.exact_transferred_instance = nil end,
        function(r) r.lootable = 0 end,
    }) do
        local f = held_fixture(Core)
        local receipt = drop_receipt(assert(f.controller:held_request(1)))
        alter(receipt); f.controller:on_observation_boundary()
        local before = f.writes
        failed("HELD_DROP_UNPROVEN", f.controller:confirm_held(receipt))
        equal(f.writes, before)
    end
end

function cases.HeldRequestsAreStableWithinSessionAndInvalidAfterRestore(Core)
    local f = held_fixture(Core)
    local request = assert(f.controller:held_request(1))
    local second = assert(f.controller:held_request(1))
    equal(second.token, request.token)
    second.payload.charges[1] = 99
    equal(f.controller:held_request(1).payload.charges[1], 3)
    f.controller = f:new_controller(); assert(f.controller:restore())
    local fresh = assert(f.controller:held_request(1))
    f.controller:on_observation_boundary()
    failed("RECEIPT_MISMATCH", f.controller:confirm_held(drop_receipt(request)))
    assert(f.controller:confirm_held(drop_receipt(fresh)))
    f.controller:destroy()
    failed("INACTIVE", f.controller:confirm_held(drop_receipt(fresh)))
end

function cases.LegacyHeldSavesRemainReadableButCannotInventProvenance(Core)
    local f = held_fixture(Core)
    f.values.flpr_prov, f.values.flpr_n0001 = nil, nil
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
    failed("HELD_PROVENANCE_UNAVAILABLE", f.controller:held_request(1))
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
end

function cases.HeldNonceValidationRejectsMissingCorruptOrRepeatedIdentity(Core)
    for _, value in ipairs({ 0, -1, 1, 2147483647 }) do
        local f = held_fixture(Core)
        f.values.flpr_n0001 = value
        f.controller = f:new_controller()
        failed(value == 0 and "HELD_NONCE_MISSING" or "HELD_NONCE_INVALID", f.controller:restore())
    end
    local f = held_fixture(Core)
    f:suppress(2)
    local op = assert(f.controller:begin_delivery(2, f:delivery_witness(2)))
    f.controller:on_observation_boundary()
    assert(f.controller:observe(held_receipt(f, op)))
    f.values.flpr_n0002 = f.values.flpr_n0001
    failed("HELD_NONCE_INVALID", f:new_controller():restore())
    f.values.flpr_prov = 2
    failed("PROVENANCE_VERSION_INVALID", f:new_controller():restore())
end

function cases.HeldCommitEveryWriteCutRetainsNoReplayState(Core)
    for _, when in ipairs({ "fail_before", "fail_after" }) do
        for cut = 1, 3 do
            local f = fixture(Core)
            creature_targets(f, "actor")
            assert(f:capture()); f:suppress(1)
            local op = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
            f.controller:on_observation_boundary()
            f[when] = f.writes + cut
            assert(not pcall(function() f.controller:observe(held_receipt(f, op)) end))
            f[when] = nil
            f.controller = f:new_controller(); assert(f.controller:restore())
            local phase = f.controller:assignments()[1].delivery_phase
            local committed = cut > 2 or (cut == 2 and when == "fail_after")
            equal(phase, committed and Core.PHASE.held or Core.PHASE.in_flight)
            failed(committed and "ALREADY_ATTEMPTED" or "MUTATION_PENDING",
                f.controller:begin_delivery(1, f:delivery_witness(1)))
        end
    end
end

function cases.HeldConfirmationWriteFailureRemainsHeldOrConfirmedNeverPending(Core)
    for _, when in ipairs({ "fail_before", "fail_after" }) do
        local f = held_fixture(Core)
        local receipt = drop_receipt(assert(f.controller:held_request(1)))
        f.controller:on_observation_boundary()
        f[when] = f.writes + 1
        assert(not pcall(function() f.controller:confirm_held(receipt) end))
        f[when] = nil
        f.controller = f:new_controller(); assert(f.controller:restore())
        equal(f.controller:assignments()[1].delivery_phase,
            when == "fail_before" and Core.PHASE.held or Core.PHASE.confirmed)
        equal(f.values.flpr_jkind, 0)
        failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    end
end

function cases.HeldConfirmationCannotInterleaveAnUnfinishedWorldMutation(Core)
    local f = held_fixture(Core)
    local receipt = drop_receipt(assert(f.controller:held_request(1)))
    f:source_operation(2)
    f.controller:on_observation_boundary()
    local before = f.writes
    failed("MUTATION_PENDING", f.controller:confirm_held(receipt))
    equal(f.writes, before)
end

function cases.RestoreDoesNotEraseJournalThatConflictsWithPublishedHeldIdentity(Core)
    local f = fixture(Core)
    creature_targets(f, "actor")
    assert(f:capture()); f:suppress(1)
    local op = assert(f.controller:begin_delivery(1, f:delivery_witness(1)))
    f.controller:on_observation_boundary()
    f.fail_before = f.writes + 3
    assert(not pcall(function() f.controller:observe(held_receipt(f, op)) end))
    f.fail_before = nil
    f.values.flpr_n0001 = op.nonce - 1
    local before = f.writes
    failed("HELD_NONCE_INVALID", f:new_controller():restore())
    equal(f.writes, before); equal(f.values.flpr_jkind, 2)
end

-- Finding 5 (Chris, 2026-10-27): a reward on its way whose observation cannot
-- go on parks with the adapter's counts before it; the next visit of its place
-- settles it as arrived (held or confirmed) or pending for a new delivery.
local function delivery_on_its_way(Core, kind, settle)
    local f = fixture(Core)
    if kind then creature_targets(f, kind) end
    assert(f:capture()); f:suppress(1)
    local witness = f:delivery_witness(1); witness.settle = settle
    local operation = assert(f.controller:begin_delivery(1, witness))
    f.controller:on_observation_boundary()
    local receipt = clone(operation); receipt.session = operation.session; receipt.parked = 1
    return f, operation, receipt
end

local function parked(Core, kind)
    local f, operation, receipt = delivery_on_its_way(Core, kind, 77)
    assert(f.controller:park(receipt))
    return f, operation
end

local function settle_receipt(request, arrived, holder)
    local receipt = clone(request)
    receipt.session, receipt.token = request.session, request.token
    receipt.arrived, receipt.holder = arrived, holder
    return receipt
end

function cases.DeliveryKeepsTheAdaptersCountsInItsJournal(Core)
    local f = fixture(Core); assert(f:capture()); f:suppress(1)
    equal(f.values.flpr_jsettle, nil)
    local witness = f:delivery_witness(1); witness.settle = 2050
    assert(f.controller:begin_delivery(1, witness))
    equal(f.values.flpr_jsettle, 2050)
    local g = fixture(Core); assert(g:capture()); g:suppress(1)
    assert(g.controller:begin_delivery(1, g:delivery_witness(1)))
    equal(g.values.flpr_jsettle, 0)
    for _, value in ipairs({ 0, -1, 1.5, 2147483648, "1" }) do
        local h = fixture(Core); assert(h:capture()); h:suppress(1)
        local bad = h:delivery_witness(1); bad.settle = value
        local before = h.writes
        failed("WITNESS_INVALID", h.controller:begin_delivery(1, bad))
        equal(h.writes, before)
    end
end

function cases.ParkClosesTheJournalAndKeepsTheCountsWithTheReward(Core)
    local f, operation, receipt = delivery_on_its_way(Core, nil, 77)
    assert(f.controller:park(receipt))
    equal(f.values.flpr_jkind, 0); equal(f.values.flpr_b0001, 77)
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.parked)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    failed("NO_PENDING_DELIVERY", f.controller:park(receipt))
    -- The other rewards go on.
    f:suppress(2)
    assert(f.controller:begin_delivery(2, f:delivery_witness(2)))
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.parked)
    equal(f.controller:assignments()[2].delivery_phase, Core.PHASE.in_flight)
    equal(operation.index, 1)
end

function cases.ParkNeedsALaterBoundaryAMatchingReceiptAndCounts(Core)
    local f = fixture(Core); assert(f:capture()); f:suppress(1)
    local witness = f:delivery_witness(1); witness.settle = 5
    local operation = assert(f.controller:begin_delivery(1, witness))
    local receipt = clone(operation); receipt.session = operation.session; receipt.parked = 1
    failed("LATER_OBSERVATION_REQUIRED", f.controller:park(receipt))
    f.controller:on_observation_boundary()
    for field, value in pairs({ parked = 0, nonce = operation.nonce + 1, index = 2, kind = "suppress",
        identity = string.rep("e", 64), revision = string.rep("f", 64), session = {} }) do
        local wrong = clone(receipt); wrong.session = receipt.session; wrong[field] = value
        local before = f.writes
        failed("RECEIPT_MISMATCH", f.controller:park(wrong))
        equal(f.writes, before)
    end
    assert(f.controller:park(receipt))
    -- Begun without counts: it cannot park, and its journal stays.
    local g = delivery_on_its_way(Core, nil, nil)
    local op = assert(g.controller:pending_observation())
    local r = clone(op); r.session = op.session; r.parked = 1
    local before = g.writes
    failed("PARK_EVIDENCE_MISSING", g.controller:park(r))
    equal(g.writes, before); equal(g.values.flpr_jkind, 2)
    -- A source suppression has none.
    local h = fixture(Core); assert(h:capture())
    local source = h:source_operation(1); h.controller:on_observation_boundary()
    local s = h:source_receipt(source); s.parked = 1
    failed("NO_PENDING_DELIVERY", h.controller:park(s))
end

function cases.SaveWithARewardOnItsWayParksOnLoad(Core)
    local f = delivery_on_its_way(Core, nil, 77)
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.values.flpr_jkind, 2)
    assert(f.controller:park_unproven())
    equal(f.values.flpr_jkind, 0); equal(f.values.flpr_b0001, 77)
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.parked)
    failed("PENDING_RELOAD_UNPROVEN", f.controller:park_unproven())
    -- Begun without counts (CIR 1.0.2 and earlier): unproven, as before.
    local g = delivery_on_its_way(Core, nil, 77)
    g.values.flpr_jsettle = nil
    g.controller = g:new_controller(); assert(g.controller:restore())
    local before = g.writes
    failed("PENDING_RELOAD_UNPROVEN", g.controller:park_unproven())
    equal(g.writes, before); equal(g.values.flpr_jkind, 2)
    -- A source suppression on its way stays unproven.
    local h = fixture(Core); assert(h:capture()); h:source_operation(1)
    h.controller = h:new_controller(); assert(h.controller:restore())
    failed("PENDING_RELOAD_UNPROVEN", h.controller:park_unproven())
end

function cases.StoppedHostForfeitsTheCountsOfARewardOnItsWay(Core)
    local f = delivery_on_its_way(Core, nil, 77)
    f.controller:forfeit_settle()
    equal(f.values.flpr_jsettle, 0); equal(f.values.flpr_jkind, 2)
    f.controller = f:new_controller(); assert(f.controller:restore())
    failed("PENDING_RELOAD_UNPROVEN", f.controller:park_unproven())
    local g = fixture(Core); assert(g:capture())
    local before = g.writes
    g.controller:forfeit_settle(); equal(g.writes, before)
    g.controller:destroy(); g.controller:forfeit_settle(); equal(g.writes, before)
end

-- The world goes on without CIR after a stop: a save made then holds no counts
-- of a parked reward either, and stops on load, as one on its way does.
function cases.StoppedHostForfeitsTheCountsOfEveryParkedReward(Core)
    local f = parked(Core, "actor")
    f:suppress(2)
    local witness = f:delivery_witness(2); witness.settle = 99
    assert(f.controller:begin_delivery(2, witness))
    f.controller:forfeit_settle()
    equal(f.values.flpr_b0001, 0); equal(f.values.flpr_jsettle, 0); equal(f.values.flpr_jkind, 2)
    failed("PARKED_EVIDENCE_INVALID", f:new_controller():restore())
    -- A save made before the stop keeps them.
    local g = parked(Core, "actor")
    local saved = clone(g.values)
    g.controller:forfeit_settle(); equal(g.values.flpr_b0001, 0)
    g.values = saved
    g.controller = g:new_controller(); assert(g.controller:restore())
    equal(g.controller:assignments()[1].delivery_phase, Core.PHASE.parked); equal(g.values.flpr_b0001, 77)
end

function cases.ParkedRewardSettlesHeldConfirmedOrPendingAgain(Core)
    -- Arrived in a creature target: held, with an identity of its own.
    local f, operation = parked(Core, "actor")
    local request = assert(f.controller:parked_request(1))
    equal(request.kind, "settle-parked"); equal(request.mode, "observe-only"); equal(request.settle, 77)
    equal(request.target_id, operation.target_id); equal(request.source_id, operation.source_id)
    equal(request.payload.charges[1], 3)
    local before = f.writes
    equal(assert(f.controller:parked_request(1)).token, request.token); equal(f.writes, before)
    assert(f.controller:confirm_parked(settle_receipt(request, 1, "creature")))
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
    equal(f.values.flpr_n0001, operation.nonce + 1); equal(f.values.flpr_sequence, operation.nonce + 1)
    equal(f.values.flpr_b0001, 0)
    failed("NOT_PARKED", f.controller:confirm_parked(settle_receipt(request, 1, "creature")))
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
    -- In the party, on the ground or in a container: confirmed.
    for _, holder in ipairs({ "party", "ground", "container" }) do
        local g = parked(Core, holder == "container" and "container" or "actor")
        assert(g.controller:confirm_parked(settle_receipt(assert(g.controller:parked_request(1)), 1, holder)))
        equal(g.controller:assignments()[1].delivery_phase, Core.PHASE.confirmed)
        equal(g.values.flpr_n0001, 0)
        g.controller = g:new_controller(); assert(g.controller:restore())
        failed("ALREADY_ATTEMPTED", g.controller:begin_delivery(1, g:delivery_witness(1)))
    end
    -- Not arrived: pending, and delivered anew.
    local h = parked(Core, "container")
    assert(h.controller:confirm_parked(settle_receipt(assert(h.controller:parked_request(1)), 0)))
    equal(h.controller:assignments()[1].delivery_phase, Core.PHASE.pending)
    h.controller = h:new_controller(); assert(h.controller:restore())
    assert(h.controller:begin_delivery(1, h:delivery_witness(1)))
end

function cases.ParkedSettleRejectsWrongOrUnprovenReceiptsWithoutWriting(Core)
    for field, value in pairs({ kind = "verify-held-drop", mode = "execute-once", revision = string.rep("f", 64),
        source_id = "other", target_id = "other", settle = 78, session = {}, token = {} }) do
        local f = parked(Core, "actor")
        local receipt = settle_receipt(assert(f.controller:parked_request(1)), 1, "creature")
        receipt[field] = value
        local before = f.writes
        failed("RECEIPT_MISMATCH", f.controller:confirm_parked(receipt))
        equal(f.writes, before); equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.parked)
    end
    for _, alter in ipairs({
        function(r) r.arrived = 2 end,
        function(r) r.arrived = nil end,
        function(r) r.holder = nil end,
        function(r) r.holder = "elsewhere" end,
        function(r) r.payload.charges[2] = 9 end,
    }) do
        local f = parked(Core, "actor")
        local receipt = settle_receipt(assert(f.controller:parked_request(1)), 1, "creature")
        alter(receipt)
        local before = f.writes
        failed("PARKED_SETTLE_UNPROVEN", f.controller:confirm_parked(receipt))
        equal(f.writes, before)
    end
    -- Only a creature target can hold it, and only a store's stock is a store's.
    local f = parked(Core, "container")
    failed("PARKED_SETTLE_UNPROVEN",
        f.controller:confirm_parked(settle_receipt(assert(f.controller:parked_request(1)), 1, "creature")))
    failed("PARKED_SETTLE_UNPROVEN",
        f.controller:confirm_parked(settle_receipt(assert(f.controller:parked_request(1)), 1, "store")))
    f = parked(Core, "store")
    failed("PARKED_SETTLE_UNPROVEN",
        f.controller:confirm_parked(settle_receipt(assert(f.controller:parked_request(1)), 1, "container")))
    assert(f.controller:confirm_parked(settle_receipt(assert(f.controller:parked_request(1)), 1, "store")))
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.confirmed)
    -- Never while a world mutation is open, never with an old session's request.
    f = parked(Core, "actor")
    local request = assert(f.controller:parked_request(1))
    f:source_operation(2)
    failed("MUTATION_PENDING", f.controller:confirm_parked(settle_receipt(request, 1, "creature")))
    f = parked(Core, "actor")
    request = assert(f.controller:parked_request(1))
    f.controller = f:new_controller(); assert(f.controller:restore())
    failed("RECEIPT_MISMATCH", f.controller:confirm_parked(settle_receipt(request, 1, "creature")))
    failed("NOT_PARKED", f.controller:parked_request(2))
    f.controller:destroy()
    failed("INACTIVE", f.controller:parked_request(1))
end

function cases.ParkedRewardNeedsItsCountsAndAValidNonceOnRestore(Core)
    for _, value in ipairs({ 0, -1 }) do
        local f = parked(Core, "actor")
        f.values.flpr_b0001 = value
        failed("PARKED_EVIDENCE_INVALID", f:new_controller():restore())
    end
    -- A cut write's nonce (an observation's or a settle's) stays until its
    -- settle: replaced where it is held, cleared where it is not.
    for _, outcome in ipairs({ { 1, "creature" }, { 1, "party" }, { 0 } }) do
        local f, operation = parked(Core, "actor")
        f.values.flpr_n0001 = operation.nonce
        f.controller = f:new_controller(); assert(f.controller:restore())
        assert(f.controller:confirm_parked(settle_receipt(assert(f.controller:parked_request(1)),
            outcome[1], outcome[2])))
        equal(f.values.flpr_n0001, outcome[2] == "creature" and operation.nonce + 1 or 0)
        f.controller = f:new_controller(); assert(f.controller:restore())
    end
    -- No other one: beyond the sequence, the first, or on a container target.
    for _, case in ipairs({
        { "actor", function(f) return f.values.flpr_sequence + 1 end },
        { "actor", function() return 1 end },
        { "container", function(_, operation) return operation.nonce end },
    }) do
        local f, operation = parked(Core, case[1])
        f.values.flpr_n0001 = case[2](f, operation)
        failed("HELD_NONCE_INVALID", f:new_controller():restore())
    end
    -- Sources never park.
    local f = parked(Core, "actor")
    f.values.flpr_s0001 = Core.PHASE.parked + 1
    local ok, reason = pcall(function() return f:new_controller():restore() end)
    assert(not ok and tostring(reason):find("PHASE_MISSING_OR_INVALID", 1, true))
end

function cases.ParkEveryWriteCutParksAgainOrRestoresParked(Core)
    for _, when in ipairs({ "fail_before", "fail_after" }) do
        for cut = 1, 3 do
            local f, _, receipt = delivery_on_its_way(Core, "actor", 77)
            f[when] = f.writes + cut
            assert(not pcall(function() f.controller:park(receipt) end))
            f[when] = nil
            f.controller = f:new_controller(); assert(f.controller:restore())
            local phase = f.controller:assignments()[1].delivery_phase
            if phase == Core.PHASE.in_flight then
                equal(f.values.flpr_jkind, 2); assert(f.controller:park_unproven())
            end
            equal(f.values.flpr_jkind, 0)
            equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.parked)
            equal(f.values.flpr_b0001, 77)
            failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
        end
    end
end

-- The host stops on such a cut (FLPRHost:_disable) and forfeits the counts: a
-- save made after it never parks or settles that reward.
function cases.ParkWriteCutThenStopNeverSettlesFromALaterSave(Core)
    for _, when in ipairs({ "fail_before", "fail_after" }) do
        for cut = 1, 3 do
            local f, _, receipt = delivery_on_its_way(Core, "actor", 77)
            f[when] = f.writes + cut
            assert(not pcall(function() f.controller:park(receipt) end))
            f[when] = nil
            f.controller:forfeit_settle()
            local controller = f:new_controller()
            local restored, reason = controller:restore()
            if restored then
                equal(f.values.flpr_jkind, 2)
                failed("PENDING_RELOAD_UNPROVEN", controller:park_unproven())
            else
                equal(reason, "PARKED_EVIDENCE_INVALID")
            end
        end
    end
end

-- Its identity is published before its phase: a cut leaves it parked with
-- that nonce, which restore keeps, or held. Never pending, never stopped.
function cases.SettleEveryWriteCutStaysParkedOrBecomesHeld(Core)
    for _, when in ipairs({ "fail_before", "fail_after" }) do
        for cut = 1, 4 do
            local f = parked(Core, "actor")
            local receipt = settle_receipt(assert(f.controller:parked_request(1)), 1, "creature")
            f[when] = f.writes + cut
            assert(not pcall(function() f.controller:confirm_parked(receipt) end))
            f[when] = nil
            f.controller = f:new_controller(); assert(f.controller:restore())
            local phase = f.controller:assignments()[1].delivery_phase
            if phase == Core.PHASE.parked then
                -- The next visit settles it, with an identity of its own.
                assert(f.controller:confirm_parked(settle_receipt(assert(f.controller:parked_request(1)), 1, "creature")))
                phase = f.controller:assignments()[1].delivery_phase
            end
            equal(phase, Core.PHASE.held); equal(f.values.flpr_n0001, f.values.flpr_sequence)
            f.controller = f:new_controller(); assert(f.controller:restore())
        end
    end
end

return cases
