//! Places whose item may not show up, as the editor marks them (the
//! reference's `carrier_hints.py`).
//!
//! Item Randomiser's lists put some items on creatures that can leave alive,
//! flee or not appear, as the player plays (lists/carriers, Shows "maybe").
//! Such a place stays, as Item Randomiser has it, and the editor marks it with
//! `LABEL` and a line on why, from the row's reason. A place on a creature
//! that never keeps its item is left out instead
//! (`original_catalog::CARRIER_GOES_MISSING`).
//!
//! The texts are English, as the editor is; their wording is Chris's to
//! choose.

use crate::error::{Error, ErrorKind, Result};
use crate::game_names::{eet_names, EetNames};
use crate::json::{Dict, Value};
use crate::original_catalog::carriers;
use crate::special_places::Names;
use std::collections::HashMap;

pub const LABEL: &str = "May not show up";
/// What the carrier may do, by lists/carriers' reason; "{}" is who carries it.
pub const REASONS: [(&str, &str); 7] = [
    ("conversation", "{} may leave after a conversation"),
    ("reward", "{} may reward you and leave"),
    ("flees", "{} may flee"),
    ("story", "{} may leave or not appear, depending on the quest"),
    ("difficulty", "{} may not appear, depending on the difficulty"),
    ("party", "{} may not appear, depending on your party"),
    ("transformed", "{} may turn into another creature"),
];
/// Names a group's line shows before "and N more", as special_places' label.
const NAMES: usize = 4;

/// The (area, creature) resources whose place's item may not show up, each
/// with its reason.
pub type Hints = HashMap<(String, String), String>;

/// The carriers of lists/carriers of the table campaigns `tables` in `game`'s
/// names whose place's item may not show up.
pub fn found<'a>(tables: impl IntoIterator<Item = &'a str>, game: &str, names: Option<&EetNames>) -> Result<Hints> {
    let mut result = HashMap::new();
    for table in tables {
        for (key, (reason, shows)) in carriers(table, game, names)? {
            if shows == "maybe" {
                result.insert(key, reason);
            }
        }
    }
    Ok(result)
}

/// `found` for the scanned game: the tables of every preset campaign it has.
/// None for a game no preset is for.
pub fn hints(world: &Value) -> Result<Hints> {
    let platform = match crate::platform::game_platform(world) {
        Ok(platform) => platform,
        Err(error) if error.kind == ErrorKind::Profile => return Ok(HashMap::new()),
        Err(error) => return Err(error),
    };
    let campaigns = match crate::platform::preset_campaigns(world) {
        Ok(campaigns) => campaigns,
        Err(error) if error.kind == ErrorKind::Profile => return Ok(HashMap::new()),
        Err(error) => return Err(error),
    };
    let mut tables: Vec<&str> = Vec::new();
    for campaign in campaigns {
        for table in crate::templates::campaign_tables(campaign)? {
            if !tables.contains(&table) {
                tables.push(table);
            }
        }
    }
    let names = if platform == "eet" {
        Some(eet_names(&crate::paths::path_argument(world.at("game_root")?)?)?)
    } else {
        None
    };
    found(tables, platform, names.as_ref())
}

/// The creatures that can carry the item of a resolved original row (a target
/// registration's authored fields): its creature, or a group's members.
pub fn carried_by(authored: &Value) -> Vec<String> {
    if let Some(creature) = authored.get("creature_resource").and_then(Value::as_str) {
        return vec![creature.to_string()];
    }
    authored
        .get("members")
        .and_then(Value::as_list)
        .map(|members| members.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default()
}

/// The mark {"label", "note"} of a place in `area` whose item one of
/// `creatures` carries, or None when none of them may go missing.
///
/// `names(area, creature)` gives the names a creature has in the area (the
/// scan's actors).
pub fn mark(area: Option<&str>, creatures: &[String], hints: &Hints, names: Option<&Names<'_>>) -> Result<Option<Dict>> {
    let Some(area) = area else {
        return Ok(None);
    };
    let mut by_reason: Vec<(&str, Vec<&str>)> = Vec::new();
    for creature in creatures {
        let Some(reason) = hints.get(&(area.to_string(), creature.clone())) else {
            continue;
        };
        match by_reason.iter_mut().find(|(known, _)| *known == reason.as_str()) {
            Some((_, carriers)) => {
                if !carriers.contains(&creature.as_str()) {
                    carriers.push(creature.as_str());
                }
            }
            None => by_reason.push((reason.as_str(), vec![creature.as_str()])),
        }
    }
    let mut lines = Vec::with_capacity(by_reason.len());
    for (reason, carriers) in &by_reason {
        let text = REASONS.iter().find(|(key, _)| key == reason).map(|(_, text)| *text).ok_or_else(|| Error::key(*reason))?;
        lines.push(text.replacen("{}", &who(area, carriers, creatures.len() == 1, names), 1) + ".");
    }
    if lines.is_empty() {
        return Ok(None);
    }
    Ok(Some(crate::dict! {"label" => LABEL, "note" => lines.join(" ")}))
}

/// "Desreta", "Necre and Taibela": the creatures by the names they have in
/// the area; "The carrier" for a place's only creature that has none.
fn who(area: &str, creatures: &[&str], alone: bool, names: Option<&Names<'_>>) -> String {
    let mut shown: Vec<String> = Vec::new();
    for creature in creatures {
        let known = names.map(|names| names(area, creature)).unwrap_or_default();
        if known.is_empty() && alone {
            return "The carrier".to_string();
        }
        let known = if known.is_empty() {
            vec![crate::py::upper(creature.split_once(':').map_or(*creature, |(_, name)| name))]
        } else {
            known
        };
        for name in known {
            if !shown.contains(&name) {
                shown.push(name);
            }
        }
    }
    let listed = &shown[..shown.len().min(NAMES)];
    let more = shown.len() - listed.len();
    if more > 0 {
        return format!("{} and {more} more", listed.join(", "));
    }
    match listed {
        [only] => only.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
        [] => String::new(),
    }
}
