//! Python-recorded parity cases for the `container_rewards` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_container_rewards.py` on the same synthetic resources, plus
//! the container patcher part of `tests/test_original_option_export.py`.
//!
//! The reviewed EET resources are read from the private installed-resource
//! cache under `work/` when it is there, as the reference test does.

mod common;

use cir_config::container_rewards::{
    own_patch, patch_container_resource, prepare_container_texts, recover, resource_targets, route_reasons, target_spec, targets,
    treasure, treasure_area, treasure_container, trigger, TEXT_RESOURCES,
};
use cir_config::error::{Error, ErrorKind};
use cir_config::game_text::GameText;
use cir_config::json::{Dict, Value};
use cir_config::py::{put_u32, splitlines, str_value};
use cir_config::quest_options::Dialogue;
use cir_config::quest_rewards::{blocks, global_guard, newline};
use cir_config::script_text::script_blocks;
use common::builders::{contains, make_tlk, replace, sha, split, tlk_text, u32_at};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;

const SCENARIO: &str = "container_rewards";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// `FIXTURE_SITES`: where the reviewed EET resources test the treasure,
/// (blocks, sites) of each script and (None, conditions) of each dialogue.
/// The fixtures copy them; the adapter finds the sites by meaning.
const FIXTURE_SITES: &[(&str, Option<usize>, &[usize])] = &[
    ("bcs:kpgol01", Some(1), &[0]),
    ("bcs:kpgol02", Some(1), &[0]),
    ("bcs:kpgol03", Some(1), &[0]),
    ("bcs:kpgol04", Some(1), &[0]),
    ("bcs:kpgol05", Some(1), &[0]),
    ("bcs:kpgol06", Some(1), &[0]),
    ("bcs:ar1202", Some(24), &[15, 16]),
    ("bcs:samia", Some(1), &[0]),
    ("bcs:safana", Some(16), &[7, 8, 9]),
    ("bcs:ppchest", Some(1), &[0]),
    ("dlg:samia", None, &[1]),
    ("dlg:safanj", None, &[0, 2, 8]),
];

fn sites(resource: &str) -> (Option<usize>, &'static [usize]) {
    FIXTURE_SITES.iter().find(|(key, _, _)| *key == resource).map(|(_, count, sites)| (*count, *sites)).unwrap()
}

fn spec(resource: &str) -> &'static Dict {
    let id = resource_targets().get(resource).and_then(Value::as_list).unwrap()[0].as_str().unwrap();
    targets().get(id).and_then(Value::as_dict).unwrap()
}

fn target_ids(resource: &str) -> BTreeSet<String> {
    resource_targets().get(resource).and_then(Value::as_list).unwrap().iter().map(str_value).collect()
}

fn field<'a>(spec: &'a Dict, key: &str) -> &'a str {
    spec.get(key).and_then(Value::as_str).unwrap_or_else(|| panic!("{key}"))
}

fn assert_profile(error: Error, needle: &str) {
    assert_eq!(error.kind, ErrorKind::Profile, "{error:?}");
    assert!(error.message.contains(needle), "{needle}: {}", error.message);
}

fn reasons(resource: &str, data: &[u8]) -> BTreeMap<String, String> {
    route_reasons(resource, data).unwrap().iter().map(|(target, reason)| (target.to_string(), str_value(reason))).collect()
}

fn crlf(data: impl AsRef<[u8]>) -> Vec<u8> {
    replace(data.as_ref(), b"\n", b"\r\n", None)
}

/// `rewards._trigger(opcode, value, negate, text, selector)` with LF.
fn tr(opcode: u32, value: i64, negate: i64, text: &str, selector: i64) -> Vec<u8> {
    trigger(opcode, value, negate, text, selector, b"\n")
}

/// `action(opcode, value=0, text="")`.
fn action(opcode: u32, value: i64, text: &str) -> Vec<u8> {
    let object = "0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB\n";
    format!("AC\n{opcode}OB\n{object}OB\n{object}OB\n{object}{value} 0 0 0 0\"{text}\" \"\" AC\n").into_bytes()
}

/// `site_condition(resource, index)`.
fn site_condition(resource: &str, index: usize) -> Vec<u8> {
    let spec = spec(resource);
    if field(spec, "activation_global") == "FLPR_CSAMIA" {
        return [tr(16521, 2, 0, "", 0), tr(16450, 0, 0, "sw1h32", 0), tr(16450, 0, 0, "shld21", 0)].concat();
    }
    if resource == "bcs:ppchest" {
        return [tr(16501, 0, 1, "bow19b", 1), tr(16399, 0, 0, "GLOBALRobbedGolem", 0)].concat();
    }
    let negative = resource == "bcs:safana" && index != 9;
    tr(16450, 0, negative as i64, &field(spec, "original_item").to_ascii_uppercase(), 0)
}

/// `script_block(condition, actions)`: an unrelated trigger first.
fn script_block(condition: &[u8], actions: &[u8]) -> Vec<u8> {
    [&b"CR\nCO\n"[..], &tr(16399, 4, 0, "GLOBALUnrelated", 0), condition, b"CO\nRS\nRE\n100", actions, b"RE\nRS\nCR\n"].concat()
}

/// `site_actions(resource)`.
fn site_actions(resource: &str) -> Vec<u8> {
    if resource == "bcs:ppchest" {
        // PPCHEST sets the latch itself when the chest no longer holds the bow.
        return action(30, 1, "GLOBALRobbedGolem");
    }
    let text = if TEXT_RESOURCES.contains(&resource) { action(151, 38758, "") } else { Vec::new() };
    [text, action(10, 0, "")].concat()
}

/// `script_fixture(resource)`.
pub fn script_fixture(resource: &str) -> Vec<u8> {
    let (count, sites) = sites(resource);
    let blocks: Vec<Vec<u8>> = (0..count.unwrap())
        .map(|index| {
            if sites.contains(&index) {
                script_block(&site_condition(resource, index), &site_actions(resource))
            } else {
                script_block(b"", &action(10, 0, ""))
            }
        })
        .collect();
    [&b"SC\n"[..], &blocks.concat(), b"SC\n"].concat()
}

/// `dialogue_fixture(resource, conditions)`: nine states; `conditions`
/// replaces the state conditions by index.
pub fn dialogue_fixture(resource: &str, conditions: &[(usize, &[u8])]) -> Vec<u8> {
    dialogue_parts(resource, conditions).render()
}

fn dialogue_parts(resource: &str, conditions: &[(usize, &[u8])]) -> Dialogue {
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
        let replaced = conditions.iter().find(|(at, _)| *at == index).map(|(_, text)| text.to_vec());
        dialogue.state_triggers.push(replaced.unwrap_or(condition));
        let transition =
            dialogue.transition(None, None, "", "SetGlobal(\"QuestProgress\",\"GLOBAL\",2)", Some(9876)).unwrap();
        let state = dialogue.state(1234, vec![transition]);
        put_u32(&mut dialogue.states[state], 12, index as u32);
    }
    dialogue
}

fn originals() -> Vec<(String, Vec<u8>)> {
    FIXTURE_SITES
        .iter()
        .map(|(resource, _, _)| {
            let data = if resource.starts_with("bcs:") { script_fixture(resource) } else { dialogue_fixture(resource, &[]) };
            (resource.to_string(), data)
        })
        .collect()
}

/// The SAFANJ of test_dialogue_conditions_are_found_by_meaning_in_another_layout,
/// and the index of the reply another mod added.
fn safanj_layout() -> (Vec<u8>, usize) {
    let conditions: [(usize, &[u8]); 3] = [
        (0, b"!PartyHasItem(\"book03\")\nGlobal(\"Unrelated\",\"GLOBAL\",4)"),
        (2, b"Global(\"Unrelated\",\"GLOBAL\",4)\nPartyHasItem(\"Book03\")"),
        (8, b"Global(\"Unrelated\",\"GLOBAL\",4)\n  !PartyHasItem(\"BOOK03\")"),
    ];
    let mut dialogue = Dialogue::new(&dialogue_fixture("dlg:safanj", &conditions), "safanj").unwrap();
    dialogue.triggers.push(b"PartyHasItem(\"book03\")\nGlobal(\"Unrelated\",\"GLOBAL\",4)".to_vec());
    let mut reply = dialogue.transition(None, None, "", "SetGlobal(\"OtherMod\",\"GLOBAL\",2)", None).unwrap();
    let flags = u32_at(&reply, 0) | 2;
    put_u32(&mut reply, 0, flags);
    let trigger_index = dialogue.triggers.len() - 1;
    put_u32(&mut reply, 12, trigger_index as u32);
    dialogue.state(1235, vec![reply]);
    (dialogue.render(), trigger_index)
}

fn golem_layout() -> Vec<u8> {
    let added = script_block(&tr(16399, 1, 0, "GLOBALOtherModGolems", 0), &action(30, 2, "GLOBALOtherMod"));
    let golem = script_block(&tr(16450, 0, 0, "Ax1h13", 0), &[action(151, 38758, ""), action(10, 0, "")].concat());
    crlf([&b"SC\n"[..], &added, &added, &golem, b"SC\n"].concat())
}

fn samia_layout() -> Vec<u8> {
    let condition = [tr(16521, 2, 0, "", 0), tr(16450, 0, 0, "SHLD21", 0), tr(16450, 0, 0, "sw1h32", 0)].concat();
    crlf([&b"SC\n"[..], &script_block(&condition, &action(10, 0, "")), b"SC\n"].concat())
}

/// The resources of test_routes_the_check_cannot_recognise_name_their_reason,
/// by the generator's labels, and the reason each gives.
fn unrecognised() -> Vec<(&'static str, &'static str, Vec<u8>, String)> {
    let scripts = |made: &[Vec<u8>]| [&b"SC\n"[..], &made.concat(), b"SC\n"].concat();
    let golem = [action(151, 38758, ""), action(10, 0, "")].concat();
    let unrecognised = |items: &str| format!("tests {items} in a way the container route does not recognise");
    let shared = "their shared latch records only that one of them was taken";
    let other = tr(16399, 1, 0, "GLOBALOther", 0);
    vec![
        ("bcs:kpgol01", "no block", scripts(&[script_block(b"", &golem)]), "KPGOL01.BCS has no block that tests AX1H13".into()),
        (
            "bcs:kpgol01",
            "no text",
            scripts(&[script_block(&tr(16450, 0, 0, "ax1h13", 0), &action(10, 0, ""))]),
            "KPGOL01.BCS no longer shows the golem's text that names the treasure (38758)".into(),
        ),
        (
            "bcs:kpgol01",
            "has item",
            scripts(&[script_block(&tr(16481, 0, 0, "ax1h13", 2), &golem)]),
            "KPGOL01.BCS block 0 tests AX1H13 with HasItem(), which the route cannot replace by the container's latch".into(),
        ),
        (
            "bcs:kpgol01",
            "or count",
            scripts(&[script_block(&[tr(16521, 3, 0, "", 0), tr(16450, 0, 0, "ax1h13", 0), other.clone()].concat(), &golem)]),
            "KPGOL01.BCS block 0 ends in an OR() group that counts more conditions than follow it, \
             so a condition added after it would join it"
                .into(),
        ),
        (
            "bcs:kpgol01",
            "nested or",
            scripts(&[script_block(
                &[tr(16521, 2, 0, "", 0), tr(16521, 1, 0, "", 0), tr(16450, 0, 0, "ax1h13", 0), other.clone()].concat(),
                &golem,
            )]),
            "KPGOL01.BCS block 0 tests AX1H13 in an OR() group that is negated or holds another OR(), \
             which the route cannot read"
                .into(),
        ),
        (
            "bcs:ppchest",
            "no latch",
            scripts(&[script_block(&tr(16501, 0, 1, "bow19b", 1), &action(10, 0, ""))]),
            "PPCHEST.BCS block 0 tests BOW19B with Contains(), which the route cannot replace by the container's latch".into(),
        ),
        (
            "bcs:samia",
            "or other",
            scripts(&[script_block(
                &[tr(16521, 2, 0, "", 0), tr(16450, 0, 0, "shld21", 0), other.clone()].concat(),
                &action(10, 0, ""),
            )]),
            format!("SAMIA.BCS block 0 does not test SHLD21/SW1H32 together in one OR() group; {shared}"),
        ),
        (
            "bcs:samia",
            "separate",
            scripts(&[script_block(&[tr(16450, 0, 0, "shld21", 0), tr(16450, 0, 0, "sw1h32", 0)].concat(), &action(10, 0, ""))]),
            format!("SAMIA.BCS block 0 does not test SHLD21/SW1H32 together in one OR() group; {shared}"),
        ),
        (
            "bcs:samia",
            "lacks one",
            scripts(&[script_block(
                &[tr(16521, 2, 0, "", 0), tr(16450, 0, 1, "shld21", 0), tr(16450, 0, 0, "sw1h32", 0)].concat(),
                &action(10, 0, ""),
            )]),
            format!("SAMIA.BCS block 0 tests whether the party lacks one of SHLD21/SW1H32; {shared}"),
        ),
        (
            "bcs:kpgol01",
            "open",
            b"SC\nCR\nCO\nSC\n".to_vec(),
            "KPGOL01.BCS cannot be read as a compiled script: its last block is not closed".into(),
        ),
        (
            "dlg:samia",
            "no condition",
            dialogue_fixture("dlg:samia", &[(1, b"Global(\"Unrelated\",\"GLOBAL\",4)\r\n")]),
            "SAMIA.DLG has no condition that tests SHLD21/SW1H32".into(),
        ),
        (
            "dlg:samia",
            "separate",
            dialogue_fixture("dlg:samia", &[(1, b"PartyHasItem(\"shld21\")\r\nPartyHasItem(\"sw1h32\")\r\n")]),
            format!("SAMIA.DLG condition 1 does not test SHLD21/SW1H32 together in one OR() group; {shared}"),
        ),
        (
            "dlg:safanj",
            "has item",
            dialogue_fixture("dlg:safanj", &[(2, b"HasItem(\"book03\",Myself)\r\n")]),
            "SAFANJ.DLG condition 2 tests BOOK03 with HasItem(), which the route cannot replace by the container's latch".into(),
        ),
        (
            "dlg:safanj",
            "object",
            dialogue_fixture("dlg:safanj", &[(2, b"PartyHasItem(\"book03\",Myself)\r\n")]),
            format!("SAFANJ.DLG condition 2 {}", unrecognised("BOOK03")),
        ),
        (
            "dlg:safanj",
            "or three",
            dialogue_fixture("dlg:safanj", &[(2, b"OR(3)\r\nPartyHasItem(\"book03\")\r\nGlobal(\"X\",\"GLOBAL\",1)\r\n")]),
            "SAFANJ.DLG condition 2 tests BOOK03 in an OR() group that counts more conditions than follow it".into(),
        ),
        (
            "dlg:safanj",
            "or unreadable",
            dialogue_fixture("dlg:safanj", &[(2, b"OR(X)\r\nPartyHasItem(\"book03\")\r\nGlobal(\"X\",\"GLOBAL\",1)\r\n")]),
            "SAFANJ.DLG condition 2 has an OR() whose count the route cannot read".into(),
        ),
        ("dlg:safanj", "broken", [b"DLG V1.0".as_slice(), &[0; 10]].concat(), "SAFANJ.DLG cannot be read as a dialogue".into()),
    ]
}

#[test]
fn builders_match_the_reference_builders() {
    let fixture = common::load(SCENARIO);
    for (resource, data) in originals() {
        assert_eq!(fixture.input(&format!("patch_container_resource patch {resource}"), "data"), data, "{resource}");
    }
    assert_eq!(fixture.input("patch_container_resource layout kpgol01 crlf", "data"), golem_layout());
    assert_eq!(fixture.input("patch_container_resource layout samia crlf", "data"), samia_layout());
    assert_eq!(fixture.input("patch_container_resource layout safanj", "data"), safanj_layout().0);
    for (resource, label, data, _) in unrecognised() {
        assert_eq!(fixture.input(&format!("own_patch reasons {label} {resource}"), "data"), data, "{label}");
    }
    let crlf_fixture = common::load("container_rewards_crlf");
    for (resource, data) in originals() {
        if resource.starts_with("bcs:") {
            assert_eq!(crlf_fixture.input(&format!("patch_container_resource patch {resource}"), "data"), crlf(&data), "{resource}");
        }
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

/// One match of `rb'TR\r?\n(\d+) (\d+) (\d+) 0 0 "([^"]*)" "" OB\r?\n[^\n]*\nTR\r?\n'`
/// at `at`; every repetition is followed by a byte it cannot match.
fn trigger_row(data: &[u8], at: usize) -> Option<((i64, i64, i64, String), usize)> {
    let after = |end: usize, literal: &[u8]| data.get(end..).filter(|tail| tail.starts_with(literal)).map(|_| end + literal.len());
    let line_end = |end: usize| after(end, b"\r\n").or_else(|| after(end, b"\n"));
    let end = line_end(after(at, b"TR")?)?;
    let (opcode, end) = digits(data, end)?;
    let end = after(end, b" ")?;
    let (value, end) = digits(data, end)?;
    let end = after(end, b" ")?;
    let (negate, end) = digits(data, end)?;
    let end = after(end, b" 0 0 \"")?;
    let close = end + data[end..].iter().position(|&byte| byte == b'"')?;
    let name = String::from_utf8_lossy(&data[end..close]).into_owned();
    let end = line_end(after(close, b"\" \"\" OB")?)?;
    let line = end + data[end..].iter().position(|&byte| byte == b'\n')?;
    let end = line_end(after(line, b"\nTR")?)?;
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
        } else if !line.is_empty() {
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
    assert!(compiled_conditions(&crlf(&script), &globals, &items));
    assert!(!compiled_conditions(&tr(16399, 1, 1, "GLOBALA", 0), &globals, &items));
    assert!(!compiled_conditions(&crlf(tr(16399, 1, 1, "GLOBALA", 0)), &globals, &items));
    assert!(text_conditions(b"Global(\"A\",\"GLOBAL\",1)\r\n  OR(2)\r\n!PartyHasItem(\"BOOK03\")\r\nTrue()\r\n", &globals, &items));
    assert!(!text_conditions(b"!Global(\"A\",\"GLOBAL\",1)\r\n", &globals, &items));
    // Blank lines are no condition: OR(1) here takes the item test.
    assert!(!text_conditions(b"OR(1)\r\n\r\n!PartyHasItem(\"BOOK03\")\r\n", &globals, &items));
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

fn owned(spec: &Dict, owns: bool) -> BTreeSet<String> {
    if owns {
        BTreeSet::from([field(spec, "original_item").to_string()])
    } else {
        BTreeSet::new()
    }
}

/// `patched(resource, original)`: the patch, its re-export and recovery,
/// and what the check reads from both and from changed copies.
fn patched(resource: &str, original: &[u8]) -> Vec<u8> {
    let reference = 100001;
    let targets = target_ids(resource);
    let result = patch_container_resource(resource, original, Some(&BTreeMap::from([(38758, reference)]))).unwrap();
    assert_eq!(patch_container_resource(resource, &result, None).unwrap(), result);
    let golem = TEXT_RESOURCES.contains(&resource).then_some(reference);
    assert_eq!(recover(resource, &result).unwrap(), (original.to_vec(), golem, true));
    assert!(!own_patch(resource, original).unwrap());
    assert!(own_patch(resource, &result).unwrap());
    assert!(reasons(resource, original).is_empty());
    assert!(reasons(resource, &result).is_empty());
    // Changes that keep our marker but not our patch: a selected copy where
    // the excluded one was, a changed recovery record, bytes after the end.
    let changed = if resource.starts_with("bcs:") {
        let flag = field(spec(resource), "activation_global");
        let line_end = newline(&result).unwrap();
        replace(&result, &global_guard(flag, false, line_end).unwrap(), &global_guard(flag, true, line_end).unwrap(), Some(1))
    } else {
        replace(&result, b"FLPR_C", b"FLPR_Z", Some(1))
    };
    for tampered in [changed, [&result[..], b"tamper"].concat()] {
        assert_profile(
            patch_container_resource(resource, &tampered, None).unwrap_err(),
            "carries a changed copy of the container route's own patch",
        );
        let found = reasons(resource, &tampered);
        assert_eq!(found.keys().cloned().collect::<BTreeSet<_>>(), targets);
        assert!(found.values().all(|reason| reason.contains("changed copy")));
    }
    result
}

/// `check_script(resource, original, result, sites)`.
fn check_script(resource: &str, original: &[u8], result: &[u8], gated: Option<&[usize]>) {
    let reference = 100001;
    let sites = gated.unwrap_or(sites(resource).1);
    let line_end = newline(original).unwrap();
    let boundary = [b"CO".as_slice(), line_end].concat();
    let heads = common::builders::count(original, &[b"CR".as_slice(), line_end, b"CO", line_end].concat());
    let count = blocks(original, heads, line_end).unwrap().0.len();
    let old = blocks(original, count, line_end).unwrap().0;
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
            let mut response = split(block, &boundary)[2].to_vec();
            if TEXT_RESOURCES.contains(&resource) {
                response = replace(&response, format!("{reference} 0 0 0 0\"\"").as_bytes(), b"38758 0 0 0 0\"\"", None);
            }
            assert_eq!(response, split(before, &boundary)[2]);
            selected = Some(block);
        }
        let excluded = &new[cursor];
        cursor += 1;
        let guard = global_guard(field(spec, "activation_global"), false, line_end).unwrap();
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
            let items = owned(spec, owns);
            let positive = !(resource == "bcs:safana" && (index == 7 || index == 8));
            if let Some(selected) = selected {
                assert_eq!(
                    compiled_conditions(split(selected, &boundary)[1], &globals, &items),
                    active && (taken == positive),
                    "{resource} {index} selected {active} {taken} {owns}"
                );
            }
            assert_eq!(
                compiled_conditions(split(excluded, &boundary)[1], &globals, &items),
                !active && compiled_conditions(split(before, &boundary)[1], &globals, &items),
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
            check_script(&resource, &original, &patched(&resource, &original), None);
        }
    }
}

#[test]
fn script_sites_are_found_by_meaning_in_another_layout() {
    // CRLF, two blocks another mod added before the golem block, the item
    // named in another letter case; Samia's two items in the other order.
    let original = golem_layout();
    let result = patched("bcs:kpgol01", &original);
    check_script("bcs:kpgol01", &original, &result, Some(&[2]));
    let samia = samia_layout();
    check_script("bcs:samia", &samia, &patched("bcs:samia", &samia), None);
}

/// `check_dialogue(resource, original, result, sites, reply_sites)`.
fn check_dialogue(resource: &str, original: &[u8], result: &[u8], reply_sites: &[usize]) {
    let before = Dialogue::new(original, &resource[4..]).unwrap();
    let after = Dialogue::new(result, &resource[4..]).unwrap();
    assert_eq!(before.states, after.states);
    assert_eq!(before.transitions, after.transitions);
    assert_eq!(before.actions, after.actions);
    let spec = spec(resource);
    for (table, changed, places) in
        [(&before.state_triggers, &after.state_triggers, sites(resource).1), (&before.triggers, &after.triggers, reply_sites)]
    {
        assert_eq!(changed.len(), table.len());
        for (index, condition) in table.iter().enumerate() {
            if !places.contains(&index) {
                assert_eq!(&changed[index], condition);
                continue;
            }
            for (active, taken, owns) in combinations() {
                let mut globals = state(spec, active, taken);
                if resource == "dlg:safanj" {
                    globals.insert("GLOBALPirateTreasure".into(), (index == 8) as i64);
                }
                let items = owned(spec, owns);
                let positive = resource == "dlg:samia" || !contains(condition, b"!PartyHasItem");
                let expected = if active { taken == positive } else { text_conditions(condition, &globals, &items) };
                assert_eq!(
                    text_conditions(&changed[index], &globals, &items),
                    expected,
                    "{resource} {index} {active} {taken} {owns}"
                );
            }
        }
    }
}

#[test]
fn dialogue_truth_table_and_quest_journal_actions_are_preserved() {
    for resource in ["dlg:samia", "dlg:safanj"] {
        let original = dialogue_fixture(resource, &[]);
        check_dialogue(resource, &original, &patched(resource, &original), &[]);
    }
}

#[test]
fn dialogue_conditions_are_found_by_meaning_in_another_layout() {
    // Beamdog's compiled text: LF between lines and none after the last, the
    // treasure test first or last; another mod's reply that tests it too.
    let (original, reply) = safanj_layout();
    let result = patched("dlg:safanj", &original);
    check_dialogue("dlg:safanj", &original, &result, &[reply]);
    let after = Dialogue::new(&result, "safanj").unwrap();
    assert!(after.state_triggers[0].ends_with(b"\nGlobal(\"Unrelated\",\"GLOBAL\",4)"));
    assert!(after.triggers.last().unwrap().ends_with(b"\nGlobal(\"Unrelated\",\"GLOBAL\",4)"));
}

/// `product((False, True), repeat=4)`.
fn combinations4() -> Vec<(bool, bool, bool, bool)> {
    (0..16).map(|bits| (bits & 8 != 0, bits & 4 != 0, bits & 2 != 0, bits & 1 != 0)).collect()
}

/// The globals of test_treasure_tests_inside_or_groups_and_repeated_golem_texts.
fn or_state(spec: &Dict, active: bool, taken: bool, other: bool) -> HashMap<String, i64> {
    HashMap::from([
        (format!("GLOBAL{}", field(spec, "activation_global")), active as i64),
        ("GLOBALOther".to_string(), other as i64),
        (format!("{}{}", field(spec, "latch_scope"), field(spec, "latch_global")), taken as i64),
    ])
}

fn treasure_set(spec: &Dict) -> BTreeSet<String> {
    treasure(field(spec, "target_id")).unwrap().into_iter().collect()
}

#[test]
fn treasure_tests_inside_or_groups_and_repeated_golem_texts() {
    // Another mod's OR() groups around the treasure tests, and a golem
    // block whose two alternative responses both show the golem's text.
    let fixture = common::load(SCENARIO);
    let other = tr(16399, 1, 0, "GLOBALOther", 0);
    let shown = action(151, 38758, "");
    let scripts = [
        (
            "bcs:kpgol01",
            "or golem responses",
            [tr(16521, 2, 0, "", 0), tr(16450, 0, 0, "ax1h13", 0), other.clone()].concat(),
            [&b"RE\n50"[..], &shown, &action(10, 0, ""), b"RE\nRE\n50", &shown, b"RE\n"].concat(),
        ),
        (
            "bcs:kpgol01",
            "or after negated",
            [tr(16450, 0, 1, "ax1h13", 0), tr(16521, 2, 0, "", 0), other.clone(), tr(16450, 0, 0, "AX1H13", 0)].concat(),
            [&b"RE\n100"[..], &shown, b"RE\n"].concat(),
        ),
        (
            "bcs:samia",
            "or three other",
            [tr(16521, 3, 0, "", 0), tr(16450, 0, 0, "shld21", 0), other.clone(), tr(16450, 0, 0, "sw1h32", 0)].concat(),
            [&b"RE\n100"[..], &action(10, 0, ""), b"RE\n"].concat(),
        ),
    ];
    let part = |data: &[u8], at: usize| split(data, b"CO\n")[at].to_vec();
    for (resource, label, condition, responses) in scripts {
        let original = [&b"SC\nCR\nCO\n"[..], &condition, b"CO\nRS\n", &responses, b"RS\nCR\nSC\n"].concat();
        assert_eq!(fixture.input(&format!("own_patch reasons {label} {resource}"), "data"), original, "{label}");
        let result = patched(resource, &original);
        let spec = spec(resource);
        let items = treasure_set(spec);
        let old = script_blocks(&original).unwrap().blocks;
        let new = script_blocks(&result).unwrap().blocks;
        assert_eq!((old.len(), new.len()), (1, 2), "{label}");
        let (block, selected, excluded) = (&old[0], &new[0], &new[1]);
        assert_eq!(part(excluded, 2), part(block, 2), "{label}");
        let after = part(selected, 2);
        let golem_text: &[u8] = b"38758 0 0 0 0\"\"";
        let moved: &[u8] = b"100001 0 0 0 0\"\"";
        assert_eq!(common::builders::count(&after, moved), common::builders::count(&responses, golem_text), "{label}");
        assert_eq!(replace(&after, moved, golem_text, None), part(block, 2), "{label}");
        for (active, taken, owns, set_other) in combinations4() {
            let globals = or_state(spec, active, taken, set_other);
            let has = if owns { items.clone() } else { BTreeSet::new() };
            let took = if taken { items.clone() } else { BTreeSet::new() };
            // The latch stands for the party having the treasure.
            assert_eq!(
                compiled_conditions(&part(selected, 1), &globals, &has),
                active && compiled_conditions(&condition, &globals, &took),
                "{label} selected {active} {taken} {owns} {set_other}"
            );
            assert_eq!(
                compiled_conditions(&part(excluded, 1), &globals, &has),
                !active && compiled_conditions(&condition, &globals, &has),
                "{label} excluded {active} {taken} {owns} {set_other}"
            );
        }
    }
    let dialogues: [(&str, &str, &[u8]); 3] = [
        ("dlg:safanj", "or other", b"OR(2)\r\nPartyHasItem(\"book03\")\r\nGlobal(\"Other\",\"GLOBAL\",1)\r\n"),
        ("dlg:safanj", "or after negated", b"!PartyHasItem(\"book03\")\nOR(2)\nGlobal(\"Other\",\"GLOBAL\",1)\nPartyHasItem(\"BOOK03\")"),
        (
            "dlg:samia",
            "or three other",
            b"OR(3)\r\nPartyHasItem(\"sw1h32\")\r\nGlobal(\"Other\",\"GLOBAL\",1)\r\nPartyHasItem(\"shld21\")\r\n",
        ),
    ];
    for (resource, label, condition) in dialogues {
        let places = sites(resource).1;
        let conditions: Vec<(usize, &[u8])> = places.iter().map(|index| (*index, condition)).collect();
        let original = dialogue_fixture(resource, &conditions);
        assert_eq!(fixture.input(&format!("own_patch reasons {label} {resource}"), "data"), original, "{label}");
        let result = patched(resource, &original);
        let after = Dialogue::new(&result, &resource[4..]).unwrap();
        let spec = spec(resource);
        let items = treasure_set(spec);
        for index in places {
            for (active, taken, owns, set_other) in combinations4() {
                let globals = or_state(spec, active, taken, set_other);
                let has = if owns { items.clone() } else { BTreeSet::new() };
                let took = if taken { items.clone() } else { BTreeSet::new() };
                assert_eq!(
                    text_conditions(&after.state_triggers[*index], &globals, &has),
                    text_conditions(condition, &globals, if active { &took } else { &has }),
                    "{label} {index} {active} {taken} {owns} {set_other}"
                );
            }
        }
    }
}

#[test]
fn routes_the_check_cannot_recognise_name_their_reason() {
    for (resource, _, data, reason) in unrecognised() {
        let found = reasons(resource, &data);
        assert_eq!(found.keys().cloned().collect::<BTreeSet<_>>(), target_ids(resource), "{reason}");
        for text in found.values() {
            assert_eq!(text, &reason);
        }
        assert_profile(patch_container_resource(resource, &data, Some(&BTreeMap::from([(38758, 100001)]))).unwrap_err(), &reason);
    }
}

#[test]
fn exact_target_selectors_shared_samia_latch_and_golem_text() {
    let (one, two) = (target_spec("bg2:mode2:target:53").unwrap(), target_spec("bg2:mode2:target:28").unwrap());
    assert_eq!((field(&one, "original_item"), field(&two, "original_item")), ("shld21", "sw1h32"));
    for key in ["area", "activation_global", "latch_global", "latch_scope"] {
        assert_eq!(field(&one, key), field(&two, key));
    }
    assert_eq!(treasure("bg2:mode2:target:53").unwrap(), ["shld21", "sw1h32"]);
    assert_eq!(treasure("bg2:mode2:target:13").unwrap(), ["ax1h13"]);
    assert_eq!(field(&target_spec("bg1:mode2:target:22").unwrap(), "area"), "ar3601");
    assert_eq!(treasure_area("bg1:mode2:target:22", "eet").unwrap(), "bg3601");
    assert_eq!(treasure_area("bg1:mode2:target:22", "bgee").unwrap(), "ar3601");
    assert_eq!(treasure_area("bg2:mode2:target:13", "eet").unwrap(), "ar1303");
    assert_profile(target_spec("bg2:mode2:target:1").unwrap_err(), "Unsupported extended reward container");
    assert_profile(own_patch("bcs:other", b"SC\nSC\n").unwrap_err(), "Unsupported extended reward container resource");
    let original = GameText::new(make_tlk(&["Preserve existing text"]));
    let (text, refs) = prepare_container_texts(&original, TEXT_RESOURCES, "en_us").unwrap();
    assert_eq!(refs.values().map(|row| row[&38758]).collect::<BTreeSet<_>>().len(), 1);
    assert_eq!(
        tlk_text(&text.male, refs["bcs:kpgol01"][&38758] as u32).as_deref(),
        Some("As you pick up the item, two of the golems come to life and attack.")
    );
    assert_eq!(prepare_container_texts(&text, TEXT_RESOURCES, "en_us").unwrap(), (text.clone(), refs));
    assert_profile(
        prepare_container_texts(&original, TEXT_RESOURCES, "es_ES").unwrap_err(),
        "reward texts have no Spanish translation yet",
    );
    assert_eq!(prepare_container_texts(&original, ["bcs:ar1202"], "es_ES").unwrap(), (original, BTreeMap::new()));
}

fn chest(area: &str, index: Value, item: &str) -> Value {
    let shown = str_value(&index);
    Value::Dict(cir_config::dict! {
        "kind" => "container", "resource" => format!("are:{area}"), "item" => item,
        "id" => format!("are:{area}/container/{shown}/item/0"), "record" => Value::Dict(cir_config::dict! {"container_index" => index}),
    })
}

#[test]
fn the_quest_container_is_the_one_that_holds_the_treasure() {
    let samia = "bg2:mode2:target:28";
    // BG2:EE and EET keep Samia's treasure in AR1202's tenth container
    // (Item Randomiser's container10 of the original game, renamed since).
    let template = Value::Dict(cir_config::dict! {"kind" => "cre-template", "resource" => "cre:x", "item" => "shld21", "record" => Value::Dict(Dict::new())});
    let sources = vec![
        chest("ar1202", Value::from(8i64), "misc01"),
        chest("ar1202", Value::from(9i64), "sw1h32"),
        chest("ar1202", Value::from(9i64), "shld21"),
        chest("ar1201", Value::from(0i64), "sw1h32"),
        template.clone(),
    ];
    assert_eq!(treasure_container(samia, &sources, "bg2ee").unwrap(), "are:ar1202/container/9");
    assert_eq!(treasure_container("bg2:mode2:target:53", &sources, "eet").unwrap(), "are:ar1202/container/9");
    // A mod added a chest before it: the treasure still names it.
    let moved: Vec<Value> = sources
        .iter()
        .map(|row| match row.get("kind").and_then(Value::as_str) {
            Some("container") => {
                let index = row.get("record").and_then(|record| record.get("container_index")).and_then(Value::as_int).unwrap();
                let mut changed = row.as_dict().unwrap().clone();
                changed.insert("record", Value::Dict(cir_config::dict! {"container_index" => index + 1}));
                Value::Dict(changed)
            }
            _ => row.clone(),
        })
        .collect();
    assert_eq!(treasure_container(samia, &moved, "bg2ee").unwrap(), "are:ar1202/container/10");
    let doubled: Vec<Value> =
        sources.iter().cloned().chain([chest("ar1202", Value::from(3i64), "sw1h32"), chest("ar1202", Value::from(3i64), "shld21")]).collect();
    for (changed, reason) in [
        (&sources[..2], "AR1202 has no container that holds SHLD21/SW1H32"),
        (&doubled[..], "AR1202 has more than one container that holds SHLD21/SW1H32"),
    ] {
        let error = treasure_container(samia, changed, "bg2ee").unwrap_err();
        assert_eq!((error.kind, error.message.as_str()), (ErrorKind::Profile, reason));
    }
    // The golems' treasures are in AR1303, whatever the table's area column says.
    let golem = [chest("ar1302", Value::from(0i64), "ax1h13"), chest("ar1303", Value::from(2i64), "ax1h13")];
    assert_eq!(treasure_container("bg2:mode2:target:13", &golem, "bg2ee").unwrap(), "are:ar1303/container/2");
    // EET names Safana's BG1 area BG3601.
    let safana = "bg1:mode2:target:22";
    assert_eq!(treasure_container(safana, &[chest("bg3601", Value::from(0i64), "book03")], "eet").unwrap(), "are:bg3601/container/0");
    assert_eq!(treasure_container(safana, &[chest("ar3601", Value::from(0i64), "book03")], "bgee").unwrap(), "are:ar3601/container/0");
    // An item record that two containers share belongs to neither.
    let aliased = [Value::Dict(cir_config::dict! {
        "kind" => "container", "resource" => "are:ar1514", "item" => "bow19b",
        "record" => Value::Dict(cir_config::dict! {"container_index" => Value::Null}),
    })];
    let error = treasure_container("bg2:mode2:target:61", &aliased, "bg2ee").unwrap_err();
    assert_eq!((error.kind, error.message.as_str()), (ErrorKind::Profile, "AR1514 has no container that holds BOW19B"));
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
    assert!(contains(&changed, b"FLPR_CPPCHST"));
}

/// The reviewed EET resources and their exact patches with the golem text
/// appended to an empty dialog.tlk (regression evidence; nothing pins these).
const REVIEWED_EET: &[(&str, &str, &str)] = &[
    ("bcs:ar1202", "d6c3de8366f99088d41e1c01a4a8d8811add81771926d9d7d70ef5193fca2280", "fbe5e57bf1fcabc548cdc6260a358ffe4d314859a4ee8ee048af5b269942463d"),
    ("bcs:kpgol01", "7e569c1e414c4afef74b274515d073c04dd92ba2fa0ab0f96b8bbccf2c80cfa6", "b082abf5ec04f97c6a568e9e2f40fedec429ebe80526114c69819ff736cbd478"),
    ("bcs:kpgol02", "3dd4220a9bc7ae4b5778f3b6a1dc7438d943b138cb9383aac26d8835489a5431", "3c7bbfef7fb61f6c782809adc9b02f2f8454ebaddf9575741fd6b15c98a4b13f"),
    ("bcs:kpgol03", "82e7a96eac8a9ad8b913982ca8d03bdc824346de42267d80065db5d3155077f0", "7e8b982fb769aefc855ed9d7b7a7eeb47fdba1e7064a4273356765279f8edb48"),
    ("bcs:kpgol04", "d33714c44ebc89dd5c2fef834781cfbfe69f1f8cd0a9ff9de21fee823b4efc7d", "b38a8656ccefc2a160de98c204ed91ba9747128c44072e5b499c6d7ef8fb78fe"),
    ("bcs:kpgol05", "d473eebb61a8a03d8bc0b1c993e0810c67a6e01225687b5cba035c6a5e067dde", "f470815225c342cbf1032ac8bc930e61e6448af2e45e88d14e4c52fb9189f844"),
    ("bcs:kpgol06", "157cc4c98135416842176cb76968280630160156e4039ba0e42cf744d33d0a66", "c15a788983784a852441eaf814333afd685a1a3fac4b07ba0cf7ea5f3272827c"),
    ("bcs:ppchest", "446b07d5de81386e5e7f6b49efb323c1d617222cc4714f1eae400e1c08c049d2", "392a2634832669cdebbfde3bd65c1000c7f18c02adaa5fa539b9e8ac329ec6de"),
    ("bcs:safana", "afc23b13dfd4e87978997e55e35c6f4f4d14238647ef1197653184e55539bf83", "c94870e206bfb3b9977e7de957731e6a7a7a32a552c1f6b39abbb38a1ac42376"),
    ("bcs:samia", "823d03307573daaa08ad7c853879e1b62796d67212b4e62e6d6ba66f0cac32c9", "2a780431c511cb9fa4b729f95fc7f1d4d0f01ac9aebd72fdacb07dcb687c3ee4"),
    ("dlg:safanj", "8a890ecdc434267ed79661db132b140c1371a068de234d8caba541aa962da252", "75051dcf0ba1fb37461ab814007279c8055622e9ed2004ec95692554fcde7ecc"),
    ("dlg:samia", "3572813dfb538c88953709b55e6bcd48139ae24c9a9b84e176f9920652a88254", "27de3f4b6f9714547362bb5d9589c980ff1927648f5b17c21ebab2f1e0c6a400"),
];

#[test]
fn reviewed_eet_scripts_and_dialogues_when_available() {
    let cache = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../work/original-preset-runtime-20260929/mode2-resources");
    if !cache.exists() {
        eprintln!("skipped: the optional isolated-lab resource cache is not present");
        return;
    }
    let mut reviewed: Vec<&str> = REVIEWED_EET.iter().map(|(resource, _, _)| *resource).collect();
    let mut known: Vec<&str> = resource_targets().keys().collect();
    reviewed.sort_unstable();
    known.sort_unstable();
    assert_eq!(reviewed, known);
    let (_text, golem) = prepare_container_texts(&GameText::new(make_tlk(&[])), TEXT_RESOURCES, "en_us").unwrap();
    for (resource, original_hash, patched_hash) in REVIEWED_EET {
        let (kind, name) = resource.split_once(':').unwrap();
        let original = std::fs::read(cache.join(format!("{name}.{kind}"))).unwrap();
        assert_eq!(sha(&original), *original_hash, "{resource}");
        let exact = patch_container_resource(resource, &original, golem.get(*resource)).unwrap();
        assert_eq!(sha(&exact), *patched_hash, "{resource}");
        let result = patched(resource, &original);
        if kind == "bcs" {
            check_script(resource, &original, &result, None);
        } else {
            check_dialogue(resource, &original, &result, &[]);
        }
    }
}
