//! Finite component-570 ITM views; these assets never replace item resrefs
//! (the reference's `cursed_disguises.py`).
//!
//! The native caller selects one ordered face per source before equipment
//! loads, using the saved campaign seed, and restores pristine definitions for
//! other campaigns. Every copy of each original item shares that campaign's
//! choice.

use crate::error::{Error, Result};
use crate::game_text::GameText;
use crate::json::{Dict, Value};
use crate::original_options::{cursed_disguise_choices_value, patch_cursed_disguise};
use crate::profiles::is_hex256;
use crate::py::{digits_value, is_line_start, replace_bytes, rfind_bytes};
use crate::resources::sha256_hex;
use crate::translations::Line;
use crate::dict;
use std::collections::{BTreeMap, BTreeSet};

pub const ALGORITHM: &str = "park-miller-cursed-v1";
pub const SEED_RANGE: i64 = 2_147_483_646;
/// Asset files by name, in the reference's order.
pub type Assets = Vec<(String, Vec<u8>)>;

fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | b'\x0b' | b'\x0c')
}

fn is_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

/// `re.findall(rb"(?im)^\s*~([^~\r\n]+)~\s+#\d+\s+#(\d+)\b", log)`. Each
/// element is followed by a byte its repetition cannot match, and greedy
/// digits followed by a word byte cannot reach `\b` by backtracking, so this
/// single forward scan finds the regex's matches.
fn installed_components(log: &[u8]) -> Vec<(&[u8], &[u8])> {
    let mut result = Vec::new();
    let mut start = 0;
    while start <= log.len() {
        if !is_line_start(log, start) {
            start += 1;
            continue;
        }
        let matched = (|| {
            let mut at = start;
            while at < log.len() && is_space(log[at]) {
                at += 1;
            }
            if log.get(at) != Some(&b'~') {
                return None;
            }
            let path_start = at + 1;
            at = path_start;
            while at < log.len() && !matches!(log[at], b'~' | b'\r' | b'\n') {
                at += 1;
            }
            if at == path_start || log.get(at) != Some(&b'~') {
                return None;
            }
            let path = &log[path_start..at];
            let spaced = |at: usize| {
                let mut end = at;
                while end < log.len() && is_space(log[end]) {
                    end += 1;
                }
                (end > at).then_some(end)
            };
            at = spaced(at + 1)?;
            if log.get(at) != Some(&b'#') {
                return None;
            }
            let digits = at + 1;
            at = digits;
            while at < log.len() && log[at].is_ascii_digit() {
                at += 1;
            }
            if at == digits {
                return None;
            }
            at = spaced(at)?;
            if log.get(at) != Some(&b'#') {
                return None;
            }
            let component_start = at + 1;
            at = component_start;
            while at < log.len() && log[at].is_ascii_digit() {
                at += 1;
            }
            if at == component_start || log.get(at).is_some_and(|&byte| is_word(byte)) {
                return None;
            }
            Some((path, &log[component_start..at], at))
        })();
        match matched {
            Some((path, component, end)) => {
                result.push((path, component));
                start = end.max(start + 1);
            }
            None => start += 1,
        }
    }
    result
}

/// Match MOD_IS_INSTALLED item_rev.tp2 0, independent of its path/language.
pub fn item_revisions_installed(weidu_log: &[u8]) -> bool {
    installed_components(weidu_log).into_iter().any(|(path, component)| {
        let path = replace_bytes(path, b"\\", b"/", None);
        let name = match rfind_bytes(&path, b"/", 0, path.len()) {
            Some(index) => &path[index + 1..],
            None => &path[..],
        };
        name.to_ascii_lowercase() == b"item_rev.tp2" && digits_value(component) == 0
    })
}

fn spec_value(item_hashes: &Dict, game: &Value, item_revisions: &Value) -> Result<Value> {
    let available: Vec<&str> = item_hashes.keys().collect();
    let choices = cursed_disguise_choices_value(&available, game, item_revisions)?;
    let mut names = BTreeSet::new();
    for row in &choices {
        names.insert(row.get("source").and_then(Value::as_str).expect("source").to_string());
    }
    for row in &choices {
        for face in row.get("variants").and_then(Value::as_list).expect("variants") {
            names.insert(face.get("donor").and_then(Value::as_str).expect("donor").to_string());
        }
    }
    let hashes: Dict = names.into_iter().map(|name| {
        let hash = item_hashes.get(&name).cloned().expect("available name");
        (name, hash)
    }).collect();
    if hashes.values().any(|value| !value.as_str().is_some_and(is_hex256)) {
        return Err(Error::profile("Invalid cursed-disguise item fingerprint"));
    }
    Ok(Value::Dict(dict! {
        "game" => game.clone(), "item_revisions" => item_revisions.clone(),
        "choices" => choices, "resource_hashes" => hashes,
    }))
}

/// Freeze the installed original sources, authored faces and donor hashes.
///
/// Use the whole catalogue, irrespective of profile item selection. Missing
/// optional donors retain their original fallback die faces and probabilities.
pub fn cursed_disguise_spec(item_hashes: &Dict, game: &str, item_revisions: bool) -> Result<Value> {
    spec_value(item_hashes, &Value::from(game), &Value::Bool(item_revisions))
}

pub fn validate_cursed_disguise_spec(spec: &Value) -> Result<()> {
    let invalid = || Error::profile("Invalid cursed-disguise metadata");
    let Some(fields) = spec.as_dict().filter(|fields| fields.has_exact_keys(&["game", "item_revisions", "choices", "resource_hashes"]))
    else {
        return Err(invalid());
    };
    let Some(hashes) = fields.get("resource_hashes").and_then(Value::as_dict) else { return Err(invalid()) };
    let expected = spec_value(hashes, fields.get("game").expect("game"), fields.get("item_revisions").expect("flag"))?;
    if !spec.py_eq(&expected) {
        return Err(Error::profile("Cursed-disguise metadata differs from original component 570"));
    }
    Ok(())
}

fn rows(spec: &Value) -> &Vec<Value> {
    spec.get("choices").and_then(Value::as_list).expect("validated choices")
}

fn variants(row: &Value) -> &Vec<Value> {
    row.get("variants").and_then(Value::as_list).expect("validated variants")
}

fn source(row: &Value) -> &str {
    row.get("source").and_then(Value::as_str).expect("validated source")
}

/// A validated integer field (equal to the reference's int, as `==` saw it).
fn int_of(value: &Value) -> Option<i64> {
    match value {
        Value::Int(number) => Some(*number),
        Value::Bool(flag) => Some(*flag as i64),
        Value::Float(number) if number.fract() == 0.0 => Some(*number as i64),
        _ => None,
    }
}

/// Reconstruct one-based face indices without consuming placement randomness.
///
/// The native equivalent uses 64-bit arithmetic. The largest multiplication
/// also stays exact in Lua 5.1/JIT doubles. Rejection preserves uniform faces.
/// The fixed component-number salt separates this stream from item placement.
pub fn cursed_disguise_indices(spec: &Value, seed: &Value) -> Result<Dict> {
    validate_cursed_disguise_spec(spec)?;
    let seed = match seed {
        Value::Int(seed) if (1..=SEED_RANGE).contains(seed) => *seed,
        _ => return Err(Error::profile("Invalid cursed-disguise campaign seed")),
    };
    let mut state = (seed - 1 + 570) % SEED_RANGE + 1;
    let mut result = Dict::new();
    for row in rows(spec) {
        let count = variants(row).len() as i64;
        let cutoff = SEED_RANGE - SEED_RANGE % count;
        loop {
            state = state * 16807 % 2_147_483_647;
            if state - 1 < cutoff {
                break;
            }
        }
        result.insert(source(row), (state - 1) % count + 1);
    }
    Ok(result)
}

/// Materialise immutable original and face assets, plus the authored text.
///
/// Resource keys are lowercase resrefs. The caller prefixes returned filenames
/// with the revision in its private asset directory, never the game override.
/// The native fixture retains all faces, including duplicate fallback faces.
/// Files are returned in the reference's order. The texts are in the game's
/// `language` where CIR has all of them, otherwise in English, with their
/// female wordings in dialogf.tlk.
pub fn prepare_cursed_disguises(
    spec: &Value,
    resources: &BTreeMap<String, Vec<u8>>,
    text: &GameText,
    language: &str,
) -> Result<(Value, Assets, GameText)> {
    validate_cursed_disguise_spec(spec)?;
    let hashes = spec.get("resource_hashes").and_then(Value::as_dict).expect("validated hashes");
    for (name, expected) in hashes.iter() {
        if !resources.get(name).is_some_and(|data| expected.py_eq(&Value::from(sha256_hex(data)))) {
            return Err(Error::profile(format!("Cursed-disguise resource changed; rescan before export: {name}")));
        }
    }
    // cir.tp2's ALWAYS block loads the EE overrides after the general strings
    // on both bgee and bg2ee engines (including EET), and so does option_lines;
    // their exact CRLF text.
    let mut ids = BTreeSet::new();
    for row in rows(spec) {
        for face in variants(row) {
            for key in ["description_tra", "name_tra"] {
                if let Some(id) = face.get(key).and_then(int_of) {
                    ids.insert(id);
                }
            }
        }
    }
    let ids: Vec<i64> = ids.into_iter().collect();
    let wanted: Vec<Line> = ids.iter().map(|id| ("in-game.tra".to_string(), *id)).collect();
    let found = crate::translations::option_lines(language, &wanted)?;
    let values: Vec<(String, String)> = wanted.iter().map(|line| found[line].clone()).collect();
    let (text, allocated) = text.append(&values)?;
    let refs: BTreeMap<i64, i64> = ids.into_iter().zip(allocated.into_iter().map(i64::from)).collect();
    let mut items = Vec::new();
    let mut files = Vec::new();
    for row in rows(spec) {
        let source = source(row);
        let original = &resources[source];
        files.push((format!("{source}.original.itm"), original.clone()));
        let mut faces = Vec::new();
        for (index, face) in variants(row).iter().enumerate() {
            let donor = face.get("donor").and_then(Value::as_str).expect("validated donor");
            let reference = |key: &str| face.get(key).and_then(int_of).and_then(|id| refs.get(&id).copied());
            let patched =
                patch_cursed_disguise(original, &resources[donor], reference("description_tra"), reference("name_tra"))?;
            faces.push(Value::Dict(dict! {"donor" => donor, "sha256" => sha256_hex(&patched)}));
            files.push((format!("{source}.{}.itm", index + 1), patched));
        }
        items.push(Value::Dict(dict! {
            "resref" => source, "sha256" => hashes.get(source).cloned().expect("source hash"),
            "size" => original.len(), "variants" => faces,
        }));
    }
    let fixture = Value::Dict(dict! {"algorithm" => ALGORITHM, "items" => items});
    Ok((fixture, files, text))
}
