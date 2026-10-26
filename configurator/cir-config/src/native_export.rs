//! Export reviewed physical and BLPOOL sources for the experimental native
//! runtime (the reference's `native_export.py`).
//!
//! This writes configuration outside the game. It does not install runtime
//! hooks, change resources, choose gameplay content, or establish live target
//! eligibility.

use crate::activation::{EXPORT_FILES, EXPORT_FILES_SCHEMA};
use crate::campaigns::{read_campaign, refused_lua, render_lua, store_path, write_immutable_replacing};
use crate::duplicates::{holdings, Holdings};
use crate::error::{Error, ErrorKind, Result, CHANGED_SINCE_SCAN};
use crate::game_text::{paired, text_files, unpaired};
use crate::json::{parse_lenient, py_str, utf8_error_message, Dict, Value};
use crate::mode2_rewards::Naming;
use crate::original_catalog::{chapter3_place, container_target_spec, mode2_route_source, mode2_route_target};
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
    chapter3, container_rewards, creature_inventory, cursed_disguises, group_recipients, mode2_rewards,
    original_options, paths, quest_options, quest_rewards, scripted_sources, spell_rewards, store_inventory,
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
    /// The resources that the campaign's reward routes patch.
    routed: BTreeSet<String>,
    /// The quest scripts it needs as CIR's installer prepared them.
    quests: BTreeSet<String>,
    selected_items: BTreeSet<String>,
    selected_actors: HashSet<String>,
    selected_owners: HashMap<String, usize>,
    /// Item Randomiser's second copies of the moved rewards, when the
    /// campaign names them.
    duplicates: Option<Vec<Value>>,
    container_names: HashMap<String, usize>,
    actor_names: HashMap<String, usize>,
    /// The actors of each area by script name and creature file.
    actor_templates: HashMap<String, usize>,
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
    /// The campaign's sources and targets, and its randomization rules.
    manifest_sources: &'a [Value],
    manifest_targets: &'a [Value],
    randomization: Option<&'a Value>,
    /// The Sahuagin treasury's place: its container and script (`require_treasury_names`).
    treasuries: Vec<(String, &'a Value, Value)>,
    /// The row of each special place that can have only one (`special_places::unique`).
    unique_places: HashMap<String, String>,
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

    /// `require_treasury_names`: the treasury's script runs with every AI
    /// update of its container, trapped or not (2.7.3.0), and destroys the
    /// king's reward items by name once his job is done: a reward of such a
    /// name is safe there only as the source whose move switches that destroy off
    /// (`gate_destroys`). The king's handover takes a reward by its name: every
    /// other item that can lie in the treasury under that name, its own or
    /// another reward, must be an equal copy, or it could not tell which one to
    /// take. A randomization puts each reward only into places of its tier
    /// (FLPRCore), with overflow several into one place; without one each
    /// reward has a place of its own. Run once every place is compiled.
    fn require_treasury_names(&mut self, identity: &str, treasury: &Value, script: &Value) -> Result<()> {
        let script = script.as_str().unwrap_or("");
        let resource = format!("bcs:{}", lower(script));
        let routes = self.routes()?;
        let destroyed = routes.destroyed(&resource)?;
        let mut gates: HashMap<String, Value> = HashMap::new();
        if mode2_rewards::destroy_gate(&resource) {
            for spec in mode2_rewards::resource_specs(&resource, routes.naming())? {
                gates.insert(lower(entry(&spec, "item")?.as_str().unwrap_or("")), entry(&spec, "selected_global")?.clone());
            }
        }
        let treasury_id = treasury.at("id")?;
        let mut held: HashMap<String, Vec<&Value>> = HashMap::new();
        for row in rows(self.world, "sources") {
            if row.at("owner")?.py_eq(treasury_id) {
                held.entry(py_str(row.at("item")?)).or_default().push(row);
            }
        }
        let payload = |record: &Value| -> Result<Vec<Value>> {
            ["charges", "flags", "expiration"].iter().map(|key| record.at(key).cloned()).collect()
        };
        let equal = |a: &[Value], b: &[Value]| a.len() == b.len() && a.iter().zip(b).all(|(a, b)| a.py_eq(b));
        let randomization = self.randomization;
        let rules = randomization.and_then(|randomization| randomization.get("sources"));
        let tiers = randomization.and_then(|randomization| randomization.get("targets"));
        let spot = |endpoint: &Value| -> Result<(String, String, Vec<Value>)> {
            Ok((
                py_str(endpoint.at("area")?),
                lower(&py_str(endpoint.at("script_name")?)),
                vec![endpoint.at("x")?.clone(), endpoint.at("y")?.clone(), endpoint.at("container_type")?.clone()],
            ))
        };
        // The other places in the treasury, and whether its own place takes
        // several rewards (its capacity, or overflow).
        let here = spot(self.targets.get(identity).ok_or_else(|| Error::key(identity))?)?;
        let mut beside = Vec::new();
        for target in self.manifest_targets {
            let id = text(target, "id")?;
            let Some(endpoint) = self.targets.get(id) else { continue };
            if id != identity && endpoint.get("kind").and_then(Value::as_str) == Some("container") {
                let there = spot(endpoint)?;
                if there.0 == here.0 && there.1 == here.1 && equal(&there.2, &here.2) {
                    beside.push(id);
                }
            }
        }
        let place = self
            .manifest_targets
            .iter()
            .find(|target| target.get("id").and_then(Value::as_str) == Some(identity))
            .ok_or_else(|| Error::key(identity))?;
        let capacity = place.at("capacity")?.as_int().is_some_and(|capacity| capacity > 1);
        let crowded = capacity
            || randomization.and_then(|randomization| randomization.get("overflow")).and_then(Value::as_bool)
                == Some(true);
        // Whether the place can get the reward (an unknown tier: it can).
        let takes = |target_id: &str, source_id: &str| {
            let slots = tiers.and_then(|tiers| tiers.get(target_id)).map(list);
            let tier = rules.and_then(|rules| rules.get(source_id)).and_then(|rule| rule.get("tier"));
            match (randomization, slots, tier) {
                (Some(_), Some(slots), Some(tier)) => slots.iter().any(|slot| slot.py_eq(tier)),
                _ => true,
            }
        };
        // Whether the reward can lie in the treasury beside this place's.
        let beside_it = |source_id: &str| {
            if randomization.is_none() {
                return !beside.is_empty() || capacity;
            }
            beside.iter().any(|other| takes(other, source_id)) || (crowded && takes(identity, source_id))
        };
        let mut rewards = Vec::new();
        for selected in self.manifest_sources {
            let id = text(selected, "id")?;
            let Some(endpoint) = self.sources.get(id) else {
                continue; // Left out in this round.
            };
            let source = selected.at("record")?;
            let mut names = BTreeSet::from([py_str(source.at("item")?)]);
            if let Some(rule) = rules.and_then(|rules| rules.get(id)) {
                names.extend(rule.get("reward_variants").map(list).unwrap_or(&[]).iter().map(py_str));
            }
            rewards.push((id, source, endpoint, names, payload(source.at("record")?)?));
        }
        for (id, source, endpoint, names, expected) in &rewards {
            if !takes(identity, id) {
                continue; // Never this place's reward.
            }
            let guard = endpoint.get("reward_selected_global").or_else(|| endpoint.get("selected_global"));
            for name in names.iter().filter(|name| destroyed.contains(*name)) {
                let gated = matches!((gates.get(name), guard), (Some(gate), Some(guard)) if gate.py_eq(guard));
                require(gated, || {
                    format!(
                        "{}.BCS destroys every {} in the treasury, and the reward {id} can be one: {identity}",
                        upper(script),
                        upper(name)
                    )
                })?;
            }
            let mut own = BTreeSet::from([id.to_string()]);
            own.extend(source.get("physical_source_ids").map(list).unwrap_or(&[]).iter().map(py_str));
            for name in names {
                let mut same = true;
                for row in held.get(name).map(Vec::as_slice).unwrap_or(&[]) {
                    if !own.contains(&py_str(row.at("id")?)) {
                        same = same && equal(&payload(row.at("record")?)?, expected);
                    }
                }
                require(same, || {
                    format!(
                        "The treasury holds another {}, which the king's handover could not tell from the reward \
                         {id}: {identity}",
                        upper(name)
                    )
                })?;
                for (other, _, _, other_names, other_payload) in &rewards {
                    let told = other == id
                        || !other_names.contains(name)
                        || equal(other_payload, expected)
                        || !beside_it(other);
                    require(told, || {
                        format!(
                            "The reward {other} can lie in the treasury as another {}, which the king's handover \
                             could not tell from the reward {id}: {identity}",
                            upper(name)
                        )
                    })?;
                }
            }
        }
        Ok(())
    }

    /// Item Randomiser's chapter 3 places ([`chapter3`]). The runtime finds
    /// each creature by its script name in its area: it must be once in the
    /// areas the handler names, as an actor that appears there or as a
    /// creature the area's own script creates, and the only creature there of
    /// that name. A creature that is not is left out of its group; without a
    /// vampire, or without a thief and Aran, so is the place.
    fn chapter3_endpoint(&mut self, identity: &str, selected: &Value) -> Result<Dict> {
        let routes = self.routes()?;
        let (world, index) = (self.world, self.index);
        // The actors that appear in each of the handler's areas, and the
        // creatures each area's own script creates, in the areas' order.
        let mut areas: Vec<(&str, Vec<&'a Value>, BTreeMap<String, usize>)> = Vec::new();
        for area in chapter3::AREAS {
            let resource = format!("are:{area}");
            if !index.resources.contains(&resource) {
                continue;
            }
            self.use_resource(&resource)?;
            let (script, made) = routes.created(&resource)?;
            if let Some(script) = script {
                self.use_resource(&script)?;
            }
            let mut placed = Vec::new();
            for target in rows(world, "targets") {
                if target.at("resource")?.as_str() == Some(resource.as_str())
                    && target.at("kind")?.as_str() == Some("actor")
                    && !target.at("selector")?.at("appearance_schedule")?.py_eq(&Value::Int(0))
                {
                    placed.push(target);
                }
            }
            areas.push((area, placed, made));
        }
        let name_of = |resource: &str| -> Result<String> {
            match index.target(world, resource) {
                Some(creature) => lower_name(creature.at("selector")?.get("script_name")),
                None => Ok(String::new()),
            }
        };
        // Where the runtime finds the creature `name`, and by which name.
        let find = |name: &str| -> Result<Option<(String, String)>> {
            let resource = format!("cre:{name}");
            let creature = index.target(world, &resource);
            let Some(creature) = creature.filter(|_| index.resources.contains(&resource) && !index.is_unreadable(&resource))
            else {
                return Ok(None);
            };
            if has_inventory_problem(creature)? {
                return Ok(None);
            }
            let script_name = creature.at("selector")?.get("script_name").and_then(Value::as_str).unwrap_or("");
            let count = script_name.chars().count();
            if !(1..=32).contains(&count) || !script_name.chars().all(|character| (' '..='~').contains(&character)) {
                return Ok(None);
            }
            let mut seen = Vec::new();
            for (area, placed, _) in &areas {
                for target in placed {
                    let template = target.at("selector")?.get("template_resource");
                    if template.and_then(Value::as_str) == Some(resource.as_str()) {
                        seen.push(*area);
                    }
                }
            }
            for (area, _, made) in &areas {
                for _ in 0..made.get(name).copied().unwrap_or(0) {
                    seen.push(*area);
                }
            }
            let &[area] = seen.as_slice() else {
                return Ok(None);
            };
            // Every creature of its area by that name: actors and created ones.
            let (_, placed, made) = areas.iter().find(|(other, _, _)| *other == area).expect("seen there");
            let wanted = lower(script_name);
            let mut same = 0;
            for target in placed {
                if lower_name(actor_name(world, index, target))? == wanted {
                    same += 1;
                }
            }
            for (made_name, count) in made {
                if name_of(&format!("cre:{made_name}"))? == wanted {
                    same += count;
                }
            }
            Ok((same == 1).then(|| (area.to_string(), script_name.to_string())))
        };
        let mut groups = Vec::new();
        let mut drawn = Vec::new();
        for group in chapter3::groups(|resource| index.resources.contains(resource)) {
            let mut members = Vec::new();
            for &name in group.names {
                if let Some((area, script_name)) = find(name)? {
                    self.use_resource(&format!("cre:{name}"))?;
                    members.push(Value::Dict(crate::dict! {
                        "resref" => name, "script_name" => script_name, "area" => upper(&area),
                    }));
                }
            }
            if !members.is_empty() {
                if group.drawn {
                    drawn.push(groups.len());
                }
                groups.push(Value::Dict(crate::dict! {
                    "members" => Value::List(members), "corpse_only" => group.corpse_only,
                }));
            }
        }
        let mut first = BTreeSet::new();
        if let Some(group) = groups.first() {
            for member in list(group.at("members")?) {
                first.insert(text(member, "resref")?.to_string());
            }
        }
        require(chapter3::VAMPIRES.iter().any(|name| first.contains(*name)), || {
            format!(
                "Item Randomiser's chapter 3 place needs one of Bodhi's vampires ({}) in AR0801, once and with a \
                 script name of its own there: {identity}",
                chapter3::VAMPIRES.iter().map(|name| upper(name)).collect::<Vec<_>>().join(", ")
            )
        })?;
        require(groups.len() > 1, || {
            format!(
                "Item Randomiser's chapter 3 place needs one of Aran's thieves or Aran himself where its handler \
                 looks for them, once and with a script name of their own there: {identity}"
            )
        })?;
        let endpoint_identity =
            identity_of(crate::dict! {"target" => selected.clone(), "groups" => Value::List(groups.clone())})?;
        for position in drawn {
            // One drawn per reward, among those who can use it (export_native).
            groups[position].as_dict_mut().expect("a group").insert("eligible", Dict::new());
        }
        Ok(crate::dict! {"kind" => "first-of", "groups" => Value::List(groups), "identity" => endpoint_identity})
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

    /// `use_route`: Apply writes this resource with the reward routes' patch.
    fn use_route(&mut self, resource: &str) -> Result<()> {
        self.use_resource(resource)?;
        self.routed.insert(resource.to_string());
        Ok(())
    }

    /// `route_resources`: the resources a route patches in this game. A
    /// script that would destroy its item needs no gate where the game has
    /// none.
    fn route_resources(&self, resources: &Value) -> Vec<Value> {
        list(resources)
            .iter()
            .filter(|resource| {
                let resource = resource.as_str().unwrap_or("");
                !mode2_rewards::destroy_gate(resource) || self.index.resources.contains(resource)
            })
            .cloned()
            .collect()
    }

    /// `use_quest`: the quest script as the installer prepared it (or as
    /// Apply extended it).
    fn use_quest(&mut self, resource: &str) -> Result<()> {
        self.use_resource(resource)?;
        self.quests.insert(resource.to_string());
        Ok(())
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
                if let Some(copies) = &mut self.duplicates {
                    copies.retain(|copy| copy.get("source_id").and_then(Value::as_str) != Some(identity));
                }
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
        // The extended reward route this row is: its own, or a preset's row of it.
        let route_source = if kind == "authored-source" { mode2_route_source(source.at("authored")?)? } else { None };
        if kind == "virtual" {
            let ok = resource == format!("itm:{}", text(source, "item")?)
                && self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?);
            require(ok, || format!("Virtual item changed since the scan: {identity}"))?;
        } else if kind == "authored-source"
            || (self.shield_truthy() && identity == original_options::SPELL_SHIELD_SOURCE)
        {
            // A post-install rescan sees our campaign-aware DLG/BCS patch,
            // also in a quest script that the installer prepared (the book's).
            // Reward route bytes are recognised below (reward_routes) and
            // again by the export, before writing any output.
            let ok = self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?)
                || mode2_rewards::resource_rewards().contains_key(resource);
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
        // The reward keeps the source's flags (identified, unstealable, stolen
        // and bits another mod set). An undroppable one (8) would never leave
        // a creature target.
        let ok = is_resref(item_name)
            && record.at("expiration")?.py_eq(&Value::Int(0))
            && {
                let flags = record.at("flags")?;
                flags.as_int().is_some_and(|flags| (0..=0xFFFF_FFFF).contains(&flags) && flags & 8 == 0)
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
        if let Some(route_source) = route_source.as_deref() {
            // The routes' items as the game names them (EET renames BG1's).
            let routes = self.routes()?;
            let spec = mode2_rewards::source_spec(route_source, routes.naming())?;
            let ok = source.at("resource")?.py_eq(entry(&spec, "resource")?) && record.py_eq(entry(&spec, "payload")?);
            require(ok, || format!("The extended reward source differs from its route: {identity}"))?;
            let route = mode2_rewards::source_rewards().get(route_source).map(crate::py::str_value).unwrap_or_default();
            self.require_route(&route)?;
            let mut endpoint = crate::dict! {"kind" => "guarded-script", "adapter_id" => "mode2-reward-v1"};
            for key in ["quest", "resource", "selected_global", "consumed_global"] {
                endpoint.insert(key, entry(&spec, key)?.clone());
            }
            endpoint.insert("payload", record.clone());
            endpoint.insert("identity", source_identity()?);
            if route_source != identity {
                // A preset's row: the runtime knows the route's flags and
                // copies by the route's own row.
                endpoint.insert("route_source", route_source);
            }
            self.sources.insert(identity, endpoint);
            for name in spec.get("required_transfers").map(list).unwrap_or(&[]) {
                let duplicate = mode2_rewards::transfer_spec(name.as_str().unwrap_or(""), routes.naming())?;
                let authored_ids = list(entry(&duplicate, "authored_source_ids")?);
                if !authored_ids.iter().any(|id| id.as_str() == Some(route_source)) {
                    continue;
                }
                // The creature's own copies of the reward, wherever this game
                // keeps them; the runtime checks each record's bytes again.
                let resource = entry(&duplicate, "source_resource")?.as_str().unwrap_or("");
                let item = entry(&duplicate, "item")?.as_str().unwrap_or("");
                let creature = format!("{}.CRE", upper(tail(resource, 4)));
                // The runtime accepts each copy under its registered names
                // (FLPRGat.lua's auxiliary_sources), which EET keeps.
                let registered = mode2_rewards::transfer_spec(name.as_str().unwrap_or(""), Naming::default())?;
                let registered_resource = entry(&registered, "source_resource")?.as_str().unwrap_or("");
                let registered_item = entry(&registered, "item")?.as_str().unwrap_or("");
                require((resource, item) == (registered_resource, registered_item), || {
                    format!(
                        "This game renames {}.CRE or {}, which Chriz Item Randomiser's runtime knows only by those \
                         names: {identity}",
                        upper(tail(registered_resource, 4)),
                        upper(registered_item)
                    )
                })?;
                let key = tuple_key(&[name_part(resource), name_part(item)]);
                let records: Vec<&'a Value> = self.template_records.get(&key).cloned().unwrap_or_default();
                let own = list(entry(&duplicate, "source_ids")?).iter().any(|id| id.as_str() == Some(route_source));
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
            let resources = self.route_resources(entry(&spec, "resources")?);
            if resources.len() > 1 {
                let endpoint = self.sources.get_mut(identity).and_then(Value::as_dict_mut).expect("inserted above");
                endpoint.insert("resources", Value::List(resources.clone()));
            }
            for resource in &resources {
                self.use_route(resource.as_str().unwrap_or(""))?;
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
            self.use_quest(resource)?;
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
            self.use_quest(scripted_sources::RESOURCE)?;
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
        for resource in self.route_resources(reward.at("resources")?) {
            self.use_route(resource.as_str().unwrap_or(""))?;
        }
        Ok(())
    }

    /// Every record of the moved item in the holder of one of Item
    /// Randomiser's second copies, as the scan finds it.
    fn compile_copy(
        &mut self,
        copy: &Value,
        held: &Holdings<'_>,
        moved: &HashSet<String>,
        claimed: &mut HashSet<String>,
    ) -> Result<()> {
        let (identity, holder, item) = (text(copy, "source_id")?, text(copy, "resource")?, text(copy, "item")?);
        let (label, kind, named) = (filename(holder), holder.split(':').next().unwrap_or(""), upper(item));
        require(!self.index.is_unreadable(holder), || {
            format!(
                "{label} cannot be read, so Item Randomiser's second copy of {named} there cannot be found: {identity}"
            )
        })?;
        let scanned = self.index.resource(self.world, holder).and_then(|row| row.get("sha256"));
        let unchanged = scanned.unwrap_or(&Value::Null).py_eq(copy.at("resource_sha256")?);
        require(unchanged, || format!("{label} changed since the scan: {identity}"))?;
        let records = held.get(&(holder.to_string(), item.to_string())).map(Vec::as_slice).unwrap_or(&[]);
        require(!records.is_empty(), || {
            format!("{label} no longer holds Item Randomiser's second copy of {named}: {identity}")
        })?;
        let stock: &[&str] = if kind == "sto" { &["quantity", "infinite_supply_flag"] } else { &[] };
        let mut payloads = Vec::with_capacity(records.len());
        for row in records {
            let record = row.at("record")?;
            let mut payload = Dict::new();
            for key in PAYLOAD_FIELDS.iter().chain(stock) {
                payload.insert(*key, record.at(key)?.clone());
            }
            payloads.push(Value::Dict(payload));
        }
        require(payloads.iter().all(|payload| payload.py_eq(&payloads[0])), || {
            format!("{label} holds Item Randomiser's second copy of {named} in records that differ: {identity}")
        })?;
        let mut ids = HashSet::new();
        for row in records {
            ids.insert(text(row, "id")?.to_string());
        }
        require(ids.is_disjoint(moved), || {
            format!("{label}'s {named} moves as a reward of its own, so it cannot also be the second copy of another: {identity}")
        })?;
        require(ids.is_disjoint(claimed), || {
            format!("{label}'s {named} is already the second copy of another reward: {identity}")
        })?;
        let mut concerns = HashSet::new();
        for row in records {
            concerns.extend(list(row.at("concerns")?).iter().filter_map(Value::as_str));
        }
        let concerned = |names: &[&str]| names.iter().any(|name| concerns.contains(name));
        // The area loader identifies a container's record by its index (FLPRGat.lua).
        require(
            kind != "are" || !concerned(&["shared-container-record", "unowned-item-record", "dynamic-container-items"]),
            || format!("{label} keeps its second copy of {named} outside a container with fixed contents: {identity}"),
        )?;
        // Removing a creature's record renumbers the slots that name later records.
        require(
            kind != "cre" || copy.get("replacement").is_some() || !concerned(&[DANGLING_INVENTORY, INVALID_INVENTORY]),
            || {
                format!(
                    "{label} has inventory slots that name no record, so its second copy of {named} cannot be \
                     removed: {identity}"
                )
            },
        )?;
        let resource_sha256 = self.use_resource(holder)?;
        let mut indices = Vec::with_capacity(records.len());
        for row in records {
            indices.push(row.at("record")?.at("record_index")?.clone());
        }
        let first = payloads.swap_remove(0);
        let field = |key: &str| first.get(key).cloned().unwrap_or(Value::Null);
        let mut payload = Dict::new();
        for key in PAYLOAD_FIELDS {
            payload.insert(key, field(key));
        }
        let mut entry = crate::dict! {
            "id" => copy.at("id")?.clone(), "source_id" => identity, "resource" => holder,
            "resource_sha256" => resource_sha256, "record_indices" => Value::List(indices), "payload" => payload,
        };
        if !stock.is_empty() {
            let mut fields = Dict::new();
            for key in stock {
                fields.insert(*key, field(key));
            }
            entry.insert("stock", fields);
        }
        if let Some(replacement) = copy.get("replacement") {
            let resref = replacement.at("resref")?;
            let digest = self.use_resource(&format!("itm:{}", py_str(resref)))?;
            require(digest.py_eq(replacement.at("resource_sha256")?), || format!("Replacement item changed: {identity}"))?;
            entry.insert("replacement", resref.clone());
        }
        if kind == "are" {
            self.pristine.insert(holder.to_string());
        }
        claimed.extend(ids);
        self.duplicates.as_mut().expect("compiled only when the campaign names copies").push(Value::Dict(entry));
        Ok(())
    }

    fn compile_target(&mut self, selected: &'a Value) -> Result<()> {
        let mut target = selected.at("record")?;
        let identity = text(selected, "id")?;
        let target_identity = || identity_of(crate::dict! {"target" => selected.clone()});
        let authored = target.get("adapter_id").and_then(Value::as_str) == Some("original-target-v1");
        let container_quest = if authored { container_target_spec(target.at("selector")?)? } else { None };
        // The extended reward route's place this row is: its own, or a preset's row there.
        let route_target = if authored { mode2_route_target(target.at("selector")?)? } else { None };
        let ok = (authored || self.index.target(self.world, identity).is_some_and(|row| row.py_eq(target))) && {
            let resource = text(target, "resource")?;
            self.scanned_hash(resource)?.py_eq(selected.at("resource_sha256")?)
                || (route_target.is_some() && mode2_rewards::resource_rewards().contains_key(resource))
        };
        require(ok, || format!("Selected target differs from the scan: {identity}"))?;
        if authored {
            let selector = target.at("selector")?;
            // Its game patches and the runtime name one row of such a place (a
            // preset's row there stands for the route's own).
            let place = crate::special_places::place(selector)?.unwrap_or_default();
            let other = self.unique_places.get(&place).map_or(identity, String::as_str);
            require(other == identity, || {
                format!("Original recipient is the same place as {other}; keep one of them: {identity}")
            })?;
            self.use_resource(text(target, "resource")?)?;
            let reward = match route_target.as_deref() {
                Some(id) => Some(mode2_rewards::target_spec(id, self.routes()?.naming())?),
                None => None,
            };
            if reward.as_ref().is_some_and(|reward| !reward.is_empty()) {
                let route =
                    mode2_rewards::target_rewards().get(route_target.as_deref().unwrap_or("")).map(py_str).unwrap_or_default();
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
                for resource in self.route_resources(entry(reward, "resources")?) {
                    self.use_route(&py_str(&resource))?;
                }
                for resource in [creature_resource.to_string(), format!("are:{}", lower(placement_area))] {
                    self.use_resource(&resource)?;
                }
                let mut endpoint = crate::dict! {
                    "kind" => "creature", "area" => upper(placement_area),
                    "script_name" => script_name(Some(placement.at("script_name")?), identity)?,
                    "inventory_reward" => true, "reward_resource" => entry(reward, "resource")?.clone(),
                    "identity" => identity_of(crate::dict! {
                        "target" => selected.clone(), "creature" => creature.cloned().unwrap_or(Value::Null),
                    })?,
                };
                let route = format!("original-target:{}", route_target.as_deref().unwrap_or(""));
                if route != identity {
                    // A preset's row: the reward reply names the route's own row.
                    endpoint.insert("route_target", route);
                }
                self.targets.insert(identity, endpoint);
                return Ok(());
            }
            if chapter3_place(selector)? {
                let endpoint = self.chapter3_endpoint(identity, selected)?;
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
                let mut alike = false;
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
                    // The runtime finds a recipient by its script name in the area.
                    // Without a name of its own (the engine's blank, None), or with
                    // one that another actor there has too, it cannot be told apart
                    // and would never get its reward (OWNER_AMBIGUOUS).
                    let lowered = lower(&name);
                    let place = key_part(target.get("resource"));
                    let own = Self::count(
                        &self.actor_templates,
                        &tuple_key(&[place.clone(), name_part(&lowered), name_part(resource)]),
                    );
                    let all = Self::count(&self.actor_names, &tuple_key(&[place, name_part(&lowered)]));
                    if lowered == "none" || own > 1 || all > own {
                        alike = true;
                        continue;
                    }
                    self.use_resource(resource)?;
                    members.push(Value::Dict(crate::dict! {"resref" => tail(resource, 4), "script_name" => name}));
                }
                require(!members.is_empty() || !alike, || {
                    format!("Native target needs a unique actor script name in its area: {identity}")
                })?;
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
                if let Some(id) = route_target.as_deref() {
                    let spec = mode2_rewards::target_spec(id, self.routes()?.naming())?;
                    for resource in self.route_resources(entry(&spec, "resources")?) {
                        self.use_route(resource.as_str().unwrap_or(""))?;
                    }
                    if spec.get("handover").is_some_and(Value::truthy) {
                        // The Sahuagin treasury: the one container of its area
                        // whose script it is, wherever a mod moved or renamed it.
                        let handover = entry(&spec, "handover")?;
                        let area_resource = format!("are:{}", text(handover, "area")?);
                        self.use_resource(&area_resource)?;
                        let script = handover.at("script")?;
                        let mut found = Vec::new();
                        for row in rows(self.world, "targets") {
                            if row.at("resource")?.as_str() == Some(area_resource.as_str())
                                && row.at("kind")?.as_str() == Some("container")
                                && row.at("selector")?.at("trap_script")?.py_eq(script)
                            {
                                found.push(row);
                            }
                        }
                        require(found.len() == 1, || {
                            format!(
                                "{}.ARE needs one container with the script {}, the treasury: {identity}",
                                upper(tail(&area_resource, 4)),
                                upper(script.as_str().unwrap_or(""))
                            )
                        })?;
                        let treasury = found[0];
                        let selection = treasury.at("selector")?;
                        let name = script_name(Some(selection.at("name")?), identity)?;
                        let unique = !selection.at("dynamic_item_table")?.truthy()
                            && Self::count(
                                &self.container_names,
                                &self.container_key(treasury.get("resource"), &lower(&name), selection)?,
                            ) == 1;
                        require(unique, || format!("Native target needs a unique static container name: {identity}"))?;
                        let (x, y) = (selection.at("x")?, selection.at("y")?);
                        require(in_coordinates(x) && in_coordinates(y), || {
                            format!("Native container coordinates are unsupported: {identity}")
                        })?;
                        self.treasuries.push((identity.to_string(), treasury, script.clone()));
                        let mut endpoint = crate::dict! {
                            "kind" => "container", "area" => area(treasury.at("resource")?)?, "script_name" => name,
                            "x" => x.clone(), "y" => y.clone(), "container_type" => selection.at("type")?.clone(),
                            "handover" => crate::dict! {
                                "event_global" => entry(&spec, "event_global")?.clone(),
                                "recipient_local" => entry(&spec, "recipient_local")?.clone(),
                            },
                            "reward_resource" => entry(&spec, "resource")?.clone(),
                            "identity" => identity_of(crate::dict! {"target" => selected.clone(), "container" => treasury.clone()})?,
                        };
                        let route = format!("original-target:{id}");
                        if route != identity {
                            // A preset's row: the reward reply names the route's own row.
                            endpoint.insert("route_target", route);
                        }
                        self.targets.insert(identity, endpoint);
                    } else if spec.get("holder").is_some_and(Value::truthy) {
                        // The Sahuagin king's place: his reward reply pays it;
                        // until then it waits for his death, at his corpse.
                        // Each king the area can have must carry the name the
                        // runtime finds him by.
                        let holder = entry(&spec, "holder")?;
                        let area_resource = Value::from(format!("are:{}", text(holder, "area")?));
                        self.use_value(&area_resource)?;
                        let mut creatures = Vec::new();
                        for resource in list(holder.at("creature_resources")?) {
                            self.use_value(resource)?;
                            let creature = resource.as_str().and_then(|resource| self.index.target(self.world, resource));
                            let ok = match creature {
                                Some(creature) => {
                                    lower(text(creature.at("selector")?, "script_name")?) == text(holder, "script_name")?
                                }
                                None => false,
                            };
                            require(ok, || format!("Original reward recipient changed: {identity}"))?;
                            creatures.push(creature.expect("checked above").clone());
                        }
                        let endpoint = crate::dict! {
                            "kind" => "creature", "area" => area(&area_resource)?,
                            "script_name" => script_name(Some(holder.at("script_name")?), identity)?, "corpse_only" => true,
                            "handover" => crate::dict! {
                                "event_global" => entry(&spec, "event_global")?.clone(),
                                "recipient_local" => entry(&spec, "recipient_local")?.clone(),
                            },
                            "reward_resource" => entry(&spec, "resource")?.clone(),
                            "identity" => identity_of(crate::dict! {"target" => selected.clone(), "creatures" => Value::List(creatures)})?,
                        };
                        self.targets.insert(identity, endpoint);
                    } else if entry(&spec, "recipient_local")?.is_null() {
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
                self.use_quest(&format!("bcs:{name}"))?;
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
///
/// `routed` collects the resources that the campaign's reward routes patch,
/// `quests` the quest scripts it needs as CIR's installer prepared them (both
/// among the required resources).
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
    routed: Option<&mut BTreeSet<String>>,
    quests: Option<&mut BTreeSet<String>>,
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
    // Item Randomiser's second copies of the moved rewards (duplicates.rs). The
    // gate rewrites a creature or shop that holds one as a whole, so a source
    // in it is rewritten with it.
    let copies = manifest.get("duplicates").filter(|copies| !copies.is_null());
    for copy in copies.map(list).unwrap_or(&[]) {
        let resource = text(copy, "resource")?;
        if !resource.starts_with("are:") {
            *selected_owners.entry(resource.to_string()).or_insert(0) += 1;
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
        routed: BTreeSet::new(),
        quests: BTreeSet::new(),
        selected_items: BTreeSet::new(),
        selected_actors: HashSet::new(),
        selected_owners,
        duplicates: copies.map(|_| Vec::new()),
        container_names: HashMap::new(),
        actor_names: HashMap::new(),
        actor_templates: HashMap::new(),
        container_items: HashMap::new(),
        physical_items: HashMap::new(),
        template_records: HashMap::new(),
        creature_items: HashMap::new(),
        owner_items: HashMap::new(),
        template_actors: HashMap::new(),
        routes: None,
        manifest_sources,
        manifest_targets,
        randomization: manifest.get("randomization"),
        treasuries: Vec::new(),
        unique_places: HashMap::new(),
        stored,
        revision: py_str(&revision),
        notes: Vec::new(),
    };
    if let Some(shield) = shield {
        let randomized = original_options::spell_shield_randomized(
            manifest_sources
                .iter()
                .map(|source| source.at("record").ok().and_then(|record| record.get("authored")).unwrap_or(&Value::Null)),
        );
        let expected = original_options::spell_shield_spec(world, randomized)?;
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
            let key = tuple_key(&[
                key_part(Some(target.at("resource")?)),
                name_part(&name),
                key_part(target.at("selector")?.get("template_resource")),
            ]);
            *compiler.actor_templates.entry(key).or_insert(0) += 1;
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

    // The records the sources move, and the copies a reward route or an
    // earlier row already removes.
    let (mut moved, mut claimed) = (HashSet::new(), HashSet::new());
    for selected in manifest_sources {
        let record = selected.at("record")?;
        if matches!(record.at("kind")?.as_str(), Some("container" | "cre-template" | "embedded-cre" | "store")) {
            moved.insert(text(selected, "id")?.to_string());
        }
        moved.extend(record.get("physical_source_ids").map(list).unwrap_or(&[]).iter().filter_map(Value::as_str).map(str::to_string));
    }
    for endpoint in compiler.sources.values() {
        for auxiliary in endpoint.get("auxiliary_creatures").map(list).unwrap_or(&[]) {
            let resource = py_str(auxiliary.at("resource")?);
            for record in list(auxiliary.at("record_indices")?) {
                claimed.insert(format!("{resource}/item/{}", py_str(record)));
            }
        }
    }
    let held = if copies.is_some_and(Value::truthy) { holdings(rows(world, "sources")) } else { Holdings::new() };
    for copy in copies.map(list).unwrap_or(&[]) {
        let identity = text(copy, "source_id")?;
        compiler.attempt(&mut problems, identity, |compiler| compiler.compile_copy(copy, &held, &moved, &mut claimed))?;
    }

    for selected in manifest_targets {
        let identity = text(selected, "id")?;
        compiler.attempt(&mut problems, identity, |compiler| compiler.compile_target(selected))?;
        let record = selected.at("record")?;
        if record.get("adapter_id").and_then(Value::as_str) == Some("original-target-v1") {
            if let Some(place) = crate::special_places::place(record.at("selector")?)? {
                if crate::special_places::unique(&place) && compiler.targets.contains_key(identity) {
                    compiler.unique_places.insert(place, identity.to_string());
                }
            }
        }
    }
    // The treasury's rewards, once every place in its container is known.
    for (identity, treasury, script) in std::mem::take(&mut compiler.treasuries) {
        if compiler.targets.contains_key(&identity) {
            compiler.attempt(&mut problems, &identity, |compiler| {
                compiler.require_treasury_names(&identity, treasury, &script)
            })?;
        }
    }

    let mut guarded_ids = Vec::new();
    for selected in manifest_sources {
        if selected.at("record")?.at("kind")?.as_str() != Some("virtual") {
            guarded_ids.push(selected.at("id")?.clone());
        }
    }
    let source_gate = !guarded_ids.is_empty()
        || compiler.shield_truthy()
        || manifest.get("cursed_disguises").is_some()
        || compiler.duplicates.as_ref().is_some_and(|copies| !copies.is_empty());
    let options = manifest.get("original_options");
    let option = |name: &str| options.and_then(|options| options.get(name)).is_some_and(Value::truthy);
    if option("kangaxx") {
        for resource in quest_options::KANGAXX_RESOURCES {
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
    if let Some(copies) = compiler.duplicates {
        native.insert("duplicates", Value::List(copies));
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
    if let Some(routed) = routed {
        routed.append(&mut compiler.routed);
    }
    if let Some(quests) = quests {
        quests.append(&mut compiler.quests);
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

/// When the runtime checks a kept revision's file and what it would stop
/// (the reference's `_CHECKED`).
const CHECKED: &str = "the runtime checks it whenever one of them is loaded and would stop CIR in it";
/// The reward dialogues, by revision, whose staged patch Apply's newer one
/// supersedes ([`superseding`]); M_FLPR.lua reads it.
const SUPERSEDED: &str = "superseded.lua";

/// The native fixture a revision was exported with (`_stored_native`).
fn stored_native(exported: &Path, key: &str) -> Result<Value> {
    let path = exported.join("revisions").join(format!("{key}.native.json"));
    let data = read_bytes(&path)?;
    let text = std::str::from_utf8(&data).map_err(|error| Error::value(utf8_error_message(&data, &error)))?;
    // Path.read_text translates newlines before json.loads sees the text.
    parse_lenient(&text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// (resource, staged file, quest) for each file a revision's guarded scripts
/// need in override (`_staged_scripts`).
///
/// When one of the revision's games is loaded, the runtime compares these
/// staged bytes with the game's override file (FLPRScript.lua). A quest
/// script that the installer prepared (`quest`) may also be the version
/// Apply extended it to ([`extended_scripts`]), and a reward dialogue Apply's
/// patch that supersedes the staged one ([`superseding`]).
fn staged_scripts(key: &str, manifest: &Value, native: &Value) -> Result<Vec<(String, String, bool)>> {
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
                    result.push((resource, format!("{base}.route-{}.patched.{extension}", route + 1), false));
                }
            }
            None => {
                let resource = py_str(source.at("record")?.at("resource")?);
                let extension = resource.split(':').next().unwrap_or("").to_string();
                let quest = endpoint.get("adapter_id").and_then(Value::as_str) == Some("original-quest-v1");
                result.push((resource, format!("{base}.patched.{extension}"), quest));
            }
        }
    }
    Ok(result)
}

/// The file that keeps Apply's own version of a guarded script beside a
/// version that saves need (read by M_FLPR.lua; `_applied_script`): the
/// reward routes' version of an installer-prepared quest script
/// ([`extended_scripts`]), or of a reward dialogue whose earlier patch it
/// supersedes ([`superseding`]).
fn applied_script(resource: &str) -> String {
    let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
    format!("{}.applied.{kind}", upper(name))
}

/// The files that keep an installer-prepared quest script and the version
/// Apply extended it to with reward routes (read by M_FLPR.lua;
/// `_extended_scripts`).
fn extended_scripts(resource: &str) -> (String, String) {
    (format!("{}.installed.bcs", upper(resource.split(':').nth(1).unwrap_or(""))), applied_script(resource))
}

/// The campaign flags a compiled revision's endpoints name, wherever they
/// nest: each value of a key that ends in _global or _local (`_named_flags`).
fn named_flags(value: &Value) -> BTreeSet<String> {
    let mut flags = BTreeSet::new();
    match value {
        Value::Dict(dict) => {
            for (key, item) in dict.iter() {
                if let (true, Some(flag)) = (key.ends_with("_global") || key.ends_with("_local"), item.as_str()) {
                    flags.insert(flag.to_string());
                }
                flags.extend(named_flags(item));
            }
        }
        Value::List(items) => items.iter().for_each(|item| flags.extend(named_flags(item))),
        _ => {}
    }
    flags
}

/// Whether a reward route that a revision was exported without does nothing
/// in its saves, where a dialogue's newer patch adds it (`_leaves_alone`).
///
/// Its replies' guards and marks read and set the route's own flags, which
/// only a revision that names them uses. Its other changes call the runtime
/// with the route's places, which finds that revision's targets there: a
/// hand-over (FLPR_Transfer) takes a creature's reward from its backpack,
/// which a revision exported before the place had its route keeps as a plain
/// creature (FLPRNat.lua), and a treasury's payment (FLPR_Handover) needs the
/// place's own route. A revision without a target there leaves both alone.
fn leaves_alone(spec: &Dict, native: &Value) -> Result<bool> {
    let named = named_flags(native);
    let flags = ["selected_global", "consumed_global", "event_global", "recipient_local"];
    if flags.iter().filter_map(|key| spec.get(key).and_then(Value::as_str)).any(|flag| named.contains(flag)) {
        return Ok(false);
    }
    let targets = native.at("targets")?;
    let targets = targets.as_dict().ok_or_else(|| Error::attribute(targets.type_name(), "items"))?;
    for identity in list(entry(spec, "target_ids")?) {
        let target = format!("original-target:{}", py_str(identity));
        for (key, endpoint) in targets.iter() {
            if key != target && endpoint.get("route_target").and_then(Value::as_str) != Some(target.as_str()) {
                continue;
            }
            if spec.get("handover").is_some_and(Value::truthy) {
                return Ok(false);
            }
            let absent = |field: &str| endpoint.get(field).is_none_or(Value::is_null);
            let plain = endpoint.get("kind").and_then(Value::as_str) == Some("creature")
                && !endpoint.get("corpse_only").is_some_and(Value::truthy)
                && absent("handover")
                && absent("event_global");
            if mode2_rewards::transfers(spec) && !(endpoint.get("inventory_reward").is_some_and(Value::truthy) || plain) {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

/// The reward dialogue as Apply leaves it, where it is this adapter's patch
/// of it now and supersedes the earlier one that a kept revision staged
/// ([`mode2_rewards::superseding_routes`]), and each route it adds leaves
/// that revision's saves alone ([`leaves_alone`]): those saves can then read
/// it instead. None otherwise (`_superseding`).
fn superseding(
    resource: &str,
    installed: Option<&Vec<u8>>,
    staged: &[u8],
    native: &Value,
    naming: Naming<'_>,
) -> Result<Option<Vec<u8>>> {
    let Some(installed) = installed else { return Ok(None) };
    if installed.as_slice() == staged || !mode2_rewards::resource_rewards().contains_key(resource) {
        return Ok(None);
    }
    let Some(added) = mode2_rewards::superseding_routes(resource, installed, staged, naming)? else {
        return Ok(None);
    };
    for spec in &added {
        if !leaves_alone(spec, native)? {
            return Ok(None);
        }
    }
    Ok(Some(installed.clone()))
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
    for copy in native.get("duplicates").map(list).unwrap_or(&[]) {
        if let Some(name) = text(copy, "resource")?.strip_prefix("are:") {
            areas.insert(upper(name));
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
/// other revisions are exported as usual. The runtime checks a listed
/// revision whenever one of its games is loaded, so a kept revision whose
/// guarded script or guarded area changed in the game stops the export
/// instead; where Apply's newer patch of a reward dialogue supersedes the one
/// it staged, its saves read Apply's (`superseded.lua`). A selected revision
/// must be prepared completely. `scans` holds the catalogues already
/// validated.
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
    // The resources each revision's reward routes patch, and the quest
    // scripts it needs as the installer prepared them.
    let mut routed_by: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut quests_by: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut notes: Vec<String> = Vec::new();
    let mut kept: BTreeSet<String> = BTreeSet::new();
    for (key, manifest) in &manifests {
        let is_stored = stored.contains(key);
        let mut problems = Dict::new();
        let (routed_here, quests_here) = (routed_by.entry(key.clone()).or_default(), quests_by.entry(key.clone()).or_default());
        let (native, resources, areas) = compile(
            manifest,
            world,
            index,
            items,
            catalog,
            is_stored.then_some(&mut problems),
            is_stored,
            Some(&mut notes),
            Some(routed_here),
            Some(quests_here),
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
    let routed: BTreeSet<String> = routed_by.values().flatten().cloned().collect();
    let quest_scripts: BTreeSet<String> = quests_by.values().flatten().cloned().collect();

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
    // The quest scripts as the installer prepared them, before any route's patch.
    let mut installed_scripts: BTreeMap<String, Vec<u8>> = BTreeMap::new();
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
            // export only checks that they still carry its changes. Apply's
            // reward routes keep them (the book's scrolls).
            if quest_scripts.contains(resource) {
                require_installed(&game_root_text, quest_change(name).ok_or_else(|| Error::key(name))?, &data)?;
                let installed = if mode2_rewards::resource_rewards().contains_key(resource) {
                    mode2_rewards::unpatched_script(resource, &data, naming)?
                } else {
                    data.clone()
                };
                installed_scripts.insert(resource.clone(), installed);
            } else if resource == "dlg:unshey" {
                require_installed(&game_root_text, "unshey", &data)?;
            } else if resource == "dlg:kpglai01" {
                require_installed(&game_root_text, "flail_heads", &data)?;
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
        for resource in &routed {
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
            for (resource, _, _) in staged_scripts(key, &manifests[key], &stored_native(&exported, key)?)? {
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
        let (patches, updated) = quest_options::prepare_quest_options(&named, &game_text, kangaxx, language)?;
        game_text = updated;
        if kangaxx {
            let dialogue = patches
                .iter()
                .find(|(name, _)| name == "hlskull.dlg")
                .map(|(_, data)| data)
                .ok_or_else(|| Error::key("hlskull.dlg"))?;
            journal = quest_options::patch_kangaxx_journal(&journal, dialogue)?;
        }
        if !text_rewards.is_empty() {
            let (updated, refs) =
                mode2_rewards::prepare_reward_texts(&game_text, text_rewards.iter(), language, naming.game)?;
            journal = mode2_rewards::journal_lua(&journal, &refs, naming.game)?;
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
            let (fixture, assets, updated) = cursed_disguises::prepare_cursed_disguises(spec, &itms, &game_text, language)?;
            game_text = updated;
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
    // A quest script that the installer prepared (the book's) is written only
    // when a revision's routes patch it. Revisions that need the installer's
    // version still record that one: the runtime also accepts the version
    // Apply extended it to, which Apply keeps beside it (`extended_scripts`).
    let mut mode2_patches: HashMap<String, Vec<u8>> = HashMap::new();
    for resource in mode2_rewards::resource_rewards().keys() {
        if !routed.contains(resource) {
            continue;
        }
        let data = resource_bytes.get(resource).ok_or_else(|| Error::key(resource))?;
        let patched = mode2_rewards::patch_reward(resource, data, reward_texts.get(resource), None, naming)?;
        let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
        let filename = format!("{}.{}", upper(name), upper(kind));
        pending.insert(format!("resource-overrides/{filename}"), patched.clone());
        for (key, native) in compiled.iter_mut() {
            let hashes = hashes_mut(native);
            if hashes.contains_key(&filename) && routed_by[key].contains(resource) {
                hashes.insert(filename.as_str(), sha256_hex(&patched));
            }
        }
        if quest_scripts.contains(resource) {
            let (installed, applied) = extended_scripts(resource);
            pending.insert(installed, installed_scripts.get(resource).ok_or_else(|| Error::key(resource))?.clone());
            pending.insert(applied, patched.clone());
        }
        mode2_patches.insert(resource.to_string(), patched);
    }
    // A revision that needs only the installer's version of a quest script
    // records that version, whichever the game has.
    for (key, native) in compiled.iter_mut() {
        let hashes = hashes_mut(native);
        for resource in quests_by[key].difference(&routed_by[key]) {
            let installed = installed_scripts.get(resource).ok_or_else(|| Error::key(resource))?;
            hashes.insert(filename(resource), sha256_hex(installed));
        }
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
            if endpoint_kind == "group" || endpoint_kind == "first-of" {
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
                let fill = |group: &mut Dict| -> Result<()> {
                    let members: Vec<Value> = list(entry(group, "members")?).to_vec();
                    let mut creatures: BTreeMap<String, &[u8]> = BTreeMap::new();
                    for member in &members {
                        let resref = py_str(member.at("resref")?);
                        let data = bytes_of(&format!("cre:{resref}"))?;
                        creatures.insert(resref, data.as_slice());
                    }
                    let mut eligible = Dict::new();
                    for item in &reward_items {
                        let preferred =
                            group_recipients::preferred_members(bytes_of(&format!("itm:{item}"))?, &creatures);
                        let mut numbers = Vec::new();
                        for (number, member) in members.iter().enumerate() {
                            if preferred.contains(&py_str(member.at("resref")?)) {
                                numbers.push(Value::from(number + 1));
                            }
                        }
                        eligible.insert(item.as_str(), Value::List(numbers));
                    }
                    group.insert("eligible", eligible);
                    Ok(())
                };
                if endpoint_kind == "group" {
                    fill(endpoint)?;
                } else {
                    // A first-of place draws one creature per reward in each
                    // of its drawn groups (those with "eligible").
                    let groups = endpoint.get_mut("groups").and_then(Value::as_list_mut).ok_or_else(|| Error::key("groups"))?;
                    for group in groups.iter_mut().filter_map(Value::as_dict_mut) {
                        if group.contains_key("eligible") {
                            fill(group)?;
                        }
                    }
                }
            }
        }
        let native = &compiled[key];
        let base = format!("campaign-store/revisions/{key}");
        pending.insert(format!("{base}.native.json"), canonical_bytes(native)?);
        pending.insert(format!("{base}.native.lua"), render_lua(native)?);
        lua_values.insert(format!("{base}.native.lua"), (key.clone(), true));
        // The creature or shop of each second copy as the game has it; an
        // area's is its shared pristine copy.
        for (number, copy) in native.get("duplicates").map(list).unwrap_or(&[]).iter().enumerate() {
            let resource = text(copy, "resource")?;
            let kind = resource.split(':').next().unwrap_or("");
            if kind != "are" {
                pending.insert(format!("{base}.duplicate-{}.original.{kind}", number + 1), bytes_of(resource)?.clone());
            }
        }
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
                        // the game still has exactly these bytes, or the
                        // version Apply extended them to (`extended_scripts`).
                        _ => pending.insert(format!("{base}.patched.bcs"), installed_scripts.get(resource).unwrap_or(raw).clone()),
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
    // When one of its games is loaded, the runtime checks a revision: the
    // staged bytes of its guarded scripts against the game's override files,
    // and the area each guarded area source recorded against the shared
    // pristine copy. A kept revision that would fail there would stop CIR in
    // each of its games, so Apply refuses instead.
    // Where Apply's newer patch of a reward dialogue supersedes the one a kept
    // revision staged, its saves read Apply's ([`superseding`]): it lists
    // them, by revision, and keeps that version beside them
    // ([`applied_script`]).
    /// A file as the game has it, as Apply would leave it, as the revision
    /// needs it, the installer's and Apply's versions that also meet a quest
    /// script's need, and Apply's patch of a reward dialogue that supersedes
    /// the staged one, with that dialogue.
    struct Needed {
        name: String,
        current: Held,
        installed: Held,
        expected: Held,
        extended: (Held, Held),
        newer: Option<(String, Vec<u8>)>,
    }
    let mut superseded: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for key in &kept {
        let before = stored_native(&exported, key)?;
        let mut needed: Vec<Needed> = Vec::new();
        for (resource, staged, quest) in staged_scripts(key, &manifests[key], &before)? {
            let name = filename(&resource);
            let current = live.get(&resource).and_then(Option::as_ref);
            let installed = pending.get(&format!("resource-overrides/{name}")).or(current);
            let expected = read_bytes(&store_path(&output, &relative_path(&staged))?)?;
            let extended = if quest {
                let (installed, applied) = extended_scripts(&resource);
                (Held::from_bytes(pending.get(&installed)), Held::from_bytes(pending.get(&applied)))
            } else {
                (Held::Absent, Held::Absent)
            };
            let newer = if quest { None } else { superseding(&resource, installed, &expected, &before, naming)? };
            needed.push(Needed {
                name,
                current: Held::from_bytes(current),
                installed: Held::from_bytes(installed),
                expected: Held::Bytes(expected),
                extended,
                newer: newer.map(|newer| (resource, newer)),
            });
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
            needed.push(Needed {
                name: format!("{area}.ARE"),
                current: digest(),
                installed: digest(),
                expected,
                extended: (Held::Absent, Held::Absent),
                newer: None,
            });
        }
        for Needed { name, current, installed, expected, extended: (base, extended), newer } in needed {
            let meets = |version: &Held| {
                version.same(&expected)
                    || (!matches!(base, Held::Absent) && base.same(&expected) && version.same(&extended))
                    || matches!((version, &newer), (Held::Bytes(data), Some((_, newer))) if data == newer)
            };
            let change = if !meets(&current) {
                format!("it changed in the game since then; {CHECKED}. Restore {name} before applying")
            } else {
                format!("Apply would now replace it with another version; {CHECKED}")
            };
            require(meets(&installed), || {
                format!("Saved games of revision {} need {name} as it was when they were exported, but {change}", short(key))
            })?;
            if let Some((resource, newer)) = newer {
                superseded.entry(key.clone()).or_default().insert(resource.clone());
                pending.insert(applied_script(&resource), newer);
            }
        }
    }
    // Only then: one that an earlier Apply left in the game names only
    // revisions whose saves it checked, and the runtime still compares the
    // game's file with the version kept beside it.
    if !superseded.is_empty() {
        let mut index = Dict::new();
        for (key, resources) in &superseded {
            index.insert(key.clone(), Value::List(resources.iter().cloned().map(Value::Str).collect()));
        }
        pending.insert(SUPERSEDED, render_lua(&Value::Dict(index))?);
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

#[cfg(test)]
mod tests {
    use super::*;

    fn route(resource: &str, name: &str) -> Dict {
        mode2_rewards::resource_specs(resource, Naming::default())
            .expect("specs")
            .into_iter()
            .find(|spec| spec.get("route").and_then(Value::as_str) == Some(name))
            .expect(name)
    }

    fn with(base: &Dict, extra: &[(&str, Value)]) -> Value {
        let mut target = base.clone();
        for (key, item) in extra {
            target.insert(*key, item.clone());
        }
        Value::Dict(target)
    }

    fn native(targets: Vec<(String, Value)>, sources: Vec<(String, Value)>) -> Value {
        let table = |rows: Vec<(String, Value)>| {
            let mut table = Dict::new();
            for (key, row) in rows {
                table.insert(key, row);
            }
            Value::Dict(table)
        };
        Value::Dict(crate::dict! {"targets" => table(targets), "sources" => table(sources)})
    }

    /// The reference's
    /// `test_a_route_a_revision_was_exported_without_leaves_its_saves_alone`:
    /// only then do its saves read a newer patch that adds the route.
    #[test]
    fn a_route_a_revision_was_exported_without_leaves_its_saves_alone() {
        let (adalon, crossbow, treasury) =
            (route("dlg:udsilver", "adalon"), route("dlg:udsilver", "udsilver"), route("dlg:sahkng01", "sahrod"));
        let place = "original-target:bg2:base:target:72".to_string();
        let creature =
            crate::dict! {"kind" => "creature", "area" => "AR2102", "script_name" => "udsilver", "identity" => "a".repeat(64)};
        let alone = |spec: &Dict, native: &Value| leaves_alone(spec, native).expect("leaves_alone");
        // Her reply hands over what she carries: none there, a plain creature
        // (CIR 1.0.2's place) or her place's own carried reward.
        let carried =
            with(&creature, &[("inventory_reward", Value::Bool(true)), ("reward_resource", Value::from("dlg:udsilver"))]);
        for targets in [vec![], vec![(place.clone(), Value::Dict(creature.clone()))], vec![(place.clone(), carried)]] {
            assert!(alone(&adalon, &native(targets, vec![])));
        }
        // Not a target it cannot take from, also where a preset's row stands
        // for her place.
        let handover = Value::Dict(crate::dict! {"event_global" => "FLPR_EM2_72", "recipient_local" => "FLPR_RM2_72"});
        for extra in [
            ("corpse_only", Value::Bool(true)),
            ("kind", Value::from("container")),
            ("event_global", Value::from("FLPR_EM2_72")),
            ("handover", handover),
        ] {
            let targets = vec![(place.clone(), with(&creature, &[extra.clone()]))];
            assert!(!alone(&adalon, &native(targets, vec![])), "{}", extra.0);
        }
        let standing = with(&creature, &[("corpse_only", Value::Bool(true)), ("route_target", Value::from(place.as_str()))]);
        assert!(!alone(&adalon, &native(vec![("original-target:bg2:other:mode2:target:9".into(), standing)], vec![])));
        // A route's flags: only a revision that names them uses them,
        // wherever its endpoints name them.
        assert!(alone(&crossbow, &native(vec![(place.clone(), Value::Dict(creature.clone()))], vec![])));
        let flag = |key: &str| crossbow.get(key).cloned().expect(key);
        let source = crate::dict! {"kind" => "guarded-script", "selected_global" => flag("selected_global")};
        let container =
            crate::dict! {"kind" => "container", "handover" => Value::Dict(crate::dict! {"event_global" => flag("event_global")})};
        for named in [
            native(vec![], vec![("original-source:bg2:mode2:item:44".into(), Value::Dict(source))]),
            native(vec![("original-target:bg2:mode2:target:66".into(), Value::Dict(container))], vec![]),
        ] {
            assert!(!alone(&crossbow, &named));
        }
        // A treasury's payment needs its place's own route.
        assert!(alone(&treasury, &native(vec![], vec![])));
        let row = treasury.get("target_ids").and_then(Value::as_list).and_then(|ids| ids.first()).and_then(Value::as_str);
        let target = crate::dict! {"kind" => "container", "area" => "AR2300"};
        let targets = vec![(format!("original-target:{}", row.expect("its place")), Value::Dict(target))];
        assert!(!alone(&treasury, &native(targets, vec![])));
    }
}
