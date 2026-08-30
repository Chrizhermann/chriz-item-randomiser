return function(context)
    local Core = context.Core
    local FakeEngine = context.FakeEngine
    local test = context.test
    local equal = context.equal
    local truthy = context.truthy

    local function seed_transaction(controller, engine, manifest, phase, endpoint_id, baseline)
        local token = manifest.tokens_by_global.fl1t1
        controller:_persist_transaction(token, 1, endpoint_id or "primary", baseline or 0, 0, nil)
        engine:set_global(Core.GLOBALS.phase, phase)
    end

    test("ExecutingTransactionPublishesCompleteNumericIdentity", function()
        local manifest = FakeEngine.manifest()
        local captured
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        engine.execute_hook = function(current)
            captured = {
                phase = current:get_global(Core.GLOBALS.phase),
                token = current:get_global(Core.GLOBALS.token),
                unit = current:get_global(Core.GLOBALS.unit),
                endpoint = current:get_global(Core.GLOBALS.endpoint),
                quantity = current:get_global(Core.GLOBALS.quantity),
                charge1 = current:get_global(Core.GLOBALS.charge1),
                charge2 = current:get_global(Core.GLOBALS.charge2),
                charge3 = current:get_global(Core.GLOBALS.charge3),
            }
        end
        local controller = assert(Core.new(engine, manifest))
        controller:poll()

        equal(captured.phase, Core.PHASE.EXECUTING)
        truthy(captured.token > 0)
        truthy(captured.unit > 0)
        truthy(captured.endpoint > 0)
        equal(captured.quantity, 1)
        equal(captured.charge1, 3)
        equal(captured.charge2, 2)
        equal(captured.charge3, 1)
    end)

    test("ClearPublishesNoneBeforeScrubbingJournalFields", function()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        controller:poll()

        local none_index
        local scrub_index
        for index, event in ipairs(engine.events) do
            if event.kind == "set_global" and event.name == Core.GLOBALS.phase and
                event.value == Core.PHASE.NONE then
                none_index = index
            elseif none_index and event.kind == "set_global" and event.value == 0 and
                event.name ~= Core.GLOBALS.phase and event.name ~= Core.GLOBALS.sequence and
                not scrub_index then
                scrub_index = index
            end
        end
        if not none_index or not scrub_index or none_index >= scrub_index then
            error("journal identity was scrubbed before NONE became the clear marker")
        end
    end)

    test("PersistedExecutingDeltaCommitsWithoutReexecution", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(engine, manifest))
        seed_transaction(first, engine, manifest, Core.PHASE.EXECUTING)
        engine:add_item("primary", "tstitema", { 3, 2, 1 })

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(#engine.executions, 0)
    end)

    test("PersistedExecutingWithoutDeltaFailsClosedWithoutReplay", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(engine, manifest))
        seed_transaction(first, engine, manifest, Core.PHASE.EXECUTING)

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global(Core.quarantine_global("fl1t1")),
            Core.REASON.EXECUTION_FAILURE)
        equal(#engine.executions, 0)
    end)

    test("PersistedVerifiedTransactionCompletesCommit", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(engine, manifest))
        seed_transaction(first, engine, manifest, Core.PHASE.VERIFIED)
        engine:add_item("primary", "tstitema", { 3, 2, 1 })

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(#engine.executions, 0)
    end)

    test("PersistedFingerprintMismatchFailsClosed", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(engine, manifest))
        seed_transaction(first, engine, manifest, Core.PHASE.EXECUTING)

        local changed = FakeEngine.manifest()
        changed.fingerprint = { 111, 222, 333, 444 }
        local recreated = assert(Core.new(engine, changed))
        recreated:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.executions, 0)
    end)

    test("CorruptEndpointSelectorCannotEscapeAuthorizedChain", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        seed_transaction(controller, engine, manifest, Core.PHASE.PREPARED)

        local endpoint_ids = {}
        for endpoint_id in pairs(manifest.endpoints_by_id) do
            endpoint_ids[#endpoint_ids + 1] = endpoint_id
        end
        table.sort(endpoint_ids)
        for selector, endpoint_id in ipairs(endpoint_ids) do
            if endpoint_id == "container" then
                engine:set_global(Core.GLOBALS.endpoint, selector)
            end
        end

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global(Core.GLOBALS.reason), Core.REASON.TRANSACTION_INVALID)
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.executions, 0)
    end)

    test("PreparedRecoveryReplansBeforeExecution", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        seed_transaction(controller, engine, manifest, Core.PHASE.PREPARED)
        engine.endpoints.primary.alive = false

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(#engine.executions, 0, "PREPARED recovery executed without replanning")
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        recreated:poll()
        recreated:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(#engine.executions, 1)
        equal(engine.executions[1].endpoint_id, "fallback")
    end)

    test("FailedExecutionRetriesOnlyAfterGameStateTeardown", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        engine.execute_create = false
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(#engine.executions, 1)

        controller:poll()
        engine.execute_create = true
        controller:poll()
        equal(#engine.executions, 1)

        controller:on_game_state_destroyed()
        controller:poll()
        equal(#engine.executions, 2)
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("UnobservableExecutingLockCanLaterVerifyWithoutDuplicate", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        seed_transaction(controller, engine, manifest, Core.PHASE.EXECUTING)
        engine:add_item("primary", "tstitema", { 3, 2, 1 })
        engine.endpoints.primary.observable = false

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.executions, 0)

        engine.endpoints.primary.observable = true
        recreated:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(#engine.executions, 0)
        equal(engine:count_exact("primary", "tstitema", { 3, 2, 1 }), 1)
    end)

    test("CallbackErrorsAreContainedAndDeduplicated", function()
        local engine = FakeEngine.new()
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        local function broken()
            error("synthetic callback failure")
        end
        equal(controller:guarded_callback("poll", broken), false)
        equal(controller:guarded_callback("poll", broken), false)
        equal(#engine.errors, 1, "duplicate callback errors were reported")
        equal(engine.errors[1].code, "CALLBACK_ERROR")
        controller:guarded_callback("other", broken)
        equal(#engine.errors, 2, "a distinct callback site was hidden")
    end)
end
