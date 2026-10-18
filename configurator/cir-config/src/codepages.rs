//! Strict decoding of the Windows code pages Item Randomiser's translations
//! are written in, as Python's codecs decode them (`bytes.decode(encoding)`).
//!
//! The tables in `codepages/` are taken from Python's own codecs by
//! `configurator/parity/codepages.py`, which also describes their layout. A
//! byte sequence Python refuses is refused here too (`None`), so a translation
//! file is either read as the reference reads it or not at all.

use std::sync::OnceLock;

/// The code pages [`decode`] knows besides `utf-8`, by Python codec name.
pub const CODE_PAGES: &[&str] = &["cp1250", "cp1251", "cp1252", "cp1254", "cp932", "cp936", "cp949"];

static TABLES: [&[u8]; 7] = [
    include_bytes!("../codepages/cp1250.bin"),
    include_bytes!("../codepages/cp1251.bin"),
    include_bytes!("../codepages/cp1252.bin"),
    include_bytes!("../codepages/cp1254.bin"),
    include_bytes!("../codepages/cp932.bin"),
    include_bytes!("../codepages/cp936.bin"),
    include_bytes!("../codepages/cp949.bin"),
];

const ONE: u8 = 0;
const LEAD: u8 = 1;

struct CodePage {
    /// What each first byte is: [`ONE`] character, a [`LEAD`] byte, or never valid.
    classes: [u8; 256],
    singles: [u16; 256],
    /// Per lead byte: its first trail byte and the characters from there on (0: refused).
    pairs: Vec<Option<(u8, Vec<u16>)>>,
}

fn u16_at(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([data[offset], data[offset + 1]])
}

fn parse(data: &[u8]) -> CodePage {
    assert_eq!(&data[..4], b"CPT1", "code page table");
    let mut classes = [0u8; 256];
    classes.copy_from_slice(&data[4..260]);
    let mut singles = [0u16; 256];
    for (byte, single) in singles.iter_mut().enumerate() {
        *single = u16_at(data, 260 + byte * 2);
    }
    let mut pairs = vec![None; 256];
    let mut at = 260 + 512;
    while at < data.len() {
        let (lead, first, count) = (data[at], data[at + 1], u16_at(data, at + 2) as usize);
        at += 4;
        pairs[lead as usize] = Some((first, (0..count).map(|index| u16_at(data, at + index * 2)).collect()));
        at += count * 2;
    }
    CodePage { classes, singles, pairs }
}

fn code_page(index: usize) -> &'static CodePage {
    static PARSED: [OnceLock<CodePage>; 7] =
        [OnceLock::new(), OnceLock::new(), OnceLock::new(), OnceLock::new(), OnceLock::new(), OnceLock::new(), OnceLock::new()];
    PARSED[index].get_or_init(|| parse(TABLES[index]))
}

/// `data.decode(encoding)` with Python's strict errors: the text, or `None`
/// where Python raises UnicodeDecodeError. `encoding` is `utf-8` or one of
/// [`CODE_PAGES`]; any other name panics (the caller's table names them).
pub fn decode(data: &[u8], encoding: &str) -> Option<String> {
    if encoding == "utf-8" {
        // Python's UTF-8 codec refuses what Rust's does (surrogates, overlong
        // forms, beyond U+10FFFF) and keeps a byte order mark as U+FEFF.
        return std::str::from_utf8(data).ok().map(str::to_string);
    }
    let index = CODE_PAGES.iter().position(|name| *name == encoding).unwrap_or_else(|| panic!("code page {encoding}"));
    let table = code_page(index);
    let mut text = String::with_capacity(data.len());
    let mut at = 0;
    while at < data.len() {
        let byte = data[at];
        let value = match table.classes[byte as usize] {
            ONE => {
                at += 1;
                table.singles[byte as usize]
            }
            LEAD => {
                let trail = *data.get(at + 1)?;
                let (first, values) = table.pairs[byte as usize].as_ref()?;
                at += 2;
                let value = *values.get((trail as usize).checked_sub(*first as usize)?)?;
                if value == 0 {
                    return None;
                }
                value
            }
            _ => return None,
        };
        text.push(char::from_u32(value as u32)?);
    }
    Some(text)
}

#[cfg(test)]
mod tests {
    use super::decode;

    #[test]
    fn single_byte_code_pages_refuse_their_undefined_bytes() {
        assert_eq!(decode(b"Zved\xe1\x9a p\xf8edm\xect", "cp1250").as_deref(), Some("Zvedáš předmět"));
        assert_eq!(decode(b"\x81", "cp1250"), None);
        assert_eq!(decode(b"\x80", "cp1252").as_deref(), Some("€"));
        assert_eq!(decode(b"\x81", "cp1252"), None);
        assert_eq!(decode(b"\x98", "cp1251"), None);
    }

    #[test]
    fn double_byte_code_pages_need_both_bytes() {
        assert_eq!(decode(b"\x82\xa0", "cp932").as_deref(), Some("あ"));
        assert_eq!(decode(b"\xb1", "cp932").as_deref(), Some("ｱ"));
        assert_eq!(decode(b"\x81\x20", "cp932"), None);
        assert_eq!(decode(b"\x82", "cp932"), None);
        assert_eq!(decode(b"\xc4\xe3", "cp936").as_deref(), Some("你"));
        assert_eq!(decode(b"\x80", "cp936"), None);
        assert_eq!(decode(b"\xb0\xa1", "cp949").as_deref(), Some("가"));
    }
}
