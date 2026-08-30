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

    test("ManifestEnforcesStaticPolicyByTargetKind", function()
        local invalid = {
            function(manifest)
                manifest.endpoints_by_id.primary.static_policy = "authored-static"
            end,
            function(manifest)
                manifest.endpoints_by_id.container.static_policy = "same-area"
            end,
            function(manifest)
                manifest.endpoints_by_id.fallback.static_policy = "runtime-resolve"
            end,
            function(manifest)
                manifest.endpoints_by_id.fallback.target_identity = "named-pile"
            end,
            function(manifest)
                manifest.endpoints_by_id.adapter = {
                    area = "artest", target_kind = "legacy_adapter",
                    target_identity = "test-hook", x = 0, y = 0, capacity = 1,
                    static_policy = "legacy", fallback_id = "-",
                    adapter = "test-hook", external_delivery = 1, enabled = 1,
                }
            end,
            function(manifest)
                manifest.endpoints_by_id.group = {
                    area = "artest", target_kind = "group",
                    target_identity = "test.flg", x = 0, y = 0, capacity = 1,
                    static_policy = "authored", fallback_id = "fallback",
                    adapter = "-", external_delivery = 0, enabled = 1,
                }
            end,
        }
        for _, mutate in ipairs(invalid) do
            local manifest = FakeEngine.manifest()
            mutate(manifest)
            local controller, code = Core.new(FakeEngine.new(), manifest)
            equal(controller, nil, "invalid target/static-policy combination was accepted")
            equal(code, "MANIFEST_ENDPOINTS")
        end

        for _, policy in ipairs({
            "derived-unique", "authored", "authored-entrance",
            "authored-nearest", "authored-static",
        }) do
            local manifest = FakeEngine.manifest()
            manifest.endpoints_by_id.fallback.static_policy = policy
            local controller, code = Core.new(FakeEngine.new(), manifest)
            truthy(controller, code)
        end

        local legacy = FakeEngine.manifest()
        legacy.endpoints_by_id.adapter = {
            area = "artest", target_kind = "legacy_adapter",
            target_identity = "test-hook", x = 0, y = 0, capacity = 1,
            static_policy = "legacy-external", fallback_id = "-",
            adapter = "test-hook", external_delivery = 1, enabled = 1,
        }
        local controller, code = Core.new(FakeEngine.new(), legacy)
        truthy(controller, code)

        local group = FakeEngine.manifest()
        group.endpoints_by_id.group = {
            area = "artest", target_kind = "group", target_identity = "test.flg",
            x = 0, y = 0, capacity = 1, static_policy = "runtime-resolve",
            fallback_id = "fallback", adapter = "-", external_delivery = 0,
            enabled = 1,
        }
        controller, code = Core.new(FakeEngine.new(), group)
        truthy(controller, code)
    end)

    test("ManifestAcceptsModPrefixInEightByteResref", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t1.item_resref = "#tstitem"
        local controller, code = Core.new(FakeEngine.new(), manifest)
        truthy(controller, code)
    end)

    test("ManifestRequiresEveryGroupSlotToBeLoweredPerUnit", function()
        local manifest = FakeEngine.manifest()
        manifest.endpoints_by_id.group = {
            area = "artest",
            target_kind = "group",
            target_identity = "test.flg",
            x = 10,
            y = 20,
            capacity = 1,
            static_policy = "runtime-resolve",
            fallback_id = "fallback",
            adapter = "",
            external_delivery = 0,
            enabled = 1,
        }
        manifest.slots_by_tier_value["1"][4] = {
            slot_id = "test:slot-group",
            tier = "1",
            slot_value = 4,
            endpoint_id = "group",
            weight = 1,
            progression_band = "test",
            enabled = 1,
        }

        local controller, code = Core.new(FakeEngine.new(), manifest)
        equal(controller, nil, "unlowered group endpoint was accepted")
        equal(code, "MANIFEST_GROUPS")

        manifest.unit_overrides["test:unit-a"] = {
            ["test:slot-group"] = "primary",
        }
        manifest.unit_overrides["test:unit-b"] = {
            ["test:slot-group"] = "override",
        }
        controller, code = Core.new(FakeEngine.new(), manifest)
        truthy(controller, code)
    end)

    test("UnitOverridePreservesDuplicateLogicalItemChoicesAndWinsFirst", function()
        local manifest = FakeEngine.manifest()
        manifest.tokens_by_global.fl1t2.item_id = "test:item-a"
        manifest.tokens_by_global.fl1t2.item_resref = "tstitema"
        manifest.unit_overrides["test:unit-a"] = {
            ["test:slot-a"] = "primary",
        }
        manifest.unit_overrides["test:unit-b"] = {
            ["test:slot-a"] = "container",
        }
        manifest.sparse_overrides["test:item-a"] = {
            ["test:slot-a"] = "override",
        }

        local first_engine = FakeEngine.new({ globals = { fl1t1 = 1 } })
        local first = assert(Core.new(first_engine, manifest))
        first:poll()
        equal(first_engine.executions[1].endpoint_id, "primary")

        local second_engine = FakeEngine.new({ globals = { fl1t2 = 1 } })
        local second = assert(Core.new(second_engine, manifest))
        second:poll()
        equal(second_engine.executions[1].endpoint_id, "container")
    end)

    test("ManifestRejectsInvalidUnitOverrides", function()
        local cases = {
            function(manifest)
                manifest.unit_overrides["test:unit-missing"] = {
                    ["test:slot-a"] = "primary",
                }
            end,
            function(manifest)
                manifest.unit_overrides["test:unit-a"] = {
                    ["test:slot-a"] = "missing",
                }
            end,
            function(manifest)
                manifest.endpoints_by_id.other = {
                    area = "other-area", target_kind = "ground", target_identity = "-",
                    x = 1, y = 2, capacity = 1, static_policy = "authored-static",
                    fallback_id = "-", adapter = "-", external_delivery = 0, enabled = 1,
                }
                manifest.unit_overrides["test:unit-a"] = {
                    ["test:slot-a"] = "other",
                }
            end,
        }
        for _, mutate in ipairs(cases) do
            local manifest = FakeEngine.manifest()
            mutate(manifest)
            local controller, code = Core.new(FakeEngine.new(), manifest)
            equal(controller, nil)
            equal(code, "MANIFEST_UNIT_OVERRIDES")
        end
    end)

    test("ManifestRejectsUnknownOverrideItems", function()
        local manifest = FakeEngine.manifest()
        manifest.sparse_overrides["test:item-missing"] = {
            ["test:slot-a"] = "override",
        }
        local controller, code = Core.new(FakeEngine.new(), manifest)
        equal(controller, nil)
        equal(code, "MANIFEST_OVERRIDES")
    end)

    test("TokenSelectionLeavesZeroAndDeliveredGlobalsUntouched", function()
        local engine = FakeEngine.new({ globals = { fl1t1 = 0, fl1t2 = -1 } })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.executions, 0, "inactive tokens were executed")
        equal(engine:get_global("fl1t1"), 0)
        equal(engine:get_global("fl1t2"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
    end)

    test("PositiveGlobalRequiresAnActiveStableSlot", function()
        local engine = FakeEngine.new({ globals = { fl1t1 = 99 } })
        local controller = assert(Core.new(engine, FakeEngine.manifest()))
        controller:poll()
        equal(#engine.executions, 0)
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
        equal(#engine.executions, 1)
        equal(engine.executions[1].endpoint_id, "primary")
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
        controller:poll()
        equal(#engine.executions, 1)
        equal(engine.executions[1].endpoint_id, "override")
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
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
            target_identity = "test-hook",
            x = 0,
            y = 0,
            capacity = 1,
            static_policy = "legacy-external",
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
        controller:poll()
        equal(#engine.executions, 1)
        equal(engine.executions[1].endpoint_id, "primary")
        equal(engine:get_global("fl1t0"), 4)
        equal(engine:get_global("fl1t1"), -1)
        equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
    end)
end
