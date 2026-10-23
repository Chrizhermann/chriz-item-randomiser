//! A reviewed post-install draft from the original Randomiser tables (the
//! reference's `original_configuration.py`).
//!
//! Every enabled authored location remains a distinct target registration.
//! Rows that need runtime adapters carry only table data and scanned resource
//! hashes; this module never supplies executable code or chooses an
//! assignment.

use crate::campaigns::{compile_campaign, OVERFLOW_TARGET_KINDS};
use crate::duplicates::{expected_copies, holdings, reviewed};
use crate::error::{Error, Result};
use crate::game_names::{convert_resource, eet_names, EetNames};
use crate::json::{Dict, Value};
use crate::original_catalog::{resolve_template, NO_ROUTE, UNREADABLE_PLACE_PROBLEMS};
use crate::original_registrations::{
    is_unreferenced_creature_source, source_lookup, target_record, SourceLookup, PAYLOAD_FIELDS, SHIPPED_REPLACEMENT,
    SOURCE_FIELDS, TARGET_FIELDS,
};
use crate::prepared::{applied, applied_changes, left_out, quest_change};
use crate::profiles::{edit_profile, new_profile, validate_catalog, ItemIndex, ProfileEdit};
use crate::resources::{sha256_hex, GameResources, ITM_TYPE};
use crate::sources::{new_binding_plan, unreadable_notes, validate_world, Scans, WorldIndex, UNUSABLE_SOURCE};
use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::Path;

/// The binding problems a reward row may still have and be used.
const USABLE_PROBLEMS: [Option<&str>; 3] =
    [None, Some("scripted-source-requires-adapter"), Some("unshey-requires-legacy-adapter")];
/// The original BG2 row that leaves the item shipped with Chriz Item Randomiser.
const SHIPPED_REPLACEMENT_ROW: &str = "bg2:base:item:86";
/// Rounds of leaving out rows the runtime check names before giving up.
const FIT_ROUNDS: usize = 8;

fn s<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

fn s_of<'a>(row: &'a Dict, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

fn tier(row: &Value) -> String {
    format!("{}:{}", crate::json::py_str(row.get("campaign").unwrap_or(&Value::Null)), crate::json::py_str(row.get("tier").unwrap_or(&Value::Null)))
}

/// The change the installer makes that this reward row needs. An extended
/// reward route needs none: Apply patches the game's own script for it, also
/// the book that the installer prepared for the ring's quest.
fn source_change(row: &Value) -> Result<Option<&'static str>> {
    let adapter = row.get("adapter").and_then(Value::as_str);
    if adapter == Some("unshey") {
        return Ok(Some("unshey"));
    }
    let resource = s(row, "source_resource");
    if adapter == Some("scripted") {
        if let Some(quest) = resource.strip_prefix("bcs:") {
            if crate::original_catalog::mode2_route_source(row)?.is_none() {
                return Ok(quest_change(quest));
            }
        }
    }
    Ok(None)
}

fn target_change(row: &Value) -> Result<Option<&'static str>> {
    Ok(s(row, "target").strip_suffix(".bcs").and_then(quest_change))
}

/// Default charges of each named installed item.
fn item_defaults(catalog: &Value, items: &ItemIndex, names: &BTreeSet<String>) -> Result<HashMap<String, Value>> {
    let mut result = HashMap::new();
    if names.is_empty() {
        return Ok(result);
    }
    let root = Path::new(catalog.at("game_root")?.as_str().unwrap_or(""));
    GameResources::with(root, &[("itm", ITM_TYPE)], |reader| {
        for name in names {
            let data = reader.read("itm", name)?;
            let expected = items.row(catalog, name).and_then(|row| row.get("sha256")).and_then(Value::as_str);
            if Some(sha256_hex(&data).as_str()) != expected {
                return Err(Error::profile(format!("Installed ITM changed; rescan before building: {name}")));
            }
            let mut charges = [0i64; 3];
            if data.len() > 0x71 {
                let offset = crate::py::u32_le(&data, 0x64) as usize;
                let count = crate::py::u16_le(&data, 0x68) as usize;
                for (index, charge) in charges.iter_mut().enumerate().take(count.min(3)) {
                    let at = offset + index * 0x38 + 0x22;
                    if at + 2 > data.len() {
                        return Err(Error::profile(format!("Installed ITM ability charges are invalid: {name}")));
                    }
                    *charge = i64::from(crate::py::u16_le(&data, at));
                }
            }
            result.insert(name.clone(), Value::from(charges.to_vec()));
        }
        Ok(())
    })?;
    Ok(result)
}

fn authored(row: &Value, keys: &[&str]) -> Result<Dict> {
    let mut authored = Dict::new();
    for key in keys {
        authored.insert(*key, row.at(key)?.clone());
    }
    Ok(authored)
}

fn original_source(
    row: &Value,
    resource_hash: &Value,
    payload: Value,
    physical_source_ids: Vec<String>,
    grouped: bool,
) -> Result<Dict> {
    let mut evidence = authored(row, &SOURCE_FIELDS)?;
    if grouped {
        evidence.insert("match_policy", "all_one_unit");
        evidence.insert("expected_matches", physical_source_ids.len());
    }
    Ok(crate::dict! {
        "id" => format!("original-source:{}", s(row, "id")), "item" => row.at("item_resref")?.clone(),
        "resource" => row.at("source_resource")?.clone(), "resource_sha256" => resource_hash.clone(),
        "adapter_id" => "original-source-v1", "selector" => row.at("id")?.clone(), "copies" => 1,
        "optional" => false, "payload" => payload, "physical_source_ids" => Value::from(physical_source_ids),
        "authored" => evidence,
    })
}

fn original_target(row: &Value, resource_hash: &Value) -> Result<Dict> {
    Ok(crate::dict! {
        "id" => format!("original-target:{}", s(row, "id")), "resource" => row.at("area_resource")?.clone(),
        "resource_sha256" => resource_hash.clone(), "adapter_id" => "original-target-v1",
        "selector" => row.at("id")?.clone(), "capacity" => 1, "optional" => false,
        "authored" => authored(row, &TARGET_FIELDS)?,
    })
}

/// One reward row's choice: a registration, or a physical source with an
/// optional replacement; Err(reason) when this game cannot use the row.
enum Choice {
    Registration(Dict),
    Physical(String, Option<String>),
}

struct Context<'a> {
    catalog: &'a Value,
    world: &'a Value,
    /// "eet", "bgee" or "bg2ee" (platform::preset_game).
    game: &'a str,
    /// EET's renames of BG1 and SoD names (game_names::eet_names).
    names: Option<&'a EetNames>,
    items: &'a ItemIndex,
    index: &'a WorldIndex,
    physical: SourceLookup<'a>,
    shield: Option<&'a Value>,
    defaults: &'a HashMap<String, Value>,
}

fn resource_sha<'a>(context: &Context<'a>, resource: &str) -> Result<&'a Value> {
    context
        .index
        .resource(context.world, resource)
        .and_then(|row| row.get("sha256"))
        .ok_or_else(|| Error::key(resource))
}

fn record_payload(source: &Value) -> Result<Value> {
    let record = source.at("record")?;
    let mut payload = Dict::new();
    for key in PAYLOAD_FIELDS {
        payload.insert(key, record.at(key)?.clone());
    }
    Ok(Value::Dict(payload))
}

fn choose(row: &Value, context: &Context<'_>) -> Result<std::result::Result<Choice, String>> {
    let item = s(row, "item_resref");
    let resource = s(row, "source_resource");
    let adapter = row.get("adapter").and_then(Value::as_str);
    let physical = &context.physical;
    if let Some(shield) = context.shield.filter(|_| crate::original_options::is_spell_shield_row(row)) {
        let original = shield.at("original_source_id")?;
        let ids = if original.truthy() { vec![crate::json::py_str(original)] } else { Vec::new() };
        let mut registration = original_source(row, shield.at("resource_sha256")?, shield.at("payload")?.clone(), ids, false)?;
        registration.insert("copies", shield.at("quantity")?.clone());
        return Ok(Ok(Choice::Registration(registration)));
    }
    if adapter == Some("virtual") {
        let sha = context.items.row(context.catalog, item).and_then(|row| row.get("sha256")).and_then(Value::as_str);
        let charges = context.defaults.get(item).ok_or_else(|| Error::key(item))?;
        let registration = crate::virtual_sources::virtual_registration(item, s(row, "id"), 1, sha.unwrap_or(""), charges)?;
        return Ok(Ok(Choice::Registration(registration.as_dict().cloned().unwrap_or_default())));
    }
    if adapter == Some("scripted") && resource == "bcs:blpool" && item == "blun12" {
        let registration = crate::scripted_sources::blpool_registration(context.world)?;
        return Ok(Ok(Choice::Registration(registration.as_dict().cloned().unwrap_or_default())));
    }
    if matches!(adapter, Some("scripted" | "unshey")) {
        if !context.index.resources.contains(resource) {
            return Ok(Err("source-resource-absent".into()));
        }
        let mut matched: Vec<String> = context
            .world
            .at("sources")?
            .as_list()
            .into_iter()
            .flatten()
            .filter(|source| s(source, "resource") == resource && s(source, "item") == item)
            .map(|source| s(source, "id").to_string())
            .collect();
        matched.sort();
        if adapter == Some("unshey") && matched.len() != 1 {
            return Ok(Err("ambiguous-unshey-records".into()));
        }
        let default_charges = || context.defaults.get(item).cloned().ok_or_else(|| Error::key(item));
        let payload = if adapter == Some("unshey") {
            record_payload(physical.get(matched[0].as_str()).ok_or_else(|| Error::key(&matched[0]))?)?
        } else if resource == "spl:spin621" {
            crate::spell_rewards::payload()
        } else if let Some(route) = crate::original_catalog::mode2_route_source(row)? {
            let naming = crate::mode2_rewards::Naming { game: Some(context.game), names: context.names };
            crate::mode2_rewards::source_spec(&route, naming)?.get("payload").cloned().ok_or_else(|| Error::key("payload"))?
        } else {
            Value::Dict(crate::dict! {"resref" => item, "expiration" => 0, "charges" => default_charges()?, "flags" => 0})
        };
        let ids = if adapter == Some("unshey") { matched } else { Vec::new() };
        let registration = original_source(row, resource_sha(context, resource)?, payload, ids, false)?;
        return Ok(Ok(Choice::Registration(registration)));
    }
    let ids: Vec<String> = row.at("source_ids")?.as_list().into_iter().flatten().map(crate::json::py_str).collect();
    if ids.is_empty() {
        return Ok(Err("source-record-absent".into()));
    }
    if ids.len() > 1 || is_unreferenced_creature_source(row, physical)? {
        let mut sources = Vec::with_capacity(ids.len());
        for identity in &ids {
            let source = *physical.get(identity.as_str()).ok_or_else(|| Error::key(identity))?;
            if s(source, "kind") != "cre-template" {
                return Ok(Err("ambiguous-source-records".into()));
            }
            sources.push(source);
        }
        let payloads: Vec<Value> = sources.iter().map(|source| record_payload(source)).collect::<Result<_>>()?;
        if payloads[1..].iter().any(|payload| !payload.py_eq(&payloads[0])) {
            return Ok(Err("grouped-source-payload-mismatch".into()));
        }
        let mut sorted_ids = ids.clone();
        sorted_ids.sort();
        let grouped = ids.len() > 1;
        let registration =
            original_source(row, resource_sha(context, resource)?, payloads[0].clone(), sorted_ids, grouped)?;
        return Ok(Ok(Choice::Registration(registration)));
    }
    let source = *physical.get(ids[0].as_str()).ok_or_else(|| Error::key(&ids[0]))?;
    let concerns = source.at("concerns")?.as_list().map(Vec::as_slice).unwrap_or(&[]);
    if concerns.iter().any(|concern| concern.as_str().is_some_and(|concern| UNUSABLE_SOURCE.contains(&concern))) {
        return Ok(Err("source-record-unusable".into()));
    }
    let kind = s(source, "kind");
    if s(row, "replacement") != "blank" {
        let replacement = convert_resource(s(row, "replacement"), "itm", s(row, "campaign"), context.game, context.names)?;
        if !["cre-template", "embedded-cre", "container"].contains(&kind) || replacement == item {
            return Ok(Err("replacement-not-applicable".into()));
        }
        if !context.items.contains(&replacement)
            && !(replacement == SHIPPED_REPLACEMENT && s(row, "id") == SHIPPED_REPLACEMENT_ROW)
        {
            return Ok(Err("replacement-item-absent".into()));
        }
        return Ok(Ok(Choice::Physical(ids[0].clone(), Some(replacement))));
    }
    if kind == "cre-template" || kind == "embedded-cre" {
        let slots = source.at("record")?.at("slots")?.as_list().map(Vec::as_slice).unwrap_or(&[]);
        if slots.iter().any(|slot| slot.as_int().is_some_and(|slot| !(21..=36).contains(&slot))) {
            return Ok(Ok(Choice::Physical(ids[0].clone(), Some("blank".into()))));
        }
    }
    Ok(Ok(Choice::Physical(ids[0].clone(), None)))
}

/// Why `count` rewards of a preset's optional `module` are left out: the mod
/// is not installed, or (when the game has any item of it) not the part of
/// it that adds them.
fn absent_mod_note(module: &str, count: usize, catalog_items: &ItemIndex) -> String {
    let prefixes = crate::templates::optional_mod_prefixes(module);
    let one = count == 1;
    if !catalog_items.resrefs().iter().any(|name| prefixes.iter().any(|prefix| name.starts_with(prefix))) {
        return format!(
            "{module} is not installed, so {count} {} from it {} left out",
            if one { "reward" } else { "rewards" },
            if one { "is" } else { "are" }
        );
    }
    format!(
        "{count} {} of {module} that this game does not have, so {} left out",
        if one { "reward needs an item" } else { "rewards need items" },
        if one { "it is" } else { "they are" }
    )
}

/// What the left-out rows are: those this game cannot use, then those whose
/// quest CIR cannot change yet, then what becomes of a left-out reward.
fn not_used_notes(rows: &[Value]) -> Vec<String> {
    let (waiting, unusable): (Vec<&Value>, Vec<&Value>) = rows.iter().partition(|row| s(row, "reason") == NO_ROUTE);
    let it_is = |rows: &[&Value]| if rows.len() == 1 { "it is" } else { "they are" };
    let mut notes = Vec::new();
    if !unusable.is_empty() {
        notes.push(format!(
            "{} from the lists cannot be used in this game, so {} left out",
            counted(&unusable),
            it_is(&unusable)
        ));
    }
    if !waiting.is_empty() {
        notes.push(format!(
            "{} from the lists {} that Chriz Item Randomiser cannot change yet, so {} left out",
            counted(&waiting),
            if waiting.len() == 1 { "belongs to a quest" } else { "belong to quests" },
            it_is(&waiting)
        ));
    }
    if rows.iter().any(|row| s(row, "kind") == "reward") {
        notes.push("A left-out reward stays as the game has it".to_string());
    }
    notes
}

/// How many rewards and places `rows` are, as "2 rewards and 1 place".
fn counted(rows: &[&Value]) -> String {
    let rewards = rows.iter().filter(|row| s(row, "kind") == "reward").count();
    [(rewards, "reward"), (rows.len() - rewards, "place")]
        .into_iter()
        .filter(|(count, _)| *count > 0)
        .map(|(count, noun)| if count == 1 { format!("{count} {noun}") } else { format!("{count} {noun}s") })
        .collect::<Vec<_>>()
        .join(" and ")
}

/// Return a data-only `{profile, plan}` preserving enabled authored rows.
///
/// A row this game cannot use is left out and listed in `not_used` instead of
/// stopping the configuration: an item, area or creature the game lacks, a
/// record that is not where the tables put it or is there more than once,
/// and each row named in `exclude` (row id -> reason). A left-out reward
/// stays as the game has it. `row_ids` maps each plan identity to its row.
/// `scans` holds the catalogues already validated.
#[allow(clippy::too_many_arguments)]
pub fn build_original_configuration(
    catalog: &Value,
    world: &Value,
    template_id: &str,
    items: Option<&Value>,
    targets: Option<&Value>,
    options: Option<&Value>,
    exclude: &HashMap<String, String>,
    scans: Option<&Scans>,
) -> Result<Value> {
    build_from(catalog, world, template_id, exclude, scans, &mut HashMap::new(), |game, names| {
        resolve_template(catalog, world, template_id, game, items, targets, options, names)
    })
}

/// `build_original_configuration` over an already resolved draft (the
/// reference's tests substitute `resolve_template` the same way).
pub fn build_original_configuration_from(
    catalog: &Value,
    world: &Value,
    template_id: &str,
    draft: &Value,
    exclude: &HashMap<String, String>,
    scans: Option<&Scans>,
) -> Result<Value> {
    build_from(catalog, world, template_id, exclude, scans, &mut HashMap::new(), |_, _| Ok(draft.clone()))
}

/// `build_original_configuration` with the draft from `resolve(game, names)`;
/// `defaults` keeps the charges of the items already read.
fn build_from(
    catalog: &Value,
    world: &Value,
    template_id: &str,
    exclude: &HashMap<String, String>,
    scans: Option<&Scans>,
    defaults: &mut HashMap<String, Value>,
    resolve: impl FnOnce(&str, Option<&EetNames>) -> Result<Value>,
) -> Result<Value> {
    let (catalog_items, index) = match scans {
        Some(scans) => {
            let (items, index) = scans.indexes(catalog, world)?;
            (Cow::Borrowed(items), Cow::Borrowed(index))
        }
        None => {
            let items = validate_catalog(catalog)?;
            (Cow::Owned(items), Cow::Owned(validate_world(world, catalog)?))
        }
    };
    let game = crate::platform::preset_game(world, template_id)?;
    let names = match game {
        "eet" => Some(eet_names(&crate::paths::path_argument(world.at("game_root")?)?)?),
        _ => None,
    };
    let draft = resolve(game, names.as_ref())?;
    let game_root = world.at("game_root")?.as_str().unwrap_or("").to_string();
    let option = |name: &str| draft.get("options").and_then(|options| options.get(name)).cloned().unwrap_or(Value::Null);
    if option("flail_heads").truthy() && !applied(&game_root, "flail_heads")? {
        return Err(Error::profile(format!(
            "The flail heads option needs Glaicus's conversation as Chriz Item Randomiser prepares it: {}",
            left_out(&game_root, "flail_heads")?
        )));
    }
    // A reward whose script or conversation the installer could not prepare
    // stays as installed: its rows are left out, and the player is told why.
    let prepared = applied_changes(&game_root)?;
    let mut unprepared: BTreeMap<&'static str, Vec<Value>> = BTreeMap::new();
    let mut enabled_sources: Vec<&Value> = Vec::new();
    let mut enabled_targets: Vec<&Value> = Vec::new();
    let draft_items = draft.at("items")?.as_list().map(Vec::as_slice).unwrap_or(&[]);
    let draft_targets = draft.at("targets")?.as_list().map(Vec::as_slice).unwrap_or(&[]);
    for (rows, change_of, enabled) in [
        (draft_items, source_change as fn(&Value) -> Result<Option<&'static str>>, &mut enabled_sources),
        (draft_targets, target_change as fn(&Value) -> Result<Option<&'static str>>, &mut enabled_targets),
    ] {
        for row in rows {
            let is_enabled = row.get("enabled").is_some_and(Value::truthy);
            let change = if is_enabled { change_of(row)? } else { None };
            match change {
                Some(change) if !prepared.contains(change) => {
                    unprepared.entry(change).or_default().push(row.at("id")?.clone());
                }
                _ if is_enabled => enabled.push(row),
                _ => {}
            }
        }
    }
    let shield = if option("more_spell_shield").truthy() {
        let randomized = enabled_sources.iter().any(|row| crate::original_options::is_spell_shield_row(row));
        Some(crate::original_options::spell_shield_spec(world, randomized)?)
    } else {
        None
    };
    let mut not_used: Vec<Value> = Vec::new();
    let leave_out = |not_used: &mut Vec<Value>, row: &Value, kind: &str, reason: &Value| -> Result<()> {
        not_used.push(Value::Dict(crate::dict! {
            "id" => row.at("id")?.clone(), "row" => row.at("source_ref")?.clone(), "kind" => kind, "reason" => reason.clone(),
        }));
        Ok(())
    };

    // The old installer omits a physical source row only when the exact item
    // has no matching record. Its authored location remains in the pool.
    let world_sources = world.at("sources")?.as_list().map(Vec::as_slice).unwrap_or(&[]);
    let mut candidates: Vec<&Value> = Vec::new();
    let mut absent_mods: BTreeMap<String, usize> = BTreeMap::new();
    for row in enabled_sources {
        let identity = s(row, "id");
        if let Some(reason) = exclude.get(identity) {
            leave_out(&mut not_used, row, "reward", &Value::from(reason.as_str()))?;
            continue;
        }
        if shield.as_ref().is_some_and(|shield| shield.truthy()) && crate::original_options::is_spell_shield_row(row) {
            if !catalog_items.contains(s(row, "item_resref")) {
                return Err(Error::profile("More Spell Shield scrolls requires SCRL8X.ITM"));
            }
            candidates.push(row);
            continue;
        }
        let problem = row.get("binding_problem").and_then(Value::as_str);
        if problem == Some(NO_ROUTE) {
            leave_out(&mut not_used, row, "reward", &Value::from(NO_ROUTE))?;
            continue;
        }
        if problem == Some("original-source-has-no-matching-record") {
            continue;
        }
        if problem == Some("optional-item-absent") {
            *absent_mods.entry(crate::json::py_str(row.at("optional_mod")?)).or_insert(0) += 1;
            continue;
        }
        if row.at("availability_reason")?.as_str() == Some("source-resource-unreadable") {
            // The scan could not read its file (the notes name it); it stays as the game has it.
            leave_out(&mut not_used, row, "reward", row.at("availability_reason")?)?;
            continue;
        }
        let adapter = row.get("adapter").and_then(Value::as_str);
        if adapter == Some("unshey")
            && !world_sources.iter().any(|source| {
                source.get("resource").is_some_and(|value| value.py_eq(row.at("source_resource").unwrap_or(&Value::Null)))
                    && source.get("item").is_some_and(|value| value.py_eq(row.at("item_resref").unwrap_or(&Value::Null)))
            })
        {
            continue;
        }
        if !row.at("available")?.truthy() && adapter != Some("unshey") {
            leave_out(&mut not_used, row, "reward", row.at("availability_reason")?)?;
        } else if !USABLE_PROBLEMS.contains(&problem) || (problem.is_none() && !row.at("binding_problem")?.is_null()) {
            leave_out(&mut not_used, row, "reward", row.at("binding_problem")?)?;
        } else {
            candidates.push(row);
        }
    }

    let wanted_defaults: BTreeSet<String> = candidates
        .iter()
        .filter(|row| matches!(row.get("adapter").and_then(Value::as_str), Some("virtual" | "scripted" | "unshey")))
        .map(|row| s(row, "item_resref").to_string())
        .collect();
    for name in &wanted_defaults {
        if !catalog_items.contains(name) {
            return Err(Error::key(name));
        }
    }
    let unread: BTreeSet<String> = wanted_defaults.iter().filter(|name| !defaults.contains_key(*name)).cloned().collect();
    defaults.extend(item_defaults(catalog, &catalog_items, &unread)?);
    let context = Context {
        catalog,
        world,
        game,
        names: names.as_ref(),
        items: &catalog_items,
        index: &index,
        physical: source_lookup(world),
        shield: shield.as_ref().filter(|shield| shield.truthy()),
        defaults,
    };

    let mut chosen: Vec<(&Value, Choice, Vec<Dict>)> = Vec::new();
    let mut claimed: HashSet<String> = HashSet::new();
    let held = holdings(world_sources);
    for row in candidates {
        let choice = match choose(row, &context)? {
            Ok(choice) => choice,
            Err(reason) => {
                leave_out(&mut not_used, row, "reward", &Value::from(reason))?;
                continue;
            }
        };
        // Item Randomiser's second copies of this reward, removed when it moves.
        let (identity, route) = match &choice {
            Choice::Registration(registration) => (
                crate::json::py_str(registration.get("id").unwrap_or(&Value::Null)),
                crate::original_catalog::mode2_route_source(registration.get("authored").unwrap_or(&Value::Null))?,
            ),
            Choice::Physical(identity, _) => (identity.clone(), None),
        };
        let item = s(row, "item_resref");
        let (campaign, ident) = (s(row, "campaign"), s(row, "ident"));
        let copies =
            expected_copies(&identity, campaign, ident, item, &index, &held, game, names.as_ref(), route.as_deref())?;
        if copies.iter().any(|copy| !catalog_items.contains(copy.get("replacement").and_then(Value::as_str).unwrap_or(item))) {
            leave_out(&mut not_used, row, "reward", &Value::from("replacement-item-absent"))?;
            continue;
        }
        let mut unreviewed = false;
        for copy in &copies {
            if !reviewed(&Value::Dict(copy.clone()))? {
                unreviewed = true;
                break;
            }
        }
        if unreviewed {
            leave_out(&mut not_used, row, "reward", &Value::from("second-copy-name-unreviewed"))?;
            continue;
        }
        let records: Vec<String> = match &choice {
            Choice::Physical(identity, _) => vec![identity.clone()],
            Choice::Registration(registration) => registration
                .get("physical_source_ids")
                .and_then(Value::as_list)
                .into_iter()
                .flatten()
                .map(crate::json::py_str)
                .collect(),
        };
        // One physical record can be moved by one row only.
        if records.iter().any(|record| claimed.contains(record)) {
            leave_out(&mut not_used, row, "reward", &Value::from("source-record-used-twice"))?;
            continue;
        }
        claimed.extend(records);
        chosen.push((row, choice, copies));
    }
    let mut places: Vec<&Value> = Vec::new();
    for row in enabled_targets {
        if let Some(reason) = exclude.get(s(row, "id")) {
            leave_out(&mut not_used, row, "place", &Value::from(reason.as_str()))?;
        } else if row.at("binding_problem")?.as_str() == Some(NO_ROUTE) {
            leave_out(&mut not_used, row, "place", &Value::from(NO_ROUTE))?;
        } else if !index.resources.contains(s(row, "area_resource")) {
            leave_out(&mut not_used, row, "place", &Value::from("area-resource-absent"))?;
        } else if row.at("binding_problem")?.as_str().is_some_and(|problem| {
            UNREADABLE_PLACE_PROBLEMS.contains(&problem) || problem == "creature-never-appears"
        }) {
            leave_out(&mut not_used, row, "place", row.at("binding_problem")?)?;
        } else {
            places.push(row);
        }
    }
    if chosen.is_empty() || places.is_empty() {
        return Err(Error::profile("Original template has no enabled sources or locations"));
    }

    let include: Vec<String> = chosen
        .iter()
        .map(|(row, _, _)| s(row, "item_resref").to_string())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let name = draft.at("name")?.as_str().unwrap_or("").to_string();
    let profile = edit_profile(
        &new_profile(catalog, template_id, &name)?,
        catalog,
        ProfileEdit { include: &include, ..ProfileEdit::default() },
    )?;
    let mut plan = new_binding_plan(&profile, catalog, world, scans)?.as_dict().cloned().unwrap_or_default();
    plan.insert("original_template_id", template_id);
    plan.insert("original_choices", draft.at("choices")?.clone());
    plan.insert("original_options", draft.at("options")?.clone());
    if let Some(shield) = &shield {
        plan.insert("spell_shield", shield.clone());
    }
    if option("cursed_disguise").truthy() {
        let log = Path::new(catalog.at("game_root")?.as_str().unwrap_or("")).join("WeiDU.log");
        let installed = match crate::paths::exists(&log) {
            Ok(true) => std::fs::read(&log).map_err(|error| Error::os(&error, &log))?,
            Ok(false) => Vec::new(),
            Err(error) => return Err(Error::win(&error, &log)),
        };
        let mut hashes = Dict::new();
        for row in catalog.at("items")?.as_list().into_iter().flatten() {
            hashes.insert(s(row, "resref"), row.at("sha256")?.clone());
        }
        let item_revisions = crate::cursed_disguises::item_revisions_installed(&installed);
        plan.insert("cursed_disguises", crate::cursed_disguises::cursed_disguise_spec(&hashes, game, item_revisions)?);
    }
    plan.insert("target_registrations", Value::List(Vec::new()));
    // Filled in place below; the key keeps Python's position before
    // `replacements`.
    plan.insert("randomization", Value::Null);
    let loss_percent =
        if option("loss_mode").as_str() == Some("some") { option("loss_percent") } else { Value::Int(0) };
    let mut randomization = crate::dict! {
        "sources" => Dict::new(), "targets" => Dict::new(),
        "row_filters" => crate::dict! {"sources" => Dict::new(), "targets" => Dict::new()},
        "loss_percent" => loss_percent, "unrandomized_percent" => option("unrandomized_percent"), "overflow" => true,
    };
    let mut registrations: Vec<Value> = Vec::new();
    let mut replacements: Option<Dict> = None;
    let mut by_item: BTreeMap<String, (Vec<String>, Vec<String>)> = BTreeMap::new();
    let mut source_rows: Vec<(String, &Value)> = Vec::new();
    let mut row_ids = Dict::new();
    let mut copies: Vec<&Dict> = chosen.iter().flat_map(|(_, _, copies)| copies).collect();
    copies.sort_by(|a, b| (s_of(a, "source_id"), s_of(a, "id")).cmp(&(s_of(b, "source_id"), s_of(b, "id"))));
    if !copies.is_empty() {
        plan.insert("duplicates", Value::List(copies.into_iter().cloned().map(Value::Dict).collect()));
    }
    for (row, choice, _) in chosen {
        let item_choices = by_item.entry(s(row, "item_resref").to_string()).or_default();
        let identity = match choice {
            Choice::Registration(registration) => {
                let identity = crate::json::py_str(registration.get("id").unwrap_or(&Value::Null));
                registrations.push(Value::Dict(registration));
                item_choices.1.push(identity.clone());
                identity
            }
            Choice::Physical(identity, replacement) => {
                item_choices.0.push(identity.clone());
                if let Some(replacement) = replacement {
                    replacements.get_or_insert_with(Dict::new).insert(identity.as_str(), replacement);
                }
                identity
            }
        };
        row_ids.insert(identity.as_str(), row.at("id")?.clone());
        source_rows.push((identity, row));
    }
    if let Some(replacements) = replacements {
        // `plan.setdefault("replacements", {})` during the loop above.
        plan.insert("replacements", replacements);
    }
    let mut plan_sources = Vec::new();
    for (item, (mut source_ids, mut registration_ids)) in by_item {
        source_ids.sort();
        registration_ids.sort();
        plan_sources.push(Value::Dict(crate::dict! {
            "item" => item, "policy" => "relocate", "source_ids" => Value::from(source_ids),
            "registration_ids" => Value::from(registration_ids),
        }));
    }
    let mut target_registrations = Vec::new();
    let mut plan_targets = Vec::new();
    let mut target_rows: Vec<(String, &Value)> = Vec::new();
    for row in places {
        let sha = index
            .resource(world, s(row, "area_resource"))
            .and_then(|resource| resource.get("sha256"))
            .cloned()
            .ok_or_else(|| Error::key(s(row, "area_resource")))?;
        let registration = original_target(row, &sha)?;
        let identity = crate::json::py_str(registration.get("id").unwrap_or(&Value::Null));
        target_registrations.push(Value::Dict(registration));
        plan_targets.push(Value::Dict(crate::dict! {"id" => identity.as_str(), "capacity" => 1, "optional" => false}));
        row_ids.insert(identity.as_str(), row.at("id")?.clone());
        target_rows.push((identity, row));
    }

    let mut targets_by_ident: HashMap<(String, String), Vec<String>> = HashMap::new();
    let group_of = |row: &Value| {
        if s(row, "ident") == "x" {
            String::new()
        } else {
            let field = |key: &str| crate::json::py_str(row.get(key).unwrap_or(&Value::Null));
            format!("{}:{}", field("campaign"), field("ident"))
        }
    };
    for (identity, row) in &target_rows {
        if s(row, "ident") != "x" {
            targets_by_ident
                .entry((s(row, "campaign").to_string(), s(row, "ident").to_string()))
                .or_default()
                .push(identity.clone());
        }
        let tiers = randomization.get_mut("targets").and_then(Value::as_dict_mut).expect("built above");
        tiers.insert(identity.as_str(), Value::from(vec![tier(row)]));
        let filters = randomization
            .get_mut("row_filters")
            .and_then(|filters| filters.get_mut("targets"))
            .and_then(Value::as_dict_mut)
            .expect("built above");
        filters.insert(identity.as_str(), crate::dict! {"chance" => row.at("chance")?.clone(), "group" => group_of(row)});
    }
    for (identity, row) in &source_rows {
        let key = (s(row, "campaign").to_string(), s(row, "ident").to_string());
        let excluded: Vec<Value> = targets_by_ident
            .get(&key)
            .into_iter()
            .flatten()
            .map(|target| Value::Dict(crate::dict! {"id" => target.as_str(), "ordinal" => 1}))
            .collect();
        let mut rule = crate::dict! {
            "tier" => tier(row), "loss_exempt" => matches!(s(row, "tier"), "fa" | "ka"),
            "exclude_targets" => Value::List(excluded),
        };
        if option("totsc_tome").truthy() && s(row, "campaign") == "bg1" && s(row, "ident") == "b2c" {
            let variants = crate::original_options::tome_variants(s(row, "campaign"), game, names.as_ref())?;
            rule.insert("reward_variants", Value::from(variants));
        }
        randomization.get_mut("sources").and_then(Value::as_dict_mut).expect("built above").insert(identity.as_str(), rule);
        let filters = randomization
            .get_mut("row_filters")
            .and_then(|filters| filters.get_mut("sources"))
            .and_then(Value::as_dict_mut)
            .expect("built above");
        filters.insert(identity.as_str(), crate::dict! {"chance" => row.at("chance")?.clone(), "group" => group_of(row)});
    }
    let by_id = |a: &Value, b: &Value| s(a, "id").cmp(s(b, "id"));
    let mut plan_registrations = plan.get("registrations").and_then(Value::as_list).cloned().unwrap_or_default();
    plan_registrations.extend(registrations);
    plan_registrations.sort_by(by_id);
    target_registrations.sort_by(by_id);
    let mut all_targets = plan.get("targets").and_then(Value::as_list).cloned().unwrap_or_default();
    all_targets.extend(plan_targets);
    all_targets.sort_by(by_id);
    let mut all_sources = plan.get("sources").and_then(Value::as_list).cloned().unwrap_or_default();
    all_sources.extend(plan_sources);
    plan.insert("registrations", Value::List(plan_registrations));
    plan.insert("target_registrations", Value::List(target_registrations));
    plan.insert("targets", Value::List(all_targets));
    plan.insert("sources", Value::List(all_sources));
    plan.insert("randomization", randomization);

    let mut left = Vec::new();
    for change in unprepared.keys() {
        left.push(Value::from(left_out(&game_root, change)?));
    }
    let absent: Vec<Value> =
        absent_mods.iter().map(|(module, count)| Value::from(absent_mod_note(module, *count, &catalog_items))).collect();
    let notes: Vec<Value> = unreadable_notes(world)
        .into_iter()
        .chain(names.iter().flat_map(|names| names.notes.iter().cloned()))
        .chain(not_used_notes(&not_used))
        .map(Value::from)
        .collect();
    Ok(Value::Dict(crate::dict! {
        "profile" => profile, "plan" => plan, "left_out" => Value::List(left), "absent_mods" => Value::List(absent),
        "not_used" => Value::List(not_used), "not_used_notes" => Value::List(notes), "row_ids" => row_ids,
    }))
}

/// Source identities whose tier lacks a place the campaign check accepts.
///
/// With overflow, each tier with rewards needs a place that can take any
/// reward and, when originals may be kept, one that no reward's home claims.
/// Without a reward of such a tier its claims go too, so this repeats until
/// stable.
fn placeless_sources(plan: &Value) -> Result<Vec<String>> {
    let randomization = plan.at("randomization")?;
    let mut kinds: HashMap<String, String> = HashMap::new();
    for registration in plan.at("target_registrations")?.as_list().into_iter().flatten() {
        let kind = target_record(registration)?.at("kind")?.as_str().unwrap_or("").to_string();
        kinds.insert(s(registration, "id").to_string(), kind);
    }
    let mut slots: HashMap<String, HashSet<(String, usize)>> = HashMap::new();
    for (identity, tiers) in randomization.at("targets")?.as_dict().into_iter().flat_map(Dict::iter) {
        if kinds.get(identity).is_some_and(|kind| OVERFLOW_TARGET_KINDS.contains(&kind.as_str())) {
            for (ordinal, tier) in tiers.as_list().into_iter().flatten().enumerate() {
                slots.entry(crate::json::py_str(tier)).or_default().insert((identity.to_string(), ordinal + 1));
            }
        }
    }
    let keeping = randomization.at("unrandomized_percent")?.as_int().unwrap_or(0) > 0;
    let mut rules: Vec<(String, &Value)> = randomization
        .at("sources")?
        .as_dict()
        .into_iter()
        .flat_map(Dict::iter)
        .map(|(identity, rule)| (identity.to_string(), rule))
        .collect();
    let mut placeless: BTreeSet<String> = BTreeSet::new();
    let empty = HashSet::new();
    loop {
        let mut claimed: HashSet<(String, usize)> = HashSet::new();
        for (_, rule) in &rules {
            for entry in rule.at("exclude_targets")?.as_list().into_iter().flatten() {
                let ordinal = entry.at("ordinal")?.as_int().unwrap_or(0).max(0) as usize;
                claimed.insert((s(entry, "id").to_string(), ordinal));
            }
        }
        let failing: BTreeSet<String> = rules
            .iter()
            .filter(|(_, rule)| {
                let tier_slots = slots.get(s(rule, "tier")).unwrap_or(&empty);
                if keeping {
                    tier_slots.iter().all(|slot| claimed.contains(slot))
                } else {
                    tier_slots.is_empty()
                }
            })
            .map(|(identity, _)| identity.clone())
            .collect();
        if failing.is_empty() {
            return Ok(placeless.into_iter().collect());
        }
        rules.retain(|(identity, _)| !failing.contains(identity));
        placeless.extend(failing);
    }
}

/// Build from the original tables, leaving out the rows the runtime cannot
/// use.
///
/// The runtime check of the built configuration names every row it cannot
/// use (a quest reward without a reviewed adapter, an item its creature
/// cannot drop, a recipient it cannot address); those rows are left out and
/// listed in `not_used`, and the configuration is built again until the
/// check passes. The rounds share the validated catalogues (`scans`, if the
/// caller has them), the resolved tables and the item charges they read.
pub fn fit_original_configuration(
    catalog: &Value,
    world: &Value,
    template_id: &Value,
    items: Option<&Value>,
    targets: Option<&Value>,
    options: Option<&Value>,
    scans: Option<&Scans>,
) -> Result<Value> {
    fit_from(catalog, world, template_id, scans, &|template_id, game, names| {
        resolve_template(catalog, world, template_id, game, items, targets, options, names)
    })
}

/// `fit_original_configuration` over an already resolved draft.
pub fn fit_original_configuration_from(
    catalog: &Value,
    world: &Value,
    template_id: &Value,
    draft: &Value,
    scans: Option<&Scans>,
) -> Result<Value> {
    fit_from(catalog, world, template_id, scans, &|_, _, _| Ok(draft.clone()))
}

/// How `fit_from` gets a draft: `resolve_template` for (template id, game,
/// names), or the draft a test patched in.
type Resolve<'a> = dyn Fn(&str, &str, Option<&EetNames>) -> Result<Value> + 'a;

fn fit_from(
    catalog: &Value,
    world: &Value,
    template_id: &Value,
    scans: Option<&Scans>,
    resolve: &Resolve<'_>,
) -> Result<Value> {
    let validated;
    let own;
    let scans = match scans {
        Some(scans) => scans,
        None => {
            let items = validate_catalog(catalog)?;
            validated = (items, validate_world(world, catalog)?);
            own = Scans::new(catalog, world, &validated.0, &validated.1);
            &own
        }
    };
    let (catalog_items, index) = scans.indexes(catalog, world)?;
    // preset_game's lookup of an unknown or non-text identity, after it has
    // told the game apart.
    let template_id = match template_id {
        Value::Str(identity) => identity.as_str(),
        value => {
            crate::platform::game_platform(world)?;
            return Err(match value {
                Value::List(_) | Value::Dict(_) => Error::type_error(format!("unhashable type: '{}'", value.type_name())),
                other => Error::value(format!("Unknown original template: {}", crate::json::py_str(other))),
            });
        }
    };
    let mut resolved: Option<Value> = None;
    let mut defaults = HashMap::new();
    let mut exclude: HashMap<String, String> = HashMap::new();
    for _ in 0..FIT_ROUNDS {
        let generated = build_from(catalog, world, template_id, &exclude, Some(scans), &mut defaults, |game, names| {
            // The same template, game and names in every round.
            match &resolved {
                Some(draft) => Ok(draft.clone()),
                None => {
                    let draft = resolve(template_id, game, names)?;
                    resolved = Some(draft.clone());
                    Ok(draft)
                }
            }
        })?;
        let row_ids = generated.at("row_ids")?;
        let placeless = placeless_sources(generated.at("plan")?)?;
        if !placeless.is_empty() {
            for identity in placeless {
                let row = crate::json::py_str(row_ids.at(&identity)?);
                exclude.insert(row, "no-place-in-tier".into());
            }
            continue;
        }
        let campaign = compile_campaign(generated.at("profile")?, catalog, world, generated.at("plan")?, Some(scans))?;
        let mut problems = Dict::new();
        crate::native_export::compile(
            &campaign,
            world,
            index,
            catalog_items,
            catalog,
            Some(&mut problems),
            false,
            None,
            None,
            None,
        )?;
        if problems.is_empty() {
            return Ok(generated);
        }
        let mut sorted: Vec<(&str, &Value)> = problems.iter().collect();
        sorted.sort_by(|a, b| a.0.cmp(b.0));
        for (identity, reason) in sorted {
            let reason = crate::json::py_str(reason);
            match row_ids.get(identity) {
                Some(row) => {
                    exclude.insert(crate::json::py_str(row), reason);
                }
                None => return Err(Error::profile(reason)),
            }
        }
    }
    Err(Error::profile(
        "The original tables could not be fitted to this game; the runtime check kept finding rows it cannot use",
    ))
}
