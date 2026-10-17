"""Hermetic binding/compilation of original authored rows."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.campaigns import _validate_campaign, compile_campaign
from flir_config.original_configuration import build_original_configuration
from flir_config.profiles import ProfileError, _digest
from flir_config.templates import DEFAULT_OPTIONS
from test_original_configuration import OriginalConfigurationTests


class OriginalRegistrationTests(unittest.TestCase):
    setUp = OriginalConfigurationTests.setUp
    _draft = OriginalConfigurationTests._draft

    def configuration(self, draft=None):
        draft = deepcopy(draft or self._draft())
        draft["options"] = DEFAULT_OPTIONS | draft["options"]
        draft["targets"][0]["ident"] = "free"
        spare = deepcopy(draft["targets"][1])
        spare["id"] = "target-spare"
        spare["ident"] = "free"
        draft["targets"].append(spare)
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        return result, draft

    def compile(self, result, draft):
        with patch("flir_config.original_registrations.resolve_template", return_value=draft):
            return compile_campaign(result["profile"], self.catalog, self.world, result["plan"])

    def test_registered_mode2_aliases_validate_only_exact_authored_primary_resource(self):
        from flir_config.original_catalog import resolve_template
        from flir_config.original_registrations import (TARGET_FIELDS, target_kind, target_record,
                                                       validate_campaign_target,
                                                       validate_target_registration)

        names = ("dlg:hendak", "dlg:lehtin", "dlg:bhoisig", "dlg:bhnalla", "dlg:bharval",
                 "are:ar1100", "dlg:uhogre01")
        world = {"resources": [{"id": name} for name in names], "sources": [], "targets": []}
        draft = resolve_template({"items": []}, world, "bg2-original", items="extended", targets="extended")
        rows = {row["id"]: row for row in draft["targets"]}
        for line in (18, 48):
            authored = rows[f"bg2:mode2:target:{line}"]
            registration = {"id": "original-target:" + authored["id"],
                            "resource": authored["area_resource"], "resource_sha256": "1" * 64,
                            "adapter_id": "original-target-v1", "selector": authored["id"],
                            "capacity": 1, "optional": False,
                            "authored": {key: authored[key] for key in TARGET_FIELDS}}
            with self.subTest(line=line):
                validate_target_registration(registration, rows)
                record = target_record(registration)
                self.assertEqual(record["kind"], "scripted-target")
                compiled = {"id": record["id"], "capacity": 1, "record": record,
                            "resource_sha256": registration["resource_sha256"]}
                self.assertEqual(validate_campaign_target(compiled), 1)
                for field, value in (("target", "unregistered.fl"), ("id", "bg2:mode2:target:999"),
                                     ("family", "base"), ("area", "ar0001")):
                    altered = deepcopy(compiled)
                    altered["record"]["selector"][field] = value
                    with self.subTest(field=field), self.assertRaises(ProfileError):
                        validate_campaign_target(altered)
                # Even a listed secondary resource cannot become the primary anchor.
                for resource in ("dlg:lehtin", "bcs:hendak", "are:ar0406"):
                    altered = deepcopy(compiled)
                    altered["record"]["resource"] = resource
                    altered["record"]["selector"]["area_resource"] = resource
                    with self.subTest(resource=resource), self.assertRaises(ProfileError):
                        validate_campaign_target(altered)
                altered = deepcopy(registration)
                altered["resource"] = "dlg:other"
                with self.assertRaisesRegex(ProfileError, "table row"):
                    validate_target_registration(altered, rows)
        self.assertEqual(target_kind(rows["bg2:mode2:target:17"]), "scripted-target")
        direct = deepcopy(rows["bg2:mode2:target:17"])
        direct["area_resource"] = "dlg:uhogre01"
        registration = {"id": "original-target:" + direct["id"], "resource": "dlg:uhogre01",
                        "resource_sha256": "1" * 64, "adapter_id": "original-target-v1",
                        "selector": direct["id"], "capacity": 1, "optional": False,
                        "authored": {key: direct[key] for key in TARGET_FIELDS}}
        with self.assertRaisesRegex(ProfileError, "resource identity"):
            validate_target_registration(registration, {direct["id"]: direct})

    def test_mode2_spin621_pair_binds_only_effective_spell_hash_and_neutral_reward(self):
        import hashlib
        from flir_config.original_catalog import resolve_template
        from flir_config.resources import RESOURCE_TYPES, scan_game
        from flir_config.sources import scan_world
        from test_original_configuration import target_row
        from test_postinstall_resources import make_itm

        spell = b"synthetic spell bytes; deliberately no generalized SPL parser"
        (self.root / "override" / "spin621.spl").write_bytes(spell)
        (self.root / "override" / "ring41.itm").write_bytes(make_itm())
        self.catalog = scan_game(self.root)
        self.world = scan_world(self.catalog)
        self.assertEqual(RESOURCE_TYPES["spl"], 1006)
        resource = next(row for row in self.world["resources"] if row["id"] == "spl:spin621")
        self.assertEqual(resource["sha256"], hashlib.sha256(spell).hexdigest())
        self.assertFalse(any(row["resource"] == "spl:spin621"
                             for row in self.world["sources"] + self.world["targets"]))
        draft = resolve_template(self.catalog, self.world, "bg2-original", items="extended", targets="extended")
        draft["items"] = [row for row in draft["items"] if row["id"] == "bg2:mode2:item:69"]
        draft["targets"] = [row for row in draft["targets"] if row["id"] == "bg2:mode2:target:70"]
        draft["targets"].append(target_row("free", "x", "10"))
        draft["targets"][-1]["campaign"] = "bg2"
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "bg2-original", items="extended", targets="extended")
        body = self.compile(result, draft)
        source = body["sources"][0]
        target = next(row for row in body["targets"] if row["id"] == "original-target:bg2:mode2:target:70")
        self.assertEqual(source["record"]["resource"], "spl:spin621")
        self.assertEqual(source["resource_sha256"], resource["sha256"])
        self.assertEqual(source["record"]["physical_source_ids"], [])
        self.assertEqual(source["record"]["record"], {"resref": "ring41", "expiration": 0,
                                                      "charges": [1, 0, 0], "flags": 0})
        self.assertEqual(target["record"]["kind"], "scripted-target")
        self.assertEqual(target["record"]["resource"], "spl:spin621")
        self.assertEqual(target["resource_sha256"], resource["sha256"])
        for part, field, value in (("sources", "source", "other.spl"),
                                  ("targets", "target", "other.spl"),
                                  ("targets", "area", "ar0001")):
            changed = deepcopy(body)
            if part == "sources":
                changed[part][0]["record"]["authored"][field] = value
            else:
                matching = next(row for row in changed[part] if row["id"] == target["id"])
                matching["record"]["selector"][field] = value
            changed["revision"] = _digest({key: value for key, value in changed.items() if key != "revision"})
            with self.subTest(part=part, field=field), self.assertRaises(ProfileError):
                _validate_campaign(changed)

    def test_authored_target_and_settings_roundtrip(self):
        result, draft = self.configuration()
        body = self.compile(result, draft)
        self.assertEqual(body["original_template_id"], "eet-original")
        self.assertEqual(body["original_choices"], {"items": "standard", "targets": "standard"})
        self.assertEqual(body["original_options"], draft["options"])
        self.assertEqual({row["record"]["kind"] for row in body["targets"]}, {"container"})
        self.assertTrue(all(row["record"]["adapter_id"] == "original-target-v1"
                            for row in body["targets"]))
        self.assertEqual(_validate_campaign(body), body)

    def test_grouped_source_preserves_one_unit_and_rejects_overlap(self):
        from test_postinstall_world_formats import item_record, make_cre
        from flir_config.resources import scan_game
        from flir_config.sources import scan_world

        (self.root / "override" / "group.cre").write_bytes(
            make_cre([item_record(), item_record()]))
        self.catalog = scan_game(self.root)
        self.world = scan_world(self.catalog)
        grouped = sorted(row["id"] for row in self.world["sources"]
                         if row["resource"] == "cre:group")
        draft = self._draft()
        draft["items"] = [draft["items"][0]]
        draft["items"][0].update(id="group-1", source_ids=grouped,
                                 source_resource="cre:group", ident="x")
        result, draft = self.configuration(draft)
        body = self.compile(result, draft)
        source = body["sources"][0]
        self.assertEqual(source["quantity"], 1)
        self.assertEqual(source["record"]["kind"], "authored-source")
        self.assertEqual(source["record"]["physical_source_ids"], grouped)
        self.assertEqual(source["record"]["authored"]["match_policy"], "all_one_unit")
        self.assertEqual(source["record"]["record"]["charges"], [7, 3, 2])

        tampered = deepcopy(result)
        tampered["plan"]["sources"][0]["source_ids"] = [grouped[0]]
        with self.assertRaisesRegex(ProfileError, "overlap"):
            self.compile(tampered, draft)

        tampered = deepcopy(result)
        tampered["plan"]["registrations"][0]["resource_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProfileError, "source resource changed"):
            self.compile(tampered, draft)

    def test_source_and_target_evidence_must_match_table(self):
        result, draft = self.configuration()
        changed = deepcopy(result)
        changed["plan"]["target_registrations"][0]["authored"]["container_name"] = "Wrong"
        with self.assertRaisesRegex(ProfileError, "table row"):
            self.compile(changed, draft)

        changed = deepcopy(result)
        changed["plan"]["target_registrations"][0]["resource_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProfileError, "changed"):
            self.compile(changed, draft)

        changed = deepcopy(result)
        changed["plan"]["target_registrations"][0]["selector"] = "wrong-row"
        with self.assertRaisesRegex(ProfileError, "enabled table row"):
            self.compile(changed, draft)

    def test_single_slotless_creature_compiles_with_exact_evidence_and_direct_binding_stays_blocked(self):
        from test_postinstall_world_formats import item_record, make_cre
        from test_original_configuration import source_row
        from flir_config.resources import scan_game
        from flir_config.sources import scan_world

        (self.root / "override" / "slotless.cre").write_bytes(make_cre([
            item_record("OTHER"), item_record(expiration=0, flags=0)], slots={21: 0}))
        self.catalog = scan_game(self.root)
        self.world = scan_world(self.catalog)
        draft = self._draft()
        authored = source_row("slotless-1", source_ids=["cre:slotless/item/1"],
                              resource="cre:slotless", ident="x")
        authored["source"] = "slotless.cre"
        draft["items"] = [authored]
        result, draft = self.configuration(draft)
        body = self.compile(result, draft)
        source = body["sources"][0]
        self.assertEqual(source["quantity"], 1)
        self.assertEqual(source["record"]["kind"], "authored-source")
        self.assertEqual(source["record"]["physical_source_ids"], ["cre:slotless/item/1"])
        self.assertEqual(source["record"]["authored"]["source"], "slotless.cre")
        self.assertEqual(source["record"]["record"]["charges"], [7, 3, 2])

        for field, value, error in (
                ("physical_source_ids", ["cre:slotless/item/0"], "scanned table row"),
                ("payload", {"resref": "synth001", "expiration": 0,
                             "charges": [1, 3, 2], "flags": 0}, "physical record"),
                ("resource_sha256", "0" * 64, "source resource changed"),
                ("selector", "wrong-row", "enabled table row"),
                ("resource", "cre:wrong", "table row")):
            changed = deepcopy(result)
            changed["plan"]["registrations"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ProfileError, error):
                self.compile(changed, draft)

        direct = deepcopy(result)
        direct["plan"]["registrations"] = []
        direct["plan"]["sources"][0].update(source_ids=["cre:slotless/item/1"], registration_ids=[])
        with self.assertRaisesRegex(ProfileError, "finite, unambiguous"):
            self.compile(direct, draft)

    def test_compiled_authored_rows_reject_rehashed_schema_tampering(self):
        result, draft = self.configuration()
        body = self.compile(result, draft)
        changed = deepcopy(body)
        changed["targets"][0]["record"]["kind"] = "store"
        changed["revision"] = _digest({key: value for key, value in changed.items()
                                       if key != "revision"})
        with self.assertRaisesRegex(ProfileError, "authored campaign target"):
            _validate_campaign(changed)

        changed = deepcopy(body)
        del changed["original_options"]
        with self.assertRaisesRegex(ProfileError, "campaign settings"):
            _validate_campaign(changed)


if __name__ == "__main__":
    unittest.main()
