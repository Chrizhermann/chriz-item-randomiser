"""Item Randomiser's chapter 3 places on synthetic BG2:EE and EET games; no real games.

Rows 25, 49, 215 and 252 of the extended BG2 locations table (its
chapter3.fl). Each reward goes to the first who can take it of the vampire
drawn for it, the shadow thief drawn for it at his corpse and Aran at his
corpse (chapter3.py). The export finds each creature where Item Randomiser's
handler looks for it; one the runtime could not tell from another is left out
of its group, and the place without a vampire, or without a thief and Aran.
"""

import json
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import chapter3
from flir_config.campaigns import read_campaign
from flir_config.original_registrations import target_kind
from flir_config.reward_routes import ChangedSinceScan
from test_mode2_rewards import action, block
from test_postinstall_resources import snapshot
from test_postinstall_world_formats import make_are
from test_reward_routes import RouteGame, named_cre, ogre_dialogue, place

ROWS = ("bg2:mode2:target:25", "bg2:mode2:target:49", "bg2:mode2:target:215", "bg2:mode2:target:252")
PLACES = tuple("original-target:" + row for row in ROWS)
# Bodhi's vampires as AR0801.BCS creates them for Aran's last job, and
# Arkanis Gath as AR0300.BCS does for Bodhi's first.
VAMPIRES = {"lassal02": "Lassal02", "bodvam01": "bodvam01", "bodvam02": "bodvam02", "bodtan": "bodtan"}
# Aran's guild house (AR0329): its thieves and Aran, as the area places them.
GUILD = (("Booter", "booter02", "booter02"), ("Haz", "arnwar06", "arnwar06"),
         ("Dedral", "arnwar07", "arnwar07"), ("Aran", "aran02", "aran"))
# Rogue Rebalancing's own guild (RR#010).
RR_GUILD = (("Booter", "rr#bootr", "rr#bootr"), ("Haz", "rr#haz", "rr#haz"),
            ("Dedral", "rr#dedrl", "rr#dedrl"), ("Aran", "rr#aran2", "rr#aran2"))
VAMPIRE_REASON = ("Item Randomiser's chapter 3 place needs one of Bodhi's vampires (LASSAL02, BODVAM01, "
                  "BODVAM02) in AR0801, once and with a script name of its own there: ")
THIEF_REASON = ("Item Randomiser's chapter 3 place needs one of Aran's thieves or Aran himself where its handler "
                "looks for them, once and with a script name of their own there: ")


def scripted(*blocks: tuple[str, ...]) -> bytes:
    """An area script whose blocks each create the creatures they name."""
    return ("SC\n" + "".join(block("".join(action(7, text=name.upper()) for name in names)) for names in blocks) +
            "SC\n").encode()


def creating(*names: str) -> bytes:
    """An area script whose one block creates the creatures <names>."""
    return scripted(names)


def area(script: str | None = None, actors=()) -> bytes:
    """An area with its own script <script> and <actors> (name, CRE)."""
    data = make_are([], [], actors)
    data[0x94:0x9C] = (script or "").upper().encode("ascii").ljust(8, b"\0")
    return bytes(data)


def never_appears(data: bytes, index: int) -> bytes:
    """The area with its actor <index> placed for no hour of the day."""
    result = bytearray(data)
    offset = struct.unpack_from("<I", result, 0x54)[0] + index * 0x110 + 0x40
    struct.pack_into("<I", result, offset, 0)
    return bytes(result)


def member(resref: str, script_name: str, area_name: str) -> dict:
    return {"resref": resref, "script_name": script_name, "area": area_name}


class Chapter3Game(RouteGame):
    """The four places beside the ogre's shield, the one reward, and a chest of its tier."""

    sources = ("bg2:mode2:item:14",)
    targets = ROWS
    # Rows 215 and 252 come with Item Randomiser's scrolls.
    options = {"scrolls": True}

    def places(self) -> list[dict]:
        return [place("bg2:base:target:9002", "2", 0)]

    def files(self, *, rr: bool = False) -> dict[str, bytes]:
        """The areas and creatures as the unmodded game (with <rr>, Rogue
        Rebalancing too) has them."""
        files = {"ar0801.are": area("ar0801"), "ar0801.bcs": creating(*VAMPIRES),
                 "ar0300.are": area("ar0300"), "ar0300.bcs": creating("arkanisg"),
                 "ar0329.are": area(None, [(actor, resref) for actor, resref, _ in GUILD]),
                 "arkanisg.cre": named_cre("arkanisg")}
        files.update({f"{name}.cre": named_cre(script) for name, script in VAMPIRES.items()})
        files.update({f"{resref}.cre": named_cre(script) for _, resref, script in GUILD})
        if rr:
            files["rr#010.are"] = area(None, [(actor, resref) for actor, resref, _ in RR_GUILD])
            files.update({f"{resref}.cre": named_cre(script) for _, resref, script in RR_GUILD})
        return files

    def build_chapter3(self, platform: str, *, rr: bool = False, changes: dict[str, bytes | None] | None = None):
        """The game, with <changes> to its files (None removes one)."""
        files = {**self.files(rr=rr), **(changes or {})}
        self.build(platform, resources={"uhogre01.dlg": ogre_dialogue()},
                   extra={name: data for name, data in files.items() if data is not None})

    def native(self, written: dict, revision: str | None = None) -> dict:
        return json.loads((self.output / "campaign-store" / "revisions" /
                           f"{revision or written['starts'][written['full']]}.native.json").read_bytes())

    def endpoint(self, changes: dict[str, bytes | None] | None = None, *, rr: bool = False) -> dict:
        """The place's endpoint in a BG2:EE game with <changes>; every place has it."""
        self.build_chapter3("bg2ee", rr=rr, changes=changes)
        fitted = self.fit()
        self.assertEqual(fitted["not_used"], [])
        native = self.native(self.apply(fitted))
        endpoints = [native["targets"][identity] for identity in PLACES]
        self.assertEqual(len({endpoint["identity"] for endpoint in endpoints}), 4)
        for other in endpoints[1:]:
            self.assertEqual({key: value for key, value in other.items() if key != "identity"},
                             {key: value for key, value in endpoints[0].items() if key != "identity"})
        return {key: value for key, value in endpoints[0].items() if key != "identity"}

    def left_out(self) -> dict[str, str]:
        fitted = self.fit()
        native = self.native(self.apply(fitted))
        for identity in PLACES:
            self.assertNotIn(identity, native["targets"])
        return {row["id"]: row["reason"] for row in fitted["not_used"]}


VANILLA = {"kind": "first-of", "groups": [
    {"members": [member("lassal02", "Lassal02", "AR0801"), member("bodvam01", "bodvam01", "AR0801"),
                 member("bodvam02", "bodvam02", "AR0801")],
     "corpse_only": False, "eligible": {"shld26": [1, 2, 3]}},
    {"members": [member("booter02", "booter02", "AR0329"), member("arnwar06", "arnwar06", "AR0329"),
                 member("arkanisg", "arkanisg", "AR0300")],
     "corpse_only": True, "eligible": {"shld26": [1, 2, 3]}},
    {"members": [member("aran02", "aran", "AR0329")], "corpse_only": True}]}


class Chapter3PlaceTests(Chapter3Game):
    def test_the_vampires_thieves_and_aran_take_the_reward_in_both_games(self):
        for platform, starts in (("bg2ee", {"SOA"}), ("eet", {"BG1", "SOD", "SOA"})):
            with self.subTest(platform=platform):
                self.build_chapter3(platform)
                rows = {row["id"]: row for row in self.draft()["targets"]}
                for row in ROWS:
                    self.assertEqual((rows[row]["area_resource"], rows[row]["binding_problem"],
                                      rows[row]["members"]),
                                     ("are:ar0801", "group-requires-runtime-selection",
                                      ["cre:" + name for name in ("lassal02", "bodvam01", "bodvam02", "booter02",
                                                                  "arnwar06", "arkanisg", "aran02", "rr#aran2")]))
                    self.assertEqual(target_kind(rows[row]), "group")
                before = snapshot(self.game)
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                self.assertTrue(set(PLACES) <= {row["id"] for row in fitted["plan"]["target_registrations"]})
                written = self.apply(fitted)
                self.assertEqual(set(written["starts"]), starts)
                self.assertEqual(snapshot(self.game), before)
                for revision in written["starts"].values():
                    campaign = read_campaign(self.store, revision)
                    self.assertEqual({row["record"]["kind"] for row in campaign["targets"]
                                      if row["id"] in PLACES}, {"group"})
                    native = self.native(written, revision)
                    for identity in PLACES:
                        endpoint = native["targets"][identity]
                        self.assertEqual({key: value for key, value in endpoint.items() if key != "identity"},
                                         VANILLA)
                    # The areas where the handler looks, their scripts, and each
                    # creature the runtime may give the reward to.
                    hashes = native["resource_hashes"]
                    for name in ("AR0801.ARE", "AR0801.BCS", "AR0329.ARE", "AR0300.ARE", "AR0300.BCS",
                                 "LASSAL02.CRE", "BODVAM01.CRE", "BODVAM02.CRE", "BOOTER02.CRE", "ARNWAR06.CRE",
                                 "ARKANISG.CRE", "ARAN02.CRE"):
                        self.assertIn(name, hashes)
                    for name in ("BODTAN.CRE", "ARNWAR07.CRE"):
                        self.assertNotIn(name, hashes)

    def test_with_rogue_rebalancing_its_guilds_thieves_take_it(self):
        # Item Randomiser takes Rogue Rebalancing's thieves where its Aran
        # (RR#ARAN2.CRE) is in the game; either Aran takes it at his corpse.
        self.assertEqual(self.endpoint(rr=True), {"kind": "first-of", "groups": [
            VANILLA["groups"][0],
            {"members": [member("rr#bootr", "rr#bootr", "RR#010"), member("rr#haz", "rr#haz", "RR#010"),
                         member("arkanisg", "arkanisg", "AR0300")],
             "corpse_only": True, "eligible": {"shld26": [1, 2, 3]}},
            {"members": [member("aran02", "aran", "AR0329"), member("rr#aran2", "rr#aran2", "RR#010")],
             "corpse_only": True}]})

    def test_each_reward_draws_among_those_who_can_use_it(self):
        # The shield is no fighter's. With Lassal a fighter the other two
        # vampires take it; a reward none of them can use goes to any.
        def fighter(script_name: str) -> bytes:
            data = bytearray(named_cre(script_name))
            data[0x273] = 2
            return bytes(data)

        for fighters, eligible in ((("lassal02",), [2, 3]), (("lassal02", "bodvam01", "bodvam02"), [1, 2, 3])):
            with self.subTest(fighters=fighters):
                self.build_chapter3("bg2ee", changes={f"{name}.cre": fighter(VAMPIRES[name]) for name in fighters})
                shield = bytearray((self.game / "override" / "shld26.itm").read_bytes())
                shield[0x1F] |= 0x08
                (self.game / "override" / "shld26.itm").write_bytes(shield)
                self.rescan()
                fitted = self.fit()
                self.assertEqual(fitted["not_used"], [])
                groups = self.native(self.apply(fitted))["targets"][PLACES[0]]["groups"]
                self.assertEqual([group.get("eligible") for group in groups],
                                 [{"shld26": eligible}, {"shld26": [1, 2, 3]}, None])

    def test_a_creature_the_runtime_could_not_tell_from_another_is_left_out_of_its_group(self):
        vampires, thieves, aran = VANILLA["groups"]
        guild = [(actor, resref) for actor, resref, _ in GUILD]
        for label, changes, expected in (
                # Two of one vampire: the area's script creates Bodvam01 in two blocks.
                ("created twice", {"ar0801.bcs": scripted(tuple(VAMPIRES), ("bodvam01",))},
                 [{**vampires, "members": [vampires["members"][0], vampires["members"][2]],
                   "eligible": {"shld26": [1, 2]}}, thieves, aran]),
                # Another creature of its area by its script name.
                ("named alike", {"bodvam02.cre": named_cre("bodvam01")},
                 [{**vampires, "members": vampires["members"][:1], "eligible": {"shld26": [1]}}, thieves, aran]),
                ("named as an actor", {"ar0329.are": area(None, guild + [("Other", "other01")]),
                                       "other01.cre": named_cre("booter02")},
                 [vampires, {**thieves, "members": thieves["members"][1:], "eligible": {"shld26": [1, 2]}},
                  aran]),
                # In two of the handler's areas.
                ("in two areas", {"ar0300.bcs": creating("arkanisg", "booter02")},
                 [vampires, {**thieves, "members": thieves["members"][1:], "eligible": {"shld26": [1, 2]}},
                  aran]),
                # Placed for no hour of the day, or without a script name.
                ("never there", {"ar0329.are": never_appears(area(None, guild), 1)},
                 [vampires, {**thieves, "members": [thieves["members"][0], thieves["members"][2]],
                             "eligible": {"shld26": [1, 2]}}, aran]),
                ("without a name", {"arkanisg.cre": named_cre("")},
                 [vampires, {**thieves, "members": thieves["members"][:2], "eligible": {"shld26": [1, 2]}},
                  aran]),
                # Neither area script creates him, nor is he placed.
                ("absent", {"ar0300.bcs": None},
                 [vampires, {**thieves, "members": thieves["members"][:2], "eligible": {"shld26": [1, 2]}},
                  aran]),
                ("no Aran", {"aran02.cre": None}, [vampires, thieves]),
                ("no thieves", {"ar0329.are": area(None, [("Aran", "aran02")]), "ar0300.bcs": None},
                 [vampires, aran])):
            with self.subTest(label):
                self.assertEqual(self.endpoint(changes), {"kind": "first-of", "groups": expected})

    def test_without_a_vampire_or_without_a_thief_and_aran_the_place_is_left_out(self):
        for label, changes, reason in (
                ("no vampire", {"ar0801.bcs": creating("bodtan")}, VAMPIRE_REASON),
                ("no area script", {"ar0801.are": area(None)}, VAMPIRE_REASON),
                ("no thief or Aran", {"ar0329.are": area(None), "ar0300.bcs": None}, THIEF_REASON),
                ("unreadable script", {"ar0801.bcs": b"SC\nCR\n"},
                 "AR0801.BCS cannot be read as a compiled script: it does not start and end with SC")):
            with self.subTest(label):
                self.build_chapter3("bg2ee", changes=changes)
                left_out = self.left_out()
                for row, identity in zip(ROWS, PLACES):
                    self.assertEqual(left_out[row], reason + identity if reason.endswith(": ") else reason)
                self.assertNotIn("bg2:mode2:item:14", left_out)

    def test_a_game_without_the_vampires_area_leaves_the_places_out(self):
        self.build_chapter3("bg2ee", changes={"ar0801.are": None})
        self.assertEqual({row: reason for row, reason in self.left_out().items() if row in ROWS},
                         {row: "area-resource-absent" for row in ROWS})

    def test_an_area_script_changed_since_the_scan_stops_the_check(self):
        self.build_chapter3("bg2ee")
        (self.game / "override" / "ar0801.bcs").write_bytes(creating("lassal02"))
        with self.assertRaisesRegex(ChangedSinceScan, "rescan before export: bcs:ar0801"):
            self.fit()
        self.rescan()
        groups = self.native(self.apply(self.fit()))["targets"][PLACES[0]]["groups"]
        self.assertEqual(groups[0]["members"], [member("lassal02", "Lassal02", "AR0801")])

    def test_the_handlers_creatures_are_its_lists(self):
        self.assertEqual(chapter3.candidates(set()), ["cre:" + name for name in (
            "lassal02", "bodvam01", "bodvam02", "booter02", "arnwar06", "arkanisg", "aran02", "rr#aran2")])
        self.assertEqual(chapter3.candidates({"cre:rr#aran2"})[3:6], ["cre:rr#bootr", "cre:rr#haz", "cre:arkanisg"])
        self.assertEqual(chapter3.created(scripted(("lassal02", "bodvam01"), ("lassal02",)), "AR0801.BCS"),
                         {"lassal02": 2, "bodvam01": 1})


if __name__ == "__main__":
    unittest.main()
