//! BG1 and Siege of Dragonspear resource names on EET (the reference's
//! `game_names.py`).
//!
//! EET imports Baldur's Gate: Enhanced Edition, Siege of Dragonspear included,
//! and renames the BG:EE resources whose names Baldur's Gate II already uses.
//! It keeps its rename lists in its own mod folder (`EET/lib/pcu_dict.tph`),
//! and those lists name exactly what that EET version renamed. [`eet_names`]
//! reads them; when the folder is gone or the lists cannot be read, it falls
//! back to the copy in `lib/fl#bg1pal.tpa`, which predates Siege of
//! Dragonspear and EET 14.
//!
//! Only the resource types needed by the post-install campaign planner are
//! exposed.

use crate::error::{py_repr_str, Error, Result};
use crate::json::{Dict, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

const EXTENSIONS: [&str; 6] = ["are", "bcs", "cre", "itm", "sto", "dlg"];
/// The resource types with rename lists, sorted.
const TABLE_EXTENSIONS: [&str; 5] = ["bcs", "cre", "dlg", "itm", "sto"];
/// EET's rename lists, below the game folder.
const INSTALLED: [&str; 3] = ["eet", "lib", "pcu_dict.tph"];
/// Lists an installed EET must have for its file to count as its rename
/// lists, sorted.
const REQUIRED_INSTALLED: [&str; 2] = ["cre", "itm"];
/// How many installed files' lists are kept (`lru_cache(maxsize=4)`).
const CACHED_FILES: usize = 4;

/// One resource type's renames, old name to new name, in the order of the
/// list. A later entry for a name replaces the earlier one in its place.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Renames {
    entries: Vec<(String, String)>,
    index: HashMap<String, usize>,
}

impl Renames {
    fn set(&mut self, old: String, new: String) {
        match self.index.get(&old) {
            Some(&at) => self.entries[at].1 = new,
            None => {
                self.index.insert(old.clone(), self.entries.len());
                self.entries.push((old, new));
            }
        }
    }

    /// The new name of `name`, if the list renames it.
    pub fn get(&self, name: &str) -> Option<&str> {
        self.index.get(name).map(|&at| self.entries[at].1.as_str())
    }

    pub fn contains(&self, name: &str) -> bool {
        self.index.contains_key(name)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, &str)> + '_ {
        self.entries.iter().map(|(old, new)| (old.as_str(), new.as_str()))
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

/// Rename lists by resource type, in the order they were read.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Tables(Vec<(String, Renames)>);

impl Tables {
    /// The list of one resource type.
    pub fn get(&self, kind: &str) -> Option<&Renames> {
        self.0.iter().find(|(known, _)| known == kind).map(|(_, renames)| renames)
    }

    /// The resource types, in order.
    pub fn kinds(&self) -> impl Iterator<Item = &str> + '_ {
        self.0.iter().map(|(kind, _)| kind.as_str())
    }

    fn entry(&mut self, kind: &str) -> &mut Renames {
        let at = match self.0.iter().position(|(known, _)| known == kind) {
            Some(at) => at,
            None => {
                self.0.push((kind.to_string(), Renames::default()));
                self.0.len() - 1
            }
        };
        &mut self.0[at].1
    }

    /// `{type: {old: new}}`.
    pub fn to_value(&self) -> Value {
        let mut result = Dict::new();
        for (kind, renames) in &self.0 {
            let mut entries = Dict::new();
            for (old, new) in renames.iter() {
                entries.insert(old, new);
            }
            result.insert(kind.as_str(), entries);
        }
        Value::Dict(result)
    }

    /// Lists as [`Tables::to_value`] writes them.
    pub fn from_value(value: &Value) -> Result<Tables> {
        let invalid = || Error::type_error("EET rename lists must map resource types to {old: new} names");
        let mut tables = Tables::default();
        for (kind, entries) in value.as_dict().ok_or_else(invalid)?.iter() {
            let renames = tables.entry(kind);
            for (old, new) in entries.as_dict().ok_or_else(invalid)?.iter() {
                renames.set(old.to_string(), new.as_str().ok_or_else(invalid)?.to_string());
            }
        }
        Ok(tables)
    }
}

/// EET's renames for one game: the installed lists, or CIR's copy with a
/// note why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EetNames {
    pub tables: Arc<Tables>,
    pub installed: bool,
    pub notes: Vec<String>,
}

impl EetNames {
    /// `{"tables": ..., "installed": ..., "notes": [...]}`.
    pub fn to_value(&self) -> Value {
        let mut result = Dict::new();
        result.insert("tables", self.tables.to_value());
        result.insert("installed", self.installed);
        result.insert("notes", Value::List(self.notes.iter().map(|note| Value::from(note.as_str())).collect()));
        Value::Dict(result)
    }

    /// Renames as [`EetNames::to_value`] writes them.
    pub fn from_value(value: &Value) -> Result<EetNames> {
        let invalid = || Error::type_error("EET names must hold tables, installed and notes");
        let dict = value.as_dict().ok_or_else(invalid)?;
        let tables = Tables::from_value(dict.get("tables").ok_or_else(invalid)?)?;
        let installed = dict.get("installed").and_then(Value::as_bool).ok_or_else(invalid)?;
        let mut notes = Vec::new();
        for note in dict.get("notes").and_then(Value::as_list).ok_or_else(invalid)? {
            notes.push(note.as_str().ok_or_else(invalid)?.to_string());
        }
        Ok(EetNames { tables: Arc::new(tables), installed, notes })
    }
}

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
    let mut tables = Tables::default();
    for (extension, body) in arrays(&source) {
        if !TABLE_EXTENSIONS.contains(&extension.as_str()) {
            continue;
        }
        if tables.get(&extension).is_some() {
            return Err(Error::profile(format!("Duplicate bg1pal EET table: {extension}")));
        }
        let entries = tables.entry(&extension);
        for raw_line in crate::py::splitlines(&body) {
            let line = crate::py::strip(raw_line.split("//").next().unwrap_or(""));
            if line.is_empty() {
                continue;
            }
            match pair(line) {
                Some((from, to)) if !entries.contains(&from) => entries.set(from, to),
                _ => return Err(Error::profile(format!("Unsupported bg1pal EET {extension} mapping: {line}"))),
            }
        }
    }
    if tables.0.len() != TABLE_EXTENSIONS.len() {
        return Err(Error::profile("Incomplete bg1pal EET resource mappings"));
    }
    Ok(tables)
}

/// CIR's own copy of EET's lists (`lib/fl#bg1pal.tpa`).
pub fn eet_tables() -> Result<Arc<Tables>> {
    static TABLES: OnceLock<std::result::Result<Arc<Tables>, Error>> = OnceLock::new();
    TABLES.get_or_init(|| load_tables().map(Arc::new)).clone()
}

/// The entry of `parent` with this name in any letter case, if there is
/// exactly one.
fn child(parent: &Path, name: &str) -> Option<PathBuf> {
    let mut matches = Vec::new();
    for entry in std::fs::read_dir(parent).ok()? {
        let entry = entry.ok()?;
        if crate::py::casefold(&entry.file_name().to_string_lossy()) == name {
            matches.push(entry.path());
        }
    }
    if matches.len() == 1 {
        matches.pop()
    } else {
        None
    }
}

fn installed_path(game_root: &Path) -> Option<PathBuf> {
    let mut path = game_root.to_path_buf();
    for name in INSTALLED {
        path = child(&path, name)?;
    }
    path.is_file().then_some(path)
}

/// `re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)`.
fn without_block_comments(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find("/*") {
        let Some(length) = rest[start + 2..].find("*/") else { break };
        result.push_str(&rest[..start]);
        rest = &rest[start + 2 + length + 2..];
    }
    result.push_str(rest);
    result
}

/// A word character of a regular expression (`\w`), for text decoded as
/// Latin-1: letters and digits as Python's `str.isalnum()` has them, and `_`.
fn is_latin1_word(c: char) -> bool {
    c.is_ascii_alphanumeric()
        || c == '_'
        || matches!(c as u32, 0xAA | 0xB2 | 0xB3 | 0xB5 | 0xB9 | 0xBA | 0xBC..=0xBE | 0xC0..=0xD6 | 0xD8..=0xF6 | 0xF8..=0xFF)
}

fn skip_blanks(text: &[u8], mut at: usize) -> usize {
    while at < text.len() && (text[at] == b' ' || text[at] == b'\t') {
        at += 1;
    }
    at
}

/// The type of an array header at the line start `at`, and where its body
/// starts:
/// `[ \t]*ACTION_DEFINE_ASSOCIATIVE_ARRAY[ \t]+remapped_([A-Za-z0-9]+)[ \t]+BEGIN[ \t]*\r?\n`.
fn array_head(text: &str, at: usize) -> Option<(&str, usize)> {
    const HEAD: &str = "ACTION_DEFINE_ASSOCIATIVE_ARRAY";
    let bytes = text.as_bytes();
    let at = skip_blanks(bytes, at);
    if !text[at..].starts_with(HEAD) {
        return None;
    }
    let after = at + HEAD.len();
    let blanks = skip_blanks(bytes, after);
    if blanks == after || !text[blanks..].starts_with("remapped_") {
        return None;
    }
    let kind_start = blanks + "remapped_".len();
    let mut kind_end = kind_start;
    while kind_end < bytes.len() && bytes[kind_end].is_ascii_alphanumeric() {
        kind_end += 1;
    }
    let blanks = skip_blanks(bytes, kind_end);
    if kind_end == kind_start || blanks == kind_end || !text[blanks..].starts_with("BEGIN") {
        return None;
    }
    let mut at = skip_blanks(bytes, blanks + "BEGIN".len());
    if bytes.get(at) == Some(&b'\r') {
        at += 1;
    }
    if bytes.get(at) != Some(&b'\n') {
        return None;
    }
    Some((&text[kind_start..kind_end], at + 1))
}

/// Where `[ \t]*END\b` ends, matched at the line start `at`.
fn array_end(text: &str, at: usize) -> Option<usize> {
    let at = skip_blanks(text.as_bytes(), at);
    if !text[at..].starts_with("END") {
        return None;
    }
    let end = at + "END".len();
    match text[end..].chars().next() {
        Some(next) if is_latin1_word(next) => None,
        _ => Some(end),
    }
}

/// `_INSTALLED_ARRAY.finditer(text)`: the type and body of each
/// `ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_<type> BEGIN` ... `END` array,
/// in file order; `^` is the text's start or a position after `\n`
/// (MULTILINE), and a body ends at the first line that starts with `END`.
fn installed_arrays(text: &str) -> Vec<(&str, &str)> {
    let line_starts: Vec<usize> =
        std::iter::once(0).chain(text.bytes().enumerate().filter(|(_, byte)| *byte == b'\n').map(|(at, _)| at + 1)).collect();
    let mut result = Vec::new();
    let mut resume = 0;
    let mut line = 0;
    while line < line_starts.len() {
        let start = line_starts[line];
        line += 1;
        if start < resume {
            continue;
        }
        let Some((kind, body)) = array_head(text, start) else { continue };
        // The first line start at or after the body's start where END stands.
        let first = line_starts.partition_point(|&candidate| candidate < body);
        let Some((close, end)) =
            line_starts[first..].iter().find_map(|&candidate| array_end(text, candidate).map(|end| (candidate, end)))
        else {
            // No later array can close either.
            break;
        };
        result.push((kind, &text[body..close]));
        resume = end;
    }
    result
}

fn is_quote(c: char) -> bool {
    c == '~' || c == '"'
}

/// `[^\s~"]`.
fn is_name_char(c: char) -> bool {
    !crate::py::is_space(c) && !is_quote(c)
}

/// `_INSTALLED_PAIR.fullmatch(line)`, the two names of
/// `[~"]?([^\s~"]{1,8})[~"]?[ \t]*=>[ \t]*[~"]?([^\s~"]{1,8})[~"]?`.
///
/// Only the first name's length can take several values (it may hold `=`
/// and `>`); the longest that lets the rest match wins, as the regular
/// expression's backtracking chooses.
fn installed_pair(line: &str) -> Option<(&str, &str)> {
    let chars: Vec<(usize, char)> = line.char_indices().collect();
    let offset = |at: usize| chars.get(at).map_or(line.len(), |(offset, _)| *offset);
    let start = usize::from(chars.first().is_some_and(|(_, c)| is_quote(*c)));
    let run = chars[start..].iter().take_while(|(_, c)| is_name_char(*c)).count();
    for length in (1..=run.min(8)).rev() {
        let mut at = start + length;
        if chars.get(at).is_some_and(|(_, c)| is_quote(*c)) {
            at += 1;
        }
        while chars.get(at).is_some_and(|(_, c)| *c == ' ' || *c == '\t') {
            at += 1;
        }
        if chars.get(at).map(|(_, c)| *c) != Some('=') || chars.get(at + 1).map(|(_, c)| *c) != Some('>') {
            continue;
        }
        at += 2;
        while chars.get(at).is_some_and(|(_, c)| *c == ' ' || *c == '\t') {
            at += 1;
        }
        if chars.get(at).is_some_and(|(_, c)| is_quote(*c)) {
            at += 1;
        }
        let second = chars[at..].iter().take_while(|(_, c)| is_name_char(*c)).count();
        let end = at + second;
        let closed = end == chars.len() || (end + 1 == chars.len() && is_quote(chars[end].1));
        if (1..=8).contains(&second) && closed {
            return Some((&line[offset(start)..offset(start + length)], &line[offset(at)..offset(end)]));
        }
    }
    None
}

/// EET's own lists (`remapped_<type>` arrays) in a pcu_dict.tph's text, or
/// why they cannot be used.
fn installed_tables(text: &str) -> std::result::Result<Tables, String> {
    let text = without_block_comments(text);
    let mut tables = Tables::default();
    for (kind, body) in installed_arrays(&text) {
        let kind = kind.to_ascii_lowercase();
        if !TABLE_EXTENSIONS.contains(&kind.as_str()) {
            continue;
        }
        let entries = tables.entry(&kind);
        for raw_line in crate::py::splitlines(body) {
            let line = crate::py::strip(raw_line.split("//").next().unwrap_or(""));
            if line.is_empty() {
                continue;
            }
            let Some((old, new)) = installed_pair(line) else {
                return Err(format!("unexpected {kind} line {}", py_repr_str(line)));
            };
            // As in WeiDU, a later entry for a name replaces an earlier one.
            entries.set(crate::py::lower(old), crate::py::lower(new));
        }
    }
    let missing: Vec<&str> = REQUIRED_INSTALLED.into_iter().filter(|kind| tables.get(kind).is_none()).collect();
    if !missing.is_empty() {
        return Err(format!("no {} list", missing.join(" or ")));
    }
    Ok(Tables(
        TABLE_EXTENSIONS.iter().map(|kind| (kind.to_string(), tables.get(kind).cloned().unwrap_or_default())).collect(),
    ))
}

/// A file's modification time in nanoseconds since 1970, for the cache key.
fn modified_ns(status: &std::fs::Metadata) -> i128 {
    match status.modified().map(|time| time.duration_since(std::time::UNIX_EPOCH)) {
        Ok(Ok(after)) => after.as_nanos() as i128,
        Ok(Err(before)) => -(before.duration().as_nanos() as i128),
        Err(_) => 0,
    }
}

type InstalledKey = (PathBuf, u64, i128);

/// The lists of the installed file `path`, read again when its size or
/// modification time changed; the reason as text when they cannot be read.
fn read_installed(path: &Path) -> std::result::Result<Arc<Tables>, String> {
    static CACHE: Mutex<Vec<(InstalledKey, Arc<Tables>)>> = Mutex::new(Vec::new());
    // The path as Python's str() of it shows it in an OSError.
    let shown = PathBuf::from(crate::paths::display(path));
    let status = std::fs::metadata(path).map_err(|error| Error::win(&error, &shown).message)?;
    let key = (path.to_path_buf(), status.len(), modified_ns(&status));
    {
        let mut cache = CACHE.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(at) = cache.iter().position(|(cached, _)| *cached == key) {
            let hit = cache.remove(at);
            let tables = hit.1.clone();
            cache.insert(0, hit);
            return Ok(tables);
        }
    }
    let data = std::fs::read(path).map_err(|error| Error::os(&error, &shown).message)?;
    // Names are ASCII; comments may use any code page.
    let tables = Arc::new(installed_tables(&crate::py::latin1(&data))?);
    let mut cache = CACHE.lock().unwrap_or_else(PoisonError::into_inner);
    cache.insert(0, (key, tables.clone()));
    cache.truncate(CACHED_FILES);
    Ok(tables)
}

/// The renames this EET game made, from its own lists when they can be
/// read.
pub fn eet_names(game_root: &Path) -> Result<EetNames> {
    const COPY: &str = "so Chriz Item Randomiser uses its own older copy; some BG1 and Siege of Dragonspear rows \
                        may not be found and are left out";
    let Some(path) = installed_path(game_root) else {
        return Ok(EetNames {
            tables: eet_tables()?,
            installed: false,
            notes: vec![format!("EET's rename lists (EET/lib/pcu_dict.tph) are not in this game, {COPY}")],
        });
    };
    match read_installed(&path) {
        Ok(tables) => Ok(EetNames { tables, installed: true, notes: Vec::new() }),
        Err(reason) => Ok(EetNames {
            tables: eet_tables()?,
            installed: false,
            notes: vec![format!("EET's rename lists (EET/lib/pcu_dict.tph) cannot be read ({reason}), {COPY}")],
        }),
    }
}

/// The effective resref for a BG1, SoD or BG2 table row on a supported game.
///
/// EET changes BG1 `ar[digits][n]` ARE/BCS names to `bg...` and renames the
/// BG:EE resources it lists, Siege of Dragonspear's among them (`names`, from
/// [`eet_names`]; without it, CIR's own copy of the lists). BG2 and vanilla
/// EE names stay as they are. BGT and Tutu are outside this adapter.
pub fn convert_resource(
    resref: &str,
    extension: &str,
    campaign: &str,
    game: &str,
    names: Option<&EetNames>,
) -> Result<String> {
    let game_lower = crate::py::lower(game);
    if !["eet", "bgee", "bg2ee"].contains(&game_lower.as_str()) {
        return Err(Error::profile(format!("Unsupported game for resource conversion: {game}")));
    }
    let campaign_lower = crate::py::lower(campaign);
    if !["bg1", "sod", "bg2"].contains(&campaign_lower.as_str()) {
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
    let copy;
    let tables = match names {
        Some(names) => &names.tables,
        None => {
            copy = eet_tables()?;
            &copy
        }
    };
    let renames = tables.get(&kind).ok_or_else(|| Error::key(&kind))?;
    Ok(renames.get(&name).map_or(name.clone(), str::to_string))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bg1_names_follow_eet() {
        assert_eq!(convert_resource("AR2600", "are", "bg1", "eet", None).unwrap(), "bg2600");
        assert_eq!(convert_resource("ar0602", "bcs", "bg2", "eet", None).unwrap(), "ar0602");
        assert_eq!(convert_resource("ar2600", "are", "bg1", "bgee", None).unwrap(), "ar2600");
        assert_eq!(convert_resource("ar0801", "bcs", "sod", "eet", None).unwrap(), "bg0801");
        assert!(eet_tables().unwrap().0.iter().all(|(_, renames)| !renames.is_empty()));
        assert!(convert_resource("a b", "itm", "bg1", "eet", None).is_err());
        assert!(convert_resource("ar0801", "are", "soa", "eet", None).is_err());
    }

    #[test]
    fn installed_lists_as_the_regular_expressions_read_them() {
        let text = "/* old\nACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\t\"OLD\" => \"WRONG\"\nEND\n*/\n\
                    ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_mus BEGIN\n\t1\t=>\t107\t//CDay1\nEND\n\
                    \x20 ACTION_DEFINE_ASSOCIATIVE_ARRAY\tremapped_ITM BEGIN \r\n\t\"SLNG03\"\t=>\t\"SLNG03_\"\t// x\r\n\
                    \tHELM15 => ~HELM15_~\r\n\tslng03 => b\r\n  END // itm\n\
                    ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nENDX => bgendx\nEND\n";
        let tables = installed_tables(text).unwrap();
        assert_eq!(tables.kinds().collect::<Vec<_>>(), TABLE_EXTENSIONS);
        let itm: Vec<_> = tables.get("itm").unwrap().iter().collect();
        assert_eq!(itm, [("slng03", "b"), ("helm15", "helm15_")]);
        // "ENDX" does not close the array: it is one of its lines.
        assert_eq!(tables.get("cre").unwrap().get("endx"), Some("bgendx"));
        assert_eq!(installed_tables(&text.replace("ENDX => bgendx", "ENDX")), Err("unexpected cre line 'ENDX'".to_string()));
        assert_eq!(installed_pair("a==>b"), Some(("a=", "b")));
        assert_eq!(installed_pair("\"a\" => b\""), Some(("a", "b")));
        assert_eq!(installed_pair("SLNG03 SLNG05"), None);
        assert_eq!(installed_pair("abcdefghi => b"), None);
        assert_eq!(installed_tables("ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_sto BEGIN\nEND\n"), Err("no cre or itm list".into()));
        assert_eq!(without_block_comments("a/*/b*/c/*d"), "ac/*d");
    }
}
