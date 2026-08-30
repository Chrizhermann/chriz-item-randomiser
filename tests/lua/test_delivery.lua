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

    test("LivingPrimaryTargetReceivesQueuedDelivery", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "primary")
        equal(engine:get_global("fl1t1"), 1, "submission committed the token")
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUEUED)
    end)

    test("UnloadedEndpointAreaWaitsWithoutChoosingFallback", function()
        local engine = FakeEngine.new({
            globals = { fl1t1 = 1 },
            current_area = "different-area",
        })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.queues, 0)
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
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "primary")
        equal(engine:get_global("fl1t0"), 4)
    end)

    test("MissingEffectiveItemIsQuarantined", function()
        local engine = FakeEngine.new({
            globals = { fl1t1 = 1 },
            missing_items = { tstitema = true },
        })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.queues, 0)
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global(Core.quarantine_global("fl1t1")), Core.REASON.ITEM_MISSING)
    end)

    test("DeadMissingAndFullPrimaryUseAuthorizedFallback", function()
        for _, state in ipairs({ "dead", "missing", "full" }) do
            local controller, engine = new_pending("fl1t1", 1, function(fake)
                if state == "dead" then
                    fake.endpoints.primary.alive = false
                elseif state == "missing" then
                    fake.endpoints.primary.present = false
                else
                    fake.endpoints.primary.full = true
                end
            end)
            controller:poll()
            equal(#engine.queues, 1, state .. " primary did not queue")
            equal(engine.queues[1].endpoint_id, "fallback", state .. " primary bypassed fallback")
        end
    end)

    test("TransientMissingPrimaryWaitsForSettlingPolicy", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake.endpoints.primary.present = false
            fake.endpoints.primary.settled = false
        end)
        controller:poll()
        equal(#engine.queues, 0)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global("fl1t1"), 1)

        engine.endpoints.primary.settled = true
        controller:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "fallback")
    end)

    test("SeveralUnitsSharingOneEndpointCommitIndependently", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 3, fl1t2 = 3 } })
        local controller = assert(Core.new(engine, manifest))

        controller:poll()
        engine:process_queue()
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global("fl1t2"), 3)

        controller:poll()
        engine:process_queue()
        controller:poll()
        equal(engine:get_global("fl1t2"), -1)
        equal(#engine.endpoints.primary.items, 2)
    end)

    test("ExactChargeSignatureControlsBaselineAndVerification", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake:add_item("primary", "tstitema", { 3, 2, 9 })
            fake:add_item("primary", "otheritm", { 3, 2, 1 })
        end)
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.baseline), 0,
            "nonmatching item instance polluted the baseline")
        engine:process_queue()
        equal(engine.queues[1].item.charges[1], 3)
        equal(engine.queues[1].item.charges[2], 2)
        equal(engine.queues[1].item.charges[3], 1)
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("LegacyRandomBookPersistsAndVerifiesConcreteVariant", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t1.item_resref = "book08"
        manifest.tokens_by_global.fl1t1.variant_policy = "legacy-random-book"
        manifest.tokens_by_global.fl1t1.charges = { 0, 0, 0 }
        local engine = FakeEngine.new({
            globals = { fl1t1 = 1 },
            variant_choice = 1,
        })
        engine:add_item("primary", "book04", { 0, 0, 0 })
        engine:add_item("primary", "book03", { 0, 0, 0 })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.variant), 1)
        equal(engine:get_global(Core.GLOBALS.baseline), 1)
        equal(engine.queues[1].item.resref, "book03")

        engine:process_queue({ create = false, acknowledge = true })
        local recreated = assert(Core.new(engine, manifest))
        recreated:poll()
        equal(engine:get_global("fl1t1"), 1,
            "pre-existing chosen variant satisfied the persisted transaction")
        engine:add_item("primary", "book03", { 0, 0, 0 })
        recreated:poll()
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("VerificationIsRelativeToPersistedBaseline", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake:add_item("primary", "tstitema", { 3, 2, 1 })
        end)
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.baseline), 1)
        engine:process_queue({ create = false, acknowledge = true })
        controller:poll()
        equal(engine:get_global("fl1t1"), 1,
            "a pre-existing copy satisfied a queued transaction")
        engine:add_item("primary", "tstitema", { 3, 2, 1 })
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
    end)

    test("QueueAcknowledgementWithoutCreationDoesNotCommit", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        engine:process_queue({ create = false, acknowledge = true })
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.ACKED)
        controller:poll()
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.queues, 1, "ACK-only transaction was replayed in the same GameState")
    end)

    test("QueuePhaseIsPersistedBeforeSubmission", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        local queued_phase_index
        local submission_index
        for index, event in ipairs(engine.events) do
            if event.kind == "set_global" and event.name == Core.GLOBALS.phase and
                event.value == Core.PHASE.QUEUED then
                queued_phase_index = index
            elseif event.kind == "queue_delivery" then
                submission_index = index
            end
        end
        if not queued_phase_index or not submission_index or queued_phase_index >= submission_index then
            error("queue submission occurred before the QUEUED phase was durable")
        end
    end)

    test("DefinitiveQueueRejectionDoesNotMonopolizeJournal", function()
        local manifest = FakeEngine.manifest()
        local engine = FakeEngine.new({ globals = { fl1t1 = 1, fl1t2 = 3 } })
        engine.queue_accept = false
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(#engine.queues, 0)
        equal(engine:get_global(Core.quarantine_global("fl1t1")),
            Core.REASON.QUEUE_FAILURE)
        equal(engine:get_global(Core.quarantine_global("fl1t2")),
            Core.REASON.QUEUE_FAILURE)

        engine.queue_accept = true
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUEUED)
        equal(#engine.queues, 1)
        equal(engine.queues[1].item.resref, "tstitema")
    end)

    test("LockedEndpointNeverSwitchesAfterQueueing", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        engine.endpoints.primary.present = false
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(engine:get_global("fl1t1"), 1)
        equal(#engine.queues, 1, "transaction switched to a fallback after queueing")
    end)

    test("PostMortemCorpseCountCannotCommit", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        engine.endpoints.primary.alive = false
        engine:process_queue({ create = true, acknowledge = true })
        controller:poll()
        equal(engine:get_global("fl1t1"), 1,
            "an item trapped in corpse inventory committed the token")
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUARANTINED)
        equal(#engine.endpoints.primary.items, 1)

        controller:on_game_state_destroyed()
        engine:discard_queues()
        controller:poll()
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        controller:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "fallback")
        engine:process_queue()
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(#engine.endpoints.fallback.items, 1)
    end)

    test("DeliveryThatFillsLastContainerSlotStillCommits", function()
        local controller, engine = new_pending("fl1t1", 2)
        controller:poll()
        engine:process_queue({ create = true, acknowledge = true })
        engine.endpoints.container.full = true
        controller:poll()
        equal(engine:get_global("fl1t1"), -1)
        equal(#engine.endpoints.container.items, 1)
    end)

    test("UnresolvableAuthorizedChainIsQuarantined", function()
        local controller, engine = new_pending("fl1t1", 1, function(fake)
            fake.endpoints.primary.present = false
            fake.endpoints.fallback.present = false
        end)
        controller:poll()
        equal(#engine.queues, 0)
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global(Core.quarantine_global("fl1t1")),
            Core.REASON.ENDPOINT_UNAVAILABLE)
    end)

    test("CommitWritesAssignmentBeforeClearingJournal", function()
        local controller, engine = new_pending("fl1t1", 1)
        controller:poll()
        engine:process_queue()
        local event_start = #engine.events + 1
        controller:poll()

        local assignment_index
        local first_clear_index
        for index = event_start, #engine.events do
            local event = engine.events[index]
            if event.kind == "set_global" and event.name == "fl1t1" and event.value == -1 then
                assignment_index = index
            elseif event.kind == "set_global" and event.value == 0 and
                event.name ~= Core.GLOBALS.sequence and not first_clear_index then
                first_clear_index = index
            end
        end
        if not assignment_index or not first_clear_index or assignment_index >= first_clear_index then
            error("journal clearing preceded the delivered assignment write")
        end
    end)
end
