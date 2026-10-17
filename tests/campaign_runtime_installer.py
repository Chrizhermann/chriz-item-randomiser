#!/usr/bin/env python3
"""Synthetic EET installs of Chriz Item Randomiser (cir.tp2, the campaign runtime).

Builds minimal KEY/BIFF/TLK games below a caller-supplied temporary root,
stages the mod exactly as the release package selects it (from the working tree
or from a built release ZIP) and runs the real TP2 with WeiDU. It never reads
or writes a real game. Every case compares the whole synthetic game, outside
WeiDU's own bookkeeping, with the expected bytes.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "tools"))

from ie_fake_game import (  # noqa: E402
    MatrixFailure,
    ProcessResult,
    Reporter,
    active_weidu_log,
    build_tlk,
    require_existing_file,
    require_scratch_parent,
    run_process,
    sha256_bytes,
    snapshot_tree,
    write_bytes,
)
import build_release  # noqa: E402
import campaign_patch_fixtures as fixtures  # noqa: E402
import tweak_fixtures as tweaks  # noqa: E402
from build_mode1_fixture import mark_initial_gam  # noqa: E402
from flir_config.activation import validate_runtime  # noqa: E402
from flir_config.prepared import REASONS, read_prepared  # noqa: E402
from flir_config.profiles import ProfileError  # noqa: E402

RESOURCE_TYPES = {"ITM": 1005, "BCS": 1007, "IDS": 1008, "ARE": 1010, "DLG": 1011, "GAM": 1013,
                  "STO": 1014, "MENU": 1032, "LUA": 1033}
RUNTIME_MODULES = (
    "M_FLPRUI.lua", "M_FLPR.lua", "FLPRCore.lua", "FLPRCur.lua", "FLPRGat.lua",
    "FLPRGlob.lua", "FLPRHost.lua", "FLPRNat.lua", "FLPRScript.lua",
    "FLPRShop.lua", "FLPRSpell.lua", "FLPRRun.menu",
)
GATE_MODULES = ("FLPRGateAsm.lua", "FLPRCampaignPolicy.lua", "FLPRInventoryAsm.lua")
# Where the configurator applies settings; the installer only creates the folder.
CAMPAIGN_DATA = "randomiser-campaigns"
# The mod folder, its TP2 and its one component.
MOD_FOLDER = "cir"
TP2 = "cir/cir.tp2"
COMPONENT = 0
# M_FLPRTXT.lua keys and the in-game.tra strings they name, in installation order.
MESSAGES = (("not_running", 1700), ("unsupported", 1701), ("other_campaign", 1702),
            ("setup_failed", 1703), ("start_plain", 1704), ("ready", 1705), ("stopped", 1706))
MESSAGE_FILE = "override/m_flprtxt.lua"
MARKER = b"\x01"
LUAJIT_INI = (b"[General]\r\n; LuaPatchMode=INTERNAL uses the engine's own Lua\r\n"
              b"LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL\r\nLuaLibrary=lua51.dll\r\n"
              b"LuaVersionExternal=5.1\r\n")
DEBUG = os.environ.get("FLIR_RUNTIME_INSTALLER_DEBUG") == "1"

# Call shapes of the reference EET UI.MENU, LeUI 4.9.1, Infinity UI 1.17 and
# the EEUITweaks 4.0.7 chargen fragments, in a minimal synthetic menu.
MENU = b"""menu
{
\tname 'START_SP'
\tbutton
\t{
\t\ttext "NEW_GAME_BUTTON"
\t\taction
\t\t"
\t\t\tInfinity_PopMenu();
\t\t\tcheckCampaign = Infinity_GetINIValue('Program Options','Active Campaign')
\t\t\tif startCampaignData[checkCampaign].forceParty then
\t\t\t\tstartEngine:OnCreatePartyButtonClick();
\t\t\telse
\t\t\t\tstartEngine:OnNewGameButtonClick();
\t\t\tend
\t\t"
\t}
}
menu
{
\tname 'CHARGEN'
\tbutton
\t{
\t\ttext lua "chargenAcceptOrExport()"
\t\tclickable lua "createCharScreen:IsChargenComplete()"
\t\taction "createCharScreen:AcceptCharacter(-1)"
\t}
}
menu
{
\tname 'CHARGEN_DIFFICULTY'
\tbutton
\t{
\t\tenabled "fromSinglePlayer"
\t\taction
\t\t"
\t\t\tInfinity_PopMenu()
\t\t\tcreateCharScreen:AcceptCharacter(currentDifficulty)
\t\t"
\t}
\tbutton
\t{
\t\tenabled "fromMultiPlayer"
\t\taction
\t\t"
\t\t\tconnectionScreen:OnNewGameButtonClick()
\t\t"
\t}
}
""".replace(b"\n", b"\r\n")
UI_MOD_MENU = MENU + (b"menu\n{\n\tname 'LEUI_START'\n\tbutton\n\t{\n\t\taction\n\t\t\"\n"
                      b"\t\t\tstartEngine:OnNewGameButtonClick()\n\t\t\"\n\t}\n\tbutton\n\t{\n"
                      b"\t\taction\n\t\t\"\n\t\t\tcreateCharScreen:AcceptCharacter(-1)\n\t\t\"\n\t}\n}\n")


# An unrelated mod installed before the randomiser.
EARLIER_TP2 = (b"BACKUP ~earlier/backup~\r\nAUTHOR ~synthetic~\r\n"
               b"BEGIN ~Earlier synthetic mod~ DESIGNATED 10\r\n"
               b"COPY_EXISTING ~UI.MENU~ ~override/earlier.txt~\r\n")


class Game:
    def __init__(self, root: Path, weidu: Path) -> None:
        self.root = root
        self.weidu = weidu

    def run(self, *arguments: str, tp2: str = TP2, language: int = 0) -> ProcessResult:
        return run_process(
            [str(self.weidu), tp2, "--game", ".", *arguments,
             "--language", str(language), "--use-lang", "en_US", "--no-exit-pause", "--quick-log"],
            self.root, "WeiDU " + tp2)

    def install(self, component: int = COMPONENT) -> ProcessResult:
        return self.run("--force-install-list", str(component))

    def uninstall(self, component: int = COMPONENT) -> ProcessResult:
        return self.run("--force-uninstall-list", str(component))

    def data(self) -> dict[str, bytes]:
        """The game as the engine and the configurator see it, without WeiDU state."""
        return {key: value for key, value in snapshot_tree(self.root).items()
                if not key.startswith((MOD_FOLDER + "/", "earlier/", "weidu_external/"))
                and key not in ("weidu.log", "weidu.conf")}

    def active(self, component: int) -> bool:
        return self.entries(component) > 0

    def entries(self, component: int) -> int:
        suffix = f" #{component} "
        return sum(line.casefold().startswith(f"~{TP2}~")
                   and (line + " ").find(suffix) > 0 for line in active_weidu_log(self.root))


def gam(variables: Sequence[tuple[str, int]], trailer: bytes = b"") -> bytes:
    """A GAMEV2.0 template: header, the variable table, then unrelated data."""
    header = bytearray(0xB4)
    header[:8] = b"GAMEV2.0"
    for offset in range(0x10, 0xB4, 4):
        struct.pack_into("<I", header, offset, 0x1000 + offset)
    struct.pack_into("<II", header, 0x38, 0xB4 if variables else 0, len(variables))
    records = bytearray()
    for index, (name, value) in enumerate(variables):
        record = bytearray(84)
        struct.pack_into("<32sHHIi", record, 0, name.encode("ascii"), 1, index, 7, value)
        record[0x2C:0x34] = struct.pack("<d", 0.5)
        record[0x34:0x54] = b"SCRIPT".ljust(32, b"\0")
        records += record
    return bytes(header + records + trailer)


DEFAULT_GAM = gam([("CHAPTER", 0), ("ENDOFBG1", 0), ("KNOWBRANWEN", 3)], b"PARTY" * 9)


def patch_menu(data: bytes) -> bytes:
    return (data.replace(b"startEngine:OnNewGameButtonClick(", b"FLPRUI_NewGame(")
            .replace(b"createCharScreen:AcceptCharacter(", b"FLPRUI_AcceptCharacter("))


def build_biff(resources: Sequence[tuple[str, str, bytes]]) -> bytes:
    payload_offset = 0x14 + 0x10 * len(resources)
    entries, payload = bytearray(), bytearray()
    for index, (_name, extension, data) in enumerate(resources):
        entries += struct.pack("<IIIHH", index, payload_offset + len(payload), len(data),
                               RESOURCE_TYPES[extension], 0)
        payload += data
    return b"BIFFV1  " + struct.pack("<III", len(resources), 0, 0x14) + bytes(entries + payload)


def build_key(resources: Sequence[tuple[str, str, bytes]], bif_size: int) -> bytes:
    bif_name = b"data/runtime.bif\0"
    resource_table = 0x18 + 0x0C
    name_offset = resource_table + 0x0E * len(resources)
    key = bytearray(b"KEY V1  " + struct.pack("<IIII", 1, len(resources), 0x18, resource_table))
    key += struct.pack("<IIHH", bif_size, name_offset, len(bif_name), 0)
    for index, (name, extension, _data) in enumerate(resources):
        key += struct.pack("<8sHI", name.encode("ascii"), RESOURCE_TYPES[extension], index)
    return bytes(key + bif_name)


def build_game(root: Path, mod: Path, weidu: Path, *, menu: bytes | None = MENU,
               baldur: bytes | None = DEFAULT_GAM, menu_in_override: bool = False,
               gam_in_override: bool = False, ini: bytes | None = LUAJIT_INI,
               override: Mapping[str, bytes | None] = {}, files: Mapping[str, bytes] = {},
               eet: bool = True, biff: Sequence[tuple[str, str, bytes]] = (),
               tlk: bytes | None = None) -> Game:
    """Synthetic EET game with EEex, LuaJIT and unrelated mod data.

    A menu, template, INI or override file given as None is absent. <biff>
    adds archived resources, <tlk> replaces the one-entry game text.
    """
    resources = [("OH6000", "ARE", b"synthetic EET marker"), *biff]
    files = dict(files)
    contents = {"EET.flag": b"1" if eet else None, "M___EEex.lua": b"EEex_Active = true\n",
                "UNRELATED.ITM": b"ITM V1  unrelated mod item", "M_OTHER.lua": b"-- another mod\n"}
    if menu is not None:
        if menu_in_override:
            contents["UI.MENU"] = menu
        else:
            resources.append(("UI", "MENU", menu))
    if baldur is not None:
        if gam_in_override:
            contents["BALDUR.GAM"] = baldur
        else:
            resources.append(("BALDUR", "GAM", baldur))
    contents.update(override)
    bif = build_biff(resources)
    write_bytes(root / "data" / "runtime.bif", bif)
    write_bytes(root / "chitin.key", build_key(resources, len(bif)))
    write_bytes(root / "dialog.tlk", build_tlk() if tlk is None else tlk)
    write_bytes(root / "lang" / "en_us" / "dialog.tlk", build_tlk() if tlk is None else tlk)
    (root / "override").mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        if data is not None:
            write_bytes(root / "override" / name, data)
    if ini is not None:
        files["InfinityLoader.ini"] = ini
    for name, data in files.items():
        write_bytes(root / name, data)
    shutil.copytree(mod, root / MOD_FOLDER)
    return Game(root, weidu)


def stage_worktree(repository: Path, destination: Path) -> None:
    """Copy the files the release package would publish, as currently on disk."""
    spec = build_release.load_spec((repository / build_release.SPEC_PATH).read_bytes())
    listed = subprocess.run(["git", "-C", str(repository), "ls-files", "-z", "-c", "-o",
                             "--exclude-standard"], capture_output=True, check=True).stdout
    tree = {path: ("100644", "") for path in listed.decode("utf-8").split("\0")
            if path and (repository / path).is_file()}
    for path in build_release.select_files(spec, tree):
        write_bytes(destination / path, (repository / path).read_bytes())


def stage_package(package: Path, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or name.parts[0] != MOD_FOLDER:
                raise MatrixFailure("release package has an unexpected entry")
            if not info.is_dir():
                write_bytes(destination / Path(*name.parts[1:]), archive.read(info))


def describe(result: ProcessResult) -> str:
    transcript = sha256_bytes(result.output.encode("utf-8", "replace"))[:16]
    return f"exit={result.returncode}, transcript={transcript}" + (f"\n{result.output}" if DEBUG else "")


def installed(result: ProcessResult) -> bool:
    return result.installed


def skipped(result: ProcessResult, message: str) -> bool:
    upper = result.output.upper()
    return "SKIPPING" in upper and "SUCCESSFULLY INSTALLED" not in upper and message in result.output


def refused(result: ProcessResult, message: str) -> bool:
    upper = result.output.upper()
    return (result.returncode != 0 and "NOT INSTALLED DUE TO ERRORS" in upper
            and message in result.output)


def utf8_translation(original: bytes, converted: bytes) -> bool:
    """A CP1250 translation as WeiDU converts it: the same text in UTF-8, coding line aside."""
    coding = re.compile(r"-\*-[\t ]*(?:en)?coding:[\t ]*[A-Za-z0-9-]+[\t ]*-\*-")
    try:
        text = converted.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return coding.sub("", text) == coding.sub("", original.decode("cp1250"))


def uninstalled(result: ProcessResult) -> bool:
    upper = result.output.upper()
    return (result.returncode == 0 and "ERROR" not in upper and "NOT UNINSTALLED" not in upper
            and "SKIPPING" not in upper)


def tlk_strings(data: bytes) -> list[str]:
    count, offset = struct.unpack_from("<II", data, 0x0A)
    strings = []
    for index in range(count):
        base = 0x12 + index * 0x1A
        start, length = struct.unpack_from("<II", data, base + 0x12)
        text = data[offset + start:offset + start + length]
        strings.append(text.decode("utf-8") if struct.unpack_from("<H", data, base)[0] & 1 else "")
    return strings


def in_game_messages(mod: Path) -> list[str]:
    text = (mod / "languages" / "english" / "in-game.tra").read_text(encoding="utf-8")
    strings = dict(re.findall(r"^@(\d+)=~([^~]*)~", text, re.M))
    return [strings[str(number)] for _key, number in MESSAGES]


def installed_messages(before: Mapping[str, bytes], after: Mapping[str, bytes],
                       messages: Sequence[str], texts: int = 0) -> dict[str, bytes] | None:
    """The changed game text and message file, if each message key names its own new string.

    <texts> more strings follow the messages: the quest and dialogue changes'.
    """
    changed = [key for key in after if key.endswith("dialog.tlk") and after[key] != before.get(key)]
    first = set()
    for key in changed:
        old, new = tlk_strings(before[key]), tlk_strings(after[key])
        if (new[:len(old)] != old or new[len(old):len(old) + len(messages)] != list(messages)
                or len(new) != len(old) + len(messages) + texts):
            return None
        first.add(len(old))
    if len(first) != 1 or MESSAGE_FILE not in after:
        return None
    start = first.pop()
    references = dict(re.findall(r"^\s*(\w+) = (\d+),\s*$", after[MESSAGE_FILE].decode("ascii"), re.M))
    if references != {key: str(start + index) for index, (key, _number) in enumerate(MESSAGES)}:
        return None
    return {key: after[key] for key in changed + [MESSAGE_FILE]}


def expected_install(game: Game, before: Mapping[str, bytes], after: Mapping[str, bytes], menu: bytes,
                     baldur: bytes, *, gam_written: bool = True, texts: int = 0) -> dict[str, bytes]:
    """Every installed byte; the game text only where it adds exactly the messages."""
    mod = game.root / MOD_FOLDER
    expected = dict(before)
    for name in RUNTIME_MODULES:
        expected["override/" + name.casefold()] = (mod / "prototype" / name).read_bytes()
    for name in GATE_MODULES:
        expected["override/" + name.casefold()] = (mod / "tools" / "probes" / name).read_bytes()
    expected.update(installed_messages(before, after, in_game_messages(mod), texts) or {})
    expected["override/ui.menu"] = patch_menu(menu)
    if gam_written:
        expected["override/baldur.gam"] = mark_initial_gam(baldur)
    expected["override/flrandomiser3.mrk"] = MARKER
    expected["override/flbhcryp.itm"] = (mod / "copy" / "flbhcryp.itm").read_bytes()
    # The record's wording is the installer's; its entries are checked separately.
    if RECORD in after:
        expected[RECORD] = after[RECORD]
    return expected


# The installer's record of its game changes, and their states in a game
# without any of the files they change.
RECORD = "override/flprpat.lua"
NOTHING_TO_PREPARE = {"quest_pool": "absent", "quest_altar": "absent", "quest_book": "absent",
                      "unshey": "absent", "flail_heads": "absent", "totsc_tome": "applied"}
CHANGE_FILES = {"quest_pool": "BLPOOL.BCS", "quest_altar": "TROALT.BCS", "quest_book": "PPBOOK.BCS",
                "unshey": "UNSHEY.DLG", "flail_heads": "KPGLAI01.DLG", "totsc_tome": "FLBHCRYP.ITM"}

# The optional tweaks that install together, and the stores that sell
# scrolls of Protection from Undead, with the order of their tables.
TWEAKS = ("9000", "10100", "10210", "10300")
SCROLL_STORES = {"SELL1": (["SCRL09", "POTN08", "scrl09", "AMUL01"], tweaks.SECTIONS),
                 "SELL2": (["POTN10", "SCRL09"], ("drinks", "sale", "cures", "purchased"))}


def recorded(game: Game, statuses: Mapping[str, str], journal: int = 0) -> bool:
    """FLPRPAT.lua, as the configurator reads it, lists exactly these states."""
    record = read_prepared(game.root)
    return ({name: entry.get("status") for name, entry in record.items()} == dict(statuses)
            and {name: entry.get("resource") for name, entry in record.items()} == CHANGE_FILES
            and record["unshey"].get("item") == "BELT04" and record["unshey"].get("journal") == journal)


def noted(result: ProcessResult, statuses: Mapping[str, str]) -> bool:
    """The installer told the player about every change it left out."""
    return all(f"cannot prepare {CHANGE_FILES[name]}, because {REASONS[status]}." in result.output
               for name, status in statuses.items() if status != "applied")


class Suite:
    def __init__(self, scratch: Path, mod: Path, weidu: Path, reporter: Reporter) -> None:
        self.scratch, self.mod, self.weidu, self.report = scratch, mod, weidu, reporter
        self.count = 0

    def game(self, **options) -> Game:
        self.count += 1
        return build_game(self.scratch / f"g{self.count:02d}", self.mod, self.weidu, **options)

    def check(self, name: str, condition: bool, result: ProcessResult | None = None) -> None:
        self.report.check(name, condition, describe(result) if result is not None and not condition else "")

    def lifecycle(self, name: str, menu: bytes = MENU, baldur: bytes = DEFAULT_GAM, gam_written: bool = True,
                  changes: Callable[[str, Game, dict, dict], tuple[dict[str, bytes], int]] | None = None,
                  **options) -> Game:
        """Install, check every byte, uninstall exactly, then reinstall identically.

        Re-installing over the installed component covers a player's Re-install,
        WeiDU's one-run uninstall and install, and a recovery that installs the
        same list again. <changes> checks the quest and dialogue changes and
        returns their files and the number of strings they add; by default the
        game has none of the files they change.
        """
        game = self.game(menu=menu, baldur=baldur, **options)
        before = game.data()
        result = game.install()
        self.check(f"{name}_Installs", installed(result) and game.active(COMPONENT), result)
        after = game.data()
        if changes is None:
            self.check(f"{name}_RecordsNothingToPrepare", recorded(game, NOTHING_TO_PREPARE))
            changed, texts = {}, 0
        else:
            changed, texts = changes(name, game, before, after)
        self.check(f"{name}_AddsMessagesToGameText",
                   installed_messages(before, after, in_game_messages(self.mod), texts) is not None)
        expected = expected_install(game, before, after, menu, baldur, gam_written=gam_written, texts=texts)
        expected.update(changed)
        self.check(f"{name}_WritesOnlyExpectedFiles", after == expected)
        self.check(f"{name}_CreatesCampaignDataFolder", (game.root / CAMPAIGN_DATA).is_dir())
        self.check(f"{name}_ConfiguratorAcceptsInstall", accepts(game.root))
        result = game.uninstall()
        self.check(f"{name}_Uninstalls", uninstalled(result) and not game.active(COMPONENT), result)
        # WeiDU keeps the strings a component appended to the game text, unused;
        # a re-install finds and reuses them.
        added_text = {key: after[key] for key in after
                      if key.endswith("dialog.tlk") and after[key] != before.get(key)}
        self.check(f"{name}_UninstallRestoresEveryByteButAddedGameText",
                   game.data() == {**before, **added_text})
        # WeiDU does not track folders: one it created stays behind, empty.
        folder = game.root / CAMPAIGN_DATA
        self.check(f"{name}_UninstallLeavesNoCampaignFiles",
                   any(key.startswith(CAMPAIGN_DATA + "/") for key in before)
                   or not folder.exists() or not any(folder.iterdir()))
        self.check(f"{name}_ConfiguratorRefusesUninstalledRuntime", not accepts(game.root))
        result = game.install()
        self.check(f"{name}_ReinstallIsIdentical", installed(result) and game.data() == after, result)
        result = game.install()
        self.check(f"{name}_ReinstallOverInstallIsIdentical",
                   installed(result) and game.data() == after and game.entries(COMPONENT) == 1, result)
        result = game.run("--force-uninstall-list", str(COMPONENT), "--force-install-list", str(COMPONENT))
        self.check(f"{name}_UninstallAndInstallInOneRunIsIdentical",
                   installed(result) and game.data() == after and game.entries(COMPONENT) == 1, result)
        return game

    def refusal(self, name: str, message: str, kind: Callable[[ProcessResult, str], bool],
                **options) -> None:
        game = self.game(**options)
        before = game.data()
        result = game.install()
        self.check(f"{name}_Refused", kind(result, message), result)
        self.check(f"{name}_LeavesGameUnchanged", game.data() == before and not game.active(COMPONENT))

    def problems(self, name: str, problems: Sequence[str]) -> None:
        self.report.check(name, not problems, "; ".join(problems) if DEBUG else f"{len(problems)} differences")

    def quest_changes(self, compiled: Mapping[str, bytes]) -> None:
        """Every change made, to original files in the archive and modded ones in override."""
        mod = self.mod

        def changes(name: str, game: Game, before: dict, after: dict) -> tuple[dict[str, bytes], int]:
            self.check(f"{name}_RecordsEveryChange",
                       recorded(game, {change: "applied" for change in CHANGE_FILES}, journal=1))
            for script in ("blpool", "troalt", "ppbook"):
                self.check(f"{name}_Splits{script.upper()}",
                           after.get(f"override/{script}.bcs") == compiled[f"x{script}.bcs"])
            tlk = after["lang/en_us/dialog.tlk"]
            texts = fixtures.tra_strings((mod / "languages/english/in-game.tra").read_text(encoding="utf-8"))
            ee_texts = fixtures.tra_strings((mod / "languages/bgee/english/in-game.tra").read_text(encoding="utf-8"))
            say, journal = text_ref(tlk, texts[112]), text_ref(tlk, texts[113])
            unshey = fixtures.expected_unshey(
                fixtures.read_dialogue(compiled["unshey.dlg"]), item="BELT04", replacement="flrbelt1",
                old_journal=fixtures.UNSHEY_JOURNAL, journal=journal, say=say)
            self.problems(f"{name}_ForksUnsheysDialogue", fixtures.dialogue_problems(
                unshey, fixtures.read_dialogue(after.get("override/unshey.dlg", b"DLG V1.0" + bytes(48)))))
            belt = bytearray((mod / "copy" / "flrbelt1.itm").read_bytes())
            name_ref = text_ref(tlk, texts[114])
            struct.pack_into("<II", belt, 0x8, name_ref, name_ref)
            struct.pack_into("<I", belt, 0x50, text_ref(tlk, texts[115], ee_texts.get(115)))
            files = {"override/flrbelt1.itm": bytes(belt),
                     "override/bgee.lua": fixtures.quest_table(fixtures.UNSHEY_JOURNAL, journal),
                     **{f"override/{script}.bcs": compiled[f"x{script}.bcs"]
                        for script in ("blpool", "troalt", "ppbook")}}
            self.check(f"{name}_AddsUnsheysGirdle", after.get("override/flrbelt1.itm") == files["override/flrbelt1.itm"])
            self.check(f"{name}_FilesNewJournalEntryUnderItsQuest",
                       after.get("override/bgee.lua") == files["override/bgee.lua"])
            original = fixtures.read_dialogue(compiled["kpglai01.dlg"])
            patched = fixtures.read_dialogue(after.get("override/kpglai01.dlg", b"DLG V1.0" + bytes(48)))
            problems = fixtures.glaicus_problems(original, patched)
            heads = fixtures.tra_strings((mod / "languages/english/kpglai01.tra").read_text(encoding="utf-8"))
            says = [state["say"] for state in patched[len(original):]]
            if says != [text_ref(tlk, heads[number]) for number in (0, 1, 2, 3, 4, 5, 6, 3, 7, 8)]:
                problems.append(f"the new states say {says}")
            self.problems(f"{name}_LeadsGlaicusToFlailHeads", problems)
            # Both dialogues are checked by structure above.
            files.update({key: after[key] for key in ("override/unshey.dlg", "override/kpglai01.dlg")
                          if key in after})
            return files, 13

        self.lifecycle(
            "QuestChanges", changes=changes, tlk=fixtures.text_tlk(),
            biff=[("BLPOOL", "BCS", compiled["blpool.bcs"]), ("UNSHEY", "DLG", compiled["unshey.dlg"]),
                  ("BGEE", "LUA", fixtures.quest_table(fixtures.UNSHEY_JOURNAL))],
            override={**fixtures.ids_files(), "TROALT.BCS": compiled["troalt.bcs"],
                      "PPBOOK.BCS": compiled["ppbook.bcs"], "KPGLAI01.DLG": compiled["kpglai01.dlg"]})

    def left_out(self, name: str, compiled: Mapping[str, bytes], statuses: Mapping[str, str],
                 *, archived: bool) -> None:
        """Files the installer cannot change unambiguously stay as installed, with a note."""
        resources = [(Path(key).stem.upper(), Path(key).suffix[1:].upper(), data)
                     for key, data in compiled.items()]
        quests = {f"{stem}.{extension}": data for stem, extension, data in resources}
        game = self.game(tlk=fixtures.text_tlk(), biff=resources if archived else (),
                         override={**fixtures.ids_files(), **({} if archived else quests)})
        before = game.data()
        result = game.install()
        self.check(f"{name}_Installs", installed(result) and game.active(COMPONENT), result)
        after = game.data()
        self.check(f"{name}_RecordsWhatWasLeftOut", recorded(game, statuses))
        self.check(f"{name}_TellsThePlayer", noted(result, statuses), result)
        self.check(f"{name}_LeavesQuestFilesAsInstalled",
                   after == expected_install(game, before, after, MENU, DEFAULT_GAM))
        result = game.uninstall()
        added_text = {key: after[key] for key in after
                      if key.endswith("dialog.tlk") and after[key] != before.get(key)}
        self.check(f"{name}_UninstallRestoresEveryByteButAddedGameText",
                   uninstalled(result) and game.data() == {**before, **added_text}, result)

    def tweak_game(self, compiled: Mapping[str, bytes], *, eet: bool = True, script: str = "ar3001.bcs",
                   extra: Sequence[tuple[str, str, bytes]] = (), **options) -> Game:
        """A game with the files the tweaks change, in the archive and in override."""
        broken = bytearray(tweaks.store(["SCRL09"]))
        struct.pack_into("<I", broken, 0x38, 99)
        return self.game(eet=eet, tlk=fixtures.text_tlk(), biff=[
            ("BOTSMITH", "DLG", compiled["botsmith.dlg"]), ("BOTSMITH", "BCS", compiled["botsmith.bcs"]),
            ("AR3001", "BCS", compiled[script]), ("SELL1", "STO", tweaks.store(*SCROLL_STORES["SELL1"])),
            ("KEEP", "STO", tweaks.store(["POTN08"])), *extra], override={
            **fixtures.ids_files(), "UDDUER01.DLG": compiled["udduer01.dlg"],
            "SELL2.STO": tweaks.store(*SCROLL_STORES["SELL2"]), "BROKEN.STO": bytes(broken)}, **options)

    def tweak_changes(self, name: str, compiled: Mapping[str, bytes], before: Mapping[str, bytes],
                      after: Mapping[str, bytes]) -> dict[str, bytes]:
        """Checks what components 9000, 10100, 10210 and 10300 changed; returns those files."""
        changed = {key: value for key, value in after.items() if before.get(key) != value}
        tlk = after["lang/en_us/dialog.tlk"]
        texts = {stem: fixtures.tra_strings((self.mod / "languages/english" / f"{stem}.tra").read_text(encoding="utf-8"))
                 for stem in ("botsmith", "udduer01")}
        self.problems(f"{name}_CespenarForgesSoaItems", tweaks.botsmith_problems(
            compiled["botsmith.dlg"], after.get("override/botsmith.dlg", compiled["botsmith.dlg"]),
            lambda number: text_ref(tlk, texts["botsmith"][number])))
        self.check(f"{name}_CespenarHandsOverForgedItems", after.get("override/botsmith.bcs") == compiled["xbotsmith.bcs"])
        self.problems(f"{name}_DuergarOffersBothStoresToGuests", tweaks.udduer01_problems(
            compiled["udduer01.dlg"], after["override/udduer01.dlg"],
            lambda number: text_ref(tlk, texts["udduer01"][number])))
        self.check(f"{name}_KeepsWatchersKeepStatues", after.get("override/ar3001.bcs") == compiled["xar3001.bcs"])
        stores = {f"override/{store.casefold()}.sto": tweaks.store(tweaks.without_scrolls(items), order)
                  for store, (items, order) in SCROLL_STORES.items()}
        self.check(f"{name}_RemovesOnlyTheScrolls", all(after.get(key) == data for key, data in stores.items()))
        added = [text for key in changed if key.endswith("dialog.tlk")
                 for text in tlk_strings(after[key])[len(tlk_strings(before[key])):]]
        self.check(f"{name}_AddsTheirTexts", sorted(added) == sorted(
            text for strings in texts.values() for text in strings.values()))
        expected = {"override/botsmith.dlg", "override/botsmith.bcs", "override/udduer01.dlg",
                    "override/ar3001.bcs", *stores}
        self.check(f"{name}_WritesOnlyExpectedFiles",
                   {key for key in changed if not key.endswith("dialog.tlk")} == expected)
        return changed

    def tweak_components(self, compiled: Mapping[str, bytes], message: Mapping[int, str]) -> None:
        """Item Randomiser's optional tweaks: installed together, checked, uninstalled exactly."""
        game = self.tweak_game(compiled)
        before = game.data()
        result = game.run("--force-install-list", *TWEAKS)
        self.check("Tweaks_Install", installed(result) and all(game.active(int(number)) for number in TWEAKS), result)
        after = game.data()
        changed = self.tweak_changes("Tweaks", compiled, before, after)
        self.check("Tweaks_TellWhatTheyChanged",
                   message[110202].replace("%flir_tw_removed%", "2").replace("%flir_tw_selling%", "2") in result.output
                   and message[110203].replace("%flir_tw_unreadable%", " BROKEN.STO").casefold()
                   in result.output.casefold()
                   and message[110303].replace("%flir_tw_other%", "1") in result.output
                   and message[109001] not in result.output and message[109002] not in result.output, result)
        result = game.run("--force-uninstall-list", *TWEAKS)
        added_text = {key: value for key, value in changed.items() if key.endswith("dialog.tlk")}
        self.check("Tweaks_UninstallRestoresEveryByteButAddedGameText",
                   uninstalled(result) and game.data() == {**before, **added_text}, result)
        result = game.run("--force-install-list", *TWEAKS)
        self.check("Tweaks_ReinstallIsIdentical", installed(result) and game.data() == after, result)

        # Baldur's Gate II: Enhanced Edition without EET.
        game = self.tweak_game(compiled, eet=False)
        before = game.data()
        result = game.run("--force-install-list", *TWEAKS)
        self.check("TweaksOnBg2ee_Install", installed(result), result)
        self.check("TweaksOnBg2ee_ChangeTheSame",
                   {key: value for key, value in game.data().items() if before.get(key) != value} == changed)

        # Cespenar already makes armour of the Shadow Dragon's scales, as on the
        # Enhanced Editions: that scale is left to his recipe.
        shadow = {**compiled, "botsmith.dlg": compiled["sbotsmith.dlg"]}
        game = self.tweak_game(shadow)
        result = game.run("--force-install-list", "9000")
        self.check("ShadowDragonRecipeExists_InstallsWithNote", installed(result)
                   and message[109002] in result.output and message[109001] not in result.output, result)
        after = game.data()
        tlk = after["lang/en_us/dialog.tlk"]
        texts = fixtures.tra_strings((self.mod / "languages/english/botsmith.tra").read_text(encoding="utf-8"))
        self.problems("ShadowDragonRecipeExists_LeavesThatScaleToIt", tweaks.botsmith_problems(
            shadow["botsmith.dlg"], after["override/botsmith.dlg"], lambda number: text_ref(tlk, texts[number]),
            taken=("scaleb",)))

        # Nine stores in ten, rolled per store.
        sellers = {f"STORE{number:02d}": ["POTN08", "SCRL09"] for number in range(30)}
        game = self.game(override={f"{store}.STO": tweaks.store(items) for store, items in sellers.items()})
        before = game.data()
        result = game.run("--force-install-list", "10200")
        after = game.data()
        keys = {store: f"override/{store.casefold()}.sto" for store in sellers}
        emptied = [store for store, key in keys.items() if after[key] != before[key]]
        self.check("UndeadScrollsFromMostStores_Install", installed(result) and game.active(10200), result)
        self.check("UndeadScrollsFromMostStores_RemovesOnlyTheScrolls", bool(emptied) and all(
            after[keys[store]] == tweaks.store(["POTN08"]) for store in emptied))
        self.check("UndeadScrollsFromMostStores_TellsHowMany",
                   message[110202].replace("%flir_tw_removed%", str(len(emptied)))
                   .replace("%flir_tw_selling%", str(len(sellers))) in result.output, result)

        # Watcher's Keep's script as SCS leaves it, or without the adjustment.
        for name, script, note in (("StatuesAlreadyKept", "sar3001.bcs", 110302),
                                   ("StatuesNotRemoved", "nar3001.bcs", 110301)):
            game = self.tweak_game(compiled, script=script)
            before = game.data()
            result = game.run("--force-install-list", "10300")
            self.check(f"{name}_InstallsWithNote", installed(result) and message[note] in result.output, result)
            self.check(f"{name}_LeavesScriptAsItIs", game.data() == before)

        # Item Randomiser's own copies would duplicate the dialogue changes.
        log = (b"// Log of Currently Installed WeiDU Mods\r\n"
               b"~SETUP-RANDOMISER.TP2~ #0 #9000 // Cespenar can forge SoA items: 8.1.1\r\n"
               b"~RANDOMISER/RANDOMISER.TP2~ #0 #10210 // Duergar merchants: 8.1.1\r\n")
        game = self.tweak_game(compiled, files={"WeiDU.log": log})
        before = game.data()
        result = game.run("--force-install-list", "9000", "10210")
        self.check("ItemRandomiserTweaksInstalled_Refused",
                   skipped(result, message[100012]) and result.output.count(message[100012]) == 2, result)
        self.check("ItemRandomiserTweaksInstalled_LeavesGameUnchanged",
                   game.data() == before and not game.active(9000) and not game.active(10210))

        # Polish dialogue texts on an Enhanced Edition game (it has BGEE.LUA),
        # converted to UTF-8.
        game = self.tweak_game(compiled, extra=[("BGEE", "LUA", b"-- synthetic\n"),
                                                ("BOLT01", "ITM", b"ITM V1  synthetic bolt")])
        result = game.run("--force-install-list", "9000", "10210", language=1)
        polish = fixtures.tra_strings((self.mod / "languages/polish/botsmith.tra").read_bytes().decode("cp1250"))
        self.check("TweaksInPolish_Install", installed(result), result)
        self.check("TweaksInPolish_UseThePolishTexts",
                   text_ref((game.root / "lang/en_us/dialog.tlk").read_bytes(), polish[0]) >= 0)


def text_ref(tlk: bytes, *texts: str | None) -> int:
    """The one game-text entry that reads one of <texts>; line breaks may differ."""
    wanted = {text.replace("\r\n", "\n") for text in texts if text is not None}
    found = [index for index, value in enumerate(tlk_strings(tlk)) if value.replace("\r\n", "\n") in wanted]
    return found[0] if len(found) == 1 else -1


# Compiles the fixture sources in a scratch game.
FIXTURE_TP2 = (b"BACKUP ~fixc/backup~\r\nAUTHOR ~synthetic~\r\n"
               b"BEGIN ~Synthetic quest files~\r\nCOMPILE ~fixc/src~\r\n")


def compile_fixtures(scratch: Path, weidu: Path,
                     groups: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, bytes]]:
    """Each group of fixture sources, compiled by WeiDU in a scratch game of its own."""
    compiled = {}
    for group, sources in groups.items():
        root = scratch / f"compile-{group}"
        resources = [("OH6000", "ARE", b"synthetic marker")]
        bif = build_biff(resources)
        write_bytes(root / "data" / "runtime.bif", bif)
        write_bytes(root / "chitin.key", build_key(resources, len(bif)))
        write_bytes(root / "dialog.tlk", fixtures.text_tlk())
        write_bytes(root / "lang" / "en_us" / "dialog.tlk", fixtures.text_tlk())
        for name, data in fixtures.ids_files().items():
            write_bytes(root / "override" / name, data)
        for name, text in sources.items():
            write_bytes(root / "fixc" / "src" / name, text.encode("ascii"))
        write_bytes(root / "fixc" / "fixc.tp2", FIXTURE_TP2)
        result = Game(root, weidu).run("--force-install-list", "0", tp2="fixc/fixc.tp2")
        if not installed(result):
            raise MatrixFailure(f"FAIL CompileFixtures_{group} ({describe(result)})")
        output = {path.name.casefold(): path.read_bytes() for path in (root / "override").iterdir()}
        compiled[group] = {}
        for name in sources:
            stem, extension = name.rsplit(".", 1)
            key = stem + (".bcs" if extension == "baf" else ".dlg")
            compiled[group][key] = output[key]
    return compiled


def accepts(game: Path) -> bool:
    try:
        validate_runtime(game, {"game_root": str(game)})
    except ProfileError:
        return False
    return True


def tra_messages(mod: Path) -> dict[int, str]:
    text = (mod / "languages" / "english" / "setup.tra").read_text(encoding="utf-8")
    messages = {}
    for line in text.splitlines():
        if line.startswith("@") and "=~" in line:
            key, value = line.split("=~", 1)
            messages[int(key[1:])] = value.rstrip("~")
    return messages


def run_suite(args: argparse.Namespace) -> int:
    weidu = require_existing_file(args.weidu, "WeiDU executable")
    temp_root = require_scratch_parent(args.temp_root)
    scratch = Path(tempfile.mkdtemp(prefix="flrt-", dir=temp_root))
    reporter = Reporter()
    mod = scratch / "mod"
    if args.package:
        stage_package(require_existing_file(args.package, "release package"), mod)
    else:
        stage_worktree(Path(args.repository_root).resolve(strict=True), mod)
    message = tra_messages(mod)
    suite = Suite(scratch, mod, weidu, reporter)

    suite.lifecycle("ArchivedTemplates", MENU, DEFAULT_GAM)

    # The installer's quest and dialogue changes, on synthetic files.
    compiled = compile_fixtures(scratch, weidu, {**fixtures.SOURCES, **tweaks.sources(mod)})
    suite.quest_changes(compiled["applied"])
    suite.left_out("QuestFilesLeftOut", compiled["left-out"], {
        "quest_pool": "grant-ambiguous", "quest_altar": "grant-missing", "quest_book": "prepared-elsewhere",
        "unshey": "item-missing", "flail_heads": "dialogue-unrecognised", "totsc_tome": "applied"},
        archived=False)
    suite.left_out("QuestFilesLeftOutInArchive",
                   {**compiled["left-out-2"], "bgee.lua": fixtures.quest_table(fixtures.UNSHEY_JOURNAL)}, {
        **NOTHING_TO_PREPARE, "unshey": "journal-unrecognised", "flail_heads": "prepared-elsewhere"},
        archived=True)

    suite.tweak_components({**compiled["tweaks"], "sbotsmith.dlg": compiled["tweaks-shadow"]["botsmith.dlg"]},
                           message)

    override_gam = gam([("SPRITE_IS_DEADX", 1), ("CHAPTER", 2)], b"\xff" * 33)
    applied = b'return "' + b"a" * 64 + b'"\n'
    game = suite.lifecycle(
        "OverrideUiModWithAppliedSettings", UI_MOD_MENU, override_gam,
        menu_in_override=True, gam_in_override=True,
        ini=b"[General]\nLuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL",
        files={CAMPAIGN_DATA + "/next.lua": applied})
    suite.check("OverrideUiModWithAppliedSettings_KeepsMultiplayerCall",
                b"connectionScreen:OnNewGameButtonClick()" in (game.root / "override/UI.MENU").read_bytes())

    marked = gam([("CHAPTER", 0), ("FLPR_FRESH", 1)])
    suite.lifecycle("TemplateAlreadyMarked", MENU, marked, gam_written=False)
    suite.lifecycle("TemplateWithoutVariables", MENU, gam([]))

    # Reinstalling an earlier mod makes WeiDU uninstall every later component
    # temporarily and install it again behind that mod.
    earlier = "earlier/earlier.tp2"
    game = suite.game(files={earlier: EARLIER_TP2})
    suite.check("Repush_EarlierModInstalls", installed(game.run("--force-install-list", "10", tp2=earlier)))
    suite.check("Repush_RuntimeInstalls", installed(game.install()))
    after = game.data()
    result = game.run("--force-uninstall-list", "10", "--force-install-list", "10", tp2=earlier)
    order = [line.casefold().split("~")[1] for line in active_weidu_log(game.root)]
    suite.check("Repush_RuntimeReinstalledBehindEarlierMod",
                installed(result) and order == [earlier, TP2]
                and game.entries(COMPONENT) == 1, result)
    suite.check("Repush_GameIdentical", game.data() == after)
    # WeiDU 249 also re-installs an installed earlier component from the install list alone.
    result = game.run("--force-install-list", "10", tp2=earlier)
    order = [line.casefold().split("~")[1] for line in active_weidu_log(game.root)]
    suite.check("Repush_InstallListAloneKeepsRuntimeBehindEarlierMod",
                installed(result) and order == [earlier, TP2]
                and game.entries(COMPONENT) == 1 and game.data() == after, result)

    # Polish loads the English setup strings first; the new strings have no Polish text.
    game = suite.game()
    result = game.run("--force-install-list", str(COMPONENT), language=1)
    suite.check("PolishLanguage_Installs", installed(result) and game.active(COMPONENT), result)
    game = suite.game(override={"M___EEex.lua": None})
    result = game.run("--force-install-list", str(COMPONENT), language=1)
    suite.check("PolishLanguage_SkipsWithEnglishText", skipped(result, message[101502]), result)
    # On an Enhanced Edition game (it has BGEE.LUA) WeiDU converts the Polish texts
    # to UTF-8 in the mod folder and marks the folder with a copy of BOLT01.ITM;
    # uninstalling restores them. The English texts are UTF-8 already and stay as written.
    game = suite.game(biff=[("BGEE", "LUA", b"-- synthetic\n"), ("BOLT01", "ITM", b"ITM V1  synthetic bolt")])
    languages = game.root / MOD_FOLDER / "languages"
    original = snapshot_tree(languages)
    result = game.run("--force-install-list", str(COMPONENT), language=1)
    suite.check("PolishOnEnhancedEdition_Installs", installed(result) and game.active(COMPONENT), result)
    converted = snapshot_tree(languages)
    polish = [key for key in original if key.startswith("polish/") and key.endswith(".tra")]
    suite.check("PolishOnEnhancedEdition_ConvertsOnlyPolish",
                polish and all(utf8_translation(original[key], converted[key]) for key in polish)
                and all(converted.get(key) == data for key, data in original.items() if key not in polish)
                and "polish/fl#utf8.mrk" in converted and "english/fl#utf8.mrk" not in converted)
    result = game.uninstall()
    suite.check("PolishOnEnhancedEdition_UninstallRestoresTexts",
                uninstalled(result) and snapshot_tree(languages) == original, result)

    log = b"// Log of Currently Installed WeiDU Mods\r\n~RANDOMISER/RANDOMISER.TP2~ #0 #560 // More Spell Shield scrolls\r\n"
    suite.refusal("SpellShieldInstalled", message[101504], skipped, files={"WeiDU.log": log})
    suite.refusal("LegacyMode1Installed", message[101503], skipped, override={"flrandomiser1.mrk": b"m"})
    suite.refusal("LegacyMode2Installed", message[101503], skipped, override={"flrandomiser2.mrk": b"m"})
    suite.refusal("StagedRuntimePresent", message[101505], skipped, override={"M_FLPR.lua": b"-- lab\n"})
    # Only WeiDU's record of this component makes an existing runtime its own.
    suite.refusal("StagedRuntimeWithStrayMarker", message[101505], skipped,
                  override={"M_FLPR.lua": b"-- lab\n", "flrandomiser3.mrk": MARKER})
    suite.refusal("NotEet", message[101501], skipped, eet=False)
    suite.refusal("NoEEex", message[101502], skipped, override={"M___EEex.lua": None})

    for name, ini in (
        ("LuaJitMissingIni", None),
        ("LuaJitInternal", b"[General]\r\nLuaPatchMode=INTERNAL\r\n"),
        ("LuaJitOnlyInComment", b"[General]\r\n; LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL\r\n"),
        ("LuaJitTrailingText", b"[General]\r\nLuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL2\r\n"),
        ("LuaJitTwoModes", LUAJIT_INI + b"LuaPatchMode=INTERNAL\r\n"),
    ):
        suite.refusal(name, message[101510], refused, ini=ini)

    for name, menu in (
        ("MenuMissing", None),
        ("MenuWithoutNewGame", MENU.replace(b"startEngine:OnNewGameButtonClick();", b"")),
        ("MenuWithoutAccept", MENU.replace(b"createCharScreen:AcceptCharacter(", b"FinishChargen(")),
        ("MenuNewGameByReference", MENU + b"local f = startEngine.OnNewGameButtonClick\r\n"),
        ("MenuAcceptBySpacedCall", MENU + b"createCharScreen:AcceptCharacter (-1)\r\n"),
        ("MenuAlreadyHooked", patch_menu(MENU)),
        ("MenuPartlyHooked", MENU + b"FLPRUI_NewGame()\r\n"),
    ):
        suite.refusal(name, message[101512], refused, menu=menu)

    # Renaming a UI mod's own definition would replace the adapter's global function.
    for name, menu in (
        ("MenuDefinesNewGame", MENU + b"`\r\nfunction startEngine:OnNewGameButtonClick()\r\nend\r\n`\r\n"),
        ("MenuDefinesAcceptAfterTab",
         MENU + b"`\r\nfunction\tcreateCharScreen:AcceptCharacter(difficulty)\r\nend\r\n`\r\n"),
        ("MenuDefinesAcceptOnNextLine",
         UI_MOD_MENU + b"`\nfunction\n\tcreateCharScreen:AcceptCharacter(difficulty)\nend\n`\n"),
    ):
        suite.refusal(name, message[101515], refused, menu=menu)

    out_of_bounds = bytearray(gam([("CHAPTER", 0)]))
    struct.pack_into("<I", out_of_bounds, 0x3C, 2)
    for name, baldur, text in (
        ("TemplateMissing", None, message[101513]),
        ("TemplateWrongVersion", b"GAMEV1.1" + DEFAULT_GAM[8:], message[101513]),
        ("TemplateTruncated", DEFAULT_GAM[:0xB0], message[101513]),
        ("TemplateTableOutOfBounds", bytes(out_of_bounds), message[101513]),
        ("TemplateMarkedZero", gam([("FLPR_FRESH", 0)]), message[101514]),
        ("TemplateMarkedTwice", gam([("FLPR_FRESH", 1), ("flpr_fresh", 1)]), message[101514]),
    ):
        suite.refusal(name, text, refused, baldur=baldur)

    reporter.summary()
    shutil.rmtree(scratch)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weidu", required=True)
    parser.add_argument("--temp-root", required=True)
    parser.add_argument("--repository-root", default=str(REPOSITORY))
    parser.add_argument("--package", help="install from this release ZIP instead of the working tree")
    args = parser.parse_args(argv)
    try:
        return run_suite(args)
    except MatrixFailure as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
