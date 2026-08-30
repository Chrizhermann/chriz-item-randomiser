return function(context)
    local Core = context.Core
    local FakeEngine = context.FakeEngine
    local test = context.test
    local equal = context.equal

    local function new_pending(global_name, slot_value, configure)
        local engine = FakeEngine.new({ globals = { [global_name] = slot_value } })
        if configure then
            configure(engine)
        end
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        return controller, engine
    end

    test("LivingPrimaryExecutesAndCommitsSynchronously", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:count_exact("primary", "tstitema", { 3, 2, 1 }), 1)
        equal(#engine.executions, 1)
        equal(engine.executions[1].endpoint_id, "primary")
    end)

    test("MissingSpawnGatedPrimaryDefersWithoutFallback", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake.endpoints.primary.present = false
            fake.endpoints.primary.settled = true
        end)
        controller:poll()
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(#engine.executions, 0)
        equal(#engine.endpoints.fallback.items, 0)
    end)

    test("UnloadedEndpointAreaDefersWithoutFallback", function()
        local engine = FakeEngine.new({
            globals = { fl1t1 = 1 },
            current_area = "different-area",
        })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.executions, 0)
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
    end)

    test("UnloadedEarlierTokenDoesNotBlockCurrentAreaToken", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t0 = {
            global = "fl1t0",
            unit_id = "test:unit-away",
            item_id = "test:item-away",
            tier = "1",
            compact_token = "0",
            item_resref = "tstaway",
            charges = { 0, 0, 0 },
            variant_policy = "exact",
            enabled = 1,
        }
        manifest.endpoints_by_id.away = {
            area = "other-area",
            target_kind = "ground",
            target_identity = "-",
            x = 50,
            y = 60,
            capacity = 1,
            static_policy = "authored-static",
            fallback_id = "-",
            adapter = "-",
            external_delivery = 0,
            enabled = 1,
        }
        manifest.slots_by_tier_value["1"][4] = {
            slot_id = "test:slot-away",
            tier = "1",
            slot_value = 4,
            endpoint_id = "away",
            weight = 1,
            progression_band = "test",
            enabled = 1,
        }
        local engine = FakeEngine.new({ globals = { fl1t0 = 4, fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(#engine.executions, 1)
        equal(engine.executions[1].endpoint_id, "primary")
        equal(engine:get_global("fl1t0"), 4)
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("MissingEffectiveItemIsQuarantined", function()
        local engine = FakeEngine.new({
            globals = { fl1t1 = 1 },
            missing_items = { tstitema = true },
        })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.executions, 0)
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global(Core.quarantine_global("fl1t1")), Core.REASON.ITEM_MISSING)
    end)

    test("DeadAndFullPrimaryUseAuthorizedFallback", function()
        for _, state in ipairs({ "dead", "full" }) do
            local controller, engine = new_pending("fl1t1", 1, function(fake)
                if state == "dead" then
                    fake.endpoints.primary.alive = false
                else
                    fake.endpoints.primary.full = true
                end
            end)
            controller:poll()
            equal(engine:get_global("fl1t1"), -1, state .. " primary did not commit")
            equal(#engine.executions, 1)
            equal(engine.executions[1].endpoint_id, "fallback",
                state .. " primary bypassed its authorized fallback")
        end
    end)

    test("SeveralUnitsSharingOneEndpointCommitIndependently", function()
        local engine = FakeEngine.new({ globals = { fl1t1 = 3, fl1t2 = 3 } })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))

        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global("fl1t2"), 3)

        controller:poll()
        equal(engine:get_global("fl1t2"), -1)
        equal(#engine.endpoints.primary.items, 2)
        equal(#engine.executions, 2)
    end)

    test("ExactChargeSignatureControlsBaselineAndVerification", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake:add_item("primary", "tstitema", { 3, 2, 9 })
            fake:add_item("primary", "otheritm", { 3, 2, 1 })
        end)
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine.executions[1].item.charges[1], 3)
        equal(engine.executions[1].item.charges[2], 2)
        equal(engine.executions[1].item.charges[3], 1)
        equal(engine:count_exact("primary", "tstitema", { 3, 2, 1 }), 1)
    end)

    test("LegacyRandomBookExecutesConcreteVariant", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t1.item_resref = "book08"
        manifest.tokens_by_global.fl1t1.variant_policy = "legacy-random-book"
        manifest.tokens_by_global.fl1t1.charges = { 0, 0, 0 }
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 }, variant_choice = 1 })
        engine:add_item("primary", "book03", { 0, 0, 0 })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine.executions[1].item.resref, "book03")
        equal(engine:count_exact("primary", "book03", { 0, 0, 0 }), 2)
    end)

    test("MissingExactResultFailsClosedWithoutSameGameStateRetry", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake.execute_create = false
        end)
        controller:poll()
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global(Core.quarantine_global("fl1t1")),
            Core.REASON.EXECUTION_FAILURE)
        equal(#engine.executions, 1)

        controller:poll()
        equal(#engine.executions, 1, "failed delivery retried in the same GameState")
    end)

    test("ExecutionErrorAfterCreationCommitsByExactObservation", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake.execute_error = "synthetic instant executor error"
            fake.execute_error_after_create = true
        end)
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:count_exact("primary", "tstitema", { 3, 2, 1 }), 1)
        equal(#engine.executions, 1)
    end)

    test("ExecutingPhaseIsDurableBeforeInstantExecution", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        local executing_index
        local execution_index
        for index, event in ipairs(engine.events) do
            if event.kind == "set_global" and event.name == Core.GLOBALS.phase and
                event.value == Core.PHASE.EXECUTING then
                executing_index = index
            elseif event.kind == "execute_delivery" then
                execution_index = index
            end
        end
        if not executing_index or not execution_index or executing_index >= execution_index then
            error("instant execution occurred before the EXECUTING phase was durable")
        end
    end)

    test("UnobservableLockedEndpointNeverSwitchesFallback", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake.execute_hook = function(current)
                current.endpoints.primary.observable = false
            end
        end)
        controller:poll()
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global(Core.GLOBALS.reason), Core.REASON.LOCKED_UNOBSERVABLE)
        equal(#engine.executions, 1)
        equal(#engine.endpoints.fallback.items, 0)
    end)

    test("DeliveryThatFillsLastContainerSlotStillCommits", function()
        local controller, engine = new_pending("fl1t1", 2, function(fake)
            fake.execute_hook = function(current)
                current.endpoints.container.full = true
            end
        end)
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(#engine.endpoints.container.items, 1)
    end)

    test("CommitWritesAssignmentBeforeClearingJournal", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()

        local assignment_index
        local first_clear_index
        for index, event in ipairs(engine.events) do
            if event.kind == "set_global" and event.name == "fl1t1" and event.value == -1 then
                assignment_index = index
            elseif assignment_index and event.kind == "set_global" and event.value == 0 and
                event.name ~= Core.GLOBALS.sequence and not first_clear_index then
                first_clear_index = index
            end
        end
        if not assignment_index or not first_clear_index or assignment_index >= first_clear_index then
            error("journal clearing preceded the delivered assignment write")
        end
    end)
end
