//! Original option data and byte patches, without choosing a campaign's rolls
//! (the reference's `original_options.py`).
//!
//! The definitions below come from Item Randomiser's component 560 and the
//! component-570 block of its lib/fixes.tpa. Callers supply installed resource
//! bytes and allocated TLK references; this module never opens or changes a
//! game.

use crate::error::{Error, Result};
use crate::game_names::{convert_resource, EetNames};
use crate::json::{Dict, Value};
use crate::profiles::is_hex256;
use crate::py::{put_u32, str_value, u16_le, u32_le};
use crate::world_formats::parse_sto;
use crate::{dict, list};
use std::cmp::Ordering;
use std::collections::BTreeSet;

/// In Item Randomiser's own randomisation, the b2c reward is one of all six
/// stat books, chosen uniformly.
pub fn tome_variants(campaign: &str, game: &str, names: Option<&EetNames>) -> Result<Vec<String>> {
    (3..9).map(|number| convert_resource(&format!("book{number:02}"), "itm", campaign, game, names)).collect()
}

fn row_int(row: &Dict, key: &str) -> i64 {
    row.get(key).and_then(Value::as_int).expect("parsed STO field")
}

/// Apply component 560 to UDDUER01, retaining all unrelated stock bytes.
///
/// As in the original, the first SCRL8X row wins, including zero stock and
/// infinite rows. Absent stock is inserted before the first matching anchor;
/// the new sale table is appended so all other section offsets stay intact.
pub fn patch_spell_shield_stock(data: &[u8]) -> Result<Vec<u8>> {
    let store = parse_sto(data)?;
    let rows: Vec<&Dict> = store.get("items").and_then(Value::as_list).expect("items").iter().map(|row| row.as_dict().expect("row")).collect();
    let mut result = data.to_vec();
    let resref = |row: &Dict| row.get("resref").and_then(Value::as_str).unwrap_or("").to_string();
    for row in &rows {
        if resref(row) == "scrl8x" {
            put_u32(&mut result, row_int(row, "byte_offset") as usize + 20, row_int(row, "quantity").max(3) as u32);
            return Ok(result);
        }
    }
    let mut records: Vec<Vec<u8>> = rows
        .iter()
        .map(|row| {
            let offset = row_int(row, "byte_offset") as usize;
            data[offset..offset + 28].to_vec()
        })
        .collect();
    let insert = rows
        .iter()
        .position(|row| ["scrlal", "scrl7q", "scrl7p"].contains(&resref(row).as_str()))
        .unwrap_or(rows.len());
    let mut added = b"scrl8x\0\0".to_vec();
    added.extend_from_slice(&[0; 8]);
    for value in [1u32, 3, 0] {
        added.extend_from_slice(&value.to_le_bytes());
    }
    records.insert(insert, added);
    put_u32(&mut result, 0x34, data.len() as u32);
    put_u32(&mut result, 0x38, records.len() as u32);
    for record in &records {
        result.extend_from_slice(record);
    }
    parse_sto(&result)?;
    Ok(result)
}

pub const SPELL_SHIELD_SOURCE: &str = "original-source:bg2:mode2:item:228";
const STOCK_PAYLOAD: [&str; 4] = ["resref", "expiration", "charges", "flags"];

/// The sole original source affected by component 560, not other SCRL8X.
pub fn is_spell_shield_row(row: &Value) -> bool {
    let Some(row) = row.as_dict() else { return false };
    [
        ("id", "bg2:mode2:item:228"),
        ("source_ref", "lists/items/mode2/bg2.2da:228"),
        ("campaign", "bg2"),
        ("family", "mode2"),
        ("source", "udduer01.sto"),
        ("ident", "sw10"),
        ("tier", "s7"),
        ("token", "w01"),
    ]
    .iter()
    .all(|(key, value)| row.get(key).unwrap_or(&Value::Null).py_eq(&Value::from(*value)))
}

/// `value[key]` with Python's KeyError and TypeError messages.
fn item<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    match value {
        Value::Dict(dict) => dict.get(key).ok_or_else(|| Error::key(key)),
        Value::List(_) => Err(Error::type_error("list indices must be integers or slices, not str")),
        Value::Str(_) => Err(Error::type_error("string indices must be integers, not 'str'")),
        other => Err(Error::type_error(format!("'{}' object is not subscriptable", other.type_name()))),
    }
}

/// Python's `a < b` ordering of sort keys: numbers with numbers, strings
/// with strings; anything else is the TypeError `sorted()` raises (for mixed
/// keys the reported pair can differ from CPython's, which compares in
/// timsort order).
fn compare(a: &Value, b: &Value) -> Result<Ordering> {
    let number = |value: &Value| match value {
        Value::Bool(flag) => Some(*flag as i64 as f64),
        Value::Int(number) => Some(*number as f64),
        Value::Float(number) => Some(*number),
        _ => None,
    };
    match (a, b) {
        (Value::Int(x), Value::Int(y)) => Ok(x.cmp(y)),
        (Value::Str(x), Value::Str(y)) => Ok(crate::json::str_cmp(x, y)),
        _ => match (number(a), number(b)) {
            (Some(x), Some(y)) => Ok(x.partial_cmp(&y).unwrap_or(Ordering::Equal)),
            _ => Err(Error::type_error(format!(
                "'<' not supported between instances of '{}' and '{}'",
                a.type_name(),
                b.type_name()
            ))),
        },
    }
}

/// `max(3, value)`.
fn at_least_three(value: &Value) -> Result<Value> {
    match value {
        Value::Int(number) => Ok(Value::Int((*number).max(3))),
        Value::BigInt(text) => Ok(if text.starts_with('-') { Value::Int(3) } else { value.clone() }),
        Value::Bool(_) => Ok(Value::Int(3)),
        Value::Float(number) => Ok(if *number > 3.0 { value.clone() } else { Value::Int(3) }),
        other => Err(Error::type_error(format!("'>' not supported between instances of '{}' and 'int'", other.type_name()))),
    }
}

/// `value in {"scrlal", "scrl7q", "scrl7p"}`.
fn is_anchor(value: &Value) -> Result<bool> {
    match value {
        Value::Str(text) => Ok(["scrlal", "scrl7q", "scrl7p"].contains(&text.as_str())),
        Value::List(_) | Value::Dict(_) => Err(Error::type_error(format!("unhashable type: '{}'", value.type_name()))),
        _ => Ok(false),
    }
}

/// Describe the effective stock without changing the scan or source IDs.
///
/// Existing payload, supply flags and quantities above three remain exact.
/// An absent row uses ADD_STORE_ITEM's authored identified, zero-charge
/// payload. Source selection remains separate: the shop option also works
/// without scroll randomisation or the extended item pool.
pub fn spell_shield_spec(world: &Value, randomized: bool) -> Result<Value> {
    let store = Value::from("sto:udduer01");
    let mut resource = None;
    for row in crate::py::iter_value(item(world, "resources")?)? {
        if item(&row, "id")?.py_eq(&store) {
            resource = Some(row);
            break;
        }
    }
    let Some(resource) = resource else {
        return Err(Error::profile("More Spell Shield scrolls requires UDDUER01.STO"));
    };
    let mut rows = Vec::new();
    for row in crate::py::iter_value(item(world, "sources")?)? {
        if item(&row, "resource")?.py_eq(&store) {
            rows.push(row);
        }
    }
    let mut keys = Vec::with_capacity(rows.len());
    for row in &rows {
        keys.push(item(item(row, "record")?, "record_index")?.clone());
    }
    // sorted() is stable; compare every key pair the way `<` would.
    let mut order: Vec<usize> = (0..rows.len()).collect();
    let mut failure = None;
    order.sort_by(|&a, &b| match compare(&keys[a], &keys[b]) {
        Ok(ordering) => ordering,
        Err(error) => {
            failure.get_or_insert(error);
            Ordering::Equal
        }
    });
    if let Some(error) = failure {
        return Err(error);
    }
    let rows: Vec<Value> = order.into_iter().map(|index| rows[index].clone()).collect();
    let scroll = Value::from("scrl8x");
    if randomized {
        let mut copies = 0;
        for row in &rows {
            copies += item(row, "item")?.py_eq(&scroll) as usize;
        }
        if copies > 1 {
            return Err(Error::profile("Spell Shield randomisation has ambiguous duplicate shop-source rows"));
        }
    }
    let mut first = None;
    for row in &rows {
        if item(row, "item")?.py_eq(&scroll) {
            first = Some(row);
            break;
        }
    }
    let record = match first {
        Some(row) => item(row, "record")?.clone(),
        None => Value::Dict(Dict::new()),
    };
    let index = match first {
        Some(_) => item(&record, "record_index")?.clone(),
        None => {
            let mut found = None;
            for row in &rows {
                if is_anchor(item(row, "item")?)? {
                    found = Some(item(item(row, "record")?, "record_index")?.clone());
                    break;
                }
            }
            found.unwrap_or(Value::from(rows.len()))
        }
    };
    let sha256 = item(&resource, "sha256")?.clone();
    let original_source_id = match first {
        Some(row) => item(row, "id")?.clone(),
        None => Value::from(""),
    };
    let quantity = match &record {
        Value::Dict(fields) => at_least_three(fields.get("quantity").unwrap_or(&Value::Int(0)))?,
        other => return Err(Error::type_error(format!("'{}' object has no attribute 'get'", other.type_name()))),
    };
    let payload = match first {
        Some(_) => {
            let mut payload = Dict::new();
            for key in STOCK_PAYLOAD {
                payload.insert(key, item(&record, key)?.clone());
            }
            payload
        }
        None => dict! {"resref" => "scrl8x", "expiration" => 0, "charges" => list![0, 0, 0], "flags" => 1},
    };
    let result = Value::Dict(dict! {
        "resource" => "sto:udduer01",
        "resource_sha256" => sha256,
        "original_source_id" => original_source_id,
        "original_record" => record,
        "record_index" => index,
        "quantity" => quantity,
        "payload" => payload,
        "source_id" => if randomized { SPELL_SHIELD_SOURCE } else { "" },
    });
    validate_spell_shield_spec(&result)?;
    Ok(result)
}

fn int_range(value: Option<&Value>, low: i64, high: i64) -> bool {
    crate::profiles::int_in(value, low, high).is_some()
}

/// Check immutable campaign metadata; the exporter also compares the scan.
pub fn validate_spell_shield_spec(spec: &Value) -> Result<()> {
    let fields = [
        "resource",
        "resource_sha256",
        "original_source_id",
        "original_record",
        "record_index",
        "quantity",
        "payload",
        "source_id",
    ];
    let invalid = || Error::profile("Invalid Spell Shield stock metadata");
    let Some(spec) = spec.as_dict().filter(|spec| spec.has_exact_keys(&fields)) else { return Err(invalid()) };
    let get = |key: &str| spec.get(key).expect("checked field");
    if !get("resource").py_eq(&Value::from("sto:udduer01"))
        || !get("resource_sha256").as_str().is_some_and(is_hex256)
        || !(get("source_id").py_eq(&Value::from("")) || get("source_id").py_eq(&Value::from(SPELL_SHIELD_SOURCE)))
        || !int_range(Some(get("record_index")), 0, 0xFFFF_FFFF)
        || !int_range(Some(get("quantity")), 3, 0xFFFF_FFFF)
    {
        return Err(invalid());
    }
    let Some(record) = get("original_record").as_dict() else {
        return Err(Error::profile("Invalid Spell Shield original record"));
    };
    let payload = if !record.is_empty() {
        let byte_offset_valid = match record.get("byte_offset") {
            Some(Value::Int(offset)) => *offset >= 0x9C,
            Some(Value::BigInt(text)) => !text.starts_with('-'),
            _ => false,
        };
        let record_index = record.get("record_index");
        let quantity = record.get("quantity");
        let flag = record.get("infinite_supply_flag");
        let differs = !record.has_exact_keys(&[
            "record_index",
            "byte_offset",
            "resref",
            "expiration",
            "charges",
            "flags",
            "quantity",
            "infinite_supply",
            "infinite_supply_flag",
        ]) || !record.get("resref").unwrap_or(&Value::Null).py_eq(&Value::from("scrl8x"))
            || !record_index.is_some_and(Value::is_int)
            || !record_index.expect("record index").py_eq(get("record_index"))
            || !get("original_source_id")
                .py_eq(&Value::from(format!("sto:udduer01/item/{}", str_value(record_index.expect("record index")))))
            || !int_range(quantity, 0, 0xFFFF_FFFF)
            || !get("quantity").py_eq(&Value::Int(quantity.and_then(Value::as_int).expect("quantity").max(3)))
            || !int_range(flag, 0, 0xFFFF_FFFF)
            || !matches!(record.get("infinite_supply"), Some(Value::Bool(_)))
            || record.get("infinite_supply").and_then(Value::as_bool) != Some(flag.and_then(Value::as_int) != Some(0))
            || !byte_offset_valid;
        if differs {
            return Err(Error::profile("Spell Shield metadata differs from its original stock"));
        }
        STOCK_PAYLOAD.iter().map(|key| (key.to_string(), record.get(key).cloned().expect("checked key"))).collect::<Dict>()
    } else {
        if !get("original_source_id").py_eq(&Value::from("")) || !get("quantity").py_eq(&Value::Int(3)) {
            return Err(Error::profile("Invalid inserted Spell Shield stock"));
        }
        dict! {"resref" => "scrl8x", "expiration" => 0, "charges" => list![0, 0, 0], "flags" => 1}
    };
    let charges_valid = match payload.get("charges") {
        Some(Value::List(charges)) => charges.len() == 3 && charges.iter().all(|n| int_range(Some(n), 0, 0xFFFF)),
        _ => false,
    };
    if !get("payload").py_eq(&Value::Dict(payload.clone()))
        || !int_range(payload.get("expiration"), 0, 0xFFFF)
        || !int_range(payload.get("flags"), 0, 0xFFFF_FFFF)
        || !charges_valid
    {
        return Err(Error::profile("Invalid Spell Shield stock payload"));
    }
    Ok(())
}

/// Python's `str()` of a hashable game value, or its TypeError when the value
/// cannot be a set member.
fn game_name(game: &Value) -> Result<Option<&str>> {
    match game {
        Value::Str(text) => Ok(Some(text.as_str())),
        Value::List(_) | Value::Dict(_) => Err(Error::type_error(format!("unhashable type: '{}'", game.type_name()))),
        _ => Ok(None),
    }
}

/// Return each original die face, including weighted fallback duplicates.
///
/// In EE/EET the original TUTU_VAR is empty. BG1-only games lack some optional
/// BG2 items: their original die faces fall back instead of being rerolled.
/// Item Revisions suppresses PLAT17 and selects alternate LEAT06 descriptions.
/// Sources absent from the install are omitted. Required donors must exist.
pub fn cursed_disguise_choices<S: AsRef<str>>(available: &[S], game: &str, item_revisions: bool) -> Result<Vec<Value>> {
    cursed_disguise_choices_value(available, &Value::from(game), &Value::Bool(item_revisions))
}

/// [`cursed_disguise_choices`] for metadata values read from JSON.
pub fn cursed_disguise_choices_value<S: AsRef<str>>(available: &[S], game: &Value, item_revisions: &Value) -> Result<Vec<Value>> {
    let supported = game_name(game)?.is_some_and(|name| ["bgee", "bg2ee", "eet"].contains(&name));
    if !supported {
        return Err(Error::profile(format!("Unsupported original-disguise game: {}", str_value(game))));
    }
    let Some(item_revisions) = item_revisions.as_bool() else {
        return Err(Error::profile("Item Revisions presence must be a Boolean"));
    };
    let names: BTreeSet<&str> = available.iter().map(AsRef::as_ref).collect();
    let mut result = Vec::new();
    let mut choices = |source: &str, faces: &[(&str, Option<i64>)], optional: &[&str], plate: bool| -> Result<()> {
        if !names.contains(source) {
            return Ok(());
        }
        let fallback = faces[faces.len() - 1];
        let mut variants = Vec::new();
        for &(donor, description) in faces {
            let (donor, description) =
                if !names.contains(donor) && optional.contains(&donor) { fallback } else { (donor, description) };
            if !names.contains(donor) {
                return Err(Error::profile(format!("Original cursed disguise needs {donor}.itm for {source}")));
            }
            variants.push(Value::Dict(dict! {
                "donor" => donor, "description_tra" => description,
                "name_tra" => if plate { Some(212i64) } else { None }, "nerf_plate" => plate,
            }));
        }
        result.push(Value::Dict(dict! {"source" => source, "variants" => variants}));
        Ok(())
    };
    choices("brac08", &[("brac06", Some(200)), ("brac07", Some(201)), ("brac10", Some(203)), ("brac09", Some(202))], &[], false)?;
    choices(
        "brac11",
        &[
            ("brac01", Some(205)),
            ("brac02", Some(205)),
            ("brac03", Some(205)),
            ("brac13", Some(205)),
            ("brac14", Some(205)),
            ("brac15", Some(205)),
            ("brac04", Some(206)),
        ],
        &["brac13", "brac14", "brac15"],
        false,
    )?;
    let (leather, studded) = if item_revisions { (210, 211) } else { (208, 209) };
    choices("leat06", &[("leat05", Some(leather)), ("leat07", Some(leather)), ("leat08", Some(studded))], &[], false)?;
    if !item_revisions {
        choices("plat17", &[("plat02", Some(213)), ("plat11", Some(214)), ("plat10", Some(213))], &[], true)?;
    }
    choices(
        "ring04",
        &[
            ("ring06", Some(216)),
            ("ring07", Some(216)),
            ("ring22", Some(218)),
            ("ring09", Some(217)),
            ("ring31", Some(219)),
            ("ring08", Some(220)),
        ],
        &["ring31"],
        false,
    )?;
    choices(
        "ring23",
        &[
            ("ring06", Some(222)),
            ("ring07", Some(222)),
            ("ring08", Some(225)),
            ("ring09", Some(223)),
            ("ring31", Some(224)),
            ("ring08", Some(225)),
        ],
        &["ring31"],
        false,
    )?;
    choices("sper03", &[("sper05", None), ("sper02", None)], &["sper05"], false)?;
    choices("sw2h03", &[("sw2h11", None), ("sw2h02", None)], &["sw2h11"], false)?;
    Ok(result)
}

fn strref(value: i64, field: &str) -> Result<u32> {
    u32::try_from(value).map_err(|_| Error::profile(format!("Invalid original option {field} string reference")))
}

/// Copy precisely the original appearance fields, plus authored text/plate fix.
///
/// SPER03 and SW2H03 retain their identified descriptions. Other choices need
/// the authored `description_tra` allocated into TLK by the caller. A plate
/// name reference requests the original PLAT17 enchantment and base-AC
/// adjustment.
pub fn patch_cursed_disguise(
    source: &[u8],
    donor: &[u8],
    description_strref: Option<i64>,
    plate_name_strref: Option<i64>,
) -> Result<Vec<u8>> {
    for (label, data) in [("source", source), ("donor", donor)] {
        if data.len() < 0x72 || &data[..8] != b"ITM V1  " {
            return Err(Error::profile(format!("Invalid cursed-disguise {label} ITM")));
        }
    }
    let mut result = source.to_vec();
    for (offset, size) in [(0x3A, 8), (0x50, 4), (0x58, 8)] {
        result[offset..offset + size].copy_from_slice(&donor[offset..offset + size]);
    }
    if let Some(value) = description_strref {
        put_u32(&mut result, 0x54, strref(value, "description")?);
    }
    if let Some(value) = plate_name_strref {
        put_u32(&mut result, 0x0C, strref(value, "plate name")?);
        put_u32(&mut result, 0x60, 0);
        let effect_offset = u32_le(source, 0x6A) as usize;
        let effect_count = u16_le(source, 0x70) as usize;
        if effect_count != 0 && (effect_offset < 0x72 || effect_offset + effect_count * 0x30 > source.len()) {
            return Err(Error::profile("Invalid cursed plate global effect range"));
        }
        for index in 0..effect_count {
            let offset = effect_offset + index * 0x30;
            if u16_le(source, offset) == 0 && u32_le(source, offset + 8) == 16 {
                put_u32(&mut result, offset + 4, 3);
            }
        }
    }
    Ok(result)
}
