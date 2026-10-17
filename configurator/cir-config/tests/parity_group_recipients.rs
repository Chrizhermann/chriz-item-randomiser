//! Python-recorded parity cases for the `group_recipients` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_group_recipients.py` on the same synthetic bytes.

mod common;

use cir_config::group_recipients::{preferred_members, usable};
use cir_config::py::put_u32;
use common::builders::{default_itm, make_cre};
use std::collections::BTreeMap;

const SCENARIO: &str = "group_recipients";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

fn item_mask(offset: usize, value: u8) -> Vec<u8> {
    let mut data = default_itm();
    data[offset] = value;
    data
}

#[derive(Default)]
struct Creature {
    klass: u8,
    race: u8,
    alignment: u8,
    kit: u32,
}

fn creature(spec: Creature) -> Vec<u8> {
    let mut data = make_cre(&[], None, 0);
    put_u32(&mut data, 0x244, spec.kit);
    data[0x272] = spec.race;
    data[0x273] = spec.klass;
    data[0x27b] = spec.alignment;
    data
}

#[test]
fn class_and_race_masks_follow_old_byte_positions() {
    // fl#gear.tpa: class 1 -> unuse3 bit 2; race 2 -> unuse3 bit 7.
    assert!(!usable(&item_mask(0x20, 0x04), &creature(Creature { klass: 1, ..Default::default() })));
    assert!(!usable(&item_mask(0x20, 0x80), &creature(Creature { race: 2, ..Default::default() })));
    assert!(usable(&item_mask(0x20, 0x04), &creature(Creature { klass: 2, ..Default::default() })));
}

#[test]
fn alignment_and_kit_masks_include_original_special_cases() {
    // Alignment 50 is 0b0001001 in the original; 0x400d0000 is kit2 bit 5.
    assert!(!usable(&item_mask(0x1e, 0x08), &creature(Creature { alignment: 50, ..Default::default() })));
    assert!(!usable(&item_mask(0x2b, 0x20), &creature(Creature { kit: 0x400d_0000, ..Default::default() })));
    assert!(!usable(&item_mask(0x2d, 0x40), &creature(Creature { kit: 0x400d_0000, ..Default::default() })));
    assert!(usable(&item_mask(0x2d, 0x40), &creature(Creature { kit: 0x4000_0000, ..Default::default() })));
}

#[test]
fn preferred_members_sorted_and_falls_back_when_none_usable() {
    let item = item_mask(0x20, 0x04);
    let members = BTreeMap::from([
        ("mage".to_string(), creature(Creature { klass: 1, ..Default::default() })),
        ("fighter".to_string(), creature(Creature { klass: 2, ..Default::default() })),
    ]);
    assert_eq!(preferred_members(&item, &members), ["fighter"]);
    let neither = BTreeMap::from([
        ("z".to_string(), creature(Creature { klass: 1, ..Default::default() })),
        ("a".to_string(), creature(Creature { klass: 1, ..Default::default() })),
    ]);
    assert_eq!(preferred_members(&item, &neither), ["a", "z"]);
}
