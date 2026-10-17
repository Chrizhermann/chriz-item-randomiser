//! Quest and dialogue changes that cir.tp2 made when it was installed (the
//! reference's `prepared.py`).
//!
//! The installer makes these changes (lib/campaign_patches.tpa), so WeiDU
//! restores the files on uninstall. It lists each change in
//! override/FLPRPAT.lua with status "applied", or why it left the change out.
//! The configurator offers a change only when it is applied there and the
//! installed file still reads the change's GLOBALs; it never writes them.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use std::path::Path;

pub const REGISTRY: &str = "override\\FLPRPAT.lua";

/// Each change's installed resource and the GLOBALs its installed form reads.
pub const CHANGES: &[(&str, &str, &[&[u8]])] = &[
    ("quest_pool", "bcs:blpool", &[b"FLPR_QPOOL", b"FLPR_XPOOL", b"FLPR_EPOOL"]),
    ("quest_altar", "bcs:troalt", &[b"FLPR_QALTAR", b"FLPR_XALTAR", b"FLPR_EALTAR"]),
    ("quest_book", "bcs:ppbook", &[b"FLPR_QBOOK", b"FLPR_XBOOK", b"FLPR_EBOOK"]),
    ("unshey", "dlg:unshey", &[b"FLPR_QUNSHEY"]),
    ("flail_heads", "dlg:kpglai01", &[b"FLPR_FLAIL"]),
    ("totsc_tome", "itm:flbhcryp", &[]),
];

/// The changes that the original mod's quest rewards depend on.
pub const QUEST_CHANGES: &[(&str, &str)] = &[("blpool", "quest_pool"), ("troalt", "quest_altar"), ("ppbook", "quest_book")];

pub fn quest_change(quest: &str) -> Option<&'static str> {
    QUEST_CHANGES.iter().find(|(name, _)| *name == quest).map(|(_, change)| *change)
}

/// Why the installer left a change out, as the registry records it.
const REASONS: &[(&str, &str)] = &[
    ("absent", "the game has no such file"),
    ("prepared-elsewhere", "it already contains changes from an earlier version of this randomiser"),
    ("grant-missing", "it no longer gives the reward"),
    ("grant-ambiguous", "it gives the reward more than once"),
    ("block-unrecognised", "it gives the reward in an unexpected way"),
    ("item-missing", "it no longer takes the quest item"),
    ("journal-unrecognised", "its journal entries have an unexpected form"),
    ("dialogue-unrecognised", "the conversation has an unexpected form"),
];

fn change(name: &str) -> Option<&'static (&'static str, &'static str, &'static [&'static [u8]])> {
    CHANGES.iter().find(|(change, _, _)| *change == name)
}

/// `_ENTRY.match(line)`: `^\s*([a-z_]+)\s*=\s*\{(.*)\},?\s*$`.
fn entry(line: &str) -> Option<(&str, &str)> {
    let rest = line.trim_start_matches(crate::py::is_space);
    let name_end = rest.find(|c: char| !(c.is_ascii_lowercase() || c == '_')).unwrap_or(rest.len());
    if name_end == 0 {
        return None;
    }
    let name = &rest[..name_end];
    let rest = rest[name_end..].trim_start_matches(crate::py::is_space).strip_prefix('=')?;
    let rest = rest.trim_start_matches(crate::py::is_space).strip_prefix('{')?;
    // The greedy body ends at the last "}" followed only by an optional comma
    // and whitespace.
    let mut search = rest.len();
    while let Some(close) = rest[..search].rfind('}') {
        let tail = &rest[close + 1..];
        let tail = tail.strip_prefix(',').unwrap_or(tail);
        if tail.chars().all(crate::py::is_space) {
            return Some((name, &rest[..close]));
        }
        search = close;
    }
    None
}

/// `_FIELD.findall(body)`: `([a-z_]+)\s*=\s*(?:"([^"\\]*)"|(-?[0-9]+))`.
fn fields(body: &str) -> Vec<(String, Value)> {
    let chars: Vec<char> = body.chars().collect();
    let mut result = Vec::new();
    let mut index = 0;
    while index < chars.len() {
        let attempt = (|| {
            let mut at = index;
            while at < chars.len() && (chars[at].is_ascii_lowercase() || chars[at] == '_') {
                at += 1;
            }
            if at == index {
                return None;
            }
            let key: String = chars[index..at].iter().collect();
            while at < chars.len() && crate::py::is_space(chars[at]) {
                at += 1;
            }
            if chars.get(at) != Some(&'=') {
                return None;
            }
            at += 1;
            while at < chars.len() && crate::py::is_space(chars[at]) {
                at += 1;
            }
            if chars.get(at) == Some(&'"') {
                let start = at + 1;
                let mut end = start;
                while end < chars.len() && chars[end] != '"' && chars[end] != '\\' {
                    end += 1;
                }
                if chars.get(end) != Some(&'"') {
                    return None;
                }
                let text: String = chars[start..end].iter().collect();
                return Some((key, Value::Str(text), end + 1));
            }
            let start = at;
            if chars.get(at) == Some(&'-') {
                at += 1;
            }
            let digits = at;
            while at < chars.len() && chars[at].is_ascii_digit() {
                at += 1;
            }
            if at == digits {
                return None;
            }
            let number: String = chars[start..at].iter().collect();
            let value = match number.parse::<i64>() {
                Ok(value) => Value::Int(value),
                Err(_) => Value::BigInt(number.trim_start_matches('0').to_string().into_boxed_str()),
            };
            Some((key, value, at))
        })();
        match attempt {
            Some((key, value, end)) => {
                result.push((key, value));
                index = end.max(index + 1);
            }
            None => index += 1,
        }
    }
    result
}

/// The registry's entries by change name; empty without the registry.
pub fn read_prepared(game_root: &Path) -> Result<Dict> {
    let path = game_root.join(REGISTRY);
    let data = match std::fs::read(&path) {
        Ok(data) => data,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Dict::new()),
        Err(error) => return Err(Error::os(&error, &path)),
    };
    let text = std::str::from_utf8(&data)
        .map_err(|error| Error::value(crate::json::utf8_error_message(&data, &error)))?
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let mut entries = Dict::new();
    for line in crate::py::splitlines(&text) {
        if let Some((name, body)) = entry(line) {
            let mut values = Dict::new();
            for (key, value) in fields(body) {
                values.insert(key, value);
            }
            entries.insert(name, Value::Dict(values));
        }
    }
    Ok(entries)
}

fn root(game_root: &str) -> std::path::PathBuf {
    std::path::PathBuf::from(crate::paths::display(Path::new(game_root)))
}

pub fn applied(game_root: &str, change_name: &str) -> Result<bool> {
    if change(change_name).is_none() {
        return Err(Error::profile(format!("Unknown installed change: {change_name}")));
    }
    let entries = read_prepared(&root(game_root))?;
    Ok(entries.get(change_name).and_then(|entry| entry.get("status")).and_then(Value::as_str) == Some("applied"))
}

pub fn applied_changes(game_root: &str) -> Result<std::collections::BTreeSet<String>> {
    let entries = read_prepared(&root(game_root))?;
    Ok(entries
        .iter()
        .filter(|(name, entry)| {
            change(name).is_some() && entry.get("status").and_then(Value::as_str) == Some("applied")
        })
        .map(|(name, _)| name.to_string())
        .collect())
}

/// Python's `str()` of a registry value.
fn display(value: &Value) -> String {
    match value {
        Value::Str(text) => text.clone(),
        Value::Int(number) => number.to_string(),
        Value::BigInt(text) => text.to_string(),
        other => crate::json::dumps_ascii(other, false),
    }
}

/// Tell the player which file a change needed and why it was not made.
pub fn left_out(game_root: &str, change_name: &str) -> Result<String> {
    let entries = read_prepared(&root(game_root))?;
    let (_, resource, _) = change(change_name).ok_or_else(|| Error::key(change_name))?;
    let (kind, name) = resource.split_once(':').expect("kind:name");
    let default_name = format!("{}.{}", name.to_ascii_uppercase(), kind.to_ascii_uppercase());
    let Some(entry) = entries.get(change_name) else {
        return Ok(format!("{default_name} was not prepared: this version of Chriz Item Randomiser is not installed"));
    };
    let resource = match entry.get("resource") {
        Some(value) if value.truthy() => display(value),
        _ => default_name,
    };
    let status = entry.get("status").cloned().unwrap_or(Value::from(""));
    let reason = match status.as_str().and_then(|text| REASONS.iter().find(|(key, _)| *key == text)) {
        Some((_, reason)) => reason.to_string(),
        None if status.truthy() => display(&status),
        None => "no reason recorded".into(),
    };
    Ok(format!("{resource} was left as installed ({reason})"))
}

/// Refuse a change that is not applied, or whose file no longer carries it.
pub fn require_installed(game_root: &str, change_name: &str, data: &[u8]) -> Result<()> {
    let (_, resource, markers) = change(change_name).ok_or_else(|| Error::key(change_name))?;
    if !applied(game_root, change_name)? {
        return Err(Error::profile(format!(
            "Chriz Item Randomiser did not prepare {resource}; reinstall it to use this option"
        )));
    }
    if markers.iter().any(|marker| crate::py::find_bytes(data, marker, 0).is_none()) {
        return Err(Error::profile(format!(
            "{resource} was replaced after Chriz Item Randomiser prepared it; \
             reinstall Chriz Item Randomiser to use this option"
        )));
    }
    Ok(())
}
