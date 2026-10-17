//! Campaign-gated original Kangaxx quest alterations (the reference's
//! `quest_options.py`).
//!
//! Only the reviewed compiled resources are accepted. Old dialogue transitions
//! remain under the inverse campaign flag; new routes follow Item Randomiser's
//! d/hlskull.d exactly. All functions operate on bytes supplied by the caller.
//! CIR makes the Flail of Ages change itself when it is installed
//! (lib/campaign_patches.tpa).
//!
//! The reviewed hashes are read through [`crate::pins`] as
//! `quest_options.ORIGINAL_HASHES`.

use crate::error::{Error, Result};
use crate::game_text::{append_texts, string, table};
use crate::py::{
    ascii_bytes, count_bytes, digits_value, find_bytes, is_line_end, is_line_start, put_u32, replace_bytes, slice,
    splitn_bytes, u32_le,
};
use crate::quest_rewards::{blocks, global_guard, newline};
use crate::resources::sha256_hex;
use std::collections::{BTreeMap, HashMap};

pub const KANGAXX_GLOBAL: &str = "FLPR_KANGAXX";
pub const ORIGINAL_HASHES: &[(&str, &str)] = &[
    ("hlskull.dlg", "c7ea5cd17d5cffe9368bd980200a05fee7e78eacadf3a6cb41de882df62f56bd"),
    ("crypt526.bcs", "9230a20a9177fe1eef1723fba0e8daa07e949663682a3bddf18e585c0482efe6"),
    ("crypt202.bcs", "ac8fd01b37e902f4484f0cf136def8411be5682aa396ebaabcb1c3723f748aaf"),
];
const TRAILER: &[u8] = b"FLPROPT1";

/// `ORIGINAL_HASHES[name]` with any test overrides.
pub fn original_hash(name: &str) -> Result<String> {
    crate::pins::get("quest_options.ORIGINAL_HASHES", ORIGINAL_HASHES, name).ok_or_else(|| Error::key(name))
}

fn check(data: &[u8], expected: &str, label: &str) -> Result<()> {
    if sha256_hex(data) != expected {
        return Err(Error::profile(format!("{label} changed; review the original option adapter")));
    }
    Ok(())
}

/// The numbered texts of a mod TRA file (`_texts`).
///
/// Matches original delimiters, including multiline text and duplicate @1502
/// (WeiDU uses the last definition), and retains authored CRLFs. Hand-coded
/// `re.finditer(r'(?m)^@(\d+)\s*=\s*([~"])(.*?)\2', source, re.DOTALL)`.
/// `\s` is Python's Unicode whitespace; `\d` is read as ASCII digits, which
/// is exact for the four embedded TRA files this reads (a parity test
/// compares every entry with the reference).
pub fn texts(path: &str) -> HashMap<i64, String> {
    let source = crate::data::text(path).unwrap_or_else(|| panic!("embedded mod text {path}"));
    let bytes = source.as_bytes();
    let mut result = HashMap::new();
    let mut position = 0;
    while position < bytes.len() {
        if !(is_line_start(bytes, position) && bytes[position] == b'@') {
            position += 1;
            continue;
        }
        let digits_start = position + 1;
        let mut cursor = digits_start;
        while cursor < bytes.len() && bytes[cursor].is_ascii_digit() {
            cursor += 1;
        }
        let digits_end = cursor;
        let skip_space = |mut at: usize| {
            while let Some(c) = source[at..].chars().next().filter(|&c| crate::py::is_space(c)) {
                at += c.len_utf8();
            }
            at
        };
        let matched = (|| {
            if digits_end == digits_start {
                return None;
            }
            let at = skip_space(digits_end);
            if bytes.get(at) != Some(&b'=') {
                return None;
            }
            let at = skip_space(at + 1);
            let delimiter = *bytes.get(at).filter(|&&byte| byte == b'~' || byte == b'"')?;
            let text_start = at + 1;
            let text_end = text_start + bytes[text_start..].iter().position(|&byte| byte == delimiter)?;
            Some((text_start, text_end))
        })();
        match matched {
            Some((text_start, text_end)) => {
                result.insert(digits_value(&bytes[digits_start..digits_end]), source[text_start..text_end].to_string());
                position = text_end + 1;
            }
            None => position += 1,
        }
    }
    result
}

fn refs_valid(refs: &BTreeMap<i64, i64>, required: impl IntoIterator<Item = i64>) -> Result<()> {
    for key in required {
        if !refs.get(&key).is_some_and(|value| (0..0xFFFF_FFFF).contains(value)) {
            return Err(Error::profile("Original quest option requires allocated TLK text"));
        }
    }
    Ok(())
}

fn pack_u32(value: i64) -> Result<[u8; 4]> {
    u32::try_from(value).map(u32::to_le_bytes).map_err(|_| Error::value("argument out of range"))
}

fn seal(data: &[u8], original: &[u8], refs: &BTreeMap<i64, i64>) -> Result<Vec<u8>> {
    let mut trailer = Vec::new();
    trailer.extend_from_slice(&(original.len() as u32).to_le_bytes());
    trailer.extend_from_slice(&pad(&original[..0x34.min(original.len())], 52));
    trailer.extend_from_slice(&(refs.len() as u32).to_le_bytes());
    for (&key, &value) in refs {
        trailer.extend_from_slice(&pack_u32(key)?);
        trailer.extend_from_slice(&pack_u32(value)?);
    }
    let mut out = data.to_vec();
    out.extend_from_slice(&trailer);
    out.extend_from_slice(TRAILER);
    out.extend_from_slice(&(trailer.len() as u32).to_le_bytes());
    Ok(out)
}

/// `struct.pack("<Ns", value)`: truncated or NUL-padded to `size` bytes.
pub fn pad(value: &[u8], size: usize) -> Vec<u8> {
    let mut out = value[..value.len().min(size)].to_vec();
    out.resize(size, 0);
    out
}

/// Recover only exact original bytes or a fully verified patch made here.
pub fn original_dialogue(name: &str, data: &[u8]) -> Result<Vec<u8>> {
    if name != "hlskull.dlg" {
        return Err(Error::profile("Unsupported original-option dialogue"));
    }
    let expected = original_hash(name)?;
    if sha256_hex(data) == expected {
        return Ok(data.to_vec());
    }
    if data.len() < 12 || &data[data.len() - 12..data.len() - 4] != TRAILER {
        return Err(Error::profile(format!("{name} changed; review original-option dialogue")));
    }
    let length = u32_le(data, data.len() - 4) as usize;
    if length < 60 || length > data.len() - 12 {
        return Err(Error::profile("Invalid original-option dialogue recovery trailer"));
    }
    let trailer = &data[data.len() - 12 - length..data.len() - 12];
    let size = u32_le(trailer, 0) as usize;
    let header = &trailer[4..56];
    let count = u32_le(trailer, 56) as usize;
    if size < 0x34 || size > data.len() - length - 12 || length != 60 + count * 8 {
        return Err(Error::profile("Invalid original-option dialogue recovery bounds"));
    }
    let mut refs = BTreeMap::new();
    for index in 0..count {
        refs.insert(u32_le(trailer, 60 + index * 8) as i64, u32_le(trailer, 64 + index * 8) as i64);
    }
    if refs.len() != count {
        return Err(Error::profile("Invalid original-option dialogue recovery references"));
    }
    let original = [header, &data[0x34..size]].concat();
    check(&original, &expected, name)?;
    if patch_hlskull_original(&original, &refs, &expected)? != data {
        return Err(Error::profile("Original-option dialogue patch changed"));
    }
    Ok(original)
}

/// An editable DLG V1.0 (the reference's `_Dialogue`): new rows and strings
/// are appended, and rendering keeps every original byte in place.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Dialogue {
    pub data: Vec<u8>,
    pub name: Vec<u8>,
    pub states: Vec<Vec<u8>>,
    pub transitions: Vec<Vec<u8>>,
    pub state_triggers: Vec<Vec<u8>>,
    pub triggers: Vec<Vec<u8>>,
    pub actions: Vec<Vec<u8>>,
}

impl Dialogue {
    pub fn new(data: &[u8], name: &str) -> Result<Dialogue> {
        if data.len() < 0x34 || &data[..8] != b"DLG V1.0" {
            return Err(Error::profile("Invalid original-option DLG header"));
        }
        let name = ascii_bytes(name)?;
        let field = |index: usize| u32_le(data, 8 + index * 4) as u64;
        let (ns, so, nt, to, sto, nst, tto, ntt, ao, na) =
            (field(0), field(1), field(2), field(3), field(4), field(5), field(6), field(7), field(8), field(9));
        let states = table(data, so, ns, 16)?.into_iter().map(<[u8]>::to_vec).collect();
        let transitions = table(data, to, nt, 32)?.into_iter().map(<[u8]>::to_vec).collect();
        let strings = |offset, count| -> Result<Vec<Vec<u8>>> {
            table(data, offset, count, 8)?.into_iter().map(|row| string(data, row).map(<[u8]>::to_vec)).collect()
        };
        let state_triggers = strings(sto, nst)?;
        let triggers = strings(tto, ntt)?;
        let actions = strings(ao, na)?;
        Ok(Dialogue { data: data.to_vec(), name, states, transitions, state_triggers, triggers, actions })
    }

    /// A new transition row; non-empty trigger and action text is appended.
    pub fn transition(
        &mut self,
        target: Option<u32>,
        reply: Option<u32>,
        trigger: &str,
        action: &str,
        journal: Option<u32>,
    ) -> Result<Vec<u8>> {
        let mut flags = (reply.is_some() as u32) | if trigger.is_empty() { 0 } else { 2 } | if action.is_empty() { 0 } else { 4 };
        flags |= if target.is_none() { 8 } else { 0 };
        flags |= if journal.is_some() { 0x50 } else { 0 };
        let (mut trigger_id, mut action_id) = (0u32, 0u32);
        if !trigger.is_empty() {
            trigger_id = self.triggers.len() as u32;
            self.triggers.push(ascii_bytes(&format!("{trigger}\r\n"))?);
        }
        if !action.is_empty() {
            action_id = self.actions.len() as u32;
            self.actions.push(ascii_bytes(&format!("{action}\r\n"))?);
        }
        let mut row = Vec::with_capacity(32);
        for value in [flags, reply.unwrap_or(0xFFFF_FFFF), journal.unwrap_or(0), trigger_id, action_id] {
            row.extend_from_slice(&value.to_le_bytes());
        }
        row.extend(pad(if target.is_some() { &self.name } else { b"" }, 8));
        row.extend_from_slice(&target.unwrap_or(0).to_le_bytes());
        Ok(row)
    }

    /// Append a state with `transitions`; returns its index.
    pub fn state(&mut self, text: u32, transitions: Vec<Vec<u8>>) -> usize {
        let index = self.states.len();
        let mut row = Vec::with_capacity(16);
        for value in [text, self.transitions.len() as u32, transitions.len() as u32, 0xFFFF_FFFF] {
            row.extend_from_slice(&value.to_le_bytes());
        }
        self.states.push(row);
        self.transitions.extend(transitions);
        index
    }

    /// Split one transition into an excluded and a selected copy under `flag`.
    pub fn branch(&mut self, state: usize, ordinal: u32, flag: &str, target: Option<u32>, journal: Option<u32>) -> Result<()> {
        let row = self.states.get(state).ok_or_else(|| Error::value("list index out of range"))?.clone();
        let (first, count) = (u32_le(&row, 4) as usize, u32_le(&row, 8) as usize);
        if ordinal as usize >= count || first + count > self.transitions.len() {
            return Err(Error::profile("Original option dialogue transition changed"));
        }
        let original = self.transitions[first + ordinal as usize].clone();
        let flags = u32_le(&original, 0);
        let trigger_id = u32_le(&original, 12) as usize;
        let prior = if flags & 2 != 0 {
            self.triggers.get(trigger_id).ok_or_else(|| Error::value("list index out of range"))?.clone()
        } else {
            Vec::new()
        };
        let mut replacement = Vec::new();
        for selected in [false, true] {
            let mut branch = original.clone();
            let guard = ascii_bytes(&format!("{}Global(\"{flag}\",\"GLOBAL\",1)\r\n", if selected { "" } else { "!" }))?;
            self.triggers.push([prior.as_slice(), &guard].concat());
            put_u32(&mut branch, 0, flags | 2);
            put_u32(&mut branch, 12, self.triggers.len() as u32 - 1);
            if selected {
                if let Some(target) = target {
                    branch[20..28].copy_from_slice(&pad(&self.name, 8));
                    put_u32(&mut branch, 28, target);
                }
                if let Some(journal) = journal {
                    if flags & 0x50 != 0x50 {
                        return Err(Error::profile("Original quest unsolved journal changed"));
                    }
                    put_u32(&mut branch, 8, journal);
                }
            }
            replacement.push(branch);
        }
        let ordinal = ordinal as usize;
        let mut new_rows: Vec<Vec<u8>> = self.transitions[first..first + ordinal].to_vec();
        new_rows.extend(replacement);
        new_rows.extend_from_slice(&self.transitions[first + ordinal + 1..first + count]);
        let row = &mut self.states[state];
        put_u32(row, 4, self.transitions.len() as u32);
        put_u32(row, 8, new_rows.len() as u32);
        self.transitions.extend(new_rows);
        Ok(())
    }

    /// The original bytes followed by the new strings and 4-aligned tables.
    pub fn render(&self) -> Vec<u8> {
        let mut out = self.data.clone();
        let strings = |values: &[Vec<u8>], out: &mut Vec<u8>| -> Vec<Vec<u8>> {
            values
                .iter()
                .map(|value| {
                    let pair = [(out.len() as u32).to_le_bytes(), (value.len() as u32).to_le_bytes()].concat();
                    out.extend_from_slice(value);
                    pair
                })
                .collect()
        };
        let st = strings(&self.state_triggers, &mut out);
        let tt = strings(&self.triggers, &mut out);
        let actions = strings(&self.actions, &mut out);
        let table = |rows: &[Vec<u8>], out: &mut Vec<u8>| -> u32 {
            out.resize(out.len() + (4 - out.len() % 4) % 4, 0);
            let position = out.len() as u32;
            for row in rows {
                out.extend_from_slice(row);
            }
            position
        };
        let so = table(&self.states, &mut out);
        let to = table(&self.transitions, &mut out);
        let sto = table(&st, &mut out);
        let tto = table(&tt, &mut out);
        let ao = table(&actions, &mut out);
        let header = [
            self.states.len() as u32,
            so,
            self.transitions.len() as u32,
            to,
            sto,
            st.len() as u32,
            tto,
            tt.len() as u32,
            ao,
            actions.len() as u32,
        ];
        for (index, value) in header.into_iter().enumerate() {
            put_u32(&mut out, 8 + index * 4, value);
        }
        out
    }
}

fn reference(refs: &BTreeMap<i64, i64>, key: i64) -> u32 {
    refs[&key] as u32
}

fn patch_hlskull_original(data: &[u8], refs: &BTreeMap<i64, i64>, expected: &str) -> Result<Vec<u8>> {
    check(data, expected, "HLSKULL.DLG")?;
    refs_valid(refs, 0..22)?;
    let mut dlg = Dialogue::new(data, "HLSKULL")?;
    if dlg.states.len() != 16 || dlg.transitions.len() != 32 {
        return Err(Error::profile("Original Kangaxx dialogue layout changed"));
    }
    let (branch, keys, keys2, onekey, nokey, body, altexit) = (16, 17, 18, 19, 20, 21, 22);
    let r = |key| reference(refs, key);
    let rows = vec![
        dlg.transition(Some(keys), Some(r(1)), "", "", None)?,
        dlg.transition(Some(body), Some(r(2)), "", "", None)?,
        dlg.transition(Some(6), Some(56194), "", "", None)?,
    ];
    dlg.state(r(0), rows);
    let rows = vec![dlg.transition(Some(keys2), None, "", "", None)?];
    dlg.state(r(3), rows);
    let rows = vec![
        dlg.transition(Some(10), Some(r(5)), "", "", None)?,
        dlg.transition(Some(body), Some(r(6)), "", "", None)?,
        dlg.transition(Some(onekey), Some(r(7)), "PartyHasItem(\"flrward1\")", "", None)?,
        dlg.transition(Some(nokey), Some(r(7)), "!PartyHasItem(\"flrward1\")", "", None)?,
        dlg.transition(Some(6), Some(r(8)), "", "", None)?,
    ];
    dlg.state(r(4), rows);
    let rows = vec![dlg.transition(Some(10), Some(r(10)), "", "", None)?, dlg.transition(Some(6), Some(r(11)), "", "", None)?];
    dlg.state(r(9), rows);
    let rows = vec![
        dlg.transition(Some(altexit), Some(r(13)), "", "", None)?,
        dlg.transition(Some(body), Some(r(14)), "", "", None)?,
        dlg.transition(Some(6), Some(r(15)), "", "", None)?,
    ];
    dlg.state(r(12), rows);
    let rows = vec![
        dlg.transition(Some(10), Some(r(17)), "", "", None)?,
        dlg.transition(Some(keys), Some(r(21)), "", "", None)?,
        dlg.transition(Some(6), Some(r(18)), "", "", None)?,
    ];
    dlg.state(r(16), rows);
    let rows = vec![dlg.transition(None, None, "", "SetGlobal(\"KangaxxQuest\",\"GLOBAL\",1)", Some(r(20)))?];
    dlg.state(r(19), rows);
    for state in [5, 6] {
        dlg.branch(state, 0, KANGAXX_GLOBAL, Some(branch), None)?;
    }
    dlg.branch(10, 0, KANGAXX_GLOBAL, None, Some(r(20)))?;
    seal(&dlg.render(), data, refs)
}

/// Patch HLSKULL.DLG (original or our own verified patch) with `refs[0..22]`.
pub fn patch_hlskull(data: &[u8], refs: &BTreeMap<i64, i64>) -> Result<Vec<u8>> {
    let original = original_dialogue("hlskull.dlg", data)?;
    patch_hlskull_original(&original, refs, &original_hash("hlskull.dlg")?)
}

/// One `re.finditer` match of the createEntry pattern: (start, end,
/// indentation, title, newline).
struct Entry<'a> {
    start: usize,
    end: usize,
    indent: &'a [u8],
    title: &'a [u8],
    newline: &'a [u8],
}

fn skip_blank(data: &[u8], mut at: usize) -> usize {
    while at < data.len() && (data[at] == b' ' || data[at] == b'\t') {
        at += 1;
    }
    at
}

fn literal(data: &[u8], at: usize, text: &[u8]) -> Option<usize> {
    data[at..].starts_with(text).then_some(at + text.len())
}

/// `re.finditer(rb"(?m)^([ \t]*)createEntry[ \t]*\([ \t]*(\d+),[ \t]*-1,[ \t]*"
/// + ref + rb",[ \t]*\{\},[ \t]*nil[ \t]*\)[ \t]*(\r?\n)", lua)`. Every element
/// is followed by a character its repetition cannot match, so the greedy
/// scan below is the regex's only match at each line start.
fn entries(lua: &[u8], reference: u32) -> Vec<Entry<'_>> {
    let reference = reference.to_string();
    let mut result = Vec::new();
    let mut start = 0;
    while start <= lua.len() {
        if !is_line_start(lua, start) {
            start += 1;
            continue;
        }
        let matched = (|| {
            let indent_end = skip_blank(lua, start);
            let at = literal(lua, indent_end, b"createEntry")?;
            let at = literal(lua, skip_blank(lua, at), b"(")?;
            let title_start = skip_blank(lua, at);
            let mut at = title_start;
            while at < lua.len() && lua[at].is_ascii_digit() {
                at += 1;
            }
            if at == title_start {
                return None;
            }
            let title_end = at;
            let at = literal(lua, at, b",")?;
            let at = literal(lua, skip_blank(lua, at), b"-1,")?;
            let at = literal(lua, skip_blank(lua, at), reference.as_bytes())?;
            let at = literal(lua, at, b",")?;
            let at = literal(lua, skip_blank(lua, at), b"{},")?;
            let at = literal(lua, skip_blank(lua, at), b"nil")?;
            let at = literal(lua, skip_blank(lua, at), b")")?;
            let newline_start = skip_blank(lua, at);
            let end = literal(lua, newline_start, b"\r\n").or_else(|| literal(lua, newline_start, b"\n"))?;
            Some(Entry {
                start,
                end,
                indent: &lua[start..indent_end],
                title: &lua[title_start..title_end],
                newline: &lua[newline_start..end],
            })
        })();
        match matched {
            Some(entry) => {
                start = entry.end.max(start + 1);
                result.push(entry);
            }
            None => start += 1,
        }
    }
    result
}

/// `len(re.findall(rb"(?m)^[ \t]*createQuest[ \t]*\([ \t]*" + title +
/// rb"[ \t]*\)", data))`.
fn quest_count(data: &[u8], title: &[u8]) -> usize {
    let mut count = 0;
    let mut start = 0;
    while start <= data.len() {
        if !is_line_start(data, start) {
            start += 1;
            continue;
        }
        let matched = (|| {
            let at = literal(data, skip_blank(data, start), b"createQuest")?;
            let at = literal(data, skip_blank(data, at), b"(")?;
            let at = literal(data, skip_blank(data, at), title)?;
            literal(data, skip_blank(data, at), b")")
        })();
        match matched {
            Some(end) => {
                count += 1;
                start = end.max(start + 1);
            }
            None => start += 1,
        }
    }
    count
}

/// Register the appended journal under the installed Kangaxx quest title.
pub fn patch_kangaxx_journal(lua: &[u8], patched_dialogue: &[u8]) -> Result<Vec<u8>> {
    let original = original_dialogue("hlskull.dlg", patched_dialogue)?;
    if original == patched_dialogue {
        return Ok(lua.to_vec());
    }
    let old = Dialogue::new(&original, "HLSKULL")?;
    let new = Dialogue::new(patched_dialogue, "HLSKULL")?;
    let index_error = || Error::value("list index out of range");
    let old_first = u32_le(old.states.get(10).ok_or_else(index_error)?, 4) as usize;
    let new_first = u32_le(new.states.get(10).ok_or_else(index_error)?, 4) as usize;
    let old_ref = u32_le(old.transitions.get(old_first).ok_or_else(index_error)?, 8);
    let new_ref = u32_le(new.transitions.get(new_first + 1).ok_or_else(index_error)?, 8);
    let matches = entries(lua, old_ref);
    if matches.len() != 1 {
        return Err(Error::profile("Kangaxx journal needs one existing BGEE.LUA entry"));
    }
    let anchor = &matches[0];
    let title = anchor.title;
    if quest_count(&lua[..anchor.start], title) != 1 {
        return Err(Error::profile("Kangaxx journal quest title is missing or ambiguous"));
    }
    let existing = entries(lua, new_ref);
    if !existing.is_empty() {
        if existing.len() != 1 || existing[0].title != title {
            return Err(Error::profile("Kangaxx journal text is registered to another quest"));
        }
        return Ok(lua.to_vec());
    }
    let entry = [
        anchor.indent,
        b"createEntry    ( ",
        title,
        b", -1, ",
        new_ref.to_string().as_bytes(),
        b", {}, nil )",
        anchor.newline,
    ]
    .concat();
    Ok([&lua[..anchor.end], &entry, &lua[anchor.end..]].concat())
}

fn crypt_patch(data: &[u8], area: &str, refs: &BTreeMap<i64, i64>) -> Result<Vec<u8>> {
    refs_valid(refs, [1500, 1501])?;
    let nl = newline(data)?;
    let (blocks, prefix, suffix) = blocks(data, 6, nl)?;
    let first = &blocks[0];
    let zero = [b"0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB" as &[u8], nl].concat();
    let trigger = |value: &[u8]| [b"TR", nl, value, nl, &zero, b"TR", nl].concat();
    // PartyHasItem, trigger.ids 0x4042.
    let ward = trigger(b"16450 0 0 0 0 \"flrward1\" \"\" OB");
    let quest = trigger(b"16399 1 0 0 0 \"GLOBALKangaxxQuest\" \"\" OB");
    let guard = global_guard(KANGAXX_GLOBAL, true, nl)?;
    let excluded_guard = global_guard(KANGAXX_GLOBAL, false, nl)?;
    let boundary = [b"CO", nl].concat();
    if count_bytes(first, &boundary) != 2 {
        return Err(Error::profile("Original crypt block changed"));
    }
    let parts = splitn_bytes(first, &boundary, 2);
    let (head, conditions, response) = (parts[0], parts[1], parts[2]);
    let area = area.as_bytes();
    let used = trigger(&[b"16399 0 0 0 0 \"", area, b"UsedCrypt\" \"\" OB"].concat());
    let advance = [b"1 0 0 0 0\"", area, b"UsedCrypt\" \"\" AC", nl].concat();
    let display = [b"35858 0 0 0 0\"\" \"\" AC" as &[u8], nl].concat();
    if count_bytes(conditions, &used) != 1 || count_bytes(response, &advance) != 1 || count_bytes(response, &display) != 1 {
        return Err(Error::profile("Original crypt gate changed"));
    }
    let mut take = [b"AC", nl, b"116OB", nl, &zero].concat();
    for _ in 0..2 {
        take.extend([b"OB", nl, &zero].concat());
    }
    take.extend([b"0 0 0 0 0\"flrward1\" \"\" AC", nl].concat());
    let display_with = |key: i64| [refs[&key].to_string().as_bytes(), b" 0 0 0 0\"\" \"\" AC", nl].concat();
    let selected_response = replace_bytes(
        &replace_bytes(response, &advance, &[advance.as_slice(), &take].concat(), Some(1)),
        &display,
        &display_with(1501),
        Some(1),
    );
    let selected = [head, &boundary, conditions, &guard, &ward, &quest, &boundary, &selected_response].concat();
    let excluded = [head, &boundary, conditions, &excluded_guard, &boundary, response].concat();
    // The first original block's second action is DisplayStringHead(Myself).
    // Reuse its full object/parameter encoding for the sealed-crypt response.
    let action_start = find_bytes(response, &[b"AC", nl, b"269OB", nl].concat(), 0)
        .ok_or_else(|| Error::value("subsection not found"))?;
    let display_end = find_bytes(response, &display, 0).expect("counted display") + display.len();
    let display_action = replace_bytes(slice(response, action_start, display_end), &display, &display_with(1500), None);
    // The first action follows RESPONSE's weight directly (100AC).
    let blocked_response = [b"RS", nl, b"RE", nl, b"100", &display_action, b"RE", nl, b"RS", nl, b"CR", nl].concat();
    // OR, trigger.ids 0x4089.
    let mut either_missing = trigger(b"16521 2 0 0 0 \"\" \"\" OB");
    either_missing.extend(replace_bytes(&ward, b"16450 0 0 ", b"16450 0 1 ", Some(1)));
    either_missing.extend(replace_bytes(&quest, b"16399 1 0 ", b"16399 1 1 ", Some(1)));
    let blocked = [head, &boundary, conditions, &guard, &either_missing, &boundary, &blocked_response].concat();
    let mut out = [prefix.as_slice(), &blocked, &selected, &excluded].concat();
    for block in &blocks[1..] {
        out.extend_from_slice(block);
    }
    out.extend_from_slice(&suffix);
    Ok(out)
}

/// `re.findall(rb'(?m)^(\d+) 0 0 0 0"" "" AC\r?$', block)`.
fn display_refs(block: &[u8]) -> Vec<i64> {
    const TAIL: &[u8] = b" 0 0 0 0\"\" \"\" AC";
    let mut result = Vec::new();
    let mut start = 0;
    while start <= block.len() {
        if !is_line_start(block, start) {
            start += 1;
            continue;
        }
        let mut at = start;
        while at < block.len() && block[at].is_ascii_digit() {
            at += 1;
        }
        let matched = (at > start)
            .then(|| literal(block, at, TAIL))
            .flatten()
            .and_then(|end| {
                if block.get(end) == Some(&b'\r') && is_line_end(block, end + 1) {
                    Some(end + 1)
                } else {
                    is_line_end(block, end).then_some(end)
                }
            });
        match matched {
            Some(end) => {
                result.push(digits_value(&block[start..at]));
                start = end.max(start + 1);
            }
            None => start += 1,
        }
    }
    result
}

fn crypt_area(name: &str) -> &'static str {
    if name == "crypt526.bcs" {
        "AR0526"
    } else {
        "AR0202"
    }
}

/// Recover the original crypt script from itself or our verified patch.
pub fn original_crypt(name: &str, data: &[u8]) -> Result<Vec<u8>> {
    if name != "crypt526.bcs" && name != "crypt202.bcs" {
        return Err(Error::profile("Unsupported original crypt script"));
    }
    let expected = original_hash(name)?;
    if sha256_hex(data) == expected {
        return Ok(data.to_vec());
    }
    let nl = newline(data)?;
    let (blocks, prefix, suffix) = blocks(data, 8, nl)?;
    let excluded_guard = global_guard(KANGAXX_GLOBAL, false, nl)?;
    if count_bytes(&blocks[2], &excluded_guard) != 1 {
        return Err(Error::profile("Original crypt recovery branch changed"));
    }
    let mut original = [prefix.as_slice(), &replace_bytes(&blocks[2], &excluded_guard, b"", Some(1))].concat();
    for block in &blocks[3..] {
        original.extend_from_slice(block);
    }
    original.extend_from_slice(&suffix);
    check(&original, &expected, name)?;
    let mut refs = BTreeMap::new();
    for (index, key) in [(0, 1500), (1, 1501)] {
        let values = display_refs(&blocks[index]);
        if values.len() != 1 {
            return Err(Error::profile("Original crypt recovery display changed"));
        }
        refs.insert(key, values[0]);
    }
    if crypt_patch(&original, crypt_area(name), &refs)? != data {
        return Err(Error::profile("Original crypt patch changed"));
    }
    Ok(original)
}

/// Patch a crypt script (original or our own verified patch).
pub fn patch_crypt(name: &str, data: &[u8], refs: &BTreeMap<i64, i64>) -> Result<Vec<u8>> {
    let original = original_crypt(name, data)?;
    crypt_patch(&original, crypt_area(name), refs)
}

/// Original copy/ward.itm with component-520 name, descriptions and icon.
pub fn ward_item(refs: &BTreeMap<i64, i64>) -> Result<Vec<u8>> {
    refs_valid(refs, [1502, 1503, 1504])?;
    let mut item = crate::data::file("copy/ward.itm").expect("embedded copy/ward.itm").to_vec();
    for (offset, key) in [(8, 1503), (12, 1503), (0x50, 1504), (0x54, 1502)] {
        put_u32(&mut item, offset, refs[&key] as u32);
    }
    item[0x3A..0x42].copy_from_slice(b"flrward1");
    Ok(item)
}

/// Return selected patched resources and TLK, leaving file writes to caller.
///
/// Input resource keys are lowercase filenames. Kangaxx needs HLSKULL.DLG and
/// CRYPT526/CRYPT202.BCS. Generated ward ITM/BAM are included in Kangaxx
/// output, in the reference's order. The caller sets the campaign flag
/// explicitly.
pub fn prepare_quest_options(
    resources: &BTreeMap<String, Vec<u8>>,
    tlk: &[u8],
    kangaxx: bool,
    language: &str,
) -> Result<(Vec<(String, Vec<u8>)>, Vec<u8>)> {
    if !["en_us", "en_gb", "english"].contains(&crate::py::casefold(language).as_str()) {
        return Err(Error::profile("Original quest options currently require English game text"));
    }
    let mut output = Vec::new();
    let mut tlk = tlk.to_vec();
    let append = |path: &str, keys: Vec<i64>, tlk: &mut Vec<u8>| -> Result<BTreeMap<i64, i64>> {
        let texts = texts(path);
        let values = keys
            .iter()
            .map(|key| texts.get(key).cloned().ok_or_else(|| Error::new(crate::error::ErrorKind::Key, key.to_string())))
            .collect::<Result<Vec<String>>>()?;
        let (updated, refs) = append_texts(tlk, &values)?;
        *tlk = updated;
        Ok(keys.into_iter().zip(refs.into_iter().map(i64::from)).collect())
    };
    let resource = |name: &str| -> Result<&Vec<u8>> {
        resources.get(name).ok_or_else(|| Error::profile(format!("Original quest option requires {name}")))
    };
    if kangaxx {
        let dialogue_refs = append("languages/english/hlskull.tra", (0..22).collect(), &mut tlk)?;
        let item_refs = append("languages/english/in-game.tra", (1500..1505).collect(), &mut tlk)?;
        output.push(("hlskull.dlg".to_string(), patch_hlskull(resource("hlskull.dlg")?, &dialogue_refs)?));
        for name in ["crypt526.bcs", "crypt202.bcs"] {
            output.push((name.to_string(), patch_crypt(name, resource(name)?, &item_refs)?));
        }
        output.push(("flrward1.itm".to_string(), ward_item(&item_refs)?));
        output.push((
            "flrward1.bam".to_string(),
            crate::data::file("copy/flrward1.bam").expect("embedded copy/flrward1.bam").to_vec(),
        ));
    }
    Ok((output, tlk))
}
