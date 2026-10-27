//! Copies of the quest routes' reward replies in other dialogues (the
//! reference's `reward_copies.py`).
//!
//! WeiDU's INTERJECT_COPY_TRANS and INTERJECT_COPY_TRANS3 (and COPY_TRANS)
//! copy a state's replies, their actions included, into another dialogue: a
//! party member's interjection then ends with the quest's own reward replies,
//! and the player can take the reward there. A route gates those copies as it
//! gates its own replies; otherwise that path gives the original item, never
//! sets the reward event, and the reward placed there is lost.
//! (INTERJECT_COPY_TRANS2 keeps the actions in the quest's dialogue, where the
//! route finds them.)
//!
//! The copies are found by following the links out of each route's dialogues
//! into other dialogues, through chained interjections too: each state once,
//! at most [`LIMIT`] states per route. A reply there whose actions are exactly
//! those of one of the route's reward replies is a copy. A reply there that
//! gives the reward another way cannot be told apart from another quest's
//! reward, so the route is left out with that reason, as it is for a copy it
//! cannot gate: a carried reward, which the copy's speaker does not carry, or
//! a copy in a dialogue that CIR changes for something else. Another route's
//! own grants of the same item (the sculptors' mace of the temple messengers)
//! stay that route's.
//!
//! A route without a grant of its own (the Sahuagin prince's, Adalon's) hands
//! over what its speaker carries in the replies that end its job. A reply
//! there that ends that job too, a copy or not, would end it without the
//! hand-over, and the reward would stay with the speaker: the route is left
//! out with that reason.
//!
//! Container quests are not followed: their dialogues test the treasure in
//! the conditions of their states, which interjections do not copy.
//!
//! A copy dialogue carries no trailer. As for the route dialogues, our patch
//! is recognised by removing it and applying it again.
//!
//! The rewards are found as the scanned game names them (EET renames BG1's
//! items): a [`Naming`], as [`crate::mode2_rewards`] takes it.

use crate::container_rewards;
use crate::error::{Error, ErrorKind, Result};
use crate::game_names::EetNames;
use crate::json::{Dict, Value};
use crate::mode2_rewards::{self, Naming, Replies};
use crate::py::{find_bytes, lower, str_value, u32_le, upper};
use crate::quest_options::Dialogue;
use crate::resources::sha256_hex;
use crate::script_text::calls;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::sync::Arc;
use std::sync::OnceLock;

/// The most states of other dialogues one route follows.
pub const LIMIT: usize = 10000;
/// Dialogues CIR changes for something other than a reward route.
const OTHER: [&str; 3] = ["dlg:hlskull", "dlg:unshey", "dlg:kpglai01"];

/// `_ROUTES`: the routes whose rewards dialogues give, with their dialogues.
fn routes() -> &'static BTreeMap<String, Vec<String>> {
    static ROUTES: OnceLock<BTreeMap<String, Vec<String>>> = OnceLock::new();
    ROUTES.get_or_init(|| {
        let mut routes = BTreeMap::new();
        for (key, spec) in mode2_rewards::rewards().iter() {
            let spec = spec.as_dict().expect("reward row");
            if !spec.get("resource").and_then(Value::as_str).is_some_and(|resource| resource.starts_with("dlg:")) {
                continue;
            }
            let names: Vec<String> = match spec.get("dialogues").and_then(Value::as_dict) {
                Some(dialogues) => dialogues.keys().map(|name| format!("dlg:{name}")).collect(),
                None => vec![format!("dlg:{}", str_value(spec.get("quest").expect("quest")))],
            };
            routes.insert(key.to_string(), names);
        }
        routes
    })
}

/// `_GATED`: the routes that gate their copies; one without a grant has none
/// (see above).
fn gated() -> Result<Vec<&'static String>> {
    let mut keys = Vec::new();
    for key in routes().keys() {
        if reward(key)?.get("selected_global").ok_or_else(|| Error::key("selected_global"))?.truthy() {
            keys.push(key);
        }
    }
    Ok(keys)
}

fn reward(key: &str) -> Result<&'static Dict> {
    mode2_rewards::rewards().get(key).and_then(Value::as_dict).ok_or_else(|| Error::key(key))
}

/// A route's item, which the reference reads as text.
fn item(spec: &Dict) -> Result<&str> {
    match spec.get("item").ok_or_else(|| Error::key("item"))? {
        Value::Str(item) => Ok(item),
        other => Err(Error::attribute(other.type_name(), "encode")),
    }
}

fn label(resource: &str) -> String {
    mode2_rewards::label(resource)
}

fn profile(message: impl Into<String>) -> Error {
    Error::profile(message)
}

/// The route `key` as it gates its copies in the dialogue `resource`.
pub fn copy_spec(key: &str, resource: &str, naming: Naming<'_>) -> Result<Dict> {
    let mut spec = mode2_rewards::reward(key, naming)?;
    spec.insert("route", key);
    spec.insert("resource", resource);
    spec.insert("quest", resource.get(4..).unwrap_or(""));
    Ok(spec)
}

/// The routes whose gates our copy patch put into a dialogue.
fn present(dialogue: &Dialogue) -> Result<Vec<String>> {
    let (triggers, actions) = (dialogue.triggers.concat(), dialogue.actions.concat());
    let mut result = Vec::new();
    for key in gated()? {
        let spec = reward(key)?;
        let guard = format!("Global(\"{}\",\"GLOBAL\",1)", str_value(spec.get("selected_global").ok_or_else(|| Error::key("selected_global"))?));
        if find_bytes(&triggers, guard.as_bytes(), 0).is_some() && find_bytes(&actions, &mode2_rewards::event(spec)?, 0).is_some() {
            result.push(key.clone());
        }
    }
    Ok(result)
}

/// The links of a state into dialogues: (resource, state).
fn links(dialogue: &Dialogue, index: usize) -> Vec<(String, usize)> {
    let state = &dialogue.states[index];
    let (first, count) = (u32_le(state, 4) as usize, u32_le(state, 8) as usize);
    let end = (first + count).min(dialogue.transitions.len());
    let mut result = Vec::new();
    for row in &dialogue.transitions[first.min(end)..end] {
        if u32_le(row, 0) & 8 != 0 {
            continue;
        }
        let link = &row[20..28];
        let length = link.iter().rposition(|&byte| byte != 0).map_or(0, |last| last + 1);
        let name = lower(&link[..length].iter().map(|&byte| byte as char).collect::<String>());
        if !name.is_empty() {
            result.push((format!("dlg:{name}"), u32_le(row, 28) as usize));
        }
    }
    result
}

/// A copy dialogue our earlier patch changed, and the routes whose gates it
/// holds.
type Patched = (Vec<u8>, Vec<String>);

/// A dialogue's bytes, its dialogue without our patches, and the problem
/// reading it.
struct Loaded {
    data: Option<Vec<u8>>,
    dialogue: Option<Arc<Dialogue>>,
    problem: Option<String>,
}

/// The copies of every quest route's reward replies in the game's dialogues
/// (`RewardCopies`).
///
/// `read` returns a dialogue's bytes by name, or None when the game has none;
/// `scanned` maps resources to the hashes the scan recorded.
pub struct RewardCopies {
    /// How the game names the rewards (its [`Naming`]).
    game: Option<String>,
    names: Option<EetNames>,
    /// Each route as the game names it.
    specs: BTreeMap<String, Dict>,
    loaded: HashMap<String, Loaded>,
    patched: BTreeMap<String, Patched>,
    failed: HashSet<(String, String)>,
    /// route -> copy dialogue -> state -> ordinals of its copied replies.
    pub copies: BTreeMap<String, BTreeMap<String, Replies>>,
    /// route -> why the route cannot be prepared because of a copy.
    pub reasons: BTreeMap<String, String>,
    /// route -> the other dialogues its links lead into (its checked files).
    pub visited: BTreeMap<String, BTreeSet<String>>,
    /// Visited dialogues that differ from the scan, or are gone.
    pub changed: BTreeSet<String>,
    /// copy dialogue -> its bytes without our patch.
    pub originals: BTreeMap<String, Vec<u8>>,
}

type Reader<'a> = dyn FnMut(&str) -> Result<Option<Vec<u8>>> + 'a;

impl RewardCopies {
    pub fn new(read: &mut Reader<'_>, scanned: &HashMap<String, Value>, naming: Naming<'_>) -> Result<RewardCopies> {
        Self::with_limit(read, scanned, LIMIT, naming)
    }

    /// [`RewardCopies::new`] following at most `limit` states per route (the
    /// reference's tests patch `LIMIT`).
    pub fn with_limit(
        read: &mut Reader<'_>,
        scanned: &HashMap<String, Value>,
        limit: usize,
        naming: Naming<'_>,
    ) -> Result<RewardCopies> {
        let mut specs = BTreeMap::new();
        for key in routes().keys() {
            specs.insert(key.clone(), mode2_rewards::reward(key, naming)?);
        }
        let mut copies = RewardCopies {
            game: naming.game.map(str::to_string),
            names: naming.names.cloned(),
            specs,
            loaded: HashMap::new(),
            patched: BTreeMap::new(),
            failed: HashSet::new(),
            copies: BTreeMap::new(),
            reasons: BTreeMap::new(),
            visited: BTreeMap::new(),
            changed: BTreeSet::new(),
            originals: BTreeMap::new(),
        };
        for key in routes().keys() {
            copies.route(read, key, limit)?;
        }
        copies.check_patches()?;
        let visited: BTreeSet<String> = copies.visited.values().flatten().cloned().collect();
        for resource in visited {
            copies.compare(&resource, scanned)?;
        }
        // Everything later reads the originals and patches kept above.
        copies.loaded.clear();
        Ok(copies)
    }

    fn naming(&self) -> Naming<'_> {
        Naming { game: self.game.as_deref(), names: self.names.as_ref() }
    }

    /// (dialogue without our patches or None, the problem reading it or None).
    fn load(&mut self, read: &mut Reader<'_>, resource: &str) -> Result<(Option<Arc<Dialogue>>, Option<String>)> {
        if !self.loaded.contains_key(resource) {
            let data = read(resource.get(4..).unwrap_or(""))?;
            let (dialogue, problem) = self.original(resource, data.as_deref())?;
            self.loaded.insert(resource.to_string(), Loaded { data, dialogue, problem });
        }
        let loaded = &self.loaded[resource];
        Ok((loaded.dialogue.clone(), loaded.problem.clone()))
    }

    fn original(&mut self, resource: &str, data: Option<&[u8]>) -> Result<(Option<Arc<Dialogue>>, Option<String>)> {
        let Some(data) = data else { return Ok((None, None)) };
        let unreadable =
            || format!("{} cannot be read as a dialogue, so the route cannot check it for copies of its reward", label(resource));
        let original = if mode2_rewards::resource_rewards().contains_key(resource) {
            mode2_rewards::examine(resource, data, self.naming()).map(|examined| examined.original)
        } else if container_rewards::resource_targets().contains_key(resource) {
            container_rewards::recover(resource, data).map(|recovered| recovered.0)
        } else {
            self.unpatched(resource, data)
        };
        match original.and_then(|original| Dialogue::new(&original, resource.get(4..).unwrap_or(""))) {
            Ok(dialogue) => Ok((Some(Arc::new(dialogue)), None)),
            Err(error) if error.kind == ErrorKind::Profile && error.message.contains("changed copy") => {
                Ok((None, Some(error.message)))
            }
            Err(error) if matches!(error.kind, ErrorKind::Profile | ErrorKind::GameText) => Ok((None, Some(unreadable()))),
            Err(error) => Err(error),
        }
    }

    /// A dialogue without our copy patch; its routes are checked later.
    fn unpatched(&mut self, resource: &str, data: &[u8]) -> Result<Vec<u8>> {
        let dialogue = mode2_rewards::read_dialogue(data, resource)?;
        let specs = gated()?.into_iter().map(|key| copy_spec(key, resource, self.naming())).collect::<Result<Vec<_>>>()?;
        if !mode2_rewards::carries_patch(&dialogue, &specs)? {
            return Ok(data.to_vec());
        }
        let present = present(&dialogue)?;
        let mut flags = Vec::new();
        for key in &present {
            flags.push(str_value(reward(key)?.get("selected_global").ok_or_else(|| Error::key("selected_global"))?));
        }
        if present.is_empty() || flags.iter().collect::<BTreeSet<_>>().len() != flags.len() {
            return Err(mode2_rewards::changed_patch(resource));
        }
        let specs = present.iter().map(|key| copy_spec(key, resource, self.naming())).collect::<Result<Vec<_>>>()?;
        let original = match mode2_rewards::unpatched_dialogue(dialogue, &specs, resource) {
            Ok(original) => original,
            Err(error) if error.kind == ErrorKind::Profile => return Err(mode2_rewards::changed_patch(resource)),
            Err(error) => return Err(error),
        };
        self.patched.insert(resource.to_string(), (data.to_vec(), present));
        Ok(original)
    }

    /// The actions of the route's reward replies, with the dialogue each is
    /// in.
    ///
    /// None when a route dialogue is missing or its grant is not recognised:
    /// the route's own reasons say why it cannot be prepared.
    fn grants(&mut self, read: &mut Reader<'_>, key: &str) -> Result<Option<HashMap<Vec<u8>, String>>> {
        let mut texts = HashMap::new();
        for resource in &routes()[key] {
            let (Some(dialogue), _) = self.load(read, resource)? else { return Ok(None) };
            let specs = mode2_rewards::resource_specs(resource, self.naming())?;
            let spec = specs.iter().find(|spec| spec.get("route").is_some_and(|route| str_value(route) == key)).expect("route spec");
            let grants = if mode2_rewards::ends_a_job(spec) {
                mode2_rewards::job_replies(&dialogue, spec, &label(resource))
            } else {
                mode2_rewards::dialogue_grants(&dialogue, spec, &label(resource), None)
                    .map(|grants| grants.into_keys().collect())
            };
            let grants = match grants {
                Ok(grants) => grants,
                Err(error) if error.kind == ErrorKind::Profile => return Ok(None),
                Err(error) => return Err(error),
            };
            for &(state, ordinal) in &grants {
                let first = u32_le(&dialogue.states[state], 4) as usize;
                let action = u32_le(&dialogue.transitions[first + ordinal], 16) as usize;
                texts.entry(dialogue.actions[action].clone()).or_insert_with(|| resource.clone());
            }
        }
        Ok(Some(texts))
    }

    fn route(&mut self, read: &mut Reader<'_>, key: &str, limit: usize) -> Result<()> {
        let Some(texts) = self.grants(read, key)? else { return Ok(()) };
        // A reply concerns the route where it names its item, or the job's
        // global or item of a route without a grant.
        let job = if mode2_rewards::ends_a_job(&self.specs[key]) {
            Some(mode2_rewards::job(&self.specs[key])?)
        } else {
            None
        };
        let item = match job {
            Some(_) => String::new(),
            None => item(&self.specs[key])?.to_string(),
        };
        let item = item.as_str();
        let own: BTreeSet<&String> = routes()[key].iter().collect();
        self.visited.entry(key.to_string()).or_default();
        let mut found: BTreeMap<String, Replies> = BTreeMap::new();
        let mut reason: Option<String> = None;
        let mut queue = VecDeque::new();
        for resource in &own {
            let (dialogue, _) = self.load(read, resource)?;
            let dialogue = dialogue.expect("route dialogue");
            for index in 0..dialogue.states.len() {
                queue.extend(links(&dialogue, index));
            }
        }
        let mut seen: HashSet<(String, usize)> = HashSet::new();
        while let Some((resource, index)) = queue.pop_front() {
            if own.contains(&resource) || seen.contains(&(resource.clone(), index)) {
                continue;
            }
            if seen.len() == limit {
                let labels: Vec<String> = own.iter().map(|resource| label(resource)).collect();
                reason.get_or_insert_with(|| {
                    format!(
                        "{} lead into more than {limit} states of other dialogues; the route cannot check them all for copies of its reward",
                        labels.join("/")
                    )
                });
                break;
            }
            seen.insert((resource.clone(), index));
            self.visited.get_mut(key).expect("visited").insert(resource.clone());
            let (dialogue, problem) = self.load(read, &resource)?;
            if let Some(problem) = problem {
                reason.get_or_insert(problem);
                continue;
            }
            // The engine cannot continue there either.
            let Some(dialogue) = dialogue.filter(|dialogue| index < dialogue.states.len()) else { continue };
            let place = format!("{} state {index}", label(&resource));
            let state = &dialogue.states[index];
            let (first, count) = (u32_le(state, 4) as usize, u32_le(state, 8) as usize);
            if first + count > dialogue.transitions.len() {
                reason.get_or_insert_with(|| format!("{place} has replies outside its tables"));
                continue;
            }
            for (ordinal, row) in dialogue.transitions[first..first + count].iter().enumerate() {
                if u32_le(row, 0) & 4 == 0 {
                    continue;
                }
                let action = u32_le(row, 16) as usize;
                if action >= dialogue.actions.len() {
                    reason.get_or_insert_with(|| format!("{place} has a reply whose actions are outside its tables"));
                    continue;
                }
                let text = &dialogue.actions[action];
                let concerns = match &job {
                    Some(job) => job.mentioned(text),
                    None => mode2_rewards::names(text, item),
                };
                if !concerns {
                    continue;
                }
                match self.copy(key, &resource, &dialogue, index, ordinal, text, &texts) {
                    Ok(true) => {
                        found.entry(resource.clone()).or_default().entry(index).or_default().insert(ordinal);
                    }
                    Ok(false) => {}
                    Err(error) if error.kind == ErrorKind::Profile => {
                        reason.get_or_insert(error.message);
                    }
                    Err(error) => return Err(error),
                }
            }
            queue.extend(links(&dialogue, index));
        }
        if !found.is_empty() {
            self.copies.insert(key.to_string(), found);
        }
        if let Some(reason) = reason {
            self.reasons.insert(key.to_string(), reason);
        }
        Ok(())
    }

    /// Whether a reply that names the reward copies a reward reply.
    ///
    /// Fails with the reason when the route cannot gate what the reply does.
    #[allow(clippy::too_many_arguments)]
    fn copy(
        &self,
        key: &str,
        resource: &str,
        dialogue: &Dialogue,
        index: usize,
        ordinal: usize,
        text: &[u8],
        texts: &HashMap<Vec<u8>, String>,
    ) -> Result<bool> {
        let spec = &self.specs[key];
        let place = format!("{} state {index}", label(resource));
        if mode2_rewards::ends_a_job(spec) {
            // Whoever's reply ends the job, the speaker who carries the reward
            // does not hand it over there.
            let job = mode2_rewards::job(spec)?;
            if mode2_rewards::reply_ends_job(text, &job, &place)? {
                let origins: BTreeSet<String> = texts.values().map(|origin| label(origin)).collect();
                return Err(profile(format!(
                    "{place} also {}, which ends the job of {}'s speaker, but there it would not hand over the reward it carries",
                    job.text(),
                    origins.into_iter().collect::<Vec<_>>().join(", ")
                )));
            }
            return Ok(false);
        }
        let item = item(spec)?;
        let shown = upper(item);
        if let Some(origin) = texts.get(text) {
            let origin = label(origin);
            if mode2_rewards::resource_rewards().contains_key(resource)
                || container_rewards::resource_targets().contains_key(resource)
                || OTHER.contains(&resource)
            {
                return Err(profile(format!(
                    "{place} copies a reward reply of {origin}, but CIR changes {} for something else",
                    label(resource)
                )));
            }
            if spec.get("grant_kind").is_some_and(|kind| kind.py_eq(&Value::from("transfer"))) {
                return Err(profile(format!(
                    "{place} copies a reward reply of {origin}, but there another speaker would hand over the {shown} that {origin}'s speaker carries"
                )));
            }
            return Ok(true);
        }
        let Some(found) = calls(text) else {
            return Err(profile(format!("{place} names {shown} in actions that cannot be read")));
        };
        if !found.iter().any(|call| mode2_rewards::gives(text, call, item)) {
            return Ok(false); // It takes, shows or tests the reward.
        }
        if let Some(others) = mode2_rewards::resource_rewards().get(resource).and_then(Value::as_list) {
            let specs = mode2_rewards::resource_specs(resource, self.naming())?;
            for other in others.iter().map(str_value) {
                let other_spec =
                    specs.iter().find(|row| row.get("route").is_some_and(|route| str_value(route) == other)).expect("route spec");
                if !other_spec.get("item").is_some_and(|other_item| other_item.py_eq(&Value::from(item))) {
                    continue;
                }
                match mode2_rewards::dialogue_grants(dialogue, other_spec, &label(resource), None) {
                    // Another route's own grant of the same item.
                    Ok(grants) if grants.contains_key(&(index, ordinal)) => return Ok(false),
                    Ok(_) => {}
                    Err(error) if error.kind == ErrorKind::Profile => {}
                    Err(error) => return Err(error),
                }
            }
        }
        let origins: BTreeSet<String> = texts.values().map(|origin| label(origin)).collect();
        Err(profile(format!(
            "{place} also gives {shown}, but not with a copy of a reward reply of {}, so the route cannot tell whether it is the same reward",
            origins.into_iter().collect::<Vec<_>>().join(", ")
        )))
    }

    /// Gate each copy dialogue for every route once; a route it fails for is
    /// left out.
    fn check_patches(&mut self) -> Result<()> {
        let resources: BTreeSet<String> = self.copies.values().flat_map(|rows| rows.keys().cloned()).collect();
        for resource in resources {
            let dialogue = self.loaded[&resource].dialogue.clone().expect("copy dialogue");
            self.originals.insert(resource.clone(), dialogue.data.clone());
            let keys: Vec<String> =
                self.copies.iter().filter(|(_, rows)| rows.contains_key(&resource)).map(|(key, _)| key.clone()).collect();
            let mut failures = Dict::new();
            match self.patch(&resource, &keys, Some(&mut failures)) {
                Ok(_) => {}
                Err(error) if error.kind == ErrorKind::Profile => {}
                Err(error) => return Err(error),
            }
            for (key, failure) in failures.iter() {
                self.failed.insert((key.to_string(), resource.clone()));
                self.reasons.entry(key.to_string()).or_insert_with(|| str_value(failure));
            }
        }
        // Our earlier patch of a copy dialogue must be exactly the patch of
        // its original.
        let patched: Vec<(String, Patched)> =
            self.patched.iter().map(|(resource, entry)| (resource.clone(), entry.clone())).collect();
        for (resource, (data, present)) in patched {
            let visitors: Vec<String> =
                self.visited.iter().filter(|(_, rows)| rows.contains(&resource)).map(|(key, _)| key.clone()).collect();
            if visitors.is_empty() {
                continue;
            }
            let all_copied = present.iter().all(|key| self.copies.get(key).is_some_and(|rows| rows.contains_key(&resource)));
            let same = if all_copied {
                let attempt = self.patch(&resource, &present, None).and_then(|again| {
                    Ok(mode2_rewards::logical(&again, &resource)? == mode2_rewards::logical(&data, &resource)?)
                });
                match attempt {
                    Ok(same) => same,
                    Err(error) if error.kind == ErrorKind::Profile => false,
                    Err(error) => return Err(error),
                }
            } else {
                false
            };
            if !same {
                self.patched.remove(&resource);
                for key in visitors {
                    self.reasons.entry(key).or_insert_with(|| mode2_rewards::changed_patch(&resource).message);
                }
            }
        }
        Ok(())
    }

    fn patch(&self, resource: &str, keys: &[String], failures: Option<&mut Dict>) -> Result<Vec<u8>> {
        let specs = keys.iter().map(|key| copy_spec(key, resource, self.naming())).collect::<Result<Vec<_>>>()?;
        let only: BTreeMap<String, Replies> =
            keys.iter().map(|key| (key.clone(), self.copies[key][resource].clone())).collect();
        mode2_rewards::patch_dialogue_in(
            &self.originals[resource],
            &specs,
            &[],
            true,
            true,
            failures,
            Some(&only),
            Some(resource),
            None,
            true,
        )
    }

    fn compare(&mut self, resource: &str, scanned: &HashMap<String, Value>) -> Result<()> {
        let Some(data) = &self.loaded[resource].data else {
            if scanned.contains_key(resource) {
                self.changed.insert(resource.to_string());
            }
            return Ok(());
        };
        if scanned.get(resource).is_some_and(|hash| hash.py_eq(&Value::from(sha256_hex(data)))) {
            return Ok(());
        }
        let own = if mode2_rewards::resource_rewards().contains_key(resource) {
            mode2_rewards::own_patch(resource, data, self.naming())
        } else if container_rewards::resource_targets().contains_key(resource) {
            container_rewards::own_patch(resource, data)
        } else {
            Ok(self.patched.contains_key(resource))
        };
        let own = match own {
            Ok(own) => own,
            // A changed copy of our patch: the reasons name it.
            Err(error) if error.kind == ErrorKind::Profile => true,
            Err(error) => return Err(error),
        };
        if !own {
            self.changed.insert(resource.to_string());
        }
        Ok(())
    }

    /// The other dialogues whose bytes the route's copies depend on.
    pub fn checked(&self, key: &str) -> BTreeSet<String> {
        self.visited.get(key).cloned().unwrap_or_default()
    }

    /// Each copy dialogue of these routes, with each of their copies there
    /// gated.
    ///
    /// A dialogue that already carries exactly this patch keeps its bytes.
    pub fn patches<S: AsRef<str>>(&self, routes: &[S]) -> Result<BTreeMap<String, Vec<u8>>> {
        let routes: BTreeSet<&str> = routes.iter().map(AsRef::as_ref).collect();
        let resources: BTreeSet<&String> =
            routes.iter().filter_map(|key| self.copies.get(*key)).flat_map(|rows| rows.keys()).collect();
        let mut result = BTreeMap::new();
        for resource in resources {
            let keys: Vec<String> = routes
                .iter()
                .filter(|key| {
                    self.copies.get(**key).is_some_and(|rows| rows.contains_key(resource))
                        && !self.failed.contains(&(key.to_string(), resource.clone()))
                })
                .map(|key| key.to_string())
                .collect();
            if keys.is_empty() {
                continue;
            }
            match self.patched.get(resource) {
                Some((data, present)) if *present == keys => {
                    result.insert(resource.clone(), data.clone());
                }
                _ => {
                    result.insert(resource.clone(), self.patch(resource, &keys, None)?);
                }
            }
        }
        Ok(result)
    }
}
