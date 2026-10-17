//! Python-recorded parity cases for the `container_rewards` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_container_rewards.py` on the same synthetic resources, plus
//! the container patcher part of `tests/test_original_option_export.py`.
//!
//! The reference tests patch `container_rewards.ORIGINAL_HASHES` to the
//! synthetic resources' hashes; those tests need the `parity-overrides`
//! feature. The cached-installation test reads resources from a private
//! game cache and has no synthetic equivalent.

mod common;

use cir_config::container_rewards::{prepare_container_texts, resource_targets, target_spec, targets, trigger, SITES, TEXT_RESOURCES};
use cir_config::json::{Dict, Value};
use cir_config::py::{put_u32, splitlines};
use cir_config::quest_options::Dialogue;
use common::builders::{make_tlk, pinned, sha, tlk_text};
use std::collections::{BTreeSet, HashMap};

const SCENARIO: &str = "container_rewards";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

fn sites(resource: &str) -> (Option<usize>, &'static [usize]) {
    SITES.iter().find(|(key, _, _)| *key == resource).map(|(_, count, sites)| (*count, *sites)).unwrap()
}

fn spec(resource: &str) -> &'static Dict {
    let id = resource_targets().get(resource).and_then(Value::as_list).unwrap()[0].as_str().unwrap();
    targets().get(id).and_then(Value::as_dict).unwrap()
}

fn field<'a>(spec: &'a Dict, key: &str) -> &'a str {
    spec.get(key).and_then(Value::as_str).unwrap_or_else(|| panic!("{key}"))
}

fn tr(opcode: u32, value: i64, negate: i64, text: &str, selector: i64) -> Vec<u8> {
    trigger(opcode, value, negate, text, selector, b"\n")
}

fn action(opcode: u32, value: i64) -> Vec<u8> {
    let object = "0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB\n";
    format!("AC\n{opcode}OB\n{object}OB\n{object}OB\n{object}{value} 0 0 0 0\"\" \"\" AC\n").into_bytes()
}

/// `script_fixture(resource)`.
pub fn script_fixture(resource: &str) -> Vec<u8> {
    let spec = spec(resource);
    let (count, sites) = sites(resource);
    let mut blocks = Vec::new();
    for index in 0..count.unwrap() {
        let mut condition = tr(16399, 4, 0, "GLOBALUnrelated", 0);
        if sites.contains(&index) {
            if field(spec, "activation_global") == "FLPR_CSAMIA" {
                condition.extend(tr(16521, 2, 0, "", 0));
                condition.extend(tr(16450, 0, 0, "sw1h32", 0));
                condition.extend(tr(16450, 0, 0, "shld21", 0));
            } else if resource == "bcs:ppchest" {
                condition.extend(tr(16501, 0, 1, "bow19b", 1));
                condition.extend(tr(16399, 0, 0, "GLOBALRobbedGolem", 0));
            } else {
                let negative = resource == "bcs:safana" && index != 9;
                condition.extend(tr(16450, 0, negative as i64, &field(spec, "original_item").to_ascii_uppercase(), 0));
            }
        }
        let mut actions = action(10, 0);
        if TEXT_RESOURCES.contains(&resource) {
            actions = [action(151, 38758), actions].concat();
        }
        blocks.push([&b"CR\nCO\n"[..], &condition, b"CO\nRS\nRE\n100", &actions, b"RE\nRS\nCR\n"].concat());
    }
    [&b"SC\n"[..], &blocks.concat(), b"SC\n"].concat()
}

/// `dialogue_fixture(resource)`.
pub fn dialogue_fixture(resource: &str) -> Vec<u8> {
    let mut header = b"DLG V1.0".to_vec();
    header.resize(52, 0);
    for (index, value) in [0, 52, 0, 52, 52, 0, 52, 0, 52, 0].into_iter().enumerate() {
        put_u32(&mut header, 8 + index * 4, value);
    }
    let mut dialogue = Dialogue::new(&header, &resource[4..]).unwrap();
    for index in 0..9 {
        let mut condition = b"Global(\"Unrelated\",\"GLOBAL\",4)\r\n".to_vec();
        if sites(resource).1.contains(&index) {
            if resource == "dlg:samia" {
                condition.extend(b"  OR(2)\r\nPartyHasItem(\"sw1h32\")\r\nPartyHasItem(\"shld21\")\r\n");
            } else {
                if index != 2 {
                    condition.push(b'!');
                }
                condition.extend(b"PartyHasItem(\"BOOK03\")\r\n");
            }
        }
        dialogue.state_triggers.push(condition);
        let transition =
            dialogue.transition(None, None, "", "SetGlobal(\"QuestProgress\",\"GLOBAL\",2)", Some(9876)).unwrap();
        let state = dialogue.state(1234, vec![transition]);
        put_u32(&mut dialogue.states[state], 12, index as u32);
    }
    dialogue.render()
}

fn originals() -> Vec<(String, Vec<u8>)> {
    SITES
        .iter()
        .map(|(resource, _, _)| {
            let data = if resource.starts_with("bcs:") { script_fixture(resource) } else { dialogue_fixture(resource) };
            (resource.to_string(), data)
        })
        .collect()
}

#[test]
fn builders_match_the_pinned_synthetic_hashes() {
    for (resource, data) in originals() {
        assert_eq!(sha(&data), pinned(SCENARIO, "container_rewards.ORIGINAL_HASHES", &resource), "{resource}");
    }
}

#[derive(Clone, Copy)]
enum Condition {
    Or(usize),
    Test(bool),
}

/// `evaluate_groups`: an OR(n) consumes the following n results.
fn evaluate_groups(values: &[Condition]) -> bool {
    let (mut result, mut index) = (true, 0);
    while index < values.len() {
        match values[index] {
            Condition::Or(count) => {
                let end = (index + count + 1).min(values.len());
                result = result && values[index + 1..end].iter().any(|value| matches!(value, Condition::Test(true) | Condition::Or(1..)));
                index += count + 1;
            }
            Condition::Test(value) => {
                result = result && value;
                index += 1;
            }
        }
    }
    result
}

fn digits(data: &[u8], at: usize) -> Option<(i64, usize)> {
    let end = at + data[at.min(data.len())..].iter().take_while(|byte| byte.is_ascii_digit()).count();
    (end > at).then(|| (std::str::from_utf8(&data[at..end]).unwrap().parse().unwrap(), end))
}

/// One match of `rb'TR\n(\d+) (\d+) (\d+) 0 0 "([^"]*)" "" OB\n[^\n]*\nTR\n'`
/// at `at`; every repetition is followed by a byte it cannot match.
fn trigger_row(data: &[u8], at: usize) -> Option<((i64, i64, i64, String), usize)> {
    let rest = data.get(at..)?;
    if !rest.starts_with(b"TR\n") {
        return None;
    }
    let (opcode, end) = digits(data, at + 3)?;
    let after = |end: usize, literal: &[u8]| data.get(end..).filter(|tail| tail.starts_with(literal)).map(|_| end + literal.len());
    let end = after(end, b" ")?;
    let (value, end) = digits(data, end)?;
    let end = after(end, b" ")?;
    let (negate, end) = digits(data, end)?;
    let end = after(end, b" 0 0 \"")?;
    let close = end + data[end..].iter().position(|&byte| byte == b'"')?;
    let name = String::from_utf8_lossy(&data[end..close]).into_owned();
    let end = after(close, b"\" \"\" OB\n")?;
    let line = end + data[end..].iter().position(|&byte| byte == b'\n')?;
    let end = after(line, b"\nTR\n")?;
    Some(((opcode, value, negate, name), end))
}

/// `compiled_conditions(data, globals_, items)`.
fn compiled_conditions(data: &[u8], globals: &HashMap<String, i64>, items: &BTreeSet<String>) -> bool {
    let mut values = Vec::new();
    let mut at = 0;
    while at < data.len() {
        let Some(((opcode, value, negate, name), end)) = trigger_row(data, at) else {
            at += 1;
            continue;
        };
        at = end;
        values.push(match opcode {
            16521 => Condition::Or(value as usize),
            16399 => Condition::Test((globals.get(&name).copied().unwrap_or(0) == value) != (negate != 0)),
            16450 | 16501 => Condition::Test(items.contains(&name.to_lowercase()) != (negate != 0)),
            // Independently assert unrelated triggers are unchanged.
            _ => Condition::Test(true),
        });
    }
    evaluate_groups(&values)
}

/// `text_conditions(data, globals_, items)`.
fn text_conditions(data: &[u8], globals: &HashMap<String, i64>, items: &BTreeSet<String>) -> bool {
    let text = std::str::from_utf8(data).unwrap();
    let mut values = Vec::new();
    for line in splitlines(text) {
        let line = line.trim();
        let negated = line.starts_with('!');
        let quoted: Vec<&str> = line.split('"').collect();
        if let Some(count) = line.strip_prefix("OR(") {
            values.push(Condition::Or(count[..count.len() - 1].parse().unwrap()));
        } else if line.contains("PartyHasItem(") {
            values.push(Condition::Test(items.contains(&quoted[1].to_lowercase()) != negated));
        } else if line.starts_with("Global(") || line.starts_with("!Global(") {
            // r'"([^"]+)","([^"]+)",(\d+)' on Global("name","scope",value)
            let (name, scope) = (quoted[1], quoted[3]);
            let value: i64 = quoted[4].trim_start_matches(',').split(')').next().unwrap().parse().unwrap();
            let result = globals.get(&format!("{scope}{name}")).copied().unwrap_or(0) == value;
            values.push(Condition::Test(result != negated));
        } else {
            values.push(Condition::Test(true));
        }
    }
    evaluate_groups(&values)
}

#[test]
fn condition_evaluators_follow_the_reference() {
    let globals = HashMap::from([("GLOBALA".to_string(), 1)]);
    let items = BTreeSet::from(["book03".to_string()]);
    let script = [tr(16399, 1, 0, "GLOBALA", 0), tr(16521, 2, 0, "", 0), tr(16450, 0, 0, "x", 0), tr(16450, 0, 0, "BOOK03", 0)].concat();
    assert!(compiled_conditions(&script, &globals, &items));
    assert!(!compiled_conditions(&[tr(16399, 1, 1, "GLOBALA", 0)].concat(), &globals, &items));
    assert!(text_conditions(b"Global(\"A\",\"GLOBAL\",1)\r\n  OR(2)\r\n!PartyHasItem(\"BOOK03\")\r\nTrue()\r\n", &globals, &items));
    assert!(!text_conditions(b"!Global(\"A\",\"GLOBAL\",1)\r\n", &globals, &items));
}

#[test]
fn exact_target_selectors_shared_samia_latch_and_golem_text() {
    let text = |id: &str, key: &str| field(&target_spec(id).unwrap(), key).to_string();
    assert_eq!(text("bg2:mode2:target:13", "physical_target_id"), "are:ar1303/container/0");
    let (one, two) = (target_spec("bg2:mode2:target:53").unwrap(), target_spec("bg2:mode2:target:28").unwrap());
    assert_eq!((field(&one, "original_item"), field(&two, "original_item")), ("shld21", "sw1h32"));
    assert_eq!(field(&one, "physical_target_id"), "are:ar1202/container/8");
    for key in ["physical_target_id", "activation_global", "latch_global", "latch_scope"] {
        assert_eq!(field(&one, key), field(&two, key));
    }
    assert_eq!(text("bg1:mode2:target:22", "area"), "bg3601");
    let original = make_tlk(&["Preserve existing text"]);
    let (tlk, refs) = prepare_container_texts(&original, TEXT_RESOURCES, "en_us").unwrap();
    assert_eq!(refs.values().map(|row| row[&38758]).collect::<BTreeSet<_>>().len(), 1);
    assert_eq!(
        tlk_text(&tlk, refs["bcs:kpgol01"][&38758] as u32).as_deref(),
        Some("As you pick up the item, two of the golems come to life and attack.")
    );
    assert_eq!(prepare_container_texts(&tlk, TEXT_RESOURCES, "en_us").unwrap(), (tlk, refs));
}

#[cfg(feature = "parity-overrides")]
mod pinned_synthetic_resources {
    use super::*;
    use cir_config::container_rewards::{original_container_resource, patch_container_resource};
    use cir_config::error::ErrorKind;
    use cir_config::quest_rewards::{blocks, global_guard, newline};
    use common::builders::{replace, split};
    use std::collections::BTreeMap;

    fn patched(resource: &str, original: &[u8]) -> Vec<u8> {
        common::setup(SCENARIO);
        let result = patch_container_resource(resource, original, Some(&BTreeMap::from([(38758, 100001)]))).unwrap();
        assert_eq!(patch_container_resource(resource, &result, None).unwrap(), result);
        assert_eq!(original_container_resource(resource, &result).unwrap(), original);
        let renamed = replace(&result, b"FLPR_C", b"FLPR_Z", Some(1));
        assert_eq!(patch_container_resource(resource, &renamed, None).unwrap_err().kind, ErrorKind::Profile);
        let appended = [&result[..], b"tamper"].concat();
        assert_eq!(patch_container_resource(resource, &appended, None).unwrap_err().kind, ErrorKind::Profile);
        result
    }

    fn combinations() -> Vec<(bool, bool, bool)> {
        // itertools.product((False, True), repeat=3)
        (0..8).map(|bits| (bits & 4 != 0, bits & 2 != 0, bits & 1 != 0)).collect()
    }

    fn state(spec: &Dict, active: bool, taken: bool) -> HashMap<String, i64> {
        HashMap::from([
            ("GLOBALUnrelated".to_string(), 4),
            (format!("GLOBAL{}", field(spec, "activation_global")), active as i64),
            (format!("{}{}", field(spec, "latch_scope"), field(spec, "latch_global")), taken as i64),
        ])
    }

    fn check_script(resource: &str, original: &[u8], result: &[u8]) {
        let (count, sites) = sites(resource);
        let count = count.unwrap();
        let old = blocks(original, count, newline(original).unwrap()).unwrap().0;
        let extra = if resource == "bcs:ppchest" { 0 } else { sites.len() };
        let new = blocks(result, count + extra, newline(result).unwrap()).unwrap().0;
        let spec = spec(resource);
        let mut cursor = 0;
        for (index, before) in old.iter().enumerate() {
            if !sites.contains(&index) {
                assert_eq!(&new[cursor], before, "{resource} block {index}");
                cursor += 1;
                continue;
            }
            let mut selected = None;
            if resource != "bcs:ppchest" {
                let block = &new[cursor];
                cursor += 1;
                let mut response = split(block, b"CO\n")[2].to_vec();
                if TEXT_RESOURCES.contains(&resource) {
                    response = replace(&response, b"100001 0 0 0 0\"\"", b"38758 0 0 0 0\"\"", None);
                }
                assert_eq!(response, split(before, b"CO\n")[2]);
                selected = Some(block);
            }
            let excluded = &new[cursor];
            cursor += 1;
            let guard = global_guard(field(spec, "activation_global"), false, b"\n").unwrap();
            assert_eq!(&replace(excluded, &guard, b"", None), before);
            for (active, taken, owns) in combinations() {
                let mut globals = state(spec, active, taken);
                // Satisfy original independent Samia progression requirements.
                if resource == "bcs:samia" {
                    globals.insert("AR1202SpawnSamia".into(), 1);
                }
                if resource == "bcs:safana" {
                    globals.insert("GLOBALPirateTreasure".into(), (index == 8) as i64);
                }
                let items: BTreeSet<String> =
                    if owns { BTreeSet::from([field(spec, "original_item").to_string()]) } else { BTreeSet::new() };
                let positive = !(resource == "bcs:safana" && (index == 7 || index == 8));
                if let Some(selected) = selected {
                    assert_eq!(
                        compiled_conditions(split(selected, b"CO\n")[1], &globals, &items),
                        active && (taken == positive),
                        "{resource} {index} selected {active} {taken} {owns}"
                    );
                }
                assert_eq!(
                    compiled_conditions(split(excluded, b"CO\n")[1], &globals, &items),
                    !active && compiled_conditions(split(before, b"CO\n")[1], &globals, &items),
                    "{resource} {index} excluded {active} {taken} {owns}"
                );
            }
        }
        assert_eq!(cursor, new.len());
    }

    #[test]
    fn compiled_routes_preserve_actions_and_ignore_later_item_loss() {
        for (resource, original) in originals() {
            if resource.starts_with("bcs:") {
                check_script(&resource, &original, &patched(&resource, &original));
            }
        }
    }

    fn check_dialogue(resource: &str, original: &[u8], result: &[u8]) {
        let before = Dialogue::new(original, &resource[4..]).unwrap();
        let after = Dialogue::new(result, &resource[4..]).unwrap();
        assert_eq!(before.states, after.states);
        assert_eq!(before.transitions, after.transitions);
        assert_eq!(before.actions, after.actions);
        assert_eq!(before.triggers, after.triggers);
        let spec = spec(resource);
        for (index, condition) in before.state_triggers.iter().enumerate() {
            if !sites(resource).1.contains(&index) {
                assert_eq!(&after.state_triggers[index], condition);
                continue;
            }
            for (active, taken, owns) in combinations() {
                let mut globals = state(spec, active, taken);
                if resource == "dlg:safanj" {
                    globals.insert("GLOBALPirateTreasure".into(), (index == 8) as i64);
                }
                let items: BTreeSet<String> =
                    if owns { BTreeSet::from([field(spec, "original_item").to_string()]) } else { BTreeSet::new() };
                let positive = resource == "dlg:samia" || index == 2;
                let expected = if active { taken == positive } else { text_conditions(condition, &globals, &items) };
                assert_eq!(
                    text_conditions(&after.state_triggers[index], &globals, &items),
                    expected,
                    "{resource} {index} {active} {taken} {owns}"
                );
            }
        }
    }

    #[test]
    fn dialogue_truth_table_and_quest_journal_actions_are_preserved() {
        for resource in ["dlg:samia", "dlg:safanj"] {
            let original = dialogue_fixture(resource);
            check_dialogue(resource, &original, &patched(resource, &original));
        }
    }

    /// The patcher part of test_original_option_export's
    /// test_ppchest_container_latch_survives_export_staging_and_rescan.
    #[test]
    fn ppchest_latch_is_skipped_only_by_the_selected_campaign() {
        let original = script_fixture("bcs:ppchest");
        let changed = patched("bcs:ppchest", &original);
        let old_block = &blocks(&original, 1, newline(&original).unwrap()).unwrap().0[0];
        let new_block = &blocks(&changed, 1, newline(&changed).unwrap()).unwrap().0[0];
        assert_eq!(split(new_block, b"CO\n")[2], split(old_block, b"CO\n")[2]);
        for selected in 0..3 {
            let globals = HashMap::from([
                ("GLOBALUnrelated".to_string(), 4),
                ("GLOBALRobbedGolem".to_string(), 0),
                ("GLOBALFLPR_CPPCHST".to_string(), selected),
            ]);
            assert!(compiled_conditions(split(old_block, b"CO\n")[1], &globals, &BTreeSet::new()));
            assert_eq!(compiled_conditions(split(new_block, b"CO\n")[1], &globals, &BTreeSet::new()), selected != 1);
        }
        assert!(common::builders::contains(&changed, b"FLPR_CPPCHST"));
    }
}
