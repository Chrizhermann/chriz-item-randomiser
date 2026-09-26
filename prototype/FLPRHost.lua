-- Bounded, opt-in native fixture host. This is not the general game adapter.
-- The caller owns the validated immutable artifact library and world boundary.
local Host = {}
Host.__index = Host

function Host.new(deps)
    assert(type(deps) == "table" and deps.core and deps.storage)
    return setmetatable({ deps = deps, status = "waiting", last_tick = nil }, Host)
end

function Host:destroy()
    if self.controller then self.controller:destroy() end
    if self.native then self.native:destroy() end
    self.controller, self.native, self.pending = nil, nil, nil
    self.last_tick, self.bundle, self.error = nil, nil, nil
    self.status = "waiting"
end

function Host:_disable(reason)
    self.error, self.status = tostring(reason), "disabled"
    if self.deps.report then self.deps.report("disabled", self.error) end
end

local function require_result(value, failure)
    if value == nil or value == false then error(failure or "HOST_OPERATION_FAILED", 0) end
    return value
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
    self.native = require_result(deps.native(selected.fixture))
    self.status = "running"
    if deps.storage:get(deps.core.G.journal) ~= 0 then
        -- No item-pointer provenance survives loading. Do not replay or infer
        -- ownership from a matching item already present in the world.
        error("PENDING_RELOAD_UNPROVEN", 0)
    end
end

function Host:capture_new(handoff)
    local ok, failure = pcall(function()
        assert(handoff == "confirmed-bg1-character-accept", "NEW_GAME_HANDOFF_REQUIRED")
        local deps = self.deps
        local state = deps.storage:get(deps.core.G.state)
        if state == 2 and deps.storage:get("FLPR_FRESH") == 0 then return end
        assert(state == 0 and deps.storage:get("FLPR_FRESH") == 1, "FRESH_TEMPLATE_REQUIRED")
        local selected = require_result(deps.next(), "NEXT_REVISION_UNAVAILABLE")
        local controller = require_result(deps.core.new(deps.storage, function() return nil end))
        require_result(controller:capture(selected.manifest, deps.seed(), "confirmed-new-game"))
        deps.storage:set("FLPR_FRESH", 0)
        assert(deps.storage:get("FLPR_FRESH") == 0, "FRESH_MARKER_WRITE_FAILED")
        self.status = "waiting"
    end)
    if not ok then self:_disable(failure); return nil, failure end
    return true
end

function Host:_step(tick)
    if self.status == "waiting" then self:_initialize() end
    if self.status ~= "running" then return end
    self.controller:on_observation_boundary()
    require_result(self.native:on_observation_boundary(tick))
    if self.pending then
        local receipt, reason = self.native:observe(self.pending)
        if receipt == nil and reason == "DELIVERY_SETTLING" then
            assert(self.pending.kind == "deliver", "UNEXPECTED_SETTLING_SOURCE")
            return
        end
        require_result(receipt, reason)
        require_result(self.controller:observe(receipt))
        self.pending = nil
        return
    end
    local assignments = require_result(self.controller:assignments())
    -- Suppress the entire fixture pool before exposing any relocated rewards.
    for index, source in ipairs(self.bundle.manifest.sources) do
        local phase
        for _, assignment in ipairs(assignments) do
            if assignment.source == index then phase = assignment.source_phase; break end
        end
        if phase ~= 2 then
            assert(phase == 0, "SOURCE_NOT_READY")
            local witness = require_result(self.native:inspect_source(source.id))
            local command = require_result(self.controller:begin_source(index, witness))
            self.pending = command
            require_result(self.native:execute(command))
            return
        end
    end
    for _, assignment in ipairs(assignments) do
        if assignment.delivery_phase ~= 2 then
            assert(assignment.delivery_phase == 0, "DELIVERY_NOT_READY")
            local witness = require_result(self.native:inspect_target(assignment.target_id))
            local command = require_result(self.controller:begin_delivery(assignment.unit, witness))
            self.pending = command
            require_result(self.native:execute(command))
            return
        end
    end
    self.status = "complete"
    if self.deps.report then self.deps.report("complete") end
end

function Host:poll()
    if self.status == "disabled" or self.status == "inactive" or self.status == "complete" then return end
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
    local result = { status = self.status, error = self.error, tick = self.last_tick }
    if self.controller and self.controller.manifest then
        result.revision = self.controller.manifest.revision
        result.seed = self.deps.storage:get(self.deps.core.G.seed)
        result.assignments = self.controller:assignments()
    end
    if self.native and self.native.diagnostics then result.native = self.native:diagnostics() end
    return result
end

return Host
