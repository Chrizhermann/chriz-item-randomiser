"""Extended reward routes on synthetic BG2:EE and EET games; no real games.

The configuration check recognises each quest reward route in the game's own
bytes, as Apply does (reward_routes.py). A route in another game's layout, or
with another mod's additions, is fitted and applied; one the check cannot
recognise is left out with its reason before anything is written.
"""

import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import mode2_rewards
from flir_config.campaigns import compile_campaign, read_campaign
from flir_config.native_export import _compile, export_native
from flir_config.original_catalog import resolve_template
from flir_config.original_configuration import fit_original_configuration
from flir_config.profiles import ProfileError, validate_catalog
from flir_config.resources import _Tlk, scan_game
from flir_config.reward_routes import ChangedSinceScan, reward_routes
from flir_config.sources import scan_world, validate_world
from flir_config.starts import write_starts
from test_container_rewards import dialogue_fixture as container_dialogue, script_fixture
from test_mode2_rewards import action, block, dialogue_fixture, layout_dialogue, well_reward, with_actions
from test_original_configuration import target_row
from test_postinstall_resources import make_game, make_itm, mark_eet, snapshot
from test_postinstall_world_formats import item_record, make_are

# Three routes of Item Randomiser's extended lists: the ogre's shield (a
# dialogue), the well's helmet (a script with the reward text) and Khollos's
# belt, whose dialogue another mod changed so that nothing gives the belt.
SOURCES = ("bg2:mode2:item:14", "bg2:mode2:item:27", "bg2:mode2:item:33")
TARGETS = ("bg2:mode2:target:17", "bg2:mode2:target:27", "bg2:mode2:target:33")
KHOLLOS = "The extended reward route for MGKHOL01 cannot be used: MGKHOL01.DLG has no reply that gives BELT02"


def ogre_dialogue() -> bytes:
    """UHOGRE01 as Beamdog compiles it, its reward in another state, with another mod's reply."""
    states = [[("", 'SetGlobal("OgreTalk","LOCALS",1)', 9)] for _ in range(9)]
    states.append([
        ('PartyHasItem("misc5x")', 'TakePartyItem("misc5x")\nGiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)', None),
        ('Global("OtherMod","GLOBAL",1)', 'SetGlobal("OtherMod","GLOBAL",2)', None)])
    return layout_dialogue("uhogre01", states)


def well_script() -> bytes:
    """DDWELL with CRLF line ends and another mod's block before the reward."""
    added = block(action(30, 1, "OtherModWell"), "OtherModQuest")
    return ("SC\n" + added + block(well_reward()) + "SC\n").encode().replace(b"\n", b"\r\n")


def khollos_dialogue() -> bytes:
    """MGKHOL01 whose reward another mod replaced with gold."""
    original = dialogue_fixture(mode2_rewards.resource_specs("dlg:mgkhol01"))
    return with_actions(original, "mgkhol01", {(4, 0): b"GiveGoldForce(500)\r\n"})


def chest_item(name: str) -> bytes:
    return item_record(name, expiration=0, charges=(0, 0, 0), flags=0)


def place(identity: str, tier: str, container: int) -> dict:
    """An empty chest of Item Randomiser's base list in a Shadows of Amn area."""
    row = target_row(identity, "x", tier)
    number = identity.rsplit(":", 1)[1]
    row.update(campaign="bg2", area="ar0100", location="ar0100.are", area_resource="are:ar0100",
               target_ids=[f"are:ar0100/container/{container}"],
               source_ref=f"lists/locations/base/bg2.2da:{number}",
               endpoint_source_ref=f"lists/locations/base/bg2.2da:{number}")
    return row


class RouteGame(unittest.TestCase):
    sources, targets = SOURCES, TARGETS

    def build(self, platform: str, *, english: bool = True, journal: bool = True,
              resources: dict[str, bytes] | None = None, extra: dict[str, bytes] | None = None) -> None:
        """A synthetic game with the three routes; <extra> adds or replaces override files."""
        temporary = tempfile.TemporaryDirectory(prefix="flir-reward-routes-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game, self.store, self.output = (self.root / name for name in
                                              ("game", "campaigns", "randomiser-campaigns"))
        make_game(self.game, [("SYNTH001", make_itm())])
        override = self.game / "override"
        visible = bytearray(make_itm())
        struct.pack_into("<I", visible, 0x18, 0x4c)
        for name in ("synth001", "shld26", "helm21", "belt02", "xbow10", "halb06"):
            (override / f"{name}.itm").write_bytes(visible)
        if platform == "eet":
            mark_eet(self.game)
            (override / "oh1000.are").write_bytes(make_are())
        (override / "oh6000.are").write_bytes(make_are())
        for area in ("ar1100", "ar1202", "ar0411"):
            (override / f"{area}.are").write_bytes(make_are())
        (override / "ar0100.are").write_bytes(make_are([], [("Place2", 0, 0), ("Place3", 0, 0)]))
        self.resources = {"uhogre01.dlg": ogre_dialogue(), "ddwell.bcs": well_script(),
                          "mgkhol01.dlg": khollos_dialogue()} if resources is None else resources
        for name, data in {**self.resources, **(extra or {})}.items():
            (override / name).write_bytes(data)
        if journal:
            (override / "BGEE.LUA").write_bytes(b"quests = {}\n")
        if english:
            language = self.game / "lang" / "en_US"
            language.mkdir(parents=True)
            shutil.copyfile(self.game / "dialog.tlk", language / "dialog.tlk")
        self.platform = platform
        self.template = {"bg2ee": "bg2-original", "eet": "eet-original"}[platform]
        self.rescan()

    def rescan(self) -> None:
        self.catalog = scan_game(self.game, **({"language": "en_US"} if (self.game / "lang").exists() else {}))
        self.world = scan_world(self.catalog)

    def places(self) -> list[dict]:
        return [place("bg2:base:target:9002", "2", 0), place("bg2:base:target:9003", "3", 1)]

    def draft(self) -> dict:
        draft = resolve_template(self.catalog, self.world, self.template, game=self.platform,
                                 items="extended", targets="extended")
        draft["items"] = [row for row in draft["items"] if row["id"] in self.sources]
        draft["targets"] = [row for row in draft["targets"] if row["id"] in self.targets] + self.places()
        self.assertEqual(len(draft["items"]), len(self.sources))
        self.assertEqual(len(draft["targets"]), len(self.targets) + len(self.places()))
        return draft

    def fit(self) -> dict:
        draft = self.draft()
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            return fit_original_configuration(self.catalog, self.world, self.template,
                                              items="extended", targets="extended")

    def apply(self, fitted: dict) -> dict:
        """Write each start's revision and export them, as Apply does."""
        draft = self.draft()
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            written = write_starts(fitted["profile"], fitted["plan"], self.catalog, self.world, self.store)
            export_native(self.store, self.catalog, self.world, self.output,
                          starts=written["starts"], main=written["main"])
        return written


class FitAndApplyTests(RouteGame):
    def check_fitted(self, fitted: dict) -> None:
        self.assertEqual(sorted((row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]),
                         [("bg2:mode2:item:33", "reward", KHOLLOS), ("bg2:mode2:target:33", "place", KHOLLOS)])
        self.assertEqual(fitted["not_used_notes"], [
            "1 reward and 1 place from the lists cannot be used in this game, so they are left out; "
            "a left-out reward stays as the game has it"])
        sources = {row["id"] for row in fitted["plan"]["registrations"]}
        self.assertEqual(sources, {"original-source:bg2:mode2:item:14", "original-source:bg2:mode2:item:27"})
        targets = {row["id"] for row in fitted["plan"]["target_registrations"]}
        self.assertEqual(targets, {"original-target:bg2:mode2:target:17", "original-target:bg2:mode2:target:27",
                                   "original-target:bg2:base:target:9002", "original-target:bg2:base:target:9003"})
        # The fitted configuration passes the strict check that Apply runs.
        draft = self.draft()
        with patch("flir_config.original_registrations.resolve_template", return_value=draft):
            campaign = compile_campaign(fitted["profile"], self.catalog, self.world, fitted["plan"])
        _compile(campaign, self.world, validate_world(self.world, self.catalog),
                 validate_catalog(self.catalog), catalog=self.catalog)

    def check_applied(self, written: dict) -> dict[str, bytes]:
        overrides = self.output / "resource-overrides"
        ogre, well = (overrides / "UHOGRE01.DLG").read_bytes(), (overrides / "DDWELL.BCS").read_bytes()
        self.assertFalse((overrides / "MGKHOL01.DLG").exists())
        self.assertEqual(ogre, mode2_rewards.patch_reward("dlg:uhogre01", self.resources["uhogre01.dlg"]))
        spec = mode2_rewards.resource_specs("bcs:ddwell")[0]
        original, ref, own = mode2_rewards._script_original(well, spec)
        self.assertEqual((original, own), (self.resources["ddwell.bcs"], True))
        tlk = (self.output / "game-overrides/lang/en_US/dialog.tlk").read_bytes()
        self.assertIn("You pull an item from the murky water", _Tlk(tlk).text(ref, []))
        for revision in written["starts"].values():
            campaign = read_campaign(self.store, revision)
            self.assertFalse(any("item:33" in row["id"] or "target:33" in row["id"]
                                 for row in campaign["sources"] + campaign["targets"]))
        return {"UHOGRE01.DLG": ogre, "DDWELL.BCS": well}

    def fit_and_apply(self, platform: str, starts: set[str]) -> None:
        self.build(platform)
        before = snapshot(self.game)
        fitted = self.fit()
        self.check_fitted(fitted)
        written = self.apply(fitted)
        self.assertEqual(set(written["starts"]), starts)
        # A Throne of Bhaal start reaches neither the BG2 table's quests nor
        # the Shadows of Amn chests: it stays as the game has it.
        self.assertEqual(written["skipped"], {"TOB": "nothing in this configuration is reached from this start"})
        self.assertEqual(snapshot(self.game), before)
        patched = self.check_applied(written)
        first = snapshot(self.output)
        # The installed patches are recognised as our own: before a rescan
        # Apply reproduces the same files, after one the check fits the same
        # rows and Apply writes the same patches again.
        for name, data in patched.items():
            (self.game / "override" / name.lower()).write_bytes(data)
        self.apply(fitted)
        self.assertEqual(snapshot(self.output), first)
        self.rescan()
        refitted = self.fit()
        self.check_fitted(refitted)
        self.apply(refitted)
        for name, data in patched.items():
            self.assertEqual((self.output / "resource-overrides" / name).read_bytes(), data)

    def test_bg2ee_fits_and_applies_routes_in_its_own_layout(self):
        self.fit_and_apply("bg2ee", {"SOA"})

    def test_eet_fits_and_applies_routes_in_its_own_layout(self):
        self.fit_and_apply("eet", {"BG1", "SOD", "SOA"})


class ContainerQuestTests(RouteGame):
    """The golems' first treasure and Samia's two, placed where the games keep them."""

    sources = SOURCES[:2]
    targets = TARGETS[:2] + ("bg2:mode2:target:13", "bg2:mode2:target:28", "bg2:mode2:target:53")

    def places(self) -> list[dict]:
        return []  # The quests' own chests take any reward of their tiers.

    def containers(self, *, golem: bool = True) -> dict[str, bytes]:
        # AR1202: another chest before Samia's; AR1302 is the table's area for
        # the golems, AR1303 the one their handler and treasure use.
        resources = {f"{name[4:]}.{name[:3]}": (script_fixture(name) if name.startswith("bcs:")
                                                else container_dialogue(name))
                     for name in ("bcs:kpgol01", "bcs:kpgol03", "bcs:ar1202", "bcs:samia", "dlg:samia")}
        return resources | {
            "ar1202.are": make_are([chest_item("SYNTH001"), chest_item("SW1H32"), chest_item("SHLD21")],
                                   [("Container 1", 0, 1), ("Closet", 1, 0), ("Container 11", 1, 2)]),
            "ar1302.are": make_are([chest_item("SYNTH001")], [("Container 1", 0, 1)]),
            "ar1303.are": make_are([chest_item("AX1H13" if golem else "SYNTH001")],
                                   [("Container 1", 0, 1), ("Container 2", 1, 0)])}

    def build_quests(self, platform: str, **changes) -> None:
        self.build(platform, resources={"uhogre01.dlg": ogre_dialogue(), "ddwell.bcs": well_script()},
                   extra=self.containers(**changes))
        visible = (self.game / "override" / "synth001.itm").read_bytes()
        for name in ("ax1h13", "sw1h32", "shld21"):
            (self.game / "override" / f"{name}.itm").write_bytes(visible)
        self.rescan()

    def test_the_quests_bind_the_chests_that_hold_their_treasures_and_apply(self):
        for platform, starts in (("bg2ee", {"SOA"}), ("eet", {"BG1", "SOD", "SOA"})):
            with self.subTest(platform=platform):
                self.build_quests(platform)
                bound = {row["id"]: (row["area_resource"], row["target_ids"], row["binding_problem"])
                         for row in self.draft()["targets"] if row["id"] in self.targets[2:]}
                self.assertEqual(bound, {
                    "bg2:mode2:target:13": ("are:ar1303", ["are:ar1303/container/0"], None),
                    "bg2:mode2:target:28": ("are:ar1202", ["are:ar1202/container/2"], None),
                    "bg2:mode2:target:53": ("are:ar1202", ["are:ar1202/container/2"], None)})
                before = snapshot(self.game)
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                written = self.apply(fitted)
                self.assertEqual(set(written["starts"]), starts)
                self.assertEqual(snapshot(self.game), before)
                native = json.loads((self.output / "campaign-store" / "revisions" /
                                     f"{written['starts'][written['full']]}.native.json").read_bytes())
                chests = {identity: (target["area"], target["script_name"], target["loot_latch"]["activation_global"])
                          for identity, target in native["targets"].items() if "loot_latch" in target}
                self.assertEqual(chests, {
                    "original-target:bg2:mode2:target:13": ("AR1303", "Container 1", "FLPR_CKEEP1"),
                    "original-target:bg2:mode2:target:28": ("AR1202", "Container 11", "FLPR_CSAMIA"),
                    "original-target:bg2:mode2:target:53": ("AR1202", "Container 11", "FLPR_CSAMIA")})

    def test_a_quest_whose_treasure_another_mod_moved_is_left_out(self):
        self.places = super().places  # Other chests take the golems' tier.
        self.build_quests("bg2ee", golem=False)
        self.assertEqual(next(row["binding_problem"] for row in self.draft()["targets"]
                              if row["id"] == "bg2:mode2:target:13"), "treasure-container-not-found")
        fitted = self.fit()
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]], [
            ("bg2:mode2:target:13", "place", "The extended reward container of AX1H13 cannot be used: "
             "AR1303 has no container that holds AX1H13")])
        self.apply(fitted)


class RouteCheckTests(RouteGame):
    def test_text_routes_need_english_and_the_journal_table(self):
        for english, journal, reason in (
                (False, True, "Item Randomiser's reward texts exist only in English"),
                (True, False, "the game has no BGEE.LUA journal table")):
            with self.subTest(reason=reason):
                self.build("bg2ee", english=english, journal=journal)
                routes = reward_routes(self.catalog, self.world)
                routes.require_route("uhogre01")
                with self.assertRaisesRegex(ProfileError, "^The extended reward route for DDWELL cannot be used: "
                                            + reason + "$"):
                    routes.require_route("ddwell")
                fitted = self.fit()
                left_out = {row["id"]: row["reason"] for row in fitted["not_used"]}
                self.assertEqual(left_out["bg2:mode2:item:27"],
                                 "The extended reward route for DDWELL cannot be used: " + reason)
                self.assertNotIn("bg2:mode2:item:14", left_out)

    def test_a_route_changed_since_the_scan_stops_the_check(self):
        self.build("bg2ee")
        # Another mod changed the ogre's dialogue after the scan.
        (self.game / "override" / "uhogre01.dlg").write_bytes(dialogue_fixture(
            mode2_rewards.resource_specs("dlg:uhogre01")))
        with self.assertRaisesRegex(ChangedSinceScan, "rescan before export: dlg:uhogre01"):
            self.fit()
        self.rescan()
        self.assertEqual(sorted(row["id"] for row in self.fit()["not_used"]),
                         ["bg2:mode2:item:33", "bg2:mode2:target:33"])

    def test_routes_sharing_a_reward_event_are_left_out_together(self):
        # The egg deal: Silver's crossbow and the demon's halberd share one
        # reward event, so neither is prepared without the other.
        silver = dialogue_fixture(mode2_rewards.resource_specs("dlg:udsilver"))
        demon = with_actions(dialogue_fixture(mode2_rewards.resource_specs("dlg:uddemon")), "uddemon",
                             {(8, 2): b"GiveGoldForce(500)\r\n"})
        self.build("bg2ee", resources={"udsilver.dlg": silver, "uddemon.dlg": demon})
        routes = reward_routes(self.catalog, self.world)
        reason = "UDDEMON.DLG has no reply that gives HALB06"
        for key in ("udsilver", "uddemon"):
            with self.assertRaisesRegex(ProfileError, f"^The extended reward route for {key.upper()} "
                                        f"cannot be used: {reason}$"):
                routes.require_route(key)
        self.build("bg2ee", resources={"udsilver.dlg": silver,
                                       "uddemon.dlg": dialogue_fixture(mode2_rewards.resource_specs("dlg:uddemon"))})
        routes = reward_routes(self.catalog, self.world)
        routes.require_route("udsilver")
        routes.require_route("uddemon")

    def test_a_changed_copy_of_our_patch_is_left_out_not_trusted(self):
        patched = mode2_rewards.patch_reward("dlg:uhogre01", ogre_dialogue())
        self.build("bg2ee", resources={"uhogre01.dlg": patched.replace(b"FLPR_", b"OTHER", 1),
                                       "ddwell.bcs": well_script()})
        with self.assertRaisesRegex(ProfileError, "^The extended reward route for UHOGRE01 cannot be used: "
                                    "UHOGRE01.DLG carries a changed copy of the reward route's own patch$"):
            reward_routes(self.catalog, self.world).require_route("uhogre01")
        reward_routes(self.catalog, self.world).require_route("ddwell")


if __name__ == "__main__":
    unittest.main()
