"""Gate.install inventory wiring and private rewrites with mocked EEex only."""

from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from tests.test_campaign_gate_bridge import CASES, CORE, GATE, POLICY, BUILD
from tests.test_campaign_inventory_assembly import BUFFER, EXPECTED, OUTPUT, run
from tests.test_postinstall_world_formats import item_record, make_cre, make_sto
from flir_config.creature_inventory import rewrite_inventory as rewrite_cre
from flir_config.store_inventory import rewrite_inventory as rewrite_sto


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = (ROOT / "tools/probes/FLPRInventoryAsm.lua").read_text(encoding="utf-8")
SETUP = r'''
return function(f, Gate, build, compile, Inventory)
    f.inventory_groups={}
    f.pristine={templates={},stores={}}
    local bundles={}
    for _,letter in ipairs({'a','b'}) do
        local revision=string.rep(letter,64)
        local manifest={revision=revision,sources={}}
        local fixture={revision=revision,sources={},resource_hashes={
            ['ACTOR.CRE']=string.rep('1',64),['SHOP01.STO']=string.rep('2',64)},
            source_gate={schema='flpr-unvisited-fixture-gate-v1',source_ids={}}}
        f.pristine.templates[revision]={}; f.pristine.stores[revision]={}
        for index=0,1 do
            for _,kind in ipairs({'cre','sto'}) do
                local resource=kind..':'..(kind=='cre' and 'actor' or 'shop01')
                local id=resource..'/item/'..index
                local payload={record_index=index,resref='item000'..index,
                    expiration=0,charges={0,0,0},flags=1}
                if kind=='sto' then payload.quantity=2; payload.infinite_supply=false end
                manifest.sources[#manifest.sources+1]={id=id,quantity=kind=='cre' and 1 or 2,
                    resource_sha256=fixture.resource_hashes[kind=='cre' and 'ACTOR.CRE' or 'SHOP01.STO'],
                    record={id=id,kind=kind=='cre' and 'cre-template' or 'store',
                        resource=resource,owner=resource,record=payload}}
                fixture.source_gate.source_ids[#fixture.source_gate.source_ids+1]=id
                fixture.sources[id]={kind=kind=='cre' and 'guarded-template' or 'guarded-store',
                    creature_resource=kind=='cre' and resource or nil,
                    store=kind=='sto' and 'shop01' or nil,
                    record_index=index,quantity=kind=='cre' and 1 or 2,
                    multiple_stock=kind=='sto' and true or nil,payload=payload}
                if kind=='cre' then f.pristine.templates[revision][id]=original_cre
                else f.pristine.stores[revision][id]={original=original_sto,
                    removed=index==0 and removed_sto_zero or removed_sto_one} end
            end
        end
        bundles[#bundles+1]={manifest=manifest,fixture=fixture}
    end
    for _,site in ipairs({
        {0x250765,'0f1047080f114308'},{0x405295,'c6435001498bcf'},
        {0x250967,'4c8b742448'},{0x341220,'48895c24185556574154415541564157'}}) do
        local bytes={}
        for hex in site[2]:gmatch('%x%x') do bytes[#bytes+1]=string.char(tonumber(hex,16)) end
        f:pokestring(0x140000000+site[1],table.concat(bytes))
    end
    f.gate=Gate.install(f.api,bundles,build,compile,f.pristine,nil,function(group)
        f.inventory_groups[#f.inventory_groups+1]=group
        return Inventory(group)
    end)
    return bundles
end
'''
STARTUP = r'''
return function(f,bundle)
    -- Mirror the accepted character handoff and its immutable captured globals.
    f.values.FLPR_FRESH=1
    local controller={manifest=bundle.manifest,session={}}
    function controller:source_is_kept(index) return index==3 or index==4 end
    for _,member in ipairs(f.gate.members) do
        member:_write(40,1); member:_write(72,1)
        if member.revision==bundle.manifest.revision then
            for _,field in ipairs(member.policy.fields) do
                f.values[field.name]=field.phase and 1 or field.value
            end
        end
    end
    assert(f.gate:prepare_new(bundle,controller))
    for _,member in ipairs(f.gate.members) do
        local selected=member.revision==bundle.manifest.revision and member.index<=2
        assert(member:_q(0)==1 and member:_q(32)==(selected and 1 or 0))
        local proof,reason=member:_proof()
        if selected then assert(proof and member.enrolled.session==controller.session)
        else assert(not proof and reason=='CAMPAIGN_GATE_INACTIVE') end
    end
end
'''


class InventoryGateTests(unittest.TestCase):
    def setup_gate(self, runtime):
        lua = runtime(unpack_returned_tuples=True)
        core, gate, build, compile_policy, inventory = (
            lua.execute(value) for value in (CORE, GATE, BUILD, POLICY, INVENTORY))
        factory = lua.execute(CASES.replace('return cases', 'return fixture'))
        f = factory(core)
        cre = bytes(make_cre([item_record(f'ITEM000{i}', expiration=0, charges=(0, 0, 0), flags=1)
                              for i in range(3)]))
        sto = bytes(make_sto([(item_record(f'ITEM000{i}', expiration=0, charges=(0, 0, 0), flags=1), 2, 0)
                              for i in range(3)]))
        for name, value in {'original_cre': cre, 'original_sto': sto,
                            'removed_sto_zero': rewrite_sto(sto, {0}),
                            'removed_sto_one': rewrite_sto(sto, {1})}.items():
            lua.globals()[name] = value
        bundles = lua.execute(SETUP)(f, gate, build, compile_policy, inventory)
        return lua, f, bundles, cre, sto

    def test_multiple_sources_share_one_inventory_hook_per_resource_and_revision(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                _, f, _, _, _ = self.setup_gate(runtime)
                self.assertEqual(len(f.gate.members), 8)
                self.assertEqual(len(f.inventory_groups), 4)
                self.assertEqual({f.hooks[i][1] - 0x140000000 for i in range(1, len(f.hooks)+1)},
                                 {0x292A84, 0x292F45, 0x250765, 0x405295, 0x341220})
                buffers = set()
                for group in f.inventory_groups.values():
                    self.assertEqual(len(group.members), 2)
                    self.assertEqual([group.edits[i].index for i in (1, 2)], [1, 0])
                    self.assertEqual(group.members[1].revision, group.members[2].revision)
                    self.assertTrue(all(member.permanent for member in group.members.values()))
                    hook_rva = 0x341220 if group.kind == 'cre' else 0x250765
                    hook = next(h for h in f.hooks.values() if h[1] == 0x140000000+hook_rva)
                    for edit in group.edits.values():
                        self.assertIn(f'mov r10, {int(edit.buffer)}', hook[5][1])
                        self.assertNotIn(edit.buffer, buffers)
                        buffers.add(edit.buffer)

    def test_template_startup_and_reset_drive_each_source_inventory_policy(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                lua, f, bundles, cre, sto = self.setup_gate(runtime)
                lua.execute(STARTUP)(f, bundles[1])
                for group in f.inventory_groups.values():
                    original = cre if group.kind == 'cre' else sto
                    selected = group.members[1].revision == bundles[1].manifest.revision
                    # Relocate the installed edit table into the existing emulator's map.
                    # Only addresses/context move; source indexes and policy flags stay exact.
                    normalized = {'kind': group.kind, 'ref': 'SHOP01', 'length': group.length,
                                  'expected': EXPECTED, 'output': OUTPUT,
                                  'item_offset': group.item_offset,
                                  'slot_offset': group.slot_offset, 'edits': []}
                    active = []
                    for i, edit in enumerate(group.edits.values()):
                        normalized['edits'].append({'index': edit.index, 'buffer': BUFFER+i*128,
                                                    'context': 40})
                        if f.peek(f, edit.buffer+32, 8) == 1:
                            active.append(i)
                    self.assertEqual([normalized['edits'][i]['index'] for i in active],
                                     [0] if selected else [])
                    pointer, rewritten = run(normalized, original, active=active)
                    expected = (rewrite_cre(original, {0: None}) if group.kind == 'cre'
                                else rewrite_sto(original, {0})) if selected else original
                    self.assertEqual(pointer, OUTPUT)
                    self.assertEqual(rewritten, expected)
                f.callbacks[1]()
                self.assertIsNone(f.gate.active_revision)
                for member in f.gate.members.values():
                    self.assertEqual(member._q(member, 32), 0)
                    self.assertIsNone(member.enrolled)
                    proof, reason = member._proof(member)
                    self.assertIsNone(proof)
                    self.assertEqual(reason, 'CAMPAIGN_GATE_INACTIVE')


if __name__ == '__main__':
    unittest.main()
