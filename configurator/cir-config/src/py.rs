//! Small re-implementations of the Python string, bytes and path behaviour
//! that the reference configurator relies on, so results match exactly.

use crate::unicode_tables::{CASEFOLD, PY_NONPRINTABLE, PY_SPACE};

/// `str.casefold()` with Python 3.11's tables.
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if c.is_ascii() {
            out.push(c.to_ascii_lowercase());
            continue;
        }
        match CASEFOLD.binary_search_by_key(&(c as u32), |row| row.0) {
            Ok(index) => out.push_str(CASEFOLD[index].1),
            Err(_) => out.push(c),
        }
    }
    out
}

/// `str.lower()`.
pub fn lower(text: &str) -> String {
    if text.is_ascii() {
        text.to_ascii_lowercase()
    } else {
        text.to_lowercase()
    }
}

/// `str.upper()`.
pub fn upper(text: &str) -> String {
    if text.is_ascii() {
        text.to_ascii_uppercase()
    } else {
        text.to_uppercase()
    }
}

/// `str.isspace()` for one character.
pub fn is_space(c: char) -> bool {
    if c.is_ascii() {
        return matches!(c, ' ' | '\t' | '\n' | '\x0b' | '\x0c' | '\r' | '\x1c' | '\x1d' | '\x1e' | '\x1f');
    }
    PY_SPACE.binary_search(&(c as u32)).is_ok()
}

/// Whether `repr()` prints the character as is.
pub fn is_printable(c: char) -> bool {
    let code = c as u32;
    let index = PY_NONPRINTABLE.partition_point(|range| range.1 < code);
    !(index < PY_NONPRINTABLE.len() && PY_NONPRINTABLE[index].0 <= code)
}

/// `str.split()` without arguments.
pub fn split_whitespace(text: &str) -> Vec<&str> {
    text.split(is_space).filter(|part| !part.is_empty()).collect()
}

/// `str.strip()` without arguments.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `str.splitlines()` (without keepends).
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, c)) = chars.next() {
        let boundary = matches!(
            c,
            '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}'
        );
        if !boundary {
            continue;
        }
        lines.push(&text[start..index]);
        let mut end = index + c.len_utf8();
        if c == '\r' {
            if let Some(&(next, '\n')) = chars.peek() {
                chars.next();
                end = next + 1;
            }
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `bytes.splitlines()`: only `\n`, `\r` and `\r\n` end a line.
pub fn splitlines_bytes(data: &[u8]) -> Vec<&[u8]> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < data.len() {
        match data[index] {
            b'\n' => {
                lines.push(&data[start..index]);
                index += 1;
                start = index;
            }
            b'\r' => {
                lines.push(&data[start..index]);
                index += if data.get(index + 1) == Some(&b'\n') { 2 } else { 1 };
                start = index;
            }
            _ => index += 1,
        }
    }
    if start < data.len() {
        lines.push(&data[start..]);
    }
    lines
}

/// `bytes.strip()` without arguments (ASCII whitespace).
pub fn strip_bytes(data: &[u8]) -> &[u8] {
    let is_ws = |b: &u8| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | b'\x0b' | b'\x0c');
    let start = data.iter().position(|b| !is_ws(b)).unwrap_or(data.len());
    let end = data.iter().rposition(|b| !is_ws(b)).map_or(start, |end| end + 1);
    &data[start..end.max(start)]
}

/// `str.isdigit()` for the ASCII data the reference reads.
pub fn is_ascii_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())
}

/// `str.isdigit()` (Unicode digits included); tables in the mod are ASCII.
pub fn is_digit_str(text: &str) -> bool {
    !text.is_empty() && text.chars().all(|c| c.is_ascii_digit() || (!c.is_ascii() && c.is_numeric() && unicode_digit(c)))
}

fn unicode_digit(c: char) -> bool {
    // Characters with Numeric_Type Digit or Decimal; approximated by the
    // decimal-digit blocks and the common superscripts used in text.
    c.to_digit(10).is_some() || matches!(c, '\u{b2}' | '\u{b3}' | '\u{b9}' | '\u{2070}' | '\u{2074}'..='\u{2079}' | '\u{2080}'..='\u{2089}')
}

/// Find `needle` in `haystack` starting at `from`.
pub fn find_bytes(haystack: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if needle.is_empty() {
        return if from <= haystack.len() { Some(from) } else { None };
    }
    if from >= haystack.len() || needle.len() > haystack.len() - from {
        return None;
    }
    let first = needle[0];
    let last_start = haystack.len() - needle.len();
    let mut index = from;
    while index <= last_start {
        match haystack[index..=last_start].iter().position(|&b| b == first) {
            None => return None,
            Some(offset) => {
                index += offset;
                if &haystack[index..index + needle.len()] == needle {
                    return Some(index);
                }
                index += 1;
            }
        }
    }
    None
}

/// `bytes.rfind(needle, start, end)`.
pub fn rfind_bytes(haystack: &[u8], needle: &[u8], start: usize, end: usize) -> Option<usize> {
    let end = end.min(haystack.len());
    if needle.len() > end || start > end {
        return None;
    }
    let mut index = end - needle.len();
    loop {
        if index < start {
            return None;
        }
        if &haystack[index..index + needle.len()] == needle {
            return Some(index);
        }
        if index == 0 {
            return None;
        }
        index -= 1;
    }
}

/// `bytes.count(needle)` (non-overlapping).
pub fn count_bytes(haystack: &[u8], needle: &[u8]) -> usize {
    if needle.is_empty() {
        return haystack.len() + 1;
    }
    let mut count = 0;
    let mut index = 0;
    while let Some(found) = find_bytes(haystack, needle, index) {
        count += 1;
        index = found + needle.len();
    }
    count
}

/// `bytes.replace(old, new, count)`; `count` None replaces every occurrence.
pub fn replace_bytes(haystack: &[u8], old: &[u8], new: &[u8], count: Option<usize>) -> Vec<u8> {
    let mut out = Vec::with_capacity(haystack.len());
    let mut index = 0;
    let mut replaced = 0;
    if old.is_empty() {
        // Python inserts `new` between every byte; the reference never does this.
        let limit = count.unwrap_or(usize::MAX);
        for (position, &byte) in haystack.iter().enumerate() {
            if replaced < limit {
                out.extend_from_slice(new);
                replaced += 1;
            }
            out.push(byte);
            let _ = position;
        }
        if replaced < limit {
            out.extend_from_slice(new);
        }
        return out;
    }
    while count.map_or(true, |limit| replaced < limit) {
        match find_bytes(haystack, old, index) {
            Some(found) => {
                out.extend_from_slice(&haystack[index..found]);
                out.extend_from_slice(new);
                index = found + old.len();
                replaced += 1;
            }
            None => break,
        }
    }
    out.extend_from_slice(&haystack[index..]);
    out
}

/// `bytes.split(separator)`.
pub fn split_bytes<'a>(data: &'a [u8], separator: &[u8]) -> Vec<&'a [u8]> {
    let mut parts = Vec::new();
    let mut index = 0;
    while let Some(found) = find_bytes(data, separator, index) {
        parts.push(&data[index..found]);
        index = found + separator.len();
    }
    parts.push(&data[index..]);
    parts
}

/// `bytes.split(separator, maxsplit)`.
pub fn splitn_bytes<'a>(data: &'a [u8], separator: &[u8], maxsplit: usize) -> Vec<&'a [u8]> {
    let mut parts = Vec::new();
    let mut index = 0;
    while parts.len() < maxsplit {
        match find_bytes(data, separator, index) {
            Some(found) => {
                parts.push(&data[index..found]);
                index = found + separator.len();
            }
            None => break,
        }
    }
    parts.push(&data[index..]);
    parts
}

pub fn concat(parts: &[&[u8]]) -> Vec<u8> {
    let mut out = Vec::with_capacity(parts.iter().map(|part| part.len()).sum());
    for part in parts {
        out.extend_from_slice(part);
    }
    out
}

pub fn u16_le(data: &[u8], offset: usize) -> u32 {
    u16::from_le_bytes([data[offset], data[offset + 1]]) as u32
}

pub fn u32_le(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([data[offset], data[offset + 1], data[offset + 2], data[offset + 3]])
}

pub fn put_u16(data: &mut [u8], offset: usize, value: u32) {
    data[offset..offset + 2].copy_from_slice(&(value as u16).to_le_bytes());
}

pub fn put_u32(data: &mut [u8], offset: usize, value: u32) {
    data[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

/// Lowercase hex of bytes (`bytes.hex()`).
pub fn hex(data: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(data.len() * 2);
    for &byte in data {
        out.push(DIGITS[(byte >> 4) as usize] as char);
        out.push(DIGITS[(byte & 15) as usize] as char);
    }
    out
}

/// `bytes.fromhex()` for lowercase/uppercase pairs without spaces.
pub fn from_hex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 {
        return None;
    }
    let digit = |c: u8| match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    };
    text.as_bytes().chunks(2).map(|pair| Some(digit(pair[0])? << 4 | digit(pair[1])?)).collect()
}

/// Latin-1 decoding (every byte is one character).
pub fn latin1(data: &[u8]) -> String {
    data.iter().map(|&b| b as char).collect()
}

/// Python's `int(text)` for plain decimal digits (with optional sign).
pub fn parse_int(text: &str) -> Option<i64> {
    let trimmed = strip(text);
    let (negative, digits) = match trimmed.as_bytes().first() {
        Some(b'-') => (true, &trimmed[1..]),
        Some(b'+') => (false, &trimmed[1..]),
        _ => (false, trimmed),
    };
    let digits: String = digits.chars().filter(|&c| c != '_').collect();
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let value: i64 = digits.parse().ok()?;
    Some(if negative { -value } else { value })
}
