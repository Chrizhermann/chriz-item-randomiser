//! The local configuration editor's state and API (the reference's
//! `editor.py` without its HTTP transport). Test-game activation requires
//! the explicit `apply_game` opt-in.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::paths;
use crate::profiles::{
    ensure_output_outside_game, is_profile_id, new_profile_named, read_json, search_items_from, validate_catalog,
    validate_profile, write_json, ItemIndex,
};
use crate::sources::{new_binding_plan, validate_world, WorldIndex};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub const KIND_LABELS: [(&str, &str); 5] = [
    ("actor", "Creature"),
    ("cre-template", "Creature template"),
    ("embedded-cre", "Creature"),
    ("container", "Container"),
    ("store", "Shop"),
];

fn kind_label(kind: &str) -> Result<&'static str> {
    KIND_LABELS.iter().find(|(name, _)| *name == kind).map(|(_, label)| *label).ok_or_else(|| Error::key(kind))
}

const DEFAULT_NAME: &str = "My next adventure";

pub struct Editor {
    pub catalog: Value,
    pub world: Value,
    items: ItemIndex,
    index: WorldIndex,
    pub workspace: PathBuf,
    pub apply_game: Option<PathBuf>,
    sources: HashMap<String, Vec<usize>>,
    draft: Value,
}

/// `int(text)` of any size, with Python's ValueError message.
fn py_int(text: &str) -> Result<Value> {
    crate::py::int_value(text).map_err(Error::value)
}

/// Python's `set(plan)` compared with the expected plan fields: Ok(true)
/// when they are equal, the TypeError Python raises for values that are not
/// iterable or hold unhashable members.
fn plan_fields_match(plan: &Value) -> Result<bool> {
    const REQUIRED: [&str; 6] = ["schema", "catalog_fingerprint", "world_fingerprint", "sources", "targets", "registrations"];
    const OPTIONAL: [&str; 6] = [
        "randomization",
        "replacements",
        "original_options",
        "original_choices",
        "original_template_id",
        "target_registrations",
    ];
    let members: Vec<Option<&str>> = match plan {
        Value::Dict(fields) => fields.keys().map(Some).collect(),
        Value::List(values) => {
            let mut members = Vec::with_capacity(values.len());
            for value in values {
                match value {
                    Value::List(_) | Value::Dict(_) => {
                        return Err(Error::type_error(format!("unhashable type: '{}'", value.type_name())))
                    }
                    Value::Str(text) => members.push(Some(text.as_str())),
                    _ => members.push(None),
                }
            }
            members
        }
        // A string's members are its characters; no field name is one character.
        Value::Str(_) => return Ok(false),
        other => return Err(Error::type_error(format!("'{}' object is not iterable", other.type_name()))),
    };
    let mut seen = std::collections::BTreeSet::new();
    for member in members {
        match member {
            Some(name) if REQUIRED.contains(&name) || OPTIONAL.contains(&name) => {
                seen.insert(name);
            }
            _ => return Ok(false),
        }
    }
    Ok(REQUIRED.iter().all(|name| seen.contains(name)))
}

fn field<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    value.at(key)
}

/// The saved configurations of a workspace, as the editor lists them: its
/// JSON files, not those in its folders.
pub fn saved_files(workspace: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    let entries = match paths::list_dir(workspace) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(files),
        Err(error) => return Err(Error::win(&error, workspace)),
    };
    for name in entries {
        if crate::py::casefold(&name).ends_with(".json") {
            files.push(workspace.join(name));
        }
    }
    files.sort_by(|a, b| paths::path_cmp(a, b));
    Ok(files)
}

/// A configuration (`profile` and `plan`) checked against these scans, as
/// the editor checks what it saves and lists. Incomplete drafts may be
/// saved; full binding checks happen on Check.
pub fn checked_draft(catalog: &Value, world: &Value, data: &Value) -> Result<Value> {
    let profile = validate_profile(field(data, "profile")?, catalog, false)?;
    let plan = field(data, "plan")?.clone();
    let stale = || Error::profile("These selections belong to a different scan or an unsupported draft");
    if !plan_fields_match(&plan)? {
        return Err(stale());
    }
    // Only a list of the field names gets here without being a dict.
    let fields = plan.as_dict().ok_or_else(|| plan.subscript_error())?;
    let fingerprint = |value: &Value| value.get("fingerprint").cloned().unwrap_or(Value::Null);
    if fields.get("schema").and_then(Value::as_str) != Some("flir-binding-plan-v1")
        || !fields.get("catalog_fingerprint").unwrap().py_eq(&fingerprint(catalog))
        || !fields.get("world_fingerprint").unwrap().py_eq(&fingerprint(world))
        || ["sources", "targets", "registrations"].iter().any(|key| !fields.get(key).unwrap().is_list())
        || fields.get("target_registrations").is_some_and(|value| !value.is_list())
    {
        return Err(stale());
    }
    Ok(Value::Dict(crate::dict! {"profile" => profile, "plan" => plan}))
}

impl Editor {
    pub fn new(
        catalog: Value,
        world: Value,
        workspace: &Path,
        profile: Option<Value>,
        plan: Option<Value>,
        apply_game: Option<&Path>,
    ) -> Result<Editor> {
        let items = validate_catalog(&catalog)?;
        let index = validate_world(&world, &catalog)?;
        let workspace = ensure_output_outside_game(workspace, &catalog)?;
        let apply_game = match apply_game {
            Some(game) => Some(crate::activation::validate_runtime(game, &catalog)?),
            None => None,
        };
        let mut sources: HashMap<String, Vec<usize>> = HashMap::new();
        for (position, source) in world.at("sources")?.as_list().into_iter().flatten().enumerate() {
            let item = source.get("item").and_then(Value::as_str).unwrap_or("").to_string();
            sources.entry(item).or_default().push(position);
        }
        if profile.is_none() != plan.is_none() {
            return Err(Error::profile("Supply both an initial profile and its binding plan"));
        }
        let mut editor = Editor {
            catalog,
            world,
            items,
            index,
            workspace,
            apply_game,
            sources,
            draft: Value::Null,
        };
        let (mut profile, mut plan) = (profile, plan);
        let applied = editor.workspace.join("prepared").join("last-applied.json");
        if profile.is_none()
            && editor.apply_game.is_some()
            && paths::is_file(&applied).map_err(|error| Error::win(&error, &applied))?
        {
            let pointer = read_json(&applied)?;
            let identity = pointer.at("id")?.clone();
            let previous = read_json(&editor.path(&identity)?)?;
            profile = Some(previous.at("profile")?.clone());
            plan = Some(previous.at("plan")?.clone());
        }
        let (profile, plan) = match (profile, plan) {
            (Some(profile), Some(plan)) => (profile, plan),
            _ => Editor::new_draft(&editor.catalog, &editor.world)?,
        };
        editor.draft = editor.checked(&Value::Dict(crate::dict! {"profile" => profile, "plan" => plan}))?;
        Ok(editor)
    }

    pub fn new_id() -> String {
        format!("adventure-{}", crate::profiles::random_hex(12))
    }

    /// The profile and binding plan of a new configuration for these scans,
    /// as the editor starts with when it reopens none.
    pub fn new_draft(catalog: &Value, world: &Value) -> Result<(Value, Value)> {
        let profile = new_profile_named(catalog, &Editor::new_id(), &Value::from(DEFAULT_NAME))?;
        let plan = new_binding_plan(&profile, catalog, world)?;
        Ok((profile, plan))
    }

    /// Incomplete drafts may be saved; full binding checks happen on Check.
    pub fn checked(&self, data: &Value) -> Result<Value> {
        checked_draft(&self.catalog, &self.world, data)
    }

    pub fn check(&self, draft: &Value) -> Result<Value> {
        let mut result = crate::dict! {
            "valid" => false, "runtime_supported" => false, "reward_units" => 0, "target_capacity" => 0,
        };
        let attempt = (|| -> Result<()> {
            let campaign = crate::campaigns::compile_campaign(
                field(draft, "profile")?,
                &self.catalog,
                &self.world,
                field(draft, "plan")?,
            )?;
            let mut units: i128 = 0;
            for source in campaign.at("sources")?.as_list().into_iter().flatten() {
                units += source.at("quantity")?.as_int().unwrap_or(0) as i128;
            }
            let mut capacity: i128 = 0;
            for target in campaign.at("targets")?.as_list().into_iter().flatten() {
                capacity += target.at("capacity")?.as_int().unwrap_or(0) as i128;
            }
            result.insert("valid", true);
            result.insert("reward_units", Value::from(units));
            result.insert("target_capacity", Value::from(capacity));
            crate::native_export::compile(&campaign, &self.world, &self.index, &self.items, &self.catalog, None, false, None)?;
            Ok(())
        })();
        match attempt {
            Ok(()) => {
                result.insert("runtime_supported", true);
                result.insert(
                    "message",
                    "Configuration checks passed for the current test runtime. \
                     Game testing and deployment are separate; nothing was activated.",
                );
                Ok(Value::Dict(result))
            }
            Err(error) if error.kind == crate::error::ErrorKind::Profile => {
                result.insert("message", error.message);
                Ok(Value::Dict(result))
            }
            Err(error) => Err(error),
        }
    }

    pub fn path(&self, identity: &Value) -> Result<PathBuf> {
        match identity.as_str() {
            Some(identity) if is_profile_id(identity) => {
                ensure_output_outside_game(&self.workspace.join(format!("{identity}.json")), &self.catalog)
            }
            _ => Err(Error::profile("Unknown configuration")),
        }
    }

    fn template_of(&self, selector: &Value) -> Option<&Value> {
        let reference = selector.get("template_resource")?.as_str()?;
        self.index.target(&self.world, reference)
    }

    pub fn label(&self, target: &Value) -> Result<String> {
        let selector = field(target, "selector")?;
        let template_selector = self.template_of(selector).and_then(|template| template.get("selector"));
        let candidates = [
            selector.get("name"),
            selector.get("script_name"),
            template_selector.and_then(|selector| selector.get("script_name")),
            selector.get("cre_resref"),
        ];
        let name = match candidates.into_iter().flatten().find(|value| value.truthy()) {
            Some(value) => crate::json::py_str(value),
            None => crate::json::py_str(field(target, "id")?),
        };
        let resource = field(target, "resource")?.as_str().unwrap_or("");
        let suffix = resource.split_once(':').map(|(_, rest)| rest).ok_or_else(|| Error::value("list index out of range"))?;
        Ok(format!("{name} ({})", crate::py::upper(suffix)))
    }

    pub fn target(&self, row: &Value) -> Result<Value> {
        let identity = field(row, "id")?.clone();
        let label = self.label(row)?;
        let kind = field(row, "kind")?.as_str().unwrap_or("");
        let kind_label = kind_label(kind)?;
        let note = match kind {
            "actor" => "Encounter availability and drops need a game test.",
            "cre-template" => "Creature definition; choosing a specific spawn still needs runtime support.",
            "container" => "Container destination; existing contents, locks and traps are preserved.",
            "store" => "Shop destination; prototype delivery is available when this configuration passes its check.",
            other => return Err(Error::key(other)),
        };
        Ok(Value::Dict(crate::dict! {
            "id" => identity, "label" => label, "kind" => kind, "kind_label" => kind_label, "note" => note,
        }))
    }

    pub fn search_text(&self, row: &Value) -> Result<String> {
        let selector = field(row, "selector")?;
        let identity = field(row, "id")?;
        let label = self.label(row)?;
        let Some(identity) = identity.as_str() else {
            return Err(Error::type_error(format!(
                "sequence item 0: expected str instance, {} found",
                identity.type_name()
            )));
        };
        let mut names = vec![identity.to_string(), label];
        let template_selector = self.template_of(selector).and_then(|template| template.get("selector")).cloned();
        let empty = Value::Dict(Dict::new());
        for fields in [selector, template_selector.as_ref().unwrap_or(&empty)] {
            for key in ["name", "script_name", "cre_resref"] {
                match fields.get(key) {
                    None => names.push(String::new()),
                    Some(Value::Str(text)) => names.push(text.clone()),
                    Some(other) => {
                        return Err(Error::type_error(format!(
                            "sequence item {}: expected str instance, {} found",
                            names.len(),
                            other.type_name()
                        )))
                    }
                }
            }
        }
        Ok(crate::py::casefold(&names.join(" ")))
    }

    fn saved_files(&self) -> Result<Vec<PathBuf>> {
        saved_files(&self.workspace)
    }

    pub fn state(&self) -> Result<Value> {
        let mut saved = Vec::new();
        for path in self.saved_files()? {
            let value = self.checked(&read_json(&path)?)?;
            let profile = field(&value, "profile")?;
            saved.push(Value::Dict(crate::dict! {"id" => field(profile, "id")?.clone(), "name" => field(profile, "name")?.clone()}));
        }
        let plan = field(&self.draft, "plan")?;
        let mut authored_targets: HashMap<String, &Value> = HashMap::new();
        if let Some(rows) = plan.get("target_registrations").and_then(Value::as_list) {
            for row in rows {
                let key = row.get("id").map(crate::json::py_repr).unwrap_or_else(|| "None".into());
                authored_targets.insert(key, row);
            }
        }
        let mut targets = Vec::new();
        for choice in field(plan, "targets")?.as_list().into_iter().flatten() {
            let identity = field(choice, "id")?;
            let row = identity.as_str().and_then(|id| self.index.target(&self.world, id));
            let authored = authored_targets
                .get(&crate::json::py_repr(identity))
                .and_then(|row| row.get("authored"))
                .cloned()
                .unwrap_or(Value::Dict(Dict::new()));
            let mut label = if let Some(row) = row {
                self.target(row)?.as_dict().cloned().unwrap()
            } else if authored.truthy() {
                let pick = |keys: &[&str], fallback: &Value| {
                    keys.iter().filter_map(|key| authored.get(key)).find(|value| value.truthy()).cloned().unwrap_or(fallback.clone())
                };
                let name = pick(&["target", "endpoint_id"], identity);
                let area = pick(&["area_resource", "area"], &Value::from(""));
                crate::dict! {
                    "id" => identity.clone(),
                    "label" => format!("{} ({})", crate::json::py_str(&name), crate::json::py_str(&area)),
                    "kind" => "original", "kind_label" => "Original recipient",
                }
            } else {
                crate::dict! {"id" => identity.clone(), "label" => identity.clone(), "kind" => "missing"}
            };
            label.insert("capacity", field(choice, "capacity")?.clone());
            targets.push(Value::Dict(label));
        }
        let mut items = Vec::new();
        for resref in field(field(&self.draft, "profile")?, "items")?.as_list().into_iter().flatten() {
            let resref = resref.as_str().unwrap_or("");
            items.push(self.items.row_value(&self.catalog, resref).cloned().ok_or_else(|| Error::key(resref))?);
        }
        let game_root = self.catalog.at("game_root")?.as_str().unwrap_or("");
        let game_name = paths::name(&paths::parent(Path::new(game_root)));
        let mut state = self.draft.as_dict().cloned().unwrap_or_default();
        state.insert("apply_enabled", self.apply_game.is_some());
        state.insert("items", Value::List(items));
        state.insert("targets", Value::List(targets));
        state.insert("saved", Value::List(saved));
        state.insert("workspace", self.workspace.to_string_lossy().into_owned());
        state.insert(
            "catalog",
            crate::dict! {
                "item_count" => self.items.len(), "language" => self.catalog.at("language")?.clone(),
                "game_name" => game_name,
            },
        );
        Ok(Value::Dict(state))
    }

    /// Twenty rows from `offset` (a Python integer of any size).
    fn page(rows: Vec<Value>, offset: &Value) -> Result<Dict> {
        let start = match offset {
            Value::Int(value) if *value >= 0 => usize::try_from(*value).unwrap_or(usize::MAX),
            Value::BigInt(text) if !text.starts_with('-') => usize::MAX,
            _ => return Err(Error::profile("Search offset must be nonnegative")),
        };
        let first = start.min(rows.len());
        let end = start.saturating_add(20).min(rows.len());
        let next_offset = match start.checked_add(20) {
            Some(next) if next < rows.len() => Value::from(next),
            _ => Value::Null,
        };
        Ok(crate::dict! {
            "total" => rows.len(), "offset" => offset.clone(), "records" => rows[first..end].to_vec(),
            "next_offset" => next_offset,
        })
    }

    pub fn get(&mut self, route: &str, params: &HashMap<String, String>) -> Result<Value> {
        match route {
            "/api/state" => return self.state(),
            "/api/templates" => {
                return Ok(crate::templates::list_templates(Some(&crate::platform::preset_campaigns(&self.world)?)));
            }
            "/api/recommended" => {
                let mut preset = crate::templates::recommended_preset(crate::platform::recommended_campaign(&self.world)?)?;
                // An option whose change the installer could not make is not offered.
                let mut unavailable = Dict::new();
                let game_root = self.world.at("game_root")?.as_str().unwrap_or("").to_string();
                if !crate::prepared::applied(&game_root, "flail_heads")? {
                    preset.get_mut("options").and_then(Value::as_dict_mut).unwrap().insert("flail_heads", false);
                    unavailable.insert("flail_heads", crate::prepared::left_out(&game_root, "flail_heads")?);
                }
                preset.insert("unavailable", unavailable);
                return Ok(Value::Dict(preset));
            }
            _ => {}
        }
        let offset = py_int(params.get("offset").map(String::as_str).unwrap_or("0"))?;
        let query = crate::py::strip(params.get("q").map(String::as_str).unwrap_or("")).to_string();
        match route {
            "/api/items" => {
                if query.is_empty() {
                    return Ok(Value::Dict(crate::dict! {
                        "total" => 0, "offset" => 0, "items" => crate::list![], "next_offset" => Value::Null,
                    }));
                }
                search_items_from(&self.catalog, &query, None, &offset, 20)
            }
            "/api/sources" => {
                let item = crate::py::lower(params.get("item").map(String::as_str).unwrap_or(""));
                if !self.items.contains(&item) {
                    return Err(Error::profile("Unknown item"));
                }
                let world_sources = self.world.at("sources")?.as_list().cloned().unwrap_or_default();
                let mut rows: Vec<Value> =
                    self.sources.get(&item).into_iter().flatten().map(|position| world_sources[*position].clone()).collect();
                let game_root = self.world.at("game_root")?.as_str().unwrap_or("").to_string();
                if item == "blun12"
                    && self.index.resources.contains("bcs:blpool")
                    && crate::prepared::applied(&game_root, "quest_pool")?
                {
                    let registration = crate::scripted_sources::blpool_registration(&self.world)?;
                    rows.insert(
                        0,
                        Value::Dict(crate::dict! {
                            "id" => registration.at("id")?.clone(), "kind" => "scripted",
                            "concerns" => crate::list![], "registration" => registration,
                        }),
                    );
                }
                let mut page = Editor::page(rows, &offset)?;
                let records = page.get("records").and_then(Value::as_list).cloned().unwrap_or_default();
                let mut shown = Vec::with_capacity(records.len());
                for source in records {
                    let kind = field(&source, "kind")?.as_str().unwrap_or("").to_string();
                    if kind == "scripted" {
                        let mut row = source.as_dict().cloned().unwrap();
                        row.insert("quantity", 1);
                        row.insert("kind_label", "Quest reward");
                        row.insert("label", "Blood pool reward (BLPOOL)");
                        row.insert(
                            "note",
                            "Moves the Mace of Disruption; the pool's other actions remain. \
                             Chriz Item Randomiser prepared the pool's script when it was installed.",
                        );
                        shown.push(Value::Dict(row));
                        continue;
                    }
                    let record = field(&source, "record")?;
                    let quantity = if kind == "store" { field(record, "quantity")?.clone() } else { Value::Int(1) };
                    let owner = field(&source, "owner")?;
                    let owner_label = match owner.as_str().and_then(|owner| self.index.target(&self.world, owner)) {
                        Some(target) => self.label(target)?,
                        None => crate::json::py_str(owner),
                    };
                    let note = if kind == "container" {
                        "Container source; check configuration for current runtime support."
                    } else {
                        "This source type still needs runtime support."
                    };
                    shown.push(Value::Dict(crate::dict! {
                        "id" => field(&source, "id")?.clone(), "kind" => kind.clone(),
                        "concerns" => field(&source, "concerns")?.clone(), "quantity" => quantity,
                        "kind_label" => kind_label(&kind)?,
                        "label" => format!("{owner_label} \u{b7} item record {}", crate::json::py_str(field(record, "record_index")?)),
                        "note" => note,
                    }));
                }
                page.insert("records", Value::List(shown));
                Ok(Value::Dict(page))
            }
            "/api/targets" => {
                let kind = params.get("kind").map(String::as_str).unwrap_or("all");
                let kinds: &[&str] = match kind {
                    "all" => &["actor", "cre-template", "embedded-cre", "container", "store"],
                    "creature" => &["actor", "cre-template"],
                    "container" => &["container"],
                    "store" => &["store"],
                    _ => return Err(Error::profile("Unknown recipient type")),
                };
                if query.is_empty() {
                    return Ok(Value::Dict(Editor::page(Vec::new(), &Value::Int(0))?));
                }
                let folded = crate::py::casefold(&query);
                let terms = crate::py::split_whitespace(&folded);
                let mut rows = Vec::new();
                for row in self.world.at("targets")?.as_list().into_iter().flatten() {
                    if !kinds.contains(&field(row, "kind")?.as_str().unwrap_or("")) {
                        continue;
                    }
                    let text = self.search_text(row)?;
                    if terms.iter().all(|term| text.contains(term)) {
                        rows.push(row.clone());
                    }
                }
                let mut page = Editor::page(rows, &offset)?;
                let records = page.get("records").and_then(Value::as_list).cloned().unwrap_or_default();
                let mut shown = Vec::with_capacity(records.len());
                for row in &records {
                    shown.push(self.target(row)?);
                }
                page.insert("records", Value::List(shown));
                Ok(Value::Dict(page))
            }
            _ => Err(Error::profile("Unknown editor request")),
        }
    }

    pub fn post(&mut self, route: &str, data: &Value) -> Result<Value> {
        match route {
            "/api/load" => {
                let path = self.path(field(data, "id")?)?;
                self.draft = self.checked(&read_json(&path)?)?;
            }
            "/api/new" => {
                let name = match data.get("name") {
                    Some(value) if value.truthy() => value.clone(),
                    _ => Value::from(DEFAULT_NAME),
                };
                let profile = new_profile_named(&self.catalog, &Editor::new_id(), &name)?;
                let plan = new_binding_plan(&profile, &self.catalog, &self.world)?;
                self.draft = Value::Dict(crate::dict! {"profile" => profile, "plan" => plan});
            }
            "/api/template" => {
                let generated = crate::original_configuration::fit_original_configuration(
                    &self.catalog,
                    &self.world,
                    field(data, "id")?,
                    data.get("items"),
                    data.get("targets"),
                    data.get("options"),
                )?;
                let mut candidate = generated.clone();
                candidate
                    .get_mut("profile")
                    .and_then(Value::as_dict_mut)
                    .ok_or_else(|| Error::key("profile"))?
                    .insert("id", Editor::new_id());
                let checked = self.checked(&candidate)?;
                let previous = std::mem::replace(&mut self.draft, checked);
                let state = (|| -> Result<Value> {
                    let mut state = self.state()?.as_dict().cloned().unwrap();
                    state.insert("left_out", field(&generated, "left_out")?.clone());
                    state.insert("absent_mods", field(&generated, "absent_mods")?.clone());
                    state.insert("not_used", field(&generated, "not_used_notes")?.clone());
                    state.insert("not_used_rows", field(&generated, "not_used")?.clone());
                    Ok(Value::Dict(state))
                })();
                if state.is_err() {
                    self.draft = previous;
                }
                return state;
            }
            "/api/save" => {
                let mut draft = self.checked(data)?;
                if matches!(data.get("copy"), Some(Value::Bool(true))) {
                    draft.get_mut("profile").and_then(Value::as_dict_mut).unwrap().insert("id", Editor::new_id());
                }
                let identity = draft.at("profile")?.at("id")?.clone();
                write_json(&self.path(&identity)?, &draft, false)?;
                self.draft = draft;
            }
            "/api/validate" => {
                let draft = self.checked(data)?;
                return self.check(&draft);
            }
            "/api/prepare" | "/api/apply" => {
                if route == "/api/apply" && self.apply_game.is_none() {
                    return Err(Error::profile("Game activation is not enabled for this editor"));
                }
                let draft = self.checked(data)?;
                let result = self.check(&draft)?;
                if !result.get("runtime_supported").is_some_and(Value::truthy) {
                    return Err(Error::profile(result.get("message").and_then(Value::as_str).unwrap_or("").to_string()));
                }
                let store = ensure_output_outside_game(&self.workspace.join("prepared"), &self.catalog)?;
                if route == "/api/apply" {
                    let game = self.apply_game.clone().expect("checked");
                    crate::activation::require_closed(&game)?;
                    let written =
                        crate::starts::write_starts(draft.at("profile")?, draft.at("plan")?, &self.catalog, &self.world, &store)?;
                    let output = self.workspace.join("native");
                    let starts: Vec<(String, String)> = written
                        .at("starts")?
                        .as_dict()
                        .into_iter()
                        .flat_map(|starts| starts.iter())
                        .map(|(start, revision)| (start.to_string(), crate::json::py_str(revision)))
                        .collect();
                    let main = written.at("main")?.as_str();
                    let exported =
                        crate::native_export::export_native(&store, &self.catalog, &self.world, &output, None, Some(&starts), main)?;
                    let identity = draft.at("profile")?.at("id")?.clone();
                    write_json(&self.path(&identity)?, &draft, false)?;
                    crate::activation::deploy_export(&game, &output)?;
                    self.draft = draft;
                    let remembered = write_json(
                        &store.join("last-applied.json"),
                        &Value::Dict(crate::dict! {"id" => identity}),
                        false,
                    );
                    if let Err(error) = remembered {
                        if error.kind == crate::error::ErrorKind::Os {
                            return Err(Error::profile(format!(
                                "Game settings were applied and saved, but the editor could not \
                                 remember the selection for reopening: {}",
                                error.message
                            )));
                        }
                        return Err(error);
                    }
                    let full = crate::json::py_str(written.at("full")?);
                    let notes: String = exported
                        .get("notes")
                        .map(crate::py::iter_value)
                        .transpose()?
                        .unwrap_or_default()
                        .iter()
                        .map(|note| format!(" {}.", crate::json::py_str(note)))
                        .collect();
                    return Ok(Value::Dict(crate::dict! {
                        "revision" => written.at("starts")?.at(&full)?.clone(),
                        "starts" => written.at("starts")?.clone(),
                        "game" => game.to_string_lossy().into_owned(),
                        "message" => format!(
                            "Saved and applied. {} Each new game randomizes afresh; existing saves keep their settings.{notes}",
                            crate::starts::describe_starts(&written)?
                        ),
                    }));
                }
                let prepared = crate::campaigns::write_campaign(
                    draft.at("profile")?,
                    &self.catalog,
                    &self.world,
                    draft.at("plan")?,
                    &store,
                )?;
                return Ok(Value::Dict(crate::dict! {
                    "revision" => prepared.at("revision")?.clone(), "store" => store.to_string_lossy().into_owned(),
                    "message" => "Test settings prepared. They can now be exported for the test game; \
                                  nothing was installed or activated.",
                }));
            }
            _ => return Err(Error::profile("Unknown editor request")),
        }
        self.state()
    }
}
