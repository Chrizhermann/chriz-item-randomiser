//! Saved configurations carried over to new scans of the same game.
//!
//! A configuration names the scans it was made with, and the editor opens
//! only the configurations of its own scans. After the game's mods change (a
//! new version of Chriz Item Randomiser among them) the start without
//! arguments scans the game again; `rebase_drafts` then carries each
//! configuration of the earlier scans over to the new ones.
//!
//! A configuration saved unchanged from a preset (its plan's record of what
//! the preset's fit made still matches it) becomes that preset as it fits the
//! new scans, with the same pools and options: what the preset gained, in a
//! new version or with the new mods, is added.
//!
//! Any other configuration keeps what the player chose: the items, which of
//! their records move, the places and their reward slots, a preset's pools
//! and options, tiers and shop prices. What the scans decide comes from the
//! new ones, as the editor would offer it now: a preset's rewards and places
//! from the preset fitted again with the same pools and options (a row of its
//! lists stays the same choice when the new version moves it in another way),
//! a special place from those this game can deliver, the pool's reward from
//! the prepared pool script, a record or recipient chosen in the editor from
//! the new world scan. A creature or container of an area, and a record in
//! one, is found as the game finds it: by the name the scan the configuration
//! was made with (`earlier_world`) shows, wherever it stands now; where it
//! stood only tells several of one name apart. Nothing is added; when its
//! preset has rewards or places it does not have, a note says so.
//!
//! What no longer fits is left out and named. A configuration that cannot be
//! read, whose preset no longer fits the game, or of which no reward remains,
//! is not carried over.

use crate::editor::checked_draft;
use crate::error::{Error, ErrorKind, Result};
use crate::game_names::eet_names;
use crate::json::{dumps_ascii, py_str, Dict, Value};
use crate::original_catalog::{resolve_template, CARRIER_GOES_MISSING, NO_ROUTE};
use crate::original_configuration::{
    counted, fit_from, fit_special_places, fit_special_places_from, placeless_sources, unchanged, FIT_RECORD,
    NO_PLACE_IN_TIER,
};
use crate::original_options::{spell_shield_randomized, spell_shield_spec};
use crate::original_registrations::{target_record, SOURCE_ADAPTER};
use crate::platform::preset_game;
use crate::prepared::applied;
use crate::profiles::{validate_catalog, validate_profile, ItemIndex};
use crate::py::lower;
use crate::scripted_sources::{
    blpool_registration, ADAPTER_ID as POOL_ADAPTER, ITEM as POOL_ITEM, RESOURCE as POOL_RESOURCE,
    SOURCE_ID as POOL_SOURCE,
};
use crate::sources::{
    validate_world, Scans, WorldIndex, DANGLING_INVENTORY, INVALID_INVENTORY, PLAN_FIELDS, PLAN_OPTIONAL_FIELDS,
    PLAN_SCHEMA, UNRESOLVED_STORAGE, UNUSABLE_SOURCE,
};
use crate::virtual_sources::ADAPTER_ID as VIRTUAL_ADAPTER;
use std::cell::{OnceCell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

// Why a choice is left out, beside the reasons a preset's fit gives.
pub const ITEM_ABSENT: &str = "item-absent";
pub const NOT_AVAILABLE: &str = "not-available";
pub const SOURCE_ABSENT: &str = "source-record-absent";
pub const SOURCE_AMBIGUOUS: &str = "source-record-ambiguous";
pub const SOURCE_UNUSABLE: &str = "source-record-unusable";
pub const TARGET_ABSENT: &str = "target-absent";
pub const TARGET_AMBIGUOUS: &str = "target-ambiguous";
pub const TARGET_UNVERIFIED: &str = "target-unverified";
pub const TARGET_UNUSABLE: &str = "target-unusable";
pub const NO_PLACE: &str = NO_PLACE_IN_TIER;
// Why a configuration is not carried over.
pub const UNREADABLE: &str = "it cannot be read";
pub const NOTHING_LEFT: &str = "none of its rewards can be used with the game as it is now";
pub const PRESET_UNFIT: &str = "its preset cannot be used with the game as it is now: ";
/// What a configuration made from a preset is told when the preset has more
/// now.
pub const PRESET_FOLLOWED: &str =
    "It was saved unchanged from its preset, so the preset's new rewards and places are added to it";
pub const PRESET_HAS_MORE: &str =
    "Its preset has rewards or places that it does not have; choosing the preset again in the editor gives them";
const PRESET_FIELDS: [&str; 3] = ["original_template_id", "original_choices", "original_options"];
/// A place's reward slots, as the binding check allows them.
const MAX_CAPACITY: i64 = 0xFFFF_FFFF;
/// Where an area's creature or container stands, as the area file keeps it.
const MAX_COORDINATE: i64 = 0xFFFF;

/// Each saved configuration carried over to these scans, in order.
///
/// A configuration these scans open already is returned as the editor checks
/// it. Otherwise the result is `{"draft", "left_out", "notes"}`: the
/// configuration for these scans, the choices left out (`{"id", "kind",
/// "reason"}`, kind `item`, `reward` or `place`) and what they are in plain
/// words, with what its preset has more now; or `{"draft": null, "reason"}`
/// when it is not carried over.
/// `earlier_world` is the world scan the configurations were made with, if
/// the caller has it. The fits of one preset are shared by the
/// configurations made from it.
pub fn rebase_drafts(
    catalog: &Value,
    world: &Value,
    drafts: &[Value],
    earlier_world: Option<&Value>,
    scans: Option<&Scans>,
) -> Result<Vec<Value>> {
    rebase_with(catalog, world, drafts, earlier_world, scans, None)?.into_iter().collect()
}

/// `rebase_drafts` with each configuration's own outcome: an error the
/// carry-over of one runs into does not stop the others.
pub fn rebase_each(
    catalog: &Value,
    world: &Value,
    drafts: &[Value],
    earlier_world: Option<&Value>,
    scans: Option<&Scans>,
) -> Result<Vec<Result<Value>>> {
    rebase_with(catalog, world, drafts, earlier_world, scans, None)
}

/// `rebase_drafts` with `draft` as every original table the carry-over
/// resolves (a preset's, the special places' lists).
pub fn rebase_drafts_from(
    catalog: &Value,
    world: &Value,
    drafts: &[Value],
    earlier_world: Option<&Value>,
    draft: &Value,
    scans: Option<&Scans>,
) -> Result<Vec<Value>> {
    rebase_with(catalog, world, drafts, earlier_world, scans, Some(draft))?.into_iter().collect()
}

fn rebase_with(
    catalog: &Value,
    world: &Value,
    drafts: &[Value],
    earlier_world: Option<&Value>,
    scans: Option<&Scans>,
    draft: Option<&Value>,
) -> Result<Vec<Result<Value>>> {
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
    let (items, index) = scans.indexes(catalog, world)?;
    let carrier = Carrier {
        catalog,
        world,
        scans,
        items,
        index,
        earlier_world,
        draft,
        presets: RefCell::new(HashMap::new()),
        special: OnceCell::new(),
        earlier: OnceCell::new(),
        now: OnceCell::new(),
    };
    Ok(drafts.iter().map(|draft| carrier.rebase(draft)).collect())
}

fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("")
}

fn texts_of(value: &Value) -> impl Iterator<Item = &str> {
    value.as_list().into_iter().flatten().filter_map(Value::as_str)
}

fn left_out(identity: &str, kind: &str, reason: &str) -> Value {
    Value::Dict(crate::dict! {"id" => identity, "kind" => kind, "reason" => reason})
}

fn set_aside(reason: &str) -> Value {
    Value::Dict(crate::dict! {"draft" => Value::Null, "reason" => reason})
}

/// A preset fitted to the new scans: what moves each of its rows now.
struct Preset {
    profile: Value,
    plan: Value,
    registrations: HashMap<String, Value>,
    places: HashMap<String, Value>,
    /// The item of each identity the fit moves, and the identity of each row
    /// it uses.
    items: HashMap<String, Value>,
    uses: HashMap<String, String>,
    reasons: HashMap<String, String>,
    /// The rows of the preset's lists the fit considers (those its options
    /// enable) by the records they move, the pool's row, and the places its
    /// options enable.
    records: HashMap<String, String>,
    pool: Option<String>,
    place_rows: HashSet<String>,
}

impl Preset {
    fn new(resolved: &Value, fitted: &Value) -> Result<Preset> {
        let (profile, plan) = (fitted.at("profile")?.clone(), fitted.at("plan")?.clone());
        let by_id = |field: &str| -> Result<HashMap<String, Value>> {
            let mut rows = HashMap::new();
            for row in plan.at(field)?.as_list().into_iter().flatten() {
                rows.insert(py_str(row.at("id")?), row.clone());
            }
            Ok(rows)
        };
        let registrations = by_id("registrations")?;
        let places = by_id("target_registrations")?;
        let mut items = HashMap::new();
        for choice in plan.at("sources")?.as_list().into_iter().flatten() {
            let item = choice.at("item")?;
            for field in ["source_ids", "registration_ids"] {
                for identity in texts_of(choice.at(field)?) {
                    items.insert(identity.to_string(), item.clone());
                }
            }
        }
        let mut uses = HashMap::new();
        for (identity, row) in fitted.at("row_ids")?.as_dict().into_iter().flat_map(Dict::iter) {
            uses.insert(py_str(row), identity.to_string());
        }
        let mut reasons = HashMap::new();
        for row in fitted.at("not_used")?.as_list().into_iter().flatten() {
            reasons.insert(py_str(row.at("id")?), py_str(row.at("reason")?));
        }
        let mut records = HashMap::new();
        let mut pool = None;
        for row in resolved.at("items")?.as_list().into_iter().flatten() {
            if !row.get("enabled").is_some_and(Value::truthy) {
                continue;
            }
            if let Some(identities) = row.get("source_ids").and_then(Value::as_list) {
                for identity in identities.iter().filter_map(Value::as_str) {
                    if !records.contains_key(identity) {
                        records.insert(identity.to_string(), py_str(row.at("id")?));
                    }
                }
            }
            if pool.is_none()
                && text(row, "adapter") == "scripted"
                && row.get("source_resource").and_then(Value::as_str) == Some(POOL_RESOURCE)
                && row.get("item_resref").and_then(Value::as_str) == Some(POOL_ITEM)
            {
                pool = Some(py_str(row.at("id")?));
            }
        }
        let mut place_rows = HashSet::new();
        for row in resolved.at("targets")?.as_list().into_iter().flatten() {
            if row.get("enabled").is_some_and(Value::truthy) {
                place_rows.insert(py_str(row.at("id")?));
            }
        }
        Ok(Preset { profile, plan, registrations, places, items, uses, reasons, records, pool, place_rows })
    }

    /// The row of the preset's lists a saved reward choice stands for, if any.
    fn row(&self, identity: &str, registered: bool, saved: Option<&Value>) -> Option<String> {
        if !registered {
            return self.records.get(identity).cloned();
        }
        let saved = saved?;
        let adapter = saved.get("adapter_id").and_then(Value::as_str);
        if matches!(adapter, Some(SOURCE_ADAPTER | VIRTUAL_ADAPTER)) {
            if let Some(selector) = saved.get("selector").and_then(Value::as_str) {
                return Some(selector.to_string());
            }
        }
        if adapter == Some(POOL_ADAPTER) {
            self.pool.clone()
        } else {
            None
        }
    }
}

/// A saved reward choice for these scans: the identity kept, with its
/// registration or None for a record; or why it is left out.
enum Reward {
    Kept(String, Option<Value>),
    LeftOut(String),
}

type Fitted = Rc<std::result::Result<Preset, String>>;
/// What a kept reward choice moves now: its records and its registrations;
/// None for a choice that adds its item.
type Moves = Option<(Vec<String>, Vec<String>)>;
/// What the game finds an area's creature or container by (`handle`).
type Handle = (String, &'static str, String, String);

/// A world scan as the carry-over looks things up in it: recipients and
/// records by identity, each holder's records in the scan's order, and an
/// area's creatures and containers by what the game finds them by.
struct Scan<'v> {
    targets: HashMap<&'v str, &'v Value>,
    sources: HashMap<&'v str, &'v Value>,
    records: HashMap<&'v str, Vec<&'v Value>>,
    alike: HashMap<Handle, Vec<&'v Value>>,
}

impl<'v> Scan<'v> {
    fn new(world: &'v Value) -> Scan<'v> {
        let mut targets: HashMap<&str, &Value> = HashMap::new();
        let mut rows = Vec::new();
        for row in listed(world, "targets") {
            if let Some(identity) = row.get("id").and_then(Value::as_str) {
                if !targets.contains_key(identity) {
                    targets.insert(identity, row);
                    rows.push(row);
                }
            }
        }
        let mut sources: HashMap<&str, &Value> = HashMap::new();
        let mut records: HashMap<&str, Vec<&Value>> = HashMap::new();
        for row in listed(world, "sources") {
            let field = |key: &str| row.get(key).and_then(Value::as_str);
            let (Some(identity), Some(owner), Some(_)) = (field("id"), field("owner"), field("item")) else {
                continue;
            };
            if !sources.contains_key(identity) {
                sources.insert(identity, row);
                records.entry(owner).or_default().push(row);
            }
        }
        let mut alike: HashMap<Handle, Vec<&Value>> = HashMap::new();
        for row in rows {
            if let Some(handle) = handle(row, &targets) {
                alike.entry(handle).or_default().push(row);
            }
        }
        Scan { targets, sources, records, alike }
    }
}

struct Carrier<'a, 's> {
    catalog: &'a Value,
    world: &'a Value,
    scans: &'a Scans<'s>,
    items: &'s ItemIndex,
    index: &'s WorldIndex,
    earlier_world: Option<&'a Value>,
    /// The draft every original table resolves to, when a test patched it in.
    draft: Option<&'a Value>,
    presets: RefCell<HashMap<String, Fitted>>,
    special: OnceCell<HashMap<String, Value>>,
    earlier: OnceCell<Scan<'a>>,
    now: OnceCell<Scan<'a>>,
}

impl<'a, 's> Carrier<'a, 's> {
    fn rebase(&self, draft: &Value) -> Result<Value> {
        if let Ok(checked) = checked_draft(self.catalog, self.world, draft) {
            return Ok(Value::Dict(crate::dict! {"draft" => checked, "left_out" => crate::list![], "notes" => crate::list![]}));
        }
        if !readable(draft) {
            return Ok(set_aside(UNREADABLE));
        }
        match self.carry(draft.at("profile")?, draft.at("plan")?) {
            Err(error) if error.kind == ErrorKind::Profile => Ok(set_aside(&error.message)),
            result => result,
        }
    }

    /// The preset of a plan fitted to the new scans, or why it does not fit
    /// (also a preset or option this version no longer has).
    fn preset(&self, plan: &Value) -> Result<Fitted> {
        let template = text(plan, "original_template_id");
        let (choices, options) = (plan.at("original_choices")?, plan.at("original_options")?);
        let key = dumps_ascii(&crate::list![template, choices.clone(), options.clone()], true);
        if let Some(fitted) = self.presets.borrow().get(&key) {
            return Ok(fitted.clone());
        }
        let fitted = Rc::new(match self.fit_preset(template, choices, options) {
            Ok(preset) => Ok(preset),
            Err(error) if error.is_value_error() => Err(error.message),
            Err(error) => return Err(error),
        });
        self.presets.borrow_mut().insert(key, fitted.clone());
        Ok(fitted)
    }

    fn fit_preset(&self, template: &str, choices: &Value, options: &Value) -> Result<Preset> {
        let game = preset_game(self.world, template)?;
        let names = match game {
            "eet" => Some(eet_names(&crate::paths::path_argument(self.world.at("game_root")?)?)?),
            _ => None,
        };
        let choice = |key: &str| choices.get(key).filter(|value| !value.is_null());
        let resolved = match self.draft {
            Some(draft) => draft.clone(),
            None => resolve_template(
                self.catalog,
                self.world,
                template,
                game,
                choice("items"),
                choice("targets"),
                Some(options),
                names.as_ref(),
            )?,
        };
        let fitted =
            fit_from(self.catalog, self.world, &Value::from(template), Some(self.scans), &|_, _, _| Ok(resolved.clone()))?;
        Preset::new(&resolved, &fitted)
    }

    /// The special places this game can deliver, by identity; found once.
    fn special_places(&self) -> Result<&HashMap<String, Value>> {
        if let Some(special) = self.special.get() {
            return Ok(special);
        }
        let rows = match self.draft {
            Some(draft) => fit_special_places_from(self.catalog, self.world, draft, Some(self.scans)),
            None => fit_special_places(self.catalog, self.world, Some(self.scans)),
        };
        let rows = match rows {
            Ok(rows) => rows,
            Err(error) if error.kind == ErrorKind::Profile => Vec::new(),
            Err(error) => return Err(error),
        };
        let mut special = HashMap::new();
        for row in rows {
            special.insert(py_str(row.at("id")?), row);
        }
        Ok(self.special.get_or_init(|| special))
    }

    /// The scan a configuration was made with, if that is the earlier world.
    fn earlier_scan(&self, fingerprint: &str) -> Option<&Scan<'a>> {
        let world = self.earlier_world.filter(|world| world.is_dict())?;
        if !world.get("fingerprint").is_some_and(|value| value.py_eq(&Value::from(fingerprint))) {
            return None;
        }
        Some(self.earlier.get_or_init(|| Scan::new(world)))
    }

    /// The new world scan, as the carry-over looks things up in it.
    fn scan(&self) -> &Scan<'a> {
        self.now.get_or_init(|| Scan::new(self.world))
    }

    /// Where an area's creature or container of the earlier scan is in the
    /// new one: its identity now, or why it is left out. The one the game
    /// finds by the same name is it, wherever it stands now. Of several of
    /// that name, it is the one nearest to where it stood, unless that one
    /// stands nearer to another of the earlier scan; two as near cannot be
    /// told apart.
    fn same(&self, identity: &str, earlier: &Scan<'a>) -> std::result::Result<String, &'static str> {
        let Some(row) = earlier.targets.get(identity).copied() else {
            return Err(TARGET_ABSENT);
        };
        let Some(handle) = handle(row, &earlier.targets) else {
            return Err(TARGET_ABSENT);
        };
        let none = Vec::new();
        let now = self.scan().alike.get(&handle).unwrap_or(&none);
        let before = earlier.alike.get(&handle).unwrap_or(&none);
        if now.is_empty() {
            return Err(TARGET_ABSENT);
        }
        if now.len() == 1 && before.len() == 1 {
            return Ok(text(now[0], "id").to_string());
        }
        let found = nearest(row, now);
        match (found, found.and_then(|found| nearest(found, before))) {
            (Some(found), Some(back)) if text(back, "id") == identity => Ok(text(found, "id").to_string()),
            (Some(_), Some(_)) => Err(TARGET_ABSENT),
            _ => Err(TARGET_AMBIGUOUS),
        }
    }

    /// Where a saved record is in the new scan: its identity now, or why it
    /// is left out. Its holder is found as a recipient is (an area's creature
    /// or container by name), the record among the holder's records of that
    /// item in the scan's order. Without the scan it was made with, the record
    /// keeps its identity when that still holds the item.
    fn record(&self, identity: &str, item: &str, fingerprint: &str) -> Result<std::result::Result<String, &'static str>> {
        let earlier = self.earlier_scan(fingerprint);
        let before = earlier
            .and_then(|earlier| earlier.sources.get(identity).copied())
            .filter(|before| text(before, "item") == item);
        let (Some(earlier), Some(before)) = (earlier, before) else {
            let same = match self.index.source(self.world, identity) {
                Some(row) => row.at("item")?.as_str() == Some(item),
                None => false,
            };
            return Ok(if same { Ok(identity.to_string()) } else { Err(SOURCE_ABSENT) });
        };
        let first = text(before, "owner");
        let mut owner = first.to_string();
        if earlier.targets.get(first).is_some_and(|holder| handle(holder, &earlier.targets).is_some()) {
            owner = match self.same(first, earlier) {
                Ok(owner) => owner,
                Err(reason) => return Ok(Err(if reason == TARGET_AMBIGUOUS { SOURCE_AMBIGUOUS } else { SOURCE_ABSENT })),
            };
        }
        let of_item = |rows: Option<&Vec<&'a Value>>| -> Vec<String> {
            rows.into_iter().flatten().filter(|row| text(row, "item") == item).map(|row| text(row, "id").to_string()).collect()
        };
        let alike = of_item(earlier.records.get(first));
        let now = of_item(self.scan().records.get(owner.as_str()));
        let place = alike.iter().position(|row| row == identity).unwrap_or(usize::MAX);
        Ok(now.get(place).cloned().ok_or(SOURCE_ABSENT))
    }

    fn source(
        &self,
        identity: &str,
        item: &str,
        registered: bool,
        saved: Option<&Value>,
        preset: Option<&Preset>,
        fingerprint: &str,
    ) -> Result<Reward> {
        let current = if registered { Ok(identity.to_string()) } else { self.record(identity, item, fingerprint)? };
        if let (Some(preset), Ok(current)) = (preset, &current) {
            if let Some(row) = preset.row(current, registered, saved) {
                if let Some(used) = preset.uses.get(&row) {
                    if preset.items.get(used).and_then(Value::as_str) == Some(item) {
                        return Ok(Reward::Kept(used.clone(), preset.registrations.get(used).cloned()));
                    }
                }
                return Ok(Reward::LeftOut(preset.reasons.get(&row).map_or(NOT_AVAILABLE, String::as_str).to_string()));
            }
        }
        let current = match current {
            Ok(current) => current,
            Err(reason) => return Ok(Reward::LeftOut(reason.into())),
        };
        if !registered {
            let row = self.index.source(self.world, &current).ok_or_else(|| Error::key(&current))?;
            if texts_of(row.at("concerns")?).any(|concern| UNUSABLE_SOURCE.contains(&concern)) {
                return Ok(Reward::LeftOut(SOURCE_UNUSABLE.into()));
            }
            return Ok(Reward::Kept(current, None));
        }
        // The pool's reward, as the editor offers it for the pool's mace.
        if saved.is_some_and(|saved| saved.get("adapter_id").and_then(Value::as_str) == Some(POOL_ADAPTER))
            && identity == POOL_SOURCE
            && item == POOL_ITEM
            && self.index.resources.contains(POOL_RESOURCE)
            && applied(text(self.world, "game_root"), "quest_pool")?
        {
            return Ok(Reward::Kept(identity.to_string(), Some(blpool_registration(self.world)?)));
        }
        Ok(Reward::LeftOut(NOT_AVAILABLE.into()))
    }

    /// A saved recipient for these scans: its identity now, with its
    /// registration or None for a recipient of the world scan; or why it is
    /// left out.
    fn place(
        &self,
        identity: &str,
        saved: Option<&Value>,
        fingerprint: &str,
        preset: Option<&Preset>,
    ) -> Result<std::result::Result<(String, Option<Value>), String>> {
        if let Some(saved) = saved {
            let row = saved.get("selector").and_then(Value::as_str);
            if let Some(preset) = preset {
                if let Some(place) = preset.places.get(identity) {
                    return Ok(Ok((identity.to_string(), Some(place.clone()))));
                }
                if let Some(row) = row.filter(|row| preset.place_rows.contains(*row)) {
                    return Ok(Err(preset.reasons.get(row).map_or(NOT_AVAILABLE, String::as_str).to_string()));
                }
            }
            return Ok(match self.special_places()?.get(identity) {
                Some(place) => Ok((identity.to_string(), Some(place.clone()))),
                None => Err(NOT_AVAILABLE.into()),
            });
        }
        let mut identity = identity.to_string();
        let row = match self.earlier_scan(fingerprint) {
            Some(earlier)
                if earlier
                    .targets
                    .get(identity.as_str())
                    .is_some_and(|before| handle(before, &earlier.targets).is_some()) =>
            {
                // An area's creature or container: the one of the same name now.
                match self.same(&identity, earlier) {
                    Ok(now) => identity = now,
                    Err(reason) => return Ok(Err(reason.into())),
                }
                self.index.target(self.world, &identity).ok_or_else(|| Error::key(&identity))?
            }
            _ => {
                let Some(row) = self.index.target(self.world, &identity) else {
                    return Ok(Err(TARGET_ABSENT.into()));
                };
                let kind = row.at("kind")?.as_str().unwrap_or("");
                if kind == "actor" || kind == "container" {
                    // Without the scan it was made with, it cannot be told
                    // which one was chosen.
                    return Ok(Err(TARGET_UNVERIFIED.into()));
                }
                row
            }
        };
        let kind = row.at("kind")?.as_str().unwrap_or("");
        if texts_of(row.at("concerns")?)
            .any(|concern| [UNRESOLVED_STORAGE, DANGLING_INVENTORY, INVALID_INVENTORY].contains(&concern))
        {
            return Ok(Err(TARGET_UNUSABLE.into()));
        }
        if kind == "actor" && !row.at("selector")?.at("embedded_cre_size")?.truthy() {
            let template = row.at("selector")?.at("template_resource")?.as_str();
            if !template.is_some_and(|template| self.index.resources.contains(template) && !self.index.is_unreadable(template))
            {
                return Ok(Err(TARGET_UNUSABLE.into()));
            }
        }
        Ok(Ok((identity, None)))
    }

    fn carry(&self, profile: &Value, plan: &Value) -> Result<Value> {
        let mut left = Vec::new();
        let mut items: HashSet<&str> = HashSet::new();
        let saved_items: Vec<&str> = texts_of(profile.at("items")?).collect();
        for item in &saved_items {
            if self.items.contains(item) {
                items.insert(item);
            } else {
                left.push(left_out(item, "item", ITEM_ABSENT));
            }
        }
        if items.is_empty() && !saved_items.is_empty() {
            return Ok(set_aside(NOTHING_LEFT));
        }
        let fitted;
        let preset = if plan.get("original_template_id").is_some() {
            fitted = self.preset(plan)?;
            match fitted.as_ref() {
                Ok(preset) => Some(preset),
                Err(message) => return Ok(set_aside(&format!("{PRESET_UNFIT}{message}"))),
            }
        } else {
            None
        };
        let mut draft = self.carry_choices(profile, plan, &saved_items, &mut items, preset, &mut left)?;
        let mut told = Vec::new();
        if let Some(preset) = preset {
            let more = more(&preset.plan, draft.as_ref())?;
            if unchanged(profile, plan)? {
                // The preset as it fits now. A reward of it left out above
                // (its places gone) is in it when the preset has another place.
                let mut fresh = preset.profile.clone();
                if let Some(fields) = fresh.as_dict_mut() {
                    for key in ["id", "name"] {
                        fields.insert(key, profile.get(key).cloned().unwrap_or(Value::Null));
                    }
                }
                let candidate = Value::Dict(crate::dict! {"profile" => fresh, "plan" => preset.plan.clone()});
                draft = Some(checked_draft(self.catalog, self.world, &candidate)?);
                let (rewards, _) = held(&preset.plan)?;
                left.retain(|row| text(row, "kind") != "reward" || !rewards.contains(text(row, "id")));
                if more {
                    told.push(Value::from(PRESET_FOLLOWED));
                }
            } else if more {
                told.push(Value::from(PRESET_HAS_MORE));
            }
        }
        let Some(draft) = draft else {
            return Ok(set_aside(NOTHING_LEFT));
        };
        let mut notes = notes(&left);
        notes.extend(told);
        Ok(Value::Dict(crate::dict! {"draft" => draft, "left_out" => left, "notes" => notes}))
    }

    /// The player's choices for these scans, those left out added to `left`;
    /// None when none of its rewards remains.
    fn carry_choices<'p>(
        &self,
        profile: &'p Value,
        plan: &Value,
        saved_items: &[&'p str],
        items: &mut HashSet<&'p str>,
        preset: Option<&Preset>,
        left: &mut Vec<Value>,
    ) -> Result<Option<Value>> {
        let fingerprint = text(plan, "world_fingerprint");
        let mut saved_registrations: HashMap<&str, &Value> = HashMap::new();
        for row in plan.at("registrations")?.as_list().into_iter().flatten() {
            saved_registrations.entry(text(row, "id")).or_insert(row);
        }
        // Each kept choice with what it moves now: (choice, records,
        // registrations), and the identity each saved reward has now.
        let mut choices: Vec<(&Value, Moves)> = Vec::new();
        let mut evidence: HashMap<String, Option<Value>> = HashMap::new();
        let mut seen: HashSet<String> = HashSet::new();
        let mut moves: HashMap<String, String> = HashMap::new();
        for choice in plan.at("sources")?.as_list().into_iter().flatten() {
            let item = text(choice, "item");
            if !items.contains(item) {
                continue;
            }
            if text(choice, "policy") != "relocate" {
                choices.push((choice, None));
                continue;
            }
            let (mut records, mut registrations) = (Vec::new(), Vec::new());
            for (registered, field) in [(false, "source_ids"), (true, "registration_ids")] {
                for identity in texts_of(choice.at(field)?) {
                    let saved = if registered { saved_registrations.get(identity).copied() } else { None };
                    match self.source(identity, item, registered, saved, preset, fingerprint)? {
                        Reward::LeftOut(reason) => left.push(left_out(identity, "reward", &reason)),
                        Reward::Kept(kept, registration) => {
                            if seen.insert(kept.clone()) {
                                if registration.is_none() {
                                    records.push(kept.clone());
                                } else {
                                    registrations.push(kept.clone());
                                }
                                moves.insert(identity.to_string(), kept.clone());
                                evidence.insert(kept, registration);
                            }
                        }
                    }
                }
            }
            choices.push((choice, Some((records, registrations))));
        }
        let mut saved_places: HashMap<&str, &Value> = HashMap::new();
        if let Some(rows) = plan.get("target_registrations").and_then(Value::as_list) {
            for row in rows {
                saved_places.insert(text(row, "id"), row);
            }
        }
        // The places kept, by their identity now, and the identity each saved
        // place has now.
        let (mut targets, mut places) = (Vec::new(), Vec::new());
        let mut capacity: Vec<(String, i64)> = Vec::new();
        let mut done: HashSet<&str> = HashSet::new();
        let mut renamed: HashMap<String, String> = HashMap::new();
        for choice in plan.at("targets")?.as_list().into_iter().flatten() {
            let identity = text(choice, "id");
            if !done.insert(identity) {
                continue;
            }
            let slots = choice.at("capacity")?.as_int().unwrap_or(0);
            let now = match self.place(identity, saved_places.get(identity).copied(), fingerprint, preset)? {
                Err(reason) => {
                    left.push(left_out(identity, "place", &reason));
                    continue;
                }
                Ok((now, Some(mut registration))) => {
                    if let Some(row) = registration.as_dict_mut() {
                        row.insert("capacity", choice.at("capacity")?.clone());
                    }
                    places.push(registration);
                    now
                }
                Ok((now, None)) => now,
            };
            let mut choice = choice.clone();
            if let Some(row) = choice.as_dict_mut() {
                row.insert("id", now.as_str());
            }
            targets.push(choice);
            capacity.push((now.clone(), slots));
            renamed.insert(identity.to_string(), now);
        }
        let mut moved: Vec<String> = choices
            .iter()
            .filter_map(|(_, moves)| moves.as_ref())
            .flat_map(|(records, registrations)| records.iter().chain(registrations))
            .cloned()
            .collect();
        let mut rules = None;
        if plan.get("randomization").is_some() || preset.is_some() {
            let empty = Value::Dict(Dict::new());
            let fresh = match preset {
                Some(preset) => Some(preset.plan.at("randomization")?),
                None => None,
            };
            let own = renamed_rules(plan.get("randomization").unwrap_or(&empty), &moves, &renamed);
            let mut table = randomization(&own, fresh, &moved, &capacity)?;
            if preset.is_some() {
                // A tier whose places are all left out keeps no reward, as the
                // preset's fit has it.
                let mut kinds: HashMap<String, String> = HashMap::new();
                for row in &places {
                    kinds.insert(py_str(row.at("id")?), target_record(row)?.at("kind")?.as_str().unwrap_or("").to_string());
                }
                for choice in &targets {
                    let identity = text(choice, "id");
                    if !kinds.contains_key(identity) {
                        let row = self.index.target(self.world, identity).ok_or_else(|| Error::key(identity))?;
                        kinds.insert(identity.to_string(), row.at("kind")?.as_str().unwrap_or("").to_string());
                    }
                }
                let placeless: HashSet<String> = placeless_sources(&table, &kinds)?.into_iter().collect();
                for identity in &moved {
                    if placeless.contains(identity) {
                        left.push(left_out(identity, "reward", NO_PLACE));
                    }
                }
                moved.retain(|identity| !placeless.contains(identity));
                if let Some(rules) = table.as_dict_mut() {
                    if let Some(sources) = rules.get_mut("sources").and_then(Value::as_dict_mut) {
                        sources.retain(|identity, _| !placeless.contains(identity));
                    }
                    let filters = rules.get_mut("row_filters").and_then(|filters| filters.get_mut("sources"));
                    if let Some(sources) = filters.and_then(Value::as_dict_mut) {
                        sources.retain(|identity, _| !placeless.contains(identity));
                    }
                }
            }
            rules = Some(table);
        }
        let kept: HashSet<&str> = moved.iter().map(String::as_str).collect();
        let (mut sources, mut registrations) = (Vec::new(), Vec::new());
        for (choice, moves) in &choices {
            let Some((records, registered)) = moves else {
                sources.push((*choice).clone());
                continue;
            };
            let records: Vec<&String> = records.iter().filter(|identity| kept.contains(identity.as_str())).collect();
            let registered: Vec<&String> =
                registered.iter().filter(|identity| kept.contains(identity.as_str())).collect();
            let chosen = |field: &str| choice.get(field).is_some_and(Value::truthy);
            if (chosen("source_ids") || chosen("registration_ids")) && records.is_empty() && registered.is_empty() {
                // Nothing of this item moves any more; its rewards are named above.
                items.remove(text(choice, "item"));
                continue;
            }
            let mut choice = (*choice).clone();
            if let Some(row) = choice.as_dict_mut() {
                row.insert("source_ids", Value::from(records.iter().map(|identity| identity.as_str()).collect::<Vec<_>>()));
                row.insert(
                    "registration_ids",
                    Value::from(registered.iter().map(|identity| identity.as_str()).collect::<Vec<_>>()),
                );
            }
            sources.push(choice);
            for identity in registered {
                registrations.push(evidence.get(identity.as_str()).cloned().flatten().unwrap_or(Value::Null));
            }
        }
        if items.is_empty() && !saved_items.is_empty() {
            return Ok(None);
        }
        let mut rebased = profile.clone();
        let fields = rebased.as_dict_mut().ok_or_else(|| profile.subscript_error())?;
        fields.insert("items", Value::from(saved_items.iter().filter(|item| items.contains(*item)).copied().collect::<Vec<_>>()));
        fields.insert("catalog_fingerprint", self.catalog.at("fingerprint")?.clone());
        if let Some(prices) =
            fields.get_mut("shop_pricing").and_then(|pricing| pricing.get_mut("base_prices")).and_then(Value::as_dict_mut)
        {
            prices.retain(|item, _| items.contains(item));
        }
        let rebased = validate_profile(&rebased, self.catalog, false)?;
        let mut result = plan.clone();
        let fields = result.as_dict_mut().ok_or_else(|| plan.subscript_error())?;
        fields.insert("catalog_fingerprint", self.catalog.at("fingerprint")?.clone());
        fields.insert("world_fingerprint", self.world.at("fingerprint")?.clone());
        fields.insert("sources", sources.clone());
        fields.insert("registrations", registrations.clone());
        fields.insert("targets", targets);
        // It is no longer what a preset's fit made.
        fields.remove(FIT_RECORD);
        if !places.is_empty() || preset.is_some() {
            fields.insert("target_registrations", places);
        } else {
            // A configuration that is no preset has original recipients only
            // for its special places.
            fields.remove("target_registrations");
        }
        if let Some(rules) = rules {
            fields.insert("randomization", rules);
        }
        let fresh = preset.map_or(plan, |preset| &preset.plan);
        let records: HashSet<&str> =
            sources.iter().flat_map(|choice| choice.get("source_ids").into_iter().flat_map(texts_of)).collect();
        // A preset's creature replacements as it gives them now; the player's
        // own for their records.
        let mut offered = Dict::new();
        if let Some(rows) = fresh.get("replacements").and_then(Value::as_dict) {
            for (identity, replacement) in rows.iter() {
                let now = if preset.is_some() { Some(identity) } else { moves.get(identity).map(String::as_str) };
                if let Some(now) = now {
                    offered.insert(now, replacement.clone());
                }
            }
        }
        let mut replacements = Dict::new();
        for (identity, replacement) in offered.iter() {
            if records.contains(identity) {
                replacements.insert(identity, replacement.clone());
            }
        }
        let duplicates: Vec<Value> = fresh
            .get("duplicates")
            .and_then(Value::as_list)
            .into_iter()
            .flatten()
            .filter(|copy| kept.contains(text(copy, "source_id")))
            .cloned()
            .collect();
        for (field, value) in [("replacements", Value::Dict(replacements)), ("duplicates", Value::List(duplicates))] {
            if value.truthy() {
                fields.insert(field, value);
            } else {
                fields.remove(field);
            }
        }
        if preset.is_some() {
            if fresh.get("spell_shield").is_some() {
                // The scroll's shop row is randomised exactly when it is one of
                // the rewards kept.
                let null = Value::Null;
                let randomized = spell_shield_randomized(
                    registrations
                        .iter()
                        .filter(|row| text(row, "adapter_id") == SOURCE_ADAPTER)
                        .map(|row| row.get("authored").unwrap_or(&null)),
                );
                fields.insert("spell_shield", spell_shield_spec(self.world, randomized)?);
            } else {
                fields.remove("spell_shield");
            }
            match fresh.get("cursed_disguises") {
                Some(disguises) => fields.insert("cursed_disguises", disguises.clone()),
                None => {
                    fields.remove("cursed_disguises");
                }
            }
        }
        let draft = checked_draft(
            self.catalog,
            self.world,
            &Value::Dict(crate::dict! {"profile" => rebased, "plan" => result}),
        )?;
        Ok(Some(draft))
    }
}

/// The rewards and the places of a configuration, by identity.
fn held(plan: &Value) -> Result<(HashSet<String>, HashSet<String>)> {
    let mut rewards = HashSet::new();
    for choice in plan.at("sources")?.as_list().into_iter().flatten() {
        for field in ["source_ids", "registration_ids"] {
            if let Some(identities) = choice.get(field) {
                rewards.extend(texts_of(identities).map(str::to_string));
            }
        }
    }
    let places = plan.at("targets")?.as_list().into_iter().flatten().map(|choice| text(choice, "id").to_string());
    Ok((rewards, places.collect()))
}

/// Whether a preset as it fits now (`fresh`) has rewards or places that a
/// configuration carried over from it does not have (none when None).
fn more(fresh: &Value, draft: Option<&Value>) -> Result<bool> {
    let (rewards, places) = held(fresh)?;
    let Some(draft) = draft else {
        return Ok(!rewards.is_empty() || !places.is_empty());
    };
    let (own_rewards, own_places) = held(draft.at("plan")?)?;
    Ok(!rewards.is_subset(&own_rewards) || !places.is_subset(&own_places))
}

/// The randomisation rules of the rewards and places kept: a preset's rows
/// as the preset gives them now (`fresh`), the player's own for the rest,
/// and the tiers the player chose for a place's slots beyond the preset's.
/// An exclusion names a slot of a place kept.
fn randomization(saved: &Value, fresh: Option<&Value>, moved: &[String], capacity: &[(String, i64)]) -> Result<Value> {
    let mut rules = fresh.unwrap_or(saved).clone();
    let empty = Value::Dict(Dict::new());
    let fresh = fresh.unwrap_or(&empty);
    let slots: HashMap<&str, i64> = capacity.iter().map(|(identity, slots)| (identity.as_str(), *slots)).collect();
    let places: Vec<String> = capacity.iter().map(|(identity, _)| identity.clone()).collect();
    let mut kept = Vec::new();
    for (name, identities) in [("sources", moved), ("targets", places.as_slice())] {
        let (own, others) = (rules_table(fresh, name), rules_table(saved, name));
        let mut table = Dict::new();
        for identity in identities {
            if let Some(value) = own.and_then(|own| own.get(identity)) {
                let mut value = value.clone();
                let extra = others.and_then(|others| others.get(identity));
                if let (true, Some(Value::List(extra)), Value::List(tiers)) = (name == "targets", extra, &mut value) {
                    if extra.len() > tiers.len() {
                        let start = tiers.len();
                        tiers.extend(extra[start..].iter().cloned());
                    }
                }
                table.insert(identity.as_str(), value);
            } else if let Some(value) = others.and_then(|others| others.get(identity)) {
                table.insert(identity.as_str(), value.clone());
            }
        }
        kept.push(table);
    }
    for rule in kept[0].values_mut() {
        let Some(rule) = rule.as_dict_mut() else { continue };
        if let Some(entries) = rule.get("exclude_targets") {
            let mut within = Vec::new();
            for entry in entries.as_list().into_iter().flatten() {
                let slots = slots.get(entry.at("id")?.as_str().unwrap_or("")).copied().unwrap_or(0);
                if entry.at("ordinal")?.as_int().is_some_and(|ordinal| 1 <= ordinal && ordinal <= slots) {
                    within.push(entry.clone());
                }
            }
            rule.insert("exclude_targets", within);
        }
    }
    let targets = kept.pop().unwrap_or_default();
    let sources = kept.pop().unwrap_or_default();
    let type_name = rules.type_name();
    let fields = rules
        .as_dict_mut()
        .ok_or_else(|| Error::type_error(format!("'{type_name}' object does not support item assignment")))?;
    fields.insert("sources", sources);
    fields.insert("targets", targets);
    let filters = match fresh.get("row_filters") {
        Some(filters) => Some(filters),
        None => saved.get("row_filters"),
    };
    if filters.is_some_and(|filters| !filters.is_null()) {
        let mut row_filters = Dict::new();
        for (name, identities) in [("sources", moved), ("targets", places.as_slice())] {
            let own = fresh.get("row_filters").and_then(|filters| rules_table(filters, name));
            let others = saved.get("row_filters").and_then(|filters| rules_table(filters, name));
            let mut table = Dict::new();
            for identity in identities {
                let value = own.and_then(|own| own.get(identity)).or_else(|| others.and_then(|others| others.get(identity)));
                if let Some(value) = value {
                    table.insert(identity.as_str(), value.clone());
                }
            }
            row_filters.insert(name, table);
        }
        fields.insert("row_filters", row_filters);
    }
    Ok(rules)
}

/// A table of rules (`sources`, `targets`), if it is one.
fn rules_table<'v>(rules: &'v Value, name: &str) -> Option<&'v Dict> {
    rules.get(name).and_then(Value::as_dict)
}

/// A configuration's own rules with its rewards and places as the new scans
/// name them (`sources`, `targets`: saved identity to the one now); those
/// left out are no longer in them.
fn renamed_rules(rules: &Value, sources: &HashMap<String, String>, targets: &HashMap<String, String>) -> Value {
    let mut renamed = rules.clone();
    let rename = |table: &mut Value, names: &HashMap<String, String>| {
        if let Some(rows) = table.as_dict() {
            let mut kept = Dict::new();
            for (identity, value) in rows.iter() {
                if let Some(name) = names.get(identity) {
                    kept.insert(name.as_str(), value.clone());
                }
            }
            *table = Value::Dict(kept);
        }
    };
    for (name, names) in [("sources", sources), ("targets", targets)] {
        if let Some(table) = renamed.get_mut(name) {
            rename(table, names);
        }
        if let Some(table) = renamed.get_mut("row_filters").and_then(|filters| filters.get_mut(name)) {
            rename(table, names);
        }
    }
    if let Some(rules) = renamed.get_mut("sources").and_then(Value::as_dict_mut) {
        for rule in rules.values_mut() {
            let Some(rule) = rule.as_dict_mut() else { continue };
            let mut kept = Vec::new();
            for entry in rule.get("exclude_targets").and_then(Value::as_list).into_iter().flatten() {
                if let Some(now) = entry.get("id").and_then(Value::as_str).and_then(|identity| targets.get(identity)) {
                    let mut entry = entry.clone();
                    if let Some(fields) = entry.as_dict_mut() {
                        fields.insert("id", now.as_str());
                    }
                    kept.push(entry);
                }
            }
            rule.insert("exclude_targets", kept);
        }
    }
    renamed
}

/// The objects of one of a scan's lists, as a scan read from a file has them.
fn listed<'v>(world: &'v Value, key: &str) -> impl Iterator<Item = &'v Value> {
    world.get(key).and_then(Value::as_list).into_iter().flatten().filter(|row| row.is_dict())
}

/// What the game finds an area's creature or container by, letter case
/// aside: a container's name, a creature's script name (its own CRE's or its
/// template's), or for a creature without one (blank or None) its name in
/// the area with its CRE. None for another kind of recipient.
fn handle(row: &Value, targets: &HashMap<&str, &Value>) -> Option<Handle> {
    let kind = row.get("kind").and_then(Value::as_str).filter(|kind| *kind == "actor" || *kind == "container")?;
    let selector = row.get("selector").filter(|selector| selector.is_dict())?;
    let resource = row.get("resource").and_then(Value::as_str)?;
    let name = selector.get("name").and_then(Value::as_str)?;
    if kind == "container" {
        return Some((resource.to_string(), "container", lower(name), String::new()));
    }
    let template = selector.get("template_resource").and_then(Value::as_str).and_then(|identity| targets.get(identity));
    let script = match template.and_then(|template| template.get("selector")).filter(|own| own.is_dict()) {
        Some(own) => own.get("script_name"),
        None => selector.get("script_name"),
    };
    if let Some(script) = script.and_then(Value::as_str).filter(|script| !script.is_empty() && lower(script) != "none") {
        return Some((resource.to_string(), "creature", lower(script), String::new()));
    }
    let creature = selector.get("cre_resref").and_then(Value::as_str).map(lower).unwrap_or_default();
    Some((resource.to_string(), "actor", lower(name), creature))
}

/// Where an area's creature or container stands, if the scan says.
fn position(row: &Value) -> Option<(i64, i64)> {
    let selector = row.get("selector").filter(|selector| selector.is_dict())?;
    let coordinate =
        |key: &str| selector.get(key).and_then(Value::as_int).filter(|value| (0..=MAX_COORDINATE).contains(value));
    Some((coordinate("x")?, coordinate("y")?))
}

/// The one of these rows nearest to where this one stands; None when two
/// are as near, or where one stands is not known.
fn nearest<'v>(row: &Value, rows: &[&'v Value]) -> Option<&'v Value> {
    let here = position(row);
    let (mut least, mut found) = (None, None);
    for &other in rows {
        let (here, there) = (here?, position(other)?);
        let distance = (here.0 - there.0).pow(2) + (here.1 - there.1).pow(2);
        match least {
            Some(shortest) if distance > shortest => {}
            Some(shortest) if distance == shortest => found = None,
            _ => {
                least = Some(distance);
                found = Some(other);
            }
        }
    }
    found
}

fn is_texts(value: Option<&Value>) -> bool {
    value.and_then(Value::as_list).is_some_and(|list| list.iter().all(Value::is_str))
}

/// Whether a value is a list of objects with text in each of these keys.
fn is_rows(value: Option<&Value>, keys: &[&str]) -> bool {
    value.and_then(Value::as_list).is_some_and(|rows| {
        rows.iter().all(|row| row.is_dict() && keys.iter().all(|key| row.get(key).is_some_and(Value::is_str)))
    })
}

/// Whether a saved configuration has the parts the editor saves, of the
/// kinds the carry-over reads; another is damaged or of a version that
/// cannot be read.
fn readable(draft: &Value) -> bool {
    let (Some(profile), Some(plan)) = (draft.get("profile"), draft.get("plan")) else {
        return false;
    };
    let Some(fields) = plan.as_dict() else {
        return false;
    };
    if !profile.is_dict() || !is_texts(profile.get("items")) {
        return false;
    }
    if fields.get("schema").and_then(Value::as_str) != Some(PLAN_SCHEMA)
        || !PLAN_FIELDS.iter().all(|key| fields.contains_key(key))
        || !fields.keys().all(|key| PLAN_FIELDS.contains(&key) || PLAN_OPTIONAL_FIELDS.contains(&key))
    {
        return false;
    }
    let sources = fields.get("sources");
    if !fields.get("world_fingerprint").is_some_and(Value::is_str) || !is_rows(sources, &["item"]) {
        return false;
    }
    for choice in sources.and_then(Value::as_list).into_iter().flatten() {
        match choice.get("policy").and_then(Value::as_str) {
            Some("add") => {}
            Some("relocate") if is_texts(choice.get("source_ids")) && is_texts(choice.get("registration_ids")) => {}
            _ => return false,
        }
    }
    let empty = Value::List(Vec::new());
    if !is_rows(fields.get("registrations"), &["id"])
        || !is_rows(Some(fields.get("target_registrations").unwrap_or(&empty)), &["id"])
    {
        return false;
    }
    let targets = fields.get("targets");
    if !is_rows(targets, &["id"])
        || !targets.and_then(Value::as_list).into_iter().flatten().all(|row| {
            row.get("capacity").and_then(Value::as_int).is_some_and(|capacity| (1..=MAX_CAPACITY).contains(&capacity))
        })
    {
        return false;
    }
    let present = PRESET_FIELDS.iter().filter(|key| fields.contains_key(key)).count();
    if present > 0
        && (present != PRESET_FIELDS.len()
            || !fields.get("original_template_id").is_some_and(Value::is_str)
            || !fields.get("original_choices").is_some_and(Value::is_dict)
            || !fields.get("original_options").is_some_and(Value::is_dict))
    {
        return false;
    }
    if fields.get("replacements").is_some_and(|value| !value.is_dict()) {
        return false;
    }
    if fields.get("duplicates").is_some() && !is_rows(fields.get("duplicates"), &["source_id"]) {
        return false;
    }
    let Some(rules) = fields.get("randomization") else {
        return true;
    };
    let Some(rules) = rules.as_dict() else {
        return false;
    };
    let table = |name: &str| rules.get(name).map_or(Some(None), |value| value.as_dict().map(Some));
    let (Some(sources), Some(targets)) = (table("sources"), table("targets")) else {
        return false;
    };
    for rule in sources.into_iter().flat_map(Dict::values) {
        // A reward's tier and the places its home claims, as the editor and
        // the fit give every reward.
        if !rule.is_dict() || !rule.get("tier").is_some_and(Value::is_str) {
            return false;
        }
        let exclusions = rule.get("exclude_targets");
        if !is_rows(exclusions, &["id"])
            || !exclusions.and_then(Value::as_list).into_iter().flatten().all(|entry| entry.get("ordinal").is_some_and(Value::is_int))
        {
            return false;
        }
    }
    if !targets.into_iter().flat_map(Dict::values).all(|tiers| is_texts(Some(tiers))) {
        return false;
    }
    match rules.get("row_filters") {
        None => true,
        Some(filters) => {
            filters.is_dict() && ["sources", "targets"].iter().all(|name| filters.get(name).is_none_or(Value::is_dict))
        }
    }
}

/// What the left-out choices are, in plain words: the items no longer in
/// the game, the rewards and places this game cannot use as it is now, the
/// places that could not be checked, those on creatures that are never there
/// or always escape, those whose quest CIR cannot change yet, then what
/// becomes of a left-out reward.
fn notes(rows: &[Value]) -> Vec<Value> {
    let kind = |row: &Value| text(row, "kind").to_string();
    let reason = |row: &Value| text(row, "reason").to_string();
    let items: Vec<&Value> = rows.iter().filter(|row| kind(row) == "item").collect();
    let choices: Vec<&Value> = rows.iter().filter(|row| kind(row) != "item").collect();
    let missing: Vec<&Value> = choices.iter().copied().filter(|row| reason(row) == CARRIER_GOES_MISSING).collect();
    let waiting: Vec<&Value> = choices.iter().copied().filter(|row| reason(row) == NO_ROUTE).collect();
    let unverified: Vec<&Value> = choices.iter().copied().filter(|row| reason(row) == TARGET_UNVERIFIED).collect();
    let unusable: Vec<&Value> = choices
        .iter()
        .copied()
        .filter(|row| ![CARRIER_GOES_MISSING, NO_ROUTE, TARGET_UNVERIFIED].contains(&reason(row).as_str()))
        .collect();
    let it_is = |rows: &[&Value]| if rows.len() == 1 { "it is" } else { "they are" };
    let mut notes = Vec::new();
    if !items.is_empty() {
        let one = items.len() == 1;
        notes.push(format!(
            "{} {} from it {} no longer in the game, so {} left out",
            items.len(),
            if one { "item" } else { "items" },
            if one { "is" } else { "are" },
            it_is(&items)
        ));
    }
    if !unusable.is_empty() {
        notes.push(format!(
            "{} from it cannot be used with the game as it is now, so {} left out",
            counted(&unusable),
            it_is(&unusable)
        ));
    }
    if !unverified.is_empty() {
        notes.push(format!(
            "{} from it could not be checked against the scan it was made with, so {} left out",
            counted(&unverified),
            it_is(&unverified)
        ));
    }
    if !missing.is_empty() {
        notes.push(format!(
            "{} from it {}, so {} left out",
            counted(&missing),
            if missing.len() == 1 {
                "is on a creature that is never there or always escapes"
            } else {
                "are on creatures that are never there or always escape"
            },
            it_is(&missing)
        ));
    }
    if !waiting.is_empty() {
        notes.push(format!(
            "{} from it {} that Chriz Item Randomiser cannot change yet, so {} left out",
            counted(&waiting),
            if waiting.len() == 1 { "belongs to a quest" } else { "belong to quests" },
            it_is(&waiting)
        ));
    }
    if rows.iter().any(|row| kind(row) == "reward") {
        notes.push("A left-out reward stays as the game has it".to_string());
    }
    notes.into_iter().map(Value::from).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::parse_lenient;

    fn value(text: &str) -> Value {
        parse_lenient(text).expect("test JSON")
    }

    /// A configuration as the editor saves it: one chest's item to two slots
    /// of another chest, with tiers.
    fn draft(capacity: &str) -> Value {
        value(&format!(
            r#"{{"profile": {{"items": ["synth001"]}},
                "plan": {{"schema": "flir-binding-plan-v1", "catalog_fingerprint": "c", "world_fingerprint": "w",
                          "sources": [{{"item": "synth001", "policy": "relocate",
                                        "source_ids": ["are:ar0200/container/0/item/0"], "registration_ids": []}}],
                          "registrations": [],
                          "targets": [{{"id": "are:ar0200/container/1", "capacity": {capacity}, "optional": false}}],
                          "randomization": {{"sources": {{"are:ar0200/container/0/item/0": {{"tier": "bg2:2",
                              "exclude_targets": [{{"id": "are:ar0200/container/1", "ordinal": 2}}]}}}},
                              "targets": {{"are:ar0200/container/1": ["bg2:2", "bg2:3"]}}}}}}}}"#
        ))
    }

    #[test]
    fn a_saved_configuration_is_read_within_the_binding_checks_bounds() {
        assert!(readable(&draft("2")));
        // A place's slots as the binding check allows them, and no other kind of number.
        assert!(readable(&draft("4294967295")));
        for capacity in ["0", "4294967296", "123456789012345678901234567890", "2.0", "true", "\"2\""] {
            assert!(!readable(&draft(capacity)), "{capacity}");
        }
        for damaged in ["[]", r#"{"profile": {}, "plan": {}}"#, r#"{"profile": {"items": []}, "plan": []}"#] {
            assert!(!readable(&value(damaged)), "{damaged}");
        }
        let mut unknown = draft("2");
        unknown.get_mut("plan").and_then(Value::as_dict_mut).unwrap().insert("notes", Value::List(Vec::new()));
        assert!(!readable(&unknown));
        // A preset's three fields come together.
        let mut preset = draft("2");
        preset.get_mut("plan").and_then(Value::as_dict_mut).unwrap().insert("original_template_id", "bg2-original");
        assert!(!readable(&preset));
    }

    #[test]
    fn the_rules_keep_the_players_tiers_and_exclusions_of_kept_slots() {
        // The preset gives one tier for the chest; the player gave its second slot another.
        let fresh = value(
            r#"{"unrandomized_percent": 10,
                "sources": {"reward": {"tier": "bg2:2", "exclude_targets": [{"id": "home", "ordinal": 1}]}},
                "targets": {"chest": ["bg2:2"], "home": ["bg2:2"]},
                "row_filters": {"sources": {"reward": {"chance": 100, "group": ""}},
                                "targets": {"chest": {"chance": 100, "group": ""},
                                            "home": {"chance": 100, "group": ""}}}}"#,
        );
        let saved = value(
            r#"{"unrandomized_percent": 10,
                "sources": {"reward": {"tier": "bg2:3", "exclude_targets": [{"id": "home", "ordinal": 1}]},
                            "gone": {"tier": "bg2:2", "exclude_targets": []}},
                "targets": {"chest": ["bg2:2", "bg2:3"], "home": ["bg2:2"], "own": ["bg2:3"]},
                "row_filters": {"sources": {}, "targets": {"own": {"chance": 50, "group": "a"}}}}"#,
        );
        let moved = vec!["reward".to_string()];
        let capacity = vec![("chest".to_string(), 2), ("own".to_string(), 1)];
        let rules = randomization(&saved, Some(&fresh), &moved, &capacity).unwrap();
        assert!(rules.py_eq(&value(
            r#"{"unrandomized_percent": 10,
                "sources": {"reward": {"tier": "bg2:2", "exclude_targets": []}},
                "targets": {"chest": ["bg2:2", "bg2:3"], "own": ["bg2:3"]},
                "row_filters": {"sources": {"reward": {"chance": 100, "group": ""}},
                                "targets": {"chest": {"chance": 100, "group": ""},
                                            "own": {"chance": 50, "group": "a"}}}}"#,
        )));
        // A configuration of the editor's own: its rules, the slots it keeps.
        let own = value(
            r#"{"sources": {"reward": {"tier": "bg2:2",
                                       "exclude_targets": [{"id": "chest", "ordinal": 2}, {"id": "chest", "ordinal": 0},
                                                           {"id": "chest", "ordinal": 1}]}},
                "targets": {"chest": ["bg2:2", "bg2:2"]}}"#,
        );
        let rules = randomization(&own, None, &moved, &[("chest".to_string(), 1)]).unwrap();
        assert!(rules.py_eq(&value(
            r#"{"sources": {"reward": {"tier": "bg2:2", "exclude_targets": [{"id": "chest", "ordinal": 1}]}},
                "targets": {"chest": ["bg2:2", "bg2:2"]}}"#,
        )));
    }

    #[test]
    fn the_notes_name_what_was_left_out_in_plain_words() {
        let rows = vec![
            left_out("helm21", "item", ITEM_ABSENT),
            left_out("original-source:bg2:mode2:item:14", "reward", "The route cannot be used"),
            left_out("are:ar0200/container/1", "place", TARGET_AMBIGUOUS),
            left_out("are:ar0200/container/2", "place", TARGET_UNVERIFIED),
            left_out("original-target:bg2:base:target:9002", "place", CARRIER_GOES_MISSING),
            left_out("original-target:bg2:base:target:9003", "place", CARRIER_GOES_MISSING),
            left_out("original-source:bg2:mode2:item:33", "reward", NO_ROUTE),
        ];
        let written: Vec<String> = notes(&rows).iter().map(py_str).collect();
        assert_eq!(
            written,
            [
                "1 item from it is no longer in the game, so it is left out",
                "1 reward and 1 place from it cannot be used with the game as it is now, so they are left out",
                "1 place from it could not be checked against the scan it was made with, so it is left out",
                "2 places from it are on creatures that are never there or always escape, so they are left out",
                "1 reward from it belongs to a quest that Chriz Item Randomiser cannot change yet, so it is left out",
                "A left-out reward stays as the game has it",
            ]
        );
        let places: Vec<String> = notes(&rows[2..3]).iter().map(py_str).collect();
        assert_eq!(places, ["1 place from it cannot be used with the game as it is now, so it is left out"]);
    }

    #[test]
    fn a_configurations_own_rules_follow_its_choices_to_their_identities_now() {
        // The chest moved on in the area; the second place is left out.
        let rules = value(
            r#"{"loss_percent": 0,
                "sources": {"old": {"tier": "bg2:2", "exclude_targets": [{"id": "chest", "ordinal": 1},
                                                                          {"id": "gone", "ordinal": 1}]},
                            "dropped": {"tier": "bg2:2", "exclude_targets": []}},
                "targets": {"chest": ["bg2:2"], "gone": ["bg2:2"]},
                "row_filters": {"sources": {"old": {"chance": 50, "group": ""}},
                                "targets": {"chest": {"chance": 100, "group": ""}}}}"#,
        );
        let sources = HashMap::from([("old".to_string(), "new".to_string())]);
        let targets = HashMap::from([("chest".to_string(), "chest2".to_string())]);
        assert!(renamed_rules(&rules, &sources, &targets).py_eq(&value(
            r#"{"loss_percent": 0,
                "sources": {"new": {"tier": "bg2:2", "exclude_targets": [{"id": "chest2", "ordinal": 1}]}},
                "targets": {"chest2": ["bg2:2"]},
                "row_filters": {"sources": {"new": {"chance": 50, "group": ""}},
                                "targets": {"chest2": {"chance": 100, "group": ""}}}}"#,
        )));
    }

    #[test]
    fn of_several_the_nearest_unless_two_are_as_near() {
        let at = |x: i64, y: i64| value(&format!(r#"{{"selector": {{"x": {x}, "y": {y}}}}}"#));
        let (here, near, far, other) = (at(100, 100), at(103, 104), at(500, 500), at(97, 96));
        assert!(nearest(&here, &[&far, &near]).is_some_and(|found| std::ptr::eq(found, &near)));
        // As near on two sides, or where one stands not known.
        assert!(nearest(&here, &[&near, &other, &far]).is_none());
        assert!(nearest(&here, &[&near, &value(r#"{"selector": {"x": 1}}"#)]).is_none());
        assert!(nearest(&value(r#"{"selector": {"x": 1, "y": 65536}}"#), &[&near]).is_none());
        assert!(nearest(&here, &[]).is_none());
    }

    #[test]
    fn a_preset_is_unchanged_while_it_holds_what_its_fit_made() {
        let profile = value(r#"{"id": "a", "name": "Boss Loot", "items": ["helm21"]}"#);
        let mut plan = value(r#"{"targets": [{"id": "t", "capacity": 1}], "sources": []}"#);
        assert!(!unchanged(&profile, &plan).unwrap()); // Saved by CIR 1.0.3: no record.
        let record = crate::original_configuration::fit_record(&profile, &plan).unwrap();
        plan.as_dict_mut().unwrap().insert(FIT_RECORD, record.as_str());
        assert!(unchanged(&profile, &plan).unwrap());
        // Another identity and name are the same configuration.
        let renamed = value(r#"{"id": "b", "name": "Mine", "items": ["helm21"]}"#);
        assert!(unchanged(&renamed, &plan).unwrap());
        // Another item or slot is a change.
        assert!(!unchanged(&value(r#"{"id": "a", "name": "Boss Loot", "items": []}"#), &plan).unwrap());
        let mut slots = plan.clone();
        slots.at_mut("targets").unwrap().as_list_mut().unwrap()[0].as_dict_mut().unwrap().insert("capacity", 2);
        assert!(!unchanged(&profile, &slots).unwrap());
        plan.as_dict_mut().unwrap().insert(FIT_RECORD, 1);
        assert!(!unchanged(&profile, &plan).unwrap());
    }

    #[test]
    fn a_preset_has_more_than_a_configuration_without_its_rewards_or_places() {
        let plan = |rewards: &str, places: &str| {
            value(&format!(
                r#"{{"sources": [{{"item": "x", "source_ids": [{rewards}], "registration_ids": []}}],
                    "targets": [{places}]}}"#
            ))
        };
        let fresh = plan(r#""r1", "r2""#, r#"{"id": "p1"}, {"id": "p2"}"#);
        let draft = |plan: Value| value(&format!(r#"{{"plan": {}}}"#, dumps_ascii(&plan, true)));
        assert!(!more(&fresh, Some(&draft(plan(r#""r1", "r2", "r3""#, r#"{"id": "p1"}, {"id": "p2"}"#)))).unwrap());
        assert!(more(&fresh, Some(&draft(plan(r#""r1""#, r#"{"id": "p1"}, {"id": "p2"}"#)))).unwrap());
        assert!(more(&fresh, Some(&draft(plan(r#""r1", "r2""#, r#"{"id": "p2"}"#)))).unwrap());
        assert!(more(&fresh, None).unwrap());
    }
}
