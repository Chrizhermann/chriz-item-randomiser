//! Reviewed quest conditions of the containers in Item Randomiser's `mode2`
//! target tables (the extended place pools), gated per campaign (the
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
//! The reviewed hashes are read through [`crate::pins`] as
//! `container_rewards.ORIGINAL_HASHES`.

use crate::error::{Error, Result};
use crate::game_text::append_texts;
use crate::json::{Dict, Value};
use crate::py::{
    count_bytes, digits_value, find_bytes, is_line_end, is_line_start, replace_bytes, split3, splitlines_bytes,
    starts_with_ignore_ascii_case, strip_bytes, u32_le,
};
use crate::quest_options::{pad, texts, Dialogue};
use crate::quest_rewards::{blocks, global_guard, newline};
use crate::resources::sha256_hex;
use crate::dict;
use std::collections::BTreeMap;
use std::sync::OnceLock;

#[allow(clippy::too_many_arguments)]
fn target(
    game: &str,
    row: i64,
    area: &str,
    container: i64,
    item: &str,
    flag: &str,
    latch: &str,
    scope: &str,
    resources: &[&str],
) -> Dict {
    dict! {
        "target_id" => format!("{game}:mode2:target:{row}"), "area" => area,
        "container_index" => container, "physical_target_id" => format!("are:{area}/container/{container}"),
        "original_item" => item, "activation_global" => flag,
        "latch_global" => latch, "latch_scope" => scope, "resources" => resources.to_vec(),
    }
}

fn rows() -> &'static [Dict] {
    static ROWS: OnceLock<Vec<Dict>> = OnceLock::new();
    ROWS.get_or_init(|| {
        let golems = |row, container, item, number: u32, resources: &[&str]| {
            target(
                "bg2",
                row,
                "ar1303",
                container,
                item,
                &format!("FLPR_CKEEP{number}"),
                &format!("flRobbedGolems{number}"),
                "AR1303",
                resources,
            )
        };
        let samia = ["bcs:ar1202", "bcs:samia", "dlg:samia"];
        vec![
            golems(13, 0, "ax1h13", 1, &["bcs:kpgol01", "bcs:kpgol03"]),
            golems(43, 1, "bow12", 2, &["bcs:kpgol02", "bcs:kpgol04"]),
            golems(3, 2, "hamm04", 3, &["bcs:kpgol05", "bcs:kpgol06"]),
            // These are the handler's item names, not the source row's Ident pairing.
            target("bg2", 53, "ar1202", 8, "shld21", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202", &samia),
            target("bg2", 28, "ar1202", 8, "sw1h32", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202", &samia),
            target("bg1", 22, "bg3601", 0, "book03", "FLPR_CSAFANA", "flSafanaTreasure", "GLOBAL", &["bcs:safana", "dlg:safanj"]),
            target("bg2", 61, "ar1514", 0, "bow19b", "FLPR_CPPCHST", "RobbedGolem", "GLOBAL", &["bcs:ppchest"]),
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

/// Compiled block indexes / DLG state-trigger table indexes in the reviewed
/// EET: `(block count, sites)`; dialogues have no block count.
pub const SITES: &[(&str, Option<usize>, &[usize])] = &[
    ("bcs:kpgol01", Some(1), &[0]),
    ("bcs:kpgol02", Some(1), &[0]),
    ("bcs:kpgol03", Some(1), &[0]),
    ("bcs:kpgol04", Some(1), &[0]),
    ("bcs:kpgol05", Some(1), &[0]),
    ("bcs:kpgol06", Some(1), &[0]),
    ("bcs:ar1202", Some(24), &[15, 16]),
    ("bcs:samia", Some(1), &[0]),
    ("bcs:safana", Some(16), &[7, 8, 9]),
    ("bcs:ppchest", Some(1), &[0]),
    ("dlg:samia", None, &[1]),
    ("dlg:safanj", None, &[0, 2, 8]),
];

pub const ORIGINAL_HASHES: &[(&str, &str)] = &[
    ("bcs:kpgol01", "7e569c1e414c4afef74b274515d073c04dd92ba2fa0ab0f96b8bbccf2c80cfa6"),
    ("bcs:kpgol02", "3dd4220a9bc7ae4b5778f3b6a1dc7438d943b138cb9383aac26d8835489a5431"),
    ("bcs:kpgol03", "82e7a96eac8a9ad8b913982ca8d03bdc824346de42267d80065db5d3155077f0"),
    ("bcs:kpgol04", "d33714c44ebc89dd5c2fef834781cfbfe69f1f8cd0a9ff9de21fee823b4efc7d"),
    ("bcs:kpgol05", "d473eebb61a8a03d8bc0b1c993e0810c67a6e01225687b5cba035c6a5e067dde"),
    ("bcs:kpgol06", "157cc4c98135416842176cb76968280630160156e4039ba0e42cf744d33d0a66"),
    ("bcs:ar1202", "d6c3de8366f99088d41e1c01a4a8d8811add81771926d9d7d70ef5193fca2280"),
    ("bcs:samia", "823d03307573daaa08ad7c853879e1b62796d67212b4e62e6d6ba66f0cac32c9"),
    ("bcs:safana", "afc23b13dfd4e87978997e55e35c6f4f4d14238647ef1197653184e55539bf83"),
    ("bcs:ppchest", "446b07d5de81386e5e7f6b49efb323c1d617222cc4714f1eae400e1c08c049d2"),
    ("dlg:samia", "3572813dfb538c88953709b55e6bcd48139ae24c9a9b84e176f9920652a88254"),
    ("dlg:safanj", "8a890ecdc434267ed79661db132b140c1371a068de234d8caba541aa962da252"),
];
pub const TEXT_RESOURCES: [&str; 3] = ["bcs:kpgol01", "bcs:kpgol02", "bcs:kpgol05"];
const TRAILER: &[u8] = b"FLPRCON1";

/// `ORIGINAL_HASHES` with any test overrides.
pub fn original_hashes() -> Vec<(String, String)> {
    crate::pins::table("container_rewards.ORIGINAL_HASHES", ORIGINAL_HASHES)
}

fn original_hash(resource: &str) -> Result<String> {
    crate::pins::get("container_rewards.ORIGINAL_HASHES", ORIGINAL_HASHES, resource).ok_or_else(|| Error::key(resource))
}

fn sites(resource: &str) -> Result<(Option<usize>, &'static [usize])> {
    SITES.iter().find(|(key, _, _)| *key == resource).map(|(_, count, sites)| (*count, *sites)).ok_or_else(|| Error::key(resource))
}

fn spec_for(resource: &str) -> Result<&'static Dict> {
    let ids = resource_targets().get(resource).and_then(Value::as_list).ok_or_else(|| Error::key(resource))?;
    let id = ids.first().and_then(Value::as_str).ok_or_else(|| Error::value("tuple index out of range"))?;
    Ok(targets().get(id).and_then(Value::as_dict).expect("target row"))
}

fn text(spec: &Dict, key: &str) -> String {
    spec.get(key).and_then(Value::as_str).expect("container field").to_string()
}

pub fn target_spec(target_id: &str) -> Result<Dict> {
    match targets().get(target_id).and_then(Value::as_dict) {
        Some(row) => Ok(row.clone()),
        None => Err(Error::profile(format!("Unsupported container quest target: {target_id}"))),
    }
}

/// Allocate the golems' original text once for every requested golem script.
pub fn prepare_container_texts<I, S>(
    tlk: &[u8],
    resources: I,
    language: &str,
) -> Result<(Vec<u8>, BTreeMap<String, BTreeMap<i64, i64>>)>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut needed: Vec<String> = resources
        .into_iter()
        .map(|resource| resource.as_ref().to_string())
        .filter(|resource| TEXT_RESOURCES.contains(&resource.as_str()))
        .collect();
    needed.sort();
    needed.dedup();
    if needed.is_empty() {
        return Ok((tlk.to_vec(), BTreeMap::new()));
    }
    if crate::py::lower(language) != "en_us" {
        return Err(Error::profile("Container quest text currently requires the original English translation"));
    }
    let text = texts("languages/english/in-game.tra")
        .remove(&101)
        .ok_or_else(|| Error::new(crate::error::ErrorKind::Key, "101"))?;
    let (updated, refs) = append_texts(tlk, &[text])?;
    let result = needed.into_iter().map(|resource| (resource, BTreeMap::from([(38758, refs[0] as i64)]))).collect();
    Ok((updated, result))
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
    trigger(16399, value, 0, &(text(spec, "latch_scope") + &text(spec, "latch_global")), 0, newline)
}

/// Return one exact original item predicate and the corresponding latch value.
fn item_condition(conditions: &[u8], resource: &str, spec: &Dict, newline: &[u8]) -> Result<(Vec<u8>, i64)> {
    if text(spec, "activation_global") == "FLPR_CSAMIA" {
        for items in [["sw1h32", "shld21"], ["SW1H32", "SHLD21"]] {
            let mut old = trigger(16521, 2, 0, "", 0, newline);
            for item in items {
                old.extend(trigger(16450, 0, 0, item, 0, newline));
            }
            if count_bytes(conditions, &old) == 1 {
                return Ok((old, 1));
            }
        }
    } else {
        for negate in [0, 1] {
            let old = trigger(16450, 0, negate, &crate::py::upper(&text(spec, "original_item")), 0, newline);
            if count_bytes(conditions, &old) == 1 {
                return Ok((old, 1 - negate));
            }
        }
    }
    Err(Error::profile(format!("{resource} container quest item condition changed")))
}

fn patch_script(resource: &str, original: &[u8], reference: Option<i64>) -> Result<Vec<u8>> {
    let (count, sites) = sites(resource)?;
    let newline = newline(original)?;
    let (blocks, prefix, suffix) = blocks(original, count.ok_or_else(|| Error::type_error("unsupported operand"))?, newline)?;
    let spec = spec_for(resource)?;
    let flag = text(spec, "activation_global");
    let boundary = [b"CO", newline].concat();
    let mut result: Vec<Vec<u8>> = Vec::new();
    for (index, block) in blocks.iter().enumerate() {
        if !sites.contains(&index) {
            result.push(block.clone());
            continue;
        }
        let (head, conditions, response) = split3(block, &boundary)?;
        let excluded = [head.as_slice(), &boundary, &conditions, &global_guard(&flag, false, newline)?, &boundary, &response].concat();
        if resource != "bcs:ppchest" {
            let (old, value) = item_condition(&conditions, resource, spec, newline)?;
            let changed = replace_bytes(&conditions, &old, &latch(spec, value, newline), Some(1));
            let mut selected_response = response.clone();
            if TEXT_RESOURCES.contains(&resource) {
                let Some(reference) = reference.filter(|value| (0..0xFFFF_FFFF).contains(value)) else {
                    return Err(Error::profile("Container quest requires allocated golem text"));
                };
                let old_text = [b"38758 0 0 0 0\"\" \"\" AC" as &[u8], newline].concat();
                if count_bytes(&response, &old_text) != 1 {
                    return Err(Error::profile("Container quest golem display changed"));
                }
                selected_response =
                    replace_bytes(&response, &old_text, &[reference.to_string().as_bytes(), &old_text[5..]].concat(), None);
            }
            result.push(
                [head.as_slice(), &boundary, &changed, &global_guard(&flag, true, newline)?, &boundary, &selected_response].concat(),
            );
        }
        // PPCHST's original setter is disabled only when the observer owns it.
        result.push(excluded);
    }
    Ok([prefix, result.concat(), suffix].concat())
}

fn literal_ci(data: &[u8], at: usize, text: &[u8]) -> Option<usize> {
    starts_with_ignore_ascii_case(data, at, text).then_some(at + text.len())
}

fn line_break(data: &[u8], at: usize) -> Option<usize> {
    literal_ci(data, at, b"\r\n").or_else(|| literal_ci(data, at, b"\n"))
}

/// The old item condition in a state trigger, as `re.finditer` matches:
/// samia `(?im)^[ \t]*OR\(2\)\r?\nPartyHasItem\("sw1h32"\)\r?\n` +
/// `PartyHasItem\("shld21"\)\r?\n`, otherwise
/// `(?im)^!?PartyHasItem\("BOOK03"\)\r?\n`. Returns (start, end) spans.
fn dialogue_conditions(prior: &[u8], samia: bool) -> Vec<(usize, usize)> {
    let mut matches = Vec::new();
    let mut start = 0;
    while start <= prior.len() {
        if !is_line_start(prior, start) {
            start += 1;
            continue;
        }
        let matched = if samia {
            (|| {
                let mut at = start;
                while at < prior.len() && (prior[at] == b' ' || prior[at] == b'\t') {
                    at += 1;
                }
                let at = line_break(prior, literal_ci(prior, at, b"OR(2)")?)?;
                let at = line_break(prior, literal_ci(prior, at, b"PartyHasItem(\"sw1h32\")")?)?;
                line_break(prior, literal_ci(prior, at, b"PartyHasItem(\"shld21\")")?)
            })()
        } else {
            let at = if prior.get(start) == Some(&b'!') { start + 1 } else { start };
            literal_ci(prior, at, b"PartyHasItem(\"BOOK03\")").and_then(|at| line_break(prior, at))
        };
        match matched {
            Some(end) => {
                matches.push((start, end));
                start = end.max(start + 1);
            }
            None => start += 1,
        }
    }
    matches
}

fn patch_dialogue(resource: &str, original: &[u8]) -> Result<Vec<u8>> {
    let mut dialogue = Dialogue::new(original, &resource[4.min(resource.len())..])?;
    let spec = spec_for(resource)?;
    let flag = text(spec, "activation_global");
    for &index in sites(resource)?.1 {
        let prior = dialogue.state_triggers.get(index).ok_or_else(|| Error::value("list index out of range"))?.clone();
        let samia = resource == "dlg:samia";
        let matches = dialogue_conditions(&prior, samia);
        if matches.len() != 1 {
            return Err(Error::profile(format!("{resource} container quest dialogue condition changed")));
        }
        let (start, end) = matches[0];
        let old = &prior[start..end];
        let negative = old.starts_with(b"!");
        let mut terms = splitlines_bytes(strip_bytes(old));
        if samia && !terms.is_empty() {
            // Flatten the old OR into the first new OR group.
            terms.remove(0);
        }
        // (selected OR original) AND (!selected OR latch). No nested OR/AND.
        let mut changed = format!("OR({})\r\nGlobal(\"{flag}\",\"GLOBAL\",1)\r\n", terms.len() + 1).into_bytes();
        changed.extend(terms.join(&b"\r\n"[..]));
        changed.extend_from_slice(b"\r\n");
        changed.extend(
            format!(
                "OR(2)\r\n!Global(\"{flag}\",\"GLOBAL\",1)\r\nGlobal(\"{}\",\"{}\",{})\r\n",
                text(spec, "latch_global"),
                text(spec, "latch_scope"),
                if negative { 0 } else { 1 }
            )
            .into_bytes(),
        );
        dialogue.state_triggers[index] = [&prior[..start], &changed, &prior[end..]].concat();
    }
    // Dialogue appends tables and leaves the original body recoverable.
    let mut out = dialogue.render();
    out.extend_from_slice(&(original.len() as u32).to_le_bytes());
    out.extend(pad(&original[..original.len().min(52)], 52));
    out.extend_from_slice(TRAILER);
    Ok(out)
}

/// `re.findall(rb'(?m)^151OB\r?\n(?:[^\n]*\n){5}(\d+) 0 0 0 0"" "" AC\r?$', block)`.
fn golem_text_refs(block: &[u8]) -> Vec<i64> {
    const TAIL: &[u8] = b" 0 0 0 0\"\" \"\" AC";
    let mut result = Vec::new();
    let mut start = 0;
    while start <= block.len() {
        if !is_line_start(block, start) {
            start += 1;
            continue;
        }
        let matched = (|| {
            let mut at = start + 5;
            if !block[start..].starts_with(b"151OB") {
                return None;
            }
            if block.get(at) == Some(&b'\r') {
                at += 1;
            }
            if block.get(at) != Some(&b'\n') {
                return None;
            }
            at += 1;
            for _ in 0..5 {
                at += find_bytes(block, b"\n", at)? - at + 1;
            }
            let digits_start = at;
            while at < block.len() && block[at].is_ascii_digit() {
                at += 1;
            }
            if at == digits_start || !block[at..].starts_with(TAIL) {
                return None;
            }
            let digits = digits_value(&block[digits_start..at]);
            let end = at + TAIL.len();
            if block.get(end) == Some(&b'\r') && is_line_end(block, end + 1) {
                Some((digits, end + 1))
            } else {
                is_line_end(block, end).then_some((digits, end))
            }
        })();
        match matched {
            Some((digits, end)) => {
                result.push(digits);
                start = end.max(start + 1);
            }
            None => start += 1,
        }
    }
    result
}

fn recover(resource: &str, data: &[u8]) -> Result<(Vec<u8>, Option<i64>)> {
    let expected = original_hash(resource)?;
    if sha256_hex(data) == expected {
        return Ok((data.to_vec(), None));
    }
    let mut reference = None;
    let original = if resource.starts_with("dlg:") {
        if data.len() < 64 || !data.ends_with(TRAILER) {
            return Err(Error::profile("Container quest dialogue changed"));
        }
        let size = u32_le(data, data.len() - 64) as usize;
        let header = &data[data.len() - 60..data.len() - 8];
        if !(52..=data.len() - 64).contains(&size) {
            return Err(Error::profile("Container quest dialogue recovery bounds changed"));
        }
        [header, &data[52..size]].concat()
    } else {
        let newline = newline(data)?;
        let (count, sites) = sites(resource)?;
        let count = count.ok_or_else(|| Error::type_error("unsupported operand"))?;
        let expected_blocks = count + if resource == "bcs:ppchest" { 0 } else { sites.len() };
        let (blocks, prefix, suffix) = blocks(data, expected_blocks, newline)?;
        let flag = text(spec_for(resource)?, "activation_global");
        let selected = global_guard(&flag, true, newline)?;
        let excluded = global_guard(&flag, false, newline)?;
        let mut restored = Vec::new();
        for block in &blocks {
            if find_bytes(block, &selected, 0).is_some() {
                if TEXT_RESOURCES.contains(&resource) {
                    let matches = golem_text_refs(block);
                    if matches.len() != 1 {
                        return Err(Error::profile("Container quest golem text recovery changed"));
                    }
                    reference = Some(matches[0]);
                }
            } else {
                restored.push(replace_bytes(block, &excluded, b"", None));
            }
        }
        [prefix, restored.concat(), suffix].concat()
    };
    if sha256_hex(&original) != expected {
        return Err(Error::profile("Container quest original resource changed"));
    }
    let regenerated =
        if resource.starts_with("dlg:") { patch_dialogue(resource, &original)? } else { patch_script(resource, &original, reference)? };
    if regenerated != data {
        return Err(Error::profile("Container quest patch changed"));
    }
    Ok((original, reference))
}

fn known(resource: &str) -> Result<String> {
    let resource = crate::py::lower(resource);
    if !original_hashes().iter().any(|(key, _)| *key == resource) {
        return Err(Error::profile("Unsupported container quest resource"));
    }
    Ok(resource)
}

/// Recover only pristine reviewed bytes or an exact patch made by this module.
pub fn original_container_resource(resource: &str, data: &[u8]) -> Result<Vec<u8>> {
    let resource = known(resource)?;
    Ok(recover(&resource, data)?.0)
}

/// Canonical campaign-gated patch; `refs` maps original golem strref 38758.
pub fn patch_container_resource(resource: &str, data: &[u8], refs: Option<&BTreeMap<i64, i64>>) -> Result<Vec<u8>> {
    let resource = known(resource)?;
    let (original, old_ref) = recover(&resource, data)?;
    if resource.starts_with("dlg:") {
        return patch_dialogue(&resource, &original);
    }
    let reference = match refs.filter(|refs| !refs.is_empty()) {
        Some(refs) => refs.get(&38758).copied().or(old_ref),
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
    }
}
