//! Which extended reward routes a game can prepare, read once per scan (the
//! reference's `reward_routes.py`).
//!
//! The configuration check and Apply run the same recognition
//! ([`mode2_rewards::route_reasons`], [`container_rewards::route_reasons`])
//! on the game's current bytes. A route the check accepts is one the export
//! can prepare; one it cannot prepare is left out of the configuration with
//! its reason instead of failing at Apply.
//!
//! A quest route needs its grant recognised in each of its resources, and so
//! does every route that shares its reward event (the egg deal's two
//! dialogues, the three sculptors). Routes with Item Randomiser's reward text
//! need the English game text and the BGEE.LUA journal table, as the export
//! does. Reading the resources once per scan keeps the repeated checks of a
//! fit cheap; Apply still reads and verifies the bytes it patches.

use crate::container_rewards;
use crate::error::{Error, ErrorKind, Result, CHANGED_SINCE_SCAN};
use crate::json::Value;
use crate::mode2_rewards;
use crate::py::str_value;
use crate::resources::{resource_type, sha256_hex, GameResources};
use std::collections::{BTreeSet, HashMap};
use std::path::Path;
use std::sync::{Arc, Mutex};

pub struct RewardRoutes {
    english: bool,
    journal: bool,
    changed: BTreeSet<String>,
    /// (resource, route or container target id) to why it cannot be prepared.
    reasons: HashMap<(String, String), String>,
}

type Key = (String, String, String);

/// The scans read so far, oldest first; at most four.
static CACHE: Mutex<Vec<(Key, Arc<RewardRoutes>)>> = Mutex::new(Vec::new());

/// The reward routes of the scanned game, read on first use per scan.
pub fn reward_routes(catalog: &Value, world: &Value) -> Result<Arc<RewardRoutes>> {
    let key = (str_value(catalog.at("game_root")?), str_value(catalog.at("language")?), str_value(world.at("fingerprint")?));
    let mut cache = CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some((_, routes)) = cache.iter().find(|(known, _)| *known == key) {
        return Ok(routes.clone());
    }
    if cache.len() >= 4 {
        cache.remove(0);
    }
    let routes = Arc::new(RewardRoutes::new(Path::new(&key.0), &key.1, world)?);
    cache.push((key, routes.clone()));
    Ok(routes)
}

fn resources_of(spec: &Value) -> Result<Vec<String>> {
    Ok(crate::py::iter_value(spec.at("resources")?)?.iter().map(str_value).collect())
}

impl RewardRoutes {
    fn new(game_root: &Path, language: &str, world: &Value) -> Result<RewardRoutes> {
        let mut scanned: HashMap<String, Value> = HashMap::new();
        for row in crate::py::iter_value(world.at("resources")?)? {
            scanned.insert(str_value(row.at("id")?), row.at("sha256")?.clone());
        }
        let resources: BTreeSet<String> = mode2_rewards::resource_rewards()
            .keys()
            .chain(container_rewards::resource_targets().keys())
            .filter(|resource| scanned.contains_key(*resource))
            .map(str::to_string)
            .collect();
        let english = crate::py::lower(language) == "en_us";
        let mut changed = BTreeSet::new();
        let mut reasons = HashMap::new();
        let types = [("bcs", resource_type("bcs")), ("dlg", resource_type("dlg")), ("lua", resource_type("lua"))];
        let journal = GameResources::with(game_root, &types, |reader| {
            let journal = reader.names("lua")?.iter().any(|name| name == "bgee");
            let present: HashMap<&str, BTreeSet<String>> =
                [("bcs", reader.names("bcs")?.into_iter().collect()), ("dlg", reader.names("dlg")?.into_iter().collect())]
                    .into_iter()
                    .collect();
            for resource in &resources {
                let (kind, name) = resource.split_once(':').expect("kind:name");
                if !present[kind].contains(name) {
                    changed.insert(resource.clone());
                    continue;
                }
                let data = reader.read(kind, name)?;
                let quest = mode2_rewards::resource_rewards().contains_key(resource);
                let own = if quest { mode2_rewards::own_patch(resource, &data) } else { container_rewards::own_patch(resource, &data) };
                let own = match own {
                    Ok(own) => own,
                    // A changed copy of our patch: route_reasons names it.
                    Err(error) if error.kind == ErrorKind::Profile => true,
                    Err(error) => return Err(error),
                };
                if !own && !scanned[resource].py_eq(&Value::from(sha256_hex(&data))) {
                    changed.insert(resource.clone());
                    continue;
                }
                let found = if quest {
                    mode2_rewards::route_reasons(resource, &data)?
                } else {
                    container_rewards::route_reasons(resource, &data)?
                };
                for (key, reason) in found.iter() {
                    reasons.insert((resource.clone(), key.to_string()), str_value(reason));
                }
            }
            Ok(journal)
        })?;
        Ok(RewardRoutes { english, journal, changed, reasons })
    }

    fn unchanged(&self, resources: &[String]) -> Result<()> {
        for resource in resources {
            if self.changed.contains(resource) {
                return Err(Error::profile_subclass(
                    CHANGED_SINCE_SCAN,
                    format!("Installed resource changed; rescan before export: {resource}"),
                ));
            }
        }
        Ok(())
    }

    fn texts(&self, needed: bool, prefix: &str) -> Result<()> {
        if needed && !self.english {
            return Err(Error::profile(format!("{prefix}Item Randomiser's reward texts exist only in English")));
        }
        if needed && !self.journal {
            return Err(Error::profile(format!("{prefix}the game has no BGEE.LUA journal table")));
        }
        Ok(())
    }

    /// Fail unless the quest route `key` (a `REWARDS` key) can be prepared.
    pub fn require_route(&self, key: &str) -> Result<()> {
        let rewards = mode2_rewards::rewards();
        let spec = rewards.get(key).ok_or_else(|| Error::key(key))?;
        let prefix = format!("The extended reward route for {} cannot be used: ", crate::py::upper(key));
        let resources = resources_of(spec)?;
        self.unchanged(&resources)?;
        let event = spec.at("event_global")?;
        for resource in &resources {
            let others = mode2_rewards::resource_rewards().get(resource).and_then(Value::as_list).cloned().unwrap_or_default();
            for other in others.iter().map(str_value) {
                if other != key && !rewards.get(&other).ok_or_else(|| Error::key(&other))?.at("event_global")?.py_eq(event) {
                    continue;
                }
                if let Some(reason) = self.reasons.get(&(resource.clone(), other)) {
                    return Err(Error::profile(format!("{prefix}{reason}")));
                }
            }
        }
        self.texts(resources.iter().any(|resource| mode2_rewards::text_refs(resource).is_some()), &prefix)
    }

    /// Fail unless the container quest target can be prepared.
    pub fn require_container(&self, target_id: &str) -> Result<()> {
        let spec = container_rewards::targets().get(target_id).ok_or_else(|| Error::key(target_id))?;
        let resources = resources_of(spec)?;
        self.unchanged(&resources)?;
        for resource in &resources {
            if let Some(reason) = self.reasons.get(&(resource.clone(), target_id.to_string())) {
                return Err(container_rewards::container_error(target_id, reason)?);
            }
        }
        let prefix = container_rewards::container_error(target_id, "")?.message;
        self.texts(resources.iter().any(|resource| container_rewards::TEXT_RESOURCES.contains(&resource.as_str())), &prefix)
    }
}
