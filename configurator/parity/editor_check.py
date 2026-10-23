"""Python/Rust comparison of the local editor's HTTP interface.

Scans a synthetic installation (built with the reference tests' builders in
a temporary folder), starts the reference editor (`randomiser_config.py ui`)
and the executable (`cir-configurator ui --no-browser`) on the same scans,
sends both the same session of requests and compares status, content
headers and bodies, then the files each wrote into its workspace. Random
draft ids (`adventure-<12 hex>`) and the workspace folders are masked; drafts
sent back are each server's own. Further phases cover CIR's install record
(read while the editor runs), the scripted quest-pool reward, a preset with
the options that add their own plan fields (More Spell Shield copies, cursed
disguises), and applying configurations to a synthetic test runtime, where
each editor runs in turn on the same game and the game's files are compared
afterwards.

    python configurator/parity/editor_check.py
"""
from __future__ import annotations

import copy
import http.client
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

import common
from common import RUST_CLI, restore_tree, snapshot_tree
from cli_check import PYTHON, build, sources

from flir_config.profiles import write_json  # noqa: E402
from test_cursed_disguises import resources as cursed_resources  # noqa: E402
from test_postinstall_blpool_script import compiled_blpool, pool_game, pool_plan, write_registry  # noqa: E402
from test_postinstall_resources import make_itm, mark_eet  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_sto  # noqa: E402

RANDOM_ID = re.compile(r"adventure-[0-9a-f]{12}")
HEADERS = ("Content-Type", "Content-Length", "Cache-Control")


class Server:
    def __init__(self, name: str, command: list[str], workspace: Path):
        self.name = name
        self.workspace = workspace
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = self.process.stdout.readline().decode("utf-8")
        if not line:
            raise RuntimeError(f"{name} editor did not start: " + self.process.stderr.read().decode("utf-8", "replace"))
        announced = json.loads(line)
        self.port = int(announced["url"].rsplit(":", 1)[1])
        self.announced = announced

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None,
                host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", self.address if host is None else host)
        for key, value in (headers or {}).items():
            connection.putheader(key, value.replace("{address}", self.address))
        if body is not None and not any(key.lower() == "content-length" for key in (headers or {})):
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, response.reason, {key: response.getheader(key) for key in HEADERS}, data

    def post(self, path: str, value) -> tuple:
        return self.request("POST", path, json.dumps(value).encode("utf-8"),
                            {"Content-Type": "application/json", "Origin": "http://{address}"})

    def stop(self) -> None:
        self.process.kill()
        self.process.wait()


def masked(text: str, server: Server) -> str:
    # As is, inside JSON, and inside a quoted path inside JSON.
    path = str(server.workspace)
    for old in (path, json.dumps(path)[1:-1], json.dumps(json.dumps(path)[1:-1])[1:-1]):
        text = text.replace(old, "<workspace>")
    text = text.replace(server.address, "<address>")
    return RANDOM_ID.sub("adventure-<id>", text)


def bound(state: dict, identity: str, area: str = "area") -> dict:
    """A server's current draft with the chest's item bound to the actor."""
    source, target = sources(area)
    draft = {"profile": copy.deepcopy(state["profile"]), "plan": copy.deepcopy(state["plan"])}
    draft["profile"]["id"] = identity
    draft["profile"]["name"] = "Parity " + identity
    draft["profile"]["items"] = ["synth001"]
    draft["plan"]["sources"] = [{"item": "synth001", "policy": "relocate", "source_ids": [source],
                                 "registration_ids": []}]
    draft["plan"]["targets"] = [{"id": target, "capacity": 1, "optional": False}]
    return draft


def session():
    """(label, action) pairs; an action takes the server and its last state."""
    get = lambda path: lambda server, state: server.request("GET", path)  # noqa: E731
    post = lambda path, value: lambda server, state: server.post(path, value)  # noqa: E731

    def with_draft(path, identity, **extra):
        return lambda server, state: server.post(path, {**bound(state, identity), **extra})

    def raw(path, body, headers, host=None):
        return lambda server, state: server.request("POST", path, body, headers, host)

    json_headers = {"Content-Type": "application/json"}
    return [
        ("static index", get("/")),
        ("static index with query", get("/?x=1")),
        ("static css", get("/style.css")),
        ("static js", get("/app.js")),
        ("unknown path", get("/nosuch")),
        ("network-path reference", get("//api/state")),
        ("path parameters", get("/api/state;x")),
        ("fragment", get("/api/state#x")),
        ("state", get("/api/state")),
        ("templates", get("/api/templates")),
        ("recommended", get("/api/recommended")),
        ("items", get("/api/items?q=blade")),
        ("items empty", get("/api/items?q=")),
        ("items no value", get("/api/items?q")),
        ("items plus and percent", get("/api/items?q=+bl%61de+&q=other")),
        ("items bad escapes", get("/api/items?q=%ZZ%FF")),
        ("items offset", get("/api/items?q=synth&offset=1")),
        ("items bad offset", get("/api/items?q=x&offset=abc")),
        ("items negative offset", get("/api/items?q=x&offset=-1")),
        ("items unicode offset", get("/api/items?q=synth&offset=%D9%A1")),
        ("sources", get("/api/sources?item=synth001")),
        ("sources upper", get("/api/sources?item=SYNTH001")),
        ("sources unknown", get("/api/sources?item=nosuch")),
        ("sources offset", get("/api/sources?item=synth001&offset=5")),
        ("sources negative", get("/api/sources?item=synth001&offset=-1")),
        ("targets", get("/api/targets?q=chest")),
        ("targets empty", get("/api/targets?q=")),
        ("targets creature", get("/api/targets?q=embed&kind=creature")),
        ("targets shop", get("/api/targets?q=shop&kind=store")),
        ("targets kind", get("/api/targets?q=x&kind=bad")),
        ("targets terms", get("/api/targets?q=TEST%20area")),
        ("new named", post("/api/new", {"name": "Parity run"})),
        ("new default", post("/api/new", {})),
        ("new empty name", post("/api/new", {"name": ""})),
        ("template original", post("/api/template", {"id": "eet-original"})),
        ("template boss loot extended", post("/api/template", {"id": "eet-boss-loot", "items": "extended",
                                                               "targets": "extended"})),
        ("template options", post("/api/template", {"id": "bg2-original",
                                                    "options": {"scrolls": False, "loss_mode": "none"}})),
        ("template unknown", post("/api/template", {"id": "nosuch"})),
        ("template missing id", post("/api/template", {})),
        ("new for binding", post("/api/new", {"name": "Binding"})),
        ("save first", with_draft("/api/save", "parity-first")),
        ("save second", with_draft("/api/save", "parity-second")),
        ("save copy", with_draft("/api/save", "parity-second", copy=True)),
        ("save bad id", with_draft("/api/save", "Not Valid")),
        ("validate", with_draft("/api/validate", "parity-first")),
        ("prepare", with_draft("/api/prepare", "parity-first")),
        ("prepare again", with_draft("/api/prepare", "parity-first")),
        ("apply disabled", with_draft("/api/apply", "parity-first")),
        ("load", post("/api/load", {"id": "parity-second"})),
        ("load traversal", post("/api/load", {"id": "../parity-first"})),
        ("load unknown", post("/api/load", {"id": "parity-none"})),
        ("load not text", post("/api/load", {"id": 7})),
        ("load missing", post("/api/load", {})),
        ("unknown post", post("/api/nosuch", {})),
        ("state after", get("/api/state")),
        ("wrong host", lambda server, state: server.request("GET", "/api/state", host="localhost")),
        ("content type charset", raw("/api/new", b"{}", {"Content-Type": "application/json; charset=utf-8"})),
        ("content type case", raw("/api/new", b"{}", {"Content-Type": "Application/JSON"})),
        ("content type missing", raw("/api/new", b"{}", {})),
        ("content type text", raw("/api/new", b"{}", {"Content-Type": "text/plain"})),
        ("origin matching", raw("/api/new", b"{}", {**json_headers, "Origin": "http://{address}"})),
        ("origin foreign", raw("/api/new", b"{}", {**json_headers, "Origin": "http://example.invalid"})),
        ("length text", raw("/api/new", b"{}", {**json_headers, "Content-Length": "abc"})),
        ("length zero", raw("/api/new", b"", {**json_headers, "Content-Length": "0"})),
        ("length large", raw("/api/new", b"{}", {**json_headers, "Content-Length": "2000001"})),
        ("body list", raw("/api/new", b"[]", json_headers)),
        ("body invalid json", raw("/api/new", b"{", json_headers)),
        ("body invalid utf-8", raw("/api/new", b"\xff{}", json_headers)),
        ("body nan", raw("/api/load", b'{"id": NaN}', json_headers)),
        ("get on post route", get("/api/new")),
    ]


def tree(folder: Path) -> dict:
    """Files below a folder with random draft ids masked."""
    result = {}
    for path in sorted(folder.rglob("*")) if folder.exists() else []:
        if path.is_file():
            relative = RANDOM_ID.sub("adventure-<id>", path.relative_to(folder).as_posix())
            data = path.read_bytes()
            try:
                data = RANDOM_ID.sub("adventure-<id>", data.decode("utf-8")).replace(
                    json.dumps(str(folder))[1:-1], "<folder>")
            except UnicodeDecodeError:
                pass
            result[relative] = data
    return result


def tree_failures(label: str, expected: dict, actual: dict) -> int:
    failures = 0
    for relative in sorted(set(expected) | set(actual)):
        if expected.get(relative) != actual.get(relative):
            failures += 1
            print(f"{label} DIFFERS:", relative,
                  "missing in rust" if relative not in actual else
                  "only in rust" if relative not in expected else "content")
    return failures


def scan(game: Path, scans: Path, language: str | None = None) -> tuple[Path, Path]:
    scans.mkdir()
    catalog, world = scans / "catalog.json", scans / "world.json"
    for step in (["scan", "--game", str(game), "--out", str(catalog)] + (["--language", language] if language else []),
                 ["world", "scan", "--catalog", str(catalog), "--out", str(world)]):
        subprocess.run(PYTHON + step, check=True, capture_output=True)
    return catalog, world


def commands(catalog: Path, world: Path, workspace: Path, *extra: str) -> dict:
    arguments = ["ui", "--catalog", str(catalog), "--world", str(world), "--port", "0",
                 "--workspace", str(workspace), *extra]
    return {"python": PYTHON + arguments, "rust": [str(RUST_CLI)] + arguments + ["--no-browser"]}


def compare_step(label: str, outcomes: dict) -> int:
    if outcomes["python"] != outcomes["rust"]:
        print("DIFFERENT:", label)
        for name, (status, reason, headers, text) in outcomes.items():
            print(f"  {name} [{status} {reason}] {headers}: {text[:1200]}")
        return 1
    print(f"same: {label} [{outcomes['python'][0]}]")
    return 0


def outcome(server: Server, result, states: dict) -> tuple:
    status, reason, headers, body = result
    text = body.decode("utf-8", "replace")
    if status == 200 and (headers["Content-Type"] or "").startswith("application/json"):
        value = json.loads(text)["result"]
        if isinstance(value, dict) and "profile" in value and "plan" in value:
            states[server.name] = value
    return status, reason, headers, masked(text, server)


def concurrent_phase(name: str, root: Path, catalog: Path, world: Path, steps) -> tuple[int, int, int]:
    """Both editors side by side, each with its own workspace."""
    failures = 0
    # Equal-length workspace paths keep Content-Length comparable.
    slug = name.replace(" ", "-")
    workspaces = {"python": root / f"{slug}-wsp", "rust": root / f"{slug}-wsr"}
    servers = []
    try:
        for side in ("python", "rust"):
            servers.append(Server(side, commands(catalog, world, workspaces[side])[side], workspaces[side]))
        announced = [masked(json.dumps(server.announced), server) for server in servers]
        if announced[0] != announced[1]:
            failures += 1
            print("DIFFERENT start:", announced)
        states = {server.name: json.loads(server.request("GET", "/api/state")[3])["result"] for server in servers}
        for label, action in steps:
            outcomes = {server.name: outcome(server, action(server, states[server.name]), states)
                        for server in servers}
            failures += compare_step(f"{name}: {label}", outcomes)
    finally:
        for server in servers:
            server.stop()
    trees = {side: tree(workspace) for side, workspace in workspaces.items()}
    failures += tree_failures(f"{name} workspace", trees["python"], trees["rust"])
    return len(steps), len(trees["python"]), failures


def registry_steps(game: Path, routes: list[str]):
    """CIR's install record changes while the editor runs."""
    def with_registry(statuses, route):
        def action(server, state):
            if statuses is None:
                (game / "override" / "FLPRPAT.lua").unlink(missing_ok=True)
            else:
                write_registry(game, **statuses)
            return server.request("GET", route)
        return action
    steps = []
    for label, statuses in (("no record", None), ("all applied", {}),
                            ("flail heads left as installed", {"flail_heads": "dialogue-unrecognised"}),
                            ("pool left as installed", {"quest_pool": "grant-ambiguous"})):
        for route in routes:
            steps.append((f"{route} with {label}", with_registry(statuses, route)))
    return steps


def pool_phase(root: Path):
    """BLPOOL as CIR prepares it: the scripted quest reward source."""
    game = root / "pool-game"
    catalog_value, world_value = pool_game(game, compiled_blpool())
    # The pool is in Shadows of Amn; EET offers its presets.
    mark_eet(game)
    scans = root / "pool-scans"
    scans.mkdir()
    catalog, world = scans / "catalog.json", scans / "world.json"
    write_json(catalog, catalog_value)
    write_json(world, world_value)
    profile, plan = pool_plan(catalog_value, world_value)
    draft = {"profile": profile, "plan": plan}

    def post(path, value):
        return lambda server, state: server.post(path, value)

    steps = [
        ("sources", lambda server, state: server.request("GET", "/api/sources?item=blun12")),
        ("validate", post("/api/validate", draft)),
        ("save", post("/api/save", draft)),
        ("prepare", post("/api/prepare", draft)),
        ("load", post("/api/load", {"id": profile["id"]})),
        *registry_steps(game, ["/api/sources?item=blun12", "/api/recommended"]),
    ]
    return concurrent_phase("quest pool", root, catalog, world, steps)


# A preset with More Spell Shield scrolls and cursed disguises: its plan holds
# the options' own metadata (the Spell Shield stock, the disguises' items).
OPTIONS_PRESET = {"id": "bg2-original", "items": "extended", "targets": "extended",
                  "options": {"scrolls": True, "more_spell_shield": True, "cursed_disguise": True}}


def add_option_resources(game: Path) -> None:
    """What the preset needs to fit with both options: Spell Shield's store
    and scroll, a place of the scroll's tier, the disguise items, and English
    game text with a journal table for the disguises' texts."""
    override = game / "override"
    scroll = bytearray(make_itm())
    struct.pack_into("<I", scroll, 0x18, 0x4C)
    (override / "scrl8x.itm").write_bytes(bytes(scroll))
    (override / "udduer01.sto").write_bytes(bytes(make_sto(
        [(item_record("scrl8x", expiration=0, charges=(1, 1, 0), flags=0), 1, 0)])))
    (override / "ar0808.are").write_bytes(make_are([], [("Chest1", 0, 0)]))
    for name, data in cursed_resources().items():
        item = bytearray(data)
        struct.pack_into("<HH", item, 0x72 + 0x1E, 0, 0)
        (override / f"{name}.itm").write_bytes(bytes(item))
    language = game / "lang" / "en_US"
    language.mkdir(parents=True)
    (language / "dialog.tlk").write_bytes((game / "dialog.tlk").read_bytes())
    (override / "BGEE.LUA").write_bytes(b"quests = {}\n")


def preset_draft(state: dict) -> dict:
    """A server's preset draft under a fixed id: the id is part of the revision."""
    draft = {"profile": copy.deepcopy(state["profile"]), "plan": copy.deepcopy(state["plan"])}
    draft["profile"]["id"] = "parity-options"
    return draft


def options_phase(root: Path):
    """The preset with both options: its draft saved, loaded, checked and prepared."""
    game = root / "options-game"
    build(game)
    add_option_resources(game)
    catalog, world = scan(game, root / "options-scans", "en_US")

    def own(path):
        return lambda server, state: server.post(path, preset_draft(state))

    steps = [
        ("preset with Spell Shield and disguises", lambda server, state: server.post("/api/template", OPTIONS_PRESET)),
        ("save", own("/api/save")),
        ("load", lambda server, state: server.post("/api/load", {"id": "parity-options"})),
        ("validate", own("/api/validate")),
        ("prepare", own("/api/prepare")),
    ]
    return concurrent_phase("preset options", root, catalog, world, steps)


OPEN_GAME = "Close this game and its installer before applying settings"


# Apply on each game CIR installs on. The area's name decides which later
# campaign starts reach it: none on EET ("area"), Siege of Dragonspear on
# BG:EE ("bd0100"), Throne of Bhaal on BG2:EE ("ar4000").
APPLY_GAMES = (("eet", "area"), ("bgee", "bd0100"), ("bg2ee", "ar4000"))


def apply_phase(root: Path, platform: str, area: str, attempts: int = 3,
                options: bool = False) -> tuple[int, int, int]:
    """Apply to a test runtime: each editor in turn on the same game and
    workspaces, restored in between, then the game and workspaces compared.
    With `options`, the applied draft is the preset with both options.

    Both check for real that no game, loader, WeiDU or mod installer runs (the
    reference asks Windows); one started meanwhile by something else refuses
    only one side's apply, so such a run is repeated."""
    name = platform + (" preset options" if options else "")
    for attempt in range(1, attempts + 1):
        results, trees = apply_attempt(root / f"apply-{name.replace(' ', '-')}-{attempt}", platform, area, options)
        refused = any(OPEN_GAME in text for outcomes in results.values() for _, (_, _, _, text) in outcomes)
        if refused and attempt < attempts:
            print(f"apply {name}: a game, loader or installer ran during attempt {attempt}; repeating",
                  flush=True)
            continue
        failures = 0
        for (label, python), (_, rust) in zip(results["python"], results["rust"]):
            failures += compare_step(f"apply {name}: {label}", {"python": python, "rust": rust})
        for folder in trees["python"]:
            failures += tree_failures(f"apply {name} {folder}", trees["python"][folder], trees["rust"][folder])
        files = sum(len(folder) for folder in trees["python"].values())
        return len(results["python"]), files, failures


def apply_attempt(root: Path, platform: str, area: str, options: bool = False) -> tuple[dict, dict]:
    root.mkdir()
    game = root / "apply-game"
    build(game, platform, area)
    if options:
        add_option_resources(game)
    for name in ("override/M_FLPR.lua", "override/FLPRRun.menu", "randomiser-campaigns/next.lua"):
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test runtime")
    (game / "override" / "other-mod.txt").write_bytes(b"preserve unrelated mod content")
    catalog, world = scan(game, root / "apply-scans", "en_US" if options else None)
    workspace, other = root / "apply-workspace", root / "apply-other"
    before = snapshot_tree(game)
    results, trees = {}, {}

    def changed(server, state):
        draft = bound(state, "parity-apply", area)
        draft["profile"]["name"] = "Changed next adventure"
        draft["profile"]["shop_pricing"] = {"percent": 140, "base_prices": {}}
        return server.post("/api/apply", draft)

    def apply(server, state):
        return server.post("/api/apply", bound(state, "parity-apply", area))

    def state_of(server, state):
        return server.request("GET", "/api/state")

    def get(path):
        return lambda server, state: server.request("GET", path)

    def preset(server, state):
        return server.post("/api/template", OPTIONS_PRESET)

    def apply_preset(server, state):
        return server.post("/api/apply", preset_draft(state))

    for side in ("python", "rust"):
        restore_tree(game, before)
        for folder in (workspace, other):
            restore_tree(folder, None)
        outcomes = []
        states = {}

        def session(folder, actions):
            server = Server(side, commands(catalog, world, folder, "--apply-game", str(game))[side], folder)
            try:
                states[side] = json.loads(server.request("GET", "/api/state")[3])["result"]
                for label, action in actions:
                    outcomes.append((label, outcome(server, action(server, states[side]), states)))
            finally:
                server.stop()

        if options:
            session(workspace, [("state", state_of), ("preset with Spell Shield and disguises", preset),
                                ("apply", apply_preset), ("state after apply", state_of)])
            session(workspace, [("reopened state", state_of)])
        else:
            session(workspace, [("state", state_of), ("presets of the game", get("/api/templates")),
                                ("recommended preset", get("/api/recommended")),
                                ("records with their parts of the game", get("/api/sources?item=synth001")),
                                *((f"recipients in {part}", get(f"/api/targets?q={part}"))
                                  for part in ("bg1", "sod", "bg2", "tob")),
                                ("apply", apply),
                                ("apply again unchanged", apply), ("apply changed", changed),
                                ("state after apply", state_of)])
            session(workspace, [("reopened state", state_of)])
            session(other, [("other workspace apply", apply)])
        results[side] = outcomes
        trees[side] = {"game": tree(game), "workspace": tree(workspace), "other workspace": tree(other)}
    restore_tree(game, before)
    return results, trees


def main() -> int:
    common.require_built()
    sys.stdout.reconfigure(errors="backslashreplace")
    totals = [0, 0, 0]
    with tempfile.TemporaryDirectory(prefix="cir-parity-editor-") as temp:
        root = Path(temp)
        game = root / "game"
        build(game)
        catalog, world = scan(game, root / "scans")
        steps = session() + registry_steps(game, ["/api/recommended"])
        phases = [concurrent_phase("synthetic", root, catalog, world, steps), pool_phase(root),
                  options_phase(root)]
        phases += [apply_phase(root, platform, area) for platform, area in APPLY_GAMES]
        phases.append(apply_phase(root, "eet", "area", options=True))
        for result in phases:
            totals = [total + value for total, value in zip(totals, result)]
    print(f"editor parity: {totals[0]} requests, {totals[1]} written files compared, {totals[2]} failures")
    return 1 if totals[2] else 0


if __name__ == "__main__":
    raise SystemExit(main())
