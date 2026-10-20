//! One applied revision for each campaign start of the game
//! (platform::starts; the reference's `starts.py`).
//!
//! The start that begins with the whole game uses the configuration as it
//! is. A later start (Siege of Dragonspear, Shadows of Amn on EET, Throne of
//! Bhaal) gets a revision of its own with only the rewards and places it
//! reaches:
//!
//! - An area tells its part of the game by its name (platform::area_part).
//! - A creature template is where the areas that place it are.
//! - Any other record (a shop, a script, a conversation, an added item) is
//!   where the table that lists it puts it: BG1, Siege of Dragonspear, or all
//!   of BG2. A Throne of Bhaal start therefore leaves BG2 shops and scripts as
//!   they are.
//! - A record whose place is unknown is left out of later starts.
//!
//! A reward left out stays as the game has it; nothing is put where the
//! start cannot go. What is left is checked as the whole configuration is: a
//! reward whose tier has no place left, or that the runtime check names, is
//! left out too. A start with nothing left gets no revision, so New Game
//! tells the player that this start is not randomised.

use crate::campaigns::{compile_campaign, write_campaign, OVERFLOW_TARGET_KINDS};
use crate::error::{Error, ErrorKind, Result};
use crate::json::{py_str, Dict, Value};
use crate::platform::{self, area_part, campaign_parts, Part};
use crate::profiles::{edit_profile, validate_catalog, ProfileEdit};
use crate::sources::{validate_world, Scans, WorldIndex};
use std::cmp::Ordering;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::Path;

/// Rounds of leaving out what the check names before giving up on a start.
const FIT_ROUNDS: usize = 8;

/// A record's table, as the parts of the game it covers are read from it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Table {
    Bg1,
    Sod,
    Bg2,
    /// No table, or a value that names none.
    Unknown,
}

impl Table {
    fn of(value: Option<&Value>) -> Table {
        match value.and_then(Value::as_str) {
            Some(name) => Table::named(name),
            None => Table::Unknown,
        }
    }

    /// The table campaign ("bg1", "sod" or "bg2") of this name.
    fn named(name: &str) -> Table {
        match name {
            "bg1" => Table::Bg1,
            "sod" => Table::Sod,
            "bg2" => Table::Bg2,
            _ => Table::Unknown,
        }
    }

    fn parts(self) -> Option<&'static [Part]> {
        let name = match self {
            Table::Bg1 => "bg1",
            Table::Sod => "sod",
            Table::Bg2 => "bg2",
            Table::Unknown => return None,
        };
        Some(campaign_parts(name).expect("a table campaign"))
    }
}

/// The table ("bg1", "sod" or "bg2") a row identity such as bg2:base:item:7
/// names.
fn table_campaign(selector: &Value) -> Table {
    match selector.as_str() {
        Some(selector) => Table::named(selector.split(':').next().unwrap_or("")),
        None => Table::Unknown,
    }
}

/// A value as a dict or set key, with Python's equality: 1, 1.0 and True are
/// the same key; lists and dicts are unhashable.
fn hash_key(value: &Value) -> Result<String> {
    Ok(match value {
        Value::Str(text) => format!("s{text}"),
        Value::Int(number) => format!("n{number}"),
        Value::Bool(flag) => format!("n{}", i64::from(*flag)),
        Value::Float(number) if number.fract() == 0.0 && number.abs() < 9.2e18 => format!("n{}", *number as i64),
        Value::Float(number) => format!("f{number:?}"),
        Value::BigInt(_) => format!("n{}", py_str(value)),
        Value::Null => "none".into(),
        Value::List(_) | Value::Dict(_) => {
            return Err(Error::type_error(format!("unhashable type: '{}'", value.type_name())))
        }
    })
}

/// `value.get(name)` on a value that must be a mapping.
fn get<'a>(value: &'a Value, name: &str) -> Result<Option<&'a Value>> {
    match value {
        Value::Dict(fields) => Ok(fields.get(name)),
        other => Err(Error::attribute(other.type_name(), "get")),
    }
}

/// The text identities of a list, as `identity in a_set` sees them: other
/// hashable values can match no identity, unhashable ones are a TypeError.
fn identities(value: &Value) -> Result<Vec<Value>> {
    let values = crate::py::iter_value(value)?;
    for value in &values {
        if matches!(value, Value::List(_) | Value::Dict(_)) {
            return Err(Error::type_error(format!("unhashable type: '{}'", value.type_name())));
        }
    }
    Ok(values)
}

fn contains(set: &HashSet<String>, identity: &Value) -> bool {
    identity.as_str().is_some_and(|identity| set.contains(identity))
}

/// `{row["id"]: row for row in rows}`.
fn by_id(rows: &Value) -> Result<HashMap<String, Value>> {
    let mut result = HashMap::new();
    for row in crate::py::iter_value(rows)? {
        let identity = row.at("id")?;
        if matches!(identity, Value::List(_) | Value::Dict(_)) {
            return Err(Error::type_error(format!("unhashable type: '{}'", identity.type_name())));
        }
        result.insert(py_str(identity), row);
    }
    Ok(result)
}

/// Where the sources and targets of one configuration are, in one game.
struct Places<'a> {
    platform: &'a str,
    world: &'a Value,
    index: &'a WorldIndex,
    /// The parts of the areas whose actors place each creature template
    /// (None for an area whose name does not tell).
    placed: HashMap<String, BTreeSet<Option<Part>>>,
    /// The table each physical source an Item Randomiser row selects comes
    /// from.
    campaigns: HashMap<String, Table>,
}

impl<'a> Places<'a> {
    fn new(
        catalog: &Value,
        world: &'a Value,
        index: &'a WorldIndex,
        plan: &Value,
        platform: &'a str,
        tables: Option<&crate::original_registrations::TableMemo>,
    ) -> Result<Self> {
        let mut placed: HashMap<String, BTreeSet<Option<Part>>> = HashMap::new();
        for target in world.at("targets")?.as_list().map(Vec::as_slice).unwrap_or(&[]) {
            let template = if target.at("kind")?.as_str() == Some("actor") {
                get(target.at("selector")?, "template_resource")?
            } else {
                None
            };
            if let Some(Value::Str(template)) = template.filter(|template| template.truthy()) {
                let area: String = py_str(target.at("resource")?).chars().skip(4).collect();
                placed.entry(template.clone()).or_default().insert(area_part(platform, &area));
            }
        }
        let mut campaigns = HashMap::new();
        if plan.as_dict().is_some_and(|plan| plan.contains_key("original_template_id")) {
            let tables = crate::original_registrations::table_rows(catalog, world, plan, tables)?;
            for row in tables.sources.values() {
                let selected = match get(row, "source_ids")? {
                    Some(value) if value.truthy() => identities(value)?,
                    _ => Vec::new(),
                };
                for identity in selected {
                    let campaign = Table::of(Some(row.at("campaign")?));
                    if let Value::Str(identity) = identity {
                        campaigns.insert(identity, campaign);
                    }
                }
            }
        }
        Ok(Places { platform, world, index, placed, campaigns })
    }

    fn source(&self, identity: &str) -> Result<&'a Value> {
        self.index.source(self.world, identity).ok_or_else(|| Error::key(identity))
    }

    fn target(&self, identity: &str) -> Option<&'a Value> {
        self.index.target(self.world, identity)
    }

    fn campaign(&self, identity: &str) -> Table {
        self.campaigns.get(identity).copied().unwrap_or(Table::Unknown)
    }

    fn parts(&self, resource: &str, campaign: Table) -> Option<Vec<Part>> {
        let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
        if kind == "are" {
            if let Some(part) = area_part(self.platform, name) {
                return Some(vec![part]);
            }
        } else if kind == "cre" {
            if let Some(parts) = self.placed.get(resource) {
                if !parts.is_empty() && !parts.contains(&None) {
                    return Some(parts.iter().flatten().copied().collect());
                }
            }
        }
        campaign.parts().map(<[Part]>::to_vec)
    }

    fn reached(&self, resource: &Value, campaign: Table, reach: &[Part]) -> Result<bool> {
        let resource = match resource {
            Value::Str(resource) => resource,
            other => return Err(Error::attribute(other.type_name(), "partition")),
        };
        Ok(self.parts(resource, campaign).is_some_and(|parts| parts.iter().all(|part| reach.contains(part))))
    }
}

/// A list kept where `keep` holds for its row's `id`.
fn keep_rows(rows: &Value, outside: &HashSet<String>) -> Result<Value> {
    let mut kept = Vec::new();
    for row in crate::py::iter_value(rows)? {
        let identity = row.at("id")?;
        if matches!(identity, Value::List(_) | Value::Dict(_)) {
            return Err(Error::type_error(format!("unhashable type: '{}'", identity.type_name())));
        }
        if !contains(outside, identity) {
            kept.push(row);
        }
    }
    Ok(Value::List(kept))
}

/// The configuration without these source and target identities.
fn without(
    profile: &Value,
    plan: &Value,
    catalog: &Value,
    sources: &HashSet<String>,
    targets: &HashSet<String>,
) -> Result<(Value, Value)> {
    let mut plan = plan.clone();
    let mut choices = Vec::new();
    let mut items: Vec<String> = Vec::new();
    for mut choice in crate::py::iter_value(plan.at("sources")?)? {
        for key in ["source_ids", "registration_ids"] {
            let kept: Vec<Value> =
                identities(choice.at(key)?)?.into_iter().filter(|identity| !contains(sources, identity)).collect();
            match &mut choice {
                Value::Dict(fields) => fields.insert(key, Value::List(kept)),
                other => {
                    return Err(Error::type_error(format!("'{}' object does not support item assignment", other.type_name())))
                }
            }
        }
        if choice.at("source_ids")?.truthy() || choice.at("registration_ids")?.truthy() {
            choices.push(choice);
        } else {
            items.push(py_str(choice.at("item")?));
        }
    }
    let fields = match &mut plan {
        Value::Dict(fields) => fields,
        other => return Err(other.subscript_error()),
    };
    fields.insert("sources", Value::List(choices));
    let registrations = keep_rows(fields.get("registrations").ok_or_else(|| Error::key("registrations"))?, sources)?;
    fields.insert("registrations", registrations);
    if let Some(replacements) = fields.get_mut("replacements") {
        match replacements {
            Value::Dict(replacements) => replacements.retain(|key, _| !sources.contains(key)),
            other => return Err(Error::attribute(other.type_name(), "items")),
        }
    }
    let kept = keep_rows(fields.get("targets").ok_or_else(|| Error::key("targets"))?, targets)?;
    fields.insert("targets", kept);
    if let Some(rows) = fields.get("target_registrations") {
        let kept = keep_rows(rows, targets)?;
        fields.insert("target_registrations", kept);
    }
    if fields.get("randomization").is_some_and(Value::truthy) {
        let randomization = match fields.get_mut("randomization") {
            Some(Value::Dict(randomization)) => randomization,
            Some(other) => return Err(Error::attribute(other.type_name(), "get")),
            None => unreachable!("checked"),
        };
        // `randomization.get("row_filters", {"sources": {}, "targets": {}})`:
        // without row filters there is nothing to remove from them.
        for (kind, identities) in [("sources", sources), ("targets", targets)] {
            for identity in identities {
                pop(randomization.get_mut(kind).ok_or_else(|| Error::key(kind))?, identity)?;
                if let Some(filters) = randomization.get_mut("row_filters") {
                    pop(filters.at_mut(kind)?, identity)?;
                }
            }
        }
        let rules = match randomization.get_mut("sources") {
            Some(Value::Dict(rules)) => rules,
            Some(other) => return Err(Error::attribute(other.type_name(), "values")),
            None => return Err(Error::key("sources")),
        };
        for rule in rules.values_mut() {
            let excluded = rule.at("exclude_targets")?;
            let mut kept = Vec::new();
            for entry in crate::py::iter_value(excluded)? {
                let identity = entry.at("id")?;
                if matches!(identity, Value::List(_) | Value::Dict(_)) {
                    return Err(Error::type_error(format!("unhashable type: '{}'", identity.type_name())));
                }
                if !contains(targets, identity) {
                    kept.push(entry);
                }
            }
            match rule {
                Value::Dict(rule) => rule.insert("exclude_targets", Value::List(kept)),
                other => {
                    return Err(Error::type_error(format!("'{}' object does not support item assignment", other.type_name())))
                }
            }
        }
    }
    let shield_source = match fields.get("spell_shield") {
        Some(shield) if shield.truthy() => Some(shield.at("source_id")?.clone()),
        _ => None,
    };
    if let Some(source) = shield_source {
        if matches!(source, Value::List(_) | Value::Dict(_)) {
            return Err(Error::type_error(format!("unhashable type: '{}'", source.type_name())));
        }
        if contains(sources, &source) {
            // Without its randomised source the start keeps the shop as
            // installed.
            let mut options = match fields.get("original_options") {
                Some(Value::Dict(options)) => options.clone(),
                Some(other) => return Err(Error::type_error(format!("'{}' object is not a mapping", other.type_name()))),
                None => return Err(Error::key("original_options")),
            };
            options.insert("more_spell_shield", false);
            fields.insert("original_options", Value::Dict(options));
            fields.remove("spell_shield");
        }
    }
    let profile = if items.is_empty() {
        profile.clone()
    } else {
        edit_profile(profile, catalog, ProfileEdit { exclude: &items, ..ProfileEdit::default() })?
    };
    Ok((profile, plan))
}

/// `mapping.pop(identity, None)`.
fn pop(mapping: &mut Value, identity: &str) -> Result<()> {
    match mapping {
        Value::Dict(mapping) => {
            mapping.remove(identity);
            Ok(())
        }
        other => Err(Error::attribute(other.type_name(), "pop")),
    }
}

/// A Python number of reward units or capacity.
#[derive(Clone, Copy, Debug)]
enum Number {
    Int(i128),
    Float(f64),
}

impl Number {
    fn of(value: &Value, total: Number) -> Result<Number> {
        match value {
            Value::Int(number) => Ok(Number::Int(i128::from(*number))),
            Value::Bool(flag) => Ok(Number::Int(i128::from(*flag))),
            Value::Float(number) => Ok(Number::Float(*number)),
            Value::BigInt(_) => Ok(py_str(value).parse::<i128>().map(Number::Int).unwrap_or_else(|_| {
                Number::Float(py_str(value).parse::<f64>().unwrap_or(f64::INFINITY))
            })),
            other => Err(Error::type_error(format!(
                "unsupported operand type(s) for +: '{}' and '{}'",
                total.type_name(),
                other.type_name()
            ))),
        }
    }

    fn type_name(self) -> &'static str {
        match self {
            Number::Int(_) => "int",
            Number::Float(_) => "float",
        }
    }

    fn float(self) -> f64 {
        match self {
            Number::Int(number) => number as f64,
            Number::Float(number) => number,
        }
    }

    fn add(self, other: Number) -> Number {
        match (self, other) {
            (Number::Int(a), Number::Int(b)) => Number::Int(a.saturating_add(b)),
            (a, b) => Number::Float(a.float() + b.float()),
        }
    }

    fn sub(self, other: Number) -> Number {
        match (self, other) {
            (Number::Int(a), Number::Int(b)) => Number::Int(a.saturating_sub(b)),
            (a, b) => Number::Float(a.float() - b.float()),
        }
    }

    fn le(self, other: Number) -> bool {
        match (self, other) {
            (Number::Int(a), Number::Int(b)) => a <= b,
            (a, b) => a.float().partial_cmp(&b.float()).is_some_and(|order| order != Ordering::Greater),
        }
    }

    /// `sum(values)`: Python starts from the int 0.
    fn sum<'v>(values: impl IntoIterator<Item = &'v Value>) -> Result<Number> {
        let mut total = Number::Int(0);
        for value in values {
            total = total.add(Number::of(value, total)?);
        }
        Ok(total)
    }
}

/// Reward units of each source identity, in the plan's order.
fn units(plan: &Value, places: &Places<'_>) -> Result<Vec<(String, Value)>> {
    let registrations = by_id(plan.at("registrations")?)?;
    let mut order: Vec<String> = Vec::new();
    let mut units: HashMap<String, Value> = HashMap::new();
    let mut set = |identity: String, value: Value| {
        if units.insert(identity.clone(), value).is_none() {
            order.push(identity);
        }
    };
    for choice in crate::py::iter_value(plan.at("sources")?)? {
        for identity in identities(choice.at("source_ids")?)? {
            let identity = py_str(&identity);
            let source = places.source(&identity)?;
            let value = if source.at("kind")?.as_str() == Some("store") {
                source.at("record")?.at("quantity")?.clone()
            } else {
                Value::Int(1)
            };
            set(identity, value);
        }
        for identity in identities(choice.at("registration_ids")?)? {
            let identity = py_str(&identity);
            let row = registrations.get(&identity).ok_or_else(|| Error::key(&identity))?;
            set(identity, row.at("copies")?.clone());
        }
    }
    Ok(order.into_iter().map(|identity| {
        let value = units[&identity].clone();
        (identity, value)
    }).collect())
}

/// Sources the remaining targets cannot take.
///
/// With overflow (Item Randomiser's tables) each tier with rewards needs a
/// place that can take any reward and, when originals may be kept, one that
/// no reward's home claims; without overflow the targets must hold every
/// reward.
fn without_place(plan: &Value, places: &Places<'_>) -> Result<Vec<String>> {
    let randomization = get(plan, "randomization")?.filter(|value| !value.is_null());
    let units = units(plan, places)?;
    let overflow = match randomization {
        None => false,
        Some(randomization) => get(randomization, "overflow")?.is_some_and(Value::truthy),
    };
    if !overflow {
        let mut capacity = Number::Int(0);
        for row in crate::py::iter_value(plan.at("targets")?)? {
            capacity = capacity.add(Number::of(row.at("capacity")?, capacity)?);
        }
        let mut total = Number::sum(units.iter().map(|(_, value)| value))?;
        let mut sorted: Vec<&(String, Value)> = units.iter().collect();
        sorted.sort_by(|a, b| b.0.cmp(&a.0));
        let mut dropped = Vec::new();
        for (identity, value) in sorted {
            if total.le(capacity) {
                break;
            }
            dropped.push(identity.clone());
            total = total.sub(Number::of(value, total)?);
        }
        return Ok(dropped);
    }
    let randomization = randomization.expect("overflow needs a randomization");
    let mut registered: HashMap<String, Value> = HashMap::new();
    if let Some(rows) = get(plan, "target_registrations")? {
        for row in crate::py::iter_value(rows)? {
            let identity = py_str(row.at("id")?);
            let kind = crate::original_registrations::target_record(&row)?.at("kind")?.clone();
            registered.insert(identity, kind);
        }
    }
    let mut kinds: HashMap<String, Value> = HashMap::new();
    for row in crate::py::iter_value(plan.at("targets")?)? {
        let identity = py_str(row.at("id")?);
        let kind = match registered.get(&identity).filter(|kind| kind.truthy()) {
            Some(kind) => kind.clone(),
            None => places.target(&identity).and_then(|target| target.get("kind")).cloned().unwrap_or(Value::Null),
        };
        kinds.insert(identity, kind);
    }
    // Tier -> (target, ordinal) places of the overflow target kinds.
    let mut slots: HashMap<String, HashSet<(String, i64)>> = HashMap::new();
    let targets = match randomization.at("targets")? {
        Value::Dict(targets) => targets,
        other => return Err(Error::attribute(other.type_name(), "items")),
    };
    for (identity, tiers) in targets.iter() {
        let kind = kinds.get(identity).and_then(Value::as_str);
        if kind.is_some_and(|kind| OVERFLOW_TARGET_KINDS.contains(&kind)) {
            for (ordinal, tier) in crate::py::iter_value(tiers)?.iter().enumerate() {
                slots.entry(hash_key(tier)?).or_default().insert((identity.to_string(), ordinal as i64 + 1));
            }
        }
    }
    let keeping = match randomization.at("unrandomized_percent")? {
        Value::Int(number) => *number > 0,
        Value::Bool(flag) => *flag,
        Value::Float(number) => *number > 0.0,
        value @ Value::BigInt(_) => !py_str(value).starts_with('-'),
        other => {
            return Err(Error::type_error(format!(
                "'>' not supported between instances of '{}' and 'int'",
                other.type_name()
            )))
        }
    };
    let mut rules: Vec<(String, Value)> = match randomization.at("sources")? {
        Value::Dict(rules) => rules.iter().map(|(identity, rule)| (identity.to_string(), rule.clone())).collect(),
        other => return Err(Error::type_error(format!("'{}' object is not iterable", other.type_name()))),
    };
    let mut dropped: BTreeSet<String> = BTreeSet::new();
    let empty = HashSet::new();
    loop {
        let mut claimed: HashSet<(String, i64)> = HashSet::new();
        for (_, rule) in &rules {
            for entry in crate::py::iter_value(rule.at("exclude_targets")?)? {
                let identity = entry.at("id")?;
                let ordinal = entry.at("ordinal")?;
                // Tuple equality: an ordinal of 1, 1.0 or True is the same slot.
                let ordinal = match ordinal {
                    Value::Int(number) => Some(*number),
                    Value::Bool(flag) => Some(i64::from(*flag)),
                    Value::Float(number) if number.fract() == 0.0 => Some(*number as i64),
                    _ => None,
                };
                if let (Some(identity), Some(ordinal)) = (identity.as_str(), ordinal) {
                    claimed.insert((identity.to_string(), ordinal));
                }
            }
        }
        let mut failing: Vec<String> = Vec::new();
        for (identity, rule) in &rules {
            let slot = slots.get(&hash_key(rule.at("tier")?)?).unwrap_or(&empty);
            let free = if keeping { slot.iter().any(|slot| !claimed.contains(slot)) } else { !slot.is_empty() };
            if !free {
                failing.push(identity.clone());
            }
        }
        if failing.is_empty() {
            return Ok(dropped.into_iter().collect());
        }
        let failing: HashSet<String> = failing.into_iter().collect();
        rules.retain(|(identity, _)| !failing.contains(identity));
        dropped.extend(failing);
    }
}

/// The configuration for a start that reaches these parts, or None if
/// nothing is left.
pub fn restrict(
    profile: &Value,
    plan: &Value,
    catalog: &Value,
    world: &Value,
    platform: &str,
    reach: &[Part],
    scans: Option<&Scans>,
) -> Result<Option<(Value, Value)>> {
    let validated;
    let (items, index) = match scans {
        Some(scans) => scans.indexes(catalog, world)?,
        None => {
            let items = validate_catalog(catalog)?;
            validated = (items, validate_world(world, catalog)?);
            (&validated.0, &validated.1)
        }
    };
    let places = Places::new(catalog, world, index, plan, platform, scans.map(|scans| &scans.tables))?;
    let registrations = by_id(plan.at("registrations")?)?;
    let target_registrations = match get(plan, "target_registrations")? {
        Some(rows) => by_id(rows)?,
        None => HashMap::new(),
    };
    let mut sources: HashSet<String> = HashSet::new();
    let mut targets: HashSet<String> = HashSet::new();
    for choice in crate::py::iter_value(plan.at("sources")?)? {
        if get(&choice, "policy")?.and_then(Value::as_str) != Some("relocate") {
            return Err(Error::profile("Only relocated rewards can be applied"));
        }
        for identity in identities(choice.at("source_ids")?)? {
            let identity = py_str(&identity);
            let source = places.source(&identity)?;
            if !places.reached(source.at("resource")?, places.campaign(&identity), reach)? {
                sources.insert(identity);
            }
        }
        for identity in identities(choice.at("registration_ids")?)? {
            let identity = py_str(&identity);
            let row = registrations.get(&identity).ok_or_else(|| Error::key(&identity))?;
            let campaign = if row.at("adapter_id")?.as_str() == Some(crate::scripted_sources::ADAPTER_ID) {
                Table::of(Some(&Value::from(crate::scripted_sources::CAMPAIGN)))
            } else {
                let authored = match get(row, "authored")? {
                    Some(authored) => get(authored, "campaign")?,
                    None => None,
                };
                match authored {
                    Some(campaign) if campaign.truthy() => Table::of(Some(campaign)),
                    _ => table_campaign(row.at("selector")?),
                }
            };
            if !places.reached(row.at("resource")?, campaign, reach)? {
                sources.insert(identity);
            }
        }
    }
    for choice in crate::py::iter_value(plan.at("targets")?)? {
        let identity = py_str(choice.at("id")?);
        let (resource, campaign) = if let Some(row) = target_registrations.get(&identity) {
            (row.at("resource")?.clone(), Table::of(Some(row.at("authored")?.at("campaign")?)))
        } else if let Some(target) = places.target(&identity) {
            (target.at("resource")?.clone(), Table::Unknown)
        } else {
            targets.insert(identity);
            continue;
        };
        if !places.reached(&resource, campaign, reach)? {
            targets.insert(identity);
        }
    }
    let (mut profile, mut plan) = without(profile, plan, catalog, &sources, &targets)?;
    for _ in 0..FIT_ROUNDS {
        if !plan.at("sources")?.truthy() || !plan.at("targets")?.truthy() {
            return Ok(None);
        }
        let placeless = without_place(&plan, &places)?;
        if !placeless.is_empty() {
            (profile, plan) = without(&profile, &plan, catalog, &placeless.into_iter().collect(), &HashSet::new())?;
            continue;
        }
        let mut problems = Dict::new();
        let campaign = compile_campaign(&profile, catalog, world, &plan, scans)?;
        crate::native_export::compile(&campaign, world, index, items, catalog, Some(&mut problems), false, None)?;
        if problems.is_empty() {
            return Ok(Some((profile, plan)));
        }
        let source_ids: HashSet<String> = units(&plan, &places)?.into_iter().map(|(identity, _)| identity).collect();
        let mut target_ids: HashSet<String> = HashSet::new();
        for row in crate::py::iter_value(plan.at("targets")?)? {
            target_ids.insert(py_str(row.at("id")?));
        }
        let unknown: BTreeSet<&str> =
            problems.keys().filter(|identity| !source_ids.contains(*identity) && !target_ids.contains(*identity)).collect();
        if let Some(first) = unknown.first() {
            return Err(Error::profile(py_str(problems.get(first).expect("a problem"))));
        }
        let named: HashSet<String> = problems.keys().map(str::to_string).collect();
        let drop_sources: HashSet<String> = named.intersection(&source_ids).cloned().collect();
        let drop_targets: HashSet<String> = named.intersection(&target_ids).cloned().collect();
        (profile, plan) = without(&profile, &plan, catalog, &drop_sources, &drop_targets)?;
    }
    Err(Error::profile("The runtime check kept finding rewards or places this start cannot use"))
}

/// Write the revision of each campaign start of this game to the store.
///
/// Returns the game, its starts and their revisions, the start the engine
/// makes without a campaign row ("main", null on EET) and, for each start
/// that gets no revision, the reason. `scans` holds the catalogues already
/// validated.
pub fn write_starts(
    profile: &Value,
    plan: &Value,
    catalog: &Value,
    world: &Value,
    store: &Path,
    scans: Option<&Scans>,
) -> Result<Value> {
    let platform = platform::game_platform(world)?;
    let full = platform::full_start(platform)?;
    let mut revisions: Vec<(String, Value)> = Vec::new();
    let mut skipped = Dict::new();
    for (start, reach) in platform::starts(platform)? {
        if *start == full {
            continue;
        }
        let restricted = match restrict(profile, plan, catalog, world, platform, reach, scans) {
            Ok(restricted) => restricted,
            Err(error) if error.kind == ErrorKind::Profile => {
                skipped.insert(*start, error.message);
                continue;
            }
            Err(error) => return Err(error),
        };
        let Some((start_profile, start_plan)) = restricted else {
            skipped.insert(*start, "nothing in this configuration is reached from this start");
            continue;
        };
        let written = write_campaign(&start_profile, catalog, world, &start_plan, store, scans)?;
        revisions.push((start.to_string(), written.at("revision")?.clone()));
    }
    // Written last, so the store's own pointer names the whole configuration.
    let written = write_campaign(profile, catalog, world, plan, store, scans)?;
    revisions.push((full.to_string(), written.at("revision")?.clone()));
    revisions.sort_by(|a, b| a.0.cmp(&b.0));
    let mut starts = Dict::new();
    for (start, revision) in revisions {
        starts.insert(start, revision);
    }
    Ok(Value::Dict(crate::dict! {
        "platform" => platform, "full" => full,
        "main" => platform::main_start(platform)?.map_or(Value::Null, Value::from),
        "starts" => Value::Dict(starts), "skipped" => Value::Dict(skipped),
    }))
}

/// Which new games the applied settings randomise, for the player.
pub fn describe_starts(written: &Value) -> Result<String> {
    let platform = written.at("platform")?;
    let starts = platform::starts(&py_str(platform))?;
    let randomised = written.at("starts")?;
    let mut names = Vec::new();
    for (start, _) in starts {
        if randomised.get(start).is_some() {
            names.push(platform::start_name(start)?);
        }
    }
    let mut text = format!("New games randomised: {}.", names.join(", "));
    let skipped = written.at("skipped")?;
    if skipped.truthy() {
        let skipped = match skipped {
            Value::Dict(skipped) => skipped,
            other => return Err(Error::attribute(other.type_name(), "items")),
        };
        let mut reasons = Vec::new();
        for (start, reason) in skipped.iter() {
            reasons.push(format!("{} ({})", platform::start_name(start)?, py_str(reason)));
        }
        text.push_str(&format!(" Not randomised: {}.", reasons.join("; ")));
    }
    Ok(text)
}
