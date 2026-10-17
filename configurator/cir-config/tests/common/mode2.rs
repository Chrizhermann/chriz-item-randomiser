//! The reference's Mode 2 test builders and graph checks
//! (`tests/test_mode2_rewards.py`), shared by the Mode 2 parity binaries.

use super::builders::{contains, count, index, outgoing, replace, u32_at};
use cir_config::json::{Dict, Value};
use cir_config::mode2_rewards::{dialogue_grant, patch_dialogue, resource_specs, ACTION_TEXT_REFS};
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

/// `spec["target_ids"][0].rsplit(":", 1)[1]`.
pub fn target_number(spec: &Dict) -> &str {
    let id = spec.get("target_ids").and_then(Value::as_list).unwrap()[0].as_str().unwrap();
    id.rsplit(':').next().unwrap()
}

pub fn specs(resource: &str) -> Vec<Dict> {
    resource_specs(resource).unwrap()
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
                    if branches(grant_spec).contains(&(state, ordinal)) {
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
            transitions.push(dialogue.transition(Some(2), Some(9876), &trigger, &action, Some(12345)).unwrap());
        }
        dialogue.state(555 + state as u32, transitions);
    }
    dialogue.render()
}

pub fn obj(selector: u32) -> String {
    format!("0 0 0 0 0 0 0 {selector} 0 0 0 0 \"\"OB\n")
}

/// `action(opcode, value=0, text="", target=0)`.
pub fn action(opcode: u32, value: i64, text: &str, target: u32) -> String {
    format!("AC\n{opcode}OB\n{}OB\n{}OB\n{}{value} 0 0 0 0\"{text}\" \"\" AC\n", obj(0), obj(target), obj(0))
}

pub fn block(actions: &str) -> String {
    let condition = format!("TR\n16399 1 0 0 0 \"AR1202Quest\" \"\" OB\n{}TR\n", obj(0));
    format!("CR\nCO\n{condition}CO\nRS\nRE\n100{actions}RE\nRS\nCR\n")
}

/// `script_fixture(name)` for asysta01 or ddwell.
pub fn script_fixture(name: &str) -> Vec<u8> {
    if name == "asysta01" {
        let actions = [action(164, 20000, "", 0), action(82, 0, "Helm19", 0), action(82, 0, "Misc8y", 0), action(30, 2, "AR1512PlantedItems", 0)].concat();
        return format!("SC\n{}SC\n", block(&actions)).into_bytes();
    }
    let other = block(&action(151, 40849, "", 1));
    let well = block(&[action(30, 2, "AR1202DragonWell", 0), action(151, 40850, "", 1), action(140, 0, "helm21", 21), action(177, 0, "", 0)].concat());
    format!("SC\n{other}{well}{other}SC\n").into_bytes()
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

/// `check_dialogue(name, original, result, action_refs)`.
pub fn check_dialogue(name: &str, original: &[u8], result: &[u8], action_refs: Option<&BTreeMap<i64, i64>>) {
    let spec = &specs(&format!("dlg:{name}"))[0];
    let reviewed = branches(spec);
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
            for selected in [false, true] {
                let branch = &new[cursor];
                cursor += 1;
                assert_eq!(u32_at(branch, 0), flags | 2);
                assert_eq!(branch[4..12], transition[4..12]); // Reply and journal.
                assert_eq!(branch[20..], transition[20..]); // Link/exit state.
                let trigger = &after.triggers[u32_at(branch, 12) as usize];
                assert_eq!(trigger, &[prior.as_slice(), &guard(text(spec, "selected_global"), selected)].concat());
                let changed = &after.actions[u32_at(branch, 16) as usize];
                let event: Vec<u8>;
                let mut restored;
                if spec.get("inventory_target").is_some_and(Value::truthy) {
                    let target = target_number(spec);
                    event = format!("\r\nEEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,{target}))\")").into_bytes();
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
                    let marker = format!(
                        "ActionOverride({},SetGlobal(\"{}\",\"LOCALS\",1))\r\n",
                        str_value(spec.get("recipient").unwrap()),
                        str_value(spec.get("recipient_local").unwrap())
                    )
                    .into_bytes();
                    event = format!("\r\nSetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "event_global")).into_bytes();
                    assert_eq!(count(changed, &marker), 1);
                    assert_eq!(count(changed, &event), 1);
                    restored = replace(&replace(changed, &marker, b"", Some(1)), &event, b"", Some(1));
                }
                let grant = dialogue_grant(original_action, spec).unwrap();
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

/// `check_aran(original, result)`: two grants, four campaign combinations.
pub fn check_aran(original: &[u8], result: &[u8]) {
    let before = Dialogue::new(original, "aran").unwrap();
    let after = Dialogue::new(result, "aran").unwrap();
    let specs = specs("dlg:aran");
    assert_eq!(specs.iter().map(|spec| text(spec, "quest")).collect::<Vec<_>>(), ["aran", "aran"]);
    for state in 0..before.states.len() {
        let (old, new) = (outgoing(&before, state), outgoing(&after, state));
        if state != 4 {
            assert_eq!(new, old);
            continue;
        }
        assert_eq!(old.len(), 8);
        assert_eq!(new.len(), 32);
        for (ordinal, transition) in old.iter().enumerate() {
            let flags = u32_at(transition, 0);
            let prior = if flags & 2 != 0 { before.triggers[u32_at(transition, 12) as usize].clone() } else { Vec::new() };
            let original_actions = &before.actions[u32_at(transition, 16) as usize];
            for (bits, branch) in new[ordinal * 4..ordinal * 4 + 4].iter().enumerate() {
                let mut condition = prior.clone();
                let mut restored = after.actions[u32_at(branch, 16) as usize].clone();
                assert_eq!(branch[4..12], transition[4..12]);
                assert_eq!(branch[20..], transition[20..]);
                for (spec, selected) in specs.iter().zip([bits & 2 != 0, bits & 1 != 0]) {
                    condition.extend(guard(text(spec, "selected_global"), selected));
                    let marker = format!(
                        "ActionOverride(LastTalkedToBy,SetGlobal(\"{}\",\"LOCALS\",1))\r\n",
                        text(spec, "recipient_local")
                    )
                    .into_bytes();
                    let event = format!("\r\nSetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "event_global")).into_bytes();
                    assert_eq!(count(&restored, &marker), 1);
                    assert_eq!(count(&restored, &event), 1);
                    restored = replace(&replace(&restored, &marker, b"", Some(1)), &event, b"", Some(1));
                    let grant = dialogue_grant(original_actions, spec).unwrap();
                    if selected {
                        assert!(!contains(&restored, &grant));
                        let consumed = format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "consumed_global")).into_bytes();
                        restored = replace(&restored, &consumed, &grant, Some(1));
                    } else {
                        assert!(contains(&restored, &grant));
                    }
                }
                assert_eq!(after.triggers[u32_at(branch, 12) as usize], condition);
                assert_eq!(&restored, original_actions);
            }
        }
    }
}

/// `check_text_graph(resource, original, result, refs)`; returns the clones.
pub fn check_text_graph(resource: &str, original: &[u8], result: &[u8], refs: &BTreeMap<i64, i64>) -> BTreeMap<usize, usize> {
    let name = resource.split(':').nth(1).unwrap();
    let specs = specs(resource);
    // Grant branching is checked independently. Check every text-route edge
    // against that graph, including unchanged external entry states.
    let action_refs: Vec<(i64, i64)> = action_text_refs(resource).into_iter().map(|old| (old, refs[&old])).collect();
    let before = Dialogue::new(&patch_dialogue(original, &specs, &action_refs, true, true).unwrap(), name).unwrap();
    let after = Dialogue::new(result, name).unwrap();
    let flag = text(&specs[0], "selected_global");
    let mut clones = BTreeMap::new();
    for (state_index, state) in before.states.iter().enumerate() {
        let affected = refs.contains_key(&(u32_at(state, 0) as i64))
            || outgoing(&before, state_index)
                .iter()
                .any(|row| u32_at(row, 0) & 1 != 0 && refs.contains_key(&(u32_at(row, 4) as i64)));
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
                let expected = [before.state_triggers[old_entry as usize].as_slice(), &guard(flag, selected_text)].concat();
                assert_eq!(after.state_triggers[entry as usize], expected);
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
                    assert_eq!(
                        (u32_at(new, 0), u32_at(new, 4), u32_at(new, 8), u32_at(new, 16), &new[20..28]),
                        (if reroute { flags | 2 } else { flags }, expected_reply, journal, action_id, next_dialogue)
                    );
                    let expected_state =
                        if reroute && selected_edge { clones[&(next_state as usize)] as u32 } else { next_state };
                    assert_eq!(u32_at(new, 28), expected_state);
                    if reroute {
                        let prior = if flags & 2 != 0 { before.triggers[trigger as usize].clone() } else { Vec::new() };
                        let expected = [prior.as_slice(), &guard(flag, selected_edge)].concat();
                        assert_eq!(after.triggers[u32_at(new, 12) as usize], expected);
                    } else {
                        assert_eq!(u32_at(new, 12), trigger);
                    }
                }
            }
            assert_eq!(cursor, new_rows.len());
        }
    }
    clones
}
