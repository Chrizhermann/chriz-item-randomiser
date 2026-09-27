"""Portable BLPOOL adapter checks; no installed game or ignored work files."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from flir_config.campaigns import _validate_source, compile_campaign, write_campaign
from flir_config.native_export import _compile, export_native
from flir_config.profiles import ProfileError, edit_profile, new_profile, validate_catalog
from flir_config.resources import scan_game
from flir_config.scripted_sources import (ORIGINAL_SHA256, PATCHED_SHA256, PAYLOAD,
                                          SOURCE_ID, blpool_registration,
                                          campaign_record, patch_blpool)
from flir_config.sources import new_binding_plan, scan_world, validate_world
from test_postinstall_resources import make_game, make_itm, snapshot
from test_postinstall_world_formats import make_sto


def compiled_blpool():
    # The original script's four small blocks, in the engine's compiled text
    # format. Objects 1/17 are Myself/LastTrigger. This also verifies the exact
    # fixture hash without depending on a developer's installation snapshot.
    def obj(value=0):
        return f'0 0 0 0 0 0 0 {value} 0 0 0 0 ""OB\n'
    def trigger(op, value=0, negate=0, string="", target=0):
        return f'TR\n{op} {value} {negate} 0 0 "{string}" "" OB\n'+obj(target)+'TR\n'
    def action(op, value=0, string="", target=0):
        return f'AC\n{op}OB\n'+obj()+'OB\n'+obj(target)+'OB\n'+obj()+f'{value} 0 0 0 0"{string}" "" AC\n'
    def block(conditions, actions):
        return 'CR\nCO\n'+conditions+'CO\nRS\nRE\n100'+actions+'RE\nRS\nCR\n'
    clicked=trigger(112); close=trigger(16408,10,target=17)
    pool=lambda value: trigger(16399,value,string='AR0801Pool')
    display=lambda value: action(151,value,target=1)
    advance=lambda value: action(30,value,'AR0801Pool')
    return ('SC\n'+block(clicked+close+pool(0),display(35875)+advance(1))+
            block(clicked+close+pool(1),display(35877)+action(140,string='BLUN12',target=17)+
                  action(181,2020,target=17)+advance(2))+
            block(clicked+close+pool(2),display(35878))+
            block(clicked+trigger(16408,10,1,target=17),display(14702))+'SC\n').encode('ascii')


class BlpoolScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = compiled_blpool()
        cls.temp = tempfile.TemporaryDirectory(prefix="flir-blpool-source-")
        cls.addClassCleanup(cls.temp.cleanup)
        game = Path(cls.temp.name) / 'game'
        make_game(game, [("BLUN12", make_itm())])
        item = bytearray(make_itm()); struct.pack_into('<I',item,0x18,0x4c)
        (game/'override/blun12.itm').write_bytes(item)
        (game/'override/blpool.bcs').write_bytes(cls.original)
        (game/'override/ambar01.sto').write_bytes(make_sto([]))
        cls.catalog = scan_game(game)
        cls.world = scan_world(cls.catalog)

    def test_rewrite_is_exact_and_preserves_other_blocks(self):
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), ORIGINAL_SHA256)
        patched = patch_blpool(self.original)
        self.assertEqual(hashlib.sha256(patched).hexdigest(), PATCHED_SHA256)
        self.assertEqual(patched.count(b'0 0 0 0 0"BLUN12" "" AC'), 1)
        self.assertEqual(patched.count(b'1 0 0 0 0"GLOBALFLPR_XPOOL" "" AC'), 1)
        witness_at = patched.index(b'1 0 0 0 0"GLOBALFLPR_XPOOL" "" AC')
        witness_action_start = patched.rfind(b'AC\n', 0, witness_at)
        self.assertTrue(patched[witness_action_start:witness_at].startswith(b'AC\n30OB\n'))
        self.assertNotIn(b'140OB', patched[witness_action_start:witness_at])
        self.assertEqual(patched.count(b'AC\n140OB\n'), 1)
        self.assertEqual(patched.count(b'16399 1 0 0 0 "GLOBALFLPR_QPOOL" "" OB'), 1)
        self.assertEqual(patched.count(b'16399 1 1 0 0 "GLOBALFLPR_QPOOL" "" OB'), 1)
        self.assertEqual(patched.count(b'2020 0 0 0 0"" "" AC'), 2)
        self.assertEqual(patched.count(b'2 0 0 0 0"AR0801Pool" "" AC'), 2)
        self.assertEqual(patched.count(b'35877 0 0 0 0"" "" AC'), 1)
        self.assertEqual(patched.count(b'35878 0 0 0 0"" "" AC'), 2)
        self.assertEqual(patched.split(b'CR\nCO\n')[0], self.original.split(b'CR\nCO\n')[0])

    def test_modified_script_is_rejected(self):
        with self.assertRaisesRegex(ProfileError, "changed"):
            patch_blpool(self.original.replace(b'BLUN12', b'BLUN13'))

    def test_registration_compiles_one_script_source_and_native_gate(self):
        registration = blpool_registration(self.world)
        profile = edit_profile(new_profile(self.catalog, "blpool-test", "Pool test"),
                               self.catalog, include=["blun12"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        plan["registrations"] = [registration]
        plan["sources"] = [{"item": "blun12", "policy": "relocate", "source_ids": [],
                            "registration_ids": [SOURCE_ID]}]
        plan["targets"] = [{"id": "sto:ambar01", "capacity": 1, "optional": False}]
        campaign = compile_campaign(profile, self.catalog, self.world, plan)
        selected = campaign["sources"][0]
        self.assertEqual(_validate_source(selected), 1)
        self.assertEqual(selected["record"]["kind"], "scripted")
        self.assertEqual(selected["record"]["record"], PAYLOAD)
        self.assertEqual(selected["resource_sha256"], ORIGINAL_SHA256)
        native, required, _ = _compile(campaign, self.world,
                                       validate_world(self.world, self.catalog),
                                       validate_catalog(self.catalog))
        endpoint = native["sources"][SOURCE_ID]
        self.assertEqual(endpoint["kind"], "guarded-script")
        self.assertEqual(endpoint["adapter_id"], "blpool-v1")
        self.assertEqual(endpoint["payload"], PAYLOAD)
        self.assertEqual(native["resource_hashes"]["BLPOOL.BCS"], PATCHED_SHA256)
        self.assertEqual(native["source_gate"]["source_ids"], [SOURCE_ID])
        self.assertIn("bcs:blpool", required)
        from flir_config.editor import Editor
        editor = Editor(self.catalog, self.world, Path(self.temp.name) / 'editor', profile, plan)
        rows = editor.get('/api/sources', {'item': 'blun12'})['records']
        self.assertEqual(rows[0]['registration'], registration)
        self.assertEqual(rows[0]['kind_label'], 'Quest reward')
        self.assertTrue(editor.post('/api/validate', editor.draft)['runtime_supported'])

    def test_unknown_registration_and_changed_manifest_fail(self):
        row = blpool_registration(self.world)
        row["copies"] = 2
        from flir_config.scripted_sources import validate_registration
        with self.assertRaisesRegex(ProfileError, "Unsupported"):
            validate_registration(row)
        registration = blpool_registration(self.world)
        source = {"id": SOURCE_ID, "quantity": 1,
                  "resource_sha256": ORIGINAL_SHA256,
                  "record": campaign_record(registration)}
        for change in ({"quantity": 2}, {"resource_sha256": "0" * 64},
                       {"replacement": {"resref": "blank"}}):
            changed = deepcopy(source)
            changed.update(change)
            with self.subTest(change=change), self.assertRaises(ProfileError):
                _validate_source(changed)
        changed = deepcopy(source)
        changed["record"]["selector"] = "another-grant"
        with self.assertRaisesRegex(ProfileError, "Unsupported"):
            _validate_source(changed)

    def test_export_writes_identical_override_and_revision_expected_copy(self):
        with tempfile.TemporaryDirectory(prefix="flir-blpool-") as folder:
            root = Path(folder)
            game, store, output = (root / name for name in ("game", "store", "export"))
            make_game(game, [("BLUN12", make_itm())])
            item = bytearray(make_itm())
            struct.pack_into("<I", item, 0x18, 0x4c)
            (game / "override" / "blun12.itm").write_bytes(item)
            (game / "override" / "blpool.bcs").write_bytes(self.original)
            (game / "override" / "shop.sto").write_bytes(make_sto([]))
            catalog = scan_game(game)
            world = scan_world(catalog)
            profile = edit_profile(new_profile(catalog, "blpool-test", "Pool test"),
                                   catalog, include=["blun12"])
            plan = new_binding_plan(profile, catalog, world)
            plan["registrations"] = [blpool_registration(world)]
            plan["sources"] = [{"item": "blun12", "policy": "relocate", "source_ids": [],
                                "registration_ids": [SOURCE_ID]}]
            plan["targets"] = [{"id": "sto:shop", "capacity": 1, "optional": False}]
            revision = write_campaign(profile, catalog, world, plan, store)["revision"]
            before = snapshot(game)
            export_native(store, catalog, world, output, revision)
            patched = patch_blpool(self.original)
            self.assertEqual((output / "script-overrides" / "BLPOOL.BCS").read_bytes(), patched)
            expected = (output / "campaign-store" / "revisions" /
                        f"{revision}.source-1.patched.bcs")
            self.assertEqual(expected.read_bytes(), patched)
            native = json.loads((output / "campaign-store" / "revisions" /
                                 f"{revision}.native.json").read_bytes())
            self.assertEqual(native["resource_hashes"]["BLPOOL.BCS"], PATCHED_SHA256)
            self.assertEqual(snapshot(game), before)
            # A later configuration export must work after our static patch
            # has been installed, without another WeiDU install or patch layer.
            (game / 'override/blpool.bcs').write_bytes(patched)
            installed = snapshot(game)
            export_native(store, catalog, world, output, revision)
            self.assertEqual(snapshot(game), installed)
            self.assertEqual(patch_blpool(patched), patched)
            patched_world = scan_world(catalog)
            patched_plan = new_binding_plan(profile, catalog, patched_world)
            patched_plan['registrations'] = [blpool_registration(patched_world)]
            patched_plan['sources'] = deepcopy(plan['sources'])
            patched_plan['targets'] = deepcopy(plan['targets'])
            next_campaign = compile_campaign(profile, catalog, patched_world, patched_plan)
            self.assertEqual(next_campaign['sources'][0]['resource_sha256'], PATCHED_SHA256)
            _compile(next_campaign, patched_world, validate_world(patched_world, catalog),
                     validate_catalog(catalog))


if __name__ == "__main__":
    unittest.main()
