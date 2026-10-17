//! Physical resource discovery and explicit, non-playable source binding
//! drafts (the reference's `sources.py`).
//!
//! An installed inventory record is not proof of a unique runtime instance.
//! CRE templates, conditional area actors, dynamic containers and scripted
//! grants stay distinguishable. This module never removes or delivers items.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::paths;
use crate::profiles::{digest, int_in, is_hex256, validate_catalog, validate_profile, ItemIndex};
use crate::resources::{resource_type, sha256_hex, GameResources};
use crate::world_formats::{parse_are, parse_cre, parse_sto, reference};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};

pub const WORLD_SCHEMA: &str = "flir-world-catalog-v1";
pub const DIAGNOSTIC_WORLD_SCHEMA: &str = "flir-world-catalog-v2";
pub const DANGLING_INVENTORY: &str = "dangling-inventory-reference";
pub const INVALID_INVENTORY: &str = "invalid-inventory-reference";
pub const INVALID_ITEM: &str = "invalid-item-reference";
pub const UNRESOLVED_STORAGE: &str = "unresolved-creature-storage";
pub const PLAN_SCHEMA: &str = "flir-binding-plan-v1";
pub const COVERAGE: &str = "physical-records-and-bcs-dlg-hashes";
pub const KINDS: [&str; 7] = ["itm", "cre", "are", "sto", "bcs", "dlg", "spl"];

/// Concerns that make a physical record unusable as a relocation source.
pub const UNUSABLE_SOURCE: [&str; 12] = [
    "unreferenced-inventory-record",
    "aliased-inventory-record",
    "unowned-item-record",
    "shared-container-record",
    "infinite-stock",
    "empty-stock",
    "unknown-stock-supply",
    "missing-item-definition",
    "empty-item-reference",
    DANGLING_INVENTORY,
    INVALID_INVENTORY,
    INVALID_ITEM,
];

pub fn world_fingerprint(world: &Value) -> Result<String> {
    let mut content = Dict::new();
    for key in ["schema", "catalog_fingerprint", "coverage", "resources", "sources", "targets"] {
        content.insert(key, world.at(key)?.clone());
    }
    digest(&Value::Dict(content))
}

fn sorted_unique(values: Vec<String>) -> Vec<String> {
    let mut values = values;
    values.sort();
    values.dedup();
    values
}

fn strings(values: Vec<String>) -> Value {
    Value::List(values.into_iter().map(Value::Str).collect())
}

fn source(resource: &str, owner: &str, kind: &str, row: &Dict, concerns: Vec<String>) -> Value {
    let index = row.get("record_index").and_then(Value::as_int).unwrap_or(0);
    let mut result = Dict::with_capacity(7);
    result.push_unique("id".into(), Value::Str(format!("{owner}/item/{index}")));
    result.push_unique("item".into(), row.get("resref").cloned().unwrap_or(Value::Null));
    result.push_unique("resource".into(), Value::from(resource));
    result.push_unique("owner".into(), Value::from(owner));
    result.push_unique("kind".into(), Value::from(kind));
    result.push_unique("record".into(), Value::Dict(row.clone()));
    result.push_unique("concerns".into(), strings(sorted_unique(concerns)));
    Value::Dict(result)
}

fn cre_sources(resource: &str, owner: &str, cre: &Dict, kind: &str) -> Result<Vec<Value>> {
    let mut result = Vec::new();
    let diagnostics = cre.get("inventory_diagnostics");
    for item in cre.get("items").and_then(Value::as_list).into_iter().flatten() {
        let mut item = item.as_dict().expect("parsed item").clone();
        let mut concerns =
            vec![if kind == "cre-template" { "template-instance-scope" } else { "actor-availability" }.to_string()];
        if let Some(diagnostics) = diagnostics {
            item.insert("inventory_diagnostics", diagnostics.clone());
            concerns.extend(inventory_concerns(diagnostics)?);
        }
        let slots = item.get("slots").and_then(Value::as_list).map_or(0, Vec::len);
        if slots == 0 {
            concerns.push("unreferenced-inventory-record".into());
        }
        if slots > 1 {
            concerns.push("aliased-inventory-record".into());
        }
        if item.get("flags").and_then(Value::as_int).unwrap_or(0) & 8 != 0 {
            concerns.push("undroppable-instance".into());
        }
        result.push(source(resource, owner, kind, &item, concerns));
    }
    Ok(result)
}

fn concerns_mut(row: &mut Value) -> &mut Vec<Value> {
    row.get_mut("concerns").and_then(Value::as_list_mut).expect("concerns list")
}

fn resort_concerns(row: &mut Value) {
    let list = concerns_mut(row);
    let mut names: Vec<String> = list.iter().filter_map(Value::as_str).map(str::to_string).collect();
    names.sort();
    names.dedup();
    *list = names.into_iter().map(Value::Str).collect();
}

/// Scan the explicitly named installation; require matching effective ITMs.
///
/// BCS/DLG bytes are fingerprinted, not decompiled or interpreted. Other
/// grant mechanisms (Lua, effects, dynamic tables) are outside this coverage.
/// Names keep the selected item catalogue's localization snapshot.
pub fn scan_world(catalog: &Value) -> Result<Value> {
    let item_index = validate_catalog(catalog)?;
    let game_root = catalog.at("game_root")?.as_str().expect("validated").to_string();
    let mut schema = WORLD_SCHEMA;
    let mut resources: Vec<Value> = Vec::new();
    let mut sources: Vec<Value> = Vec::new();
    let mut targets: Vec<Value> = Vec::new();
    let types: Vec<(&str, u32)> = KINDS.iter().map(|kind| (*kind, resource_type(kind))).collect();
    let root = PathBuf::from(paths::display(Path::new(&game_root)));
    let fingerprint = GameResources::with(&root, &types, |reader| {
        if reader.names("itm")? != item_index.resrefs() {
            return Err(Error::scan("Installed items changed; scan the item catalogue again before binding sources."));
        }
        for kind in KINDS {
            for resref in reader.names(kind)? {
                let data = reader.read(kind, &resref)?;
                let hash = sha256_hex(&data);
                let resource = format!("{kind}:{resref}");
                if kind == "itm" {
                    let expected = item_index.row(catalog, &resref).and_then(|row| row.get("sha256")).and_then(Value::as_str);
                    if expected != Some(hash.as_str()) {
                        return Err(Error::scan(
                            "Installed item bytes changed; scan the item catalogue again before binding sources.",
                        ));
                    }
                }
                resources.push(Value::Dict(crate::dict! {
                    "id" => resource.clone(), "kind" => kind, "resref" => resref.clone(),
                    "sha256" => hash, "byte_length" => data.len(),
                }));
                let wrap = |error: Error| Error::scan(format!("{resource}: {}", error.message));
                match kind {
                    "cre" => {
                        let cre = parse_cre(&data, true, true).map_err(wrap)?;
                        sources.extend(cre_sources(&resource, &resource, &cre, "cre-template")?);
                        let mut selector = crate::dict! {"script_name" => cre.get("script_name").cloned().unwrap_or(Value::Null)};
                        if let Some(diagnostics) = cre.get("inventory_diagnostics") {
                            selector.insert("inventory_diagnostics", diagnostics.clone());
                        }
                        targets.push(Value::Dict(crate::dict! {
                            "id" => resource.clone(), "resource" => resource.clone(), "kind" => "cre-template",
                            "selector" => selector,
                            "concerns" => crate::list!["template-instance-scope", "lootability-unverified"],
                        }));
                    }
                    "are" => {
                        let area = parse_are(&data, true, true, true).map_err(wrap)?;
                        let containers = area.get("containers").and_then(Value::as_list).cloned().unwrap_or_default();
                        let dynamic_by_index: HashMap<i64, bool> = containers
                            .iter()
                            .map(|row| {
                                (
                                    row.get("record_index").and_then(Value::as_int).unwrap_or(0),
                                    row.get("dynamic_item_table").is_some_and(Value::truthy),
                                )
                            })
                            .collect();
                        for container in &containers {
                            let fields = container.as_dict().expect("container");
                            let index = fields.get("record_index").and_then(Value::as_int).unwrap_or(0);
                            let mut concerns = crate::list!["container-access-unverified"];
                            if fields.get("dynamic_item_table").is_some_and(Value::truthy) {
                                concerns.as_list_mut().unwrap().push(Value::from("dynamic-container-items"));
                            }
                            let mut selector = Dict::new();
                            for (key, value) in fields.iter() {
                                if key != "items" {
                                    selector.push_unique(key.to_string(), value.clone());
                                }
                            }
                            targets.push(Value::Dict(crate::dict! {
                                "id" => format!("{resource}/container/{index}"), "resource" => resource.clone(),
                                "kind" => "container", "selector" => selector, "concerns" => concerns,
                            }));
                        }
                        for item in area.get("items").and_then(Value::as_list).into_iter().flatten() {
                            let fields = item.as_dict().expect("item");
                            let mut owners: Vec<i64> = fields
                                .get("container_indexes")
                                .and_then(Value::as_list)
                                .map(|list| list.iter().filter_map(Value::as_int).collect())
                                .unwrap_or_default();
                            if owners.is_empty() {
                                if let Some(single) = fields.get("container_index").and_then(Value::as_int) {
                                    owners = vec![single];
                                }
                            }
                            let mut concerns = Vec::new();
                            let owner = if owners.len() != 1 {
                                concerns.push(
                                    if owners.is_empty() { "unowned-item-record" } else { "shared-container-record" }
                                        .to_string(),
                                );
                                format!("{resource}/unresolved")
                            } else {
                                if dynamic_by_index.get(&owners[0]).copied().unwrap_or(false) {
                                    concerns.push("dynamic-container-items".into());
                                }
                                format!("{resource}/container/{}", owners[0])
                            };
                            if fields.get("flags").and_then(Value::as_int).unwrap_or(0) & 8 != 0 {
                                concerns.push("undroppable-instance".into());
                            }
                            sources.push(source(&resource, &owner, "container", fields, concerns));
                        }
                        for actor in area.get("actors").and_then(Value::as_list).into_iter().flatten() {
                            let fields = actor.as_dict().expect("actor");
                            let index = fields.get("record_index").and_then(Value::as_int).unwrap_or(0);
                            let target_id = format!("{resource}/actor/{index}");
                            let mut selector = Dict::new();
                            for (key, value) in fields.iter() {
                                if key != "embedded_cre" {
                                    selector.push_unique(key.to_string(), value.clone());
                                }
                            }
                            let mut concerns = vec!["actor-availability".to_string(), "lootability-unverified".to_string()];
                            let embedded = fields.get("embedded_cre").and_then(Value::as_dict);
                            if fields.contains_key("storage_diagnostics") {
                                concerns.push(UNRESOLVED_STORAGE.into());
                                schema = DIAGNOSTIC_WORLD_SCHEMA;
                            } else if let Some(cre) = embedded {
                                sources.extend(cre_sources(&resource, &target_id, cre, "embedded-cre")?);
                                selector.insert("script_name", cre.get("script_name").cloned().unwrap_or(Value::Null));
                                if let Some(diagnostics) = cre.get("inventory_diagnostics") {
                                    selector.insert("inventory_diagnostics", diagnostics.clone());
                                }
                            } else {
                                let cre_resref = fields.get("cre_resref").and_then(Value::as_str).unwrap_or("");
                                let template =
                                    if cre_resref.is_empty() { Value::Null } else { Value::Str(format!("cre:{cre_resref}")) };
                                selector.insert("template_resource", template);
                                concerns.push("external-cre-template".into());
                            }
                            targets.push(Value::Dict(crate::dict! {
                                "id" => target_id, "resource" => resource.clone(), "kind" => "actor",
                                "selector" => selector, "concerns" => strings(concerns),
                            }));
                        }
                    }
                    "sto" => {
                        let store = parse_sto(&data).map_err(wrap)?;
                        let mut selector = Dict::new();
                        for (key, value) in store.iter() {
                            if key != "items" {
                                selector.push_unique(key.to_string(), value.clone());
                            }
                        }
                        targets.push(Value::Dict(crate::dict! {
                            "id" => resource.clone(), "resource" => resource.clone(), "kind" => "store",
                            "selector" => selector, "concerns" => crate::list!["store-access-unverified"],
                        }));
                        for item in store.get("items").and_then(Value::as_list).into_iter().flatten() {
                            let fields = item.as_dict().expect("item");
                            let mut concerns = Vec::new();
                            let flag = fields.get("infinite_supply_flag").and_then(Value::as_int).unwrap_or(0);
                            if fields.get("infinite_supply").is_some_and(Value::truthy) {
                                concerns.push("infinite-stock".to_string());
                            } else if fields.get("quantity").and_then(Value::as_int) == Some(0) {
                                concerns.push("empty-stock".to_string());
                            }
                            if flag != 0 && flag != 1 {
                                concerns.push("unknown-stock-supply".to_string());
                            }
                            sources.push(source(&resource, &resource, "store", fields, concerns));
                        }
                    }
                    _ => {}
                }
            }
        }
        let resource_ids: HashSet<String> =
            resources.iter().filter_map(|row| row.get("id").and_then(Value::as_str)).map(str::to_string).collect();
        for row in sources.iter_mut() {
            let item = row.get("item").and_then(Value::as_str).unwrap_or("").to_string();
            let added = if row.get("record").is_some_and(|record| record.get("raw_resref_hex").is_some()) {
                Some(INVALID_ITEM)
            } else if item.is_empty() {
                Some("empty-item-reference")
            } else if !item_index.contains(&item) {
                Some("missing-item-definition")
            } else {
                None
            };
            if let Some(concern) = added {
                concerns_mut(row).push(Value::from(concern));
            }
            resort_concerns(row);
        }
        let positions: HashMap<String, usize> = targets
            .iter()
            .enumerate()
            .map(|(position, row)| (row.get("id").and_then(Value::as_str).unwrap_or("").to_string(), position))
            .collect();
        for position in 0..targets.len() {
            let reference = targets[position].get("selector").and_then(|selector| selector.get("template_resource")).cloned();
            let inherited = reference
                .as_ref()
                .and_then(Value::as_str)
                .and_then(|id| positions.get(id))
                .and_then(|template| targets[*template].get("selector"))
                .and_then(|selector| selector.get("inventory_diagnostics"))
                .cloned();
            let row = &mut targets[position];
            if let Some(diagnostics) = inherited {
                row.get_mut("selector").and_then(Value::as_dict_mut).unwrap().insert("template_inventory_diagnostics", diagnostics);
            }
            let selector = row.get("selector").and_then(Value::as_dict).unwrap();
            let diag = selector.get("inventory_diagnostics").or_else(|| selector.get("template_inventory_diagnostics")).cloned();
            if let Some(diag) = diag {
                let extra = inventory_concerns(&diag)?;
                for concern in extra {
                    concerns_mut(row).push(Value::Str(concern));
                }
                schema = DIAGNOSTIC_WORLD_SCHEMA;
            }
            let missing_template = row.get("kind").and_then(Value::as_str) == Some("actor")
                && concerns_mut(row).iter().any(|concern| concern.as_str() == Some("external-cre-template"))
                && !reference.as_ref().and_then(Value::as_str).is_some_and(|id| resource_ids.contains(id));
            if missing_template {
                concerns_mut(row).push(Value::from("missing-creature-template"));
            }
            resort_concerns(row);
        }
        let by_id = |a: &Value, b: &Value| {
            let left = a.get("id").and_then(Value::as_str).unwrap_or("");
            let right = b.get("id").and_then(Value::as_str).unwrap_or("");
            left.as_bytes().cmp(right.as_bytes())
        };
        resources.sort_by(by_id);
        sources.sort_by(by_id);
        targets.sort_by(by_id);
        let partial = world_value(&game_root, catalog, schema, &resources, &sources, &targets, None);
        world_fingerprint(&partial)
    })?;
    let world = world_value(&game_root, catalog, schema, &resources, &sources, &targets, Some(fingerprint));
    validate_world(&world, catalog)?;
    Ok(world)
}

fn world_value(
    game_root: &str,
    catalog: &Value,
    schema: &str,
    resources: &[Value],
    sources: &[Value],
    targets: &[Value],
    fingerprint: Option<String>,
) -> Value {
    let mut world = Dict::with_capacity(8);
    world.push_unique("schema".into(), Value::from(schema));
    world.push_unique("game_root".into(), Value::from(game_root));
    world.push_unique("catalog_fingerprint".into(), catalog.get("fingerprint").cloned().unwrap_or(Value::Null));
    world.push_unique("coverage".into(), Value::from(COVERAGE));
    world.push_unique("resources".into(), Value::List(resources.to_vec()));
    world.push_unique("sources".into(), Value::List(sources.to_vec()));
    world.push_unique("targets".into(), Value::List(targets.to_vec()));
    if let Some(fingerprint) = fingerprint {
        world.push_unique("fingerprint".into(), Value::Str(fingerprint));
    }
    Value::Dict(world)
}

// ---------------------------------------------------------------- checks

pub(crate) fn text<'a>(value: Option<&'a Value>, label: &str) -> Result<&'a str> {
    match value {
        Some(Value::Str(text)) if !text.is_empty() && text.chars().all(|c| c as u32 >= 32) => Ok(text),
        _ => Err(Error::profile(format!("Invalid {label}"))),
    }
}

pub(crate) fn integer(value: Option<&Value>, label: &str, low: i64, high: i64) -> Result<i64> {
    int_in(value, low, high).ok_or_else(|| Error::profile(format!("Invalid {label}")))
}

pub(crate) fn fields<'a>(value: &'a Value, expected: &[&str], label: &str) -> Result<&'a Dict> {
    match value.as_dict() {
        Some(fields) if fields.has_exact_keys(expected) => Ok(fields),
        _ => Err(Error::profile(format!("Unsupported {label} fields"))),
    }
}

pub(crate) fn rows<'a>(value: Option<&'a Value>, label: &str) -> Result<&'a [Value]> {
    match value {
        Some(Value::List(list)) if list.iter().all(Value::is_dict) => Ok(list),
        _ => Err(Error::profile(format!("Invalid {label} list"))),
    }
}

/// Rows by identity in list order (`_indexed`).
#[derive(Clone, Debug, Default)]
pub struct RowIndex {
    positions: HashMap<String, usize>,
    pub order: Vec<String>,
}

impl RowIndex {
    pub fn contains(&self, id: &str) -> bool {
        self.positions.contains_key(id)
    }
    pub fn position(&self, id: &str) -> Option<usize> {
        self.positions.get(id).copied()
    }
    pub fn len(&self) -> usize {
        self.order.len()
    }
    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }
}

pub(crate) fn indexed(rows: &[Value], label: &str) -> Result<RowIndex> {
    let mut index = RowIndex::default();
    for (position, row) in rows.iter().enumerate() {
        let identity = text(row.get("id"), &format!("{label} identity"))?;
        if index.positions.contains_key(identity) {
            return Err(Error::profile(format!("Duplicate {label} identity: {identity}")));
        }
        index.positions.insert(identity.to_string(), position);
        index.order.push(identity.to_string());
    }
    if index.order.windows(2).any(|pair| pair[0] > pair[1]) {
        return Err(Error::profile(format!("{label} records must be sorted")));
    }
    Ok(index)
}

pub(crate) fn resref_name(value: Option<&Value>) -> Result<&str> {
    let text = text(value, "resource name")?;
    let count = text.chars().count();
    if !(1..=8).contains(&count)
        || text != crate::py::lower(text)
        || text != text.trim_matches(' ')
        || text == "."
        || text == ".."
        || text.chars().any(|c| c as u32 > 126 || "/\\:*?\"<>|".contains(c))
    {
        return Err(Error::profile("Invalid resource name"));
    }
    Ok(text)
}

pub(crate) fn indexes<'a>(value: Option<&'a Value>, label: &str, high: i64) -> Result<&'a [Value]> {
    let Some(Value::List(list)) = value else {
        return Err(Error::profile(format!("Invalid {label} indexes")));
    };
    let mut numbers = Vec::with_capacity(list.len());
    for number in list {
        numbers.push(integer(Some(number), label, 0, high)?);
    }
    if numbers.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(Error::profile(format!("Ambiguous {label} indexes")));
    }
    Ok(list)
}

pub fn inventory_concerns(diag: &Value) -> Result<Vec<String>> {
    let Some(fields) = diag.as_dict() else { return Err(Error::profile("Invalid inventory diagnostics")) };
    let count = integer(fields.get("item_count"), "inventory diagnostic item count", 0, 0xFFFF_FFFF)?;
    let words = match fields.get("slot_words") {
        Some(Value::List(words)) if words.len() == 40 => words,
        _ => return Err(Error::profile("Inventory diagnostics require all 40 raw slot words")),
    };
    let mut values = Vec::with_capacity(40);
    for word in words {
        values.push(integer(Some(word), "inventory diagnostic slot word", 0, 0xFFFF)?);
    }
    if fields.contains_key("invalid_item_indexes") {
        indexes(fields.get("invalid_item_indexes"), "invalid inventory item", 0xFFFF_FFFF)?;
    }
    let mut concerns = Vec::new();
    if values[..38].iter().any(|&word| word != 0xFFFF && word >= count) {
        concerns.push(DANGLING_INVENTORY.to_string());
    }
    if fields.get("invalid_item_indexes").is_some_and(Value::truthy) {
        concerns.push(INVALID_INVENTORY.to_string());
    }
    Ok(concerns)
}

fn concern_list(value: Option<&Value>) -> Vec<&str> {
    value.and_then(Value::as_list).map(|list| list.iter().filter_map(Value::as_str).collect()).unwrap_or_default()
}

fn source_concerns(row: &Dict, world: &Value, index: &WorldIndex, item_ids: &ItemIndex) -> Result<Vec<String>> {
    let record = row.get("record").and_then(Value::as_dict).expect("validated record");
    let kind = row.get("kind").and_then(Value::as_str).unwrap_or("");
    let mut concerns: Vec<String> = Vec::new();
    match kind {
        "cre-template" | "embedded-cre" => {
            if let Some(diagnostics) = record.get("inventory_diagnostics") {
                concerns.extend(inventory_concerns(diagnostics)?);
            }
            concerns.push(if kind == "cre-template" { "template-instance-scope" } else { "actor-availability" }.into());
            let slots = record.get("slots").and_then(Value::as_list).map_or(0, Vec::len);
            if slots == 0 {
                concerns.push("unreferenced-inventory-record".into());
            } else if slots > 1 {
                concerns.push("aliased-inventory-record".into());
            }
        }
        "container" => {
            let owners = record.get("container_indexes").and_then(Value::as_list).map_or(0, Vec::len);
            if owners != 1 {
                concerns.push(if owners == 0 { "unowned-item-record" } else { "shared-container-record" }.into());
            } else {
                let owner = row.get("owner").and_then(Value::as_str).unwrap_or("");
                let target = index.target(world, owner).ok_or_else(|| Error::key(owner))?;
                let dynamic = target.get("selector").and_then(|selector| selector.get("dynamic_item_table"));
                if dynamic.ok_or_else(|| Error::key("dynamic_item_table"))?.truthy() {
                    concerns.push("dynamic-container-items".into());
                }
            }
        }
        _ => {
            let flag = record.get("infinite_supply_flag").ok_or_else(|| Error::key("infinite_supply_flag"))?;
            let quantity = record.get("quantity").ok_or_else(|| Error::key("quantity"))?;
            if flag.truthy() {
                concerns.push("infinite-stock".into());
            } else if !quantity.truthy() {
                concerns.push("empty-stock".into());
            }
            if !(flag.py_eq(&Value::Int(0)) || flag.py_eq(&Value::Int(1))) {
                concerns.push("unknown-stock-supply".into());
            }
        }
    }
    if kind != "store" && record.get("flags").and_then(Value::as_int).unwrap_or(0) & 8 != 0 {
        concerns.push("undroppable-instance".into());
    }
    let item = row.get("item");
    if record.contains_key("raw_resref_hex") {
        concerns.push(INVALID_ITEM.into());
    } else if item.and_then(Value::as_str) == Some("") {
        concerns.push("empty-item-reference".into());
    } else if !item.and_then(Value::as_str).is_some_and(|item| item_ids.contains(item)) {
        concerns.push("missing-item-definition".into());
    }
    concerns.sort();
    Ok(concerns)
}

/// `validate_world`'s index of the world's records by identity.
#[derive(Clone, Debug, Default)]
pub struct WorldIndex {
    pub resources: RowIndex,
    pub sources: RowIndex,
    pub targets: RowIndex,
}

fn list<'a>(world: &'a Value, field: &str) -> &'a [Value] {
    world.get(field).and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[])
}

impl WorldIndex {
    pub fn resource<'a>(&self, world: &'a Value, id: &str) -> Option<&'a Value> {
        self.resources.position(id).map(|position| &list(world, "resources")[position])
    }
    pub fn source<'a>(&self, world: &'a Value, id: &str) -> Option<&'a Value> {
        self.sources.position(id).map(|position| &list(world, "sources")[position])
    }
    pub fn target<'a>(&self, world: &'a Value, id: &str) -> Option<&'a Value> {
        self.targets.position(id).map(|position| &list(world, "targets")[position])
    }
}

fn int_of(value: Option<&Value>) -> i64 {
    value.and_then(Value::as_int).unwrap_or(0)
}

/// Check preserved raw slot tables and derive quarantine, including aliases.
fn validate_world_inventory_diagnostics(world: &Value, index: &WorldIndex) -> Result<()> {
    let mut owner_rows: HashMap<&str, Vec<&Dict>> = HashMap::new();
    for row in list(world, "sources") {
        let kind = row.get("kind").and_then(Value::as_str).unwrap_or("");
        if kind == "cre-template" || kind == "embedded-cre" {
            let owner = row.get("owner").and_then(Value::as_str).unwrap_or("");
            owner_rows.entry(owner).or_default().push(row.get("record").and_then(Value::as_dict).expect("record"));
        }
    }
    let mut direct_count = 0;
    let mut unresolved_count = 0;
    for target in list(world, "targets") {
        let selector = target.get("selector").and_then(Value::as_dict).expect("validated selector");
        let kind = target.get("kind").and_then(Value::as_str).unwrap_or("");
        let unresolved = selector.contains_key("storage_diagnostics");
        if unresolved {
            unresolved_count += 1;
        }
        let direct = selector.contains_key("inventory_diagnostics");
        let inherited = selector.contains_key("template_inventory_diagnostics");
        let external = kind == "actor" && int_of(selector.get("embedded_cre_size")) == 0 && !unresolved;
        if direct {
            direct_count += 1;
            let (base, end) = if kind == "cre-template" {
                let resource = target.get("resource").and_then(Value::as_str).unwrap_or("");
                (0, int_of(index.resource(world, resource).and_then(|row| row.get("byte_length"))))
            } else if kind == "actor" && !external {
                let base = int_of(selector.get("embedded_cre_offset"));
                (base, base + int_of(selector.get("embedded_cre_size")))
            } else {
                return Err(Error::profile("Inventory diagnostics need a physical creature owner"));
            };
            let diag = selector.get("inventory_diagnostics").expect("direct");
            let mut expected = vec!["cre_offset", "item_count", "items_offset", "item_slots_offset", "slot_words"];
            if diag.as_dict().is_some_and(|fields| fields.contains_key("invalid_item_indexes")) {
                expected.push("invalid_item_indexes");
            }
            let fields_map = fields(diag, &expected, "inventory diagnostics")?;
            for key in ["cre_offset", "item_count", "items_offset", "item_slots_offset"] {
                integer(fields_map.get(key), &format!("inventory diagnostics {key}"), 0, 0xFFFF_FFFF)?;
            }
            if int_of(fields_map.get("cre_offset")) != base {
                return Err(Error::profile("Inventory diagnostics name the wrong creature offset"));
            }
            let slot_start = base + int_of(fields_map.get("item_slots_offset"));
            if slot_start < base + 0x2D4 || slot_start + 80 > end {
                return Err(Error::profile("Inventory diagnostic slots lie outside their creature"));
            }
            let words = match fields_map.get("slot_words") {
                Some(Value::List(words)) if words.len() == 40 => words,
                _ => return Err(Error::profile("Inventory diagnostics require all 40 raw slot words")),
            };
            let mut values = Vec::with_capacity(40);
            for word in words {
                values.push(integer(Some(word), "inventory diagnostic slot word", 0, 0xFFFF)?);
            }
            let count = int_of(fields_map.get("item_count"));
            let target_id = target.get("id").and_then(Value::as_str).unwrap_or("");
            let rows = owner_rows.get(target_id).cloned().unwrap_or_default();
            let mut record_indexes: Vec<i64> = rows.iter().map(|row| int_of(row.get("record_index"))).collect();
            record_indexes.sort();
            if count != rows.len() as i64 || record_indexes != (0..count).collect::<Vec<i64>>() {
                return Err(Error::profile("Inventory diagnostics disagree with physical item records"));
            }
            let mut invalid_indexes: Vec<i64> = rows
                .iter()
                .filter(|row| row.contains_key("raw_resref_hex"))
                .map(|row| int_of(row.get("record_index")))
                .collect();
            invalid_indexes.sort();
            if fields_map.contains_key("invalid_item_indexes") {
                let listed = indexes(fields_map.get("invalid_item_indexes"), "invalid inventory item", 0xFFFF_FFFF)?;
                let listed: Vec<i64> = listed.iter().map(|value| int_of(Some(value))).collect();
                if listed.is_empty() || listed != invalid_indexes {
                    return Err(Error::profile("Inventory diagnostics disagree with invalid item records"));
                }
            } else if !invalid_indexes.is_empty() {
                return Err(Error::profile("Inventory diagnostics omit invalid item records"));
            }
            if inventory_concerns(diag)?.is_empty() {
                return Err(Error::profile("Inventory diagnostics contain no invalid references"));
            }
            // Empty tables have no byte span; their unused offset is kept.
            let item_start = base + int_of(fields_map.get("items_offset"));
            let item_end = item_start + count * 20;
            if count != 0
                && (item_start < base + 0x2D4
                    || item_end > end
                    || item_start.max(slot_start) < item_end.min(slot_start + 80))
            {
                return Err(Error::profile(
                    "Inventory diagnostic item and slot sections overlap or exceed their creature",
                ));
            }
            for row in &rows {
                let record_index = int_of(row.get("record_index"));
                let expected_slots: Vec<Value> = values[..38]
                    .iter()
                    .enumerate()
                    .filter(|(_, &word)| word == record_index)
                    .map(|(slot, _)| Value::from(slot as i64))
                    .collect();
                if !row.get("byte_offset").is_some_and(|offset| offset.py_eq(&Value::Int(item_start + record_index * 20)))
                    || !row.get("slots").is_some_and(|slots| slots.py_eq(&Value::List(expected_slots)))
                {
                    return Err(Error::profile("Inventory diagnostics disagree with item offsets or slots"));
                }
            }
        }
        let template = if external {
            selector.get("template_resource").and_then(Value::as_str).and_then(|id| index.target(world, id))
        } else {
            None
        };
        let expected = template.and_then(|template| template.get("selector")).and_then(|selector| selector.get("inventory_diagnostics"));
        let expected = expected.filter(|value| !value.is_null());
        if inherited != expected.is_some()
            || (inherited && !selector.get("template_inventory_diagnostics").expect("inherited").py_eq(expected.unwrap()))
        {
            return Err(Error::profile("Actor inventory diagnostics disagree with its template"));
        }
        let diag = if selector.contains_key("inventory_diagnostics") {
            selector.get("inventory_diagnostics")
        } else {
            selector.get("template_inventory_diagnostics")
        };
        let expected_concerns = match diag {
            Some(diag) if !diag.is_null() => inventory_concerns(diag)?,
            _ => Vec::new(),
        };
        let concerns = concern_list(target.get("concerns"));
        for concern in [DANGLING_INVENTORY, INVALID_INVENTORY] {
            if concerns.contains(&concern) != expected_concerns.iter().any(|value| value == concern) {
                return Err(Error::profile("Target concerns disagree with its inventory diagnostics"));
            }
        }
    }
    let diagnostic_schema = world.get("schema").and_then(Value::as_str) == Some(DIAGNOSTIC_WORLD_SCHEMA);
    if diagnostic_schema != (direct_count > 0 || unresolved_count > 0) {
        return Err(Error::profile("World schema disagrees with its inventory diagnostics"));
    }
    Ok(())
}

/// Counts over direct owners only; external actor references are not copies.
pub fn inventory_diagnostic_counts(world: &Value) -> Result<Dict> {
    let targets = list(world, "targets");
    let diagnostics: Vec<&Value> = targets
        .iter()
        .filter_map(|row| row.get("selector").and_then(|selector| selector.get("inventory_diagnostics")))
        .collect();
    let mut dangling_owners = 0;
    let mut dangling_slots = 0;
    let mut invalid_owners = 0;
    for diag in &diagnostics {
        let concerns = inventory_concerns(diag)?;
        if concerns.iter().any(|concern| concern == DANGLING_INVENTORY) {
            dangling_owners += 1;
        }
        if concerns.iter().any(|concern| concern == INVALID_INVENTORY) {
            invalid_owners += 1;
        }
        let count = int_of(diag.get("item_count"));
        let words = diag.get("slot_words").and_then(Value::as_list).expect("checked");
        dangling_slots += words[..38].iter().filter(|word| {
            let word = int_of(Some(word));
            word != 0xFFFF && word >= count
        }).count();
    }
    let invalid_records = list(world, "sources")
        .iter()
        .filter(|row| row.get("record").is_some_and(|record| record.get("raw_resref_hex").is_some()))
        .count();
    let unresolved = targets
        .iter()
        .filter(|row| row.get("selector").is_some_and(|selector| selector.get("storage_diagnostics").is_some()))
        .count();
    Ok(crate::dict! {
        "dangling_inventory_owners" => dangling_owners as i64,
        "dangling_inventory_slots" => dangling_slots as i64,
        "invalid_inventory_owners" => invalid_owners as i64,
        "invalid_item_records" => invalid_records as i64,
        "unresolved_actor_storage" => unresolved as i64,
    })
}

pub fn validate_world(world: &Value, catalog: &Value) -> Result<WorldIndex> {
    let item_index = validate_catalog(catalog)?;
    let world_fields = fields(
        world,
        &["schema", "game_root", "catalog_fingerprint", "coverage", "resources", "sources", "targets", "fingerprint"],
        "world catalogue",
    )?;
    let field = |key: &str| world_fields.get(key).expect("exact fields");
    let schema = field("schema").as_str();
    if !matches!(schema, Some(WORLD_SCHEMA) | Some(DIAGNOSTIC_WORLD_SCHEMA)) || field("coverage").as_str() != Some(COVERAGE) {
        return Err(Error::profile("Unsupported world catalogue coverage"));
    }
    if !field("catalog_fingerprint").py_eq(catalog.at("fingerprint")?) {
        return Err(Error::profile("World catalogue belongs to another item scan"));
    }
    if !field("game_root").py_eq(catalog.at("game_root")?) {
        return Err(Error::profile("World and item catalogues name different installations"));
    }
    let resource_rows = rows(Some(field("resources")), "resource")?;
    let resources = indexed(resource_rows, "resource")?;
    let source_rows = rows(Some(field("sources")), "source")?;
    let sources = indexed(source_rows, "source")?;
    let target_rows = rows(Some(field("targets")), "target")?;
    let targets = indexed(target_rows, "target")?;
    let index = WorldIndex { resources, sources, targets };
    for row in resource_rows {
        let row_fields = fields(row, &["id", "kind", "resref", "sha256", "byte_length"], "resource")?;
        resref_name(row_fields.get("resref"))?;
        integer(row_fields.get("byte_length"), "resource byte length", 0, 0xFFFF_FFFF)?;
        let kind = row_fields.get("kind").and_then(Value::as_str);
        let valid_kind = kind.is_some_and(|kind| KINDS.contains(&kind));
        let resref = row_fields.get("resref").and_then(Value::as_str).unwrap_or("");
        if !valid_kind || row_fields.get("id").and_then(Value::as_str) != Some(&format!("{}:{resref}", kind.unwrap())) {
            return Err(Error::profile("Invalid resource identity"));
        }
        if !row_fields.get("sha256").and_then(Value::as_str).is_some_and(is_hex256) {
            return Err(Error::profile("Invalid resource fingerprint"));
        }
    }
    let mut installed: BTreeMap<&str, &Value> = BTreeMap::new();
    for row in resource_rows {
        if row.get("kind").and_then(Value::as_str) == Some("itm") {
            installed.insert(row.get("resref").and_then(Value::as_str).unwrap_or(""), row.get("sha256").unwrap());
        }
    }
    let catalog_items: BTreeMap<&str, &Value> = item_index
        .resrefs()
        .iter()
        .map(|resref| (resref.as_str(), item_index.row(catalog, resref).and_then(|row| row.get("sha256")).unwrap()))
        .collect();
    if installed.len() != catalog_items.len()
        || installed.iter().any(|(key, value)| !catalog_items.get(key).is_some_and(|other| value.py_eq(other)))
    {
        return Err(Error::profile("World catalogue item resources disagree with the item catalogue"));
    }
    for row in source_rows.iter().chain(target_rows) {
        match row.get("resource") {
            Some(Value::Str(resource)) if index.resources.contains(resource) => {}
            _ => return Err(Error::profile("A world record refers to an unknown resource")),
        }
        let valid = match row.get("concerns") {
            Some(Value::List(concerns)) => {
                concerns.iter().all(Value::is_str)
                    && concerns.windows(2).all(|pair| pair[0].as_str().unwrap() < pair[1].as_str().unwrap())
            }
            _ => false,
        };
        if !valid {
            return Err(Error::profile("Invalid world record concerns"));
        }
    }
    let byte_length = |resource: &str| int_of(index.resource(world, resource).and_then(|row| row.get("byte_length")));
    for row in target_rows {
        let target = fields(row, &["id", "resource", "kind", "selector", "concerns"], "target")?;
        let kind = target.get("kind").and_then(Value::as_str).unwrap_or("");
        let selector = target.get("selector").and_then(Value::as_dict);
        let Some(selector) = selector.filter(|_| ["cre-template", "actor", "container", "store"].contains(&kind)) else {
            return Err(Error::profile("Invalid target selector"));
        };
        let resource = target.get("resource").and_then(Value::as_str).unwrap();
        let resource_kind = index.resource(world, resource).and_then(|row| row.get("kind")).and_then(Value::as_str);
        let expected_kind = match kind {
            "cre-template" => "cre",
            "store" => "sto",
            _ => "are",
        };
        if resource_kind != Some(expected_kind) {
            return Err(Error::profile("Target kind disagrees with its resource"));
        }
        let unresolved = selector.contains_key("storage_diagnostics");
        if concern_list(target.get("concerns")).contains(&UNRESOLVED_STORAGE) != unresolved {
            return Err(Error::profile("Target concerns disagree with its creature storage"));
        }
        if unresolved && kind != "actor" {
            return Err(Error::profile("Only area actors can have unresolved creature storage"));
        }
        let id = target.get("id").and_then(Value::as_str).unwrap_or("");
        if kind == "actor" || kind == "container" {
            let record_index = integer(selector.get("record_index"), "target record index", 0, 0xFFFF_FFFF)?;
            integer(selector.get("byte_offset"), "target byte offset", 0, 0xFFFF_FFFF)?;
            if id != format!("{resource}/{kind}/{record_index}") {
                return Err(Error::profile("Invalid target record identity"));
            }
            integer(selector.get("flags"), "target flags", 0, 0xFFFF_FFFF)?;
            let size = if kind == "actor" { 0x110 } else { 0xC0 };
            if int_of(selector.get("byte_offset")) + size > byte_length(resource) {
                return Err(Error::profile("Target lies outside its resource"));
            }
            if kind == "container" {
                indexes(selector.get("item_indexes"), "container item", 0xFFFF_FFFF)?;
                match selector.get("dynamic_item_table") {
                    None | Some(Value::Null) | Some(Value::Str(_)) => {}
                    _ => return Err(Error::profile("Invalid dynamic container marker")),
                }
                if !selector.contains_key("dynamic_item_table") {
                    return Err(Error::profile("Missing dynamic container marker"));
                }
            } else {
                integer(selector.get("embedded_cre_offset"), "embedded creature offset", 0, 0xFFFF_FFFF)?;
                integer(selector.get("embedded_cre_size"), "embedded creature size", 0, 0xFFFF_FFFF)?;
                let offset = int_of(selector.get("embedded_cre_offset"));
                let size = int_of(selector.get("embedded_cre_size"));
                let template = selector.get("template_resource");
                let incomplete = (offset != 0) != (size != 0);
                if unresolved {
                    let diag = fields(selector.get("storage_diagnostics").unwrap(), &["reason"], "actor storage diagnostics")?;
                    if diag.get("reason").and_then(Value::as_str) != Some("incomplete-embedded-cre")
                        || !incomplete
                        || int_of(selector.get("flags")) & 1 != 0
                        || ["template_resource", "inventory_diagnostics", "template_inventory_diagnostics"]
                            .iter()
                            .any(|key| selector.contains_key(key))
                    {
                        return Err(Error::profile("Actor storage diagnostics disagree with raw storage fields"));
                    }
                } else if incomplete {
                    return Err(Error::profile("Incomplete actor storage is missing its diagnostics"));
                } else if size == 0 {
                    let Some(template) = template.and_then(Value::as_str).filter(|text| text.starts_with("cre:")) else {
                        return Err(Error::profile("Invalid external creature template"));
                    };
                    resref_name(Some(&Value::from(&template[4..])))?;
                    let cre_resref = match selector.get("cre_resref") {
                        Some(Value::Str(text)) => text.clone(),
                        Some(other) => crate::json::py_str(other),
                        None => "None".into(),
                    };
                    if template != format!("cre:{cre_resref}") {
                        return Err(Error::profile("External creature reference mismatch"));
                    }
                } else if offset + size > byte_length(resource) {
                    return Err(Error::profile("Embedded creature lies outside its area"));
                }
            }
        } else if id != resource {
            return Err(Error::profile("Invalid template target identity"));
        }
    }
    let mut physical_spans: HashMap<&str, Vec<(i64, i64)>> = HashMap::new();
    let mut inventory_slots: HashSet<(String, i64)> = HashSet::new();
    let mut container_records: HashMap<(String, i64), &Dict> = HashMap::new();
    for row in source_rows {
        let source = fields(row, &["id", "item", "resource", "owner", "kind", "record", "concerns"], "source")?;
        let kind = source.get("kind").and_then(Value::as_str).unwrap_or("");
        if !["cre-template", "embedded-cre", "container", "store"].contains(&kind) {
            return Err(Error::profile("Invalid source kind"));
        }
        let item = source.get("item").unwrap();
        let record = match source.get("record") {
            Some(Value::Dict(record)) if record.get("resref").unwrap_or(&Value::Null).py_eq(item) => record,
            _ => return Err(Error::profile("Invalid source item record")),
        };
        // Only physical observations can have an empty item reference.
        if !item.py_eq(&Value::from("")) {
            resref_name(Some(item))?;
        }
        let creature = kind == "cre-template" || kind == "embedded-cre";
        if let Some(raw) = record.get("raw_resref_hex") {
            let raw_text = raw.as_str().filter(|text| {
                text.len() == 16 && text.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            });
            if !creature || !item.py_eq(&Value::from("")) || raw_text.is_none() || !record.contains_key("inventory_diagnostics") {
                return Err(Error::profile("Invalid raw creature item reference"));
            }
            let bytes = crate::py::from_hex(raw_text.unwrap()).expect("hex");
            if reference(&bytes, 0, true, false).is_ok() {
                return Err(Error::profile("Raw invalid item reference is actually a valid or empty reference"));
            }
        }
        for key in ["record_index", "byte_offset", "expiration", "flags"] {
            integer(record.get(key), &format!("source {key}"), 0, 0xFFFF_FFFF)?;
        }
        let resource = source.get("resource").and_then(Value::as_str).unwrap();
        let record_start = int_of(record.get("byte_offset"));
        let record_end = record_start + if kind == "store" { 28 } else { 20 };
        if record_end > byte_length(resource) {
            return Err(Error::profile("Source lies outside its resource"));
        }
        physical_spans.entry(resource).or_default().push((record_start, record_end));
        let owner = text(source.get("owner"), "source owner")?;
        let record_index = int_of(record.get("record_index"));
        if source.get("id").and_then(Value::as_str) != Some(&format!("{owner}/item/{record_index}")) {
            return Err(Error::profile("Invalid source selector"));
        }
        let resource_kind = index.resource(world, resource).and_then(|row| row.get("kind")).and_then(Value::as_str);
        let expected_kind = match kind {
            "cre-template" => "cre",
            "store" => "sto",
            _ => "are",
        };
        if resource_kind != Some(expected_kind) {
            return Err(Error::profile("Source kind disagrees with its resource"));
        }
        if (kind == "cre-template" || kind == "store") && owner != resource {
            return Err(Error::profile("Invalid source template owner"));
        }
        let charges = match record.get("charges") {
            Some(Value::List(charges)) if charges.len() == 3 => charges,
            _ => return Err(Error::profile("Source charges must contain three values")),
        };
        for charge in charges {
            integer(Some(charge), "source charge", 0, 0xFFFF)?;
        }
        if creature {
            let Some(owner_target) = index.target(world, owner) else {
                return Err(Error::profile("Creature source has no matching owner target"));
            };
            let owner_selector = owner_target.get("selector").and_then(Value::as_dict).expect("validated");
            if owner_selector.contains_key("storage_diagnostics") {
                return Err(Error::profile("Unresolved actor storage cannot own physical item records"));
            }
            let mine = record.get("inventory_diagnostics");
            let theirs = owner_selector.get("inventory_diagnostics");
            if mine.is_some() != theirs.is_some()
                || !mine.unwrap_or(&Value::Null).py_eq(theirs.unwrap_or(&Value::Null))
            {
                return Err(Error::profile("Source inventory diagnostics disagree with its owner"));
            }
            for slot in indexes(record.get("slots"), "inventory slot", 37)? {
                if !inventory_slots.insert((owner.to_string(), int_of(Some(slot)))) {
                    return Err(Error::profile("Distinct inventory records claim the same creature slot"));
                }
            }
            if kind == "embedded-cre" {
                let target = owner_target;
                let target_selector = owner_selector;
                if target.get("kind").and_then(Value::as_str) != Some("actor")
                    || target.get("resource").and_then(Value::as_str) != Some(resource)
                    || !target_selector.get("embedded_cre_size").ok_or_else(|| Error::key("embedded_cre_size"))?.truthy()
                {
                    return Err(Error::profile("Embedded source has no matching embedded actor"));
                }
                let start = int_of(target_selector.get("embedded_cre_offset"));
                let size = int_of(target_selector.get("embedded_cre_size"));
                if record_start < start || record_end > start + size {
                    return Err(Error::profile("Embedded inventory lies outside its actor"));
                }
            }
        } else if record.contains_key("inventory_diagnostics") {
            return Err(Error::profile("Only creature sources can carry inventory diagnostics"));
        }
        if kind == "container" {
            container_records.insert((resource.to_string(), record_index), record);
            let owners = indexes(record.get("container_indexes"), "container owner", 0xFFFF_FFFF)?;
            let expected_single = if owners.len() == 1 { owners[0].clone() } else { Value::Null };
            if !record.get("container_index").unwrap_or(&Value::Null).py_eq(&expected_single) {
                return Err(Error::profile("Inconsistent container ownership"));
            }
            let expected_owner = if owners.len() == 1 {
                format!("{resource}/container/{}", int_of(Some(&owners[0])))
            } else {
                format!("{resource}/unresolved")
            };
            if owner != expected_owner {
                return Err(Error::profile("Invalid container source owner"));
            }
            for number in owners {
                let target = index.target(world, &format!("{resource}/container/{}", int_of(Some(number))));
                let listed = target
                    .and_then(|target| target.get("selector"))
                    .and_then(|selector| selector.get("item_indexes"))
                    .and_then(Value::as_list)
                    .is_some_and(|list| list.iter().any(|value| value.py_eq(&Value::Int(record_index))));
                if !listed {
                    return Err(Error::profile("Container source disagrees with its owner range"));
                }
            }
        }
        if kind == "store" {
            integer(record.get("quantity"), "store quantity", 0, 0xFFFF_FFFF)?;
            integer(record.get("infinite_supply_flag"), "store supply flag", 0, 0xFFFF_FFFF)?;
            let flag = int_of(record.get("infinite_supply_flag")) != 0;
            if !matches!(record.get("infinite_supply"), Some(Value::Bool(value)) if *value == flag) {
                return Err(Error::profile("Invalid store infinite supply marker"));
            }
        }
        let expected = source_concerns(source, world, &index, &item_index)?;
        let actual = concern_list(source.get("concerns"));
        if actual.len() != expected.len() || actual.iter().zip(&expected).any(|(a, b)| a != b) {
            return Err(Error::profile("Source concerns disagree with its inventory data"));
        }
    }
    for spans in physical_spans.values_mut() {
        spans.sort();
        let mut end = 0;
        for (start, next_end) in spans.iter() {
            if *start < end {
                return Err(Error::profile("Physical source records overlap or are duplicated"));
            }
            end = *next_end;
        }
    }
    for target in target_rows {
        if target.get("kind").and_then(Value::as_str) != Some("container") {
            continue;
        }
        let resource = target.get("resource").and_then(Value::as_str).unwrap();
        let selector = target.get("selector").unwrap();
        let record_index = selector.get("record_index").unwrap();
        for number in selector.get("item_indexes").and_then(Value::as_list).into_iter().flatten() {
            let record = container_records.get(&(resource.to_string(), int_of(Some(number))));
            let owned = record.is_some_and(|record| {
                record
                    .get("container_indexes")
                    .and_then(Value::as_list)
                    .is_some_and(|owners| owners.iter().any(|owner| owner.py_eq(record_index)))
            });
            if !owned {
                return Err(Error::profile("Container owner range disagrees with its source records"));
            }
        }
    }
    validate_world_inventory_diagnostics(world, &index)?;
    let fingerprint = field("fingerprint");
    if !fingerprint.is_str() || fingerprint.as_str() != Some(world_fingerprint(world)?.as_str()) {
        return Err(Error::profile("World catalogue content changed; scan again"));
    }
    Ok(index)
}

fn page(rows: Vec<&Value>, offset: i64, limit: i64) -> Result<Dict> {
    integer(Some(&Value::Int(offset)), "offset", 0, 0xFFFF_FFFF)?;
    integer(Some(&Value::Int(limit)), "limit", 1, 100)?;
    let start = (offset as usize).min(rows.len());
    let end = (offset as usize).saturating_add(limit as usize).min(rows.len());
    let next = offset + limit;
    Ok(crate::dict! {
        "total" => rows.len(), "offset" => offset,
        "records" => Value::List(rows[start..end].iter().map(|row| (*row).clone()).collect()),
        "next_offset" => if (next as usize) < rows.len() { Value::Int(next) } else { Value::Null },
    })
}

pub fn inspect_sources(world: &Value, catalog: &Value, item: &str, offset: i64, limit: i64) -> Result<Value> {
    validate_world(world, catalog)?;
    let item = crate::py::lower(item);
    let known = catalog
        .get("items")
        .and_then(Value::as_list)
        .is_some_and(|rows| rows.iter().any(|row| row.get("resref").and_then(Value::as_str) == Some(item.as_str())));
    if !known {
        return Err(Error::profile("Unknown item identity"));
    }
    let rows: Vec<&Value> =
        list(world, "sources").iter().filter(|row| row.get("item").and_then(Value::as_str) == Some(item.as_str())).collect();
    let discovery = match rows.len() {
        0 => "absent",
        1 => "single-record",
        _ => "multiple-records",
    };
    let mut result = page(rows, offset, limit)?;
    result.insert("discovery", discovery);
    result.insert("runtime_availability", "unverified");
    Ok(Value::Dict(result))
}

pub fn search_targets(
    world: &Value,
    catalog: &Value,
    query: &str,
    kind: Option<&str>,
    offset: i64,
    limit: i64,
) -> Result<Value> {
    validate_world(world, catalog)?;
    let folded = crate::py::casefold(crate::py::strip(query));
    let terms = crate::py::split_whitespace(&folded);
    if terms.is_empty() && kind.is_none() {
        return Err(Error::profile("Enter a target search term or choose a target kind"));
    }
    if let Some(kind) = kind {
        if !["cre-template", "actor", "container", "store"].contains(&kind) {
            return Err(Error::profile("Unknown target kind"));
        }
    }
    let mut rows = Vec::new();
    for row in list(world, "targets") {
        if let Some(kind) = kind {
            if row.get("kind").and_then(Value::as_str) != Some(kind) {
                continue;
            }
        }
        let selector = row.get("selector");
        let parts: Vec<String> = ["name", "script_name", "cre_resref"]
            .iter()
            .map(|key| match selector.and_then(|selector| selector.get(key)) {
                Some(value) => crate::json::py_str(value),
                None => String::new(),
            })
            .collect();
        let id = row.get("id").and_then(Value::as_str).unwrap_or("");
        let haystack = crate::py::casefold(&format!("{id} {}", parts.join(" ")));
        if terms.iter().all(|term| haystack.contains(term)) {
            rows.push(row);
        }
    }
    Ok(Value::Dict(page(rows, offset, limit)?))
}

pub fn new_binding_plan(profile: &Value, catalog: &Value, world: &Value) -> Result<Value> {
    validate_profile(profile, catalog, false)?;
    validate_world(world, catalog)?;
    Ok(Value::Dict(crate::dict! {
        "schema" => PLAN_SCHEMA, "catalog_fingerprint" => catalog.at("fingerprint")?.clone(),
        "world_fingerprint" => world.at("fingerprint")?.clone(), "sources" => crate::list![],
        "registrations" => crate::list![], "targets" => crate::list![],
    }))
}

// ---------------------------------------------------------------- binding

pub fn bind_profile(_profile: &Value, _catalog: &Value, _world: &Value, _plan: &Value) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: bind_profile"))
}

pub fn prepare_bound_profile(_profile: &Value, _catalog: &Value, _world: &Value, _plan: &Value, _store: &Path) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: prepare_bound_profile"))
}

pub fn read_bound_profile(_store: &Path, _revision: Option<&str>) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: read_bound_profile"))
}
