//! Checked-in original-table evidence for data-only source and target
//! records (the reference's `original_registrations.py`).

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::mode2_rewards::Naming;
use crate::original_catalog::{
    container_target_spec, is_spin621_source, is_spin621_target, mode2_alias_spec, resolve_template,
};
use crate::original_options::{is_spell_shield_row, validate_spell_shield_spec};
use crate::profiles::value_is_hex256;
use crate::templates::POOL_CHOICES;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

pub const SOURCE_ADAPTER: &str = "original-source-v1";
pub const TARGET_ADAPTER: &str = "original-target-v1";
pub const SOURCE_FIELDS: [&str; 10] =
    ["id", "source_ref", "campaign", "family", "source", "ident", "token", "tier", "occurrence", "chance"];
pub const TARGET_FIELDS: [&str; 19] = [
    "id",
    "source_ref",
    "endpoint_id",
    "endpoint_kind",
    "endpoint_source_ref",
    "campaign",
    "family",
    "location",
    "target",
    "area",
    "ident",
    "tier",
    "occurrence",
    "chance",
    "area_resource",
    "creature_resource",
    "container_name",
    "members",
    "target_ids",
];
pub const PAYLOAD_FIELDS: [&str; 4] = ["resref", "expiration", "charges", "flags"];
pub const SHIPPED_REPLACEMENT: &str = "flbhcryp";
pub const SHIPPED_REPLACEMENT_HASH: &str = "b0b7c29014cc40a9ee118279c2e7c77bb7a4f46fbd5d2ed45481dbddb1114a62";

/// Require the original mod's exact authored replacement file.
pub fn shipped_replacement_hash() -> Result<String> {
    let data = crate::data::file("copy/flbhcryp.itm")
        .ok_or_else(|| Error::profile("Original flbhcryp replacement file is absent"))?;
    let actual = crate::resources::sha256_hex(data);
    if actual != SHIPPED_REPLACEMENT_HASH {
        return Err(Error::profile("Original flbhcryp replacement file changed"));
    }
    Ok(actual)
}

fn fields<'a>(value: Option<&'a Value>, expected: &[&str], label: &str) -> Result<&'a Dict> {
    match value.and_then(Value::as_dict) {
        Some(fields) if fields.has_exact_keys(expected) => Ok(fields),
        _ => Err(Error::profile(format!("Invalid original {label} fields"))),
    }
}

fn hash(value: Option<&Value>) -> Result<()> {
    if value_is_hex256(value) {
        Ok(())
    } else {
        Err(Error::profile("Invalid original resource fingerprint"))
    }
}

fn resource<'a>(value: Option<&'a Value>, kinds: &[&str]) -> Result<&'a str> {
    let invalid = || Error::profile("Invalid original resource identity");
    let text = value.and_then(Value::as_str).ok_or_else(invalid)?;
    let (kind, name) = text.split_once(':').ok_or_else(invalid)?;
    let length = name.chars().count();
    if !kinds.contains(&kind)
        || !(1..=8).contains(&length)
        || name != crate::py::lower(name)
        || name.chars().any(|c| (c as u32) < 33 || (c as u32) > 126 || "/\\:*?\"<>|".contains(c))
    {
        return Err(invalid());
    }
    Ok(text)
}

fn exact_int(value: Option<&Value>, low: i64, high: i64) -> bool {
    matches!(value, Some(Value::Int(number)) if (low..=high).contains(number))
}

fn payload<'a>(value: Option<&'a Value>, item: &Value) -> Result<&'a Dict> {
    let payload = fields(value, &PAYLOAD_FIELDS, "item payload")?;
    let charges_valid = match payload.get("charges") {
        Some(Value::List(charges)) => {
            charges.len() == 3 && charges.iter().all(|charge| exact_int(Some(charge), 0, 0xFFFF))
        }
        _ => false,
    };
    if !payload.get("resref").is_some_and(|resref| resref.py_eq(item))
        || !exact_int(payload.get("expiration"), 0, 0xFFFF)
        || !exact_int(payload.get("flags"), 0, 0xFFFF_FFFF)
        || !charges_valid
    {
        return Err(Error::profile("Invalid original item payload"));
    }
    Ok(payload)
}

/// Rows of a resolved template by identity, in table order.
pub type Rows = Dict;

fn by_id(rows: &Value) -> Rows {
    let mut result = Dict::new();
    for row in rows.as_list().into_iter().flatten() {
        let identity = row.get("id").map(crate::json::py_str).unwrap_or_default();
        result.insert(identity, row.clone());
    }
    result
}

/// Python's `value in {...}` for a set of strings: unhashable values raise.
fn hashable_str(value: Option<&Value>) -> Result<Option<&str>> {
    match value {
        Some(value @ (Value::List(_) | Value::Dict(_))) => {
            Err(Error::type_error(format!("unhashable type: '{}'", value.type_name())))
        }
        Some(Value::Str(text)) => Ok(Some(text)),
        _ => Ok(None),
    }
}

/// The original tables a plan's choices resolve to: the resolved draft's
/// choices and options, and its item and target rows by identity.
pub struct Tables {
    choices: Value,
    options: Value,
    pub sources: Rows,
    pub targets: Rows,
}

/// The tables already resolved for one set of scans (`Scans::tables`), by
/// the template, choices and options that name them.
pub type TableMemo = RefCell<HashMap<String, Rc<Tables>>>;

/// Resolve the exact checked-in pool choices recorded in a preset plan;
/// `memo` keeps the tables already resolved for these scans.
pub fn table_rows(catalog: &Value, world: &Value, plan: &Value, memo: Option<&TableMemo>) -> Result<Rc<Tables>> {
    let template_id = hashable_str(plan.get("original_template_id"))?;
    let choices = plan.get("original_choices");
    let options = plan.get("original_options");
    let known = crate::templates::template_ids();
    let Some(template_id) = template_id.filter(|identity| known.iter().any(|known| known == identity)) else {
        return Err(Error::profile("Unknown original template identity"));
    };
    let valid_choices = choices.and_then(Value::as_dict).is_some_and(|choices| {
        choices.has_exact_keys(&["items", "targets"])
            && choices.values().all(|value| value.as_str().is_some_and(|value| POOL_CHOICES.contains(&value)))
    });
    if !valid_choices || !options.is_some_and(Value::is_dict) {
        return Err(Error::profile("Invalid original pool choices or options"));
    }
    let choices = choices.expect("checked");
    let options = options.expect("checked");
    // The game is told apart before the tables are read, also when a test
    // substitutes the resolved draft.
    let game = crate::platform::preset_game(world, template_id)?;
    let pinned = crate::pins::registration_draft();
    // A test's substituted draft is never kept.
    let key = memo.filter(|_| pinned.is_none()).map(|_| {
        crate::json::dumps_ascii(&crate::list![template_id, choices.clone(), options.clone()], true)
    });
    let kept = match (memo, &key) {
        (Some(memo), Some(key)) => memo.borrow().get(key).cloned(),
        _ => None,
    };
    let tables = match kept {
        Some(tables) => tables,
        None => {
            let draft = match pinned {
                Some(draft) => draft,
                None => resolve_template(
                    catalog,
                    world,
                    template_id,
                    game,
                    choices.get("items"),
                    choices.get("targets"),
                    Some(options),
                    None,
                )?,
            };
            let tables = Rc::new(Tables {
                choices: draft.at("choices")?.clone(),
                options: draft.at("options")?.clone(),
                sources: by_id(draft.at("items")?),
                targets: by_id(draft.at("targets")?),
            });
            if let (Some(memo), Some(key)) = (memo, key) {
                memo.borrow_mut().insert(key, Rc::clone(&tables));
            }
            tables
        }
    };
    if !tables.choices.py_eq(choices) || !tables.options.py_eq(options) {
        return Err(Error::profile("Original pool choices or options changed"));
    }
    Ok(tables)
}

/// Physical sources by identity.
pub type SourceLookup<'a> = HashMap<&'a str, &'a Value>;

pub fn source_lookup(world: &Value) -> SourceLookup<'_> {
    world
        .get("sources")
        .and_then(Value::as_list)
        .into_iter()
        .flatten()
        .map(|source| (source.get("id").and_then(Value::as_str).unwrap_or(""), source))
        .collect()
}

/// Recognize one authored, slotless CRE record without weakening direct
/// binding.
pub fn is_unreferenced_creature_source(row: &Value, physical: &SourceLookup<'_>) -> Result<bool> {
    let identities = row.at("source_ids")?.as_list().map(Vec::as_slice).unwrap_or(&[]);
    if identities.len() != 1 || row.at("replacement")?.as_str() != Some("blank") {
        return Ok(false);
    }
    let Some(source) = identities[0].as_str().and_then(|identity| physical.get(identity)) else {
        return Ok(false);
    };
    let concerns = Value::from(vec!["template-instance-scope", "unreferenced-inventory-record"]);
    Ok(source.get("kind").and_then(Value::as_str) == Some("cre-template")
        && source.at("resource")?.py_eq(row.at("source_resource")?)
        && source.at("item")?.py_eq(row.at("item_resref")?)
        && source.at("record")?.at("slots")?.py_eq(&Value::List(Vec::new()))
        && source.at("concerns")?.py_eq(&concerns))
}

fn authored_source(row: &Value) -> Result<Dict> {
    let mut authored = Dict::new();
    for key in SOURCE_FIELDS {
        authored.insert(key, row.at(key)?.clone());
    }
    Ok(authored)
}

fn authored_target(row: &Value) -> Result<Dict> {
    let mut authored = Dict::new();
    for key in TARGET_FIELDS {
        authored.insert(key, row.at(key)?.clone());
    }
    Ok(authored)
}

/// The registration the More Spell Shield scrolls option stocks.
fn spell_shield_registration(spell_shield: &Value, selector: &Value, authored: Value) -> Result<(Value, Vec<String>)> {
    let original = spell_shield.at("original_source_id")?;
    let physical: Vec<String> = if original.truthy() { vec![crate::json::py_str(original)] } else { Vec::new() };
    let registration = crate::dict! {
        "id" => spell_shield.at("source_id")?.clone(), "item" => "scrl8x", "resource" => "sto:udduer01",
        "resource_sha256" => spell_shield.at("resource_sha256")?.clone(), "adapter_id" => SOURCE_ADAPTER,
        "selector" => selector.clone(), "copies" => spell_shield.at("quantity")?.clone(), "optional" => false,
        "payload" => spell_shield.at("payload")?.clone(),
        "physical_source_ids" => Value::from(physical.clone()), "authored" => authored,
    };
    Ok((Value::Dict(registration), physical))
}

/// Validate an authored source, returning the physical IDs it reserves.
pub fn validate_source_registration(
    registration: &Value,
    rows: &Rows,
    world: &Value,
    spell_shield: Option<&Value>,
) -> Result<Vec<String>> {
    let fields = fields(
        Some(registration),
        &[
            "id",
            "item",
            "resource",
            "resource_sha256",
            "adapter_id",
            "selector",
            "copies",
            "optional",
            "payload",
            "physical_source_ids",
            "authored",
        ],
        "source registration",
    )?;
    let selector = fields.get("selector").expect("checked");
    let identity = hashable_str(Some(selector))?;
    let row = identity.and_then(|identity| rows.get(identity));
    let Some(row) = row.filter(|row| row.get("enabled").is_some_and(Value::truthy)).filter(|_| {
        fields.get("adapter_id").and_then(Value::as_str) == Some(SOURCE_ADAPTER)
    }) else {
        return Err(Error::profile("Original source is not an enabled table row"));
    };
    let identity = identity.expect("found a row");
    if is_spell_shield_row(row) {
        if let Some(spell_shield) = spell_shield {
            validate_spell_shield_spec(spell_shield)?;
            let (expected, physical) =
                spell_shield_registration(spell_shield, &Value::from(identity), Value::Dict(authored_source(row)?))?;
            if !registration.py_eq(&expected) {
                return Err(Error::profile("Original Spell Shield source differs from its option stock"));
            }
            return Ok(physical);
        }
    }
    let text = |key: &str| fields.get(key).and_then(Value::as_str);
    if text("id") != Some(format!("original-source:{identity}").as_str())
        || !fields.get("item").expect("checked").py_eq(row.at("item_resref")?)
        || !fields.get("resource").expect("checked").py_eq(row.at("source_resource")?)
        || !fields.get("copies").expect("checked").py_eq(&Value::Int(1))
        || !matches!(fields.get("optional"), Some(Value::Bool(false)))
    {
        return Err(Error::profile("Original source identity disagrees with its table row"));
    }
    let spell_grant = is_spin621_source(row) && text("resource") == Some("spl:spin621") && text("item") == Some("ring41");
    resource(fields.get("resource"), if spell_grant { &["cre", "bcs", "dlg", "spl"] } else { &["cre", "bcs", "dlg"] })?;
    hash(fields.get("resource_sha256"))?;
    let mut authored = authored_source(row)?;
    let physical_ids: Vec<&str> = match fields.get("physical_source_ids") {
        Some(Value::List(values)) if values.iter().all(Value::is_str) => {
            values.iter().map(|value| value.as_str().expect("checked")).collect()
        }
        _ => return Err(Error::profile("Invalid original physical source IDs")),
    };
    if physical_ids.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(Error::profile("Original physical source IDs must be distinct and sorted"));
    }
    let adapter = row.at("adapter")?.as_str();
    let source_ids: Vec<String> =
        row.at("source_ids")?.as_list().into_iter().flatten().map(crate::json::py_str).collect();
    let lookup = source_lookup(world);
    let expected: Vec<String> = if matches!(adapter, Some("scripted" | "unshey")) {
        if adapter == Some("unshey") {
            let wanted_resource = row.at("source_resource")?;
            let wanted_item = row.at("item_resref")?;
            let mut matched: Vec<String> = world
                .at("sources")?
                .as_list()
                .into_iter()
                .flatten()
                .filter(|source| {
                    source.get("resource").is_some_and(|value| value.py_eq(wanted_resource))
                        && source.get("item").is_some_and(|value| value.py_eq(wanted_item))
                })
                .map(|source| crate::json::py_str(source.get("id").unwrap_or(&Value::Null)))
                .collect();
            matched.sort();
            matched
        } else {
            Vec::new()
        }
    } else if source_ids.len() > 1 || is_unreferenced_creature_source(row, &lookup)? {
        let mut expected = source_ids.clone();
        expected.sort();
        if expected.len() > 1 {
            authored.insert("match_policy", "all_one_unit");
            authored.insert("expected_matches", expected.len());
        }
        expected
    } else {
        return Err(Error::profile("Original physical row needs a direct source selection"));
    };
    if !fields.get("authored").expect("checked").py_eq(&Value::Dict(authored)) || physical_ids != expected {
        return Err(Error::profile("Original source evidence disagrees with the scanned table row"));
    }
    let payload = payload(fields.get("payload"), row.at("item_resref")?)?;
    if !physical_ids.is_empty() {
        for selected in &physical_ids {
            let record = lookup.get(selected).ok_or_else(|| Error::key(selected))?.at("record")?;
            let mut physical = Dict::new();
            for key in PAYLOAD_FIELDS {
                physical.insert(key, record.at(key)?.clone());
            }
            if !Value::Dict(payload.clone()).py_eq(&Value::Dict(physical)) {
                return Err(Error::profile("Original source payload differs from its physical record"));
            }
        }
    } else if !payload.get("expiration").expect("checked").py_eq(&Value::Int(0))
        || !payload.get("flags").expect("checked").py_eq(&Value::Int(0))
    {
        return Err(Error::profile("Original scripted source must use a neutral item payload"));
    }
    Ok(physical_ids.into_iter().map(str::to_string).collect())
}

pub fn validate_target_registration(registration: &Value, rows: &Rows) -> Result<()> {
    let fields = fields(
        Some(registration),
        &["id", "resource", "resource_sha256", "adapter_id", "selector", "capacity", "optional", "authored"],
        "target registration",
    )?;
    let identity = hashable_str(fields.get("selector"))?;
    let row = identity.and_then(|identity| rows.get(identity));
    let Some(row) = row.filter(|row| row.get("enabled").is_some_and(Value::truthy)).filter(|_| {
        fields.get("adapter_id").and_then(Value::as_str) == Some(TARGET_ADAPTER)
    }) else {
        return Err(Error::profile("Original target is not an enabled table row"));
    };
    let identity = identity.expect("found a row");
    if fields.get("id").and_then(Value::as_str) != Some(format!("original-target:{identity}").as_str())
        || !fields.get("resource").expect("checked").py_eq(row.at("area_resource")?)
        || !exact_int(fields.get("capacity"), 1, 4096)
        || !matches!(fields.get("optional"), Some(Value::Bool(false)))
        || !fields.get("authored").expect("checked").py_eq(&Value::Dict(authored_target(row)?))
    {
        return Err(Error::profile("Original target evidence disagrees with its table row"));
    }
    target_resource(row, fields.get("resource").expect("checked"))?;
    hash(fields.get("resource_sha256"))
}

fn target_resource(authored: &Value, resource_value: &Value) -> Result<()> {
    if let Some(reward) = mode2_alias_spec(authored, Naming::default())? {
        if !reward.get("resource").is_some_and(|primary| primary.py_eq(resource_value)) {
            return Err(Error::profile("Original reward target must use its registered primary resource"));
        }
        resource(Some(resource_value), &["dlg", "bcs"])?;
    } else {
        let spell_target = is_spin621_target(authored) && resource_value.as_str() == Some("spl:spin621");
        resource(Some(resource_value), if spell_target { &["spl"] } else { &["are"] })?;
    }
    Ok(())
}

pub fn target_kind(authored: &Value) -> Result<String> {
    if container_target_spec(authored)?.is_some() {
        return Ok("container".into());
    }
    let reward = mode2_alias_spec(authored, Naming::default())?;
    if reward.as_ref().is_some_and(|reward| reward.get("inventory_target").is_some_and(Value::truthy)) {
        return Ok("creature-target".into());
    }
    if is_spin621_target(authored) || reward.is_some() {
        return Ok("scripted-target".into());
    }
    let kind = match authored.at("endpoint_kind")? {
        Value::Null => {
            let Some(raw) = authored.at("target")?.as_str() else {
                return Err(Error::profile("Invalid original target identity"));
            };
            if raw.ends_with(".flg") {
                "group"
            } else if raw.ends_with(".cre") {
                "creature"
            } else if raw.ends_with(".sto") {
                "store"
            } else if raw.ends_with(".bcs") || raw.ends_with(".dlg") {
                "legacy_adapter"
            } else {
                "container"
            }
            .to_string()
        }
        Value::Str(kind) => kind.clone(),
        other @ (Value::List(_) | Value::Dict(_)) => {
            return Err(Error::type_error(format!("unhashable type: '{}'", other.type_name())))
        }
        _ => return Err(Error::profile("Invalid original target kind")),
    };
    if !["creature", "legacy_adapter", "group", "container", "store"].contains(&kind.as_str()) {
        return Err(Error::profile("Invalid original target kind"));
    }
    Ok(match kind.as_str() {
        "creature" => "creature-target".into(),
        "legacy_adapter" => "scripted-target".into(),
        _ => kind,
    })
}

pub fn source_record(registration: &Value) -> Result<Value> {
    let authored = registration.at("authored")?;
    let kind = if is_spell_shield_row(authored) { "store" } else { "authored-source" };
    Ok(Value::Dict(crate::dict! {
        "id" => registration.at("id")?.clone(), "item" => registration.at("item")?.clone(),
        "resource" => registration.at("resource")?.clone(), "owner" => registration.at("resource")?.clone(),
        "kind" => kind, "record" => registration.at("payload")?.clone(),
        "adapter_id" => SOURCE_ADAPTER, "selector" => registration.at("selector")?.clone(),
        "physical_source_ids" => registration.at("physical_source_ids")?.clone(),
        "authored" => authored.clone(), "concerns" => crate::list![],
    }))
}

pub fn target_record(registration: &Value) -> Result<Value> {
    Ok(Value::Dict(crate::dict! {
        "id" => registration.at("id")?.clone(), "resource" => registration.at("resource")?.clone(),
        "kind" => target_kind(registration.at("authored")?)?,
        "selector" => registration.at("authored")?.clone(),
        "adapter_id" => TARGET_ADAPTER, "concerns" => crate::list![],
    }))
}

pub fn validate_campaign_source(row: &Value, spell_shield: Option<&Value>) -> Result<i64> {
    fields(Some(row), &["id", "quantity", "record", "resource_sha256"], "campaign source row")?;
    let record = fields(
        row.get("record"),
        &[
            "id",
            "item",
            "resource",
            "owner",
            "kind",
            "record",
            "adapter_id",
            "selector",
            "physical_source_ids",
            "authored",
            "concerns",
        ],
        "campaign source",
    )?;
    let field = |key: &str| record.get(key).expect("checked");
    let authored = field("authored");
    if is_spell_shield_row(authored) {
        if let Some(spell_shield) = spell_shield {
            validate_spell_shield_spec(spell_shield)?;
            let (registration, _) =
                spell_shield_registration(spell_shield, &Value::from("bg2:mode2:item:228"), authored.clone())?;
            let authored_fields_match = authored.as_dict().is_some_and(|authored| authored.has_exact_keys(&SOURCE_FIELDS));
            if !authored_fields_match
                || !Value::Dict(record.clone()).py_eq(&source_record(&registration)?)
                || !row.at("id")?.py_eq(spell_shield.at("source_id")?)
                || !row.at("quantity")?.py_eq(spell_shield.at("quantity")?)
                || !row.at("resource_sha256")?.py_eq(spell_shield.at("resource_sha256")?)
            {
                return Err(Error::profile("Invalid authored Spell Shield campaign source"));
            }
            return row.at("quantity")?.as_int().ok_or_else(|| Error::profile("Invalid authored Spell Shield campaign source"));
        }
    }
    let selector = field("selector");
    let identity = field("id");
    let expected_identity = selector.as_str().map(|selector| format!("original-source:{selector}"));
    if !selector.is_str()
        || field("kind").as_str() != Some("authored-source")
        || field("adapter_id").as_str() != Some(SOURCE_ADAPTER)
        || !field("owner").py_eq(field("resource"))
        || !field("concerns").py_eq(&Value::List(Vec::new()))
        || !row.get("id").is_some_and(|value| identity.py_eq(value))
        || identity.as_str() != expected_identity.as_deref()
        || !exact_int(row.get("quantity"), 1, 1)
        || row.get("replacement").is_some()
    {
        return Err(Error::profile("Invalid authored campaign source"));
    }
    hash(row.get("resource_sha256"))?;
    let spell_grant = is_spin621_source(authored)
        && field("resource").as_str() == Some("spl:spin621")
        && field("item").as_str() == Some("ring41");
    resource(Some(field("resource")), if spell_grant { &["cre", "bcs", "dlg", "spl"] } else { &["cre", "bcs", "dlg"] })?;
    let item_identity = field("item").as_str().map(|item| Value::from(format!("itm:{item}")));
    resource(item_identity.as_ref(), &["itm"])?;
    payload(Some(field("record")), field("item"))?;
    let physical = field("physical_source_ids");
    let mut extended: Vec<&str> = SOURCE_FIELDS.to_vec();
    extended.extend(["match_policy", "expected_matches"]);
    let physical_ids: Option<Vec<&str>> = physical.as_list().and_then(|values| {
        values.iter().map(|value| value.as_str().filter(|text| !text.is_empty())).collect()
    });
    let valid = authored.as_dict().is_some_and(|fields| {
        (fields.has_exact_keys(&SOURCE_FIELDS) || fields.has_exact_keys(&extended))
            && fields.get("id").is_some_and(|identity| identity.py_eq(selector))
    }) && physical_ids.as_ref().is_some_and(|ids| ids.windows(2).all(|pair| pair[0] < pair[1]));
    if !valid {
        return Err(Error::profile("Invalid authored campaign source evidence"));
    }
    let physical_ids = physical_ids.expect("checked");
    if let Some(policy) = authored.get("match_policy") {
        let expected = authored.get("expected_matches");
        let count = physical_ids.len() as i64;
        if policy.as_str() != Some("all_one_unit")
            || !matches!(expected, Some(Value::Int(number)) if *number == count && *number >= 2)
        {
            return Err(Error::profile("Invalid authored grouped source evidence"));
        }
    }
    if spell_grant && !physical_ids.is_empty() {
        return Err(Error::profile("Spell-grant source cannot reserve physical inventory records"));
    }
    Ok(1)
}

pub fn validate_campaign_target(row: &Value) -> Result<i64> {
    fields(Some(row), &["id", "capacity", "record", "resource_sha256"], "campaign target row")?;
    let record = fields(
        row.get("record"),
        &["id", "resource", "kind", "selector", "adapter_id", "concerns"],
        "campaign target",
    )?;
    let field = |key: &str| record.get(key).expect("checked");
    let selector = field("selector");
    let invalid = || Error::profile("Invalid authored campaign target");
    let selector_fields_match = selector.as_dict().is_some_and(|selector| selector.has_exact_keys(&TARGET_FIELDS));
    if field("adapter_id").as_str() != Some(TARGET_ADAPTER)
        || !field("concerns").py_eq(&Value::List(Vec::new()))
        || !row.get("id").is_some_and(|value| field("id").py_eq(value))
        || !selector_fields_match
    {
        return Err(invalid());
    }
    let expected_identity = format!("original-target:{}", crate::json::py_str(selector.get("id").unwrap_or(&Value::Null)));
    if field("id").as_str() != Some(expected_identity.as_str())
        || !selector.get("area_resource").is_some_and(|area| field("resource").py_eq(area))
        || field("kind").as_str() != Some(target_kind(selector)?.as_str())
        || !exact_int(row.get("capacity"), 1, 4096)
    {
        return Err(invalid());
    }
    target_resource(selector, field("resource"))?;
    hash(row.get("resource_sha256"))?;
    Ok(row.at("capacity")?.as_int().expect("checked"))
}
