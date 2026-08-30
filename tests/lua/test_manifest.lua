return function(context)
    local Core = context.Core
    local FakeEngine = context.FakeEngine
    local test = context.test
    local equal = context.equal
    local truthy = context.truthy

    test("ManifestRejectsSchemaBackendAndFingerprintMismatch", function()
        local cases = {
            { field = "schema", value = "future-schema", code = "MANIFEST_SCHEMA" },
            { field = "backend", value = "legacy-bcs-v1", code = "MANIFEST_BACKEND" },
            { field = "fingerprint", value = { 101, 202, 303 }, code = "MANIFEST_FINGERPRINT" },
        }
        for _, case in ipairs(cases) do
            local manifest = FakeEngine.manifest()
            manifest[case.field] = case.value
            local controller, code = Core.new(FakeEngine.new(), manifest)
            equal(controller, nil, "invalid manifest was accepted")
            equal(code, case.code, "wrong manifest rejection")
        end
    end)

    test("ManifestRejectsCrossAreaFallbackAndFallbackCycle", function()
        local cross_area = FakeEngine.manifest()
        cross_area.endpoints_by_id.fallback.area = "other-area"
        local controller, code = Core.new(FakeEngine.new(), cross_area)
        equal(controller, nil)
        equal(code, "MANIFEST_ENDPOINTS")

        local cycle = FakeEngine.manifest()
        cycle.endpoints_by_id.fallback.fallback_id = "primary"
        controller, code = Core.new(FakeEngine.new(), cycle)
        equal(controller, nil)
        equal(code, "MANIFEST_ENDPOINTS")
    end)

    test("ManifestAcceptsModPrefixInEightByteResref", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t1.item_resref = "#tstitem"
        local controller, code = Core.new(FakeEngine.new(), manifest)
        truthy(controller, code)
    end)

    test("TokenSelectionLeavesZeroAndDeliveredGlobalsUntouched", function()
        local engine = FakeEngine.new({ globals = { fl1t1 = 0, fl1t2 = -1 } })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.queues, 0, "inactive tokens were queued")
        equal(engine:get_global("fl1t1"), 0)
        equal(engine:get_global("fl1t2"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
    end)

    test("PositiveGlobalRequiresAnActiveStableSlot", function()
        local engine = FakeEngine.new({ globals = { fl1t1 = 99 } })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.queues, 0)
        equal(engine:get_global("fl1t1"), 99, "invalid assignment was marked delivered")
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
        equal(engine:get_global(Core.quarantine_global("fl1t1")), Core.REASON.BAD_SLOT)
    end)

    test("QuarantinedEarlierTokenDoesNotBlockValidToken", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t0 = {
            global = "fl1t0",
            unit_id = "test:unit-invalid",
            item_id = "test:item-invalid",
            tier = "1",
            compact_token = "0",
            item_resref = "tstbad",
            charges = { 0, 0, 0 },
            variant_policy = "exact",
            enabled = 1,
        }
        local engine = FakeEngine.new({ globals = { fl1t0 = 99, fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "primary")
        equal(engine:get_global("fl1t0"), 99)
        equal(engine:get_global(Core.quarantine_global("fl1t0")), Core.REASON.BAD_SLOT)
    end)

    test("SparseOverrideWinsBeforeEndpointSelection", function()
        local manifest = FakeEngine.manifest()
        manifest.sparse_overrides["test:item-a"] = {
            ["test:slot-a"] = "override",
        }
        local engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local controller = assert(Core.new(engine, manifest))
        controller:poll()
        equal(#engine.queues, 1)
        equal(engine.queues[1].endpoint_id, "override")
        truthy(engine:get_global(Core.GLOBALS.endpoint) > 0,
            "locked endpoint selector was not persisted")
    end)

    test("LegacyAdapterEndpointRemainsOwnedBySpecialDeliveryWithoutBlocking", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t0 = {
            global = "fl1t0",
            unit_id = "test:unit-adapter",
            item_id = "test:item-adapter",
            tier = "1",
            compact_token = "0",
            item_resref = "tstadapt",
            charges = { 0, 0, 0 },
            variant_policy = "exact",
            enabled = 1,
        }
        manifest.endpoints_by_id.adapter = {
            area = "artest",
            target_kind = "legacy_adapter",
            target_identity = "-",
            x = 0,
            y = 0,
            capacity = 1,
            static_policy = "legacy",
            fallback_id = "-",
            adapter = "test-hook",
            external_delivery = 1,
            enabled = 1,
        }
        manifest.slots_by_tier_value["1"][4] = {
            slot_id = "test:slot-adapter",
            tier = "1",
            slot_value = 4,
            endpoint_id = "adapter",
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
        equal(engine:get_global("fl1t1"), 1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.QUEUED)
    end)
end
