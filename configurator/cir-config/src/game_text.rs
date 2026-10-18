//! Read DLG tables and append game text, in memory (the reference's
//! `game_text.py`). Shared by the Kangaxx, extended-pool, container and
//! cursed-disguise adapters; the caller owns writing the returned bytes.
//!
//! A language's game text is its dialog.tlk and, in most languages other than
//! English, dialogf.tlk, which the engine reads instead for a female
//! protagonist. Both hold a text under the same number (WeiDU appends to
//! both), so an appended text gets one number in both files, with its female
//! wording where it has one.

use crate::error::{Error, ErrorKind, Result};
use crate::py::{put_u32, u16_le, u32_le};
use crate::resources::Tlk;
use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::path::{Path, PathBuf};

/// `count` records of `stride` bytes at `offset` (a DLG table).
pub fn table(data: &[u8], offset: u64, count: u64, stride: u64) -> Result<Vec<&[u8]>> {
    if offset < 0x30 || offset + count * stride > data.len() as u64 {
        return Err(Error::game_text("Unsupported dialogue table layout"));
    }
    Ok((0..count).map(|index| &data[(offset + stride * index) as usize..(offset + stride * (index + 1)) as usize]).collect())
}

/// The bytes a DLG (offset, length) pair names.
pub fn string<'a>(data: &'a [u8], pair: &[u8]) -> Result<&'a [u8]> {
    let offset = u32_le(pair, 0) as u64;
    let length = u32_le(pair, 4) as u64;
    if offset < 0x30 || offset + length > data.len() as u64 {
        return Err(Error::game_text("Unsupported dialogue string layout"));
    }
    Ok(&data[offset as usize..(offset + length) as usize])
}

/// Flags, sound, volume and pitch of an appended entry: text only.
const TEXT_ONLY: [u8; 0x12] = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

/// The entry count and string data offset of a TLK, as a GameTextError.
fn layout(data: &[u8]) -> Result<(u32, u32)> {
    Tlk::layout(data).map_err(|error| Error::game_text(error.message))
}

/// The text of every text-only entry (no sound), by number.
fn plain(data: &[u8], (count, offset): (u32, u32)) -> BTreeMap<u32, &[u8]> {
    let mut result = BTreeMap::new();
    for index in 0..count {
        let row = 0x12 + index as usize * 0x1A;
        let sound = &data[row + 2..row + 10];
        if u16_le(data, row) == 1 && sound.iter().all(|&byte| byte == 0) && u32_le(data, row + 10) == 0 && u32_le(data, row + 14) == 0 {
            let start = offset as usize + u32_le(data, row + 18) as usize;
            result.insert(index, &data[start..start + u32_le(data, row + 22) as usize]);
        }
    }
    result
}

/// `data` with entries appended, each (flags, sound, volume and pitch, text).
fn extended(data: &[u8], (count, offset): (u32, u32), rows: &[(&[u8], &[u8])]) -> Vec<u8> {
    if rows.is_empty() {
        return data.to_vec();
    }
    let table_end = 0x12 + count as usize * 0x1A;
    let mut entries: Vec<u8> = Vec::with_capacity(rows.len() * 0x1A);
    let mut strings: Vec<u8> = Vec::new();
    for (head, raw) in rows {
        entries.extend_from_slice(head);
        entries.extend_from_slice(&((data.len() - offset as usize + strings.len()) as u32).to_le_bytes());
        entries.extend_from_slice(&(raw.len() as u32).to_le_bytes());
        strings.extend_from_slice(raw);
    }
    let mut out = Vec::with_capacity(data.len() + entries.len() + strings.len());
    out.extend_from_slice(&data[..table_end]);
    out.extend_from_slice(&entries);
    out.extend_from_slice(&data[table_end..]);
    out.extend_from_slice(&strings);
    put_u32(&mut out, 0xA, count + rows.len() as u32);
    put_u32(&mut out, 0xE, offset + entries.len() as u32);
    out
}

/// A language's dialog.tlk and its dialogf.tlk, if it has one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GameText {
    pub male: Vec<u8>,
    pub female: Option<Vec<u8>>,
}

/// (dialog.tlk, dialogf.tlk or `None`, number of each text) after an append.
type Appended = (Vec<u8>, Option<Vec<u8>>, Vec<u32>);

fn append_to(male_data: &[u8], female_data: Option<&[u8]>, texts: &[(&str, &str)]) -> Result<Appended> {
    let male = layout(male_data)?;
    let female = female_data.map(layout).transpose()?;
    if female.is_some_and(|female| female.0 != male.0) {
        return Err(Error::game_text("dialog.tlk and dialogf.tlk hold different numbers of strings"));
    }
    let female_texts = female_data.zip(female).map(|(data, layout)| plain(data, layout));
    let mut existing: HashMap<(&[u8], Option<&[u8]>), u32> = HashMap::new();
    for (index, raw) in plain(male_data, male) {
        let wording = female_texts.as_ref().and_then(|texts| texts.get(&index).copied());
        existing.entry((raw, wording)).or_insert(index);
    }
    let (mut male_rows, mut female_rows, mut refs) = (Vec::new(), Vec::new(), Vec::with_capacity(texts.len()));
    for (male_text, female_text) in texts {
        let key = (male_text.as_bytes(), female.map(|_| female_text.as_bytes()));
        let next = male.0 + male_rows.len() as u32;
        let number = *existing.entry(key).or_insert_with(|| {
            male_rows.push((&TEXT_ONLY[..], key.0));
            female_rows.push((&TEXT_ONLY[..], key.1.unwrap_or_default()));
            next
        });
        refs.push(number);
    }
    let female_out = female_data.zip(female).map(|(data, layout)| extended(data, layout, &female_rows));
    Ok((extended(male_data, male, &male_rows), female_out, refs))
}

impl GameText {
    /// The game text of a language without dialogf.tlk.
    pub fn new(male: Vec<u8>) -> GameText {
        GameText { male, female: None }
    }

    /// Append (male, female) texts as text-only entries, preserving all
    /// existing entries and data. Returns the game text and each text's
    /// number.
    ///
    /// An existing text-only entry is used again where it reads the same in
    /// both files; without dialogf.tlk the female wording is not used.
    pub fn append<M: AsRef<str>, F: AsRef<str>>(&self, texts: &[(M, F)]) -> Result<(GameText, Vec<u32>)> {
        let texts: Vec<(&str, &str)> = texts.iter().map(|(male, female)| (male.as_ref(), female.as_ref())).collect();
        let (male, female, refs) = append_to(&self.male, self.female.as_deref(), &texts)?;
        Ok((GameText { male, female }, refs))
    }
}

/// dialog.tlk and dialogf.tlk, with the entries dialog.tlk holds beyond
/// dialogf.tlk's end copied into it (flags, sound, volume, pitch and text).
///
/// For texts appended to dialog.tlk alone (they have no female wording) and
/// numbered after the strings both files share.
pub fn paired(male: Vec<u8>, female: Option<Vec<u8>>) -> Result<GameText> {
    let Some(female) = female else {
        return Ok(GameText::new(male));
    };
    let (source, target) = (layout(&male)?, layout(&female)?);
    if source.0 < target.0 {
        return Err(Error::game_text("dialogf.tlk holds strings beyond dialog.tlk's end"));
    }
    let rows: Vec<(&[u8], &[u8])> = (target.0..source.0)
        .map(|index| {
            let row = 0x12 + index as usize * 0x1A;
            let start = source.1 as usize + u32_le(&male, row + 0x12) as usize;
            (&male[row..row + 0x12], &male[start..start + u32_le(&male, row + 0x16) as usize])
        })
        .collect();
    let female = extended(&female, target, &rows);
    Ok(GameText { male, female: Some(female) })
}

/// The game's dialog.tlk for the scan's `language`, and the dialogf.tlk next
/// to it if it has one (its name as found, in any case).
pub fn text_files(root: &Path, language: &str) -> Result<(PathBuf, Option<PathBuf>)> {
    let folder = if language == "default" { root.to_path_buf() } else { root.join("lang").join(language) };
    let mut female = None;
    for entry in std::fs::read_dir(&folder).map_err(|error| Error::win(&error, &folder))? {
        let name = entry.map_err(|error| Error::win(&error, &folder))?.file_name();
        if crate::py::casefold(&name.to_string_lossy()) == "dialogf.tlk" {
            female = Some(folder.join(name));
            break;
        }
    }
    Ok((folder.join("dialog.tlk"), female))
}

fn file_name(path: &Path) -> String {
    path.file_name().map_or_else(String::new, |name| name.to_string_lossy().into_owned())
}

/// The string count in a TLK file's header.
fn count(path: &Path) -> Result<u32> {
    let mut head = Vec::with_capacity(0x12);
    let file = std::fs::File::open(path).map_err(|error| Error::os(&error, path))?;
    file.take(0x12).read_to_end(&mut head).map_err(|error| Error::os(&error, path))?;
    if head.len() < 0x12 || &head[..8] != b"TLK V1  " {
        return Err(Error::game_text(format!("the game's {} is not a TLK V1 file", file_name(path))));
    }
    Ok(u32_le(&head, 0xA))
}

/// Why a text appended to dialog.tlk `male` cannot get the same number in
/// dialogf.tlk `female`; `None` when it can, or when the game has no
/// dialogf.tlk.
pub fn unpaired(male: &Path, female: Option<&Path>) -> Result<Option<String>> {
    let Some(female) = female else {
        return Ok(None);
    };
    let counts = match count(male).and_then(|male| Ok((male, count(female)?))) {
        Ok(counts) => counts,
        Err(error) if error.kind == ErrorKind::GameText => return Ok(Some(error.message)),
        Err(error) => return Err(error),
    };
    if counts.0 == counts.1 {
        return Ok(None);
    }
    Ok(Some(format!(
        "the game's {} and {} hold different numbers of strings ({} and {}), so a new text cannot get the same \
         number in both",
        file_name(female),
        file_name(male),
        counts.1,
        counts.0
    )))
}

/// Append text-only entries to dialog.tlk alone, keeping every existing TLK
/// entry and byte. Returns the new TLK and the strref of each text (reusing
/// identical existing text-only entries).
pub fn append_texts(data: &[u8], texts: &[String]) -> Result<(Vec<u8>, Vec<u32>)> {
    let pairs: Vec<(&str, &str)> = texts.iter().map(|text| (text.as_str(), text.as_str())).collect();
    let (male, _, refs) = append_to(data, None, &pairs)?;
    Ok((male, refs))
}
