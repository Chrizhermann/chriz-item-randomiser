//! Python-recorded parity cases for the `original_options` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_original_options.py` on the same synthetic resources.

mod common;

use cir_config::error::ErrorKind;
use cir_config::json::Value;
use cir_config::original_options::{cursed_disguise_choices, patch_cursed_disguise, patch_spell_shield_stock, tome_variants};
use cir_config::py::{put_u16, put_u32};
use cir_config::world_formats::parse_sto;
use common::builders::{default_itm, item_record, make_itm, make_sto, record, u32_at};

const SCENARIO: &str = "original_options";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

pub const ALL_DISGUISE_ITEMS: [&str; 35] = [
    "brac08", "brac06", "brac07", "brac10", "brac09", "brac11", "brac01", "brac02", "brac03", "brac13", "brac14",
    "brac15", "brac04", "leat06", "leat05", "leat07", "leat08", "plat17", "plat02", "plat11", "plat10", "ring04",
    "ring23", "ring06", "ring07", "ring22", "ring09", "ring31", "ring08", "sper03", "sper05", "sper02", "sw2h03",
    "sw2h11", "sw2h02",
];

fn without(names: &[&str]) -> Vec<&'static str> {
    ALL_DISGUISE_ITEMS.iter().copied().filter(|name| !names.contains(name)).collect()
}

fn text<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or_else(|| panic!("{key}"))
}

fn variants(row: &Value) -> &Vec<Value> {
    row.get("variants").and_then(Value::as_list).unwrap()
}

fn donors(row: &Value) -> Vec<&str> {
    variants(row).iter().map(|face| text(face, "donor")).collect()
}

fn find<'a>(rows: &'a [Value], source: &str) -> &'a Value {
    rows.iter().find(|row| text(row, "source") == source).unwrap_or_else(|| panic!("{source}"))
}

fn sale_rows(data: &[u8]) -> Vec<Value> {
    parse_sto(data).unwrap().get("items").and_then(Value::as_list).unwrap().clone()
}

#[test]
fn totsc_tome_has_all_six_original_choices_on_each_supported_game() {
    for game in ["bgee", "bg2ee", "eet"] {
        assert_eq!(tome_variants("bg1", game).unwrap(), ["book03", "book04", "book05", "book06", "book07", "book08"]);
    }
}

#[test]
fn spell_shield_only_changes_first_matching_quantity() {
    for first_quantity in [0u32, 1, 3, 9] {
        let original = make_sto(
            &[
                (item_record("scrl8x", 13, [2, 3, 4], 9), first_quantity, 1),
                (item_record("scrl8x", 13, [9, 8, 7], 4), 1, 0),
                (record("other"), 5, 0),
            ],
            11,
        );
        let patched = patch_spell_shield_stock(&original).unwrap();
        let mut expected = original.clone();
        let offset = sale_rows(&original)[0].get("byte_offset").and_then(Value::as_int).unwrap() as usize;
        put_u32(&mut expected, offset + 20, first_quantity.max(3));
        assert_eq!(patched, expected);
        assert_eq!(patch_spell_shield_stock(&patched).unwrap(), patched);
    }
}

#[test]
fn spell_shield_insertion_preserves_other_sections_and_sale_payloads() {
    let mut original =
        make_sto(&[(record("other"), 4, 0), (record("scrl7q"), 8, 1), (record("scrlal"), 9, 0)], 13);
    // An unrelated purchased-category section after the sale table must
    // retain its absolute offset and exact bytes after insertion.
    let length = original.len() as u32;
    put_u32(&mut original, 0x2C, length);
    put_u32(&mut original, 0x30, 1);
    original.extend_from_slice(&16u32.to_le_bytes());
    let before = original.clone();
    let patched = patch_spell_shield_stock(&before).unwrap();
    let rows = sale_rows(&patched);
    assert_eq!(rows.iter().map(|row| text(row, "resref")).collect::<Vec<_>>(), ["other", "scrl8x", "scrl7q", "scrlal"]);
    let added = &rows[1];
    let int = |key: &str| added.get(key).and_then(Value::as_int).unwrap();
    assert_eq!(
        (int("quantity"), int("flags"), int("infinite_supply_flag"), int("expiration")),
        (3, 1, 0, 0)
    );
    assert!(added.get("charges").unwrap().py_eq(&Value::from(vec![0i64, 0, 0])));
    let mut expected_old_bytes = before.clone();
    expected_old_bytes[0x34..0x3C].copy_from_slice(&patched[0x34..0x3C]);
    assert_eq!(patched[..before.len()], expected_old_bytes[..]);
    assert_eq!(patch_spell_shield_stock(&patched).unwrap(), patched);
    assert_eq!(original, before);
}

#[test]
fn spell_shield_can_populate_empty_store() {
    let patched = patch_spell_shield_stock(&make_sto(&[], 0)).unwrap();
    assert_eq!(text(&sale_rows(&patched)[0], "resref"), "scrl8x");
}

#[test]
fn disguise_preserves_original_dice_and_optional_fallback_weights() {
    let rows = cursed_disguise_choices(&ALL_DISGUISE_ITEMS, "eet", false).unwrap();
    let summary: Vec<(&str, usize)> = rows.iter().map(|row| (text(row, "source"), variants(row).len())).collect();
    assert_eq!(
        summary,
        [("brac08", 4), ("brac11", 7), ("leat06", 3), ("plat17", 3), ("ring04", 6), ("ring23", 6), ("sper03", 2), ("sw2h03", 2)]
    );
    assert_eq!(donors(find(&rows, "ring23")), ["ring06", "ring07", "ring08", "ring09", "ring31", "ring08"]);
    let absent = without(&["brac13", "brac14", "brac15", "ring31", "sper05", "sw2h11"]);
    let mapped = cursed_disguise_choices(&absent, "eet", false).unwrap();
    assert_eq!(donors(find(&mapped, "brac11")), ["brac01", "brac02", "brac03", "brac04", "brac04", "brac04", "brac04"]);
    assert_eq!(donors(find(&mapped, "ring23")).iter().filter(|donor| **donor == "ring08").count(), 3);
    assert_eq!(donors(find(&mapped, "sper03")), ["sper02", "sper02"]);
}

#[test]
fn item_revisions_keeps_plate_and_uses_its_authored_leather_descriptions() {
    let rows = cursed_disguise_choices(&ALL_DISGUISE_ITEMS, "eet", true).unwrap();
    assert!(!rows.iter().any(|row| text(row, "source") == "plat17"));
    let descriptions: Vec<Option<i64>> =
        variants(find(&rows, "leat06")).iter().map(|face| face.get("description_tra").and_then(Value::as_int)).collect();
    assert_eq!(descriptions, [Some(210), Some(210), Some(211)]);
    let error = cursed_disguise_choices(&without(&["brac06"]), "eet", false).unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    assert!(error.message.contains("needs brac06"), "{}", error.message);
}

#[test]
fn disguise_changes_only_appearance_and_requested_description() {
    let original = make_itm(15, 16, 500);
    let mut donor = make_itm(25, 26, 900);
    donor[0x3A..0x42].copy_from_slice(b"INVICON1");
    donor[0x58..0x60].copy_from_slice(b"CARICON1");
    put_u32(&mut donor, 0x50, 123);
    let mut expected = original.clone();
    expected[0x3A..0x42].copy_from_slice(&donor[0x3A..0x42]);
    expected[0x50..0x54].copy_from_slice(&donor[0x50..0x54]);
    expected[0x58..0x60].copy_from_slice(&donor[0x58..0x60]);
    assert_eq!(patch_cursed_disguise(&original, &donor, None, None).unwrap(), expected);
    put_u32(&mut expected, 0x54, 456);
    assert_eq!(patch_cursed_disguise(&original, &donor, Some(456), None).unwrap(), expected);
}

#[test]
fn plate_fix_only_changes_original_owned_global_ac_effect() {
    let mut original = default_itm();
    let length = original.len() as u32;
    put_u32(&mut original, 0x6A, length);
    put_u16(&mut original, 0x6E, 0);
    put_u16(&mut original, 0x70, 3);
    original.extend(vec![0u8; 3 * 0x30]);
    // Original code adjusts opcode 0 / parameter2 16, not other AC modes or
    // an unrelated opcode that happens to use parameter2 16.
    for (index, (opcode, mode)) in [(0, 16), (0, 1), (17, 16)].into_iter().enumerate() {
        put_u16(&mut original, 0x72 + index * 0x30, opcode);
        put_u32(&mut original, 0x76 + index * 0x30, 9);
        put_u32(&mut original, 0x7A + index * 0x30, mode);
    }
    let patched = patch_cursed_disguise(&original, &default_itm(), Some(77), Some(88)).unwrap();
    assert_eq!(u32_at(&patched, 0x0C), 88);
    assert_eq!(u32_at(&patched, 0x60), 0);
    assert_eq!((0..3).map(|i| u32_at(&patched, 0x76 + i * 0x30)).collect::<Vec<_>>(), [3, 9, 9]);
    let error = patch_cursed_disguise(&original[..original.len() - 1], &default_itm(), None, Some(88)).unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    assert!(error.message.contains("effect range"), "{}", error.message);
}
