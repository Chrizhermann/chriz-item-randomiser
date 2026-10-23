//! The game files Apply writes outside CIR's folder, and what the game had
//! there before (the reference's `applied_files.py`).
//!
//! Apply (`activation::deploy_export`) writes a configuration's prepared
//! resources into override: the extended reward routes' dialogues and scripts
//! and the copies of their reward replies, the Kangaxx option's dialogue and
//! scripts, the Deck of Many Things key spell and the BGEE.LUA journal table.
//! It appends its texts to the game text (dialog.tlk, dialogf.tlk). These
//! files are what a configuration prepares, not a change of the game's mods,
//! so the scans (`resources::scan_game`, `sources::scan_world`) read each of
//! them, while it still holds exactly the bytes Apply wrote, as the game had it
//! before CIR first wrote it. A file Apply wrote that another mod or the player
//! changed since is read as it is, like every other file. Route recognition and
//! the export read the game's files as they are and recognise CIR's own patches
//! there.
//!
//! `randomiser-campaigns/applied-files.json` lists each file by its path in the
//! game, as the export names it ("override/SAFANJ.DLG",
//! "lang/en_us/dialog.tlk"): "written" is the SHA-256 of the bytes Apply wrote
//! last, "before" what the game had there before CIR first wrote it: null when
//! there was no such file, else its SHA-256 and size. CIR keeps a copy of each
//! earlier file under `randomiser-campaigns/before-apply/` by the same path.
//! Apply only appends to game text, so an earlier game text file is the first
//! "strings" entries of the one Apply wrote; no copy is kept of it.
//!
//! A record that cannot be read is not used: the scans then read every file as
//! it is, Apply's included, and the next Apply that writes outside CIR's folder
//! starts a new record, which takes each file it replaces as it is.

use crate::error::{ErrorKind, Result};
use crate::json::{Dict, Value};
use crate::profiles::{is_hex256, read_json};
use std::collections::HashSet;
use std::path::Path;

/// CIR's folder in the game (`activation::CAMPAIGN_DATA`).
pub const DATA: &str = "randomiser-campaigns";
pub const RECORD: &str = "applied-files.json";
pub const SCHEMA: &str = "flir-applied-files-v1";
/// The files as the game had them before, by their path in the game.
pub const BEFORE: &str = "before-apply";
pub const TEXT_SUFFIX: &str = ".tlk";
const LIMIT: i64 = 0xFFFF_FFFF;

/// A game path compared as Windows compares file names (ASCII letters without case).
pub fn key(path: &str) -> String {
    path.to_ascii_lowercase()
}

/// Whether the file is game text, to which Apply only appends.
pub fn is_text(path: &str) -> bool {
    key(path).ends_with(TEXT_SUFFIX)
}

fn is_part(part: &str) -> bool {
    !part.is_empty() && part != "." && part != ".." && !part.chars().any(|c| c <= '\u{1f}' || "\\/:*?\"<>|".contains(c))
}

/// Whether `path` names a file of the game outside CIR's folder, as the record keeps it.
pub fn is_game_path(path: &str) -> bool {
    let parts: Vec<&str> = path.split('/').collect();
    parts.iter().all(|part| is_part(part)) && key(parts[0]) != DATA
}

fn count(value: Option<&Value>) -> bool {
    matches!(value, Some(Value::Int(number)) if (0..=LIMIT).contains(number))
}

fn digest(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(is_hex256)
}

/// The files of a record by their game path; None unless it is a complete record.
pub fn entries(record: &Value) -> Option<&Dict> {
    let fields = record.as_dict()?;
    if !fields.has_exact_keys(&["schema", "files"]) || fields.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return None;
    }
    let files = fields.get("files")?.as_dict()?;
    let mut seen = HashSet::new();
    for (path, entry) in files.iter() {
        if !is_game_path(path) || !seen.insert(key(path)) {
            return None;
        }
        let entry = entry.as_dict()?;
        if !entry.has_exact_keys(&["before", "written"]) || !digest(entry.get("written")) {
            return None;
        }
        let before = match entry.get("before")? {
            Value::Null => continue,
            Value::Dict(before) => before,
            _ => return None,
        };
        let numbers: &[&str] = if is_text(path) { &["size", "strings"] } else { &["size"] };
        let fields: Vec<&str> = std::iter::once("sha256").chain(numbers.iter().copied()).collect();
        if !before.has_exact_keys(&fields) || !digest(before.get("sha256")) ||
            !numbers.iter().all(|field| count(before.get(field)))
        {
            return None;
        }
    }
    Some(files)
}

/// The files of the record at `path`; none when it is absent or cannot be read.
pub fn read(path: &Path) -> Result<Dict> {
    match read_json(path) {
        Ok(record) => Ok(entries(&record).cloned().unwrap_or_default()),
        Err(error) if matches!(error.kind, ErrorKind::Os | ErrorKind::Profile) => Ok(Dict::new()),
        Err(error) => Err(error),
    }
}

/// The record's own spelling of a game path, if it lists that file.
pub fn find<'a>(files: &'a Dict, path: &str) -> Option<&'a str> {
    let wanted = key(path);
    files.keys().find(|name| key(name) == wanted)
}

/// An entry's field as a number the record format allows.
pub fn number(fields: &Dict, field: &str) -> u64 {
    fields.get(field).and_then(Value::as_int).expect("checked by entries") as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(files: Vec<(&str, Value)>) -> Value {
        let mut entries = Dict::new();
        for (name, value) in files {
            entries.insert(name, value);
        }
        Value::Dict(crate::dict! {"schema" => SCHEMA, "files" => Value::Dict(entries)})
    }

    fn parse(text: &str) -> Value {
        crate::json::parse_lenient(text).unwrap()
    }

    #[test]
    fn record_format() {
        let entry = || parse(&format!(r#"{{"before": null, "written": "{}"}}"#, "a".repeat(64)));
        let earlier = |fields: &str| parse(&format!(r#"{{"before": {fields}, "written": "{}"}}"#, "c".repeat(64)));
        let hash = "b".repeat(64);
        let good = record(vec![
            ("override/X.DLG", entry()),
            ("lang/en_us/dialog.tlk", earlier(&format!(r#"{{"sha256": "{hash}", "size": 3, "strings": 2}}"#))),
            ("override/Y.BCS", earlier(&format!(r#"{{"sha256": "{hash}", "size": 0}}"#))),
        ]);
        assert_eq!(entries(&good).map(Dict::len), Some(3));
        let one = |name: &str, value: Value| record(vec![(name, value)]);
        let mut bad = vec![
            parse("[]"),
            Value::Dict(crate::dict! {"schema" => SCHEMA}),
            parse(&format!(r#"{{"schema": "{SCHEMA}", "files": [], "other": 1}}"#)),
            parse(r#"{"schema": "flir-applied-files-v2", "files": {}}"#),
            record(vec![("override/X.DLG", entry()), ("OVERRIDE/x.dlg", entry())]),
        ];
        for name in [
            "randomiser-campaigns/next.lua",
            "Randomiser-Campaigns/before-apply/override/X.DLG",
            "../override/X.DLG",
            "/override/X.DLG",
            "override//X.DLG",
            "override\\X.DLG",
            "C:/override/X.DLG",
            "override/./X.DLG",
            "",
        ] {
            bad.push(one(name, entry()));
        }
        for value in [
            format!(r#"{{"before": null, "written": "{}"}}"#, "A".repeat(64)),
            r#"{"before": null}"#.to_string(),
            format!(r#"{{"before": null, "written": "{}", "other": 1}}"#, "a".repeat(64)),
            format!(r#"{{"before": [], "written": "{}"}}"#, "c".repeat(64)),
        ] {
            bad.push(one("override/X.DLG", parse(&value)));
        }
        for fields in [
            format!(r#"{{"sha256": "{hash}", "size": 1, "strings": 2}}"#),
            format!(r#"{{"sha256": "{hash}", "size": true}}"#),
            format!(r#"{{"sha256": "{hash}", "size": -1}}"#),
            format!(r#"{{"sha256": "{hash}", "size": 4294967296}}"#),
            format!(r#"{{"sha256": "{hash}", "size": 1.0}}"#),
        ] {
            bad.push(one("override/X.DLG", earlier(&fields)));
        }
        bad.push(one("lang/en_us/dialog.tlk", earlier(&format!(r#"{{"sha256": "{hash}", "size": 1}}"#))));
        for value in bad {
            assert!(entries(&value).is_none(), "{value:?}");
        }
    }

    #[test]
    fn paths_compare_without_ascii_case() {
        let files = match record(vec![("override/SAFANJ.DLG", Value::Null)]) {
            Value::Dict(mut fields) => match fields.remove("files") {
                Some(Value::Dict(files)) => files,
                _ => unreachable!(),
            },
            _ => unreachable!(),
        };
        assert_eq!(find(&files, "override/safanj.dlg"), Some("override/SAFANJ.DLG"));
        assert_eq!(find(&files, "override/SAFANJ.BCS"), None);
        assert!(is_text("lang/en_US/DIALOGF.TLK"));
        assert!(!is_game_path("RANDOMISER-CAMPAIGNS/x"));
        assert!(is_game_path("override/MONKTU 8.DLG"));
    }
}
