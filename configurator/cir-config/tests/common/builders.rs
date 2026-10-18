//! Synthetic resources mirroring the reference's test builders
//! (`tests/test_postinstall_resources.py`, `test_postinstall_world_formats.py`,
//! `test_game_text.py`). No game resource is read.

use cir_config::json::Value;
use cir_config::py::{put_u16, put_u32, u16_le, u32_le};
use cir_config::resources::Tlk;
use std::collections::BTreeSet;

pub fn u32s(values: &[u32]) -> Vec<u8> {
    values.iter().flat_map(|value| value.to_le_bytes()).collect()
}

/// `make_tlk(strings)`.
pub fn make_tlk(strings: &[&str]) -> Vec<u8> {
    let (mut entries, mut payload) = (Vec::new(), Vec::new());
    for text in strings {
        let raw = text.as_bytes();
        entries.extend_from_slice(&1u16.to_le_bytes());
        entries.extend_from_slice(&[0; 8]);
        entries.extend(u32s(&[0, 0, payload.len() as u32, raw.len() as u32]));
        payload.extend_from_slice(raw);
    }
    let mut data = b"TLK V1  ".to_vec();
    data.extend_from_slice(&0u16.to_le_bytes());
    data.extend(u32s(&[strings.len() as u32, 0x12 + entries.len() as u32]));
    data.extend(entries);
    data.extend(payload);
    data
}

/// `_Tlk(data).text(strref, set())`.
pub fn tlk_text(tlk: &[u8], strref: u32) -> Option<String> {
    Tlk::new(tlk.to_vec()).expect("TLK").text(strref, &mut BTreeSet::new()).expect("TLK text")
}

/// `make_itm(unidentified, identified, price=...)`.
pub fn make_itm(unidentified: u32, identified: u32, price: u32) -> Vec<u8> {
    let mut data = vec![0u8; 0x72];
    data[..8].copy_from_slice(b"ITM V1  ");
    put_u32(&mut data, 8, unidentified);
    put_u32(&mut data, 12, identified);
    put_u32(&mut data, 0x18, 0x44);
    put_u16(&mut data, 0x1C, 16);
    data[0x31] = 101;
    put_u32(&mut data, 0x34, price);
    put_u16(&mut data, 0x38, 1);
    put_u32(&mut data, 0x60, 2);
    data
}

pub fn default_itm() -> Vec<u8> {
    make_itm(0, 1, 300)
}

/// `item_record(name, expiration, charges, flags)`: `<8s4HI`.
pub fn item_record(name: &str, expiration: u16, charges: [u16; 3], flags: u32) -> Vec<u8> {
    let mut record = name.as_bytes().to_vec();
    record.resize(8, 0);
    for value in [expiration, charges[0], charges[1], charges[2]] {
        record.extend_from_slice(&value.to_le_bytes());
    }
    record.extend_from_slice(&flags.to_le_bytes());
    record
}

pub fn record(name: &str) -> Vec<u8> {
    item_record(name, 13, [7, 3, 2], 0x8000_0009)
}

/// `make_cre(records, slots, gap=..., slots_first=False)`; `slots` None
/// places record i in slot 21 + i.
pub fn make_cre(records: &[Vec<u8>], slots: Option<&[(usize, u16)]>, gap: usize) -> Vec<u8> {
    let mut header = vec![0u8; 0x2D4];
    header[..8].copy_from_slice(b"CRE V1.0");
    put_u32(&mut header, 8, 123);
    header[0x280..0x287].copy_from_slice(b"TestDV\0");
    let mut slot_values = vec![0xFFFFu16; 38];
    slot_values.extend([1000, 0xFFFF]);
    match slots {
        Some(slots) => slots.iter().for_each(|&(slot, index)| slot_values[slot] = index),
        None => (0..records.len()).for_each(|index| slot_values[21 + index] = index as u16),
    }
    let slot_bytes: Vec<u8> = slot_values.iter().flat_map(|value| value.to_le_bytes()).collect();
    let record_bytes = records.concat();
    let item_offset = header.len() + gap;
    let slot_offset = header.len() + gap + record_bytes.len() + gap;
    let mut result = header;
    result.extend(vec![0; gap]);
    result.extend(record_bytes);
    result.extend(vec![0; gap]);
    result.extend(slot_bytes);
    put_u32(&mut result, 0x2B8, slot_offset as u32);
    put_u32(&mut result, 0x2BC, item_offset as u32);
    put_u32(&mut result, 0x2C0, records.len() as u32);
    result
}

/// `make_sto(records, gap=...)`: rows of (item record, quantity, infinite).
pub fn make_sto(records: &[(Vec<u8>, u32, u32)], gap: usize) -> Vec<u8> {
    let mut data = vec![0u8; 0x9C + gap];
    data[..8].copy_from_slice(b"STORV1.0");
    put_u32(&mut data, 8, 5);
    put_u32(&mut data, 12, 456);
    put_u32(&mut data, 16, 3);
    let length = data.len() as u32;
    put_u32(&mut data, 0x34, length);
    put_u32(&mut data, 0x38, records.len() as u32);
    for (item, quantity, infinite) in records {
        data.extend_from_slice(item);
        data.extend(u32s(&[*quantity, *infinite]));
    }
    data
}

/// `test_game_text.parse_dialogue`: rows unpacked as integers/bytes.
pub struct ParsedDialogue {
    /// (text, first transition, count, trigger)
    pub states: Vec<[u32; 4]>,
    pub transitions: Vec<Transition>,
    pub triggers: Vec<Vec<u8>>,
    pub actions: Vec<Vec<u8>>,
    pub transition_triggers: Vec<Vec<u8>>,
}

/// `<5I8sI`: flags, reply, journal, trigger, action, next dialogue, next state.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Transition {
    pub flags: u32,
    pub reply: u32,
    pub journal: u32,
    pub trigger: u32,
    pub action: u32,
    pub next_dialogue: Vec<u8>,
    pub next_state: u32,
}

impl Transition {
    pub fn parse(row: &[u8]) -> Transition {
        Transition {
            flags: u32_le(row, 0),
            reply: u32_le(row, 4),
            journal: u32_le(row, 8),
            trigger: u32_le(row, 12),
            action: u32_le(row, 16),
            next_dialogue: row[20..28].to_vec(),
            next_state: u32_le(row, 28),
        }
    }
}

pub fn parse_dialogue(data: &[u8]) -> ParsedDialogue {
    let field = |index: usize| u32_le(data, 8 + index * 4) as usize;
    let (n, s, m, t, st, sn, tt, tn, a, an) =
        (field(0), field(1), field(2), field(3), field(4), field(5), field(6), field(7), field(8), field(9));
    let strings = |offset: usize, count: usize| -> Vec<Vec<u8>> {
        (0..count)
            .map(|index| {
                let start = u32_le(data, offset + index * 8) as usize;
                let size = u32_le(data, offset + index * 8 + 4) as usize;
                data[start..start + size].to_vec()
            })
            .collect()
    };
    ParsedDialogue {
        states: (0..n)
            .map(|index| {
                let base = s + index * 16;
                [u32_le(data, base), u32_le(data, base + 4), u32_le(data, base + 8), u32_le(data, base + 12)]
            })
            .collect(),
        transitions: (0..m).map(|index| Transition::parse(&data[t + index * 32..t + index * 32 + 32])).collect(),
        triggers: strings(st, sn),
        actions: strings(a, an),
        transition_triggers: strings(tt, tn),
    }
}

impl ParsedDialogue {
    pub fn outgoing(&self, state: usize) -> &[Transition] {
        let [_, first, count, _] = self.states[state];
        &self.transitions[first as usize..(first + count) as usize]
    }
}

/// The outgoing transition rows of a `_Dialogue` state.
pub fn outgoing(dialogue: &cir_config::quest_options::Dialogue, state: usize) -> Vec<Vec<u8>> {
    let row = &dialogue.states[state];
    let (first, count) = (u32_le(row, 4) as usize, u32_le(row, 8) as usize);
    dialogue.transitions[first..first + count].to_vec()
}

/// The (first, count) of a `_Dialogue` state's transitions.
pub fn span(dialogue: &cir_config::quest_options::Dialogue, state: usize) -> (usize, usize) {
    let row = &dialogue.states[state];
    (u32_le(row, 4) as usize, u32_le(row, 8) as usize)
}

pub fn u16_at(data: &[u8], offset: usize) -> u32 {
    u16_le(data, offset)
}

pub fn u32_at(data: &[u8], offset: usize) -> u32 {
    u32_le(data, offset)
}

/// The pinned-hash overrides a scenario's reference tests apply.
pub fn pinned(scenario: &str, table: &str, key: &str) -> String {
    let path = super::fixtures().join(format!("{scenario}.pins.json"));
    let document = cir_config::json::parse_strict(&std::fs::read(path).expect("pins file"), "pins").expect("pins");
    document
        .get(table)
        .and_then(|entries| entries.get(key))
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("no pin {table}[{key}] in {scenario}"))
        .to_string()
}

pub fn sha(data: &[u8]) -> String {
    cir_config::resources::sha256_hex(data)
}

pub fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    cir_config::py::find_bytes(haystack, needle, 0).is_some()
}

pub fn count(haystack: &[u8], needle: &[u8]) -> usize {
    cir_config::py::count_bytes(haystack, needle)
}

pub fn index(haystack: &[u8], needle: &[u8]) -> usize {
    cir_config::py::find_bytes(haystack, needle, 0).unwrap_or_else(|| panic!("{:?} not found", String::from_utf8_lossy(needle)))
}

pub fn replace(haystack: &[u8], old: &[u8], new: &[u8], limit: Option<usize>) -> Vec<u8> {
    cir_config::py::replace_bytes(haystack, old, new, limit)
}

pub fn split<'a>(data: &'a [u8], separator: &[u8]) -> Vec<&'a [u8]> {
    cir_config::py::split_bytes(data, separator)
}
