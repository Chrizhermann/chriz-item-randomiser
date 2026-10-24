//! Python-recorded parity cases for the `cursed_disguises` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_cursed_disguises.py` on the same synthetic resources.

mod common;

use cir_config::cursed_disguises::{
    cursed_disguise_indices, cursed_disguise_spec, item_revisions_installed, prepare_cursed_disguises,
    validate_cursed_disguise_spec,
};
use cir_config::error::ErrorKind;
use cir_config::json::{Dict, Value};
use cir_config::py::{put_u16, put_u32};
use cir_config::quest_options::texts;
use cir_config::game_text::GameText;
use cir_config::resources::Tlk;
use cir_config::translations;
use common::builders::{make_itm, make_tlk, sha, tlk_text, u32_at, ALL_DISGUISE_ITEMS};
use std::collections::{BTreeMap, HashMap};

const SCENARIO: &str = "cursed_disguises";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// `resources()`: one distinguishable ITM per catalogue name, with one
/// ability and three global effects.
fn resources() -> BTreeMap<String, Vec<u8>> {
    let mut names = ALL_DISGUISE_ITEMS.to_vec();
    names.sort();
    let mut result = BTreeMap::new();
    for (index, name) in (1u32..).zip(names) {
        let mut data = make_itm(index, index + 100, 1000 + index);
        data[0x3A..0x42].copy_from_slice(format!("I{index:07}").as_bytes());
        data[0x58..0x60].copy_from_slice(format!("C{index:07}").as_bytes());
        for (at, value) in [20 + index, index + 200, index + 300].into_iter().enumerate() {
            put_u32(&mut data, 0x4C + at * 4, value);
        }
        // Only the original plate's opcode-0/base-AC effect is owned by
        // component 570.
        put_u32(&mut data, 0x64, 0x72);
        put_u16(&mut data, 0x68, 1);
        put_u32(&mut data, 0x6A, 0xAA);
        put_u16(&mut data, 0x6E, 0);
        put_u16(&mut data, 0x70, 3);
        data.extend(vec![index as u8; 0x38]);
        data.extend(vec![0u8; 3 * 0x30]);
        for (at, (opcode, mode)) in [(0, 16), (0, 1), (17, 16)].into_iter().enumerate() {
            put_u16(&mut data, 0xAA + at * 0x30, opcode);
            put_u32(&mut data, 0xAE + at * 0x30, 9);
            put_u32(&mut data, 0xB2 + at * 0x30, mode);
        }
        result.insert(name.to_string(), data);
    }
    result
}

#[test]
fn builder_matches_the_generator_inputs() {
    let recorded = common::load(SCENARIO);
    let fixture = recorded.args("prepare all").get("resources").and_then(Value::as_dict).unwrap();
    let data = resources();
    assert_eq!(fixture.keys().collect::<Vec<_>>(), data.keys().map(String::as_str).collect::<Vec<_>>());
    for (name, value) in &data {
        assert_eq!(&recorded.blob(fixture.get(name).unwrap()), value, "{name}");
    }
}

fn spec_for(data: &BTreeMap<String, Vec<u8>>, item_revisions: bool) -> Value {
    let hashes: Dict = data.iter().map(|(name, value)| (name.clone(), Value::from(sha(value)))).collect();
    cursed_disguise_spec(&hashes, "eet", item_revisions).unwrap()
}

fn choices(spec: &Value) -> &Vec<Value> {
    spec.get("choices").and_then(Value::as_list).unwrap()
}

fn text<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or_else(|| panic!("{key}"))
}

fn variants(row: &Value) -> &Vec<Value> {
    row.get("variants").and_then(Value::as_list).unwrap()
}

#[test]
fn all_eight_items_and_exact_original_face_weights_are_frozen() {
    let mut data = resources();
    let spec = spec_for(&data, false);
    let summary: Vec<(&str, usize)> = choices(&spec).iter().map(|row| (text(row, "source"), variants(row).len())).collect();
    assert_eq!(
        summary,
        [("brac08", 4), ("brac11", 7), ("leat06", 3), ("plat17", 3), ("ring04", 6), ("ring23", 6), ("sper03", 2), ("sw2h03", 2)]
    );
    for name in ["brac13", "brac14", "brac15", "ring31", "sper05", "sw2h11"] {
        data.remove(name);
    }
    let fallback = spec_for(&data, false);
    validate_cursed_disguise_spec(&fallback).unwrap();
    let by_name: HashMap<&str, Vec<&str>> = choices(&fallback)
        .iter()
        .map(|row| (text(row, "source"), variants(row).iter().map(|face| text(face, "donor")).collect()))
        .collect();
    assert_eq!(by_name["brac11"], ["brac01", "brac02", "brac03", "brac04", "brac04", "brac04", "brac04"]);
    assert_eq!(by_name["ring23"].iter().filter(|donor| **donor == "ring08").count(), 3);
    assert_eq!(by_name["sper03"], ["sper02", "sper02"]);
    let mut changed = spec.clone();
    let face = changed.get_mut("choices").and_then(Value::as_list_mut).unwrap()[0]
        .get_mut("variants")
        .and_then(Value::as_list_mut)
        .unwrap()[0]
        .as_dict_mut()
        .unwrap();
    face.insert("donor", "brac01");
    let error = validate_cursed_disguise_spec(&changed).unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    assert!(error.message.contains("differs from original"), "{}", error.message);
}

#[test]
fn assets_copy_only_original_owned_bytes_and_exact_ee_text() {
    let data = resources();
    let spec = spec_for(&data, false);
    let (fixture, files, game_text) =
        prepare_cursed_disguises(&spec, &data, &GameText::new(make_tlk(&["Existing text"])), "en_us").unwrap();
    assert_eq!(files.len(), 41); // eight originals plus 33 die faces
    assert_eq!(game_text.female, None);
    let tlk = &game_text.male;
    assert_eq!(fixture.get("algorithm").and_then(Value::as_str), Some("park-miller-cursed-v1"));
    let by_name: HashMap<&str, &Vec<u8>> = files.iter().map(|(name, value)| (name.as_str(), value)).collect();
    let authored = texts("languages/bgee/english/in-game.tra");
    assert_eq!(tlk_text(tlk, 0).as_deref(), Some("Existing text"));
    let items = fixture.get("items").and_then(Value::as_list).unwrap();
    assert_eq!(items.len(), choices(&spec).len());
    for (row, item) in choices(&spec).iter().zip(items) {
        let source = text(row, "source");
        assert_eq!(by_name[format!("{source}.original.itm").as_str()], &data[source]);
        assert_eq!(text(item, "resref"), source);
        for (face_number, (face, metadata)) in (1..).zip(variants(row).iter().zip(variants(item))) {
            let variant = by_name[format!("{source}.{face_number}.itm").as_str()];
            assert_eq!(variant.len(), data[source].len());
            assert_eq!(text(metadata, "sha256"), sha(variant));
            let mut expected = data[source].clone();
            let donor = &data[text(face, "donor")];
            for (offset, size) in [(0x3A, 8), (0x50, 4), (0x58, 8)] {
                expected[offset..offset + size].copy_from_slice(&donor[offset..offset + size]);
            }
            if let Some(description) = face.get("description_tra").and_then(Value::as_int) {
                let reference = u32_at(variant, 0x54);
                assert_eq!(tlk_text(tlk, reference).as_deref(), Some(authored[&description].as_str()));
                expected[0x54..0x58].copy_from_slice(&variant[0x54..0x58]);
            }
            if source == "plat17" {
                let reference = u32_at(variant, 0x0C);
                assert_eq!(tlk_text(tlk, reference).as_deref(), Some(authored[&212].as_str()));
                put_u32(&mut expected, 0x0C, reference);
                put_u32(&mut expected, 0x60, 0); // enchantment, NOT weight
                put_u32(&mut expected, 0xAE, 3);
            }
            assert_eq!(variant, &expected);
        }
    }
    // Re-export reuses old text references, keeping prior saved revisions valid.
    let (again, again_files, again_text) = prepare_cursed_disguises(&spec, &data, &game_text, "en_us").unwrap();
    assert!(again.py_eq(&fixture));
    assert_eq!(again_files, files);
    assert_eq!(again_text, game_text);
}

/// German and Polish games get their own descriptions, in dialog.tlk and
/// dialogf.tlk alike; any other language gets the English ones.
#[test]
fn descriptions_are_the_games_language_or_english() {
    let data = resources();
    let spec = spec_for(&data, false);
    let ring = choices(&spec).iter().find(|row| text(row, "source") == "ring04").unwrap();
    for (language, own) in [("de_DE", "de_DE"), ("pl_PL", "pl_PL"), ("es_ES", "en_US"), ("default", "en_US")] {
        let start = GameText { male: make_tlk(&["old"]), female: Some(make_tlk(&["old (f)"])) };
        let (_, files, game_text) = prepare_cursed_disguises(&spec, &data, &start, language).unwrap();
        let female = game_text.female.clone().unwrap();
        assert_eq!(Tlk::new(game_text.male.clone()).unwrap().count, Tlk::new(female.clone()).unwrap().count);
        let expected = translations::texts(own, "in-game.tra").unwrap();
        let by_name: HashMap<&str, &Vec<u8>> = files.iter().map(|(name, value)| (name.as_str(), value)).collect();
        for (number, face) in (1..).zip(variants(ring)) {
            let reference = u32_at(by_name[format!("ring04.{number}.itm").as_str()], 0x54);
            let description = face.get("description_tra").and_then(Value::as_int).unwrap();
            assert_eq!(
                (tlk_text(&game_text.male, reference).unwrap(), tlk_text(&female, reference).unwrap()),
                expected[&description],
                "{language}"
            );
        }
    }
    let german = &translations::texts("de_DE", "in-game.tra").unwrap()[&216].0;
    assert!(german.starts_with("Ungeschicklichkeitsring: Die Torheit des Narren\r\n"), "{german}");
}

#[test]
fn item_revisions_component_zero_selects_exact_original_compatibility() {
    assert!(item_revisions_installed(b"// ignored\n~ITEM_REV/ITEM_REV.TP2~ #2 #0 // core\n"));
    assert!(item_revisions_installed(b"~C:\\Mods\\item_rev.tp2~ #0 #000 // core\n"));
    for log in [&b"// ~item_rev.tp2~ #0 #0"[..], b"~item_rev.tp2~ #0 #10", b"~other_item_rev.tp2~ #0 #0", b""] {
        assert!(!item_revisions_installed(log), "{}", String::from_utf8_lossy(log));
    }
    let data = resources();
    let spec = spec_for(&data, true);
    let (fixture, files, _text) = prepare_cursed_disguises(&spec, &data, &GameText::new(make_tlk(&[])), "en_us").unwrap();
    let items = fixture.get("items").and_then(Value::as_list).unwrap();
    assert!(!items.iter().any(|row| text(row, "resref") == "plat17"));
    assert!(!files.iter().any(|(name, _)| name.starts_with("plat17.")));
    let leather = choices(&spec).iter().find(|row| text(row, "source") == "leat06").unwrap();
    let descriptions: Vec<Option<i64>> =
        variants(leather).iter().map(|face| face.get("description_tra").and_then(Value::as_int)).collect();
    assert_eq!(descriptions, [Some(210), Some(210), Some(211)]);
}

#[test]
fn seed_restoration_reconstructs_faces_without_changing_inputs() {
    let spec = spec_for(&resources(), false);
    let before = spec.clone();
    // Boundary seeds exercise the component salt's wraparound too.
    let traces: Vec<Dict> = [1i64, 1_234_567, 2_147_483_646]
        .into_iter()
        .map(|seed| cursed_disguise_indices(&spec, &Value::from(seed)).unwrap())
        .collect();
    let values: Vec<Vec<i64>> =
        traces.iter().map(|trace| trace.values().map(|value| value.as_int().unwrap()).collect()).collect();
    assert_eq!(values, [[1, 2, 3, 2, 6, 3, 2, 1], [4, 3, 2, 3, 2, 2, 2, 2], [2, 3, 2, 3, 3, 2, 2, 2]]);
    for (trace, seed) in traces.iter().zip([1i64, 1_234_567, 2_147_483_646]) {
        assert!(Value::Dict(trace.clone()).py_eq(&Value::Dict(cursed_disguise_indices(&spec.clone(), &Value::from(seed)).unwrap())));
    }
    assert!(spec.py_eq(&before));
    assert!(!Value::Dict(traces[0].clone()).py_eq(&Value::Dict(traces[1].clone())));
    for seed in [Value::from(0i64), Value::from(-1i64), Value::from(2_147_483_647i64), Value::Bool(true)] {
        let error = cursed_disguise_indices(&spec, &seed).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Profile);
        assert!(error.message.contains("campaign seed"), "{}", error.message);
    }
}

#[test]
fn changed_install_bytes_cannot_create_new_variants_for_saved_revision() {
    let mut data = resources();
    let spec = spec_for(&data, false);
    data.get_mut("brac06").unwrap().extend_from_slice(b"different mod data");
    let error = prepare_cursed_disguises(&spec, &data, &GameText::new(make_tlk(&[])), "en_us").unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    // assertRaisesRegex "resource changed.*brac06"
    let at = error.message.find("resource changed").expect(&error.message);
    assert!(error.message[at..].contains("brac06"), "{}", error.message);
}

/// The patcher part of test_original_option_export's
/// test_cursed_assets_are_independent_of_selected_items_and_immutable:
/// abilities without effects, the whole catalogue, immutable originals.
#[test]
fn export_assets_are_independent_of_selected_items_and_immutable() {
    let mut data = resources();
    for value in data.values_mut() {
        put_u16(value, 0x72 + 0x1E, 0);
        put_u16(value, 0x72 + 0x20, 0);
    }
    let spec = spec_for(&data, false);
    let (fixture, files, game_text) =
        prepare_cursed_disguises(&spec, &data, &GameText::new(make_tlk(&[])), "en_US").unwrap();
    assert_eq!(fixture.get("items").and_then(Value::as_list).unwrap().len(), 8);
    assert_eq!(files.iter().filter(|(name, _)| name.ends_with(".itm")).count(), 41);
    assert_eq!(files.iter().find(|(name, _)| name == "plat17.original.itm").unwrap().1, data["plat17"]);
    let again = prepare_cursed_disguises(&spec, &data, &game_text, "en_US").unwrap();
    assert!(again.0.py_eq(&fixture));
    assert_eq!((again.1, again.2), (files, game_text));
}
