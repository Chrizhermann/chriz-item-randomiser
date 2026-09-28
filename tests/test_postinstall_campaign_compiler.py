"""Hermetic campaign compiler tests; no installed game or runtime activation."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import campaigns
from flir_config.campaigns import compile_campaign, read_campaign, write_campaign
from flir_config.profiles import (ProfileError, _digest, canonical_bytes,
                                 edit_profile, new_profile)
from flir_config.resources import scan_game
from flir_config.sources import bind_profile, new_binding_plan, scan_world, world_fingerprint
from test_postinstall_resources import make_game, snapshot
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto


class CampaignCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flir-campaign-compiler-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.store = self.root / "campaigns"
        make_game(self.game)
        override = self.game / "override"
        (override / "actor.cre").write_bytes(make_cre([
            item_record(flags=1), item_record(charges=(2, 9, 0), flags=4)]))
        (override / "area.are").write_bytes(make_are(
            [item_record(flags=1)], [("Chest", 0, 1)],
            [("External", "ACTOR"), ("Embedded", make_cre([item_record(flags=1)]))]))
        (override / "shop.sto").write_bytes(make_sto([(item_record(flags=1), 3, 0)]))
        (override / "grant.bcs").write_bytes(b"SC\nsynthetic grant\nSC\n")
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)
        self.profile = edit_profile(new_profile(self.catalog, "example", "Example adventure"),
                                    self.catalog, include=["synth001"])

    def plan(self, sources=None, capacity=1):
        plan = new_binding_plan(self.profile, self.catalog, self.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
            "source_ids": sources or ["are:area/container/0/item/0"], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/1", "capacity": capacity, "optional": False}]
        return plan

    def compile(self, plan=None):
        return compile_campaign(self.profile, self.catalog, self.world, plan or self.plan())

    def write(self, plan=None, store=None):
        return write_campaign(self.profile, self.catalog, self.world, plan or self.plan(),
                              store or self.store)

    def rehash(self, body):
        body["revision"] = _digest({key: value for key, value in body.items() if key != "revision"})
        return body

    def replace_artifact(self, body):
        self.rehash(body)
        revision = body["revision"]
        (self.store / "revisions" / (revision + ".json")).write_bytes(canonical_bytes(body))
        (self.store / "revisions" / (revision + ".lua")).write_bytes(campaigns._render_lua(body))
        return revision

    def test_compiles_exact_records_quantities_and_all_four_physical_source_kinds(self):
        sources = ["sto:shop/item/0", "cre:actor/item/1", "are:area/actor/1/item/0",
                   "are:area/container/0/item/0"]
        plan = self.plan(sources, capacity=6)
        before = deepcopy((self.profile, self.catalog, self.world, plan))
        game_before = snapshot(self.game)
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        body = self.compile(plan)
        self.assertEqual((self.profile, self.catalog, self.world, plan), before)

        self.assertEqual(snapshot(self.game), game_before)
        self.assertEqual(body["schema"], "flir-campaign-prototype-v1")
        self.assertEqual(body["state"], "prototype-not-playable")
        self.assertEqual(body["bound_revision"], bound["fingerprint"])
        self.assertEqual(body["world_fingerprint"], self.world["fingerprint"])
        self.assertEqual(body["runtime_requirements"], bound["runtime_requirements"])
        self.assertEqual([row["id"] for row in body["sources"]], sorted(sources))
        by_id = {row["id"]: row for row in self.world["sources"]}
        for row in body["sources"]:
            self.assertEqual(row["record"], by_id[row["id"]])
        self.assertEqual(sum(row["quantity"] for row in body["sources"]), 6)
        creature = next(row for row in body["sources"] if row["id"] == "cre:actor/item/1")
        self.assertEqual(creature["record"]["record"]["charges"], [2, 9, 0])
        self.assertEqual(creature["record"]["record"]["flags"], 4)
        self.assertEqual(creature["record"]["record"]["expiration"], 13)
        self.assertNotIn("assignments", body)
        body["sources"][0]["record"]["record"]["charges"][0] = 123
        self.assertEqual((self.profile, self.catalog, self.world, plan), before)

    def test_shop_pricing_is_frozen_per_revision_and_legacy_campaign_reads(self):
        first = self.compile()
        self.assertEqual(first["shop_pricing"], {"percent": 120, "base_prices": {}})
        self.profile = edit_profile(self.profile, self.catalog,
                                    shop_price_percent=150,
                                    shop_base_prices={"SYNTH001": 250})
        second = self.compile()
        self.assertEqual(second["shop_pricing"],
                         {"percent": 150, "base_prices": {"synth001": 250}})
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(first["shop_pricing"], {"percent": 120, "base_prices": {}})
        legacy_profile = deepcopy(self.profile)
        del legacy_profile["shop_pricing"]
        legacy = compile_campaign(legacy_profile, self.catalog, self.world, self.plan())
        del legacy["shop_pricing"]
        self.store.mkdir(parents=True)
        (self.store / "revisions").mkdir()
        old_revision = self.replace_artifact(legacy)
        self.assertNotIn("shop_pricing", read_campaign(self.store, old_revision))
        tampered = deepcopy(second)
        tampered["shop_pricing"]["base_prices"] = {"absent": 1}
        self.rehash(tampered)
        with self.assertRaisesRegex(ProfileError, "selected"):
            campaigns._validate_campaign(tampered)

    def test_all_target_kinds_and_multiple_capacities_are_preserved_in_sorted_order(self):
        plan = self.plan(["sto:shop/item/0"], capacity=3)
        plan["targets"] = [{"id": identity, "capacity": amount, "optional": False}
            for identity, amount in [("sto:shop", 2), ("cre:actor", 1),
                                     ("are:area/container/0", 4), ("are:area/actor/0", 2)]]
        body = self.compile(plan)
        self.assertEqual([row["id"] for row in body["targets"]], sorted(row["id"] for row in plan["targets"]))
        by_id = {row["id"]: row for row in self.world["targets"]}
        for row in body["targets"]:
            self.assertEqual(row["record"], by_id[row["id"]])
        self.assertEqual(sum(row["capacity"] for row in body["targets"]), 9)

    def test_randomization_roundtrips_with_tier_slots_and_exclusion(self):
        plan = self.plan(["are:area/container/0/item/0", "cre:actor/item/1"], capacity=2)
        target = "are:area/actor/1"
        plan["randomization"] = {
            "sources": {
                "are:area/container/0/item/0": {
                    "tier": "low", "loss_exempt": False,
                    "exclude_targets": [{"id": target, "ordinal": 1}]},
                "cre:actor/item/1": {
                    "tier": "low", "loss_exempt": True, "exclude_targets": []}},
            "targets": {target: ["low", "low"]},
            "loss_percent": 25, "unrandomized_percent": 40}
        old = self.compile(self.plan(["are:area/container/0/item/0", "cre:actor/item/1"], capacity=2))
        bound = bind_profile(self.profile, self.catalog, self.world, plan)
        self.assertEqual(bound["plan"]["randomization"], plan["randomization"])
        result = self.write(plan)
        campaign = read_campaign(self.store, result["revision"])
        self.assertEqual(campaign["randomization"], plan["randomization"])
        self.assertEqual(campaign["bound_revision"], bound["fingerprint"])
        self.assertNotEqual(campaign["revision"], old["revision"])
        self.assertNotIn("randomization", old)

    def test_randomization_rejects_per_tier_shortage_despite_total_capacity(self):
        plan = self.plan(["are:area/container/0/item/0", "cre:actor/item/1"], capacity=2)
        plan["randomization"] = {
            "sources": {identity: {"tier": "low", "loss_exempt": False,
                                   "exclude_targets": []}
                        for identity in plan["sources"][0]["source_ids"]},
            "targets": {"are:area/actor/1": ["low", "high"]},
            "loss_percent": 0, "unrandomized_percent": 0}
        with self.assertRaisesRegex(ProfileError, "tier capacity is short"):
            self.compile(plan)

    def test_mixed_keep_exclusions_need_spare_capacity_before_publication(self):
        sources = ["are:area/container/0/item/0", "cre:actor/item/1"]
        plan = self.plan(sources, capacity=2)
        target = plan["targets"][0]["id"]
        plan["randomization"] = {
            "sources": {
                sources[0]: {"tier": "low", "loss_exempt": False,
                             "exclude_targets": [{"id": target, "ordinal": 1},
                                                 {"id": target, "ordinal": 2}]},
                sources[1]: {"tier": "low", "loss_exempt": False,
                             "exclude_targets": []}},
            "targets": {target: ["low", "low"]},
            "loss_percent": 0, "unrandomized_percent": 50}
        with self.assertRaisesRegex(ProfileError, "needs more target capacity"):
            self.write(plan)
        self.assertFalse(self.store.exists())

        spare = deepcopy(plan)
        spare["targets"][0]["capacity"] = 3
        spare["randomization"]["targets"][target].append("low")
        self.assertEqual(self.compile(spare)["randomization"], spare["randomization"])

        all_kept = deepcopy(plan)
        all_kept["randomization"]["unrandomized_percent"] = 100
        self.assertEqual(self.compile(all_kept)["randomization"], all_kept["randomization"])

    def test_randomization_rejects_invalid_references_and_rehashed_drift(self):
        plan = self.plan()
        source, target = plan["sources"][0]["source_ids"][0], plan["targets"][0]["id"]
        plan["randomization"] = {
            "sources": {source: {"tier": "low", "loss_exempt": False,
                                "exclude_targets": [{"id": target, "ordinal": 1}]}},
            "targets": {target: ["low"]},
            "loss_percent": 0, "unrandomized_percent": 0}
        good = self.compile(plan)
        invalid = deepcopy(plan)
        invalid["randomization"]["sources"][source]["exclude_targets"] = [
            {"id": "are:area/actor/absent", "ordinal": 1}]
        with self.assertRaisesRegex(ProfileError, "distinct selected targets"):
            self.compile(invalid)
        drifted = deepcopy(good)
        drifted["randomization"]["targets"][target] = ["high"]
        self.rehash(drifted)
        with self.assertRaisesRegex(ProfileError, "tier capacity is short"):
            campaigns._validate_campaign(drifted)

    def test_rejects_additions_and_registered_sources_including_skipped_optional(self):
        plan = self.plan()
        plan["sources"] = [{"item": "synth001", "policy": "add", "copies": 1}]
        with self.assertRaisesRegex(ProfileError, "additional item copies"):
            self.compile(plan)
        for optional in (False, True):
            plan = self.plan(capacity=2)
            plan["sources"][0]["registration_ids"] = ["registered-one"]
            plan["registrations"] = [{"id": "registered-one", "item": "synth001",
                "resource": "bcs:absent" if optional else "bcs:grant",
                "resource_sha256": hashlib.sha256(b"SC\nsynthetic grant\nSC\n").hexdigest(),
                "adapter_id": "example-v1", "selector": "block:0/action:0",
                "copies": 1, "optional": optional}]
            with self.subTest(optional=optional), self.assertRaisesRegex(ProfileError, "registered script"):
                self.compile(plan)

    def test_scan_and_plan_staleness_are_not_bypassed(self):
        plan = self.plan()
        plan["world_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(ProfileError, "stale"):
            self.compile(plan)
        self.profile["catalog_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(ProfileError, "another scan"):
            self.compile()

    def test_source_reward_unit_limit_counts_store_stock_and_never_truncates(self):
        for stock, valid in ((4096, True), (4097, False)):
            (self.game / "override" / "shop.sto").write_bytes(make_sto([(item_record(flags=1), stock, 0)]))
            self.world = scan_world(self.catalog)
            plan = self.plan(["sto:shop/item/0"], capacity=stock)
            if valid:
                body = self.compile(plan)
                self.assertEqual(body["sources"][0]["quantity"], 4096)
            else:
                with self.assertRaisesRegex(ProfileError, "reward units.*4096"):
                    self.compile(plan)

    def test_total_target_capacity_limit_and_absent_oversized_choice_are_rejected(self):
        for choices in ([{"id": "cre:actor", "capacity": 4097, "optional": False}],
                        [{"id": "cre:actor", "capacity": 4096, "optional": False},
                         {"id": "sto:shop", "capacity": 1, "optional": False}],
                        [{"id": "cre:actor", "capacity": 1, "optional": False},
                         {"id": "cre:absent", "capacity": 4097, "optional": True}],
                        [{"id": "cre:actor", "capacity": 1, "optional": False},
                         {"id": "cre:absent", "capacity": 4096, "optional": True}]):
            plan = self.plan()
            plan["targets"] = choices
            with self.subTest(choices=choices), self.assertRaisesRegex(ProfileError, "target capacity.*4096"):
                self.compile(plan)

    def test_optional_absent_target_is_resolved_without_phantom_capacity(self):
        plan = self.plan()
        plan["targets"].append({"id": "cre:absent", "capacity": 1, "optional": True})
        body = self.compile(plan)
        self.assertEqual(len(body["targets"]), 1)
        self.assertEqual(body["targets"][0]["capacity"], 1)

    def test_publishes_matching_immutable_pair_and_preserves_earlier_revision(self):
        game_before = snapshot(self.game)
        first = self.write()
        first_body = read_campaign(self.store)
        self.assertEqual(first_body, self.compile())
        self.assertEqual(self.write(), first)
        second = self.write(self.plan(["cre:actor/item/1"]))
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(read_campaign(self.store, first["revision"]), first_body)
        self.assertEqual(read_campaign(self.store)["revision"], second["revision"])
        self.assertEqual(Path(first["lua_path"]).read_bytes(), campaigns._render_lua(first_body))
        self.assertEqual(snapshot(self.game), game_before)
        self.assertEqual(set(self.store.iterdir()), {self.store / "revisions", self.store / "prototype-next.json"})

    def test_rejects_game_directory_outputs_before_any_write(self):
        before = snapshot(self.game)
        for path in (self.game, self.game / "override" / "drafts"):
            with self.subTest(path=path), self.assertRaisesRegex(ProfileError, "outside"):
                self.write(store=path)
        self.assertEqual(snapshot(self.game), before)

    def test_failed_lua_publication_leaves_old_pointer_complete(self):
        first = self.write()
        writer = campaigns._write_immutable
        def fail_lua(path, data):
            if path.suffix == ".lua":
                raise OSError("simulated disk failure")
            return writer(path, data)
        with patch.object(campaigns, "_write_immutable", side_effect=fail_lua):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                self.write(self.plan(["cre:actor/item/1"]))
        self.assertEqual(read_campaign(self.store)["revision"], first["revision"])
        self.assertEqual(list(self.store.rglob(".flir-*.tmp")), [])

    def test_existing_revision_conflict_does_not_change_pointer(self):
        first = self.write()
        for key in ("revision_path", "lua_path"):
            path = Path(first[key])
            original = path.read_bytes()
            path.write_bytes(b"corrupt")
            with self.subTest(key=key), self.assertRaisesRegex(ProfileError, "conflicting content"):
                self.write()
            path.write_bytes(original)
        self.assertEqual(read_campaign(self.store)["revision"], first["revision"])

    def test_lua_rendering_escapes_every_utf8_byte_and_never_injects_code(self):
        hostile = '"]; os.execute("unexpected"); --\\\n123\x00한글 é'
        target = next(row for row in self.world["targets"] if row["id"] == "are:area/actor/1")
        target["selector"]["name"] = hostile
        target["selector"]["optional_note"] = None
        self.world["fingerprint"] = world_fingerprint(self.world)
        result = self.write()
        body = read_campaign(self.store)
        lua = Path(result["lua_path"]).read_text("ascii")
        self.assertEqual(body["targets"][0]["record"]["selector"]["name"], hostile)
        self.assertNotIn("os.execute", lua)
        self.assertNotIn(hostile, lua)
        literals = re.findall(r'"((?:\\[0-9]{3})*)"', lua)
        decoded = [bytes(int(part) for part in re.findall(r"\\([0-9]{3})", text)).decode("utf-8")
                   for text in literals]
        self.assertIn(hostile, decoded)
        stripped = re.sub(r'"(?:\\[0-9]{3})*"', '""', lua)
        self.assertNotRegex(stripped, r"[A-Za-z_]+(?=\s*\()")
        self.assertTrue(lua.startswith("return {\n"))
        self.assertIn(" = nil,", lua)

    def test_lua_exact_scalars_and_sparse_arrays(self):
        lua = campaigns._render_lua({"a": [True, None, False], "n": 2**53 - 1})
        self.assertIn(b"[1] = true,", lua)
        self.assertIn(b"[2] = nil,", lua)
        self.assertIn(b"[3] = false,", lua)
        self.assertIn(b"9007199254740991", lua)
        for value in (float("nan"), float("inf"), 0.5, 2**53, {1: "bad key"}, "\ud800", set()):
            with self.subTest(value=repr(value)), self.assertRaises(ProfileError):
                campaigns._render_lua(value)

    def test_rejects_nonfinite_or_inexact_nested_data_before_publication(self):
        for value in (float("nan"), float("inf"), 0.5, 2**53):
            target = next(row for row in self.world["targets"] if row["id"] == "are:area/actor/1")
            target["selector"]["unsupported_number"] = value
            with self.subTest(value=value), self.assertRaises(ProfileError):
                self.world["fingerprint"] = world_fingerprint(self.world)
                self.write()
            self.assertFalse(self.store.exists())

    def test_tampered_lua_json_and_missing_companion_fail(self):
        result = self.write()
        path = Path(result["lua_path"])
        expected = path.read_bytes()
        path.write_bytes(expected + b"os.execute('unexpected')\n")
        with self.assertRaisesRegex(ProfileError, "disagree"):
            read_campaign(self.store)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            read_campaign(self.store)
        path.write_bytes(expected)
        body = read_campaign(self.store)
        body["targets"][0]["capacity"] = 2
        Path(result["revision_path"]).write_bytes(canonical_bytes(body))
        with self.assertRaisesRegex(ProfileError, "modified"):
            read_campaign(self.store)

    def test_read_validates_schema_quantities_ids_order_and_limits_after_rehash(self):
        self.write()
        original = read_campaign(self.store)
        changes = [
            lambda body: body.update(state="playable"),
            lambda body: body.update(unexpected=True),
            lambda body: body.update(runtime_requirements=[]),
            lambda body: body["sources"][0].update(quantity=2),
            lambda body: body["sources"][0].update(quantity=True),
            lambda body: body["sources"][0].update(id="invented"),
            lambda body: body["sources"][0]["record"]["record"].update(charges=[1]),
            lambda body: body["targets"][0].update(capacity=4097),
            lambda body: body["targets"][0].update(capacity=0),
            lambda body: body["targets"][0].update(resource_sha256="not a hash"),
            lambda body: body["targets"][0]["record"].update(id="cre:other"),
            lambda body: body["sources"].append(deepcopy(body["sources"][0])),
            lambda body: body.update(sources=[]),
        ]
        for change in changes:
            body = deepcopy(original)
            change(body)
            revision = self.replace_artifact(body)
            with self.subTest(change=change), self.assertRaises(ProfileError):
                read_campaign(self.store, revision)

    def test_store_stock_count_cannot_be_forged_even_with_recomputed_hash(self):
        self.write(self.plan(["sto:shop/item/0"], capacity=3))
        body = read_campaign(self.store)
        body["sources"][0]["quantity"] = 2
        revision = self.replace_artifact(body)
        with self.assertRaisesRegex(ProfileError, "finite stock"):
            read_campaign(self.store, revision)

    def test_revision_pointer_and_filename_are_checked(self):
        result = self.write()
        original = read_campaign(self.store)
        for revision in ("../escape", 1, "F" * 64, ""):
            with self.subTest(revision=revision), self.assertRaises(ProfileError):
                read_campaign(self.store, revision)
        pointer = Path(result["pointer_path"])
        pointer.write_text(json.dumps({"schema": "flir-next-draft-v1", "revision": result["revision"]}))
        with self.assertRaisesRegex(ProfileError, "pointer"):
            read_campaign(self.store)
        renamed = "0" * 64
        (self.store / "revisions" / (renamed + ".json")).write_bytes(canonical_bytes(original))
        with self.assertRaisesRegex(ProfileError, "filename"):
            read_campaign(self.store, renamed)

    def test_invalid_json_does_not_reach_lua(self):
        result = self.write()
        path = Path(result["revision_path"])
        for invalid in ('{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}', '{"x":"\\ud800"}'):
            path.write_text(invalid, encoding="utf-8")
            with self.subTest(invalid=invalid), self.assertRaises(ProfileError):
                read_campaign(self.store, result["revision"])

    def test_no_hidden_dependency_on_game_after_catalogue_scan(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
                patch("io.open", side_effect=AssertionError("unexpected file access")):
            body = self.compile()
        self.assertEqual(body["state"], "prototype-not-playable")


if __name__ == "__main__":
    unittest.main()
