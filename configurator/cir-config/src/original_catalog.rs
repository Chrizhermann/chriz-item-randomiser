//! Binding the original Randomiser table rows to one scanned installation
//! (the reference's `original_catalog.py`).
//!
//! This keeps the complete authored tables, including rows whose source or
//! destination is absent from the physical scan. Runtime adapters and option
//! filters remain separate from structural binding.

use crate::container_rewards::{treasure_area, treasure_container};
use crate::error::{Error, ErrorKind, Result};
use crate::game_names::{convert_resource, eet_names, EetNames};
use crate::json::{Dict, Value};
use crate::templates::{build_template, int_field, table_rows, ENDPOINT_CAMPAIGNS};
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

const PHYSICAL_SOURCES: [&str; 3] = ["are", "cre", "sto"];
const SCRIPTED_SOURCES: [&str; 2] = ["bcs", "dlg"];

/// `all(row.get(key) == value ...)` for a dict row.
fn has_values(row: &Value, expected: &[(&str, &str)]) -> bool {
    row.is_dict() && expected.iter().all(|(key, value)| row.get(key).and_then(Value::as_str) == Some(*value))
}

/// Only the authored extended-pool Deck of Many Things item-grant source.
pub fn is_spin621_source(row: &Value) -> bool {
    has_values(
        row,
        &[
            ("id", "bg2:mode2:item:69"),
            ("source_ref", "lists/items/mode2/bg2.2da:69"),
            ("campaign", "bg2"),
            ("family", "mode2"),
            ("source", "spin621.spl"),
            ("ident", "w26"),
            ("tier", "10"),
            ("token", "w01"),
        ],
    )
}

/// Only the paired authored spell target, which has no physical area.
pub fn is_spin621_target(row: &Value) -> bool {
    has_values(
        row,
        &[
            ("id", "bg2:mode2:target:70"),
            ("source_ref", "lists/locations/mode2/bg2.2da:70"),
            ("campaign", "bg2"),
            ("family", "mode2"),
            ("target", "spin621.spl"),
            ("area", "blank"),
            ("ident", "w26"),
            ("tier", "10"),
            ("location", "w1"),
        ],
    )
}

type Lines = HashMap<usize, Vec<String>>;

/// The extended-pool location rows of a campaign by line number.
fn mode2_locations(campaign: &str) -> Result<&'static Lines> {
    static BG1: OnceLock<std::result::Result<Lines, Error>> = OnceLock::new();
    static BG2: OnceLock<std::result::Result<Lines, Error>> = OnceLock::new();
    let cell = if campaign == "bg1" { &BG1 } else { &BG2 };
    let load = || -> std::result::Result<Lines, Error> {
        Ok(table_rows(&format!("lists/locations/mode2/{campaign}.2da"), 6)?.into_iter().collect())
    };
    cell.get_or_init(load).as_ref().map_err(Clone::clone)
}

fn authored_mode2_target(row: &Value) -> Result<bool> {
    let campaign = match row.get("campaign").and_then(Value::as_str) {
        Some(campaign @ ("bg1" | "bg2")) => campaign,
        _ => return Ok(false),
    };
    let relative = format!("lists/locations/mode2/{campaign}.2da");
    let identity = row.at("id")?;
    let prefix = format!("{campaign}:mode2:target:");
    let Some(line) = identity
        .as_str()
        .and_then(|identity| identity.strip_prefix(&prefix))
        .and_then(|number| number.parse::<usize>().ok().filter(|line| line.to_string() == number))
    else {
        return Ok(false);
    };
    let Some(fields) = mode2_locations(campaign)?.get(&line) else {
        return Ok(false);
    };
    let lower = |index: usize| crate::py::lower(&fields[index]);
    let expected = [
        ("source_ref", format!("{relative}:{line}")),
        ("campaign", campaign.to_string()),
        ("family", "mode2".to_string()),
        ("location", lower(0)),
        ("tier", lower(1)),
        ("target", lower(2)),
        ("area", lower(3)),
        ("ident", lower(4)),
    ];
    Ok(expected.iter().all(|(key, value)| row.get(key).and_then(Value::as_str) == Some(value.as_str())))
}

/// A registered reward only for its exact authored `.fl` location.
pub fn mode2_alias_spec(row: &Value) -> Result<Option<Dict>> {
    let Some(identity) = row.get("id").and_then(Value::as_str) else {
        return Ok(None);
    };
    if !crate::mode2_rewards::target_rewards().contains_key(identity)
        || !row.get("target").and_then(Value::as_str).is_some_and(|target| target.ends_with(".fl"))
        || !authored_mode2_target(row)?
    {
        return Ok(None);
    }
    crate::mode2_rewards::target_spec(identity).map(Some)
}

pub fn container_target_spec(row: &Value) -> Result<Option<Dict>> {
    let Some(identity) = row.get("id").and_then(Value::as_str) else {
        return Ok(None);
    };
    if !crate::container_rewards::targets().contains_key(identity) || !authored_mode2_target(row)? {
        return Ok(None);
    }
    crate::container_rewards::target_spec(identity).map(Some)
}

/// The two authored Spellhold scroll selectors have legacy suffixes absent
/// from the scanned EET AR0411 container names. Each has one same-item
/// record under the corresponding base name; exact names keep precedence so
/// a game that still has the legacy name never silently switches containers.
const SOURCE_NAME_ALIASES: [((&str, &str, &str, &str, &str), &str); 2] = [
    (("bg2", "are:ar0411", "scrl6u", "is32", "toolct13"), "toolct1"),
    (("bg2", "are:ar0411", "scrl6u", "is33", "body1er7"), "body1"),
];

/// AR1202's original Container8 (Tazok's table) and Container9 (the closet)
/// are named Container9 / Container10 in the reviewed EET scan; the authored
/// coordinates distinguish them.
const TARGET_COORDINATE_ALIASES: [((&str, &str, &str), (i64, i64)); 2] = [
    (("bg2", "are:ar1202", "ep-bg2-ca49c7b59b05"), (2103, 812)),
    (("bg2", "are:ar1202", "ep-bg2-dc7accff0b4e"), (1949, 716)),
];

/// Names compared by their letters and digits only.
fn normal_name(value: &str) -> String {
    crate::py::lower(value).chars().filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit()).collect()
}

type Selectors = HashMap<String, (String, String, i64)>;

fn source_selectors(campaign: &str) -> Result<Selectors> {
    let mut result = HashMap::new();
    for (_, fields) in table_rows(&format!("lists/sources/base/{campaign}.2da"), 4)? {
        let expected = int_field(&fields[3])?;
        result.insert(crate::py::lower(&fields[0]), (fields[1].clone(), fields[2].clone(), expected));
    }
    Ok(result)
}

type Endpoints = HashMap<String, (String, String, String)>;

fn endpoint_rows(campaign: &str) -> Result<Endpoints> {
    let mut result: Endpoints = HashMap::new();
    if !ENDPOINT_CAMPAIGNS.contains(&campaign) {
        return Ok(result);
    }
    for (_, fields) in table_rows(&format!("lists/endpoints/base/{campaign}.2da"), 17)? {
        // A stable endpoint can be reused by many original location rows.
        let identity = (fields[9].clone(), fields[10].clone(), fields[11].clone());
        let prior = result.entry(fields[6].clone()).or_insert_with(|| identity.clone());
        if *prior != identity {
            return Err(Error::value(format!("Conflicting endpoint identity: {}", fields[6])));
        }
    }
    Ok(result)
}

fn group_members(campaign: &str, group: &str, game: &str, names: Option<&EetNames>) -> Result<Vec<String>> {
    let relative = format!("lists/groups/{campaign}.2da");
    let text = crate::data::text(&relative).ok_or_else(|| {
        Error::os(&std::io::Error::from(std::io::ErrorKind::NotFound), std::path::Path::new(&relative))
    })?;
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut result: Vec<String> = Vec::new();
    let wanted = crate::py::lower(group);
    for line in crate::py::splitlines(&text) {
        let fields = crate::py::split_whitespace(line);
        if fields.len() >= 2 && crate::py::lower(fields[0]) == wanted {
            let identity = format!("cre:{}", convert_resource(fields[1], "cre", campaign, game, names)?);
            if !result.contains(&identity) {
                result.push(identity);
            }
        }
    }
    Ok(result)
}

fn text_of<'a>(value: Option<&'a Value>) -> &'a str {
    value.and_then(Value::as_str).unwrap_or("")
}

fn source_owner_name<'a>(source: &Value, targets_by_id: &HashMap<&str, &'a Value>) -> &'a str {
    let owner = text_of(source.get("owner"));
    targets_by_id
        .get(owner)
        .and_then(|target| target.get("selector"))
        .and_then(|selector| selector.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("")
}

/// `row["binding_problem"] = row["binding_problem"] or reason`.
fn note_problem(row: &mut Dict, reason: &str) {
    if !row.get("binding_problem").is_some_and(Value::truthy) {
        row.insert("binding_problem", reason);
    }
}

fn str_field<'a>(row: &'a Dict, key: &str) -> Result<&'a str> {
    match row.get(key) {
        Some(Value::Str(text)) => Ok(text),
        Some(other) => Err(Error::type_error(format!("expected str for {key}, found {}", other.type_name()))),
        None => Err(Error::key(key)),
    }
}

fn sorted_ids<'a>(rows: impl Iterator<Item = &'a Value>) -> Vec<String> {
    let mut ids: Vec<String> = rows.map(|row| text_of(row.get("id")).to_string()).collect();
    ids.sort();
    ids
}

struct Scan<'a> {
    catalog_items: HashSet<&'a str>,
    resources: HashSet<&'a str>,
    sources: HashMap<(&'a str, &'a str), Vec<&'a Value>>,
    targets_by_resource: HashMap<&'a str, Vec<&'a Value>>,
    targets_by_id: HashMap<&'a str, &'a Value>,
    /// `world["sources"]`, in scan order.
    world_sources: &'a [Value],
    game: &'a str,
    /// EET's renames of BG1 and SoD names (game_names::eet_names).
    names: Option<&'a EetNames>,
}

fn resolve_source(row: &mut Dict, scan: &Scan<'_>, selectors: &HashMap<String, Selectors>) -> Result<()> {
    let campaign = str_field(row, "campaign")?.to_string();
    let item = convert_resource(str_field(row, "resref")?, "itm", &campaign, scan.game, scan.names)?;
    row.insert("item_resref", item.as_str());
    row.insert("source_resource", Value::Null);
    row.insert("source_ids", Value::List(Vec::new()));
    row.insert("adapter", Value::Null);
    row.insert("available", true);
    row.insert("availability_reason", Value::Null);
    row.insert("binding_problem", Value::Null);
    if !scan.catalog_items.contains(item.as_str()) {
        // A preset's item from another mod is absent when that mod is not
        // installed; any other absent item means the tables do not fit.
        let reason =
            if row.get("optional_mod").is_some_and(Value::truthy) { "optional-item-absent" } else { "item-definition-absent" };
        row.insert("available", false);
        row.insert("availability_reason", reason);
        row.insert("binding_problem", reason);
    }

    let raw = str_field(row, "source")?.to_string();
    if raw == "blank" {
        row.insert("adapter", "virtual");
        return Ok(());
    }
    if raw == "unshey.fl" {
        row.insert("adapter", "unshey");
        let name = convert_resource("ogreunsh", "cre", &campaign, scan.game, scan.names)?;
        row.insert("source_resource", format!("cre:{name}"));
        note_problem(row, "unshey-requires-legacy-adapter");
        return Ok(());
    }
    let Some((name, extension)) = raw.rsplit_once('.') else {
        note_problem(row, "unsupported-source-kind");
        return Ok(());
    };
    let spell_grant = extension == "spl" && is_spin621_source(&Value::Dict(row.clone())) && item == "ring41";
    if !PHYSICAL_SOURCES.contains(&extension) && !SCRIPTED_SOURCES.contains(&extension) && !spell_grant {
        note_problem(row, "unsupported-source-kind");
        return Ok(());
    }
    let converted = if spell_grant { name.to_string() } else { convert_resource(name, extension, &campaign, scan.game, scan.names)? };
    let resource = format!("{extension}:{converted}");
    row.insert("source_resource", resource.as_str());
    if SCRIPTED_SOURCES.contains(&extension) || spell_grant {
        row.insert("adapter", "scripted");
        note_problem(row, "scripted-source-requires-adapter");
        if !scan.resources.contains(resource.as_str()) {
            row.insert("available", false);
            row.insert("availability_reason", "source-resource-absent");
        }
        return Ok(());
    }
    if !scan.resources.contains(resource.as_str()) {
        row.insert("available", false);
        row.insert("availability_reason", "source-resource-absent");
        note_problem(row, "source-resource-absent");
        return Ok(());
    }

    let candidates: &[&Value] =
        scan.sources.get(&(resource.as_str(), item.as_str())).map(Vec::as_slice).unwrap_or(&[]);
    let ident = str_field(row, "ident")?.to_string();
    let default = ("*".to_string(), "single".to_string(), 1);
    let (object_name, policy, expected) = if row.get("family").and_then(Value::as_str) == Some("base") {
        let campaign_selectors = selectors.get(&campaign).ok_or_else(|| Error::key(&campaign))?;
        campaign_selectors.get(&ident).cloned().unwrap_or(default)
    } else {
        default
    };
    let mut matches: Vec<&Value> = candidates.to_vec();
    if object_name != "*" && extension == "are" {
        let normalized = normal_name(&object_name);
        let named = |wanted: &str| -> Vec<&Value> {
            candidates
                .iter()
                .copied()
                .filter(|source| normal_name(source_owner_name(source, &scan.targets_by_id)) == wanted)
                .collect()
        };
        matches = named(&normalized);
        if matches.is_empty() {
            let key = (campaign.as_str(), resource.as_str(), item.as_str(), ident.as_str(), normalized.as_str());
            if let Some((_, alias)) = SOURCE_NAME_ALIASES.iter().find(|(entry, _)| *entry == key) {
                matches = named(alias);
            }
        }
    }
    if policy == "all_one_unit" {
        row.insert("source_ids", Value::from(sorted_ids(matches.iter().copied())));
        if matches.len() as i64 != expected {
            note_problem(row, "source-match-count-mismatch");
        }
        return Ok(());
    }
    if matches.is_empty() {
        if !candidates.is_empty() {
            note_problem(row, "source-selector-not-matched");
        } else {
            row.insert("available", false);
            row.insert("availability_reason", "original-source-has-no-matching-record");
            note_problem(row, "original-source-has-no-matching-record");
        }
        return Ok(());
    }
    row.insert("source_ids", Value::from(sorted_ids(matches.iter().copied())));
    if matches.len() != 1 {
        note_problem(row, "ambiguous-source-records");
    }
    Ok(())
}

fn selector_of(target: &Value) -> Option<&Value> {
    target.get("selector")
}

fn is_kind(target: &Value, kind: &str) -> bool {
    target.get("kind").and_then(Value::as_str) == Some(kind)
}

fn resolve_target(row: &mut Dict, scan: &Scan<'_>, endpoints: &HashMap<String, Endpoints>) -> Result<()> {
    let campaign = str_field(row, "campaign")?.to_string();
    let area = convert_resource(str_field(row, "area")?, "are", &campaign, scan.game, scan.names)?;
    let area_resource = format!("are:{area}");
    row.insert("area_resource", area_resource.as_str());
    row.insert("target_ids", Value::List(Vec::new()));
    row.insert("creature_resource", Value::Null);
    row.insert("container_name", Value::Null);
    row.insert("members", Value::List(Vec::new()));
    row.insert("binding_problem", Value::Null);
    let snapshot = Value::Dict(row.clone());
    if let Some(container) = container_target_spec(&snapshot)? {
        // The container that holds the quest's treasure, in the quest's own
        // area: the table's area column is not where every handler puts it.
        let identity = str_field(row, "id")?.to_string();
        let area_resource = format!("are:{}", treasure_area(&identity, scan.game)?);
        row.insert("area_resource", area_resource.as_str());
        let physical_id = match treasure_container(&identity, scan.world_sources, scan.game) {
            Ok(chest) => Some(chest),
            Err(error) if error.kind == ErrorKind::Profile => None,
            Err(error) => return Err(error),
        };
        let matches: Vec<&Value> = scan
            .targets_by_resource
            .get(area_resource.as_str())
            .into_iter()
            .flatten()
            .copied()
            .filter(|target| physical_id.as_deref() == Some(text_of(target.get("id"))) && is_kind(target, "container"))
            .collect();
        if let (1, Some(physical_id)) = (matches.len(), &physical_id) {
            row.insert("target_ids", Value::from(vec![physical_id.clone()]));
            let name = selector_of(matches[0]).and_then(|selector| selector.get("name")).cloned();
            row.insert("container_name", name.ok_or_else(|| Error::key("name"))?);
        }
        let resources_absent = container
            .get("resources")
            .and_then(Value::as_list)
            .is_some_and(|resources| resources.iter().any(|resource| !scan.resources.contains(text_of(Some(resource)))));
        let problem = if !scan.resources.contains(area_resource.as_str()) {
            Value::from("area-resource-absent")
        } else if matches.len() != 1 {
            Value::from("treasure-container-not-found")
        } else if resources_absent {
            Value::from("target-resource-absent")
        } else {
            Value::Null
        };
        row.insert("binding_problem", problem);
        return Ok(());
    }
    if is_spin621_target(&snapshot) {
        row.insert("area_resource", "spl:spin621");
        let problem =
            if scan.resources.contains("spl:spin621") { "legacy-target-requires-adapter" } else { "target-resource-absent" };
        row.insert("binding_problem", problem);
        return Ok(());
    }
    if let Some(reward) = mode2_alias_spec(&snapshot)? {
        row.insert("area_resource", reward.get("resource").cloned().unwrap_or(Value::Null));
        let present = reward
            .get("resources")
            .and_then(Value::as_list)
            .is_none_or(|resources| resources.iter().all(|resource| scan.resources.contains(text_of(Some(resource)))));
        row.insert("binding_problem", if present { "legacy-target-requires-adapter" } else { "target-resource-absent" });
        return Ok(());
    }
    let endpoint_id = row.get("endpoint_id").cloned().unwrap_or(Value::Null);
    let endpoint = if endpoint_id.truthy() {
        let campaign_endpoints = endpoints.get(&campaign).ok_or_else(|| Error::key(&campaign))?;
        endpoint_id.as_str().and_then(|identity| campaign_endpoints.get(identity))
    } else {
        None
    };
    let raw = str_field(row, "target")?.to_string();
    let mut kind = endpoint.map(|endpoint| endpoint.0.clone());
    let mut identity = match endpoint {
        Some(endpoint) => endpoint.2.clone(),
        None => raw.split('.').next().unwrap_or("").to_string(),
    };
    if kind.is_none() {
        kind = Some(
            if raw.ends_with(".cre") {
                "creature"
            } else if raw.ends_with(".sto") {
                "store"
            } else if raw.ends_with(".flg") {
                "group"
            } else if raw.ends_with(".bcs") || raw.ends_with(".dlg") {
                "legacy_adapter"
            } else {
                "container"
            }
            .to_string(),
        );
    }
    let kind = kind.expect("set above");
    if !scan.resources.contains(area_resource.as_str()) {
        row.insert("binding_problem", "area-resource-absent");
    }
    let area_targets: Vec<&Value> =
        scan.targets_by_resource.get(area_resource.as_str()).cloned().unwrap_or_default();
    let matches: Vec<&Value> = match kind.as_str() {
        "container" => {
            row.insert("container_name", identity.as_str());
            let wanted = normal_name(&identity);
            let containers = area_targets.iter().copied().filter(|target| is_kind(target, "container"));
            let mut matches: Vec<&Value> = containers
                .clone()
                .filter(|target| {
                    normal_name(text_of(selector_of(target).and_then(|selector| selector.get("name")))) == wanted
                })
                .collect();
            let key = (campaign.as_str(), area_resource.as_str(), endpoint_id.as_str().unwrap_or("\u{0}"));
            if let Some((_, (x, y))) = TARGET_COORDINATE_ALIASES.iter().find(|(entry, _)| *entry == key) {
                matches = containers.clone().filter(|target| at_point(target, *x, *y)).collect();
            }
            if matches.len() > 1 && endpoint_id.truthy() {
                // Original endpoint coordinates identify e.g. Firkraag's golem
                // stash among two containers both named "Container 11". This
                // selects an existing container; it never creates a pile.
                for (_, fields) in table_rows("lists/endpoints/fallbacks.2da", 10)? {
                    if Some(fields[2].as_str()) == endpoint_id.as_str()
                        && convert_resource(&fields[3], "are", &campaign, scan.game, scan.names)? == area
                    {
                        let (x, y) = (int_field(&fields[4])?, int_field(&fields[5])?);
                        let pointed: Vec<&Value> = matches.iter().copied().filter(|target| at_point(target, x, y)).collect();
                        if pointed.len() == 1 {
                            matches = pointed;
                        }
                        break;
                    }
                }
            }
            matches
        }
        "creature" => {
            let name = raw.rsplit_once('.').map_or(raw.as_str(), |(name, _)| name);
            let creature = format!("cre:{}", convert_resource(name, "cre", &campaign, scan.game, scan.names)?);
            row.insert("creature_resource", creature.as_str());
            let matches: Vec<&Value> = area_targets
                .iter()
                .copied()
                .filter(|target| {
                    is_kind(target, "actor")
                        && selector_of(target).and_then(|selector| selector.get("template_resource")).and_then(Value::as_str)
                            == Some(creature.as_str())
                })
                .collect();
            if !scan.resources.contains(creature.as_str()) {
                note_problem(row, "creature-resource-absent");
            } else if !matches.is_empty()
                && matches.iter().all(|target| {
                    selector_of(target)
                        .and_then(|selector| selector.get("appearance_schedule"))
                        .is_some_and(|schedule| schedule.py_eq(&Value::Int(0)))
                })
            {
                // Placed for no hour of the day, the creature never appears
                // (how mods such as SoD Remix take a creature out); a reward
                // there would be lost.
                note_problem(row, "creature-never-appears");
            }
            matches
        }
        "store" => {
            let name = raw.rsplit_once('.').map_or(raw.as_str(), |(name, _)| name);
            identity = format!("sto:{}", convert_resource(name, "sto", &campaign, scan.game, scan.names)?);
            scan.targets_by_resource
                .get(identity.as_str())
                .into_iter()
                .flatten()
                .copied()
                .filter(|target| text_of(target.get("id")) == identity && is_kind(target, "store"))
                .collect()
        }
        "group" => {
            let members = group_members(&campaign, &raw, scan.game, scan.names)?;
            row.insert("members", Value::from(members.clone()));
            let matches = area_targets
                .iter()
                .copied()
                .filter(|target| {
                    is_kind(target, "actor")
                        && selector_of(target)
                            .and_then(|selector| selector.get("template_resource"))
                            .and_then(Value::as_str)
                            .is_some_and(|template| members.iter().any(|member| member == template))
                })
                .collect();
            note_problem(row, "group-requires-runtime-selection");
            matches
        }
        _ => {
            note_problem(row, "legacy-target-requires-adapter");
            Vec::new()
        }
    };
    row.insert("target_ids", Value::from(sorted_ids(matches.iter().copied())));
    let unset = row.get("binding_problem").is_none_or(Value::is_null);
    if matches.is_empty() && unset {
        row.insert("binding_problem", if kind == "creature" { "creature-not-static-in-area" } else { "target-not-in-scan" });
    } else if matches.len() > 1 && unset {
        row.insert("binding_problem", "ambiguous-target-records");
    }
    Ok(())
}

/// `(selector["x"], selector["y"]) == (x, y)`.
fn at_point(target: &Value, x: i64, y: i64) -> bool {
    let selector = selector_of(target);
    let coordinate = |key: &str| selector.and_then(|selector| selector.get(key)).cloned().unwrap_or(Value::Null);
    coordinate("x").py_eq(&Value::Int(x)) && coordinate("y").py_eq(&Value::Int(y))
}

/// Augment every original table row with exact installed scan candidates.
#[allow(clippy::too_many_arguments)]
pub fn resolve_template(
    catalog: &Value,
    world: &Value,
    template_id: &str,
    game: &str,
    items: Option<&Value>,
    targets: Option<&Value>,
    options: Option<&Value>,
    names: Option<&EetNames>,
) -> Result<Value> {
    let mut draft = build_template(template_id, items, targets, options)?;
    // On EET, BG1 and SoD names follow the lists of the scanned game by
    // default; a world without a game folder uses CIR's copy.
    let read;
    let names = match (names, world.get("game_root")) {
        (None, Some(root)) if game == "eet" => {
            read = eet_names(&crate::paths::path_argument(root)?)?;
            Some(&read)
        }
        (names, _) => names,
    };
    let list = |value: &Value, key: &str| -> Result<Vec<Value>> {
        Ok(value.at(key)?.as_list().cloned().unwrap_or_default())
    };
    let catalog_rows = list(catalog, "items")?;
    let world_resources = list(world, "resources")?;
    let world_sources = list(world, "sources")?;
    let world_targets = list(world, "targets")?;
    let mut scan = Scan {
        catalog_items: catalog_rows.iter().map(|row| text_of(row.get("resref"))).collect(),
        resources: world_resources.iter().map(|row| text_of(row.get("id"))).collect(),
        sources: HashMap::new(),
        targets_by_resource: HashMap::new(),
        targets_by_id: HashMap::new(),
        world_sources: &world_sources,
        game,
        names,
    };
    for source in &world_sources {
        scan.sources.entry((text_of(source.get("resource")), text_of(source.get("item")))).or_default().push(source);
    }
    for target in &world_targets {
        scan.targets_by_resource.entry(text_of(target.get("resource"))).or_default().push(target);
        scan.targets_by_id.insert(text_of(target.get("id")), target);
    }
    let campaigns: Vec<String> = draft
        .at("campaigns")?
        .as_list()
        .into_iter()
        .flatten()
        .filter_map(|campaign| campaign.as_str().map(str::to_string))
        .collect();
    let mut selectors = HashMap::new();
    for campaign in &campaigns {
        selectors.insert(campaign.clone(), source_selectors(campaign)?);
    }
    let mut endpoints = HashMap::new();
    for campaign in &campaigns {
        endpoints.insert(campaign.clone(), endpoint_rows(campaign)?);
    }
    for row in draft.at_mut("items")?.as_list_mut().into_iter().flatten() {
        let fields = row.as_dict_mut().ok_or_else(|| Error::type_error("template rows are dicts"))?;
        resolve_source(fields, &scan, &selectors)?;
    }
    for row in draft.at_mut("targets")?.as_list_mut().into_iter().flatten() {
        let fields = row.as_dict_mut().ok_or_else(|| Error::type_error("template rows are dicts"))?;
        resolve_target(fields, &scan, &endpoints)?;
    }
    Ok(draft)
}
