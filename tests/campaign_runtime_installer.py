#!/usr/bin/env python3
"""Synthetic EET installs of randomiser.tp2 component 1500 (campaign runtime).

Builds minimal KEY/BIFF/TLK games below a caller-supplied temporary root,
stages the mod exactly as the release package selects it (from the working tree
or from a built release ZIP) and runs the real TP2 with WeiDU. It never reads
or writes a real game. Every case compares the whole synthetic game, outside
WeiDU's own bookkeeping, with the expected bytes.
"""

from __future__ import annotations

import argparse
import os
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
from build_mode1_fixture import mark_initial_gam  # noqa: E402
from flir_config.activation import validate_runtime  # noqa: E402
from flir_config.profiles import ProfileError  # noqa: E402

RESOURCE_TYPES = {"ARE": 1010, "GAM": 1013, "MENU": 1032}
RUNTIME_MODULES = (
    "M_FLPRUI.lua", "M_FLPR.lua", "FLPRCore.lua", "FLPRCur.lua", "FLPRGat.lua",
    "FLPRGlob.lua", "FLPRHost.lua", "FLPRNat.lua", "FLPRQA.lua", "FLPRScript.lua",
    "FLPRShop.lua", "FLPRSpell.lua", "FLPRRun.menu",
)
FIXTURE_MODULES = ("FLPRGateAsm.lua", "FLPRCampaignPolicy.lua", "FLPRInventoryAsm.lua")
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

    def run(self, *arguments: str, tp2: str = "randomiser/randomiser.tp2") -> ProcessResult:
        return run_process(
            [str(self.weidu), tp2, "--game", ".", *arguments,
             "--language", "0", "--use-lang", "en_US", "--no-exit-pause", "--quick-log"],
            self.root, "WeiDU " + tp2)

    def install(self, component: int = 1500) -> ProcessResult:
        return self.run("--force-install-list", str(component))

    def uninstall(self, component: int = 1500) -> ProcessResult:
        return self.run("--force-uninstall-list", str(component))

    def data(self) -> dict[str, bytes]:
        """The game as the engine and the configurator see it, without WeiDU state."""
        return {key: value for key, value in snapshot_tree(self.root).items()
                if not key.startswith(("randomiser/", "earlier/", "weidu_external/"))
                and key not in ("weidu.log", "weidu.conf")}

    def active(self, component: int) -> bool:
        return self.entries(component) > 0

    def entries(self, component: int) -> int:
        suffix = f" #{component} "
        return sum(line.casefold().startswith("~randomiser/randomiser.tp2~")
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
               eet: bool = True) -> Game:
    """Synthetic EET game with EEex, LuaJIT and unrelated mod data.

    A menu, template, INI or override file given as None is absent.
    """
    resources = [("OH6000", "ARE", b"synthetic EET marker")]
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
    write_bytes(root / "dialog.tlk", build_tlk())
    write_bytes(root / "lang" / "en_us" / "dialog.tlk", build_tlk())
    (root / "override").mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        if data is not None:
            write_bytes(root / "override" / name, data)
    if ini is not None:
        files["InfinityLoader.ini"] = ini
    for name, data in files.items():
        write_bytes(root / name, data)
    shutil.copytree(mod, root / "randomiser")
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
            if name.is_absolute() or ".." in name.parts or name.parts[0] != "randomiser":
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


def uninstalled(result: ProcessResult) -> bool:
    upper = result.output.upper()
    return (result.returncode == 0 and "ERROR" not in upper and "NOT UNINSTALLED" not in upper
            and "SKIPPING" not in upper)


def expected_install(game: Game, before: Mapping[str, bytes], menu: bytes, baldur: bytes,
                     *, gam_written: bool = True) -> dict[str, bytes]:
    mod = game.root / "randomiser"
    expected = dict(before)
    for name in RUNTIME_MODULES:
        expected["override/" + name.casefold()] = (mod / "prototype" / name).read_bytes()
    for name in FIXTURE_MODULES:
        expected["flpr-fixture/" + name.casefold()] = (mod / "tools" / "probes" / name).read_bytes()
    expected["override/ui.menu"] = patch_menu(menu)
    if gam_written:
        expected["override/baldur.gam"] = mark_initial_gam(baldur)
    expected["override/flrandomiser3.mrk"] = MARKER
    return expected


class Suite:
    def __init__(self, scratch: Path, mod: Path, weidu: Path, reporter: Reporter) -> None:
        self.scratch, self.mod, self.weidu, self.report = scratch, mod, weidu, reporter
        self.count = 0

    def game(self, **options) -> Game:
        self.count += 1
        return build_game(self.scratch / f"g{self.count:02d}", self.mod, self.weidu, **options)

    def check(self, name: str, condition: bool, result: ProcessResult | None = None) -> None:
        self.report.check(name, condition, describe(result) if result is not None and not condition else "")

    def lifecycle(self, name: str, menu: bytes, baldur: bytes, gam_written: bool = True, **options) -> Game:
        """Install, check every byte, uninstall exactly, then reinstall identically.

        Re-installing over the installed component covers a player's Re-install,
        WeiDU's one-run uninstall and install, and a recovery that installs the
        same list again.
        """
        game = self.game(menu=menu, baldur=baldur, **options)
        before = game.data()
        result = game.install()
        self.check(f"{name}_Installs", installed(result) and game.active(1500), result)
        after = game.data()
        expected = expected_install(game, before, menu, baldur, gam_written=gam_written)
        self.check(f"{name}_WritesOnlyRuntimeHooksAndMarker", after == expected)
        self.check(f"{name}_ConfiguratorAcceptsInstall", accepts(game.root))
        result = game.uninstall()
        self.check(f"{name}_Uninstalls", uninstalled(result) and not game.active(1500), result)
        self.check(f"{name}_UninstallRestoresEveryByte", game.data() == before)
        # WeiDU does not track folders: one it created stays behind, empty.
        folder = game.root / "flpr-fixture"
        self.check(f"{name}_UninstallLeavesNoFixtureFiles",
                   any(key.startswith("flpr-fixture/") for key in before)
                   or not folder.exists() or not any(folder.iterdir()))
        self.check(f"{name}_ConfiguratorRefusesUninstalledRuntime", not accepts(game.root))
        result = game.install()
        self.check(f"{name}_ReinstallIsIdentical", installed(result) and game.data() == after, result)
        result = game.install()
        self.check(f"{name}_ReinstallOverInstallIsIdentical",
                   installed(result) and game.data() == after and game.entries(1500) == 1, result)
        result = game.run("--force-uninstall-list", "1500", "--force-install-list", "1500")
        self.check(f"{name}_UninstallAndInstallInOneRunIsIdentical",
                   installed(result) and game.data() == after and game.entries(1500) == 1, result)
        return game

    def refusal(self, name: str, message: str, kind: Callable[[ProcessResult, str], bool],
                **options) -> None:
        game = self.game(**options)
        before = game.data()
        result = game.install()
        self.check(f"{name}_Refused", kind(result, message), result)
        self.check(f"{name}_LeavesGameUnchanged", game.data() == before and not game.active(1500))


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
        if line.startswith("@1015") and "=~" in line:
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

    override_gam = gam([("SPRITE_IS_DEADX", 1), ("CHAPTER", 2)], b"\xff" * 33)
    applied = b'return "' + b"a" * 64 + b'"\n'
    game = suite.lifecycle(
        "OverrideUiModWithAppliedSettings", UI_MOD_MENU, override_gam,
        menu_in_override=True, gam_in_override=True,
        ini=b"[General]\nLuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL",
        files={"flpr-fixture/next.lua": applied})
    suite.check("OverrideUiModWithAppliedSettings_KeepsMultiplayerCall",
                b"connectionScreen:OnNewGameButtonClick()" in (game.root / "override/UI.MENU").read_bytes())

    marked = gam([("CHAPTER", 0), ("FLPR_FRESH", 1)])
    suite.lifecycle("TemplateAlreadyMarked", MENU, marked, gam_written=False)
    suite.lifecycle("TemplateWithoutVariables", MENU, gam([]))

    # Exclusions in both directions, checked against an installed runtime.
    game = suite.game()
    suite.check("Exclusions_RuntimeInstalls", installed(game.install()))
    before = game.data()
    for component, text in ((560, message[101506]), (1100, message[101507]), (1200, message[101507]),
                            (1300, message[101507]), (1400, message[101507])):
        result = game.install(component)
        suite.check(f"Exclusions_Component{component}Skipped", skipped(result, text), result)
    suite.check("Exclusions_LeaveGameUnchanged", game.data() == before and game.active(1500))

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
                installed(result) and order == [earlier, "randomiser/randomiser.tp2"]
                and game.entries(1500) == 1, result)
    suite.check("Repush_GameIdentical", game.data() == after)

    log = b"// Log of Currently Installed WeiDU Mods\r\n~RANDOMISER/RANDOMISER.TP2~ #0 #560 // More Spell Shield scrolls\r\n"
    suite.refusal("SpellShieldInstalled", message[101504], skipped, files={"WeiDU.log": log})
    suite.refusal("LegacyMode1Installed", message[101503], skipped, override={"flrandomiser1.mrk": b"m"})
    suite.refusal("LegacyMode2Installed", message[101503], skipped, override={"flrandomiser2.mrk": b"m"})
    suite.refusal("StagedRuntimePresent", message[101505], skipped, override={"M_FLPR.lua": b"-- lab\n"})
    # Only WeiDU's record of component 1500 makes an existing runtime its own.
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
