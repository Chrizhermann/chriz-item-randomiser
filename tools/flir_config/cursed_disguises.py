"""Finite component-570 ITM views; these assets never replace item resrefs.

The native caller selects one ordered face per source before equipment loads,
using the saved campaign seed, and restores pristine definitions for other
campaigns. Every copy of each original item shares that campaign's choice.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
import re

from .original_options import cursed_disguise_choices, patch_cursed_disguise
from .profiles import HEX256, ProfileError
from .quest_options import _texts
from .game_text import _append_texts

ALGORITHM = "park-miller-cursed-v1"
SEED_RANGE = 2147483646


def item_revisions_installed(weidu_log: bytes) -> bool:
    """Match MOD_IS_INSTALLED item_rev.tp2 0, independent of its path/language."""
    return any(path.replace(b"\\", b"/").rsplit(b"/", 1)[-1].lower() == b"item_rev.tp2"
               and int(component) == 0
               for path, component in re.findall(
                   rb"(?im)^\s*~([^~\r\n]+)~\s+#\d+\s+#(\d+)\b", weidu_log))


def cursed_disguise_spec(item_hashes: Mapping[str, str], *, game: str = "eet",
                         item_revisions: bool = False) -> dict:
    """Freeze the installed original sources, authored faces and donor hashes.

    Use the whole catalogue, irrespective of profile item selection. Missing
    optional donors retain their original fallback die faces and probabilities.
    """
    choices = cursed_disguise_choices(item_hashes, game=game, item_revisions=item_revisions)
    names = {row["source"] for row in choices}
    names.update(face["donor"] for row in choices for face in row["variants"])
    hashes = {name: item_hashes[name] for name in sorted(names)}
    if any(not isinstance(value, str) or not HEX256.fullmatch(value) for value in hashes.values()):
        raise ProfileError("Invalid cursed-disguise item fingerprint")
    return {"game": game, "item_revisions": item_revisions,
            "choices": choices, "resource_hashes": hashes}


def validate_cursed_disguise_spec(spec: dict) -> None:
    if (not isinstance(spec, dict) or set(spec) != {
            "game", "item_revisions", "choices", "resource_hashes"}
            or not isinstance(spec["resource_hashes"], dict)):
        raise ProfileError("Invalid cursed-disguise metadata")
    if spec != cursed_disguise_spec(spec["resource_hashes"], game=spec["game"],
                                   item_revisions=spec["item_revisions"]):
        raise ProfileError("Cursed-disguise metadata differs from original component 570")


def cursed_disguise_indices(spec: dict, seed: int) -> dict[str, int]:
    """Reconstruct one-based face indices without consuming placement randomness.

    The native equivalent uses 64-bit arithmetic. The largest multiplication
    also stays exact in Lua 5.1/JIT doubles. Rejection preserves uniform faces.
    The fixed component-number salt separates this stream from item placement.
    """
    validate_cursed_disguise_spec(spec)
    if type(seed) is not int or not 1 <= seed <= SEED_RANGE:
        raise ProfileError("Invalid cursed-disguise campaign seed")
    state = (seed - 1 + 570) % SEED_RANGE + 1
    result = {}
    for row in spec["choices"]:
        count = len(row["variants"])
        cutoff = SEED_RANGE - SEED_RANGE % count
        while True:
            state = state * 16807 % 2147483647
            if state - 1 < cutoff:
                break
        result[row["source"]] = (state - 1) % count + 1
    return result


def prepare_cursed_disguises(spec: dict, resources: Mapping[str, bytes], tlk: bytes, *,
                            language: str = "en_us") -> tuple[dict, dict[str, bytes], bytes]:
    """Materialise immutable original and face assets, plus exact authored text.

    Resource keys are lowercase resrefs. The caller prefixes returned filenames
    with the revision in its private asset directory, never the game override.
    The native fixture retains all faces, including duplicate fallback faces.
    """
    validate_cursed_disguise_spec(spec)
    if language.casefold() not in {"en_us", "en_gb", "english"}:
        raise ProfileError("Original cursed-disguise text currently requires English")
    for name, expected in spec["resource_hashes"].items():
        if name not in resources or hashlib.sha256(resources[name]).hexdigest() != expected:
            raise ProfileError(f"Cursed-disguise resource changed; rescan before export: {name}")
    # cir.tp2's ALWAYS block loads these EE overrides after the general strings
    # on both bgee and bg2ee engines (including EET); use their exact CRLF text.
    texts = _texts("languages/english/in-game.tra")
    texts.update(_texts("languages/bgee/english/in-game.tra"))
    ids = sorted({face[key] for row in spec["choices"] for face in row["variants"]
                  for key in ("description_tra", "name_tra") if face[key] is not None})
    tlk, allocated = _append_texts(tlk, tuple(texts[key] for key in ids))
    refs = dict(zip(ids, allocated))
    fixture, files = {"algorithm": ALGORITHM, "items": []}, {}
    for row in spec["choices"]:
        source = row["source"]
        original = resources[source]
        files[source + ".original.itm"] = original
        item = {"resref": source, "sha256": spec["resource_hashes"][source],
                "size": len(original), "variants": []}
        for index, face in enumerate(row["variants"], 1):
            patched = patch_cursed_disguise(original, resources[face["donor"]],
                        description_strref=refs.get(face["description_tra"]),
                        plate_name_strref=refs.get(face["name_tra"]))
            files[f"{source}.{index}.itm"] = patched
            item["variants"].append({"donor": face["donor"],
                                     "sha256": hashlib.sha256(patched).hexdigest()})
        fixture["items"].append(item)
    return fixture, files, tlk
