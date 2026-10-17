//! Python-recorded parity cases for the `store_inventory` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_store_inventory.py` on the same synthetic bytes.

mod common;

use cir_config::error::ErrorKind;
use cir_config::json::Value;
use cir_config::py::put_u32;
use cir_config::store_inventory::rewrite_inventory;
use cir_config::world_formats::parse_sto;
use common::builders::{item_record, make_sto, record, u32_at};
use std::collections::BTreeSet;

const SCENARIO: &str = "store_inventory";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

fn set(values: &[i64]) -> BTreeSet<i64> {
    values.iter().copied().collect()
}

#[test]
fn middle_row_removal_compacts_stock_without_moving_other_sections() {
    let rows = vec![
        (item_record("FIRST", 13, [1, 2, 3], 0x8000_0009), 2, 0),
        (item_record("REMOVE", 13, [4, 5, 6], 0x8000_0009), 1, 0),
        (item_record("THIRD", 13, [7, 8, 9], 0x8000_0009), 5, 0),
    ];
    let mut data = make_sto(&rows, 9);
    // Place a purchased-category section after the sale table.
    data.extend_from_slice(b"TAIL");
    data.extend_from_slice(&0x1234_5678u32.to_le_bytes());
    let category_offset = data.len() - 4;
    put_u32(&mut data, 0x2C, category_offset as u32);
    put_u32(&mut data, 0x30, 1);
    let before = data.clone();
    let sale_offset = u32_at(&data, 0x34) as usize;

    let result = rewrite_inventory(&before, &set(&[1])).unwrap();

    assert_eq!(result.len(), before.len());
    assert_eq!(result[..0x38], before[..0x38]);
    assert_eq!(result[0x3C..sale_offset], before[0x3C..sale_offset]);
    assert_eq!(result[category_offset..], before[category_offset..]);
    assert_eq!((u32_at(&result, 0x2C), u32_at(&result, 0x30)), (category_offset as u32, 1));
    assert_eq!(result[sale_offset + 56..sale_offset + 84], [0u8; 28]);
    let parsed = parse_sto(&result).unwrap();
    let rows: Vec<(String, Vec<i64>, i64)> = parsed
        .get("items")
        .and_then(Value::as_list)
        .unwrap()
        .iter()
        .map(|row| {
            (
                row.get("resref").and_then(Value::as_str).unwrap().to_string(),
                row.get("charges").and_then(Value::as_list).unwrap().iter().map(|value| value.as_int().unwrap()).collect(),
                row.get("quantity").and_then(Value::as_int).unwrap(),
            )
        })
        .collect();
    assert_eq!(rows, vec![("first".to_string(), vec![1, 2, 3], 2), ("third".to_string(), vec![7, 8, 9], 5)]);
    assert_eq!(before, data);
}

#[test]
fn invalid_indices_and_malformed_store_fail_before_rewrite() {
    let data = make_sto(&[(record("SYNTH001"), 1, 0)], 0);
    // {True} is the set {1} once Python's bool is an int; a BTreeSet<i64>
    // cannot hold a bool, so that subtest is the {1} case.
    for removals in [&[1][..], &[-1]] {
        let error = rewrite_inventory(&data, &set(removals)).unwrap_err();
        assert!(error.is_value_error(), "{removals:?}: {error:?}");
    }
    assert_eq!(rewrite_inventory(&data, &BTreeSet::new()).unwrap(), data);
    assert_eq!(rewrite_inventory(&data[..data.len() - 1], &set(&[0])).unwrap_err().kind, ErrorKind::Scan);
}
