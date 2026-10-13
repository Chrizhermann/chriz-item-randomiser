"""The new original options survive configuration compilation and native export."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import test_postinstall_native_export as native_tests
from test_container_rewards import compiled_conditions, script_fixture
from test_cursed_disguises import resources as cursed_resources, spec_for
from test_mode2_rewards import dialogue_fixture
from test_postinstall_world_formats import item_record, make_are, make_cre
from test_postinstall_resources import snapshot
from flir_config import container_rewards, mode2_rewards
from flir_config.campaigns import read_campaign, write_campaign
from flir_config.original_catalog import resolve_template
from flir_config.original_configuration import build_original_configuration
from flir_config.profiles import ProfileError, edit_profile, new_profile
from flir_config.quest_options import _Dialogue
from flir_config.quest_rewards import _blocks, _newline
from flir_config.resources import _Tlk, scan_game
from flir_config.sources import new_binding_plan, scan_world
from flir_config.templates import DEFAULT_OPTIONS
from flir_config.world_formats import parse_cre


class OriginalOptionExportTests(unittest.TestCase):
    def fixture(self):
        case = native_tests.NativeExportTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def dialogue_routes(self, f, resources):
        originals = {resource: dialogue_fixture(mode2_rewards.resource_specs(resource))
                     for resource in resources}
        hashes = {resource[4:]: hashlib.sha256(data).hexdigest()
                  for resource, data in originals.items()}
        for resource, data in originals.items():
            (f.override / (resource[4:] + '.dlg')).write_bytes(data)
        # Only these exact synthetic bytes substitute for the reviewed game hashes.
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, hashes), \
             patch.dict(mode2_rewards.PATCHED_HASHES, {name: '' for name in hashes}):
            changed = {resource: mode2_rewards.patch_reward(resource, data)
                       for resource, data in originals.items()}
        patched_hashes = {resource[4:]: hashlib.sha256(data).hexdigest()
                          for resource, data in changed.items()}
        return hashes, patched_hashes, changed

    def mode2_configuration(self, f, source, target):
        draft = resolve_template(f.catalog, f.world, 'bg2-mode1-plus-mode2')
        draft['items'] = [row for row in draft['items'] if row['id'] == source]
        draft['targets'] = [row for row in draft['targets'] if row['id'] == target]
        self.assertEqual(len(draft['items']), 1)
        self.assertEqual(len(draft['targets']), 1)
        with patch('flir_config.original_configuration.resolve_template', return_value=draft):
            configuration = build_original_configuration(f.catalog, f.world, 'bg2-mode1-plus-mode2')
        configuration['plan']['randomization']['overflow'] = False
        return configuration

    def test_copper_routes_remain_immutable_after_staging_and_rescan(self):
        f = self.fixture()
        resources = ('dlg:hendak', 'dlg:lehtin')
        original_hashes, patched_hashes, changed = self.dialogue_routes(f, resources)
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        f.refresh()
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, original_hashes), \
             patch.dict(mode2_rewards.PATCHED_HASHES, patched_hashes):
            configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:mode2:target:18')
            # The authored source is tier 3 and Copper recipient is tier 2.
            # This mechanical route/export fixture uses an unrestricted pairing;
            # allocation of the full original preset is tested separately.
            del configuration['plan']['randomization']
            revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                      configuration['plan'], f.store)['revision']
            before = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), before)
            base = f.output / 'campaign-store/revisions' / revision
            native = json.loads(Path(str(base) + '.native.json').read_text())
            self.assertEqual(native['sources']['original-source:bg2:mode2:item:30']['resources'],
                             list(resources))
            target = native['targets']['original-target:bg2:mode2:target:18']
            self.assertEqual((target['resource'], target['area'], target['recipient_local']),
                             ('dlg:hendak', '*', 'FLPR_RA_COPPER'))
            self.assertEqual(Path(str(base) + '.source-1.patched.dlg').read_bytes(),
                             changed['dlg:hendak'])
            for number, resource in enumerate(resources, 1):
                self.assertEqual((f.output / ('resource-overrides/' + resource[4:].upper() + '.DLG')).read_bytes(),
                                 changed[resource])
                self.assertEqual(Path(str(base) + f'.source-1.route-{number}.patched.dlg').read_bytes(),
                                 changed[resource])
            first = snapshot(f.output)
            for resource, data in changed.items():
                (f.override / (resource[4:] + '.dlg')).write_bytes(data)
            # Already staged own patches reproduce the same immutable campaign.
            f.export(revision)
            self.assertEqual(snapshot(f.output), first)
            f.refresh()
            staged = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), staged)
            self.assertEqual(snapshot(f.output), first)
            # A rescan permits our own installed branches, not unknown edits.
            tampered = changed['dlg:lehtin'].replace(b'FLPR_', b'OTHER', 1)
            self.assertNotEqual(tampered, changed['dlg:lehtin'])
            (f.override / 'lehtin.dlg').write_bytes(tampered)
            f.refresh()
            with self.assertRaises(ProfileError):
                f.export(revision)
            self.assertEqual(snapshot(f.output), first)

    def test_temple_reward_exports_auxiliary_creature_without_another_unit(self):
        f = self.fixture()
        temple = ('dlg:bhoisig', 'dlg:bhnalla', 'dlg:bharval')
        resources = temple + ('dlg:hendak', 'dlg:lehtin')
        original_hashes, patched_hashes, changed = self.dialogue_routes(f, resources)
        (f.override / 'blun20.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        creature = bytes(make_cre([item_record('BLUN20', expiration=0, charges=(0, 0, 0), flags=0)],
                                  slots={9: 0}))
        (f.override / 'scyarryl.cre').write_bytes(creature)
        f.refresh()
        physical = next(row for row in f.world['sources'] if row['id'] == 'cre:scyarryl/item/0')
        transfer = deepcopy(mode2_rewards.TRANSFER_SPECS['scyarryl'])
        transfer['source_record'] = deepcopy(physical['record'])
        transfer['source_resource_sha256'] = next(row['sha256'] for row in f.world['resources']
                                                  if row['id'] == 'cre:scyarryl')
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, original_hashes), \
             patch.dict(mode2_rewards.PATCHED_HASHES, patched_hashes), \
             patch.dict(mode2_rewards.TRANSFER_SPECS, {'scyarryl': transfer}):
            configuration = self.mode2_configuration(f, 'bg2:mode2:item:12', 'bg2:mode2:target:18')
            revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                      configuration['plan'], f.store)['revision']
            manifest = read_campaign(f.store, revision)
            self.assertEqual([(row['id'], row['quantity']) for row in manifest['sources']],
                             [('original-source:bg2:mode2:item:12', 1)])
            self.assertEqual(sum(row['capacity'] for row in manifest['targets']), 1)
            before = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), before)
        base = f.output / 'campaign-store/revisions' / revision
        native = json.loads(Path(str(base) + '.native.json').read_text())
        self.assertEqual(list(native['sources']), ['original-source:bg2:mode2:item:12'])
        endpoint = native['sources']['original-source:bg2:mode2:item:12']
        self.assertEqual(endpoint['resources'], list(temple))
        self.assertEqual(endpoint['auxiliary_creatures'], [{
            'resource': 'cre:scyarryl',
            'resource_sha256': hashlib.sha256(creature).hexdigest(),
            'record_indices': [0], 'payload': transfer['payload'],
            'selected_global': 'FLPR_QM2_12'}])
        self.assertEqual(Path(str(base) + '.source-1.duplicate-1.original.cre').read_bytes(), creature)
        for number, resource in enumerate(temple, 1):
            self.assertEqual((f.output / ('resource-overrides/' + resource[4:].upper() + '.DLG')).read_bytes(),
                             changed[resource])
            self.assertEqual(Path(str(base) + f'.source-1.route-{number}.patched.dlg').read_bytes(),
                             changed[resource])

    def test_ppchest_container_latch_survives_export_staging_and_rescan(self):
        f = self.fixture()
        original_hashes, patched_hashes, dialogues = self.dialogue_routes(
            f, ('dlg:hendak', 'dlg:lehtin'))
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        area = bytes(make_are([], [('PPChest', 0, 0)]))
        (f.override / 'ar1514.are').write_bytes(area)
        original = script_fixture('bcs:ppchest')
        (f.override / 'ppchest.bcs').write_bytes(original)
        f.refresh()
        resolved = resolve_template(f.catalog, f.world, 'bg2-mode1-plus-mode2')
        row = next(row for row in resolved['targets'] if row['id'] == 'bg2:mode2:target:61')
        self.assertIsNone(row['binding_problem'])
        self.assertEqual((row['area_resource'], row['container_name'], row['target_ids']),
                         ('are:ar1514', 'PPChest', ['are:ar1514/container/0']))
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, original_hashes), \
             patch.dict(mode2_rewards.PATCHED_HASHES, patched_hashes), \
             patch.dict(container_rewards.ORIGINAL_HASHES,
                        {'bcs:ppchest': hashlib.sha256(original).hexdigest()}):
            changed = container_rewards.patch_container_resource('bcs:ppchest', original)
            configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:mode2:target:61')
            # Copper source tier 3 and PPChest tier 6 form an unrestricted
            # mechanical export pairing; original preset tier rules stay intact.
            del configuration['plan']['randomization']
            revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                      configuration['plan'], f.store)['revision']
            manifest = read_campaign(f.store, revision)
            self.assertEqual([(source['id'], source['quantity']) for source in manifest['sources']],
                             [('original-source:bg2:mode2:item:30', 1)])
            self.assertEqual([(target['id'], target['capacity'], target['record']['kind'])
                              for target in manifest['targets']],
                             [('original-target:bg2:mode2:target:61', 1, 'container')])
            before = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), before)
            base = f.output / 'campaign-store/revisions' / revision
            native = json.loads(Path(str(base) + '.native.json').read_text())
            self.assertEqual(len(native['sources']), 1)
            self.assertEqual(len(native['targets']), 1)
            target = native['targets']['original-target:bg2:mode2:target:61']
            self.assertEqual((target['kind'], target['area'], target['script_name'],
                              target['x'], target['y'], target['container_type']),
                             ('container', 'AR1514', 'PPChest', 111, 222, 2))
            self.assertEqual(target['loot_latch'], {
                'activation_global': 'FLPR_CPPCHST', 'latch_global': 'RobbedGolem',
                'latch_scope': 'GLOBAL'})
            self.assertEqual((f.output / 'resource-overrides/PPCHEST.BCS').read_bytes(), changed)
            old_block = _blocks(original, 1, _newline(original))[0][0]
            new_block = _blocks(changed, 1, _newline(changed))[0][0]
            self.assertEqual(new_block.split(b'CO\n')[2], old_block.split(b'CO\n')[2])
            for selected in (0, 1, 2):
                globals_ = {'GLOBALUnrelated': 4, 'GLOBALRobbedGolem': 0,
                            'GLOBALFLPR_CPPCHST': selected}
                self.assertTrue(compiled_conditions(old_block.split(b'CO\n')[1], globals_, set()))
                self.assertEqual(compiled_conditions(new_block.split(b'CO\n')[1], globals_, set()),
                                 selected != 1)
            first = snapshot(f.output)
            for resource, data in dialogues.items():
                (f.override / (resource[4:] + '.dlg')).write_bytes(data)
            (f.override / 'ppchest.bcs').write_bytes(changed)
            staged = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), staged)
            self.assertEqual(snapshot(f.output), first)
            f.refresh()
            f.export(revision)
            self.assertEqual(snapshot(f.game), staged)
            self.assertEqual(snapshot(f.output), first)

    def test_trgeni_inventory_reward_exports_transfer_and_frozen_original(self):
        f = self.fixture()
        spec = mode2_rewards.REWARDS['trgeni01']
        dialogue = _Dialogue(dialogue_fixture(mode2_rewards.resource_specs(spec['resource'])), 'trgeni01')
        # Include the adapter's exact authored text location in synthetic bytes.
        struct.pack_into('<I', dialogue.states[35], 0, 43514)
        original = dialogue.render()
        (f.override / 'trgeni01.dlg').write_bytes(original)
        item = (f.override / 'synth001.itm').read_bytes()
        (f.override / 'sw1h23.itm').write_bytes(item)
        (f.override / 'blun05.itm').write_bytes(item)
        creature = make_cre([
            item_record('SW1H23', expiration=0, charges=(0, 0, 0), flags=0),
            item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1)], slots={9: 0, 21: 1})
        creature[0x280:0x2A0] = b'trgeni01' + bytes(24)
        creature = bytes(creature)
        (f.override / 'trgeni01.cre').write_bytes(creature)
        (f.override / 'ar2014.are').write_bytes(make_are([], [], [('Genie', 'TRGENI01')]))
        (f.override / 'BGEE.LUA').write_bytes(b'quests = {}\n')
        language = f.game / 'lang/en_US'
        language.mkdir(parents=True)
        tlk = (f.game / 'dialog.tlk').read_bytes()
        (language / 'dialog.tlk').write_bytes(tlk)
        f.catalog = scan_game(f.game, language='en_US')
        f.world = scan_world(f.catalog)
        physical = next(row for row in f.world['sources'] if row['id'] == 'cre:trgeni01/item/0')
        transfer = deepcopy(mode2_rewards.TRANSFER_SPECS['trgeni01'])
        transfer['source_record'] = deepcopy(physical['record'])
        transfer['source_resource_sha256'] = hashlib.sha256(creature).hexdigest()
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, {'trgeni01': hashlib.sha256(original).hexdigest()}), \
             patch.dict(mode2_rewards.TRANSFER_SPECS, {'trgeni01': transfer}):
            _, refs = mode2_rewards.prepare_reward_texts(tlk, (spec['resource'],), 'en_US')
            changed = mode2_rewards.patch_reward(spec['resource'], original, refs=refs[spec['resource']])
            configuration = self.mode2_configuration(f, 'bg2:mode2:item:32', 'bg2:mode2:target:32')
            revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                      configuration['plan'], f.store)['revision']
            manifest = read_campaign(f.store, revision)
            self.assertEqual([(source['id'], source['quantity']) for source in manifest['sources']],
                             [('original-source:bg2:mode2:item:32', 1)])
            self.assertEqual([(target['id'], target['record']['kind']) for target in manifest['targets']],
                             [('original-target:bg2:mode2:target:32', 'creature-target')])
            before = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), before)
            base = f.output / 'campaign-store/revisions' / revision
            native = json.loads(Path(str(base) + '.native.json').read_text())
            target = native['targets']['original-target:bg2:mode2:target:32']
            self.assertEqual((target['kind'], target['area'], target['script_name'],
                              target['inventory_reward'], target['reward_resource']),
                             ('creature', 'AR2014', 'trgeni01', True, spec['resource']))
            source = native['sources']['original-source:bg2:mode2:item:32']
            self.assertEqual(source['kind'], 'guarded-script')
            self.assertEqual(source['auxiliary_creatures'], [{
                'resource': 'cre:trgeni01', 'resource_sha256': hashlib.sha256(creature).hexdigest(),
                'record_indices': [0], 'payload': transfer['payload'],
                'selected_global': 'FLPR_QM2_32', 'replacement': 'blun05'}])
            self.assertEqual(native['resource_hashes']['BLUN05.ITM'], hashlib.sha256(item).hexdigest())
            self.assertEqual(Path(str(base) + '.source-1.duplicate-1.original.cre').read_bytes(), creature)
            self.assertEqual(Path(str(base) + '.source-1.patched.dlg').read_bytes(), changed)
            self.assertEqual((f.output / 'resource-overrides/TRGENI01.DLG').read_bytes(), changed)
            patched = _Dialogue(changed, 'trgeni01')
            self.assertEqual(patched.actions[:len(dialogue.actions)], dialogue.actions)
            callback = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,32))")'
            self.assertEqual(sum(action.count(callback) for action in patched.actions), 4)
            self.assertEqual((f.override / 'synth002.itm').read_bytes(), item)
            first = snapshot(f.output)
            f.export(revision)
            self.assertEqual(snapshot(f.output), first)
            self.assertEqual(snapshot(f.game), before)

    def test_udlesa_keeps_one_physical_source_and_links_duel_transfer(self):
        f = self.fixture()
        spec = deepcopy(mode2_rewards.REWARDS['udlesa'])
        dialogue = _Dialogue(dialogue_fixture(mode2_rewards.resource_specs(spec['resource'])), 'udlesa')
        struct.pack_into('<I', dialogue.states[6], 0, 52664)
        original = dialogue.render()
        (f.override / 'udlesa.dlg').write_bytes(original)
        item = (f.override / 'synth001.itm').read_bytes()
        for name in ('sw1h39', 'dwsw1h02'):
            (f.override / (name + '.itm')).write_bytes(item)
        creature = make_cre([
            item_record('SYNTH001', expiration=0, charges=(1, 2, 3), flags=1),
            item_record('SYNTH002', expiration=0, charges=(3, 2, 1), flags=1),
            item_record('SW1H39', expiration=0, charges=(0, 0, 0), flags=0)],
            slots={21: 0, 22: 1, 9: 2})
        creature[0x280:0x2A0] = b'udlesa' + bytes(26)
        creature = bytes(creature)
        (f.override / 'udlesa.cre').write_bytes(creature)
        (f.override / 'ar2202.are').write_bytes(make_are([], [], [('Lasaonar', 'UDLESA')]))
        (f.override / 'BGEE.LUA').write_bytes(b'quests = {}\n')
        language = f.game / 'lang/en_US'
        language.mkdir(parents=True)
        tlk = (f.game / 'dialog.tlk').read_bytes()
        (language / 'dialog.tlk').write_bytes(tlk)
        f.catalog = scan_game(f.game, language='en_US')
        f.world = scan_world(f.catalog)
        physical = next(row for row in f.world['sources'] if row['id'] == 'cre:udlesa/item/2')
        spec['source_record'] = deepcopy(physical['record'])
        spec['source_resource_sha256'] = hashlib.sha256(creature).hexdigest()
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, {'udlesa': hashlib.sha256(original).hexdigest()}), \
             patch.dict(mode2_rewards.REWARDS, {'udlesa': spec}):
            _, refs = mode2_rewards.prepare_reward_texts(tlk, (spec['resource'],), 'en_US')
            changed = mode2_rewards.patch_reward(spec['resource'], original, refs=refs[spec['resource']])
            configuration = self.mode2_configuration(f, 'bg2:mode2:item:42', 'bg2:mode2:target:45')
            self.assertEqual(configuration['plan']['registrations'], [])
            self.assertEqual(configuration['plan']['sources'][0]['source_ids'], ['cre:udlesa/item/2'])
            revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                      configuration['plan'], f.store)['revision']
            manifest = read_campaign(f.store, revision)
            self.assertEqual([(source['id'], source['quantity'], source['record']['kind'])
                              for source in manifest['sources']],
                             [('cre:udlesa/item/2', 1, 'cre-template')])
            self.assertEqual(manifest['sources'][0]['replacement']['resref'], 'dwsw1h02')
            self.assertEqual([(target['id'], target['record']['kind'], target['record']['resource'])
                              for target in manifest['targets']],
                             [('original-target:bg2:mode2:target:45', 'creature-target', 'dlg:udlesa')])
            before = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), before)
            base = f.output / 'campaign-store/revisions' / revision
            native = json.loads(Path(str(base) + '.native.json').read_text())
            self.assertEqual(list(native['sources']), ['cre:udlesa/item/2'])
            source = native['sources']['cre:udlesa/item/2']
            self.assertEqual((source['kind'], source['replacement'], source['reward_selected_global']),
                             ('guarded-creature', 'dwsw1h02', 'FLPR_QM2_42'))
            self.assertNotIn('auxiliary_creatures', source)
            self.assertFalse(list(base.parent.glob(revision + '.source-*.duplicate-*.original.cre')))
            self.assertEqual(native['resource_hashes']['DWSW1H02.ITM'], hashlib.sha256(item).hexdigest())
            target = native['targets']['original-target:bg2:mode2:target:45']
            self.assertEqual((target['kind'], target['area'], target['script_name'],
                              target['inventory_reward'], target['reward_resource']),
                             ('creature', 'AR2202', 'udlesa', True, 'dlg:udlesa'))
            self.assertEqual(Path(str(base) + '.source-1.original.cre').read_bytes(), creature)
            removed = parse_cre(Path(str(base) + '.source-1.removed.cre').read_bytes())['items']
            pristine = parse_cre(creature)['items']
            self.assertEqual(removed[:2], pristine[:2])
            self.assertEqual(removed[2], {**pristine[2], 'resref': 'dwsw1h02'})
            self.assertEqual((f.output / 'resource-overrides/UDLESA.DLG').read_bytes(), changed)
            patched = _Dialogue(changed, 'udlesa')
            self.assertEqual(patched.actions[:len(dialogue.actions)], dialogue.actions)
            callback = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,45))")'
            self.assertEqual(sum(action.count(callback) for action in patched.actions), 12)
            allocated = refs['dlg:udlesa'][52664]
            self.assertTrue(any(struct.unpack_from('<I', state)[0] == allocated for state in patched.states))
            exported_tlk = (f.output / 'game-overrides/lang/en_US/dialog.tlk').read_bytes()
            self.assertEqual(_Tlk(exported_tlk).text(allocated, []),
                             'I yield. You have proven the better combatant this day. I... I yield the victory.')

    def test_dialogue_grant_keeps_exact_charges_through_export(self):
        f = self.fixture()
        spec = mode2_rewards.REWARDS['ppstat01']
        original = dialogue_fixture(spec)
        (f.override / 'ppstat01.dlg').write_bytes(original)
        (f.override / 'ar1512.are').write_bytes(make_are([], []))
        # The ITM has no abilities; the authored grant nevertheless has [1,1,1].
        (f.override / 'ring31.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        f.refresh()
        draft = resolve_template(f.catalog, f.world, 'bg2-mode1-plus-mode2')
        draft['items'] = [row for row in draft['items'] if row['id'] == 'bg2:mode2:item:28']
        draft['targets'] = [row for row in draft['targets'] if row['id'] == 'bg2:mode2:target:35']
        with patch('flir_config.original_configuration.resolve_template', return_value=draft):
            configuration = build_original_configuration(f.catalog, f.world, 'bg2-mode1-plus-mode2')
        # This focused configuration has equal capacity; the original preset's
        # overflow policy deliberately reserves ordinary physical locations.
        configuration['plan']['randomization']['overflow'] = False
        self.assertEqual(configuration['plan']['registrations'][0]['payload']['charges'], [1, 1, 1])
        digest = hashlib.sha256(original).hexdigest()
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, {'ppstat01': digest}), \
             patch.dict(mode2_rewards.PATCHED_HASHES, {'ppstat01': ''}):
            changed = mode2_rewards.patch_reward('dlg:ppstat01', original)
        with patch.dict(mode2_rewards.ORIGINAL_HASHES, {'ppstat01': digest}), \
             patch.dict(mode2_rewards.PATCHED_HASHES, {'ppstat01': hashlib.sha256(changed).hexdigest()}):
            revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                      configuration['plan'], f.store)['revision']
            before = snapshot(f.game)
            f.export(revision)
            self.assertEqual(snapshot(f.game), before)
        base = f.output / 'campaign-store/revisions' / revision
        self.assertEqual(Path(str(base) + '.source-1.patched.dlg').read_bytes(), changed)
        self.assertEqual((f.output / 'resource-overrides/PPSTAT01.DLG').read_bytes(), changed)
        native = json.loads(Path(str(base) + '.native.json').read_text())
        target = native['targets']['original-target:bg2:mode2:target:35']
        self.assertEqual((target['area'], target['recipient_local']), ('*', 'FLPR_RM2_28'))

    def test_cursed_assets_are_independent_of_selected_items_and_immutable(self):
        f = self.fixture()
        data = cursed_resources()
        for name, value in data.items():
            parsed = bytearray(value)
            struct.pack_into('<HH', parsed, 0x72 + 0x1E, 0, 0)
            data[name] = bytes(parsed)
            (f.override / (name + '.itm')).write_bytes(data[name])
        (f.override / 'BGEE.LUA').write_bytes(b'quests = {}\n')
        language = f.game / 'lang/en_US'
        language.mkdir(parents=True)
        (language / 'dialog.tlk').write_bytes((f.game / 'dialog.tlk').read_bytes())
        f.catalog = scan_game(f.game, language='en_US')
        f.world = scan_world(f.catalog)
        profile = edit_profile(new_profile(f.catalog, 'cursed-test', 'Cursed'), f.catalog, include=['synth001'])
        plan = new_binding_plan(profile, f.catalog, f.world)
        plan.update(original_template_id='bg2-mode1', original_choices={'items': 'mode1', 'targets': 'mode1'},
                    original_options={**DEFAULT_OPTIONS, 'cursed_disguise': True}, target_registrations=[],
                    cursed_disguises=spec_for(data))
        plan['sources'] = [{'item': 'synth001', 'policy': 'relocate', 'source_ids': [f.first], 'registration_ids': []}]
        plan['targets'] = [{'id': 'are:area/actor/0', 'capacity': 1, 'optional': False}]
        revision = write_campaign(profile, f.catalog, f.world, plan, f.store)['revision']
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        base = f.output / 'campaign-store/revisions' / revision
        native = json.loads(Path(str(base) + '.native.json').read_text())
        self.assertEqual(len(native['cursed_disguises']['items']), 8)
        assets = Path(str(base) + '.cursed')
        self.assertEqual(len(list(assets.glob('*.itm'))), 41)
        self.assertEqual((assets / 'plat17.original.itm').read_bytes(), data['plat17'])
        first = snapshot(f.output)
        f.export(revision)
        self.assertEqual(snapshot(f.output), first)
