//! Immutable inputs for the isolated logical campaign prototype (the
//! reference's `campaigns.py`).
//!
//! These artifacts do not activate the installed Randomiser or prove engine
//! source suppression. Physical records and the reviewed BLPOOL grant are
//! supported. The Lua companion is generated data, never user-provided
//! executable configuration.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::original_registrations as original;
use crate::paths;
use crate::profiles::{
    canonical_bytes, default_shop_pricing, digest, ensure_output_outside_game, is_hex256, is_profile_id,
    make_dirs, random_name, read_json, write_json,
};
use crate::sources::{
    bind_profile, PyNumber, Scans, DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE, UNUSABLE_SOURCE,
};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

pub const CAMPAIGN_SCHEMA: &str = "flir-campaign-prototype-v1";
pub const POINTER_SCHEMA: &str = "flir-prototype-next-v1";
pub const STATE: &str = "prototype-not-playable";
pub const MAX_REWARD_UNITS: i64 = 4096;
pub const MAX_TARGET_CAPACITY: i64 = 4096;
/// Target kinds that can take a reward beyond the authored pairing (overflow).
pub const OVERFLOW_TARGET_KINDS: [&str; 6] = ["actor", "cre-template", "container", "store", "group", "creature-target"];
pub const RUNTIME_REQUIREMENTS: [&str; 6] = crate::sources::RUNTIME_REQUIREMENTS;
const FIELDS: [&str; 9] = [
    "schema",
    "state",
    "revision",
    "bound_revision",
    "profile_id",
    "world_fingerprint",
    "sources",
    "targets",
    "runtime_requirements",
];
const MAX_LUA_INTEGER: i64 = (1 << 53) - 1;

fn fields<'a>(value: Option<&'a Value>, expected: &[&str], label: &str) -> Result<&'a Dict> {
    match value.and_then(Value::as_dict) {
        Some(fields) if fields.has_exact_keys(expected) => Ok(fields),
        _ => Err(Error::profile(format!("Unsupported campaign {label} fields"))),
    }
}

fn integer(value: Option<&Value>, label: &str, low: i64, high: i64) -> Result<i64> {
    crate::profiles::int_in(value, low, high).ok_or_else(|| Error::profile(format!("Invalid campaign {label}")))
}

fn text<'a>(value: Option<&'a Value>, label: &str) -> Result<&'a str> {
    match value {
        Some(Value::Str(text)) if !text.is_empty() && text.chars().all(|c| c as u32 >= 32) => Ok(text),
        _ => Err(Error::profile(format!("Invalid campaign {label}"))),
    }
}

fn hash<'a>(value: Option<&'a Value>, label: &str) -> Result<&'a str> {
    match value {
        Some(Value::Str(text)) if is_hex256(text) => Ok(text),
        _ => Err(Error::profile(format!("Invalid campaign {label}"))),
    }
}

fn resref_text(value: &str) -> Result<()> {
    let length = value.chars().count();
    if !(1..=8).contains(&length)
        || value != crate::py::lower(value)
        || value.chars().any(|c| c as u32 > 126 || "/\\:*?\"<>| ".contains(c))
    {
        return Err(Error::profile("Invalid campaign resource name"));
    }
    Ok(())
}

fn resref(value: Option<&Value>) -> Result<&str> {
    let value = text(value, "resource name")?;
    resref_text(value)?;
    Ok(value)
}

fn resource<'a>(value: Option<&'a Value>, kind: &str) -> Result<&'a str> {
    let value = text(value, "resource identity")?;
    let Some(name) = value.strip_prefix(&format!("{kind}:")) else {
        return Err(Error::profile("Campaign resource kind disagrees with its record"));
    };
    text(Some(&Value::from(name)), "resource name")?;
    resref_text(name)?;
    Ok(value)
}

fn concerns(record: &Dict) -> Result<Vec<&str>> {
    let invalid = || Error::profile("Invalid campaign record concerns");
    let list = record.get("concerns").and_then(Value::as_list).ok_or_else(invalid)?;
    let names: Option<Vec<&str>> = list.iter().map(Value::as_str).collect();
    let names = names.ok_or_else(invalid)?;
    if names.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(invalid());
    }
    Ok(names)
}

fn is_original_adapter(row: &Value, adapter: &str) -> bool {
    row.get("record").and_then(|record| record.get("adapter_id")).and_then(Value::as_str) == Some(adapter)
        && row.get("record").is_some_and(Value::is_dict)
}

fn validate_source(row: &Value, spell_shield: Option<&Value>) -> Result<i64> {
    if row.is_dict() && is_original_adapter(row, original::SOURCE_ADAPTER) {
        fields(Some(row), &["id", "quantity", "record", "resource_sha256"], "source")?;
        return original::validate_campaign_source(row, spell_shield);
    }
    let mut expected = vec!["id", "quantity", "record", "resource_sha256"];
    if row.get("replacement").is_some() {
        expected.push("replacement");
    }
    let row_fields = fields(Some(row), &expected, "source")?;
    hash(row_fields.get("resource_sha256"), "source resource fingerprint")?;
    let quantity = integer(row_fields.get("quantity"), "source quantity", 1, MAX_REWARD_UNITS)?;
    let record_kind = row.get("record").and_then(|record| record.get("kind")).and_then(Value::as_str);
    let mut expected = vec!["id", "item", "resource", "owner", "kind", "record", "concerns"];
    if row.get("record").is_some_and(Value::is_dict) && matches!(record_kind, Some("scripted" | "virtual")) {
        expected.extend(["adapter_id", "selector"]);
    }
    let source = fields(row_fields.get("record"), &expected, "source record")?;
    let field = |key: &str| source.get(key).expect("checked");
    let row_field = |key: &str| row_fields.get(key).expect("checked");
    match field("kind").as_str() {
        Some("scripted") => {
            let registration = Value::Dict(crate::dict! {
                "id" => crate::scripted_sources::SOURCE_ID, "item" => crate::scripted_sources::ITEM,
                "resource" => crate::scripted_sources::RESOURCE, "resource_sha256" => row_field("resource_sha256").clone(),
                "adapter_id" => "blpool-v1", "selector" => "pool-reward", "copies" => 1, "optional" => false,
            });
            if row_field("id").as_str() != Some(crate::scripted_sources::SOURCE_ID)
                || !row_field("quantity").py_eq(&Value::Int(1))
                || !Value::Dict(source.clone()).py_eq(&crate::scripted_sources::campaign_record(&registration)?)
                || row_fields.contains_key("replacement")
            {
                return Err(Error::profile("Unsupported scripted campaign source"));
            }
            return Ok(1);
        }
        Some("virtual") => {
            let registration = Value::Dict(crate::dict! {
                "id" => field("id").clone(), "item" => field("item").clone(), "resource" => field("resource").clone(),
                "resource_sha256" => row_field("resource_sha256").clone(), "adapter_id" => field("adapter_id").clone(),
                "selector" => field("selector").clone(), "copies" => quantity, "optional" => false,
                "payload" => field("record").clone(),
            });
            crate::virtual_sources::validate_registration(&registration)?;
            if !row_field("id").py_eq(field("id"))
                || !Value::Dict(source.clone()).py_eq(&crate::virtual_sources::campaign_record(&registration)?)
                || row_fields.contains_key("replacement")
            {
                return Err(Error::profile("Unsupported virtual campaign source"));
            }
            return Ok(quantity);
        }
        _ => {}
    }
    let kind = match field("kind").as_str() {
        Some(kind @ ("cre-template" | "embedded-cre" | "container" | "store")) => kind,
        _ => return Err(Error::profile("Campaign prototype accepts only physical sources")),
    };
    if concerns(source)?.iter().any(|concern| UNUSABLE_SOURCE.contains(concern)) {
        return Err(Error::profile("Campaign source is not finite and unambiguous"));
    }
    let item = resref(Some(field("item")))?;
    let owner = text(Some(field("owner")), "source owner")?;
    let record = match field("record") {
        Value::Dict(record) if record.get("resref").is_some_and(|resref| resref.py_eq(field("item"))) => record,
        _ => return Err(Error::profile("Invalid campaign inventory record")),
    };
    if record.contains_key("inventory_diagnostics") || record.contains_key("raw_resref_hex") {
        return Err(Error::profile("Campaign source has quarantined inventory diagnostics"));
    }
    for key in ["record_index", "byte_offset", "flags"] {
        integer(record.get(key), &format!("source {key}"), 0, 0xFFFF_FFFF)?;
    }
    integer(record.get("expiration"), "source expiration", 0, 0xFFFF)?;
    let identity = text(Some(row_field("id")), "source identity")?;
    let record_index = record.get("record_index").and_then(Value::as_int).expect("checked");
    if !field("id").as_str().is_some_and(|id| id == identity) || identity != format!("{owner}/item/{record_index}") {
        return Err(Error::profile("Campaign source identity disagrees with its record"));
    }
    match record.get("charges") {
        Some(Value::List(charges)) if charges.len() == 3 => {
            for charge in charges {
                integer(Some(charge), "source charge", 0, 0xFFFF)?;
            }
        }
        _ => return Err(Error::profile("Campaign source needs three charge values")),
    }
    if let Some(replacement) = row_fields.get("replacement") {
        if !replacement.is_dict() || !["cre-template", "embedded-cre", "container"].contains(&kind) {
            return Err(Error::profile("Campaign replacement requires a creature or container source"));
        }
        let name = resref(replacement.get("resref"))?;
        let expected: &[&str] = if name == "blank" { &["resref"] } else { &["resref", "resource_sha256"] };
        fields(Some(replacement), expected, "replacement")?;
        if name == item {
            return Err(Error::profile("Campaign replacement must differ from the relocated item"));
        }
        if name != "blank" {
            let sha = hash(replacement.get("resource_sha256"), "replacement fingerprint")?;
            if name == original::SHIPPED_REPLACEMENT && sha != original::SHIPPED_REPLACEMENT_HASH {
                return Err(Error::profile("Original flbhcryp replacement fingerprint changed"));
            }
        }
    }
    let resource_kind = match kind {
        "cre-template" => "cre",
        "store" => "sto",
        _ => "are",
    };
    let resource = resource(Some(field("resource")), resource_kind)?;
    if (kind == "cre-template" || kind == "store") && owner != resource {
        return Err(Error::profile("Campaign source has a mismatched template owner"));
    }
    if kind == "cre-template" || kind == "embedded-cre" {
        match record.get("slots") {
            Some(Value::List(slots)) if slots.len() == 1 => {
                integer(Some(&slots[0]), "source slot", 0, 37)?;
            }
            _ => return Err(Error::profile("Campaign creature source needs one unambiguous slot")),
        }
    }
    if kind == "embedded-cre" {
        let prefix = format!("{resource}/actor/");
        let valid = owner.strip_prefix(&prefix).is_some_and(|number| {
            !number.is_empty()
                && number.bytes().all(|b| b.is_ascii_digit())
                && (number == "0" || !number.starts_with('0'))
        });
        if !valid {
            return Err(Error::profile("Campaign embedded source has an invalid owner"));
        }
    }
    if kind == "container" {
        let index = integer(record.get("container_index"), "source container index", 0, 0xFFFF_FFFF)?;
        if !record.get("container_indexes").is_some_and(|indexes| indexes.py_eq(&Value::from(vec![index])))
            || owner != format!("{resource}/container/{index}")
        {
            return Err(Error::profile("Campaign container source has ambiguous ownership"));
        }
    }
    if kind == "store" {
        let stock = record.get("quantity");
        if !matches!(stock, Some(Value::Int(count)) if *count == quantity)
            || !matches!(record.get("infinite_supply"), Some(Value::Bool(false)))
            || !matches!(record.get("infinite_supply_flag"), Some(Value::Int(0)))
        {
            return Err(Error::profile("Campaign store quantity must match finite stock"));
        }
    } else if quantity != 1 {
        return Err(Error::profile("A physical non-store source contains one reward record"));
    }
    Ok(quantity)
}

fn validate_target(row: &Value) -> Result<i64> {
    if row.is_dict() && is_original_adapter(row, original::TARGET_ADAPTER) {
        fields(Some(row), &["id", "capacity", "record", "resource_sha256"], "target")?;
        return original::validate_campaign_target(row);
    }
    let row_fields = fields(Some(row), &["id", "capacity", "record", "resource_sha256"], "target")?;
    let capacity = integer(row_fields.get("capacity"), "target capacity", 1, MAX_TARGET_CAPACITY)?;
    hash(row_fields.get("resource_sha256"), "target resource fingerprint")?;
    let target = fields(row_fields.get("record"), &["id", "resource", "kind", "selector", "concerns"], "target record")?;
    let kind = match target.get("kind").and_then(Value::as_str) {
        Some(kind @ ("cre-template" | "actor" | "container" | "store")) => kind,
        _ => return Err(Error::profile("Invalid campaign target kind")),
    };
    if concerns(target)?.iter().any(|concern| [DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE].contains(concern)) {
        return Err(Error::profile("Campaign target has invalid inventory references"));
    }
    let resource_kind = match kind {
        "cre-template" => "cre",
        "store" => "sto",
        _ => "are",
    };
    let resource = resource(target.get("resource"), resource_kind)?;
    let identity = text(row_fields.get("id"), "target identity")?;
    let Some(selector) = target.get("selector").and_then(Value::as_dict) else {
        return Err(Error::profile("Invalid campaign target selector"));
    };
    if ["inventory_diagnostics", "template_inventory_diagnostics", "storage_diagnostics"]
        .iter()
        .any(|key| selector.contains_key(key))
    {
        return Err(Error::profile("Campaign target has quarantined inventory diagnostics"));
    }
    let expected = if kind == "actor" || kind == "container" {
        let index = integer(selector.get("record_index"), "target record index", 0, 0xFFFF_FFFF)?;
        integer(selector.get("byte_offset"), "target byte offset", 0, 0xFFFF_FFFF)?;
        if kind == "actor" {
            let offset = integer(selector.get("embedded_cre_offset"), "target embedded creature offset", 0, 0xFFFF_FFFF)?;
            let size = integer(selector.get("embedded_cre_size"), "target embedded creature size", 0, 0xFFFF_FFFF)?;
            if (offset != 0) != (size != 0) {
                return Err(Error::profile("Campaign actor target has incomplete creature storage"));
            }
        }
        format!("{resource}/{kind}/{index}")
    } else {
        resource.to_string()
    };
    if target.get("id").and_then(Value::as_str) != Some(identity) || identity != expected {
        return Err(Error::profile("Campaign target identity disagrees with its record"));
    }
    Ok(capacity)
}

/// A counter that keeps its keys in first-seen order (`collections.Counter`).
#[derive(Default)]
struct Counter {
    order: Vec<String>,
    counts: HashMap<String, i64>,
}

impl Counter {
    fn add(&mut self, key: &str, amount: i64) {
        if !self.counts.contains_key(key) {
            self.order.push(key.to_string());
        }
        *self.counts.entry(key.to_string()).or_insert(0) += amount;
    }
    fn get(&self, key: &str) -> i64 {
        self.counts.get(key).copied().unwrap_or(0)
    }
    fn items(&self) -> impl Iterator<Item = (&str, i64)> {
        self.order.iter().map(|key| (key.as_str(), self.counts[key]))
    }
}

fn tier(value: &Value) -> Result<&str> {
    match value.as_str() {
        Some(name)
            if (1..=64).contains(&name.len())
                && name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b":_-".contains(&b)) =>
        {
            Ok(name)
        }
        _ => Err(Error::profile("Invalid randomization tier")),
    }
}

fn same_keys(dict: &Dict, rows: &[(&str, &Value)]) -> bool {
    dict.len() == rows.len() && rows.iter().all(|(identity, _)| dict.contains_key(identity))
}

fn validate_randomization(value: &Value, sources: &[Value], targets: &[Value]) -> Result<()> {
    let mut expected = vec!["sources", "targets", "loss_percent", "unrandomized_percent"];
    if let Some(present) = value.as_dict() {
        expected.extend(["overflow", "row_filters"].into_iter().filter(|key| present.contains_key(key)));
    }
    let metadata = fields(Some(value), &expected, "randomization")?;
    let overflow = match metadata.get("overflow") {
        None => false,
        Some(Value::Bool(flag)) => *flag,
        Some(_) => return Err(Error::profile("Invalid randomization overflow flag")),
    };
    fn identity_of(row: &Value) -> &str {
        row.get("id").and_then(Value::as_str).unwrap_or("")
    }
    let source_rows: Vec<(&str, &Value)> = sources.iter().map(|row| (identity_of(row), row)).collect();
    let target_rows: Vec<(&str, &Value)> = targets.iter().map(|row| (identity_of(row), row)).collect();
    let target_lookup: HashMap<&str, &Value> = target_rows.iter().copied().collect();
    let source_metadata = metadata.get("sources").and_then(Value::as_dict);
    let target_metadata = metadata.get("targets").and_then(Value::as_dict);
    let (Some(source_metadata), Some(target_metadata)) = (source_metadata, target_metadata) else {
        return Err(Error::profile("Randomization must name exactly the selected sources and targets"));
    };
    if !same_keys(source_metadata, &source_rows) || !same_keys(target_metadata, &target_rows) {
        return Err(Error::profile("Randomization must name exactly the selected sources and targets"));
    }
    for key in ["loss_percent", "unrandomized_percent"] {
        integer(metadata.get(key), &format!("randomization {key}"), 0, 100)?;
    }
    let unrandomized = metadata.get("unrandomized_percent").and_then(Value::as_int).expect("checked");
    if let Some(filters) = metadata.get("row_filters") {
        let filters = fields(Some(filters), &["sources", "targets"], "row filters")?;
        for (kind, selected) in [("sources", &source_rows), ("targets", &target_rows)] {
            let rows = match filters.get(kind).and_then(Value::as_dict) {
                Some(rows) if same_keys(rows, selected) => rows,
                _ => return Err(Error::profile("Row filters must name exactly the selected sources and targets")),
            };
            for details in rows.values() {
                let details = fields(Some(details), &["chance", "group"], "row filter")?;
                integer(details.get("chance"), "row filter chance", 0, 100)?;
                let group = details.get("group").expect("checked");
                if group.as_str() != Some("") {
                    tier(group)?;
                }
            }
        }
    }

    let mut target_capacity = Counter::default();
    let mut eligible_slots: HashMap<String, HashSet<(String, i64)>> = HashMap::new();
    for (identity, row) in &target_rows {
        let slots = match target_metadata.get(identity) {
            Some(Value::List(slots)) if row.get("capacity").is_some_and(|capacity| capacity.py_eq(&Value::from(slots.len()))) => slots,
            _ => return Err(Error::profile("Randomization target tiers must match target capacity")),
        };
        let overflow_kind = row
            .get("record")
            .and_then(|record| record.get("kind"))
            .and_then(Value::as_str)
            .is_some_and(|kind| OVERFLOW_TARGET_KINDS.contains(&kind));
        for (ordinal, raw_name) in slots.iter().enumerate() {
            let name = tier(raw_name)?;
            target_capacity.add(name, 1);
            if overflow_kind {
                eligible_slots.entry(name.to_string()).or_default().insert((identity.to_string(), ordinal as i64 + 1));
            }
        }
    }

    let mut source_units = Counter::default();
    let mut keep_reserve = Counter::default();
    let mut all_exclusions: HashSet<(String, i64)> = HashSet::new();
    for (identity, row) in &source_rows {
        let raw = source_metadata.get(identity).expect("checked");
        let mut expected = vec!["tier", "loss_exempt", "exclude_targets"];
        if raw.get("reward_variants").is_some() {
            expected.push("reward_variants");
        }
        let details = fields(Some(raw), &expected, "randomization source")?;
        if let Some(variants) = details.get("reward_variants") {
            let valid = match variants {
                Value::List(variants) if (1..=MAX_REWARD_UNITS as usize).contains(&variants.len()) => {
                    let names: Option<Vec<&str>> = variants.iter().map(Value::as_str).collect();
                    names.is_some_and(|names| {
                        names.iter().all(|name| {
                            (1..=8).contains(&name.len())
                                && name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"#_-".contains(&b))
                        }) && names.iter().collect::<HashSet<_>>().len() == names.len()
                    })
                }
                _ => false,
            };
            if !valid {
                return Err(Error::profile("Invalid randomization reward variants"));
            }
        }
        let name = tier(details.get("tier").expect("checked"))?;
        if !matches!(details.get("loss_exempt"), Some(Value::Bool(_))) {
            return Err(Error::profile("Invalid randomization loss exemption"));
        }
        let Some(exclusions) = details.get("exclude_targets").and_then(Value::as_list) else {
            return Err(Error::profile("Invalid randomization target exclusions"));
        };
        let mut seen: HashSet<(String, i64)> = HashSet::new();
        let mut excluded_tiers = Counter::default();
        for entry in exclusions {
            let entry = fields(Some(entry), &["id", "ordinal"], "randomization target exclusion")?;
            let distinct = || Error::profile("Randomization exclusions need distinct selected targets");
            let target_id = entry.get("id").and_then(Value::as_str).ok_or_else(distinct)?;
            let target = target_lookup.get(target_id).ok_or_else(distinct)?;
            let capacity = target.get("capacity").and_then(Value::as_int).unwrap_or(0);
            let ordinal = integer(entry.get("ordinal"), "randomization target ordinal", 1, capacity)?;
            if !seen.insert((target_id.to_string(), ordinal)) {
                return Err(distinct());
            }
            all_exclusions.insert((target_id.to_string(), ordinal));
            let slot = target_metadata
                .get(target_id)
                .and_then(Value::as_list)
                .and_then(|slots| slots.get(ordinal as usize - 1))
                .and_then(Value::as_str)
                .unwrap_or("");
            excluded_tiers.add(slot, 1);
        }
        // Reserve for each possible keep decision independently. Shared
        // exclusions can overestimate this bound.
        let quantity = row.get("quantity").and_then(Value::as_int).unwrap_or(0);
        for (excluded_tier, count) in excluded_tiers.items() {
            let own = if excluded_tier == name { quantity } else { 0 };
            keep_reserve.add(excluded_tier, (count - own).max(0));
        }
        source_units.add(name, quantity);
    }
    for (name, count) in source_units.items() {
        if overflow {
            let Some(slots) = eligible_slots.get(name).filter(|slots| !slots.is_empty()) else {
                return Err(Error::profile(format!("Randomization overflow needs an eligible target in tier {name}")));
            };
            if unrandomized > 0 && slots.iter().all(|slot| all_exclusions.contains(slot)) {
                return Err(Error::profile(format!("Randomization overflow needs an unpaired target in tier {name}")));
            }
            continue;
        }
        if target_capacity.get(name) < count {
            return Err(Error::profile(format!(
                "Randomization tier capacity is short for {name}: {} target slots for {count} source units",
                target_capacity.get(name)
            )));
        }
        if 0 < unrandomized && unrandomized < 100 && target_capacity.get(name) < count + keep_reserve.get(name) {
            return Err(Error::profile(format!(
                "Randomization tier {name} needs more target capacity to guarantee room after keeping originals"
            )));
        }
    }
    Ok(())
}

const ORIGINAL_KEYS: [&str; 3] = ["original_template_id", "original_choices", "original_options"];

fn validate_campaign(body: &Value) -> Result<()> {
    let mut expected: Vec<&str> = FIELDS.to_vec();
    if let Some(present) = body.as_dict() {
        for key in ["shop_pricing", "randomization"] {
            if present.contains_key(key) {
                expected.push(key);
            }
        }
        for key in ["original_template_id", "original_choices", "original_options", "spell_shield", "cursed_disguises"] {
            if present.contains_key(key) {
                expected.push(key);
            }
        }
    }
    let campaign = fields(Some(body), &expected, "artifact")?;
    let field = |key: &str| campaign.get(key).expect("checked");
    if field("schema").as_str() != Some(CAMPAIGN_SCHEMA) || field("state").as_str() != Some(STATE) {
        return Err(Error::profile("Unsupported campaign prototype schema or state"));
    }
    for key in ["revision", "bound_revision", "world_fingerprint"] {
        hash(Some(field(key)), key)?;
    }
    if !field("profile_id").as_str().is_some_and(is_profile_id) {
        return Err(Error::profile("Invalid campaign profile identity"));
    }
    if !field("runtime_requirements").py_eq(&Value::from(RUNTIME_REQUIREMENTS.to_vec())) {
        return Err(Error::profile("Campaign runtime requirements are missing or unsupported"));
    }
    let present_original = ORIGINAL_KEYS.iter().filter(|key| campaign.contains_key(key)).count();
    let invalid_settings = || Error::profile("Invalid original campaign settings");
    if present_original > 0 {
        let valid_options = match crate::templates::options(campaign.get("original_options")) {
            Ok(options) => options,
            Err(error) if error.is_value_error() => return Err(invalid_settings()),
            Err(error) => return Err(error),
        };
        if present_original != ORIGINAL_KEYS.len() {
            return Err(invalid_settings());
        }
        let template_id = match field("original_template_id") {
            value @ (Value::List(_) | Value::Dict(_)) => {
                return Err(Error::type_error(format!("unhashable type: '{}'", value.type_name())))
            }
            value => value.as_str(),
        };
        let known = crate::templates::template_ids();
        let choices_valid = field("original_choices").as_dict().is_some_and(|choices| {
            choices.has_exact_keys(&["items", "targets"])
                && choices.values().all(|value| value.as_str().is_some_and(|value| crate::templates::POOL_CHOICES.contains(&value)))
        });
        if !template_id.is_some_and(|identity| known.iter().any(|known| known == identity))
            || !choices_valid
            || !field("original_options").is_dict()
            || !Value::Dict(valid_options).py_eq(field("original_options"))
        {
            return Err(invalid_settings());
        }
    }
    let present_original = present_original > 0;
    let shield = campaign.get("spell_shield");
    let option = |name: &str| -> Option<&Value> { campaign.get("original_options").and_then(|options| options.get(name)) };
    if present_original && option("more_spell_shield").is_some_and(Value::truthy) {
        let shield_value = shield.unwrap_or(&Value::Null);
        crate::original_options::validate_spell_shield_spec(shield_value)?;
        // The scroll's row is in BG2's extended table: only a preset reading
        // it randomises the stock.
        let randomized = option("scrolls").is_some_and(Value::truthy)
            && field("original_choices").get("items").and_then(Value::as_str) == Some("extended")
            && field("original_template_id")
                .as_str()
                .map(crate::templates::template_tables)
                .transpose()?
                .is_some_and(|tables| tables.contains(&"bg2"));
        let expected = if randomized { crate::original_options::SPELL_SHIELD_SOURCE } else { "" };
        if shield_value.at("source_id")?.as_str() != Some(expected) {
            return Err(Error::profile("Spell Shield source does not match original item pool choices"));
        }
    } else if campaign.contains_key("spell_shield") {
        return Err(Error::profile("Spell Shield stock requires its original option"));
    }
    if present_original && option("cursed_disguise").is_some_and(Value::truthy) {
        crate::cursed_disguises::validate_cursed_disguise_spec(campaign.get("cursed_disguises").unwrap_or(&Value::Null))?;
    } else if campaign.contains_key("cursed_disguises") {
        return Err(Error::profile("Cursed disguises require their original option"));
    }
    let mut totals = HashMap::new();
    for key in ["sources", "targets"] {
        let rows = match field(key) {
            Value::List(rows) if (1..=MAX_REWARD_UNITS as usize).contains(&rows.len()) => rows,
            _ => return Err(Error::profile(format!("Campaign {key} must contain 1-{MAX_REWARD_UNITS} records"))),
        };
        let mut total = 0i64;
        let mut identities: Vec<&str> = Vec::with_capacity(rows.len());
        for row in rows {
            total += if key == "sources" { validate_source(row, shield)? } else { validate_target(row)? };
            identities.push(row.at("id")?.as_str().unwrap_or(""));
        }
        if identities.windows(2).any(|pair| pair[0] >= pair[1]) {
            return Err(Error::profile(format!("Campaign {key} must have unique, sorted identities")));
        }
        if total > MAX_REWARD_UNITS {
            return Err(Error::profile(format!("Campaign {key} total exceeds prototype limit {MAX_REWARD_UNITS}")));
        }
        totals.insert(key, total);
    }
    let sources = field("sources").as_list().expect("checked");
    let targets = field("targets").as_list().expect("checked");
    if let Some(shield) = shield.filter(|shield| shield.truthy()) {
        let source_id = shield.at("source_id")?;
        if source_id.truthy() && sources.iter().filter(|row| row.get("id").is_some_and(|id| id.py_eq(source_id))).count() != 1 {
            return Err(Error::profile("Spell Shield option source is missing"));
        }
    }
    if !present_original
        && sources.iter().chain(targets).any(|row| {
            matches!(
                row.get("record").and_then(|record| record.get("adapter_id")).and_then(Value::as_str),
                Some(original::SOURCE_ADAPTER | original::TARGET_ADAPTER)
            )
        })
    {
        return Err(Error::profile("Authored records need original campaign settings"));
    }
    if let Some(randomization) = campaign.get("randomization") {
        validate_randomization(randomization, sources, targets)?;
    }
    let overflow = campaign
        .get("randomization")
        .and_then(|randomization| randomization.get("overflow"))
        .is_some_and(|flag| matches!(flag, Value::Bool(true)));
    if totals["targets"] < totals["sources"] && !overflow {
        return Err(Error::profile("Campaign target capacity cannot hold its selected reward records"));
    }
    if let Some(pricing) = campaign.get("shop_pricing") {
        let mut selected: Vec<String> = sources
            .iter()
            .filter_map(|row| row.get("record").and_then(|record| record.get("item")).and_then(Value::as_str))
            .map(str::to_string)
            .collect();
        selected.sort();
        selected.dedup();
        let items: Vec<Value> = selected.iter().map(|item| Value::from(item.as_str())).collect();
        crate::profiles::validate_shop_pricing_prices(pricing, &items, &|_| 0)?;
    }
    let mut payload = campaign.clone();
    payload.remove("revision");
    if digest(&Value::Dict(payload))? != field("revision").as_str().unwrap_or("") {
        return Err(Error::profile("Campaign prototype was modified"));
    }
    // Validate every nested field for the same scalar representation as Lua.
    render_lua(body)?;
    Ok(())
}

/// Compile reviewed physical selections, without choosing assignments.
pub fn compile_campaign(
    profile: &Value,
    catalog: &Value,
    world: &Value,
    plan: &Value,
    scans: Option<&Scans>,
) -> Result<Value> {
    let bound = bind_profile(profile, catalog, world, plan, scans)?;
    if bound.at("additions")?.truthy() {
        return Err(Error::profile("Campaign prototype does not support additional item copies"));
    }
    for row in plan.at("registrations")?.as_list().into_iter().flatten() {
        match row.get("adapter_id").and_then(Value::as_str) {
            Some(crate::virtual_sources::ADAPTER_ID) => crate::virtual_sources::validate_registration(row)?,
            // bind_profile already compared this record with the authored
            // table row, selected scan, and resource fingerprint.
            Some(original::SOURCE_ADAPTER) => {}
            _ => crate::scripted_sources::validate_registration(row)?,
        }
    }
    let bound_sources = bound.at("sources")?.as_list().cloned().unwrap_or_default();
    if bound_sources.iter().any(|row| !matches!(row.get("kind").and_then(Value::as_str), Some("physical" | "registered"))) {
        return Err(Error::profile("Unsupported bound campaign source"));
    }
    let limit = PyNumber::Int(i128::from(MAX_REWARD_UNITS));
    if limit.less_than(PyNumber::Int(0).add(bound.at("reward_units")?)) {
        return Err(Error::profile(format!("Campaign reward units exceed prototype limit {MAX_REWARD_UNITS}")));
    }
    // Reject oversized explicit choices even when an optional target is absent.
    let mut requested = PyNumber::Int(0);
    for row in plan.at("targets")?.as_list().into_iter().flatten() {
        requested = requested.add(row.at("capacity")?);
    }
    let capacity_limit = PyNumber::Int(i128::from(MAX_TARGET_CAPACITY));
    if capacity_limit.less_than(requested) || capacity_limit.less_than(PyNumber::Int(0).add(bound.at("target_capacity")?)) {
        return Err(Error::profile(format!("Campaign target capacity exceeds prototype limit {MAX_TARGET_CAPACITY}")));
    }
    let bound_profile = bound.at("profile")?;
    let mut body = crate::dict! {
        "schema" => CAMPAIGN_SCHEMA, "state" => STATE, "bound_revision" => bound.at("fingerprint")?.clone(),
        "profile_id" => bound_profile.at("id")?.clone(), "world_fingerprint" => bound.at("world_fingerprint")?.clone(),
        "sources" => crate::list![], "targets" => crate::list![],
        "shop_pricing" => bound_profile.get("shop_pricing").cloned().unwrap_or_else(default_shop_pricing),
        "runtime_requirements" => bound.at("runtime_requirements")?.clone(),
    };
    let mut sources = Vec::new();
    for selected in &bound_sources {
        let source = selected.at("source")?;
        if selected.at("kind")?.as_str() == Some("registered") {
            let adapter = source.at("adapter_id")?.as_str();
            let row = if adapter == Some(crate::virtual_sources::ADAPTER_ID) {
                crate::virtual_sources::validate_registration(source)?;
                crate::dict! {
                    "id" => source.at("id")?.clone(), "quantity" => source.at("copies")?.clone(),
                    "record" => crate::virtual_sources::campaign_record(source)?,
                    "resource_sha256" => source.at("resource_sha256")?.clone(),
                }
            } else if adapter == Some(original::SOURCE_ADAPTER) {
                crate::dict! {
                    "id" => source.at("id")?.clone(), "quantity" => source.at("copies")?.clone(),
                    "record" => original::source_record(source)?,
                    "resource_sha256" => source.at("resource_sha256")?.clone(),
                }
            } else {
                crate::scripted_sources::validate_registration(source)?;
                crate::dict! {
                    "id" => crate::scripted_sources::SOURCE_ID, "quantity" => 1,
                    "record" => crate::scripted_sources::campaign_record(source)?,
                    "resource_sha256" => source.at("resource_sha256")?.clone(),
                }
            };
            sources.push(Value::Dict(row));
            continue;
        }
        let quantity = if source.at("kind")?.as_str() == Some("store") {
            source.at("record")?.at("quantity")?.clone()
        } else {
            Value::Int(1)
        };
        let mut row = crate::dict! {
            "id" => source.at("id")?.clone(), "quantity" => quantity, "record" => source.clone(),
            "resource_sha256" => selected.at("resource_sha256")?.clone(),
        };
        if let Some(replacement) = selected.get("replacement") {
            row.insert("replacement", replacement.clone());
        }
        sources.push(Value::Dict(row));
    }
    let mut targets = Vec::new();
    for selected in bound.at("targets")?.as_list().into_iter().flatten() {
        let target = selected.at("target")?;
        targets.push(Value::Dict(crate::dict! {
            "id" => target.at("id")?.clone(), "capacity" => selected.at("capacity")?.clone(),
            "record" => target.clone(), "resource_sha256" => selected.at("resource_sha256")?.clone(),
        }));
    }
    let by_id = |a: &Value, b: &Value| {
        let key = |row: &Value| row.get("id").map(crate::json::py_str).unwrap_or_default();
        key(a).cmp(&key(b))
    };
    sources.sort_by(by_id);
    targets.sort_by(by_id);
    body.insert("sources", Value::List(sources));
    body.insert("targets", Value::List(targets));
    let bound_plan = bound.at("plan")?;
    if let Some(randomization) = bound_plan.get("randomization") {
        body.insert("randomization", randomization.clone());
    }
    if bound_plan.get("original_template_id").is_some() {
        for key in ORIGINAL_KEYS {
            body.insert(key, bound_plan.at(key)?.clone());
        }
    }
    for key in ["spell_shield", "cursed_disguises"] {
        if let Some(value) = bound_plan.get(key) {
            body.insert(key, value.clone());
        }
    }
    let revision = digest(&Value::Dict(body.clone()))?;
    body.insert("revision", revision);
    let body = Value::Dict(body);
    validate_campaign(&body)?;
    Ok(body)
}

/// LuaJIT's per-function limit for GC constants and for number constants.
const LUAJIT_CONSTANTS: usize = 65536;
/// The constants of any kind a function of a split Lua rendering may hold.
const LUA_PART: usize = 32768;

/// The two limits above; with the `parity-overrides` feature as
/// `CIR_PARITY_LUA_LIMITS` ("constants,part") names them, the way the
/// reference's tests patch them.
fn lua_limits() -> (usize, usize) {
    #[cfg(feature = "parity-overrides")]
    if let Some(text) = std::env::var_os("CIR_PARITY_LUA_LIMITS") {
        let text = text.to_string_lossy();
        let limits = text.split_once(',').and_then(|(constants, part)| Some((constants.parse().ok()?, part.parse().ok()?)));
        return limits.expect("CIR_PARITY_LUA_LIMITS is constants,part");
    }
    (LUAJIT_CONSTANTS, LUA_PART)
}

/// Whether LuaJIT refuses the single table constructor of this value.
///
/// The game's LuaJIT compiles at most 65536 GC and 65536 number constants
/// into one function. A constructor costs a GC constant (its template table)
/// when it has a string key or a non-table value; a string key whose value is
/// a table costs that string once per function, and an array index above
/// 32767 whose value is a table costs a number once per function.
fn luajit_overflows(value: &Value, limit: usize) -> bool {
    let table = |item: &Value| matches!(item, Value::Dict(_) | Value::List(_));
    let mut templates = 0;
    let mut keys: HashSet<&str> = HashSet::new();
    let mut numbers: HashSet<usize> = HashSet::new();
    let mut pending = vec![(value, 0)];
    while let Some((node, depth)) = pending.pop() {
        if depth > 32 {
            continue; // The renderer refuses it.
        }
        match node {
            Value::Dict(dict) => {
                templates += usize::from(!dict.is_empty());
                for (key, item) in dict.iter() {
                    if table(item) {
                        keys.insert(key);
                        pending.push((item, depth + 1));
                    }
                }
            }
            Value::List(list) => {
                templates += usize::from(list.iter().any(|item| !table(item)));
                for (index, item) in list.iter().enumerate() {
                    if table(item) {
                        if index + 1 > 32767 {
                            numbers.insert(index + 1);
                        }
                        pending.push((item, depth + 1));
                    }
                }
            }
            _ => {}
        }
    }
    templates + keys.len() > limit || numbers.len() > limit
}

/// A data-only Lua table, preserving UTF-8 bytes without raw text.
///
/// Fixed-width decimal escapes also prevent a following digit from joining
/// an escape. JSON null becomes nil; null-valued object keys are therefore
/// absent in Lua. Arrays use explicit one-based keys so nil never shifts
/// later values. Floats and integers inexact in Lua 5.1 are unsupported.
///
/// A value LuaJIT cannot compile as one table constructor is built instead
/// by functions that each hold at most about `LUA_PART` constants of any
/// kind, so LuaJIT and Lua 5.1 to 5.4 load it. Every other value keeps the
/// single constructor.
pub fn render_lua(value: &Value) -> Result<Vec<u8>> {
    render_lua_as(value, false)
}

/// The single table constructor earlier versions rendered for `value`
/// although LuaJIT refuses it, else None. No game could load it, so the
/// current rendering of the same data replaces it.
pub fn refused_lua(value: &Value) -> Result<Option<Vec<u8>>> {
    if luajit_overflows(value, lua_limits().0) {
        render_lua_as(value, true).map(Some)
    } else {
        Ok(None)
    }
}

/// Whether `new` is the current rendering of a revision Lua artifact's JSON
/// twin (next to `lua`) and `old` the refused rendering earlier versions wrote.
pub fn lua_upgrade(lua: &Path, old: &[u8], new: &[u8]) -> Result<bool> {
    if lua.extension().is_none_or(|extension| extension != "lua") {
        return Ok(false);
    }
    let Ok(value) = read_json(&lua.with_extension("json")) else { return Ok(false) };
    Ok(match refused_lua(&value)? {
        Some(refused) => old == refused.as_slice() && new == render_lua(&value)?.as_slice(),
        None => false,
    })
}

fn render_lua_as(value: &Value, single: bool) -> Result<Vec<u8>> {
    render_lua_limited(value, single, lua_limits())
}

fn render_lua_limited(value: &Value, single: bool, (limit, part): (usize, usize)) -> Result<Vec<u8>> {
    let split = !single && luajit_overflows(value, limit);
    fn string(text: &str) -> String {
        let mut out = String::with_capacity(text.len() * 4 + 2);
        out.push('"');
        for byte in text.bytes() {
            out.push_str(&format!("\\{byte:03}"));
        }
        out.push('"');
        out
    }
    // The expression and, for split renderings, a bound of the constants it
    // adds to the function evaluating it.
    fn render(node: &Value, depth: usize, split: Option<usize>) -> Result<(String, usize)> {
        if depth > 32 {
            return Err(Error::profile("Campaign data nesting exceeds prototype limit 32"));
        }
        let entries: Vec<(String, &Value)> = match node {
            Value::Null => return Ok(("nil".to_string(), 0)),
            Value::Bool(flag) => return Ok(((if *flag { "true" } else { "false" }).to_string(), 0)),
            Value::Int(number) if number.unsigned_abs() <= MAX_LUA_INTEGER as u64 => {
                return Ok((number.to_string(), 1))
            }
            Value::Str(text) => return Ok((string(text), 1)),
            Value::Dict(dict) => {
                let mut items: Vec<(&str, &Value)> = dict.iter().collect();
                items.sort_by(|a, b| a.0.cmp(b.0));
                let mut entries = Vec::with_capacity(items.len());
                for (key, item) in items {
                    // Keys are rendered one level deeper, as values are.
                    if depth + 1 > 32 {
                        return Err(Error::profile("Campaign data nesting exceeds prototype limit 32"));
                    }
                    entries.push((string(key), item));
                }
                entries
            }
            Value::List(list) => list.iter().enumerate().map(|(index, item)| ((index + 1).to_string(), item)).collect(),
            _ => {
                return Err(Error::profile(
                    "Campaign data must use strings, exact integers, booleans, nulls or tables",
                ))
            }
        };
        let mut rendered = Vec::with_capacity(entries.len());
        let mut cost = 1;
        for (key, item) in entries {
            let (text, item_cost) = render(item, depth + 1, split)?;
            cost += 1 + item_cost;
            rendered.push((key, text, item_cost));
        }
        let Some(part) = split.filter(|part| cost > *part) else {
            let indent = "  ".repeat(depth + 1);
            let mut out = String::from("{\n");
            for (key, text, _) in &rendered {
                out.push_str(&indent);
                out.push('[');
                out.push_str(key);
                out.push_str("] = ");
                out.push_str(text);
                out.push_str(",\n");
            }
            out.push_str(&"  ".repeat(depth));
            out.push('}');
            return Ok((out, cost));
        };
        // Consecutive entries fill the table in separate functions.
        let mut parts: Vec<String> = Vec::new();
        let (mut body, mut entries_in_body, mut body_cost) = (String::new(), 0, 0);
        for (key, text, item_cost) in &rendered {
            if entries_in_body > 0 && body_cost + 1 + item_cost > part {
                parts.push(std::mem::take(&mut body));
                (entries_in_body, body_cost) = (0, 0);
            }
            body.push_str("t[");
            body.push_str(key);
            body.push_str("] = ");
            body.push_str(text);
            body.push('\n');
            entries_in_body += 1;
            body_cost += 1 + item_cost;
        }
        parts.push(body);
        let mut out = String::from("(function()\nlocal t, parts = {}, {\n");
        for body in &parts {
            out.push_str("function(t)\n");
            out.push_str(body);
            out.push_str("end,\n");
        }
        out.push_str("}\nfor i = 1, #parts do parts[i](t) end\nreturn t\nend)()");
        Ok((out, 1))
    }
    let (text, _) = render(value, 0, split.then_some(part))?;
    Ok(format!("return {text}\n").into_bytes())
}

/// Reject linked artifacts and paths redirected outside this revision store.
pub fn store_path(store: &Path, relative: &Path) -> Result<PathBuf> {
    let store = paths::resolve(store, false).map_err(|(error, at)| Error::win(&error, &at))?;
    let path = store.join(relative);
    let resolved = paths::resolve(&path, false).map_err(|(error, at)| Error::win(&error, &at))?;
    if paths::is_symlink(&path) || !paths::is_inside(&resolved, &store) {
        return Err(Error::profile("Campaign artifact escapes its store or is a linked file"));
    }
    Ok(resolved)
}

/// Publish a complete revision with create-if-absent semantics.
pub fn write_immutable(path: &Path, data: &[u8]) -> Result<()> {
    write_immutable_replacing(path, data, None)
}

/// An earlier version's content that new content may replace, else None.
pub type Replaceable<'a> = &'a dyn Fn() -> Result<Option<Vec<u8>>>;

/// `write_immutable`, where `replaceable` returns an earlier version's
/// content that `data` may replace (a refused Lua rendering, `refused_lua`).
pub fn write_immutable_replacing(path: &Path, data: &[u8], replaceable: Option<Replaceable>) -> Result<()> {
    make_dirs(&paths::parent(path))?;
    if paths::is_symlink(path) {
        return Err(Error::profile("Refusing to publish a linked campaign artifact"));
    }
    let replace = paths::exists(path).map_err(|error| Error::win(&error, path))?;
    if replace {
        let existing = fs::read(path).map_err(|error| Error::os(&error, path))?;
        if existing == data {
            return Ok(());
        }
        let earlier = match replaceable {
            Some(replaceable) => replaceable()?,
            None => None,
        };
        if earlier.as_deref() != Some(existing.as_slice()) {
            return Err(Error::profile("An immutable campaign revision has conflicting content"));
        }
    }
    let parent = paths::parent(path);
    let mut temporary: Option<PathBuf> = None;
    let result = (|| -> Result<()> {
        let mut attempts = 0;
        let (mut handle, name) = loop {
            let candidate = parent.join(format!(".flir-{}.tmp", random_name()));
            match fs::OpenOptions::new().write(true).create_new(true).open(&candidate) {
                Ok(handle) => break (handle, candidate),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && attempts < 100 => attempts += 1,
                Err(error) => return Err(Error::os(&error, &candidate)),
            }
        };
        temporary = Some(name.clone());
        handle.write_all(data).map_err(|error| Error::os(&error, &name))?;
        handle.flush().map_err(|error| Error::os(&error, &name))?;
        handle.sync_all().map_err(|error| Error::os(&error, &name))?;
        drop(handle);
        if replace {
            // The replaceable earlier version checked above.
            return fs::rename(&name, path).map_err(|error| Error::win_pair(&error, &name, path));
        }
        // A hard link publishes the fully written bytes without replacing an
        // existing immutable revision if another process won the same race.
        match fs::hard_link(&name, path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if paths::is_symlink(path) || fs::read(path).map_err(|error| Error::os(&error, path))? != data {
                    return Err(Error::profile("An immutable campaign revision has conflicting content"));
                }
                Ok(())
            }
            Err(error) => Err(Error::win_pair(&error, &name, path)),
        }
    })();
    if let Some(name) = temporary {
        if paths::exists(&name).unwrap_or(false) {
            let _ = fs::remove_file(&name);
        }
    }
    result
}

/// Publish a verified JSON/Lua pair before updating a separate draft pointer.
pub fn write_campaign(
    profile: &Value,
    catalog: &Value,
    world: &Value,
    plan: &Value,
    store: &Path,
    scans: Option<&Scans>,
) -> Result<Value> {
    let body = compile_campaign(profile, catalog, world, plan, scans)?;
    let store = ensure_output_outside_game(store, catalog)?;
    let revision = body.at("revision")?.as_str().unwrap_or("").to_string();
    let revisions = Path::new("revisions");
    let revision_path = store_path(&store, &revisions.join(format!("{revision}.json")))?;
    let lua_path = store_path(&store, &revisions.join(format!("{revision}.lua")))?;
    let pointer_path = store_path(&store, Path::new("prototype-next.json"))?;
    for path in [&revision_path, &lua_path, &pointer_path] {
        ensure_output_outside_game(path, catalog)?;
    }
    write_immutable(&revision_path, &canonical_bytes(&body)?)?;
    write_immutable_replacing(&lua_path, &render_lua(&body)?, Some(&|| refused_lua(&body)))?;
    write_json(
        &pointer_path,
        &Value::Dict(crate::dict! {"schema" => POINTER_SCHEMA, "revision" => revision.as_str()}),
        false,
    )?;
    Ok(Value::Dict(crate::dict! {
        "revision" => revision.as_str(), "state" => STATE,
        "revision_path" => paths::display(&revision_path), "lua_path" => paths::display(&lua_path),
        "pointer_path" => paths::display(&pointer_path),
    }))
}

/// Read a hash-validated prototype and require the matching Lua companion.
pub fn read_campaign(store: &Path, revision: Option<&str>) -> Result<Value> {
    let revision: Value = match revision {
        Some(revision) => Value::from(revision),
        None => {
            let pointer = read_json(&store_path(store, Path::new("prototype-next.json"))?)?;
            let pointer = fields(Some(&pointer), &["schema", "revision"], "pointer")?;
            if pointer.get("schema").and_then(Value::as_str) != Some(POINTER_SCHEMA) {
                return Err(Error::profile("Unsupported campaign prototype pointer"));
            }
            pointer.get("revision").cloned().unwrap_or(Value::Null)
        }
    };
    let revision = hash(Some(&revision), "revision identity")?.to_string();
    let revisions = Path::new("revisions");
    let body = read_json(&store_path(store, &revisions.join(format!("{revision}.json")))?)?;
    validate_campaign(&body)?;
    if body.at("revision")?.as_str() != Some(revision.as_str()) {
        return Err(Error::profile("Campaign revision identity disagrees with its filename"));
    }
    let lua = store_path(store, &revisions.join(format!("{revision}.lua")))?;
    let lua = fs::read(&lua).map_err(|error| Error::os(&error, &lua))?;
    // A refused rendering is the same data; publishing it again replaces it.
    if lua != render_lua(&body)? && Some(lua) != refused_lua(&body)? {
        return Err(Error::profile("Campaign JSON and Lua artifacts disagree"));
    }
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resources::sha256_hex;

    fn list(items: impl IntoIterator<Item = Value>) -> Value {
        Value::List(items.into_iter().collect())
    }

    fn dict<const N: usize>(entries: [(&str, Value); N]) -> Value {
        let mut dict = Dict::new();
        for (key, value) in entries {
            dict.insert(key, value);
        }
        Value::Dict(dict)
    }

    // The boundaries tests/test_campaign_lua_rendering.py checks against LuaJIT.
    #[test]
    fn luajit_limits_match_the_reference_boundaries() {
        let keyed = |count: usize| {
            let mut keys = Dict::new();
            for index in 0..count {
                keys.insert(format!("k{index}"), list([Value::Int(1)]));
            }
            Value::Dict(keys)
        };
        let cases: [(&dyn Fn(usize) -> Value, usize); 5] = [
            (&|count| list((0..count).map(|_| list([Value::Int(1)]))), 65536),
            (&|count| list((0..count).map(|_| list([Value::Null]))), 65536),
            (&keyed, 32767),
            (&|count| list((0..count).map(|_| dict([("record", dict([("a", Value::Int(1))]))]))), 32767),
            (&|count| list((0..count).map(|_| dict([]))), 98303),
        ];
        for (make, fits) in cases {
            for count in [fits, fits + 1] {
                let value = make(count);
                assert_eq!(luajit_overflows(&value, LUAJIT_CONSTANTS), count > fits, "{fits} {count}");
                let single = render_lua_as(&value, true).unwrap();
                if count > fits {
                    assert_eq!(refused_lua(&value).unwrap(), Some(single.clone()));
                    assert_ne!(render_lua(&value).unwrap(), single);
                } else {
                    assert_eq!(refused_lua(&value).unwrap(), None);
                    assert_eq!(render_lua(&value).unwrap(), single);
                }
            }
        }
    }

    // The same bytes as the reference renders (tests/test_campaign_lua_rendering.py).
    #[test]
    fn split_renderings_match_the_reference() {
        let rows = (0..70000i64).map(|index| {
            dict([
                ("flag", Value::Bool(index % 3 == 0)),
                ("id", Value::from(format!("row-{index}"))),
                ("n", Value::Int(index)),
                ("none", Value::Null),
                ("tags", list([Value::from(format!("t{}", index % 7)), Value::Int(index % 5)])),
            ])
        });
        let golden = dict([("name", Value::from("golden \u{e9}")), ("rows", list(rows))]);
        assert_eq!(
            sha256_hex(&render_lua(&golden).unwrap()),
            "c6449d33c1b94fba6e9414f4562af3238124a8f6eb73fc05804c5abb687d5ce8"
        );
        let record = || {
            dict([
                ("b", list([Value::Int(1), Value::Int(2), Value::Null, Value::from("x")])),
                ("c", dict([("d", Value::Bool(true)), ("e", Value::Bool(false))])),
            ])
        };
        let nested = list([list([list([list([list([Value::Int(9007199254740991)])])])])]);
        let small = dict([
            ("a", list((0..5).map(|_| record()))),
            ("f", Value::from("\u{e9}\u{0}\n")),
            ("g", Value::Null),
            ("h", list([])),
            ("i", dict([])),
            ("j", nested),
        ]);
        let text = render_lua_limited(&small, false, (4, 8)).unwrap();
        assert_eq!(sha256_hex(&text), "258544c293cfb8ce83942bd9b8fbe72fd4b6455cf5e6f402a414983868b519f3");
        assert!(text.starts_with(b"return (function()\n"));
    }

    #[test]
    fn invalid_data_is_refused_in_split_renderings_too() {
        for value in [Value::Float(0.5), Value::Int(1 << 53), Value::BigInt("18446744073709551616".into())] {
            let error = render_lua_limited(&list([value]), false, (0, 2)).unwrap_err();
            assert!(error.to_string().contains("strings, exact integers"), "{error}");
        }
        let mut deep = list([]);
        for _ in 0..33 {
            deep = list([deep]);
        }
        let error = render_lua_limited(&deep, false, (0, 2)).unwrap_err();
        assert!(error.to_string().contains("nesting exceeds prototype limit 32"), "{error}");
    }
}
