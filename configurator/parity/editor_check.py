"""Python/Rust comparison of the local editor's HTTP interface.

Scans a synthetic installation (built with the reference tests' builders in
a temporary folder), starts the reference editor (`randomiser_config.py ui`)
and the executable (`cir-configurator ui --no-browser`) on the same scans,
sends both the same session of requests and compares status, content
headers and bodies, then the files each wrote into its workspace. Random
draft ids (`adventure-<12 hex>`) and the workspace folders are masked; drafts
sent back are each server's own.

    python configurator/parity/editor_check.py
"""
from __future__ import annotations

import copy
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import common
from common import RUST_CLI
from cli_check import PYTHON, SOURCE, TARGET, build

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


def bound(state: dict, identity: str) -> dict:
    """A server's current draft with the chest's item bound to the actor."""
    draft = {"profile": copy.deepcopy(state["profile"]), "plan": copy.deepcopy(state["plan"])}
    draft["profile"]["id"] = identity
    draft["profile"]["name"] = "Parity " + identity
    draft["profile"]["items"] = ["synth001"]
    draft["plan"]["sources"] = [{"item": "synth001", "policy": "relocate", "source_ids": [SOURCE],
                                 "registration_ids": []}]
    draft["plan"]["targets"] = [{"id": TARGET, "capacity": 1, "optional": False}]
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


def main() -> int:
    common.require_built()
    sys.stdout.reconfigure(errors="backslashreplace")
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cir-parity-editor-") as temp:
        root = Path(temp)
        game = root / "game"
        build(game)
        scans = root / "scans"
        scans.mkdir()
        catalog, world = scans / "catalog.json", scans / "world.json"
        for step in (["scan", "--game", str(game), "--out", str(catalog)],
                     ["world", "scan", "--catalog", str(catalog), "--out", str(world)]):
            subprocess.run(PYTHON + step, check=True, capture_output=True)
        # Equal-length workspace paths keep Content-Length comparable.
        workspaces = {"python": root / "wsp", "rust": root / "wsr"}
        arguments = ["ui", "--catalog", str(catalog), "--world", str(world), "--port", "0", "--workspace"]
        servers = []
        try:
            servers.append(Server("python", PYTHON + arguments + [str(workspaces["python"])], workspaces["python"]))
            servers.append(Server("rust", [str(RUST_CLI)] + arguments + [str(workspaces["rust"]), "--no-browser"],
                                  workspaces["rust"]))
            announced = [masked(json.dumps(server.announced), server) for server in servers]
            if announced[0] != announced[1]:
                failures += 1
                print("DIFFERENT start:", announced)
            states = {server.name: json.loads(server.request("GET", "/api/state")[3])["result"] for server in servers}
            steps = session()
            for label, action in steps:
                outcomes = {}
                for server in servers:
                    status, reason, headers, body = action(server, states[server.name])
                    text = body.decode("utf-8", "replace")
                    if status == 200 and headers["Content-Type"].startswith("application/json"):
                        value = json.loads(text)["result"]
                        if isinstance(value, dict) and "profile" in value and "plan" in value:
                            states[server.name] = value
                    outcomes[server.name] = (status, reason, headers, masked(text, server))
                if outcomes["python"] != outcomes["rust"]:
                    failures += 1
                    print("DIFFERENT:", label)
                    for name, (status, reason, headers, text) in outcomes.items():
                        print(f"  {name} [{status} {reason}] {headers}: {text[:1200]}")
                else:
                    status = outcomes["python"][0]
                    print(f"same: {label} [{status}]")
        finally:
            for server in servers:
                server.stop()
        trees = {}
        for name, workspace in workspaces.items():
            tree = {}
            for path in sorted(workspace.rglob("*")):
                if path.is_file():
                    relative = RANDOM_ID.sub("adventure-<id>", path.relative_to(workspace).as_posix())
                    data = path.read_bytes()
                    try:
                        data = RANDOM_ID.sub("adventure-<id>", data.decode("utf-8")).replace(
                            json.dumps(str(workspace))[1:-1], "<workspace>")
                    except UnicodeDecodeError:
                        pass
                    tree[relative] = data
            trees[name] = tree
        for relative in sorted(set(trees["python"]) | set(trees["rust"])):
            if trees["python"].get(relative) != trees["rust"].get(relative):
                failures += 1
                print("WORKSPACE DIFFERS:", relative,
                      "missing in rust" if relative not in trees["rust"] else
                      "only in rust" if relative not in trees["python"] else "content")
        print(f"editor parity: {len(steps)} requests, {len(trees['python'])} workspace files compared, "
              f"{failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
