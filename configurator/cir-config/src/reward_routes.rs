//! Which extended reward routes a game can prepare, read once per scan (the
//! reference's `reward_routes.py`).
//!
//! The configuration check and Apply run the same recognition
//! ([`mode2_rewards::route_reasons`], [`container_rewards::route_reasons`]
//! and the copies of reward replies in other dialogues,
//! [`crate::reward_copies`]) on the game's current bytes. A route the check
//! accepts is one the export can prepare; one it cannot prepare is left out
//! of the configuration with its reason instead of failing at Apply.
//!
//! A quest route needs its grant recognised in each of its resources, and so
//! does every route that shares its reward event (the egg deal's two
//! dialogues, the three sculptors); copies of their reward replies elsewhere
//! must be gateable too. Routes with Item Randomiser's reward text need the
//! English game text and the BGEE.LUA journal table, as the export does.
//!
//! The resources are read once per scan, which keeps the repeated checks of a
//! fit cheap. Each check run first compares the files they came from with
//! what was read (their stat identities, see [`file_identity`]) and reads
//! them again when one changed; a resource whose bytes then differ from the
//! scan stops the check. Apply still reads and verifies the bytes it patches.

use crate::container_rewards;
use crate::error::{Error, ErrorKind, Result, CHANGED_SINCE_SCAN};
use crate::json::Value;
use crate::mode2_rewards;
use crate::py::str_value;
use crate::resources::{file_identity, resource_type, sha256_hex, FileIdentity, GameResources};
use crate::reward_copies::RewardCopies;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

pub struct RewardRoutes {
    english: bool,
    journal: bool,
    changed: BTreeSet<String>,
    /// (resource, route or container target id) to why it cannot be prepared.
    reasons: HashMap<(String, String), String>,
    /// The files the resources were read from, with their identities then.
    files: Vec<(PathBuf, FileIdentity)>,
    /// The copies of the quest routes' reward replies in other dialogues.
    pub copies: RewardCopies,
}

type Key = (String, String, String);

/// The scans read so far, oldest first; at most four.
static CACHE: Mutex<Vec<(Key, Arc<RewardRoutes>)>> = Mutex::new(Vec::new());

/// The reward routes of the scanned game, read again when its files changed.
pub fn reward_routes(catalog: &Value, world: &Value) -> Result<Arc<RewardRoutes>> {
    let key = (str_value(catalog.at("game_root")?), str_value(catalog.at("language")?), str_value(world.at("fingerprint")?));
    let mut cache = CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(position) = cache.iter().position(|(known, _)| *known == key) {
        if cache[position].1.current()? {
            return Ok(cache[position].1.clone());
        }
        cache.remove(position);
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

/// The reward keys that edit a resource (`RESOURCE_REWARDS.get(resource, ())`).
fn resource_rewards(resource: &str) -> Vec<String> {
    mode2_rewards::resource_rewards().get(resource).and_then(Value::as_list).map_or_else(Vec::new, |keys| keys.iter().map(str_value).collect())
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
        let mut files: Vec<(PathBuf, FileIdentity)> = Vec::new();
        let types = [("bcs", resource_type("bcs")), ("dlg", resource_type("dlg")), ("lua", resource_type("lua"))];
        let (journal, copies) = GameResources::with(game_root, &types, |reader| {
            let mut present: HashMap<&str, HashSet<String>> = HashMap::new();
            for kind in ["bcs", "dlg", "lua"] {
                present.insert(kind, reader.names(kind)?.into_iter().collect());
            }
            let mut known: HashSet<PathBuf> = HashSet::new();
            for path in reader.lookup_paths()? {
                let identity = file_identity(&path)?;
                if known.insert(path.clone()) {
                    files.push((path, identity));
                }
            }
            let mut read = |kind: &str, name: &str| -> Result<Option<Vec<u8>>> {
                if !present[kind].contains(name) {
                    return Ok(None);
                }
                let path = reader.location(kind, name)?;
                if !known.contains(&path) {
                    let identity = file_identity(&path)?;
                    known.insert(path.clone());
                    files.push((path, identity));
                }
                reader.read(kind, name).map(Some)
            };
            let journal = present["lua"].contains("bgee");
            for resource in &resources {
                let (kind, name) = resource.split_once(':').expect("kind:name");
                let Some(data) = read(kind, name)? else {
                    changed.insert(resource.clone());
                    continue;
                };
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
            let copies = RewardCopies::new(&mut |name| read("dlg", name), &scanned)?;
            Ok((journal, copies))
        })?;
        changed.extend(copies.changed.iter().cloned());
        Ok(RewardRoutes { english, journal, changed, reasons, files, copies })
    }

    /// Whether every file these routes were read from is as it was.
    pub fn current(&self) -> Result<bool> {
        for (path, identity) in &self.files {
            match file_identity(path) {
                Ok(now) if now == *identity => {}
                Ok(_) => return Ok(false),
                Err(error) if error.kind == ErrorKind::Os => return Ok(false),
                Err(error) => return Err(error),
            }
        }
        Ok(true)
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

    /// The route and every route that shares its reward event in its
    /// resources, sorted.
    fn sharing(&self, key: &str) -> Result<Vec<String>> {
        let rewards = mode2_rewards::rewards();
        let spec = rewards.get(key).ok_or_else(|| Error::key(key))?;
        let event = spec.at("event_global")?;
        let mut sharing = BTreeSet::from([key.to_string()]);
        for resource in resources_of(spec)? {
            for other in resource_rewards(&resource) {
                if rewards.get(&other).ok_or_else(|| Error::key(&other))?.at("event_global")?.py_eq(event) {
                    sharing.insert(other);
                }
            }
        }
        Ok(sharing.into_iter().collect())
    }

    /// Why a copy of the route's reward replies elsewhere cannot be gated, if
    /// one cannot.
    pub fn copy_reason(&self, key: &str) -> Result<Option<String>> {
        Ok(self.sharing(key)?.iter().find_map(|other| self.copies.reasons.get(other).cloned()))
    }

    /// Fail unless the quest route `key` (a `REWARDS` key) can be prepared.
    ///
    /// Without `copies`, a copy of its reward replies that cannot be gated
    /// does not stop it (see [`RewardRoutes::copy_reason`]); a revision
    /// exported before CIR looked for copies keeps its route.
    pub fn require_route(&self, key: &str, copies: bool) -> Result<()> {
        let rewards = mode2_rewards::rewards();
        let spec = rewards.get(key).ok_or_else(|| Error::key(key))?;
        let prefix = format!("The extended reward route for {} cannot be used: ", crate::py::upper(key));
        let sharing = self.sharing(key)?;
        let resources = resources_of(spec)?;
        let checked: BTreeSet<String> = sharing.iter().flat_map(|other| self.copies.checked(other)).collect();
        self.unchanged(&[resources.clone(), checked.into_iter().collect()].concat())?;
        for resource in &resources {
            for other in resource_rewards(resource) {
                if !sharing.contains(&other) {
                    continue;
                }
                if let Some(reason) = self.reasons.get(&(resource.clone(), other)) {
                    return Err(Error::profile(format!("{prefix}{reason}")));
                }
            }
        }
        if let Some(reason) = self.copy_reason(key)? {
            if copies {
                return Err(Error::profile(format!("{prefix}{reason}")));
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
