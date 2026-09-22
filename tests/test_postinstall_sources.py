"""Source binding integration on temporary, synthetic installations only."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config.profiles import ProfileError, edit_profile, new_profile, write_json
from flir_config.resources import ScanError, scan_game
from flir_config import sources as source_module
from flir_config.sources import (bind_profile, inspect_sources, new_binding_plan,
    prepare_bound_profile, read_bound_profile, scan_world, search_targets,
    validate_world, world_fingerprint)
from test_postinstall_resources import make_game, make_itm, snapshot
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto
from test_postinstall_resource_reader import biff, key


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flir-source-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        make_game(self.game)
        self.override = self.game / "override"
        item = item_record(flags=1)
        self.payloads = {
            "actor.cre": make_cre([item, item_record(charges=(2, 9, 0), flags=4)]),
            "area.are": make_are([item], [("Chest", 0, 1)],
                                  [("External", "ACTOR"), ("Embedded", make_cre([item]))]),
            "shop.sto": make_sto([(item, 3, 0)]),
            "grant.bcs": b"SC\nsynthetic grant\nSC\n",
            "grant.dlg": b"DLG V1.0synthetic grant",
        }
        for name, data in self.payloads.items():
            (self.override / name).write_bytes(data)
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)
        self.profile = edit_profile(new_profile(self.catalog, "test", "Test adventure"), self.catalog, include=["synth001"])

    def plan(self, source_ids=None, capacity=1):
        plan = new_binding_plan(self.profile, self.catalog, self.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": source_ids or ["are:area/container/0/item/0"], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/1", "capacity": capacity, "optional": False}]
        return plan

    def rehash(self, world):
        world["fingerprint"] = world_fingerprint(world)
        return world

    def source(self, world, identity):
        return next(row for row in world["sources"] if row["id"] == identity)

    def test_scan_is_read_only_and_keeps_record_and_template_identities(self):
        before = snapshot(self.game)
        world = scan_world(self.catalog)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(world, self.world)
        self.assertEqual(len(world["sources"]), 5)
        self.assertEqual(len(world["targets"]), 5)
        # An external actor references its CRE: do not fabricate another item copy.
        self.assertFalse(any(row["owner"] == "are:area/actor/0" for row in world["sources"]))
        creature = self.source(world, "cre:actor/item/1")
        self.assertEqual(creature["record"]["charges"], [2, 9, 0])
        self.assertEqual(creature["record"]["flags"], 4)
        self.assertEqual(creature["record"]["expiration"], 13)
        self.assertIn("template-instance-scope", creature["concerns"])
        embedded = self.source(world, "are:area/actor/1/item/0")
        self.assertGreater(embedded["record"]["byte_offset"], 0x11C)

    def test_archive_and_override_world_scans_have_identical_semantics(self):
        rows = [("synth001", 1005, make_itm())]
        types = {"cre": 1009, "are": 1010, "sto": 1014, "bcs": 1007, "dlg": 1011}
        rows += [(Path(name).stem, types[Path(name).suffix[1:]], bytes(data)) for name, data in self.payloads.items()]
        records = [(name, kind, index * 3 + 2) for index, (name, kind, _) in enumerate(rows)]
        (self.game / "chitin.key").write_bytes(key(records))
        (self.game / "data" / "world.bif").write_bytes(biff([(locator, kind, rows[index][2]) for index, (_, kind, locator) in enumerate(records)]))
        for name in self.payloads:
            (self.override / name).unlink()
        archived = scan_world(self.catalog)
        self.assertEqual(archived, self.world)
        (self.override / "actor.cre").write_bytes(make_cre([item_record(charges=(99, 0, 0), flags=1)]))
        changed = scan_world(self.catalog)
        self.assertNotEqual(changed["fingerprint"], archived["fingerprint"])
        self.assertEqual(self.source(changed, "cre:actor/item/0")["record"]["charges"], [99, 0, 0])

    def test_item_catalogue_must_match_effective_items(self):
        (self.override / "synth001.itm").write_bytes(make_itm(price=999))
        with self.assertRaisesRegex(ScanError, "item bytes changed"):
            scan_world(self.catalog)
        (self.override / "synth001.itm").unlink()
        (self.override / "extra.itm").write_bytes(make_itm())
        with self.assertRaisesRegex(ScanError, "Installed items changed"):
            scan_world(self.catalog)

    def test_script_change_changes_world_fingerprint(self):
        (self.override / "grant.bcs").write_bytes(b"SC\nchanged grant\nSC")
        changed = scan_world(self.catalog)
        self.assertNotEqual(changed["fingerprint"], self.world["fingerprint"])
        with self.assertRaisesRegex(ProfileError, "stale"):
            bind_profile(self.profile, self.catalog, changed, self.plan())

    def test_concurrent_change_during_parse_rejects_whole_world_scan(self):
        original = source_module.parse_sto
        def parse_then_change(data):
            result = original(data)
            (self.override / "grant.bcs").write_bytes(b"changed during caller parsing")
            return result
        with patch.object(source_module, "parse_sto", side_effect=parse_then_change):
            with self.assertRaisesRegex(ScanError, "changed.*scanning"):
                scan_world(self.catalog)

    def test_bounded_source_and_target_discovery_does_not_claim_guarantees(self):
        found = inspect_sources(self.world, self.catalog, "SYNTH001", limit=2)
        self.assertEqual((found["total"], found["next_offset"]), (5, 2))
        self.assertEqual(found["discovery"], "multiple-records")
        self.assertEqual(found["runtime_availability"], "unverified")
        self.assertEqual(search_targets(self.world, self.catalog, "embedded")["total"], 1)
        self.assertEqual(search_targets(self.world, self.catalog, "", kind="actor")["total"], 2)
        with self.assertRaises(ProfileError):
            search_targets(self.world, self.catalog, "")
        with self.assertRaises(ProfileError):
            inspect_sources(self.world, self.catalog, "synth001", limit=101)

    def test_absent_single_and_multiple_record_discovery(self):
        for name in self.payloads:
            (self.override / name).unlink()
        world = scan_world(self.catalog)
        self.assertEqual(inspect_sources(world, self.catalog, "synth001")["discovery"], "absent")
        (self.override / "actor.cre").write_bytes(make_cre([item_record()]))
        world = scan_world(self.catalog)
        self.assertEqual(inspect_sources(world, self.catalog, "synth001")["discovery"], "single-record")

    def test_bind_preserves_exact_records_and_does_not_mutate_inputs(self):
        plan = self.plan(["cre:actor/item/0", "cre:actor/item/1"], capacity=2)
        before = deepcopy((self.profile, self.catalog, self.world, plan))
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual((self.profile, self.catalog, self.world, plan), before)
        self.assertEqual((bound["reward_units"], bound["target_capacity"]), (2, 2))
        self.assertEqual(bound["state"], "draft-needs-runtime-adapter")
        self.assertIn("nonoverlapping-runtime-selectors", bound["runtime_requirements"])
        self.assertEqual(bound["sources"][1]["source"]["record"]["charges"], [2, 9, 0])

    def test_no_automatic_source_or_target_choices(self):
        blank = new_binding_plan(self.profile, self.catalog, self.world)
        self.assertEqual(blank["sources"], [])
        self.assertEqual(blank["targets"], [])
        with self.assertRaisesRegex(ProfileError, "explicit sources"):
            bind_profile(self.profile, self.catalog, self.world, blank)
        plan = self.plan()
        plan["sources"][0]["source_ids"] = []
        with self.assertRaisesRegex(ProfileError, "explicit physical"):
            bind_profile(self.profile, self.catalog, self.world, plan)

    def test_addition_requires_explicit_policy_and_keeps_originals_out_of_relocation(self):
        plan = self.plan(capacity=2)
        plan["sources"] = [{"item": "synth001", "policy": "add", "copies": 2}]
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual(bound["sources"], [])
        self.assertEqual(bound["additions"][0]["copies"], 2)
        self.assertEqual(bound["reward_units"], 2)

    def test_store_stock_capacity_counts_each_copy_but_keeps_stack_charges(self):
        plan = self.plan(["sto:shop/item/0"], capacity=2)
        with self.assertRaisesRegex(ProfileError, "cannot hold 3"):
            bind_profile(self.profile, self.catalog, self.world, plan)
        plan["targets"][0]["capacity"] = 3
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual(bound["sources"][0]["source"]["record"]["charges"], [7, 3, 2])
        self.assertEqual(bound["reward_units"], 3)

    def test_infinite_and_empty_stock_cannot_be_relocated(self):
        for quantity, infinite in ((0, 1), (1, 1), (0, 0), (1, 2)):
            (self.override / "shop.sto").write_bytes(make_sto([(item_record(), quantity, infinite)]))
            self.world = scan_world(self.catalog)
            with self.assertRaisesRegex(ProfileError, "finite, unambiguous"):
                bind_profile(self.profile, self.catalog, self.world, self.plan(["sto:shop/item/0"]))

    def test_shared_unslotted_and_orphan_records_are_visible_but_not_relocatable(self):
        (self.override / "actor.cre").write_bytes(make_cre([item_record(), item_record()], slots={0: 0, 21: 0}))
        (self.override / "area.are").write_bytes(make_are([item_record(), item_record()], [("A", 0, 1), ("B", 0, 1)]))
        self.world = scan_world(self.catalog)
        for identity in ("cre:actor/item/0", "cre:actor/item/1", "are:area/unresolved/item/0", "are:area/unresolved/item/1"):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(ProfileError, "finite, unambiguous"):
                    bind_profile(self.profile, self.catalog, self.world, self.plan([identity]))

    def test_dynamic_container_is_marked_and_missing_definitions_are_visible(self):
        (self.override / "area.are").write_bytes(make_are([item_record(), item_record("UNKNOWN")], [("T2TEST", 0, 2)]))
        world = scan_world(self.catalog)
        first = self.source(world, "are:area/container/0/item/0")
        self.assertIn("dynamic-container-items", first["concerns"])
        self.assertIn("missing-item-definition", self.source(world, "are:area/container/0/item/1")["concerns"])

    def test_optional_targets_resolve_before_capacity_validation(self):
        plan = self.plan()
        plan["targets"].append({"id": "cre:optional", "capacity": 2, "optional": True})
        result = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual(result["target_capacity"], 1)
        self.assertEqual(result["skipped"][0]["id"], "cre:optional")
        plan["targets"] = plan["targets"][1:]
        with self.assertRaisesRegex(ProfileError, "capacity 0"):
            bind_profile(self.profile, self.catalog, self.world, plan)
        plan["targets"][0]["optional"] = False
        with self.assertRaisesRegex(ProfileError, "Required target"):
            bind_profile(self.profile, self.catalog, self.world, plan)

    def test_missing_external_creature_is_not_a_compatible_target(self):
        (self.override / "area.are").write_bytes(make_are([], [], [("Missing", "MISSING")]))
        self.world = scan_world(self.catalog)
        plan = self.plan(["cre:actor/item/0"])
        plan["targets"][0]["id"] = "are:area/actor/0"
        with self.assertRaisesRegex(ProfileError, "missing creature"):
            bind_profile(self.profile, self.catalog, self.world, plan)

    def registration(self, optional=False, resource="bcs:grant"):
        return {"id": "grant-one", "item": "synth001", "resource": resource,
                "resource_sha256": hashlib.sha256(self.payloads["grant.bcs"]).hexdigest(),
                "adapter_id": "synthetic-grant-v1", "selector": "block:0/action:0", "copies": 1, "optional": optional}

    def test_script_registration_is_hash_anchored_and_remains_unverified(self):
        plan = self.plan()
        plan["sources"][0]["source_ids"] = []
        plan["sources"][0]["registration_ids"] = ["grant-one"]
        plan["registrations"] = [self.registration()]
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual(bound["sources"][0]["availability"], "unverified")
        plan["registrations"][0]["resource_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProfileError, "adapter needs review"):
            bind_profile(self.profile, self.catalog, self.world, plan)

    def test_optional_registered_source_does_not_create_unearned_reward(self):
        plan = self.plan()
        plan["sources"][0]["registration_ids"] = ["grant-one"]
        plan["registrations"] = [self.registration(True, "bcs:absent")]
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual(bound["reward_units"], 1)
        self.assertEqual(bound["skipped"][0]["kind"], "source")
        plan["sources"][0]["source_ids"] = []
        with self.assertRaisesRegex(ProfileError, "No available reward"):
            bind_profile(self.profile, self.catalog, self.world, plan)

    def test_duplicate_grants_targets_and_sources_fail(self):
        for location in ("sources", "targets", "registration"):
            plan = self.plan()
            if location == "sources":
                plan["sources"][0]["source_ids"] *= 2
            elif location == "targets":
                plan["targets"] *= 2
            else:
                first = self.registration()
                second = dict(first, id="same-grant")
                plan["registrations"] = [first, second]
            with self.subTest(location=location), self.assertRaises(ProfileError):
                bind_profile(self.profile, self.catalog, self.world, plan)

    def test_bound_revisions_are_immutable_and_earlier_choices_survive(self):
        before = snapshot(self.game)
        store = self.root / "drafts"
        first = prepare_bound_profile(self.profile, self.catalog, self.world, self.plan(), store)
        first_body = read_bound_profile(store)
        second = prepare_bound_profile(self.profile, self.catalog, self.world, self.plan(["cre:actor/item/1"]), store)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(read_bound_profile(store, first["revision"]), first_body)
        self.assertEqual(prepare_bound_profile(self.profile, self.catalog, self.world, self.plan(), store), first)
        path = Path(first["revision_path"])
        body = json.loads(path.read_text())
        body["reward_units"] = 999
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ProfileError, "modified"):
            read_bound_profile(store)
        with self.assertRaisesRegex(ProfileError, "conflicting"):
            prepare_bound_profile(self.profile, self.catalog, self.world, self.plan(), store)
        with self.assertRaisesRegex(ProfileError, "outside"):
            prepare_bound_profile(self.profile, self.catalog, self.world, self.plan(), self.game / "drafts")
        self.assertEqual(snapshot(self.game), before)

    def test_imported_world_record_types_and_recomputed_hash_do_not_bypass_validation(self):
        for field, value in (("owner", None), ("kind", []), ("item", "UPPER")):
            changed = deepcopy(self.world)
            self.source(changed, "sto:shop/item/0")[field] = value
            with self.subTest(field=field), self.assertRaises(ProfileError):
                validate_world(self.rehash(changed), self.catalog)
        for field, value in (("quantity", 0), ("infinite_supply_flag", 1), ("infinite_supply", True), ("charges", [1])):
            changed = deepcopy(self.world)
            self.source(changed, "sto:shop/item/0")["record"][field] = value
            with self.subTest(field=field), self.assertRaises(ProfileError):
                validate_world(self.rehash(changed), self.catalog)

    def test_alternate_identity_or_index_cannot_duplicate_physical_bytes(self):
        for forgery in ("owner", "index"):
            changed = deepcopy(self.world)
            duplicate = deepcopy(self.source(changed, "sto:shop/item/0"))
            if forgery == "owner":
                duplicate["owner"], duplicate["id"] = "sto:shop2", "sto:shop2/item/0"
            else:
                duplicate["record"]["record_index"] = 1
                duplicate["id"] = "sto:shop/item/1"
            changed["sources"].append(duplicate)
            changed["sources"].sort(key=lambda row: row["id"])
            with self.subTest(forgery=forgery), self.assertRaises(ProfileError):
                validate_world(self.rehash(changed), self.catalog)

    def test_distinct_creature_records_cannot_share_the_same_slot(self):
        changed = deepcopy(self.world)
        self.source(changed, "cre:actor/item/1")["record"]["slots"] = [21]
        with self.assertRaisesRegex(ProfileError, "same creature slot"):
            validate_world(self.rehash(changed), self.catalog)

    def test_container_ownership_is_validated_in_both_directions(self):
        changed = deepcopy(self.world)
        row = self.source(changed, "are:area/container/0/item/0")
        row["record"]["container_indexes"] = []
        row["record"]["container_index"] = None
        row["owner"], row["id"] = "are:area/unresolved", "are:area/unresolved/item/0"
        row["concerns"] = ["unowned-item-record"]
        changed["sources"].sort(key=lambda entry: entry["id"])
        with self.assertRaisesRegex(ProfileError, "owner range disagrees"):
            validate_world(self.rehash(changed), self.catalog)

    def test_changed_world_without_rehash_fails(self):
        changed = deepcopy(self.world)
        changed["resources"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ProfileError, "content changed"):
            validate_world(changed, self.catalog)

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "randomiser_config.py"), *map(str, args)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout if success else result.stderr)

    def test_command_workflow_and_structured_failures(self):
        catalog_path, world_path = self.root / "catalog.json", self.root / "world.json"
        profile_path, plan_path = self.root / "profile.json", self.root / "plan.json"
        write_json(catalog_path, self.catalog)
        write_json(profile_path, self.profile)
        self.cli("world", "scan", "--catalog", catalog_path, "--out", world_path)
        self.cli("world", "sources", "--catalog", catalog_path, "--world", world_path, "--item", "synth001", "--limit", "1")
        self.cli("world", "targets", "--catalog", catalog_path, "--world", world_path, "--query", "embedded")
        args = ["--catalog", catalog_path, "--world", world_path, "--profile", profile_path]
        self.cli("binding", "create", *args, "--out", plan_path)
        self.cli("binding", "validate", *args, "--plan", plan_path, success=False)
        write_json(plan_path, self.plan())
        self.cli("binding", "validate", *args, "--plan", plan_path)
        result = self.cli("binding", "prepare", *args, "--plan", plan_path, "--store", self.root / "drafts")
        inspected = self.cli("binding", "inspect", "--store", self.root / "drafts")
        self.assertEqual(inspected["result"]["fingerprint"], result["result"]["revision"])
        changed = deepcopy(self.world)
        self.source(changed, "sto:shop/item/0")["owner"] = None
        write_json(world_path, self.rehash(changed))
        self.cli("binding", "validate", *args, "--plan", plan_path, success=False)


if __name__ == "__main__":
    unittest.main()
