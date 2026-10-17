//! Read DLG tables and append game text, in memory (the reference's
//! `game_text.py`). Shared by the Kangaxx, extended-pool, container and
//! cursed-disguise adapters; the caller owns writing the returned bytes.

use crate::error::{Error, Result};
use crate::py::{put_u32, u16_le, u32_le};
use crate::resources::Tlk;
use std::collections::HashMap;

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

/// Append text-only entries, keeping every existing TLK entry and byte.
/// Returns the new TLK and the strref of each text (reusing identical
/// existing text-only entries).
pub fn append_texts(data: &[u8], texts: &[String]) -> Result<(Vec<u8>, Vec<u32>)> {
    let tlk = Tlk::new(data.to_vec()).map_err(|error| Error::game_text(error.message))?;
    let table_end = 0x12 + tlk.count as usize * 0x1A;
    let mut entries: Vec<u8> = Vec::new();
    let mut strings: Vec<u8> = Vec::new();
    let mut refs = Vec::with_capacity(texts.len());
    let mut existing: HashMap<Vec<u8>, u32> = HashMap::new();
    for index in 0..tlk.count as usize {
        let row = 0x12 + index * 0x1A;
        let flags = u16_le(data, row);
        let sound = &data[row + 2..row + 10];
        let volume = u32_le(data, row + 10);
        let pitch = u32_le(data, row + 14);
        let offset = u32_le(data, row + 18) as usize;
        let length = u32_le(data, row + 22) as usize;
        if flags == 1 && sound.iter().all(|&byte| byte == 0) && volume == 0 && pitch == 0 {
            let start = tlk.offset as usize + offset;
            existing.entry(data[start..start + length].to_vec()).or_insert(index as u32);
        }
    }
    for text in texts {
        let raw = text.as_bytes().to_vec();
        if !existing.contains_key(&raw) {
            let strref = tlk.count + (entries.len() / 0x1A) as u32;
            existing.insert(raw.clone(), strref);
            let mut entry = vec![0u8; 0x1A];
            entry[0] = 1;
            put_u32(&mut entry, 18, (data.len() - tlk.offset as usize + strings.len()) as u32);
            put_u32(&mut entry, 22, raw.len() as u32);
            entries.extend_from_slice(&entry);
            strings.extend_from_slice(&raw);
        }
        refs.push(existing[&raw]);
    }
    if entries.is_empty() {
        return Ok((data.to_vec(), refs));
    }
    let mut out = Vec::with_capacity(data.len() + entries.len() + strings.len());
    out.extend_from_slice(&data[..table_end]);
    out.extend_from_slice(&entries);
    out.extend_from_slice(&data[table_end..]);
    out.extend_from_slice(&strings);
    put_u32(&mut out, 0xA, tlk.count + (entries.len() / 0x1A) as u32);
    put_u32(&mut out, 0xE, tlk.offset + entries.len() as u32);
    Ok((out, refs))
}
