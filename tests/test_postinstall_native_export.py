"""Native export checks on a temporary synthetic installation only."""

import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config.campaigns import _render_lua, read_campaign, write_campaign
from flir_config.native_export import export_native
from flir_config.profiles import ProfileError, _digest, canonical_bytes, edit_profile, new_profile
from flir_config.resources import scan_game
from flir_config.sources import new_binding_plan, scan_world
from flir_config.world_formats import parse_cre, parse_sto
from tests.test_postinstall_resources import make_game, make_itm, mark_eet, snapshot
from tests.test_postinstall_world_formats import item_record, make_are, make_cre, make_sto


class NativeExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="flir-native-export-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game, self.store, self.output = (self.root / name for name in ("game", "campaigns", "randomiser-campaigns"))
        make_game(self.game, [("SYNTH001", make_itm()), ("SYNTH002", make_itm())])
        mark_eet(self.game)
        self.override = self.game / "override"
        for name in ("synth001", "synth002"):
            item = bytearray(make_itm())
            struct.pack_into("<I", item, 0x18, 0x4c)
            (self.override / f"{name}.itm").write_bytes(item)
        self.first = "are:area/container/0/item/0"
        self.second = "are:area/container/1/item/1"
        embedded = bytearray(make_cre([]))
        embedded[0x280:0x288] = b"EmbedDV\0"
        (self.override / "actor.cre").write_bytes(make_cre([]))
        self.area = bytes(make_are([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1),
            item_record("SYNTH002", expiration=0, charges=(0, 0, 0), flags=1)],
            [("ChestOne", 0, 1), ("ChestTwo", 1, 1)],
            [("External", "ACTOR"), ("Embedded", bytes(embedded))]))
        (self.override / "area.are").write_bytes(self.area)
        self.refresh()

    def refresh(self):
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def write(self, items=("synth001",), target="are:area/actor/0", *,
              shop_price_percent=None, shop_base_prices=None):
        profile = edit_profile(new_profile(self.catalog, "native-test", "Native test"),
                               self.catalog, include=list(items),
                               shop_price_percent=shop_price_percent,
                               shop_base_prices=shop_base_prices)
        plan = new_binding_plan(profile, self.catalog, self.world)
        ids = {"synth001": self.first, "synth002": self.second}
        plan["sources"] = [{"item": item, "policy": "relocate", "source_ids": [ids[item]],
                            "registration_ids": []} for item in items]
        plan["targets"] = [{"id": target, "capacity": len(items), "optional": False}]
        return write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]

    def export(self, revision=None):
        return export_native(self.store, self.catalog, self.world, self.output, revision)

    def write_creature_source(self, kind, target, replacement=None):
        profile = edit_profile(new_profile(self.catalog, "native-creature", "Creature test"),
                               self.catalog, include=["synth001"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        source = next(row for row in self.world["sources"]
                      if row["kind"] == kind and row["item"] == "synth001")
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": [source["id"]], "registration_ids": []}]
        if replacement is not None:
            plan["replacements"] = {source["id"]: replacement}
        plan["targets"] = [{"id": target, "capacity": 1, "optional": False}]
        return write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"], source

    def creature_artifacts(self, revision):
        base = self.output / "campaign-store" / "revisions" / f"{revision}.source-1"
        return Path(str(base) + ".original.cre"), Path(str(base) + ".removed.cre")

    def write_store_source(self, target="are:area/actor/0", target_capacity=None):
        profile = edit_profile(new_profile(self.catalog, "native-store", "Store test"),
                               self.catalog, include=["synth001"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        source = next(row for row in self.world["sources"]
                      if row["kind"] == "store" and row["item"] == "synth001")
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": [source["id"]], "registration_ids": []}]
        quantity = source["record"]["quantity"]
        capacity = quantity if target_capacity is None else target_capacity
        plan["targets"] = [{"id": target, "capacity": capacity,
                            "optional": False}]
        if capacity < quantity:
            plan["targets"].append({"id": "are:area/actor/0", "capacity": quantity - capacity,
                                    "optional": False})
        return write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"], source

    def test_finite_store_row_exports_exact_private_before_and_after(self):
        original = bytes(make_sto([
            (item_record("SYNTH002", expiration=0, charges=(1, 2, 3), flags=1), 2, 0),
            (item_record("SYNTH001", expiration=0, charges=(4, 5, 6), flags=1), 3, 0),
            (item_record("SYNTH002", expiration=0, charges=(7, 8, 9), flags=1), 1, 0)], gap=5))
        (self.override / "shop.sto").write_bytes(original)
        self.refresh()
        revision, source = self.write_store_source()
        before = snapshot(self.game)
        self.export(revision)
        base = self.output / "campaign-store" / "revisions" / f"{revision}.source-1"
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        endpoint = native["sources"][source["id"]]
        self.assertEqual({key: value for key, value in endpoint.items() if key != "identity"}, {
            "kind": "guarded-store", "store": "shop", "record_index": 1, "quantity": 3,
            "payload": {"resref": "synth001", "expiration": 0, "charges": [4, 5, 6],
                        "flags": 1}})
        self.assertEqual(Path(str(base) + ".original.sto").read_bytes(), original)
        removed = Path(str(base) + ".removed.sto").read_bytes()
        self.assertEqual(len(removed), len(original))
        self.assertEqual([(row["resref"], row["quantity"], row["charges"])
                          for row in parse_sto(removed)["items"]],
                         [("synth002", 2, [1, 2, 3]), ("synth002", 1, [7, 8, 9])])
        self.assertEqual(snapshot(self.game), before)

    def test_empty_store_target_exports_identity_hash_and_frozen_shop_prices(self):
        (self.override / "shop.sto").write_bytes(make_sto([]))
        self.refresh()
        revision = self.write(target="sto:shop", shop_price_percent=150,
                              shop_base_prices={"synth001": 250})
        before = snapshot(self.game)
        self.export(revision)
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        self.assertEqual(native["shop_pricing"],
                         {"percent": 150, "base_prices": {"synth001": 250}})
        self.assertEqual(native["shop_prices"], {"synth001": 375})
        target = native["targets"]["sto:shop"]
        self.assertEqual((target["kind"], target["store"]), ("store", "shop"))
        self.assertEqual(target["identity"],
                         _digest({"target": read_campaign(self.store, revision)["targets"][0]}))
        self.assertEqual(native["resource_hashes"]["SHOP.STO"],
                         next(row["sha256"] for row in self.world["resources"]
                              if row["id"] == "sto:shop"))
        self.assertEqual(snapshot(self.game), before)

    def test_store_target_requires_buy_permission_without_restricting_store_type(self):
        store = make_sto([])
        struct.pack_into("<I", store, 0x10, 2)  # Player may sell, but cannot buy.
        (self.override / "shop.sto").write_bytes(store)
        self.refresh()
        blocked = self.write(target="sto:shop")
        with self.assertRaisesRegex(ProfileError, "does not allow the player to buy"):
            self.export(blocked)
        self.assertFalse((self.output / "next.lua").exists())

        struct.pack_into("<I", store, 0x08, 1)  # Inn with merchandise.
        struct.pack_into("<I", store, 0x10, 1)
        (self.override / "shop.sto").write_bytes(store)
        self.refresh()
        allowed = self.write(target="sto:shop")
        self.export(allowed)
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{allowed}.native.json").read_bytes())
        self.assertEqual(native["targets"]["sto:shop"]["kind"], "store")

    def test_store_target_preserves_existing_stock_and_source_overlap(self):
        (self.override / "shop.sto").write_bytes(make_sto([
            (item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1), 3, 0)]))
        self.refresh()
        revision = self.write(target="sto:shop")
        before = snapshot(self.game)
        self.export(revision)
        revision, source = self.write_store_source(target="sto:shop")
        self.export(revision)
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        self.assertEqual(native["targets"]["sto:shop"]["store"], "shop")
        self.assertEqual(native["sources"][source["id"]]["store"], "shop")
        self.assertEqual(snapshot(self.game), before)

    def test_multi_copy_and_distinct_items_can_share_shop(self):
        (self.override / "origin.sto").write_bytes(make_sto([
            (item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1), 2, 0)]))
        (self.override / "shop.sto").write_bytes(make_sto([]))
        self.refresh()
        revision, _ = self.write_store_source(target="sto:shop")
        self.export(revision)
        manifest = read_campaign(self.store, revision)
        self.assertEqual(manifest["sources"][0]["quantity"], 2)
        self.assertEqual(manifest["targets"][0]["capacity"], 2)
        split, _ = self.write_store_source(target="sto:shop", target_capacity=1)
        self.export(split)
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{split}.native.json").read_bytes())
        self.assertEqual(set(native["targets"]), {"sto:shop", "are:area/actor/0"})
        distinct = self.write(items=("synth001", "synth002"), target="sto:shop")
        self.export(distinct)
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{distinct}.native.json").read_bytes())
        self.assertEqual(set(native["shop_prices"]), {"synth001", "synth002"})

    def test_several_originals_of_one_item_export_as_distinct_rewards(self):
        record = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
        area = bytes(make_are([record, record, record],
                             [("ChestOne", 0, 1), ("ChestTwo", 1, 1), ("Unselected", 2, 1)],
                             [("External", "ACTOR")]))
        (self.override / "area.are").write_bytes(area)
        (self.override / "shop.sto").write_bytes(make_sto([]))
        (self.override / "shop2.sto").write_bytes(make_sto([]))
        self.refresh()
        profile = edit_profile(new_profile(self.catalog, "copies", "Two copies"),
                               self.catalog, include=["synth001"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": [self.second, self.first], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/0", "capacity": 2, "optional": False}]
        revision = write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]
        self.export(revision)
        manifest = read_campaign(self.store, revision)
        self.assertEqual([(s["id"], s["quantity"]) for s in manifest["sources"]],
                         [(self.first, 1), (self.second, 1)])
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        self.assertEqual(set(native["sources"]), {self.first, self.second})
        self.assertEqual(len({s["identity"] for s in native["sources"].values()}), 2)
        self.assertEqual(native["source_gate"]["source_ids"], [self.first, self.second])
        self.assertEqual((self.output / "AREA.pristine.are").read_bytes(), area)

        # Both originals remain separate reward units even at one recipient.
        plan["targets"] = [{"id": "sto:shop", "capacity": 2, "optional": False}]
        combined = write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]
        self.export(combined)
        manifest = read_campaign(self.store, combined)
        self.assertEqual(len(manifest["sources"]), 2)
        self.assertEqual(manifest["targets"][0]["capacity"], 2)
        plan["targets"] = [{"id": shop, "capacity": 1, "optional": False}
                           for shop in ("sto:shop", "sto:shop2")]
        split = write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]
        self.export(split)
        exported = json.loads((self.output / "campaign-store" / "revisions" /
                               f"{split}.native.json").read_bytes())
        self.assertEqual(set(exported["targets"]), {"sto:shop", "sto:shop2"})

    def test_new_profile_preserves_old_native_revision_and_pristine_area(self):
        game_before = snapshot(self.game)
        current = self.write(("synth001", "synth002"))
        legacy = read_campaign(self.store, current)
        del legacy["shop_pricing"]
        legacy["revision"] = _digest({key: value for key, value in legacy.items()
                                      if key != "revision"})
        a = legacy["revision"]
        revision_base = self.store / "revisions" / a
        Path(str(revision_base) + ".json").write_bytes(canonical_bytes(legacy))
        Path(str(revision_base) + ".lua").write_bytes(_render_lua(legacy))
        first = self.export(a)
        self.assertEqual((first["revision"], first["revisions"], first["state"]),
                         (a, [a], "experimental-native-config"))
        a_paths = [self.output / "campaign-store" / "revisions" / f"{a}{suffix}"
                   for suffix in (".json", ".lua", ".native.json", ".native.lua")]
        old_bytes = [path.read_bytes() for path in a_paths]
        a_native = json.loads(old_bytes[2])
        self.assertEqual(set(a_native["sources"]), {self.first, self.second})
        self.assertEqual(a_native["source_gate"]["source_ids"], [self.first, self.second])
        self.assertEqual(a_native["targets"]["are:area/actor/0"]["script_name"], "TestDV")
        self.assertNotIn("shop_pricing", a_native)
        self.assertNotIn("shop_prices", a_native)
        pristine = self.output / "AREA.pristine.are"
        self.assertEqual(pristine.read_bytes(), self.area)

        b = self.write()
        second = self.export()
        self.assertEqual((second["revision"], second["revisions"]), (b, sorted([a, b])))
        self.assertEqual(Path(second["config_path"]), self.output)
        self.assertEqual([path.read_bytes() for path in a_paths], old_bytes)
        self.assertEqual(pristine.read_bytes(), self.area)
        b_native = json.loads((self.output / "campaign-store" / "revisions" /
                               f"{b}.native.json").read_bytes())
        self.assertEqual(list(b_native["sources"]), [self.first])
        self.assertEqual(b_native["source_gate"]["source_ids"], [self.first])
        self.assertEqual(b_native["shop_pricing"], {"percent": 120, "base_prices": {}})
        self.assertEqual(b_native["shop_prices"], {"synth001": 360})
        self.assertEqual((self.output / "source-gate.lua").read_bytes(), _render_lua(sorted([a, b])))
        self.assertEqual((self.output / "next.lua").read_bytes(), _render_lua(b))
        self.assertEqual(snapshot(self.game), game_before)

    def test_an_area_an_earlier_revision_guards_must_stay_as_it_was(self):
        # At every start the runtime compares the area each revision's guarded
        # sources recorded with the one pristine copy of it. A new revision would
        # replace that copy with the changed area, and the earlier revision would
        # then stop the runtime for every game: Apply says so and writes nothing.
        earlier = self.write()
        self.export(earlier)
        changed = bytearray(self.area)
        changed[0x30] ^= 1
        (self.override / "area.are").write_bytes(changed)
        self.refresh()
        later = self.write(("synth001", "synth002"))
        before = snapshot(self.output)
        with self.assertRaisesRegex(ProfileError, f"Saved games of revision {earlier[:12]} need AREA.ARE as it was "
                                    "when they were exported, but it changed in the game since then"):
            self.export(later)
        self.assertEqual(snapshot(self.output), before)

    def test_an_earlier_revision_keeps_its_files_when_a_file_it_only_reads_changed(self):
        earlier = self.write()
        self.export(earlier)
        revisions = self.output / "campaign-store" / "revisions"
        files = {path.name: path.read_bytes() for path in revisions.iterdir() if path.name.startswith(earlier)}
        # The target creature changes; the runtime does not compare it at start.
        creature = bytearray((self.override / "actor.cre").read_bytes())
        creature[0x24] ^= 1
        (self.override / "actor.cre").write_bytes(creature)
        self.refresh()
        later = self.write(("synth001", "synth002"))
        result = self.export(later)
        self.assertEqual(result["revisions"], sorted([earlier, later]))
        self.assertEqual({path.name: path.read_bytes() for path in revisions.iterdir()
                          if path.name.startswith(earlier)}, files)
        self.assertEqual(result["notes"], [f"Saved games of revision {earlier[:12]} keep the settings exported "
                                           "earlier, although ACTOR.CRE changed since then"])

    def test_rescan_can_add_unrelated_resources_without_rewriting_saved_configuration(self):
        revision = self.write()
        self.export(revision)
        native = self.output / "campaign-store" / "revisions" / f"{revision}.native.json"
        before, fingerprint = native.read_bytes(), self.world["fingerprint"]
        (self.override / "extra.itm").write_bytes(make_itm())
        self.refresh()
        self.assertNotEqual(self.world["fingerprint"], fingerprint)
        self.export(revision)
        self.assertEqual(native.read_bytes(), before)
        # Changed selected content still needs a new configuration/scan binding.
        changed = bytearray(self.area)
        changed[0x30] ^= 1
        (self.override / "area.are").write_bytes(changed)
        self.refresh()
        with self.assertRaisesRegex(ProfileError, "differs from the scan"):
            self.export(revision)

    def test_embedded_and_external_actor_names_become_native_creature_targets(self):
        for index, name in ((0, "TestDV"), (1, "EmbedDV")):
            with self.subTest(actor=index):
                revision = self.write(target=f"are:area/actor/{index}")
                self.export(revision)
                native = json.loads((self.output / "campaign-store" / "revisions" /
                                     f"{revision}.native.json").read_bytes())
                self.assertEqual(native["targets"][f"are:area/actor/{index}"]["script_name"], name)
                self.assertEqual(native["targets"][f"are:area/actor/{index}"]["kind"], "creature")

    def test_nonempty_container_target_exports_static_identity_with_repeated_capacity(self):
        area = bytes(make_are([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1),
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1),
            item_record("SYNTH002", expiration=0, charges=(3, 4, 5), flags=1)],
            [("OriginOne", 0, 1), ("OriginTwo", 1, 1), ("RewardChest", 2, 1)],
            [("External", "ACTOR")]))
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        profile = edit_profile(new_profile(self.catalog, "container-target", "Container target"),
                               self.catalog, include=["synth001"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": ["are:area/container/0/item/0",
                                           "are:area/container/1/item/1"],
                            "registration_ids": []}]
        identity = "are:area/container/2"
        plan["targets"] = [{"id": identity, "capacity": 2, "optional": False}]
        revision = write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]
        before = snapshot(self.game)
        self.export(revision)
        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        selected_target = read_campaign(self.store, revision)["targets"][0]
        self.assertEqual(selected_target["capacity"], 2)
        self.assertEqual(native["targets"][identity], {
            "kind": "container", "area": "AREA", "script_name": "RewardChest",
            "x": 111, "y": 222, "container_type": 2,
            "identity": _digest({"target": selected_target})})
        self.assertEqual(native["resource_hashes"]["AREA.ARE"],
                         next(row["sha256"] for row in self.world["resources"]
                              if row["id"] == "are:area"))
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual((self.override / "area.are").read_bytes(), area)

    def test_ambiguous_container_target_name_is_rejected(self):
        area = make_are([item_record("SYNTH001", expiration=0,
                                     charges=(0, 0, 0), flags=1)],
                        [("SourceChest", 0, 1), ("Duplicate", 1, 0),
                         ("Duplicate", 1, 0)], [("External", "ACTOR")])
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        self.write(target="are:area/container/1")
        with self.assertRaisesRegex(ProfileError, "unique static container name"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())

    def test_stackable_item_exports_exact_payload_without_changing_game(self):
        stackable = bytearray((self.override / "synth001.itm").read_bytes())
        struct.pack_into("<H", stackable, 0x38, 20)
        (self.override / "synth001.itm").write_bytes(stackable)
        self.refresh()
        self.write()
        game_before = snapshot(self.game)
        result = self.export()
        native = json.loads((self.output / 'campaign-store/revisions' /
                             (result['revision'] + '.native.json')).read_bytes())
        self.assertEqual(native['sources'][self.first]['payload'], {
            'resref': 'synth001', 'expiration': 0, 'flags': 1, 'charges': [0, 0, 0]})
        self.assertTrue((self.output / "next.lua").exists())
        self.assertEqual(snapshot(self.game), game_before)

    def test_expired_source_fails_before_publishing_next_pointer(self):
        area = bytearray(self.area)
        struct.pack_into("<H", area, area.index(b"SYNTH001") + 8, 3)
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        self.write()
        with self.assertRaisesRegex(ProfileError, "unexpired"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())

    def test_same_container_duplicate_resref_cannot_export_one_original(self):
        original = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
        area = make_are([original, original], [("ChestOne", 0, 2)], [("External", "ACTOR")])
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        self.write()
        with self.assertRaisesRegex(ProfileError, "another copy"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())

    def test_external_creature_source_exports_original_and_backpack_removal(self):
        original = bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1),
            item_record("SYNTH002", expiration=0, charges=(2, 3, 4), flags=1)]))
        (self.override / "actor.cre").write_bytes(original)
        self.refresh()
        revision, source = self.write_creature_source("cre-template", "are:area/actor/1")
        game_before = snapshot(self.game)
        self.export(revision)

        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        self.assertEqual(native["source_gate"]["source_ids"], [source["id"]])
        compiled_source = native["sources"][source["id"]]
        self.assertRegex(compiled_source["identity"], r"^[0-9a-f]{64}$")
        self.assertEqual({key: value for key, value in compiled_source.items()
                          if key != "identity"}, {
            "kind": "guarded-creature", "area": "AREA", "script_name": "TestDV",
            "actor_index": 0, "creature_resource": "cre:actor",
            "actor_owner": "are:area/actor/0",
            "payload": {"resref": "synth001", "expiration": 0,
                        "charges": [0, 0, 0], "flags": 1}})
        self.assertEqual(set(native["resource_hashes"]), {"AREA.ARE", "ACTOR.CRE",
                                                         "SYNTH001.ITM"})
        original_path, removed_path = self.creature_artifacts(revision)
        self.assertEqual(original_path.read_bytes(), original)
        removed = removed_path.read_bytes()
        self.assertEqual(len(removed), len(original))
        self.assertEqual([row["resref"] for row in parse_cre(removed)["items"]], ["synth002"])
        self.assertEqual(parse_cre(removed)["items"][0]["charges"], [2, 3, 4])
        self.assertEqual(parse_cre(removed)["items"][0]["slots"], [22])
        self.assertEqual((self.output / "AREA.pristine.are").read_bytes(), self.area)
        self.assertEqual(snapshot(self.game), game_before)

        later = self.write()
        self.export(later)
        self.assertNotEqual(later, revision)
        self.assertEqual(original_path.read_bytes(), original)
        self.assertEqual(removed_path.read_bytes(), removed)

    def test_creature_source_keeps_stolen_and_other_flags_but_never_undroppable(self):
        # Baeloth's items are identified and stolen (5); a mod gave Koshi's sword 0x3E7.
        # An undroppable reward (8) would never leave a creature target.
        for flags, exported in ((5, True), (0x3E7, True), (0x80000004, True),
                                (8, False), (9, False), (0x3EF, False)):
            with self.subTest(flags=hex(flags)):
                original = bytes(make_cre([
                    item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=flags),
                    item_record("SYNTH002", expiration=0, charges=(2, 3, 4), flags=1)]))
                (self.override / "actor.cre").write_bytes(original)
                self.refresh()
                self.output = self.root / f"randomiser-campaigns-{flags:x}"
                revision, source = self.write_creature_source("cre-template", "are:area/actor/1")
                if not exported:
                    with self.assertRaisesRegex(ProfileError, "^Native source requires an unexpired, "
                                                "visible droppable item: " + re.escape(source["id"]) + "$"):
                        self.export(revision)
                    self.assertFalse((self.output / "next.lua").exists())
                    continue
                self.export(revision)
                native = json.loads((self.output / "campaign-store" / "revisions" /
                                     f"{revision}.native.json").read_bytes())
                self.assertEqual(native["sources"][source["id"]]["payload"], {
                    "resref": "synth001", "expiration": 0, "charges": [0, 0, 0], "flags": flags})
                original_path, removed_path = self.creature_artifacts(revision)
                self.assertEqual(original_path.read_bytes(), original)
                self.assertEqual([(row["resref"], row["flags"]) for row in
                                  parse_cre(removed_path.read_bytes())["items"]], [("synth002", 1)])

    def test_embedded_creature_source_exports_exact_embedded_bytes(self):
        embedded = bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1),
            item_record("SYNTH002", expiration=0, charges=(5, 6, 7), flags=1)]))
        embedded = bytearray(embedded)
        embedded[0x280:0x288] = b"EmbedDV\0"
        area = bytes(make_are([], [], [("External", "ACTOR"), ("Embedded", bytes(embedded))]))
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        revision, source = self.write_creature_source("embedded-cre", "are:area/actor/0")
        game_before = snapshot(self.game)
        self.export(revision)

        native = json.loads((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").read_bytes())
        self.assertEqual(native["sources"][source["id"]]["actor_owner"], "are:area/actor/1")
        self.assertEqual(native["sources"][source["id"]]["actor_index"], 1)
        self.assertEqual(native["sources"][source["id"]]["creature_resource"], "are:area")
        original_path, removed_path = self.creature_artifacts(revision)
        self.assertEqual(original_path.read_bytes(), bytes(embedded))
        removed = removed_path.read_bytes()
        self.assertEqual(len(removed), len(embedded))
        self.assertEqual([row["resref"] for row in parse_cre(removed)["items"]], ["synth002"])
        self.assertEqual((self.output / "AREA.pristine.are").read_bytes(), area)
        self.assertEqual(snapshot(self.game), game_before)

    def test_external_template_with_two_area_actors_exports_template_guard(self):
        (self.override / "actor.cre").write_bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)]))
        recipient = make_cre([])
        recipient[0x280:0x2a0] = b'RecipientDV'.ljust(32, b'\0')
        (self.override / "area.are").write_bytes(make_are([], [], [
            ("First", "ACTOR"), ("Second", "ACTOR"), ("Recipient", recipient)]))
        self.refresh()
        revision, source = self.write_creature_source("cre-template", "are:area/actor/2")
        self.export()
        native = json.loads((self.output / 'campaign-store/revisions' /
                             (revision + '.native.json')).read_bytes())
        self.assertEqual(native['sources'][source['id']]['kind'], 'guarded-template')
        self.assertEqual(self.creature_artifacts(revision)[0].read_bytes(),
                         (self.override / 'actor.cre').read_bytes())

    def test_equipped_creature_source_requires_explicit_replacement(self):
        (self.override / "actor.cre").write_bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)],
            slots={9: 0}))
        self.refresh()
        self.write_creature_source("cre-template", "are:area/actor/1")
        with self.assertRaisesRegex(ProfileError, "needs explicit replacement equipment"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())

    def test_container_source_replacement_is_frozen_in_native_fixture(self):
        profile = edit_profile(new_profile(self.catalog, "container-replacement", "Container replacement"),
                               self.catalog, include=["synth001"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": [self.first], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/0", "capacity": 1, "optional": False}]
        plan["replacements"] = {self.first: "synth002"}
        before = snapshot(self.game)
        revision = write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]
        self.export(revision)
        base = self.output / "campaign-store" / "revisions"
        manifest = json.loads((base / f"{revision}.json").read_bytes())
        native = json.loads((base / f"{revision}.native.json").read_bytes())
        replacement_hash = next(item["sha256"] for item in self.catalog["items"]
                                if item["resref"] == "synth002")
        self.assertEqual(manifest["sources"][0]["replacement"],
                         {"resref": "synth002", "resource_sha256": replacement_hash})
        self.assertEqual(native["sources"][self.first]["kind"], "guarded-container")
        self.assertEqual(native["sources"][self.first]["replacement"], "synth002")
        self.assertEqual(native["resource_hashes"]["SYNTH002.ITM"], replacement_hash)
        self.assertEqual(snapshot(self.game), before)

    def test_equipped_replacement_and_blank_are_frozen_per_revision(self):
        original = bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(2, 3, 4), flags=1)],
            slots={9: 0}))
        (self.override / "actor.cre").write_bytes(original)
        self.refresh()
        game_before = snapshot(self.game)

        equipped, source = self.write_creature_source(
            "cre-template", "are:area/actor/1", replacement="synth002")
        self.export(equipped)
        base = self.output / "campaign-store" / "revisions"
        manifest_path = base / f"{equipped}.json"
        native_path = base / f"{equipped}.native.json"
        manifest = json.loads(manifest_path.read_bytes())
        native = json.loads(native_path.read_bytes())
        replacement = manifest["sources"][0]["replacement"]
        self.assertEqual(replacement, {
            "resref": "synth002",
            "resource_sha256": next(item["sha256"] for item in self.catalog["items"]
                                    if item["resref"] == "synth002")})
        self.assertEqual(native["sources"][source["id"]]["replacement"], "synth002")
        self.assertEqual(native["resource_hashes"]["SYNTH002.ITM"],
                         replacement["resource_sha256"])
        original_path, replaced_path = self.creature_artifacts(equipped)
        replaced = replaced_path.read_bytes()
        self.assertEqual(original_path.read_bytes(), original)
        self.assertEqual(len(replaced), len(original))
        self.assertEqual(replaced[:source["record"]["byte_offset"]],
                         original[:source["record"]["byte_offset"]])
        offset = source["record"]["byte_offset"]
        self.assertEqual(replaced[offset:offset + 8], b"synth002")
        self.assertEqual(replaced[offset + 8:], original[offset + 8:])
        item = parse_cre(replaced)["items"][0]
        self.assertEqual((item["resref"], item["slots"], item["charges"], item["flags"]),
                         ("synth002", [9], [2, 3, 4], 1))

        blank, _ = self.write_creature_source(
            "cre-template", "are:area/actor/1", replacement="blank")
        self.assertNotEqual(blank, equipped)
        self.export(blank)
        blank_manifest = json.loads((base / f"{blank}.json").read_bytes())
        blank_native = json.loads((base / f"{blank}.native.json").read_bytes())
        self.assertEqual(blank_manifest["sources"][0]["replacement"], {"resref": "blank"})
        self.assertEqual(blank_native["sources"][source["id"]]["replacement"], "blank")
        self.assertEqual(parse_cre(self.creature_artifacts(blank)[1].read_bytes())["items"], [])
        self.assertEqual(replaced_path.read_bytes(), replaced)
        self.assertEqual(json.loads(manifest_path.read_bytes()), manifest)
        self.assertEqual(json.loads(native_path.read_bytes()), native)
        self.assertEqual(snapshot(self.game), game_before)

    def test_duplicate_creature_resref_cannot_export_one_original(self):
        record = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
        (self.override / "actor.cre").write_bytes(make_cre([record, record]))
        self.refresh()
        self.write_creature_source("cre-template", "are:area/actor/1")
        with self.assertRaisesRegex(ProfileError, "another copy"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())


if __name__ == "__main__":
    unittest.main()
