//! Rewrite selected STO sale rows within their existing table allocation (the
//! reference's `store_inventory.py`).

use crate::error::{Error, Result};
use crate::json::Value;
use crate::py::put_u32;
use crate::world_formats::parse_sto;
use std::collections::BTreeSet;

/// Remove exact finite sale rows without shifting other STO sections.
///
/// The reference iterates a Python set; with several invalid indexes the
/// first one reported here is the smallest, which can differ from CPython's
/// set order.
pub fn rewrite_inventory(data: &[u8], removals: &BTreeSet<i64>) -> Result<Vec<u8>> {
    let store = parse_sto(data)?;
    let items = store.get("items").and_then(Value::as_list).expect("parsed sale rows");
    let field = |index: usize, key: &str| items[index].get(key).and_then(Value::as_int).expect("parsed STO field");
    for &index in removals {
        if !(0..items.len() as i64).contains(&index) {
            return Err(Error::value(format!("STO sale index out of range: {index}")));
        }
        if field(index as usize, "quantity") == 0 || field(index as usize, "infinite_supply_flag") != 0 {
            return Err(Error::value(format!("STO sale row is not finite stock: {index}")));
        }
    }
    if removals.is_empty() {
        return Ok(data.to_vec());
    }
    let offset = field(0, "byte_offset") as usize;
    let mut table = Vec::with_capacity(28 * items.len());
    for index in 0..items.len() {
        if !removals.contains(&(index as i64)) {
            let start = field(index, "byte_offset") as usize;
            table.extend_from_slice(&data[start..start + 28]);
        }
    }
    let kept = table.len() / 28;
    table.resize(28 * items.len(), 0);
    let mut result = data.to_vec();
    result[offset..offset + table.len()].copy_from_slice(&table);
    put_u32(&mut result, 0x38, kept as u32);
    Ok(result)
}
