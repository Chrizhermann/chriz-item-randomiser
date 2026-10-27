-- Bounded, opt-in native fixture host. This is not the general game adapter.
-- The caller owns the validated immutable artifact library and world boundary.
local Host = {}
Host.__index = Host
-- An unavailable target is retried after this many game ticks (15/second).
-- Deferral never creates anything or substitutes another recipient. A dead
-- creature target's reward goes to the ground at its corpse instead, or at a
-- party member's feet where it left no body (FLPRNat); deps.notice tells the
-- player of the latter.
Host.DEFER_TICKS = 30
-- Guarded sources whose lease, and whose removal, one step observes.
Host.LEASE_CHECKS = 16
Host.REMOVAL_CHECKS = 16
-- Target inspection failures that describe current availability, reported
-- before the core journals anything. Every other failure disables the host.
Host.DEFERRABLE = { AREA_UNAVAILABLE = true, OWNER_MISSING = true, OWNER_AMBIGUOUS = true,
    TARGET_NOT_ALIVE = true, TARGET_PARTY_MEMBER = true, TARGET_FAMILIAR = true,
    TARGET_INVENTORY_FULL = true, TARGET_QUEUE_BUSY = true, TARGET_NOT_EMPTY = true,
    TARGET_STACK_COLLISION = true, QUEST_NOT_CLAIMED = true, CORPSE_ITEM_AMBIGUOUS = true,
    TARGET_ALIVE = true, PARTY_ITEM_AMBIGUOUS = true }

function Host.new(deps)
    assert(type(deps) == "table" and deps.core and deps.storage)
    return setmetatable({ deps = deps, status = "waiting", last_tick = nil, deferred = {} }, Host)
end

function Host:destroy()
    if self.controller then self.controller:destroy() end
    if self.native then self.native:destroy() end
    if self.guard then self.guard:destroy() end
    self.controller, self.native, self.pending = nil, nil, nil
    self.guard, self.guarded, self.guard_pending, self.guard_order = nil, nil, nil, nil
    self.view, self.view_phases, self.view_controller, self.view_version = nil, nil, nil, nil
    self.guard_monitor, self.container_latch_monitor = nil, nil
    self.last_tick, self.bundle, self.error = nil, nil, nil
    self.deferred = {}
    self.status = "waiting"
end

function Host:_disable(reason)
    self.error, self.status = tostring(reason), "disabled"
    -- A reward on its way now is never settled from a later save
    -- (Core:forfeit_settle); a failure here leaves the first one reported.
    if self.controller then pcall(self.controller.forfeit_settle, self.controller) end
    if self.deps.report then self.deps.report("disabled", self.error) end
end

local function require_result(value, failure)
    if value == nil or value == false then error(failure or "HOST_OPERATION_FAILED", 0) end
    return value
end

-- Each configured source has independent protection and removal state.
local function guarded_indices(bundle)
    local config, endpoints = bundle.fixture.source_gate, {}
    for id, endpoint in pairs(bundle.fixture.sources or {}) do
        if endpoint.kind == "guarded-container" or endpoint.kind == "guarded-creature" or
            endpoint.kind == "guarded-store" or endpoint.kind == "guarded-script" or
            endpoint.kind == "guarded-template" then
            endpoints[id] = endpoint.kind
        end
    end
    if config == nil then
        assert(next(endpoints) == nil, "SOURCE_GUARD_CONFIG_INVALID")
        return nil
    end
    assert(type(config) == "table" and config.schema == "flpr-unvisited-fixture-gate-v1" and
        (config.source_id == nil) ~= (config.source_ids == nil), "SOURCE_GUARD_CONFIG_INVALID")
    local ids = config.source_ids or {config.source_id}
    assert(type(ids) == "table" and (#ids > 0 or type(bundle.fixture.spell_shield) == "table" or
        type(bundle.fixture.cursed_disguises) == "table" or type(bundle.fixture.duplicates) == "table"),
        "SOURCE_GUARD_CONFIG_INVALID")
    local selected, result, count = {}, {}, 0
    for key, id in pairs(ids) do
        assert(type(key) == "number" and key == math.floor(key) and key >= 1 and key <= #ids and
            type(id) == "string" and endpoints[id] and not selected[id], "SOURCE_GUARD_CONFIG_INVALID")
        selected[id], endpoints[id] = endpoints[id], nil
        count = count + 1
    end
    assert(count == #ids and next(endpoints) == nil, "SOURCE_GUARD_CONFIG_INVALID")
    for index, source in ipairs(bundle.manifest.sources) do
        if selected[source.id] then
            local endpoint_kind, source_kind = selected[source.id], source.record.kind
            assert((endpoint_kind == "guarded-store" and source_kind == "store") or (source.quantity == 1 and
                ((endpoint_kind == "guarded-script" and (source_kind == "scripted" or source_kind == "authored-source")) or
                 (endpoint_kind == "guarded-container" and source_kind == "container") or
                 ((endpoint_kind == "guarded-creature" or endpoint_kind == "guarded-template") and
                  (source_kind == "cre-template" or source_kind == "embedded-cre" or source_kind == "authored-source")))),
                "SOURCE_GUARD_CONFIG_INVALID")
            result[index], selected[source.id] = {}, nil
        end
    end
    assert(next(selected) == nil, "SOURCE_GUARD_CONFIG_INVALID")
    return result
end

function Host:_initialize()
    local deps = self.deps
    local selected
    self.controller = require_result(deps.core.new(deps.storage, function(revision)
        local bundle = deps.resolve(revision)
        if bundle then selected = bundle; return bundle.manifest end
    end))
    local state = deps.storage:get(deps.core.G.state)
    if state == 0 then
        -- Even an armed template marker in a loaded early autosave is not
        -- permission to redraw. Only the confirmed character-accept path can
        -- capture a campaign, before the engine has an opportunity to save it.
        self.status = "inactive"
        return
    else
        require_result(self.controller:restore())
        -- A crash after the core commit but before marker consumption cannot
        -- cause a redraw: state takes precedence over the template marker.
        if deps.storage:get("FLPR_FRESH") == 1 then deps.storage:set("FLPR_FRESH", 0) end
    end
    self.bundle = assert(selected, "REVISION_UNAVAILABLE")
    self.guarded = guarded_indices(selected)
    self.guard_order, self.lease_cursor, self.removal_cursor = {}, 0, 0
    for index in pairs(self.guarded or {}) do self.guard_order[#self.guard_order + 1] = index end
    table.sort(self.guard_order)
    self.native = require_result(deps.native(selected.fixture))
    if self.guarded then
        assert(type(deps.source_guard) == "function", "SOURCE_GUARD_ADAPTER_MISSING")
        self.guard = require_result(deps.source_guard(selected, self.native))
        assert(type(self.guard) == "table" and type(self.guard.observe_guard) == "function" and
            type(self.guard.observe_removal) == "function" and type(self.guard.destroy) == "function",
            "SOURCE_GUARD_ADAPTER_INVALID")
    end
    self.status = "running"
    if deps.storage:get(deps.core.G.journal) ~= 0 then
        -- No item-pointer provenance survives loading, and no queued message.
        -- Nothing is replayed or inferred from a matching item already present:
        -- a reward on its way parks with its counts before it, and the next
        -- visit of its place settles it (Host:_settle_parked).
        require_result(self.controller:park_unproven())
    end
end

-- Parked rewards (Core.PHASE.parked), settled by an observation of their
-- place: arrived, or pending for a new delivery. Outside their area this
-- costs one comparison and is tried on every step, so that a visit settles
-- them before anyone there can take or move anything (and before any other
-- reward there begins, see below); so is a queue that is busy for a moment.
-- Other waits are retried after DEFER_TICKS. Returns true after a settle.
function Host:_settle_parked(assignments, tick)
    local parked = self.deps.core.PHASE.parked
    for _, assignment in ipairs(assignments) do
        local unit, deferral = assignment.unit, self.deferred[assignment.unit]
        if assignment.delivery_phase == parked and (not deferral or tick >= deferral.retry) then
            local request = require_result(self.controller:parked_request(unit))
            local receipt, reason = self.native:settle_parked(request)
            if receipt then
                self.deferred[unit] = nil
                require_result(self.controller:confirm_parked(receipt))
                return true
            end
            if not Host.DEFERRABLE[reason] then error(reason or "HOST_OPERATION_FAILED", 0) end
            self.deferred[unit] = { reason = reason, retry = (reason == "AREA_UNAVAILABLE" or
                reason == "TARGET_QUEUE_BUSY") and tick + 1 or tick + Host.DEFER_TICKS }
        end
    end
    return false
end

-- Capture is atomic: a failure undoes every GLOBAL written while capturing, so
-- the caller may start the same game without randomisation instead.
function Host:capture_new(handoff)
    local ok, failure = pcall(function()
        assert(handoff == "confirmed-character-accept", "NEW_GAME_HANDOFF_REQUIRED")
        local deps, storage = self.deps, self.deps.storage
        assert(type(storage.begin) == "function" and type(storage.commit) == "function" and
            type(storage.rollback) == "function", "STORAGE_TRANSACTION_UNSUPPORTED")
        local state = storage:get(deps.core.G.state)
        if state == 2 and storage:get("FLPR_FRESH") == 0 then return end
        assert(state == 0 and storage:get("FLPR_FRESH") == 1, "FRESH_TEMPLATE_REQUIRED")
        local selected = require_result(deps.next(), "NEXT_REVISION_UNAVAILABLE")
        local guarded = guarded_indices(selected)
        if guarded then assert(type(deps.prepare_source_guard) == "function", "SOURCE_GUARD_PREPARER_MISSING") end
        storage:begin()
        local captured, reason = pcall(function()
            local controller = require_result(deps.core.new(storage, function() return nil end))
            require_result(controller:capture(selected.manifest, deps.seed(), "confirmed-new-game"))
            if deps.prepare_campaign then require_result(deps.prepare_campaign(selected,controller)) end
            -- Before engine character acceptance can load an original's area, the
            -- native adapter must already protect this exact frozen campaign.
            if guarded then require_result(deps.prepare_source_guard(selected, controller)) end
            storage:set("FLPR_FRESH", 0)
            assert(storage:get("FLPR_FRESH") == 0, "FRESH_MARKER_WRITE_FAILED")
        end)
        if not captured then
            storage:rollback()
            error(reason, 0)
        end
        storage:commit()
        -- A retry after a refused capture starts clean.
        self.status, self.error = "waiting", nil
    end)
    if not ok then self:_disable(failure); return nil, failure end
    return true
end

function Host:_step(tick)
    if self.status == "waiting" then self:_initialize() end
    if self.status ~= "running" and self.status ~= "complete" then return end
    if self.guard and self.guard.check then require_result(self.guard:check()) end
    self.controller:on_observation_boundary()
    require_result(self.native:on_observation_boundary(tick))
    -- The assignments change only with the controller's state. Rebuilding them
    -- on every engine tick cost milliseconds in campaigns of a few hundred units.
    local version = self.controller:state_version()
    if self.view_controller ~= self.controller or self.view_version ~= version then
        local view, phases = require_result(self.controller:assignments()), {}
        for _, assignment in ipairs(view) do phases[assignment.source] = assignment.source_phase end
        self.view, self.view_phases = view, phases
        self.view_controller, self.view_version = self.controller, version
    end
    local assignments, source_phases = self.view, self.view_phases
    -- Proving every protected source's lease on every engine tick took longer
    -- than a frame with a few hundred guarded sources. Each step proves a share
    -- of them in turn, so a lapsed lease still stops the host within seconds; a
    -- delivery always needs its own source's lease (Core:_begin, Core:observe).
    local order = self.guard_order or {}
    for _ = 1, math.min(Host.LEASE_CHECKS, #order) do
        self.lease_cursor = self.lease_cursor % #order + 1
        local index = order[self.lease_cursor]
        if self.guarded[index].bound and source_phases[index] == self.deps.core.PHASE.protected then
            assert(self.controller:source_guard_active(index), "SOURCE_GUARD_INACTIVE")
        end
    end
    if self.pending then
        local receipt, reason = self.native:observe(self.pending)
        if receipt == nil and reason == "DELIVERY_SETTLING" then
            assert(self.pending.kind == "deliver", "UNEXPECTED_SETTLING_SOURCE")
            return
        end
        require_result(receipt, reason)
        -- The party left the place of its target with the reward on its way.
        if receipt.parked then require_result(self.controller:park(receipt))
        else require_result(self.controller:observe(receipt)) end
        self.pending = nil
        if receipt.notice and self.deps.notice then self.deps.notice(receipt.notice) end
        return
    end
    if self:_settle_parked(assignments, tick) then return end
    if self.native.observe_container_latches then
        local observed,monitor=self.native:observe_container_latches(assignments,self.deps.core.PHASE.confirmed)
        require_result(observed,monitor)
        self.container_latch_monitor=monitor==true
    end
    -- The Sahuagin treasury's rewards the king's reply could not hand over yet.
    if self.native.retry_handovers then require_result(self.native:retry_handovers(assignments,self.deps.core.PHASE)) end
    if self.guard_pending then
        for _, request in ipairs(self.guard_pending) do
            local receipt, reason = self.guard:observe_guard(request)
            require_result(receipt, reason)
            require_result(self.controller:confirm_source_guard(receipt))
            self.guarded[request.index].bound = true
        end
        self.guard_pending, self.guard_monitor = nil, true
        return
    end
    -- Guard requests are independent observations. Register them together, then
    -- confirm each on the next boundary before any source or reward mutation.
    local requests = {}
    for index in ipairs(self.bundle.manifest.sources) do
        local phase, state = source_phases[index], self.guarded and self.guarded[index]
        if state and not state.bound and phase ~= self.deps.core.PHASE.confirmed and
            phase ~= self.deps.core.PHASE.kept then
            assert(phase == self.deps.core.PHASE.pending or phase == self.deps.core.PHASE.protected,
                "SOURCE_NOT_READY")
            requests[#requests + 1] = require_result(self.controller:source_guard_request(index))
        end
    end
    if #requests > 0 then self.guard_pending = requests; return end
    self.guard_monitor = false
    -- Waiting for removal proof withholds no reward (see below), so each step
    -- observes the removal of a share of the guarded sources in turn.
    local observe_removal = {}
    for _ = 1, math.min(Host.REMOVAL_CHECKS, #order) do
        self.removal_cursor = self.removal_cursor % #order + 1
        observe_removal[order[self.removal_cursor]] = true
    end
    -- Every original must be removed or protected before exposing any rewards.
    for index, source in ipairs(self.bundle.manifest.sources) do
        local phase, state = source_phases[index], self.guarded and self.guarded[index]
        if phase == self.deps.core.PHASE.kept then
            -- This campaign left the item at its original source.
        elseif state and phase ~= self.deps.core.PHASE.confirmed then
            assert(phase == self.deps.core.PHASE.pending or phase == self.deps.core.PHASE.protected,
                "SOURCE_NOT_READY")
            assert(state.bound, "SOURCE_GUARD_INACTIVE")
            if state.removal_pending then
                local receipt, reason = nil, "SOURCE_NOT_LOADED"
                if observe_removal[index] then receipt, reason = self.guard:observe_removal(state.removal_pending) end
                if receipt == nil and reason == "SOURCE_NOT_LOADED" then
                    -- Waiting for physical proof never withholds an already
                    -- protected source's reward and never repeats suppression.
                else
                    require_result(receipt, reason)
                    require_result(self.controller:confirm_guarded_source_removal(receipt))
                    state.removal_pending, state.bound, phase = nil, nil, self.deps.core.PHASE.confirmed
                end
            else
                state.removal_pending = require_result(self.controller:guarded_source_removal_request(index))
            end
            if phase ~= self.deps.core.PHASE.confirmed then self.guard_monitor = true end
        elseif phase ~= 2 then
            assert(phase == 0, "SOURCE_NOT_READY")
            local witness = require_result(self.native:inspect_source(source.id))
            local command = require_result(self.controller:begin_source(index, witness))
            self.pending = command
            require_result(self.native:execute(command))
            return
        end
    end
    local phases, waiting, parked = self.deps.core.PHASE, false, {}
    -- No reward goes to a place while another one of it is parked: the settle
    -- reads the items of that place as they were (Adapter:settle_parked).
    for _, assignment in ipairs(assignments) do
        if assignment.delivery_phase == phases.parked then parked[assignment.target_id], waiting = true, true end
    end
    for _, assignment in ipairs(assignments) do
        local phase = assignment.delivery_phase
        if phase ~= phases.confirmed and phase ~= phases.held and phase ~= phases.kept and phase ~= phases.lost and
            phase ~= phases.parked then
            assert(phase == phases.pending, "DELIVERY_NOT_READY")
            local deferral = self.deferred[assignment.unit]
            if not parked[assignment.target_id] and (not deferral or tick >= deferral.retry) then
                local witness, reason = self.native:inspect_target(assignment.target_id, assignment.source_id, assignment.unit)
                if witness then
                    self.deferred[assignment.unit] = nil
                    local command = require_result(self.controller:begin_delivery(assignment.unit, witness))
                    self.pending = command
                    require_result(self.native:execute(command))
                    return
                end
                if not Host.DEFERRABLE[reason] then error(reason or "HOST_OPERATION_FAILED", 0) end
                self.deferred[assignment.unit] = { reason = reason, retry = tick + Host.DEFER_TICKS }
            end
            waiting = true
        end
    end
    -- Deferred rewards keep the campaign running; nothing is placed elsewhere.
    if waiting then return end
    if self.status ~= "complete" then
        self.status = "complete"
        if self.deps.report then self.deps.report("complete") end
    end
end

function Host:poll()
    if self.status == "disabled" or self.status == "inactive" or
        (self.status == "complete" and not self.guard_monitor and not self.container_latch_monitor and
         not (self.native and self.native.owes_handover and self.native:owes_handover())) then return end
    local ok, failure = pcall(function()
        if not self.deps.world() then return end
        local tick = self.deps.tick()
        assert(type(tick) == "number" and tick >= 0 and tick < math.huge and tick == math.floor(tick), "ENGINE_TIME_UNAVAILABLE")
        if self.last_tick == nil then self.last_tick = tick; return end
        if tick <= self.last_tick then return end
        self.last_tick = tick
        self:_step(tick)
    end)
    if not ok then self:_disable(failure) end
end

function Host:inspect()
    local result = { status = self.status, error = self.error, tick = self.last_tick, deferred = {},
        source_guard_monitor = self.guard_monitor == true,
        container_latch_monitor = self.container_latch_monitor == true }
    for unit, deferral in pairs(self.deferred) do
        result.deferred[#result.deferred + 1] = { unit = unit, reason = deferral.reason, retry = deferral.retry }
    end
    table.sort(result.deferred, function(a, b) return a.unit < b.unit end)
    if self.controller and self.controller.manifest then
        result.revision = self.controller.manifest.revision
        result.seed = self.deps.storage:get(self.deps.core.G.seed)
        result.assignments = self.controller:assignments()
    end
    if self.native and self.native.diagnostics then result.native = self.native:diagnostics() end
    return result
end

return Host
