//! Item Randomiser's second copies of the rewards it moves (the reference's
//! `duplicates.py`).
//!
//! Some rewards are in the game twice: a shop, a creature or an area container
//! holds a second copy of the item that a table row moves. Item Randomiser's
//! `lists/duplicates/bg1.2da` and `bg2.2da` name these copies by the row's
//! Ident and item; when that row's item moves, its installer removes the copy
//! (`lib/delete.tpa`), leaving the table's replacement where a creature carried
//! it. Chriz Item Randomiser does the same while a game runs: the copies belong
//! to their moved source, and the source gate removes them unless the campaign
//! keeps the source in place (`prototype/FLPRGat.lua`).
//!
//! A copy is found by meaning in the scanned game: every record of the moved
//! item in the holder, with EET's names for BG1 rows, whatever its slot,
//! charges or flags. A holder this game lacks, or one without the item, has no
//! copy to remove. Where a reward's route already hands over the creature's own
//! copy (`mode2_rewards::transfer_specs`), that copy is not named twice, also
//! for a preset's row of that route (`original_catalog::mode2_route_source`).
//!
//! The runtime removes only rows it knows, under the names reviewed for them
//! ([`reviewed`]): as authored and, for a BG1 row, as EET names them in Chriz
//! Item Randomiser's own copy of EET's lists. An EET whose lists rename one
//! otherwise leaves that reward out.

use crate::error::{Error, Result};
use crate::game_names::{convert_resource, EetNames};
use crate::json::{Dict, Value};
use crate::mode2_rewards::{rewards, source_rewards, transfer_spec, Naming};
use crate::sources::WorldIndex;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

/// The scanned records of each holder kind: area containers, creature files, shops.
pub const KINDS: [(&str, &str); 3] = [("are", "container"), ("cre", "cre-template"), ("sto", "store")];
pub const CAMPAIGNS: [&str; 2] = ["bg1", "bg2"];

/// The record kind the scan gives a holder of this file kind.
pub fn kind_of(extension: &str) -> Option<&'static str> {
    KINDS.iter().find(|(known, _)| *known == extension).map(|(_, kind)| *kind)
}

/// One table row, as authored but in lower case.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub item: String,
    pub source: String,
    pub replacement: String,
    pub ident: String,
}

impl Row {
    /// The holder's name and file kind ("bernard", "sto").
    fn holder(&self) -> (&str, &str) {
        self.source.rsplit_once('.').expect("checked when read")
    }
}

/// The rows of one campaign's table by line, as authored but in lower case.
pub fn table(campaign: &str) -> Result<BTreeMap<usize, Row>> {
    let mut rows = BTreeMap::new();
    if !CAMPAIGNS.contains(&campaign) {
        return Ok(rows);
    }
    let relative = format!("lists/duplicates/{campaign}.2da");
    let text = crate::data::text(&relative).ok_or_else(|| {
        Error::os(&std::io::Error::from(std::io::ErrorKind::NotFound), std::path::Path::new(&relative))
    })?;
    let mut header = false;
    for (number, line) in crate::py::splitlines(text).into_iter().enumerate() {
        let line = crate::py::lower(line);
        let fields = crate::py::split_whitespace(&line);
        if fields.is_empty() {
            continue;
        }
        if !header {
            header = true; // Item Source Replacement Ident
            continue;
        }
        let kind = fields.get(1).and_then(|source| source.rsplit_once('.')).and_then(|(_, kind)| kind_of(kind));
        if fields.len() != 4 || kind.is_none() {
            return Err(Error::value(format!("Invalid duplicate row: {relative}:{}", number + 1)));
        }
        rows.insert(
            number + 1,
            Row {
                item: fields[0].to_string(),
                source: fields[1].to_string(),
                replacement: fields[2].to_string(),
                ident: fields[3].to_string(),
            },
        );
    }
    Ok(rows)
}

/// The campaign and authored row a copy identity ("bg2:duplicate:5") names.
pub fn row(identity: Option<&Value>) -> Result<Option<(&'static str, Row)>> {
    let Some(identity) = identity.and_then(Value::as_str) else {
        return Ok(None);
    };
    let Some((campaign, line)) = identity.split_once(":duplicate:") else {
        return Ok(None);
    };
    let Some(campaign) = CAMPAIGNS.iter().copied().find(|known| *known == campaign) else {
        return Ok(None);
    };
    // [1-9][0-9]*, as the reference's pattern reads it.
    if line.is_empty() || line.starts_with('0') || !line.bytes().all(|byte| byte.is_ascii_digit()) {
        return Ok(None);
    }
    let Ok(line) = line.parse::<usize>() else {
        return Ok(None);
    };
    Ok(table(campaign)?.remove(&line).map(|found| (campaign, found)))
}

/// The reviewed names of a row's holder ("sto:bernard"), item and replacement.
pub struct Accepted {
    pub resource: BTreeSet<String>,
    pub item: BTreeSet<String>,
    pub replacement: Option<BTreeSet<String>>,
}

pub fn accepted(campaign: &str, entry: &Row) -> Result<Accepted> {
    let names = |resref: &str, extension: &str| -> Result<BTreeSet<String>> {
        Ok(BTreeSet::from([resref.to_string(), convert_resource(resref, extension, campaign, "eet", None)?]))
    };
    let (name, extension) = entry.holder();
    let resource = names(name, extension)?.into_iter().map(|resref| format!("{extension}:{resref}")).collect();
    let replacement =
        if entry.replacement != "blank" { Some(names(&entry.replacement, "itm")?) } else { None };
    Ok(Accepted { resource, item: names(&entry.item, "itm")?, replacement })
}

/// Whether a copy (of a plan, or a campaign's with fingerprints) is a shipped
/// row under names reviewed for it (FLPRGat.lua).
pub fn reviewed(copy: &Value) -> Result<bool> {
    let Some((campaign, entry)) = row(copy.get("id"))? else {
        return Ok(false);
    };
    let names = accepted(campaign, &entry)?;
    let mut replacement = copy.get("replacement");
    if let Some(Value::Dict(fields)) = replacement {
        replacement = fields.get("resref");
    }
    let listed = |value: Option<&Value>, names: &BTreeSet<String>| value.and_then(Value::as_str).is_some_and(|name| names.contains(name));
    let replacement_known = match (&names.replacement, replacement) {
        (None, None | Some(Value::Null)) => true,
        (None, Some(_)) => false,
        (Some(names), value) => listed(value, names),
    };
    Ok(listed(copy.get("resource"), &names.resource) && listed(copy.get("item"), &names.item) && replacement_known)
}

/// Each holder's scanned records of each item, in record order.
pub type Holdings<'a> = HashMap<(String, String), Vec<&'a Value>>;

pub fn holdings<'a>(sources: impl IntoIterator<Item = &'a Value>) -> Holdings<'a> {
    let mut result: Holdings<'a> = HashMap::new();
    for source in sources {
        let text = |key: &str| source.get(key).and_then(Value::as_str).unwrap_or("");
        let resource = text("resource");
        let extension = resource.split_once(':').map_or(resource, |(kind, _)| kind);
        if kind_of(extension).is_some_and(|kind| kind == text("kind")) {
            result.entry((resource.to_string(), text("item").to_string())).or_default().push(source);
        }
    }
    let index = |source: &Value| source.get("record").and_then(|record| record.get("record_index")).and_then(Value::as_int);
    for records in result.values_mut() {
        records.sort_by_key(|source| index(source));
    }
    result
}

/// (creature, item) of the copies that the reward route `route` already hands
/// over, as the game names them.
fn route_copies(route: Option<&str>, game: &str, names: Option<&EetNames>) -> Result<HashSet<(String, String)>> {
    let mut result = HashSet::new();
    let Some(route) = route else {
        return Ok(result);
    };
    let key = source_rewards().get(route).and_then(Value::as_str).unwrap_or("");
    let Some(spec) = rewards().get(key) else {
        return Ok(result);
    };
    let transfers = spec.get("required_transfers").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]);
    for name in transfers {
        let name = name.as_str().unwrap_or("");
        let transfer = transfer_spec(name, Naming { game: Some(game), names })?;
        let ids = transfer.get("authored_source_ids").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]);
        if ids.iter().any(|id| id.as_str() == Some(route)) {
            let text = |key: &str| transfer.get(key).and_then(Value::as_str).unwrap_or("").to_string();
            result.insert((text("source_resource"), text("item")));
        }
    }
    Ok(result)
}

/// The copies of `item` that move with source `identity` of table row `ident`.
///
/// A row names a copy of the same item; its holder must be in the game and
/// hold the item, or be a file the scan could not read (the export then leaves
/// the reward out, since its copy cannot be found). `route` is the extended
/// reward route the source is, as the export finds it for a source
/// registration (`original_catalog::mode2_route_source` of its authored row:
/// its own row, or a preset's row of it); the copies that route hands over are
/// not named again.
#[allow(clippy::too_many_arguments)]
pub fn expected_copies(
    identity: &str,
    campaign: &str,
    ident: &str,
    item: &str,
    index: &WorldIndex,
    held: &Holdings<'_>,
    game: &str,
    names: Option<&EetNames>,
    route: Option<&str>,
) -> Result<Vec<Dict>> {
    let mut result = Vec::new();
    let covered = route_copies(route, game, names)?;
    for (line, entry) in table(campaign)? {
        if entry.ident != ident || convert_resource(&entry.item, "itm", campaign, game, names)? != item {
            continue;
        }
        let (name, extension) = entry.holder();
        let holder = format!("{extension}:{}", convert_resource(name, extension, campaign, game, names)?);
        let key = (holder.clone(), item.to_string());
        if covered.contains(&key) || (!index.is_unreadable(&holder) && held.get(&key).is_none_or(Vec::is_empty)) {
            continue;
        }
        let mut copy = crate::dict! {
            "id" => format!("{campaign}:duplicate:{line}"), "source_id" => identity, "resource" => holder.as_str(),
            "item" => item,
        };
        if entry.replacement != "blank" {
            copy.insert("replacement", convert_resource(&entry.replacement, "itm", campaign, game, names)?);
        }
        result.push(copy);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn value(text: &str) -> Value {
        crate::json::parse_lenient(text).expect("test JSON")
    }

    #[test]
    fn shipped_tables_are_item_randomisers() {
        // The reference's test_shipped_tables_are_item_randomisers.
        let (bg1, bg2) = (table("bg1").unwrap(), table("bg2").unwrap());
        assert_eq!((bg1.len(), bg2.len()), (10, 27));
        let row = |item: &str, source: &str, replacement: &str, ident: &str| Row {
            item: item.into(),
            source: source.into(),
            replacement: replacement.into(),
            ident: ident.into(),
        };
        assert_eq!(bg2[&3], row("xbow06", "bernard.sto", "blank", "i3c"));
        assert_eq!(bg2[&25].replacement, "sper02");
        assert_eq!(bg1[&4].source, "ar1006.are");
        assert!(table("soa").unwrap().is_empty());
    }

    #[test]
    fn copy_identities_name_a_line_of_a_table() {
        let found = row(Some(&Value::from("bg2:duplicate:25"))).unwrap().expect("a row");
        assert_eq!((found.0, found.1.item.as_str()), ("bg2", "sper08"));
        // The header, a line past the table, a leading zero, another campaign.
        for identity in ["bg2:duplicate:1", "bg2:duplicate:99", "bg2:duplicate:025", "soa:duplicate:3", "bg2:dup:3"] {
            assert!(row(Some(&Value::from(identity))).unwrap().is_none(), "{identity}");
        }
        assert!(row(Some(&Value::Int(3))).unwrap().is_none());
        assert!(row(None).unwrap().is_none());
    }

    #[test]
    fn runtime_knows_each_row_under_its_authored_and_eet_names() {
        // The reference's test of the same name.
        let zero = "0".repeat(64);
        let cases = [
            (r#"{"id": "bg1:duplicate:4", "resource": "are:bg1006", "item": "sw1h03"}"#.to_string(), true),
            (r#"{"id": "bg1:duplicate:4", "resource": "are:ar1006", "item": "sw1h03"}"#.to_string(), true),
            (
                format!(
                    r#"{{"id": "bg2:duplicate:25", "resource": "cre:sahpr2", "item": "sper08",
                        "replacement": {{"resref": "sper02", "resource_sha256": "{zero}"}}}}"#
                ),
                true,
            ),
            // EET 14's lists name Varci's shield SHLD07A; CIR's copy of them does not.
            (r#"{"id": "bg1:duplicate:5", "resource": "cre:varci", "item": "shld07a"}"#.to_string(), false),
            (r#"{"id": "bg2:duplicate:25", "resource": "cre:sahpr2", "item": "sper08"}"#.to_string(), false),
            (
                r#"{"id": "bg2:duplicate:3", "resource": "sto:bernard", "item": "xbow06", "replacement": "sper02"}"#
                    .to_string(),
                false,
            ),
            (r#"{"id": "bg2:duplicate:3", "resource": "sto:bernard2", "item": "xbow06"}"#.to_string(), false),
            (r#"{"id": "bg2:duplicate:1", "resource": "sto:bernard", "item": "xbow06"}"#.to_string(), false),
        ];
        for (copy, known) in cases {
            assert_eq!(reviewed(&value(&copy)).unwrap(), known, "{copy}");
        }
    }

    #[test]
    fn a_carried_routes_copy_is_its_routes_also_for_a_presets_row() {
        // The reference's test of the same name: Nemphre's route hands over the
        // scroll she carries, for her own row and for Boss Loot's row of it.
        let route = "original-source:bg1:mode2:item:159";
        let record = value(r#"{"id": "cre:nemphr/item/0"}"#);
        let held: Holdings = HashMap::from([(("cre:nemphr".to_string(), "scrl1q".to_string()), vec![&record])]);
        let index = WorldIndex::default();
        let copies = |identity: &str, route: Option<&str>| {
            expected_copies(identity, "bg1", "vs1", "scrl1q", &index, &held, "bgee", None, route).unwrap()
        };
        assert!(copies(route, Some(route)).is_empty());
        assert!(copies("original-source:bg1:boss-loot:mode2:item:339", Some(route)).is_empty());
        // A source that is no route moves the creature's copy as the table's.
        let expected = value(
            r#"{"id": "bg1:duplicate:12", "source_id": "cre:nemphr/item/0", "resource": "cre:nemphr", "item": "scrl1q"}"#,
        );
        let found: Vec<Value> = copies("cre:nemphr/item/0", None).into_iter().map(Value::Dict).collect();
        assert!(Value::List(found).py_eq(&Value::List(vec![expected])));
    }

    #[test]
    fn holdings_keep_each_holders_records_in_record_order() {
        let sources = [
            r#"{"resource": "sto:bernard", "kind": "store", "item": "xbow06", "record": {"record_index": 2}}"#,
            r#"{"resource": "sto:bernard", "kind": "store", "item": "xbow06", "record": {"record_index": 0}}"#,
            r#"{"resource": "are:rr#010", "kind": "container", "item": "ring26", "record": {"record_index": 1}}"#,
            // An area's ground item and a creature's own copy in another file
            // kind are not a holder's records.
            r#"{"resource": "are:rr#010", "kind": "ground", "item": "ring26", "record": {"record_index": 0}}"#,
            r#"{"resource": "sto:bernard", "kind": "cre-template", "item": "xbow06", "record": {"record_index": 1}}"#,
        ]
        .map(value);
        let held = holdings(&sources);
        let indices = |resource: &str, item: &str| -> Vec<i64> {
            held[&(resource.to_string(), item.to_string())]
                .iter()
                .map(|source| source.at("record").unwrap().at("record_index").unwrap().as_int().unwrap())
                .collect()
        };
        assert_eq!(indices("sto:bernard", "xbow06"), [0, 2]);
        assert_eq!(indices("are:rr#010", "ring26"), [1]);
        assert_eq!(held.len(), 2);
    }
}
