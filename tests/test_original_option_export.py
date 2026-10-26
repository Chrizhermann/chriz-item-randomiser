"""The new original options survive configuration compilation and native export."""
import hashlib
import json
from pathlib import Path
import re
import struct
import unittest
from unittest.mock import patch

import test_postinstall_native_export as native_tests
from test_container_rewards import compiled_conditions, script_fixture
from test_cursed_disguises import resources as cursed_resources, spec_for
from test_mode2_rewards import dialogue_fixture
from test_postinstall_world_formats import item_record, make_are, make_cre
from test_postinstall_resources import snapshot, write_eet_names
from flir_config import container_rewards, mode2_rewards, translations
from flir_config.game_names import _eet_tables, eet_names
from flir_config.game_text import GameText
from flir_config.campaigns import read_campaign, write_campaign
from flir_config.original_catalog import resolve_template
from flir_config.original_configuration import build_original_configuration
from flir_config.profiles import ProfileError, _digest, edit_profile, new_profile
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
        """Write synthetic route dialogues to the game; return their expected patches."""
        originals = {resource: dialogue_fixture(mode2_rewards.resource_specs(resource))
                     for resource in resources}
        for resource, data in originals.items():
            (f.override / (resource[4:] + '.dlg')).write_bytes(data)
        return {resource: mode2_rewards.patch_reward(resource, data)
                for resource, data in originals.items()}

    def mode2_configuration(self, f, source, target, template='bg2-original', options=None):
        draft = resolve_template(f.catalog, f.world, template, items='extended', targets='extended',
                                 options=options)
        draft['items'] = [row for row in draft['items'] if row['id'] == source]
        draft['targets'] = [row for row in draft['targets'] if row['id'] == target]
        self.assertEqual(len(draft['items']), 1)
        self.assertEqual(len(draft['targets']), 1)
        with patch('flir_config.original_configuration.resolve_template', return_value=draft):
            configuration = build_original_configuration(f.catalog, f.world, template, items='extended',
                                                         targets='extended', options=options)
        configuration['plan']['randomization']['overflow'] = False
        return configuration

    def test_copper_routes_remain_immutable_after_staging_and_rescan(self):
        f = self.fixture()
        resources = ('dlg:hendak', 'dlg:lehtin')
        changed = self.dialogue_routes(f, resources)
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        f.refresh()
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
        with self.assertRaisesRegex(ProfileError, "The extended reward route for HENDAK cannot be used: "
                                    "LEHTIN.DLG carries a changed copy of the reward route's own patch"):
            f.export(revision)
        self.assertEqual(snapshot(f.output), first)

    TEMPLE = ('dlg:bhoisig', 'dlg:bhnalla', 'dlg:bharval')
    COPY = {'expiration': 0, 'charges': (0, 0, 0)}

    def temple_campaign(self, creature: bytes):
        """The temple's reward (source 12) with this SCYARRYL.CRE: fixture, revision and route patches."""
        f = self.fixture()
        changed = self.dialogue_routes(f, self.TEMPLE + ('dlg:hendak', 'dlg:lehtin'))
        (f.override / 'blun20.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        (f.override / 'scyarryl.cre').write_bytes(creature)
        f.refresh()
        configuration = self.mode2_configuration(f, 'bg2:mode2:item:12', 'bg2:mode2:target:18')
        revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                  configuration['plan'], f.store)['revision']
        manifest = read_campaign(f.store, revision)
        self.assertEqual([(row['id'], row['quantity']) for row in manifest['sources']],
                         [('original-source:bg2:mode2:item:12', 1)])
        self.assertEqual(sum(row['capacity'] for row in manifest['targets']), 1)
        return f, revision, changed

    def test_a_trolls_place_exports_the_troll_that_stands_in_its_area(self):
        """pptroll.fl's place (row 37): the painting troll PPTROLL1 in AR1514,
        as a creature place (original_catalog.CREATURE_VERSIONS)."""
        f = self.fixture()
        self.dialogue_routes(f, ('dlg:hendak', 'dlg:lehtin'))
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        troll = bytearray(make_cre([]))
        troll[0x280:0x289] = b'pptroll1\0'
        (f.override / 'pptroll1.cre').write_bytes(troll)
        (f.override / 'ar1514.are').write_bytes(make_are([], [], [('Troll', 'PPTROLL1')]))
        f.refresh()
        # The sword of the Copper Coronet's quest and the troll are both tier 3.
        configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:mode2:target:37')
        revision = write_campaign(configuration['profile'], f.catalog, f.world, configuration['plan'],
                                  f.store)['revision']
        manifest = read_campaign(f.store, revision)
        self.assertEqual([(row['id'], row['record']['kind']) for row in manifest['targets']],
                         [('original-target:bg2:mode2:target:37', 'creature-target')])
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        native = json.loads(Path(str(f.output / 'campaign-store/revisions' / revision) + '.native.json').read_text())
        self.assertEqual(native['targets']['original-target:bg2:mode2:target:37'], {
            'kind': 'creature', 'area': 'AR1514', 'script_name': 'pptroll1',
            'identity': _digest({'target': manifest['targets'][0]})})
        self.assertIn('PPTROLL1.CRE', native['resource_hashes'])

    def test_the_princes_place_exports_the_prince_and_his_reply_that_hands_it_over(self):
        """tadpoles2.fl's place (row 59): the Sahuagin prince SAHPR2 in AR2300
        carries the reward, and his reply that ends his job hands it over,
        then the one of tadpoles3.fl's place (row 60), his too."""
        f = self.fixture()
        self.dialogue_routes(f, ('dlg:hendak', 'dlg:lehtin'))
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        original = dialogue_fixture(mode2_rewards.resource_specs('dlg:sahpr2'))
        (f.override / 'sahpr2.dlg').write_bytes(original)
        prince = bytearray(make_cre([]))
        prince[0x280:0x287] = b'sahpr2\0'
        (f.override / 'sahpr2.cre').write_bytes(prince)
        (f.override / 'ar2300.are').write_bytes(make_are([], [], [('Prince', 'SAHPR2')]))
        f.refresh()
        configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:mode2:target:59')
        # The sword is tier 3, the prince's place tier 6: this fixture pairs them without tiers.
        del configuration['plan']['randomization']
        revision = write_campaign(configuration['profile'], f.catalog, f.world, configuration['plan'],
                                  f.store)['revision']
        manifest = read_campaign(f.store, revision)
        self.assertEqual([(row['id'], row['record']['kind']) for row in manifest['targets']],
                         [('original-target:bg2:mode2:target:59', 'creature-target')])
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        native = json.loads(Path(str(f.output / 'campaign-store/revisions' / revision) + '.native.json').read_text())
        creature = next(row for row in f.world['targets'] if row['id'] == 'cre:sahpr2')
        self.assertEqual(native['targets']['original-target:bg2:mode2:target:59'], {
            'kind': 'creature', 'area': 'AR2300', 'script_name': 'sahpr2', 'inventory_reward': True,
            'reward_resource': 'dlg:sahpr2', 'identity': _digest({'target': manifest['targets'][0],
                                                                  'creature': creature})})
        for name in ('SAHPR2.DLG', 'SAHPR2.CRE', 'AR2300.ARE'):
            self.assertIn(name, native['resource_hashes'])
        changed = mode2_rewards.patch_reward('dlg:sahpr2', original)
        self.assertEqual((f.output / 'resource-overrides/SAHPR2.DLG').read_bytes(), changed)
        # The reply that ends his job hands over first (row 59, then row 60,
        # which this campaign leaves empty).
        transfer = (b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,59))")\r\n'
                    b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,60))")\r\n')
        replies = [_Dialogue(data, 'sahpr2') for data in (original, changed)]
        plain, patched = (dialogue.actions[struct.unpack_from('<I', dialogue.transitions[
            struct.unpack_from('<I', dialogue.states[5], 4)[0]], 16)[0]] for dialogue in replies)
        self.assertEqual(patched, transfer + plain)
        self.assertEqual(sum(action.count(b'FLPR_Transfer') for action in replies[1].actions), 2)
        first = snapshot(f.output)
        f.export(revision)
        self.assertEqual(snapshot(f.output), first)
        # Row 60's reward is his too, with the same patch.
        configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:mode2:target:60')
        del configuration['plan']['randomization']
        other = write_campaign(configuration['profile'], f.catalog, f.world, configuration['plan'],
                               f.store)['revision']
        manifest = read_campaign(f.store, other)
        self.assertEqual([(row['id'], row['record']['kind']) for row in manifest['targets']],
                         [('original-target:bg2:mode2:target:60', 'creature-target')])
        f.export(other)
        native = json.loads(Path(str(f.output / 'campaign-store/revisions' / other) + '.native.json').read_text())
        self.assertEqual(native['targets']['original-target:bg2:mode2:target:60'], {
            'kind': 'creature', 'area': 'AR2300', 'script_name': 'sahpr2', 'inventory_reward': True,
            'reward_resource': 'dlg:sahpr2', 'identity': _digest({'target': manifest['targets'][0],
                                                                  'creature': creature})})
        self.assertEqual((f.output / 'resource-overrides/SAHPR2.DLG').read_bytes(), changed)
        # A prince with another script name is not the one the runtime finds;
        # a dialogue in which no reply ends his job cannot hand anything over.
        for name, data, reason in (
                ('sahpr2.cre', bytes(prince).replace(b'sahpr2\0', b'sahpr3\0', 1),
                 'Original reward recipient changed: original-target:bg2:mode2:target:59'),
                ('sahpr2.dlg', original.replace(b'"villynatyjob","GLOBAL",2', b'"villynatyjob","GLOBAL",3'),
                 'The extended reward route for SAHPR2 cannot be used: SAHPR2.DLG has no reply that sets '
                 'villynatyjob to 2')):
            with self.subTest(changed=name):
                (f.override / name).write_bytes(data)
                f.refresh()
                configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:mode2:target:59')
                del configuration['plan']['randomization']
                revision = write_campaign(configuration['profile'], f.catalog, f.world, configuration['plan'],
                                          f.store)['revision']
                with self.assertRaisesRegex(ProfileError, re.escape(reason)):
                    f.export(revision)
                (f.override / name).write_bytes(bytes(prince) if name.endswith('.cre') else original)
                f.refresh()

    def test_adalons_place_exports_her_and_her_reply_that_hands_it_over(self):
        """The base table's row 72: the silver dragon UDSILVER in AR2102
        carries the reward, and her reply that takes her eggs hands it over,
        whichever way her crossbow's route (row 44) goes."""
        f = self.fixture()
        self.dialogue_routes(f, ('dlg:hendak', 'dlg:lehtin'))
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        original = dialogue_fixture(mode2_rewards.resource_specs('dlg:udsilver'))
        (f.override / 'udsilver.dlg').write_bytes(original)
        dragon = bytearray(make_cre([]))
        dragon[0x280:0x289] = b'udsilver\0'
        (f.override / 'udsilver.cre').write_bytes(dragon)
        (f.override / 'ar2102.are').write_bytes(make_are([], [], [('Adalon', 'UDSILVER')]))
        f.refresh()
        configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:base:target:72')
        # The sword is tier 3, her place tier 5: this fixture pairs them without tiers.
        del configuration['plan']['randomization']
        revision = write_campaign(configuration['profile'], f.catalog, f.world, configuration['plan'],
                                  f.store)['revision']
        manifest = read_campaign(f.store, revision)
        self.assertEqual([(row['id'], row['record']['kind']) for row in manifest['targets']],
                         [('original-target:bg2:base:target:72', 'creature-target')])
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        native = json.loads(Path(str(f.output / 'campaign-store/revisions' / revision) + '.native.json').read_text())
        creature = next(row for row in f.world['targets'] if row['id'] == 'cre:udsilver')
        self.assertEqual(native['targets']['original-target:bg2:base:target:72'], {
            'kind': 'creature', 'area': 'AR2102', 'script_name': 'udsilver', 'inventory_reward': True,
            'reward_resource': 'dlg:udsilver', 'identity': _digest({'target': manifest['targets'][0],
                                                                    'creature': creature})})
        for name in ('UDSILVER.DLG', 'UDSILVER.CRE', 'AR2102.ARE'):
            self.assertIn(name, native['resource_hashes'])
        changed = mode2_rewards.patch_reward('dlg:udsilver', original)
        self.assertEqual((f.output / 'resource-overrides/UDSILVER.DLG').read_bytes(), changed)
        # Both variants of her reward reply hand over first.
        transfer = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,72,2,1))")\r\n'
        patched = _Dialogue(changed, 'udsilver')
        first, count = struct.unpack_from('<II', patched.states[41], 4)
        self.assertEqual(count, 2)
        for row in patched.transitions[first:first + count]:
            self.assertTrue(patched.actions[struct.unpack_from('<I', row, 16)[0]].startswith(transfer))
        self.assertEqual(sum(action.count(b'FLPR_Transfer') for action in patched.actions), 2)
        output = snapshot(f.output)
        f.export(revision)
        self.assertEqual(snapshot(f.output), output)
        # A dragon with another script name is not the one the runtime finds;
        # a dialogue in which no reply takes her eggs cannot hand anything over.
        for name, data, reason in (
                ('udsilver.cre', bytes(dragon).replace(b'udsilver\0', b'udsilvex\0', 1),
                 'Original reward recipient changed: original-target:bg2:base:target:72'),
                ('udsilver.dlg', original.replace(b'TakePartyItem("misc9t")', b'TakePartyItem("misc9x")'),
                 'The extended reward route for ADALON cannot be used: UDSILVER.DLG has no reply that takes '
                 'MISC9T')):
            with self.subTest(changed=name):
                (f.override / name).write_bytes(data)
                f.refresh()
                configuration = self.mode2_configuration(f, 'bg2:mode2:item:30', 'bg2:base:target:72')
                del configuration['plan']['randomization']
                revision = write_campaign(configuration['profile'], f.catalog, f.world, configuration['plan'],
                                          f.store)['revision']
                with self.assertRaisesRegex(ProfileError, re.escape(reason)):
                    f.export(revision)
                (f.override / name).write_bytes(bytes(dragon) if name.endswith('.cre') else original)
                f.refresh()

    def test_temple_reward_exports_auxiliary_creature_without_another_unit(self):
        # Yarryl's club as the game has it, and as another mod may keep it:
        # after other items, flagged, carried twice. Without it, there is
        # nothing to remove.
        for rows, slots, auxiliary in (
                ([item_record('BLUN20', flags=0, **self.COPY)], {9: 0}, ([0], 0)),
                ([item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1), item_record('SYNTH001'),
                  item_record('BLUN20', flags=2, **self.COPY), item_record('BLUN20', flags=2, **self.COPY)],
                 {21: 0, 22: 1, 9: 2, 23: 3}, ([2, 3], 2)),
                ([item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1)], {21: 0}, None)):
            with self.subTest(auxiliary=auxiliary):
                creature = bytes(make_cre(rows, slots=slots))
                f, revision, changed = self.temple_campaign(creature)
                before = snapshot(f.game)
                f.export(revision)
                self.assertEqual(snapshot(f.game), before)
                base = f.output / 'campaign-store/revisions' / revision
                native = json.loads(Path(str(base) + '.native.json').read_text())
                self.assertEqual(list(native['sources']), ['original-source:bg2:mode2:item:12'])
                endpoint = native['sources']['original-source:bg2:mode2:item:12']
                self.assertEqual(endpoint['resources'], list(self.TEMPLE))
                duplicate = Path(str(base) + '.source-1.duplicate-1.original.cre')
                if auxiliary:
                    indices, flags = auxiliary
                    self.assertEqual(endpoint['auxiliary_creatures'], [{
                        'resource': 'cre:scyarryl', 'resource_sha256': hashlib.sha256(creature).hexdigest(),
                        'record_indices': indices,
                        'payload': {'resref': 'blun20', 'expiration': 0, 'charges': [0, 0, 0], 'flags': flags},
                        'selected_global': 'FLPR_QM2_12'}])
                    self.assertEqual(duplicate.read_bytes(), creature)
                else:
                    self.assertNotIn('auxiliary_creatures', endpoint)
                    self.assertFalse(duplicate.exists())
                for number, resource in enumerate(self.TEMPLE, 1):
                    self.assertEqual((f.output / ('resource-overrides/' + resource[4:].upper() + '.DLG')).read_bytes(),
                                     changed[resource])
                    self.assertEqual(Path(str(base) + f'.source-1.route-{number}.patched.dlg').read_bytes(),
                                     changed[resource])

    def test_copies_of_a_reward_that_differ_leave_its_row_out(self):
        # The runtime checks every copy against one record's bytes.
        creature = bytes(make_cre([item_record('BLUN20', flags=0, **self.COPY),
                                   item_record('BLUN20', flags=2, **self.COPY)], slots={9: 0, 21: 1}))
        f, revision, _ = self.temple_campaign(creature)
        with self.assertRaisesRegex(ProfileError, "SCYARRYL.CRE carries BLUN20 in records that differ: "
                                    "original-source:bg2:mode2:item:12"):
            f.export(revision)

    def test_ppchest_container_latch_survives_export_staging_and_rescan(self):
        f = self.fixture()
        dialogues = self.dialogue_routes(f, ('dlg:hendak', 'dlg:lehtin'))
        (f.override / 'sw1h03.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        # The golem room's chest holds the bow, as in the game.
        area = bytes(make_are([item_record('BOW19B', expiration=0, charges=(0, 0, 0), flags=0)],
                              [('PPChest', 0, 1)]))
        (f.override / 'ar1514.are').write_bytes(area)
        (f.override / 'bow19b.itm').write_bytes((f.override / 'synth001.itm').read_bytes())
        original = script_fixture('bcs:ppchest')
        (f.override / 'ppchest.bcs').write_bytes(original)
        f.refresh()
        resolved = resolve_template(f.catalog, f.world, 'bg2-original', items='extended', targets='extended')
        row = next(row for row in resolved['targets'] if row['id'] == 'bg2:mode2:target:61')
        self.assertIsNone(row['binding_problem'])
        self.assertEqual((row['area_resource'], row['container_name'], row['target_ids']),
                         ('are:ar1514', 'PPChest', ['are:ar1514/container/0']))
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

    # A carried reward's area, actor, script name and the states whose text
    # names the reward (state -> strref). The fixture is EET: BG1's area has
    # EET's name and BG1's texts EET's numbers.
    CARRIED = {'trgeni01': ('ar2014', 'Genie', 'trgeni01', {35: 43514}),
               'jugjer01': ('ar1103', 'Jermien', 'Jermien', {17: 33760, 23: 33777}),
               'aldeth': ('bg0128', 'Aldeth', 'Aldeth', {27: 205740}),
               'nemphr': ('bg0813', 'Nemphre', 'Nemphre', {})}
    # A BGEE.LUA that files Aldeth's journal entry under its quest.
    JOURNAL = (b'createQuest( 87 )\r\ncreateEntry    ( 87, -1, 226822, {}, nil )\r\n'
               b'createEntry    ( 87, -1, 226828, {}, nil )\r\n')

    def carried_campaign(self, name: str, rows: list, slots: dict, *, journals: dict | None = None,
                         lua: bytes = b'quests = {}\n', renames: dict | None = None, options: dict | None = None,
                         preset: tuple[str, str, str] | None = None):
        """A reward the creature <name> carries (the source and place of its
        route) with a CRE of these records: fixture, revision, creature, the
        dialogue and its patch. <journals> sets journal entries of reward
        replies (state -> strref); <renames> adds item renames to EET's lists;
        <options> are the template's; <preset> names a template and its rows
        of the route's reward and place instead of the route's own rows."""
        f = self.fixture()
        area, actor, script, texts = self.CARRIED[name]
        spec = mode2_rewards.REWARDS[name]
        if renames:
            write_eet_names(f.game, {**_eet_tables(), 'itm': {**_eet_tables()['itm'], **renames}})
        naming = {'game': 'eet', 'names': eet_names(f.game)}
        named = mode2_rewards.reward(name, **naming)
        dialogue = _Dialogue(dialogue_fixture(mode2_rewards.resource_specs(spec['resource'], **naming)), name)
        # Include the adapter's exact authored text location in synthetic bytes.
        for state, strref in texts.items():
            struct.pack_into('<I', dialogue.states[state], 0, strref)
        for state, strref in (journals or {}).items():
            first = struct.unpack_from('<I', dialogue.states[state], 4)[0]
            struct.pack_into('<I', dialogue.transitions[first], 8, strref)
        original = dialogue.render()
        (f.override / f'{name}.dlg').write_bytes(original)
        item = (f.override / 'synth001.itm').read_bytes()
        for resref in (named['item'], named.get('replacement')):
            if resref:
                (f.override / f'{resref}.itm').write_bytes(item)
        creature = make_cre(rows, slots=slots)
        creature[0x280:0x2A0] = script.encode() + bytes(32 - len(script))
        creature = bytes(creature)
        (f.override / f'{name}.cre').write_bytes(creature)
        (f.override / f'{area}.are').write_bytes(make_are([], [], [(actor, name.upper())]))
        (f.override / 'BGEE.LUA').write_bytes(lua)
        language = f.game / 'lang/en_US'
        language.mkdir(parents=True)
        tlk = (f.game / 'dialog.tlk').read_bytes()
        (language / 'dialog.tlk').write_bytes(tlk)
        f.catalog = scan_game(f.game, language='en_US')
        f.world = scan_world(f.catalog)
        _, refs = mode2_rewards.prepare_reward_texts(GameText(tlk), (spec['resource'],), 'en_US', game='eet')
        changed = mode2_rewards.patch_reward(spec['resource'], original, refs=refs.get(spec['resource']),
                                             **naming)
        source, target = spec['source_ids'][0], spec['target_ids'][0]
        template = 'eet-original' if spec['campaign'] == 'bg1' else 'bg2-original'
        if preset:
            template, row, target = preset
            source = 'original-source:' + row
        configuration = self.mode2_configuration(f, source.removeprefix('original-source:'), target,
                                                 template, options)
        if preset:
            # A preset's two rows may have other tiers: this route/export
            # fixture pairs them without tiers.
            del configuration['plan']['randomization']
        revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                  configuration['plan'], f.store)['revision']
        manifest = read_campaign(f.store, revision)
        self.assertEqual([(row['id'], row['quantity']) for row in manifest['sources']], [(source, 1)])
        self.assertEqual([(row['id'], row['record']['kind']) for row in manifest['targets']],
                         [('original-target:' + target, 'creature-target')])
        return f, revision, creature, dialogue, changed

    def test_trgeni_inventory_reward_exports_transfer_and_frozen_original(self):
        # The sword as the game has it, and after another item, identified.
        for rows, slots, (index, flags) in (
                ([item_record('SW1H23', flags=0, **self.COPY),
                  item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1)], {9: 0, 21: 1}, (0, 0)),
                ([item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1),
                  item_record('SW1H23', flags=1, **self.COPY)], {21: 0, 9: 1}, (1, 1))):
            with self.subTest(record=index):
                f, revision, creature, dialogue, changed = self.carried_campaign('trgeni01', rows, slots)
                item = (f.override / 'synth001.itm').read_bytes()
                before = snapshot(f.game)
                f.export(revision)
                self.assertEqual(snapshot(f.game), before)
                base = f.output / 'campaign-store/revisions' / revision
                native = json.loads(Path(str(base) + '.native.json').read_text())
                target = native['targets']['original-target:bg2:mode2:target:32']
                self.assertEqual((target['kind'], target['area'], target['script_name'],
                                  target['inventory_reward'], target['reward_resource']),
                                 ('creature', 'AR2014', 'trgeni01', True, 'dlg:trgeni01'))
                source = native['sources']['original-source:bg2:mode2:item:32']
                self.assertEqual(source['kind'], 'guarded-script')
                self.assertEqual(source['auxiliary_creatures'], [{
                    'resource': 'cre:trgeni01', 'resource_sha256': hashlib.sha256(creature).hexdigest(),
                    'record_indices': [index],
                    'payload': {'resref': 'sw1h23', 'expiration': 0, 'charges': [0, 0, 0], 'flags': flags},
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

    def test_jermiens_sword_exports_his_texts_transfer_and_charged_unstealable_copy(self):
        # Jermien keeps the sword after his other items, in a quick slot,
        # unstealable, with one charge of its ability.
        rows = [item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=10),
                item_record('SW1H26', expiration=0, charges=(1, 1, 0), flags=2)]
        f, revision, creature, dialogue, changed = self.carried_campaign('jugjer01', rows, {0: 0, 18: 1})
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        base = f.output / 'campaign-store/revisions' / revision
        manifest = read_campaign(f.store, revision)
        self.assertEqual(manifest['sources'][0]['record']['record'],
                         {'resref': 'sw1h26', 'expiration': 0, 'charges': [1, 1, 0], 'flags': 0})
        native = json.loads(Path(str(base) + '.native.json').read_text())
        target = native['targets']['original-target:bg2:mode2:target:31']
        self.assertEqual((target['kind'], target['area'], target['script_name'],
                          target['inventory_reward'], target['reward_resource']),
                         ('creature', 'AR1103', 'Jermien', True, 'dlg:jugjer01'))
        source = native['sources']['original-source:bg2:mode2:item:31']
        self.assertEqual(source['kind'], 'guarded-script')
        self.assertEqual(source['auxiliary_creatures'], [{
            'resource': 'cre:jugjer01', 'resource_sha256': hashlib.sha256(creature).hexdigest(),
            'record_indices': [1],
            'payload': {'resref': 'sw1h26', 'expiration': 0, 'charges': [1, 1, 0], 'flags': 2},
            'selected_global': 'FLPR_QM2_31'}])
        self.assertEqual(Path(str(base) + '.source-1.duplicate-1.original.cre').read_bytes(), creature)
        self.assertEqual((f.output / 'resource-overrides/JUGJER01.DLG').read_bytes(), changed)
        # Both texts that name the sword get a copy with Item Randomiser's line about an item.
        patched = _Dialogue(changed, 'jugjer01')
        self.assertEqual(patched.actions[:len(dialogue.actions)], dialogue.actions)
        self.assertEqual([struct.unpack_from('<I', patched.states[state])[0] for state in (17, 23)], [33760, 33777])
        clones = [struct.unpack_from('<I', state)[0] for state in patched.states[len(dialogue.states):]]
        self.assertEqual(len(clones), 2)
        self.assertFalse({33760, 33777} & set(clones))
        # His one reward reply, kept and selected; the text copy of its state shares them.
        callback = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,31))")'
        self.assertEqual(sum(action.count(callback) for action in patched.actions), 2)
        first = snapshot(f.output)
        f.export(revision)
        self.assertEqual(snapshot(f.output), first)
        self.assertEqual(snapshot(f.game), before)

    def test_aldeths_sword_exports_his_texts_journal_entry_and_bg1_transfer(self):
        # Aldeth keeps the sword after another item. EET numbers his thanks
        # and his reward reply's journal entry 200000 higher than BG:EE.
        rows = [item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1),
                item_record('SW1H03', flags=0, **self.COPY)]
        f, revision, creature, dialogue, changed = self.carried_campaign(
            'aldeth', rows, {21: 0, 9: 1}, journals={27: 226828}, lua=self.JOURNAL)
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        base = f.output / 'campaign-store/revisions' / revision
        native = json.loads(Path(str(base) + '.native.json').read_text())
        target = native['targets']['original-target:bg1:mode2:target:16']
        self.assertEqual((target['kind'], target['area'], target['script_name'],
                          target['inventory_reward'], target['reward_resource']),
                         ('creature', 'BG0128', 'Aldeth', True, 'dlg:aldeth'))
        source = native['sources']['original-source:bg1:mode2:item:16']
        self.assertEqual(source['kind'], 'guarded-script')
        self.assertEqual(source['auxiliary_creatures'], [{
            'resource': 'cre:aldeth', 'resource_sha256': hashlib.sha256(creature).hexdigest(),
            'record_indices': [1],
            'payload': {'resref': 'sw1h03', 'expiration': 0, 'charges': [0, 0, 0], 'flags': 0},
            'selected_global': 'FLPR_QB1_16'}])
        self.assertEqual(Path(str(base) + '.source-1.duplicate-1.original.cre').read_bytes(), creature)
        self.assertEqual((f.output / 'resource-overrides/ALDETH.DLG').read_bytes(), changed)
        # His reward reply, kept and selected, passes the BG1 place's row and campaign.
        patched = _Dialogue(changed, 'aldeth')
        callback = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,16,1))")'
        self.assertEqual(sum(action.count(callback) for action in patched.actions), 2)
        # The copy of his state shows Item Randomiser's thanks, and its replies its journal entry.
        self.assertEqual(len(patched.states), len(dialogue.states) + 1)
        clone = patched.states[-1]
        first, count = struct.unpack_from('<II', clone, 4)
        journals = {struct.unpack_from('<I', row, 8)[0] for row in patched.transitions[first:first + count]}
        self.assertEqual(len(journals), 1)
        thanks, entry = struct.unpack_from('<I', clone)[0], journals.pop()
        found = translations.lines('en_US', mode2_rewards.text_lines('dlg:aldeth'))
        tlk = _Tlk((f.output / 'game-overrides/lang/en_US/dialog.tlk').read_bytes())
        # This game's dialog.tlk has no entry 226828, so no title to keep.
        self.assertEqual((tlk.text(thanks, set()), tlk.text(entry, set())),
                         (found['in-game.tra', 1649][0], found['in-game.tra', 1650][0]))
        # BGEE.LUA files the new entry under the original's quest.
        self.assertEqual((f.output / 'resource-overrides/BGEE.LUA').read_bytes(),
                         self.JOURNAL + f'createEntry    ( 87, -1, {entry}, {{}}, nil )\r\n'.encode())
        first = snapshot(f.output)
        f.export(revision)
        self.assertEqual(snapshot(f.output), first)
        self.assertEqual(snapshot(f.game), before)

    def test_nemphres_scroll_exports_the_bg1_transfer_to_her_own_place(self):
        # Item Randomiser's lists name her scroll row 159 and her place row 136,
        # both scroll tiers: the scrolls option enables them.
        rows = [item_record('SCRL1Q', flags=0, **self.COPY)]
        f, revision, creature, dialogue, changed = self.carried_campaign('nemphr', rows, {21: 0},
                                                                         options={'scrolls': True})
        f.export(revision)
        base = f.output / 'campaign-store/revisions' / revision
        native = json.loads(Path(str(base) + '.native.json').read_text())
        target = native['targets']['original-target:bg1:mode2:target:136']
        self.assertEqual((target['kind'], target['area'], target['script_name'],
                          target['inventory_reward'], target['reward_resource']),
                         ('creature', 'BG0813', 'Nemphre', True, 'dlg:nemphr'))
        source = native['sources']['original-source:bg1:mode2:item:159']
        self.assertEqual(source['auxiliary_creatures'], [{
            'resource': 'cre:nemphr', 'resource_sha256': hashlib.sha256(creature).hexdigest(),
            'record_indices': [0],
            'payload': {'resref': 'scrl1q', 'expiration': 0, 'charges': [0, 0, 0], 'flags': 0},
            'selected_global': 'FLPR_QB1_159'}])
        self.assertEqual((f.output / 'resource-overrides/NEMPHR.DLG').read_bytes(), changed)
        patched = _Dialogue(changed, 'nemphr')
        self.assertEqual(len(patched.states), len(dialogue.states))
        callback = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,136,1))")'
        self.assertEqual(sum(action.count(callback) for action in patched.actions), 2)

    def test_boss_loots_rows_of_a_carried_reward_take_its_route(self):
        # Boss Loot's rows of Aldeth's sword and place, and of Nemphre's scroll
        # and place, are their routes: the same reply patch, and the runtime
        # finds the flags, copies and the place the reply names by the route's rows.
        for name, rows, slots, kwargs, preset, (source_row, target_row, flag) in (
                ('aldeth', [item_record('SW1H03', flags=0, **self.COPY)], {9: 0},
                 {'journals': {27: 226828}, 'lua': self.JOURNAL},
                 ('eet-boss-loot', 'bg1:boss-loot:mode2:item:62', 'bg1:boss-loot:mode2:target:46'),
                 (16, 16, 'FLPR_QB1_16')),
                ('nemphr', [item_record('SCRL1Q', flags=0, **self.COPY)], {21: 0}, {'options': {'scrolls': True}},
                 ('eet-boss-loot', 'bg1:boss-loot:mode2:item:339', 'bg1:boss-loot:mode2:target:291'),
                 (159, 136, 'FLPR_QB1_159'))):
            with self.subTest(route=name):
                f, revision, creature, _, changed = self.carried_campaign(name, rows, slots, preset=preset,
                                                                          **kwargs)
                f.export(revision)
                base = f.output / 'campaign-store/revisions' / revision
                native = json.loads(Path(str(base) + '.native.json').read_text())
                source = native['sources']['original-source:' + preset[1]]
                self.assertEqual((source['kind'], source['route_source'], source['selected_global']),
                                 ('guarded-script', f'original-source:bg1:mode2:item:{source_row}', flag))
                self.assertEqual([(copy['resource'], copy['selected_global'])
                                  for copy in source['auxiliary_creatures']], [('cre:' + name, flag)])
                self.assertEqual(Path(str(base) + '.source-1.duplicate-1.original.cre').read_bytes(), creature)
                target = native['targets']['original-target:' + preset[2]]
                self.assertEqual((target['kind'], target['inventory_reward'], target['route_target']),
                                 ('creature', True, f'original-target:bg1:mode2:target:{target_row}'))
                self.assertEqual((f.output / f'resource-overrides/{name.upper()}.DLG').read_bytes(), changed)
                callback = f'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,{target_row},1))")'
                self.assertEqual(sum(action.count(callback.encode()) for action in _Dialogue(changed, name).actions),
                                 2)

    def test_boss_loots_rows_of_varcis_shield_take_his_route(self):
        # A route whose reply gives the shield: the runtime finds the place by
        # its flags, so only the source names its route's row.
        f = self.fixture()
        naming = {'game': 'eet', 'names': eet_names(f.game)}
        named = mode2_rewards.reward('varci', **naming)
        original = dialogue_fixture(mode2_rewards.resource_specs('dlg:varci', **naming))
        (f.override / 'varci.dlg').write_bytes(original)
        (f.override / f"{named['item']}.itm").write_bytes((f.override / 'synth001.itm').read_bytes())
        f.refresh()
        changed = mode2_rewards.patch_reward('dlg:varci', original, **naming)
        configuration = self.mode2_configuration(f, 'bg1:boss-loot:mode2:item:71', 'bg1:boss-loot:mode2:target:71',
                                                 'eet-boss-loot')
        revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                  configuration['plan'], f.store)['revision']
        f.export(revision)
        base = f.output / 'campaign-store/revisions' / revision
        native = json.loads(Path(str(base) + '.native.json').read_text())
        source = native['sources']['original-source:bg1:boss-loot:mode2:item:71']
        self.assertEqual((source['route_source'], source['selected_global'], source['consumed_global']),
                         ('original-source:bg1:mode2:item:20', 'FLPR_QB1_20', 'FLPR_XB1_20'))
        target = native['targets']['original-target:bg1:boss-loot:mode2:target:71']
        self.assertEqual((target['kind'], target['area'], target['event_global'], target['recipient_local']),
                         ('quest', '*', 'FLPR_EB1_20', 'FLPR_RB1_20'))
        self.assertNotIn('route_target', target)
        self.assertEqual((f.output / 'resource-overrides/VARCI.DLG').read_bytes(), changed)

    def test_a_carried_bg1_reward_this_game_renames_cannot_be_exported(self):
        # The runtime accepts Aldeth's copy only as ALDETH.CRE's SW1H03.
        rows = [item_record('SW1H03X', flags=0, **self.COPY)]
        f, revision, _, _, _ = self.carried_campaign('aldeth', rows, {9: 0}, journals={27: 226828},
                                                     renames={'sw1h03': 'sw1h03x'})
        with self.assertRaisesRegex(ProfileError, "This game renames ALDETH.CRE or SW1H03, which Chriz Item "
                                    "Randomiser's runtime knows only by those names: "
                                    "original-source:bg1:mode2:item:16"):
            f.export(revision)

    def test_a_carried_reward_the_creature_does_not_carry_leaves_its_row_out(self):
        # The genie's dialogue hands over the sword it carries; this one has none.
        f, revision, _, _, _ = self.carried_campaign(
            'trgeni01', [item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1)], {21: 0})
        with self.assertRaisesRegex(ProfileError, "TRGENI01.CRE does not carry SW1H23, which the reward of "
                                    "TRGENI01.DLG hands over: original-source:bg2:mode2:item:32"):
            f.export(revision)

    def test_udlesa_keeps_one_physical_source_and_links_duel_transfer(self):
        # Lesa's sword as the game has it, and first and identified, as
        # another mod may keep it: the link follows the record that is the row.
        for rows, slots, index in (
                ([item_record('SYNTH001', expiration=0, charges=(1, 2, 3), flags=1),
                  item_record('SYNTH002', expiration=0, charges=(3, 2, 1), flags=1),
                  item_record('SW1H39', flags=0, **self.COPY)], {21: 0, 22: 1, 9: 2}, 2),
                ([item_record('SW1H39', flags=1, **self.COPY),
                  item_record('SYNTH001', expiration=0, charges=(1, 2, 3), flags=1)], {9: 0, 21: 1}, 0)):
            with self.subTest(record=index):
                self.check_udlesa(rows, slots, index)

    def check_udlesa(self, rows: list, slots: dict, index: int):
        f = self.fixture()
        spec = mode2_rewards.REWARDS['udlesa']
        physical = f'cre:udlesa/item/{index}'
        dialogue = _Dialogue(dialogue_fixture(mode2_rewards.resource_specs(spec['resource'])), 'udlesa')
        struct.pack_into('<I', dialogue.states[6], 0, 52664)
        original = dialogue.render()
        (f.override / 'udlesa.dlg').write_bytes(original)
        item = (f.override / 'synth001.itm').read_bytes()
        for name in ('sw1h39', 'dwsw1h02'):
            (f.override / (name + '.itm')).write_bytes(item)
        creature = make_cre(rows, slots=slots)
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
        _, refs = mode2_rewards.prepare_reward_texts(GameText(tlk), (spec['resource'],), 'en_US')
        changed = mode2_rewards.patch_reward(spec['resource'], original, refs=refs[spec['resource']])
        configuration = self.mode2_configuration(f, 'bg2:mode2:item:42', 'bg2:mode2:target:45')
        self.assertEqual(configuration['plan']['registrations'], [])
        self.assertEqual(configuration['plan']['sources'][0]['source_ids'], [physical])
        revision = write_campaign(configuration['profile'], f.catalog, f.world,
                                  configuration['plan'], f.store)['revision']
        manifest = read_campaign(f.store, revision)
        self.assertEqual([(source['id'], source['quantity'], source['record']['kind'])
                          for source in manifest['sources']],
                         [(physical, 1, 'cre-template')])
        self.assertEqual(manifest['sources'][0]['replacement']['resref'], 'dwsw1h02')
        self.assertEqual([(target['id'], target['record']['kind'], target['record']['resource'])
                          for target in manifest['targets']],
                         [('original-target:bg2:mode2:target:45', 'creature-target', 'dlg:udlesa')])
        before = snapshot(f.game)
        f.export(revision)
        self.assertEqual(snapshot(f.game), before)
        base = f.output / 'campaign-store/revisions' / revision
        native = json.loads(Path(str(base) + '.native.json').read_text())
        self.assertEqual(list(native['sources']), [physical])
        source = native['sources'][physical]
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
        self.assertEqual(removed, [{**row, 'resref': 'dwsw1h02'} if number == index else row
                                   for number, row in enumerate(pristine)])
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
        draft = resolve_template(f.catalog, f.world, 'bg2-original', items='extended', targets='extended')
        draft['items'] = [row for row in draft['items'] if row['id'] == 'bg2:mode2:item:28']
        draft['targets'] = [row for row in draft['targets'] if row['id'] == 'bg2:mode2:target:35']
        with patch('flir_config.original_configuration.resolve_template', return_value=draft):
            configuration = build_original_configuration(f.catalog, f.world, 'bg2-original', items='extended', targets='extended')
        # This focused configuration has equal capacity; the original preset's
        # overflow policy deliberately reserves ordinary physical locations.
        configuration['plan']['randomization']['overflow'] = False
        self.assertEqual(configuration['plan']['registrations'][0]['payload']['charges'], [1, 1, 1])
        changed = mode2_rewards.patch_reward('dlg:ppstat01', original)
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
        plan.update(original_template_id='bg2-original', original_choices={'items': 'standard', 'targets': 'standard'},
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
