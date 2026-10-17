//! Original Mode 1 group recipient preference, without making a random draw
//! (the reference's `group_recipients.py`).
//!
//! The eight masks follow `read_itm_unusabilities` and
//! `define_cre_unusabilities` in `lib/fl#gear.tpa`. The fallback to all
//! available creatures follows `select_group_cre` in `lib/lib.tpa`.

use std::collections::BTreeMap;

fn lookup(table: &[(u32, u8)], key: u32) -> u8 {
    table.iter().find(|(entry, _)| *entry == key).map_or(0, |(_, mask)| *mask)
}

const ALIGNMENT: &[(u32, u8)] =
    &[(17, 0x14), (33, 0x24), (49, 0x05), (18, 0x18), (34, 0x28), (50, 0x09), (19, 0x12), (35, 0x22), (51, 0x03)];
const CLASS_2: &[(u32, u8)] = &[(17, 0x80), (8, 0x40), (7, 0x20), (16, 0x10), (2, 0x08), (18, 0x04), (15, 0x02), (14, 0x01)];
const CLASS_3: &[(u32, u8)] = &[(10, 0x01), (9, 0x02), (1, 0x04), (13, 0x08), (6, 0x10), (12, 0x20), (4, 0x40)];
const RACE_4: &[(u32, u8)] = &[(4, 0x01), (3, 0x02), (5, 0x04), (1, 0x08), (6, 0x10), (7, 0x80)];
const KIT_1: &[(u32, u8)] = &[
    (0x4013_0000, 0x01),
    (0x4014_0000, 0x02),
    (0x4015_0000, 0x04),
    (0x4010_0000, 0x08),
    (0x4011_0000, 0x10),
    (0x4012_0000, 0x20),
    (0x0000_4000, 0x40),
    (0x0000_8000, 0x80),
];
const KIT_3: &[(u32, u8)] = &[
    (0x0100_0000, 0x01),
    (0x0200_0000, 0x02),
    (0x0400_0000, 0x04),
    (0x0800_0000, 0x08),
    (0x1000_0000, 0x10),
    (0x2000_0000, 0x20),
    (0x4007_0000, 0x80),
];
const KIT_4: &[(u32, u8)] = &[
    (0x4001_0000, 0x01),
    (0x4002_0000, 0x02),
    (0x4003_0000, 0x04),
    (0x4004_0000, 0x08),
    (0x4005_0000, 0x10),
    (0x4006_0000, 0x20),
    (0x0040_0000, 0x40),
    (0x0080_0000, 0x80),
];

/// `_KIT_2 = {0x40080000 + i * 0x10000: 1 << i for i in range(8)}`.
fn kit_2(kit: u32) -> u8 {
    (0..8).find(|&i| kit == 0x4008_0000 + i * 0x1_0000).map_or(0, |i| 1 << i)
}

fn byte(data: &[u8], offset: usize) -> u8 {
    data.get(offset).copied().unwrap_or(0)
}

fn long(data: &[u8], offset: usize) -> u32 {
    if data.len() >= offset + 4 {
        crate::py::u32_le(data, offset)
    } else {
        0
    }
}

fn item_masks(item: &[u8]) -> [u8; 8] {
    if item.len() <= 0x71 {
        return [0; 8];
    }
    [0x1e, 0x1f, 0x20, 0x21, 0x29, 0x2b, 0x2d, 0x2f].map(|offset| byte(item, offset))
}

fn creature_masks(cre: &[u8]) -> [u8; 8] {
    let kit = long(cre, 0x244);
    let (race, class, alignment) = (byte(cre, 0x272) as u32, byte(cre, 0x273) as u32, byte(cre, 0x27b) as u32);
    let unuse_1 = lookup(ALIGNMENT, alignment) | lookup(&[(5, 0x40), (3, 0x80)], class);
    let unuse_2 = lookup(CLASS_2, class);
    let unuse_3 = lookup(CLASS_3, class) | if race == 2 { 0x80 } else { 0 };
    let unuse_4 = lookup(RACE_4, race) | lookup(&[(20, 0x20), (11, 0x40)], class);
    let kit_3 = lookup(KIT_3, kit) | if kit != 0 && kit != 0x4000_0000 { 0x40 } else { 0 };
    [unuse_1, unuse_2, unuse_3, unuse_4, lookup(KIT_1, kit), kit_2(kit), kit_3, lookup(KIT_4, kit)]
}

/// Whether the old group selector would prefer this creature for the item.
pub fn usable(item: &[u8], cre: &[u8]) -> bool {
    item_masks(item).iter().zip(creature_masks(cre)).all(|(item_mask, cre_mask)| item_mask & cre_mask == 0)
}

/// Return sorted usable members, or all sorted members when none qualify.
pub fn preferred_members<B: AsRef<[u8]>>(item: &[u8], members: &BTreeMap<String, B>) -> Vec<String> {
    let names: Vec<String> = members.keys().cloned().collect();
    let preferred: Vec<String> = names.iter().filter(|name| usable(item, members[*name].as_ref())).cloned().collect();
    if preferred.is_empty() {
        names
    } else {
        preferred
    }
}
