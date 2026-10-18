//! Python-recorded parity cases for the `mode2_rewards` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_mode2_rewards.py` on the same synthetic resources, plus the
//! direct grant patcher part of `tests/test_original_option_export.py`.
//!
//! The Lua halves of the callback test run `FLPR_Transfer` chunks on Lua VMs
//! and stay with the reference. The reviewed EET resources are read from the
//! private installed-resource cache under `work/` when it is there, as the
//! reference test does.

mod common;

use cir_config::error::{Error, ErrorKind};
use cir_config::json::{Dict, Value};
use cir_config::mode2_rewards::{
    direct_rewards, own_patch, patch_dialogue, patch_reward, physical_source_rewards, prepare_ddwell_text, prepare_reward_texts,
    resource_rewards, resource_specs, rewards, route_reasons, script_original, source_rewards, source_spec, target_spec, text_original,
    text_refs, transfer_spec, transfer_specs, TEXT_REFS,
};
use cir_config::py::{put_u32, str_value};
use cir_config::quest_options::Dialogue;
use cir_config::quest_rewards::blocks;
use cir_config::script_text::script_blocks;
use common::builders::{contains, count, index, make_tlk, outgoing, replace, sha, tlk_text, u32_at};
use common::mode2::{
    action, action_with, alternatives, block, branches, check_aran, check_dialogue, check_text_graph, dialogue_fixture,
    layout_dialogue, obj, script_fixture, specs, target_number, text, text_variant, well_reward, with_actions, LayoutReply,
};
use std::collections::BTreeMap;
use std::path::PathBuf;

const SCENARIO: &str = "mode2_rewards";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

fn list(values: &[&str]) -> Value {
    Value::from(values.to_vec())
}

fn ints(values: &[i64]) -> Value {
    Value::from(values.to_vec())
}

fn assert_profile(error: Error, needle: &str) {
    assert_eq!(error.kind, ErrorKind::Profile, "{error:?}");
    assert!(error.message.contains(needle), "{needle}: {}", error.message);
}

fn reward(name: &str) -> &'static Dict {
    rewards().get(name).and_then(Value::as_dict).unwrap()
}

fn reasons(resource: &str, data: &[u8]) -> BTreeMap<String, String> {
    route_reasons(resource, data).unwrap().iter().map(|(route, reason)| (route.to_string(), str_value(reason))).collect()
}

fn named(rows: &[(&str, &str)]) -> BTreeMap<String, String> {
    rows.iter().map(|(route, reason)| (route.to_string(), reason.to_string())).collect()
}

fn crlf(data: impl AsRef<[u8]>) -> Vec<u8> {
    replace(data.as_ref(), b"\n", b"\r\n", None)
}

fn text_dialogues() -> Vec<&'static str> {
    TEXT_REFS.iter().map(|(key, _)| *key).filter(|key| key.starts_with("dlg:")).collect()
}

/// The synthetic originals of the generator (`mode2_inputs`).
fn originals() -> Vec<(String, Vec<u8>)> {
    let mut result = Vec::new();
    for resource in resource_rewards().keys() {
        if resource.starts_with("dlg:") {
            let data = if text_refs(resource).is_some() { text_variant(resource) } else { dialogue_fixture(&specs(resource)) };
            result.push((resource.to_string(), data));
        }
    }
    result.push(("bcs:asysta01".into(), script_fixture("asysta01")));
    result.push(("bcs:ddwell".into(), script_fixture("ddwell")));
    result
}

/// The Beamdog-compiled UHOGRE01 of
/// test_grants_are_found_by_meaning_in_another_layout_with_another_mods_replies.
fn ogre_states() -> Vec<Vec<LayoutReply<'static>>> {
    let mut states: Vec<Vec<LayoutReply>> = (0..9).map(|_| vec![("", "SetGlobal(\"OgreTalk\",\"LOCALS\",1)", Some(9))]).collect();
    states.push(vec![
        (
            "PartyHasItem(\"misc5x\")",
            "TakePartyItem(\"misc5x\")\nGiveItemCreate(\"SHLD26\",LastTalkedToBy,0,0,0)\nAddexperienceParty(9000)",
            None,
        ),
        ("!PartyHasItem(\"misc5x\")", "GIVEITEMCREATE(\"shld26\",Player1,1,0,0)\nEscapeArea()", None),
        ("Global(\"OtherMod\",\"GLOBAL\",1)", "SetGlobal(\"OtherMod\",\"GLOBAL\",2)", Some(12)),
    ]);
    states.push(vec![("", "", None)]);
    states.push(vec![("", "", None)]);
    states.push(vec![(
        "",
        " GiveItemCreate( \"Shld26\" , LastTalkedToBy , 0 , 0 , 0 ) \nSetGlobal(\"OtherMod\",\"GLOBAL\",3)",
        None,
    )]);
    states
}

/// The well of test_script_blocks_are_found_by_meaning_in_another_layout,
/// with LF line ends.
fn well_layout() -> Vec<u8> {
    let added = block(&action(30, 1, "OtherModWell", 0, 0), "OtherModQuest");
    let second = block(&(action(140, 0, "HELM21", 17, 0) + &action(30, 3, "OtherModWell", 0, 0)), "OtherModQuest");
    format!("SC\n{added}{}{second}SC\n", block(&well_reward(17, 40850), "AR1202Quest")).into_bytes()
}

const GRANT: &str = "GiveItemCreate(\"shld26\",LastTalkedToBy,0,0,0)";

/// New actions of replies, by (state, reply).
type Changes = Vec<((usize, usize), Vec<u8>)>;

/// The replies of test_routes_the_check_cannot_recognise_name_their_reason,
/// by the generator's labels, and the reason each gives.
fn ogre_reasons() -> Vec<(&'static str, Changes, &'static str)> {
    vec![
        (
            "no grant",
            vec![((22, 0), b"GiveGoldForce(500)".to_vec()), ((22, 1), b"GiveGoldForce(500)".to_vec())],
            "UHOGRE01.DLG has no reply that gives SHLD26",
        ),
        ("twice", vec![((22, 0), format!("{GRANT}\r\n{GRANT}").into_bytes())], "UHOGRE01.DLG state 22 gives SHLD26 twice in one reply"),
        (
            "given other",
            vec![((22, 1), format!("{GRANT}\r\nGiveItem(\"shld26\",Player2)").into_bytes())],
            "UHOGRE01.DLG state 22 also gives SHLD26 with GiveItem(), which the route cannot gate",
        ),
        (
            "take replace",
            vec![((23, 0), b"TakeItemReplace(\"Shld26\",\"misc5x\",LastTalkedToBy)".to_vec())],
            "UHOGRE01.DLG state 23 also gives SHLD26 with TakeItemReplace(), which the route cannot gate",
        ),
        (
            "override",
            vec![((22, 0), b"ActionOverride(Player2,GiveItemCreate(\"shld26\",Myself,0,0,0))".to_vec())],
            "UHOGRE01.DLG state 22 gives SHLD26 through ActionOverride(), whose recipient the route cannot mark",
        ),
        (
            "unreadable",
            vec![((22, 0), b"GiveItemCreate(\"shld26\",LastTalkedToBy,0,0,0".to_vec())],
            "UHOGRE01.DLG state 22 names SHLD26 in actions that cannot be read",
        ),
        ("no recipient", vec![((22, 0), b"GiveItemCreate(\"shld26\")".to_vec())], "UHOGRE01.DLG state 22 gives SHLD26 without a recipient"),
    ]
}

fn ogre_with(changes: &[((usize, usize), Vec<u8>)]) -> Vec<u8> {
    let changes: Vec<((usize, usize), &[u8])> = changes.iter().map(|(at, text)| (*at, text.as_slice())).collect();
    with_actions(&dialogue_fixture(&specs("dlg:uhogre01")), "uhogre01", &changes)
}

/// The compiled scripts of test_routes_the_check_cannot_recognise_name_their_reason.
fn well_reasons() -> Vec<(&'static str, Vec<u8>, &'static str)> {
    let other = block(&action(151, 40849, "", 1, 0), "AR1202Quest");
    let well = |blocks: &[String]| format!("SC\n{}SC\n", blocks.concat()).into_bytes();
    let reward_block = |actions: String| block(&actions, "AR1202Quest");
    let grant = action(140, 0, "helm21", 21, 0);
    vec![
        ("no block", well(&[other.clone(), other.clone()]), "DDWELL.BCS has no block that gives HELM21"),
        (
            "twice",
            well(&[other.clone(), reward_block(well_reward(21, 40850) + &action(140, 0, "HELM21", 21, 0))]),
            "DDWELL.BCS block 1 gives HELM21 twice in one response",
        ),
        (
            "another actor",
            well(&[other.clone(), reward_block(well_reward(21, 40850).replace(&grant, &action(140, 0, "helm21", 21, 17)))]),
            "DDWELL.BCS block 1 gives HELM21 through ActionOverride(), whose recipient the route cannot mark",
        ),
        (
            "other action",
            well(&[other.clone(), reward_block(well_reward(21, 40850) + &action(82, 0, "helm21", 0, 0))]),
            "DDWELL.BCS block 1 also gives HELM21 with CreateItem(), which the route cannot gate",
        ),
        (
            "add store",
            well(&[
                other.clone(),
                alternatives(&[&well_reward(21, 40850), &action_with(363, 0, "shop01", 0, 0, "helm21")], "AR1202Quest", 50),
            ]),
            "DDWELL.BCS block 1 also gives HELM21 with AddStoreItem(), which the route cannot gate",
        ),
        (
            "other text",
            well(&[other.clone(), reward_block(well_reward(21, 40849))]),
            "DDWELL.BCS no longer shows the text that names the reward (40850)",
        ),
        ("open", b"SC\nCR\nCO\nSC\n".to_vec(), "DDWELL.BCS cannot be read as a compiled script: its last block is not closed"),
        (
            "mixed",
            replace(&script_fixture("ddwell"), b"\n", b"\r\n", Some(3)),
            "DDWELL.BCS cannot be read as a compiled script: its line endings are mixed",
        ),
    ]
}

fn asysta_other_action() -> Vec<u8> {
    replace(
        &script_fixture("asysta01"),
        action(82, 0, "Helm19", 0, 0).as_bytes(),
        action(140, 0, "Helm19", 0, 0).as_bytes(),
        None,
    )
}

/// ARAN without the AMUL21 grant, and without both grants.
fn aran_without() -> (Vec<u8>, Vec<u8>) {
    let mut dialogue = Dialogue::new(&dialogue_fixture(&specs("dlg:aran")), "aran").unwrap();
    let mut rendered = ["amul21", "ring07"].into_iter().map(|item| {
        let grant = format!("GiveItemCreate(\"{item}\",LastTalkedToBy,0,0,0)\r\r\n");
        dialogue.actions = dialogue.actions.iter().map(|text| replace(text, grant.as_bytes(), b"", None)).collect();
        dialogue.render()
    });
    let without = rendered.next().unwrap();
    (without, rendered.next().unwrap())
}

#[test]
fn builders_match_the_reference_builders() {
    let fixture = common::load(SCENARIO);
    for (resource, data) in originals() {
        assert_eq!(fixture.input(&format!("patch_reward {resource}"), "data"), data, "{resource}");
    }
    for (beamdog, label) in [(true, "True"), (false, "False")] {
        assert_eq!(fixture.input(&format!("patch_reward layout {label}"), "data"), layout_dialogue("uhogre01", &ogre_states(), beamdog));
    }
    assert_eq!(fixture.input("patch_reward script layout lf", "data"), well_layout());
    assert_eq!(fixture.input("patch_reward script layout crlf", "data"), crlf(well_layout()));
    for (label, changes, _) in ogre_reasons() {
        assert_eq!(fixture.input(&format!("own_patch uhogre01 {label}"), "data"), ogre_with(&changes), "{label}");
    }
    for (label, data, _) in well_reasons() {
        assert_eq!(fixture.input(&format!("own_patch ddwell {label}"), "data"), data, "{label}");
    }
    assert_eq!(fixture.input("own_patch asysta01 other action", "data"), asysta_other_action());
    let (without, neither) = aran_without();
    assert_eq!(fixture.input("own_patch aran without amul21", "data"), without);
    assert_eq!(fixture.input("own_patch aran neither", "data"), neither);
}

#[test]
fn graph_checks_reject_an_unpatched_dialogue() {
    let original = dialogue_fixture(&specs("dlg:ppstat01"));
    let outcome = std::panic::catch_unwind(|| check_dialogue("ppstat01", &original, &original, None, None));
    assert!(outcome.is_err(), "check_dialogue accepted unbranched rewards");
}

/// `synthetic_patch(name, original)`: the patch, its re-export, and what the
/// check reads from both and from a changed copy.
fn synthetic_patch(name: &str, original: &[u8]) -> Vec<u8> {
    let result = patch_reward(name, original, None, Some(100001)).unwrap();
    assert_eq!(patch_reward(name, &result, None, None).unwrap(), result);
    assert!(!own_patch(name, original).unwrap());
    assert!(own_patch(name, &result).unwrap());
    assert!(reasons(name, original).is_empty());
    assert!(reasons(name, &result).is_empty());
    let tampered = replace(&result, b"FLPR_", b"OTHER", Some(1));
    assert_profile(patch_reward(name, &tampered, None, None).unwrap_err(), "carries a changed copy of the reward route's own patch");
    assert!(reasons(name, &tampered).values().next().unwrap().contains("changed copy"));
    result
}

#[test]
fn dialogue_both_campaign_routes_keep_exact_recipient_and_surrounding_actions() {
    for resource in resource_rewards().keys() {
        if resource.starts_with("dlg:") && resource != "dlg:aran" && text_refs(resource).is_none() {
            let name = &resource[4..];
            let original = dialogue_fixture(&specs(resource));
            check_dialogue(name, &original, &synthetic_patch(name, &original), None, None);
        }
    }
}

#[test]
fn grants_are_found_by_meaning_in_another_layout_with_another_mods_replies() {
    // Beamdog-compiled texts without trailing line breaks, other strrefs and
    // state numbers, letter case and spacing, another recipient and charges.
    // Another mod added a reply that gives nothing and a path that gives the
    // reward too: every reply that gives it is gated, nothing else changes.
    for beamdog in [true, false] {
        let original = layout_dialogue("uhogre01", &ogre_states(), beamdog);
        let result = synthetic_patch("uhogre01", &original);
        check_dialogue("uhogre01", &original, &result, None, Some(&[(9, 0), (9, 1), (12, 0)]));
        let after = Dialogue::new(&result, "uhogre01").unwrap();
        // Both gated replies became two each; the added reply follows.
        let actions = &after.actions[u32_at(&outgoing(&after, 9)[4], 16) as usize];
        let end = actions.len() - actions.iter().rev().take_while(|byte| matches!(byte, b'\r' | b'\n')).count();
        assert_eq!(&actions[..end], b"SetGlobal(\"OtherMod\",\"GLOBAL\",2)");
        assert!(contains(&after.actions.concat(), b"ActionOverride(Player1,SetGlobal(\"FLPR_RM2_14\",\"LOCALS\",1))"));
    }
}

#[test]
fn routes_the_check_cannot_recognise_name_their_reason() {
    for (_, changes, reason) in ogre_reasons() {
        let data = ogre_with(&changes);
        assert_eq!(reasons("dlg:uhogre01", &data), named(&[("uhogre01", reason)]));
        assert!(!own_patch("dlg:uhogre01", &data).unwrap());
        assert_profile(patch_reward("dlg:uhogre01", &data, None, None).unwrap_err(), reason);
    }
    let broken = [b"DLG V1.0".as_slice(), &[0; 10]].concat();
    assert_eq!(reasons("dlg:uhogre01", &broken), named(&[("uhogre01", "UHOGRE01.DLG cannot be read as a dialogue")]));
    // A text route needs the text that names the reward where it gives it.
    let plain = dialogue_fixture(&specs("dlg:ppdjinn"));
    assert_eq!(
        reasons("dlg:ppdjinn", &plain),
        named(&[("ppdjinn", "PPDJINN.DLG no longer shows the text that names the reward (48995)")])
    );
    // Compiled scripts.
    for (_, data, reason) in well_reasons() {
        assert_eq!(reasons("bcs:ddwell", &data), named(&[("ddwell", reason)]));
        assert_profile(patch_reward("bcs:ddwell", &data, None, Some(100001)).unwrap_err(), reason);
    }
    assert_eq!(
        reasons("bcs:asysta01", &asysta_other_action()),
        named(&[("asysta01", "ASYSTA01.BCS block 0 also gives HELM19 with GiveItemCreate(), which the route cannot gate")])
    );
}

/// The replies and script of
/// test_actions_that_only_take_show_or_test_the_reward_never_stop_a_route.
fn only_taken() -> (Vec<u8>, Vec<u8>) {
    let dialogue = ogre_with(&[
        ((22, 1), format!("TakePartyItem(\"misc5x\")\r\n{GRANT}\r\nDestroyItem(\"SHLD26\")").into_bytes()),
        (
            (23, 0),
            b"TakeItemReplace(\"misc5x\",\"shld26\",LastTalkedToBy)\r\nActionOverride(Player2,DestroyItem(\"shld26\"))\r\n\
              SetGlobal(\"SHLD26\",\"GLOBAL\",1)"
                .to_vec(),
        ),
    ]);
    let other = block(&action(151, 40849, "", 1, 0), "AR1202Quest");
    let twice_shown = well_reward(21, 40850) + &action(151, 40850, "", 2, 0) + &action(116, 0, "HELM21", 0, 0);
    let script =
        format!("SC\n{other}{}{other}SC\n", alternatives(&[&well_reward(21, 40850), &twice_shown], "AR1202Quest", 50)).into_bytes();
    (dialogue, script)
}

#[test]
fn actions_that_only_take_show_or_test_the_reward_never_stop_a_route() {
    // After a moved reward these find it gone, as when the player sold it.
    let (data, script) = only_taken();
    let fixture = common::load(SCENARIO);
    assert_eq!(fixture.input("own_patch uhogre01 only taken", "data"), data);
    assert_eq!(fixture.input("own_patch ddwell alternatives", "data"), script);
    assert!(reasons("dlg:uhogre01", &data).is_empty());
    let result = synthetic_patch("uhogre01", &data);
    check_dialogue("uhogre01", &data, &result, None, Some(&[(22, 0), (22, 1)]));
    // A script block chooses one of its responses: each may give the reward
    // once, and every display of the reward's text changes.
    assert!(reasons("bcs:ddwell", &script).is_empty());
    let patched = synthetic_patch("ddwell", &script);
    let blocks = script_blocks(&patched).unwrap().blocks;
    assert_eq!(blocks.len(), 4);
    let (unselected, selected) = (&blocks[1], &blocks[2]);
    let consumed = action(30, 1, "GLOBALFLPR_XM2_27", 0, 0);
    let event = action(30, 1, "GLOBALFLPR_EM2_27", 0, 0);
    assert_eq!(count(unselected, action(140, 0, "helm21", 21, 0).as_bytes()), 2);
    assert_eq!(count(unselected, event.as_bytes()), 2);
    assert_eq!(count(selected, consumed.as_bytes()), 2);
    assert!(!contains(selected, b"\"helm21\""));
    assert_eq!(count(selected, b"151OB"), 3);
    assert_eq!(count(selected, b"100001 0 0 0 0"), 3);
    assert!(contains(selected, action(116, 0, "HELM21", 0, 0).as_bytes()));
}

#[test]
fn a_cloned_start_state_keeps_its_place_among_the_start_conditions() {
    // The engine tries start states in the order of their conditions. The
    // djinn's reward state starts the dialogue; another mod's start state
    // comes after it and must stay after both of its variants.
    let mut d = Dialogue::new(&dialogue_fixture(&specs("dlg:ppdjinn")), "ppdjinn").unwrap();
    put_u32(&mut d.states[0], 0, 48995);
    d.state_triggers.push(b"NumTimesTalkedTo(0)\r\n".to_vec());
    d.state_triggers.push(b"Global(\"OtherModDjinn\",\"GLOBAL\",1)\r\n".to_vec());
    put_u32(&mut d.states[0], 12, 0);
    put_u32(&mut d.states[1], 12, 1);
    let original = d.render();
    let fixture = common::load(SCENARIO);
    assert_eq!(fixture.input("patch_reward start order first", "data"), original);
    let refs = BTreeMap::from([(48995, 100001)]);
    let result = patch_reward("dlg:ppdjinn", &original, Some(&refs), None).unwrap();
    let after = Dialogue::new(&result, "ppdjinn").unwrap();
    let clone = d.states.len();
    assert_eq!(u32_at(&after.states[clone], 0), 100001);
    let entry = |index: usize| u32_at(&after.states[index], 12);
    assert_eq!((entry(0), entry(clone), entry(1)), (0, 1, 2));
    assert_eq!(
        after.state_triggers,
        vec![
            b"NumTimesTalkedTo(0)\r\n!Global(\"FLPR_QM2_24\",\"GLOBAL\",1)\r\n".to_vec(),
            b"NumTimesTalkedTo(0)\r\nGlobal(\"FLPR_QM2_24\",\"GLOBAL\",1)\r\n".to_vec(),
            b"Global(\"OtherModDjinn\",\"GLOBAL\",1)\r\n".to_vec(),
        ]
    );
    assert_eq!(text_original(&result, "dlg:ppdjinn").unwrap(), Some((original, refs.clone())));
    assert_eq!(patch_reward("dlg:ppdjinn", &result, None, None).unwrap(), result);
    // Where every other start condition comes first, the gated pair is
    // appended, as earlier exports did.
    put_u32(&mut d.states[0], 12, 1);
    put_u32(&mut d.states[1], 12, 0);
    assert_eq!(fixture.input("patch_reward start order last", "data"), d.render());
    let after = Dialogue::new(&patch_reward("dlg:ppdjinn", &d.render(), Some(&refs), None).unwrap(), "ppdjinn").unwrap();
    assert_eq!(after.state_triggers[..2], d.state_triggers[..]);
    assert_eq!([1, 0, clone].map(|index| u32_at(&after.states[index], 12)), [0, 2, 3]);
}

#[test]
fn compiled_grants_keep_owner_or_original_object_and_do_not_move_other_rewards() {
    for name in ["asysta01", "ddwell"] {
        let original = script_fixture(name);
        let result = synthetic_patch(name, &original);
        let spec = reward(name);
        let old = blocks(&original, if name == "asysta01" { 1 } else { 3 }, b"\n").unwrap().0;
        let new = blocks(&result, old.len() + 1, b"\n").unwrap().0;
        let at = spec.get("branches").and_then(Value::as_list).unwrap()[0].as_int().unwrap() as usize;
        assert_eq!(new[..at], old[..at]);
        assert_eq!(new[at + 2..], old[at + 1..]);
        for (selected, branch) in new[at..at + 2].iter().enumerate() {
            let grant = if name == "asysta01" { action(82, 0, "Helm19", 0, 0) } else { action(140, 0, "helm21", 21, 0) };
            assert_eq!(count(branch, grant.as_bytes()), if selected == 1 { 0 } else { 1 });
            assert_eq!(count(branch, text(spec, "event_global").as_bytes()), 1);
            assert_eq!(count(branch, text(spec, "consumed_global").as_bytes()), selected);
            if name == "asysta01" {
                assert!(!contains(branch, b"LOCALSFLPR_R"));
                assert!(contains(branch, action(82, 0, "Misc8y", 0, 0).as_bytes()));
                assert!(contains(branch, action(30, 2, "AR1512PlantedItems", 0, 0).as_bytes()));
            } else {
                let marker = format!(
                    "AC\n30OB\n{}OB\n{}OB\n{}1 0 0 0 0\"LOCALS{}\" \"\" AC\n",
                    obj(21),
                    obj(0),
                    obj(0),
                    text(spec, "recipient_local")
                );
                assert!(contains(branch, marker.as_bytes()));
                let event = format!("GLOBAL{}", text(spec, "event_global"));
                assert!(index(branch, marker.as_bytes()) < index(branch, event.as_bytes()));
                let display = action(151, if selected == 1 { 100001 } else { 40850 }, "", 1, 0);
                assert!(contains(branch, display.as_bytes()));
                assert!(contains(branch, action(177, 0, "", 0, 0).as_bytes()));
            }
        }
    }
}

#[test]
fn script_blocks_are_found_by_meaning_in_another_layout() {
    // CRLF line ends, another mod's block before the reward, the reward
    // given to another object, and another mod's second block that gives
    // it without the text: both blocks are gated, the text changes where
    // it is shown, every other block stays as it is.
    let original = crlf(well_layout());
    let result = patch_reward("bcs:ddwell", &original, None, Some(100001)).unwrap();
    assert_eq!(patch_reward("bcs:ddwell", &result, None, None).unwrap(), result);
    assert!(own_patch("bcs:ddwell", &result).unwrap());
    assert_eq!(script_original(&result, &specs("bcs:ddwell")[0]).unwrap(), (original.clone(), Some(100001), true));
    let old = blocks(&original, 3, b"\r\n").unwrap().0;
    let new = blocks(&result, 5, b"\r\n").unwrap().0;
    assert_eq!(new[0], old[0]);
    let spec = reward("ddwell");
    let marker = crlf(format!(
        "AC\n30OB\n{}OB\n{}OB\n{}1 0 0 0 0\"LOCALS{}\" \"\" AC\n",
        obj(17),
        obj(0),
        obj(0),
        text(spec, "recipient_local")
    ));
    for (first, grant) in [(1, crlf(action(140, 0, "helm21", 17, 0))), (3, crlf(action(140, 0, "HELM21", 17, 0)))] {
        let (unselected, selected) = (&new[first], &new[first + 1]);
        assert!(contains(unselected, b"\"GLOBALFLPR_QM2_27\""));
        assert_eq!(count(unselected, &marker), 1);
        assert_eq!(count(selected, &marker), 1);
        assert!(contains(unselected, &grant));
        assert!(!contains(selected, &grant));
        assert!(contains(selected, b"GLOBALFLPR_XM2_27"));
    }
    assert!(contains(&new[2], &crlf(action(151, 100001, "", 1, 0))));
    assert!(contains(&new[1], &crlf(action(151, 40850, "", 1, 0))));
}

#[test]
fn registry_exposes_authored_charges_and_separate_container_recipient() {
    assert_eq!(direct_rewards().len(), 9);
    let mut spec = source_spec("original-source:bg2:mode2:item:28").unwrap();
    let expected = cir_config::dict! {"resref" => "ring31", "expiration" => 0i64, "charges" => ints(&[1, 1, 1]), "flags" => 0i64};
    assert!(spec.get("payload").unwrap().py_eq(&Value::Dict(expected)));
    spec.get_mut("payload").and_then(|payload| payload.get_mut("charges")).and_then(Value::as_list_mut).unwrap()[0] =
        Value::from(99i64);
    let target = target_spec("bg2:mode2:target:35").unwrap();
    assert!(target.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[1, 1, 1])));
    let container = target_spec("bg2:mode2:target:21").unwrap();
    assert_eq!(text(&container, "recipient"), "are:ar1512/container/0");
    assert!(container.get("recipient_local").unwrap().is_null());
    assert_eq!(text(&target_spec("bg2:mode2:target:40").unwrap(), "recipient"), "Player1");
    let temple = target_spec("bg2:mode2:target:48").unwrap();
    assert!(temple.get("resources").unwrap().py_eq(&list(&["dlg:bhoisig", "dlg:bhnalla", "dlg:bharval"])));
    let demon = source_spec("original-source:bg2:mode2:item:65").unwrap();
    let silver = source_spec("original-source:bg2:mode2:item:44").unwrap();
    assert!(demon.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[0, 3, 0])));
    assert_eq!(text(&demon, "recipient"), "Player1");
    assert_eq!(text(&demon, "event_global"), text(&silver, "event_global"));
    assert_eq!(text(&demon, "recipient_local"), text(&silver, "recipient_local"));
    assert_ne!(text(&demon, "selected_global"), text(&silver, "selected_global"));
    for (message, error) in [
        ("Unsupported extended reward source", source_spec("x").unwrap_err()),
        ("Unsupported extended reward recipient", target_spec("x").unwrap_err()),
        ("Unsupported carried reward", transfer_spec("x").unwrap_err()),
        ("Unsupported extended reward resource", resource_specs("dlg:x").unwrap_err()),
    ] {
        assert_profile(error, message);
    }
}

#[test]
fn aran_two_grants_have_all_four_independent_campaign_combinations() {
    let original = dialogue_fixture(&specs("dlg:aran"));
    check_aran(&original, &synthetic_patch("aran", &original));
}

#[test]
fn aran_route_left_out_alone_keeps_the_other_route() {
    let (without, neither) = aran_without();
    let amulet = named(&[("aran2", "ARAN.DLG has no reply that gives AMUL21")]);
    assert_eq!(reasons("dlg:aran", &without), amulet);
    let result = patch_reward("dlg:aran", &without, None, None).unwrap();
    assert!(own_patch("dlg:aran", &result).unwrap());
    assert_eq!(patch_reward("dlg:aran", &result, None, None).unwrap(), result);
    assert_eq!(reasons("dlg:aran", &result), amulet);
    let patched = Dialogue::new(&result, "aran").unwrap().triggers.concat();
    assert!(contains(&patched, b"FLPR_QM2_47"));
    assert!(!contains(&patched, b"FLPR_QM2_56"));
    // Neither recognised: each route names its own reason.
    assert_eq!(
        reasons("dlg:aran", &neither),
        named(&[("aran", "ARAN.DLG has no reply that gives RING07"), ("aran2", "ARAN.DLG has no reply that gives AMUL21")])
    );
}

#[test]
fn text_variants_preserve_original_routes_replies_and_verified_recovery() {
    let resources = text_dialogues();
    let (tlk, allocated) = prepare_reward_texts(&make_tlk(&["Unchanged text"]), resources.clone(), "en_us").unwrap();
    assert_eq!(prepare_reward_texts(&tlk, resources.clone(), "en_us").unwrap(), (tlk.clone(), allocated.clone()));
    assert_eq!(tlk_text(&tlk, 0).as_deref(), Some("Unchanged text"));
    assert!(tlk_text(&tlk, allocated["dlg:ppdjinn"][&48995] as u32).unwrap().contains("<GABBER>"));
    assert_eq!(allocated["dlg:gorcamb"][&63681], allocated["dlg:gorcamb"][&64609]);
    assert_profile(prepare_reward_texts(&tlk, resources.clone(), "de_DE").unwrap_err(), "reward texts exist only in English");
    for resource in resources {
        let name = &resource[4..];
        let original = text_variant(resource);
        let refs = &allocated[resource];
        let result = patch_reward(resource, &original, Some(refs), None).unwrap();
        check_text_graph(resource, &original, &result, refs);
        assert_eq!(text_original(&result, resource).unwrap(), Some((original.clone(), refs.clone())));
        assert!(!own_patch(resource, &original).unwrap());
        assert!(own_patch(resource, &result).unwrap());
        assert!(reasons(resource, &result).is_empty());
        assert_eq!(patch_reward(resource, &result, None, None).unwrap(), result);
        let moved: BTreeMap<i64, i64> = refs.iter().map(|(old, new)| (*old, new + 1000)).collect();
        let revised = patch_reward(resource, &result, Some(&moved), None).unwrap();
        assert_eq!(text_original(&revised, resource).unwrap(), Some((original.clone(), moved.clone())));
        check_text_graph(resource, &original, &revised, &moved);
        // A changed reply or recovery record is not our patch any more.
        let mut flipped = result.clone();
        let at = flipped.len() - 20;
        flipped[at] ^= 1;
        for damaged in [replace(&result, b"FLPR_", b"OTHER", Some(1)), flipped] {
            assert_profile(own_patch(resource, &damaged).unwrap_err(), "carries a changed copy");
            assert!(reasons(resource, &damaged)[name].contains("changed copy"));
        }
        assert_profile(patch_reward(resource, &original, None, None).unwrap_err(), "allocated");
    }
    assert!(target_spec("bg2:mode2:target:22").unwrap().get("required_transfers").unwrap().py_eq(&list(&["scyarryl"])));
    assert_eq!(text(reward("sctelwyn"), "event_global"), "FLPR_EA_SCULPTOR");
    assert_eq!(text(reward("scsain"), "event_global"), "FLPR_EA_SCULPTOR");
}

#[test]
fn sculptor_transfer_is_source12_auxiliary_copy_and_preserves_ordinary_giveitem() {
    let spec = transfer_spec("scyarryl").unwrap();
    let authored = "original-source:bg2:mode2:item:12";
    assert!(spec.get("authored_source_ids").unwrap().py_eq(&list(&[authored])));
    assert!(spec.get("source_ids").unwrap().py_eq(&list(&[])));
    assert_eq!(source_rewards().get(authored).and_then(Value::as_str), Some("bhoisig"));
    assert!(spec.get("physical_source_ids").unwrap().py_eq(&list(&["cre:scyarryl/item/0"])));
    let record = cir_config::dict! {
        "byte_offset" => 1308i64, "record_index" => 0i64, "resref" => "blun20", "expiration" => 0i64,
        "charges" => ints(&[0, 0, 0]), "flags" => 0i64, "slots" => ints(&[9]),
    };
    assert!(spec.get("source_record").unwrap().py_eq(&Value::Dict(record)));
    assert_eq!(text(&spec, "selected_global"), text(reward("bhoisig"), "selected_global"));
    assert!(source_spec(authored).unwrap().get("required_transfers").unwrap().py_eq(&list(&["scyarryl"])));
    assert_eq!(text(&spec, "event_global"), "FLPR_EA_SCULPTOR");
    assert!(target_spec("bg2:mode2:target:22")
        .unwrap()
        .get("resources")
        .unwrap()
        .py_eq(&list(&["dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl"])));
    let original = dialogue_fixture(std::slice::from_ref(&spec));
    // Test the transfer branching itself independently of selected narrative.
    let result = patch_dialogue(&original, &specs("dlg:scyarryl"), &[], true, true, None).unwrap();
    check_dialogue("scyarryl", &original, &result, None, None);
    let dlg = Dialogue::new(&result, "scyarryl").unwrap();
    let rows = outgoing(&dlg, 19);
    assert_eq!(rows.len(), 2);
    let ordinary_action = &dlg.actions[u32_at(&rows[0], 16) as usize];
    let selected_action = &dlg.actions[u32_at(&rows[1], 16) as usize];
    assert!(contains(ordinary_action, b"GiveItem(\"blun20\",LastTalkedToBy)"));
    assert!(!contains(selected_action, b"GiveItem(\"blun20\""));
    assert!(!contains(selected_action, b"GiveItemCreate(\"blun20\""));
    assert!(contains(selected_action, b"SetGlobal(\"FLPR_XM2_12\",\"GLOBAL\",1)"));
}

#[test]
fn carried_rewards_keep_sources_and_transfer_callbacks_before_later_actions() {
    let cases = [
        ("trgeni01", 32, 32, "ar2014", "trgeni01", 0i64, 1152i64, 9i64),
        ("loutcapt", 39, 42, "ar0514", "Dennis", 6, 1116, 11),
        ("amcler01", 84, 85, "ar5500", "amcler01", 0, 1272, 4),
    ];
    let (tlk, allocated) = prepare_reward_texts(&make_tlk(&["Unchanged"]), ["dlg:trgeni01", "dlg:amcler01"], "en_us").unwrap();
    assert_eq!(tlk_text(&tlk, allocated["dlg:amcler01"][&74025] as u32).as_deref(), Some("Please, accept this gift."));
    for (name, source, target, area, script, index, offset, slot) in cases {
        let identity = format!("original-source:bg2:mode2:item:{source}");
        let spec = transfer_spec(name).unwrap();
        assert!(Value::Dict(spec.clone()).py_eq(&Value::Dict(source_spec(&identity).unwrap())));
        assert!(Value::Dict(spec.clone()).py_eq(&Value::Dict(target_spec(&format!("bg2:mode2:target:{target}")).unwrap())));
        assert!(spec.get("source_ids").unwrap().py_eq(&list(&[&identity])));
        assert!(spec.get("authored_source_ids").unwrap().py_eq(&list(&[&identity])));
        assert!(spec.get("required_transfers").unwrap().py_eq(&list(&[name])));
        let replacement = spec.get("replacement").and_then(Value::as_str);
        assert_eq!(replacement, if name == "trgeni01" { Some("blun05") } else { None });
        let inventory = cir_config::dict! {"area" => area, "creature_resource" => format!("cre:{name}"), "script_name" => script};
        assert!(spec.get("inventory_target").unwrap().py_eq(&Value::Dict(inventory)));
        assert!(spec.get("physical_source_ids").unwrap().py_eq(&list(&[&format!("cre:{name}/item/{index}")])));
        let record = cir_config::dict! {
            "byte_offset" => offset, "record_index" => index, "resref" => text(&spec, "item"), "expiration" => 0i64,
            "charges" => ints(&[0, 0, 0]), "flags" => 0i64, "slots" => ints(&[slot]),
        };
        assert!(spec.get("source_record").unwrap().py_eq(&Value::Dict(record)));
        assert_eq!(text(&spec, "recipient"), "LastTalkedToBy");
        assert_eq!(text(&spec, "grant_kind"), "transfer");
        let original = dialogue_fixture(std::slice::from_ref(&spec));
        let action_refs = if name == "amcler01" { Some(allocated[&format!("dlg:{name}")].clone()) } else { None };
        let ordered: Vec<(i64, i64)> = action_refs.iter().flatten().map(|(old, new)| (*old, *new)).collect();
        let result = patch_dialogue(&original, &specs(&format!("dlg:{name}")), &ordered, true, true, None).unwrap();
        check_dialogue(name, &original, &result, action_refs.as_ref(), None);
        for actions in &Dialogue::new(&result, name).unwrap().actions {
            assert!(!contains(actions, format!("GiveItemCreate(\"{}\"", text(&spec, "item")).as_bytes()));
        }
    }
}

/// `re.findall(rb'EEex_LuaAction\("(return[^"\r\n]*)"\)', actions)`; the
/// class cannot match the `"` that must follow it, so no backtracking.
fn lua_chunks(actions: &[u8]) -> Vec<Vec<u8>> {
    let head = b"EEex_LuaAction(\"return";
    let mut chunks = Vec::new();
    let mut at = 0;
    while let Some(start) = cir_config::py::find_bytes(actions, head, at) {
        let body = start + head.len() - b"return".len();
        let end = body + actions[body..].iter().take_while(|byte| !matches!(byte, b'"' | b'\r' | b'\n')).count();
        if actions[end..].starts_with(b"\")") {
            chunks.push(actions[body..end].to_vec());
            at = end + 2;
        } else {
            at = start + 1;
        }
    }
    chunks
}

#[test]
fn inventory_callback_survives_dialogue_parser() {
    // The Lua evaluation of each chunk stays with the reference test.
    for name in ["trgeni01", "loutcapt", "amcler01", "udlesa"] {
        let spec = specs(&format!("dlg:{name}")).remove(0);
        let original = dialogue_fixture(std::slice::from_ref(&spec));
        let patched =
            Dialogue::new(&patch_dialogue(&original, std::slice::from_ref(&spec), &[], true, true, None).unwrap(), name).unwrap();
        let target = target_number(&spec);
        let mut chunks = Vec::new();
        for (state, _ordinal) in branches(&spec) {
            for row in outgoing(&patched, state) {
                chunks.extend(lua_chunks(&patched.actions[u32_at(&row, 16) as usize]));
            }
        }
        assert_eq!(chunks.len(), branches(&spec).len() * 2, "{name}");
        for chunk in &chunks {
            assert_eq!(chunk, format!("return(FLPR_Transfer(EEex_LuaAction_Object,{target}))").as_bytes());
            assert!(!chunk.contains(&b' ') && !chunk.contains(&b':'));
        }
        for target_ids in [
            &["bg1:mode2:target:32"][..],
            &["bg2:mode1:target:32"],
            &["bg2:mode2:target:32extra"],
            &["bg2:mode2:target:32", "bg2:mode2:target:42"],
        ] {
            let mut invalid = spec.clone();
            invalid.insert("target_ids", list(target_ids));
            assert_profile(patch_dialogue(&original, &[invalid], &[], true, true, None).unwrap_err(), "transfer target changed");
        }
    }
}

#[test]
fn udlesa_physical_source_and_six_distinct_duel_continuations() {
    let spec = target_spec("bg2:mode2:target:45").unwrap();
    assert_eq!(physical_source_rewards().get("cre:udlesa/item/2").and_then(Value::as_str), Some("udlesa"));
    assert!(!source_rewards().contains_key("original-source:bg2:mode2:item:42"));
    assert!(!transfer_specs().contains_key("udlesa"));
    assert!(!spec.contains_key("required_transfers"));
    assert!(spec.get("source_ids").unwrap().py_eq(&list(&[])));
    assert!(spec.get("authored_source_ids").unwrap().py_eq(&list(&["original-source:bg2:mode2:item:42"])));
    assert_eq!(text(&spec, "replacement"), "dwsw1h02");
    assert_eq!(text(&spec, "source_resource"), "cre:udlesa");
    assert_eq!(text(&spec, "resource"), "dlg:udlesa");
    let inventory = cir_config::dict! {"area" => "ar2202", "creature_resource" => "cre:udlesa", "script_name" => "udlesa"};
    assert!(spec.get("inventory_target").unwrap().py_eq(&Value::Dict(inventory)));
    assert!(spec.get("physical_source_ids").unwrap().py_eq(&list(&["cre:udlesa/item/2"])));
    let record = cir_config::dict! {
        "byte_offset" => 3676i64, "record_index" => 2i64, "resref" => "sw1h39", "expiration" => 0i64,
        "charges" => ints(&[0, 0, 0]), "flags" => 0i64, "slots" => ints(&[9]),
    };
    assert!(spec.get("source_record").unwrap().py_eq(&Value::Dict(record)));
    assert_eq!(text(&spec, "selected_global"), "FLPR_QM2_42");
    assert_eq!(text(&spec, "consumed_global"), "FLPR_XM2_42");
    assert_eq!(branches(&spec), (6..12).map(|state| (state, 0)).collect::<Vec<_>>());
    let mut dialogue = Dialogue::new(&dialogue_fixture(std::slice::from_ref(&spec)), "udlesa").unwrap();
    for state in 6..12 {
        let action_id = u32_at(&outgoing(&dialogue, state)[0], 16) as usize;
        let following = format!(
            "SetGlobal(\"WaitingToDuel\",\"GLOBAL\",2)\r\nSetGlobal(\"DuelStarts\",\"AR2202\",3)\r\nActionOverride(Player{},JumpToPoint([608.1123]))\r\n",
            state - 5
        );
        dialogue.actions[action_id] = replace(&dialogue.actions[action_id], b"Wait(1)", format!("{following}Wait(1)").as_bytes(), None);
    }
    let original = dialogue.render();
    let result = patch_dialogue(&original, &specs("dlg:udlesa"), &[], true, true, None).unwrap();
    check_dialogue("udlesa", &original, &result, None, None);
    let after = Dialogue::new(&result, "udlesa").unwrap();
    for state in 6..12 {
        for transition in outgoing(&after, state) {
            let actions = &after.actions[u32_at(&transition, 16) as usize];
            assert!(contains(actions, format!("ActionOverride(Player{},JumpToPoint([608.1123]))", state - 5).as_bytes()));
            assert!(index(actions, b"FLPR_Transfer(") < index(actions, b"SetGlobal(\"WaitingToDuel\""));
            assert!(!contains(actions, b"GiveItemCreate(\"sw1h39\""));
        }
    }
}

#[test]
fn well_uses_appended_original_text_only_for_selected_source_and_reexports() {
    let tlk = make_tlk(&["Existing unrelated text", "Existing helmet narrative"]);
    let (updated, reference) = prepare_ddwell_text(&tlk, "en_us").unwrap();
    assert_eq!(reference, 2);
    assert_eq!(prepare_ddwell_text(&updated, "en_us").unwrap(), (updated.clone(), reference));
    assert_eq!(tlk_text(&updated, 1).as_deref(), Some("Existing helmet narrative"));
    assert!(tlk_text(&updated, reference as u32).unwrap().contains("You pull an item from the murky water"));
    let original = script_fixture("ddwell");
    let spec = &specs("bcs:ddwell")[0];
    let result = patch_reward("ddwell", &original, None, Some(reference)).unwrap();
    assert_eq!(patch_reward("ddwell", &result, None, None).unwrap(), result);
    assert_eq!(script_original(&result, spec).unwrap(), (original.clone(), Some(reference), true));
    assert_eq!(script_original(&original, spec).unwrap(), (original.clone(), None, false));
    let changed_ref = patch_reward("ddwell", &result, None, Some(reference + 1)).unwrap();
    assert_eq!(script_original(&changed_ref, spec).unwrap(), (original.clone(), Some(reference + 1), true));
    assert_eq!(patch_reward("bcs:ddwell", &result, Some(&BTreeMap::from([(40850, reference + 1)])), None).unwrap(), changed_ref);
    assert!(contains(&result, action(151, 40850, "", 1, 0).as_bytes()));
    assert!(contains(&result, action(151, reference, "", 1, 0).as_bytes()));
    let gone = replace(&result, b"AR1202DragonWell", b"AR1202DragonGone", Some(1));
    assert_profile(patch_reward("ddwell", &gone, None, None).unwrap_err(), "changed copy");
    assert_profile(patch_reward("ddwell", &original, None, None).unwrap_err(), "allocated");
    assert_profile(patch_reward("bcs:ddwell", &original, Some(&BTreeMap::from([(40849, reference)])), None).unwrap_err(), "allocated");
}

/// The patcher part of test_original_option_export's
/// test_dialogue_grant_keeps_exact_charges_through_export.
#[test]
fn dialogue_grant_keeps_exact_charges() {
    let spec = reward("ppstat01").clone();
    assert!(spec.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[1, 1, 1])));
    let original = dialogue_fixture(std::slice::from_ref(&spec));
    let changed = patch_reward("dlg:ppstat01", &original, None, None).unwrap();
    assert_eq!(patch_reward("dlg:ppstat01", &changed, None, None).unwrap(), changed);
    check_dialogue("ppstat01", &original, &changed, None, None);
    let dialogue = Dialogue::new(&changed, "ppstat01").unwrap();
    let grant: &[u8] = b"GiveItemCreate(\"ring31\",LastTalkedToBy,1,1,1)";
    for (state, _ordinal) in branches(&spec) {
        let rows = outgoing(&dialogue, state);
        let with_grant: Vec<bool> = rows.iter().map(|row| contains(&dialogue.actions[u32_at(row, 16) as usize], grant)).collect();
        assert_eq!(with_grant.iter().filter(|present| **present).count(), 1, "state {state}");
    }
}

/// The reviewed EET resources and their exact patches (text routes with the
/// reward texts appended to an empty dialog.tlk), as the reference test
/// lists them: regression evidence, nothing pins these.
const REVIEWED_EET: &[(&str, &str, &str)] = &[
    ("bcs:asysta01", "f0f1773357f2280b7b6a05695ff278650110dc60898e2c7ddc1241867782fadd", "26bf2a67793e7b05fe35781f85ec4885aac806520927c35deba9aed5a2f5b841"),
    ("bcs:ddwell", "4d2232892ce30f7e55333f696bb2717e5208e3d588e0ef77e990fc0077955544", "d0422206eb8c983e6fc14dea29ec78a8607674dd74548c0ff29be93fbd0accdb"),
    ("dlg:amcler01", "5bac12a183463f6faa912b15e80c45c321cd2189f318c5056cf38b2150a6d79e", "4f891f7909f6d3f0f22e747b5f2cb2bdb5b96d6875e95c7ab299719cd7cedf28"),
    ("dlg:ammayor", "d37409df94285e4bdce88bdfdb38382790dca567dc6304744244d9d5504030d6", "d71af3922a33954154f7f2745df7466647465ad3920b6a04548b6a9df73a2e05"),
    ("dlg:aran", "da4bebc9ceb58fad2d34cf261b73d458ece55ad70128c656ede9e44a8631cee1", "26551e099ce0936a8e0da8e128ae41546650d058685091849cc6406a882d50a7"),
    ("dlg:bharval", "ef4e704dc616f3658410cb798bb48b323b49b3b419986ff0345eb81979fb2730", "d1ca99803daf9f609ea13f438eb75fe65ffb87d0f417b483a4873ece6006f31a"),
    ("dlg:bhnalla", "038818ff7d8e4fc3a62e10530b74764657e26b3f44203ebd1877db7535d17ab3", "69fa93487cb7f0f4aa9883733e5d8a784155dbb726e06d88c595f591dfd763d5"),
    ("dlg:bhoisig", "5a55df4f2a0b814027735467fbf69017d9a13b0cd749d8566cf4f57c1f932377", "43ca1ecf37968c0737109cb06b8c9fbb522a2becbe51a89662ef1d14df3c52f7"),
    ("dlg:gorbat1", "c1da7c158296ed5e553942a735ebc84773007b099a4eadb1cc78a50142a0088a", "39d7e944e7d9efb983bb34013e7be3a9ab7cca2825d18010f16a22da60443c27"),
    ("dlg:gorcamb", "76cdb11e99cdbd676d7af08f48713fceb01061ea6180e762e6be1c997f3b1de4", "cae698747ba37b187f0ab294ec01401630b868e1572c3f7717d5ed258bcd8665"),
    ("dlg:gorlum2", "8228c02ac6dee8ac4652ad550a0fef83a8ddf2d59400fcb7c1e025f6395b5e9c", "28a2a669d4388c155abfcece1857611260628717e38c3bb49b1af245ce1e4ca5"),
    ("dlg:gortan1", "27a8575386e30a36a1f00f63c2004a095f301b511cd310eb03e36606265f3f3c", "0a70f5274d7eacfd4c0c1941266c7bd9be3e5332026f24116c3cadfa6c441ede"),
    ("dlg:hendak", "0f5a4956e7ee05cb5d6a02e2d61bcf8be0ab575ecd921af31656578330b4a804", "b19099dbd5a42cbb653b1a7abba30aeda7dbdc5697ba0e95106fe39ba7b1b7c4"),
    ("dlg:lehtin", "a067828ca2055d4c3fd406c43672d9da3547e92b3da67a5eb84b341fdbebd2d1", "980b634e457adec11ba48405c65dbc5f9c6d032eac0d51bfcf8703d1eb6c44bf"),
    ("dlg:loutcapt", "1bb3dc1337c087cc049f0d4fa431f4a7f909dbfacabf4dd30087d8b4f52f2d98", "9a7db245aa8f59ff1743421ed4621b96391521f48e8f6eeafb0d44070a1ddd25"),
    ("dlg:mgkhol01", "d4762221de29275f29a3516980cf2b6360612a1670ad5733f3e599a1ba72e3ea", "3a47fea6c2cdb1405267342bad65ecc58741481006153dd3a15b43c3807decfd"),
    ("dlg:ppdjinn", "06c1671065b549921263594db378d7610223cb16b70b7f718b3885b45b32af38", "cf0269858c28d24c6e837074c8b6d7dfae267fee0b3d5bce647cd0f60734b332"),
    ("dlg:ppstat01", "7022962710da01f3d66088d8f7c5aa2a7963d7e994ff0d55f61dc5b64c9673ae", "69505c964ee036a6d9b974407e34cbd4ff35f4cebd10373819c5ef2f5d50f73a"),
    ("dlg:riftg03", "4bb8a0866e3470ae558c108160ebef18c33de9f14ad3181ccf33ebd0d5e4912a", "34e7c5e57d9e69394b40ce09f4c5cd5a5e6249d871b676c82bafbd3d707f3ff5"),
    ("dlg:scsain", "c2a43b0d67eca9b531aa591463f74d5333225a95f0be87f8d03ed1cf91f8dafe", "cd1db0265daa8dc239bbe1a325cdf2ad3fc0033f3bf8eaba981a46e65c91b148"),
    ("dlg:sctelwyn", "d5dd048c43e16ba937ff583eef8f5966e549fb0c4e3f92679883d35fcdda1759", "6be82dfdf03c7b5f8bbeee67681344e1cb753a803fe461181c52e23ab5434067"),
    ("dlg:scyarryl", "f96880bdabcfe8393d9ad6df502d3d001e2d79913736e480f6cc717f012dd367", "247dd86c080239e80b4cd07516127a1ce55d90e12c6087b983ff935a3ffaea51"),
    ("dlg:trgeni01", "4220763c55e065bfb2f500aa9d615a1a1e112ae3fc60eee061a3d9f3f59f7549", "0e7674a115890c50bc5cf23ae66bc428e1e39e601368130cae732b95b5502715"),
    ("dlg:trhmer01", "e5651fc5ef72780fe2ce9bb6ec8c3632665a5279d41cf79244fbef393304752e", "21aac581490c963ecf778f2ffc760bb3c56cce5be72fd637f783e70eccdbdd75"),
    ("dlg:uddemon", "582fbcfecf3e71363dff1aa9bcef4dbc202d28c3af6e66e6a29840b342f1bdfd", "b1976c5f2f0b948ec00f972348b5ef208b0ba127bbc5678297b133747836a042"),
    ("dlg:udlesa", "6c518483372ebe4073986dc2d41e4f7ae73a7bce133aab4289a29ef1be7c7e50", "4af88ce4eede1adcf79556223c0535768d94c8a8af5409380f21fe7c43984ee4"),
    ("dlg:udsilver", "48f697db47942b74b4530975a5a4a46cac1a0cc850453b2b9d50ab173b01090c", "593cdf5a0313db7065fb2c1eaafc8efc309485d3b5a654c24b585f397e94917b"),
    ("dlg:udsvir03", "5918919ea366eceb27f4096f025cde79c44310a9e57bcd49d123fdbb48352994", "2003095671c146bb3d06b92fcc17df06dc18e5be4ab59a0e5d17bfd6db412e66"),
    ("dlg:uhmay01", "4213656595fdde6afeefee881597ddd62814ba886747bc4d60cfca25a2d852be", "45f6042dd98804a72901084fdfd503a61dd7738c45b85724f2e2a008a45e395d"),
    ("dlg:uhogre01", "8706469468f7277542ba0e6d7fcf44b47a15d36c3879f505464e0a119be00b97", "722213d1f53966780ab8bba2e2dbe400e64c14ced6da2ff9f48b43749b629e25"),
];

/// The reference's read-only installed-resource cache (not distributed).
pub fn reviewed_cache() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../work/original-preset-runtime-20260929/mode2-resources")
}

#[test]
fn reviewed_eet_resources_when_available_patch_exactly_and_reexport() {
    let cache = reviewed_cache();
    if !cache.exists() {
        eprintln!("skipped: the read-only installed-resource cache is not distributed");
        return;
    }
    let mut reviewed: Vec<&str> = REVIEWED_EET.iter().map(|(resource, _, _)| *resource).collect();
    let mut known: Vec<&str> = resource_rewards().keys().collect();
    reviewed.sort_unstable();
    known.sort_unstable();
    assert_eq!(reviewed, known);
    let (_tlk, allocated) = prepare_reward_texts(&make_tlk(&[]), known.clone(), "en_us").unwrap();
    for (resource, original_hash, patched_hash) in REVIEWED_EET {
        let (kind, name) = resource.split_once(':').unwrap();
        let original = std::fs::read(cache.join(format!("{name}.{kind}"))).unwrap();
        assert_eq!(sha(&original), *original_hash, "{resource}");
        let result = patch_reward(resource, &original, allocated.get(*resource), None).unwrap();
        assert_eq!(sha(&result), *patched_hash, "{resource}");
        assert_eq!(patch_reward(resource, &result, None, None).unwrap(), result);
        assert!(reasons(resource, &original).is_empty(), "{resource}");
        assert!(reasons(resource, &result).is_empty(), "{resource}");
        assert!(!own_patch(resource, &original).unwrap());
        assert!(own_patch(resource, &result).unwrap());
        if kind == "dlg" && text_refs(resource).is_some() {
            let refs = &allocated[*resource];
            assert_eq!(text_original(&result, resource).unwrap(), Some((original.clone(), refs.clone())));
            let clones = check_text_graph(resource, &original, &result, refs);
            let cloned: Vec<usize> = clones.keys().copied().collect();
            if ["trgeni01", "amcler01", "udlesa"].contains(&name) {
                let action_refs = if name == "amcler01" { Some(refs.clone()) } else { None };
                let ordered: Vec<(i64, i64)> = action_refs.iter().flatten().map(|(old, new)| (*old, *new)).collect();
                let branched = patch_dialogue(&original, &specs(resource), &ordered, true, true, None).unwrap();
                check_dialogue(name, &original, &branched, action_refs.as_ref(), None);
                let expected: &[usize] = match name {
                    "trgeni01" => &[35],
                    "udlesa" => &[6],
                    _ => &[],
                };
                assert_eq!(cloned, expected, "{resource}");
                if name == "udlesa" {
                    let dlg = Dialogue::new(&branched, name).unwrap();
                    for state in 6..12 {
                        for row in outgoing(&dlg, state) {
                            let actions = &dlg.actions[u32_at(&row, 16) as usize];
                            assert!(contains(actions, format!("ActionOverride(Player{},JumpToPoint([608.1123]))", state - 5).as_bytes()));
                            assert!(index(actions, b"FLPR_Transfer(") < index(actions, b"SetGlobal(\"WaitingToDuel\""));
                        }
                    }
                }
            }
            if ["sctelwyn", "scsain", "scyarryl"].contains(&name) {
                assert_eq!(cloned, [19], "{resource}");
                // External temple-messenger entry states stay in place.
                let (before, after) = (Dialogue::new(&original, name).unwrap(), Dialogue::new(&result, name).unwrap());
                assert_eq!(before.states[27][..4], after.states[27][..4]);
            }
            if name == "ppdjinn" {
                let dlg = Dialogue::new(&result, name).unwrap();
                assert_eq!(dlg.states.len(), 2);
                for state in &dlg.states {
                    assert!(contains(&dlg.state_triggers[u32_at(state, 12) as usize], b"NumTimesTalkedTo(0)"));
                }
                assert!(contains(&dlg.actions.concat(), b"ReallyForceSpell(Myself,DRYAD_TELEPORT)"));
            }
        } else if kind == "bcs" {
            assert_eq!(script_original(&result, &specs(resource)[0]).unwrap().0, original);
        } else if name == "aran" {
            check_aran(&original, &result);
        } else {
            check_dialogue(name, &original, &result, None, None);
        }
    }
}
