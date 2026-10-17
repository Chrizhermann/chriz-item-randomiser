//! BG1 resource names on EET from the checked-in bg1pal tables (the
//! reference's `game_names.py`). The authoritative collision lists stay in
//! `lib/fl#bg1pal.tpa`.

use crate::error::{Error, Result};
use std::collections::HashMap;
use std::sync::OnceLock;

const EXTENSIONS: [&str; 6] = ["are", "bcs", "cre", "itm", "sto", "dlg"];
const TABLE_EXTENSIONS: [&str; 5] = ["bcs", "cre", "itm", "sto", "dlg"];

fn is_resref_char(c: char) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '#'
}

/// `[a-z0-9_#]{1,8}` as a full match.
fn is_resref(text: &str) -> bool {
    (1..=8).contains(&text.chars().count()) && text.chars().all(is_resref_char)
}

/// `ar[0-9]+n?` as a full match.
fn is_bg1_area(name: &str) -> bool {
    let Some(rest) = name.strip_prefix("ar") else { return false };
    let rest = rest.strip_suffix('n').unwrap_or(rest);
    !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_digit())
}

/// `_PAIR.fullmatch(line)`: `([a-z0-9_#]{1,8})\s*=>\s*([a-z0-9_#]{1,8})`.
fn pair(line: &str) -> Option<(String, String)> {
    let left_end = line.find(|c: char| !is_resref_char(c))?;
    let left = &line[..left_end];
    let rest = line[left_end..].trim_start_matches(crate::py::is_space);
    let rest = rest.strip_prefix("=>")?;
    let right = rest.trim_start_matches(crate::py::is_space);
    if !is_resref(left) || !is_resref(right) {
        return None;
    }
    Some((left.to_string(), right.to_string()))
}

type Tables = HashMap<String, HashMap<String, String>>;

/// The bodies of `ACTION_DEFINE_ASSOCIATIVE_ARRAY fl#bg1pal#eet#<ext> BEGIN`
/// ... `END` blocks, in file order.
fn arrays(source: &str) -> Vec<(String, String)> {
    const HEAD: &str = "ACTION_DEFINE_ASSOCIATIVE_ARRAY fl#bg1pal#eet#";
    let lines: Vec<&str> = source.split('\n').collect();
    let mut result = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let line = lines[index];
        index += 1;
        let Some(rest) = line.strip_prefix(HEAD) else { continue };
        let name_end = rest.find(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit())).unwrap_or(rest.len());
        let extension = &rest[..name_end];
        let tail = &rest[name_end..];
        if extension.is_empty() || !(tail == " BEGIN" || tail == " BEGIN\r") || index > lines.len() - 1 {
            continue;
        }
        let mut body = Vec::new();
        let mut closed = false;
        let mut cursor = index;
        while cursor < lines.len() {
            let candidate = lines[cursor];
            if let Some(after) = candidate.strip_prefix("END") {
                if after.chars().all(|c| c == ' ' || c == '\t') {
                    closed = true;
                    break;
                }
            }
            body.push(candidate);
            cursor += 1;
        }
        if !closed {
            continue;
        }
        let mut text = body.join("\n");
        if !body.is_empty() {
            text.push('\n');
        }
        result.push((extension.to_string(), text));
        index = cursor + 1;
    }
    result
}

fn load_tables() -> Result<Tables> {
    let raw = crate::data::text("lib/fl#bg1pal.tpa").expect("embedded bg1pal library");
    let source = raw.replace("\r\n", "\n").replace('\r', "\n");
    let mut tables: Tables = HashMap::new();
    for (extension, body) in arrays(&source) {
        if !TABLE_EXTENSIONS.contains(&extension.as_str()) {
            continue;
        }
        if tables.contains_key(&extension) {
            return Err(Error::profile(format!("Duplicate bg1pal EET table: {extension}")));
        }
        let mut entries = HashMap::new();
        for raw_line in crate::py::splitlines(&body) {
            let line = crate::py::strip(raw_line.split("//").next().unwrap_or(""));
            if line.is_empty() {
                continue;
            }
            match pair(line) {
                Some((from, to)) if !entries.contains_key(&from) => {
                    entries.insert(from, to);
                }
                _ => return Err(Error::profile(format!("Unsupported bg1pal EET {extension} mapping: {line}"))),
            }
        }
        tables.insert(extension, entries);
    }
    if tables.len() != TABLE_EXTENSIONS.len() {
        return Err(Error::profile("Incomplete bg1pal EET resource mappings"));
    }
    Ok(tables)
}

fn eet_tables() -> Result<&'static Tables> {
    static TABLES: OnceLock<std::result::Result<Tables, Error>> = OnceLock::new();
    TABLES.get_or_init(load_tables).as_ref().map_err(Clone::clone)
}

/// The effective resref for a BG1/BG2 campaign on a supported game.
///
/// EET changes BG1 `ar[digits][n]` ARE/BCS names to `bg...` and applies the
/// library's explicit collision maps. BG2 and vanilla EE names stay as they
/// are. BGT and Tutu are outside this adapter.
pub fn convert_resource(resref: &str, extension: &str, campaign: &str, game: &str) -> Result<String> {
    let game_lower = crate::py::lower(game);
    if !["eet", "bgee", "bg2ee"].contains(&game_lower.as_str()) {
        return Err(Error::profile(format!("Unsupported game for resource conversion: {game}")));
    }
    let campaign_lower = crate::py::lower(campaign);
    if !["bg1", "bg2"].contains(&campaign_lower.as_str()) {
        return Err(Error::profile(format!("Unsupported campaign for resource conversion: {campaign}")));
    }
    let kind = crate::py::lower(extension);
    if !EXTENSIONS.contains(&kind.as_str()) {
        return Err(Error::profile(format!("Unsupported resource extension: {extension}")));
    }
    let name = crate::py::lower(resref);
    if !is_resref(&name) {
        return Err(Error::profile(format!("Invalid resource name: {resref}")));
    }
    if game_lower != "eet" || campaign_lower == "bg2" {
        return Ok(name);
    }
    if (kind == "are" || kind == "bcs") && is_bg1_area(&name) {
        return Ok(format!("bg{}", &name[2..]));
    }
    if kind == "are" {
        return Ok(name);
    }
    Ok(eet_tables()?.get(&kind).and_then(|table| table.get(&name)).cloned().unwrap_or(name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bg1_names_follow_eet() {
        assert_eq!(convert_resource("AR2600", "are", "bg1", "eet").unwrap(), "bg2600");
        assert_eq!(convert_resource("ar0602", "bcs", "bg2", "eet").unwrap(), "ar0602");
        assert_eq!(convert_resource("ar2600", "are", "bg1", "bgee").unwrap(), "ar2600");
        assert!(eet_tables().unwrap().values().all(|table| !table.is_empty()));
        assert!(convert_resource("a b", "itm", "bg1", "eet").is_err());
    }
}
