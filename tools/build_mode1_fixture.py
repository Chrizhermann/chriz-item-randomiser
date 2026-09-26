"""Build a disposable BG1/EET engine fixture without installing it.

Reads only explicitly named override resources; never reads or writes saves.
The output is not a general installation scan or a supported content profile.
Variant "ground-piles" is the accepted fixture; "creatures" gives the same three
rewards to two disposable, killable recipients for native creature acceptance.
"quiet-creatures" retains those fixture actors while suppressing ambient actors,
area scripting, spawn points, and rest encounters for diagnostic save tests.
Binary layouts: IESDP gam_v2.0.htm, are_v1.htm, cre_v1.htm, itm_v1.htm.
Native accessibility and source loading must still be checked in the engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from copy import deepcopy
from pathlib import Path

from flir_config.campaigns import (CAMPAIGN_SCHEMA, POINTER_SCHEMA,
    RUNTIME_REQUIREMENTS, STATE, _render_lua, _validate_campaign)
from flir_config.profiles import _digest, canonical_bytes
from flir_config.world_formats import parse_are, parse_cre

AREA = "bg2600"
ITEM_BASES = ("sw1h01", "ax1h01", "blun04")
SOURCE_POSITIONS = ((3349, 339), (3389, 339), (3429, 339))
TARGET_POSITIONS = ((1140, 650), (1200, 650), (1260, 650))
# "ground-piles" is the accepted fixture and must stay byte-identical.
# "creatures" gives the rewards to two disposable, killable actors instead:
# FLPRC1 holds two (empty inventory) and FLPRC2 one (beside an unrelated
# disposable FLPRX1 that must never change). Capacities are fixture-only.
VARIANTS = ("ground-piles", "creatures", "quiet-creatures")
CREATURE_VARIANTS = ("creatures", "quiet-creatures")
# Recipients stand on accepted pile positions 1 and 3, which the party reached.
CREATURE_TARGETS = (("FLPRC1", TARGET_POSITIONS[0], 2), ("FLPRC2", TARGET_POSITIONS[2], 1))
UNRELATED_ITEM, UNRELATED_BASE, UNRELATED_OWNER = "FLPRX1", ITEM_BASES[0], "FLPRC2"
CREATURE_READY_DETAIL = "have been given to the two test cows near the Candlekeep entrance."
FIXTURE_RESOURCES = (*(f"FLPRS{index}.CRE" for index in range(1, 4)),
    *(f"FLPRI{index}.ITM" for index in range(1, 4)),
    *(name + ".CRE" for name, _, _ in CREATURE_TARGETS), UNRELATED_ITEM + ".ITM")


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mark_initial_gam(original: bytes) -> bytes:
    if len(original) < 0xB4 or original[:8] != b"GAMEV2.0":
        raise ValueError("Fixture requires a complete GAMEV2.0 initial GAM")
    offset, count = u32(original, 0x38), u32(original, 0x3C)
    if count and (offset < 0xB4 or offset + count * 84 > len(original)):
        raise ValueError("Invalid original GAM globals")
    records = original[offset:offset + count * 84]
    for index in range(count):
        name = records[index * 84:index * 84 + 32].split(b"\0", 1)[0]
        if name.upper() == b"FLPR_FRESH":
            raise ValueError("Initial GAM already contains the fixture marker")
    marker = bytearray(84)
    struct.pack_into("<32s", marker, 0, b"FLPR_FRESH")
    struct.pack_into("<H", marker, 0x20, 1)
    struct.pack_into("<i", marker, 0x28, 1)
    result = bytearray(original)
    struct.pack_into("<II", result, 0x38, len(result), count + 1)
    result.extend(records + marker)
    return bytes(result)


def item_payload(resref: str, data: bytes) -> dict:
    if len(data) < 0x72 or data[:8] != b"ITM V1  ":
        raise ValueError("Fixture item must be ITM V1")
    flags = u32(data, 0x18)
    if flags & 0xC != 0xC or u16(data, 0x38) > 1 or u32(data, 0x60) != 0:
        raise ValueError("Fixture item must be mundane, nonstackable and droppable/displayable")
    offset, count = u32(data, 0x64), u16(data, 0x68)
    if not count or offset < 0x72 or offset + count * 0x38 > len(data):
        raise ValueError("Invalid fixture item abilities")
    charges = [u16(data, offset + index * 0x38 + 0x22) for index in range(min(count, 3))]
    # The engine identifies these ordinary weapons while loading a creature.
    # Freeze that expected bit in the original record and manifest, rather
    # than weakening the native adapter's exact-payload comparison.
    return {"resref": resref, "expiration": 0,
            "charges": charges + [0] * (3 - len(charges)), "flags": 1}


def _fixture_cre(original: bytes, script_name: str, payload: dict | None) -> bytes:
    parse_cre(original)
    result = bytearray(original)
    result[0x248:0x270] = b"\0" * 40  # Five creature scripts.
    result[0x2CC:0x2D4] = b"\0" * 8  # Dialog.
    struct.pack_into("<32s", result, 0x280, script_name.encode("ascii"))
    # No XP, gold, permanent states, spells or effects on fixture carriers.
    result[0x14:0x24] = b"\0" * 16
    for offset in (0x2A0, 0x2A8, 0x2B0, 0x2C4):
        struct.pack_into("<II", result, offset, 0, 0)
    result[0x270] = 128  # Neutral; no actions are scripted.
    item_offset = len(result)
    if payload is not None:
        result.extend(struct.pack("<8s4HI", payload["resref"].encode("ascii"),
            payload["expiration"], *payload["charges"], payload["flags"]))
    slots_offset = len(result)
    slots = [0xFFFF] * 38 + [1000, 0]
    if payload is not None:
        slots[21] = 0  # First ordinary inventory slot; never equipped.
    result.extend(struct.pack("<40H", *slots))
    struct.pack_into("<III", result, 0x2B8, slots_offset, item_offset, int(payload is not None))
    parsed = parse_cre(result)
    expected = [] if payload is None else [[21]]
    assert [item["slots"] for item in parsed["items"]] == expected
    return bytes(result)


def make_source_cre(original: bytes, script_name: str, payload: dict) -> bytes:
    return _fixture_cre(original, script_name, payload)


def make_target_cre(original: bytes, script_name: str, payload: dict | None = None) -> bytes:
    """A disposable reward recipient; an optional payload is unrelated inventory."""
    return _fixture_cre(original, script_name, payload)


def _actor(name: str, x: int, y: int, flags: int) -> bytes:
    actor = bytearray(0x110)
    struct.pack_into("<32s4HI", actor, 0, name.encode("ascii"), x, y, x, y, flags)
    struct.pack_into("<i", actor, 0x38, -1)
    struct.pack_into("<I", actor, 0x40, 0xFFFFFF)
    struct.pack_into("<8s", actor, 0x80, name.encode("ascii"))
    return bytes(actor)


def add_area_fixture(original: bytes, variant: str = "ground-piles") -> bytes:
    if variant not in VARIANTS:
        raise ValueError("Unknown fixture variant")
    parsed = parse_are(original)
    if any(row["name"].upper().startswith("FLPR") for key in ("actors", "containers")
           for row in parsed[key]):
        raise ValueError("Area already contains fixture records")
    # Actor flags 5: external CRE, invulnerable, so sources survive until
    # suppressed. Flags 1: external CRE only, so death tests can kill recipients.
    actors = [_actor(f"FLPRS{index}", x, y, 5) for index, (x, y) in enumerate(SOURCE_POSITIONS, 1)]
    if variant in CREATURE_VARIANTS:
        actors += [_actor(name, x, y, 1) for name, (x, y), _ in CREATURE_TARGETS]
    piles = TARGET_POSITIONS if variant == "ground-piles" else ()
    result = bytearray(original)
    actor_count, container_count = len(parsed["actors"]), len(parsed["containers"])
    if variant == "quiet-creatures":
        # Point at a new actor table containing only fixture records. The old
        # table remains inert raw bytes so all other ARE section offsets hold.
        actor_count = 0
        result[0x94:0x9C] = bytes(8)  # Disable the area's ambient script.
        struct.pack_into("<I", result, 0x64, 0)  # No spawn points are active.
        rest_offset = u32(original, 0xC0)
        if rest_offset:
            result[rest_offset:rest_offset + 0xE4] = bytes(0xE4)
    if actor_count + len(actors) > 0xFFFF or container_count + len(piles) > 0xFFFF:
        raise ValueError("Area fixture exceeds record count limits")
    # Append the active actor table at EOF: embedded offsets stay absolute.
    # The quiet variant leaves the old actor table inert and omits its records.
    actor_offset = len(result)
    result.extend(original[u32(original, 0x54):u32(original, 0x54) + actor_count * 0x110])
    for actor in actors:
        result.extend(actor)
    struct.pack_into("<IH", result, 0x54, actor_offset, actor_count + len(actors))
    if piles:
        container_offset = len(result)
        result.extend(original[u32(original, 0x70):u32(original, 0x70) + container_count * 0xC0])
        for index, (x, y) in enumerate(piles, 1):
            container = bytearray(0xC0)
            struct.pack_into("<32s3H", container, 0, f"FLPRT{index}".encode("ascii"), x, y, 4)
            # Ground piles use location/bounding box; no polygon, owner, lock or trap.
            struct.pack_into("<4H", container, 0x38, x - 12, y - 8, x + 12, y + 8)
            struct.pack_into("<I", container, 0x84, 0xFFFFFFFF)
            result.extend(container)
        struct.pack_into("<IH", result, 0x70, container_offset, container_count + len(piles))
    after = parse_are(result)
    assert len(after["actors"]) == actor_count + len(actors)
    assert len(after["containers"]) == container_count + len(piles)
    return bytes(result)


def _creature_targets(area: dict, hashes: dict[str, str], manifest: dict, native: dict) -> None:
    """Bind each recipient to its exact ARE actor record and external CRE."""
    for name, _, capacity in CREATURE_TARGETS:
        rows = [row for row in area["actors"] if row["name"] == name]
        if len(rows) != 1 or rows[0]["cre_resref"] != name.lower() or rows[0]["embedded_cre_size"]:
            raise ValueError(f"Fixture area must contain exactly one external-CRE actor {name}")
        actor = rows[0]
        identity = f"are:{AREA}/actor/{actor['record_index']}"
        # Same record shape as the post-install world scan's actor targets.
        selector = {key: deepcopy(value) for key, value in actor.items() if key != "embedded_cre"}
        selector["template_resource"] = "cre:" + name.lower()
        target = {"id": identity, "resource": "are:" + AREA, "kind": "actor", "selector": selector,
            "concerns": ["actor-availability", "external-cre-template", "lootability-unverified"]}
        manifest["targets"].append({"id": identity, "capacity": capacity, "record": target,
                                    "resource_sha256": hashes["BG2600.ARE"]})
        native["targets"][identity] = {"kind": "creature", "area": AREA.upper(), "script_name": name,
            "identity": _digest({"target": target, "resource_sha256": hashes["BG2600.ARE"],
                                 "template_sha256": hashes[name + ".CRE"]})}


def compile_fixture(resources: dict[str, bytes], inputs: dict[str, str],
                    variant: str = "ground-piles") -> tuple[dict, dict]:
    """Validate an exact fixture artifact; no claim to a complete game scan."""
    if variant not in VARIANTS:
        raise ValueError("Unknown fixture variant")
    area = parse_are(resources["BG2600.ARE"])
    hashes = {key: digest(value) for key, value in sorted(resources.items())}
    context = {"scope": "disposable-bg1-fixture-only", "inputs": inputs, "outputs": hashes}
    manifest = {"schema": CAMPAIGN_SCHEMA, "state": STATE,
        "bound_revision": _digest(context), "profile_id": "native-mode1-fixture",
        "world_fingerprint": _digest({"fixture_resources": hashes}),
        "sources": [], "targets": [], "runtime_requirements": deepcopy(RUNTIME_REQUIREMENTS)}
    native = {"schema": "flir-native-fixture-v1", "area": AREA.upper(),
        "fresh_global": "FLPR_FRESH", "sources": {}, "targets": {},
        "resource_hashes": hashes, "input_hashes": inputs}
    if variant in CREATURE_VARIANTS:
        # Added only here: the accepted ground-pile artifacts must stay byte-identical.
        manifest["profile_id"] = ("native-mode1-quiet-creature-fixture"
            if variant == "quiet-creatures" else "native-mode1-creature-fixture")
        cre = parse_cre(resources[UNRELATED_OWNER + ".CRE"])
        native.update({"variant": variant, "ready_detail": CREATURE_READY_DETAIL,
            "unrelated_items": {UNRELATED_OWNER: [{key: deepcopy(record[key])
                for key in ("resref", "expiration", "charges", "flags")} for record in cre["items"]]}})
    for index in range(1, 4):
        resref = f"flprs{index}"
        cre = parse_cre(resources[resref.upper() + ".CRE"])
        record = cre["items"][0]
        identity = f"cre:{resref}/item/0"
        source = {"id": identity, "item": record["resref"], "resource": "cre:" + resref,
            "owner": "cre:" + resref, "kind": "cre-template", "record": record,
            "concerns": ["template-instance-scope"]}
        source_hash = hashes[resref.upper() + ".CRE"]
        manifest["sources"].append({"id": identity, "quantity": 1, "record": source,
                                    "resource_sha256": source_hash})
        native["sources"][identity] = {"area": AREA.upper(), "script_name": resref.upper(),
            "payload": {key: deepcopy(record[key]) for key in ("resref", "expiration", "charges", "flags")},
            "identity": _digest({"source": source, "resource_sha256": source_hash})}
    piles = area["containers"][-3:] if variant == "ground-piles" else []
    for container in piles:
        identity = f"are:{AREA}/container/{container['record_index']}"
        selector = {key: value for key, value in container.items() if key != "items"}
        target = {"id": identity, "resource": "are:" + AREA, "kind": "container",
            "selector": selector, "concerns": ["container-access-unverified"]}
        manifest["targets"].append({"id": identity, "capacity": 1, "record": target,
                                    "resource_sha256": hashes["BG2600.ARE"]})
        native["targets"][identity] = {"area": AREA.upper(), "script_name": container["name"],
            "x": container["x"], "y": container["y"],
            "identity": _digest({"target": target, "resource_sha256": hashes["BG2600.ARE"]})}
    if variant in CREATURE_VARIANTS:
        _creature_targets(area, hashes, manifest, native)
    for key in ("sources", "targets"):
        manifest[key].sort(key=lambda row: row["id"])
    manifest["revision"] = _digest(manifest)
    _validate_campaign(manifest)
    native["revision"] = manifest["revision"]
    return manifest, native


def build(game: Path, output: Path, variant: str = "ground-piles") -> dict:
    if variant not in VARIANTS:
        raise ValueError("Unknown fixture variant")
    game, output = game.resolve(strict=True), output.resolve()
    if output.is_relative_to(game) or game.is_relative_to(output):
        raise ValueError("Fixture output must be outside, and not contain, the game directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Fixture output must be absent or empty; never overwrite an existing payload")
    override = game / "override"
    originals = {}
    for name in ("BALDUR.GAM", "BG2600.ARE", "COW.CRE", *(name.upper() + ".ITM" for name in ITEM_BASES)):
        path = override / name
        if not path.resolve(strict=True).is_relative_to(game):
            raise ValueError("Input resource escapes the selected game")
        originals[name] = path.read_bytes()
    # This fixture deliberately supports this BG1 EET start only.
    start = (override / "BGSTARTA.2DA").read_text(encoding="ascii")
    rows = [line.split() for line in start.splitlines()]
    if [row[1].upper() for row in rows if len(row) >= 2 and row[0] == "START_AREA"] != ["BG2600"]:
        raise ValueError("Fixture requires the BG2600 BG1 campaign start")
    # Either variant needs pristine inputs, never an earlier fixture's output.
    if any((override / name).exists() for name in FIXTURE_RESOURCES):
        raise ValueError("Selected game already contains fixture resources")
    resources = {"BALDUR.GAM": mark_initial_gam(originals["BALDUR.GAM"]),
                 "BG2600.ARE": add_area_fixture(originals["BG2600.ARE"], variant)}
    for index, base in enumerate(ITEM_BASES, 1):
        item = originals[base.upper() + ".ITM"]
        resources[f"FLPRI{index}.ITM"] = item
        resources[f"FLPRS{index}.CRE"] = make_source_cre(originals["COW.CRE"], f"FLPRS{index}",
            item_payload(f"flpri{index}", item))
    if variant in CREATURE_VARIANTS:
        unrelated = originals[UNRELATED_BASE.upper() + ".ITM"]
        resources[UNRELATED_ITEM + ".ITM"] = unrelated
        for name, _, _ in CREATURE_TARGETS:
            resources[name + ".CRE"] = make_target_cre(originals["COW.CRE"], name,
                item_payload(UNRELATED_ITEM.lower(), unrelated) if name == UNRELATED_OWNER else None)
    inputs = {name: digest(data) for name, data in sorted(originals.items())}
    inputs["BGSTARTA.2DA"] = digest((override / "BGSTARTA.2DA").read_bytes())
    manifest, native = compile_fixture(resources, inputs, variant)
    # All in-memory binary and manifest checks pass before any output is made.
    (output / "override").mkdir(parents=True)
    for name, data in resources.items():
        (output / "override" / name).write_bytes(data)
    store = output / "campaign-store"
    (store / "revisions").mkdir(parents=True)
    revision = manifest["revision"]
    (store / "revisions" / (revision + ".json")).write_bytes(canonical_bytes(manifest))
    (store / "revisions" / (revision + ".lua")).write_bytes(_render_lua(manifest))
    (store / "prototype-next.json").write_bytes(canonical_bytes({"schema": POINTER_SCHEMA,
        "revision": revision}))
    (output / "fixture-native.json").write_bytes(canonical_bytes(native))
    (output / "fixture-native.lua").write_bytes(_render_lua(native))
    access = "pile access" if variant == "ground-piles" else "recipient spawning"
    limitations = ["Fixture-only resource fingerprint; not a full game scan",
        f"Initial GAM marker, source loading and {access} need live verification",
        "No save files read or changed; no game files written"]
    if variant in CREATURE_VARIANTS:
        limitations.insert(2, "Creature transport, final flags, death drop and held-item "
                              "save/reload are unproven until native acceptance")
    if variant == "quiet-creatures":
        limitations.insert(2, "Diagnostic area suppresses ambient actors, area script, "
                              "spawn points and rest encounters; engine behavior remains unverified")
    report = {"state": "staged-not-installed", "variant": variant, "game": str(game),
        "output": str(output), "revision": revision, "area": AREA,
        "source_ids": list(native["sources"]), "target_ids": list(native["targets"]),
        "inputs": inputs, "outputs": native["resource_hashes"], "limitations": limitations}
    (output / "fixture-report.json").write_bytes(canonical_bytes(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--variant", choices=VARIANTS, default="ground-piles",
                        help="ground-piles is accepted; creatures keeps ambient actors; "
                             "quiet-creatures isolates five synthetic actors")
    args = parser.parse_args()
    print(json.dumps(build(args.game, args.output, args.variant), indent=2))


if __name__ == "__main__":
    main()
