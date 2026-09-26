"""Focused offline checks for the four-item creature death-drop diagnostic."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_fixture_drop_trace import TraceError, read_remote, verify


REFS = ("flprx1", "flpri1", "flpri2", "flpri3")
OWNERS = ("flprc2", "flprc2", "flprc1", "flprc1")
SLOTS = (18, 19, 18, 19)
ACTOR_POINTERS = {"flprc2": "1610843200", "flprc1": "1610821808"}
ACTOR_IDS = {"flprc2": 136513570, "flprc1": 136448033}
CONTAINER_POINTERS = {"flprc2": "1700000002", "flprc1": "1700000001"}
CONTAINER_IDS = {"flprc2": 142600002, "flprc1": 142600001}
PAYLOAD = {"charge1": 0, "charge2": 0, "charge3": 0, "expiration": 0, "flags": 1}


def evidence():
    actors = [{"name": name, "pointer": ptr, "id": ACTOR_IDS[name], "alive": True}
              for name, ptr in ACTOR_POINTERS.items()]
    before = {"actors": actors, "items": [], "seed": 715864367, "seeds": 1,
              "tick": 100,
              "assignments": [{"unit": n, "source_phase": 2, "delivery_phase": 4,
                               "source_id": f"cre:flprs{n}/item/0",
                               "target_id": "are:bg2600/actor/46" if n == 1 else "are:bg2600/actor/45"}
                              for n in (1, 2, 3)],
              "journal": 0, "host": "complete", "globals": {"FLPR_SEED": 715864367}}
    after = copy.deepcopy(before)
    after["tick"] = 110
    for actor in after["actors"]:
        actor["alive"] = False
    after["items"] = []
    events = []
    for index, (ref, owner, slot) in enumerate(zip(REFS, OWNERS, SLOTS), 1):
        original = str(1600000000 + index)
        new = str(1800000000 + index)
        message = str(1900000000 + index)
        container = CONTAINER_POINTERS[owner]
        before["items"].append({"resref": ref, "pointer": original,
                                "owner_pointer": ACTOR_POINTERS[owner], "owner_id": ACTOR_IDS[owner],
                                "owner": owner, "slot": slot, "kind": "actor", **PAYLOAD})
        after["items"].append({"resref": ref, "pointer": new,
                               "owner_pointer": container, "owner_id": CONTAINER_IDS[owner],
                               "slot": index, "kind": "container", "container_type": 4, **PAYLOAD})
        events.extend([
            {"kind": 1, "a": ACTOR_POINTERS[owner], "b": original, "c": container,
             "d": slot, "resref": ref, **PAYLOAD},
            {"kind": 2, "a": original, "b": message, "c": container,
             "d": 0, "resref": ref, **PAYLOAD},
            {"kind": 3, "a": message, "b": new, "c": container,
             "d": 0, "resref": ref, **PAYLOAD},
        ])
    for index, event in enumerate(events, 1):
        event["sequence"] = index
    trace = {"schema": "flpr-drop-trace-v1", "overflow": False, "stable": True,
             "armed": False, "events": events}
    return before, trace, after


def wrapped(result, *, shape="result", returncode=0, stderr=""):
    response = {"status": "ok", shape: result if shape == "result" else [result]}
    return {"returncode": returncode, "stdout": json.dumps(response), "stderr": stderr}


class DropTraceTests(unittest.TestCase):
    def reject(self, change):
        before, trace, after = evidence()
        change(before, trace, after)
        with self.assertRaises(TraceError):
            verify(before, trace, after)

    def test_valid_chains_and_offline_scope(self):
        result = verify(*evidence())
        self.assertEqual(result["events_checked"], 12)
        self.assertEqual(result["tracked_items"], sorted(REFS))
        self.assertEqual(result["chains"][0]["sequences"], [4, 5, 6])
        self.assertFalse(result["automatic_runtime_receipt_proven"])

    def test_loaded_campaign_and_recycled_old_item_address(self):
        before, trace, after = evidence()
        before["seeds"] = after["seeds"] = 0
        # A later item's new allocation may reuse an earlier item's freed
        # address. Only the complete event chain establishes its identity.
        recycled = before["items"][0]["pointer"]
        trace["events"][5]["b"] = recycled
        after["items"][1]["pointer"] = recycled
        self.assertEqual(verify(before, trace, after)["events_checked"], 12)

    def test_rejects_unheld_assignment_or_missing_tick_boundary(self):
        def unheld(b, t, a):
            for capture in (b, a):
                capture["assignments"][0]["delivery_phase"] = 1
        self.reject(unheld)
        self.reject(lambda b, t, a: a.update(tick=b["tick"]))

    def test_rejects_missing_or_reordered_link(self):
        self.reject(lambda b, t, a: t["events"][1].update(a="1999999999"))
        self.reject(lambda b, t, a: t["events"][2].update(a="1999999999"))
        self.reject(lambda b, t, a: t["events"][2].update(sequence=4))
        self.reject(lambda b, t, a: t["events"].pop(2))

    def test_rejects_owner_slot_or_container_change(self):
        self.reject(lambda b, t, a: t["events"][0].update(a=ACTOR_POINTERS["flprc1"]))
        self.reject(lambda b, t, a: t["events"][0].update(d=20))
        self.reject(lambda b, t, a: t["events"][1].update(c=CONTAINER_POINTERS["flprc1"]))
        self.reject(lambda b, t, a: a["items"][0].update(owner_pointer="1700000999"))
        self.reject(lambda b, t, a: a["items"][1].update(owner_id=142699999))
        self.reject(lambda b, t, a: a["actors"][0].update(alive=True))

    def test_rejects_payload_change_matching_duplicate_overflow_and_unrelated_event(self):
        self.reject(lambda b, t, a: t["events"][2].update(charge1=1))
        self.reject(lambda b, t, a: a["items"][0].update(flags=3))
        self.reject(lambda b, t, a: b["items"].append(copy.deepcopy(b["items"][0])))
        self.reject(lambda b, t, a: t.update(overflow=True))
        self.reject(lambda b, t, a: t.update(armed=True))
        self.reject(lambda b, t, a: t.update(stable=False))
        self.reject(lambda b, t, a: t.pop("stable"))
        self.reject(lambda b, t, a: t["events"][0].update(resref="unrelated"))
        self.reject(lambda b, t, a: a.update(seed=42))

    def test_message_pointer_reuse_allowed_only_after_prior_chain_closes(self):
        before, trace, after = evidence()
        trace["events"][4]["b"] = trace["events"][1]["b"]
        trace["events"][5]["a"] = trace["events"][1]["b"]
        self.assertEqual(verify(before, trace, after)["events_checked"], 12)
        trace["events"][2:5] = [trace["events"][3], trace["events"][4], trace["events"][2]]
        for index, event in enumerate(trace["events"], 1):
            event["sequence"] = index
        with self.assertRaises(TraceError):
            verify(before, trace, after)

    def test_rejects_failed_or_truncated_remote_wrapper(self):
        before, _, _ = evidence()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.json"
            for data in (wrapped(before, returncode=1),
                         {"returncode": 0, "stdout": '{"status":"ok","result":', "stderr": ""},
                         wrapped(before, stderr="error")):
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(TraceError):
                        read_remote(path)

    def test_cli_accepts_observed_world_shape_and_never_overwrites(self):
        before, trace, after = evidence()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, value, shape in (("before", before, "returnValues"),
                                       ("trace", trace, "returnValues"),
                                       ("after", after, "returnValues")):
                (folder / f"{name}.json").write_text(json.dumps(wrapped(value, shape=shape)),
                                                      encoding="utf-8")
            output = folder / "summary.json"
            command = [sys.executable, str(ROOT / "tools" / "verify_fixture_drop_trace.py"),
                       "--before", str(folder / "before.json"), "--trace", str(folder / "trace.json"),
                       "--after", str(folder / "after.json"), "--output", str(output)]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            saved = output.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(output.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
