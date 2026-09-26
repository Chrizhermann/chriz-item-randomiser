"""Check a bounded, offline creature death-drop trace against two world captures.

This checks supplied evidence only. It does not establish an automatic runtime receipt.
"""

import argparse
import json
import re
import sys
from pathlib import Path


ITEM_OWNERS = {"flpri1": "flprc2", "flpri2": "flprc1",
               "flpri3": "flprc1", "flprx1": "flprc2"}
ITEM_SLOTS = {"flpri1": 19, "flpri2": 18, "flpri3": 19, "flprx1": 18}
PAYLOAD = ("charge1", "charge2", "charge3", "expiration", "flags")
EXPECTED_PAYLOAD = (0, 0, 0, 0, 1)
EVENT_FIELDS = {"sequence", "kind", "a", "b", "c", "d", "resref", *PAYLOAD}
POINTER = re.compile(r"[1-9][0-9]*\Z")


class TraceError(ValueError):
    """The evidence is malformed, incomplete, or inconsistent."""


def require(condition, message):
    if not condition:
        raise TraceError(message)


def pointer(value, label):
    require(isinstance(value, str) and POINTER.fullmatch(value),
            f"{label}: expected a nonzero decimal pointer string")
    return value


def integer(value, label):
    require(type(value) is int, f"{label}: expected an integer")
    return value


def payload(row, label):
    result = tuple(integer(row.get(key), f"{label}.{key}") for key in PAYLOAD)
    require(result == EXPECTED_PAYLOAD,
            f"{label}: payload differs from the four-item fixture")
    return result


def read_remote(path):
    try:
        wrapper = json.loads(Path(path).read_text(encoding="utf-8"))
        require(type(wrapper) is dict, f"{path}: wrapper is not an object")
        require(type(wrapper.get("returncode")) is int and wrapper["returncode"] == 0,
                f"{path}: remote call failed")
        require(wrapper.get("stderr") == "", f"{path}: remote stderr is not empty")
        stdout = wrapper.get("stdout")
        require(isinstance(stdout, str), f"{path}: stdout is missing")
        response = json.loads(stdout)
        require(type(response) is dict and response.get("status") == "ok",
                f"{path}: remote response is not ok")
        has_result = "result" in response
        has_values = "returnValues" in response
        require(has_result != has_values, f"{path}: ambiguous or absent remote result")
        if has_values:
            values = response["returnValues"]
            require(type(values) is list and len(values) == 1,
                    f"{path}: expected one remote return value")
            result = values[0]
        else:
            result = response["result"]
        require(type(result) is dict, f"{path}: result is not an object")
        return result
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TraceError(f"{path}: unreadable or truncated remote capture: {exc}") from exc


def world(result, label, alive):
    require(type(result.get("actors")) is list and len(result["actors"]) == 2,
            f"{label}: expected exactly two actors")
    actors = {}
    for actor in result["actors"]:
        require(type(actor) is dict, f"{label}: malformed actor")
        name = actor.get("name")
        require(isinstance(name, str) and name in {"flprc1", "flprc2"} and name not in actors,
                f"{label}: unexpected or duplicate cow")
        require(type(actor.get("alive")) is bool and actor["alive"] is alive,
                f"{label}: wrong cow life state")
        pointer(actor.get("pointer"), f"{label}.{name}.pointer")
        integer(actor.get("id"), f"{label}.{name}.id")
        actors[name] = actor
    require(set(actors) == {"flprc1", "flprc2"}, f"{label}: missing cow")
    require(len({actor["pointer"] for actor in actors.values()}) == 2 and
            len({actor["id"] for actor in actors.values()}) == 2,
            f"{label}: cow identities overlap")

    items = result.get("items")
    require(type(items) is list and len(items) == 4,
            f"{label}: expected exactly four observed items")
    indexed = {}
    item_pointers = set()
    container_ids = {}
    id_containers = {}
    for item in items:
        require(type(item) is dict, f"{label}: malformed item")
        ref = item.get("resref")
        require(isinstance(ref, str) and ref in ITEM_OWNERS and ref not in indexed,
                f"{label}: unexpected or duplicate matching item")
        item_pointer = pointer(item.get("pointer"), f"{label}.{ref}.pointer")
        require(item_pointer not in item_pointers, f"{label}: duplicate item pointer")
        item_pointers.add(item_pointer)
        pointer(item.get("owner_pointer"), f"{label}.{ref}.owner_pointer")
        payload(item, f"{label}.{ref}")
        owner = ITEM_OWNERS[ref]
        if alive:
            require(item.get("kind") == "actor" and item.get("owner") == owner,
                    f"{label}.{ref}: wrong living owner")
            require(item.get("owner_id") == actors[owner]["id"] and
                    item["owner_pointer"] == actors[owner]["pointer"],
                    f"{label}.{ref}: owner identity differs from cow")
            require(integer(item.get("slot"), f"{label}.{ref}.slot") == ITEM_SLOTS[ref],
                    f"{label}.{ref}: unexpected fixture slot")
        else:
            require(item.get("kind") == "container" and item.get("container_type") == 4,
                    f"{label}.{ref}: item is not in a type-4 ground container")
            container_id = integer(item.get("owner_id"), f"{label}.{ref}.owner_id")
            container_ptr = item["owner_pointer"]
            require(container_ids.setdefault(container_ptr, container_id) == container_id and
                    id_containers.setdefault(container_id, container_ptr) == container_ptr,
                    f"{label}.{ref}: container pointer/id mismatch")
        indexed[ref] = item
    require(set(indexed) == set(ITEM_OWNERS), f"{label}: missing tracked item")
    return actors, indexed


def verify(before, trace, after):
    before_actors, before_items = world(before, "before", True)
    after_actors, after_items = world(after, "after", False)
    for name in before_actors:
        require((before_actors[name]["id"], before_actors[name]["pointer"]) ==
                (after_actors[name]["id"], after_actors[name]["pointer"]),
                f"{name}: actor identity changed across death")
    for field in ("seed", "seeds", "assignments", "journal", "host"):
        require(field in before and field in after and before[field] == after[field],
                f"{field}: campaign state changed across death")
    if "globals" in before or "globals" in after:
        require("globals" in before and before["globals"] == after.get("globals"),
                "globals: changed across death")
    integer(before["seed"], "seed")
    integer(before["seeds"], "seeds")
    # A loaded held save allocates no new seed in this process. A freshly made
    # fixture allocates one. Both must remain unchanged throughout the trace.
    require(before["seeds"] in (0, 1) and 1 <= before["seed"] <= 2147483646 and before["journal"] == 0 and
            before["host"] == "complete" and type(before["assignments"]) is list and
            len(before["assignments"]) == 3,
            "campaign state is not the held four-item fixture")
    units = set()
    for assignment in before["assignments"]:
        require(type(assignment) is dict, "malformed assignment")
        unit = integer(assignment.get("unit"), "assignment.unit")
        require(unit in (1, 2, 3) and unit not in units and
                assignment.get("source_phase") == 2 and assignment.get("delivery_phase") == 4 and
                assignment.get("source_id") == f"cre:flprs{unit}/item/0" and
                assignment.get("target_id") == ("are:bg2600/actor/46" if unit == 1 else "are:bg2600/actor/45"),
                "assignment is not a unique held reward in this saved fixture")
        units.add(unit)
    require(integer(after.get("tick"), "after.tick") > integer(before.get("tick"), "before.tick"),
            "world observation did not cross a later game tick")

    require(trace.get("schema") == "flpr-drop-trace-v1", "wrong trace schema")
    require(trace.get("overflow") is False and trace.get("armed") is False,
            "trace overflowed or remained armed")
    require(trace.get("stable") is True, "trace snapshot raced an active writer")
    events = trace.get("events")
    require(type(events) is list and len(events) == 12,
            "expected exactly twelve drop events")
    by_original = {item["pointer"]: ref for ref, item in before_items.items()}
    by_message = {}
    stages = {}
    completed = {}
    for sequence, event in enumerate(events, 1):
        require(type(event) is dict and set(event) == EVENT_FIELDS,
                f"event {sequence}: malformed event fields")
        require(integer(event["sequence"], "sequence") == sequence,
                f"event {sequence}: non-contiguous sequence")
        kind = integer(event["kind"], f"event {sequence}.kind")
        require(kind in (1, 2, 3), f"event {sequence}: unexpected kind")
        for key in ("a", "b", "c"):
            pointer(event[key], f"event {sequence}.{key}")
        slot = integer(event["d"], f"event {sequence}.d")
        ref = event["resref"]
        require(isinstance(ref, str) and ref in ITEM_OWNERS,
                f"event {sequence}: unrelated item")
        payload(event, f"event {sequence}")
        if kind == 1:
            require(ref not in stages, f"{ref}: duplicate first stage")
            require(event["b"] == before_items[ref]["pointer"] and
                    event["a"] == before_items[ref]["owner_pointer"] and
                    slot == before_items[ref]["slot"],
                    f"{ref}: wrong original owner, pointer, or slot")
            require(event["c"] == after_items[ref]["owner_pointer"],
                    f"{ref}: wrong ground container")
            stages[ref] = {"stage": 1, "container": event["c"], "first_sequence": sequence}
        elif kind == 2:
            require(event["a"] in by_original and by_original[event["a"]] == ref and
                    ref in stages and stages[ref]["stage"] == 1,
                    f"{ref}: message constructor lacks its original")
            require(event["c"] == stages[ref]["container"] and slot == 0,
                    f"{ref}: message constructor changed container or slot")
            require(event["b"] not in by_message,
                    f"{ref}: message pointer reused while a chain is open")
            by_message[event["b"]] = ref
            stages[ref]["stage"] = 2
            stages[ref]["message"] = event["b"]
            stages[ref]["second_sequence"] = sequence
        else:
            require(event["a"] in by_message and by_message[event["a"]] == ref and
                    ref in stages and stages[ref]["stage"] == 2,
                    f"{ref}: SetItem lacks its message constructor")
            require(event["c"] == stages[ref]["container"] and slot == 0 and
                    event["b"] == after_items[ref]["pointer"],
                    f"{ref}: SetItem changed container or new item pointer")
            del by_message[event["a"]]
            stages[ref]["stage"] = 3
            completed[ref] = {"resref": ref, "owner_pointer": before_items[ref]["owner_pointer"],
                              "before_slot": before_items[ref]["slot"],
                              "original_item_pointer": before_items[ref]["pointer"],
                              "message_pointer": event["a"], "new_item_pointer": event["b"],
                              "container_pointer": event["c"],
                              "sequences": [stages[ref]["first_sequence"],
                                            stages[ref]["second_sequence"], sequence]}
    require(not by_message and set(completed) == set(ITEM_OWNERS),
            "missing or unfinished drop chain")
    return {"status": "offline-trace-consistent", "schema": "flpr-drop-trace-verification-v1",
            "tracked_items": sorted(completed), "chains": [completed[ref] for ref in sorted(completed)],
            "events_checked": len(events),
            "automatic_runtime_receipt_proven": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "trace", "after", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(read_remote(args.before), read_remote(args.trace),
                        read_remote(args.after))
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    except (TraceError, OSError) as exc:
        parser.exit(1, f"drop trace rejected: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
