"""BLPOOL as CIR prepares it; no installed game or ignored work files."""

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
from flir_config.scripted_sources import (PAYLOAD, SOURCE_ID, blpool_registration,
                                          campaign_record, validate_registration)
from flir_config.sources import new_binding_plan, scan_world, validate_world
from test_postinstall_resources import make_game, make_itm, snapshot
from test_postinstall_world_formats import make_sto

CHANGES = ("quest_pool", "quest_altar", "quest_book", "unshey", "flail_heads", "totsc_tome")


def write_registry(game, **statuses):
    """override/FLPRPAT.lua as CIR writes it; unnamed changes are applied."""
    lines = [f'    {name} = {{ status = "{statuses.get(name, "applied")}" }},' for name in CHANGES]
    text = "FLPR_PATCHES = {\n    version = 1,\n" + "\n".join(lines) + "\n}\n"
    (game / "override" / "FLPRPAT.lua").write_text(text, encoding="utf-8")


def compiled_blpool(prepared=True):
    # The original script's four small blocks, in the engine's compiled text
    # format. Objects 1/17 are Myself/LastTrigger. Prepared, the reward block
    # is split as CIR splits it: the selected copy reads FLPR_QPOOL,
    # gives nothing and sets FLPR_XPOOL; both copies set FLPR_EPOOL.
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
    grant=action(140,string='BLUN12',target=17); bite=action(181,2020,target=17)
    reward=[block(clicked+close+pool(1),display(35877)+grant+bite+advance(2))]
    if prepared:
        guard=lambda negate: trigger(16399,1,negate,'GLOBALFLPR_QPOOL')
        event=action(30,1,'GLOBALFLPR_EPOOL')
        reward=[block(clicked+close+pool(1)+guard(0),
                      display(35878)+action(30,1,'GLOBALFLPR_XPOOL')+bite+advance(2)+event),
                block(clicked+close+pool(1)+guard(1),display(35877)+grant+bite+advance(2)+event)]
    return ('SC\n'+block(clicked+close+pool(0),display(35875)+advance(1))+''.join(reward)+
            block(clicked+close+pool(2),display(35878))+
            block(clicked+trigger(16408,10,1,target=17),display(14702))+'SC\n').encode('ascii')


def pool_game(game, script, **statuses):
    make_game(game, [("BLUN12", make_itm())])
    item = bytearray(make_itm()); struct.pack_into('<I', item, 0x18, 0x4c)
    (game / 'override/blun12.itm').write_bytes(item)
    (game / 'override/blpool.bcs').write_bytes(script)
    (game / 'override/shop.sto').write_bytes(make_sto([]))
    if statuses.get("registry", True):
        write_registry(game, **{key: value for key, value in statuses.items() if key != "registry"})
    catalog = scan_game(game)
    return catalog, scan_world(catalog)


def pool_plan(catalog, world):
    profile = edit_profile(new_profile(catalog, "blpool-test", "Pool test"), catalog, include=["blun12"])
    plan = new_binding_plan(profile, catalog, world)
    plan["registrations"] = [blpool_registration(world)]
    plan["sources"] = [{"item": "blun12", "policy": "relocate", "source_ids": [],
                        "registration_ids": [SOURCE_ID]}]
    plan["targets"] = [{"id": "sto:shop", "capacity": 1, "optional": False}]
    return profile, plan


class BlpoolScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flir-blpool-source-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prepared = compiled_blpool()
        self.catalog, self.world = pool_game(self.root / 'game', self.prepared)

    def test_registration_follows_cir_record(self):
        registration = blpool_registration(self.world)
        self.assertEqual(registration["resource_sha256"], hashlib.sha256(self.prepared).hexdigest())
        validate_registration(registration)
        for name, statuses in (("missing", {"registry": False}),
                               ("left-out", {"quest_pool": "grant-ambiguous"})):
            catalog, world = pool_game(self.root / name, self.prepared, **statuses)
            with self.subTest(name), self.assertRaisesRegex(ProfileError, "did not prepare BLPOOL"):
                blpool_registration(world)
            from flir_config.editor import Editor
            profile = new_profile(catalog, "pool", "Pool")
            editor = Editor(catalog, world, self.root / (name + "-editor"), profile,
                            new_binding_plan(profile, catalog, world))
            rows = editor.get('/api/sources', {'item': 'blun12'})['records']
            self.assertFalse(any(row['kind'] == 'scripted' for row in rows))

    def test_registration_compiles_one_script_source_and_native_gate(self):
        profile, plan = pool_plan(self.catalog, self.world)
        campaign = compile_campaign(profile, self.catalog, self.world, plan)
        selected = campaign["sources"][0]
        self.assertEqual(_validate_source(selected), 1)
        self.assertEqual(selected["record"]["kind"], "scripted")
        self.assertEqual(selected["record"]["record"], PAYLOAD)
        self.assertEqual(selected["resource_sha256"], hashlib.sha256(self.prepared).hexdigest())
        native, required, _ = _compile(campaign, self.world,
                                       validate_world(self.world, self.catalog),
                                       validate_catalog(self.catalog), catalog=self.catalog)
        endpoint = native["sources"][SOURCE_ID]
        self.assertEqual(endpoint["kind"], "guarded-script")
        self.assertEqual(endpoint["adapter_id"], "blpool-v1")
        self.assertEqual(endpoint["payload"], PAYLOAD)
        self.assertEqual(native["resource_hashes"]["BLPOOL.BCS"], hashlib.sha256(self.prepared).hexdigest())
        self.assertEqual(native["source_gate"]["source_ids"], [SOURCE_ID])
        self.assertIn("bcs:blpool", required)
        from flir_config.editor import Editor
        editor = Editor(self.catalog, self.world, self.root / 'editor', profile, plan)
        rows = editor.get('/api/sources', {'item': 'blun12'})['records']
        self.assertEqual(rows[0]['registration'], plan["registrations"][0])
        self.assertEqual(rows[0]['kind_label'], 'Quest reward')
        self.assertTrue(editor.post('/api/validate', editor.draft)['runtime_supported'])

    def test_unknown_registration_and_changed_manifest_fail(self):
        row = blpool_registration(self.world)
        row["copies"] = 2
        with self.assertRaisesRegex(ProfileError, "Unsupported"):
            validate_registration(row)
        registration = blpool_registration(self.world)
        source = {"id": SOURCE_ID, "quantity": 1,
                  "resource_sha256": registration["resource_sha256"],
                  "record": campaign_record(registration)}
        for change in ({"quantity": 2}, {"resource_sha256": "not a fingerprint"},
                       {"replacement": {"resref": "blank"}}):
            changed = deepcopy(source)
            changed.update(change)
            with self.subTest(change=change), self.assertRaises(ProfileError):
                _validate_source(changed)
        changed = deepcopy(source)
        changed["record"]["selector"] = "another-grant"
        with self.assertRaisesRegex(ProfileError, "Unsupported"):
            _validate_source(changed)

    def test_export_reads_the_installed_script_and_never_writes_it(self):
        game, store, output = self.root / 'game', self.root / 'store', self.root / 'export'
        profile, plan = pool_plan(self.catalog, self.world)
        revision = write_campaign(profile, self.catalog, self.world, plan, store)["revision"]
        before = snapshot(game)
        export_native(store, self.catalog, self.world, output, revision)
        self.assertEqual(snapshot(game), before)
        self.assertFalse(any(path.name.lower() == "blpool.bcs" for path in output.rglob("*")))
        expected = output / "campaign-store" / "revisions" / f"{revision}.source-1.patched.bcs"
        self.assertEqual(expected.read_bytes(), self.prepared)
        native = json.loads((output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        self.assertEqual(native["resource_hashes"]["BLPOOL.BCS"], hashlib.sha256(self.prepared).hexdigest())
        listed = json.loads((output / "export-files.json").read_bytes())["files"]
        self.assertTrue(all(not name.startswith(("resource-overrides/", "game-overrides/"))
                            for name in listed))

    def test_export_refuses_a_script_without_the_installed_change(self):
        game, store = self.root / 'game', self.root / 'store'
        profile, plan = pool_plan(self.catalog, self.world)
        revision = write_campaign(profile, self.catalog, self.world, plan, store)["revision"]
        # Another mod replaced BLPOOL after the scan.
        (game / 'override/blpool.bcs').write_bytes(compiled_blpool(prepared=False))
        with self.assertRaisesRegex(ProfileError, "rescan before export: bcs:blpool"):
            export_native(store, self.catalog, self.world, self.root / 'export-a', revision)
        # A record that no longer lists the change.
        (game / 'override/blpool.bcs').write_bytes(self.prepared)
        write_registry(game, quest_pool="prepared-elsewhere")
        with self.assertRaisesRegex(ProfileError, "did not prepare BLPOOL"):
            export_native(store, self.catalog, self.world, self.root / 'export-b', revision)
        # Another mod replaced BLPOOL after CIR, before the scan.
        replaced = self.root / 'replaced'
        catalog, world = pool_game(replaced, compiled_blpool(prepared=False))
        profile, plan = pool_plan(catalog, world)
        revision = write_campaign(profile, catalog, world, plan, self.root / 'store-c')["revision"]
        with self.assertRaisesRegex(ProfileError, "replaced after Chriz Item Randomiser"):
            export_native(self.root / 'store-c', catalog, world, self.root / 'export-c', revision)
        self.assertEqual((replaced / 'override/blpool.bcs').read_bytes(), compiled_blpool(prepared=False))


if __name__ == "__main__":
    unittest.main()
