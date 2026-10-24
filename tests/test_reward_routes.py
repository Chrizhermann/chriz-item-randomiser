"""Extended reward routes on synthetic BG2:EE and EET games; no real games.

The configuration check recognises each quest reward route in the game's own
bytes, as Apply does (reward_routes.py). A route in another game's layout, or
with another mod's additions, is fitted and applied; one the check cannot
recognise is left out with its reason before anything is written.
"""

import hashlib
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

from flir_config import container_rewards, mode2_rewards, reward_copies, translations
from flir_config.activation import EXPORT_FILES, adopt_revisions, deploy_export
from flir_config.campaigns import _render_lua, compile_campaign, read_campaign
from flir_config.native_export import _compile, export_native
from flir_config.original_catalog import resolve_template
from flir_config.original_configuration import fit_original_configuration
from flir_config.profiles import ProfileError, validate_catalog
from flir_config.resources import _Tlk, scan_game
from flir_config.quest_options import _Dialogue
from flir_config.reward_routes import ChangedSinceScan, reward_routes
from flir_config.script_text import script_blocks
from flir_config.sources import scan_world, validate_world
from flir_config.starts import write_starts
from test_container_rewards import dialogue_fixture as container_dialogue, script_fixture
from test_mode2_rewards import (BOOK_GIFTS, KING_DESTROYS, KING_PAID, action, block, book_block, book_fixture,
                                dialogue_fixture, layout_dialogue, treasury_fixture, well_reward, with_actions)
from test_original_configuration import target_row
from test_postinstall_resources import make_game, make_itm, make_tlk, mark_eet, snapshot
from test_postinstall_world_formats import item_record, make_are
from test_reward_copies import interject

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
    # The preset's options (Item Randomiser's components) that bring the rows.
    options = None

    def build(self, platform: str, *, language: str | None = "en_US", female: bytes | None = None,
              journal: bool = True, resources: dict[str, bytes] | None = None,
              extra: dict[str, bytes] | None = None) -> None:
        """A synthetic game with the three routes; <extra> adds or replaces override files.

        Its text is in lang/<language> (with dialogF.tlk <female>), or only in
        the game folder without a language.
        """
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
        self.language = language
        if language:
            folder = self.game / "lang" / language
            folder.mkdir(parents=True)
            shutil.copyfile(self.game / "dialog.tlk", folder / "dialog.tlk")
            if female is not None:
                (folder / "dialogF.tlk").write_bytes(female)
        self.platform = platform
        self.template = {"bg2ee": "bg2-original", "eet": "eet-original"}[platform]
        self.rescan()

    def rescan(self) -> None:
        self.catalog = scan_game(self.game, **({"language": self.language} if self.language else {}))
        self.world = scan_world(self.catalog)

    def places(self) -> list[dict]:
        return [place("bg2:base:target:9002", "2", 0), place("bg2:base:target:9003", "3", 1)]

    def draft(self) -> dict:
        draft = resolve_template(self.catalog, self.world, self.template, game=self.platform,
                                 items="extended", targets="extended", options=self.options)
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
            self.exported = export_native(self.store, self.catalog, self.world, self.output,
                                          starts=written["starts"], main=written["main"])
        return written


class FitAndApplyTests(RouteGame):
    def check_fitted(self, fitted: dict) -> None:
        self.assertEqual(sorted((row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]),
                         [("bg2:mode2:item:33", "reward", KHOLLOS), ("bg2:mode2:target:33", "place", KHOLLOS)])
        self.assertEqual(fitted["not_used_notes"], [
            "1 reward and 1 place from the lists cannot be used in this game, so they are left out",
            "A left-out reward stays as the game has it"])
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
        original, ref, own = mode2_rewards._script_original(well, mode2_rewards.resource_specs("bcs:ddwell"))
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

    def build_quests(self, platform: str, *, golem: bool = True, **game) -> None:
        self.build(platform, resources={"uhogre01.dlg": ogre_dialogue(), "ddwell.bcs": well_script()},
                   extra=self.containers(golem=golem), **game)
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

    def test_earlier_revisions_keep_the_chest_they_recorded(self):
        # A revision exported with the earlier rule bound Samia's treasure to
        # the closet. It belongs to running saves: while AR1202 is unchanged,
        # Apply keeps that binding for it and binds new revisions by the treasure.
        self.build_quests("bg2ee")
        real = container_rewards.treasure_container

        def closet(target_id, sources, platform):
            if target_id in ("bg2:mode2:target:28", "bg2:mode2:target:53"):
                return "are:ar1202/container/1"
            return real(target_id, sources, platform)

        with patch("flir_config.original_catalog.treasure_container", closet), \
                patch("flir_config.native_export.treasure_container", closet):
            written = self.apply(self.fit())
        earlier = written["starts"][written["full"]]
        revisions = self.output / "campaign-store" / "revisions"

        def files(revision):
            return {path.name: path.read_bytes() for path in revisions.iterdir() if path.name.startswith(revision)}

        def chest(revision, identity="original-target:bg2:mode2:target:53"):
            native = json.loads((revisions / f"{revision}.native.json").read_bytes())
            return native["targets"][identity]["script_name"]

        before = files(earlier)
        self.assertEqual(chest(earlier), "Closet")
        written = self.apply(self.fit())
        later = written["starts"][written["full"]]
        self.assertNotEqual(later, earlier)
        self.assertEqual((chest(earlier), chest(later)), ("Closet", "Container 11"))
        self.assertEqual(files(earlier), before)
        self.assertNotIn("notes", self.exported)
        # A real change of AR1202 leaves its places (the well, Samia's quest)
        # out of those saves with a note; Apply still succeeds for the new one.
        area = make_are([chest_item("SYNTH001"), chest_item("SW1H32"), chest_item("SHLD21")],
                        [("Container 1", 0, 1), ("Closet", 1, 0), ("Container 11", 1, 2), ("Shelf", 3, 0)])
        (self.game / "override" / "ar1202.are").write_bytes(area)
        self.rescan()
        kept = {revision: files(revision) for revision in (earlier, later)}
        written = self.apply(self.fit())
        self.assertNotIn(written["starts"][written["full"]], (earlier, later))
        self.assertEqual({revision: files(revision) for revision in (earlier, later)}, kept)
        self.assertEqual(self.exported["notes"], sorted(
            f"Saved games of revision {revision[:12]} keep their settings, but original-target:bg2:mode2:target:{row} "
            f"may no longer work in them: Selected target differs from the scan: original-target:bg2:mode2:target:{row}"
            for revision in (earlier, later) for row in (27, 28, 53)))
        listed = json.loads((self.output / EXPORT_FILES).read_text())["files"]
        self.assertTrue(all(f"campaign-store/revisions/{name}" in listed for rows in kept.values() for name in rows))

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


ROD_GRANT = b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\r\n'


def king_dialogue(rod: bytes = ROD_GRANT) -> bytes:
    """SAHKNG01 whose reward reply gives the bracer and, as <rod> says, the rod."""
    dialogue = _Dialogue(dialogue_fixture(mode2_rewards.resource_specs("dlg:sahkng01")), "sahkng01")
    dialogue.actions = [text.replace(ROD_GRANT, rod) for text in dialogue.actions]
    return dialogue.render()


class TreasuryGame(RouteGame):
    """The Sahuagin treasury's rod and the ogre's shield, which keeps a reward
    in the configuration where the rod is left out."""

    sources = ("bg2:mode2:item:7", "bg2:mode2:item:14")
    ROD = "are:ar2300/container/0/item/0"

    def places(self) -> list[dict]:
        # Chests of the rod's and the shield's tiers.
        return [place("bg2:base:target:9002", "1", 0), place("bg2:base:target:9003", "2", 1)]

    def rod_row(self, draft: dict) -> dict:
        return next(row for row in draft["items"] if row["id"] == self.sources[0])

    def build_treasury(self, platform: str, king: bytes | None, *, rods: int = 1) -> None:
        """AR2300 with the rod in its treasury and in <rods> - 1 other chests."""
        area = make_are([chest_item("RODS02")] * rods,
                        [("Treasury", 0, 1)] + [(f"Chest {number}", number, 1) for number in range(1, rods)])
        resources = {"uhogre01.dlg": ogre_dialogue()} | ({} if king is None else {"sahkng01.dlg": king})
        self.build(platform, resources=resources, extra={"ar2300.are": area})
        visible = (self.game / "override" / "synth001.itm").read_bytes()
        (self.game / "override" / "rods02.itm").write_bytes(visible)
        self.rescan()

    def native(self, written: dict) -> dict:
        return json.loads((self.output / "campaign-store" / "revisions" /
                           f"{written['starts'][written['full']]}.native.json").read_bytes())


class TreasuryRodTests(TreasuryGame):
    """The Sahuagin treasury's rod: a container reward of which the king's
    reward reply creates another one."""

    targets = ("bg2:mode2:target:17",)

    def test_a_moved_rod_stops_the_kings_second_one(self):
        for platform, starts in (("bg2ee", {"SOA"}), ("eet", {"BG1", "SOD", "SOA"})):
            with self.subTest(platform=platform):
                self.build_treasury(platform, king_dialogue())
                rod = self.rod_row(self.draft())
                self.assertEqual((rod["source_ids"], rod["binding_problem"]), ([self.ROD], None))
                before = snapshot(self.game)
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                written = self.apply(fitted)
                self.assertEqual(set(written["starts"]), starts)
                self.assertEqual(snapshot(self.game), before)
                # The reply creates the rod only while the treasury's stays.
                patched = mode2_rewards.patch_reward("dlg:sahkng01", king_dialogue())
                self.assertIn(b'Global("FLPR_QM2_7","GLOBAL",1)', b"".join(_Dialogue(patched, "sahkng01").triggers))
                self.assertEqual((self.output / "resource-overrides" / "SAHKNG01.DLG").read_bytes(), patched)
                for revision in written["starts"].values():
                    native = json.loads((self.output / "campaign-store" / "revisions" /
                                         f"{revision}.native.json").read_bytes())
                    source = native["sources"][self.ROD]
                    self.assertEqual((source["kind"], source["reward_selected_global"]),
                                     ("guarded-container", "FLPR_QM2_7"))
                    self.assertEqual(native["resource_hashes"]["SAHKNG01.DLG"], hashlib.sha256(patched).hexdigest())

    def test_where_nothing_else_gives_the_rod_it_moves_as_any_container_reward(self):
        # Another mod's king who gives no rod, also beside the treasury's
        # script, and a game without his dialogue.
        for king, script in ((king_dialogue(b""), None), (king_dialogue(b""), treasury_fixture()), (None, None)):
            with self.subTest(dialogue=king is not None, script=script is not None):
                self.build_treasury("bg2ee", king)
                if script is not None:
                    (self.game / "override" / "sahtreas.bcs").write_bytes(script)
                    self.rescan()
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                written = self.apply(fitted)
                self.assertNotIn("reward_selected_global", self.native(written)["sources"][self.ROD])
                for name in ("SAHKNG01.DLG", "SAHTREAS.BCS"):
                    self.assertFalse((self.output / "resource-overrides" / name).exists())

    def test_a_rod_the_king_gives_in_a_way_the_route_cannot_gate_is_left_out(self):
        self.build_treasury("bg2ee", king_dialogue(
            b'ActionOverride("sahpr2",GiveItemCreate("rods02",LastTalkedToBy,0,0,0))\r\r\n'))
        fitted = self.fit()
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]], [
            ("bg2:mode2:item:7", "reward", "The extended reward route for SAHROD cannot be used: SAHKNG01.DLG "
             "state 81 gives RODS02 through ActionOverride(), whose recipient the route cannot mark")])
        self.apply(fitted)
        self.assertFalse((self.output / "resource-overrides" / "SAHKNG01.DLG").exists())

    def test_one_of_two_rods_in_the_area_is_left_out(self):
        # The tables' row needs the area's only rod. A configuration that names
        # one of two cannot tell which one the king's reward stands for.
        self.build_treasury("bg2ee", king_dialogue(), rods=2)
        self.assertEqual(self.rod_row(self.draft())["binding_problem"], "ambiguous-source-records")
        draft = self.draft

        def one_of_two() -> dict:
            result = draft()
            self.rod_row(result).update(source_ids=[self.ROD], binding_problem=None)
            return result

        self.draft = one_of_two
        fitted = self.fit()
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]], [
            ("bg2:mode2:item:7", "reward", "AR2300.ARE holds another RODS02 that the reward of SAHKNG01.DLG "
             f"could stand for: {self.ROD}")])


def with_trap_script(area: bytes, index: int, script: str) -> bytes:
    """The area with another script on its container <index>."""
    data = bytearray(area)
    offset = struct.unpack_from("<I", data, 0x70)[0] + index * 0xC0 + 0x48
    struct.pack_into("<8s", data, offset, script.encode("ascii"))
    return bytes(data)


class TreasuryPlaceTests(TreasuryGame):
    """The Sahuagin treasury as a place (Item Randomiser's sahtreas.fl): its
    reward waits in the AR2300 container whose script is SAHTREAS, and the
    king's reward reply hands it to the one he pays. SAHTREAS.BCS destroys the
    king's reward items only while his grants are unchanged."""

    targets = ("bg2:mode2:target:6", "bg2:mode2:target:17")
    PLACE = "original-target:bg2:mode2:target:6"

    def build_place(self, platform: str, *, script: bytes | None = None, trap: str = "SAHTREAS") -> None:
        self.build_treasury(platform, king_dialogue())
        area = with_trap_script((self.game / "override" / "ar2300.are").read_bytes(), 0, trap)
        (self.game / "override" / "ar2300.are").write_bytes(area)
        if script is not None:
            (self.game / "override" / "sahtreas.bcs").write_bytes(script)
        self.rescan()

    def test_the_treasurys_reward_waits_there_until_the_king_pays(self):
        for platform, starts in (("bg2ee", {"SOA"}), ("eet", {"BG1", "SOD", "SOA"})):
            with self.subTest(platform=platform):
                self.build_place(platform, script=treasury_fixture())
                row = next(row for row in self.draft()["targets"] if row["id"] == "bg2:mode2:target:6")
                self.assertEqual((row["area_resource"], row["binding_problem"]),
                                 ("dlg:sahkng01", "legacy-target-requires-adapter"))
                before = snapshot(self.game)
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                self.assertIn(self.PLACE, {row["id"] for row in fitted["plan"]["target_registrations"]})
                written = self.apply(fitted)
                self.assertEqual(set(written["starts"]), starts)
                self.assertEqual(snapshot(self.game), before)
                overrides = self.output / "resource-overrides"
                king = mode2_rewards.patch_reward("dlg:sahkng01", king_dialogue())
                self.assertIn(b"FLPR_Handover(EEex_LuaTrigger_Object,6)", king)
                self.assertEqual((overrides / "SAHKNG01.DLG").read_bytes(), king)
                treasury = mode2_rewards.patch_reward("bcs:sahtreas", treasury_fixture())
                self.assertEqual(len(script_blocks(treasury).blocks), 4)
                self.assertEqual((overrides / "SAHTREAS.BCS").read_bytes(), treasury)
                area = (self.game / "override" / "ar2300.are").read_bytes()
                for revision in written["starts"].values():
                    native = json.loads((self.output / "campaign-store" / "revisions" /
                                         f"{revision}.native.json").read_bytes())
                    endpoint = native["targets"][self.PLACE]
                    self.assertEqual({key: value for key, value in endpoint.items() if key != "identity"}, {
                        "kind": "container", "area": "AR2300", "script_name": "Treasury",
                        "x": 111, "y": 222, "container_type": 2,
                        "handover": {"event_global": "FLPR_EA_SAHROD", "recipient_local": "FLPR_RA_SAHROD"},
                        "reward_resource": "dlg:sahkng01"})
                    hashes = native["resource_hashes"]
                    self.assertEqual((hashes["SAHKNG01.DLG"], hashes["SAHTREAS.BCS"], hashes["AR2300.ARE"]),
                                     tuple(hashlib.sha256(data).hexdigest() for data in (king, treasury, area)))

    def test_the_kings_bracer_checks_the_treasurys_script_too(self):
        # The bracer's route gates SAHTREAS as well: the runtime finds both
        # files as Apply wrote them (FLPRScript.lua), or only the king's
        # where the game has no such script.
        self.sources = ("bg2:mode2:item:57", "bg2:mode2:item:14")
        self.targets = ("bg2:mode2:target:58", "bg2:mode2:target:17")
        # A chest of the bracer's tier: no reward returns to its own place.
        self.places = lambda: [place("bg2:base:target:9002", "6", 0), place("bg2:base:target:9003", "2", 1)]
        for script in (treasury_fixture(), None):
            with self.subTest(script=script is not None):
                self.build_place("bg2ee", script=script)
                visible = (self.game / "override" / "synth001.itm").read_bytes()
                (self.game / "override" / "brac19.itm").write_bytes(visible)
                self.rescan()
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                written = self.apply(fitted)
                native = self.native(written)
                bracer = native["sources"]["original-source:bg2:mode2:item:57"]
                self.assertEqual((bracer["kind"], bracer.get("resources")),
                                 ("guarded-script", ["dlg:sahkng01", "bcs:sahtreas"] if script else None))
                revision = written["starts"][written["full"]]
                number = 1 + [row["id"] for row in read_campaign(self.store, revision)["sources"]].index(
                    "original-source:bg2:mode2:item:57")
                staged = self.output / "campaign-store" / "revisions" / f"{revision}.source-{number}.route-2.patched.bcs"
                if script:
                    treasury = mode2_rewards.patch_reward("bcs:sahtreas", script)
                    self.assertEqual(staged.read_bytes(), treasury)
                    self.assertEqual((self.output / "resource-overrides" / "SAHTREAS.BCS").read_bytes(), treasury)
                else:
                    self.assertFalse(staged.exists())

    def test_a_game_without_the_treasurys_script_needs_no_gate(self):
        self.build_place("bg2ee")
        fitted = self.fit()
        self.assertEqual(fitted["not_used"], [])
        written = self.apply(fitted)
        self.assertFalse((self.output / "resource-overrides" / "SAHTREAS.BCS").exists())
        native = self.native(written)
        self.assertEqual(native["targets"][self.PLACE]["kind"], "container")
        self.assertNotIn("SAHTREAS.BCS", native["resource_hashes"])

    def test_a_treasury_script_that_could_take_a_moved_rod_leaves_the_rods_route_out(self):
        giving = treasury_fixture(book_block([KING_PAID], KING_DESTROYS + action(15, text="RODS02", target=21)))
        self.build_place("bg2ee", script=giving)
        fitted = self.fit()
        reason = ("The extended reward route for SAHROD cannot be used: SAHTREAS.BCS block 0 uses RODS02 in an "
                  "action other than DestroyItem(), which the route cannot gate")
        self.assertEqual(sorted((row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]),
                         [("bg2:mode2:item:7", "reward", reason), ("bg2:mode2:target:6", "place", reason)])
        self.apply(fitted)
        # The bracer's route is not in this configuration: nothing is gated.
        self.assertFalse((self.output / "resource-overrides" / "SAHTREAS.BCS").exists())

    def test_an_area_without_one_treasury_leaves_its_place_out(self):
        self.build_place("bg2ee", script=treasury_fixture(), trap="IMPCHES3")
        row = next(row for row in self.draft()["targets"] if row["id"] == "bg2:mode2:target:6")
        self.assertEqual(row["binding_problem"], "treasure-container-not-found")
        fitted = self.fit()
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]], [
            ("bg2:mode2:target:6", "place",
             f"AR2300.ARE needs one container with the script SAHTREAS, the treasury: {self.PLACE}")])
        self.apply(fitted)


class BookScrollTests(RouteGame):
    """The two scrolls of the Spellhold book (rows 260 and 262) beside its
    ring (row 109), whose quest CIR's installer prepared in PPBOOK.BCS."""

    RING, SCROLLS = ("bg2:base:item:109",), ("bg2:mode2:item:260", "bg2:mode2:item:262")
    targets = ()
    options = {"scrolls": True}

    def places(self) -> list[dict]:
        # Chests of the ring's and the scrolls' tiers.
        return [place("bg2:base:target:9002", "6", 0), place("bg2:base:target:9003", "s8", 1),
                place("bg2:base:target:9004", "s8", 2)]

    def build_book(self, *, installed: bool = True) -> None:
        chests = make_are([], [("Place2", 0, 0), ("Place3", 0, 0), ("Place4", 0, 0)])
        self.build("bg2ee", resources={"ppbook.bcs": book_fixture(installed)},
                   extra={"ar0100.are": chests, "ar1513.are": make_are()})
        override = self.game / "override"
        visible = (override / "synth001.itm").read_bytes()
        for name in ("ring09", "scrl9b", "scrl8z"):
            (override / f"{name}.itm").write_bytes(visible)
        if installed:
            (override / "FLPRPAT.lua").write_text('quest_book = {status="applied"},\n', encoding="utf-8")
        self.rescan()

    def applied(self, sources: tuple[str, ...]) -> tuple[str, dict]:
        """Fit, apply and deploy a configuration of <sources>; its revision and native settings."""
        self.sources = sources
        fitted = self.fit()
        self.assertEqual(fitted["not_used"], [])
        written = self.apply(fitted)
        deploy_export(self.game, self.output)
        revision = written["starts"][written["full"]]
        return revision, json.loads((self.output / "campaign-store" / "revisions" /
                                     f"{revision}.native.json").read_bytes())

    def staged(self, revision: str) -> dict[str, bytes]:
        """The scripts a revision's guarded sources need in override, by source."""
        manifest = read_campaign(self.output / "campaign-store", revision)
        folder = self.output / "campaign-store" / "revisions"
        return {source["id"]: (folder / f"{revision}.source-{number}.patched.bcs").read_bytes()
                for number, source in enumerate(manifest["sources"], 1)}

    def test_apply_writes_the_book_only_for_its_scrolls_and_earlier_saves_keep_the_ring(self):
        self.build_book()
        installed = book_fixture()
        patched = mode2_rewards.patch_reward("bcs:ppbook", installed)
        overrides, versions = self.output / "resource-overrides", (self.output / "PPBOOK.installed.bcs",
                                                                   self.output / "PPBOOK.applied.bcs")
        # The ring alone: the book stays as the installer prepared it.
        ring, ring_native = self.applied(self.RING)
        self.assertFalse((overrides / "PPBOOK.BCS").exists())
        self.assertFalse(any(path.exists() for path in versions))
        self.assertEqual(set(self.staged(ring).values()), {installed})
        self.assertEqual(ring_native["resource_hashes"]["PPBOOK.BCS"], hashlib.sha256(installed).hexdigest())
        self.assertEqual((self.game / "override" / "PPBOOK.BCS").read_bytes(), installed)
        ring_files = {path.name: path.read_bytes() for path in (self.output / "campaign-store" / "revisions").iterdir()
                      if path.name.startswith(ring)}
        # The ring and both scrolls: Apply writes the book with the scrolls'
        # routes and keeps the installer's version beside it.
        both, both_native = self.applied(self.RING + self.SCROLLS)
        self.assertNotIn("notes", self.exported)
        self.assertEqual((overrides / "PPBOOK.BCS").read_bytes(), patched)
        self.assertEqual([path.read_bytes() for path in versions], [installed, patched])
        self.assertEqual((self.game / "override" / "PPBOOK.BCS").read_bytes(), patched)
        self.assertEqual((self.game / "randomiser-campaigns" / "PPBOOK.installed.bcs").read_bytes(), installed)
        self.assertEqual((self.game / "randomiser-campaigns" / "PPBOOK.applied.bcs").read_bytes(), patched)
        staged = self.staged(both)
        self.assertEqual(staged, {"original-source:bg2:base:item:109": installed,
                                  "original-source:bg2:mode2:item:260": patched,
                                  "original-source:bg2:mode2:item:262": patched})
        self.assertEqual({identity: both_native["sources"][identity]["adapter_id"] for identity in staged},
                         {"original-source:bg2:base:item:109": "original-quest-v1",
                          "original-source:bg2:mode2:item:260": "mode2-reward-v1",
                          "original-source:bg2:mode2:item:262": "mode2-reward-v1"})
        self.assertEqual(both_native["resource_hashes"]["PPBOOK.BCS"], hashlib.sha256(patched).hexdigest())
        # The ring's earlier revision keeps its files: its saves accept the
        # book Apply wrote, beside the installer's version (FLPRScript.lua).
        self.assertEqual({path.name: path.read_bytes() for path in (self.output / "campaign-store" / "revisions").iterdir()
                          if path.name.startswith(ring)}, ring_files)
        # A rescan reads Apply's book as the installer prepared it
        # (applied_files.py), so the ring alone again is the ring's revision:
        # it records the installer's version, and nothing earlier changes.
        self.rescan()
        self.assertEqual(next(row["sha256"] for row in self.world["resources"] if row["id"] == "bcs:ppbook"),
                         hashlib.sha256(installed).hexdigest())
        again, again_native = self.applied(self.RING)
        self.assertEqual(again, ring)
        self.assertNotIn("notes", self.exported)
        self.assertEqual(set(self.staged(again).values()), {installed})
        self.assertEqual(again_native["resource_hashes"]["PPBOOK.BCS"], hashlib.sha256(installed).hexdigest())
        self.assertEqual((overrides / "PPBOOK.BCS").read_bytes(), patched)
        # When the two earlier revisions are kept for another change (their
        # chests' area), the ring's installer version still meets their need.
        (self.game / "override" / "ar0100.are").write_bytes(
            make_are([], [("Place2", 0, 0), ("Place3", 0, 0), ("Place4", 0, 0), ("Place5", 0, 0)]))
        self.rescan()
        self.applied(self.RING + self.SCROLLS)
        self.assertEqual(len(self.exported["notes"]), 2 * 3)
        self.assertTrue(all("keep their settings" in note for note in self.exported["notes"]))

    def test_without_a_readable_record_the_scan_sees_the_book_apply_wrote(self):
        # As before CIR kept a record of Apply's files: the scans read Apply's
        # book as the game's, and the book's pair keeps the installer's version.
        self.build_book()
        installed = book_fixture()
        patched = mode2_rewards.patch_reward("bcs:ppbook", installed)
        revisions = self.output / "campaign-store" / "revisions"
        ring, _ = self.applied(self.RING)
        ring_files = {path.name: path.read_bytes() for path in revisions.iterdir() if path.name.startswith(ring)}
        self.applied(self.RING + self.SCROLLS)
        record = self.game / "randomiser-campaigns" / "applied-files.json"
        self.assertTrue(record.is_file())
        record.write_text("{", encoding="utf-8")
        self.rescan()
        self.assertEqual(next(row["sha256"] for row in self.world["resources"] if row["id"] == "bcs:ppbook"),
                         hashlib.sha256(patched).hexdigest())
        # The ring alone gets a revision of its own, which still records the
        # installer's version; the game keeps Apply's book beside it.
        again, again_native = self.applied(self.RING)
        self.assertNotEqual(again, ring)
        self.assertNotIn("notes", self.exported)
        self.assertEqual(set(self.staged(again).values()), {installed})
        self.assertEqual(again_native["resource_hashes"]["PPBOOK.BCS"], hashlib.sha256(installed).hexdigest())
        self.assertEqual((self.game / "override" / "PPBOOK.BCS").read_bytes(), patched)
        self.assertEqual((self.game / "randomiser-campaigns" / "PPBOOK.installed.bcs").read_bytes(), installed)
        self.assertEqual((self.game / "randomiser-campaigns" / "PPBOOK.applied.bcs").read_bytes(), patched)
        self.assertEqual({path.name: path.read_bytes() for path in revisions.iterdir() if path.name.startswith(ring)},
                         ring_files)
        # That Apply wrote nothing new outside CIR's folder, so no new record
        # took Apply's book for the game's.
        self.assertEqual(record.read_text(encoding="utf-8"), "{")
        # The area change keeps all three earlier revisions.
        (self.game / "override" / "ar0100.are").write_bytes(
            make_are([], [("Place2", 0, 0), ("Place3", 0, 0), ("Place4", 0, 0), ("Place5", 0, 0)]))
        self.rescan()
        self.applied(self.RING + self.SCROLLS)
        self.assertEqual(len(self.exported["notes"]), 3 * 3)
        self.assertTrue(all("keep their settings" in note for note in self.exported["notes"]))

    def test_the_scrolls_alone_need_no_prepared_book(self):
        # A book the installer left as it was: the ring's quest is left out,
        # the scrolls' routes patch the game's own book.
        self.build_book(installed=False)
        self.sources = self.RING + self.SCROLLS
        fitted = self.fit()
        self.assertEqual(fitted["not_used"], [])
        self.assertEqual(fitted["left_out"], ["PPBOOK.BCS was not prepared: this version of "
                                              "Chriz Item Randomiser is not installed"])
        self.assertEqual({row["id"] for row in fitted["plan"]["registrations"]},
                         {"original-source:" + identity for identity in self.SCROLLS})
        self.apply(fitted)
        self.assertEqual((self.output / "resource-overrides" / "PPBOOK.BCS").read_bytes(),
                         mode2_rewards.patch_reward("bcs:ppbook", book_fixture(False)))
        self.assertFalse((self.output / "PPBOOK.installed.bcs").exists())

    def test_a_scroll_the_book_gives_twice_is_left_out(self):
        self.build_book()
        twice = book_fixture().replace(BOOK_GIFTS.encode(),
                                       (BOOK_GIFTS + action(140, text="scrl9b", target=17)).encode())
        (self.game / "override" / "ppbook.bcs").write_bytes(twice)
        self.rescan()
        self.sources = self.RING + self.SCROLLS
        fitted = self.fit()
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in fitted["not_used"]], [
            ("bg2:mode2:item:260", "reward", "The extended reward route for PPBOOK cannot be used: "
             "PPBOOK.BCS block 1 gives SCRL9B twice in one response")])
        self.apply(fitted)
        self.assertEqual((self.output / "resource-overrides" / "PPBOOK.BCS").read_bytes(),
                         mode2_rewards.patch_reward("bcs:ppbook", twice))


class NoCopies:
    """The copy analysis as CIR had it before it looked for copies of reward replies."""

    def __init__(self, read, scanned, **naming):
        self.copies, self.reasons, self.visited, self.changed, self.originals = {}, {}, {}, set(), {}

    def checked(self, key):
        return set()

    def patches(self, routes):
        return {}


class CopyTests(RouteGame):
    """A party member's interjection copies the ogre's reward reply into its own dialogue."""

    sources, targets = SOURCES[:2], TARGETS[:2]

    def interjected(self, change=None) -> tuple[bytes, bytes, int]:
        route, npc, state = interject(ogre_dialogue(), "uhogre01", 9, "xnpcj")
        if change:
            dialogue = _Dialogue(npc, "xnpcj")
            first = struct.unpack_from("<I", dialogue.states[state], 4)[0]
            index = struct.unpack_from("<I", dialogue.transitions[first], 16)[0]
            dialogue.actions[index] = change(dialogue.actions[index])
            npc = dialogue.render()
        return route, npc, state

    def test_copies_are_gated_and_checked_like_the_route(self):
        route, npc, state = self.interjected()
        self.build("bg2ee", resources={"uhogre01.dlg": route, "ddwell.bcs": well_script(), "xnpcj.dlg": npc})
        fitted = self.fit()
        self.assertEqual(fitted["not_used"], [])
        self.apply(fitted)
        overrides = self.output / "resource-overrides"
        copies = reward_copies.RewardCopies({"uhogre01": route, "xnpcj": npc}.get, {})
        self.assertEqual(copies.copies, {"uhogre01": {"dlg:xnpcj": {state: {0}}}})
        expected = copies.patches(["uhogre01"])["dlg:xnpcj"]
        self.assertEqual((overrides / "XNPCJ.DLG").read_bytes(), expected)
        self.assertEqual((overrides / "UHOGRE01.DLG").read_bytes(), mode2_rewards.patch_reward("dlg:uhogre01", route))
        # The copy's grant has both of the route's variants; the other reply stays as it was.
        dialogue = _Dialogue(expected, "xnpcj")
        first, count = struct.unpack_from("<II", dialogue.states[state], 4)
        actions = [dialogue.actions[struct.unpack_from("<I", row, 16)[0]]
                   for row in dialogue.transitions[first:first + count]]
        self.assertEqual(count, 3)
        self.assertIn(b'GiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)', actions[0])
        self.assertIn(b'SetGlobal("FLPR_XM2_14","GLOBAL",1)', actions[1])
        self.assertTrue(all(b'SetGlobal("FLPR_EM2_14","GLOBAL",1)' in text for text in actions[:2]))
        first_export = snapshot(self.output)
        # Installed as Apply deploys them, both patches are our own: before a
        # rescan and after one, Apply writes the same files.
        for name in ("UHOGRE01.DLG", "XNPCJ.DLG"):
            (self.game / "override" / name.lower()).write_bytes((overrides / name).read_bytes())
        self.apply(fitted)
        self.assertEqual(snapshot(self.output), first_export)
        self.rescan()
        refitted = self.fit()
        self.assertEqual(refitted["not_used"], [])
        self.apply(refitted)
        self.assertEqual((overrides / "XNPCJ.DLG").read_bytes(), expected)
        # A change of the copy's dialogue after this check run stops the next one.
        (self.game / "override" / "xnpcj.dlg").write_bytes(npc)
        with self.assertRaisesRegex(ChangedSinceScan, "rescan before export: dlg:xnpcj"):
            self.fit()

    def test_a_copy_the_route_cannot_gate_leaves_it_out_with_its_reason(self):
        route, npc, state = self.interjected(lambda text: text + b'\nSetGlobal("XNPCJ","GLOBAL",1)')
        self.build("bg2ee", resources={"uhogre01.dlg": route, "ddwell.bcs": well_script(), "xnpcj.dlg": npc})
        reason = ("The extended reward route for UHOGRE01 cannot be used: XNPCJ.DLG state 0 also gives SHLD26, "
                  "but not with a copy of a reward reply of UHOGRE01.DLG, so the route cannot tell whether it is "
                  "the same reward")
        fitted = self.fit()
        self.assertEqual(sorted((row["id"], row["reason"]) for row in fitted["not_used"]),
                         [("bg2:mode2:item:14", reason), ("bg2:mode2:target:17", reason)])
        self.apply(fitted)
        self.assertFalse((self.output / "resource-overrides" / "XNPCJ.DLG").exists())

    def test_an_earlier_revision_keeps_its_route_when_a_newer_check_finds_a_copy(self):
        # A revision exported before CIR looked for copies of reward replies
        # keeps its route for its saves; new configurations leave it out.
        route, npc, state = self.interjected(lambda text: text + b'\nSetGlobal("XNPCJ","GLOBAL",1)')
        self.build("bg2ee", resources={"uhogre01.dlg": route, "ddwell.bcs": well_script(), "xnpcj.dlg": npc})
        with patch.dict("flir_config.reward_routes._CACHE", clear=True), \
                patch("flir_config.reward_routes.RewardCopies", NoCopies), \
                patch("flir_config.native_export.RewardCopies", NoCopies):
            fitted = self.fit()
            self.assertEqual(fitted["not_used"], [])
            written = self.apply(fitted)
        earlier = written["starts"][written["full"]]
        revisions = self.output / "campaign-store" / "revisions"
        files = {path.name: path.read_bytes() for path in revisions.iterdir() if path.name.startswith(earlier)}
        fitted = self.fit()
        self.assertEqual(sorted(row["id"] for row in fitted["not_used"]), ["bg2:mode2:item:14", "bg2:mode2:target:17"])
        later = self.apply(fitted)
        self.assertNotEqual(later["starts"][later["full"]], earlier)
        self.assertEqual({path.name: path.read_bytes() for path in revisions.iterdir()
                          if path.name.startswith(earlier)}, files)
        self.assertEqual(self.exported["notes"], [
            f"Saved games of revision {earlier[:12]} keep the extended reward route for UHOGRE01, although "
            "XNPCJ.DLG state 0 also gives SHLD26, but not with a copy of a reward reply of UHOGRE01.DLG, so the "
            "route cannot tell whether it is the same reward"])
        # Its route stays prepared for those saves; the copy stays as it is.
        overrides = self.output / "resource-overrides"
        self.assertEqual((overrides / "UHOGRE01.DLG").read_bytes(), mode2_rewards.patch_reward("dlg:uhogre01", route))
        self.assertFalse((overrides / "XNPCJ.DLG").exists())

    def test_a_mod_that_changes_an_earlier_revisions_route_stops_apply(self):
        self.build("bg2ee", resources={"uhogre01.dlg": ogre_dialogue(), "ddwell.bcs": well_script()})
        written = self.apply(self.fit())
        earlier = written["starts"][written["full"]]
        # Another mod's interjection then replaces the ogre's dialogue. The new
        # configuration leaves the route out, but the earlier revision's saves
        # need the dialogue as Apply prepared it for them: the runtime would stop
        # at the next start, so Apply says so and writes nothing.
        route, npc, state = self.interjected(lambda text: text + b'\nSetGlobal("XNPCJ","GLOBAL",1)')
        (self.game / "override" / "uhogre01.dlg").write_bytes(route)
        (self.game / "override" / "xnpcj.dlg").write_bytes(npc)
        self.rescan()
        fitted = self.fit()
        self.assertEqual(sorted(row["id"] for row in fitted["not_used"]), ["bg2:mode2:item:14", "bg2:mode2:target:17"])
        before = snapshot(self.output)
        with self.assertRaisesRegex(ProfileError, f"Saved games of revision {earlier[:12]} need UHOGRE01.DLG as it "
                                    "was when they were exported, but it changed in the game since then; the "
                                    "runtime checks it at every start and would stop for every game. Restore "
                                    "UHOGRE01.DLG before applying"):
            self.apply(fitted)
        self.assertEqual(snapshot(self.output), before)


class OtherWorkspaceTests(RouteGame):
    """A new configuration workspace applies over the routes another one applied.

    The game then holds the first workspace's patched dialogue and script, the
    reward texts it appended and its revisions, which the saves of those
    settings use. The player lost that workspace (Windows reinstalled, another
    PC, AppData cleared).
    """

    sources, targets = SOURCES[:2], TARGETS[:2]

    def apply_from(self, workspace: Path) -> set[str]:
        """Apply as the editor does from <workspace>: the game's revisions, export, deploy."""
        fitted = self.fit()
        draft = self.draft()
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            written = write_starts(fitted["profile"], fitted["plan"], self.catalog, self.world,
                                   workspace / "prepared")
            adopt_revisions(self.game, workspace / "native")
            self.exported = export_native(workspace / "prepared", self.catalog, self.world, workspace / "native",
                                          starts=written["starts"], main=written["main"])
        with patch("flir_config.activation.require_closed"):
            deploy_export(self.game, workspace / "native")
        return set(written["starts"].values())

    def test_a_new_workspace_applies_over_the_routes_another_applied(self):
        self.build("bg2ee")
        earlier = self.apply_from(self.root / "workspace-a")
        applied = snapshot(self.game)
        overrides = {name: applied[str(Path("override", name))] for name in ("uhogre01.dlg", "ddwell.bcs")}
        self.assertIn(str(Path("lang/en_US/dialog.tlk")), applied)
        # Workspace A is lost; a new one scans the game as A left it and applies other settings.
        self.rescan()
        self.sources = SOURCES[:1]
        later = self.apply_from(self.root / "workspace-b")
        self.assertFalse(earlier & later)
        # A's revisions recompile as they were: nothing is kept apart, nothing noted.
        self.assertNotIn("notes", self.exported)
        after = snapshot(self.game)
        pointers = {str(Path("randomiser-campaigns", name)) for name in ("next.lua", "source-gate.lua")}
        self.assertEqual({name for name, digest in applied.items() if after.get(name) != digest}, pointers)
        revisions = Path("randomiser-campaigns/campaign-store/revisions")
        self.assertTrue(all(Path(name).parent == revisions and Path(name).name.split(".")[0] in later
                            for name in set(after) - set(applied)))
        self.assertEqual({name: after[str(Path("override", name))] for name in overrides}, overrides)
        gate = self.game / "randomiser-campaigns" / "source-gate.lua"
        self.assertEqual(gate.read_bytes(), _render_lua(sorted(earlier | later)))
        # Workspace A comes back (a backup) and applies again, keeping B's revisions too.
        self.rescan()
        self.sources = SOURCES[:2]
        last = self.apply_from(self.root / "workspace-a")
        self.assertNotIn("notes", self.exported)
        self.assertEqual(gate.read_bytes(), _render_lua(sorted(earlier | later | last)))
        self.assertTrue(all(snapshot(self.game)[name] == digest for name, digest in after.items()
                            if name not in pointers))


class RouteCheckTests(RouteGame):
    def test_text_routes_need_a_translation_and_the_journal_table(self):
        for language, journal, reason in (
                (None, True, "the game keeps its text outside a language directory, so CIR cannot tell which "
                             "translation of Item Randomiser's reward texts it needs"),
                ("es_ES", True, "Item Randomiser's reward texts have no Spanish translation yet"),
                ("nb_NO", True, "Item Randomiser's reward texts have no translation for the game language nb_no"),
                ("en_US", False, "the game has no BGEE.LUA journal table")):
            with self.subTest(reason=reason):
                self.build("bg2ee", language=language, journal=journal)
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

    def test_a_change_after_an_earlier_check_run_stops_the_next_one(self):
        self.build("bg2ee")
        self.fit()
        (self.game / "override" / "uhogre01.dlg").write_bytes(dialogue_fixture(
            mode2_rewards.resource_specs("dlg:uhogre01")))
        with self.assertRaisesRegex(ChangedSinceScan, "rescan before export: dlg:uhogre01"):
            self.fit()

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


class LanguageTests(ContainerQuestTests):
    """Reward texts in the game's language, the female wording in its dialogF.tlk."""

    # The game's own strings, in both files: Apply keeps them as they are.
    MALE, FEMALE = ["Blade", "Épée de test"], ["Blade (f)", "Épée de test (f)"]

    def places(self) -> list[dict]:
        return RouteGame.places(self)  # Other chests take the tiers of routes left out.

    def texts(self, language: str) -> dict[str, tuple[str, str]]:
        """The well's and the golems' texts of the translation, (text, female wording)."""
        lines = translations.lines(language, mode2_rewards.text_lines("bcs:ddwell") + container_rewards.TEXT_LINES)
        return {"well": lines["in-game.tra", 399], "golems": lines["in-game.tra", 101]}

    def test_polish_and_german_texts_get_the_same_numbers_in_both_files(self):
        for language in ("pl_PL", "de_DE"):
            with self.subTest(language=language):
                self.build_quests("bg2ee", language=language, female=make_tlk(self.FEMALE))
                before = snapshot(self.game)
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                self.apply(fitted)
                self.assertEqual(snapshot(self.game), before)
                folder = self.output / "game-overrides" / "lang" / language.casefold()
                male = _Tlk((folder / "dialog.tlk").read_bytes())
                female = _Tlk((folder / "dialogF.tlk").read_bytes())
                self.assertEqual(male.count, female.count)
                self.assertEqual([male.text(n, set()) for n in range(2)], self.MALE)
                self.assertEqual([female.text(n, set()) for n in range(2)], self.FEMALE)
                found = {male.text(n, set()): n for n in range(2, male.count)}
                texts = self.texts(language)
                for name, (text, wording) in texts.items():
                    self.assertEqual(female.text(found[text], set()), wording, name)
                # The scripts show those numbers.
                overrides = self.output / "resource-overrides"
                self.assertEqual((overrides / "DDWELL.BCS").read_bytes(), mode2_rewards.patch_reward(
                    "bcs:ddwell", self.resources["ddwell.bcs"], refs={40850: found[texts["well"][0]]}))
                self.assertEqual((overrides / "KPGOL01.BCS").read_bytes(), container_rewards.patch_container_resource(
                    "bcs:kpgol01", self.containers()["kpgol01.bcs"], refs={38758: found[texts["golems"][0]]}))
                # Polish and German word the golems' text the same for both;
                # Polish has its own female wording of it.
                self.assertEqual(texts["golems"][0] != texts["golems"][1], language == "pl_PL")
                # A second Apply uses the same numbers.
                first = snapshot(self.output)
                self.apply(fitted)
                self.assertEqual(snapshot(self.output), first)

    def test_a_language_without_a_translation_leaves_only_the_text_routes_out(self):
        self.build_quests("bg2ee", language="es_ES", female=make_tlk(self.FEMALE))
        fitted = self.fit()
        reason = "Item Randomiser's reward texts have no Spanish translation yet"
        self.assertEqual(sorted((row["id"], row["reason"]) for row in fitted["not_used"]), [
            ("bg2:mode2:item:27", "The extended reward route for DDWELL cannot be used: " + reason),
            ("bg2:mode2:target:13", "The extended reward container of AX1H13 cannot be used: " + reason),
            ("bg2:mode2:target:27", "The extended reward route for DDWELL cannot be used: " + reason)])
        self.apply(fitted)
        self.assertFalse((self.output / "game-overrides").exists())

    def test_a_dialogf_of_another_length_leaves_the_text_routes_out(self):
        self.build_quests("bg2ee", language="pl_PL", female=make_tlk(self.FEMALE + ["Another mod (f)"]))
        reason = ("the game's dialogF.tlk and dialog.tlk hold different numbers of strings (3 and 2), so a new "
                  "text cannot get the same number in both")
        fitted = self.fit()
        self.assertEqual(sorted((row["id"], row["reason"]) for row in fitted["not_used"]), [
            ("bg2:mode2:item:27", "The extended reward route for DDWELL cannot be used: " + reason),
            ("bg2:mode2:target:13", "The extended reward container of AX1H13 cannot be used: " + reason),
            ("bg2:mode2:target:27", "The extended reward route for DDWELL cannot be used: " + reason)])
        self.apply(fitted)
        self.assertFalse((self.output / "game-overrides").exists())

    def test_a_new_translation_reaches_the_next_check(self):
        # The check keeps what it read per scan, but not past a change of CIR's
        # translations: a Spanish translation added later is used at once.
        self.build_quests("bg2ee", language="es_ES", female=make_tlk(self.FEMALE))
        copy = self.root / "cir" / "languages"
        shutil.copytree(ROOT / "languages", copy, ignore=shutil.ignore_patterns("iconv"))
        with patch.object(translations, "LANGUAGES_DIR", copy):
            self.assertEqual(len(self.fit()["not_used"]), 3)
            english = translations.texts("en_US", "in-game.tra")
            (copy / "spanish").mkdir()
            (copy / "spanish" / "in-game.tra").write_bytes("\r\n".join(
                f"@{number} = ~{english[number][0]} (es)~" for number in (101, 399)).encode("cp1252"))
            self.assertEqual(self.fit()["not_used"], [])
