"""Synthetic lifecycle checks for the fixture host and its actual UI bootstrap.

Uses the real campaign protocol and numeric save maps; no engine is launched.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
HOST = (ROOT / "prototype/FLPRHost.lua").read_text(encoding="utf-8")
GLOBALS = (ROOT / "prototype/FLPRGlob.lua").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "prototype/M_FLPR.lua").read_text(encoding="utf-8")
CASES = r'''
local cases={}
local function copy(t)
    if type(t)~="table" then return t end
    local r={}; for k,v in pairs(t) do r[k]=copy(v) end; return r
end
local function setup(Core,Host)
    local f={values={},tick=0,world=true,seeds=0,next_calls=0,resolves={},events={},reports={},native_instances={}}
    local manifest={schema=Core.SCHEMA,state="prototype-not-playable",revision=string.rep("a",64),
        bound_revision=string.rep("b",64),world_fingerprint=string.rep("c",64),profile_id="fixture",sources={},targets={}}
    for i=1,3 do
        local id="cre:flprs"..i.."/item/0"
        manifest.sources[i]={id=id,quantity=1,resource_sha256=string.rep(tostring(i),64),
            record={id=id,record={resref="flpri"..i,charges={0,0,0},expiration=0,flags=0}}}
        id="are:bg2600/container/"..(i+4)
        manifest.targets[i]={id=id,capacity=1,record={id=id},resource_sha256=string.rep(tostring(i+3),64)}
    end
    f.manifest=manifest; f.next_revision=manifest.revision
    f.bundles={[manifest.revision]={manifest=manifest,fixture={revision=manifest.revision}}}
    f.storage={get=function(_,name) return f.values[name] or 0 end,
        set=function(_,name,value) f.values[name]=value end}
    f.make_native=function(fixture)
        local native={fixture=fixture}
        f.native_instances[#f.native_instances+1]=native
        function native:inspect_source(id)
            for i,source in ipairs(f.bundles[self.fixture.revision].manifest.sources) do
                if source.id==id then return {identity=string.rep(tostring(i),64),source_id=id,
                    exact_matches=1,quantity=1,payload=copy(source.record.record)} end
            end
            return nil,"MISSING_SOURCE"
        end
        function native:inspect_target(id)
            f.target_inspections=(f.target_inspections or 0)+1
            local reason=f.unavailable or (f.unavailable_targets and f.unavailable_targets[id])
            if reason then return nil,reason end
            return {identity=string.rep("d",64),target_id=id,accepts=1}
        end
        function native:on_observation_boundary(tick)
            assert(not self.tick or tick>self.tick,"NATIVE_CLOCK_DID_NOT_ADVANCE")
            self.tick=tick; return true
        end
        function native:execute(command)
            f.events[#f.events+1]={kind=command.kind,id=command.source_id,target=command.target_id,tick=self.tick,nonce=command.nonce}
            self.pending=command; self.issued=self.tick
            if f.fail_execute then return nil,"NATIVE_EXECUTION_FAILED" end
            return true
        end
        function native:observe(command)
            assert(self.pending==command and self.tick>self.issued,"NATIVE_LATER_BOUNDARY_REQUIRED")
            if f.fail_observe then return nil,"NATIVE_OBSERVATION_FAILED" end
            if command.kind=="deliver" and (f.delivery_waits or 0)>0 then
                f.delivery_waits=f.delivery_waits-1
                return nil,"DELIVERY_SETTLING"
            end
            local receipt=copy(command); receipt.session=command.session
            if command.kind=="suppress" then receipt.removed,receipt.remaining=1,0
            elseif f.held then receipt.created,receipt.nonce_bound_instance,receipt.lootable,receipt.held=1,1,0,1
            else receipt.created,receipt.nonce_bound_instance,receipt.lootable=1,1,1 end
            self.pending=nil; return receipt
        end
        function native:destroy() self.destroyed=true; self.pending=nil end
        function native:diagnostics() return {destroyed=self.destroyed,tick=self.tick} end
        return native
    end
    f.deps={core=Core,storage=f.storage,
        resolve=function(revision) f.resolves[#f.resolves+1]=revision; return f.bundles[revision] end,
        next=function() f.next_calls=f.next_calls+1; return f.bundles[f.next_revision] end,
        seed=function() f.seeds=f.seeds+1; return 1000+f.seeds end,
        native=f.make_native,world=function() return f.world end,tick=function() return f.tick end,
        report=function(status,reason) f.reports[#f.reports+1]={status,reason} end}
    f.host=Host.new(f.deps)
    function f:poll(tick) self.tick=tick or self.tick+1; self.host:poll() end
    function f:capture() self.values.FLPR_FRESH=1; return self.host:capture_new("confirmed-bg1-character-accept") end
    function f:finish()
        for i=1,25 do self:poll() end
        assert(self.host.status=="complete",tostring(self.host.error))
    end
    return f
end
local function fail(value,reason,want) assert(value==nil and tostring(reason):find(want,1,true),tostring(reason)) end

function cases.unmarked_world_never_captures(Core,Host)
    local f=setup(Core,Host); f:poll(); f:poll()
    assert(f.host.status=="inactive" and f.seeds==0 and f.next_calls==0 and #f.events==0)
    assert(next(f.values)==nil)
end
function cases.armed_old_save_world_never_captures(Core,Host)
    local f=setup(Core,Host); f.values.FLPR_FRESH=1; f:poll(); f:poll()
    assert(f.host.status=="inactive" and f.values.FLPR_FRESH==1 and f.values.flpr_state==nil)
    assert(f.seeds==0 and f.next_calls==0 and #f.events==0)
end
function cases.explicit_capture_requires_handoff_and_marker(Core,Host)
    local f=setup(Core,Host)
    local x,e=f.host:capture_new("anything"); fail(x,e,"NEW_GAME_HANDOFF_REQUIRED")
    f.host:destroy(); x,e=f.host:capture_new("confirmed-bg1-character-accept"); fail(x,e,"FRESH_TEMPLATE_REQUIRED")
    assert(f.seeds==0 and #f.events==0)
end
function cases.capture_freezes_once_before_world(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    assert(f.values.flpr_state==2 and f.values.FLPR_FRESH==0 and #f.events==0 and f.seeds==1)
    local saved=copy(f.values)
    assert(f.host:capture_new("confirmed-bg1-character-accept"))
    assert(f.seeds==1 and f.next_calls==1)
    for key,value in pairs(saved) do assert(f.values[key]==value,key) end
end
function cases.all_sources_are_verified_before_first_delivery(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f:finish()
    assert(#f.events==6)
    for i=1,3 do assert(f.events[i].kind=="suppress") end
    for i=4,6 do assert(f.events[i].kind=="deliver") end
    for i=2,6 do assert(f.events[i].tick>=f.events[i-1].tick+2) end
    for _,assignment in ipairs(f.host:inspect().assignments) do
        assert(assignment.source_phase==2 and assignment.delivery_phase==2)
    end
end
function cases.same_or_backwards_tick_does_not_observe_or_repeat(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f:poll(10); f:poll(11)
    assert(#f.events==1 and f.host.pending~=nil)
    for i=1,5 do f:poll(11); f:poll(9) end
    assert(#f.events==1 and f.host.pending~=nil)
    f:poll(12); assert(f.host.pending==nil and #f.events==1)
end
function cases.queued_delivery_waits_without_replaying_or_committing(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.delivery_waits=2
    for i=1,8 do f:poll() end
    assert(#f.events==4 and f.host.pending.kind=="deliver")
    f:poll(); f:poll()
    assert(#f.events==4 and f.host.status=="running" and f.values.flpr_jkind==2)
    assert(f.host:inspect().assignments[1].delivery_phase==1)
    f:poll(); assert(#f.events==4 and f.host.pending==nil)
    f:finish(); assert(#f.events==6)
end
function cases.menu_without_world_never_starts_mutation(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.world=false
    for i=1,10 do f:poll() end
    assert(f.host.status=="waiting" and f.host.last_tick==nil and #f.events==0)
end
function cases.destroy_restore_uses_frozen_revision_not_next_profile(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f:poll(1); f:poll(2); f:poll(3)
    assert(#f.events==1 and not f.host.pending)
    local original_seed=f.values.flpr_seed
    local next_manifest=copy(f.manifest); next_manifest.revision=string.rep("e",64)
    f.bundles[next_manifest.revision]={manifest=next_manifest,fixture={revision=next_manifest.revision}}
    f.next_revision=next_manifest.revision
    local old_native=f.host.native; f.host:destroy(); assert(old_native.destroyed)
    f:poll(1); f:poll(2)
    assert(f.host.bundle.manifest.revision==f.manifest.revision and f.values.flpr_seed==original_seed)
    assert(f.seeds==1 and f.next_calls==1 and f.resolves[#f.resolves]==f.manifest.revision)
    assert(#f.events==2 and f.events[2].id~=f.events[1].id)
end
function cases.missing_frozen_revision_disables_without_fallback(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.bundles[f.manifest.revision]=nil
    f:poll(); f:poll()
    assert(f.host.status=="disabled" and f.host.error=="REVISION_UNAVAILABLE")
    assert(f.next_calls==1 and #f.events==0)
end
function cases.inflight_reload_never_replays(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f:poll(); f:poll(); assert(#f.events==1)
    f.host:destroy(); f:poll(); f:poll()
    assert(f.host.status=="disabled" and f.host.error=="PENDING_RELOAD_UNPROVEN")
    for i=1,10 do f:poll() end
    assert(#f.events==1)
end
function cases.failed_native_execute_never_repeats(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.fail_execute=true; f:poll(); f:poll()
    assert(f.host.status=="disabled" and f.host.error=="NATIVE_EXECUTION_FAILED")
    for i=1,10 do f:poll() end
    assert(#f.events==1 and f.values.flpr_jkind==1)
end
function cases.failed_native_observation_never_repeats(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.fail_observe=true; f:poll(); f:poll(); f:poll()
    assert(f.host.status=="disabled" and f.host.error=="NATIVE_OBSERVATION_FAILED")
    for i=1,10 do f:poll() end
    assert(#f.events==1)
end
function cases.completed_restore_never_duplicates_rewards(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f:finish(); assert(#f.events==6)
    local seed=f.values.flpr_seed
    f.host:destroy(); f:poll(1); f:poll(2)
    assert(f.host.status=="complete" and #f.events==6 and f.values.flpr_seed==seed)
end
function cases.committed_state_takes_precedence_over_stale_fresh_marker(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.values.FLPR_FRESH=1; f:poll(); f:poll()
    assert(f.host.status=="running" and f.values.FLPR_FRESH==0 and f.seeds==1 and f.next_calls==1)
end
function cases.incomplete_capture_never_redraws(Core,Host)
    local f=setup(Core,Host); f.values.flpr_state=1; f.values.FLPR_FRESH=1; f:poll(); f:poll()
    assert(f.host.status=="disabled" and f.host.error=="CAPTURE_INCOMPLETE")
    assert(f.seeds==0 and f.next_calls==0 and #f.events==0)
end
function cases.unavailable_targets_defer_without_journal_backoff_then_deliver(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.unavailable="OWNER_MISSING"
    for i=1,8 do f:poll() end
    assert(f.host.status=="running" and #f.events==3 and f.values.flpr_jkind==0)
    local deferred=f.host:inspect().deferred
    assert(#deferred==3 and deferred[1].reason=="OWNER_MISSING" and deferred[1].unit==1)
    local inspections=f.target_inspections; f.unavailable=nil
    for i=1,5 do f:poll() end
    assert(#f.events==3 and f.target_inspections==inspections)
    f:poll(f.tick+Host.DEFER_TICKS)
    assert(#f.events==4 and f.events[4].kind=="deliver")
    f:finish(); assert(#f.events==6 and #f.host:inspect().deferred==0)
end
function cases.one_unavailable_target_defers_only_its_own_reward(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f.unavailable_targets={["are:bg2600/container/5"]="AREA_UNAVAILABLE"}
    for i=1,20 do f:poll() end
    assert(f.host.status=="running" and #f.events==5 and f.values.flpr_jkind==0)
    local deferred=f.host:inspect().deferred
    assert(#deferred==1 and deferred[1].reason=="AREA_UNAVAILABLE")
    for _,event in ipairs(f.events) do assert(event.target~="are:bg2600/container/5") end
    f.unavailable_targets=nil; f:poll(f.tick+Host.DEFER_TICKS); f:finish()
    assert(#f.events==6)
end
function cases.unexpected_target_failure_still_disables_before_journal(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.unavailable="TARGET_NOT_FIXTURE_GROUND_PILE"
    for i=1,8 do f:poll() end
    assert(f.host.status=="disabled" and f.host.error=="TARGET_NOT_FIXTURE_GROUND_PILE")
    assert(#f.events==3 and f.values.flpr_jkind==0)
end
function cases.held_creature_rewards_complete_and_never_redeliver_after_restore(Core,Host)
    local f=setup(Core,Host)
    for _,target in ipairs(f.manifest.targets) do target.record.kind="actor" end
    f.held=true; assert(f:capture()); f:finish(); assert(#f.events==6)
    for _,assignment in ipairs(f.host:inspect().assignments) do
        assert(assignment.delivery_phase==Core.PHASE.held)
    end
    f.host:destroy(); f:poll(1); f:poll(2)
    assert(f.host.status=="complete" and #f.events==6)
end
function cases.held_receipt_for_non_creature_target_is_quarantined_and_stops(Core,Host)
    local f=setup(Core,Host); f.held=true; assert(f:capture())
    for i=1,10 do f:poll() end
    assert(f.host.status=="disabled" and f.host.error=="MUTATION_QUARANTINED" and #f.events==4)
    -- Deliveries run in unit order, so the first unit carries the quarantine.
    assert(f.host:inspect().assignments[1].delivery_phase==Core.PHASE.quarantined)
end

local function bootstrap(Core,Host,Globals,text,options)
    options=options or {}
    local f=setup(Core,Host)
    local listeners={}
    f.bootstrap_events={}; f.prepare_calls={}; f.factory_calls={}
    f.pristine_bytes='AREAV1.0'..string.rep('\0',0x70-8)..'\212\104\0\0'..
        string.rep('\0',4)..'\20\107\0\0'..string.rep('\0',27492-0x7C)
    f.pristine_opens=0
    f.native_fixture={revision=f.manifest.revision,ready_detail=f.ready_detail}
    if options.gate then
        local id="are:flpru1/container/1"
        f.manifest.sources[1].id=id
        f.manifest.sources[1].record.id=id
        f.manifest.sources[1].record.kind="container"
        f.native_fixture.sources={[id]={kind="guarded-container",area="FLPRU1",script_name="FLPRU1A",
            x=1140,y=650,identity=string.rep("a",64),
            payload={resref="flpri1",charges={0,0,0},expiration=0,flags=1}}}
        f.native_fixture.source_gate={schema="flpr-unvisited-fixture-gate-v1",source_id=id}
        f.native_fixture.resource_hashes={['FLPRU1.ARE']=string.rep('1',64)}
    end
    EEex_Active=true; FLPR=nil
    EEex_GameState_GetGlobalInt=function(key) return f.storage:get(key) end
    EEex_GameState_SetGlobalInt=function(key,value) return f.storage:set(key,value) end
    EEex_GameState_AddDestroyedListener=function(fn) listeners.destroy=fn end
    EEex_Menu_AddAfterMainFileLoadedListener=function(fn) listeners.menus=fn end
    Infinity_GetClockTicks=function() return 100 end
    Infinity_IsMenuOnStack=function(name) return name=="WORLD_ACTIONBAR" and f.world end
    Infinity_GetCurrentScreenName=function() return "world" end
    Infinity_DisplayString=function() end
    EEexRemote={Poll=function() f.remote_polls=(f.remote_polls or 0)+1; return "" end}
    print=function(message) f.last_log=message end
    io={open=function(path,mode)
        assert(options.gate and path=='flpr-fixture/FLPRU1.pristine.are' and mode=='rb',
            'UNEXPECTED_FIXTURE_FILE_OPEN')
        f.bootstrap_events[#f.bootstrap_events+1]='pristine_open'
        f.pristine_opens=f.pristine_opens+1
        if options.missing_pristine then return nil end
        return {read=function(_,form)
                assert(form=='*a'); f.bootstrap_events[#f.bootstrap_events+1]='pristine_read'
                return f.pristine_bytes
            end,
            close=function()
                f.bootstrap_events[#f.bootstrap_events+1]='pristine_close'
                return true
            end}
    end}
    EngineGlobals={g_pBaldurChitin={m_pObjectGame={m_worldTime={m_gameTime=0}}}}
    startCampaignData={{id="BG1"},{id="BG2"}}; currentCampaign=1
    startEngine={OnNewGameButtonClick=function()
        f.bootstrap_events[#f.bootstrap_events+1]="newgame"
        f.new_game_clicked=(f.new_game_clicked or 0)+1
    end}
    createCharScreen={AcceptCharacter=function(_,difficulty)
        f.bootstrap_events[#f.bootstrap_events+1]="engine_accept"
        f.accept_count=(f.accept_count or 0)+1
        f.accepted_state=f.values.flpr_state; f.accepted_fresh=f.values.FLPR_FRESH
        return difficulty
    end}
    local host_module=Host
    if options.gate then
        host_module={new=function(deps)
            f.bootstrap_events[#f.bootstrap_events+1]="host_new"
            assert(f.gate_installed,"HOST_BEFORE_GATE_INSTALL")
            return Host.new(deps)
        end}
    end
    local modules={ ["override/FLPRCore.lua"]=Core,["override/FLPRGlob.lua"]=Globals,
        ["override/FLPRHost.lua"]=host_module,["override/FLPRNat.lua"]={
            new=function(_,fixture) return f.make_native(fixture) end,
            supported=function(_,item)
                assert(item.resref=='flpri1')
                if options.unsupported_item then return nil,'ITEM_NOT_NONSTACKABLE' end
                return true
            end},
        ["override/FLPRQA.lua"]={new=function() return {poll=function() end} end}}
    if options.gate then
        local builder,compiler={},{}
        f.builder,f.compiler=builder,compiler
        modules["flpr-fixture/FLPRGateAsm.lua"]=builder
        modules["flpr-fixture/FLPRCampaignPolicy.lua"]=compiler
        modules["override/FLPRGat.lua"]={install=function(env,bundle,build,compile,pristine)
            f.bootstrap_events[#f.bootstrap_events+1]="install"
            assert(env==_G and bundle.manifest==f.manifest and bundle.fixture==f.native_fixture)
            assert(build==builder and compile==compiler)
            assert(type(pristine)=='table' and pristine.bytes==f.pristine_bytes and
                #pristine.bytes==27492 and pristine.resource_sha256==
                bundle.fixture.resource_hashes['FLPRU1.ARE'])
            if options.install_fail then error("GATE_INSTALL_FAILED") end
            f.gate_installed=true
            return {
                reset=function()
                    f.bootstrap_events[#f.bootstrap_events+1]="reset"
                    f.reset_calls=(f.reset_calls or 0)+1
                end,
                prepare_new=function(_,selected,controller)
                    f.bootstrap_events[#f.bootstrap_events+1]="prepare"
                    f.prepare_calls[#f.prepare_calls+1]={bundle=selected,controller=controller,
                        state=f.values.flpr_state,fresh=f.values.FLPR_FRESH}
                    assert(selected.manifest==f.manifest and selected.fixture==f.native_fixture)
                    return true
                end,
                adapter=function(_,selected,native)
                    f.bootstrap_events[#f.bootstrap_events+1]="factory"
                    f.factory_calls[#f.factory_calls+1]={bundle=selected,native=native}
                    return {observe_guard=function() end,observe_removal=function() end,destroy=function() end}
                end,
            }
        end}
    end
    loadfile=function(path)
        if modules[path] then return function() return modules[path] end end
        if options.gate and path=="flpr-fixture/source-gate.lua" then
            return function() return options.missing_pinned and string.rep("e",64) or f.manifest.revision end
        end
        if path=="flpr-fixture/campaign-store/revisions/"..f.manifest.revision..".native.lua" then
            return function()
                f.native_fixture.ready_detail=f.ready_detail
                return f.native_fixture
            end
        end
        if path=="flpr-fixture/next.lua" then return function() return f.next_revision end end
        local revision=path:match("revisions/([0-9a-f]+)%.lua$")
        if revision and f.bundles[revision] then return function() return f.bundles[revision].manifest end end
        return nil
    end
    assert((loadstring or load)(text))()
    if not options.allow_disabled then assert(FLPR.Disabled==nil,tostring(FLPR.Disabled)) end
    f.root=FLPR; f.listeners=listeners
    return f
end
function cases.bootstrap_accept_captures_before_engine_accept(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    assert(f.pristine_opens==0 and f.root.SourceGate==nil)
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(f.root.AcceptCharacter(2)==2 and f.accepted_state==2 and f.accepted_fresh==0)
    assert(f.new_game_clicked==1 and f.accept_count==1)
    local seed=f.values.flpr_seed; assert(f.root.AcceptCharacter(3)==3 and f.values.flpr_seed==seed)
end
function cases.bootstrap_unsupported_item_stops_before_source_suppression(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,unsupported_item=true,allow_disabled=true})
    assert(f.root.Disabled:find('ITEM_NOT_NONSTACKABLE',1,true) and not f.gate_installed)
    assert(f.root.Host==nil and not f.accept_count)
end
function cases.bootstrap_opt_in_gate_installs_prepares_once_and_binds_frozen_native(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true})
    assert(f.gate_installed and f.pristine_opens==1 and
        table.concat(f.bootstrap_events,",")=="pristine_open,pristine_read,pristine_close,install,host_new")
    assert(f.new_game_clicked==nil and f.values.flpr_state==nil and f.accept_count==nil)
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(f.reset_calls==1 and f.bootstrap_events[#f.bootstrap_events-1]=="reset" and
        f.bootstrap_events[#f.bootstrap_events]=="newgame")
    assert(f.root.AcceptCharacter(2)==2)
    assert(#f.prepare_calls==1 and f.prepare_calls[1].state==2 and f.prepare_calls[1].fresh==1)
    assert(f.prepare_calls[1].bundle.manifest==f.manifest and
        f.prepare_calls[1].bundle.fixture==f.native_fixture)
    assert(f.accepted_state==2 and f.accepted_fresh==0)
    assert(table.concat(f.bootstrap_events,",")==
        "pristine_open,pristine_read,pristine_close,install,host_new,reset,newgame,prepare,engine_accept")
    local seed=f.values.flpr_seed
    assert(f.root.AcceptCharacter(3)==3)
    assert(#f.prepare_calls==1 and f.values.flpr_seed==seed and f.accept_count==2)
    f.next_revision=string.rep("e",64)
    f.root.Poll()
    EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime.m_gameTime=1
    f.root.Poll()
    assert(#f.factory_calls==1 and f.factory_calls[1].bundle==f.root.Host.bundle and
        f.factory_calls[1].native==f.root.Host.native)
    assert(f.factory_calls[1].bundle.manifest.revision==f.manifest.revision and
        f.factory_calls[1].bundle.fixture==f.native_fixture)
    assert(f.bootstrap_events[#f.bootstrap_events]=="factory")
end
function cases.bootstrap_opt_in_gate_failure_disables_before_capture_acceptance(Core,Host,Globals,text)
    for _,variant in ipairs({"missing_pinned","missing_pristine","install_fail"}) do
        local options={gate=true,allow_disabled=true}
        options[variant]=true
        local f=bootstrap(Core,Host,Globals,text,options)
        local expected=(variant=="missing_pinned" and "SOURCE_GATE_REVISION_UNAVAILABLE") or
            (variant=="missing_pristine" and "SOURCE_GATE_PRISTINE_MISSING") or "GATE_INSTALL_FAILED"
        assert(f.root.Disabled and tostring(f.root.Disabled):find(expected,1,true))
        assert(f.root.Host==nil and f.gate_installed==nil and f.values.flpr_state==nil)
        assert(#f.prepare_calls==0 and #f.factory_calls==0)
        assert(f.pristine_opens==(variant=="missing_pinned" and 0 or 1))
        f.root.NewGame(); f.values.FLPR_FRESH=1
        local ok=pcall(function() f.root.AcceptCharacter(2) end)
        assert(not ok and f.accept_count==nil and f.values.flpr_state==nil and f.seeds==0)
        assert(#f.prepare_calls==0 and #f.factory_calls==0)
    end
end
function cases.bootstrap_no_intent_accept_cannot_capture(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.values.FLPR_FRESH=1
    f.root.AcceptCharacter(2); assert(f.values.flpr_state==nil and f.accepted_fresh==1)
end
function cases.bootstrap_missing_marker_stops_before_engine_accept(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.root.NewGame()
    assert(not pcall(function() f.root.AcceptCharacter(2) end))
    assert(f.accept_count==nil and f.values.flpr_state==nil and f.root.Host.status=="disabled")
end
function cases.bootstrap_other_campaign_is_not_armed(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); currentCampaign=2; f.root.NewGame()
    assert(f.root.NewGameIntent==nil); f.values.FLPR_FRESH=1; f.root.AcceptCharacter(2)
    assert(f.values.flpr_state==nil)
end
function cases.bootstrap_destroy_clears_intent(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.root.NewGame(); assert(f.root.NewGameIntent)
    f.listeners.destroy(); assert(f.root.NewGameIntent==nil and f.root.Host.status=="waiting")
    f.values.FLPR_FRESH=1; f.root.AcceptCharacter(2); assert(f.values.flpr_state==nil)
end
local function bootstrap_to_complete(f)
    f.root.NewGame(); f.values.FLPR_FRESH=1; assert(f.root.AcceptCharacter(2)==2)
    local shown={}
    Infinity_DisplayString=function(message) shown[#shown+1]=message end
    local clock=EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime
    for i=1,25 do clock.m_gameTime=clock.m_gameTime+1; f.root.Poll() end
    assert(f.root.Host.status=="complete",tostring(f.root.Host.error))
    assert(f.values.flpr_done==1)
    return shown
end
function cases.bootstrap_ready_message_keeps_the_accepted_ground_pile_text(Core,Host,Globals,text)
    local shown=bootstrap_to_complete(bootstrap(Core,Host,Globals,text))
    assert(#shown==1 and shown[1]==
        "Randomiser test ready: 3 test weapons have been placed near the Candlekeep entrance.",tostring(shown[1]))
end
function cases.bootstrap_ready_message_uses_the_fixture_detail(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    f.ready_detail="have been given to the two test cows near the Candlekeep entrance."
    local shown=bootstrap_to_complete(f)
    assert(#shown==1 and shown[1]==
        "Randomiser test ready: 3 test weapons have been given to the two test cows near the Candlekeep entrance.",
        tostring(shown[1]))
end
function cases.bootstrap_save_load_blocks_background_remote_poll(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    for _,blocked in ipairs({"SAVE","SAVE_NEWSAVE","LOAD"}) do
        Infinity_IsMenuOnStack=function(name) return name==blocked and 1 or nil end
        assert(EEexRemote.Poll()=="")
    end
    assert(f.remote_polls==nil)
    Infinity_IsMenuOnStack=function(name) return name=="WORLD_ACTIONBAR" and 1 or nil end
    EEexRemote.Poll(); assert(f.remote_polls==1)
end
function cases.bootstrap_stops_held_capture_when_host_stops_or_world_changes(Core,Host,Globals,text)
    for _, boundary in ipairs({"disabled", "new-game", "destroy"}) do
        local f=bootstrap(Core,Host,Globals,text); bootstrap_to_complete(f)
        local stopped,polled=0,0
        f.root.Held={destroy=function() stopped=stopped+1 end,poll=function() polled=polled+1 end}
        f.root.Poll(); assert(polled==1 and stopped==0)
        if boundary=="disabled" then f.root.Host.status="disabled"; f.root.Poll()
        elseif boundary=="new-game" then f.root.NewGame()
        else f.listeners.destroy() end
        assert(stopped==1 and polled==1)
        if boundary~="disabled" then assert(f.root.Held==nil) end
    end
end
return cases
'''

CASE_NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](lua.execute(CORE), lua.execute(HOST), lua.execute(GLOBALS), BOOTSTRAP)
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "NativeHostTests"] = type(label + "NativeHostTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}})


if __name__ == "__main__":
    unittest.main()
