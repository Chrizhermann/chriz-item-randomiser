"""Hermetic bounded native adapter checks; no game process or game files touched."""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (ROOT / "prototype" / "FLPRNat.lua").read_text(encoding="utf-8")
CASES = r'''
local cases = {}
local function setup(Native)
    local function str(value) return {get=function() return value end} end
    local function item(ref, address, flags)
        return {cResRef=str(ref), m_useCount1=0, m_useCount2=0, m_useCount3=0,
            m_wear=0, m_flags=flags or 0, address=address}
    end
    local slots = {[21]=item("FLPRI1",301), [22]=item("OTHER",302)}
    local source = {address=101, sprite=true, alive=true, m_scriptName=str("FLPRS1"),
        m_equipment={m_items={get=function(_,slot) return slots[slot] end}}}
    local target = {address=102, sprite=false, m_scriptName=str("FLPRT1"),
        m_containerType=4, m_pos={x=100,y=200}, m_lstItems={}}
    local objects = {[1]=source,[2]=target}
    local area = {m_resref=str("BG2600"), m_lVertSort={1,2}, m_lVertSortBack={},
        m_lVertSortFlight={}, m_lVertSortUnder={}}
    local header={maxStackable=1,itemFlags=12}
    local api, calls, queue = {}, {}, {}
    api.EngineGlobals={g_pBaldurChitin={m_cMessageHandler={m_messageList=queue}}}
    api.EEex_CastUD=function(object,kind) assert(kind=="CMessageContainerAddItem"); return object end
    api.EEex_Area_GetVisible=function() return area end
    api.EEex_GameObject_Get=function(id) return objects[id] end
    api.EEex_GameObject_CastUserType=function(o) return o end
    api.EEex_GameObject_IsSprite=function(o,dead) return o.sprite and (dead or o.alive) or false end
    api.EEex_Utility_IterateCPtrList=function(list,fn) for _,entry in ipairs(list) do fn(entry) end end
    api.EEex_UDToPtr=function(o) return o.address end
    api.EEex_Resource_Demand=function(ref,ext) assert(ext=="ITM"); return header end
    api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,o)
        calls[#calls+1]=action
        if action:match("^DestroyItem") then slots[21]=nil
        else o.m_lstItems[1]=item("FLPRI1",401) end
    end
    local source_id,target_id="cre:flprs1/item/0","are:bg2600/container/5"
    local fixture={sources={[source_id]={area="BG2600",script_name="FLPRS1",identity=string.rep("a",64),
        payload={resref="flpri1",charges={0,0,0},expiration=0,flags=0}}},
        targets={[target_id]={area="BG2600",script_name="FLPRT1",identity=string.rep("b",64),x=100,y=200}}}
    local adapter=Native.new(api,fixture)
    local session={}
    local function command(kind,nonce)
        return {mode="execute-once",kind=kind,session=session,nonce=nonce or 2,index=1,
            revision=string.rep("c",64), identity=kind=="suppress" and string.rep("a",64) or string.rep("b",64),
            source_id=source_id,target_id=target_id,quantity=1,payload=fixture.sources[source_id].payload}
    end
    local function message(address,target_id,ref,flags,slot,subtype)
        return {address=address,m_targetId=target_id or 2,m_sourceId=target_id or 2,
            m_item=item(ref or "FLPRI1",address+16,flags),m_slotNum=slot or 32767,m_bCompressContainer=1,
            virtual_GetMsgType=function() return 67 end,virtual_GetMsgSubType=function() return subtype or 12 end,
            virtual_GetCommType=function() return 1 end}
    end
    return {adapter=adapter,api=api,source=source,target=target,area=area,slots=slots,objects=objects,
        header=header,calls=calls,queue=queue,message=message,fixture=fixture,item=item,command=command,source_id=source_id,target_id=target_id}
end
local function fail(value,reason,want) assert(value==nil and reason==want,tostring(reason).." ~= "..want) end
local function guarded(f,N)
    local old_id=f.source_id
    local source=f.fixture.sources[old_id]
    f.fixture.sources[old_id]=nil
    local id="are:flpru1/container/1"
    source.kind="guarded-container"; source.area="FLPRU1"; source.script_name="FLPRU1A"
    source.x=1140; source.y=650; source.payload.flags=1
    f.fixture.sources[id]=source
    f.fixture.source_gate={schema="flpr-unvisited-fixture-gate-v1",source_id=id}
    f.adapter=N.new(f.api,f.fixture)
    f.guarded_id=id
    local session={}
    f.command=function(kind,nonce)
        return {mode="execute-once",kind=kind,session=session,nonce=nonce or 2,index=1,
            revision=string.rep("c",64),identity=kind=="suppress" and source.identity or string.rep("b",64),
            source_id=id,target_id=f.target_id,quantity=1,payload=source.payload}
    end
    return f
end
local function invalid(N,f,want)
    local ok,reason=pcall(function() N.new(f.api,f.fixture) end)
    assert(not ok and tostring(reason):find(want,1,true),tostring(reason))
end
local function guarded_world(N)
    local f=guarded(setup(N),N)
    f.area.address=901
    f.area.m_resref={get=function() return "FLPRU1" end}
    local container={address=701,sprite=false,m_scriptName={get=function() return "FLPRU1A" end},
        m_containerType=4,m_pos={x=1140,y=650},
        m_lstItems={f.item("FLPRI1",801,1),f.item("FLPRX1",802,7)}}
    f.objects[3]=container
    f.area.m_lVertSort={3}
    f.container=container
    f.api.EEex_Resource_Demand=function() error("READ_ONLY_RESOURCE_DEMAND") end
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function() error("READ_ONLY_MUTATION") end
    return f
end
local function two_guarded_world(N)
    local f=guarded_world(N)
    local second_id="are:flpru1/container/2"
    f.fixture.sources[second_id]={kind="guarded-container",area="FLPRU1",script_name="FLPRU1B",
        x=1200,y=700,identity=string.rep("d",64),
        payload={resref="flpri2",charges={0,0,0},expiration=0,flags=1}}
    f.fixture.source_gate={schema="flpr-unvisited-fixture-gate-v1",
        source_ids={f.guarded_id,second_id}}
    local second={address=702,sprite=false,m_scriptName={get=function() return "FLPRU1B" end},
        m_containerType=4,m_pos={x=1200,y=700},
        m_lstItems={f.item("FLPRI2",811,1),f.item("FLPRX2",812,7)}}
    f.objects[4]=second
    f.area.m_lVertSort={3,4}
    f.adapter=N.new(f.api,f.fixture)
    f.second_id, f.second=second_id, second
    return f
end

function cases.two_guarded_sources_observe_independent_selected_and_excluded_originals(N)
    local f=two_guarded_world(N)
    local first_selected,first_excluded=f.container.m_lstItems[1],f.container.m_lstItems[2]
    local second_selected,second_excluded=f.second.m_lstItems[1],f.second.m_lstItems[2]
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==1)
    assert(f.adapter:guarded_source_remaining(f.second_id,"901","702").remaining==1)
    table.remove(f.second.m_lstItems,1)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==1)
    assert(f.adapter:guarded_source_remaining(f.second_id,"901","702").remaining==0)
    assert(f.container.m_lstItems[1]==first_selected and f.container.m_lstItems[2]==first_excluded)
    assert(f.second.m_lstItems[1]==second_excluded and second_selected.address==811)
    table.remove(f.container.m_lstItems,1)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==0)
    assert(f.adapter:guarded_source_remaining(f.second_id,"901","702").remaining==0)
    assert(f.container.m_lstItems[1]==first_excluded and f.second.m_lstItems[1]==second_excluded)
    assert(#f.calls==0)
end

function cases.guard_gate_list_must_name_every_guarded_source_once(N)
    for _,variant in ipairs({"missing","unknown","ordinary","repeated","sparse","nonarray","mixed","singular"}) do
        local f=two_guarded_world(N)
        local first,second=f.guarded_id,f.second_id
        if variant=="missing" then f.fixture.source_gate.source_ids={first}
        elseif variant=="unknown" then f.fixture.source_gate.source_ids={first,"unknown"}
        elseif variant=="ordinary" then
            local id="cre:other/item/0"
            f.fixture.sources[id]={area="FLPRU1",script_name="OTHER",identity=string.rep("e",64),
                payload={resref="flpri3",charges={0,0,0},expiration=0,flags=0}}
            f.fixture.source_gate.source_ids={first,id}
        elseif variant=="repeated" then f.fixture.source_gate.source_ids={first,first}
        elseif variant=="sparse" then f.fixture.source_gate.source_ids={[1]=first,[3]=second}
        elseif variant=="nonarray" then f.fixture.source_gate.source_ids={one=first,two=second}
        elseif variant=="mixed" then f.fixture.source_gate.source_id=first
        else f.fixture.source_gate={schema="flpr-unvisited-fixture-gate-v1",source_id=first} end
        invalid(N,f,"NATIVE_SOURCE_GATE_INVALID")
    end
    local f=two_guarded_world(N)
    f.fixture.source_gate.source_ids={f.second_id,f.guarded_id}
    assert(N.new(f.api,f.fixture):guarded_source_remaining(f.guarded_id,"901","701").remaining==1)
    f.fixture.source_gate.source_ids={}
    invalid(N,f,"NATIVE_SOURCE_GATE_INVALID")
end

function cases.guarded_remaining_counts_exact_visible_container_without_mutation(N)
    local f=guarded_world(N)
    local other=f.container.m_lstItems[2]
    other.m_useCount1=3; other.m_useCount2=4; other.m_useCount3=5; other.m_wear=55
    local result=assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701"))
    assert(result.remaining==1 and result.area_pointer=="901" and result.owner_pointer=="701")
    assert(other.address==802 and other.cResRef:get()=="FLPRX1" and other.m_flags==7 and
        other.m_useCount1==3 and other.m_useCount2==4 and other.m_useCount3==5 and other.m_wear==55)
    assert(f.container.m_lstItems[1].address==801 and #f.calls==0)
    table.remove(f.container.m_lstItems,1)
    result=assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701"))
    assert(result.remaining==0 and other.address==802 and other.m_flags==7)
    table.insert(f.container.m_lstItems,1,f.item("FLPRI1",803,0))
    result=assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701"))
    assert(result.remaining==1)
end
function cases.guarded_remaining_rejects_unavailable_or_changed_area_before_owner_access(N)
    local f=guarded_world(N)
    local visible=f.api.EEex_Area_GetVisible
    f.api.EEex_Area_GetVisible=function() return nil end
    local x,e=f.adapter:guarded_source_remaining(f.guarded_id,"901","701"); fail(x,e,"AREA_UNAVAILABLE")
    f.api.EEex_Area_GetVisible=visible
    f.area.m_resref={get=function() return "BG2600" end}
    x,e=f.adapter:guarded_source_remaining(f.guarded_id,"901","701"); fail(x,e,"AREA_UNAVAILABLE")
    f.area.m_resref={get=function() return "FLPRU1" end}
    f.api.EEex_GameObject_Get=function() error("OWNER_ACCESSED_BEFORE_AREA_CHECK") end
    x,e=f.adapter:guarded_source_remaining(f.guarded_id,"902","701"); fail(x,e,"AREA_CHANGED")
    assert(f.container.m_lstItems[1].address==801 and #f.calls==0)
end
function cases.guarded_remaining_requires_unique_matching_owner_and_geometry(N)
    for _,variant in ipairs({"missing","duplicate","pointer","x","y","type"}) do
        local f=guarded_world(N)
        if variant=="missing" then f.area.m_lVertSort={}
        elseif variant=="duplicate" then
            f.objects[4]={address=702,sprite=false,m_scriptName=f.container.m_scriptName,
                m_containerType=4,m_pos={x=1140,y=650},m_lstItems={}}
            f.area.m_lVertSort={3,4}
        elseif variant=="x" then f.container.m_pos.x=1141
        elseif variant=="y" then f.container.m_pos.y=651
        elseif variant=="type" then f.container.m_containerType=1 end
        local pointer=variant=="pointer" and "702" or "701"
        local want=(variant=="missing" and "OWNER_MISSING") or
            (variant=="duplicate" and "OWNER_AMBIGUOUS") or
            (variant=="pointer" and "OWNER_CHANGED") or "TARGET_NOT_FIXTURE_GROUND_PILE"
        local x,e=f.adapter:guarded_source_remaining(f.guarded_id,"901",pointer); fail(x,e,want)
        assert(f.container.m_lstItems[1].address==801 and #f.calls==0)
    end
end
function cases.guarded_remaining_rejects_unknown_wrong_kind_or_pointer(N)
    local f=guarded_world(N)
    local x,e=f.adapter:guarded_source_remaining("unknown","901","701"); fail(x,e,"SOURCE_UNKNOWN")
    x,e=f.adapter:guarded_source_remaining(f.guarded_id,901,"701"); fail(x,e,"SOURCE_POINTER_INVALID")
    x,e=f.adapter:guarded_source_remaining(f.guarded_id,"901","0"); fail(x,e,"SOURCE_POINTER_INVALID")
    local ordinary=setup(N)
    ordinary.api.EEex_Area_GetVisible=function() error("WRONG_KIND_AREA_ACCESS") end
    x,e=ordinary.adapter:guarded_source_remaining(ordinary.source_id,"901","701")
    fail(x,e,"SOURCE_NOT_GUARDED")
end

function cases.guarded_source_never_resolves_or_destroys_original(N)
    local f=guarded(setup(N),N)
    f.api.EEex_Area_GetVisible=function() error("SOURCE_AREA_ACCESSED") end
    f.api.EEex_Resource_Demand=function() error("SOURCE_RESOURCE_ACCESSED") end
    local x,e=f.adapter:inspect_source(f.guarded_id); fail(x,e,"SOURCE_GUARD_REQUIRED")
    x,e=f.adapter:execute(f.command("suppress")); fail(x,e,"SOURCE_GUARD_REQUIRED")
    assert(f.slots[21].address==301 and #f.calls==0 and f.adapter.pending==nil)
    assert(f.adapter.attempted[f.command("suppress").session]==nil)
end
function cases.guarded_source_declared_endpoint_is_exact(N)
    for _,variant in ipairs({"x","y","type","expiration","charge","flags","undeclared"}) do
        local f=guarded(setup(N),N)
        local endpoint=f.fixture.sources[f.guarded_id]
        if variant=="x" then endpoint.x=-1
        elseif variant=="y" then endpoint.y=32768
        elseif variant=="type" then endpoint.container_type=-1
        elseif variant=="expiration" then endpoint.payload.expiration=1
        elseif variant=="charge" then endpoint.payload.charges[2]=65536
        elseif variant=="flags" then endpoint.payload.flags=8
        else
            f.fixture.sources.other={kind="guarded-container",area="FLPRU1",script_name="FLPRU1A",
                x=1140,y=650,identity=string.rep("d",64),
                payload={resref="flpri2",charges={0,0,0},expiration=0,flags=1}}
        end
        invalid(N,f,(variant=="expiration" or variant=="charge" or variant=="flags") and
            "NATIVE_PAYLOAD_UNSUPPORTED" or
            (variant=="undeclared" and "NATIVE_SOURCE_GATE_INVALID" or "NATIVE_GUARDED_SOURCE_INVALID"))
    end
end
function cases.configured_container_uses_declared_item_geometry_and_type(N)
    local f=guarded_world(N); local e=f.fixture.sources[f.guarded_id]
    e.area="ARTEST"; e.script_name="Other Chest"; e.x=301; e.y=502; e.container_type=1
    e.payload.resref="sw1h01"; e.payload.charges={7,8,9}; e.payload.flags=0
    f.area.m_resref={get=function() return "ARTEST" end}
    f.container.m_scriptName={get=function() return "Other Chest" end}
    f.container.m_pos={x=301,y=502}; f.container.m_containerType=1
    f.container.m_lstItems[1]=f.item("SW1H01",801,0)
    f.adapter=N.new(f.api,f.fixture)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==1)
    table.remove(f.container.m_lstItems,1)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==0)
    assert(f.container.m_lstItems[1].cResRef:get()=="FLPRX1" and #f.calls==0)
end
function cases.guarded_source_gate_and_kind_are_required(N)
    for _,variant in ipairs({"missing","schema","source_id","ungated","unknown"}) do
        local f=guarded(setup(N),N)
        if variant=="missing" then f.fixture.source_gate=nil
        elseif variant=="schema" then f.fixture.source_gate.schema="other"
        elseif variant=="source_id" then f.fixture.source_gate.source_id="wrong"
        elseif variant=="ungated" then f.fixture.sources[f.guarded_id].kind=nil
        else f.fixture.sources[f.guarded_id].kind="unexpected" end
        invalid(N,f,variant=="unknown" and "NATIVE_SOURCE_KIND_UNSUPPORTED" or "NATIVE_SOURCE_GATE_INVALID")
    end
    local f=setup(N); f.fixture.sources[f.source_id].kind="unexpected"
    invalid(N,f,"NATIVE_SOURCE_KIND_UNSUPPORTED")
end
function cases.guarded_declared_payload_delivers_without_original_access(N)
    local f=guarded(setup(N),N)
    local area_reads,resources=0,0
    f.api.EEex_Area_GetVisible=function() area_reads=area_reads+1; return f.area end
    f.api.EEex_Resource_Demand=function(ref,ext)
        resources=resources+1; assert(ref=="flpri1" and ext=="ITM"); return f.header
    end
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,owner)
        f.calls[#f.calls+1]=action; assert(owner==f.target)
        owner.m_lstItems[1]=f.item("FLPRI1",401,1)
    end
    local a=f.adapter; assert(a:inspect_target(f.target_id)); assert(a:on_observation_boundary(1))
    local command=f.command("deliver"); assert(a:execute(command)); assert(a:on_observation_boundary(2))
    local receipt=assert(a:observe(command))
    assert(receipt.created==1 and receipt.payload.flags==1 and receipt.payload.resref=="flpri1")
    assert(#f.calls==1 and f.calls[1]=='CreateItem("flpri1",0,0,0)')
    assert(area_reads>0 and resources>0 and f.slots[21].address==301)
end
function cases.guarded_resref_stays_exclusive_in_delivery_queue(N)
    local f=guarded(setup(N),N)
    assert(f.adapter.source_resrefs.flpri1==true)
    assert(f.adapter:inspect_target(f.target_id)); assert(f.adapter:on_observation_boundary(1))
    f.queue[1]=f.message(601,99,"FLPRI1",1)
    local x,e=f.adapter:execute(f.command("deliver")); fail(x,e,"DELIVERY_QUEUE_NOT_EXCLUSIVE")
    assert(#f.calls==0 and f.slots[21].address==301)
end

function cases.removal_is_exact_and_waits_for_game_time(N)
    local f=setup(N); local a=f.adapter
    assert(a:inspect_source(f.source_id).quantity==1)
    assert(a:on_observation_boundary(100)); local command=f.command("suppress")
    assert(a:execute(command)); assert(f.slots[21]==nil and f.slots[22].address==302)
    assert(#f.calls==1 and f.calls[1]=='DestroyItem("flpri1")')
    local x,e=a:observe(command); fail(x,e,"LATER_GAME_TICK_REQUIRED")
    x,e=a:on_observation_boundary(100); fail(x,e,"LATER_GAME_TICK_REQUIRED")
    assert(a:on_observation_boundary(101))
    local receipt=assert(a:observe(command)); assert(receipt.removed==1 and receipt.remaining==0)
    assert(receipt.session==command.session and receipt.payload.flags==0)
end
function cases.same_resref_different_payload_is_ambiguous(N)
    local f=setup(N); f.slots[23]=f.item("FLPRI1",303,4)
    local x,e=f.adapter:inspect_source(f.source_id); fail(x,e,"SOURCE_ITEM_AMBIGUOUS"); assert(#f.calls==0)
end
function cases.full_payload_checks_flags_wear_and_charges(N)
    for _,key in ipairs({"m_flags","m_wear","m_useCount1","m_useCount2","m_useCount3"}) do
        local f=setup(N); f.slots[21][key]=1
        local x,e=f.adapter:inspect_source(f.source_id); fail(x,e,"SOURCE_PAYLOAD_MISMATCH")
    end
end
function cases.identified_payload_is_preserved_in_witness_and_source_receipt(N)
    local f=setup(N)
    f.fixture.sources[f.source_id].payload.flags=1
    f.slots[21].m_flags=1
    local a=N.new(f.api,f.fixture)
    local witness=assert(a:inspect_source(f.source_id)); assert(witness.payload.flags==1)
    a:on_observation_boundary(1); local command=f.command("suppress"); assert(a:execute(command))
    a:on_observation_boundary(2); local receipt=assert(a:observe(command))
    assert(receipt.payload.flags==1 and receipt.removed==1)
end
function cases.identified_delivery_preserves_exact_flags(N)
    local f=setup(N); f.fixture.sources[f.source_id].payload.flags=1
    local a=N.new(f.api,f.fixture); assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,owner)
        f.calls[#f.calls+1]=action; owner.m_lstItems[1]=f.item("FLPRI1",401,1)
    end
    local command=f.command("deliver"); assert(a:execute(command)); a:on_observation_boundary(2)
    local receipt=assert(a:observe(command)); assert(receipt.payload.flags==1 and receipt.created==1)
end
function cases.identified_configuration_rejects_unidentified_source_or_delivery(N)
    local f=setup(N); f.fixture.sources[f.source_id].payload.flags=1
    local a=N.new(f.api,f.fixture)
    local x,e=a:inspect_source(f.source_id); fail(x,e,"SOURCE_PAYLOAD_MISMATCH")
    assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    x,e=a:execute(f.command("deliver")); fail(x,e,"CREATED_INSTANCE_UNPROVEN")
end
function cases.stolen_nonstealable_and_nondroppable_flags_are_unsupported(N)
    for _,flags in ipairs({2,4,8,9}) do
        local f=setup(N); f.fixture.sources[f.source_id].payload.flags=flags
        local ok,reason=pcall(function() N.new(f.api,f.fixture) end)
        assert(not ok and tostring(reason):find("NATIVE_PAYLOAD_UNSUPPORTED",1,true))
    end
end
function cases.same_named_second_owner_is_ambiguous(N)
    local f=setup(N); f.objects[3]=f.source; f.area.m_lVertSort[3]=3
    local x,e=f.adapter:inspect_source(f.source_id); fail(x,e,"OWNER_AMBIGUOUS")
end
function cases.duplicate_list_ids_do_not_duplicate_owners(N)
    local f=setup(N); f.area.m_lVertSortBack={1,2}
    assert(f.adapter:inspect_source(f.source_id)); assert(f.adapter:inspect_target(f.target_id))
end
function cases.owner_replacement_between_inspect_and_execute_rejected(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_source(f.source_id)); a:on_observation_boundary(1)
    f.source.address=999; local x,e=a:execute(f.command("suppress")); fail(x,e,"OWNER_CHANGED"); assert(#f.calls==0)
end
function cases.item_replacement_between_inspect_and_execute_rejected(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_source(f.source_id)); a:on_observation_boundary(1)
    f.slots[21]=f.item("FLPRI1",888); local x,e=a:execute(f.command("suppress")); fail(x,e,"INVENTORY_CHANGED")
end
function cases.delivers_exact_instance_to_empty_ground_pile(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command)); assert(f.calls[1]=='CreateItem("flpri1",0,0,0)')
    a:on_observation_boundary(2); local receipt=assert(a:observe(command))
    assert(receipt.created==1 and receipt.nonce_bound_instance==1 and receipt.lootable==1)
    local x,e=a:execute(command); fail(x,e,"ALREADY_ATTEMPTED"); assert(#f.calls==1)
end
local function stocked_container(f,N)
    f.fixture.targets[f.target_id].kind="container"
    f.fixture.targets[f.target_id].container_type=4
    local first,second=f.item("OLDONE",501,1),f.item("OLDTWO",502,7)
    second.m_useCount1=3
    f.target.m_lstItems={first,second}
    f.target.m_locked=1; f.target.m_trapActivated=1; f.target.m_trapDetectionDiff=67
    f.adapter=N.new(f.api,f.fixture)
    return first,second
end
function cases.stocked_locked_trapped_container_appends_synchronously(N)
    local f=setup(N); local first,second=stocked_container(f,N)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,owner)
        f.calls[#f.calls+1]=action
        owner.m_lstItems[3]=f.item("FLPRI1",701)
    end
    local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command))
    assert(#f.target.m_lstItems==3 and f.target.m_lstItems[1]==first and f.target.m_lstItems[2]==second)
    a:on_observation_boundary(2); local receipt=assert(a:observe(command))
    assert(receipt.created==1 and receipt.nonce_bound_instance==1 and receipt.lootable==1)
    assert(first.address==501 and first.m_flags==1 and second.address==502 and second.m_flags==7 and second.m_useCount1==3)
    assert(f.target.m_locked==1 and f.target.m_trapActivated==1 and f.target.m_trapDetectionDiff==67)
    assert(#f.calls==1 and f.calls[1]=='CreateItem("flpri1",0,0,0)')
end
function cases.stocked_container_queued_append_then_second_reward(N)
    local f=setup(N); local first,second=stocked_container(f,N)
    local serial=0
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action)
        serial=serial+1; f.calls[#f.calls+1]=action
        f.queue[#f.queue+1]=f.message(600+serial)
    end
    local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command))
    assert(#f.target.m_lstItems==2 and #f.queue==1)
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    f.queue[1]=nil; f.target.m_lstItems[3]=f.item("FLPRI1",701)
    a:on_observation_boundary(3); x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    a:on_observation_boundary(4); assert(a:observe(command))
    assert(a:inspect_target(f.target_id)); a:on_observation_boundary(5)
    local second_command=f.command("deliver",3); second_command.index=2
    assert(a:execute(second_command)); assert(#f.target.m_lstItems==3 and #f.queue==1)
    a:on_observation_boundary(6); x,e=a:observe(second_command); fail(x,e,"DELIVERY_SETTLING")
    f.queue[1]=nil; f.target.m_lstItems[4]=f.item("FLPRI1",702)
    a:on_observation_boundary(7); x,e=a:observe(second_command); fail(x,e,"DELIVERY_SETTLING")
    a:on_observation_boundary(8); local receipt=assert(a:observe(second_command))
    assert(receipt.created==1 and receipt.nonce_bound_instance==1 and receipt.lootable==1)
    assert(#f.target.m_lstItems==4 and f.target.m_lstItems[1]==first and f.target.m_lstItems[2]==second)
    assert(f.target.m_lstItems[3].address==701 and f.target.m_lstItems[4].address==702)
    assert(f.target.m_locked==1 and f.target.m_trapActivated==1 and #f.calls==2)
end
function cases.stocked_container_rejects_changed_old_item_during_queued_append(N)
    local f=setup(N); local first=stocked_container(f,N)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action)
        f.calls[#f.calls+1]=action; f.queue[1]=f.message(601)
    end
    local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command))
    f.queue[1]=nil; first.m_flags=5; f.target.m_lstItems[3]=f.item("FLPRI1",701)
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_MESSAGE_LOST")
    assert(#f.calls==1)
end
function cases.target_must_be_empty_and_exact_ground_pile(N)
    local f=setup(N); f.target.m_lstItems[1]=f.item("OTHER",501)
    local x,e=f.adapter:inspect_target(f.target_id); fail(x,e,"TARGET_NOT_EMPTY")
    f.target.m_lstItems={}; f.target.m_containerType=1
    x,e=f.adapter:inspect_target(f.target_id); fail(x,e,"TARGET_NOT_FIXTURE_GROUND_PILE")
    f.target.m_containerType=4; f.target.m_pos.x=200
    x,e=f.adapter:inspect_target(f.target_id); fail(x,e,"TARGET_NOT_FIXTURE_GROUND_PILE")
end
function cases.new_pointer_must_survive_later_observation(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command)); f.target.m_lstItems[1]=f.item("FLPRI1",402)
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"CREATED_INSTANCE_CHANGED")
end
function cases.late_create_is_not_assumed_nonce_bound(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function() end
    local command=f.command("deliver"); local x,e=a:execute(command); fail(x,e,"CREATED_INSTANCE_UNPROVEN")
    f.target.m_lstItems[1]=f.item("FLPRI1",401); a:on_observation_boundary(2)
    x,e=a:observe(command); fail(x,e,"CREATED_INSTANCE_UNPROVEN")
end
function cases.unrelated_inventory_change_after_removal_rejected(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_source(f.source_id)); a:on_observation_boundary(1)
    local command=f.command("suppress"); assert(a:execute(command)); f.slots[22].m_flags=1
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"REMOVAL_NOT_EXACT")
end
function cases.extra_removal_is_detected_immediately(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_source(f.source_id)); a:on_observation_boundary(1)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function() f.slots[21]=nil; f.slots[22]=nil end
    local x,e=a:execute(f.command("suppress")); fail(x,e,"REMOVAL_NOT_EXACT")
end
function cases.pending_reload_is_never_replayed(N)
    local f=setup(N); local a=f.adapter; local command=f.command("deliver"); command.mode="observe-only"
    local x,e=a:execute(command); fail(x,e,"COMMAND_INVALID")
    x,e=a:observe(command); fail(x,e,"PENDING_RELOAD_UNPROVEN"); assert(#f.calls==0)
end
function cases.stackable_or_invisible_or_nondroppable_resource_rejected(N)
    local f=setup(N); f.header.maxStackable=20
    local x,e=f.adapter:inspect_source(f.source_id); fail(x,e,"ITEM_NOT_NONSTACKABLE")
    f.header.maxStackable=1; f.header.itemFlags=4
    x,e=f.adapter:inspect_source(f.source_id); fail(x,e,"ITEM_NOT_VISIBLE_DROPPABLE")
    f.header.itemFlags=8
    x,e=f.adapter:inspect_source(f.source_id); fail(x,e,"ITEM_NOT_VISIBLE_DROPPABLE")
end
function cases.clock_missing_backwards_and_invalid_rejected(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_source(f.source_id))
    local x,e=a:execute(f.command("suppress")); fail(x,e,"OBSERVATION_CLOCK_MISSING")
    a:on_observation_boundary(10); x,e=a:on_observation_boundary(9); fail(x,e,"LATER_GAME_TICK_REQUIRED")
    x,e=a:on_observation_boundary("11"); fail(x,e,"OBSERVATION_CLOCK_INVALID")
end
function cases.diagnostics_are_read_only_and_expose_full_payload(N)
    local f=setup(N); local state=f.adapter:diagnostics()
    assert(state.area=="bg2600" and state.sources[f.source_id].items[1].payload.flags==0)
    assert(state.sources[f.source_id].items[1].slot==21 and #state.targets[f.target_id].items==0)
    assert(#f.calls==0)
end
function cases.throwing_action_remains_attempted_and_pending(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_source(f.source_id)); a:on_observation_boundary(1)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function() error("engine failure") end
    local command=f.command("suppress"); assert(not pcall(function() a:execute(command) end))
    local x,e=a:execute(command); fail(x,e,"MUTATION_PENDING")
    a:on_observation_boundary(2); x,e=a:observe(command); fail(x,e,"MUTATION_OUTCOME_UNPROVEN")
end
function cases.stale_session_receipt_rejected(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command)); a:on_observation_boundary(2)
    command.session={}; local x,e=a:observe(command); fail(x,e,"RECEIPT_MISMATCH")
end
function cases.destroy_forgets_transient_proof(N)
    local f=setup(N); local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver"); assert(a:execute(command)); a:destroy(); a:on_observation_boundary(2)
    local x,e=a:observe(command); fail(x,e,"NO_PENDING_MUTATION"); assert(#f.calls==1)
end
local function queued(f)
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action)
        f.calls[#f.calls+1]=action; f.queue[#f.queue+1]=f.message(601)
    end
    local a=f.adapter; assert(a:inspect_target(f.target_id)); assert(a:on_observation_boundary(1))
    local command=f.command("deliver"); assert(a:execute(command)); return a,command
end
function cases.queued_delivery_requires_message_consumption_and_two_later_ticks(N)
    local f=setup(N); local a,command=queued(f)
    assert(#f.target.m_lstItems==0 and a:diagnostics().pending.queued.pointer=="601")
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    f.queue[1]=nil; f.target.m_lstItems[1]=f.item("FLPRI1",701)
    a:on_observation_boundary(3); x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    assert(a:diagnostics().pending.created_pointer=="701")
    x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    a:on_observation_boundary(4); local receipt=assert(a:observe(command))
    assert(receipt.created==1 and receipt.nonce_bound_instance==1 and receipt.lootable==1 and #f.calls==1)
end
function cases.preexisting_destination_or_same_item_message_prevents_execution(N)
    for _,choice in ipairs({{2,"OTHER"},{99,"FLPRI1"}}) do
        local f=setup(N); local a=f.adapter; f.queue[1]=f.message(600,choice[1],choice[2])
        assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
        local x,e=a:execute(f.command("deliver")); fail(x,e,"DELIVERY_QUEUE_NOT_EXCLUSIVE")
        assert(#f.calls==0)
    end
end
function cases.unrelated_message_is_not_cast_or_mistaken_for_created_item(N)
    local f=setup(N); f.queue[1]=f.message(500,99,"OTHER",0,32767,40)
    local casts=0
    f.api.EEex_CastUD=function(message,kind)
        assert(message:virtual_GetMsgSubType()==12 and kind=="CMessageContainerAddItem")
        casts=casts+1; return message
    end
    local a,command=queued(f); assert(casts==1 and #f.queue==2)
    f.queue[2]=nil; f.target.m_lstItems[1]=f.item("FLPRI1",701)
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    a:on_observation_boundary(3); assert(a:observe(command))
end
function cases.foreign_destination_wrong_slot_or_payload_message_is_rejected(N)
    for _,variant in ipairs({"destination","source","slot","payload","extra"}) do
        local f=setup(N); local a=f.adapter
        f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function()
            local m=f.message(601)
            if variant=="destination" then m.m_targetId=99
            elseif variant=="source" then m.m_sourceId=99
            elseif variant=="slot" then m.m_slotNum=0
            elseif variant=="payload" then m.m_item.m_flags=1
            elseif variant=="extra" then f.queue[2]=f.message(602,99,"OTHER") end
            f.queue[1]=m
        end
        assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
        local x,e=a:execute(f.command("deliver")); fail(x,e,"CREATED_INSTANCE_UNPROVEN")
    end
end
function cases.queued_message_tampering_or_competing_message_stops_observation(N)
    for _,variant in ipairs({"changed","competitor"}) do
        local f=setup(N); local a,command=queued(f)
        if variant=="changed" then f.queue[1].m_item.m_flags=1
        else f.queue[2]=f.message(602,2,"OTHER") end
        a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_QUEUE_CONFLICT")
    end
end
function cases.queued_command_never_reexecutes_during_settling(N)
    local f=setup(N); local a,command=queued(f)
    for tick=2,5 do
        a:on_observation_boundary(tick); local x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
        x,e=a:execute(command); fail(x,e,"MUTATION_PENDING")
    end
    assert(#f.calls==1)
end
function cases.stuck_queued_message_times_out_and_never_recovers_or_replays(N)
    local f=setup(N); local a,command=queued(f)
    a:on_observation_boundary(N.MAX_SETTLE_TICKS)
    local x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    a:on_observation_boundary(N.MAX_SETTLE_TICKS+1)
    x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING_TIMEOUT")
    -- A late materialization cannot turn an expired observation into success.
    f.queue[1]=nil; f.target.m_lstItems[1]=f.item("FLPRI1",701)
    a:on_observation_boundary(N.MAX_SETTLE_TICKS+2)
    x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING_TIMEOUT")
    x,e=a:execute(command); fail(x,e,"MUTATION_PENDING")
    assert(#f.calls==1)
end
function cases.synchronous_creation_rejects_later_conflicting_queue_messages(N)
    for _,choice in ipairs({{2,"OTHER"},{99,"FLPRI1"}}) do
        local f=setup(N); local a=f.adapter; assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
        local command=f.command("deliver"); assert(a:execute(command))
        f.queue[1]=f.message(602,choice[1],choice[2]); a:on_observation_boundary(2)
        local x,e=a:observe(command); fail(x,e,"DELIVERY_QUEUE_CONFLICT")
        assert(#f.calls==1)
    end
end
function cases.consumed_message_without_item_is_not_a_success(N)
    local f=setup(N); local a,command=queued(f); f.queue[1]=nil
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_MESSAGE_LOST")
end
function cases.item_present_while_own_message_is_pending_is_ambiguous(N)
    local f=setup(N); local a,command=queued(f); f.target.m_lstItems[1]=f.item("FLPRI1",701)
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_EARLY_ITEM")
end
function cases.queued_item_pointer_must_survive_second_observation(N)
    local f=setup(N); local a,command=queued(f); f.queue[1]=nil; f.target.m_lstItems[1]=f.item("FLPRI1",701)
    a:on_observation_boundary(2); local x,e=a:observe(command); fail(x,e,"DELIVERY_SETTLING")
    f.target.m_lstItems[1]=f.item("FLPRI1",702); a:on_observation_boundary(3)
    x,e=a:observe(command); fail(x,e,"CREATED_INSTANCE_CHANGED")
end
function cases.same_resref_guarded_originals_in_distinct_owners_remain_independent(N)
    local f=two_guarded_world(N)
    local first,second=f.container.m_lstItems[1],f.second.m_lstItems[1]
    f.fixture.sources[f.second_id].payload.resref="flpri1"
    f.second.m_lstItems[1]=f.item("FLPRI1",second.address,1)
    f.adapter=N.new(f.api,f.fixture)
    assert(f.adapter.source_resrefs.flpri1==true)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==1)
    assert(f.adapter:guarded_source_remaining(f.second_id,"901","702").remaining==1)
    table.remove(f.second.m_lstItems,1)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==1)
    assert(f.adapter:guarded_source_remaining(f.second_id,"901","702").remaining==0)
    assert(f.container.m_lstItems[1]==first)
    table.remove(f.container.m_lstItems,1)
    assert(f.adapter:guarded_source_remaining(f.guarded_id,"901","701").remaining==0)
    assert(f.adapter:guarded_source_remaining(f.second_id,"901","702").remaining==0)
    assert(#f.calls==0)
end
function cases.configured_item_resref_removes_only_the_selected_copy(N)
    local f=setup(N); f.fixture.sources[f.source_id].payload.resref="sw1h01"
    f.slots[21]=f.item("SW1H01",301)
    local a=N.new(f.api,f.fixture); assert(a:inspect_source(f.source_id)); a:on_observation_boundary(1)
    local command=f.command("suppress"); assert(a:execute(command)); a:on_observation_boundary(2)
    assert(a:observe(command).removed==1 and f.slots[21]==nil and f.slots[22].address==302)
    assert(#f.calls==1 and f.calls[1]=='DestroyItem("sw1h01")')
end
local function store_target(N, delivery)
    local f=setup(N)
    f.fixture.targets[f.target_id]={kind="store",store="SHOP01",identity=string.rep("b",64)}
    local calls={inspect=0,deliver=0}
    f.api.FLPR={Shop={
        inspect_target=function(_,endpoint)
            calls.inspect=calls.inspect+1
            assert(endpoint.kind=="store" and endpoint.store=="shop01")
            return true
        end,
        deliver=function(_,endpoint,item,index)
            calls.deliver=calls.deliver+1
            assert(endpoint.store=="shop01" and item.resref=="flpri1" and index==1)
            return delivery()
        end,
    }}
    f.adapter=N.new(f.api,f.fixture)
    f.api.EEex_Area_GetVisible=function() error("STORE_ROUTE_RESOLVED_AREA") end
    f.api.EEex_GameObject_Get=function() error("STORE_ROUTE_RESOLVED_OWNER") end
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function() error("STORE_ROUTE_USED_ACTOR_ACTION") end
    return f,calls
end
function cases.store_target_uses_service_and_receipt_needs_later_tick(N)
    local f,calls=store_target(N,function() return true end)
    local a=f.adapter
    local witness=assert(a:inspect_target(f.target_id))
    assert(witness.identity==string.rep("b",64) and witness.target_id==f.target_id and witness.accepts==1)
    assert(a:on_observation_boundary(1))
    local command=f.command("deliver")
    assert(a:execute(command))
    assert(calls.inspect==1 and calls.deliver==1 and #f.calls==0)
    local x,e=a:observe(command); fail(x,e,"LATER_GAME_TICK_REQUIRED")
    x,e=a:execute(command); fail(x,e,"MUTATION_PENDING")
    assert(a:on_observation_boundary(2))
    local receipt=assert(a:observe(command))
    assert(receipt.session==command.session and receipt.kind=="deliver" and
        receipt.index==command.index and receipt.nonce==command.nonce and
        receipt.revision==command.revision and receipt.identity==command.identity and
        receipt.source_id==command.source_id and receipt.target_id==command.target_id and
        receipt.created==1 and receipt.nonce_bound_instance==1 and receipt.lootable==1)
    f.api.EEex_Area_GetVisible=function() return nil end
    local store=a:diagnostics().targets[f.target_id]
    assert(store.store=="shop01" and store.identity==string.rep("b",64) and store.unavailable==nil)
    x,e=a:execute(command); fail(x,e,"ALREADY_ATTEMPTED")
    assert(calls.deliver==1)
end
function cases.store_target_failure_is_not_retried(N)
    local f,calls=store_target(N,function() return nil,"SHOP_WRITE_FAILED" end)
    local a=f.adapter
    assert(a:inspect_target(f.target_id)); a:on_observation_boundary(1)
    local command=f.command("deliver")
    local x,e=a:execute(command); fail(x,e,"SHOP_WRITE_FAILED")
    assert(calls.deliver==1 and a.pending and not a.pending.complete)
    x,e=a:execute(command); fail(x,e,"MUTATION_PENDING")
    a:on_observation_boundary(2)
    x,e=a:observe(command); fail(x,e,"SHOP_WRITE_FAILED")
    assert(calls.deliver==1)
end
function cases.store_target_requires_service_only_when_declared(N)
    local f=setup(N)
    assert(N.new(f.api,f.fixture))
    f.fixture.targets[f.target_id]={kind="store",store="SHOP01",identity=string.rep("b",64)}
    invalid(N,f,"NATIVE_SHOP_SERVICE_MISSING")
end
return cases
'''

CASE_NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](lua.execute(ADAPTER))
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "NativeAdapterTests"] = type(label + "NativeAdapterTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}})


if __name__ == "__main__":
    unittest.main()
