//! Python-recorded parity cases for the `creature_inventory` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_creature_inventory.py` on the same synthetic bytes.

mod common;

use cir_config::creature_inventory::rewrite_inventory;
use cir_config::json::{Dict, Value};
use cir_config::py::{put_u16, put_u32};
use cir_config::world_formats::parse_cre;
use common::builders::{item_record, make_cre, record, u32_at};

const SCENARIO: &str = "creature_inventory";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

fn with_selection(data: &[u8], weapon: u32, ability: u32) -> Vec<u8> {
    let mut result = data.to_vec();
    let slots_offset = parse_cre(data, false, false).unwrap().get("item_slots_offset").and_then(Value::as_int).unwrap() as usize;
    put_u16(&mut result, slots_offset + 76, weapon);
    put_u16(&mut result, slots_offset + 78, ability);
    result
}

fn parsed(data: &[u8]) -> Dict {
    parse_cre(data, false, false).unwrap()
}

fn int(dict: &Dict, key: &str) -> i64 {
    dict.get(key).and_then(Value::as_int).unwrap_or_else(|| panic!("{key}"))
}

fn slot(dict: &Dict, index: usize) -> Option<i64> {
    dict.get("slots").and_then(Value::as_list).unwrap()[index].as_int()
}

fn resrefs(dict: &Dict) -> Vec<String> {
    dict.get("items").and_then(Value::as_list).unwrap().iter().map(|row| row.get("resref").and_then(Value::as_str).unwrap().to_string()).collect()
}

#[test]
fn mixed_edits_compact_records_but_keep_other_sections_and_input() {
    let records: Vec<Vec<u8>> = (0..5u16)
        .map(|i| item_record(&format!("ITEM{i:04}"), i + 10, [i + 1, i + 2, i + 3], 0x100 + i as u32))
        .collect();
    let mut original = make_cre(&records, Some(&[(9, 1), (10, 2), (21, 0), (22, 3), (37, 4)]), 7);
    // Noninventory spell and effect sections must remain byte-for-byte fixed.
    let known_offset = original.len();
    original.extend(0u8..12);
    let effect_offset = original.len();
    original.extend(0u8..48);
    put_u32(&mut original, 0x2A0, known_offset as u32);
    put_u32(&mut original, 0x2A4, 1);
    put_u32(&mut original, 0x2C4, effect_offset as u32);
    put_u32(&mut original, 0x2C8, 1);
    let original = with_selection(&original, 0, 6);
    let before = original.clone();

    let rewritten = rewrite_inventory(&original, &[(0, None), (1, Some("newitem")), (3, None)]).unwrap();
    let parsed = parsed(&rewritten);
    assert_eq!(original, before);
    assert_eq!(rewritten.len(), original.len());
    assert_eq!(resrefs(&parsed), ["newitem", "item0002", "item0004"]);
    assert_eq!((slot(&parsed, 9), slot(&parsed, 10)), (Some(0), Some(1)));
    assert_eq!([slot(&parsed, 21), slot(&parsed, 22), slot(&parsed, 37)], [None, None, Some(2)]);
    assert_eq!((int(&parsed, "selected_weapon"), int(&parsed, "selected_weapon_ability")), (0, 6));
    let items = int(&parsed, "items_offset") as usize;
    assert_eq!(rewritten[items..items + 20], [&b"newitem\0"[..], &records[1][8..]].concat()[..]);
    assert_eq!(rewritten[items + 60..items + 100], [0u8; 40]);
    assert_eq!(rewritten[known_offset..effect_offset], before[known_offset..effect_offset]);
    assert_eq!(rewritten[effect_offset..], before[effect_offset..]);
    assert_eq!(rewritten[..0x2A0], before[..0x2A0]);
    assert_eq!(u32_at(&rewritten, 0x2A0) as usize, known_offset);
}

#[test]
fn removed_selected_weapon_chooses_first_remaining_slot() {
    let original = with_selection(
        &make_cre(&[record("WEAPON00"), record("WEAPON01"), record("BACKPACK")], Some(&[(9, 0), (11, 1), (21, 2)]), 0),
        0,
        4,
    );
    let rewritten = parsed(&rewrite_inventory(&original, &[(0, None)]).unwrap());
    assert_eq!(int(&rewritten, "selected_weapon"), 2);
    assert_eq!(int(&rewritten, "selected_weapon_ability"), 0);
    assert_eq!(slot(&rewritten, 11), Some(0));
}

#[test]
fn removed_last_weapon_selects_fists_and_preserves_special_selector() {
    let original = with_selection(&make_cre(&[record("WEAPON00")], Some(&[(9, 0)]), 0), 0, 3);
    let rewritten = parsed(&rewrite_inventory(&original, &[(0, None)]).unwrap());
    assert_eq!((int(&rewritten, "selected_weapon"), int(&rewritten, "selected_weapon_ability")), (1000, 0));
    let special = with_selection(&original, 0xFFFF, 5);
    let preserved = parsed(&rewrite_inventory(&special, &[(0, None)]).unwrap());
    assert_eq!((int(&preserved, "selected_weapon"), int(&preserved, "selected_weapon_ability")), (0xFFFF, 5));
}

#[test]
fn invalid_index_and_replacement_are_rejected_without_mutation() {
    let original = make_cre(&[record("SYNTH001")], None, 0);
    assert!(rewrite_inventory(&original, &[(1, None)]).unwrap_err().is_value_error());
    assert!(rewrite_inventory(&original, &[(0, Some("UPPER"))]).unwrap_err().is_value_error());
    assert_eq!(resrefs(&parsed(&original))[0], "synth001");
}
