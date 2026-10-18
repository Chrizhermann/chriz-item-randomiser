//! Offline drafts of the original Randomiser item and location pools (the
//! reference's `templates.py`).
//!
//! `list_templates()` describes the bundled presets: Item Randomiser's tables
//! (BG1 and BG2), Chriz Item Randomiser's tables for Siege of Dragonspear
//! (SoD), and preset list sets (`lists/presets/<name>/`) that replace a
//! campaign's tables. Each preset has a standard and an extended pool of items and of
//! places: the standard pools read the `base` tables, the extended pools also
//! the `mode2` tables. Chriz Item Randomiser has no modes; every pool is drawn
//! for each new game. `build_template()` loads the checked-in WeiDU tables
//! without changing the game or flattening repeated rewards.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use std::collections::HashMap;

pub const SCHEMA: &str = "flir-original-template-draft-v1";
/// "standard": the base tables; "extended": the base and the mode2 tables.
pub const POOL_CHOICES: [&str; 2] = ["standard", "extended"];

/// Installer component choices and percentages, with their defaults.
pub fn default_options() -> Dict {
    crate::dict! {
        "scrolls" => false,
        "kangaxx" => false,
        "flail_heads" => false,
        "totsc_tome" => false,
        "more_spell_shield" => false,
        "cursed_disguise" => false,
        "loss_mode" => "none",
        "loss_percent" => 0,
        "unrandomized_percent" => 0,
    }
}

/// Item Randomiser's lists with the standard pools, no items lost, the Flail
/// of Ages heads and the randomised Tales of the Sword Coast tome.
pub fn recommended_options() -> Dict {
    default_options().merged(&crate::dict! {"flail_heads" => true, "totsc_tome" => true})
}

/// The preset campaigns and their labels. Item Randomiser has tables for BG1
/// and BG2; the SoD tables are Chriz Item Randomiser's. The tables of each
/// part of the game stay apart (tiers are per part), so a later option can mix
/// them.
pub const CAMPAIGNS: [(&str, &str); 6] =
    [("bg1", "BG1"), ("bg2", "BG2"), ("eet", "EET"), ("sod", "SoD"), ("bg1-sod", "BG1 + SoD"), ("eet-sod", "EET + SoD")];
/// The tables of each part of the game a preset campaign reads, in order.
const PARTS: [(&str, &[&str]); 6] = [
    ("bg1", &["bg1"]),
    ("bg2", &["bg2"]),
    ("eet", &["bg1", "bg2"]),
    ("sod", &["sod"]),
    ("bg1-sod", &["bg1", "sod"]),
    ("eet-sod", &["bg1", "sod", "bg2"]),
];
const PART_LABELS: [(&str, &str); 3] = [("bg1", "BG1"), ("sod", "SoD"), ("bg2", "BG2")];
/// Item Randomiser's tables have an authored endpoint for every row of their
/// base location tables; the SoD tables have none, so a row's target names
/// its kind.
pub const ENDPOINT_CAMPAIGNS: [&str; 2] = ["bg1", "bg2"];

/// A preset list set in lists/presets/<name>/.
pub struct Preset {
    pub id: &'static str,
    pub name: &'static str,
    pub author: &'static str,
    pub campaigns: &'static [&'static str],
    pub description: &'static str,
    pub extended_description: &'static str,
    /// Items that only another mod adds: (resref prefix, mod name).
    pub optional_items: &'static [(&'static str, &'static str)],
}

pub const PRESETS: &[Preset] = &[Preset {
    id: "boss-loot",
    name: "Boss Loot",
    author: "aShuN",
    campaigns: &["bg1"],
    description: "Powerful items at difficult boss fights, the ability tomes on the main path \
                  and wizard items at mage enemies.",
    extended_description: "The extended item pool adds shop stock, and Rogue Rebalancing \
                           and IWDification items where those mods are installed.",
    optional_items: &[("rr#", "Rogue Rebalancing"), ("cdia", "IWDification")],
}];

pub fn preset(id: &str) -> Option<&'static Preset> {
    PRESETS.iter().find(|preset| preset.id == id)
}

/// The default preset for a preset campaign ([`CAMPAIGNS`]).
pub fn recommended_preset(campaign: &str) -> Result<Dict> {
    if !PARTS.iter().any(|(known, _)| *known == campaign) {
        return Err(Error::value("Unknown campaign"));
    }
    Ok(crate::dict! {
        "id" => format!("{campaign}-original"), "items" => "standard", "targets" => "standard",
        "options" => recommended_options(),
    })
}

/// The table campaigns a preset campaign reads; none for an unknown one.
pub fn parts(campaign: &str) -> Vec<&'static str> {
    PARTS.iter().find(|(known, _)| *known == campaign).map_or_else(Vec::new, |(_, parts)| parts.to_vec())
}

/// The table campaigns ("bg1", "sod", "bg2") a preset campaign reads.
pub fn campaign_tables(campaign: &str) -> Result<Vec<&'static str>> {
    if !PARTS.iter().any(|(known, _)| *known == campaign) {
        return Err(Error::value("Unknown campaign"));
    }
    Ok(parts(campaign))
}

/// The table campaigns ("bg1", "sod", "bg2") a preset reads.
pub fn template_tables(template_id: &str) -> Result<Vec<&'static str>> {
    Ok(parts(template_campaign(template_id)?))
}

fn part_label(part: &str) -> &'static str {
    PART_LABELS.iter().find(|(known, _)| *known == part).map_or("", |(_, label)| label)
}

/// The descriptor of a campaign's own tables.
fn original(campaign: &'static str, label: &str) -> Descriptor {
    let parts = parts(campaign);
    let (name, description) = if !parts.contains(&"sod") {
        (
            format!("{label}: Item Randomiser's lists"),
            "The item and location lists of Item Randomiser by Wisp, by tier.".to_string(),
        )
    } else if parts == ["sod"] {
        (
            format!("{label}: Chriz Item Randomiser's lists"),
            "Item and location lists for Siege of Dragonspear by Chriz Item Randomiser, by tier.".to_string(),
        )
    } else {
        (
            format!("{label}: Item Randomiser's lists and the SoD lists"),
            "The item and location lists of Item Randomiser by Wisp, by tier, and Chriz Item Randomiser's lists \
             for Siege of Dragonspear."
                .to_string(),
        )
    };
    Descriptor { id: format!("{campaign}-original"), name, description, campaign, preset: None }
}

/// A preset's descriptor: name, description, campaign and preset id.
#[derive(Clone, Debug)]
pub struct Descriptor {
    pub id: String,
    pub name: String,
    pub description: String,
    pub campaign: &'static str,
    pub preset: Option<&'static str>,
}

pub fn descriptors() -> Vec<Descriptor> {
    let mut result: Vec<Descriptor> = CAMPAIGNS.iter().map(|&(campaign, label)| original(campaign, label)).collect();
    for preset in PRESETS {
        for (campaign, label) in CAMPAIGNS {
            let covered: Vec<&str> =
                parts(campaign).into_iter().filter(|part| preset.campaigns.contains(part)).map(part_label).collect();
            let others: Vec<&str> = parts(campaign).into_iter().filter(|part| !preset.campaigns.contains(part)).collect();
            if covered.is_empty() {
                continue;
            }
            let mut description = preset.description.to_string();
            if !others.is_empty() {
                let kept: Vec<String> = others
                    .iter()
                    .map(|part| {
                        let lists = if *part == "sod" { "Chriz Item Randomiser's lists" } else { "Item Randomiser's lists" };
                        format!("{} keeps {lists}", part_label(part))
                    })
                    .collect();
                description.push_str(&format!(" For {}; {}.", covered.join(", "), kept.join(" and ")));
            }
            result.push(Descriptor {
                id: format!("{campaign}-{}", preset.id),
                name: format!("{label}: {} by {}", preset.name, preset.author),
                description: format!("{description} {}", preset.extended_description),
                campaign,
                preset: Some(preset.id),
            });
        }
    }
    result
}

pub fn descriptor(template_id: &str) -> Option<Descriptor> {
    descriptors().into_iter().find(|descriptor| descriptor.id == template_id)
}

/// Stable, compact descriptors of the bundled presets; `campaigns` limits
/// them to presets for those preset campaigns ([`CAMPAIGNS`]).
pub fn list_templates(campaigns: Option<&[&str]>) -> Value {
    Value::List(
        descriptors()
            .into_iter()
            .filter(|row| campaigns.is_none_or(|campaigns| campaigns.contains(&row.campaign)))
            .map(|row| Value::Dict(crate::dict! {"id" => row.id, "name" => row.name, "description" => row.description}))
            .collect(),
    )
}

/// The preset campaign ([`CAMPAIGNS`]) a preset is for.
pub fn template_campaign(template_id: &str) -> Result<&'static str> {
    descriptor(template_id)
        .map(|row| row.campaign)
        .ok_or_else(|| Error::value(format!("Unknown original template: {template_id}")))
}

pub fn template_ids() -> Vec<String> {
    descriptors().into_iter().map(|row| row.id).collect()
}

fn choice(value: Option<&Value>, label: &str) -> Result<String> {
    let result = match value {
        None | Some(Value::Null) => return Ok("standard".into()),
        Some(value) => value,
    };
    match result.as_str() {
        Some(text) if POOL_CHOICES.contains(&text) => Ok(text.to_string()),
        _ => Err(Error::value(format!("{label} must be 'standard' or 'extended'"))),
    }
}

/// The reference's `_options`: installer choices merged over the defaults.
pub fn options(value: Option<&Value>) -> Result<Dict> {
    let value = match value {
        None | Some(Value::Null) => return Ok(default_options()),
        Some(value) => value,
    };
    let defaults = default_options();
    let Some(fields) = value.as_dict().filter(|fields| fields.keys().all(|key| defaults.contains_key(key))) else {
        return Err(Error::value("Unknown original-template option"));
    };
    let result = defaults.merged(fields);
    for key in ["scrolls", "kangaxx", "flail_heads", "totsc_tome", "more_spell_shield", "cursed_disguise"] {
        if !matches!(result.get(key), Some(Value::Bool(_))) {
            return Err(Error::value(format!("{key} must be a Boolean")));
        }
    }
    if !matches!(result.get("loss_mode").and_then(Value::as_str), Some("none" | "some")) {
        return Err(Error::value("loss_mode must be 'none' or 'some'"));
    }
    for key in ["loss_percent", "unrandomized_percent"] {
        if crate::profiles::int_in(result.get(key), 0, 100).is_none() {
            return Err(Error::value(format!("{key} must be an integer percentage from 0 to 100")));
        }
    }
    Ok(result)
}

fn gate(tier: &str) -> Option<&'static str> {
    if tier.starts_with('s') {
        return Some("scrolls");
    }
    match tier {
        "ka" => Some("kangaxx"),
        "fa" => Some("flail_heads"),
        _ => None,
    }
}

/// `int(text)` with Python's ValueError message.
pub fn int_field(text: &str) -> Result<i64> {
    crate::py::int_saturating(text).map_err(Error::value)
}

/// 2DA rows of a mod table: (line number, whitespace-split fields) for lines
/// with at least `columns` fields whose last required field is a number.
pub fn table_rows(relative: &str, columns: usize) -> Result<Vec<(usize, Vec<String>)>> {
    let text = crate::data::text(relative).ok_or_else(|| {
        Error::os(&std::io::Error::from(std::io::ErrorKind::NotFound), std::path::Path::new(relative))
    })?;
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut rows = Vec::new();
    for (number, line) in crate::py::splitlines(&text).into_iter().enumerate() {
        let fields = crate::py::split_whitespace(line);
        // Headers, blank lines and historical one-word notes are not 2DA rows.
        if fields.len() >= columns && crate::py::is_digit_str(fields[columns - 1]) {
            rows.push((number + 1, fields.into_iter().map(str::to_string).collect()));
        }
    }
    Ok(rows)
}

/// An authored endpoint (`lists/endpoints/base/<campaign>.2da`).
#[derive(Clone, Debug)]
pub struct Endpoint {
    pub id: String,
    pub kind: String,
    pub target_identity: String,
    pub source_ref: String,
}

type EndpointKey = (String, String, String, i64);

pub fn endpoint_index(campaign: &str) -> Result<HashMap<EndpointKey, Endpoint>> {
    let relative = format!("lists/endpoints/base/{campaign}.2da");
    let mut result = HashMap::new();
    for (line, fields) in table_rows(&relative, 17)? {
        let key = (fields[2].clone(), fields[3].clone(), fields[4].clone(), int_field(&fields[5])?);
        if result.contains_key(&key) {
            return Err(Error::value(format!("Duplicate endpoint key in {relative}:{line}")));
        }
        result.insert(
            key,
            Endpoint {
                id: fields[6].clone(),
                kind: fields[9].clone(),
                target_identity: fields[11].clone(),
                source_ref: format!("lists/endpoints/base/{campaign}.2da:{line}"),
            },
        );
    }
    Ok(result)
}

/// The authored endpoints by physical place: target and area of their
/// location rows.
fn place_endpoints(campaign: &str) -> Result<HashMap<(String, String), Endpoint>> {
    let endpoints = endpoint_index(campaign)?;
    let mut occurrences: HashMap<(String, String, String), i64> = HashMap::new();
    let mut result: HashMap<(String, String), Endpoint> = HashMap::new();
    for (_, fields) in table_rows(&format!("lists/locations/base/{campaign}.2da"), 6)? {
        let (raw, tier, target, area, ident) = (&fields[0], &fields[1], &fields[2], &fields[3], &fields[4]);
        let count = occurrences.entry((tier.clone(), raw.clone(), ident.clone())).or_insert(0);
        *count += 1;
        let key = (tier.clone(), raw.clone(), ident.clone(), *count);
        let endpoint = endpoints.get(&key).ok_or_else(|| Error::key(&format!("{key:?}")))?;
        let place = (crate::py::lower(target), crate::py::lower(area));
        let existing = result.entry(place).or_insert_with(|| endpoint.clone());
        if existing.id != endpoint.id {
            return Err(Error::value(format!("Two authored endpoints for {target} in {area}")));
        }
    }
    Ok(result)
}

/// The table a template reads, and the prefix of its row identities.
pub fn list_set(kind: &str, campaign: &str, family: &str, preset_id: Option<&str>) -> (String, String) {
    if let Some(preset_id) = preset_id {
        if preset(preset_id).is_some_and(|preset| preset.campaigns.contains(&campaign)) {
            return (
                format!("lists/presets/{preset_id}/{kind}/{family}/{campaign}.2da"),
                format!("{campaign}:{preset_id}:{family}"),
            );
        }
    }
    (format!("lists/{kind}/{family}/{campaign}.2da"), format!("{campaign}:{family}"))
}

fn optional_mod(preset_id: Option<&str>, item: &str) -> Option<&'static str> {
    let preset = preset(preset_id?)?;
    let lowered = crate::py::lower(item);
    preset.optional_items.iter().find(|(prefix, _)| lowered.starts_with(prefix)).map(|(_, name)| *name)
}

fn enabled(gate: Option<&str>, options: &Dict) -> bool {
    match gate {
        None => true,
        Some(gate) => options.get(gate).is_some_and(Value::truthy),
    }
}

fn load_items(campaign: &str, family: &str, options: &Dict, preset_id: Option<&str>) -> Result<Vec<Value>> {
    let (relative, prefix) = list_set("items", campaign, family, preset_id);
    let mut occurrences: HashMap<(String, String), i64> = HashMap::new();
    let mut result = Vec::new();
    for (line, fields) in table_rows(&relative, 7)? {
        let (item, replacement, source, tier, token, ident, chance) =
            (&fields[0], &fields[1], &fields[2], &fields[3], &fields[4], &fields[5], &fields[6]);
        let count = occurrences.entry((tier.clone(), item.clone())).or_insert(0);
        *count += 1;
        let gate = gate(tier);
        let source_kind = if source.contains('.') { source.rsplit('.').next().unwrap() } else { source.as_str() };
        let runtime_support = match source_kind {
            "blank" | "fl" => "needs-virtual-source-adapter",
            "bcs" | "dlg" => "needs-scripted-source-adapter",
            _ => "needs-binding",
        };
        let mut row = Dict::with_capacity(20);
        row.push_unique("id".into(), Value::Str(format!("{prefix}:item:{line}")));
        row.push_unique("campaign".into(), Value::from(campaign));
        row.push_unique("family".into(), Value::from(family));
        row.push_unique("source_ref".into(), Value::Str(format!("{relative}:{line}")));
        row.push_unique("resref".into(), Value::Str(crate::py::lower(item)));
        row.push_unique("replacement".into(), Value::Str(crate::py::lower(replacement)));
        row.push_unique("source".into(), Value::Str(crate::py::lower(source)));
        row.push_unique("source_kind".into(), Value::Str(crate::py::lower(source_kind)));
        row.push_unique("tier".into(), Value::Str(crate::py::lower(tier)));
        row.push_unique("token".into(), Value::Str(crate::py::lower(token)));
        row.push_unique("ident".into(), Value::Str(crate::py::lower(ident)));
        row.push_unique("chance".into(), Value::Int(int_field(chance)?));
        row.push_unique("occurrence".into(), Value::Int(*count));
        row.push_unique("option_gate".into(), Value::from(gate));
        row.push_unique("enabled".into(), Value::Bool(enabled(gate, options)));
        row.push_unique("requires_game_conversion".into(), Value::Bool(["bg1", "sod"].contains(&campaign)));
        row.push_unique("optional_mod".into(), Value::from(optional_mod(preset_id, item)));
        row.push_unique("binding".into(), Value::from("unbound"));
        row.push_unique("runtime_support".into(), Value::from(runtime_support));
        result.push(Value::Dict(row));
    }
    Ok(result)
}

enum Endpoints {
    None,
    Original(HashMap<EndpointKey, Endpoint>),
    Places(HashMap<(String, String), Endpoint>),
}

fn load_targets(campaign: &str, family: &str, options: &Dict, preset_id: Option<&str>) -> Result<Vec<Value>> {
    let (relative, prefix) = list_set("locations", campaign, family, preset_id);
    let original = relative.starts_with("lists/locations/");
    // A preset's place takes the endpoint authored for the same place, if
    // any; the original tables have one for every row of the base table.
    let authored = ENDPOINT_CAMPAIGNS.contains(&campaign);
    let endpoints = if family != "base" || !authored {
        Endpoints::None
    } else if original {
        Endpoints::Original(endpoint_index(campaign)?)
    } else {
        Endpoints::Places(place_endpoints(campaign)?)
    };
    let mut occurrences: HashMap<(String, String, String), i64> = HashMap::new();
    let mut result = Vec::new();
    for (line, fields) in table_rows(&relative, 6)? {
        let (raw, tier, target, area, ident, chance) =
            (&fields[0], &fields[1], &fields[2], &fields[3], &fields[4], &fields[5]);
        let count = occurrences.entry((tier.clone(), raw.clone(), ident.clone())).or_insert(0);
        *count += 1;
        let occurrence = *count;
        let endpoint = match &endpoints {
            Endpoints::None => None,
            Endpoints::Original(index) if original => {
                index.get(&(tier.clone(), raw.clone(), ident.clone(), occurrence))
            }
            Endpoints::Places(index) if !original => index.get(&(crate::py::lower(target), crate::py::lower(area))),
            _ => None,
        };
        if family == "base" && original && authored && endpoint.is_none() {
            return Err(Error::value(format!("Missing authored endpoint for {relative}:{line}")));
        }
        let gate = gate(tier);
        let runtime_support = match endpoint {
            None => "needs-endpoint-adapter",
            Some(endpoint) if endpoint.kind == "group" => "needs-group-target-adapter",
            Some(endpoint) if endpoint.kind == "legacy_adapter" => "needs-legacy-script-adapter",
            Some(_) => "needs-binding",
        };
        let mut row = Dict::with_capacity(20);
        row.push_unique("id".into(), Value::Str(format!("{prefix}:target:{line}")));
        row.push_unique("campaign".into(), Value::from(campaign));
        row.push_unique("family".into(), Value::from(family));
        row.push_unique("source_ref".into(), Value::Str(format!("{relative}:{line}")));
        row.push_unique("location".into(), Value::Str(crate::py::lower(raw)));
        row.push_unique("tier".into(), Value::Str(crate::py::lower(tier)));
        row.push_unique("target".into(), Value::Str(crate::py::lower(target)));
        row.push_unique("area".into(), Value::Str(crate::py::lower(area)));
        row.push_unique("ident".into(), Value::Str(crate::py::lower(ident)));
        row.push_unique("chance".into(), Value::Int(int_field(chance)?));
        row.push_unique("occurrence".into(), Value::Int(occurrence));
        row.push_unique("option_gate".into(), Value::from(gate));
        row.push_unique("enabled".into(), Value::Bool(enabled(gate, options)));
        row.push_unique("endpoint_id".into(), Value::from(endpoint.map(|endpoint| endpoint.id.clone())));
        row.push_unique("endpoint_kind".into(), Value::from(endpoint.map(|endpoint| endpoint.kind.clone())));
        row.push_unique("endpoint_source_ref".into(), Value::from(endpoint.map(|endpoint| endpoint.source_ref.clone())));
        row.push_unique("requires_game_conversion".into(), Value::Bool(["bg1", "sod"].contains(&campaign)));
        row.push_unique("binding".into(), Value::from("unbound"));
        row.push_unique("runtime_support".into(), Value::from(runtime_support));
        result.push(Value::Dict(row));
    }
    Ok(result)
}

/// Build a complete offline draft, drawn for each new game.
///
/// `items` and `targets` each accept `standard` (the base tables, the
/// default) or `extended` (also the mode2 tables). Gated rows stay present
/// with `enabled=False` so the draft never silently loses content.
pub fn build_template(template_id: &str, items: Option<&Value>, targets: Option<&Value>, options_value: Option<&Value>) -> Result<Value> {
    let Some(descriptor) = descriptor(template_id) else {
        return Err(Error::value(format!("Unknown original template: {template_id}")));
    };
    let item_choice = choice(items, "items")?;
    let target_choice = choice(targets, "targets")?;
    let selected_options = options(options_value)?;
    let campaigns = parts(descriptor.campaign);
    let mut item_rows = Vec::new();
    let mut target_rows = Vec::new();
    for part in &campaigns {
        let item_families: &[&str] = if item_choice == "extended" { &["base", "mode2"] } else { &["base"] };
        for family in item_families {
            item_rows.extend(load_items(part, family, &selected_options, descriptor.preset)?);
        }
        let target_families: &[&str] = if target_choice == "extended" { &["base", "mode2"] } else { &["base"] };
        for family in target_families {
            target_rows.extend(load_targets(part, family, &selected_options, descriptor.preset)?);
        }
    }
    let field = |row: &Value, key: &str| row.get(key).cloned().unwrap_or(Value::Null);
    let unsupported_sources = item_rows
        .iter()
        .filter(|row| matches!(field(row, "source_kind").as_str(), Some("blank" | "fl" | "bcs" | "dlg")))
        .count();
    let unsupported_targets = target_rows
        .iter()
        .filter(|row| {
            matches!(field(row, "endpoint_kind").as_str(), Some("group" | "legacy_adapter")) || field(row, "endpoint_id").is_null()
        })
        .count();
    let enabled_items = item_rows.iter().filter(|row| field(row, "enabled").truthy()).count();
    let enabled_targets = target_rows.iter().filter(|row| field(row, "enabled").truthy()).count();
    let counts = crate::dict! {
        "items" => item_rows.len(), "enabled_items" => enabled_items,
        "targets" => target_rows.len(), "enabled_targets" => enabled_targets,
        "unbound_items" => item_rows.len(), "unbound_targets" => target_rows.len(),
        "unsupported_source_rows" => unsupported_sources,
        "unsupported_target_rows" => unsupported_targets,
    };
    Ok(Value::Dict(crate::dict! {
        "schema" => SCHEMA, "state" => "template-draft-awaiting-binding-and-tier-runtime",
        "id" => template_id, "name" => descriptor.name,
        "runtime_mode" => "new-game", "campaigns" => campaigns.iter().map(|part| Value::from(*part)).collect::<Vec<_>>(),
        "choices" => crate::dict! {"items" => item_choice, "targets" => target_choice},
        "options" => selected_options, "items" => item_rows, "targets" => target_rows,
        "counts" => counts,
        "runtime_limitations" => crate::list![
            "Rows require installation-specific item, source, and target binding.",
            "Tiered draws, loss and kept originals work for bound plans; original overflow and token expansion still need integration.",
            "Virtual, scripted, group, and unadapted extended-pool rows need runtime support.",
            "BG1 and SoD resource names require game-specific conversion on EET, BGT, and Tutu.",
            "Installer mod-compatibility and per-row availability filters need evaluation at binding.",
        ],
    }))
}
