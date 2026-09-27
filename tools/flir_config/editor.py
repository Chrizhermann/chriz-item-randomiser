"""Local draft editor over saved scans; never reads or writes game resources."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .campaigns import compile_campaign, write_campaign
from .native_export import _compile as compile_native
from .profiles import (PROFILE_ID, ProfileError, ensure_output_outside_game,
                       new_profile, read_json, search_items, validate_catalog,
                       validate_profile, write_json)
from .sources import new_binding_plan, validate_world
from .scripted_sources import blpool_registration, SUPPORTED_HASHES


KIND_LABELS = {"actor": "Creature", "cre-template": "Creature template",
               "embedded-cre": "Creature", "container": "Container", "store": "Shop"}


class Editor:
    def __init__(self, catalog: dict, world: dict, workspace: Path,
                 profile: dict | None = None, plan: dict | None = None):
        self.items = validate_catalog(catalog)
        self.index = validate_world(world, catalog)
        self.catalog, self.world = catalog, world
        self.workspace = ensure_output_outside_game(workspace, catalog)
        self.sources = defaultdict(list)
        for source in world["sources"]:
            self.sources[source["item"]].append(source)
        if (profile is None) != (plan is None):
            raise ProfileError("Supply both an initial profile and its binding plan")
        if profile is None:
            profile = new_profile(catalog, self.new_id(), "My next adventure")
            plan = new_binding_plan(profile, catalog, world)
        self.draft = self.checked({"profile": profile, "plan": plan})

    @staticmethod
    def new_id() -> str:
        return "adventure-" + uuid4().hex[:12]

    def checked(self, data: dict) -> dict:
        """Incomplete drafts may be saved; full binding checks happen on Check."""
        profile = validate_profile(data["profile"], self.catalog)
        plan = deepcopy(data["plan"])
        expected = {"schema", "catalog_fingerprint", "world_fingerprint", "sources", "targets", "registrations"}
        if (set(plan) != expected or plan["schema"] != "flir-binding-plan-v1" or
                plan["catalog_fingerprint"] != self.catalog["fingerprint"] or
                plan["world_fingerprint"] != self.world["fingerprint"] or
                any(not isinstance(plan[k], list) for k in ("sources", "targets", "registrations"))):
            raise ProfileError("These selections belong to a different scan or an unsupported draft")
        return {"profile": profile, "plan": plan}

    def check(self, draft: dict) -> dict:
        result = {"valid": False, "runtime_supported": False, "reward_units": 0, "target_capacity": 0}
        try:
            campaign = compile_campaign(draft["profile"], self.catalog, self.world, draft["plan"])
            result.update(valid=True, reward_units=sum(s["quantity"] for s in campaign["sources"]),
                          target_capacity=sum(t["capacity"] for t in campaign["targets"]))
            compile_native(campaign, self.world, self.index, self.items)
        except ProfileError as exc:
            return {**result, "message": str(exc)}
        return {**result, "runtime_supported": True,
                "message": "Configuration checks passed for the current test runtime. "
                           "Game testing and deployment are separate; nothing was activated."}

    def path(self, identity: str) -> Path:
        if not isinstance(identity, str) or not PROFILE_ID.fullmatch(identity):
            raise ProfileError("Unknown configuration")
        return ensure_output_outside_game(self.workspace / (identity + ".json"), self.catalog)

    def label(self, target: dict) -> str:
        selector = target["selector"]
        template = self.index["targets"].get(selector.get("template_resource"), {})
        name = (selector.get("name") or selector.get("script_name") or
                template.get("selector", {}).get("script_name") or selector.get("cre_resref") or target["id"])
        return f"{name} ({target['resource'].split(':', 1)[1].upper()})"

    def target(self, row: dict) -> dict:
        notes = {"actor": "Encounter availability and drops need a game test.",
                 "cre-template": "Creature definition; choosing a specific spawn still needs runtime support.",
                 "container": "Container destination; delivery here still needs runtime support.",
                 "store": "Shop destination; prototype delivery is available when this configuration passes its check."}
        return {"id": row["id"], "label": self.label(row), "kind": row["kind"],
                "kind_label": KIND_LABELS[row["kind"]], "note": notes[row["kind"]]}

    def search_text(self, row: dict) -> str:
        selector = row["selector"]
        template = self.index["targets"].get(selector.get("template_resource"), {})
        names = [row["id"], self.label(row)]
        for fields in (selector, template.get("selector", {})):
            names.extend(fields.get(key, "") for key in ("name", "script_name", "cre_resref"))
        return " ".join(names).casefold()

    def state(self) -> dict:
        saved = []
        for path in sorted(self.workspace.glob("*.json")):
            value = self.checked(read_json(path))
            saved.append({key: value["profile"][key] for key in ("id", "name")})
        targets = []
        for choice in self.draft["plan"]["targets"]:
            row = self.index["targets"].get(choice["id"])
            targets.append({**(self.target(row) if row else
                                {"id": choice["id"], "label": choice["id"], "kind": "missing"}),
                            "capacity": choice["capacity"]})
        return {**deepcopy(self.draft), "items": [self.items[i] for i in self.draft["profile"]["items"]],
                "targets": targets, "saved": saved, "workspace": str(self.workspace),
                "catalog": {"item_count": len(self.items), "language": self.catalog["language"],
                            "game_name": Path(self.catalog["game_root"]).parent.name}}

    @staticmethod
    def page(rows: list[dict], offset: int) -> dict:
        if type(offset) is not int or offset < 0:
            raise ProfileError("Search offset must be nonnegative")
        return {"total": len(rows), "offset": offset, "records": rows[offset:offset + 20],
                "next_offset": offset + 20 if offset + 20 < len(rows) else None}

    def get(self, route: str, params: dict) -> dict:
        if route == "/api/state":
            return self.state()
        offset = int(params.get("offset", "0"))
        query = params.get("q", "").strip()
        if route == "/api/items":
            if not query:
                return {"total": 0, "offset": 0, "items": [], "next_offset": None}
            return search_items(self.catalog, query, offset=offset)
        if route == "/api/sources":
            item = params.get("item", "").lower()
            if item not in self.items:
                raise ProfileError("Unknown item")
            rows = list(self.sources[item])
            if item == "blun12" and self.index["resources"].get("bcs:blpool", {}).get("sha256") in SUPPORTED_HASHES:
                registration = blpool_registration(self.world)
                rows.insert(0, {"id": registration["id"], "kind": "scripted", "concerns": [],
                               "registration": registration})
            page = self.page(rows, offset)
            page["records"] = [{"id": s["id"], "kind": s["kind"], "concerns": s["concerns"],
                "quantity": s["record"]["quantity"] if s["kind"] == "store" else 1,
                "kind_label": KIND_LABELS[s["kind"]],
                "label": (self.label(self.index["targets"][s["owner"]]) if s["owner"] in self.index["targets"]
                          else s["owner"]) + f" · item record {s['record']['record_index']}",
                "note": ("Container source; check configuration for current runtime support."
                         if s["kind"] == "container" else "This source type still needs runtime support.")}
                if s["kind"] != "scripted" else {
                    **s, "quantity": 1, "kind_label": "Quest reward",
                    "label": "Blood pool reward (BLPOOL)",
                    "note": "Moves the Mace of Disruption; the pool's other actions remain. "
                            "The installed script must match the supported reward before export."}
                for s in page["records"]]
            return page
        if route == "/api/targets":
            kinds = {"all": set(KIND_LABELS), "creature": {"actor", "cre-template"},
                     "container": {"container"}, "store": {"store"}}
            kind = params.get("kind", "all")
            if kind not in kinds:
                raise ProfileError("Unknown recipient type")
            if not query:
                return self.page([], 0)
            terms = query.casefold().split()
            rows = [row for row in self.world["targets"] if row["kind"] in kinds[kind] and
                    all(term in self.search_text(row) for term in terms)]
            page = self.page(rows, offset)
            page["records"] = [self.target(row) for row in page["records"]]
            return page
        raise ProfileError("Unknown editor request")

    def post(self, route: str, data: dict) -> dict:
        if route == "/api/load":
            self.draft = self.checked(read_json(self.path(data["id"])))
        elif route == "/api/new":
            profile = new_profile(self.catalog, self.new_id(), data.get("name") or "My next adventure")
            self.draft = {"profile": profile, "plan": new_binding_plan(profile, self.catalog, self.world)}
        elif route == "/api/save":
            draft = self.checked(data)
            if data.get("copy") is True:
                draft["profile"]["id"] = self.new_id()
            write_json(self.path(draft["profile"]["id"]), draft)
            self.draft = draft
        elif route == "/api/validate":
            return self.check(self.checked(data))
        elif route == "/api/prepare":
            draft = self.checked(data)
            result = self.check(draft)
            if not result["runtime_supported"]:
                raise ProfileError(result["message"])
            store = ensure_output_outside_game(self.workspace / "prepared", self.catalog)
            prepared = write_campaign(draft["profile"], self.catalog, self.world, draft["plan"], store)
            return {"revision": prepared["revision"], "store": str(store),
                    "message": "Test settings prepared. They can now be exported for the test game; "
                               "nothing was installed or activated."}
        else:
            raise ProfileError("Unknown editor request")
        return self.state()


def create_server(editor: Editor, port: int = 0) -> HTTPServer:
    assets = Path(__file__).with_name("ui")
    static = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
              "/app.js": ("app.js", "text/javascript")}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status: int, value: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(value)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(value)

        def dispatch(self, post: bool = False) -> None:
            try:
                address = f"127.0.0.1:{self.server.server_port}"
                if self.headers.get("Host") != address:
                    raise ProfileError("Use the editor's local address")
                parsed = urlsplit(self.path)
                if not post and parsed.path in static:
                    name, kind = static[parsed.path]
                    self.reply(200, (assets / name).read_bytes(), kind)
                    return
                if post:
                    if (self.headers.get_content_type() != "application/json" or
                            self.headers.get("Origin") not in (None, "http://" + address)):
                        raise ProfileError("Use the local editor to change a configuration")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2_000_000:
                        raise ProfileError("Invalid configuration request size")
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ProfileError("Expected a configuration object")
                    value = editor.post(parsed.path, data)
                else:
                    params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                    value = editor.get(parsed.path, params)
                self.reply(200, json.dumps({"ok": True, "result": value}, ensure_ascii=True).encode())
            except (ProfileError, OSError, ValueError, KeyError, TypeError) as exc:
                self.reply(400, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True).encode())

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(post=True)

    return HTTPServer(("127.0.0.1", port), Handler)


def serve(catalog: Path, world: Path, workspace: Path, port: int = 0,
          profile: Path | None = None, plan: Path | None = None) -> dict:
    editor = Editor(read_json(catalog), read_json(world), workspace,
                    read_json(profile) if profile else None, read_json(plan) if plan else None)
    with create_server(editor, port) as server:
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}",
                          "workspace": str(editor.workspace)}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return {"state": "editor-closed"}
