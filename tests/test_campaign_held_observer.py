"""Controlled fixture receipt integration, without native execution."""
from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
CASES = r'''
local cases = {}
local function setup(Core, Drop, Held)
    local f = {values={},verified=0,stopped=0}
    local p={resref="flpri1",charges={0,0,0},expiration=0,flags=1}
    local m={schema=Core.SCHEMA,state="prototype-not-playable",revision=Held.REVISION,
        bound_revision=string.rep("b",64),world_fingerprint=string.rep("c",64),
        profile_id="fixture",sources={},targets={{id="target",capacity=3,
            resource_sha256=string.rep("d",64),record={id="target",kind="actor"}}}}
    for i=1,3 do
        local ref="flpri"..i
        m.sources[i]={id=ref,quantity=1,resource_sha256=string.rep("e",64),
            record={id=ref,record={resref=ref,charges={0,0,0},expiration=0,flags=1}}}
    end
    local s={get=function(_,k) return f.values[k] or 0 end,set=function(_,k,v) f.values[k]=v end}
    local c=assert(Core.new(s,function() return m end)); assert(c:capture(m,17,"confirmed-new-game"))
    for i=1,3 do
        local op=assert(c:begin_source(i,{identity=string.rep("1",64),source_id=m.sources[i].id,
            exact_matches=1,quantity=1,payload=m.sources[i].record.record}))
        c:on_observation_boundary(); op.removed,op.remaining=1,0; assert(c:observe(op))
        op=assert(c:begin_delivery(i,{identity=string.rep("2",64),target_id="target",accepts=1}))
        c:on_observation_boundary(); op.created,op.nonce_bound_instance,op.lootable,op.held=1,1,0,1
        assert(c:observe(op))
    end
    local native={}
    function native:held_binding(request)
        return {owner_pointer=tostring(100+request.index),original_item_pointer=tostring(200+request.index),
            slot=18,payload=request.payload}
    end
    function native:verify_held_drop(request,candidate)
        assert(request.session==c.session and candidate.request_token==request)
        f.verified=f.verified+1
        if f.reject then return nil,"DROP_GROUND_INSTANCE_UNPROVEN" end
        return true
    end
    f.trace={events={},armed=false}
    function f.trace.arm() assert(not f.trace.armed); f.trace.armed=true end
    function f.trace.stop() f.trace.armed=false; f.stopped=f.stopped+1 end
    function f.trace.inspect() return {schema="flpr-drop-trace-v1",installed=true,
        events=f.trace.events,armed=f.trace.armed,
        stable=not f.trace.armed and not f.unstable,overflow=f.overflow==true} end
    function f.drop()
        for i=1,4 do
            local common={resref=i<4 and "flpri"..i or "flprx1",charge1=0,charge2=0,charge3=0,expiration=0,flags=1}
            local function row(kind,a,b,d)
                local r={kind=kind,a=tostring(a),b=tostring(b),c="500",d=d or 0,sequence=#f.trace.events+1}
                for k,v in pairs(common) do r[k]=v end
                f.trace.events[#f.trace.events+1]=r
            end
            row(1,100+i,200+i,18); row(2,200+i,300+i); row(3,300+i,400+i)
        end
    end
    f.host={status="complete",controller=c,native=native,deps={core=Core,storage=s}}
    f.c,f.s,f.m=c,s,m
    f.observer=Held.new(f.host,Drop,f.trace)
    return f
end
function cases.confirm_only_after_complete_trace_and_two_ground_observations(Core,Drop,Held)
    local f=setup(Core,Drop,Held); local o=f.observer
    o:poll(10); f.drop(); o:poll(11); assert(o.status=="stopping")
    o:poll(12); assert(o.status=="ground-observed" and f.verified==3)
    for _,a in ipairs(f.c:assignments()) do assert(a.delivery_phase==4) end
    o:poll(12); assert(f.verified==3)
    o:poll(13); assert(o.status=="complete" and f.verified==6,o.error)
    for _,a in ipairs(f.c:assignments()) do assert(a.delivery_phase==2) end
    local restored=assert(Core.new(f.s,function() return f.m end)); assert(restored:restore())
    for _,a in ipairs(restored:assignments()) do assert(a.delivery_phase==2) end
    assert(f.values.flpr_jkind==0 and f.stopped==1)
end
function cases.changed_ground_instance_stops_without_confirming(Core,Drop,Held)
    local f=setup(Core,Drop,Held); local o=f.observer
    o:poll(10); f.drop(); o:poll(11); o:poll(12); f.reject=true; o:poll(13)
    assert(o.status=="disabled" and o.error=="DROP_GROUND_INSTANCE_UNPROVEN")
    for _,a in ipairs(f.c:assignments()) do assert(a.delivery_phase==4) end
end
function cases.missing_drop_times_out_without_reaward(Core,Drop,Held)
    local f=setup(Core,Drop,Held); local o=f.observer
    o:poll(10); o:poll(11); o:poll(311)
    assert(o.status=="disabled" and o.error:find("HELD_DROP_TIMEOUT",1,true) and not f.trace.armed)
    for _,a in ipairs(f.c:assignments()) do assert(a.delivery_phase==4) end
end
function cases.overflow_or_unstable_snapshot_never_confirms(Core,Drop,Held)
    local f=setup(Core,Drop,Held); local o=f.observer
    o:poll(10); f.drop(); f.overflow=true; o:poll(11)
    assert(o.status=="disabled" and o.error:find("DROP_TRACE_OVERFLOW",1,true) and f.verified==0)
    f=setup(Core,Drop,Held); o=f.observer; o:poll(10); f.drop(); o:poll(11)
    f.unstable=true; o:poll(12); assert(o.status=="stopping" and f.verified==0)
    f.unstable=false; o:poll(13); o:poll(14); assert(o.status=="complete",o.error)
end
function cases.destroy_or_session_change_invalidates_observer(Core,Drop,Held)
    local f=setup(Core,Drop,Held); f.observer:destroy(); f.drop(); f.observer:poll(99)
    assert(f.observer.status=="destroyed" and f.verified==0)
    f=setup(Core,Drop,Held); f.observer:poll(10); f.drop(); f.host.controller={}; f.observer:poll(11)
    assert(f.observer.status=="disabled" and f.observer.error:find("HELD_SESSION_CHANGED",1,true) and f.verified==0)
end
function cases.malformed_stopped_metadata_cannot_be_laundered_into_valid_evidence(Core,Drop,Held)
    for _, field in ipairs({"schema","installed","armed","stable","overflow"}) do
        local f=setup(Core,Drop,Held); local o=f.observer
        o:poll(10); f.drop(); o:poll(11)
        local inspect=f.trace.inspect
        f.trace.inspect=function() local s=inspect(); s[field]=nil; return s end
        o:poll(12)
        assert(o.status=="disabled" and f.verified==0,field)
        for _,a in ipairs(f.c:assignments()) do assert(a.delivery_phase==4) end
    end
end
return cases
'''


class HeldObserverTests(unittest.TestCase):
    def test_controlled_receipts_in_three_runtimes(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            for name in Lua51().execute(CASES).keys():
                with self.subTest(runtime=runtime.__module__, case=name):
                    lua = runtime(unpack_returned_tuples=True)
                    modules = [lua.execute((ROOT / 'prototype' / file).read_text())
                               for file in ('FLPRCore.lua', 'FLPRDrop.lua', 'FLPRHeld.lua')]
                    lua.execute(CASES)[name](*modules)


if __name__ == '__main__':
    unittest.main()
