"""Offline drafts of the original Randomiser item and location pools.

``list_templates()`` describes the bundled presets: Item Randomiser's tables (BG1
and BG2), and preset list sets (``lists/presets/<name>/``) that replace a campaign's
tables.
Each preset has a standard and an extended pool of items and of places: the
standard pools read the ``base`` tables, the extended pools also the ``mode2``
tables (the larger lists of Item Randomiser's install-time Mode 2). Chriz Item
Randomiser has no modes; every pool is drawn for each new game.
``build_template()`` loads their checked-in WeiDU tables without changing the
game or flattening repeated rewards into the user-profile item set. The result
needs installation specific binding and remaining original-rule adapters before
it can be played.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "flir-original-template-draft-v1"
# "standard": the base tables; "extended": the base and the mode2 tables.
POOL_CHOICES = ("standard", "extended")
DEFAULT_OPTIONS = {
    "scrolls": False,             # component 530, tiers beginning with s
    "kangaxx": False,             # component 520, tier ka
    "flail_heads": False,         # component 540, tier fa
    "totsc_tome": False,          # component 500, ident b2c variant
    "more_spell_shield": False,   # component 560, independent store edit
    "cursed_disguise": False,     # component 570, independent item edit
    "loss_mode": "none",         # Item Randomiser components 1100 / 1200
    "loss_percent": 0,
    "unrandomized_percent": 0,   # component 510, source-bearing items only
}
# The default: Item Randomiser's lists with the standard pools, no items lost
# (component 1100), the Flail of Ages heads (540) and the randomised Tales of the
# Sword Coast tome (500); every other option off.
RECOMMENDED_OPTIONS = DEFAULT_OPTIONS | {"flail_heads": True, "totsc_tome": True}
# Preset campaigns: the tables of each part of the game a preset reads, in order.
# Item Randomiser has tables for BG1 and BG2. The tables of each part stay apart
# (tiers are per part), so a later option can mix them. Siege of Dragonspear's
# tables (table campaign "sod") come with Chris's design; the configurator reads
# them as soon as a preset campaign names them (docs/sod-lists.md).
CAMPAIGNS = (("bg1", "BG1"), ("bg2", "BG2"), ("eet", "EET"))
_PARTS = {"bg1": ("bg1",), "bg2": ("bg2",), "eet": ("bg1", "bg2")}
# Item Randomiser's tables have an authored endpoint for every row of their base
# location tables; other tables (SoD's) need none: a row's target names its kind.
ENDPOINT_CAMPAIGNS = frozenset({"bg1", "bg2"})
# Preset list sets in lists/presets/<name>/, in the original tables' format. A preset
# replaces the tables of the campaigns it covers; a template for more campaigns takes
# the other campaigns' own tables. Its rows are the original rows where they keep an
# original pairing ident, so source selectors, kept originals and the tome option
# work as for the original tables.
PRESETS = {
    "boss-loot": {
        "name": "Boss Loot", "author": "aShuN", "campaigns": ("bg1",),
        "description": "Powerful items at difficult boss fights, the ability tomes on the main path "
                       "and wizard items at mage enemies.",
        "extended_description": "The extended item pool adds shop stock, and Rogue Rebalancing "
                                "and IWDification items where those mods are installed.",
        # Items that only another mod adds. A game without the item leaves their rows
        # out and names the mod; any other absent item is left out with its reason.
        "optional_items": (("rr#", "Rogue Rebalancing"), ("cdia", "IWDification")),
    },
}


def recommended_preset(campaign: str) -> dict:
    """The default preset for a preset campaign (CAMPAIGNS)."""
    if campaign not in _PARTS:
        raise ValueError("Unknown campaign")
    return {"id": f"{campaign}-original", "items": "standard", "targets": "standard",
            "options": RECOMMENDED_OPTIONS.copy()}


def _parts(campaign: str) -> tuple[str, ...]:
    return _PARTS[campaign]


def campaign_tables(campaign: str) -> tuple[str, ...]:
    """The table campaigns ("bg1", "sod", "bg2") a preset campaign reads."""
    if campaign not in _PARTS:
        raise ValueError("Unknown campaign")
    return _parts(campaign)


def template_tables(template_id: str) -> tuple[str, ...]:
    """The table campaigns ("bg1", "sod", "bg2") a preset reads."""
    return _parts(template_campaign(template_id))


def _templates() -> dict[str, dict]:
    result = {}
    for campaign, label in CAMPAIGNS:
        result[f"{campaign}-original"] = {
            "name": f"{label}: Item Randomiser's lists",
            "description": "The item and location lists of Item Randomiser by Wisp, by tier.",
            "campaign": campaign, "preset": None}
    for preset, spec in PRESETS.items():
        for campaign, label in CAMPAIGNS:
            covered = [part.upper() for part in _parts(campaign) if part in spec["campaigns"]]
            others = [part.upper() for part in _parts(campaign) if part not in spec["campaigns"]]
            if not covered:
                continue
            description = spec["description"]
            if others:
                description += (f" For {', '.join(covered)}; {', '.join(others)} keeps "
                                "Item Randomiser's lists.")
            result[f"{campaign}-{preset}"] = {
                "name": f"{label}: {spec['name']} by {spec['author']}",
                "description": f"{description} {spec['extended_description']}",
                "campaign": campaign, "preset": preset}
    return result


def list_templates(campaigns: tuple[str, ...] | None = None) -> list[dict]:
    """Return stable, compact descriptors for the bundled presets.

    <campaigns> limits them to presets for those preset campaigns (CAMPAIGNS).
    """
    return [{"id": identity, "name": row["name"], "description": row["description"]}
            for identity, row in _templates().items()
            if campaigns is None or row["campaign"] in campaigns]


def template_campaign(template_id: str) -> str:
    """The preset campaign (CAMPAIGNS) a preset is for."""
    descriptors = _templates()
    if template_id not in descriptors:
        raise ValueError(f"Unknown original template: {template_id}")
    return descriptors[template_id]["campaign"]


def _choice(value: str | None, label: str) -> str:
    result = "standard" if value is None else value
    if result not in POOL_CHOICES:
        raise ValueError(f"{label} must be 'standard' or 'extended'")
    return result


def _options(value: dict | None) -> dict:
    if value is None:
        return DEFAULT_OPTIONS.copy()
    if not isinstance(value, dict) or set(value) - set(DEFAULT_OPTIONS):
        raise ValueError("Unknown original-template option")
    result = DEFAULT_OPTIONS | value
    for key in ("scrolls", "kangaxx", "flail_heads", "totsc_tome",
                "more_spell_shield", "cursed_disguise"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be a Boolean")
    if result["loss_mode"] not in ("none", "some"):
        raise ValueError("loss_mode must be 'none' or 'some'")
    for key in ("loss_percent", "unrandomized_percent"):
        if type(result[key]) is not int or not 0 <= result[key] <= 100:
            raise ValueError(f"{key} must be an integer percentage from 0 to 100")
    return result


def _gate(tier: str) -> str | None:
    if tier.startswith("s"):
        return "scrolls"
    return {"ka": "kangaxx", "fa": "flail_heads"}.get(tier)


def _table_rows(path: Path, columns: int):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        # Headers, blank lines, and historical one-word notes are not 2DA rows.
        if len(fields) >= columns and fields[columns - 1].isdigit():
            yield number, fields


def _endpoint_index(campaign: str) -> dict[tuple[str, str, str, int], dict]:
    path = ROOT / "lists" / "endpoints" / "base" / f"{campaign}.2da"
    result = {}
    for line, fields in _table_rows(path, 17):
        key = (fields[2], fields[3], fields[4], int(fields[5]))
        if key in result:
            raise ValueError(f"Duplicate endpoint key in {path}:{line}")
        result[key] = {
            "id": fields[6], "kind": fields[9], "target_identity": fields[11],
            "source_ref": f"lists/endpoints/base/{campaign}.2da:{line}",
        }
    return result


def _place_endpoints(campaign: str) -> dict[tuple[str, str], dict]:
    """The authored endpoints by physical place: target and area of their location rows."""
    endpoints = _endpoint_index(campaign)
    occurrences: Counter[tuple[str, str, str]] = Counter()
    result = {}
    for _, fields in _table_rows(ROOT / "lists" / "locations" / "base" / f"{campaign}.2da", 6):
        raw, tier, target, area, ident = fields[:5]
        occurrences[(tier, raw, ident)] += 1
        endpoint = endpoints[(tier, raw, ident, occurrences[(tier, raw, ident)])]
        if result.setdefault((target.lower(), area.lower()), endpoint)["id"] != endpoint["id"]:
            raise ValueError(f"Two authored endpoints for {target} in {area}")
    return result


def _list_set(kind: str, campaign: str, family: str, preset: str | None) -> tuple[str, str]:
    """The table a template reads, and the prefix of its row identities."""
    if preset is not None and campaign in PRESETS[preset]["campaigns"]:
        return (f"lists/presets/{preset}/{kind}/{family}/{campaign}.2da",
                f"{campaign}:{preset}:{family}")
    return f"lists/{kind}/{family}/{campaign}.2da", f"{campaign}:{family}"


def _optional_mod(preset: str | None, item: str) -> str | None:
    if preset is None:
        return None
    return next((mod for prefix, mod in PRESETS[preset]["optional_items"]
                 if item.lower().startswith(prefix)), None)


def _load_items(campaign: str, family: str, options: dict, preset: str | None = None) -> list[dict]:
    relative, prefix = _list_set("items", campaign, family, preset)
    occurrences: Counter[tuple[str, str]] = Counter()
    result = []
    for line, fields in _table_rows(ROOT / relative, 7):
        item, replacement, source, tier, token, ident, chance = fields[:7]
        occurrences[(tier, item)] += 1
        gate = _gate(tier)
        source_kind = source.rsplit(".", 1)[-1] if "." in source else source
        runtime_support = ("needs-virtual-source-adapter" if source_kind in ("blank", "fl")
                           else "needs-scripted-source-adapter" if source_kind in ("bcs", "dlg")
                           else "needs-binding")
        result.append({
            "id": f"{prefix}:item:{line}", "campaign": campaign,
            "family": family, "source_ref": f"{relative}:{line}",
            "resref": item.lower(), "replacement": replacement.lower(),
            "source": source.lower(), "source_kind": source_kind.lower(),
            "tier": tier.lower(), "token": token.lower(), "ident": ident.lower(),
            "chance": int(chance), "occurrence": occurrences[(tier, item)],
            "option_gate": gate, "enabled": gate is None or options[gate],
            "requires_game_conversion": campaign in ("bg1", "sod"),
            "optional_mod": _optional_mod(preset, item),
            "binding": "unbound", "runtime_support": runtime_support,
        })
    return result


def _load_targets(campaign: str, family: str, options: dict, preset: str | None = None) -> list[dict]:
    relative, prefix = _list_set("locations", campaign, family, preset)
    original = relative.startswith("lists/locations/")
    # A preset's place takes the endpoint authored for the same place, if any; the
    # original tables have one for every row of the base table.
    endpoints = ({} if family != "base" or campaign not in ENDPOINT_CAMPAIGNS
                 else _endpoint_index(campaign) if original else _place_endpoints(campaign))
    occurrences: Counter[tuple[str, str, str]] = Counter()
    result = []
    for line, fields in _table_rows(ROOT / relative, 6):
        raw, tier, target, area, ident, chance = fields[:6]
        occurrences[(tier, raw, ident)] += 1
        occurrence = occurrences[(tier, raw, ident)]
        endpoint = (endpoints.get((tier, raw, ident, occurrence)) if original
                    else endpoints.get((target.lower(), area.lower())))
        if family == "base" and original and campaign in ENDPOINT_CAMPAIGNS and endpoint is None:
            raise ValueError(f"Missing authored endpoint for {relative}:{line}")
        gate = _gate(tier)
        runtime_support = ("needs-endpoint-adapter" if endpoint is None
                           else "needs-group-target-adapter" if endpoint["kind"] == "group"
                           else "needs-legacy-script-adapter" if endpoint["kind"] == "legacy_adapter"
                           else "needs-binding")
        result.append({
            "id": f"{prefix}:target:{line}", "campaign": campaign,
            "family": family, "source_ref": f"{relative}:{line}",
            "location": raw.lower(), "tier": tier.lower(),
            "target": target.lower(), "area": area.lower(), "ident": ident.lower(),
            "chance": int(chance), "occurrence": occurrence,
            "option_gate": gate, "enabled": gate is None or options[gate],
            "endpoint_id": endpoint["id"] if endpoint else None,
            "endpoint_kind": endpoint["kind"] if endpoint else None,
            "endpoint_source_ref": endpoint["source_ref"] if endpoint else None,
            "requires_game_conversion": campaign in ("bg1", "sod"),
            "binding": "unbound", "runtime_support": runtime_support,
        })
    return result


def build_template(template_id: str, *, items: str | None = None,
                   targets: str | None = None, options: dict | None = None) -> dict:
    """Build a complete offline draft, drawn for each new game.

    ``items`` and ``targets`` each accept ``standard`` (the base tables, the
    default) or ``extended`` (also the mode2 tables).
    Options are installer component choices and percentages; gated rows remain
    present with ``enabled=False`` so the draft never silently loses content.
    """
    descriptors = _templates()
    if template_id not in descriptors:
        raise ValueError(f"Unknown original template: {template_id}")
    campaign, preset = descriptors[template_id]["campaign"], descriptors[template_id]["preset"]
    item_choice = _choice(items, "items")
    target_choice = _choice(targets, "targets")
    selected_options = _options(options)
    campaigns = _parts(campaign)
    item_rows, target_rows = [], []
    for part in campaigns:
        for family in (("base", "mode2") if item_choice == "extended" else ("base",)):
            item_rows.extend(_load_items(part, family, selected_options, preset))
        for family in (("base", "mode2") if target_choice == "extended" else ("base",)):
            target_rows.extend(_load_targets(part, family, selected_options, preset))
    unsupported_sources = sum(row["source_kind"] in ("blank", "fl", "bcs", "dlg")
                              for row in item_rows)
    unsupported_targets = sum(row["endpoint_kind"] in ("group", "legacy_adapter")
                              or row["endpoint_id"] is None for row in target_rows)
    return {
        "schema": SCHEMA, "state": "template-draft-awaiting-binding-and-tier-runtime",
        "id": template_id, "name": descriptors[template_id]["name"],
        "runtime_mode": "new-game", "campaigns": list(campaigns),
        "choices": {"items": item_choice, "targets": target_choice},
        "options": selected_options, "items": item_rows, "targets": target_rows,
        "counts": {
            "items": len(item_rows), "enabled_items": sum(row["enabled"] for row in item_rows),
            "targets": len(target_rows), "enabled_targets": sum(row["enabled"] for row in target_rows),
            "unbound_items": len(item_rows), "unbound_targets": len(target_rows),
            "unsupported_source_rows": unsupported_sources,
            "unsupported_target_rows": unsupported_targets,
        },
        "runtime_limitations": [
            "Rows require installation-specific item, source, and target binding.",
            "Tiered draws, loss and kept originals work for bound plans; original overflow and token expansion still need integration.",
            "Virtual, scripted, group, and unadapted extended-pool rows need runtime support.",
            "BG1 resource names require game-specific conversion on EET, BGT, and Tutu.",
            "Installer mod-compatibility and per-row availability filters need evaluation at binding.",
        ],
    }
