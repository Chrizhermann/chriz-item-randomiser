//! Campaign-gated conditions of the extended pools' container quests (the
//! reference's `container_rewards.py`).
//!
//! The Host observes a delivered reward becoming absent from its loaded
//! container and sets the original saved latch. This module only patches the
//! latch's consumers; it never substitutes current party possession for
//! having taken the treasure. Set activation_global at campaign creation only
//! for actual assignments. Observe each assigned target only after confirmed
//! delivery, and latch 0 -> 1 once; an unloaded/unresolved container is not an
//! empty container. Samia's two assignments share one latch, so either
//! observed absence suffices. Never clear it on item loss. See Item
//! Randomiser's lib/weidu_action.tpa and baf/flrkeep*.baf, flrsamia.baf and
//! flrsafan.baf.
//!
//! The conditions are found by their meaning, never by the bytes of one
//! reviewed game: every script block and dialogue condition that tests
//! whether the party has the treasure, and the block that sets the latch
//! itself when the container no longer holds it. Blocks and conditions
//! another mod added stay as found.
//!
//! The container is found by its meaning too: the one of the quest's area
//! that holds the whole treasure ([`treasure_container`]). Item Randomiser
//! names the chests as the original game did, which the EE games renamed in
//! AR1202, and its tables put the golems' treasures in AR1302 although its
//! handler and the treasures use AR1303; a container index would name
//! another chest in a game where a mod added one.

use crate::error::{Error, ErrorKind, Result, SCRIPT_ERROR};
use crate::game_names::convert_resource;
use crate::game_text::GameText;
use crate::mode2_rewards::TextRefs;
use crate::json::{Dict, Value};
use crate::py::{find_bytes, rfind_bytes, str_value, u32_le, upper};
use crate::quest_options::{pad, Dialogue};
use crate::translations::Line;
use crate::quest_rewards::global_guard;
use crate::script_text::{
    block_actions, block_parts, block_triggers, calls, integer_argument, named, naming, script_blocks, string_argument,
    Action, Script,
};
use crate::dict;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::OnceLock;

/// One container quest. `area` is named as in the campaign's own game
/// (BG:EE for bg1).
#[allow(clippy::too_many_arguments)]
fn target(
    campaign: &str,
    row: i64,
    area: &str,
    item: &str,
    flag: &str,
    latch: &str,
    scope: &str,
    resources: &[&str],
) -> Dict {
    dict! {
        "target_id" => format!("{campaign}:mode2:target:{row}"), "campaign" => campaign, "area" => area,
        "original_item" => item, "activation_global" => flag,
        "latch_global" => latch, "latch_scope" => scope, "resources" => resources.to_vec(),
    }
}

fn rows() -> &'static [Dict] {
    static ROWS: OnceLock<Vec<Dict>> = OnceLock::new();
    ROWS.get_or_init(|| {
        let golems = |row, item, number: u32, resources: &[&str]| {
            target(
                "bg2",
                row,
                "ar1303",
                item,
                &format!("FLPR_CKEEP{number}"),
                &format!("flRobbedGolems{number}"),
                "AR1303",
                resources,
            )
        };
        let samia = ["bcs:ar1202", "bcs:samia", "dlg:samia"];
        vec![
            golems(13, "ax1h13", 1, &["bcs:kpgol01", "bcs:kpgol03"]),
            golems(43, "bow12", 2, &["bcs:kpgol02", "bcs:kpgol04"]),
            golems(3, "hamm04", 3, &["bcs:kpgol05", "bcs:kpgol06"]),
            // These are the handler's item names, not the source row's Ident pairing.
            target("bg2", 53, "ar1202", "shld21", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202", &samia),
            target("bg2", 28, "ar1202", "sw1h32", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202", &samia),
            target("bg1", 22, "ar3601", "book03", "FLPR_CSAFANA", "flSafanaTreasure", "GLOBAL", &["bcs:safana", "dlg:safanj"]),
            target("bg2", 61, "ar1514", "bow19b", "FLPR_CPPCHST", "RobbedGolem", "GLOBAL", &["bcs:ppchest"]),
        ]
    })
}

/// `TARGETS`: target id to its container quest row.
pub fn targets() -> &'static Dict {
    static TARGETS: OnceLock<Dict> = OnceLock::new();
    TARGETS.get_or_init(|| {
        rows().iter().map(|row| (row.get("target_id").and_then(Value::as_str).expect("id").to_string(), Value::Dict(row.clone()))).collect()
    })
}

fn resources_of(row: &Dict) -> Vec<&str> {
    row.get("resources").and_then(Value::as_list).expect("resources").iter().map(|value| value.as_str().expect("resource")).collect()
}

/// `RESOURCE_TARGETS`: resource to the target ids that patch it.
pub fn resource_targets() -> &'static Dict {
    static RESOURCE_TARGETS: OnceLock<Dict> = OnceLock::new();
    RESOURCE_TARGETS.get_or_init(|| {
        let mut result = Dict::new();
        for row in rows() {
            for resource in resources_of(row) {
                let ids: Vec<Value> = rows()
                    .iter()
                    .filter(|other| resources_of(other).contains(&resource))
                    .map(|other| other.get("target_id").cloned().expect("id"))
                    .collect();
                result.insert(resource, Value::List(ids));
            }
        }
        result
    })
}

pub const TEXT_RESOURCES: [&str; 3] = ["bcs:kpgol01", "bcs:kpgol02", "bcs:kpgol05"];
/// The (TRA file, number) of Item Randomiser's golem text, which they show.
pub const TEXT_LINES: &[(&str, i64)] = &[("in-game.tra", 101)];
const GOLEM_TEXT: i64 = 38758;
const TRAILER: &[u8] = b"FLPRCON1";
const PARTY_HAS_ITEM: i64 = 16450;
const CONTAINS: i64 = 16501;
const OR: i64 = 16521;
const SET_GLOBAL: i64 = 30;
/// TRIGGER.IDS names of the triggers that test an item, for the reasons.
const TRIGGERS: &[(i64, &str)] = &[
    (1, "Acquired"),
    (9, "Unusable"),
    (16450, "PartyHasItem"),
    (16481, "HasItem"),
    (16501, "Contains"),
    (16503, "NumItems"),
    (16504, "NumItemsGT"),
    (16505, "NumItemsLT"),
    (16506, "NumItemsParty"),
    (16507, "NumItemsPartyGT"),
    (16508, "NumItemsPartyLT"),
    (16511, "HasItemEquiped"),
    (16553, "PartyHasItemIdentified"),
    (16578, "HasItemEquipedReal"),
    (16643, "CheckItemSlot"),
    (16644, "CurrentAmmo"),
];

fn row_text(spec: &Dict, key: &str) -> String {
    spec.get(key).and_then(Value::as_str).expect("container field").to_string()
}

fn profile(message: impl Into<String>) -> Error {
    Error::profile(message)
}

pub fn target_spec(target_id: &str) -> Result<Dict> {
    match targets().get(target_id).and_then(Value::as_dict) {
        Some(row) => Ok(row.clone()),
        None => Err(profile(format!("Unsupported extended reward container: {target_id}"))),
    }
}

/// The quest's treasure: every item of the targets sharing its latch
/// (Samia's two), sorted.
pub fn treasure(target_id: &str) -> Result<Vec<String>> {
    let flag = row_text(&target_spec(target_id)?, "activation_global");
    let items: BTreeSet<String> =
        rows().iter().filter(|row| row_text(row, "activation_global") == flag).map(|row| row_text(row, "original_item")).collect();
    Ok(items.into_iter().collect())
}

fn shown(items: &[String]) -> String {
    items.iter().map(|item| upper(item)).collect::<Vec<_>>().join("/")
}

/// Why the quest's container cannot be used, as the check leaves it out.
pub fn container_error(target_id: &str, problem: &str) -> Result<Error> {
    Ok(profile(format!("The extended reward container of {} cannot be used: {problem}", shown(&treasure(target_id)?))))
}

/// The quest's area as this game names it (EET renames BG1's).
pub fn treasure_area(target_id: &str, platform: &str) -> Result<String> {
    let spec = target_spec(target_id)?;
    convert_resource(&row_text(&spec, "area"), "are", &row_text(&spec, "campaign"), platform, None)
}

/// The scanned container of the quest's area that holds its whole treasure.
///
/// `sources` are the scan's sources. Raises when no container, or more than
/// one, holds every item of the treasure: then the quest's container is not
/// there as the quest expects it (another mod moved or added the treasure).
pub fn treasure_container<'a>(target_id: &str, sources: impl IntoIterator<Item = &'a Value>, platform: &str) -> Result<String> {
    let area = treasure_area(target_id, platform)?;
    let items: BTreeSet<String> = treasure(target_id)?.into_iter().collect();
    let resource = format!("are:{area}");
    let mut holding: Vec<(String, BTreeSet<String>)> = Vec::new();
    for source in sources {
        let index = source.get("record").filter(|record| record.truthy()).and_then(|record| record.get("container_index"));
        let kind = source.get("kind").ok_or_else(|| Error::key("kind"))?;
        if !kind.py_eq(&Value::from("container")) {
            continue;
        }
        if !source.get("resource").ok_or_else(|| Error::key("resource"))?.py_eq(&Value::from(resource.as_str())) {
            continue;
        }
        let item = source.get("item").ok_or_else(|| Error::key("item"))?;
        let Some(item) = item.as_str().filter(|item| items.contains(*item)) else { continue };
        let Some(index) = index.filter(|index| !index.is_null()) else { continue };
        let chest = format!("are:{area}/container/{}", str_value(index));
        match holding.iter_mut().find(|(known, _)| *known == chest) {
            Some((_, found)) => {
                found.insert(item.to_string());
            }
            None => holding.push((chest, BTreeSet::from([item.to_string()]))),
        }
    }
    let mut chests: Vec<String> = holding.into_iter().filter(|(_, found)| *found == items).map(|(chest, _)| chest).collect();
    chests.sort();
    if chests.len() != 1 {
        let what = if chests.is_empty() { "no container" } else { "more than one container" };
        let sorted: Vec<String> = items.into_iter().collect();
        return Err(profile(format!("{} has {what} that holds {}", upper(&area), shown(&sorted))));
    }
    Ok(chests.remove(0))
}

/// The (TRA file, number) lines of Item Randomiser's golem text, which the
/// golems' scripts show.
pub fn text_lines() -> Vec<Line> {
    TEXT_LINES.iter().map(|&(file, number)| (file.to_string(), number)).collect()
}

/// Allocate the golem text, in the game's language, for the requested
/// container resources.
pub fn prepare_container_texts<I, S>(
    text: &GameText,
    resources: I,
    language: &str,
) -> Result<(GameText, TextRefs)>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let needed: BTreeSet<String> = resources
        .into_iter()
        .map(|resource| resource.as_ref().to_string())
        .filter(|resource| TEXT_RESOURCES.contains(&resource.as_str()))
        .collect();
    if needed.is_empty() {
        return Ok((text.clone(), BTreeMap::new()));
    }
    let lines = text_lines();
    let golems = crate::translations::lines(language, &lines)?.remove(&lines[0]).expect("the line asked for");
    let (updated, refs) = text.append(&[golems])?;
    let result = needed.into_iter().map(|resource| (resource, BTreeMap::from([(GOLEM_TEXT, refs[0] as i64)]))).collect();
    Ok((updated, result))
}

fn label(resource: &str) -> String {
    let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
    format!("{}.{}", upper(name), upper(kind))
}

/// The first container quest a resource belongs to (`_spec`).
fn spec_for(resource: &str) -> Result<&'static Dict> {
    let id = first_target(resource)?;
    Ok(targets().get(&id).and_then(Value::as_dict).expect("target row"))
}

fn first_target(resource: &str) -> Result<String> {
    let ids = resource_targets().get(resource).and_then(Value::as_list).ok_or_else(|| Error::key(resource))?;
    ids.first().and_then(Value::as_str).map(str::to_string).ok_or_else(|| Error::value("tuple index out of range"))
}

/// The treasure the resource's conditions test.
fn items(resource: &str) -> Result<Vec<String>> {
    treasure(&first_target(resource)?)
}

/// Whether text names one of the items as a quoted resref, in any letter case.
fn names(text: &[u8], items: &[String]) -> bool {
    items.iter().any(|item| {
        let item = item.as_bytes();
        text.windows(item.len() + 2).any(|window| {
            window[0] == b'"' && window[window.len() - 1] == b'"' && window[1..window.len() - 1].eq_ignore_ascii_case(item)
        })
    })
}

fn is_item(value: &[u8], items: &[String]) -> bool {
    let value = value.to_ascii_lowercase();
    items.iter().any(|item| item.as_bytes() == value)
}

/// A compiled trigger (`_trigger`).
pub fn trigger(opcode: u32, value: i64, negate: i64, text: &str, selector: i64, newline: &[u8]) -> Vec<u8> {
    [
        b"TR" as &[u8],
        newline,
        format!("{opcode} {value} {negate} 0 0 \"{text}\" \"\" OB").as_bytes(),
        newline,
        format!("0 0 0 0 0 0 0 {selector} 0 0 0 0 \"\"OB").as_bytes(),
        newline,
        b"TR",
        newline,
    ]
    .concat()
}

fn latch(spec: &Dict, value: i64, newline: &[u8]) -> Vec<u8> {
    trigger(16399, value, 0, &(row_text(spec, "latch_scope") + &row_text(spec, "latch_global")), 0, newline)
}

/// A compiled action with its first integer parameter replaced.
fn with_integer(action: &[u8], newline: &[u8], value: Option<i64>) -> Vec<u8> {
    let separator = [b"OB", newline].concat();
    let start = rfind_bytes(action, &separator, 0, action.len()).expect("a compiled action") + separator.len();
    let mut end = start + usize::from(action.get(start) == Some(&b'-'));
    while action.get(end).is_some_and(u8::is_ascii_digit) {
        end += 1;
    }
    let shown = value.map_or_else(|| "None".to_string(), |value| value.to_string());
    [&action[..start], shown.as_bytes(), &action[end..]].concat()
}

/// A byte span of text and what replaces it.
type Edit = (usize, usize, Vec<u8>);

/// A block that tests the treasure: its parts, the edits of its conditions
/// (none for a setter block) and the actions that show the golem's text.
struct Site {
    head: Vec<u8>,
    conditions: Vec<u8>,
    responses: Vec<u8>,
    edits: Option<Vec<Edit>>,
    displays: Vec<Action>,
}

/// A compiled OR() trigger that joins `count` conditions (`_with_count`).
fn with_count(trigger: &[u8], newline: &[u8], count: usize) -> Vec<u8> {
    // `TR<newline>[0-9]+ (-?[0-9]+)`: the opcode, then the count.
    let mut start = 2 + newline.len();
    while trigger.get(start).is_some_and(u8::is_ascii_digit) {
        start += 1;
    }
    start += 1;
    let mut end = start + usize::from(trigger.get(start) == Some(&b'-'));
    while trigger.get(end).is_some_and(u8::is_ascii_digit) {
        end += 1;
    }
    [&trigger[..start], count.to_string().as_bytes(), &trigger[end..]].concat()
}

/// One group of a condition list: its OR() position (None for a condition
/// standing alone), its members and whether as many follow as it counts.
type Group = (Option<usize>, Vec<usize>, bool);

/// The groups of a condition list (`_groups`).
///
/// `counts` has an OR()'s count at its position and None elsewhere. OR(n)
/// joins the n conditions after it; any other condition stands alone. Only
/// the last group can be incomplete, when fewer conditions follow its OR().
fn groups(counts: &[Option<i64>]) -> Vec<Group> {
    let (mut result, mut at) = (Vec::new(), 0);
    while at < counts.len() {
        let Some(count) = counts[at] else {
            result.push((None, vec![at], true));
            at += 1;
            continue;
        };
        let wanted = count.max(0);
        let last = (at as i64 + 1).saturating_add(wanted).min(counts.len() as i64) as usize;
        let members: Vec<usize> = (at + 1..last).collect();
        let complete = members.len() as i64 == wanted;
        let taken = members.len();
        result.push((Some(at), members, complete));
        at += 1 + taken;
    }
    result
}

/// Fail unless the route can rewrite a group that holds treasure tests
/// (`_checked_group`).
///
/// `tested` holds the items the group's tests name, `negated` whether one of
/// them tests that the party lacks it, `nested` whether the group negates its
/// OR() or holds another one.
#[allow(clippy::too_many_arguments)]
fn checked_group(
    place: &str,
    shown: &str,
    items: &[String],
    first: Option<usize>,
    complete: bool,
    tested: &BTreeSet<Vec<u8>>,
    negated: bool,
    nested: bool,
) -> Result<()> {
    if !complete {
        return Err(profile(format!("{place} tests {shown} in an OR() group that counts more conditions than follow it")));
    }
    if nested {
        return Err(profile(format!(
            "{place} tests {shown} in an OR() group that is negated or holds another OR(), which the route cannot read"
        )));
    }
    if items.len() > 1 {
        // The shared latch records that one of the treasures was taken: it can
        // stand only for "the party has one of them".
        let all: BTreeSet<Vec<u8>> = items.iter().map(|item| item.as_bytes().to_vec()).collect();
        if first.is_none() || *tested != all {
            return Err(profile(format!(
                "{place} does not test {shown} together in one OR() group; their shared latch records only that one of them was taken"
            )));
        }
        if negated {
            return Err(profile(format!(
                "{place} tests whether the party lacks one of {shown}; their shared latch records only that one of them was taken"
            )));
        }
    }
    Ok(())
}

/// Text as Python's `bytes.decode("latin-1")` reads it.
fn latin1(data: &[u8]) -> String {
    data.iter().map(|&byte| byte as char).collect()
}

/// Each block that tests the treasure: its parts, its edits and its displays
/// (`_script_sites`).
///
/// A consumer block tests whether the party has the treasure; each test
/// becomes the latch (edits of its conditions), wherever it stands in its
/// block. A setter block (no edits) sets the latch itself when the container
/// no longer holds the treasure; the Host takes that over.
fn script_sites(script: &Script, resource: &str) -> Result<BTreeMap<usize, Site>> {
    let (spec, items, label) = (spec_for(resource)?, items(resource)?, label(resource));
    let shown = shown(&items);
    let newline = script.newline;
    let latch_name = (row_text(spec, "latch_scope") + &row_text(spec, "latch_global")).to_ascii_lowercase().into_bytes();
    let mut sites = BTreeMap::new();
    for (index, block) in script.blocks.iter().enumerate() {
        if !names(block, &items) {
            continue;
        }
        let (head, conditions, responses) = block_parts(block, newline)?;
        let triggers = block_triggers(&conditions, newline)?;
        let tests: Vec<usize> =
            triggers.iter().enumerate().filter(|(_, trigger)| is_item(&trigger.strings[0], &items)).map(|(number, _)| number).collect();
        if tests.is_empty() {
            continue;
        }
        let place = format!("{label} block {index}");
        let actions = block_actions(&responses, newline)?;
        let counts: Vec<Option<i64>> = triggers.iter().map(|trigger| (trigger.opcode == OR).then_some(trigger.value)).collect();
        let groups = groups(&counts);
        if groups.last().is_some_and(|group| !group.2) {
            // The route adds its own condition at the end of the block.
            return Err(profile(format!(
                "{place} ends in an OR() group that counts more conditions than follow it, so a condition added after it would join it"
            )));
        }
        if tests.iter().all(|&number| triggers[number].opcode == CONTAINS && triggers[number].negated)
            && actions.iter().any(|action| action.opcode == SET_GLOBAL && action.strings[0].to_ascii_lowercase() == latch_name)
        {
            sites.insert(index, Site { head, conditions, responses, edits: None, displays: Vec::new() });
            continue;
        }
        if let Some(other) = tests.iter().map(|&number| &triggers[number]).find(|trigger| trigger.opcode != PARTY_HAS_ITEM) {
            let name = TRIGGERS
                .iter()
                .find(|(opcode, _)| *opcode == other.opcode)
                .map_or_else(|| format!("trigger {}", other.opcode_shown), |(_, name)| name.to_string());
            return Err(profile(format!(
                "{place} tests {} with {name}(), which the route cannot replace by the container's latch",
                upper(&latin1(&other.strings[0]))
            )));
        }
        let mut edits = Vec::new();
        for (first, members, complete) in &groups {
            let hit: Vec<usize> = members.iter().copied().filter(|number| tests.contains(number)).collect();
            if hit.is_empty() {
                continue;
            }
            let nested = first.is_some_and(|first| {
                triggers[first].negated || members.iter().any(|&number| triggers[number].opcode == OR)
            });
            let tested = hit.iter().map(|&number| triggers[number].strings[0].to_ascii_lowercase()).collect();
            let negated = hit.iter().any(|&number| triggers[number].negated);
            checked_group(&place, &shown, &items, *first, *complete, &tested, negated, nested)?;
            if items.len() == 1 {
                for &number in &hit {
                    let value = if triggers[number].negated { 0 } else { 1 };
                    edits.push((triggers[number].start, triggers[number].end, latch(spec, value, newline)));
                }
            } else if hit.len() == members.len() {
                // OR(n) of the treasure tests only: the latch replaces the group.
                let first = first.expect("checked group");
                edits.push((triggers[first].start, triggers[members[members.len() - 1]].end, latch(spec, 1, newline)));
            } else {
                let group = &triggers[first.expect("checked group")];
                let count = members.len() - hit.len() + 1;
                edits.push((group.start, group.end, with_count(&conditions[group.start..group.end], newline, count)));
                edits.push((triggers[hit[0]].start, triggers[hit[0]].end, latch(spec, 1, newline)));
                for &number in &hit[1..] {
                    edits.push((triggers[number].start, triggers[number].end, Vec::new()));
                }
            }
        }
        let displays = actions.into_iter().filter(|action| action.integer == GOLEM_TEXT).collect();
        sites.insert(index, Site { head, conditions, responses, edits: Some(edits), displays });
    }
    if sites.is_empty() {
        return Err(profile(format!("{label} has no block that tests {shown}")));
    }
    if TEXT_RESOURCES.contains(&resource) && !sites.values().any(|site| !site.displays.is_empty()) {
        return Err(profile(format!("{label} no longer shows the golem's text that names the treasure ({GOLEM_TEXT})")));
    }
    Ok(sites)
}

/// Apply edits from the last one back (`_replaced`), slicing as Python does.
fn replaced(data: &[u8], edits: &[Edit]) -> Vec<u8> {
    let mut ordered = edits.to_vec();
    ordered.sort();
    let mut data = data.to_vec();
    for (start, end, replacement) in ordered.into_iter().rev() {
        let (start, end) = (start.min(data.len()), end.min(data.len()));
        data = [&data[..start], &replacement, &data[end..]].concat();
    }
    data
}

fn patch_script(resource: &str, original: &[u8], reference: Option<i64>) -> Result<Vec<u8>> {
    if TEXT_RESOURCES.contains(&resource) && !reference.is_some_and(|value| (0..0xFFFF_FFFF).contains(&value)) {
        return Err(profile(format!("{} requires allocated golem text", label(resource))));
    }
    let (script, sites) = naming(&label(resource), || {
        let script = script_blocks(original)?;
        let sites = script_sites(&script, resource)?;
        Ok((script, sites))
    })?;
    let newline = script.newline;
    let flag = row_text(spec_for(resource)?, "activation_global");
    let mut blocks = script.blocks.clone();
    for (index, site) in sites {
        let excluded =
            [site.head.as_slice(), b"CO", newline, &site.conditions, &global_guard(&flag, false, newline)?, b"CO", newline, &site.responses]
                .concat();
        let mut variants = Vec::new();
        if let Some(edits) = &site.edits {
            let mut selected_response = site.responses.clone();
            if TEXT_RESOURCES.contains(&resource) {
                // Every response that shows the golem's text shows the new one.
                let shown: Vec<Edit> = site
                    .displays
                    .iter()
                    .map(|display| {
                        (display.start, display.end, with_integer(&site.responses[display.start..display.end], newline, reference))
                    })
                    .collect();
                selected_response = replaced(&site.responses, &shown);
            }
            variants.extend(
                [
                    site.head.as_slice(),
                    b"CO",
                    newline,
                    &replaced(&site.conditions, edits),
                    &global_guard(&flag, true, newline)?,
                    b"CO",
                    newline,
                    &selected_response,
                ]
                .concat(),
            );
        }
        // A setter block (PPCHST's) is disabled only when the observer owns it.
        variants.extend(excluded);
        blocks[index] = variants;
    }
    Ok([script.prefix, blocks.concat(), script.suffix].concat())
}

/// The rewrites of the treasure tests in a dialogue condition
/// (`_condition_edits`); none without one.
///
/// Each group of conditions that tests the treasure (an OR() group, or a test
/// standing alone) becomes two: (selected OR the group) AND (!selected OR the
/// group with the latch in place of its tests). Every other group stays as
/// written.
fn condition_edits(text: &[u8], resource: &str, place: &str) -> Result<Vec<Edit>> {
    let items = items(resource)?;
    if !names(text, &items) {
        return Ok(Vec::new());
    }
    let shown = shown(&items);
    let Some(found) = calls(text) else {
        return Err(profile(format!("{place} tests {shown} in a way the container route does not recognise")));
    };
    let is_or: Vec<bool> = found
        .iter()
        .map(|call| {
            let bare = call.name.iter().position(|&byte| byte != b'!').map_or(&[][..], |first| &call.name[first..]);
            bare.eq_ignore_ascii_case(b"or")
        })
        .collect();
    let counts: Vec<Option<i64>> =
        found.iter().zip(&is_or).map(|(call, &group)| if group { integer_argument(text, call, 0, None) } else { None }).collect();
    if is_or.iter().zip(&counts).any(|(&group, count)| group && count.is_none()) {
        return Err(profile(format!("{place} has an OR() whose count the route cannot read")));
    }
    let tests: Vec<usize> =
        found.iter().enumerate().filter(|(_, call)| names(&text[call.start..call.end], &items)).map(|(number, _)| number).collect();
    for &number in &tests {
        let call = &found[number];
        let name = latin1(&call.name).trim_start_matches('!').to_string();
        if !named(call, &["PartyHasItem", "!PartyHasItem"]) {
            let item = items
                .iter()
                .find(|item| names(&text[call.start..call.end], std::slice::from_ref(*item)))
                .expect("a test names an item");
            return Err(profile(format!(
                "{place} tests {} with {name}(), which the route cannot replace by the container's latch",
                upper(item)
            )));
        }
        if call.args.len() != 1 || !is_item(string_argument(text, call, 0).unwrap_or(b""), &items) {
            return Err(profile(format!("{place} tests {shown} in a way the container route does not recognise")));
        }
    }
    let spec = spec_for(resource)?;
    let flag = row_text(spec, "activation_global");
    let latch = |negative: bool| {
        format!(
            "Global(\"{}\",\"{}\",{})",
            row_text(spec, "latch_global"),
            row_text(spec, "latch_scope"),
            if negative { 0 } else { 1 }
        )
        .into_bytes()
    };
    let written = |number: usize| text[found[number].start..found[number].end].to_vec();
    let lacks = |number: usize| named(&found[number], &["!PartyHasItem"]);
    let mut edits = Vec::new();
    for (first, members, complete) in groups(&counts) {
        let hit: Vec<usize> = members.iter().copied().filter(|number| tests.contains(number)).collect();
        if hit.is_empty() {
            continue;
        }
        let nested = first.is_some_and(|first| found[first].name.starts_with(b"!") || members.iter().any(|&number| is_or[number]));
        let tested = hit.iter().map(|&number| string_argument(text, &found[number], 0).unwrap_or(b"").to_ascii_lowercase()).collect();
        checked_group(place, &shown, &items, first, complete, &tested, hit.iter().any(|&number| lacks(number)), nested)?;
        let latched: Vec<Vec<u8>> = if items.len() == 1 {
            members.iter().map(|&number| if hit.contains(&number) { latch(lacks(number)) } else { written(number) }).collect()
        } else {
            // The shared latch stands for all of the group's treasure tests.
            members
                .iter()
                .filter(|&&number| number == hit[0] || !hit.contains(&number))
                .map(|&number| if number == hit[0] { latch(false) } else { written(number) })
                .collect()
        };
        // Take the whole lines: their indentation before and the line end after.
        let mut start = found[first.unwrap_or(members[0])].start;
        let line = b"\n\r"
            .iter()
            .filter_map(|&byte| text[..start].iter().rposition(|&found| found == byte))
            .max()
            .map_or(0, |at| at + 1);
        if text[line..start].iter().all(|&byte| byte == b' ' || byte == b'\t') {
            start = line;
        }
        let mut end = found[members[members.len() - 1]].end;
        for newline in [&b"\r\n"[..], b"\n"] {
            if text[end..].starts_with(newline) {
                end += newline.len();
                break;
            }
        }
        // (selected OR original) AND (!selected OR latched). No nested OR/AND.
        let mut changed = format!("OR({})\r\nGlobal(\"{flag}\",\"GLOBAL\",1)\r\n", members.len() + 1).into_bytes();
        changed.extend(members.iter().map(|&number| written(number)).collect::<Vec<_>>().join(&b"\r\n"[..]));
        changed.extend_from_slice(b"\r\n");
        changed.extend(format!("OR({})\r\n!Global(\"{flag}\",\"GLOBAL\",1)\r\n", latched.len() + 1).into_bytes());
        changed.extend(latched.join(&b"\r\n"[..]));
        changed.extend_from_slice(b"\r\n");
        edits.push((start, end, changed));
    }
    Ok(edits)
}

fn read_dialogue(data: &[u8], resource: &str) -> Result<Dialogue> {
    Dialogue::new(data, resource.get(4..).unwrap_or("")).map_err(|error| match error.kind {
        ErrorKind::Profile | ErrorKind::GameText => profile(format!("{} cannot be read as a dialogue", label(resource))),
        _ => error,
    })
}

fn patch_dialogue(resource: &str, original: &[u8]) -> Result<Vec<u8>> {
    let mut dialogue = read_dialogue(original, resource)?;
    let label = label(resource);
    let mut sites = 0;
    for (kind, table) in [("condition", &mut dialogue.state_triggers), ("reply condition", &mut dialogue.triggers)] {
        for (index, condition) in table.iter_mut().enumerate() {
            let edits = condition_edits(condition, resource, &format!("{label} {kind} {index}"))?;
            if !edits.is_empty() {
                *condition = replaced(condition, &edits);
                sites += 1;
            }
        }
    }
    if sites == 0 {
        return Err(profile(format!("{label} has no condition that tests {}", shown(&items(resource)?))));
    }
    // Dialogue appends tables and leaves the original body recoverable.
    let mut out = dialogue.render();
    out.extend_from_slice(&(original.len() as u32).to_le_bytes());
    out.extend(pad(&original[..original.len().min(52)], 52));
    out.extend_from_slice(TRAILER);
    Ok(out)
}

fn changed_patch(resource: &str) -> Error {
    profile(format!("{} carries a changed copy of the container route's own patch", label(resource)))
}

/// The original, the golem text ref and whether the bytes are our own patch
/// (`_recover`).
pub fn recover(resource: &str, data: &[u8]) -> Result<(Vec<u8>, Option<i64>, bool)> {
    let flag = row_text(spec_for(resource)?, "activation_global");
    let marker = flag.as_bytes();
    if resource.starts_with("dlg:") {
        if !data.ends_with(TRAILER) {
            if find_bytes(data, marker, 0).is_some() {
                return Err(changed_patch(resource));
            }
            return Ok((data.to_vec(), None, false));
        }
        if data.len() < 64 {
            return Err(changed_patch(resource));
        }
        let size = u32_le(data, data.len() - 64) as usize;
        let header = &data[data.len() - 60..data.len() - 8];
        if !(52..=data.len() - 64).contains(&size) {
            return Err(changed_patch(resource));
        }
        let original = [header, &data[52..size]].concat();
        match patch_dialogue(resource, &original) {
            Ok(patched) if patched == data => return Ok((original, None, true)),
            Ok(_) => {}
            Err(error) if error.kind == ErrorKind::Profile => {}
            Err(error) => return Err(error),
        }
        return Err(changed_patch(resource));
    }
    if find_bytes(data, &[b"\"GLOBAL", marker, b"\""].concat(), 0).is_none() {
        return Ok((data.to_vec(), None, false));
    }
    let read = || -> Result<(Script, Vec<Vec<u8>>, BTreeSet<i64>)> {
        let script = script_blocks(data)?;
        let newline = script.newline;
        let (selected, excluded) = (global_guard(&flag, true, newline)?, global_guard(&flag, false, newline)?);
        let (mut restored, mut numbers) = (Vec::new(), BTreeSet::new());
        for block in &script.blocks {
            if find_bytes(block, &selected, 0).is_some() {
                let (_, _, responses) = block_parts(block, newline)?;
                numbers.extend(block_actions(&responses, newline)?.iter().map(|action| action.integer));
            } else {
                restored.push(crate::py::replace_bytes(block, &excluded, b"", Some(1)));
            }
        }
        Ok((script, restored, numbers))
    };
    let (script, restored, numbers) = match read() {
        Ok(found) => found,
        Err(error) if error.is(SCRIPT_ERROR) => return Err(changed_patch(resource)),
        Err(error) => return Err(error),
    };
    let original = [script.prefix, restored.concat(), script.suffix].concat();
    let candidates: Vec<Option<i64>> = if TEXT_RESOURCES.contains(&resource) {
        numbers.into_iter().filter(|number| (0..0xFFFF_FFFF).contains(number)).map(Some).collect()
    } else {
        vec![None]
    };
    for reference in candidates {
        match patch_script(resource, &original, reference) {
            Ok(patched) if patched == data => return Ok((original, reference, true)),
            Ok(_) => {}
            Err(error) if error.kind == ErrorKind::Profile => break,
            Err(error) => return Err(error),
        }
    }
    Err(changed_patch(resource))
}

/// The registry resource, lowered (`_resource`).
fn known(resource: &str) -> Result<String> {
    let resource = crate::py::lower(resource);
    if !resource_targets().contains_key(&resource) {
        return Err(profile(format!("Unsupported extended reward container resource: {resource}")));
    }
    Ok(resource)
}

/// Whether the bytes are this module's own patch, verified exactly.
///
/// False for an unpatched resource; raises for a changed copy of our patch.
pub fn own_patch(resource: &str, data: &[u8]) -> Result<bool> {
    let resource = known(resource)?;
    Ok(recover(&resource, data)?.2)
}

/// Why each container target using this resource cannot be prepared from
/// these bytes (target id to reason).
pub fn route_reasons(resource: &str, data: &[u8]) -> Result<Dict> {
    let resource = known(resource)?;
    let outcome = recover(&resource, data).and_then(|(original, _, _)| {
        if resource.starts_with("dlg:") {
            patch_dialogue(&resource, &original)
        } else {
            patch_script(&resource, &original, Some(GOLEM_TEXT))
        }
    });
    match outcome {
        Ok(_) => Ok(Dict::new()),
        Err(error) if error.kind == ErrorKind::Profile => {
            let ids = resource_targets().get(&resource).and_then(Value::as_list).expect("targets");
            Ok(ids.iter().map(|id| (str_value(id), Value::from(error.message.clone()))).collect())
        }
        Err(error) => Err(error),
    }
}

/// Canonical campaign-gated patch; `refs` maps original golem strref 38758.
pub fn patch_container_resource(resource: &str, data: &[u8], refs: Option<&BTreeMap<i64, i64>>) -> Result<Vec<u8>> {
    let resource = known(resource)?;
    let (original, old_ref, _) = recover(&resource, data)?;
    if resource.starts_with("dlg:") {
        return patch_dialogue(&resource, &original);
    }
    let reference = match refs.filter(|refs| !refs.is_empty()) {
        Some(refs) => refs.get(&GOLEM_TEXT).copied().or(old_ref),
        None => old_ref,
    };
    patch_script(&resource, &original, reference)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::list;

    #[test]
    fn registry_matches_reference_layout() {
        assert!(resource_targets()
            .get("bcs:ar1202")
            .is_some_and(|ids| ids.py_eq(&list!["bg2:mode2:target:53", "bg2:mode2:target:28"])));
        assert_eq!(targets().len(), 7);
        assert_eq!(trigger(16450, 0, 1, "BOOK03", 0, b"\n"), b"TR\n16450 0 1 0 0 \"BOOK03\" \"\" OB\n0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB\nTR\n".to_vec());
        assert_eq!(treasure("bg2:mode2:target:53").unwrap(), ["shld21", "sw1h32"]);
        assert_eq!(treasure_area("bg1:mode2:target:22", "eet").unwrap(), "bg3601");
        assert_eq!(treasure_area("bg1:mode2:target:22", "bgee").unwrap(), "ar3601");
    }

    #[test]
    fn treasure_container_needs_one_chest_with_the_whole_treasure() {
        let source = |index: i64, item: &str| {
            Value::Dict(dict! {"kind" => "container", "resource" => "are:ar1202", "item" => item,
                               "record" => dict! {"container_index" => index}})
        };
        let sources = [source(8, "shld21"), source(8, "sw1h32"), source(3, "sw1h32")];
        assert_eq!(treasure_container("bg2:mode2:target:28", &sources, "bg2ee").unwrap(), "are:ar1202/container/8");
        let error = treasure_container("bg2:mode2:target:28", &sources[1..], "bg2ee").unwrap_err();
        assert_eq!(error.message, "AR1202 has no container that holds SHLD21/SW1H32");
    }
}
