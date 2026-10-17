//! Rewrite a campaign-private CRE inventory without moving other sections
//! (the reference's `creature_inventory.py`).

use crate::error::{Error, Result};
use crate::error::py_repr_str;
use crate::json::Value;
use crate::py::{put_u16, put_u32, u16_le};
use crate::world_formats::parse_cre;
use std::collections::HashMap;

fn replacement_bytes(resref: &str) -> Result<[u8; 8]> {
    let length = resref.chars().count();
    if !(1..=8).contains(&length)
        || resref != crate::py::lower(resref)
        || resref.chars().any(|c| (c as u32) < 33 || (c as u32) > 126 || "/\\:*?\"<>|".contains(c))
    {
        return Err(Error::value(format!("Invalid replacement item resref: {}", py_repr_str(resref))));
    }
    let mut out = [0u8; 8];
    out[..resref.len()].copy_from_slice(resref.as_bytes());
    Ok(out)
}

/// Replace or remove exact physical item indexes, keeping CRE offsets fixed.
///
/// Item records are compacted inside their original table allocation; vacated
/// records are zeroed. The 38 inventory slot words are remapped. The following
/// two words select a weapon slot and ability, rather than an item index.
/// `edits` is the reference's dict in insertion order: a repeated index keeps
/// its first position and its last replacement; None removes the record.
pub fn rewrite_inventory(data: &[u8], edits: &[(i64, Option<&str>)]) -> Result<Vec<u8>> {
    let cre = parse_cre(data, false, false)?;
    let count = cre.get("items").and_then(Value::as_list).expect("parsed items").len();
    let mut ordered: Vec<(i64, Option<&str>)> = Vec::new();
    for &(index, replacement) in edits {
        match ordered.iter_mut().find(|(existing, _)| *existing == index) {
            Some(entry) => entry.1 = replacement,
            None => ordered.push((index, replacement)),
        }
    }
    let mut replacements: HashMap<usize, Option<[u8; 8]>> = HashMap::new();
    for (index, replacement) in ordered {
        if !(0..count as i64).contains(&index) {
            return Err(Error::value(format!("CRE item index out of range: {index}")));
        }
        replacements.insert(index as usize, replacement.map(replacement_bytes).transpose()?);
    }
    if replacements.is_empty() {
        return Ok(data.to_vec());
    }
    let int = |key: &str| cre.get(key).and_then(Value::as_int).expect("parsed CRE field") as usize;
    let (item_offset, slot_offset) = (int("items_offset"), int("item_slots_offset"));
    let mut old_to_new: HashMap<usize, usize> = HashMap::new();
    let mut kept: Vec<u8> = Vec::with_capacity(20 * count);
    for index in 0..count {
        if let Some(None) = replacements.get(&index) {
            continue;
        }
        let mut record = data[item_offset + 20 * index..item_offset + 20 * (index + 1)].to_vec();
        if let Some(Some(resref)) = replacements.get(&index) {
            record[..8].copy_from_slice(resref);
        }
        old_to_new.insert(index, kept.len() / 20);
        kept.extend(record);
    }
    let kept_count = kept.len() / 20;
    kept.resize(20 * count, 0);
    let mut result = data.to_vec();
    result[item_offset..item_offset + kept.len()].copy_from_slice(&kept);
    put_u32(&mut result, 0x2C0, kept_count as u32);
    let slots: Vec<Option<usize>> = cre
        .get("slots")
        .and_then(Value::as_list)
        .expect("parsed slots")
        .iter()
        .map(|slot| slot.as_int().map(|index| index as usize))
        .collect();
    for (slot, old_index) in slots.iter().enumerate() {
        let new_index = old_index.and_then(|old| old_to_new.get(&old).copied()).map_or(0xFFFF, |new| new as u32);
        put_u16(&mut result, slot_offset + 2 * slot, new_index);
    }
    let selected = int("selected_weapon");
    if selected < 4 {
        if let Some(selected_index) = slots[9 + selected].filter(|index| replacements.contains_key(index)) {
            if replacements[&selected_index].is_none() {
                let first_weapon = (9..13).find(|&slot| u16_le(&result, slot_offset + 2 * slot) != 0xFFFF);
                let replacement_selector = first_weapon.map_or(1000, |slot| slot - 9);
                if replacement_selector != selected {
                    put_u16(&mut result, slot_offset + 76, replacement_selector as u32);
                    put_u16(&mut result, slot_offset + 78, 0);
                }
            }
        }
    }
    Ok(result)
}
