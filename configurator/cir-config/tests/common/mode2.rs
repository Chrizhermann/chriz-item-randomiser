//! The reference's direct grant test builders and graph checks
//! (`tests/test_mode2_rewards.py`), shared by the `parity_mode2_rewards*`
//! binaries.

use super::builders::{contains, count, index, outgoing, replace, u32_at};
use cir_config::json::{Dict, Value};
use cir_config::mode2_rewards::{patch_dialogue, resource_specs, Naming, ACTION_TEXT_REFS};
use cir_config::py::{put_u32, splitlines_bytes, str_value};
use cir_config::quest_options::Dialogue;
use std::collections::{BTreeMap, BTreeSet};

pub const TRGENI_DEPARTURE: &[u8] =
    b"EEex_LuaAction(\"EEex_Action_QueueResponseStringOnAIBase([[DestroySelf()]],EEex_LuaAction_Object)\")";

pub fn text<'a>(spec: &'a Dict, key: &str) -> &'a str {
    spec.get(key).and_then(Value::as_str).unwrap_or_else(|| panic!("spec field {key}"))
}

/// `spec["branches"]` as (state, ordinal) pairs.
pub fn branches(spec: &Dict) -> Vec<(usize, usize)> {
    spec.get("branches")
        .and_then(Value::as_list)
        .expect("branches")
        .iter()
        .map(|row| {
            let pair = row.as_list().expect("branch pair");
            (pair[0].as_int().unwrap() as usize, pair[1].as_int().unwrap() as usize)
        })
        .collect()
}

/// `spec["target_ids"][0]`.
pub fn target_id(spec: &Dict) -> &str {
    spec.get("target_ids").and_then(Value::as_list).unwrap()[0].as_str().unwrap()
}

/// `spec["target_ids"][0].rsplit(":", 1)[1]`.
pub fn target_number(spec: &Dict) -> &str {
    target_id(spec).rsplit(':').next().unwrap()
}

pub fn specs(resource: &str) -> Vec<Dict> {
    resource_specs(resource, Naming::default()).unwrap()
}

/// Python's `bytes.isspace()` for one byte; also what `\s` matches in a
/// bytes pattern.
fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

fn is_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

/// One match of `(?i)\bVERB\(\s*"ITEM"\s*,\s*([^,()]+?(?:\([^()]*\))?)\s*(?:,[^()]*)?\)`
/// at `at`: its end and the group's span. A recipient may be an object
/// function, as Varci's `LastTalkedToBy(Myself)`.
fn grant_at(actions: &[u8], at: usize, verb: &[u8], item: &[u8]) -> Option<(usize, (usize, usize))> {
    let spaces = |mut at: usize| {
        while actions.get(at).is_some_and(|byte| is_space(*byte)) {
            at += 1;
        }
        at
    };
    if at > 0 && is_word(actions[at - 1]) || !actions.get(at..at + verb.len())?.eq_ignore_ascii_case(verb) {
        return None;
    }
    let mut cursor = at + verb.len();
    if actions.get(cursor) != Some(&b'(') {
        return None;
    }
    cursor = spaces(cursor + 1);
    if actions.get(cursor) != Some(&b'"') || !actions.get(cursor + 1..cursor + 1 + item.len())?.eq_ignore_ascii_case(item) {
        return None;
    }
    cursor += 1 + item.len();
    if actions.get(cursor) != Some(&b'"') {
        return None;
    }
    cursor = spaces(cursor + 1);
    if actions.get(cursor) != Some(&b',') {
        return None;
    }
    let group = spaces(cursor + 1);
    let limit = group + actions[group..].iter().take_while(|byte| !matches!(byte, b',' | b'(' | b')')).count();
    // The lazy group ends where only spaces lead to the call's end or to
    // further arguments without a nested call.
    for end in group + 1..=limit {
        // The object function's own arguments follow its name at once.
        let end = if end == limit && actions.get(end) == Some(&b'(') {
            let close = end + 1 + actions[end + 1..].iter().take_while(|byte| !matches!(byte, b'(' | b')')).count();
            if actions.get(close) != Some(&b')') {
                return None;
            }
            close + 1
        } else {
            end
        };
        let after = spaces(end);
        match actions.get(after) {
            Some(b',') => {
                let close = after + 1 + actions[after + 1..].iter().take_while(|byte| !matches!(byte, b'(' | b')')).count();
                if actions.get(close) == Some(&b')') {
                    return Some((close + 1, (group, end)));
                }
            }
            Some(b')') => return Some((after + 1, (group, end))),
            _ => {}
        }
    }
    None
}

/// `grant_in(actions, spec)`: the grant of the route's item in one reply's
/// actions, and its recipient.
pub fn grant_in(actions: &[u8], spec: &Dict) -> (Vec<u8>, Vec<u8>) {
    let verb: &[u8] = if spec.get("grant_kind").and_then(Value::as_str) == Some("transfer") { b"GiveItem" } else { b"GiveItemCreate" };
    let item = text(spec, "item").as_bytes();
    let mut found = Vec::new();
    let mut at = 0;
    while at <= actions.len() {
        match grant_at(actions, at, verb, item) {
            Some((end, group)) => {
                found.push((at, end, group));
                at = end;
            }
            None => at += 1,
        }
    }
    assert_eq!(found.len(), 1, "{}", String::from_utf8_lossy(actions));
    let (start, end, (group, group_end)) = found[0];
    (actions[start..end].to_vec(), actions[group..group_end].to_vec())
}

/// `guarded(prior, guard)`: a reply condition with a guard line added, as
/// the adapter joins it.
pub fn guarded(prior: &[u8], guard: &[u8]) -> Vec<u8> {
    let mut result = prior.to_vec();
    if prior.last().is_some_and(|byte| !is_space(*byte)) {
        result.extend_from_slice(b"\r\n");
    }
    result.extend_from_slice(guard);
    result
}

pub fn trgeni_terminal_actions() -> String {
    let mut actions: Vec<String> = ["SetGlobal(\"zahraajob\",\"GLOBAL\",2)", "SetGlobal(\"geniesgone\",\"GLOBAL\",1)", "AddexperienceParty(10000)"]
        .iter()
        .map(|action| action.to_string())
        .collect();
    let effects = ["CreateVisualEffectObject(\"SPDISPMA\",Myself)", "Wait(1)", "CreateVisualEffectObject(\"SPFLESHS\",Myself)", "DestroySelf()"];
    for other in ["trgeni02", "trgeni03"] {
        actions.extend(effects.iter().map(|action| format!("ActionOverride(\"{other}\",{action})")));
    }
    actions.extend(effects.iter().map(|action| action.to_string()));
    actions.join("\r\r\n") + "\r\r\n"
}

/// `fixture_target(quest, state)`: where a fixture reply leads; TRGENI's
/// grants lead on to the genies' departure.
pub fn fixture_target(quest: &str, state: usize) -> Option<u32> {
    if quest == "trgeni01" && [35, 36, 37, 38, 40].contains(&state) {
        return match state {
            35 | 40 => Some(36),
            36 => Some(37),
            37 => Some(38),
            _ => None,
        };
    }
    Some(2)
}

pub fn empty_dialogue(name: &str) -> Dialogue {
    let mut header = b"DLG V1.0".to_vec();
    header.resize(52, 0);
    for (at, value) in [0, 52, 0, 52, 52, 0, 52, 0, 52, 0].into_iter().enumerate() {
        put_u32(&mut header, 8 + at * 4, value);
    }
    Dialogue::new(&header, name).unwrap()
}

/// `dialogue_fixture(specs)`: every reviewed branch grants its item beside
/// an unrelated grant, followed by a journal erase and departure.
pub fn dialogue_fixture(specs: &[Dict]) -> Vec<u8> {
    let spec = &specs[0];
    let quest = text(spec, "quest");
    let mut dialogue = empty_dialogue(quest);
    let required: BTreeSet<(usize, usize)> = specs.iter().flat_map(branches).collect();
    let last = required.iter().map(|row| row.0).max().unwrap();
    for state in 0..last + 2 {
        let count = required.iter().filter(|row| row.0 == state).map(|row| row.1).max().unwrap_or(0) + 1;
        let mut transitions = Vec::new();
        for ordinal in 0..count {
            let mut action = String::from("SetGlobal(\"Untouched\",\"GLOBAL\",8)\r\r\n");
            if required.contains(&(state, ordinal)) {
                action += "GiveItemCreate(\"misc42\",Player2,0,0,0)\r\r\n";
                for grant_spec in specs {
                    if branches(grant_spec).contains(&(state, ordinal)) && grant_spec.get("job_end").is_some_and(Value::truthy) {
                        // A reply that ends the speaker's job and gives nothing.
                        let job = grant_spec.get("job_end").and_then(Value::as_list).unwrap();
                        action += &format!("SetGlobal(\"{}\",\"GLOBAL\",{})\r\r\n", str_value(&job[0]), str_value(&job[1]));
                    } else if branches(grant_spec).contains(&(state, ordinal)) {
                        let charges: Vec<String> = grant_spec
                            .get("payload")
                            .and_then(|payload| payload.get("charges"))
                            .and_then(Value::as_list)
                            .unwrap()
                            .iter()
                            .map(str_value)
                            .collect();
                        if text(grant_spec, "quest") == "amcler01" {
                            action += "DisplayStringHead(Myself,74025)\r\r\n";
                        }
                        let (item, recipient) = (text(grant_spec, "item"), str_value(grant_spec.get("recipient").unwrap()));
                        if grant_spec.get("grant_kind").and_then(Value::as_str) == Some("transfer") {
                            action += &format!("GiveItem(\"{item}\",{recipient})\r\r\n");
                        } else {
                            action += &format!("GiveItemCreate(\"{item}\",{recipient},{})\r\r\n", charges.join(","));
                        }
                    }
                }
            }
            action += "EraseJournalEntry(12345)\r\r\nWait(1)\r\r\nEscapeArea()";
            if quest == "trgeni01" && (state == 37 || state == 38) {
                action = trgeni_terminal_actions();
            }
            let trigger = format!("Global(\"OriginalCondition\",\"GLOBAL\",{ordinal})");
            let target = fixture_target(quest, state);
            transitions.push(dialogue.transition(target, Some(9876), &trigger, &action, Some(12345)).unwrap());
        }
        dialogue.state(555 + state as u32, transitions);
    }
    dialogue.render()
}

/// One reply of [`layout_dialogue`]: (trigger, actions, next state or None).
pub type LayoutReply<'a> = (&'a str, &'a str, Option<u32>);

/// `layout_dialogue(name, states, beamdog=...)`: a DLG as Beamdog's compiler
/// writes it (LF between lines, none after the last) or as WeiDU does (CRLF
/// after every line).
pub fn layout_dialogue(name: &str, states: &[Vec<LayoutReply>], beamdog: bool) -> Vec<u8> {
    let mut dialogue = empty_dialogue(name);
    let line = |text: &str| if beamdog { text.to_string() } else { text.replace('\n', "\r\n") };
    for (number, replies) in states.iter().enumerate() {
        let mut transitions = Vec::new();
        for (trigger, actions, target) in replies {
            transitions.push(dialogue.transition(*target, Some(70000 + number as u32), &line(trigger), &line(actions), None).unwrap());
        }
        dialogue.state(60000 + number as u32, transitions);
    }
    if beamdog {
        let trim = |text: &mut Vec<u8>| {
            while text.last().is_some_and(|byte| matches!(byte, b'\r' | b'\n')) {
                text.pop();
            }
        };
        dialogue.triggers.iter_mut().for_each(trim);
        dialogue.actions.iter_mut().for_each(trim);
    }
    dialogue.render()
}

/// `with_actions(data, name, changes)`: the dialogue with the actions of
/// some replies, by (state, ordinal), replaced.
pub fn with_actions(data: &[u8], name: &str, changes: &[((usize, usize), &[u8])]) -> Vec<u8> {
    let mut dialogue = Dialogue::new(data, name).unwrap();
    for ((state, ordinal), text) in changes {
        let row = &outgoing(&dialogue, *state)[*ordinal];
        let action = u32_at(row, 16) as usize;
        dialogue.actions[action] = text.to_vec();
    }
    dialogue.render()
}

pub fn obj(selector: u32) -> String {
    format!("0 0 0 0 0 0 0 {selector} 0 0 0 0 \"\"OB\n")
}

/// `action(opcode, value=0, text="", target=0, actor=0)`.
pub fn action(opcode: u32, value: i64, text: &str, target: u32, actor: u32) -> String {
    action_with(opcode, value, text, target, actor, "")
}

/// `action(opcode, value, text, target, actor, second)`: with a second string.
pub fn action_with(opcode: u32, value: i64, text: &str, target: u32, actor: u32, second: &str) -> String {
    format!("AC\n{opcode}OB\n{}OB\n{}OB\n{}{value} 0 0 0 0\"{text}\" \"{second}\" AC\n", obj(actor), obj(target), obj(0))
}

/// `block(actions, condition="AR1202Quest")`.
pub fn block(actions: &str, condition: &str) -> String {
    alternatives(&[actions], condition, 100)
}

/// `alternatives(*responses, condition="AR1202Quest", weight=50)`: a block
/// whose responses the engine chooses between by weight.
pub fn alternatives(responses: &[&str], condition: &str, weight: u32) -> String {
    let condition = format!("TR\n16399 1 0 0 0 \"{condition}\" \"\" OB\n{}TR\n", obj(0));
    let responses: String = responses.iter().map(|actions| format!("RE\n{weight}{actions}RE\n")).collect();
    format!("CR\nCO\n{condition}CO\nRS\n{responses}RS\nCR\n")
}

/// `well_reward(target=21, display=40850)`.
pub fn well_reward(target: u32, display: i64) -> String {
    [action(30, 2, "AR1202DragonWell", 0, 0), action(151, display, "", 1, 0), action(140, 0, "helm21", target, 0), action(177, 0, "", 0, 0)]
        .concat()
}

/// `script_fixture(name)` for asysta01 or ddwell.
pub fn script_fixture(name: &str) -> Vec<u8> {
    if name == "asysta01" {
        let actions = [action(164, 20000, "", 0, 0), action(82, 0, "Helm19", 0, 0), action(82, 0, "Misc8y", 0, 0), action(30, 2, "AR1512PlantedItems", 0, 0)]
            .concat();
        return format!("SC\n{}SC\n", block(&actions, "AR1202Quest")).into_bytes();
    }
    let other = block(&action(151, 40849, "", 1, 0), "AR1202Quest");
    format!("SC\n{other}{}{other}SC\n", block(&well_reward(21, 40850), "AR1202Quest")).into_bytes()
}

/// `trigger(opcode, value=0, text="", negate=0)`.
pub fn trigger(opcode: u32, value: i64, text: &str, negate: u32) -> String {
    format!("TR\n{opcode} {value} {negate} 0 0 \"{text}\" \"\" OB\n{}TR\n", obj(0))
}

/// `book_block(triggers, actions)`: a block with one response.
pub fn book_block(triggers: &[String], actions: &str) -> String {
    format!("CR\nCO\n{}CO\nRS\nRE\n100{actions}RE\nRS\nCR\n", triggers.concat())
}

/// `BOOK_GIFTS`: the two scrolls the book gives the reader.
pub fn book_gifts() -> String {
    action(140, 0, "scrl8z", 17, 0) + &action(140, 0, "scrl9b", 17, 0)
}

/// `book_fixture(installed=True)`: PPBOOK.BCS as the game has it, or as CIR's
/// installer splits its last page for the ring (FLPR_QBOOK): that page gives
/// the ring and both scrolls to the reader (LastTrigger).
pub fn book_fixture(installed: bool) -> Vec<u8> {
    let (clicked, page) = (trigger(112, 0, "", 0), trigger(16399, 5, "AR1513page", 0));
    let turn = action(151, 50658, "", 1, 0) + &action(109, 1, "AR1513page", 0, 0);
    let ring = action(140, 0, "ring09", 17, 0);
    let earlier = book_block(
        &[clicked.clone(), trigger(16399, 4, "AR1513page", 0)],
        &(action(151, 50657, "", 1, 0) + &action(109, 1, "AR1513page", 0, 0)),
    );
    let gifts = book_gifts();
    let pages = if installed {
        let event = action(30, 1, "GLOBALFLPR_EBOOK", 0, 0);
        book_block(
            &[clicked.clone(), page.clone(), trigger(16399, 1, "GLOBALFLPR_QBOOK", 0)],
            &[turn.as_str(), &action(30, 1, "GLOBALFLPR_XBOOK", 0, 0), &gifts, &event].concat(),
        ) + &book_block(
            &[clicked, page, trigger(16399, 1, "GLOBALFLPR_QBOOK", 1)],
            &[turn.as_str(), &ring, &gifts, &event].concat(),
        )
    } else {
        book_block(&[clicked, page], &[turn.as_str(), &ring, &gifts].concat())
    };
    format!("SC\n{earlier}{pages}SC\n").into_bytes()
}

/// `KING_PAID`: the king's job is done (sahkngjob 2), just before he pays.
pub fn king_paid() -> String {
    trigger(16399, 2, "GLOBALsahkngjob", 0)
}

/// `KING_DESTROYS`: SAHTREAS.BCS destroys the items the king's reward reply
/// gives, the treasury's rod among them.
pub fn king_destroys() -> String {
    action(169, 0, "RODS02", 0, 0) + &action(169, 0, "BRAC19", 0, 0)
}

/// `NO_ACTION`.
pub fn no_action() -> String {
    action(0, 0, "", 0, 0)
}

/// `treasury_fixture(*blocks)`: SAHTREAS.BCS as the game has it, or with
/// these blocks.
pub fn treasury_fixture(blocks: &[String]) -> Vec<u8> {
    let blocks = if blocks.is_empty() { book_block(&[king_paid()], &king_destroys()) } else { blocks.concat() };
    format!("SC\n{blocks}SC\n").into_bytes()
}

/// `HAS_RING`: Tolgerias's class script (TOLGER.BCS, which both TOLGER and
/// TOLGER2 run) tests his ring.
pub fn has_ring() -> String {
    trigger(16481, 0, "ring33", 0)
}

/// `RING_GONE`: the other Tolgerias has lost his ring.
pub fn ring_gone() -> String {
    trigger(16399, 1, "GLOBALCDRing33Gone", 0)
}

/// `RING_DESTROYED`.
pub fn ring_destroyed() -> String {
    action(169, 0, "ring33", 0, 0)
}

/// `tolgerias_fixture(*blocks)`: TOLGER.BCS as the game has it, or with these
/// blocks: the one that has lost his ring marks it gone, and the other then
/// destroys his.
pub fn tolgerias_fixture(blocks: &[String]) -> Vec<u8> {
    let blocks = if blocks.is_empty() {
        [
            book_block(
                &[trigger(16481, 0, "ring33", 1), trigger(16399, 0, "GLOBALCDRing33Gone", 0)],
                &action(30, 1, "GLOBALCDRing33Gone", 0, 0),
            ),
            book_block(&[has_ring(), ring_gone()], &ring_destroyed()),
            book_block(&[trigger(16465, 0, "TOLGER2", 0)], &action(111, 0, "", 0, 0)),
        ]
        .concat()
    } else {
        blocks.concat()
    };
    format!("SC\n{blocks}SC\n").into_bytes()
}

/// `route_guard(flag, selected)`: the guard of a route's selected flag, as
/// compiled.
pub fn route_guard(flag: &str, selected: bool) -> String {
    trigger(16399, 1, &format!("GLOBAL{flag}"), if selected { 0 } else { 1 })
}

/// `handover_line(spec)`: the reward reply's condition by which its place's
/// treasury goes to the payee.
pub fn handover_line(spec: &Dict) -> Vec<u8> {
    let (campaign, number) = target_id(spec).split_once(":mode2:target:").unwrap_or((target_id(spec), ""));
    let campaign = if campaign == "bg1" { ",1" } else { "" };
    format!("EEex_LuaTrigger(\"return(FLPR_Handover==nil)or(FLPR_Handover(EEex_LuaTrigger_Object,{number}{campaign}))\")\r\n")
        .into_bytes()
}

/// The text-route fixture of test_text_variants_preserve_original_routes...
pub fn text_variant(resource: &str) -> Vec<u8> {
    let name = resource.split(':').nth(1).unwrap();
    let mut d = Dialogue::new(&dialogue_fixture(&specs(resource)), name).unwrap();
    let originals = cir_config::mode2_rewards::text_refs(resource).unwrap();
    let action_refs = action_text_refs(resource);
    for (at, (old_ref, _)) in originals.iter().enumerate() {
        if action_refs.contains(old_ref) {
            continue;
        }
        if originals.len() > 1 && at == originals.len() - 1 {
            let first = u32_at(&d.states[0], 4) as usize;
            put_u32(&mut d.transitions[first], 4, *old_ref as u32);
        } else {
            put_u32(&mut d.states[at], 0, *old_ref as u32);
        }
    }
    d.state_triggers.push(b"NumTimesTalkedTo(0)\r\n".to_vec());
    let entry = d.state_triggers.len() as u32 - 1;
    put_u32(&mut d.states[0], 12, entry);
    d.render()
}

pub fn action_text_refs(resource: &str) -> Vec<i64> {
    ACTION_TEXT_REFS.iter().find(|(key, _)| *key == resource).map_or_else(Vec::new, |(_, refs)| refs.to_vec())
}

fn guard(flag: &str, selected: bool) -> Vec<u8> {
    format!("{}Global(\"{flag}\",\"GLOBAL\",1)\r\n", if selected { "" } else { "!" }).into_bytes()
}

/// `check_dialogue(name, original, result, action_refs, branches, naming)`.
pub fn check_dialogue(
    name: &str,
    original: &[u8],
    result: &[u8],
    action_refs: Option<&BTreeMap<i64, i64>>,
    gated: Option<&[(usize, usize)]>,
    naming: Naming<'_>,
) {
    let spec = &resource_specs(&format!("dlg:{name}"), naming).unwrap()[0];
    let reviewed = gated.map_or_else(|| branches(spec), <[_]>::to_vec);
    let before = Dialogue::new(original, name).unwrap();
    let after = Dialogue::new(result, name).unwrap();
    assert_eq!(after.state_triggers, before.state_triggers);
    assert_eq!(after.actions[..before.actions.len()], before.actions[..]);
    for (state_index, state) in before.states.iter().enumerate() {
        assert_eq!(after.states[state_index][..4], state[..4]);
        assert_eq!(after.states[state_index][12..], state[12..]);
        let (old, new) = (outgoing(&before, state_index), outgoing(&after, state_index));
        let mut cursor = 0;
        for (ordinal, transition) in old.iter().enumerate() {
            if !reviewed.contains(&(state_index, ordinal)) {
                if name == "trgeni01" && (state_index == 37 || state_index == 38) {
                    assert_eq!(new[cursor][..16], transition[..16]);
                    assert_eq!(new[cursor][20..], transition[20..]);
                    let original_action = &before.actions[u32_at(transition, 16) as usize];
                    let changed = &after.actions[u32_at(&new[cursor], 16) as usize];
                    assert_eq!(count(changed, TRGENI_DEPARTURE), 1);
                    assert_eq!(&replace(changed, TRGENI_DEPARTURE, b"DestroySelf()", None), original_action);
                    assert!(!splitlines_bytes(changed).contains(&&b"DestroySelf()"[..]));
                    for other in ["trgeni02", "trgeni03"] {
                        assert!(contains(changed, format!("ActionOverride(\"{other}\",DestroySelf())").as_bytes()));
                    }
                } else {
                    assert_eq!(&new[cursor], transition, "{name} {state_index} {ordinal}");
                }
                cursor += 1;
                continue;
            }
            let flags = u32_at(transition, 0);
            let original_action = &before.actions[u32_at(transition, 16) as usize];
            let prior = if flags & 2 != 0 { before.triggers[u32_at(transition, 12) as usize].clone() } else { Vec::new() };
            let (grant, recipient) = grant_in(original_action, spec);
            for selected in [false, true] {
                let branch = &new[cursor];
                cursor += 1;
                assert_eq!(u32_at(branch, 0), flags | 2);
                assert_eq!(branch[4..12], transition[4..12]); // Reply and journal.
                assert_eq!(branch[20..], transition[20..]); // Link/exit state.
                let trigger = &after.triggers[u32_at(branch, 12) as usize];
                assert_eq!(trigger, &guarded(&prior, &guard(text(spec, "selected_global"), selected)));
                let changed = &after.actions[u32_at(branch, 16) as usize];
                let event: Vec<u8>;
                let mut restored;
                if spec.get("inventory_target").is_some_and(Value::truthy) {
                    // The target's row, and a BG1 row's campaign (1).
                    let target = target_number(spec);
                    let campaign = if target_id(spec).starts_with("bg1:") { ",1" } else { "" };
                    event = format!("\r\nEEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,{target}{campaign}))\")")
                        .into_bytes();
                    assert_eq!(count(changed, &event), 1);
                    assert!(!contains(changed, text(spec, "recipient_local").as_bytes()));
                    assert!(!contains(changed, text(spec, "event_global").as_bytes()));
                    restored = replace(changed, &event, b"", Some(1));
                    if let (true, Some(action_refs)) = (selected, action_refs) {
                        for (old, new) in action_refs {
                            let new_text = format!("DisplayStringHead(Myself,{new})").into_bytes();
                            assert_eq!(count(&restored, &new_text), 1);
                            restored = replace(&restored, &new_text, format!("DisplayStringHead(Myself,{old})").as_bytes(), Some(1));
                        }
                    }
                } else {
                    // The grant's own recipient, as this reply names it.
                    let marker = [
                        b"ActionOverride(".as_slice(),
                        &recipient,
                        format!(",SetGlobal(\"{}\",\"LOCALS\",1))\r\n", str_value(spec.get("recipient_local").unwrap())).as_bytes(),
                    ]
                    .concat();
                    event = format!("\r\nSetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "event_global")).into_bytes();
                    assert_eq!(count(changed, &marker), 1);
                    assert_eq!(count(changed, &event), 1);
                    assert!(index(changed, &marker) < index(changed, &event));
                    restored = replace(&replace(changed, &marker, b"", Some(1)), &event, b"", Some(1));
                }
                if selected {
                    assert!(!contains(changed, &grant));
                    let consumed = format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "consumed_global")).into_bytes();
                    restored = replace(&restored, &consumed, &grant, Some(1));
                }
                assert_eq!(&restored, original_action, "{name} {state_index} {ordinal} {selected}");
                if contains(changed, b"EscapeArea()") {
                    assert!(index(changed, &event) < index(changed, b"EscapeArea()"));
                }
                if contains(changed, b"DestroySelf()") {
                    assert!(index(changed, &event) < index(changed, b"DestroySelf()"));
                }
            }
        }
        assert_eq!(cursor, new.len());
    }
}

/// The transfer by which the prince hands over what he carries for row 59.
pub const PRINCE_TRANSFER_59: &[u8] = b"EEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,59))\")\r\n";
/// The one for row 60, which follows it.
pub const PRINCE_TRANSFER_60: &[u8] = b"EEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,60))\")\r\n";
/// Both, as a reply that ends his job begins.
pub const PRINCE_TRANSFER: &[u8] = b"EEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,59))\")\r\n\
EEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,60))\")\r\n";

/// `check_job_end(original, result)`: the prince's dialogue as his routes
/// patch it. Each reply that ends his job hands over what he carries for both
/// his places (rows 59 and 60, in that order) before its own actions, and his
/// departures after it wait for that; nothing else changes. Returns the
/// replies that hand over.
pub fn check_job_end(original: &[u8], result: &[u8]) -> BTreeSet<(usize, usize)> {
    assert_eq!(PRINCE_TRANSFER, [PRINCE_TRANSFER_59, PRINCE_TRANSFER_60].concat());
    assert_eq!(specs("dlg:sahpr2").iter().map(target_number).collect::<Vec<_>>(), ["59", "60"]);
    let before = Dialogue::new(original, "sahpr2").unwrap();
    let after = Dialogue::new(result, "sahpr2").unwrap();
    assert_eq!(after.state_triggers, before.state_triggers);
    assert_eq!(after.triggers, before.triggers);
    assert_eq!(after.actions[..before.actions.len()], before.actions[..]);
    assert_eq!(after.states.len(), before.states.len());
    let mut handed = BTreeSet::new();
    for (state_index, state) in before.states.iter().enumerate() {
        assert_eq!(after.states[state_index][..4], state[..4]);
        assert_eq!(after.states[state_index][8..], state[8..]);
        for (ordinal, (old, new)) in outgoing(&before, state_index).iter().zip(outgoing(&after, state_index)).enumerate() {
            if &new == old {
                continue;
            }
            assert_eq!((&new[..16], &new[20..]), (&old[..16], &old[20..]));
            let original_action = &before.actions[u32_at(old, 16) as usize];
            let mut changed = after.actions[u32_at(&new, 16) as usize].clone();
            assert!(!splitlines_bytes(&replace(&changed, TRGENI_DEPARTURE, b"", None)).contains(&&b"DestroySelf()"[..]));
            if changed.starts_with(PRINCE_TRANSFER) {
                handed.insert((state_index, ordinal));
                changed.drain(..PRINCE_TRANSFER.len());
            }
            assert!(!contains(&changed, b"FLPR_Transfer"));
            assert_eq!(&replace(&changed, TRGENI_DEPARTURE, b"DestroySelf()", None), original_action);
        }
    }
    handed
}

/// `check_pair(name, original, result)`: two routes whose grants share every
/// granting reply, four campaign combinations each.
pub fn check_pair(name: &str, original: &[u8], result: &[u8]) {
    let before = Dialogue::new(original, name).unwrap();
    let after = Dialogue::new(result, name).unwrap();
    let specs = specs(&format!("dlg:{name}"));
    assert_eq!(specs.iter().map(|spec| text(spec, "quest")).collect::<Vec<_>>(), [name, name]);
    let shared: BTreeSet<(usize, usize)> = branches(&specs[0]).into_iter().collect();
    assert_eq!(branches(&specs[1]).into_iter().collect::<BTreeSet<_>>(), shared);
    for state in 0..before.states.len() {
        let (old, new) = (outgoing(&before, state), outgoing(&after, state));
        let ordinals: BTreeSet<usize> =
            shared.iter().filter(|(granting, _)| *granting == state).map(|(_, ordinal)| *ordinal).collect();
        if ordinals.is_empty() {
            assert_eq!(new, old);
            continue;
        }
        assert_eq!(new.len(), old.len() + 3 * ordinals.len());
        let mut cursor = 0;
        for (ordinal, transition) in old.iter().enumerate() {
            if !ordinals.contains(&ordinal) {
                assert_eq!(&new[cursor], transition);
                cursor += 1;
                continue;
            }
            let flags = u32_at(transition, 0);
            let prior = if flags & 2 != 0 { before.triggers[u32_at(transition, 12) as usize].clone() } else { Vec::new() };
            let original_actions = &before.actions[u32_at(transition, 16) as usize];
            for (bits, branch) in new[cursor..cursor + 4].iter().enumerate() {
                let mut condition = prior.clone();
                let mut restored = after.actions[u32_at(branch, 16) as usize].clone();
                assert_eq!(branch[4..12], transition[4..12]);
                assert_eq!(branch[20..], transition[20..]);
                for (spec, selected) in specs.iter().zip([bits & 2 != 0, bits & 1 != 0]) {
                    condition = guarded(&condition, &guard(text(spec, "selected_global"), selected));
                    let (grant, recipient) = grant_in(original_actions, spec);
                    let marker = [
                        b"ActionOverride(".as_slice(),
                        &recipient,
                        format!(",SetGlobal(\"{}\",\"LOCALS\",1))\r\n", text(spec, "recipient_local")).as_bytes(),
                    ]
                    .concat();
                    let event = format!("\r\nSetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "event_global")).into_bytes();
                    assert_eq!(count(&restored, &marker), 1);
                    assert_eq!(count(&restored, &event), 1);
                    restored = replace(&replace(&restored, &marker, b"", Some(1)), &event, b"", Some(1));
                    if selected {
                        assert!(!contains(&restored, &grant));
                        let consumed = format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "consumed_global")).into_bytes();
                        restored = replace(&restored, &consumed, &grant, Some(1));
                    } else {
                        assert!(contains(&restored, &grant));
                    }
                }
                // A treasury's handover follows the guards, in every variant.
                for spec in &specs {
                    if spec.get("handover").is_some_and(Value::truthy) {
                        condition = guarded(&condition, &handover_line(spec));
                    }
                }
                assert_eq!(after.triggers[u32_at(branch, 12) as usize], condition);
                assert_eq!(&restored, original_actions);
            }
            cursor += 4;
        }
        assert_eq!(cursor, new.len());
    }
}

/// `check_text_graph(resource, original, result, refs, naming)`; returns the
/// clones.
pub fn check_text_graph(
    resource: &str,
    original: &[u8],
    result: &[u8],
    refs: &BTreeMap<i64, i64>,
    naming: Naming<'_>,
) -> BTreeMap<usize, usize> {
    let name = resource.split(':').nth(1).unwrap();
    let specs = resource_specs(resource, naming).unwrap();
    // Grant branching is checked independently. Check every text-route edge
    // against that graph, including unchanged external entry states.
    let action_refs: Vec<(i64, i64)> = action_text_refs(resource).into_iter().map(|old| (old, refs[&old])).collect();
    let before = Dialogue::new(&patch_dialogue(original, &specs, &action_refs, true, true, None).unwrap(), name).unwrap();
    let after = Dialogue::new(result, name).unwrap();
    let flag = text(&specs[0], "selected_global");
    let mut clones = BTreeMap::new();
    for (state_index, state) in before.states.iter().enumerate() {
        // A reply's own text (flag 1) and its journal entry (flag 0x10).
        let affected = refs.contains_key(&(u32_at(state, 0) as i64))
            || outgoing(&before, state_index).iter().any(|row| {
                [(1, 4), (0x10, 8)]
                    .iter()
                    .any(|&(bit, offset)| u32_at(row, 0) & bit != 0 && refs.contains_key(&(u32_at(row, offset) as i64)))
            });
        if affected {
            let clone = before.states.len() + clones.len();
            clones.insert(state_index, clone);
        }
    }
    assert_eq!(after.states.len(), before.states.len() + clones.len());
    assert_eq!(after.actions, before.actions);
    let lookup = |value: u32| refs.get(&(value as i64)).map_or(value, |new| *new as u32);
    for (state_index, state) in before.states.iter().enumerate() {
        let mut destinations = vec![(state_index, false)];
        if let Some(clone) = clones.get(&state_index) {
            destinations.push((*clone, true));
        }
        let (old_text, old_entry) = (u32_at(state, 0), u32_at(state, 12));
        for (destination, selected_text) in destinations {
            let (new_text, entry) = (u32_at(&after.states[destination], 0), u32_at(&after.states[destination], 12));
            assert_eq!(new_text, if selected_text { lookup(old_text) } else { old_text });
            if old_entry != 0xFFFF_FFFF && clones.contains_key(&state_index) {
                let expected = guarded(&before.state_triggers[old_entry as usize], &guard(flag, selected_text));
                assert_eq!(after.state_triggers[entry as usize], expected);
            } else if old_entry != 0xFFFF_FFFF {
                // Another start state keeps its condition; a gated pair before it moves it.
                assert_eq!(after.state_triggers[entry as usize], before.state_triggers[old_entry as usize]);
            } else {
                assert_eq!(entry, old_entry);
            }
            let new_rows = outgoing(&after, destination);
            let mut cursor = 0;
            for row in outgoing(&before, state_index) {
                let (flags, reply, journal, trigger, action_id) =
                    (u32_at(&row, 0), u32_at(&row, 4), u32_at(&row, 8), u32_at(&row, 12), u32_at(&row, 16));
                let (next_dialogue, next_state) = (&row[20..28], u32_at(&row, 28));
                let trimmed: Vec<u8> = next_dialogue.iter().copied().take_while(|byte| *byte != 0).collect();
                let internal = trimmed.to_ascii_lowercase() == name.as_bytes();
                let reroute = flags & 8 == 0 && internal && clones.contains_key(&(next_state as usize));
                let edges: &[bool] = if reroute { &[false, true] } else { &[false] };
                for &selected_edge in edges {
                    let new = &new_rows[cursor];
                    cursor += 1;
                    let expected_reply = if selected_text && flags & 1 != 0 { lookup(reply) } else { reply };
                    let expected_journal = if selected_text && flags & 0x10 != 0 { lookup(journal) } else { journal };
                    assert_eq!(
                        (u32_at(new, 0), u32_at(new, 4), u32_at(new, 8), u32_at(new, 16), &new[20..28]),
                        (if reroute { flags | 2 } else { flags }, expected_reply, expected_journal, action_id, next_dialogue)
                    );
                    let expected_state =
                        if reroute && selected_edge { clones[&(next_state as usize)] as u32 } else { next_state };
                    assert_eq!(u32_at(new, 28), expected_state);
                    if reroute {
                        let prior = if flags & 2 != 0 { before.triggers[trigger as usize].clone() } else { Vec::new() };
                        assert_eq!(after.triggers[u32_at(new, 12) as usize], guarded(&prior, &guard(flag, selected_edge)));
                    } else {
                        assert_eq!(u32_at(new, 12), trigger);
                    }
                }
            }
            assert_eq!(cursor, new_rows.len());
        }
    }
    // The engine tries start states in the order of their conditions: each
    // gated pair takes the original condition's place.
    let start_order = |dialogue: &Dialogue| -> Vec<usize> {
        let mut entries: Vec<(u32, usize)> =
            dialogue.states.iter().enumerate().map(|(index, state)| (u32_at(state, 12), index)).collect();
        entries.sort_unstable();
        entries.into_iter().filter(|(entry, _)| *entry != 0xFFFF_FFFF).map(|(_, index)| index).collect()
    };
    let expected: Vec<usize> = start_order(&before)
        .into_iter()
        .flat_map(|state| std::iter::once(state).chain(clones.get(&state).copied()))
        .collect();
    assert_eq!(start_order(&after), expected);
    clones
}
