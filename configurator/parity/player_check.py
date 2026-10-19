"""The executable started without arguments, as a double-click starts it.

Copies the executable into synthetic games (built with the reference tests'
builders in a temporary folder) and starts it there without arguments, with
LOCALAPPDATA in the temporary folder and CIR_NO_BROWSER set. Every start
runs on a separate Windows desktop that is never shown, so no window
appears on the screen; this script answers the folder window there.

- In the game's cir folder and in cir/tools, the executable finds its game.
  Its scans equal the reference `scan` (in the language weidu.conf names)
  and `world scan`, and its editor's state equals the reference editor's
  with --apply-game. Nothing is written into the game before Apply; Apply
  works, and the next start reopens the applied configuration from the
  same workspace.
- After the game's mods changed, the configurations of the earlier scan
  are kept aside and the editor opens with a new configuration; Apply
  works again.
- Outside a game, the folder window asks until a game with CIR is chosen:
  a folder without a game, a game without CIR, then a folder inside a
  game whose WeiDU.log records CIR.
- In a game without CIR, closing the folder window opens nothing.
- An incomplete installation is reported in plain words.

    python configurator/parity/player_check.py
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import common
from common import RUST_CLI, snapshot_tree
from cli_check import PYTHON, build
from editor_check import Server, bound, commands, masked

from test_postinstall_resources import make_tlk  # noqa: E402
from test_postinstall_world_formats import make_are  # noqa: E402

EXE = "cir-configurator.exe"
EDITOR = re.compile(r"Open (http://127\.0\.0\.1:(\d+)) in your browser to use the editor\.")
APPLIED = "Saved and applied."

user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

GENERIC_ALL = 0x10000000
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
STILL_ACTIVE = 259
WM_SETTEXT = 0x000C
WM_COMMAND = 0x0111
IDOK, IDCANCEL = 1, 2
# The common item dialog's name box: a plain edit (edt1) when picking folders,
# the file name combo box (cmb13) otherwise.
FOLDER_BOX, NAME_BOX = 1152, 1148


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def declare() -> None:
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                      wintypes.DWORD, ctypes.c_void_p]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, WNDENUMPROC, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class Desktop:
    """A desktop of its own for the executable's windows, never shown."""

    def __init__(self) -> None:
        self.name = f"cir-player-check-{os.getpid()}"
        self.handle = user32.CreateDesktopW(self.name, None, None, 0, GENERIC_ALL, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        user32.CloseDesktop(self.handle)


class Start:
    """The executable started without arguments on the desktop, with its
    output in files; its own (hidden) console, as a double-click gives it."""

    def __init__(self, desktop: Desktop, exe: Path, cwd: Path, environment: dict, logs: Path, label: str):
        self.label = label
        self.out, self.err = logs / f"{label}.out", logs / f"{label}.err"
        files = [open(os.devnull, "rb"), open(self.out, "wb"), open(self.err, "wb")]
        try:
            handles = [msvcrt.get_osfhandle(file.fileno()) for file in files]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            info = STARTUPINFOW()
            info.cb = ctypes.sizeof(info)
            info.lpDesktop = desktop.name
            info.dwFlags = STARTF_USESTDHANDLES
            info.hStdInput, info.hStdOutput, info.hStdError = handles
            block = "".join(f"{key}={value}\0" for key, value in
                            sorted(environment.items(), key=lambda pair: pair[0].upper())) + "\0"
            variables = (ctypes.c_wchar * len(block))(*block)
            command = ctypes.create_unicode_buffer(f'"{exe}"')
            process = PROCESS_INFORMATION()
            if not kernel32.CreateProcessW(None, command, None, None, True,
                                           CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, variables, str(cwd),
                                           ctypes.byref(info), ctypes.byref(process)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            for file in files:
                file.close()
        kernel32.CloseHandle(process.hThread)
        self.handle, self.pid = process.hProcess, process.dwProcessId

    def text(self, which: str = "out") -> str:
        return (self.out if which == "out" else self.err).read_bytes().decode("utf-8", "replace")

    def exit_code(self) -> int | None:
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code))
        return None if code.value == STILL_ACTIVE else code.value

    def wait(self, timeout: float = 120) -> int:
        kernel32.WaitForSingleObject(self.handle, int(timeout * 1000))
        code = self.exit_code()
        if code is None:
            raise RuntimeError(f"{self.label}: still running after {timeout} s:\n{self.text()}{self.text('err')}")
        return code

    def editor(self, workspace: Path, timeout: float = 300) -> "Editor":
        """The editor once its address is printed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = EDITOR.search(self.text())
            if found:
                return Editor(int(found.group(2)), workspace)
            if self.exit_code() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError(f"{self.label}: no editor:\n{self.text()}{self.text('err')}")

    def stop(self) -> None:
        if self.handle is None:
            return
        if self.exit_code() is None:
            kernel32.TerminateProcess(self.handle, 1)
            kernel32.WaitForSingleObject(self.handle, 10000)
        kernel32.CloseHandle(self.handle)
        self.handle = None


class Editor(Server):
    """The executable's editor (a Server without its own process)."""

    def __init__(self, port: int, workspace: Path):
        self.name, self.port, self.workspace = "rust", port, workspace


def windows(enumerate_, parent) -> list[int]:
    found = []
    callback = WNDENUMPROC(lambda window, _: found.append(window) or True)
    enumerate_(parent, callback, 0)
    return found


def class_name(window: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(window, buffer, 256)
    return buffer.value


def window_text(window: int) -> str:
    buffer = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(window, buffer, 1024)
    return buffer.value


class FolderWindow(threading.Thread):
    """Answers the executable's folder windows on its desktop, one answer
    per window: a folder to choose, or None to close the window. Notes each
    window's title, button labels and the folder it opened in."""

    def __init__(self, desktop: Desktop, start: Start, answers: list):
        super().__init__(daemon=True)
        self.desktop, self.start_, self.answers = desktop, start, answers
        self.answered = 0
        self.seen: list[dict] = []
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            # A new thread has no windows yet, so it may move to the desktop.
            if not user32.SetThreadDesktop(self.desktop.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            for answer in self.answers:
                window = self.window()
                box = self.box(window)
                children = windows(user32.EnumChildWindows, window)
                self.seen.append({
                    "title": window_text(window),
                    "buttons": {user32.GetDlgCtrlID(child): window_text(child) for child in children
                                if class_name(child) == "Button" and user32.IsWindowVisible(child)},
                    "address": self.address(window),
                })
                if answer is None:
                    user32.PostMessageW(window, WM_COMMAND, IDCANCEL, 0)
                else:
                    # As a player does: type the folder and press Enter, which
                    # opens it, then "Use this folder".
                    text = ctypes.create_unicode_buffer(str(answer))
                    user32.SendMessageW(box, WM_SETTEXT, 0, ctypes.addressof(text))
                    user32.PostMessageW(window, WM_COMMAND, IDOK, 0)
                    self.wait(lambda: not user32.IsWindow(window) or self.address(window) == f"Address: {answer}",
                              f"the folder window did not open {answer}")
                    if user32.IsWindow(window):
                        user32.PostMessageW(window, WM_COMMAND, IDOK, 0)
                self.wait(lambda: not user32.IsWindow(window), f"the folder window stayed open after {answer}")
                self.answered += 1
        except BaseException as exc:  # reported by the main thread
            self.error = exc

    @staticmethod
    def wait(condition, failure: str, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise RuntimeError(failure)
            time.sleep(0.05)

    @staticmethod
    def address(window: int) -> str | None:
        """The folder the window shows, as its address bar names it."""
        return next((text for text in map(window_text, windows(user32.EnumChildWindows, window))
                     if text.startswith("Address: ")), None)

    def window(self, timeout: float = 60) -> int:
        """The executable's visible dialog window on the desktop."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for window in windows(user32.EnumDesktopWindows, self.desktop.handle):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
                if (owner.value == self.start_.pid and class_name(window) == "#32770"
                        and user32.IsWindowVisible(window)):
                    return window
            if self.start_.exit_code() is not None:
                raise RuntimeError("the executable ended without a folder window")
            time.sleep(0.05)
        raise RuntimeError("no folder window appeared")

    def box(self, window: int, timeout: float = 30) -> int:
        """The edit field of the dialog's "Folder:" box."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for child in windows(user32.EnumChildWindows, window):
                if user32.GetDlgCtrlID(child) == FOLDER_BOX and class_name(child) == "Edit":
                    return child
                if user32.GetDlgCtrlID(child) == NAME_BOX:
                    edits = [inner for inner in windows(user32.EnumChildWindows, child) if class_name(inner) == "Edit"]
                    if edits:
                        return edits[0]
            time.sleep(0.05)
        raise RuntimeError("the folder window has no folder box")


class Checks:
    def __init__(self) -> None:
        self.passed = self.failed = 0

    def __call__(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print("ok:", label, flush=True)
        else:
            self.failed += 1
            print("FAILED:", label, detail, flush=True)


def install_cir(game: Path, marker: bool = True, log: bool = True, runtime: bool = True) -> None:
    """CIR component 0 as installed: its marker, its WeiDU.log line and its
    runtime files (the campaign data when the installed runtime is not
    complete)."""
    files = {}
    if runtime:
        files.update({"override/M_FLPR.lua": "test runtime", "override/FLPRRun.menu": "test runtime",
                      "override/M_FLPRUI.lua": "test runtime"})
    if marker:
        files["override/flrandomiser3.mrk"] = ""
    else:
        files["randomiser-campaigns/next.lua"] = "test runtime"
    if log:
        files["WeiDU.log"] = ("// Log of Currently Installed WeiDU Mods\n"
                              "~CIR/CIR.TP2~ #0 #0 // Chriz Item Randomiser: test\n")
    for name, text in files.items():
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def two_languages(game: Path) -> None:
    """English and German, with the mods installed in German."""
    (game / "dialog.tlk").unlink()
    for folder, names in (("en_US", ["Blade", "Test sword"]), ("de_DE", ["Klinge", "Testschwert"])):
        path = game / "lang" / folder / "dialog.tlk"
        path.parent.mkdir(parents=True)
        path.write_bytes(make_tlk(names))
    (game / "weidu.conf").write_text("lang_dir = de_de\n")


def place(exe_folder: Path) -> Path:
    exe_folder.mkdir(parents=True, exist_ok=True)
    exe = exe_folder / EXE
    shutil.copy2(RUST_CLI, exe)
    return exe


def workspace_of(appdata: Path, game: Path) -> Path:
    """game_folder::game_workspace: the folder name and a hash of the path."""
    digest = hashlib.sha256(str(game).lower().encode("utf-8")).hexdigest()[:12]
    name = game.name[:40].rstrip(". ") or "game"
    return appdata / "Chriz Item Randomiser" / f"{name}-{digest}"


def reference(game: Path, folder: Path, language: str | None, workspace: Path) -> tuple[dict, str]:
    """The reference scans (bytes) and its editor's state with --apply-game."""
    folder.mkdir(parents=True)
    catalog, world = folder / "catalog.json", folder / "world.json"
    scan = ["scan", "--game", str(game), "--out", str(catalog)] + (["--language", language] if language else [])
    for step in (scan, ["world", "scan", "--catalog", str(catalog), "--out", str(world)]):
        subprocess.run(PYTHON + step, check=True, capture_output=True)
    server = Server("python", commands(catalog, world, workspace, "--apply-game", str(game))["python"], workspace)
    try:
        state = masked(server.request("GET", "/api/state")[3].decode("utf-8"), server)
    finally:
        server.stop()
    return {"catalog.json": catalog.read_bytes(), "world.json": world.read_bytes()}, state


def state_of(editor: Editor) -> tuple[dict, str]:
    status, _, _, body = editor.request("GET", "/api/state")
    text = body.decode("utf-8")
    if status != 200:
        raise RuntimeError(f"state: {status} {text}")
    return json.loads(text)["result"], masked(text, editor)


def apply(editor: Editor, state: dict, identity: str) -> tuple[int, str]:
    status, _, _, body = editor.post("/api/apply", bound(state, identity))
    return status, body.decode("utf-8", "replace")


def found_game(check: Checks, label: str, start: Start, game: Path, workspace: Path,
               expected: tuple[dict, str] | None) -> tuple[Editor, dict]:
    editor = start.editor(workspace / "configurations")
    output = start.text()
    check(f"{label}: finds {game.name}", f"Game: {game}\n" in output.replace("\r\n", "\n"), output)
    check(f"{label}: workspace outside the game",
          f"Scans and configurations: {workspace}\n" in output.replace("\r\n", "\n"), output)
    state, text = state_of(editor)
    check(f"{label}: Apply is enabled", state.get("apply_enabled") is True)
    if expected is not None:
        scans, reference_state = expected
        for name, data in scans.items():
            check(f"{label}: {name} equals the reference scan", (workspace / name).read_bytes() == data)
        check(f"{label}: editor state equals the reference editor's with --apply-game", text == reference_state,
              f"\n  reference: {reference_state[:600]}\n  rust:      {text[:600]}")
    return editor, state


def cases(check: Checks, desktop: Desktop, root: Path, starts: list) -> None:
    appdata, games, logs = root / "appdata", root / "Games", root / "logs"
    logs.mkdir()
    games.mkdir()
    environment = {**os.environ, "LOCALAPPDATA": str(appdata), "CIR_NO_BROWSER": "1"}

    def start(exe: Path, label: str, answers: list | None = None) -> tuple[Start, FolderWindow | None]:
        started = Start(desktop, exe, root, environment, logs, label)
        starts.append(started)
        driver = None
        if answers is not None:
            driver = FolderWindow(desktop, started, answers)
            driver.start()
        return started, driver

    # The executable in the game's cir folder: German, as weidu.conf says.
    game_a = games / "Test Game A"
    build(game_a, "eet")
    two_languages(game_a)
    install_cir(game_a)
    workspace_a = workspace_of(appdata, game_a)
    expected = reference(game_a, root / "reference-a", "de_de", root / "reference-a-configurations")
    exe = place(game_a / "cir")
    before = snapshot_tree(game_a)
    first, _ = start(exe, "in-cir")
    editor, state = found_game(check, "in cir", first, game_a, workspace_a, expected)
    check("in cir: nothing written into the game before Apply", snapshot_tree(game_a) == before)
    status, text = apply(editor, state, "player-apply")
    check("in cir: Apply", status == 200 and APPLIED in text, text[:600])
    first.stop()

    # In cir/tools: the same game and workspace; the applied configuration reopens.
    second, _ = start(place(game_a / "cir" / "tools"), "in-cir-tools")
    editor, state = found_game(check, "in cir/tools", second, game_a, workspace_a, None)
    check("in cir/tools: reopens the applied configuration", state["profile"]["id"] == "player-apply",
          state["profile"]["id"])
    second.stop()

    # The game's mods change: the earlier configurations are kept aside. One
    # mod's area cannot be read: the scan leaves it out and says so.
    (game_a / "override" / "synth002.itm").write_bytes((game_a / "override" / "synth001.itm").read_bytes())
    (game_a / "override" / "broken.are").write_bytes(bytes(make_are())[:0x100])
    third, _ = start(game_a / "cir" / EXE, "mods-changed")
    editor, state = found_game(check, "mods changed", third, game_a, workspace_a, None)
    output = third.text()
    earlier = workspace_a / "configurations" / "earlier scans"
    check("mods changed: the earlier configuration is kept aside",
          "One saved configuration was made before the game's mods changed" in output
          and (earlier / "player-apply.json").is_file(), output)
    check("mods changed: a new configuration",
          "The configuration applied last cannot be reopened; the editor starts with a new one." in output
          and state["profile"]["id"] != "player-apply" and state["saved"] == [], output)
    check("mods changed: names the area the scan leaves out",
          "BROKEN.ARE (Truncated AREA header) cannot be read, so Chriz Item Randomiser leaves it out: "
          "its items stay as the game has them and no reward goes there.\n" in output.replace("\r\n", "\n"),
          output)
    status, text = apply(editor, state, "player-apply-again")
    check("mods changed: Apply", status == 200 and APPLIED in text, text[:600])
    third.stop()

    # Outside a game: the folder window asks until a game with CIR is chosen.
    game_b, game_c = games / "Test Game B", games / "Test Game C"
    build(game_b, "bgee")
    build(game_c, "bg2ee")
    install_cir(game_c, marker=False)
    downloads = root / "Downloads"
    expected = reference(game_c, root / "reference-c", None, root / "reference-c-configurations")
    fourth, driver = start(place(downloads / "cir"), "outside-a-game",
                           [downloads, game_b, game_c / "override"])
    try:
        editor, state = found_game(check, "outside a game", fourth, game_c, workspace_of(appdata, game_c),
                                   expected)
    except RuntimeError:
        print("folder windows:", driver.seen, "answers:", driver.answered, "error:", repr(driver.error))
        raise
    driver.join(30)
    output = fourth.text()
    check("outside a game: says why it asks",
          f"No game folder was found at or above {downloads / 'cir'}, where this program is." in output, output)
    check("outside a game: a folder without a game is not taken",
          f"{downloads} is not a game folder, and no folder above it is one" in output, output)
    check("outside a game: a game without CIR is not taken",
          f"Chriz Item Randomiser is not installed in {game_b}.\n" in output.replace("\r\n", "\n"), output)
    check("outside a game: three answers", driver.error is None and driver.answered == 3,
          f"{driver.answered} answers, {driver.error!r}")
    title = "Choose the game folder where Chriz Item Randomiser is installed"
    check("outside a game: the folder window's title and buttons",
          all(seen["title"] == title and seen["buttons"].get(IDOK) == "Use this folder"
              and seen["buttons"].get(IDCANCEL) == "Cancel" for seen in driver.seen), driver.seen)
    check("outside a game: each window opens where the last answer was",
          [seen["address"] for seen in driver.seen]
          == [f"Address: {folder}" for folder in (downloads / "cir", downloads, game_b)], driver.seen)
    fourth.stop()

    # In a game without CIR, closing the folder window opens nothing.
    fifth, driver = start(place(game_b / "cir"), "not-installed", [None])
    code = fifth.wait()
    driver.join(30)
    output = fifth.text()
    check("not installed: says so",
          f"Chriz Item Randomiser is not installed in {game_b}, the game this program is in." in output, output)
    check("not installed: closing the window opens nothing",
          code == 1 and "No game folder was chosen, so the editor was not opened." in output
          and EDITOR.search(output) is None and not workspace_of(appdata, game_b).exists(),
          f"exit {code}: {output}")
    check("not installed: one answer", driver.error is None and driver.answered == 1,
          f"{driver.answered} answers, {driver.error!r}")

    # An incomplete installation: told in plain words; the window waits for Enter.
    game_d = games / "Test Game D"
    build(game_d, "eet")
    install_cir(game_d)
    (game_d / "override" / "M_FLPR.lua").unlink()
    sixth, _ = start(place(game_d / "cir"), "incomplete")
    code = sixth.wait()
    errors = sixth.text("err")
    check("incomplete: told in plain words",
          code == 1 and "Could not open the editor: Install Chriz Item Randomiser in this game first: "
          "override/M_FLPR.lua" in errors, f"exit {code}: {sixth.text()}{errors}")
    check("incomplete: the window waits for Enter", "Press Enter to close this window." in sixth.text(),
          sixth.text())


def main() -> int:
    if os.name != "nt":
        raise SystemExit("player_check.py starts the executable on a Windows desktop of its own; run it on Windows.")
    common.require_built()
    declare()
    check = Checks()
    desktop = Desktop()
    starts: list[Start] = []
    try:
        with tempfile.TemporaryDirectory(prefix="cir-player-") as temp:
            try:
                cases(check, desktop, Path(temp).resolve(), starts)
            finally:
                # Before the folder goes: a running executable keeps its file.
                for started in starts:
                    started.stop()
    finally:
        desktop.close()
    print(f"{check.passed} checks passed, {check.failed} failed")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
