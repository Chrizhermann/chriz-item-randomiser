return function(context)
    local Core = context.Core
    local FakeEngine = context.FakeEngine
    local test = context.test
    local equal = context.equal
    local truthy = context.truthy

    test("PreparedTransactionPersistsCompleteNumericIdentity", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()

        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUEUED)
        truthy(engine:get_global(Core.GLOBALS.token) > 0)
        truthy(engine:get_global(Core.GLOBALS.unit) > 0)
        truthy(engine:get_global(Core.GLOBALS.endpoint) > 0)
        equal(engine:get_global(Core.GLOBALS.quantity), 1)
        equal(engine:get_global(Core.GLOBALS.charge1), 3)
        equal(engine:get_global(Core.GLOBALS.charge2), 2)
        equal(engine:get_global(Core.GLOBALS.charge3), 1)
        for index = 1, 4 do
            equal(engine:get_global(Core.GLOBALS["fingerprint" .. index]), manifest.fingerprint[index])
        end
        truthy(engine:get_global(Core.GLOBALS.nonce) > 0)
    end)

    test("ClearPublishesNoneBeforeScrubbingJournalFields", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:process_queue()
        local event_start = #engine.events + 1
        controller:poll()

        local none_index
        local scrub_index
        for index = event_start, #engine.events do
            local event = engine.events[index]
            if event.kind == "set_global" and event.name == Core.GLOBALS.phase and
                event.value == Core.PHASE.NONE then
                none_index = index
            elseif event.kind == "set_global" and event.value == 0 and
                event.name ~= Core.GLOBALS.phase and event.name ~= Core.GLOBALS.sequence and
                not scrub_index then
                scrub_index = index
            end
        end
        if not none_index or not scrub_index or none_index >= scrub_index then
            error("journal identity was scrubbed before NONE became the clear commit marker")
        end
    end)

    test("RecreatedControllerDoesNotReplayQueuedTransaction", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(engine, manifest))
        first:poll()
        equal(#engine.queues, 1)

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(#engine.queues, 1, "persisted queued transaction was blindly replayed")
        equal(engine:get_global("fl1t1"), 1)
    end)

    test("ExternalDeliveredValueCannotCancelPendingQueueInSameGameState", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:set_global("fl1t1", -1)
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUEUED)
        equal(#engine.queues, 1)

        controller:on_game_state_destroyed()
        engine:discard_queues()
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("ExternalDeliveredValueAfterAckReleasesJournal", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1, fl1t2 = 3 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:process_queue({ create = false, acknowledge = true })
        engine:set_global("fl1t1", -1)
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        controller:poll()
        equal(#engine.queues, 2)
        equal(engine.queues[2].item.resref, "tstitemb")
    end)

    test("ExternalDeliveredValueAfterTeardownDoesNotNeedOldEndpoint", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:set_global("fl1t1", -1)
        controller:on_game_state_destroyed()
        engine:discard_queues()
        engine.endpoints.primary.observable = false
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("PersistedFingerprintMismatchFailsClosed", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(engine, manifest))
        first:poll()

        local changed = FakeEngine.manifest()
        changed.fingerprint = { 111, 222, 333, 444 }
        local recreated = assert(Core.new(engine, changed))
        recreated:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.queues, 1)
    end)

    test("CorruptEndpointSelectorCannotEscapeAuthorizedChain", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:discard_queues()

        local endpoint_ids = {}
        for endpoint_id in pairs(manifest.endpoints_by_id) do
            endpoint_ids[#endpoint_ids + 1] = endpoint_id
        end
        table.sort(endpoint_ids)
        local container_selector
        for selector, endpoint_id in ipairs(endpoint_ids) do
            if endpoint_id == "container" then
                container_selector = selector
            end
        end
        engine:set_global(Core.GLOBALS.endpoint, assert(container_selector))
        engine:set_global(Core.GLOBALS.phase, Core.PHASE.PREPARED)

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(#engine.queues, 0, "corrupt journal rerouted a prepared delivery")
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global(Core.GLOBALS.reason), Core.REASON.TRANSACTION_INVALID)
        equal(engine:get_global("fl1t1"), 1)
    end)

    test("PreparedRecoveryReplansBeforeAnySubmission", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:discard_queues()
        engine:set_global(Core.GLOBALS.phase, Core.PHASE.PREPARED)
        engine.endpoints.primary.alive = false

        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(#engine.queues, 0, "PREPARED recovery submitted without replanning")
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        recreated:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "fallback")
    end)

    test("PreparedRecoveryAcceptsAssignmentRemovalOrReassignment", function()
        local cases = {
            {
                assignment = 0,
                expected_resref = "tstitemb",
                expected_endpoint = "primary",
            },
            {
                assignment = 2,
                expected_resref = "tstitema",
                expected_endpoint = "container",
            },
        }

        for _, case in ipairs(cases) do
            local manifest = FakeEngine.manifest()
            local engine = FakeEngine.new({ globals = { fl1t1 = 1, fl1t2 = 3 } })
            local controller = assert(Core.new(engine, manifest))
            controller:poll()
            engine:discard_queues()
            engine:set_global(Core.GLOBALS.phase, Core.PHASE.PREPARED)
            engine:set_global("fl1t1", case.assignment)

            local recreated = assert(Core.new(engine, manifest))
            recreated:poll()
            equal(#engine.queues, 0, "PREPARED recovery submitted without replanning")
            equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)

            recreated:poll()
            equal(#engine.queues, 1, "recovered PREPARED record blocked fresh work")
            equal(engine.queues[1].item.resref, case.expected_resref)
            equal(engine.queues[1].endpoint_id, case.expected_endpoint)
        end
    end)

    test("StaleAcknowledgementIsIgnoredAndNonceIsMonotonic", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1, fl1t2 = 3 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        local first_nonce = engine:get_global(Core.GLOBALS.nonce)
        controller:ack(first_nonce + 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUEUED)
        controller:ack(first_nonce)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.ACKED)

        engine:add_item("primary", "tstitema", { 3, 2, 1 })
        controller:poll()
        controller:poll()
        local second_nonce = engine:get_global(Core.GLOBALS.nonce)
        truthy(second_nonce > first_nonce, "transaction nonce was reused after commit")
    end)

    test("GameStateTeardownAllowsObservationThenRetry", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:process_queue({ create = false, acknowledge = true })
        controller:poll()
        equal(#engine.queues, 1)

        controller:on_game_state_destroyed()
        engine:discard_queues()
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE,
            "post-teardown observation did not make the absent delivery retryable")
        controller:poll()
        equal(#engine.queues, 1, "post-teardown retry was not prepared")
        equal(engine:get_global("fl1t1"), 1)
    end)

    test("PostTeardownRetryMayReselectAuthorizedFallback", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        controller:on_game_state_destroyed()
        engine:discard_queues()
        engine.endpoints.primary.alive = false

        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        controller:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "fallback")
    end)

    test("PostTeardownUnobservableLockCannotCauseDuplicateRetry", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        engine:process_queue({ create = true, acknowledge = true })
        controller:on_game_state_destroyed()
        engine:discard_queues()
        engine.endpoints.primary.observable = false

        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.endpoints.primary.items, 1)

        engine.endpoints.primary.observable = true
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(#engine.endpoints.primary.items, 1,
            "unobservable post-teardown endpoint triggered a duplicate retry")
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
        equal(#engine.errors, 2, "a distinct callback site was hidden by deduplication")
    end)
end
