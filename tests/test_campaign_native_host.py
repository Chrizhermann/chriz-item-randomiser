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
# The engine loads both installed M_ files; M_FLPR.lua shows the messages of M_FLPRUI.lua.
UI = (ROOT / "prototype/M_FLPRUI.lua").read_text(encoding="utf-8")
CASES = r'''
local cases={}
local function copy(t)
    if type(t)~="table" then return t end
    local r={}; for k,v in pairs(t) do r[k]=copy(v) end; return r
end
-- The native fixture shape the exporter and the fixture builder write: an
-- endpoint for every manifest source and target.
local function native_fixture(manifest)
    local fixture={revision=manifest.revision,sources={},targets={}}
    for i,source in ipairs(manifest.sources) do
        fixture.sources[source.id]={area="BG2600",script_name="FLPRS"..i,
            identity=string.rep(tostring(i),64),payload=copy(source.record.record)}
    end
    for i,target in ipairs(manifest.targets) do
        fixture.targets[target.id]={kind="ground-pile",area="BG2600",
            script_name="FLPRT"..i,identity=string.rep(tostring(i+3),64),x=100+i,y=200}
    end
    return fixture
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
    f.bundles={[manifest.revision]={manifest=manifest,fixture=native_fixture(manifest)}}
    -- Same transaction contract as FLPRGlob.lua: rollback restores what each
    -- GLOBAL read before its first write, so an absent GLOBAL becomes 0.
    f.storage={get=function(_,name) return f.values[name] or 0 end,
        set=function(self,name,value)
            local undo=f.transaction
            if undo and undo.previous[name]==nil then
                undo.previous[name]=self:get(name); undo.order[#undo.order+1]=name
            end
            f.values[name]=value
        end,
        begin=function() assert(f.transaction==nil,"STORAGE_TRANSACTION_ACTIVE"); f.transaction={previous={},order={}} end,
        commit=function() assert(f.transaction,"STORAGE_TRANSACTION_MISSING"); f.transaction=nil end,
        rollback=function()
            local undo=f.transaction; f.transaction=nil
            for i=#undo.order,1,-1 do f.values[undo.order[i]]=undo.previous[undo.order[i]] end
        end}
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
    function f:capture() self.values.FLPR_FRESH=1; return self.host:capture_new("confirmed-character-accept") end
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
    f.host:destroy(); x,e=f.host:capture_new("confirmed-character-accept"); fail(x,e,"FRESH_TEMPLATE_REQUIRED")
    assert(f.seeds==0 and #f.events==0)
end
function cases.capture_freezes_once_before_world(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    assert(f.values.flpr_state==2 and f.values.FLPR_FRESH==0 and #f.events==0 and f.seeds==1)
    local saved=copy(f.values)
    assert(f.host:capture_new("confirmed-character-accept"))
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
    f.bundles[next_manifest.revision]={manifest=next_manifest,fixture=native_fixture(next_manifest)}
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
-- A dead target whose corpse still holds a copy of the reward: DropItem could
-- take the wrong one, so the reward waits like one for an unavailable target.
function cases.corpse_holding_a_copy_defers_like_an_unavailable_target(Core,Host)
    local f=setup(Core,Host); assert(f:capture()); f.unavailable="CORPSE_ITEM_AMBIGUOUS"
    for i=1,8 do f:poll() end
    assert(f.host.status=="running" and #f.events==3 and f.values.flpr_jkind==0)
    local deferred=f.host:inspect().deferred
    assert(#deferred==3 and deferred[1].reason=="CORPSE_ITEM_AMBIGUOUS")
    f.unavailable=nil; f:poll(f.tick+Host.DEFER_TICKS); f:finish()
    assert(#f.events==6 and #f.host:inspect().deferred==0)
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
-- Without values written by an earlier part of the capture, a failed capture
-- would leave a half-captured campaign the next New Game cannot repair.
function cases.failed_capture_restores_every_global_and_can_retry(Core,Host)
    local f=setup(Core,Host)
    f.values.FLPR_QOTHER=7
    f.deps.prepare_campaign=function(_,controller)
        f.values_seen=f.values.flpr_state
        f.storage:set("FLPR_QOTHER",1); f.storage:set("FLPR_QNEW",1)
        if f.refuse then return nil,"PREPARE_REFUSED" end
        return true
    end
    f.refuse=true
    local x,e=f:capture(); fail(x,e,"PREPARE_REFUSED")
    assert(f.values_seen==2 and f.host.status=="disabled" and f.transaction==nil)
    assert(f.values.FLPR_FRESH==1 and f.values.FLPR_QOTHER==7 and f.values.FLPR_QNEW==0)
    for name,value in pairs(f.values) do
        assert(not name:find("^flpr_") or value==0,name)
    end
    assert(#f.reports==1 and f.reports[1][1]=="disabled" and f.reports[1][2]=="PREPARE_REFUSED")
    f.refuse=nil; f.host:destroy(); assert(f:capture())
    assert(f.values.flpr_state==2 and f.values.FLPR_FRESH==0 and f.values.FLPR_QOTHER==1 and f.seeds==2)
    f:finish(); assert(#f.events==6)
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
    f.bootstrap_events={}; f.prepare_calls={}; f.factory_calls={}; f.supported_items={}
    f.pristine_bytes='AREAV1.0'..string.rep('\0',0x70-8)..'\212\104\0\0'..
        string.rep('\0',4)..'\20\107\0\0'..string.rep('\0',27492-0x7C)
    f.pristine_opens=0
    -- Match the native fixture shape consumed by the adapter and bootstrap.
    f.native_fixture=native_fixture(f.manifest)
    if options.gate then
        local id="are:flpru1/container/1"
        f.native_fixture.sources[f.manifest.sources[1].id]=nil
        f.manifest.sources[1].id=id
        f.manifest.sources[1].record.id=id
        f.manifest.sources[1].record.kind="container"
        f.native_fixture.sources[id]={kind="guarded-container",area="FLPRU1",script_name="FLPRU1A",
            x=1140,y=650,identity=string.rep("a",64),
            payload={resref="flpri1",charges={0,0,0},expiration=0,flags=1}}
        f.native_fixture.source_gate={schema="flpr-unvisited-fixture-gate-v1",source_id=id}
        f.native_fixture.resource_hashes={['FLPRU1.ARE']=string.rep('1',64)}
        if options.multiple_pinned then
            local second_revision=string.rep('e',64)
            local second_id='are:flpru1/container/2'
            local second_manifest=copy(f.manifest)
            second_manifest.revision=second_revision
            second_manifest.sources[1].id=second_id
            second_manifest.sources[1].record.id=second_id
            second_manifest.sources[1].record.record.resref='flpri2'
            local second_fixture=copy(f.native_fixture)
            second_fixture.revision=second_revision
            second_fixture.sources[id]=nil
            second_fixture.sources[second_id]=copy(f.native_fixture.sources[id])
            second_fixture.sources[second_id].script_name='FLPRU1B'
            second_fixture.sources[second_id].payload.resref='flpri2'
            second_fixture.source_gate.source_id=second_id
            f.bundles[second_revision]={manifest=second_manifest,fixture=second_fixture}
            f.pinned_revisions={f.manifest.revision,second_revision}
        end
    end
    f.bundles[f.manifest.revision].fixture=f.native_fixture
    EEex_Active=true; FLPR=nil
    EEex_GameState_GetGlobalInt=function(key) return f.storage:get(key) end
    EEex_GameState_SetGlobalInt=function(key,value) return f.storage:set(key,value) end
    EEex_GameState_AddDestroyedListener=function(fn) listeners.destroy=fn end
    EEex_Menu_AddAfterMainFileLoadedListener=function(fn) listeners.menus=fn end
    Infinity_GetClockTicks=function() return 100 end
    Infinity_IsMenuOnStack=function(name) return name=="WORLD_ACTIONBAR" and f.world end
    Infinity_GetCurrentScreenName=function() return "world" end
    f.shown={}
    Infinity_DisplayString=function(message) f.shown[#f.shown+1]=message end
    -- The engine's popup (util.lua) and string table, as M_FLPRUI.lua uses them.
    f.popups={}; uiStrings={CANCEL_BUTTON="Cancel"}; FLPR_TEXT=nil
    popup2Button=function(info,ok_text,ok_func,cancel_text,cancel_func)
        f.popups[#f.popups+1]={text=uiStrings[info],ok=uiStrings[ok_text],start=ok_func,
            cancel=uiStrings[cancel_text],cancel_func=cancel_func}
    end
    -- util.lua's info popup, which the player closes with Done.
    f.infos={}
    popupInfo=function(info) f.infos[#f.infos+1]=uiStrings[info] end
    print=function(message) f.last_log=message end
    io={open=function(path,mode)
        if path=='randomiser-campaigns/last-error.txt' then
            assert(mode=='w','UNEXPECTED_ERROR_FILE_MODE')
            local parts={}
            return {write=function(_,...) for _,part in ipairs({...}) do parts[#parts+1]=part end end,
                close=function() f.error_file=table.concat(parts); return true end}
        end
        -- Item Randomiser's Mode 1 and Mode 2 markers; options.item_randomiser names the installed one.
        local marker=path:match('^override/(flrandomiser[12]%.mrk)$')
        if marker then
            assert(mode=='rb','UNEXPECTED_MARKER_FILE_MODE')
            if options.item_randomiser~=marker then return nil end
            return {close=function() return true end}
        end
        assert(options.gate and path=='randomiser-campaigns/FLPRU1.pristine.are' and mode=='rb',
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
    -- The engine's campaign state: CInfGame's campaign (a CAMPAIGN.2DA row, or
    -- nil) and its Black Pits, tutorial and Throne of Bhaal flags.
    f.campaign={name="BG1"}
    EngineGlobals={g_pBaldurChitin={m_pObjectGame={m_worldTime={m_gameTime=0},
        m_sCurrentCampaign={m_pchData={get=function() return f.campaign.name or "" end}}}}}
    game={GetMissionPackInfoTBP=function() return f.campaign.tbp==true end,
        GetMissionPackInfo=function() return f.campaign.tutorial==true end,
        GetMissionPackInfo25=function() return f.campaign.tob==true end}
    startEngine={GetCampaign=function() return f.campaign.name and 3 or (f.campaign.tbp and 2 or 1) end,
        OnNewGameButtonClick=function()
            f.bootstrap_events[#f.bootstrap_events+1]="newgame"
            f.new_game_clicked=(f.new_game_clicked or 0)+1; f.chargen_mode=1
        end,
        OnImportCharacterButtonClick=function()
            f.bootstrap_events[#f.bootstrap_events+1]="import"
            f.import_clicked=(f.import_clicked or 0)+1; f.chargen_mode=7
        end}
    -- Character creation mode (CScreenCreateChar engine state) the last start began.
    createCharScreen={GetEngineState=function() return f.chargen_mode end,
        AcceptCharacter=function(_,difficulty)
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
    local script_module={new=function() error('UNEXPECTED_SCRIPT_SOURCE') end}
    -- Hook locations; M_FLPR hands this table to each module that patches code.
    f.site_module={}; f.module_args={}
    local modules={ ["override/FLPRCore.lua"]=Core,["override/FLPRGlob.lua"]=Globals,
        ["override/FLPRSite.lua"]=f.site_module,
        ["override/FLPRHost.lua"]=host_module,["override/FLPRNat.lua"]={
            new=function(_,fixture) return f.make_native(fixture) end,
            supported=function(_,item)
                assert(item.resref=='flpri1' or (options.multiple_pinned and item.resref=='flpri2'))
                f.supported_items[#f.supported_items+1]=item.resref
                if options.unsupported_item or (options.unsupported_second and item.resref=='flpri2') then
                    return nil,'ITEM_NOT_NONSTACKABLE'
                end
                return true
            end},
        ["override/FLPRScript.lua"]=script_module}
    if options.gate then
        local builder,compiler={},{}
        f.builder,f.compiler=builder,compiler
        modules["override/FLPRGateAsm.lua"]=builder
        modules["override/FLPRCampaignPolicy.lua"]=compiler
        -- The gate hooks at launch and builds a revision's checks when a game
        -- needs it, through the loader M_FLPR hands it (FLPRGat.lua).
        modules["override/FLPRGat.lua"]={
            source_ids=function(fixture) return fixture.source_gate.source_ids or {fixture.source_gate.source_id} end,
            new=function(env,deps)
            f.bootstrap_events[#f.bootstrap_events+1]="install"
            assert(env==_G and type(deps)=='table' and type(deps.load)=='function' and
                type(deps.report)=='function' and type(deps.inventory)=='function' and
                type(deps.cursed)=='function' and type(deps.pristine)=='table')
            local pinned=options.missing_pinned and {string.rep("e",64)} or
                f.pinned_revisions or {f.manifest.revision}
            assert(#deps.revisions==#pinned)
            for index,revision in ipairs(pinned) do assert(deps.revisions[index]==revision) end
            assert(deps.build==builder and deps.compile==compiler and deps.Script==script_module)
            if options.install_fail then error(options.install_fail==true and "GATE_INSTALL_FAILED" or
                options.install_fail) end
            f.gate_installed=true; f.gate_deps=deps; f.gate_bundles={}
            local allowed={}
            for _,revision in ipairs(deps.revisions) do allowed[revision]=true end
            return {
                guards=function(_,revision) return allowed[revision]==true end,
                ensure=function(_,revision)
                    f.bootstrap_events[#f.bootstrap_events+1]="ensure"
                    local bundle=deps.load(revision)
                    assert(bundle.manifest==f.bundles[revision].manifest and
                        bundle.fixture==f.bundles[revision].fixture)
                    local pristine=deps.pristine.FLPRU1
                    assert(type(pristine)=='table' and pristine.bytes==f.pristine_bytes and
                        #pristine.bytes==27492 and pristine.resource_sha256==
                        bundle.fixture.resource_hashes['FLPRU1.ARE'])
                    if options.ensure_fail then error(options.ensure_fail,0) end
                    f.gate_bundles[#f.gate_bundles+1]=bundle
                    return true
                end,
                reset=function()
                    f.bootstrap_events[#f.bootstrap_events+1]="reset"
                    f.reset_calls=(f.reset_calls or 0)+1
                end,
                prepare_new=function(_,selected,controller)
                    f.bootstrap_events[#f.bootstrap_events+1]="prepare"
                    f.prepare_calls[#f.prepare_calls+1]={bundle=selected,controller=controller,
                        state=f.values.flpr_state,fresh=f.values.FLPR_FRESH}
                    assert(selected.manifest==f.manifest and selected.fixture==f.native_fixture)
                    local built=false
                    for _,bundle in ipairs(f.gate_bundles) do
                        if bundle.manifest.revision==selected.manifest.revision then built=true end
                    end
                    if not built then return nil,"CAMPAIGN_GATE_REVISION_NOT_BUILT" end
                    if options.prepare_fail then return nil,"GATE_PREPARE_FAILED" end
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
        if modules[path] then
            return function(...) f.module_args[path]={...}; return modules[path] end
        end
        if options.gate and path=="randomiser-campaigns/source-gate.lua" then
            return function() return options.missing_pinned and string.rep("e",64) or
                f.pinned_revisions or f.manifest.revision end
        end
        local native_revision=path:match("revisions/([0-9a-f]+)%.native%.lua$")
        if native_revision and f.bundles[native_revision] then
            return function() return f.bundles[native_revision].fixture end
        end
        if path=="randomiser-campaigns/next.lua" then
            return function() return f.next_pointer or {schema="flir-next-v2",starts={BG1=f.next_revision}} end
        end
        local revision=path:match("revisions/([0-9a-f]+)%.lua$")
        if revision then f.revision_reads=(f.revision_reads or 0)+1 end
        if revision and f.bundles[revision] then return function() return f.bundles[revision].manifest end end
        return nil
    end
    assert((loadstring or load)(text))()
    if not options.allow_disabled then assert(FLPR.Disabled==nil,tostring(FLPR.Disabled)) end
    f.root=FLPR; f.listeners=listeners
    return f
end
-- A refused New Game or character acceptance shows the engine's two-button
-- popup: "Start without randomiser" (start) or Cancel (nothing happens).
local function popup(f,key)
    assert(#f.popups==1,"expected one popup, got "..#f.popups)
    local shown=table.remove(f.popups)
    assert(shown.text==FLPRUI_Text(key) and shown.ok==FLPRUI_Text("start_plain") and
        shown.cancel=="Cancel" and shown.cancel_func==nil,tostring(shown.text))
    return shown
end
local function captured_nothing(f)
    for name,value in pairs(f.values) do
        assert(name=="FLPR_FRESH" or value==0,name.."="..tostring(value))
    end
end
function cases.bootstrap_accept_captures_before_engine_accept(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    assert(f.pristine_opens==0 and f.root.SourceGate==nil)
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(f.root.AcceptCharacter(2)==2 and f.accepted_state==2 and f.accepted_fresh==0)
    assert(f.new_game_clicked==1 and f.accept_count==1)
    local seed=f.values.flpr_seed; assert(f.root.AcceptCharacter(3)==3 and f.values.flpr_seed==seed)
end
-- An item this engine cannot handle stops its revision before any of its
-- sources is suppressed: New Game asks, and the start choice is unrandomised.
function cases.bootstrap_unsupported_item_stops_before_source_suppression(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,unsupported_item=true})
    assert(f.root.Disabled==nil and f.gate_installed and f.root.Host and f.pristine_opens==0)
    f.root.NewGame()
    local shown=popup(f,"setup_failed")
    assert(f.error_file:find("new-game: ",1,true) and f.error_file:find('ITEM_NOT_NONSTACKABLE',1,true))
    assert(#f.gate_bundles==0 and f.new_game_clicked==nil and f.root.NewGameIntent==nil)
    shown.start(); f.values.FLPR_FRESH=1
    assert(f.new_game_clicked==1 and f.root.AcceptCharacter(2)==2 and f.accept_count==1)
    assert(f.values.flpr_state==nil and #f.prepare_calls==0)
end
-- Launch reads no campaign revision, so a revision whose item this engine
-- cannot handle fails only the games that need it.
function cases.bootstrap_second_profile_unsupported_fails_only_its_games(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,multiple_pinned=true,unsupported_second=true})
    assert(f.root.Disabled==nil and f.gate_installed and f.root.Host)
    assert(f.pristine_opens==0 and #f.supported_items==0 and f.revision_reads==nil)
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(#f.popups==0 and f.root.AcceptCharacter(2)==2 and f.accepted_state==2)
    assert(table.concat(f.supported_items,',')=='flpri1')
    local ok,reason=pcall(f.gate_deps.load,f.pinned_revisions[2])
    assert(not ok and tostring(reason):find('ITEM_NOT_NONSTACKABLE',1,true))
    assert(table.concat(f.supported_items,',')=='flpri1,flpri2' and f.pristine_opens==1)
end
function cases.bootstrap_two_profiles_share_one_pristine_area_copy(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,multiple_pinned=true})
    assert(f.root.Disabled==nil and f.gate_installed and f.root.Host and f.pristine_opens==0)
    for _,revision in ipairs(f.pinned_revisions) do assert(f.root.SourceGate:ensure(revision)) end
    assert(f.pristine_opens==1 and table.concat(f.supported_items,',')=='flpri1,flpri2')
    assert(#f.gate_bundles==2 and f.gate_bundles[1].manifest==f.manifest and
        f.gate_bundles[1].fixture==f.native_fixture)
    local second=f.bundles[f.pinned_revisions[2]]
    assert(f.gate_bundles[2].manifest==second.manifest and f.gate_bundles[2].fixture==second.fixture)
    assert(table.concat(f.bootstrap_events,',')==
        'install,host_new,ensure,pristine_open,pristine_read,pristine_close,ensure')
end
function cases.bootstrap_opt_in_gate_installs_prepares_once_and_binds_frozen_native(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true})
    -- Launch places the gate's hooks and reads no campaign revision.
    assert(f.gate_installed and f.pristine_opens==0 and f.revision_reads==nil and
        table.concat(f.bootstrap_events,",")=="install,host_new")
    local gate_args=f.module_args["override/FLPRGat.lua"]
    assert(#gate_args==1 and gate_args[1]==f.site_module)
    assert(f.new_game_clicked==nil and f.values.flpr_state==nil and f.accept_count==nil)
    -- New Game builds the started campaign's checks before the engine starts it.
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(f.reset_calls==1 and f.pristine_opens==1 and #f.gate_bundles==1 and
        f.bootstrap_events[#f.bootstrap_events]=="newgame")
    assert(f.root.AcceptCharacter(2)==2)
    assert(#f.prepare_calls==1 and f.prepare_calls[1].state==2 and f.prepare_calls[1].fresh==1)
    assert(f.prepare_calls[1].bundle.manifest==f.manifest and
        f.prepare_calls[1].bundle.fixture==f.native_fixture)
    assert(f.accepted_state==2 and f.accepted_fresh==0)
    assert(table.concat(f.bootstrap_events,",")==
        "install,host_new,reset,ensure,pristine_open,pristine_read,pristine_close,newgame,prepare,engine_accept")
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
-- A gate that cannot place its hooks stops the runtime at launch.
function cases.bootstrap_gate_install_failure_disables_before_capture_acceptance(Core,Host,Globals,text)
    for _,variant in ipairs({"install_fail","code_mismatch"}) do
        local options={gate=true,allow_disabled=true}
        options.install_fail=variant=="code_mismatch" and "CAMPAIGN_GATE_CODE_MISMATCH" or true
        local f=bootstrap(Core,Host,Globals,text,options)
        local expected=variant=="install_fail" and "GATE_INSTALL_FAILED" or "CAMPAIGN_GATE_CODE_MISMATCH"
        assert(f.root.Disabled and tostring(f.root.Disabled):find(expected,1,true))
        assert(f.error_file:find("start: ",1,true) and f.error_file:find(expected,1,true),f.error_file)
        assert(f.root.Host==nil and f.gate_installed==nil and f.values.flpr_state==nil)
        assert(#f.prepare_calls==0 and #f.factory_calls==0 and f.pristine_opens==0)
        f.root.NewGame()
        local shown=popup(f,variant=="code_mismatch" and "unsupported" or "setup_failed")
        assert(f.new_game_clicked==nil and f.root.NewGameIntent==nil)
        shown.start()
        assert(f.new_game_clicked==1 and f.root.NewGameIntent==nil)
        f.values.FLPR_FRESH=1
        assert(f.root.AcceptCharacter(2)==2 and f.accept_count==1 and f.accepted_fresh==1)
        assert(f.values.flpr_state==nil and f.seeds==0 and #f.popups==0)
        assert(#f.prepare_calls==0 and #f.factory_calls==0)
    end
end
-- A revision whose checks cannot be built stops only its own starts: New Game
-- asks, and the start choice is unrandomised. An older applied revision whose
-- files are gone fails only when a game needs it.
function cases.bootstrap_revision_failure_asks_before_capture_acceptance(Core,Host,Globals,text)
    for _,variant in ipairs({"missing_pristine","build_fail","code_mismatch"}) do
        local options={gate=true}
        if variant=="missing_pristine" then options.missing_pristine=true
        else options.ensure_fail=variant=="code_mismatch" and "CAMPAIGN_GATE_CODE_MISMATCH:gate_item:not-found" or
            "GATE_BUILD_FAILED" end
        local f=bootstrap(Core,Host,Globals,text,options)
        assert(f.root.Disabled==nil and f.root.Host and f.gate_installed and f.pristine_opens==0)
        local expected=(variant=="missing_pristine" and "SOURCE_GATE_PRISTINE_MISSING") or
            (variant=="build_fail" and "GATE_BUILD_FAILED") or "CAMPAIGN_GATE_CODE_MISMATCH:gate_item"
        f.root.NewGame()
        assert(f.error_file:find("new-game: ",1,true) and f.error_file:find(expected,1,true),f.error_file)
        assert(f.pristine_opens==1 and #f.gate_bundles==0)
        local shown=popup(f,variant=="code_mismatch" and "unsupported" or "setup_failed")
        assert(f.new_game_clicked==nil and f.root.NewGameIntent==nil)
        shown.start()
        assert(f.new_game_clicked==1 and f.root.NewGameIntent==nil)
        f.values.FLPR_FRESH=1
        assert(f.root.AcceptCharacter(2)==2 and f.accept_count==1 and f.accepted_fresh==1)
        assert(f.values.flpr_state==nil and f.seeds==0 and #f.popups==0)
        assert(#f.prepare_calls==0 and #f.factory_calls==0)
    end
    local f=bootstrap(Core,Host,Globals,text,{gate=true,missing_pinned=true})
    assert(f.root.Disabled==nil and f.root.Host and f.pristine_opens==0 and f.revision_reads==nil)
    local ok,reason=pcall(f.gate_deps.load,string.rep("e",64))
    assert(not ok and tostring(reason):find("SOURCE_GATE_REVISION_UNAVAILABLE",1,true))
end
-- Item Randomiser's Mode 1 or Mode 2, installed after this mod, stops the runtime
-- before it loads anything: New Game asks, and the start choice is unrandomised.
function cases.bootstrap_item_randomiser_installed_later_disables(Core,Host,Globals,text)
    for _,marker in ipairs({"flrandomiser1.mrk","flrandomiser2.mrk"}) do
        local f=bootstrap(Core,Host,Globals,text,{gate=true,allow_disabled=true,item_randomiser=marker})
        local reason="ITEM_RANDOMISER_MODE_INSTALLED:override/"..marker
        assert(f.root.Disabled==reason,tostring(f.root.Disabled))
        assert(f.error_file:find("start: "..reason,1,true),f.error_file)
        assert(f.root.Host==nil and f.gate_installed==nil and f.pristine_opens==0)
        f.root.NewGame()
        local shown=popup(f,"setup_failed")
        assert(f.new_game_clicked==nil); shown.start()
        assert(f.new_game_clicked==1 and f.root.NewGameIntent==nil)
        f.values.FLPR_FRESH=1
        assert(f.root.AcceptCharacter(2)==2 and f.accept_count==1 and f.values.flpr_state==nil)
    end
end
function cases.bootstrap_no_intent_accept_cannot_capture(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.values.FLPR_FRESH=1
    f.root.AcceptCharacter(2); assert(f.values.flpr_state==nil and f.accepted_fresh==1)
end
-- The capture is undone; Cancel keeps character creation open, and the start
-- choice begins this adventure unrandomised.
local function refused_accept(f,code,fresh)
    f.root.NewGame(); f.values.FLPR_FRESH=fresh
    assert(f.root.AcceptCharacter(2)==nil)
    local shown=popup(f,"setup_failed")
    assert(f.accept_count==nil and f.root.Host.status=="disabled" and
        f.root.Host.error:find(code,1,true) and f.error_file:find(code,1,true),tostring(f.error_file))
    captured_nothing(f); assert((f.values.FLPR_FRESH or 0)==(fresh or 0))
    assert(#f.shown==0)
    shown.start()
    assert(f.accept_count==1 and (f.accepted_state or 0)==0 and f.root.NewGameIntent==nil)
    assert(f.root.Host.status=="waiting")
    local clock=EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime
    for i=1,3 do clock.m_gameTime=clock.m_gameTime+1; f.root.Poll() end
    assert(f.root.Host.status=="inactive" and #f.shown==0 and #f.events==0)
    captured_nothing(f)
end
function cases.bootstrap_missing_marker_stops_before_engine_accept(Core,Host,Globals,text)
    refused_accept(bootstrap(Core,Host,Globals,text),"FRESH_TEMPLATE_REQUIRED",nil)
end
function cases.bootstrap_fixture_without_sources_stops_before_engine_accept(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.native_fixture.sources=nil
    refused_accept(f,"FIXTURE_SOURCES_MISSING",1)
end
function cases.bootstrap_source_without_endpoint_stops_before_engine_accept(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    f.native_fixture.sources[f.manifest.sources[2].id]=nil
    refused_accept(f,"SOURCE_ENDPOINT_MISSING",1)
end
function cases.bootstrap_refused_accept_can_be_cancelled_and_retried(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    local sources=f.native_fixture.sources; f.native_fixture.sources=nil
    f.root.NewGame(); f.values.FLPR_FRESH=1
    f.root.AcceptCharacter(2); popup(f,"setup_failed")
    assert(f.accept_count==nil and f.root.NewGameIntent)
    f.native_fixture.sources=sources
    assert(f.root.AcceptCharacter(2)==2 and #f.popups==0)
    assert(f.accept_count==1 and f.accepted_state==2 and f.accepted_fresh==0)
end
function cases.bootstrap_failed_gate_preparation_disarms_the_gate(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,prepare_fail=true})
    f.root.NewGame(); f.values.FLPR_FRESH=1; assert(f.reset_calls==1)
    assert(f.root.AcceptCharacter(2)==nil and #f.prepare_calls==1 and f.reset_calls==2)
    local shown=popup(f,"setup_failed"); captured_nothing(f)
    assert(f.values.FLPR_FRESH==1 and f.error_file:find("GATE_PREPARE_FAILED",1,true))
    shown.start(); assert(f.reset_calls==3 and f.accept_count==1 and f.accepted_fresh==1)
end
function cases.bootstrap_accept_sets_reward_selection_flags(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    f.native_fixture.sources[f.manifest.sources[2].id].reward_selected_global="FLPR_QREWARD"
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(f.root.AcceptCharacter(2)==2 and f.accept_count==1)
    -- Without randomization rules no source keeps its original reward.
    assert(f.values.FLPR_QREWARD==1 and f.values.FLPR_QM2_42==0)
end
-- A campaign the applied configuration has no revision for: EET's Black Pits,
-- Black Pits, the tutorial and Throne of Bhaal without a campaign row, and
-- Siege of Dragonspear here.
function cases.bootstrap_other_campaign_asks_before_starting_unrandomised(Core,Host,Globals,text)
    for _,campaign in ipairs({{name="BP1"},{tbp=true},{tutorial=true},{tob=true},{name="SOD"}}) do
        local f=bootstrap(Core,Host,Globals,text); f.campaign=campaign; f.root.NewGame()
        local shown=popup(f,"other_campaign")
        assert(f.new_game_clicked==nil and f.root.NewGameIntent==nil)
        shown.start(); assert(f.new_game_clicked==1 and f.root.NewGameIntent==nil)
        f.values.FLPR_FRESH=1; assert(f.root.AcceptCharacter(2)==2)
        assert(f.values.flpr_state==nil and f.accepted_fresh==1 and f.error_file==nil)
    end
end
-- Each campaign start uses the revision the applied configuration names for
-- it; the main campaign (no campaign row, no flags) is the pointer's "main".
function cases.bootstrap_each_campaign_start_captures_its_own_revision(Core,Host,Globals,text)
    local other=string.rep("d",64)
    local starts={
        {campaign={name="SOD"},pointer={SOD="self"},main=nil,id="SOD"},
        {campaign={name="sod"},pointer={SOD="self"},main=nil,id="SOD"},
        {campaign={},pointer={SOA="self",TOB=other},main="SOA",id="SOA"},
        {campaign={tob=true},pointer={SOA=other,TOB="self"},main="SOA",id="TOB"},
        {campaign={},pointer={BG1="self",SOD=other},main="BG1",id="BG1"},
    }
    for _,start in ipairs(starts) do
        local f=bootstrap(Core,Host,Globals,text)
        local pointer={}
        for name,revision in pairs(start.pointer) do
            pointer[name]=revision=="self" and f.manifest.revision or revision
        end
        f.next_pointer={schema="flir-next-v2",starts=pointer,main=start.main}
        f.campaign=start.campaign; f.root.NewGame()
        assert(#f.popups==0 and f.new_game_clicked==1)
        assert(f.root.NewGameIntent.campaign==start.id and f.root.NewGameIntent.revision==f.manifest.revision)
        f.values.FLPR_FRESH=1
        assert(f.root.AcceptCharacter(2)==2 and f.accepted_state==2 and f.accepted_fresh==0)
        assert(f.root.Inspect().intent.campaign==start.id)
    end
end
-- Importing a character starts a new game through the engine's import screen
-- and captures at character acceptance like New Game.
function cases.bootstrap_import_character_captures_like_new_game(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    f.next_pointer={schema="flir-next-v2",starts={SOA=f.manifest.revision},main="SOA"}
    f.campaign={}; f.root.ImportCharacter()
    assert(#f.popups==0 and f.import_clicked==1 and f.new_game_clicked==nil)
    assert(f.root.NewGameIntent.campaign=="SOA")
    f.values.FLPR_FRESH=1
    assert(f.root.AcceptCharacter(2)==2 and f.accepted_state==2 and f.accepted_fresh==0)
    -- Black Pits imports a party: asked, and the start choice imports unrandomised.
    f=bootstrap(Core,Host,Globals,text); f.campaign={tbp=true}; f.root.ImportCharacter()
    local shown=popup(f,"other_campaign")
    assert(f.import_clicked==nil); shown.start()
    assert(f.import_clicked==1 and f.root.NewGameIntent==nil)
end
-- A cancelled start leaves its intent behind. Character creation that no
-- hooked start began (the tutorial, pre-generating a character, party
-- creation) accepts unrandomised and drops it; restarting creation keeps it.
function cases.bootstrap_only_the_begun_character_creation_captures(Core,Host,Globals,text)
    local others={
        {campaign={tutorial=true},mode=1},
        {mode=4},
        {mode=5},
        {campaign={name="SOD"},mode=1},
    }
    for _,other in ipairs(others) do
        local f=bootstrap(Core,Host,Globals,text)
        f.next_pointer={schema="flir-next-v2",starts={BG1=f.manifest.revision},main="BG1"}
        f.campaign={}; f.root.NewGame()
        assert(#f.popups==0 and f.root.NewGameIntent.chargen==1)
        if other.campaign then f.campaign=other.campaign end
        f.chargen_mode=other.mode; f.values.FLPR_FRESH=1
        assert(f.root.AcceptCharacter(2)==2 and f.accept_count==1 and #f.popups==0)
        assert(f.values.flpr_state==nil and f.accepted_fresh==1 and f.root.NewGameIntent==nil)
    end
    -- Without the engine's creation mode the campaign still tells starts apart.
    local f=bootstrap(Core,Host,Globals,text)
    createCharScreen.GetEngineState=nil
    f.root.NewGame(); f.values.FLPR_FRESH=1
    assert(f.root.NewGameIntent.chargen==nil)
    assert(f.root.AcceptCharacter(2)==2 and f.accepted_state==2 and f.accepted_fresh==0)
    f=bootstrap(Core,Host,Globals,text)
    f.root.ImportCharacter(); f.campaign={tutorial=true}; createCharScreen.GetEngineState=nil
    f.values.FLPR_FRESH=1; assert(f.root.AcceptCharacter(2)==2 and f.values.flpr_state==nil)
end
-- A pointer written before campaign starts were configurable is a BG1 start.
function cases.bootstrap_single_revision_pointer_is_a_bg1_start(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.next_pointer=f.manifest.revision
    f.root.NewGame(); assert(#f.popups==0 and f.root.NewGameIntent.revision==f.manifest.revision)
    f.values.FLPR_FRESH=1; assert(f.root.AcceptCharacter(2)==2 and f.accepted_state==2)
    f=bootstrap(Core,Host,Globals,text); f.next_pointer=f.manifest.revision; f.campaign={name="SOA"}
    f.root.NewGame(); popup(f,"other_campaign")
end
-- An unreadable pointer or campaign state is a setup failure with details,
-- never a silent unrandomised start.
function cases.bootstrap_unreadable_pointer_or_campaign_asks_with_details(Core,Host,Globals,text)
    local broken={
        {pointer={schema="flir-next-v1",starts={}},reason="NEXT_POINTER_INVALID"},
        {pointer={schema="flir-next-v2",starts={BG1="ABC"}},reason="NEXT_POINTER_INVALID"},
        {pointer={schema="flir-next-v2",starts={},main=7},reason="NEXT_POINTER_INVALID"},
        {campaign={name=""},reason="CAMPAIGN_NAME_UNAVAILABLE"},
        {game_missing=true,campaign={},reason="game"},
    }
    for _,case in ipairs(broken) do
        local f=bootstrap(Core,Host,Globals,text)
        f.next_pointer=case.pointer
        if case.campaign then f.campaign=case.campaign end
        if case.game_missing then game=nil end
        f.root.NewGame()
        local shown=popup(f,"setup_failed")
        assert(f.error_file:find("new-game: ",1,true) and f.error_file:find(case.reason,1,true),tostring(f.error_file))
        assert(f.new_game_clicked==nil and f.root.NewGameIntent==nil)
        shown.start(); assert(f.new_game_clicked==1 and f.root.NewGameIntent==nil)
    end
end
function cases.bootstrap_destroy_clears_intent(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text); f.root.NewGame(); assert(f.root.NewGameIntent)
    f.listeners.destroy(); assert(f.root.NewGameIntent==nil and f.root.Host.status=="waiting")
    f.values.FLPR_FRESH=1; f.root.AcceptCharacter(2); assert(f.values.flpr_state==nil)
end
local function bootstrap_to_complete(f)
    f.root.NewGame(); f.values.FLPR_FRESH=1; assert(f.root.AcceptCharacter(2)==2)
    local clock=EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime
    for i=1,25 do clock.m_gameTime=clock.m_gameTime+1; f.root.Poll() end
    assert(f.root.Host.status=="complete",tostring(f.root.Host.error))
    assert(f.values.flpr_done==1)
    return f.shown
end
function cases.bootstrap_confirms_randomisation_once_when_the_world_appears(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    local shown=bootstrap_to_complete(f)
    assert(#shown==1 and shown[1]==FLPRUI_Text("ready"),tostring(shown[1]))
    -- Reloading the same campaign restores it silently.
    f.listeners.destroy()
    local clock=EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime
    for i=1,3 do clock.m_gameTime=clock.m_gameTime+1; f.root.Poll() end
    assert(f.root.Host.status=="complete" and #shown==1)
end
function cases.bootstrap_messages_use_the_installed_game_text(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    FLPR_TEXT={ready=4711,other_campaign=4712,start_plain=4713}
    Infinity_FetchString=function(ref) return ({[4711]="Gotowe.",[4712]="Tylko BG1.",[4713]="Bez"})[ref] end
    bootstrap_to_complete(f)
    assert(#f.shown==1 and f.shown[1]=="Gotowe.")
    f.campaign={name="BP1"}; f.root.NewGame()
    local shown=table.remove(f.popups)
    assert(shown.text=="Tylko BG1." and shown.ok=="Bez")
    -- A key without a game-text entry keeps the English text.
    assert(FLPRUI_Text("setup_failed"):find("could not randomise",1,true))
    Infinity_FetchString=nil; FLPR_TEXT=nil
end
function cases.bootstrap_stopped_campaign_tells_the_player_once(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    f.root.NewGame(); f.values.FLPR_FRESH=1; assert(f.root.AcceptCharacter(2)==2)
    local clock=EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime
    clock.m_gameTime=clock.m_gameTime+1; f.root.Poll()
    clock.m_gameTime=clock.m_gameTime+1; f.root.Poll()
    assert(f.root.Host.status=="running" and #f.shown==1 and f.shown[1]==FLPRUI_Text("ready"))
    f.fail_observe=true
    for i=1,10 do clock.m_gameTime=clock.m_gameTime+1; f.root.Poll() end
    assert(f.root.Host.status=="disabled" and f.error_file:find("campaign: NATIVE_OBSERVATION_FAILED",1,true),
        tostring(f.error_file))
    assert(#f.shown==2 and f.shown[2]==FLPRUI_Text("stopped"),tostring(f.shown[2]))
    -- A line in the message log is easily missed: the info popup shows it too, once.
    assert(#f.infos==1 and f.infos[1]==FLPRUI_Text("stopped"),tostring(f.infos[1]))
    for i=1,3 do clock.m_gameTime=clock.m_gameTime+1; f.root.Poll() end
    assert(#f.shown==2 and #f.infos==1)
end
function cases.bootstrap_runtime_that_cannot_start_warns_each_randomised_game_once(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,allow_disabled=true,install_fail=true})
    f.root.Poll(); f.root.Poll(); assert(#f.shown==0 and #f.infos==0)
    -- A randomised game loads: the message log at once, the popup once the world screen is up.
    f.values.flpr_state=2; f.world=false
    f.root.Poll(); f.root.Poll()
    assert(#f.shown==1 and f.shown[1]==FLPRUI_Text("stopped") and #f.infos==0)
    f.world=true
    f.root.Poll(); f.root.Poll()
    assert(#f.shown==1 and #f.infos==1 and f.infos[1]==FLPRUI_Text("stopped"))
    f.listeners.destroy(); f.root.Poll(); assert(#f.shown==2 and #f.infos==2)
end
function cases.bootstrap_stopped_message_stays_in_the_log_without_the_engine_popup(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text,{gate=true,allow_disabled=true,install_fail=true})
    popupInfo=nil; f.values.flpr_state=2
    f.root.Poll(); f.root.Poll()
    assert(#f.shown==1 and f.shown[1]==FLPRUI_Text("stopped") and #f.infos==0)
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
        lua.execute(UI)
        lua.execute(CASES)[name](lua.execute(CORE), lua.execute(HOST), lua.execute(GLOBALS), BOOTSTRAP)
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "NativeHostTests"] = type(label + "NativeHostTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}})


if __name__ == "__main__":
    unittest.main()
