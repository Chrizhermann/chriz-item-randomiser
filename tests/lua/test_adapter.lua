return function(context)
    local Core = context.Core
    local FakeEngine = context.FakeEngine
    local FakeEEex = context.FakeEEex
    local repository_root = context.repository_root
    local test = context.test
    local equal = context.equal
    local truthy = context.truthy

    local function pack(...)
        return { n = select("#", ...), ... }
    end

    local function new_loaded(options)
        options = options or {}
        local manifest = options.manifest or FakeEngine.manifest()
        local fake = FakeEEex.new(repository_root, manifest, options)
        fake:load_bootstrap()
        return fake, fake.env.FLDLV
    end

    local function add_ready_endpoints(fake)
        fake:set_area("artest")
        local creature = fake:add_object({
            script_name = "test-primary",
            kind = "creature",
            alive = true,
            full = false,
            slots = {},
        }, "m_lVertSort")
        local container = fake:add_object({
            script_name = "test-container",
            kind = "container",
            container_type = 8,
            items = {},
        }, "m_lVertSortBack")
        local ground = fake:add_object({
            script_name = "",
            kind = "container",
            container_type = 4,
            x = 10,
            y = 20,
            items = {},
        }, "m_lVertSortUnder")
        return creature, container, ground
    end

    local function valid_item(resref, charges)
        return {
            resref = resref,
            charges = charges,
            quantity = 1,
        }
    end

    test("AdapterBootstrapFailsClosedForEveryRequiredCapability", function()
        local required = {
            "Infinity_DoFile",
            "Infinity_GetClockTicks",
            "Infinity_IsMenuOnStack",
            "Infinity_PushMenu",
            "EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly",
            "EEex_Action_ParseResponseString",
            "EEex_Area_GetVisible",
            "EEex_GameObject_CastUserType",
            "EEex_GameObject_Get",
            "EEex_GameObject_IsSprite",
            "EEex_GameState_AddDestroyedListener",
            "EEex_GameState_GetGlobalInt",
            "EEex_GameState_SetGlobalInt",
            "EEex_Menu_AddAfterMainFileLoadedListener",
            "EEex_Menu_Find",
            "EEex_Menu_GetItemFunction",
            "EEex_Menu_LoadFile",
            "EEex_Menu_SetItemFunction",
            "EEex_Resource_Fetch",
            "EEex_Sprite_AddLoadedListener",
            "EEex_Trigger_EvalConditionalStringAsAIBase",
            "EEex_Utility_IterateCPtrList",
        }
        for _, name in ipairs(required) do
            local fake = FakeEEex.new(repository_root, FakeEngine.manifest(), {
                missing_capability = name,
            })
            fake:load_bootstrap()
            equal(fake.env.FLDLV.Disabled, "CAPABILITY",
                "missing capability did not fail closed: " .. name)
            equal(#fake.listeners.sprite_loaded, 0)
            equal(#fake.listeners.game_state_destroyed, 0)
            equal(#fake.listeners.menu_loaded, 0)
            equal(#fake.do_files, 0, "dependencies loaded before capability gate")
        end

        local without_pop = FakeEEex.new(repository_root, FakeEngine.manifest(), {
            missing_capability = "Infinity_PopMenu",
        })
        without_pop:load_bootstrap()
        equal(without_pop.env.FLDLV.Disabled, nil,
            "unused Infinity_PopMenu must not be a required capability")
    end)

    test("AdapterRegistersDynamicListenersOnceAcrossReload", function()
        local fake, root = new_loaded()
        local first_controller = root.Controller
        fake:load_bootstrap()
        truthy(root.Controller ~= first_controller, "hot reload retained stale controller")
        equal(#fake.listeners.sprite_loaded, 1)
        equal(#fake.listeners.game_state_destroyed, 1)
        equal(#fake.listeners.menu_loaded, 1)

        local dirty_calls = 0
        root._OnSpriteLoaded = function(...)
            dirty_calls = dirty_calls + select("#", ...)
        end
        fake:trigger_sprite_loaded("a", nil, "c")
        equal(dirty_calls, 3, "listener trampoline retained stale handler")
    end)

    test("AdapterPreservesRetryFuseAcrossHotReload", function()
        local fake, root = new_loaded()
        root.Controller.retry_blocked.fl1t1 = true
        local shared_retry_blocked = root.Controller.retry_blocked

        fake:load_bootstrap()
        equal(root.Controller.retry_blocked, shared_retry_blocked,
            "hot reload replaced the same-GameState retry fuse")
        equal(root.Controller.retry_blocked.fl1t1, true)

        fake:trigger_destroyed()
        equal(next(shared_retry_blocked), nil,
            "GameState destruction did not clear the shared retry fuse")
    end)

    test("AdapterFailedReloadCannotRetainPriorController", function()
        local fake, root = new_loaded()
        truthy(root.Controller)
        fake.env.EEex_GameObject_Get = nil
        fake:load_bootstrap()
        equal(root.Disabled, "CAPABILITY")
        equal(root.Controller, nil, "failed reload retained the prior controller")
        equal(root.Engine, nil, "failed reload retained the prior engine adapter")
        equal(root._DeliveryAck, nil)
        equal(root._MenuPoll(), true)
    end)

    test("AdapterMenuHookPreservesArgumentsAndReturnArityWithoutStacking", function()
        local open_arguments
        local open_calls = 0
        local close_arguments
        local fake, root = new_loaded({
            on_open = function(...)
                open_calls = open_calls + 1
                open_arguments = pack(...)
                return "open", nil, 17
            end,
            on_close = function(...)
                close_arguments = pack(...)
                return nil, "close", nil
            end,
        })
        local original_close = fake.menu.reference_onClose.fn
        fake:trigger_menu_loaded()
        equal(fake.loaded_menus[1], "FLDLV")
        equal(fake.menu.reference_onClose.fn, original_close,
            "WORLD_ACTIONBAR onClose identity must remain untouched")

        local open_wrapper = fake.menu.reference_onOpen.fn
        local first = pack(open_wrapper("x", nil, "z"))
        equal(open_arguments.n, 3)
        equal(open_arguments[1], "x")
        equal(open_arguments[3], "z")
        equal(first.n, 3)
        equal(first[1], "open")
        equal(first[2], nil)
        equal(first[3], 17)
        equal(open_calls, 1)
        equal(#fake.menu_pushes, 1)

        fake:load_bootstrap()
        equal(root._menuPushed, true,
            "Lua hot reload forgot the already-pushed poll menu")
        local reloaded_handler = root._OnWorldOpen
        local dynamic_calls = 0
        root._OnWorldOpen = function(previous, ...)
            dynamic_calls = dynamic_calls + 1
            if reloaded_handler then
                return reloaded_handler(previous, ...)
            end
            if previous then
                return previous(...)
            end
        end
        local reloaded = pack(open_wrapper("reload", nil, "handler"))
        equal(dynamic_calls, 1,
            "stable world-open wrapper retained its pre-reload local handler")
        equal(open_calls, 2,
            "hot-reloaded wrapper did not invoke the prior world callback exactly once")
        equal(reloaded.n, 3)
        equal(reloaded[1], "open")
        equal(reloaded[2], nil)
        equal(reloaded[3], 17)
        equal(#fake.menu_pushes, 1,
            "Lua hot reload allowed the poll menu to stack")

        fake:trigger_menu_loaded()
        equal(fake.menu.reference_onOpen.fn, open_wrapper,
            "same host menu was wrapped twice")
        equal(#fake.loaded_menus, 1,
            "same poll menu resource was loaded twice")
        equal(fake.menu.reference_onClose.fn, original_close,
            "repeated menu-loaded notification replaced WORLD_ACTIONBAR onClose")
        local open_calls_before_repeat = open_calls
        open_wrapper()
        equal(open_calls, open_calls_before_repeat + 1,
            "stable wrapper invoked the prior world callback more than once")
        equal(dynamic_calls, 2,
            "same host wrapper bypassed the current root handler")
        equal(#fake.menu_pushes, 1, "poll menu was pushed twice")

        local closed = pack(fake.menu.reference_onClose.fn(1, nil, 3))
        equal(close_arguments.n, 3)
        equal(closed.n, 3)
        equal(closed[1], nil)
        equal(closed[2], "close")
        equal(closed[3], nil)
        equal(#fake.menu_pops, 0,
            "WORLD_ACTIONBAR close must not explicitly pop the poll menu")

        local rebuilt_calls = 0
        local rebuilt_close = function()
            return "rebuilt-close"
        end
        fake:rebuild_world_menu(function(value)
            rebuilt_calls = rebuilt_calls + 1
            return value, nil
        end, rebuilt_close)
        fake:trigger_menu_loaded()
        equal(#fake.loaded_menus, 2,
            "F5-style menu rebuild did not reload the poll menu")
        local rebuilt = pack(fake.menu.reference_onOpen.fn("fresh"))
        equal(rebuilt_calls, 1)
        equal(rebuilt.n, 2)
        equal(rebuilt[1], "fresh")
        equal(rebuilt[2], nil)
        equal(dynamic_calls, 3,
            "F5 replacement wrapper bypassed the current root handler")
        truthy(fake.menu.reference_onOpen.fn ~= open_wrapper,
            "F5-style menu rebuild was not re-hooked")
        equal(fake.menu.reference_onClose.fn, rebuilt_close,
            "F5-style menu rebuild close callback was wrapped")
        equal(#fake.menu_pops, 0,
            "F5-style menu rebuild explicitly popped the poll menu")
        equal(root.Disabled, nil)
    end)

    test("AdapterWorldOpenRepushesMissingPollMenuWithoutStacking", function()
        local fake, root = new_loaded({
            on_open = function() end,
        })
        fake:trigger_menu_loaded()
        local open_wrapper = fake.menu.reference_onOpen.fn

        open_wrapper()
        equal(#fake.menu_pushes, 1)
        open_wrapper()
        equal(#fake.menu_pushes, 1,
            "world reopen stacked an already-present poll menu")

        fake:set_menu_on_stack("FLDLV_POLL", false)
        equal(root._menuPushed, true,
            "fixture did not reproduce the stale pushed flag")
        open_wrapper()
        equal(#fake.menu_pushes, 2,
            "missing poll menu was not repushed after stack restoration")
        truthy(fake.menu_stack.FLDLV_POLL,
            "repushed poll menu was not present on the menu stack")
    end)

    test("AdapterPollGuardsVisibleAreaDebouncesAndSweepsFourLists", function()
        local fake, root = new_loaded()
        local polls = 0
        root.Controller.poll = function()
            polls = polls + 1
        end
        truthy(root._MenuPoll())
        equal(polls, 0, "poll ran without a visible area")

        local area_entry_tick = root.Constants.DEBOUNCE_TICKS * 10
        fake:set_clock(area_entry_tick)
        root._MenuPoll()
        equal(polls, 0, "long main-menu idle unexpectedly scanned")

        fake:set_area("artest")
        local first = fake:add_object({
            script_name = "one",
            kind = "creature",
        }, "m_lVertSort")
        fake:add_object({ script_name = "two" }, "m_lVertSortBack")
        fake:add_object({ script_name = "three" }, "m_lVertSortFlight")
        fake:add_object({ script_name = "four" }, "m_lVertSortUnder")
        fake:add_existing_id(first.id, "m_lVertSortBack")

        root._MenuPoll()
        equal(polls, 0,
            "area entry reused an expired main-menu debounce window")
        fake:set_clock(area_entry_tick + root.Constants.DEBOUNCE_TICKS - 1)
        root._MenuPoll()
        equal(polls, 0, "dirty area was scanned before debounce")
        fake:set_clock(area_entry_tick + root.Constants.DEBOUNCE_TICKS)
        root._MenuPoll()
        equal(polls, 1)
        for _, name in ipairs({
            "m_lVertSort", "m_lVertSortBack", "m_lVertSortFlight", "m_lVertSortUnder",
        }) do
            equal(fake.list_visits[name], 1, "live list not swept exactly once: " .. name)
        end
        equal(root._settlingSweeps, 1)

        fake:set_clock(area_entry_tick + root.Constants.DEBOUNCE_TICKS +
            root.Constants.SETTLE_TICKS)
        root._MenuPoll()
        equal(polls, 2)
        equal(root._settlingSweeps, 2, "second settling sweep was not recorded")

        fake:trigger_sprite_loaded({})
        fake:set_clock(fake.clock + root.Constants.DEBOUNCE_TICKS - 1)
        root._MenuPoll()
        equal(polls, 2, "sprite-loaded dirty event was not debounced")
    end)

    test("AdapterEndpointTypingRejectsDuplicatesDeadAndFullCreatures", function()
        local fake, root = new_loaded()
        local creature, container, ground = add_ready_endpoints(fake)
        root._settlingSweeps = 2
        local engine = root.Engine
        local manifest = root.Manifest

        local duplicate = fake:add_object({
            script_name = " TEST-PRIMARY ",
            kind = "creature",
        }, "m_lVertSortFlight")
        local observation = engine:observe_endpoint("primary",
            manifest.endpoints_by_id.primary)
        equal(observation.observable, false)
        equal(observation.settled, true)
        fake:remove_id(duplicate.id)

        creature.alive = false
        observation = engine:observe_endpoint("primary",
            manifest.endpoints_by_id.primary)
        equal(observation.observable, true)
        equal(observation.eligible, false)
        equal(observation.verifiable, false)

        creature.alive = true
        creature.full = true
        observation = engine:observe_endpoint("primary",
            manifest.endpoints_by_id.primary)
        equal(observation.eligible, false)
        equal(observation.verifiable, true)
        creature.full = false
        observation = engine:observe_endpoint("primary",
            manifest.endpoints_by_id.primary)
        equal(observation.eligible, true)

        observation = engine:observe_endpoint("container",
            manifest.endpoints_by_id.container)
        equal(observation.observable, true)
        equal(observation.eligible, true)
        equal(observation.verifiable, true)

        observation = engine:observe_endpoint("fallback",
            manifest.endpoints_by_id.fallback)
        equal(observation.observable, true)
        equal(observation.eligible, true)
        ground.container_type = 8
        observation = engine:observe_endpoint("fallback",
            manifest.endpoints_by_id.fallback)
        equal(observation.observable, false,
            "ground endpoint accepted a non-type-4 container")
        truthy(container.id ~= ground.id)
    end)

    test("AdapterListsExactChargesAcrossCreatureSlotsAndContainers", function()
        local fake, root = new_loaded()
        local creature, container = add_ready_endpoints(fake)
        creature.slots[0] = FakeEEex.item("first", { 1, 2, 3 })
        creature.slots[38] = FakeEEex.item("last", { 4, 5, 6 })
        container.items[1] = FakeEEex.item("inside", { 7, 8, 9 })
        root._settlingSweeps = 2

        local creature_observation = root.Engine:observe_endpoint("primary",
            root.Manifest.endpoints_by_id.primary)
        fake.slot_reads = {}
        local creature_items = root.Engine:list_items(creature_observation)
        equal(#creature_items, 2)
        equal(creature_items[1].resref, "first")
        equal(creature_items[1].charges[3], 3)
        equal(creature_items[2].resref, "last")
        equal(creature_items[2].charges[1], 4)
        equal(#fake.slot_reads, 39)
        equal(fake.slot_reads[1], 0)
        equal(fake.slot_reads[39], 38)

        local container_observation = root.Engine:observe_endpoint("container",
            root.Manifest.endpoints_by_id.container)
        local container_items = root.Engine:list_items(container_observation)
        equal(#container_items, 1)
        equal(container_items[1].resref, "inside")
        equal(container_items[1].charges[2], 8)
    end)

    test("AdapterReResolvesByStableIdentityWithoutCachingObjectIds", function()
        local fake, root = new_loaded()
        add_ready_endpoints(fake)
        root._settlingSweeps = 2
        local endpoint = root.Manifest.endpoints_by_id.container
        local observation = root.Engine:observe_endpoint("container", endpoint)
        equal(observation.id, nil)
        equal(observation.object, nil)
        equal(observation.userdata, nil)
        local after_observe = fake.fetch_count
        root.Engine:list_items(observation)
        truthy(fake.fetch_count > after_observe,
            "item listing reused an observed object wrapper")
        local before_execution = fake.fetch_count
        local accepted = root.Engine:execute_delivery("container", endpoint,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(accepted, true)
        truthy(fake.fetch_count > before_execution,
            "instant execution reused an earlier object wrapper")
        truthy(fake.executed[1].fetch_serial > before_execution,
            "instant target was not fetched during the execution sweep")
        equal(fake.executed[1].object_id, 101)
    end)

    test("AdapterExecutesExactChargedTransportInstantly", function()
        local fake, root = new_loaded()
        add_ready_endpoints(fake)
        root._settlingSweeps = 2
        local accepted = root.Engine:execute_delivery("primary",
            root.Manifest.endpoints_by_id.primary,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(accepted, true, "successful instant execution was rejected")
        equal(fake.instant_attempts, 1)
        equal(fake.queue_attempts, nil)
        equal(fake.free_count, 1)
        equal(fake.parsed[1].source,
            'GiveItemCreate("tstitema",Myself,3,2,1)')
        equal(root._DeliveryAck, nil)
        equal(root._ackCallbacks, nil)
        equal(root._generation, nil)

        root.Engine:execute_delivery("container",
            root.Manifest.endpoints_by_id.container,
            valid_item("tstitemb", { 0, 4, 0 }))
        equal(fake.parsed[2].source,
            'CreateItem("tstitemb",0,4,0)')
    end)

    test("AdapterRejectsPreflightBeforeExecutionAndFreesParseOnce", function()
        local fake, root = new_loaded()
        local creature = add_ready_endpoints(fake)
        root._settlingSweeps = 2
        creature.full = true
        local accepted = root.Engine:execute_delivery("primary",
            root.Manifest.endpoints_by_id.primary,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(accepted, false)
        equal(fake.parse_calls, 0)
        equal(fake.instant_attempts, 0)

        creature.full = false
        fake.next_action_count = 2
        accepted = root.Engine:execute_delivery("primary",
            root.Manifest.endpoints_by_id.primary,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(accepted, false)
        equal(fake.parse_calls, 1)
        equal(fake.free_count, 1)
        equal(fake.instant_attempts, 0)
    end)

    test("AdapterInstantExceptionPropagatesAndFreesOnce", function()
        local fake, root = new_loaded({ instant_error = "synthetic instant failure" })
        add_ready_endpoints(fake)
        root._settlingSweeps = 2
        local ok = pcall(root.Engine.execute_delivery, root.Engine, "container",
            root.Manifest.endpoints_by_id.container,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(ok, false, "instant exception was converted to deterministic rejection")
        equal(fake.instant_attempts, 1)
        equal(fake.free_count, 1)
    end)

    test("AdapterGroundTransportRequiresUniqueTypeFourCoordinates", function()
        local fake, root = new_loaded()
        local _, _, ground = add_ready_endpoints(fake)
        root._settlingSweeps = 2
        local duplicate = fake:add_object({
            kind = "container",
            container_type = 4,
            x = 10,
            y = 20,
            items = {},
        }, "m_lVertSortFlight")
        local accepted = root.Engine:execute_delivery("fallback",
            root.Manifest.endpoints_by_id.fallback,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(accepted, false)
        equal(fake.instant_attempts, 0)
        fake:remove_id(duplicate.id)
        accepted = root.Engine:execute_delivery("fallback",
            root.Manifest.endpoints_by_id.fallback,
            valid_item("tstitema", { 3, 2, 1 }))
        equal(accepted, true)
        equal(fake.parsed[1].source,
            'CreateItem("tstitema",3,2,1)')
        truthy(ground.id ~= duplicate.id)
    end)

    test("AdapterResourceGateAndDiagnosticsRemainOpaque", function()
        local fake, root = new_loaded()
        equal(root.Engine:is_item_available("tstitema"), true)
        fake.resources.tstitema = false
        equal(root.Engine:is_item_available("TSTITEMA"), false)

        root.Engine:report_error("DELIVERY_QUARANTINED",
            "secret-item-at-secret-location")
        root.Engine:report_error("DELIVERY_QUARANTINED", "another-secret")
        equal(root.Diagnostics.counts.DELIVERY_QUARANTINED, 2)
        equal(root.Diagnostics.detail, nil)
        equal(root.Diagnostics.last_detail, nil)

        root._Boundary("poll", function()
            error("sensitive mapping")
        end)
        root._Boundary("poll", function()
            error("different sensitive mapping")
        end)
        equal(root.Diagnostics.counts.CALLBACK_ERROR, 1,
            "callback diagnostic was not deduplicated by opaque boundary")
    end)

    test("AdapterDestroyedBoundaryHasNoEphemeralTransportState", function()
        local fake, root = new_loaded()
        add_ready_endpoints(fake)
        root._settlingSweeps = 2
        root._currentArea = "artest"
        root.Controller.retry_blocked.fl1t1 = true
        root.Engine:execute_delivery("container",
            root.Manifest.endpoints_by_id.container,
            valid_item("tstitema", { 3, 2, 1 }))
        fake:trigger_destroyed()
        equal(root._ackCallbacks, nil)
        equal(root._generation, nil)
        equal(root._DeliveryAck, nil)
        equal(root._currentArea, nil)
        equal(next(root.Controller.retry_blocked), nil)
    end)

    test("AdapterMenuContainsOneInvisiblePollPredicate", function()
        local path = repository_root .. "/copy/FLDLV.menu"
        local file = assert(io.open(path, "rb"))
        local source = file:read("*a")
        file:close()
        truthy(source:find("name%s+'FLDLV_POLL'"), "poll menu name missing")
        truthy(source:find('enabled%s+"FLDLV%._MenuPoll%(%)"'),
            "poll predicate must use the menu enabled-expression form")
        truthy(not source:find('enabled%s+"return'),
            "menu enabled predicates must not include a return statement")
        local count = 0
        for _ in source:gmatch("FLDLV%._MenuPoll%s*%(") do
            count = count + 1
        end
        equal(count, 1, "menu must call exactly one contained poll predicate")
        truthy(not source:find("item_resref", 1, true))
        truthy(not source:find("target_identity", 1, true))
    end)
end
