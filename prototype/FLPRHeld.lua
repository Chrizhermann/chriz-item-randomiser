-- Controlled, one-shot fixture observer. Never installed by the normal TP2.
-- The harness owns the quiet area and allows only the two ordinary death
-- actions while armed, then leaves the ground items untouched until complete.
-- No arbitrary item replacement, transformation, or unfinished-save recovery
-- is claimed. This joins proven native trace events to the saved Core receipt.
local Held = {}
Held.__index = Held
Held.REVISION = "0d3ede4e2caa0665b9753645169fbc5ce0f681870709da66d90bc08ba5e83877"
Held.MAX_TICKS = 300

local function require_result(value, reason)
    if value == nil or value == false then error(reason or "HELD_OBSERVATION_FAILED", 0) end
    return value
end
local function flat(p)
    return {resref=p.resref,charge1=p.charges[1],charge2=p.charges[2],charge3=p.charges[3],
        expiration=p.expiration,flags=p.flags}
end
local function snapshot_valid(snapshot, armed)
    assert(type(snapshot) == "table" and snapshot.schema == "flpr-drop-trace-v1" and
        snapshot.installed == true and type(snapshot.events) == "table" and
        snapshot.armed == armed and type(snapshot.stable) == "boolean", "HELD_TRACE_METADATA_INVALID")
    assert(snapshot.overflow == false, "DROP_TRACE_OVERFLOW")
end

function Held.new(host, Drop, trace)
    assert(host.status == "complete" and host.controller.manifest.revision == Held.REVISION,
        "HELD_REQUIRES_QUIET_FIXTURE")
    assert(host.deps.storage:get(host.deps.core.G.journal) == 0, "MUTATION_PENDING")
    local self = setmetatable({host=host,controller=host.controller,native=host.native,
        trace=trace,generation={},status="waiting",last_tick=nil}, Held)
    local watches = {}
    for _, a in ipairs(require_result(self.controller:assignments())) do
        assert(a.source_phase == 2 and a.delivery_phase == 4, "HELD_FIXTURE_NOT_READY")
        local request = require_result(self.controller:held_request(a.unit))
        local binding = require_result(self.native:held_binding(request))
        watches[#watches+1] = {request_token=request,owner_pointer=binding.owner_pointer,
            original_item_pointer=binding.original_item_pointer,slot=binding.slot,payload=flat(binding.payload)}
    end
    assert(#watches == 3, "HELD_FIXTURE_SHAPE")
    self.tracker = require_result(Drop.new(self.generation, watches))
    trace.arm()
    return self
end

function Held:destroy()
    self.trace.stop()
    self.status, self.candidates = "destroyed", nil
end

function Held:poll(tick)
    if self.status == "complete" or self.status == "disabled" or self.status == "destroyed" then return end
    local ok, reason = pcall(function()
        assert(self.host.controller == self.controller and self.host.native == self.native and
            self.host.status == "complete", "HELD_SESSION_CHANGED")
        assert(type(tick) == "number" and tick == math.floor(tick) and tick >= 0 and tick < math.huge, "HELD_CLOCK_INVALID")
        if not self.last_tick then self.last_tick, self.started_tick = tick, tick; return end
        if tick <= self.last_tick then return end
        self.last_tick = tick
        assert(tick - self.started_tick < Held.MAX_TICKS, "HELD_DROP_TIMEOUT")
        if self.status == "waiting" then
            local snapshot = self.trace.inspect()
            snapshot_valid(snapshot, true)
            -- This synthetic scene has three rewards and one control item.
            -- Each ordinary death drop produces the three verified hook rows.
            if #snapshot.events < 12 then return end
            self.trace.stop(); self.status = "stopping"
            return
        end
        if self.status == "stopping" then
            local snapshot = self.trace.inspect()
            snapshot_valid(snapshot, false)
            if not snapshot.stable then return end
            assert(not snapshot.armed and #snapshot.events == 12, "HELD_UNEXPECTED_TRACE")
            for _, event in ipairs(snapshot.events) do require_result(self.tracker:ingest(self.generation, event)) end
            self.candidates = require_result(self.tracker:finish(self.generation,
                {count=#snapshot.events,stable=snapshot.stable,armed=snapshot.armed,overflow=snapshot.overflow}))
            assert(#self.candidates == 3, "HELD_TRACE_INCOMPLETE")
            for _, candidate in ipairs(self.candidates) do
                require_result(self.native:verify_held_drop(candidate.request_token, candidate))
            end
            self.status = "ground-observed"
            return
        end
        assert(self.status == "ground-observed", "HELD_STATE_INVALID")
        -- Require the exact ground instances on another later engine tick.
        -- No pickup/action is permitted by the fixture harness during this gap.
        for _, candidate in ipairs(self.candidates) do
            require_result(self.native:verify_held_drop(candidate.request_token, candidate))
        end
        self.controller:on_observation_boundary()
        for _, candidate in ipairs(self.candidates) do
            local request, receipt = candidate.request_token, {}
            for key, value in pairs(request) do receipt[key] = value end
            receipt.exact_transferred_instance, receipt.lootable = 1, 1
            require_result(self.controller:confirm_held(receipt))
        end
        self.status = "complete"
    end)
    if not ok then
        self.trace.stop()
        self.status, self.error = "disabled", tostring(reason)
    end
end

function Held:inspect()
    return {status=self.status,error=self.error,tick=self.last_tick,
        candidates=self.candidates and #self.candidates or 0,
        scope="controlled-quiet-fixture-only",unfinished_reload_supported=false}
end
return Held
