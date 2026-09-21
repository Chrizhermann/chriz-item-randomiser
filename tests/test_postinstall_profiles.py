"""Hermetic profile/search/preparation tests; no installed game is accessed."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config.profiles import (  # noqa: E402
    ProfileError, canonical_bytes, edit_profile, ensure_output_outside_game,
    new_profile, prepare_profile, read_json, read_prepared, rebase_profile,
    search_items, validate_catalog, validate_profile, write_json,
)
from flir_config.resources import catalog_fingerprint  # noqa: E402
from test_postinstall_resources import make_game, make_itm, snapshot  # noqa: E402


def make_catalog(game: Path) -> dict:
    items = []
    for resref, name, category in (("fla", "Ámber Blade", 20),
                                    ("flb", "Azure Blade", 20),
                                    ("flc", "Amber Ring", 10)):
        items.append({"resref": resref, "name": name, "identified_name": name,
                      "unidentified_name": "Blade" if category == 20 else "Ring",
                      "category": category, "flags": 0, "price": 100,
                      "stack_size": 1, "enchantment": 1, "proficiency": 89,
                      "sha256": hashlib.sha256(resref.encode()).hexdigest(),
                      "resource_origin": "override"})
    catalog = {"schema": "flir-item-catalog-v1", "game_root": str(game.resolve()),
               "language": "en_us", "items": items, "warnings": []}
    catalog["fingerprint"] = catalog_fingerprint(catalog)
    return catalog


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="flir-profile-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.game = self.root / "game"
        self.game.mkdir()
        (self.game / "sentinel").write_bytes(b"game must remain untouched")
        self.catalog = make_catalog(self.game)
        self.profile = new_profile(self.catalog, "adventure", "Next adventure")

    def test_search_is_bounded_and_supports_names_and_ids(self) -> None:
        first = search_items(self.catalog, "blade", limit=1)
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["items"][0]["resref"], "fla")
        self.assertEqual(first["next_offset"], 1)
        second = search_items(self.catalog, "blade", offset=1, limit=1)
        self.assertEqual(second["items"][0]["resref"], "flb")
        self.assertIsNone(second["next_offset"])
        self.assertEqual(search_items(self.catalog, "ÁMBER")["total"], 1)
        self.assertEqual(search_items(self.catalog, "FLC")["items"][0]["name"], "Amber Ring")
        self.assertEqual(search_items(self.catalog, "", category=10)["total"], 1)
        with self.assertRaises(ProfileError):
            search_items(self.catalog, "")
        for offset, limit in ((-1, 20), (0, 0), (0, 101), (False, 20), (0, True)):
            with self.assertRaises(ProfileError):
                search_items(self.catalog, "blade", offset=offset, limit=limit)

    def test_edit_does_not_alias_catalog_or_previous_profile(self) -> None:
        original_catalog = deepcopy(self.catalog)
        updated = edit_profile(self.profile, self.catalog, include=["FLA", "flb", "fla"])
        self.assertEqual(updated["items"], ["fla", "flb"])
        self.assertEqual(self.profile["items"], [])
        excluded = edit_profile(updated, self.catalog, exclude=["fla"])
        self.assertEqual(excluded["items"], ["flb"])
        self.assertEqual(updated["items"], ["fla", "flb"])
        results = search_items(self.catalog, "fla")
        results["items"][0]["name"] = "Changed in caller"
        self.assertEqual(self.catalog, original_catalog)

    def test_unknown_and_conflicting_edits_fail(self) -> None:
        for kwargs in ({"include": ["missing"]}, {"exclude": ["missing"]},
                       {"include": ["fla"], "exclude": ["FLA"]}):
            with self.assertRaises(ProfileError):
                edit_profile(self.profile, self.catalog, **kwargs)

    def test_changed_item_bytes_invalidate_profile_until_explicit_rebase(self) -> None:
        selected = edit_profile(self.profile, self.catalog, include=["fla"])
        changed = deepcopy(self.catalog)
        changed["items"][0]["sha256"] = "a" * 64
        changed["fingerprint"] = catalog_fingerprint(changed)
        with self.assertRaises(ProfileError):
            prepare_profile(selected, changed, self.root / "drafts")
        rebased = rebase_profile(selected, changed)
        self.assertEqual(rebased["catalog_fingerprint"], changed["fingerprint"])
        self.assertEqual(selected["catalog_fingerprint"], self.catalog["fingerprint"])

    def test_rebase_never_silently_drops_absent_items(self) -> None:
        selected = edit_profile(self.profile, self.catalog, include=["fla"])
        changed = deepcopy(self.catalog)
        changed["items"] = changed["items"][1:]
        changed["fingerprint"] = catalog_fingerprint(changed)
        with self.assertRaisesRegex(ProfileError, "absent"):
            rebase_profile(selected, changed)

    def test_catalog_tampering_and_malformed_fields_fail(self) -> None:
        changed = deepcopy(self.catalog)
        changed["items"][0]["name"] = "Unexpected edit"
        with self.assertRaisesRegex(ProfileError, "changed"):
            validate_catalog(changed)
        for field, value in (("items", {}), ("game_root", "relative"), ("fingerprint", "bad")):
            changed = deepcopy(self.catalog)
            changed[field] = value
            with self.assertRaises(ProfileError):
                validate_catalog(changed)
        changed = deepcopy(self.catalog)
        changed["items"][0]["category"] = True
        with self.assertRaises(ProfileError):
            validate_catalog(changed)

    def test_profile_schema_and_duplicates_fail(self) -> None:
        for update in ({"schema": "future"}, {"id": "../escape"}, {"name": " "},
                       {"items": ["fla", "fla"]}, {"items": ["flb", "fla"]},
                       {"items": [4]}, {"catalog_fingerprint": "broken"}, {"extra": 1},
                       {"name": "bad\ud800"}, {"name": "line\nbreak"}):
            changed = deepcopy(self.profile)
            changed.update(update)
            with self.assertRaises(ProfileError):
                validate_profile(changed, self.catalog)

    def test_preparing_two_configurations_preserves_first_immutable_revision(self) -> None:
        store = self.root / "drafts"
        first_profile = edit_profile(self.profile, self.catalog, include=["fla", "flb"])
        first = prepare_profile(first_profile, self.catalog, store)
        first_bytes = Path(first["revision_path"]).read_bytes()
        same = prepare_profile(first_profile, self.catalog, store)
        self.assertEqual(first, same)
        second_profile = edit_profile(first_profile, self.catalog, exclude=["flb"])
        second = prepare_profile(second_profile, self.catalog, store)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(Path(first["revision_path"]).read_bytes(), first_bytes)
        self.assertEqual(read_prepared(store)["profile"]["items"], ["fla"])
        self.assertEqual(read_prepared(store, first["revision"])["profile"]["items"], ["fla", "flb"])
        self.assertEqual(first["state"], "draft-needs-source-binding")
        self.assertEqual(list(self.game.iterdir()), [self.game / "sentinel"])
        self.assertEqual((self.game / "sentinel").read_bytes(), b"game must remain untouched")

    def test_preparation_rejects_empty_profile_and_game_output(self) -> None:
        with self.assertRaises(ProfileError):
            prepare_profile(self.profile, self.catalog, self.root / "drafts")
        selected = edit_profile(self.profile, self.catalog, include=["fla"])
        for target in (self.game, self.game / "config", self.game / "override" / "draft"):
            with self.assertRaises(ProfileError):
                prepare_profile(selected, self.catalog, target)
        self.assertFalse((self.game / "config").exists())

    def test_linked_output_into_game_is_rejected(self) -> None:
        link = self.root / "linked"
        try:
            link.symlink_to(self.game, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable on this host: {exc}")
        with self.assertRaises(ProfileError):
            ensure_output_outside_game(link / "draft.json", self.catalog)

    def test_corrupt_immutable_revision_is_not_replaced(self) -> None:
        store = self.root / "drafts"
        selected = edit_profile(self.profile, self.catalog, include=["fla"])
        prepared = prepare_profile(selected, self.catalog, store)
        revision_path = Path(prepared["revision_path"])
        revision_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ProfileError):
            prepare_profile(selected, self.catalog, store)
        self.assertEqual(revision_path.read_text(), "{}")
        with self.assertRaises(ProfileError):
            read_prepared(store, prepared["revision"])
        with self.assertRaises(ProfileError):
            read_prepared(store, "../../outside")

    def test_pointer_is_unchanged_when_revision_publication_fails(self) -> None:
        store = self.root / "drafts"
        first_profile = edit_profile(self.profile, self.catalog, include=["fla"])
        first = prepare_profile(first_profile, self.catalog, store)
        before = Path(first["pointer_path"]).read_bytes()
        second_profile = edit_profile(first_profile, self.catalog, include=["flb"])
        with patch("flir_config.profiles.os.replace", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                prepare_profile(second_profile, self.catalog, store)
        self.assertEqual(Path(first["pointer_path"]).read_bytes(), before)
        self.assertEqual(list((store / "revisions").glob(".flir-*.tmp")), [])

    def test_preparation_ignores_nonsemantic_packaging_metadata(self) -> None:
        selected = edit_profile(self.profile, self.catalog, include=["fla"])
        first = prepare_profile(selected, self.catalog, self.root / "drafts")
        self.catalog["items"][0]["resource_origin"] = "biff"
        self.catalog["items"][0]["packaging_detail"] = "irrelevant"
        second = prepare_profile(selected, self.catalog, self.root / "drafts")
        self.assertEqual(first["revision"], second["revision"])

    def test_json_loader_rejects_duplicates_and_nonfinite_numbers(self) -> None:
        path = self.root / "bad.json"
        for raw in ('{"id":"first","id":"second"}', '{"x":NaN}', '{"x":Infinity}',
                    '{"x":1e999}', '{"x":-1e999}', '{"x":"\\ud800"}',
                    '{"x":' + '9' * 5000 + '}', '{"x":' + '[' * 1500 + '0' + ']' * 1500 + '}', '[]'):
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ProfileError):
                read_json(path)

    def test_command_workflow_and_stale_scan_error_are_structured(self) -> None:
        catalog_path = self.root / "catalog.json"
        profile_path = self.root / "profile.json"
        write_json(catalog_path, self.catalog)

        def invoke(*arguments: str, expected: int = 0) -> dict:
            result = subprocess.run([sys.executable, str(ROOT / "tools" / "randomiser_config.py"),
                                     *map(str, arguments)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, expected, result.stderr)
            output = json.loads(result.stdout if expected == 0 else result.stderr)
            self.assertEqual(output["ok"], expected == 0)
            return output

        invoke("profile", "create", "--catalog", catalog_path, "--id", "first", "--name", "First run",
               "--out", profile_path)
        invoke("profile", "edit", "--catalog", catalog_path, "--profile", profile_path,
               "--include", "fla", "--out", profile_path)
        searched = invoke("search", "--catalog", catalog_path, "--query", "blade", "--limit", "1")
        self.assertEqual(len(searched["result"]["items"]), 1)
        prepared = invoke("prepare", "--catalog", catalog_path, "--profile", profile_path,
                          "--store", self.root / "drafts")
        self.assertEqual(prepared["result"]["state"], "draft-needs-source-binding")
        invoke("inspect", "--store", self.root / "drafts")
        changed = deepcopy(self.catalog)
        changed["items"][0]["price"] += 1
        changed["fingerprint"] = catalog_fingerprint(changed)
        write_json(catalog_path, changed)
        error = invoke("profile", "validate", "--catalog", catalog_path, "--profile", profile_path, expected=1)
        self.assertIn("another scan", error["error"])

    def test_malformed_json_commands_return_structured_failures(self) -> None:
        path = self.root / "bad.json"
        for raw in ('{"x":1e999}', '{"x":"\\ud800"}', '{"x":' + '9' * 5000 + '}'):
            path.write_text(raw, encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "tools" / "randomiser_config.py"),
                                     "search", "--catalog", str(path), "--query", "test"],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stderr)["ok"])
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_scan_search_prepare_end_to_end_and_refuse_game_output(self) -> None:
        game = self.root / "synthetic-game"
        make_game(game)
        (game / "override" / "synth001.itm").write_bytes(make_itm(price=800))
        before = snapshot(game)
        output = self.root / "scanned.json"

        def invoke(*arguments: str, expected: int = 0) -> dict:
            result = subprocess.run([sys.executable, str(ROOT / "tools" / "randomiser_config.py"),
                                     *map(str, arguments)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, expected, result.stderr)
            return json.loads(result.stdout if expected == 0 else result.stderr)

        scanned = invoke("scan", "--game", game, "--out", output)
        self.assertEqual(scanned["result"]["item_count"], 1)
        searched = invoke("search", "--catalog", output, "--query", "SYNTH001")
        self.assertEqual(searched["result"]["items"][0]["price"], 800)
        catalog = read_json(output)
        selected = edit_profile(new_profile(catalog, "test", "Synthetic run"), catalog,
                                include=["synth001"])
        prepared = prepare_profile(selected, catalog, self.root / "prepared")
        self.assertEqual(read_prepared(self.root / "prepared", prepared["revision"])["items"][0]["price"], 800)
        refused = invoke("scan", "--game", game, "--out", game / "output.json", expected=1)
        self.assertFalse(refused["ok"])
        self.assertEqual(snapshot(game), before)


if __name__ == "__main__":
    unittest.main()
