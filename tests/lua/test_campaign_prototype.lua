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
    f.fail_before = f.writes + 2
    assert(not pcall(function() f.controller:observe(held_receipt(f, operation)) end))
    f.fail_before = nil
    f.controller = f:new_controller(); assert(f.controller:restore())
    equal(f.values.flpr_jkind, 0)
    equal(f.controller:assignments()[1].delivery_phase, Core.PHASE.held)
    failed("ALREADY_ATTEMPTED", f.controller:begin_delivery(1, f:delivery_witness(1)))
end

return cases
