//! Python-recorded parity cases for the `quest_options` scenario (which also
//! holds the `quest_rewards` helper cases; see
//! `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_quest_options.py` on the same synthetic resources.
//!
//! The reference tests patch `quest_options.ORIGINAL_HASHES` to the synthetic
//! resources' hashes; those tests need the `parity-overrides` feature.

mod common;

use cir_config::game_text::GameText;
use cir_config::json::Value;
use cir_config::quest_options::prepare_quest_options;
use cir_config::quest_rewards::quests;
use common::builders::{make_tlk, pinned, sha, u32s};
use std::collections::BTreeMap;

const SCENARIO: &str = "quest_options";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// `dialogue_fixture(name)`.
pub fn dialogue_fixture(name: &str) -> Vec<u8> {
    let kangaxx = name == "hlskull.dlg";
    let count = if kangaxx { 16 } else { 10 };
    let mut resource = name.split('.').next().unwrap().to_ascii_uppercase().into_bytes();
    resource.resize(8, 0);
    let (mut states, mut transitions): (Vec<u8>, Vec<Vec<u8>>) = (Vec::new(), Vec::new());
    for index in 0..count {
        let first = transitions.len() as u32;
        let n = if kangaxx { 2 } else if index == 0 { 3 } else { 1 };
        for ordinal in 0..n {
            let target = if kangaxx && (index == 5 || index == 6) {
                8
            } else if !kangaxx && index == 5 {
                6
            } else {
                0
            };
            let mut row;
            if kangaxx && index == 10 && ordinal == 0 {
                row = u32s(&[92, 0xFFFF_FFFF, 54293, 0, 0]);
                row.extend([0; 8]);
                row.extend(u32s(&[0]));
            } else {
                row = u32s(&[0, 0xFFFF_FFFF, 0, 0, 0]);
                row.extend(&resource);
                row.extend(u32s(&[target]));
            }
            transitions.push(row);
        }
        states.extend(u32s(&[500 + index, first, n, 0xFFFF_FFFF]));
    }
    let mut data = b"DLG V1.0".to_vec();
    data.resize(0x34, 0);
    let so = data.len() as u32;
    data.extend(&states);
    let to = data.len() as u32;
    data.extend(transitions.concat());
    let action = b"SetGlobal(\"KangaxxQuest\",\"GLOBAL\",1)\r\n";
    let text = data.len() as u32;
    data.extend(action);
    let ao = data.len() as u32;
    data.extend(u32s(&[text, action.len() as u32]));
    let header = u32s(&[(states.len() / 16) as u32, so, transitions.len() as u32, to, ao, 0, ao, 0, ao, 1]);
    data[8..48].copy_from_slice(&header);
    data
}

/// `crypt_fixture(area)`: one ward block followed by five later blocks.
pub fn crypt_fixture(area: &str) -> Vec<u8> {
    let empty = "0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB\n";
    let trigger = |value: &str| format!("TR\n{value}\n{empty}TR\n");
    let action = |opcode: u32, args: &str, myself: bool| {
        let object = if myself { "0 0 0 0 0 0 0 1 0 0 0 0 \"\"OB\n" } else { empty };
        format!("AC\n{opcode}OB\n{empty}OB\n{object}OB\n{empty}{args} AC\n")
    };
    let first = format!(
        "CR\nCO\n{}{}{}CO\nRS\nRE\n100{}{}RE\nRS\nCR\n",
        trigger("112 0 0 0 0 \"\" \"\" OB"),
        trigger("16408 12 0 0 0 \"\" \"\" OB"),
        trigger(&format!("16399 0 0 0 0 \"{area}UsedCrypt\" \"\" OB")),
        action(30, &format!("1 0 0 0 0\"{area}UsedCrypt\" \"\""), false),
        action(269, "35858 0 0 0 0\"\" \"\"", true),
    );
    let later = format!(
        "CR\nCO\n{}CO\nRS\nRE\n100{}RE\nRS\nCR\n",
        trigger(&format!("16399 3 0 0 0 \"{area}UsedCrypt\" \"\" OB")),
        action(269, "123 0 0 0 0\"\" \"\"", true),
    );
    format!("SC\n{first}{}SC\n", later.repeat(5)).into_bytes()
}

fn resources() -> BTreeMap<String, Vec<u8>> {
    BTreeMap::from([
        ("hlskull.dlg".to_string(), dialogue_fixture("hlskull.dlg")),
        ("crypt526.bcs".to_string(), crypt_fixture("AR0526")),
        ("crypt202.bcs".to_string(), crypt_fixture("AR0202")),
    ])
}

#[test]
fn builders_match_the_pinned_synthetic_hashes() {
    for (name, data) in resources() {
        assert_eq!(sha(&data), pinned(SCENARIO, "quest_options.ORIGINAL_HASHES", &name), "{name}");
    }
}

#[test]
fn no_selected_options_changes_nothing() {
    let original = GameText { male: make_tlk(&["old"]), female: Some(make_tlk(&["old"])) };
    assert_eq!(prepare_quest_options(&BTreeMap::new(), &original, false, "en_us").unwrap(), (Vec::new(), original.clone()));
    assert_eq!(prepare_quest_options(&BTreeMap::new(), &original, false, "de_de").unwrap(), (Vec::new(), original.clone()));
}

/// The data the reference's Lua guard test (test_postinstall_quest_rewards)
/// feeds `FLPRScript.lua` from QUESTS; the Lua runtime part stays with the
/// reference.
#[test]
fn quest_rewards_expose_independent_selected_and_consumed_globals() {
    let expected = [
        ("blpool", "pool", "bcs:blpool", "FLPR_QPOOL", "FLPR_XPOOL", "bcs:blpool/grant/pool-reward"),
        ("troalt", "altar", "bcs:troalt", "FLPR_QALTAR", "FLPR_XALTAR", "original-source:bg2:base:item:9"),
        ("ppbook", "book", "bcs:ppbook", "FLPR_QBOOK", "FLPR_XBOOK", "original-source:bg2:base:item:109"),
    ];
    for (name, quest, resource, selected, consumed, source) in expected {
        let spec = quests().get(name).unwrap();
        let text = |key: &str| spec.get(key).and_then(Value::as_str).unwrap();
        assert_eq!((text("quest"), text("resource"), text("selected_global"), text("consumed_global")),
                   (quest, resource, selected, consumed));
        assert_ne!(text("selected_global"), text("consumed_global"));
        assert_eq!(spec.get("source_ids").and_then(Value::as_list).unwrap()[0].as_str(), Some(source));
        let bound = cir_config::quest_rewards::quest_spec(source).unwrap();
        assert!(Value::Dict(bound).py_eq(spec));
    }
}

#[cfg(feature = "parity-overrides")]
mod pinned_synthetic_resources {
    use super::*;
    use cir_config::error::ErrorKind;
    use cir_config::quest_options::{original_crypt, original_dialogue, patch_crypt, patch_hlskull, patch_kangaxx_journal};
    use cir_config::quest_rewards::blocks;
    use cir_config::resources::Tlk;
    use cir_config::translations::{self, Texts};
    use common::builders::{contains, count, parse_dialogue, replace, tlk_text, u32_at, ParsedDialogue};

    fn refs() -> BTreeMap<i64, i64> {
        (0..22).map(|index| (index, 1000 + index)).collect()
    }

    fn setup() -> BTreeMap<String, Vec<u8>> {
        common::setup(SCENARIO);
        resources()
    }

    fn trigger_of(dialogue: &ParsedDialogue, index: u32) -> &[u8] {
        &dialogue.transition_triggers[index as usize]
    }

    #[test]
    fn kangaxx_original_dialogue_choices_and_actions_remain_for_excluded_campaign() {
        let resources = setup();
        let refs = refs();
        let original = &resources["hlskull.dlg"];
        let old = parse_dialogue(original);
        let data = patch_hlskull(original, &refs).unwrap();
        let new = parse_dialogue(&data);
        assert_eq!(new.transitions[..32], old.transitions[..]);
        assert_eq!(new.actions[..old.actions.len()], old.actions[..]);
        for state in [5, 6, 10] {
            let original_row = &old.outgoing(state)[0];
            let (excluded, selected) = (&new.outgoing(state)[0], &new.outgoing(state)[1]);
            assert_eq!(
                (excluded.flags, excluded.reply, excluded.journal),
                (original_row.flags | 2, original_row.reply, original_row.journal)
            );
            assert_eq!(
                (excluded.action, &excluded.next_dialogue, excluded.next_state),
                (original_row.action, &original_row.next_dialogue, original_row.next_state)
            );
            assert_eq!(trigger_of(&new, excluded.trigger), b"!Global(\"FLPR_KANGAXX\",\"GLOBAL\",1)\r\n");
            assert_eq!(trigger_of(&new, selected.trigger), b"Global(\"FLPR_KANGAXX\",\"GLOBAL\",1)\r\n");
            if state == 5 || state == 6 {
                assert_eq!(selected.next_state, 16);
            } else {
                assert_eq!(selected.journal as i64, refs[&20]);
            }
        }
        assert_eq!(&original_dialogue("hlskull.dlg", &data).unwrap(), original);
        assert_eq!(patch_hlskull(&data, &refs).unwrap(), data);
    }

    #[test]
    fn kangaxx_new_key_dialogue_has_exact_choices_and_journal_exit() {
        let resources = setup();
        let refs = refs();
        let new = parse_dialogue(&patch_hlskull(&resources["hlskull.dlg"], &refs).unwrap());
        assert_eq!(new.states.len(), 23);
        let replies: Vec<i64> = new.outgoing(16).iter().map(|row| row.reply as i64).collect();
        assert_eq!(replies, [refs[&1], refs[&2], 56194]);
        let keys = new.outgoing(18);
        assert_eq!(keys.iter().map(|row| row.next_state).collect::<Vec<_>>(), [10, 21, 19, 20, 6]);
        assert_eq!(trigger_of(&new, keys[2].trigger), b"PartyHasItem(\"flrward1\")\r\n");
        assert_eq!(trigger_of(&new, keys[3].trigger), b"!PartyHasItem(\"flrward1\")\r\n");
        let last = &new.outgoing(22)[0];
        assert_eq!(last.flags, 92);
        assert_eq!(last.journal as i64, refs[&20]);
        assert_eq!(new.actions[last.action as usize], b"SetGlobal(\"KangaxxQuest\",\"GLOBAL\",1)\r\n");
    }

    #[test]
    fn crypt_enforces_one_ward_and_quest_only_for_selected_campaign() {
        let resources = setup();
        let refs = BTreeMap::from([(1500, 100), (1501, 101)]);
        for name in ["crypt526.bcs", "crypt202.bcs"] {
            let original = &resources[name];
            let data = patch_crypt(name, original, &refs).unwrap();
            let (new, _prefix, _suffix) = blocks(&data, 8, b"\n").unwrap();
            assert!(contains(&new[0], b"16399 1 0 0 0 \"GLOBALFLPR_KANGAXX\""));
            assert!(contains(&new[0], b"16521 2 0 0 0 \"\" \"\" OB"));
            assert!(contains(&new[0], b"16450 0 1 0 0 \"flrward1\""));
            assert!(contains(&new[0], b"16399 1 1 0 0 \"GLOBALKangaxxQuest\""));
            assert!(!contains(&new[0], b"116OB"));
            assert!(contains(&new[1], b"16450 0 0 0 0 \"flrward1\""));
            assert!(contains(&new[1], b"16399 1 0 0 0 \"GLOBALKangaxxQuest\""));
            assert_eq!(count(&new[1], b"116OB"), 1);
            assert!(contains(&new[2], b"16399 1 1 0 0 \"GLOBALFLPR_KANGAXX\""));
            assert!(!contains(&new[2], b"flrward1"));
            let (old, _, _) = blocks(original, 6, b"\n").unwrap();
            assert_eq!(new[3..], old[1..]);
            assert_eq!(&original_crypt(name, &data).unwrap(), original);
            assert_eq!(patch_crypt(name, &data, &refs).unwrap(), data);
        }
    }

    #[test]
    fn complete_prepare_uses_authored_last_ward_text_and_is_idempotent() {
        let resources = setup();
        let (output, text) = prepare_quest_options(&resources, &GameText::new(make_tlk(&["old"])), true, "en_us").unwrap();
        let mut names: Vec<&str> = output.iter().map(|(name, _)| name.as_str()).collect();
        names.sort();
        assert_eq!(names, ["crypt202.bcs", "crypt526.bcs", "flrward1.bam", "flrward1.itm", "hlskull.dlg"]);
        assert_eq!(text.female, None);
        let ward = &output.iter().find(|(name, _)| name == "flrward1.itm").unwrap().1;
        assert_eq!(&ward[0x3A..0x42], b"flrward1");
        assert_eq!(tlk_text(&text.male, u32_at(ward, 8)).as_deref(), Some("Ward Stone"));
        let description = tlk_text(&text.male, u32_at(ward, 0x54)).unwrap();
        assert!(description.starts_with("The cardinal points"), "{description}");
        let again_input: BTreeMap<String, Vec<u8>> = output.iter().cloned().collect();
        assert_eq!(prepare_quest_options(&again_input, &text, true, "en_us").unwrap(), (output, text));
    }

    /// German and Polish games get their own texts, a female protagonist her
    /// wording in dialogf.tlk; any other language gets the English ones.
    #[test]
    fn kangaxx_texts_are_the_games_language_or_english() {
        let resources = setup();
        let prepared = |language: &str| {
            let start = GameText { male: make_tlk(&["old"]), female: Some(make_tlk(&["old (f)"])) };
            let (output, text) = prepare_quest_options(&resources, &start, true, language).unwrap();
            let female = text.female.clone().unwrap();
            let count = Tlk::new(text.male.clone()).unwrap().count;
            assert_eq!(Tlk::new(female.clone()).unwrap().count, count);
            let appended: Vec<(String, String)> = (1..count)
                .map(|number| (tlk_text(&text.male, number).unwrap(), tlk_text(&female, number).unwrap()))
                .collect();
            let ward = &output.iter().find(|(name, _)| name == "flrward1.itm").unwrap().1;
            (tlk_text(&text.male, u32_at(ward, 8)).unwrap(), appended)
        };
        let lines = |language: &str| -> (Texts, Texts) {
            (translations::texts(language, "hlskull.tra").unwrap(), translations::texts(language, "in-game.tra").unwrap())
        };
        for (language, name, own) in [
            ("de_DE", Some("Bannstein"), "de_DE"),
            ("pl_PL", None, "pl_PL"),
            ("es_ES", Some("Ward Stone"), "en_US"),
            ("default", Some("Ward Stone"), "en_US"),
        ] {
            let (ward, appended) = prepared(language);
            let (dialogue, items) = lines(own);
            // Each text once (two of the conversation's replies read the same).
            let mut expected: Vec<(String, String)> = Vec::new();
            for text in (0..22).map(|key| &dialogue[&key]).chain((1500..1505).map(|key| &items[&key])) {
                if !expected.contains(text) {
                    expected.push(text.clone());
                }
            }
            assert_eq!(appended, expected, "{language}");
            assert_eq!(ward, items[&1503].0, "{language}");
            if let Some(name) = name {
                assert_eq!(ward, name);
            }
        }
        // Polish addresses a female protagonist differently in four lines.
        let polish: Vec<i64> =
            lines("pl_PL").0.iter().filter(|(_, (male, female))| male != female).map(|(key, _)| *key).collect();
        assert_eq!(polish, [4, 7, 16, 20]);
        assert!(prepared("pl_PL").1.contains(&lines("pl_PL").0[&4]));
    }

    #[test]
    fn new_kangaxx_journal_joins_existing_quest_and_rejects_conflicting_registration() {
        let resources = setup();
        let dialogue = patch_hlskull(&resources["hlskull.dlg"], &refs()).unwrap();
        let original: &[u8] =
            b"function buildQuestsTable()\r\n\tcreateQuest ( 74458 )\r\n\tcreateEntry ( 74458, -1, 54293, {}, nil )\r\nend\r\n";
        let patched = patch_kangaxx_journal(original, &dialogue).unwrap();
        assert!(contains(&patched, b"createEntry    ( 74458, -1, 1020, {}, nil )"));
        assert_eq!(patch_kangaxx_journal(&patched, &dialogue).unwrap(), patched);
        assert_eq!(replace(&patched, b"\tcreateEntry    ( 74458, -1, 1020, {}, nil )\r\n", b"", None), original);
        let conflicting = [original, b"createEntry ( 9, -1, 1020, {}, nil )\r\n"].concat();
        let error = patch_kangaxx_journal(&conflicting, &dialogue).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Profile);
        assert!(error.message.contains("another quest"), "{}", error.message);
    }

    #[test]
    fn changed_source_or_own_generated_bytes_fail_closed() {
        let resources = setup();
        let original = &resources["hlskull.dlg"];
        let dlg = patch_hlskull(original, &refs()).unwrap();
        for offset in [0x34, original.len() + 3, dlg.len() - 1] {
            let mut changed = dlg.clone();
            changed[offset] ^= 1;
            let error = original_dialogue("hlskull.dlg", &changed).unwrap_err();
            assert!(error.kind == ErrorKind::Profile || error.is_value_error(), "{offset}: {error:?}");
        }
        let changed = [&resources["crypt526.bcs"][..], b"changed"].concat();
        let error = patch_crypt("crypt526.bcs", &changed, &BTreeMap::from([(1500, 1), (1501, 2)])).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Profile);
    }
}
