//! Export reviewed physical and BLPOOL sources for the experimental native
//! runtime (the reference's `native_export.py`).
//!
//! This writes configuration outside the game. It does not install runtime
//! hooks, change resources, choose gameplay content, or establish live target
//! eligibility.

use crate::activation::{EXPORT_FILES, EXPORT_FILES_SCHEMA};
use crate::campaigns::{read_campaign, refused_lua, render_lua, store_path, write_immutable_replacing};
use crate::error::{Error, ErrorKind, Result, CHANGED_SINCE_SCAN};
use crate::game_text::{paired, text_files, unpaired};
use crate::json::{parse_lenient, py_str, utf8_error_message, Dict, Value};
use crate::mode2_rewards::Naming;
use crate::original_catalog::container_target_spec;
use crate::platform::game_platform;
use crate::reward_copies::RewardCopies;
use crate::reward_routes::{reward_routes, RewardRoutes};
use crate::prepared::{applied, quest_change, require_installed};
use crate::profiles::{
    canonical_bytes, default_shop_pricing, digest, ensure_output_outside_game, is_hex256, make_dirs,
    validate_catalog, ItemIndex, MAX_ITEM_PRICE,
};
use crate::py::{lower, upper};
use crate::resources::{resource_type, sha256_hex, GameResources, Tlk};
use crate::sources::{validate_world, Scans, WorldIndex, DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE};
use crate::{
    container_rewards, creature_inventory, cursed_disguises, group_recipients, mode2_rewards, original_options,
    paths, quest_options, quest_rewards, scripted_sources, spell_rewards, store_inventory,
};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

const INVENTORY_PROBLEMS: [&str; 3] = [DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE];
const PAYLOAD_FIELDS: [&str; 4] = ["resref", "expiration", "charges", "flags"];
/// The reviewed Deck of Many Things reward and its recipient.
const SPELL_SOURCE: &str = "original-source:bg2:mode2:item:69";
const SPELL_TARGET: &str = "original-target:bg2:mode2:target:70";
const EXPORT_TYPES: [&str; 8] = ["itm", "are", "cre", "sto", "bcs", "dlg", "spl", "lua"];

fn require(condition: bool, message: impl FnOnce() -> String) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(Error::profile(message()))
    }
}

fn text<'v>(value: &'v Value, key: &str) -> Result<&'v str> {
    Ok(value.at(key)?.as_str().unwrap_or(""))
}

fn entry<'d>(dict: &'d Dict, key: &str) -> Result<&'d Value> {
    dict.get(key).ok_or_else(|| Error::key(key))
}

fn list(value: &Value) -> &[Value] {
    value.as_list().map(Vec::as_slice).unwrap_or(&[])
}

fn rows<'v>(world: &'v Value, field: &str) -> &'v [Value] {
    world.get(field).map(list).unwrap_or(&[])
}

/// `text[start:]` for the ASCII resource identities used here.
fn tail(text: &str, start: usize) -> &str {
    text.get(start..).unwrap_or("")
}

/// `data[start:end]` with Python's clamping.
fn slice(data: &[u8], start: usize, end: usize) -> &[u8] {
    let end = end.min(data.len());
    &data[start.min(end)..end]
}

fn int_like(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(flag) => Some(i64::from(*flag)),
        other => other.as_int(),
    }
}

fn payload(record: &Value) -> Result<Value> {
    let mut payload = Dict::new();
    for key in PAYLOAD_FIELDS {
        payload.insert(key, record.at(key)?.clone());
    }
    Ok(Value::Dict(payload))
}

fn identity_of(parts: Dict) -> Result<Value> {
    Ok(Value::from(digest(&Value::Dict(parts))?))
}

fn read_bytes(path: &Path) -> Result<Vec<u8>> {
    fs::read(path).map_err(|error| Error::os(&error, path))
}

fn write_bytes(path: &Path, data: &[u8]) -> Result<()> {
    fs::write(path, data).map_err(|error| Error::os(&error, path))
}

/// `_name`: a nonempty printable-ASCII script name of at most 32 characters.
fn script_name(value: Option<&Value>, identity: &str) -> Result<String> {
    if let Some(Value::Str(name)) = value {
        let count = name.chars().count();
        if (1..=32).contains(&count) && name.chars().all(|character| (' '..='~').contains(&character)) {
            return Ok(name.clone());
        }
    }
    Err(Error::profile(format!("Native endpoint needs a nonempty ASCII script name: {identity}")))
}

fn is_resref(name: &str) -> bool {
    (1..=8).contains(&name.len())
        && name.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'#' | b'_' | b'-'))
}

/// `_area`: the upper-case name of an `are:` resource.
fn area(resource: &Value) -> Result<String> {
    let Value::Str(resource) = resource else {
        return Err(Error::type_error(format!(
            "expected string or bytes-like object, got '{}'",
            resource.type_name()
        )));
    };
    require(resource.strip_prefix("are:").is_some_and(is_resref), || {
        format!("Native export needs an ARE resource: {resource}")
    })?;
    Ok(upper(tail(resource, 4)))
}

/// One part of a hashable key with Python's equality (1, 1.0 and True are
/// one key).
fn key_part(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "N".into(),
        Some(Value::Bool(flag)) => format!("n{}", u8::from(*flag)),
        Some(Value::Int(number)) => format!("n{number}"),
        Some(Value::BigInt(number)) => format!("n{number}"),
        Some(Value::Float(number)) if number.fract() == 0.0 && number.abs() < 9.0e15 => {
            format!("n{}", *number as i64)
        }
        Some(Value::Float(number)) => format!("f{number:?}"),
        Some(Value::Str(text)) => format!("s{text}"),
        Some(other) => format!("u{}", py_str(other)),
    }
}

fn name_part(name: &str) -> String {
    format!("s{name}")
}

fn tuple_key(parts: &[String]) -> String {
    parts.join("\u{0}")
}

/// `(name or "").lower()`.
fn lower_name(name: Option<&Value>) -> Result<String> {
    match name {
        Some(Value::Str(name)) => Ok(lower(name)),
        Some(value) if value.truthy() => {
            Err(Error::type_error(format!("'{}' object has no attribute 'lower'", value.type_name())))
        }
        _ => Ok(String::new()),
    }
}

/// The script name the runtime addresses for an actor target.
fn actor_name<'v>(world: &'v Value, index: &WorldIndex, target: &'v Value) -> Option<&'v Value> {
    let selector = target.get("selector")?;
    let template = selector
        .get("template_resource")
        .and_then(Value::as_str)
        .and_then(|identity| index.target(world, identity))
        .filter(|template| template.truthy());
    match template {
        Some(template) => template.get("selector").and_then(|selector| selector.get("script_name")),
        None => selector.get("script_name"),
    }
}

fn has_inventory_problem(creature: &Value) -> Result<bool> {
    Ok(list(creature.at("concerns")?)
        .iter()
        .any(|concern| concern.as_str().is_some_and(|concern| INVENTORY_PROBLEMS.contains(&concern))))
}

fn in_coordinates(value: &Value) -> bool {
    int_like(value).is_some_and(|number| (0..=32767).contains(&number))
}

fn in_inventory(slot: &Value) -> bool {
    int_like(slot).is_some_and(|slot| (21..=36).contains(&slot))
}

/// The selected replacement (`selected.get("replacement")`), None when
/// absent or null.
fn replacement_of(selected: &Value) -> Option<&Value> {
    selected.get("replacement").filter(|replacement| !replacement.is_null())
}

struct Compiler<'a> {
    world: &'a Value,
    index: &'a WorldIndex,
    items: &'a ItemIndex,
    catalog: &'a Value,
    game_root: String,
    shield: Option<&'a Value>,
    sources: Dict,
    targets: Dict,
    resource_hashes: Dict,
    required: BTreeSet<String>,
    pristine: BTreeSet<String>,
    selected_items: BTreeSet<String>,
    selected_actors: HashSet<String>,
    selected_owners: HashMap<String, usize>,
    container_names: HashMap<String, usize>,
    actor_names: HashMap<String, usize>,
    container_items: HashMap<String, usize>,
    /// The sources of each item by (resource, kind, item).
    physical_items: HashMap<String, usize>,
    /// The records of each item in each creature file, in record order.
    template_records: HashMap<String, Vec<&'a Value>>,
    creature_items: HashMap<String, usize>,
    /// The sources of each item in each owner, of any kind.
    owner_items: HashMap<String, usize>,
    /// The actors placed from each creature template, in world order.
    template_actors: HashMap<String, Vec<&'a Value>>,
    /// The reward routes, looked at once per compile.
    routes: Option<Arc<RewardRoutes>>,
    /// Whether the revision was exported before and belongs to running saves.
    stored: bool,
    revision: String,
    notes: Vec<String>,
}

impl<'a> Compiler<'a> {
    /// One look at the reward routes per compile: each check run sees the
    /// game's files as they are now.
    fn routes(&mut self) -> Result<Arc<RewardRoutes>> {
        if self.routes.is_none() {
            self.routes = Some(reward_routes(self.catalog, self.world)?);
        }
        Ok(self.routes.clone().expect("looked at above"))
    }

    fn require_route(&mut self, name: &str) -> Result<()> {
        let routes = self.routes()?;
        routes.require_route(name, !self.stored)?;
        let reason = if self.stored { routes.copy_reason(name)? } else { None };
        if let Some(reason) = reason {
            self.notes.push(format!(
                "Saved games of revision {} keep the extended reward route for {}, although {reason}",
                short(&self.revision),
                upper(name)
            ));
        }
        Ok(())
    }

    fn require_container(&mut self, target_id: &str) -> Result<()> {
        self.routes()?.require_container(target_id)
    }

    fn gives(&mut self, name: &str) -> Result<bool> {
        self.routes()?.gives(name)
    }

    fn scanned_hash(&self, resource: &str) -> Result<&'a Value> {
        self.index.resource(self.world, resource).ok_or_else(|| Error::key(resource))?.at("sha256")
    }

    /// A file the scan left out is in the game, but nothing in it is known.
    fn readable(&self, resource: &str) -> Result<()> {
        require(!self.index.is_unreadable(resource), || format!("Required reward resource cannot be read: {resource}"))
    }

    /// `use`: require a scanned resource and record its hash.
    fn use_resource(&mut self, resource: &str) -> Result<Value> {
        self.readable(resource)?;
        self.required.insert(resource.to_string());
        let Some((kind, name)) = resource.split_once(':') else {
            return Err(Error::value("not enough values to unpack (expected 2, got 1)"));
        };
        // The installer adds the mod's stand-in items with its other changes.
        let row = self.index.resource(self.world, resource);
        require(row.is_some() || !matches!(resource, "itm:flbhcryp" | "itm:flrbelt1"), || {
            format!("Chriz Item Randomiser did not install {resource}; reinstall it, then rescan")
        })?;
        let row = row.ok_or_else(|| Error::profile(format!("Required reward resource is absent: {resource}")))?;
        let digest = row.at("sha256")?.clone();
        self.resource_hashes.insert(format!("{}.{}", upper(name), upper(kind)), digest.clone());
        Ok(digest)
    }

    fn use_value(&mut self, resource: &Value) -> Result<Value> {
        self.use_resource(resource.as_str().unwrap_or(""))
    }

    fn container_key(&self, resource: Option<&Value>, name: &str, selector: &Value) -> Result<String> {
        Ok(tuple_key(&[
            key_part(resource),
            name_part(name),
            key_part(Some(selector.at("x")?)),
            key_part(Some(selector.at("y")?)),
            key_part(Some(selector.at("type")?)),
        ]))
    }

    fn count(counter: &HashMap<String, usize>, key: &str) -> usize {
        counter.get(key).copied().unwrap_or(0)
    }

    /// `attempt`: with `problems`, a row the runtime cannot use is recorded
    /// and left out of the result instead of stopping the configuration.
    fn attempt(
        &mut self,
        problems: &mut Option<&mut Dict>,
        identity: &str,
        step: impl FnOnce(&mut Self) -> Result<()>,
    ) -> Result<()> {
        let Some(problems) = problems.as_deref_mut() else {
            return step(self);
        };
        if problems.contains_key(identity) {
            return Ok(());
        }
        let actors = self.selected_actors.clone();
        match step(self) {
            // A reward resource changed since the scan stops the compile.
            Err(error) if error.kind == ErrorKind::Profile && !error.is(CHANGED_SINCE_SCAN) => {
                problems.insert(identity, error.message);
                self.selected_actors = actors;
                self.sources.remove(identity);
                self.targets.remove(identity);
                Ok(())
            }
            other => other,
        }
    }

    fn shield_truthy(&self) -> bool {
        self.shield.is_some_and(Value::truthy)
    }

    fn compile_source(&mut self, selected: &'a Value) -> Result<()> {
        let source = selected.at("record")?;
        let identity = text(selected, "id")?;
        let kind = text(source, "kind")?;
        require(
            matches!(
                kind,
                "container" | "cre-template" | "embedded-cre" | "store" | "scripted" | "virtual" | "authored-source"
            ),
            || format!("Native export does not yet support this source: {identity}"),
        )?;
        let resource = text(source, "resource")?;
        if kind == "virtual" {
            let ok = resource == format!("itm:{}", text(source, "item")?)
                && self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?);
            require(ok, || format!("Virtual item changed since the scan: {identity}"))?;
        } else if kind == "authored-source"
            || (self.shield_truthy() && identity == original_options::SPELL_SHIELD_SOURCE)
        {
            // A post-install rescan sees our campaign-aware DLG/BCS patch.
            // Reward route bytes are recognised below (reward_routes) and
            // again by the export, before writing any output.
            let ok = self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?)
                || (mode2_rewards::source_rewards().contains_key(identity)
                    && mode2_rewards::resource_rewards().contains_key(resource));
            require(ok, || format!("Authored source changed since the scan: {identity}"))?;
        } else if kind == "scripted" {
            require(applied(&self.game_root, "quest_pool")?, || {
                format!("Chriz Item Randomiser did not prepare BLPOOL.BCS; reinstall it, then rescan: {identity}")
            })?;
            let ok = identity == scripted_sources::SOURCE_ID
                && selected.at("quantity")?.py_eq(&Value::Int(1))
                && selected.get("replacement").is_none()
                && source.py_eq(&scripted_sources::campaign_record(&Value::Dict(crate::dict! {
                    "id" => scripted_sources::SOURCE_ID, "item" => scripted_sources::ITEM,
                    "resource" => scripted_sources::RESOURCE, "resource_sha256" => selected.at("resource_sha256")?.clone(),
                    "adapter_id" => scripted_sources::ADAPTER_ID, "selector" => scripted_sources::SELECTOR,
                    "copies" => 1, "optional" => false,
                }))?)
                && self
                    .index
                    .resource(self.world, scripted_sources::RESOURCE)
                    .and_then(|row| row.get("sha256"))
                    .unwrap_or(&Value::Null)
                    .py_eq(selected.at("resource_sha256")?);
            require(ok, || format!("Scripted source differs from the reviewed scan: {identity}"))?;
        } else {
            let ok = self.index.source(self.world, identity).is_some_and(|row| row.py_eq(source))
                && self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?);
            require(ok, || format!("Selected source differs from the scan: {identity}"))?;
        }
        let record = source.at("record")?;
        let item_name = text(source, "item")?;
        let item = self.items.row(self.catalog, item_name).ok_or_else(|| Error::key(item_name))?;
        let ok = is_resref(item_name)
            && record.at("expiration")?.py_eq(&Value::Int(0))
            && {
                let flags = record.at("flags")?;
                (0..4).any(|flag| flags.py_eq(&Value::Int(flag)))
            }
            && entry(item, "flags")?.as_int().unwrap_or(0) & 12 == 12;
        require(ok, || format!("Native source requires an unexpired, visible droppable item: {identity}"))?;
        self.selected_items.insert(item_name.to_string());
        let item_resource = format!("itm:{item_name}");
        let source_identity = || identity_of(crate::dict! {"source" => selected.clone()});

        if kind == "authored-source" && resource.starts_with("cre:") {
            let mut records = Vec::new();
            for key in list(source.at("physical_source_ids")?) {
                let key = key.as_str().unwrap_or("");
                records.push(self.index.source(self.world, key).ok_or_else(|| Error::key(key))?);
            }
            let mut ok = !records.is_empty();
            for row in &records {
                if !ok {
                    break;
                }
                ok = row.at("resource")?.py_eq(source.at("resource")?) && row.at("item")?.py_eq(source.at("item")?);
            }
            require(ok, || format!("Authored creature original differs from its physical records: {identity}"))?;
            let mut indices = Vec::with_capacity(records.len());
            for row in &records {
                indices.push(row.at("record")?.at("record_index")?.clone());
            }
            let mut endpoint = crate::dict! {
                "kind" => "guarded-template", "creature_resource" => resource,
                "record_indices" => Value::List(indices), "payload" => record.clone(), "identity" => source_identity()?,
            };
            if source.at("authored")?.at("source")?.as_str() == Some("unshey.fl") {
                endpoint.insert("replacement", "flrbelt1");
                self.use_resource("itm:flrbelt1")?;
                self.use_resource("dlg:unshey")?;
            }
            self.sources.insert(identity, endpoint);
            self.use_resource(resource)?;
            self.use_resource(&item_resource)?;
            return Ok(());
        }
        let spell = spell_rewards::spell();
        if kind == "authored-source" && Some(resource) == spell.get("resource").and_then(Value::as_str) {
            let ok = identity == SPELL_SOURCE
                && record.py_eq(&spell_rewards::payload())
                && selected.at("resource_sha256")?.as_str().is_some_and(|hash| {
                    hash == spell_rewards::original_sha256() || hash == spell_rewards::patched_sha256()
                });
            require(ok, || format!("Deck of Many Things source differs from reviewed spell: {identity}"))?;
            let mut endpoint = crate::dict! {"kind" => "guarded-script", "adapter_id" => "spell-key-v1"};
            for key in ["quest", "resource", "selected_global", "consumed_global"] {
                endpoint.insert(key, entry(&spell, key)?.clone());
            }
            endpoint.insert("payload", record.clone());
            endpoint.insert("identity", source_identity()?);
            self.sources.insert(identity, endpoint);
            self.use_resource(resource)?;
            self.use_resource(&item_resource)?;
            self.resource_hashes.insert("SPIN621.SPL", spell_rewards::patched_sha256());
            return Ok(());
        }
        if kind == "authored-source" && mode2_rewards::source_rewards().contains_key(identity) {
            // The routes' items as the game names them (EET renames BG1's).
            let routes = self.routes()?;
            let spec = mode2_rewards::source_spec(identity, routes.naming())?;
            let ok = source.at("resource")?.py_eq(entry(&spec, "resource")?) && record.py_eq(entry(&spec, "payload")?);
            require(ok, || format!("The extended reward source differs from its route: {identity}"))?;
            let route = mode2_rewards::source_rewards().get(identity).map(crate::py::str_value).unwrap_or_default();
            self.require_route(&route)?;
            let mut endpoint = crate::dict! {"kind" => "guarded-script", "adapter_id" => "mode2-reward-v1"};
            for key in ["quest", "resource", "selected_global", "consumed_global"] {
                endpoint.insert(key, entry(&spec, key)?.clone());
            }
            endpoint.insert("payload", record.clone());
            endpoint.insert("identity", source_identity()?);
            self.sources.insert(identity, endpoint);
            for name in spec.get("required_transfers").map(list).unwrap_or(&[]) {
                let duplicate = mode2_rewards::transfer_spec(name.as_str().unwrap_or(""), routes.naming())?;
                let authored_ids = list(entry(&duplicate, "authored_source_ids")?);
                if !authored_ids.iter().any(|id| id.as_str() == Some(identity)) {
                    continue;
                }
                // The creature's own copies of the reward, wherever this game
                // keeps them; the runtime checks each record's bytes again.
                let resource = entry(&duplicate, "source_resource")?.as_str().unwrap_or("");
                let item = entry(&duplicate, "item")?.as_str().unwrap_or("");
                let creature = format!("{}.CRE", upper(tail(resource, 4)));
                let key = tuple_key(&[name_part(resource), name_part(item)]);
                let records: Vec<&'a Value> = self.template_records.get(&key).cloned().unwrap_or_default();
                let own = list(entry(&duplicate, "source_ids")?).iter().any(|id| id.as_str() == Some(identity));
                if records.is_empty() && !own {
                    continue; // A copy of another route's reward that this game does not have.
                }
                let quest = upper(entry(&duplicate, "quest")?.as_str().unwrap_or(""));
                require(!records.is_empty(), || {
                    format!(
                        "{creature} does not carry {}, which the reward of {quest}.DLG hands over: {identity}",
                        upper(item)
                    )
                })?;
                let resource_sha256 = self.use_resource(resource)?;
                let mut payloads = Vec::with_capacity(records.len());
                let mut indices = Vec::with_capacity(records.len());
                for row in &records {
                    let record = row.at("record")?;
                    let mut payload = Dict::new();
                    for key in ["resref", "expiration", "charges", "flags"] {
                        payload.insert(key, record.at(key)?.clone());
                    }
                    payloads.push(Value::Dict(payload));
                    indices.push(record.at("record_index")?.clone());
                }
                require(payloads.iter().all(|payload| payload.py_eq(&payloads[0])), || {
                    format!("{creature} carries {} in records that differ: {identity}", upper(item))
                })?;
                let mut auxiliary = crate::dict! {
                    "resource" => resource,
                    "resource_sha256" => resource_sha256,
                    "record_indices" => Value::List(indices),
                    "payload" => payloads.swap_remove(0),
                    "selected_global" => entry(&duplicate, "selected_global")?.clone(),
                };
                if let Some(replacement) = duplicate.get("replacement").filter(|replacement| replacement.truthy()) {
                    auxiliary.insert("replacement", replacement.clone());
                    self.use_resource(&format!("itm:{}", py_str(replacement)))?;
                }
                let endpoint = self.sources.get_mut(identity).and_then(Value::as_dict_mut).expect("inserted above");
                endpoint
                    .setdefault("auxiliary_creatures", Value::List(Vec::new()))
                    .as_list_mut()
                    .expect("a list")
                    .push(Value::Dict(auxiliary));
            }
            // All alternate quest routes share the same saved selection flag.
            // Their actual bytes are reviewed by patch_reward at export.
            let resources = list(entry(&spec, "resources")?);
            if resources.len() > 1 {
                let endpoint = self.sources.get_mut(identity).and_then(Value::as_dict_mut).expect("inserted above");
                endpoint.insert("resources", Value::List(resources.to_vec()));
            }
            for resource in resources {
                self.use_value(resource)?;
            }
            self.use_resource(&item_resource)?;
            return Ok(());
        }
        if kind == "authored-source" {
            let spec = quest_rewards::quest_spec(identity)?;
            let mut ok = source.at("resource")?.py_eq(entry(&spec, "resource")?)
                && source.at("item")?.py_eq(entry(&spec, "item")?);
            if ok {
                let quest = tail(entry(&spec, "resource")?.as_str().unwrap_or(""), 4);
                let change = quest_change(quest).ok_or_else(|| Error::key(quest))?;
                ok = applied(&self.game_root, change)?;
            }
            require(ok, || {
                format!("Chriz Item Randomiser did not prepare this quest reward; reinstall it, then rescan: {identity}")
            })?;
            let mut endpoint = crate::dict! {"kind" => "guarded-script", "adapter_id" => "original-quest-v1"};
            for key in ["quest", "resource", "selected_global", "consumed_global"] {
                endpoint.insert(key, entry(&spec, key)?.clone());
            }
            endpoint.insert("payload", record.clone());
            endpoint.insert("identity", source_identity()?);
            self.sources.insert(identity, endpoint);
            self.use_resource(resource)?;
            self.use_resource(&item_resource)?;
            return Ok(());
        }
        if kind == "virtual" {
            let endpoint = crate::dict! {"kind" => "virtual", "payload" => record.clone(), "identity" => source_identity()?};
            self.sources.insert(identity, endpoint);
            self.use_resource(resource)?;
            return Ok(());
        }
        if kind == "scripted" {
            let endpoint = crate::dict! {
                "kind" => "guarded-script", "adapter_id" => scripted_sources::ADAPTER_ID,
                "resource" => "BLPOOL", "selector" => scripted_sources::SELECTOR,
                "payload" => scripted_sources::payload(), "identity" => source_identity()?,
            };
            self.sources.insert(identity, endpoint);
            self.use_resource(scripted_sources::RESOURCE)?;
            self.use_resource(&item_resource)?;
            return Ok(());
        }
        if kind == "store" {
            let stock_option = match self.shield {
                Some(shield) => shield.at("source_id")?.py_eq(&Value::from(identity)),
                None => false,
            };
            let owner = source.at("owner")?;
            let item_value = source.at("item")?;
            let stock_rows =
                Self::count(&self.owner_items, &tuple_key(&[key_part(Some(owner)), key_part(Some(item_value))]));
            let ok = if stock_option { stock_rows <= 1 } else { stock_rows == 1 };
            require(ok, || format!("Native source shop holds another row of {}: {identity}", py_str(item_value)))?;
            let record_index = match (stock_option, self.shield) {
                (true, Some(shield)) => shield.at("record_index")?.clone(),
                _ => record.at("record_index")?.clone(),
            };
            let mut endpoint = crate::dict! {
                "kind" => "guarded-store", "store" => tail(resource, 4), "record_index" => record_index,
                "quantity" => selected.at("quantity")?.clone(), "payload" => payload(record)?,
                "identity" => source_identity()?,
            };
            if stock_option {
                endpoint.insert("stock_option", "more_spell_shield");
            }
            let shared_shield = match self.shield.filter(|shield| shield.truthy()) {
                Some(shield) => Value::from(resource).py_eq(shield.at("resource")?),
                None => false,
            };
            if Self::count(&self.selected_owners, resource) > 1 || shared_shield {
                endpoint.insert("multiple_stock", true);
            }
            self.sources.insert(identity, endpoint);
            self.use_resource(resource)?;
            self.use_resource(&item_resource)?;
            return Ok(());
        }
        let owner: &'a Value;
        if kind == "container" {
            let owner_id = source.at("owner")?;
            owner = owner_id
                .as_str()
                .and_then(|owner_id| self.index.target(self.world, owner_id))
                .ok_or_else(|| Error::key(&py_str(owner_id)))?;
            let selector = owner.at("selector")?;
            let area_name = area(source.at("resource")?)?;
            let name = script_name(Some(selector.at("name")?), identity)?;
            let unique = !selector.at("dynamic_item_table")?.truthy()
                && Self::count(&self.container_names, &self.container_key(source.get("resource"), &lower(&name), selector)?)
                    == 1;
            require(unique, || format!("Native source needs a unique static container name: {identity}"))?;
            let (x, y) = (selector.at("x")?, selector.at("y")?);
            require(in_coordinates(x) && in_coordinates(y), || {
                format!("Native container coordinates are unsupported: {identity}")
            })?;
            // The current native confirmation counts this resref in its owner.
            // An unselected duplicate must not prevent confirmation indefinitely.
            let copies = Self::count(&self.container_items, &tuple_key(&[key_part(Some(owner_id)), key_part(Some(source.at("item")?))]));
            require(copies == 1, || {
                format!("Native source container holds another copy of {}: {identity}", item_name)
            })?;
            let mut endpoint = crate::dict! {
                "kind" => "guarded-container", "area" => area_name, "script_name" => name.as_str(),
                "x" => x.clone(), "y" => y.clone(), "container_type" => selector.at("type")?.clone(),
                "payload" => payload(record)?,
                "identity" => identity_of(crate::dict! {"source" => selected.clone(), "owner" => owner.clone()})?,
            };
            let resref = match selected.get("replacement") {
                Some(replacement) => replacement.get("resref").cloned().unwrap_or(Value::from("blank")),
                None => Value::from("blank"),
            };
            if !resref.py_eq(&Value::from("blank")) {
                let replacement = text(selected.at("replacement")?, "resref")?;
                self.use_resource(&format!("itm:{replacement}"))?;
                endpoint.insert("replacement", replacement);
            }
            self.sources.insert(identity, endpoint);
        } else {
            if kind == "cre-template" {
                let actors: Vec<&'a Value> =
                    self.template_actors.get(&key_part(Some(&Value::from(resource)))).cloned().unwrap_or_default();
                let fallback = actors.len() != 1
                    || Self::count(&self.selected_owners, resource) > 1
                    || Self::count(
                        &self.actor_names,
                        &tuple_key(&[
                            key_part(actors[0].get("resource")),
                            name_part(&lower_name(actor_name(self.world, self.index, actors[0]))?),
                        ]),
                    ) != 1;
                if fallback {
                    let replacement = replacement_of(selected);
                    let slots = list(record.at("slots")?);
                    let ok = slots.len() == 1 && (in_inventory(&slots[0]) || replacement.is_some());
                    require(ok, || {
                        format!("Equipped creature source needs explicit replacement equipment (or blank): {identity}")
                    })?;
                    let mut endpoint = crate::dict! {
                        "kind" => "guarded-template", "creature_resource" => resource,
                        "payload" => payload(record)?, "identity" => source_identity()?,
                    };
                    if let Some(replacement) = replacement.filter(|replacement| replacement.truthy()) {
                        let resref = replacement.at("resref")?;
                        endpoint.insert("replacement", resref.clone());
                        if !resref.py_eq(&Value::from("blank")) {
                            let ok = self
                                .use_resource(&format!("itm:{}", py_str(resref)))?
                                .py_eq(replacement.at("resource_sha256")?);
                            require(ok, || format!("Replacement item changed: {identity}"))?;
                        }
                    }
                    self.sources.insert(identity, endpoint);
                    self.use_resource(resource)?;
                    self.use_resource(&item_resource)?;
                    return Ok(());
                }
                owner = actors[0];
            } else {
                let owner_id = source.at("owner")?;
                owner = owner_id
                    .as_str()
                    .and_then(|owner_id| self.index.target(self.world, owner_id))
                    .ok_or_else(|| Error::key(&py_str(owner_id)))?;
            }
            let selector = owner.at("selector")?;
            let area_name = area(owner.at("resource")?)?;
            let name = script_name(actor_name(self.world, self.index, owner), identity)?;
            let unique = Self::count(
                &self.actor_names,
                &tuple_key(&[key_part(owner.get("resource")), name_part(&lower(&name))]),
            ) == 1;
            require(unique, || {
                format!("Native creature source needs a unique actor script name in its area: {identity}")
            })?;
            let owner_id = py_str(owner.at("id")?);
            require(!self.selected_actors.contains(&owner_id), || {
                format!("Native export does not yet support two selected sources on one actor: {owner_id}")
            })?;
            self.selected_actors.insert(owner_id);
            let copies = Self::count(
                &self.creature_items,
                &tuple_key(&[key_part(Some(source.at("owner")?)), key_part(Some(source.at("item")?))]),
            );
            require(copies == 1, || {
                format!("Native source creature holds another copy of {}: {identity}", item_name)
            })?;
            let slots = list(record.at("slots")?);
            require(slots.len() == 1, || format!("Native creature source requires one inventory slot: {identity}"))?;
            let replacement = replacement_of(selected);
            require(in_inventory(&slots[0]) || replacement.is_some(), || {
                format!("Equipped creature source needs explicit replacement equipment (or blank): {identity}")
            })?;
            if let Some(replacement) = replacement.filter(|replacement| replacement.truthy()) {
                if !replacement.at("resref")?.py_eq(&Value::from("blank")) {
                    let resource = format!("itm:{}", py_str(replacement.at("resref")?));
                    let ok = self.use_resource(&resource)?.py_eq(replacement.at("resource_sha256")?);
                    require(ok, || format!("Replacement item changed or is absent: {identity}"))?;
                    self.use_resource(&resource)?;
                }
            }
            let mut endpoint = crate::dict! {
                "kind" => "guarded-creature", "area" => area_name, "script_name" => name,
                "actor_index" => selector.at("record_index")?.clone(), "creature_resource" => resource,
                "actor_owner" => owner.at("id")?.clone(), "payload" => payload(record)?,
                "identity" => identity_of(crate::dict! {"source" => selected.clone(), "owner" => owner.clone()})?,
            };
            if let Some(replacement) = replacement.filter(|replacement| replacement.truthy()) {
                endpoint.insert("replacement", replacement.at("resref")?.clone());
            }
            self.sources.insert(identity, endpoint);
            self.use_resource(text(owner, "resource")?)?;
        }
        self.use_resource(resource)?;
        self.use_resource(&item_resource)?;
        self.pristine.insert(text(owner, "resource")?.to_string());
        Ok(())
    }

    fn compile_variants(&mut self, rule: &Value) -> Result<()> {
        for name in rule.get("reward_variants").map(list).unwrap_or(&[]) {
            let name = py_str(name);
            require(self.items.contains(&name), || format!("Original reward variant is absent from the scan: {name}"))?;
            self.use_resource(&format!("itm:{name}"))?;
            self.selected_items.insert(name);
        }
        Ok(())
    }

    /// UDLESA's sword is an ordinary physical source. Its conversation must
    /// use the same kept/relocated decision without registering a second
    /// reward unit. So must the Sahuagin king's reward reply, which creates
    /// another treasury rod.
    fn compile_inventory_reward(&mut self, selected: &Value) -> Result<()> {
        let identity = text(selected, "id")?;
        let source = selected.at("record")?;
        let (resource, kind, item) = (text(source, "resource")?, text(source, "kind")?, text(source, "item")?);
        // Where nothing else gives the item, it moves as any other source.
        let Some(name) = mode2_rewards::physical_source_reward(resource, kind, item) else {
            return Ok(());
        };
        if !self.gives(name)? {
            return Ok(());
        }
        let reward = mode2_rewards::rewards().get(name).ok_or_else(|| Error::key(name))?;
        // Both records would set the one flag the dialogue reads.
        let key = tuple_key(&[name_part(resource), name_part(kind), name_part(item)]);
        let (owner_kind, owner) = resource.split_once(':').unwrap_or((resource, ""));
        let (file, item, quest) =
            (format!("{}.{}", upper(owner), upper(owner_kind)), upper(item), upper(text(reward, "quest")?));
        require(Self::count(&self.physical_items, &key) == 1, || {
            format!("{file} holds another {item} that the reward of {quest}.DLG could stand for: {identity}")
        })?;
        self.require_route(name)?;
        let endpoint = self
            .sources
            .get_mut(identity)
            .and_then(Value::as_dict_mut)
            .ok_or_else(|| Error::key(identity))?;
        endpoint.insert("reward_selected_global", reward.at("selected_global")?.clone());
        for resource in list(reward.at("resources")?) {
            self.use_value(resource)?;
        }
        Ok(())
    }

    fn compile_target(&mut self, selected: &'a Value) -> Result<()> {
        let mut target = selected.at("record")?;
        let identity = text(selected, "id")?;
        let target_identity = || identity_of(crate::dict! {"target" => selected.clone()});
        let authored = target.get("adapter_id").and_then(Value::as_str) == Some("original-target-v1");
        let container_quest = if authored { container_target_spec(target.at("selector")?)? } else { None };
        let ok = (authored || self.index.target(self.world, identity).is_some_and(|row| row.py_eq(target))) && {
            let resource = text(target, "resource")?;
            self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?)
                || (authored
                    && target
                        .at("selector")?
                        .at("id")?
                        .as_str()
                        .is_some_and(|id| mode2_rewards::target_rewards().contains_key(id))
                    && mode2_rewards::resource_rewards().contains_key(resource))
        };
        require(ok, || format!("Selected target differs from the scan: {identity}"))?;
        if authored {
            let selector = target.at("selector")?;
            self.use_resource(text(target, "resource")?)?;
            let selector_id = selector.at("id")?;
            let reward = match selector_id.as_str() {
                Some(id) if mode2_rewards::target_rewards().contains_key(id) => {
                    Some(mode2_rewards::target_spec(id, self.routes()?.naming())?)
                }
                _ => None,
            };
            if reward.as_ref().is_some_and(|reward| !reward.is_empty()) {
                let route = mode2_rewards::target_rewards().get(selector_id.as_str().unwrap_or("")).map(py_str).unwrap_or_default();
                self.require_route(&route)?;
            }
            let placement = reward
                .as_ref()
                .filter(|reward| !reward.is_empty())
                .and_then(|reward| reward.get("inventory_target"))
                .filter(|placement| placement.truthy());
            if let (Some(reward), Some(placement)) = (&reward, placement) {
                let creature_resource = text(placement, "creature_resource")?;
                self.readable(creature_resource)?;
                let placement_area = text(placement, "area")?;
                self.readable(&format!("are:{}", lower(placement_area)))?;
                let creature = self.index.target(self.world, creature_resource);
                let ok = match creature {
                    Some(creature) => {
                        lower(text(creature.at("selector")?, "script_name")?) == lower(text(placement, "script_name")?)
                            && !has_inventory_problem(creature)?
                    }
                    None => false,
                };
                require(ok, || format!("Original reward recipient changed: {identity}"))?;
                let mut resources: Vec<String> = list(entry(reward, "resources")?).iter().map(py_str).collect();
                resources.push(creature_resource.to_string());
                resources.push(format!("are:{}", lower(placement_area)));
                for resource in &resources {
                    self.use_resource(resource)?;
                }
                let endpoint = crate::dict! {
                    "kind" => "creature", "area" => upper(placement_area),
                    "script_name" => script_name(Some(placement.at("script_name")?), identity)?,
                    "inventory_reward" => true, "reward_resource" => entry(reward, "resource")?.clone(),
                    "identity" => identity_of(crate::dict! {
                        "target" => selected.clone(), "creature" => creature.cloned().unwrap_or(Value::Null),
                    })?,
                };
                self.targets.insert(identity, endpoint);
                return Ok(());
            }
            let target_kind = text(target, "kind")?;
            if matches!(target_kind, "group" | "creature-target") {
                let resources: Vec<&Value> = if target_kind == "group" {
                    list(selector.at("members")?).iter().collect()
                } else {
                    vec![selector.at("creature_resource")?]
                };
                let mut members = Vec::new();
                for resource in resources {
                    let resource = resource.as_str().unwrap_or("");
                    if !self.index.resources.contains(resource) || self.index.is_unreadable(resource) {
                        // Original group tables also filter absent CREs; one the
                        // scan could not read cannot be addressed either.
                        continue;
                    }
                    let creature = self.index.target(self.world, resource).ok_or_else(|| Error::key(resource))?;
                    require(!has_inventory_problem(creature)?, || {
                        format!("Original recipient has inventory diagnostics: {resource}")
                    })?;
                    let name = script_name(Some(creature.at("selector")?.at("script_name")?), identity)?;
                    self.use_resource(resource)?;
                    members.push(Value::Dict(crate::dict! {"resref" => tail(resource, 4), "script_name" => name}));
                }
                require(!members.is_empty(), || format!("Original recipient has no installed member: {identity}"))?;
                let mut endpoint = crate::dict! {
                    "kind" => if target_kind == "group" { "group" } else { "creature" },
                    "area" => area(target.at("resource")?)?, "identity" => target_identity()?,
                };
                if target_kind == "group" {
                    endpoint.insert("members", Value::List(members));
                } else {
                    endpoint.insert("script_name", members[0].at("script_name")?.clone());
                }
                self.targets.insert(identity, endpoint);
                return Ok(());
            }
            if target_kind == "scripted-target" {
                if let Some(id) = selector_id.as_str().filter(|id| mode2_rewards::target_rewards().contains_key(*id)) {
                    let spec = mode2_rewards::target_spec(id, self.routes()?.naming())?;
                    for resource in list(entry(&spec, "resources")?) {
                        self.use_value(resource)?;
                    }
                    if entry(&spec, "recipient_local")?.is_null() {
                        let recipient = entry(&spec, "recipient")?.as_str().unwrap_or("");
                        self.readable(recipient.split('/').next().unwrap_or(""))?;
                        let physical =
                            entry(&spec, "recipient")?.as_str().and_then(|recipient| self.index.target(self.world, recipient));
                        let ok = match physical {
                            Some(physical) => {
                                physical.at("kind")?.as_str() == Some("container")
                                    && physical.at("selector")?.at("trap_script")?.py_eq(entry(&spec, "quest")?)
                            }
                            None => false,
                        };
                        require(ok, || format!("The extended reward container changed: {identity}"))?;
                        let physical = physical.expect("checked above");
                        let selection = physical.at("selector")?;
                        self.use_resource(text(physical, "resource")?)?;
                        let endpoint = crate::dict! {
                            "kind" => "container", "area" => area(physical.at("resource")?)?,
                            "script_name" => script_name(Some(selection.at("name")?), identity)?,
                            "x" => selection.at("x")?.clone(), "y" => selection.at("y")?.clone(),
                            "container_type" => selection.at("type")?.clone(),
                            "claim_global" => entry(&spec, "event_global")?.clone(),
                            "reward_resource" => entry(&spec, "resource")?.clone(),
                            "identity" => identity_of(crate::dict! {"target" => selected.clone(), "container" => physical.clone()})?,
                        };
                        self.targets.insert(identity, endpoint);
                    } else {
                        let endpoint = crate::dict! {
                            "kind" => "quest", "area" => "*", "adapter_id" => "mode2-reward-v1",
                            "resource" => entry(&spec, "resource")?.clone(),
                            "event_global" => entry(&spec, "event_global")?.clone(),
                            "recipient_local" => entry(&spec, "recipient_local")?.clone(),
                            "identity" => target_identity()?,
                        };
                        self.targets.insert(identity, endpoint);
                    }
                    return Ok(());
                }
                let spell = spell_rewards::spell();
                if target.at("resource")?.py_eq(entry(&spell, "resource")?) {
                    let ok = identity == SPELL_TARGET
                        && selected.at("resource_sha256")?.as_str().is_some_and(|hash| {
                            hash == spell_rewards::original_sha256() || hash == spell_rewards::patched_sha256()
                        });
                    require(ok, || format!("Unreviewed spell recipient: {identity}"))?;
                    let endpoint = crate::dict! {
                        "kind" => "quest", "area" => "*", "resource" => entry(&spell, "resource")?.clone(),
                        "event_global" => entry(&spell, "event_global")?.clone(),
                        "recipient_local" => entry(&spell, "recipient_local")?.clone(),
                        "identity" => target_identity()?,
                    };
                    self.targets.insert(identity, endpoint);
                    self.resource_hashes.insert("SPIN621.SPL", spell_rewards::patched_sha256());
                    return Ok(());
                }
                let script = text(selector, "target")?;
                let name = script.strip_suffix(".bcs").unwrap_or(script);
                let quests = quest_rewards::quests();
                require(quests.contains_key(name), || format!("Unreviewed original quest recipient: {identity}"))?;
                let change = quest_change(name).ok_or_else(|| Error::key(name))?;
                require(applied(&self.game_root, change)?, || {
                    format!("Chriz Item Randomiser did not prepare this quest reward; reinstall it, then rescan: {identity}")
                })?;
                self.use_resource(&format!("bcs:{name}"))?;
                let endpoint = crate::dict! {
                    "kind" => "quest", "area" => area(target.at("resource")?)?, "resource" => format!("bcs:{name}"),
                    "event_global" => entry(quests, name)?.at("event_global")?.clone(), "identity" => target_identity()?,
                };
                self.targets.insert(identity, endpoint);
                return Ok(());
            }
            let mut physical = Vec::new();
            for key in list(selector.at("target_ids")?) {
                if let Some(row) = key.as_str().and_then(|key| self.index.target(self.world, key)) {
                    physical.push(row);
                }
            }
            if let Some(quest) = container_quest.as_ref().filter(|quest| !quest.is_empty()) {
                let quest_id = py_str(entry(quest, "target_id")?);
                if self.stored {
                    // Its area is unchanged (checked above): keep the
                    // container it was exported with, even where a better
                    // rule now finds another one (Samia's chest before the EE
                    // renaming).
                    let area = format!("are:{}", container_rewards::treasure_area(&quest_id, game_platform(self.world)?)?);
                    let ok = physical.len() == 1
                        && physical[0].at("kind")?.py_eq(&Value::from("container"))
                        && physical[0].at("resource")?.py_eq(&Value::from(area));
                    require(ok, || format!("Original container quest binding changed: {identity}"))?;
                } else {
                    // The container that holds the quest's treasure in this
                    // scan, found as the template binds it.
                    let chest = game_platform(self.world).and_then(|platform| {
                        container_rewards::treasure_container(&quest_id, rows(self.world, "sources"), platform)
                    });
                    let chest = match chest {
                        Ok(chest) => chest,
                        Err(error) if error.kind == ErrorKind::Profile => {
                            return Err(container_rewards::container_error(&quest_id, &error.message)?)
                        }
                        Err(error) => return Err(error),
                    };
                    let mut bound = Vec::new();
                    for target in &physical {
                        bound.push(target.at("id")?);
                    }
                    require(bound.len() == 1 && bound[0].py_eq(&Value::from(chest)), || {
                        format!("Original container quest binding changed: {identity}")
                    })?;
                }
                self.require_container(&quest_id)?;
                for resource in list(entry(quest, "resources")?) {
                    self.use_value(resource)?;
                }
            }
            require(physical.len() == 1, || format!("Original recipient needs one physical binding: {identity}"))?;
            target = physical[0];
        }
        let target_kind = text(target, "kind")?;
        if target_kind == "store" {
            let flags = int_like(target.at("selector")?.at("flags")?).unwrap_or(0);
            require(flags & 1 != 0, || {
                format!("Experimental native store target does not allow the player to buy: {identity}")
            })?;
            let resource = text(target, "resource")?;
            self.use_resource(resource)?;
            let endpoint = crate::dict! {"kind" => "store", "store" => tail(resource, 4), "identity" => target_identity()?};
            self.targets.insert(identity, endpoint);
            return Ok(());
        }
        if target_kind == "container" {
            let selector = target.at("selector")?;
            let name = script_name(Some(selector.at("name")?), identity)?;
            let unique = !selector.at("dynamic_item_table")?.truthy()
                && Self::count(&self.container_names, &self.container_key(target.get("resource"), &lower(&name), selector)?)
                    == 1;
            require(unique, || format!("Native target needs a unique static container name: {identity}"))?;
            let (x, y) = (selector.at("x")?, selector.at("y")?);
            require(in_coordinates(x) && in_coordinates(y), || {
                format!("Native container coordinates are unsupported: {identity}")
            })?;
            self.use_resource(text(target, "resource")?)?;
            let mut endpoint = crate::dict! {
                "kind" => "container", "area" => area(target.at("resource")?)?, "script_name" => name,
                "x" => x.clone(), "y" => y.clone(), "container_type" => selector.at("type")?.clone(),
                "identity" => target_identity()?,
            };
            if let Some(quest) = container_quest.as_ref().filter(|quest| !quest.is_empty()) {
                let mut latch = Dict::new();
                for key in ["activation_global", "latch_global", "latch_scope"] {
                    latch.insert(key, entry(quest, key)?.clone());
                }
                endpoint.insert("loot_latch", latch);
            }
            self.targets.insert(identity, endpoint);
            return Ok(());
        }
        require(target_kind == "actor", || {
            format!("Native export needs an ARE actor, container or store target: {identity}")
        })?;
        let name = script_name(actor_name(self.world, self.index, target), identity)?;
        let unique =
            Self::count(&self.actor_names, &tuple_key(&[key_part(target.get("resource")), name_part(&lower(&name))])) == 1;
        require(unique, || format!("Native target needs a unique actor script name in its area: {identity}"))?;
        let template = target.at("selector")?.get("template_resource");
        self.use_resource(text(target, "resource")?)?;
        if let Some(template) = template.filter(|template| template.truthy()) {
            self.use_value(template)?;
        }
        let template_row = template
            .and_then(Value::as_str)
            .and_then(|template| self.index.resource(self.world, template))
            .cloned()
            .unwrap_or(Value::Null);
        let endpoint = crate::dict! {
            "kind" => "creature", "area" => area(target.at("resource")?)?, "script_name" => name,
            "identity" => identity_of(crate::dict! {"target" => selected.clone(), "template" => template_row})?,
        };
        self.targets.insert(identity, endpoint);
        Ok(())
    }
}

/// Compile a campaign for the native runtime: (native fixture, required
/// resources, pristine resources).
///
/// Without `problems` any unsupported row stops the compile. With a dict,
/// each source or target row the runtime cannot use is recorded there by its
/// identity instead, and the other rows are still checked. Extended reward
/// routes are recognised in the game's bytes as the export recognises them
/// ([`crate::reward_routes`]); a reward resource changed since the scan stops
/// the compile.
///
/// A `stored` revision was exported before and belongs to running saves.
/// While the game's files are unchanged it keeps what it recorded: its
/// container quests' bindings, and its reward routes when CIR's newer check
/// finds a copy of their reward replies that cannot be gated (said in
/// `notes`).
#[allow(clippy::too_many_arguments)]
pub fn compile(
    manifest: &Value,
    world: &Value,
    index: &WorldIndex,
    items: &ItemIndex,
    catalog: &Value,
    mut problems: Option<&mut Dict>,
    stored: bool,
    notes: Option<&mut Vec<String>>,
) -> Result<(Value, BTreeSet<String>, BTreeSet<String>)> {
    // A rescan can discover unrelated resources (or a new resource type).
    // Compare the selected records and resource hashes below; existing native
    // revisions must still reproduce byte-for-byte when exported again.
    let revision = manifest.at("revision")?.clone();
    let manifest_sources = list(manifest.at("sources")?);
    let manifest_targets = list(manifest.at("targets")?);
    // Existing actor-only native artifacts are immutable. Their campaigns lack
    // pricing, so recompilation must retain the exact old native field set.
    let mut has_shop_pricing = manifest.get("shop_pricing").is_some();
    if !has_shop_pricing {
        for row in manifest_targets {
            if row.at("record")?.at("kind")?.as_str() == Some("store") {
                has_shop_pricing = true;
                break;
            }
        }
    }
    let shop_pricing =
        has_shop_pricing.then(|| manifest.get("shop_pricing").cloned().unwrap_or_else(default_shop_pricing));
    let mut selected_owners = HashMap::new();
    for selected in manifest_sources {
        let record = selected.at("record")?;
        if matches!(record.at("kind")?.as_str(), Some("cre-template" | "store")) {
            *selected_owners.entry(text(record, "resource")?.to_string()).or_insert(0) += 1;
        }
    }
    let shield = manifest.get("spell_shield").filter(|shield| !shield.is_null());
    let mut compiler = Compiler {
        world,
        index,
        items,
        catalog,
        game_root: text(world, "game_root")?.to_string(),
        shield,
        sources: Dict::new(),
        targets: Dict::new(),
        resource_hashes: Dict::new(),
        required: BTreeSet::new(),
        pristine: BTreeSet::new(),
        selected_items: BTreeSet::new(),
        selected_actors: HashSet::new(),
        selected_owners,
        container_names: HashMap::new(),
        actor_names: HashMap::new(),
        container_items: HashMap::new(),
        physical_items: HashMap::new(),
        template_records: HashMap::new(),
        creature_items: HashMap::new(),
        owner_items: HashMap::new(),
        template_actors: HashMap::new(),
        routes: None,
        stored,
        revision: py_str(&revision),
        notes: Vec::new(),
    };
    if let Some(shield) = shield {
        let expected = original_options::spell_shield_spec(world, shield.at("source_id")?.truthy())?;
        require(shield.py_eq(&expected), || "Spell Shield stock differs from the scan".into())?;
        compiler.use_value(shield.at("resource")?)?;
    }
    if let Some(disguise) = manifest.get("cursed_disguises") {
        cursed_disguises::validate_cursed_disguise_spec(disguise)?;
        for (name, digest) in disguise.at("resource_hashes")?.as_dict().into_iter().flat_map(Dict::iter) {
            let ok = compiler.use_resource(&format!("itm:{name}"))?.py_eq(digest);
            require(ok, || format!("Cursed item appearance resource changed: {name}"))?;
        }
    }

    for target in rows(world, "targets") {
        if target.at("kind")?.as_str() == Some("container") {
            let selector = target.at("selector")?;
            let name = lower_name(Some(selector.at("name")?))?;
            let key = compiler.container_key(Some(target.at("resource")?), &name, selector)?;
            *compiler.container_names.entry(key).or_insert(0) += 1;
        }
    }
    for target in rows(world, "targets") {
        if target.at("kind")?.as_str() == Some("actor") {
            let name = lower_name(actor_name(world, index, target))?;
            let key = tuple_key(&[key_part(Some(target.at("resource")?)), name_part(&name)]);
            *compiler.actor_names.entry(key).or_insert(0) += 1;
        }
    }
    for source in rows(world, "sources") {
        if source.at("kind")?.as_str() == Some("container") {
            let key = tuple_key(&[key_part(Some(source.at("owner")?)), key_part(Some(source.at("item")?))]);
            *compiler.container_items.entry(key).or_insert(0) += 1;
        }
    }
    for source in rows(world, "sources") {
        let key = tuple_key(&[
            key_part(Some(source.at("resource")?)),
            key_part(Some(source.at("kind")?)),
            key_part(Some(source.at("item")?)),
        ]);
        *compiler.physical_items.entry(key).or_insert(0) += 1;
    }
    // The records of each item in each creature file, in record order.
    let mut templates = Vec::new();
    for source in rows(world, "sources") {
        if source.at("kind")?.as_str() == Some("cre-template") {
            let index = int_like(source.at("record")?.at("record_index")?)
                .ok_or_else(|| Error::type_error("a record index is a number".to_string()))?;
            templates.push((index, source));
        }
    }
    templates.sort_by_key(|(index, _)| *index);
    for (_, source) in templates {
        let key = tuple_key(&[key_part(Some(source.at("resource")?)), key_part(Some(source.at("item")?))]);
        compiler.template_records.entry(key).or_default().push(source);
    }
    for source in rows(world, "sources") {
        if matches!(source.at("kind")?.as_str(), Some("cre-template" | "embedded-cre")) {
            let key = tuple_key(&[key_part(Some(source.at("owner")?)), key_part(Some(source.at("item")?))]);
            *compiler.creature_items.entry(key).or_insert(0) += 1;
        }
    }
    for source in rows(world, "sources") {
        let key = tuple_key(&[key_part(Some(source.at("owner")?)), key_part(Some(source.at("item")?))]);
        *compiler.owner_items.entry(key).or_insert(0) += 1;
    }
    for target in rows(world, "targets") {
        if target.get("kind").and_then(Value::as_str) == Some("actor") {
            let template = target.get("selector").and_then(|selector| selector.get("template_resource"));
            compiler.template_actors.entry(key_part(template)).or_default().push(target);
        }
    }

    for selected in manifest_sources {
        let identity = text(selected, "id")?;
        compiler.attempt(&mut problems, identity, |compiler| compiler.compile_source(selected))?;
    }
    let rules = manifest
        .get("randomization")
        .and_then(|randomization| randomization.get("sources"))
        .and_then(Value::as_dict);
    for (identity, rule) in rules.into_iter().flat_map(Dict::iter) {
        compiler.attempt(&mut problems, identity, |compiler| compiler.compile_variants(rule))?;
    }
    let mut shop_prices = Dict::new();
    if let Some(pricing) = &shop_pricing {
        let percent = int_like(pricing.at("percent")?).unwrap_or(0);
        let overrides = pricing.at("base_prices")?;
        for resref in &compiler.selected_items {
            let price = entry(compiler.items.row(catalog, resref).ok_or_else(|| Error::key(resref))?, "price")?;
            let base = int_like(overrides.get(resref).unwrap_or(price)).unwrap_or(0);
            let effective = (i128::from(base) * i128::from(percent)).div_euclid(100);
            require(effective <= i128::from(MAX_ITEM_PRICE), || {
                format!("Experimental native shop price exceeds 32-bit gold range: {resref}")
            })?;
            shop_prices.insert(resref.as_str(), Value::from(effective));
        }
    }
    for selected in manifest_sources {
        let identity = text(selected, "id")?;
        compiler.attempt(&mut problems, identity, |compiler| compiler.compile_inventory_reward(selected))?;
    }
    for selected in manifest_targets {
        let identity = text(selected, "id")?;
        compiler.attempt(&mut problems, identity, |compiler| compiler.compile_target(selected))?;
    }

    let mut guarded_ids = Vec::new();
    for selected in manifest_sources {
        if selected.at("record")?.at("kind")?.as_str() != Some("virtual") {
            guarded_ids.push(selected.at("id")?.clone());
        }
    }
    let source_gate = !guarded_ids.is_empty()
        || compiler.shield_truthy()
        || manifest.get("cursed_disguises").is_some();
    let options = manifest.get("original_options");
    let option = |name: &str| options.and_then(|options| options.get(name)).is_some_and(Value::truthy);
    if option("kangaxx") {
        for resource in ["dlg:hlskull", "bcs:crypt526", "bcs:crypt202"] {
            compiler.use_resource(resource)?;
        }
    }
    if option("flail_heads") {
        compiler.use_resource("dlg:kpglai01")?;
    }
    let mut native = crate::dict! {
        "schema" => "flir-native-fixture-v1", "revision" => revision,
        "sources" => compiler.sources, "targets" => compiler.targets, "resource_hashes" => compiler.resource_hashes,
        "ready_detail" => "have been assigned to the selected recipients.",
    };
    if let Some(pricing) = shop_pricing {
        native.insert("shop_pricing", pricing);
        native.insert("shop_prices", shop_prices);
    }
    if let Some(shield) = shield {
        native.insert("spell_shield", shield.clone());
    }
    if source_gate {
        native.insert(
            "source_gate",
            crate::dict! {"schema" => "flpr-unvisited-fixture-gate-v1", "source_ids" => Value::List(guarded_ids)},
        );
    }
    if let Some(notes) = notes {
        notes.append(&mut compiler.notes);
    }
    Ok((Value::Dict(native), compiler.required, compiler.pristine))
}

/// The first twelve characters of a revision, as notes name it.
fn short(revision: &str) -> String {
    revision.chars().take(12).collect()
}

/// Files to write, in Python's dict order (a repeated key keeps its first
/// position and takes the last bytes).
#[derive(Default)]
struct Pending {
    entries: Vec<(String, Vec<u8>)>,
    positions: HashMap<String, usize>,
}

impl Pending {
    fn insert(&mut self, key: impl Into<String>, data: Vec<u8>) {
        let key = key.into();
        match self.positions.get(&key) {
            Some(&position) => self.entries[position].1 = data,
            None => {
                self.positions.insert(key.clone(), self.entries.len());
                self.entries.push((key, data));
            }
        }
    }

    fn get(&self, key: &str) -> Option<&Vec<u8>> {
        self.positions.get(key).map(|&position| &self.entries[position].1)
    }

    fn contains(&self, key: &str) -> bool {
        self.positions.contains_key(key)
    }

    /// `del pending[key]`.
    fn remove(&mut self, key: &str) {
        if let Some(position) = self.positions.remove(key) {
            self.entries.remove(position);
            for index in self.positions.values_mut() {
                if *index > position {
                    *index -= 1;
                }
            }
        }
    }
}

/// What the runtime checks at every start and would stop for (the
/// reference's `_CHECKED`).
const CHECKED: &str = "the runtime checks it at every start and would stop for every game";

/// The native fixture a revision was exported with (`_stored_native`).
fn stored_native(exported: &Path, key: &str) -> Result<Value> {
    let path = exported.join("revisions").join(format!("{key}.native.json"));
    let data = read_bytes(&path)?;
    let text = std::str::from_utf8(&data).map_err(|error| Error::value(utf8_error_message(&data, &error)))?;
    // Path.read_text translates newlines before json.loads sees the text.
    parse_lenient(&text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// (resource, staged file) for each file a revision's guarded scripts need
/// in override (`_staged_scripts`).
///
/// At every start the runtime compares these staged bytes with the game's
/// override file (FLPRScript.lua).
fn staged_scripts(key: &str, manifest: &Value, native: &Value) -> Result<Vec<(String, String)>> {
    let mut result = Vec::new();
    let endpoints = native.at("sources")?;
    for (position, source) in list(manifest.at("sources")?).iter().enumerate() {
        let Some(endpoint) = endpoints.get(text(source, "id")?) else { continue };
        if endpoint.get("kind").and_then(Value::as_str) != Some("guarded-script") {
            continue;
        }
        let base = format!("campaign-store/revisions/{key}.source-{}", position + 1);
        match endpoint.get("resources").filter(|resources| resources.truthy()) {
            Some(resources) => {
                for (route, resource) in list(resources).iter().enumerate() {
                    let resource = py_str(resource);
                    let extension = resource.split(':').next().unwrap_or("").to_string();
                    result.push((resource, format!("{base}.route-{}.patched.{extension}", route + 1)));
                }
            }
            None => {
                let resource = py_str(source.at("record")?.at("resource")?);
                let extension = resource.split(':').next().unwrap_or("").to_string();
                result.push((resource, format!("{base}.patched.{extension}")));
            }
        }
    }
    Ok(result)
}

/// The areas whose shared pristine copy the runtime compares with a
/// revision's record (M_FLPR.lua; `_gate_areas`).
fn gate_areas(native: &Value) -> Result<BTreeSet<String>> {
    let gate = native.get("source_gate").filter(|gate| gate.truthy());
    let ids: Vec<Value> = match gate {
        None => Vec::new(),
        Some(gate) => match gate.get("source_ids").filter(|ids| ids.truthy()) {
            Some(ids) => list(ids).to_vec(),
            None => gate.get("source_id").filter(|id| id.truthy()).into_iter().cloned().collect(),
        },
    };
    let mut areas = BTreeSet::new();
    for identity in ids {
        let source = native.at("sources")?.at(&py_str(&identity))?;
        if matches!(source.at("kind")?.as_str(), Some("guarded-template" | "guarded-script" | "guarded-store")) {
            continue;
        }
        if let Some(area) = source.get("area").filter(|area| area.truthy()) {
            areas.insert(upper(&py_str(area)));
        }
    }
    Ok(areas)
}

/// `NAME.KIND` of a `kind:name` resource (`_filename`).
fn filename(resource: &str) -> String {
    let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
    format!("{}.{}", upper(name), upper(kind))
}

/// A file as the game has it, as Apply would leave it, or as a kept
/// revision needs it: absent, its bytes, or its recorded hash.
enum Held {
    Absent,
    Bytes(Vec<u8>),
    Hash(Value),
}

impl Held {
    /// Python's `==` between None, bytes and the recorded JSON value.
    fn same(&self, other: &Held) -> bool {
        match (self, other) {
            (Held::Absent, Held::Absent) => true,
            (Held::Bytes(a), Held::Bytes(b)) => a == b,
            (Held::Hash(a), Held::Hash(b)) => a.py_eq(b),
            _ => false,
        }
    }

    fn from_bytes(data: Option<&Vec<u8>>) -> Held {
        data.map_or(Held::Absent, |data| Held::Bytes(data.clone()))
    }
}

/// Every file below a directory, by its path relative to `relative`
/// (`path.rglob("*")` with `is_file()`).
pub(crate) fn files_below(directory: &Path, relative: &str, found: &mut BTreeSet<String>) -> Result<()> {
    let names = paths::list_dir(directory).map_err(|error| Error::win(&error, directory))?;
    for name in names {
        let path = directory.join(&name);
        let inner = format!("{relative}/{name}");
        if paths::is_dir(&path).map_err(|error| Error::win(&error, &path))? {
            files_below(&path, &inner, found)?;
        } else if paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
            found.insert(inner);
        }
    }
    Ok(())
}

/// `_export_tlk`: keep text references already used by saved, exported
/// configurations.
fn export_tlk(installed: Vec<u8>, prepared: &Path) -> Result<Vec<u8>> {
    if !paths::exists(prepared).map_err(|error| Error::win(&error, prepared))? {
        return Ok(installed);
    }
    let previous = read_bytes(prepared)?;
    if extends(&installed, &previous)? {
        return Ok(installed);
    }
    require(extends(&previous, &installed)?, || {
        "Game text changed since export; rescan into a fresh export directory".into()
    })?;
    Ok(previous)
}

fn extends(new: &[u8], old: &[u8]) -> Result<bool> {
    let a = Tlk::new(new.to_vec())?;
    let b = Tlk::new(old.to_vec())?;
    let table_end = 18 + b.count as usize * 26;
    let (a_offset, b_offset) = (a.offset as usize, b.offset as usize);
    Ok(a.count >= b.count
        && slice(new, 0, 10) == slice(old, 0, 10)
        && slice(new, 18, table_end) == slice(old, 18, table_end)
        && slice(new, a_offset, a_offset + old.len().saturating_sub(b_offset)) == slice(old, b_offset, old.len()))
}

/// `directory.glob("*.json")` on Windows: entry names ending in `.json` in
/// any case; nothing when the directory is absent or unreadable.
fn json_names(directory: &Path) -> Result<Vec<String>> {
    paths::glob_suffix(directory, ".json")
}

fn relative_path(relative: &str) -> PathBuf {
    relative.split('/').collect()
}

fn hashes_mut(native: &mut Value) -> &mut Dict {
    native
        .get_mut("resource_hashes")
        .and_then(Value::as_dict_mut)
        .expect("compiled native fixtures have resource hashes")
}

/// next.lua: the revision of each campaign start (read by M_FLPR.lua).
pub const NEXT_SCHEMA: &str = "flir-next-v2";

/// Export next-run settings while retaining already exported saved
/// revisions.
///
/// `starts` names the revision of each campaign start
/// (starts::write_starts) and `main` the start the engine makes without a
/// campaign row; next.lua then lists them. Without `starts`, next.lua names
/// `revision` (the store's current one by default), which the runtime reads
/// as a BG1 start.
///
/// A revision exported before that this export does not select belongs to
/// running saves; its files never change. When the game changed so that one
/// of its rows can no longer be prepared, or its files would now differ, it
/// keeps the files it was exported with and the result's "notes" say so; the
/// other revisions are exported as usual. The runtime checks every listed
/// revision at each start, so a kept revision whose guarded script or
/// guarded area changed in the game stops the export instead. A selected
/// revision must be prepared completely. `scans` holds the catalogues
/// already validated.
#[allow(clippy::too_many_arguments)]
pub fn export_native(
    store: &Path,
    catalog: &Value,
    world: &Value,
    output: &Path,
    revision: Option<&str>,
    starts: Option<&[(String, String)]>,
    main: Option<&str>,
    scans: Option<&Scans>,
) -> Result<Value> {
    let validated;
    let (items, index) = match scans {
        Some(scans) => scans.indexes(catalog, world)?,
        None => {
            let items = validate_catalog(catalog)?;
            validated = (items, validate_world(world, catalog)?);
            (&validated.0, &validated.1)
        }
    };
    let output = ensure_output_outside_game(output, catalog)?;
    let mut selected_starts: Vec<Value> = Vec::new();
    match starts {
        Some(starts) => {
            require(
                !starts.is_empty()
                    && revision.is_none()
                    && main.is_none_or(|main| starts.iter().any(|(start, _)| start == main)),
                || "Invalid campaign start revisions".into(),
            )?;
            for (_, key) in starts {
                selected_starts.push(read_campaign(store, Some(key))?);
            }
        }
        None => {
            require(main.is_none(), || "A main start needs campaign start revisions".into())?;
            selected_starts.push(read_campaign(store, revision)?);
        }
    }
    let exported = output.join("campaign-store");
    let mut manifests: BTreeMap<String, Value> = BTreeMap::new();
    for name in json_names(&exported.join("revisions"))? {
        let stem = paths::stem(&name);
        if is_hex256(stem) {
            let manifest = read_campaign(&exported, Some(stem))?;
            manifests.insert(stem.to_string(), manifest);
        }
    }
    // Without starts, the one selected revision.
    let selected_revision = py_str(selected_starts[0].at("revision")?);
    let mut selected_keys = BTreeSet::new();
    for manifest in selected_starts {
        let key = py_str(manifest.at("revision")?);
        selected_keys.insert(key.clone());
        manifests.insert(key, manifest);
    }
    let mut stored = BTreeSet::new();
    for key in manifests.keys() {
        let path = exported.join("revisions").join(format!("{key}.native.json"));
        if !selected_keys.contains(key) && paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
            stored.insert(key.clone());
        }
    }

    let mut pending = Pending::default();
    // The revision and kind of each pending Lua artifact, for the refused
    // rendering of its data it replaces.
    let mut lua_values: HashMap<String, (String, bool)> = HashMap::new();
    let mut required = BTreeSet::new();
    let mut pristine = BTreeSet::new();
    let mut compiled: BTreeMap<String, Value> = BTreeMap::new();
    let mut notes: Vec<String> = Vec::new();
    let mut kept: BTreeSet<String> = BTreeSet::new();
    for (key, manifest) in &manifests {
        let is_stored = stored.contains(key);
        let mut problems = Dict::new();
        let (native, resources, areas) = compile(
            manifest,
            world,
            index,
            items,
            catalog,
            is_stored.then_some(&mut problems),
            is_stored,
            Some(&mut notes),
        )?;
        compiled.insert(key.clone(), native);
        required.extend(resources);
        pristine.extend(areas);
        if !problems.is_empty() {
            // The other rows of its saves still need their prepared resources.
            kept.insert(key.clone());
            let mut rows: Vec<(String, String)> =
                problems.iter().map(|(identity, reason)| (identity.to_string(), py_str(reason))).collect();
            rows.sort();
            for (identity, reason) in rows {
                notes.push(format!(
                    "Saved games of revision {} keep their settings, but {identity} may no longer work in them: {reason}",
                    short(key)
                ));
            }
            continue;
        }
        let base = format!("campaign-store/revisions/{key}");
        pending.insert(format!("{base}.json"), canonical_bytes(manifest)?);
        pending.insert(format!("{base}.lua"), render_lua(manifest)?);
        lua_values.insert(format!("{base}.lua"), (key.clone(), false));
    }

    // The quest routes' items as the game names them (EET renames BG1's).
    let checked = if required.iter().any(|resource| mode2_rewards::resource_rewards().contains_key(resource)) {
        Some(reward_routes(catalog, world)?)
    } else {
        None
    };
    let naming = checked.as_deref().map_or_else(Naming::default, RewardRoutes::naming);
    // Reuse the scanner's resource reader; only selected resources are read.
    let game_root_text = text(catalog, "game_root")?.to_string();
    let game_root = PathBuf::from(&game_root_text);
    let types: Vec<(&str, u32)> = EXPORT_TYPES.iter().map(|extension| (*extension, resource_type(extension))).collect();
    let spell = spell_rewards::spell();
    let spell_resource = entry(&spell, "resource")?.as_str().unwrap_or("").to_string();
    let mut resource_bytes: BTreeMap<String, Vec<u8>> = BTreeMap::new();
    let mut journal: Option<Vec<u8>> = None;
    let (copies, routes, live) = GameResources::with(&game_root, &types, |reader| {
        // The EE journal table: EET installs it in override, BG:EE and BG2:EE
        // keep it in their archives.
        if reader.names("lua")?.iter().any(|name| name == "bgee") {
            journal = Some(reader.read("lua", "bgee")?);
        }
        for resource in &required {
            let Some((kind, name)) = resource.split_once(':') else {
                return Err(Error::value("not enough values to unpack (expected 2, got 1)"));
            };
            let data = reader.read(kind, name)?;
            let actual = sha256_hex(&data);
            let expected = py_str(index.resource(world, resource).ok_or_else(|| Error::key(resource))?.at("sha256")?);
            let mut own_patch = false;
            if actual != expected {
                let recovered = match resource.as_str() {
                    "dlg:hlskull" => Some(quest_options::original_dialogue("hlskull.dlg", &data)?),
                    "bcs:crypt526" => Some(quest_options::original_crypt("crypt526.bcs", &data)?),
                    "bcs:crypt202" => Some(quest_options::original_crypt("crypt202.bcs", &data)?),
                    _ => None,
                };
                if let Some(recovered) = recovered {
                    own_patch = sha256_hex(&recovered) == expected;
                }
                if *resource == spell_resource {
                    own_patch = expected == spell_rewards::original_sha256() && actual == spell_rewards::patched_sha256();
                }
                // A reward route is recognised again from these bytes below;
                // the scan holds nothing else that depends on them.
                if mode2_rewards::resource_rewards().contains_key(resource) {
                    own_patch = mode2_rewards::own_patch(resource, &data, naming)?;
                }
                if container_rewards::resource_targets().contains_key(resource) {
                    own_patch = container_rewards::own_patch(resource, &data)?;
                }
            }
            require(actual == expected || own_patch, || {
                format!("Installed resource changed; rescan before export: {resource}")
            })?;
            // The installer changed these files when it was installed; the
            // export only checks that they still carry its changes.
            match (kind, quest_change(name)) {
                ("bcs", Some(change)) => require_installed(&game_root_text, change, &data)?,
                _ if resource == "dlg:unshey" => require_installed(&game_root_text, "unshey", &data)?,
                _ if resource == "dlg:kpglai01" => require_installed(&game_root_text, "flail_heads", &data)?,
                _ => {}
            }
            if pristine.contains(resource) {
                pending.insert(format!("{}.pristine.are", upper(name)), data.clone());
            }
            resource_bytes.insert(resource.clone(), data);
        }
        // The quest routes' reward replies that other dialogues copy are gated
        // there too; the dialogues they read must be as scanned.
        let dialogues: HashSet<String> = reader.names("dlg")?.into_iter().collect();
        let mut scanned: HashMap<String, Value> = HashMap::new();
        for row in rows(world, "resources") {
            scanned.insert(py_str(row.at("id")?), row.at("sha256")?.clone());
        }
        let copies = RewardCopies::new(
            &mut |name| if dialogues.contains(name) { reader.read("dlg", name).map(Some) } else { Ok(None) },
            &scanned,
            naming,
        )?;
        let mut routes = BTreeSet::new();
        for resource in &required {
            if let Some(keys) = mode2_rewards::resource_rewards().get(resource).and_then(Value::as_list) {
                routes.extend(keys.iter().map(py_str));
            }
        }
        let routes: Vec<String> = routes.into_iter().collect();
        let checked: BTreeSet<String> = routes.iter().flat_map(|key| copies.checked(key)).collect();
        let changed: Vec<&String> = checked.iter().filter(|resource| copies.changed.contains(*resource)).collect();
        require(changed.is_empty(), || {
            format!("Installed resource changed; rescan before export: {}", changed.first().map_or("", |resource| resource.as_str()))
        })?;
        // What the runtime finds in override for the guarded scripts of the
        // revisions exported before; checked below for those that are kept.
        let mut live: HashMap<String, Option<Vec<u8>>> = HashMap::new();
        for key in &stored {
            for (resource, _) in staged_scripts(key, &manifests[key], &stored_native(&exported, key)?)? {
                let Some((kind, name)) = resource.split_once(':') else {
                    return Err(Error::value("not enough values to unpack (expected 2, got 1)"));
                };
                let found = if reader.names(kind)?.iter().any(|known| known == name) && reader.origin(kind, name)? == "override" {
                    Some(reader.read(kind, name)?)
                } else {
                    None
                };
                live.insert(resource.clone(), found);
            }
        }
        Ok((copies, routes, live))
    })?;

    let kangaxx = manifests.values().any(|manifest| {
        manifest
            .get("original_options")
            .and_then(|options| options.get("kangaxx"))
            .is_some_and(Value::truthy)
    });
    let cursed = manifests.values().any(|manifest| manifest.get("cursed_disguises").is_some());
    let text_rewards: Vec<&String> = required
        .iter()
        .filter(|resource| mode2_rewards::TEXT_REFS.iter().any(|(name, _)| name == resource))
        .collect();
    let container_text_resources: Vec<&String> = required
        .iter()
        .filter(|resource| container_rewards::TEXT_RESOURCES.contains(&resource.as_str()))
        .collect();
    let mut reward_texts = mode2_rewards::TextRefs::new();
    let mut container_texts = mode2_rewards::TextRefs::new();
    if kangaxx || cursed || !text_rewards.is_empty() || !container_text_resources.is_empty() {
        let language = text(catalog, "language")?;
        let (male, female) = text_files(&game_root, language)?;
        let folder: Vec<String> =
            if language == "default" { Vec::new() } else { vec!["lang".to_string(), language.to_string()] };
        let within = |name: String| -> Vec<String> { folder.iter().cloned().chain([name]).collect() };
        let tlk_parts = within("dialog.tlk".to_string());
        let tlk = export_tlk(read_bytes(&male)?, &tlk_parts.iter().fold(output.join("game-overrides"), |path, part| path.join(part)))?;
        // The language's dialogf.tlk gets every appended text under the same
        // number. Where it holds another number of strings than dialog.tlk, the
        // configuration check left the reward text routes out, and other
        // appended texts leave it as it is.
        let reason = unpaired(&male, female.as_deref())?;
        require(!(reason.is_some() && (!text_rewards.is_empty() || !container_text_resources.is_empty())), || {
            format!("Item Randomiser's reward texts cannot be prepared: {}", reason.as_deref().unwrap_or_default())
        })?;
        let female = female.filter(|_| reason.is_none());
        let female_parts = female.as_ref().map(|path| {
            within(path.file_name().map_or_else(String::new, |name| name.to_string_lossy().into_owned()))
        });
        let female_tlk = match (&female, &female_parts) {
            (Some(path), Some(parts)) => Some(export_tlk(
                read_bytes(path)?,
                &parts.iter().fold(output.join("game-overrides"), |path, part| path.join(part)),
            )?),
            _ => None,
        };
        let mut game_text = paired(tlk, female_tlk).map_err(|error| match error.kind {
            ErrorKind::GameText => Error::profile("Game text changed since export; rescan into a fresh export directory"),
            _ => error,
        })?;
        require(journal.is_some(), || "The game has no BGEE.LUA journal table".into())?;
        let mut journal = journal.take().expect("checked");
        let named: BTreeMap<String, Vec<u8>> = resource_bytes
            .iter()
            .map(|(resource, data)| (format!("{}.{}", tail(resource, 4), resource.get(..3).unwrap_or("")), data.clone()))
            .collect();
        let (patches, updated) = quest_options::prepare_quest_options(&named, &game_text.male, kangaxx, language)?;
        game_text = paired(updated, game_text.female)?;
        if kangaxx {
            let dialogue = patches
                .iter()
                .find(|(name, _)| name == "hlskull.dlg")
                .map(|(_, data)| data)
                .ok_or_else(|| Error::key("hlskull.dlg"))?;
            journal = quest_options::patch_kangaxx_journal(&journal, dialogue)?;
        }
        if !text_rewards.is_empty() {
            let (updated, refs) = mode2_rewards::prepare_reward_texts(&game_text, text_rewards.iter(), language)?;
            game_text = updated;
            reward_texts = refs;
        }
        if !container_text_resources.is_empty() {
            let (updated, refs) =
                container_rewards::prepare_container_texts(&game_text, container_text_resources.iter(), language)?;
            game_text = updated;
            container_texts = refs;
        }
        for (revision, manifest) in &manifests {
            let Some(spec) = manifest.get("cursed_disguises") else {
                continue;
            };
            let itms: BTreeMap<String, Vec<u8>> = resource_bytes
                .iter()
                .filter(|(name, _)| name.starts_with("itm:"))
                .map(|(name, data)| (tail(name, 4).to_string(), data.clone()))
                .collect();
            let (fixture, assets, updated) =
                cursed_disguises::prepare_cursed_disguises(spec, &itms, &game_text.male, language)?;
            game_text = paired(updated, game_text.female)?;
            if let Some(native) = compiled.get_mut(revision).and_then(Value::as_dict_mut) {
                native.insert("cursed_disguises", fixture);
            }
            for (name, data) in assets {
                pending.insert(format!("campaign-store/revisions/{revision}.cursed/{name}"), data);
            }
        }
        pending.insert("resource-overrides/BGEE.LUA", journal);
        pending.insert(format!("game-overrides/{}", tlk_parts.join("/")), game_text.male);
        if let (Some(parts), Some(female)) = (female_parts, game_text.female) {
            pending.insert(format!("game-overrides/{}", parts.join("/")), female);
        }
        for (name, data) in &patches {
            pending.insert(format!("resource-overrides/{}", upper(name)), data.clone());
        }
        for native in compiled.values_mut() {
            let hashes = hashes_mut(native);
            for (name, data) in &patches {
                let key = upper(name);
                if hashes.contains_key(&key) {
                    hashes.insert(key, sha256_hex(data));
                }
            }
        }
    }

    // Each reward route patch reads campaign flags. Prepare it once,
    // retaining the same bytes for every revision that shares the resource.
    let mut mode2_patches: HashMap<String, Vec<u8>> = HashMap::new();
    for resource in mode2_rewards::resource_rewards().keys() {
        if !required.contains(resource) {
            continue;
        }
        let data = resource_bytes.get(resource).ok_or_else(|| Error::key(resource))?;
        let patched = mode2_rewards::patch_reward(resource, data, reward_texts.get(resource), None, naming)?;
        let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
        let filename = format!("{}.{}", upper(name), upper(kind));
        pending.insert(format!("resource-overrides/{filename}"), patched.clone());
        for native in compiled.values_mut() {
            let hashes = hashes_mut(native);
            if hashes.contains_key(&filename) {
                hashes.insert(filename.as_str(), sha256_hex(&patched));
            }
        }
        mode2_patches.insert(resource.to_string(), patched);
    }
    for (resource, patched) in copies.patches(&routes)? {
        let filename = format!("{}.DLG", upper(tail(&resource, 4)));
        let relative = format!("resource-overrides/{filename}");
        require(!pending.contains(&relative), || format!("Two reward changes would replace the same dialogue: {filename}"))?;
        pending.insert(relative, patched);
    }
    for resource in required.iter().filter(|resource| container_rewards::resource_targets().contains_key(resource)) {
        let data = resource_bytes.get(resource).ok_or_else(|| Error::key(resource))?;
        let patched = container_rewards::patch_container_resource(resource, data, container_texts.get(resource))?;
        let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
        let filename = format!("{}.{}", upper(name), upper(kind));
        pending.insert(format!("resource-overrides/{filename}"), patched.clone());
        for native in compiled.values_mut() {
            let hashes = hashes_mut(native);
            if hashes.contains_key(&filename) {
                hashes.insert(filename.as_str(), sha256_hex(&patched));
            }
        }
    }

    let bytes_of = |resource: &str| resource_bytes.get(resource).ok_or_else(|| Error::key(resource));
    for (key, manifest) in &manifests {
        if kept.contains(key) {
            continue;
        }
        if let Some(shield) = manifest.get("spell_shield").filter(|shield| shield.truthy()) {
            let prefix = format!("campaign-store/revisions/{key}.spell-shield");
            let original = bytes_of(text(shield, "resource")?)?;
            let effective = original_options::patch_spell_shield_stock(original)?;
            pending.insert(format!("{prefix}.original.sto"), original.clone());
            pending.insert(format!("{prefix}.effective.sto"), effective);
        }
        let native = compiled.get_mut(key).expect("compiled above");
        let targets = native.get_mut("targets").and_then(Value::as_dict_mut).expect("compiled targets");
        for (_, endpoint) in targets.iter_mut() {
            let endpoint = endpoint.as_dict_mut().expect("compiled endpoints are dicts");
            let mode2_resource = if endpoint.get("adapter_id").and_then(Value::as_str) == Some("mode2-reward-v1") {
                endpoint.get("resource")
            } else {
                endpoint.get("reward_resource")
            }
            .filter(|resource| resource.truthy())
            .cloned();
            if let Some(resource) = &mode2_resource {
                require(mode2_patches.contains_key(&py_str(resource)), || "The reward route's recipient patch is missing".into())?;
            }
            let endpoint_kind = endpoint.get("kind").and_then(Value::as_str).unwrap_or("").to_string();
            if endpoint_kind == "quest" && mode2_resource.is_none() {
                // An original quest's script was prepared by the installer;
                // the original grant has been staged above.
                let resource = py_str(entry(endpoint, "resource")?);
                if resource == spell_resource {
                    pending.insert("resource-overrides/SPIN621.SPL", spell_rewards::patch_key_spell(bytes_of(&resource)?)?);
                }
            }
            if endpoint_kind == "group" {
                let members: Vec<Value> = list(entry(endpoint, "members")?).to_vec();
                let mut creatures: BTreeMap<String, &[u8]> = BTreeMap::new();
                for member in &members {
                    let resref = py_str(member.at("resref")?);
                    let data = bytes_of(&format!("cre:{resref}"))?;
                    creatures.insert(resref, data.as_slice());
                }
                let mut reward_items: BTreeSet<String> = BTreeSet::new();
                for selected in list(manifest.at("sources")?) {
                    reward_items.insert(py_str(selected.at("record")?.at("item")?));
                }
                let rules = manifest
                    .get("randomization")
                    .and_then(|randomization| randomization.get("sources"))
                    .and_then(Value::as_dict);
                for rule in rules.into_iter().flat_map(Dict::values) {
                    reward_items.extend(rule.get("reward_variants").map(list).unwrap_or(&[]).iter().map(py_str));
                }
                let mut eligible = Dict::new();
                for item in &reward_items {
                    let preferred = group_recipients::preferred_members(bytes_of(&format!("itm:{item}"))?, &creatures);
                    let mut numbers = Vec::new();
                    for (number, member) in members.iter().enumerate() {
                        if preferred.contains(&py_str(member.at("resref")?)) {
                            numbers.push(Value::from(number + 1));
                        }
                    }
                    eligible.insert(item.as_str(), Value::List(numbers));
                }
                endpoint.insert("eligible", eligible);
            }
        }
        let native = &compiled[key];
        let base = format!("campaign-store/revisions/{key}");
        pending.insert(format!("{base}.native.json"), canonical_bytes(native)?);
        pending.insert(format!("{base}.native.lua"), render_lua(native)?);
        lua_values.insert(format!("{base}.native.lua"), (key.clone(), true));
        let compiled_sources = native.at("sources")?;
        for (position, selected_source) in list(manifest.at("sources")?).iter().enumerate() {
            let source = selected_source.at("record")?;
            let compiled_source = compiled_sources.at(text(selected_source, "id")?)?;
            let base = format!("campaign-store/revisions/{key}.source-{}", position + 1);
            for (number, auxiliary) in compiled_source.get("auxiliary_creatures").map(list).unwrap_or(&[]).iter().enumerate() {
                pending.insert(format!("{base}.duplicate-{}.original.cre", number + 1), bytes_of(text(auxiliary, "resource")?)?.clone());
            }
            let resource = text(source, "resource")?;
            match text(compiled_source, "kind")? {
                "guarded-script" => {
                    let raw = bytes_of(resource)?;
                    match text(compiled_source, "adapter_id")? {
                        "mode2-reward-v1" => {
                            let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
                            let patched = mode2_patches.get(resource).ok_or_else(|| Error::key(resource))?.clone();
                            pending.insert(format!("{base}.patched.{kind}"), patched.clone());
                            for (number, route) in compiled_source.get("resources").map(list).unwrap_or(&[]).iter().enumerate() {
                                let route = py_str(route);
                                let extension = route.split(':').next().unwrap_or("");
                                let data = mode2_patches.get(&route).ok_or_else(|| Error::key(&route))?.clone();
                                pending.insert(format!("{base}.route-{}.patched.{extension}", number + 1), data);
                            }
                            pending.insert(format!("resource-overrides/{}.{}", upper(name), upper(kind)), patched);
                        }
                        "spell-key-v1" => {
                            let patched = spell_rewards::patch_key_spell(raw)?;
                            pending.insert(format!("{base}.patched.spl"), patched.clone());
                            pending.insert("resource-overrides/SPIN621.SPL", patched);
                        }
                        // The installer's script: the runtime checks that
                        // the game still has exactly these bytes.
                        _ => pending.insert(format!("{base}.patched.bcs"), raw.clone()),
                    }
                }
                "guarded-store" => {
                    let mut original = bytes_of(resource)?.clone();
                    if compiled_source.get("stock_option").and_then(Value::as_str) == Some("more_spell_shield") {
                        original = original_options::patch_spell_shield_stock(&original)?;
                    }
                    let removals = BTreeSet::from([int_like(compiled_source.at("record_index")?).unwrap_or(-1)]);
                    let removed = store_inventory::rewrite_inventory(&original, &removals)?;
                    pending.insert(format!("{base}.original.sto"), original);
                    pending.insert(format!("{base}.removed.sto"), removed);
                }
                "guarded-template" => pending.insert(format!("{base}.original.cre"), bytes_of(resource)?.clone()),
                "guarded-creature" => {
                    let original = if text(source, "kind")? == "cre-template" {
                        bytes_of(resource)?.clone()
                    } else {
                        let owner = text(compiled_source, "actor_owner")?;
                        let selector = index.target(world, owner).ok_or_else(|| Error::key(owner))?.at("selector")?;
                        let offset = int_like(selector.at("embedded_cre_offset")?).unwrap_or(0).max(0) as usize;
                        let size = int_like(selector.at("embedded_cre_size")?).unwrap_or(0).max(0) as usize;
                        slice(bytes_of(resource)?, offset, offset + size).to_vec()
                    };
                    let replacement = match selected_source.get("replacement") {
                        Some(replacement) => replacement.get("resref").map(py_str).unwrap_or_else(|| "blank".into()),
                        None => "blank".into(),
                    };
                    let record_index = int_like(source.at("record")?.at("record_index")?).unwrap_or(-1);
                    let edit = (replacement != "blank").then_some(replacement.as_str());
                    let removed = creature_inventory::rewrite_inventory(&original, &[(record_index, edit)])?;
                    pending.insert(format!("{base}.original.cre"), original);
                    pending.insert(format!("{base}.removed.cre"), removed);
                }
                _ => {}
            }
        }
    }
    // A refused Lua rendering holds the same data as the current one.
    let refused = |relative: &str| -> Result<Option<Vec<u8>>> {
        match lua_values.get(relative) {
            Some((key, true)) => refused_lua(&compiled[key]),
            Some((key, false)) => refused_lua(&manifests[key]),
            None => Ok(None),
        }
    };
    // Where the game changed so that a revision exported earlier would now
    // get other files, its saves keep the files they were exported with.
    let candidates: Vec<String> = stored.iter().filter(|key| !kept.contains(*key)).cloned().collect();
    for key in candidates {
        let prefix = format!("campaign-store/revisions/{key}.");
        let own: Vec<String> =
            pending.entries.iter().map(|(relative, _)| relative).filter(|relative| relative.starts_with(&prefix)).cloned().collect();
        let mut differs = false;
        for relative in &own {
            let path = store_path(&output, &relative_path(relative))?;
            if !paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
                continue;
            }
            let existing = read_bytes(&path)?;
            if Some(&existing) != pending.get(relative) && refused(relative)?.as_ref() != Some(&existing) {
                differs = true;
                break;
            }
        }
        if !differs {
            continue;
        }
        kept.insert(key.clone());
        for relative in &own {
            pending.remove(relative);
        }
        let before = stored_native(&exported, &key)?;
        let empty = Dict::new();
        let old = match before.get("resource_hashes") {
            None => &empty,
            Some(hashes) => hashes.as_dict().ok_or_else(|| Error::attribute(hashes.type_name(), "get"))?,
        };
        let new = compiled[&key].at("resource_hashes")?.as_dict().ok_or_else(|| Error::type_error("resource hashes"))?;
        let names: BTreeSet<&str> = old.keys().chain(new.keys()).collect();
        let changed: Vec<&str> = names
            .into_iter()
            .filter(|name| {
                // `dict.get`: a missing hash and a null one are both None.
                let (a, b) = (old.get(name).filter(|a| !a.is_null()), new.get(name).filter(|b| !b.is_null()));
                match (a, b) {
                    (Some(a), Some(b)) => !a.py_eq(b),
                    (None, None) => false,
                    _ => true,
                }
            })
            .collect();
        let changed = if changed.is_empty() { "game files they use".to_string() } else { changed.join(", ") };
        notes.push(format!(
            "Saved games of revision {} keep the settings exported earlier, although {changed} changed since then",
            short(&key)
        ));
    }
    // At every start the runtime checks each listed revision: the staged
    // bytes of its guarded scripts against the game's override files, and the
    // area each guarded area source recorded against the shared pristine
    // copy. A kept revision that would fail there stops the runtime for
    // every game.
    for key in &kept {
        let before = stored_native(&exported, key)?;
        // (file, as the game has it, as Apply would leave it, as the revision needs it)
        let mut needed: Vec<(String, Held, Held, Held)> = Vec::new();
        for (resource, staged) in staged_scripts(key, &manifests[key], &before)? {
            let name = filename(&resource);
            let current = live.get(&resource).and_then(Option::as_ref);
            let installed = pending.get(&format!("resource-overrides/{name}")).or(current);
            let expected = read_bytes(&store_path(&output, &relative_path(&staged))?)?;
            needed.push((name, Held::from_bytes(current), Held::from_bytes(installed), Held::Bytes(expected)));
        }
        for area in gate_areas(&before)? {
            // The pristine copy is the game's area as Apply prepares it now.
            let pristine_name = format!("{area}.pristine.are");
            let mut copy = pending.get(&pristine_name).cloned();
            let pristine_path = store_path(&output, Path::new(&pristine_name))?;
            if copy.is_none() && paths::is_file(&pristine_path).map_err(|error| Error::win(&error, &pristine_path))? {
                copy = Some(read_bytes(&pristine_path)?);
            }
            // `copy and sha256(copy)`: an empty copy stays bytes, equal to no hash.
            let digest = || match &copy {
                None => Held::Absent,
                Some(data) if data.is_empty() => Held::Bytes(Vec::new()),
                Some(data) => Held::Hash(Value::from(sha256_hex(data))),
            };
            let recorded = before.at("resource_hashes")?.get(&format!("{area}.ARE")).filter(|hash| !hash.is_null());
            let expected = recorded.map_or(Held::Absent, |hash| Held::Hash(hash.clone()));
            needed.push((format!("{area}.ARE"), digest(), digest(), expected));
        }
        for (name, current, installed, expected) in needed {
            let change = if !current.same(&expected) {
                format!("it changed in the game since then; {CHECKED}. Restore {name} before applying")
            } else {
                format!("Apply would now replace it with another version; {CHECKED}")
            };
            require(installed.same(&expected), || {
                format!("Saved games of revision {} need {name} as it was when they were exported, but {change}", short(key))
            })?;
        }
    }

    for (relative, data) in &pending.entries {
        let path = store_path(&output, &relative_path(relative))?;
        if relative.starts_with("campaign-store/") {
            write_immutable_replacing(&path, data, Some(&|| refused(relative)))?;
        } else {
            // These are the prepared common resources for all exported
            // revisions; adding a compatible quest branch or TLK entry can
            // update them without altering any campaign's assignments.
            make_dirs(&paths::parent(&path))?;
            write_bytes(&path, data)?;
        }
    }
    let revisions: Vec<String> = manifests.keys().cloned().collect();
    let mut listed: BTreeSet<String> = pending.entries.iter().map(|(relative, _)| relative.clone()).collect();
    for key in &kept {
        // Its files stay as they were exported: list them, and read its gate.
        compiled.insert(key.clone(), stored_native(&exported, key)?);
        let directory = exported.join("revisions");
        let names = paths::list_dir(&directory).map_err(|error| Error::win(&error, &directory))?;
        for name in names.into_iter().filter(|name| name.starts_with(&format!("{key}."))) {
            let path = directory.join(&name);
            let relative = format!("campaign-store/revisions/{name}");
            if paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
                listed.insert(relative);
            } else if paths::is_dir(&path).map_err(|error| Error::win(&error, &path))? {
                files_below(&path, &relative, &mut listed)?;
            }
        }
    }
    let guarded: Vec<Value> = revisions
        .iter()
        .filter(|key| {
            let native = &compiled[key.as_str()];
            native.get("source_gate").is_some() || native.get("spell_shield").is_some()
        })
        .map(Value::from)
        .collect();
    let gate_path = store_path(&output, Path::new("source-gate.lua"))?;
    write_bytes(&gate_path, &render_lua(&Value::List(guarded))?)?;
    // dict(sorted(starts.items())).
    let sorted_starts = starts.map(|starts| {
        let mut sorted = starts.to_vec();
        sorted.sort();
        let mut dict = Dict::new();
        for (start, key) in sorted {
            dict.insert(start, key);
        }
        Value::Dict(dict)
    });
    let pointer = match &sorted_starts {
        None => Value::from(selected_revision.as_str()),
        Some(sorted) => {
            let mut pointer = crate::dict! {"schema" => NEXT_SCHEMA, "starts" => sorted.clone()};
            if let Some(main) = main {
                pointer.insert("main", main);
            }
            Value::Dict(pointer)
        }
    };
    let next_path = store_path(&output, Path::new("next.lua"))?;
    write_bytes(&next_path, &render_lua(&pointer)?)?;
    let mut files = listed;
    files.insert("source-gate.lua".into());
    files.insert("next.lua".into());
    let files_path = store_path(&output, Path::new(EXPORT_FILES))?;
    let listing = crate::dict! {"schema" => EXPORT_FILES_SCHEMA, "files" => Value::from(files.into_iter().collect::<Vec<_>>())};
    write_bytes(&files_path, &canonical_bytes(&Value::Dict(listing))?)?;
    let mut result = crate::dict! {
        "revisions" => Value::from(revisions), "config_path" => paths::display(&output),
        "state" => "experimental-native-config",
    };
    if !notes.is_empty() {
        let notes: BTreeSet<String> = notes.into_iter().collect();
        result.insert("notes", Value::from(notes.into_iter().collect::<Vec<_>>()));
    }
    match sorted_starts {
        None => result.insert("revision", selected_revision),
        Some(sorted) => result.insert("starts", sorted),
    };
    Ok(Value::Dict(result))
}
