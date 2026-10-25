"""Portable original-table binding checks; no installed game is read."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.original_catalog import resolve_template  # noqa: E402
from sod_table_fixtures import sod_tables  # noqa: E402


def _source(identity, resource, item, owner):
    return {"id": identity, "resource": resource, "item": item, "owner": owner}


def _target(identity, resource, kind, name="", template=""):
    return {"id": identity, "resource": resource, "kind": kind,
            "selector": {"name": name, "template_resource": template}}


class OriginalCatalogTests(unittest.TestCase):
    def test_registered_mode2_aliases_use_primary_dialogue_and_require_all_branches(self):
        from flir_config.templates import build_template

        names = ("dlg:hendak", "dlg:lehtin", "dlg:bhoisig", "dlg:bhnalla", "dlg:bharval",
                 "are:ar1100", "dlg:uhogre01")
        world = {"resources": [{"id": name} for name in names], "sources": [], "targets": []}
        draft = build_template("bg2-original", items="extended", targets="extended")
        authored = {row["id"]: row for row in draft["targets"]}
        result = resolve_template({"items": []}, world, "bg2-original", items="extended", targets="extended")
        rows = {row["id"]: row for row in result["targets"]}
        for line, resource, alias, area in ((18, "dlg:hendak", "copper.fl", "ar0406"),
                                             (48, "dlg:bhoisig", "templemess.fl", "blank")):
            identity = f"bg2:mode2:target:{line}"
            row = rows[identity]
            self.assertEqual(row["area_resource"], resource)
            self.assertEqual(row["target"], alias)
            self.assertEqual(row["area"], area)
            self.assertEqual({key: row[key] for key in authored[identity]}, authored[identity])
            self.assertEqual(row["target_ids"], [])
            self.assertIsNone(row["container_name"])
            self.assertEqual(row["binding_problem"], "legacy-target-requires-adapter")
        # Direct Mode 2 dialogues retain their physical area anchor.
        self.assertEqual(rows["bg2:mode2:target:17"]["area_resource"], "are:ar1100")
        for missing in ("dlg:hendak", "dlg:lehtin", "dlg:bhnalla"):
            absent = {**world, "resources": [r for r in world["resources"] if r["id"] != missing]}
            bound = resolve_template({"items": []}, absent, "bg2-original", items="extended", targets="extended")
            identity = "bg2:mode2:target:48" if missing == "dlg:bhnalla" else "bg2:mode2:target:18"
            with self.subTest(missing=missing):
                self.assertEqual(next(r for r in bound["targets"] if r["id"] == identity)
                                 ["binding_problem"], "target-resource-absent")

    def test_preset_items_from_other_mods_are_absent_rather_than_unfit(self):
        world = {"resources": [], "sources": [], "targets": []}
        problems = {}
        for row in resolve_template({"items": []}, world, "bg1-boss-loot", items="extended", targets="extended")["items"]:
            problems.setdefault(row["optional_mod"], set()).add(row["binding_problem"])
        # The conversation rewards have the original lists' routes.
        self.assertEqual(problems, {"Rogue Rebalancing": {"optional-item-absent"},
                                    "IWDification": {"optional-item-absent"},
                                    None: {"item-definition-absent"}})

    def test_exact_mode2_spin621_pair_uses_spell_hash_without_area_or_physical_target(self):
        catalog = {"items": [{"resref": "ring41"}]}
        world = {"resources": [{"id": "spl:spin621"}], "sources": [], "targets": []}
        result = resolve_template(catalog, world, "bg2-original", items="extended", targets="extended")
        source = next(row for row in result["items"] if row["id"] == "bg2:mode2:item:69")
        target = next(row for row in result["targets"] if row["id"] == "bg2:mode2:target:70")
        self.assertEqual(source["source_resource"], "spl:spin621")
        self.assertEqual(source["adapter"], "scripted")
        self.assertEqual(source["source_ids"], [])
        self.assertEqual(source["binding_problem"], "scripted-source-requires-adapter")
        self.assertEqual(target["area_resource"], "spl:spin621")
        self.assertEqual(target["target_ids"], [])
        self.assertIsNone(target["container_name"])
        self.assertEqual(target["binding_problem"], "legacy-target-requires-adapter")
        missing = resolve_template(catalog, {"resources": [], "sources": [], "targets": []},
                                   "bg2-original", items="extended", targets="extended")
        self.assertEqual(next(row for row in missing["items"] if row["id"] == source["id"])
                         ["availability_reason"], "source-resource-absent")
        self.assertEqual(next(row for row in missing["targets"] if row["id"] == target["id"])
                         ["binding_problem"], "target-resource-absent")

    def test_duplicate_container_names_use_original_coordinates_not_pile_type(self):
        targets = [_target(f"are:ar1202/container/{n}", "are:ar1202", "container", "Container 11")
                   for n in (9, 10)]
        targets[0]["selector"].update(x=3431, y=170, type=8)
        targets[1]["selector"].update(x=845, y=518, type=8)
        world = {"resources": [{"id": "are:ar1202"}], "sources": [], "targets": targets}
        result = resolve_template({"items": []}, world, "bg2-original")
        row = next(row for row in result["targets"] if row["id"] == "bg2:base:target:77")
        self.assertEqual(row["target_ids"], ["are:ar1202/container/10"])
        self.assertIsNone(row["binding_problem"])

    def test_ar1202_table_and_closet_use_authored_coordinates_after_name_shift(self):
        table = _target("are:ar1202/container/7", "are:ar1202", "container", "Container 9")
        table["selector"].update(x=2103, y=812, type=5)
        closet = _target("are:ar1202/container/8", "are:ar1202", "container", "Container 10")
        closet["selector"].update(x=1949, y=716, type=3)
        world = {"resources": [{"id": "are:ar1202"}], "sources": [],
                 "targets": [table, closet]}
        result = resolve_template({"items": []}, world, "bg2-original", options={"scrolls": True})
        rows = {row["id"]: row for row in result["targets"]}
        # The scroll's absent Container8 name is the table. The closet's old
        # Container9 name is misleading, so its exact name match cannot win.
        self.assertEqual(rows["bg2:base:target:250"]["target_ids"], [table["id"]])
        for line in (74, 248, 279):
            row = rows[f"bg2:base:target:{line}"]
            self.assertEqual(row["target_ids"], [closet["id"]])
            self.assertIsNone(row["binding_problem"])
        self.assertIsNone(rows["bg2:base:target:250"]["binding_problem"])

    def test_ar1202_coordinate_aliases_require_one_exact_location(self):
        wrong = _target("are:ar1202/container/7", "are:ar1202", "container", "Container 8")
        wrong["selector"].update(x=2104, y=812, type=5)
        world = {"resources": [{"id": "are:ar1202"}], "sources": [], "targets": [wrong]}
        result = resolve_template({"items": []}, world, "bg2-original", options={"scrolls": True})
        row = next(row for row in result["targets"] if row["id"] == "bg2:base:target:250")
        self.assertEqual(row["target_ids"], [])
        self.assertEqual(row["binding_problem"], "target-not-in-scan")
        wrong["selector"]["x"] = 2103
        duplicate = _target("are:ar1202/container/8", "are:ar1202", "container", "Other table")
        duplicate["selector"].update(x=2103, y=812, type=5)
        world["targets"].append(duplicate)
        result = resolve_template({"items": []}, world, "bg2-original", options={"scrolls": True})
        row = next(row for row in result["targets"] if row["id"] == "bg2:base:target:250")
        self.assertEqual(row["target_ids"], [wrong["id"], duplicate["id"]])
        self.assertEqual(row["binding_problem"], "ambiguous-target-records")

    def test_other_ar1202_endpoints_keep_unique_name_precedence(self):
        named = _target("are:ar1202/container/0", "are:ar1202", "container", "Container 5")
        named["selector"].update(x=999, y=999, type=3)
        pointed = _target("are:ar1202/container/1", "are:ar1202", "container", "Other closet")
        pointed["selector"].update(x=832, y=2057, type=3)
        world = {"resources": [{"id": "are:ar1202"}], "sources": [],
                 "targets": [named, pointed]}
        result = resolve_template({"items": []}, world, "bg2-original")
        row = next(row for row in result["targets"] if row["id"] == "bg2:base:target:8")
        self.assertEqual(row["target_ids"], [named["id"]])
        self.assertIsNone(row["binding_problem"])

    def test_spellhold_scroll_source_aliases_are_exact_and_unambiguous(self):
        catalog = {"items": [{"resref": "scrl6u"}]}
        tool = "are:ar0411/container/2/item/11"
        body = "are:ar0411/container/6/item/38"
        world = {
            "resources": [{"id": "are:ar0411"}],
            "sources": [_source(tool, "are:ar0411", "scrl6u",
                                "are:ar0411/container/2"),
                        _source(body, "are:ar0411", "scrl6u",
                                "are:ar0411/container/6")],
            "targets": [_target("are:ar0411/container/2", "are:ar0411",
                                "container", "Tool CT 1"),
                        _target("are:ar0411/container/6", "are:ar0411",
                                "container", "Body 1")],
        }
        result = resolve_template(catalog, world, "bg2-original", options={"scrolls": True})
        rows = {row["source_ref"]: row for row in result["items"]}
        self.assertEqual(rows["lists/items/base/bg2.2da:255"]["source_ids"], [tool])
        self.assertEqual(rows["lists/items/base/bg2.2da:256"]["source_ids"], [body])
        self.assertIsNone(rows["lists/items/base/bg2.2da:255"]["binding_problem"])
        # An original exact selector must win over the reviewed EET alias.
        exact = "are:ar0411/container/8/item/50"
        world["sources"].append(_source(exact, "are:ar0411", "scrl6u",
                                        "are:ar0411/container/8"))
        world["targets"].append(_target("are:ar0411/container/8", "are:ar0411",
                                        "container", "Tool CT 13"))
        result = resolve_template(catalog, world, "bg2-original", options={"scrolls": True})
        row = next(row for row in result["items"] if row["source_ref"].endswith(":255"))
        self.assertEqual(row["source_ids"], [exact])

    def test_repeated_sources_selectors_and_unavailable_rows(self):
        catalog = {"items": [{"resref": name} for name in
                             ("brac01", "scrl68", "scrl87", "shld02", "ring08_")]}
        world = {
            "resources": [{"id": name} for name in
                          ("sto:inn3351", "sto:taerum", "are:bg4701",
                           "cre:arkush", "are:bg1803", "are:bg2300")],
            "sources": [
                _source("sto:inn3351/item/6", "sto:inn3351", "brac01", "sto:inn3351"),
                _source("are:bg4701/container/0/item/2", "are:bg4701", "brac01",
                        "are:bg4701/container/0"),
                _source("cre:arkush/item/2", "cre:arkush", "scrl68", "cre:arkush"),
                _source("cre:arkush/item/7", "cre:arkush", "scrl68", "cre:arkush"),
                _source("are:bg1803/container/3/item/8", "are:bg1803", "scrl87",
                        "are:bg1803/container/3"),
                _source("are:bg1803/container/9/item/23", "are:bg1803", "scrl87",
                        "are:bg1803/container/9"),
                _source("are:bg2300/container/0/item/0", "are:bg2300", "ring08_",
                        "are:bg2300/container/0"),
            ],
            "targets": [
                _target("are:bg1803/container/3", "are:bg1803", "container", "Container 4"),
                _target("are:bg1803/container/9", "are:bg1803", "container", "Container 11"),
            ],
        }
        result = resolve_template(catalog, world, "bg1-original", options={"scrolls": True})
        rows = {row["source_ref"].rsplit(":", 1)[1]: row for row in result["items"]}
        self.assertEqual(len(rows), 212)
        self.assertEqual(rows["3"]["source_ids"], ["sto:inn3351/item/6"])
        self.assertEqual(rows["4"]["source_resource"], "are:bg4701")
        self.assertEqual(rows["4"]["source_ids"], ["are:bg4701/container/0/item/2"])
        self.assertEqual(rows["83"]["item_resref"], "ring08_")
        self.assertEqual(rows["83"]["source_ids"], ["are:bg2300/container/0/item/0"])
        self.assertEqual(rows["134"]["source_ids"],
                         ["cre:arkush/item/2", "cre:arkush/item/7"])
        self.assertIsNone(rows["134"]["binding_problem"])
        self.assertEqual(rows["153"]["source_ids"], ["are:bg1803/container/3/item/8"])
        self.assertEqual(rows["154"]["source_ids"], ["are:bg1803/container/9/item/23"])
        self.assertTrue(rows["12"]["enabled"])
        self.assertFalse(rows["12"]["available"])
        self.assertEqual(rows["12"]["availability_reason"],
                         "original-source-has-no-matching-record")
        self.assertEqual(rows["49"]["adapter"], "unshey")
        self.assertEqual(rows["49"]["source_resource"], "cre:ogreunsh")

    def test_target_static_dynamic_group_and_mode2_rows(self):
        catalog = {"items": []}
        world = {
            "resources": [{"id": name} for name in
                          ("are:bg5500", "are:bg4801", "are:bg1900", "are:bg0609",
                           "are:bg0615", "cre:neira", "cre:taugos", "cre:jalant")],
            "sources": [],
            "targets": [
                _target("are:bg5500/container/0", "are:bg5500", "container", "Container 1"),
                _target("are:bg4801/actor/5", "are:bg4801", "actor", "Neira", "cre:neira"),
                _target("are:bg1900/actor/0", "are:bg1900", "actor", "Taurgosz Khosann",
                        "cre:taugos"),
                _target("are:bg0615/actor/5", "are:bg0615", "actor", "Gardush",
                        "cre:gardus"),
            ],
        }
        result = resolve_template(catalog, world, "bg1-original", items="extended",
                                  targets="extended")
        rows = {row["source_ref"]: row for row in result["targets"]}
        container = rows["lists/locations/base/bg1.2da:3"]
        self.assertEqual(container["area_resource"], "are:bg5500")
        self.assertEqual(container["container_name"], "container1")
        self.assertEqual(container["target_ids"], ["are:bg5500/container/0"])
        neira = rows["lists/locations/base/bg1.2da:6"]
        self.assertEqual(neira["creature_resource"], "cre:neira")
        self.assertEqual(neira["target_ids"], ["are:bg4801/actor/5"])
        taurgosz = rows["lists/locations/base/bg1.2da:56"]
        self.assertEqual(taurgosz["target_ids"], ["are:bg1900/actor/0"])
        jalantha = rows["lists/locations/base/bg1.2da:30"]
        self.assertEqual(jalantha["creature_resource"], "cre:jalant")
        self.assertEqual(jalantha["binding_problem"], "creature-not-static-in-area")
        group = rows["lists/locations/base/bg1.2da:31"]
        self.assertIn("cre:gardus", group["members"])
        self.assertEqual(group["target_ids"], ["are:bg0615/actor/5"])
        self.assertEqual(group["binding_problem"], "group-requires-runtime-selection")
        self.assertTrue(any(row["family"] == "mode2" and "target_ids" in row
                            for row in result["targets"]))

    def test_baldurs_gate_two_does_not_convert_area(self):
        catalog = {"items": [{"resref": "ring08"}]}
        world = {"resources": [{"id": "cre:jaga3"}], "sources": [], "targets": []}
        result = resolve_template(catalog, world, "bg2-original")
        row = next(row for row in result["items"] if row["source_ref"].endswith(":94"))
        self.assertEqual(row["item_resref"], "ring08")
        self.assertEqual(row["source_resource"], "cre:jaga3")
        self.assertFalse(row["available"])
        target = next(row for row in result["targets"] if row["area"] == "ar0202")
        self.assertEqual(target["area_resource"], "are:ar0202")

    def test_sod_rows_bind_by_place_name_and_follow_eets_lists(self):
        from flir_config.game_names import EetNames
        from flir_config.templates import build_template

        with sod_tables():
            draft = build_template("sod-original")
            item = draft["items"][0]
            place = next(row for row in draft["targets"] if row["ident"] == item["ident"])
            area = f"are:{place['area']}"
            for game, names, resref in (("bgee", None, item["resref"]),
                                        ("eet", EetNames({kind: {} for kind in ("itm", "cre", "sto", "dlg", "bcs")}
                                                         | {"itm": {item["resref"]: "renamed"}}, True), "renamed")):
                with self.subTest(game=game):
                    chest = _target(f"{area}/container/0", area, "container", place["target"].upper())
                    spare = _target(f"{area}/container/1", area, "container", "Other")
                    world = {"resources": [{"id": area}], "targets": [chest, spare],
                             "sources": [_source(f"{area}/container/0/item/0", area, resref, chest["id"])]}
                    result = resolve_template({"items": [{"resref": resref}]}, world, "sod-original",
                                              game=game, names=names)
                    source = next(row for row in result["items"] if row["id"] == item["id"])
                    self.assertEqual((source["item_resref"], source["source_resource"], source["source_ids"],
                                      source["binding_problem"]),
                                     (resref, area, [f"{area}/container/0/item/0"], None))
                    target = next(row for row in result["targets"] if row["id"] == place["id"])
                    self.assertEqual((target["area_resource"], target["target_ids"], target["endpoint_id"],
                                      target["binding_problem"]), (area, [chest["id"]], None, None))

    def test_creature_target_that_never_appears_is_named(self):
        from flir_config.templates import build_template

        place = next(row for row in build_template("bg1-original")["targets"] if row["target"] == "neira.cre")
        area = f"are:{place['area']}"
        for schedules, problem in (((0,), "creature-never-appears"), ((0, 0), "creature-never-appears"),
                                   ((0xFFFFFF,), None),
                                   # One placement appears by day: as before, two placements.
                                   ((0, 0x000FFFC0), "ambiguous-target-records")):
            with self.subTest(schedules=schedules):
                actors = []
                for index, schedule in enumerate(schedules):
                    actor = _target(f"{area}/actor/{index}", area, "actor", "Neira", "cre:neira")
                    actor["selector"]["appearance_schedule"] = schedule
                    actors.append(actor)
                world = {"resources": [{"id": area}, {"id": "cre:neira"}], "sources": [], "targets": actors}
                result = resolve_template({"items": []}, world, "bg1-original", game="bgee")
                row = next(row for row in result["targets"] if row["id"] == place["id"])
                self.assertEqual(row["target_ids"], [actor["id"] for actor in actors])
                self.assertEqual(row["binding_problem"], problem)

    def test_quest_places_are_named_not_taken_for_containers(self):
        world = {"resources": [{"id": name} for name in ("are:ar1103", "are:ar2300", "are:ar1514",
                                                         "are:ar0801", "are:ar1100", "dlg:uhogre01")],
                 "sources": [],
                 # A chest named like the place is not the place.
                 "targets": [_target("are:ar1514/container/0", "are:ar1514", "container", "pptroll"),
                             _target("are:ar1103/container/0", "are:ar1103", "container", "jugjer01")]}
        result = resolve_template({"items": []}, world, "bg2-original", items="extended", targets="extended")
        rows = {row["id"]: row for row in result["targets"]}
        # Item Randomiser's chapter 3 places are its vampires and thieves
        # (chapter3.py), whom the export finds; this game lacks their files.
        for line in (25, 49):
            row = rows[f"bg2:mode2:target:{line}"]
            with self.subTest(target=row["target"], area=row["area"]):
                self.assertEqual((row["area_resource"], row["target_ids"], row["container_name"],
                                  row["binding_problem"]),
                                 ("are:ar0801", [], None, "group-requires-runtime-selection"))
                self.assertEqual(row["members"][:3], ["cre:lassal02", "cre:bodvam01", "cre:bodvam02"])
        # The painting troll's place is the troll, whom this game lacks.
        row = rows["bg2:mode2:target:37"]
        self.assertEqual((row["target_ids"], row["container_name"], row["creature_resource"], row["binding_problem"]),
                         ([], None, "cre:pptroll1", "creature-resource-absent"))
        # The quest places CIR has a route for keep theirs, a handler's place too
        # (the Sahuagin treasury's, king's and prince's, the king's or prince's,
        # Jermien's and Tolgerias's rewards; this game lacks their conversations).
        self.assertEqual(rows["bg2:base:target:47"]["binding_problem"], "legacy-target-requires-adapter")
        self.assertEqual(rows["bg2:mode2:target:17"]["binding_problem"], "legacy-target-requires-adapter")
        for line, dialogue in ((6, "dlg:sahkng01"), (58, "dlg:sahkng01"), (59, "dlg:sahpr2"),
                               (60, "dlg:sahpr2"), (31, "dlg:jugjer01"), (50, "dlg:tolger")):
            row = rows[f"bg2:mode2:target:{line}"]
            self.assertEqual((row["area_resource"], row["target_ids"], row["container_name"], row["binding_problem"]),
                             (dialogue, [], None, "target-resource-absent"))

    def test_the_treasurys_place_needs_the_kings_reply_and_one_treasury(self):
        """Row 6's reward waits in the AR2300 container whose script is
        SAHTREAS until the king's reward reply hands it over."""
        def treasury(identity, name, script="sahtreas"):
            target = _target(identity, "are:ar2300", "container", name)
            target["selector"]["trap_script"] = script
            return target

        def place(resources, targets, unreadable=()):
            world = {"resources": [{"id": name} for name in resources], "sources": [], "targets": targets,
                     "unreadable": [{"id": name} for name in unreadable]}
            result = resolve_template({"items": []}, world, "bg2-original", items="extended", targets="extended")
            row = next(row for row in result["targets"] if row["id"] == "bg2:mode2:target:6")
            return row["area_resource"], row["target_ids"], row["container_name"], row["binding_problem"]

        king = ("dlg:sahkng01", "are:ar2300")
        hoard = treasury("are:ar2300/container/7", "Hoard")
        chest = treasury("are:ar2300/container/1", "Treasury", "impches3")
        routed = ("dlg:sahkng01", [], None, "legacy-target-requires-adapter")
        # Wherever a mod moved or renamed it; SAHTREAS itself may be absent.
        for resources in (king, king + ("bcs:sahtreas",)):
            with self.subTest(resources=resources):
                self.assertEqual(place(resources, [chest, hoard]), routed)
        absent = ("dlg:sahkng01", [], None, "target-resource-absent")
        self.assertEqual(place(("are:ar2300",), [hoard]), absent)
        self.assertEqual(place(("dlg:sahkng01",), []), absent)
        self.assertEqual(place(king, [hoard], unreadable=("are:ar2300",)),
                         ("dlg:sahkng01", [], None, "area-resource-unreadable"))
        # No treasury, or two that it could be.
        for targets in ([chest], [hoard, treasury("are:ar2300/container/8", "Treasury")]):
            with self.subTest(targets=len(targets)):
                self.assertEqual(place(king, targets), ("dlg:sahkng01", [], None, "treasure-container-not-found"))

    def test_the_princes_place_is_his_reply_that_ends_his_job(self):
        """Row 59's reward waits on the prince, whose dialogue hands it over
        in each reply that ends his job (mode2_rewards: sahpr2), and so does
        row 60's, the king's or the prince's (sahpr2b)."""
        from flir_config.original_registrations import target_kind

        for template in ("bg2-original", "eet-original"):
            for resources, problem in ((("dlg:sahpr2",), "legacy-target-requires-adapter"),
                                       (("are:ar2300", "cre:sahpr2"), "target-resource-absent")):
                world = {"resources": [{"id": name} for name in resources], "sources": [], "targets": []}
                result = resolve_template({"items": []}, world, template, items="extended", targets="extended")
                for line, handler in ((59, "tadpoles2.fl"), (60, "tadpoles3.fl")):
                    with self.subTest(template=template, resources=resources, line=line):
                        row = next(row for row in result["targets"] if row["id"] == f"bg2:mode2:target:{line}")
                        self.assertEqual((row["target"], row["area"]), (handler, "ar2300"))
                        self.assertEqual((row["area_resource"], row["target_ids"], row["container_name"],
                                          row["binding_problem"]), ("dlg:sahpr2", [], None, problem))
                        self.assertEqual(target_kind(row), "creature-target")

    def test_quest_rewards_with_a_cir_route_are_bound_to_it(self):
        catalog = {"items": [{"resref": name} for name in ("shld26", "blun23", "blun12", "ring41", "brac19",
                                                            "sw1h26", "scrl9b")]}
        world = {"resources": [{"id": name} for name in ("dlg:uhogre01", "bcs:troalt", "bcs:blpool",
                                                         "spl:spin621", "dlg:sahkng01", "dlg:jugjer01",
                                                         "bcs:ppbook")],
                 "sources": [], "targets": []}
        result = resolve_template(catalog, world, "bg2-original", items="extended", targets="extended",
                                  options={"scrolls": True})
        rows = {row["id"]: row for row in result["items"]}
        # Conversations, the altar, the pool, the Deck of Many Things and the
        # Spellhold book's scrolls have routes.
        for identity in ("bg2:mode2:item:14", "bg2:mode2:item:31", "bg2:mode2:item:57", "bg2:base:item:9",
                         "bg2:base:item:90", "bg2:mode2:item:69", "bg2:mode2:item:260"):
            self.assertEqual(rows[identity]["binding_problem"], "scripted-source-requires-adapter")
        # The game lacks the book's other scroll: that row is left out for its
        # item, as any other.
        row = rows["bg2:mode2:item:262"]
        self.assertEqual((row["adapter"], row["available"], row["binding_problem"], row["availability_reason"]),
                         ("scripted", False, "item-definition-absent", "item-definition-absent"))

    def test_no_list_row_waits_for_a_cir_route(self):
        """No row is left out in every game because CIR cannot change its quest
        yet: a row of a handler without a route would show here."""
        from flir_config.templates import list_templates

        options = dict.fromkeys(("scrolls", "kangaxx", "flail_heads", "totsc_tome",
                                 "more_spell_shield", "cursed_disguise"), True)
        world = {"resources": [], "sources": [], "targets": []}
        waiting = set()
        for template in list_templates():
            result = resolve_template({"items": []}, world, template["id"], items="extended",
                                      targets="extended", options=options)
            waiting |= {row["id"] for row in result["items"] + result["targets"]
                        if row["binding_problem"] == "route-not-supported"}
        # Every place that Item Randomiser's own quest handlers name has one.
        self.assertEqual(waiting, set())

    def test_a_troll_in_several_versions_is_the_place_of_the_one_in_the_area(self):
        """Item Randomiser's pptroll.fl and torgal.fl give the item to each
        version of the troll; the EE games bring in only the first, which
        stands in the area (original_catalog.CREATURE_VERSIONS)."""
        from flir_config.original_registrations import target_kind

        for line, area, first, other in ((37, "are:ar1514", "pptroll1", "pptroll2"),
                                         (89, "are:ar1301", "torgal", "torgal2")):
            creature = f"cre:{first}"
            troll = _target(f"{area}/actor/0", area, "actor", first, creature)
            fallen = _target(f"{area}/actor/1", area, "actor", first, f"cre:{other}")
            twice = _target(f"{area}/actor/2", area, "actor", first, creature)
            for template, game in (("bg2-original", "bg2ee"), ("eet-original", "eet")):
                def place(targets, resources=(area, creature, f"cre:{other}")):
                    world = {"resources": [{"id": name} for name in resources], "sources": [], "targets": targets}
                    result = resolve_template({"items": []}, world, template, game=game, items="extended",
                                              targets="extended", options={"flail_heads": True})
                    return next(row for row in result["targets"] if row["id"] == f"bg2:mode2:target:{line}")

                with self.subTest(line=line, template=template):
                    row = place([troll, fallen])
                    self.assertEqual((row["area_resource"], row["creature_resource"], row["container_name"],
                                      row["target_ids"], row["binding_problem"]),
                                     (area, creature, None, [troll["id"]], None))
                    self.assertEqual(target_kind(row), "creature-target")
                    # Only another version in the area, the first twice, or no first.
                    for targets, resources, ids, problem in (
                            ([fallen], (area, creature), [], "creature-not-static-in-area"),
                            ([troll, twice], (area, creature), [troll["id"], twice["id"]], "ambiguous-target-records"),
                            ([troll], (area, f"cre:{other}"), [troll["id"]], "creature-resource-absent"),
                            ([], (creature,), [], "area-resource-absent")):
                        row = place(targets, resources)
                        self.assertEqual((row["target_ids"], row["binding_problem"]), (ids, problem))

    def test_a_presets_rows_of_an_original_reward_route_are_that_route(self):
        from flir_config.original_catalog import mode2_route_source, mode2_route_target

        catalog = {"items": [{"resref": name} for name in ("sw1h03", "shld07", "scrl1q")]}
        world = {"resources": [{"id": name} for name in ("dlg:aldeth", "dlg:varci", "dlg:nemphr")],
                 "sources": [], "targets": []}
        result = resolve_template(catalog, world, "bg1-boss-loot", game="bgee", items="extended",
                                  targets="extended", options={"scrolls": True})
        items = {row["id"]: row for row in result["items"]}
        places = {row["id"]: row for row in result["targets"]}
        # Boss Loot's rows of Aldeth's sword, Varci's shield and Nemphre's scroll.
        for preset, original, dialogue in ((62, 16, "dlg:aldeth"), (71, 20, "dlg:varci"), (339, 159, "dlg:nemphr")):
            row = items[f"bg1:boss-loot:mode2:item:{preset}"]
            with self.subTest(item=preset):
                self.assertEqual(mode2_route_source(row), f"original-source:bg1:mode2:item:{original}")
                self.assertEqual((row["adapter"], row["source_resource"], row["binding_problem"]),
                                 ("scripted", dialogue, "scripted-source-requires-adapter"))
        for preset, original, dialogue in ((46, 16, "dlg:aldeth"), (71, 20, "dlg:varci"), (291, 136, "dlg:nemphr")):
            row = places[f"bg1:boss-loot:mode2:target:{preset}"]
            with self.subTest(place=preset):
                self.assertEqual(mode2_route_target(row), f"bg1:mode2:target:{original}")
                self.assertEqual((row["area_resource"], row["binding_problem"]),
                                 (dialogue, "legacy-target-requires-adapter"))
        # Only the preset's rows as its tables have them.
        sword, place = items["bg1:boss-loot:mode2:item:62"], places["bg1:boss-loot:mode2:target:46"]
        for changed in ({"tier": "27"}, {"source": "varci.dlg"}, {"id": "bg1:boss-loot:mode2:item:63"},
                        {"campaign": "bg2"}):
            with self.subTest(changed=changed):
                self.assertIsNone(mode2_route_source({**sword, **changed}))
        for changed in ({"area": "ar0617"}, {"tier": "27"}, {"id": "bg1:boss-loot:mode2:target:47"}):
            with self.subTest(changed=changed):
                self.assertIsNone(mode2_route_target({**place, **changed}))

    def test_a_preset_with_two_rows_of_one_route_has_the_route_for_neither(self):
        """One reward or place, one row: the runtime finds a route's row by it."""
        import shutil
        import tempfile
        from unittest.mock import patch
        from flir_config import original_catalog, templates
        from flir_config.templates import ROOT, build_template

        draft = build_template("bg1-boss-loot", items="extended", targets="extended")
        sword = next(row for row in draft["items"] if row["id"] == "bg1:boss-loot:mode2:item:62")
        place = next(row for row in draft["targets"] if row["id"] == "bg1:boss-loot:mode2:target:46")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for kind in ("items", "locations"):
                for relative in (f"lists/{kind}/mode2/bg1.2da", f"lists/presets/boss-loot/{kind}/mode2/bg1.2da"):
                    (root / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / relative, root / relative)
            # Both lists roots, as configurator/parity/replay_tests.py passes them on.
            with patch.object(original_catalog, "ROOT", root), patch.object(templates, "ROOT", root):
                self.assertEqual(original_catalog.mode2_route_source(sword), "original-source:bg1:mode2:item:16")
                self.assertEqual(original_catalog.mode2_route_target(place), "bg1:mode2:target:16")
                for relative, line in (("items", "sw1h03 blank aldeth.dlg 25 w13 v8 100"),
                                       ("locations", "w9 26 aldeth.fl ar0128 v8 100")):
                    with (root / f"lists/presets/boss-loot/{relative}/mode2/bg1.2da").open("a") as table:
                        table.write("\n" + line + "\n")
                self.assertIsNone(original_catalog.mode2_route_source(sword))
                self.assertIsNone(original_catalog.mode2_route_target(place))

    def test_a_presets_place_of_an_original_container_quest_is_that_quest(self):
        from flir_config.original_catalog import container_target_spec

        chest = _target("are:bg3601/container/0", "are:bg3601", "container", "Container 1")
        book = {"id": "are:bg3601/container/0/item/0", "resource": "are:bg3601", "item": "book03",
                "owner": chest["id"], "kind": "container", "record": {"container_index": 0}}
        world = {"resources": [{"id": name} for name in ("are:bg3601", "bcs:safana", "dlg:safanj")],
                 "sources": [book], "targets": [chest]}
        rows = {}
        for template in ("eet-original", "eet-boss-loot"):
            result = resolve_template({"items": []}, world, template, items="extended", targets="extended")
            rows.update((row["id"], row) for row in result["targets"])
        safana = rows["bg1:boss-loot:mode2:target:47"]
        for row in (rows["bg1:mode2:target:22"], safana):
            with self.subTest(row=row["id"]):
                self.assertEqual((row["area_resource"], row["target_ids"], row["container_name"],
                                  row["binding_problem"]),
                                 ("are:bg3601", [chest["id"]], "Container 1", None))
        self.assertEqual(container_target_spec(safana)["target_id"], "bg1:mode2:target:22")
        # Only the preset's row as its table has it: another place, or a changed row, is no quest.
        for changed in ({"area": "ar0128"}, {"tier": "27"}, {"id": "bg1:boss-loot:mode2:target:46"}):
            with self.subTest(changed=changed):
                self.assertIsNone(container_target_spec({**safana, **changed}))


if __name__ == "__main__":
    unittest.main()
