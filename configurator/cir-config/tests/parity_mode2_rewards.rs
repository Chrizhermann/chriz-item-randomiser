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
use cir_config::game_names::{EetNames, Tables};
use cir_config::game_text::GameText;
use cir_config::json::{Dict, Value};
use cir_config::mode2_rewards::{
    destroy_gate, direct_rewards, game_text_refs, gives_reward, handover, journal_lua, journal_refs, own_patch,
    patch_dialogue, patch_reward, physical_source_reward, prepare_ddwell_text, prepare_reward_texts, resource_rewards,
    resource_specs, rewards, route_reasons, script_original, source_rewards, source_spec, target_rewards, target_spec,
    text_lines, text_original, text_refs, titled, transfer_spec, transfer_specs, unpatched_script, Naming, TextRefs,
    DESTROY_GATES, PHYSICAL_SOURCE_REWARDS, TEXT_REFS,
};
use cir_config::py::{put_u32, str_value};
use cir_config::quest_options::Dialogue;
use cir_config::quest_rewards::{blocks, global_guard};
use cir_config::script_text::{block_actions, block_parts, script_blocks};
use common::builders::{contains, count, index, make_tlk, outgoing, replace, sha, tlk_text, u32_at};
use common::mode2::{
    action, action_with, alternatives, block, book_block, book_fixture, book_gifts, branches, check_dialogue, check_job_end,
    check_pair, check_text_graph, dialogue_fixture, handover_line, has_ring, king_destroys, king_paid, layout_dialogue,
    no_action, obj, ring_destroyed, ring_gone, route_guard, script_fixture, specs, target_number, text, text_variant,
    tolgerias_fixture, treasury_fixture, trigger, well_reward, with_actions, LayoutReply, PRINCE_TRANSFER,
    PRINCE_TRANSFER_59, PRINCE_TRANSFER_60, TRGENI_DEPARTURE,
};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::Arc;

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
    let found = route_reasons(resource, data, Naming::default()).unwrap();
    found.iter().map(|(route, reason)| (route.to_string(), str_value(reason))).collect()
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
    result.push(("bcs:ppbook".into(), book_fixture(true)));
    result.push(("bcs:sahtreas".into(), treasury_fixture(&[])));
    result.push(("bcs:tolger".into(), tolgerias_fixture(&[])));
    result
}

/// The TOLGER.BCS scripts of the generator (`tolgerias_cases`).
fn tolgerias_variants() -> Vec<(&'static str, Vec<u8>)> {
    let (has, gone, destroy) = (has_ring(), ring_gone(), ring_destroyed());
    let more = action(30, 2, "GLOBALOtherMod", 0, 0);
    let other = book_block(&[trigger(16399, 1, "GLOBALOtherMod", 0)], &more);
    // A block that tests the ring, whose responses the engine chooses between.
    let tested = |responses: &[&str]| -> String {
        let responses: String = responses.iter().map(|text| format!("RE\n50{text}RE\n")).collect();
        format!("CR\nCO\n{has}{gone}CO\nRS\n{responses}RS\nCR\n")
    };
    let both = |actions: String| book_block(&[has.clone(), gone.clone()], &actions);
    let script = |blocks: &[String]| tolgerias_fixture(blocks);
    vec![
        ("installed", script(&[])),
        ("crlf", crlf(script(&[]))),
        ("mixed", script(&[other.clone(), both(destroy.clone()), other])),
        ("untested", script(&[book_block(std::slice::from_ref(&gone), &destroy)])),
        ("tested and untested", script(&[both(destroy.clone()), book_block(std::slice::from_ref(&gone), &destroy)])),
        (
            "uppercase",
            script(&[book_block(&[trigger(16481, 0, "RING33", 0), gone.clone()], &action(169, 0, "RING33", 0, 0))]),
        ),
        ("other actor", script(&[both(action(169, 0, "ring33", 0, 21))])),
        ("tested twice", script(&[both(destroy.clone() + &destroy)])),
        ("tested alternatives", script(&[tested(&[&destroy, &destroy])])),
        ("tested alternatives more", script(&[tested(&[&destroy, &(destroy.clone() + &more)])])),
        ("more", script(&[both(destroy.clone() + &more)])),
        ("more before", script(&[both(more.clone() + &destroy)])),
        ("giving", script(&[both(destroy.clone() + &action(15, 0, "ring33", 21, 0))])),
        ("testing only", script(&[book_block(std::slice::from_ref(&has), &more)])),
        ("plain", script(&[book_block(&[trigger(16465, 0, "TOLGER2", 0)], &action(111, 0, "", 0, 0))])),
        ("unreadable", replace(&script(&[]), b"169OB", b"169XX", Some(1))),
        ("no blocks", b"SC\nSC\n".to_vec()),
        ("empty", Vec::new()),
    ]
}

/// Another mod's block in SAHTREAS.BCS, and one that only tests the rod.
fn treasury_neighbours() -> (String, String) {
    (
        book_block(&[trigger(16399, 1, "GLOBALOtherMod", 0)], &action(30, 2, "GLOBALOtherMod", 0, 0)),
        book_block(&[trigger(16481, 0, "RODS02", 0)], &action(30, 3, "GLOBALOtherMod", 0, 0)),
    )
}

/// The treasury scripts of the generator (`treasury_cases`).
fn treasury_variants() -> Vec<(&'static str, Vec<u8>)> {
    let (other, testing) = treasury_neighbours();
    let king = book_block(&[king_paid()], &king_destroys());
    let destroy = |item: &str| action(169, 0, item, 0, 0);
    let give = |item: &str| action(15, 0, item, 21, 0);
    let paid = |actions: String| book_block(&[king_paid()], &actions);
    vec![
        ("installed", treasury_fixture(&[])),
        ("mixed", treasury_fixture(&[other.clone(), king.clone(), testing.clone()])),
        ("mixed crlf", crlf(treasury_fixture(&[other.clone(), king, testing.clone()]))),
        ("lowercase", treasury_fixture(&[paid(destroy("rods02") + &destroy("brac19"))])),
        ("other actor", treasury_fixture(&[paid(action(169, 0, "BRAC19", 0, 21) + &action(30, 1, "GLOBALOtherMod", 0, 0))])),
        (
            "alternatives",
            treasury_fixture(&[alternatives(
                &[&destroy("RODS02"), &(destroy("RODS02") + &action(30, 1, "GLOBALOtherMod", 0, 0))],
                "GLOBALsahkngjob",
                50,
            )]),
        ),
        (
            "two blocks",
            treasury_fixture(&[
                paid(destroy("RODS02")),
                book_block(&[trigger(16399, 3, "GLOBALsahkngjob", 0)], &destroy("BRAC19")),
            ]),
        ),
        ("rod only", treasury_fixture(&[paid(destroy("RODS02"))])),
        ("plain", treasury_fixture(&[other, testing])),
        ("giving", treasury_fixture(&[paid(king_destroys() + &give("RODS02"))])),
        ("both given", treasury_fixture(&[paid(give("RODS02") + &give("BRAC19"))])),
        ("created", treasury_fixture(&[paid(king_destroys() + &action(82, 0, "BRAC19", 0, 0))])),
        ("second string", treasury_fixture(&[paid(king_destroys() + &action_with(363, 0, "shop01", 0, 0, "rods02"))])),
        ("unreadable", replace(&treasury_fixture(&[]), b"169OB", b"169XX", Some(1))),
        ("no blocks", b"SC\nSC\n".to_vec()),
        ("no header", b"XX\nSC\n".to_vec()),
        ("empty", Vec::new()),
    ]
}

/// The king of the generator's "sahkng01 split": he pays with the rod in
/// another reply than with the bracer.
fn split_king() -> Vec<u8> {
    let mut dialogue = Dialogue::new(&dialogue_fixture(&specs("dlg:sahkng01")), "sahkng01").unwrap();
    let grant = b"GiveItemCreate(\"rods02\",LastTalkedToBy,0,0,0)\r\r\n";
    dialogue.actions = dialogue.actions.iter().map(|text| replace(text, grant, b"", None)).collect();
    with_actions(&dialogue.render(), "sahkng01", &[((80, 0), b"GiveItemCreate(\"rods02\",LastTalkedToBy,0,0,0)\r\n".as_slice())])
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
    for (beamdog, label) in [(true, "beamdog"), (false, "weidu")] {
        assert_eq!(fixture.input(&format!("patch_reward sahpr2 {label}"), "data"), layout_dialogue("sahpr2", &prince_states(), beamdog));
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
    for (label, data) in treasury_variants() {
        assert_eq!(fixture.input(&format!("patch_reward sahtreas {label}"), "data"), data, "{label}");
    }
    for (label, data) in tolgerias_variants() {
        assert_eq!(fixture.input(&format!("patch_reward tolger {label}"), "data"), data, "{label}");
    }
    assert_eq!(fixture.input("patch_reward sahkng01 split", "data"), split_king());
}

#[test]
fn graph_checks_reject_an_unpatched_dialogue() {
    let original = dialogue_fixture(&specs("dlg:ppstat01"));
    let outcome = std::panic::catch_unwind(|| check_dialogue("ppstat01", &original, &original, None, None, Naming::default()));
    assert!(outcome.is_err(), "check_dialogue accepted unbranched rewards");
}

/// `synthetic_patch(name, original)`: the patch, its re-export, and what the
/// check reads from both and from a changed copy.
fn synthetic_patch(name: &str, original: &[u8]) -> Vec<u8> {
    let result = patch_reward(name, original, None, Some(100001), Naming::default()).unwrap();
    assert_eq!(patch_reward(name, &result, None, None, Naming::default()).unwrap(), result);
    assert!(!own_patch(name, original, Naming::default()).unwrap());
    assert!(own_patch(name, &result, Naming::default()).unwrap());
    assert!(reasons(name, original).is_empty());
    assert!(reasons(name, &result).is_empty());
    let tampered = replace(&result, b"FLPR_", b"OTHER", Some(1));
    assert_profile(patch_reward(name, &tampered, None, None, Naming::default()).unwrap_err(), "carries a changed copy of the reward route's own patch");
    assert!(reasons(name, &tampered).values().next().unwrap().contains("changed copy"));
    result
}

#[test]
fn dialogue_both_campaign_routes_keep_exact_recipient_and_surrounding_actions() {
    // Two routes of one dialogue have their own tests (check_pair), so has
    // the route without a grant (check_job_end).
    for (resource, keys) in resource_rewards().iter() {
        let single = keys.as_list().is_some_and(|keys| keys.len() == 1);
        let job_end = keys.as_list().is_some_and(|keys| {
            keys.iter().any(|key| reward(&str_value(key)).get("job_end").is_some_and(Value::truthy))
        });
        if resource.starts_with("dlg:") && single && text_refs(resource).is_none() && !job_end {
            let name = &resource[4..];
            let original = dialogue_fixture(&specs(resource));
            check_dialogue(name, &original, &synthetic_patch(name, &original), None, None, Naming::default());
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
        check_dialogue("uhogre01", &original, &result, None, Some(&[(9, 0), (9, 1), (12, 0)]), Naming::default());
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
        assert!(!own_patch("dlg:uhogre01", &data, Naming::default()).unwrap());
        assert_profile(patch_reward("dlg:uhogre01", &data, None, None, Naming::default()).unwrap_err(), reason);
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
        assert_profile(patch_reward("bcs:ddwell", &data, None, Some(100001), Naming::default()).unwrap_err(), reason);
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
    check_dialogue("uhogre01", &data, &result, None, Some(&[(22, 0), (22, 1)]), Naming::default());
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
    let result = patch_reward("dlg:ppdjinn", &original, Some(&refs), None, Naming::default()).unwrap();
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
    assert_eq!(text_original(&result, "dlg:ppdjinn", Naming::default()).unwrap(), Some((original, refs.clone())));
    assert_eq!(patch_reward("dlg:ppdjinn", &result, None, None, Naming::default()).unwrap(), result);
    // Where every other start condition comes first, the gated pair is
    // appended, as earlier exports did.
    put_u32(&mut d.states[0], 12, 1);
    put_u32(&mut d.states[1], 12, 0);
    assert_eq!(fixture.input("patch_reward start order last", "data"), d.render());
    let after = Dialogue::new(&patch_reward("dlg:ppdjinn", &d.render(), Some(&refs), None, Naming::default()).unwrap(), "ppdjinn").unwrap();
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
    let result = patch_reward("bcs:ddwell", &original, None, Some(100001), Naming::default()).unwrap();
    assert_eq!(patch_reward("bcs:ddwell", &result, None, None, Naming::default()).unwrap(), result);
    assert!(own_patch("bcs:ddwell", &result, Naming::default()).unwrap());
    assert_eq!(script_original(&result, &specs("bcs:ddwell")).unwrap(), (original.clone(), Some(100001), true));
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
fn bg1_routes_have_their_own_flags_and_take_the_games_names() {
    // Varci's payment: BG:EE names the shield SHLD07, EET SHLD07A (its rename lists).
    let spec = source_spec("original-source:bg1:mode2:item:20", Naming::default()).unwrap();
    assert!(Value::Dict(spec.clone()).py_eq(&Value::Dict(target_spec("bg1:mode2:target:20", Naming::default()).unwrap())));
    assert_eq!([text(&spec, "campaign"), text(&spec, "item"), text(&spec, "resource")], ["bg1", "shld07", "dlg:varci"]);
    assert!(spec.get("target_ids").unwrap().py_eq(&list(&["bg1:mode2:target:20"])));
    let flags = ["selected_global", "consumed_global", "event_global", "recipient_local"].map(|key| text(&spec, key));
    assert_eq!(flags, ["FLPR_QB1_20", "FLPR_XB1_20", "FLPR_EB1_20", "FLPR_RB1_20"]);
    let names = eet_names();
    for (game, shown) in [(None, "shld07"), (Some("bgee"), "shld07"), (Some("eet"), "shld07a")] {
        let naming = Naming { game, names: Some(&names) };
        for found in [
            source_spec("original-source:bg1:mode2:item:20", naming).unwrap(),
            cir_config::mode2_rewards::reward("varci", naming).unwrap(),
            resource_specs("dlg:varci", naming).unwrap().remove(0),
        ] {
            let resref = found.get("payload").and_then(|payload| payload.get("resref")).and_then(Value::as_str);
            assert_eq!((text(&found, "item"), resref), (shown, Some(shown)), "{game:?}");
        }
    }
    // EET keeps the names of BG2's routes.
    let eet = Naming { game: Some("eet"), names: Some(&names) };
    assert!(Value::Dict(source_spec("original-source:bg2:mode2:item:14", eet).unwrap())
        .py_eq(&Value::Dict(source_spec("original-source:bg2:mode2:item:14", Naming::default()).unwrap())));
    assert_profile(
        cir_config::mode2_rewards::reward("x", Naming::default()).unwrap_err(),
        "Unsupported extended reward route: x",
    );
}

#[test]
fn eets_varci_gives_shld07a_to_an_object_function_and_the_route_gates_it() {
    let names = eet_names();
    let eet = Naming { game: Some("eet"), names: Some(&names) };
    let spec = resource_specs("dlg:varci", eet).unwrap();
    let original = dialogue_fixture(&spec);
    assert!(contains(&original, b"GiveItemCreate(\"shld07a\",LastTalkedToBy(Myself),0,0,0)"));
    // Without EET's names the route looks for BG:EE's shield and names that.
    assert_eq!(reasons("dlg:varci", &original), named(&[("varci", "VARCI.DLG has no reply that gives SHLD07")]));
    assert!(route_reasons("dlg:varci", &original, eet).unwrap().is_empty());
    let result = patch_reward("dlg:varci", &original, None, None, eet).unwrap();
    assert_eq!(patch_reward("dlg:varci", &result, None, None, eet).unwrap(), result);
    assert!(own_patch("dlg:varci", &result, eet).unwrap());
    assert!(route_reasons("dlg:varci", &result, eet).unwrap().is_empty());
    let dialogue = Dialogue::new(&result, "varci").unwrap();
    let actions = dialogue.actions.concat();
    assert!(contains(&actions, b"ActionOverride(LastTalkedToBy(Myself),SetGlobal(\"FLPR_RB1_20\",\"LOCALS\",1))"));
    assert!(contains(&actions, b"SetGlobal(\"FLPR_XB1_20\",\"GLOBAL\",1)"));
    assert!(contains(&actions, b"SetGlobal(\"FLPR_EB1_20\",\"GLOBAL\",1)"));
    assert!(contains(&dialogue.triggers.concat(), b"!Global(\"FLPR_QB1_20\",\"GLOBAL\",1)"));
}

#[test]
fn registry_exposes_authored_charges_and_separate_container_recipient() {
    assert_eq!(direct_rewards().len(), 11);
    let mut spec = source_spec("original-source:bg2:mode2:item:28", Naming::default()).unwrap();
    let expected = cir_config::dict! {"resref" => "ring31", "expiration" => 0i64, "charges" => ints(&[1, 1, 1]), "flags" => 0i64};
    assert!(spec.get("payload").unwrap().py_eq(&Value::Dict(expected)));
    spec.get_mut("payload").and_then(|payload| payload.get_mut("charges")).and_then(Value::as_list_mut).unwrap()[0] =
        Value::from(99i64);
    let target = target_spec("bg2:mode2:target:35", Naming::default()).unwrap();
    assert!(target.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[1, 1, 1])));
    let container = target_spec("bg2:mode2:target:21", Naming::default()).unwrap();
    assert_eq!(text(&container, "recipient"), "are:ar1512/container/0");
    assert!(container.get("recipient_local").unwrap().is_null());
    assert_eq!(text(&target_spec("bg2:mode2:target:40", Naming::default()).unwrap(), "recipient"), "Player1");
    let temple = target_spec("bg2:mode2:target:48", Naming::default()).unwrap();
    assert!(temple.get("resources").unwrap().py_eq(&list(&["dlg:bhoisig", "dlg:bhnalla", "dlg:bharval"])));
    let demon = source_spec("original-source:bg2:mode2:item:65", Naming::default()).unwrap();
    let silver = source_spec("original-source:bg2:mode2:item:44", Naming::default()).unwrap();
    assert!(demon.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[0, 3, 0])));
    assert_eq!(text(&demon, "recipient"), "Player1");
    assert_eq!(text(&demon, "event_global"), text(&silver, "event_global"));
    assert_eq!(text(&demon, "recipient_local"), text(&silver, "recipient_local"));
    assert_ne!(text(&demon, "selected_global"), text(&silver, "selected_global"));
    for (message, error) in [
        ("Unsupported extended reward source", source_spec("x", Naming::default()).unwrap_err()),
        ("Unsupported extended reward recipient", target_spec("x", Naming::default()).unwrap_err()),
        ("Unsupported carried reward", transfer_spec("x", Naming::default()).unwrap_err()),
        ("Unsupported extended reward resource", resource_specs("dlg:x", Naming::default()).unwrap_err()),
    ] {
        assert_profile(error, message);
    }
}

#[test]
fn aran_two_grants_have_all_four_independent_campaign_combinations() {
    let original = dialogue_fixture(&specs("dlg:aran"));
    check_pair("aran", &original, &synthetic_patch("aran", &original));
}

#[test]
fn the_sahuagin_kings_reply_gates_the_bracer_and_the_treasury_rod_apart() {
    // The rod is the treasury's container reward; the reply that creates
    // another one follows its selected flag. The rod's place is the
    // treasury, whose reward the reply hands to the one the king pays.
    let rod = reward("sahrod");
    assert_eq!(text(rod, "item"), "rods02");
    assert!(rod.get("source_ids").unwrap().py_eq(&list(&[])));
    assert!(rod.get("target_ids").unwrap().py_eq(&list(&["bg2:mode2:target:6"])));
    assert!(rod.get("authored_source_ids").unwrap().py_eq(&list(&["original-source:bg2:mode2:item:7"])));
    assert_eq!((text(rod, "selected_global"), text(rod, "consumed_global")), ("FLPR_QM2_7", "FLPR_XM2_7"));
    let treasury = rod.get("handover").and_then(Value::as_dict).unwrap();
    assert_eq!((text(treasury, "area"), text(treasury, "script")), ("ar2300", "sahtreas"));
    assert_eq!(
        handover_line(rod),
        b"EEex_LuaTrigger(\"return(FLPR_Handover==nil)or(FLPR_Handover(EEex_LuaTrigger_Object,6))\")\r\n"
    );
    for key in ["sahkng01", "sahrod"] {
        assert!(reward(key).get("resources").unwrap().py_eq(&list(&["dlg:sahkng01", "bcs:sahtreas"])), "{key}");
    }
    assert_eq!(target_rewards().get("bg2:mode2:target:6").and_then(Value::as_str), Some("sahrod"));
    assert_eq!(
        PHYSICAL_SOURCE_REWARDS,
        [
            ("cre:udlesa", "cre-template", "sw1h39", "udlesa"),
            ("are:ar2300", "container", "rods02", "sahrod"),
            ("cre:tolger2", "cre-template", "ring33", "tolger"),
        ]
    );
    assert_eq!(physical_source_reward("are:ar2300", "container", "rods02"), Some("sahrod"));
    assert_eq!(physical_source_reward("are:ar2300", "container", "brac19"), None);
    assert_eq!(physical_source_reward("are:ar2300", "embedded-cre", "rods02"), None);
    assert!(!source_rewards().contains_key("original-source:bg2:mode2:item:7"));
    let specs = specs("dlg:sahkng01");
    assert_eq!(specs.iter().map(|spec| text(spec, "route")).collect::<Vec<_>>(), ["sahkng01", "sahrod"]);
    let original = dialogue_fixture(&specs);
    let result = synthetic_patch("sahkng01", &original);
    check_pair("sahkng01", &original, &result);
    for data in [&original, &result] {
        assert!(gives_reward("dlg:sahkng01", data, "sahrod", Naming::default()).unwrap());
    }
    // Another mod's king gives no rod: the bracer's route stays and the
    // rod needs no route; the rod's place, whose reward no reply would
    // hand over, cannot be used (route_reasons).
    let mut dialogue = Dialogue::new(&original, "sahkng01").unwrap();
    let grant = b"GiveItemCreate(\"rods02\",LastTalkedToBy,0,0,0)\r\r\n";
    dialogue.actions = dialogue.actions.iter().map(|text| replace(text, grant, b"", None)).collect();
    let without = dialogue.render();
    assert!(!gives_reward("dlg:sahkng01", &without, "sahrod", Naming::default()).unwrap());
    assert_eq!(reasons("dlg:sahkng01", &without), named(&[("sahrod", "SAHKNG01.DLG has no reply that gives RODS02")]));
    let patched = patch_reward("dlg:sahkng01", &without, None, None, Naming::default()).unwrap();
    assert!(!contains(&Dialogue::new(&patched, "sahkng01").unwrap().triggers.concat(), b"FLPR_QM2_7"));
    assert!(!contains(&patched, b"FLPR_Handover"));
    // One that only takes or tests the rod gives none either; one that
    // gives it another way does, and the route says why it cannot gate it.
    let taking = with_actions(&without, "sahkng01", &[((80, 0), b"TakePartyItem(\"RODS02\")\r\n".as_slice())]);
    assert!(!gives_reward("dlg:sahkng01", &taking, "sahrod", Naming::default()).unwrap());
    for (actions, reason) in [
        (
            b"ActionOverride(\"sahpr2\",GiveItemCreate(\"rods02\",LastTalkedToBy,0,0,0))\r\n".as_slice(),
            "SAHKNG01.DLG state 80 gives RODS02 through ActionOverride(), whose recipient the route cannot mark",
        ),
        (b"CreateItem(\"rods02\",0,0,0)\r\n", "SAHKNG01.DLG state 80 also gives RODS02 with CreateItem(), which the route cannot gate"),
        (b"GiveItemCreate(\"rods02\",LastTalkedToBy,0,0,0\r\n", "SAHKNG01.DLG state 80 names RODS02 in actions that cannot be read"),
    ] {
        let other = with_actions(&without, "sahkng01", &[((80, 0), actions)]);
        assert!(gives_reward("dlg:sahkng01", &other, "sahrod", Naming::default()).unwrap(), "{reason}");
        assert_eq!(reasons("dlg:sahkng01", &other)["sahrod"], reason);
    }
    assert_profile(
        gives_reward("dlg:sahkng01", &original, "aran", Naming::default()).unwrap_err(),
        "Unsupported extended reward route in SAHKNG01.DLG: aran",
    );
}

/// The routes left out of these bytes, in route order.
fn left_out(resource: &str, data: &[u8]) -> Vec<String> {
    reasons(resource, data).into_keys().collect()
}

#[test]
fn each_reply_that_pays_with_the_rod_hands_the_treasury_over_after_its_guards() {
    let specs = specs("dlg:sahkng01");
    let rod = specs.iter().find(|spec| text(spec, "route") == "sahrod").unwrap();
    let line = handover_line(rod);
    let original = dialogue_fixture(&specs);
    let result = patch_reward("dlg:sahkng01", &original, None, None, Naming::default()).unwrap();
    // What the recovery reads patches to the same dialogue again.
    assert_eq!(patch_reward("dlg:sahkng01", &result, None, None, Naming::default()).unwrap(), result);
    let triggers = Dialogue::new(&result, "sahkng01").unwrap().triggers;
    let handed: Vec<usize> = (0..triggers.len()).filter(|at| contains(&triggers[*at], &line)).collect();
    assert_eq!(handed.len(), 4);
    // A missing, repeated, moved or other handover is a changed copy of the patch.
    for label in ["missing", "twice", "another place", "before the guards"] {
        let mut dialogue = Dialogue::new(&result, "sahkng01").unwrap();
        let text = &dialogue.triggers[handed[0]];
        dialogue.triggers[handed[0]] = match label {
            "missing" => replace(text, &line, b"", None),
            "twice" => [text.as_slice(), &line].concat(),
            "another place" => replace(text, b",6))", b",7))", None),
            _ => [line.as_slice(), &replace(text, &line, b"", None)].concat(),
        };
        let tampered = dialogue.render();
        assert_profile(
            patch_reward("dlg:sahkng01", &tampered, None, None, Naming::default()).unwrap_err(),
            "carries a changed copy of the reward route's own patch",
        );
        assert_eq!(left_out("dlg:sahkng01", &tampered), ["sahkng01", "sahrod"], "{label}");
    }
    // A mod whose king pays with the rod in another reply: that reply hands
    // the treasury over, the bracer's does not.
    let split = split_king();
    assert!(reasons("dlg:sahkng01", &split).is_empty());
    let patched = Dialogue::new(&synthetic_patch("sahkng01", &split), "sahkng01").unwrap();
    for (state, hands) in [(80, true), (81, false)] {
        let rows = outgoing(&patched, state);
        assert_eq!(rows.len(), 2);
        for row in rows {
            assert_eq!(patched.triggers[u32_at(&row, 12) as usize].ends_with(&line), hands, "{state}");
        }
    }
    // Only the route's own row of Item Randomiser's table is its place; a
    // BG1 row would name its campaign.
    for targets in [&[][..], &["bg2:mode2:target:6", "bg2:mode2:target:58"], &["bg2:base:target:6"]] {
        let mut spec = rod.clone();
        spec.insert("target_ids", list(targets));
        assert_profile(handover(&spec).unwrap_err(), "The treasury's place changed");
    }
    let mut bg1 = rod.clone();
    bg1.insert("target_ids", list(&["bg1:mode2:target:6"]));
    assert_eq!(handover(&bg1).unwrap(), handover_line(&bg1));
    assert!(contains(&handover_line(&bg1), b"(EEex_LuaTrigger_Object,6,1))"));
}

/// The game's reward reply beside another mod's: one that ends his job
/// through ActionOverride in other letter case and spacing, then leaves in
/// the state after it, and some that name the job but do not end it.
fn prince_states() -> Vec<Vec<LayoutReply<'static>>> {
    let game = "AddXPObject(Player1,60500)\nSetGlobal(\"senityilijob\",\"GLOBAL\",2)\n\
                SetGlobal(\"villynatyjob\",\"GLOBAL\",2)\nRunAwayFrom(\"sahkng01\",15)";
    vec![
        vec![
            ("True()", "SetGlobal(\"villynatyjob\",\"GLOBAL\",1)", Some(1)),
            ("", "SetGlobal(\"villynatyjob\",\"LOCALS\",2)\nIncrementGlobal(\"VillynatyJob\",\"Locals\",1)", Some(1)),
            ("", "SetGlobal(\"villynatyjob2\",\"GLOBAL\",2)\nDestroySelf()", None),
        ],
        vec![("Global(\"villynatyjob\",\"GLOBAL\",2)", "DisplayStringHead(Myself,1)", None), ("", game, None)],
        vec![("", "ActionOverride( \"sahpr2\" , SetGlobal( \"VillynatyJob\" , \"global\" , 2 ) )\nDestroySelf()", Some(3))],
        vec![("", "DestroySelf()", None)],
    ]
}

#[test]
fn the_princes_reward_reply_hands_over_what_he_carries() {
    // Item Randomiser's tadpoles2.fl gives its reward to SAHPR2.CRE and adds
    // a GiveItem to the reply that ends his job, which gives nothing in the
    // games. Here the prince carries the place's reward, and each reply of
    // his that ends his job hands it to the one he talks to. Row 60
    // (tadpoles3.fl, the king or the prince) is his too: the king leaves with
    // what he carries on either side.
    assert!(resource_rewards().get("dlg:sahpr2").unwrap().py_eq(&list(&["sahpr2", "sahpr2b"])));
    let names = eet_names();
    let eet = Naming { game: Some("eet"), names: Some(&names) };
    for (key, line) in [("sahpr2", 59), ("sahpr2b", 60)] {
        let target = format!("bg2:mode2:target:{line}");
        let spec = target_spec(&target, Naming::default()).unwrap();
        assert_eq!(target_rewards().get(&target).and_then(Value::as_str), Some(key));
        assert!(!source_rewards().values().any(|route| route.as_str() == Some(key)));
        for field in ["item", "payload", "selected_global", "consumed_global", "event_global", "recipient_local"] {
            assert!(matches!(spec.get(field), Some(Value::Null)), "{key} {field}");
        }
        for field in ["source_ids", "authored_source_ids"] {
            assert!(spec.get(field).unwrap().py_eq(&list(&[])), "{key} {field}");
        }
        assert!(spec.get("target_ids").unwrap().py_eq(&list(&[target.as_str()])), "{key}");
        assert!(spec.get("job_end").unwrap().py_eq(&cir_config::list!["villynatyjob", 2]));
        let carrier = cir_config::dict! {"area" => "ar2300", "creature_resource" => "cre:sahpr2", "script_name" => "sahpr2"};
        assert!(spec.get("inventory_target").unwrap().py_eq(&Value::Dict(carrier)));
        assert_eq!((text(&spec, "recipient"), text(&spec, "grant_kind")), ("LastTalkedToBy", "transfer"));
        assert!(Value::Dict(cir_config::mode2_rewards::reward(key, eet).unwrap()).py_eq(&Value::Dict(spec.clone())));
        assert_profile(
            gives_reward("dlg:sahpr2", &dialogue_fixture(&specs("dlg:sahpr2")), key, Naming::default()).unwrap_err(),
            &format!("Unsupported extended reward route in SAHPR2.DLG: {key}"),
        );
    }
    for beamdog in [true, false] {
        let original = layout_dialogue("sahpr2", &prince_states(), beamdog);
        let result = patch_reward("dlg:sahpr2", &original, None, None, Naming::default()).unwrap();
        assert_eq!(patch_reward("dlg:sahpr2", &result, None, None, Naming::default()).unwrap(), result);
        assert!(!own_patch("dlg:sahpr2", &original, Naming::default()).unwrap());
        assert!(own_patch("dlg:sahpr2", &result, Naming::default()).unwrap());
        assert!(reasons("dlg:sahpr2", &original).is_empty());
        assert!(reasons("dlg:sahpr2", &result).is_empty());
        assert_eq!(check_job_end(&original, &result), BTreeSet::from([(1, 1), (2, 0)]));
        let after = Dialogue::new(&result, "sahpr2").unwrap();
        let at = |state: usize, ordinal: usize| u32_at(&outgoing(&after, state)[ordinal], 16) as usize;
        // His departures after the transfer wait for it; the one of a reply
        // that never hands over stays instant.
        for (state, ordinal, waits) in [(0, 2, false), (2, 0, true), (3, 0, true)] {
            assert_eq!(contains(&after.actions[at(state, ordinal)], TRGENI_DEPARTURE), waits, "{beamdog} {state} {ordinal}");
        }
        // The transfers as the dialogue parser leaves them, once each, in
        // front.
        let (job, other) = (at(1, 1), at(0, 0));
        let handing = &after.actions[job];
        assert!(handing.starts_with(PRINCE_TRANSFER));
        assert_eq!(count(handing, b"FLPR_Transfer"), 2);
        // Changed copies of the patch: another place, a transfer moved into
        // a reply that does not end his job or behind its actions, twice,
        // gone from one of the replies, only one place's, or the two in the
        // other order.
        let without = handing[PRINCE_TRANSFER.len()..].to_vec();
        for (label, changes) in [
            ("another place", vec![(job, replace(handing, b",59))", b",58))", None))]),
            ("other reply", vec![(job, without.clone()), (other, [PRINCE_TRANSFER, after.actions[other].as_slice()].concat())]),
            ("behind", vec![(job, [without.as_slice(), b"\r\n", PRINCE_TRANSFER].concat())]),
            ("twice", vec![(job, [PRINCE_TRANSFER, handing.as_slice()].concat())]),
            ("gone", vec![(job, without.clone())]),
            ("one place", vec![(job, [PRINCE_TRANSFER_59, without.as_slice()].concat())]),
            ("the other place", vec![(job, [PRINCE_TRANSFER_60, without.as_slice()].concat())]),
            ("swapped", vec![(job, [PRINCE_TRANSFER_60, PRINCE_TRANSFER_59, without.as_slice()].concat())]),
        ] {
            let mut tampered = Dialogue::new(&result, "sahpr2").unwrap();
            for (index, text) in changes {
                tampered.actions[index] = text;
            }
            let tampered = tampered.render();
            assert_profile(
                patch_reward("dlg:sahpr2", &tampered, None, None, Naming::default()).unwrap_err(),
                "carries a changed copy of the reward route's own patch",
            );
            let found = reasons("dlg:sahpr2", &tampered);
            assert_eq!(found.keys().map(String::as_str).collect::<Vec<_>>(), ["sahpr2", "sahpr2b"], "{beamdog} {label}");
            assert!(found["sahpr2"].contains("changed copy"), "{beamdog} {label}");
            assert_eq!(found["sahpr2b"], found["sahpr2"], "{beamdog} {label}");
        }
    }
    // A dialogue in which no reply ends his job has nothing to hand over;
    // one whose actions name the job unreadably, or change it another way
    // that could end it unseen, cannot be told apart.
    for (changed, reason) in [
        (
            vec![(1, vec![("", "SetGlobal(\"villynatyjob\",\"GLOBAL\",3)", None)]), (2, vec![("", "NoAction()", None)])],
            "SAHPR2.DLG has no reply that sets villynatyjob to 2",
        ),
        (
            vec![(2, vec![("", "SetGlobal(\"villynatyjob\",\"GLOBAL\",2", None)])],
            "SAHPR2.DLG state 2 names villynatyjob in actions that cannot be read",
        ),
        (
            vec![(2, vec![("", "ActionOverride(\"sahpr2\",SetGlobal(\"villynatyjob\",\"GLOBAL\",2)", None)])],
            "SAHPR2.DLG state 2 names villynatyjob in actions that cannot be read",
        ),
        (
            vec![(2, vec![("", "SetGlobal(\"villynatyjob\",GLOBAL,2)", None)])],
            "SAHPR2.DLG state 2 names villynatyjob in actions that cannot be read",
        ),
        (
            vec![(0, vec![("", "IncrementGlobal(\"villynatyjob\",\"GLOBAL\",1)", Some(1))])],
            "SAHPR2.DLG state 0 changes villynatyjob with IncrementGlobal(), which the route cannot follow",
        ),
        (
            vec![(2, vec![("", "ActionOverride(\"sahpr2\",SetGlobalTimer(\"VILLYNATYJOB\",\"global\",2))", None)])],
            "SAHPR2.DLG state 2 changes villynatyjob with SetGlobalTimer(), which the route cannot follow",
        ),
    ] {
        let mut variant = prince_states();
        for (index, replies) in changed {
            variant[index] = replies;
        }
        let data = layout_dialogue("sahpr2", &variant, true);
        assert_eq!(reasons("dlg:sahpr2", &data), named(&[("sahpr2", reason), ("sahpr2b", reason)]));
        assert_profile(patch_reward("dlg:sahpr2", &data, None, None, Naming::default()).unwrap_err(), reason);
        assert!(!own_patch("dlg:sahpr2", &data, Naming::default()).unwrap());
    }
}

/// Whether script text names `item` as a quoted resref, in any letter case.
fn names_item(text: &[u8], item: &str) -> bool {
    let quoted = format!("\"{}\"", item.to_ascii_lowercase()).into_bytes();
    text.to_ascii_lowercase().windows(quoted.len()).any(|window| window == quoted)
}

/// `check_destroys(original, result, specs)`: each block that destroys
/// routes' items becomes one block per combination of those routes' flags,
/// in route order; where a route is selected, NoAction() stands in for each
/// of its item's destroys. A block whose conditions test a route's item only
/// runs while that route is not selected.
fn check_destroys(original: &[u8], result: &[u8], specs: &[Dict]) {
    let (before, after) = (script_blocks(original).unwrap(), script_blocks(result).unwrap());
    let newline = before.newline;
    assert_eq!((after.newline, &after.prefix, &after.suffix), (newline, &before.prefix, &before.suffix));
    let stand_in = replace(no_action().as_bytes(), b"\n", newline, None);
    let mut expected = Vec::new();
    for block in &before.blocks {
        let (head, conditions, responses) = block_parts(block, newline).unwrap();
        let actions = block_actions(&responses, newline).unwrap();
        let destroys: Vec<Vec<(usize, usize)>> = specs
            .iter()
            .map(|spec| {
                let item = text(spec, "item").as_bytes();
                actions
                    .iter()
                    .filter(|action| action.opcode == 169 && action.strings[0].to_ascii_lowercase() == item)
                    .map(|action| (action.start, action.end))
                    .collect()
            })
            .collect();
        let gating: Vec<usize> = (0..specs.len()).filter(|route| !destroys[*route].is_empty()).collect();
        // product(...) of each route's choices: the first route varies slowest.
        let mut choices: Vec<Vec<bool>> = vec![Vec::new()];
        for route in &gating {
            let options: &[bool] =
                if names_item(&conditions, text(&specs[*route], "item")) { &[false] } else { &[false, true] };
            choices = choices
                .into_iter()
                .flat_map(|prefix| options.iter().map(move |value| [prefix.clone(), vec![*value]].concat()))
                .collect();
        }
        for choice in choices {
            let selected = |at: usize| choice[at];
            let mut edits: Vec<(usize, usize)> =
                (0..gating.len()).filter(|at| selected(*at)).flat_map(|at| destroys[gating[at]].clone()).collect();
            edits.sort_unstable();
            let (mut changed, mut at) = (Vec::new(), 0);
            for (start, end) in edits {
                changed.extend_from_slice(&responses[at..start]);
                changed.extend_from_slice(&stand_in);
                at = end;
            }
            changed.extend_from_slice(&responses[at..]);
            let guards: Vec<u8> = (0..gating.len())
                .flat_map(|at| global_guard(text(&specs[gating[at]], "selected_global"), selected(at), newline).unwrap())
                .collect();
            expected.push([head.as_slice(), b"CO", newline, &conditions, &guards, b"CO", newline, &changed].concat());
        }
    }
    assert_eq!(after.blocks, expected);
}

#[test]
fn the_treasury_destroys_the_kings_items_only_while_his_grants_are_unchanged() {
    let specs = specs("bcs:sahtreas");
    let routes: Vec<(&str, &str, &str)> =
        specs.iter().map(|spec| (text(spec, "route"), text(spec, "item"), text(spec, "selected_global"))).collect();
    assert_eq!(routes, [("sahkng01", "brac19", "FLPR_QM2_57"), ("sahrod", "rods02", "FLPR_QM2_7")]);
    assert_eq!(DESTROY_GATES, &[("bcs:sahtreas", &["sahkng01", "sahrod"][..]), ("bcs:tolger", &["tolger"][..])]);
    assert!(destroy_gate("bcs:sahtreas") && destroy_gate("bcs:tolger") && !destroy_gate("bcs:ppbook"));
    // Another mod's block, and one that only tests the rod, stay as found.
    let (other, testing) = treasury_neighbours();
    let original = treasury_fixture(&[other.clone(), book_block(&[king_paid()], &king_destroys()), testing.clone()]);
    let mut gated = String::new();
    for bracer in [false, true] {
        for rod in [false, true] {
            let rod_action = if rod { no_action() } else { action(169, 0, "RODS02", 0, 0) };
            let bracer_action = if bracer { no_action() } else { action(169, 0, "BRAC19", 0, 0) };
            gated += &book_block(
                &[king_paid(), route_guard("FLPR_QM2_57", bracer), route_guard("FLPR_QM2_7", rod)],
                &(rod_action + &bracer_action),
            );
        }
    }
    let result = synthetic_patch("sahtreas", &original);
    assert_eq!(result, format!("SC\n{other}{gated}{testing}SC\n").into_bytes());
    check_destroys(&original, &result, &specs);
    assert_eq!(unpatched_script("bcs:sahtreas", &result, Naming::default()).unwrap(), original);
    // In CRLF lines too.
    assert_eq!(synthetic_patch("sahtreas", &crlf(&original)), crlf(&result));
    // By meaning: a lowercase name, a destroy through ActionOverride(), one
    // in each alternative response and one in another block are gated alike.
    for (label, data) in treasury_variants() {
        if ["lowercase", "other actor", "alternatives", "two blocks"].contains(&label) {
            check_destroys(&data, &synthetic_patch("sahtreas", &data), &specs);
        }
    }
    // A script that destroys neither item needs no gate.
    let plain = treasury_fixture(&[other, testing]);
    assert_eq!(patch_reward("bcs:sahtreas", &plain, None, None, Naming::default()).unwrap(), plain);
    assert!(!own_patch("bcs:sahtreas", &plain, Naming::default()).unwrap());
    assert!(reasons("bcs:sahtreas", &plain).is_empty());
}

#[test]
fn a_treasury_that_could_take_a_moved_reward_otherwise_leaves_its_route_out() {
    let reason = "SAHTREAS.BCS block 0 uses RODS02 in an action other than DestroyItem(), which the route cannot gate";
    let give = |item: &str| action(15, 0, item, 21, 0);
    // GiveItem() from the treasury: the rod's route stays out, the bracer's is gated.
    let giving = treasury_fixture(&[book_block(&[king_paid()], &(king_destroys() + &give("RODS02")))]);
    assert_eq!(reasons("bcs:sahtreas", &giving), named(&[("sahrod", reason)]));
    let result = patch_reward("bcs:sahtreas", &giving, None, None, Naming::default()).unwrap();
    let expected: Vec<String> = [false, true]
        .into_iter()
        .map(|bracer| {
            let bracer_action = if bracer { no_action() } else { action(169, 0, "BRAC19", 0, 0) };
            book_block(
                &[king_paid(), route_guard("FLPR_QM2_57", bracer)],
                &[action(169, 0, "RODS02", 0, 0), bracer_action, give("RODS02")].concat(),
            )
        })
        .collect();
    assert_eq!(result, treasury_fixture(&expected));
    assert!(own_patch("bcs:sahtreas", &result, Naming::default()).unwrap());
    assert_eq!(patch_reward("bcs:sahtreas", &result, None, None, Naming::default()).unwrap(), result);
    assert_eq!(reasons("bcs:sahtreas", &result), named(&[("sahrod", reason)]));
    // Both items so used, or a script that cannot be read: neither route.
    let both = treasury_fixture(&[book_block(&[king_paid()], &(give("RODS02") + &give("BRAC19")))]);
    let bracer_reason = reason.replace("RODS02", "BRAC19");
    assert_eq!(reasons("bcs:sahtreas", &both), named(&[("sahrod", reason), ("sahkng01", &bracer_reason)]));
    assert_profile(
        patch_reward("bcs:sahtreas", &both, None, None, Naming::default()).unwrap_err(),
        "BRAC19 in an action other than DestroyItem",
    );
    let unreadable = replace(&treasury_fixture(&[]), b"169OB", b"169XX", Some(1));
    let found = reasons("bcs:sahtreas", &unreadable);
    assert_eq!(found.keys().collect::<Vec<_>>(), ["sahkng01", "sahrod"]);
    assert!(found["sahrod"].contains("SAHTREAS.BCS"));
    // Our patch changed: a block dropped, a destroy back, a guard turned.
    let result = patch_reward("bcs:sahtreas", &treasury_fixture(&[]), None, None, Naming::default()).unwrap();
    let kept: Vec<String> =
        script_blocks(&result).unwrap().blocks[..3].iter().map(|block| String::from_utf8(block.clone()).unwrap()).collect();
    for (label, changed) in [
        ("dropped", treasury_fixture(&kept)),
        ("destroy back", replace(&result, no_action().as_bytes(), action(169, 0, "RODS02", 0, 0).as_bytes(), Some(1))),
        (
            "turned",
            replace(&result, route_guard("FLPR_QM2_7", false).as_bytes(), route_guard("FLPR_QM2_7", true).as_bytes(), Some(1)),
        ),
    ] {
        assert_profile(own_patch("bcs:sahtreas", &changed, Naming::default()).unwrap_err(), "SAHTREAS.BCS carries a changed copy");
        assert_eq!(left_out("bcs:sahtreas", &changed), ["sahkng01", "sahrod"], "{label}");
    }
}

#[test]
fn tolgerias_pays_with_his_ring_and_hands_his_place_to_the_one_he_pays() {
    // Row 50 (Item Randomiser's tolger.fl): Tolgerias's four payments for
    // Valygar's body give the ring he carries (row 40, TOLGER2's) as UDLESA's
    // reply gives his sword, and pay the place's reward; until then it waits
    // for TOLGER2's death in the planar sphere, at his corpse.
    let spec = target_spec("bg2:mode2:target:50", Naming::default()).unwrap();
    assert_eq!(physical_source_reward("cre:tolger2", "cre-template", "ring33"), Some("tolger"));
    assert!(!source_rewards().contains_key("original-source:bg2:mode2:item:40"));
    assert!(!transfer_specs().contains_key("tolger"));
    assert!(spec.get("source_ids").unwrap().py_eq(&list(&[])));
    assert!(spec.get("authored_source_ids").unwrap().py_eq(&list(&["original-source:bg2:mode2:item:40"])));
    assert_eq!(
        (text(&spec, "item"), text(&spec, "grant_kind"), text(&spec, "recipient")),
        ("ring33", "transfer", "LastTalkedToBy")
    );
    assert!(spec.get("resources").unwrap().py_eq(&list(&["dlg:tolger", "bcs:tolger"])));
    assert_eq!(
        ["selected_global", "consumed_global", "event_global", "recipient_local"].map(|key| text(&spec, key)),
        ["FLPR_QM2_40", "FLPR_XM2_40", "FLPR_EA_TOLGER", "FLPR_RA_TOLGER"]
    );
    let holder = cir_config::dict! {
        "area" => "ar0412", "script_name" => "tolger2", "creature_resources" => list(&["cre:tolger2"]),
    };
    assert!(spec.get("holder").unwrap().py_eq(&Value::Dict(holder)));
    assert_eq!(branches(&spec), [(37, 0), (37, 1), (37, 2), (44, 0)]);
    for absent in ["inventory_target", "source_resource", "required_transfers", "replacement", "handover"] {
        assert!(!spec.contains_key(absent), "{absent}");
    }
    assert_eq!(text_refs("dlg:tolger"), Some(&[(18409, 103)][..]));
    assert_eq!(specs("dlg:tolger").iter().map(|spec| text(spec, "route")).collect::<Vec<_>>(), ["tolger"]);
    // Each reply keeps his ring transfer while the ring stays, and marks the
    // one he pays and sets the event either way. Where he pays by force, his
    // words name the ring (state 44): a moved one shows CIR's text.
    let mut dialogue = Dialogue::new(&dialogue_fixture(&specs("dlg:tolger")), "tolger").unwrap();
    put_u32(&mut dialogue.states[44], 0, 18409);
    let original = dialogue.render();
    let branched = patch_dialogue(&original, &specs("dlg:tolger"), &[], true, true, None).unwrap();
    check_dialogue("tolger", &original, &branched, None, None, Naming::default());
    assert!(!contains(&Dialogue::new(&branched, "tolger").unwrap().actions.concat(), b"FLPR_Transfer("));
    let refs = BTreeMap::from([(18409, 100001)]);
    let result = patch_reward("dlg:tolger", &original, Some(&refs), None, Naming::default()).unwrap();
    let clones = check_text_graph("dlg:tolger", &original, &result, &refs, Naming::default());
    assert_eq!(clones.keys().copied().collect::<Vec<_>>(), [44]);
    assert_eq!(text_original(&result, "dlg:tolger", Naming::default()).unwrap(), Some((original.clone(), refs)));
    for data in [&original, &result] {
        assert!(gives_reward("dlg:tolger", data, "tolger", Naming::default()).unwrap());
        assert!(reasons("dlg:tolger", data).is_empty());
    }
    // The dialogue and the script share a name: a file names its kind, a
    // bare name is refused.
    let script = tolgerias_fixture(&[]);
    for named in ["TOLGER.DLG", "dlg:tolger", "DLG:TOLGER"] {
        assert!(!own_patch(named, &original, Naming::default()).unwrap(), "{named}");
        assert_profile(unpatched_script(named, &original, Naming::default()).unwrap_err(), "TOLGER.DLG is not a reward script");
    }
    for named in ["tolger.bcs", "bcs:tolger"] {
        assert_eq!(unpatched_script(named, &script, Naming::default()).unwrap(), script, "{named}");
    }
    for named in ["tolger", "itm:tolger", "tolger.cre"] {
        assert_profile(
            own_patch(named, &script, Naming::default()).unwrap_err(),
            &format!("Unsupported extended reward resource: {named}"),
        );
    }
}

#[test]
fn tolgerias_destroys_his_ring_only_while_it_is_the_original() {
    // His class script destroys his ring once the other Tolgerias has lost
    // his. A moved ring at one of TOLGER2's own places must stay; the block
    // tests the ring, so a NoAction() stand-in beside a kept reward would run
    // with every AI update and hold back his later blocks and scripts, his
    // fighting among them. It runs only while the ring is the original.
    let specs = specs("bcs:tolger");
    let routes: Vec<(&str, &str, &str)> =
        specs.iter().map(|spec| (text(spec, "route"), text(spec, "item"), text(spec, "selected_global"))).collect();
    assert_eq!(routes, [("tolger", "ring33", "FLPR_QM2_40")]);
    let original = tolgerias_fixture(&[]);
    let blocks: Vec<String> =
        script_blocks(&original).unwrap().blocks.iter().map(|block| String::from_utf8(block.clone()).unwrap()).collect();
    let gated = book_block(&[has_ring(), ring_gone(), route_guard("FLPR_QM2_40", false)], &ring_destroyed());
    let result = patch_reward("bcs:tolger", &original, None, None, Naming::default()).unwrap();
    assert_eq!(result, format!("SC\n{}{gated}{}SC\n", blocks[0], blocks[2]).into_bytes());
    check_destroys(&original, &result, &specs);
    assert_eq!(patch_reward("bcs:tolger", &result, None, None, Naming::default()).unwrap(), result);
    assert!(!own_patch("bcs:tolger", &original, Naming::default()).unwrap());
    assert!(own_patch("TOLGER.BCS", &result, Naming::default()).unwrap());
    assert_eq!(unpatched_script("bcs:tolger", &result, Naming::default()).unwrap(), original);
    for data in [&original, &result] {
        assert!(reasons("bcs:tolger", data).is_empty());
        let destroyed = cir_config::mode2_rewards::destroyed_items("TOLGER.BCS", data).unwrap();
        assert_eq!(destroyed.into_iter().collect::<Vec<_>>(), ["ring33"]);
    }
    // In CRLF lines too.
    assert_eq!(patch_reward("bcs:tolger", &crlf(&original), None, None, Naming::default()).unwrap(), crlf(&result));
    // A turned guard is a changed copy of the patch.
    let turned =
        replace(&result, route_guard("FLPR_QM2_40", false).as_bytes(), route_guard("FLPR_QM2_40", true).as_bytes(), Some(1));
    assert_profile(own_patch("bcs:tolger", &turned, Naming::default()).unwrap_err(), "TOLGER.BCS carries a changed copy");
    assert!(reasons("bcs:tolger", &turned)["tolger"].contains("changed copy"));
    // A destroy whose block does not test the ring keeps a NoAction() in its
    // place while the ring is moved, as the treasury's do.
    let untested = tolgerias_fixture(&[book_block(&[ring_gone()], &ring_destroyed())]);
    let expected: Vec<String> = [false, true]
        .map(|selected| {
            let actions = if selected { no_action() } else { ring_destroyed() };
            book_block(&[ring_gone(), route_guard("FLPR_QM2_40", selected)], &actions)
        })
        .to_vec();
    let patched = patch_reward("bcs:tolger", &untested, None, None, Naming::default()).unwrap();
    assert_eq!(patched, tolgerias_fixture(&expected));
    check_destroys(&untested, &patched, &specs);
    // One that tests it and does more than destroy it cannot be gated: the
    // rest of the block would not run while the ring is moved.
    let more = tolgerias_fixture(&[book_block(
        &[has_ring(), ring_gone()],
        &(ring_destroyed() + &action(30, 2, "GLOBALOtherMod", 0, 0)),
    )]);
    let reason = "TOLGER.BCS block 0 tests RING33 and does more than destroy it, which the route cannot gate";
    assert_eq!(reasons("bcs:tolger", &more), named(&[("tolger", reason)]));
    assert_profile(patch_reward("bcs:tolger", &more, None, None, Naming::default()).unwrap_err(), reason);
    // A script that destroys no ring needs no gate.
    let plain = tolgerias_fixture(&[book_block(&[trigger(16465, 0, "TOLGER2", 0)], &action(111, 0, "", 0, 0))]);
    assert_eq!(patch_reward("bcs:tolger", &plain, None, None, Naming::default()).unwrap(), plain);
    assert!(!own_patch("bcs:tolger", &plain, Naming::default()).unwrap());
    assert!(reasons("bcs:tolger", &plain).is_empty());
}

#[test]
fn aran_route_left_out_alone_keeps_the_other_route() {
    let (without, neither) = aran_without();
    let amulet = named(&[("aran2", "ARAN.DLG has no reply that gives AMUL21")]);
    assert_eq!(reasons("dlg:aran", &without), amulet);
    let result = patch_reward("dlg:aran", &without, None, None, Naming::default()).unwrap();
    assert!(own_patch("dlg:aran", &result, Naming::default()).unwrap());
    assert_eq!(patch_reward("dlg:aran", &result, None, None, Naming::default()).unwrap(), result);
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
    let (game_text, allocated) =
        prepare_reward_texts(&GameText::new(make_tlk(&["Unchanged text"])), resources.clone(), "en_us", None).unwrap();
    assert_eq!(prepare_reward_texts(&game_text, resources.clone(), "en_us", None).unwrap(), (game_text.clone(), allocated.clone()));
    assert_eq!(game_text.female, None);
    let tlk = game_text.male.clone();
    assert_eq!(tlk_text(&tlk, 0).as_deref(), Some("Unchanged text"));
    assert!(tlk_text(&tlk, allocated["dlg:ppdjinn"][&48995] as u32).unwrap().contains("<GABBER>"));
    assert_eq!(allocated["dlg:gorcamb"][&63681], allocated["dlg:gorcamb"][&64609]);
    assert_profile(
        prepare_reward_texts(&game_text, resources.clone(), "es_ES", None).unwrap_err(),
        "reward texts have no Spanish translation yet",
    );
    for resource in resources {
        let name = &resource[4..];
        let original = text_variant(resource);
        let refs = &allocated[resource];
        let result = patch_reward(resource, &original, Some(refs), None, Naming::default()).unwrap();
        check_text_graph(resource, &original, &result, refs, Naming::default());
        assert_eq!(text_original(&result, resource, Naming::default()).unwrap(), Some((original.clone(), refs.clone())));
        assert!(!own_patch(resource, &original, Naming::default()).unwrap());
        assert!(own_patch(resource, &result, Naming::default()).unwrap());
        assert!(reasons(resource, &result).is_empty());
        assert_eq!(patch_reward(resource, &result, None, None, Naming::default()).unwrap(), result);
        let moved: BTreeMap<i64, i64> = refs.iter().map(|(old, new)| (*old, new + 1000)).collect();
        let revised = patch_reward(resource, &result, Some(&moved), None, Naming::default()).unwrap();
        assert_eq!(text_original(&revised, resource, Naming::default()).unwrap(), Some((original.clone(), moved.clone())));
        check_text_graph(resource, &original, &revised, &moved, Naming::default());
        // A changed reply or recovery record is not our patch any more.
        let mut flipped = result.clone();
        let at = flipped.len() - 20;
        flipped[at] ^= 1;
        for damaged in [replace(&result, b"FLPR_", b"OTHER", Some(1)), flipped] {
            assert_profile(own_patch(resource, &damaged, Naming::default()).unwrap_err(), "carries a changed copy");
            assert!(reasons(resource, &damaged)[name].contains("changed copy"));
        }
        assert_profile(patch_reward(resource, &original, None, None, Naming::default()).unwrap_err(), "allocated");
    }
    assert!(target_spec("bg2:mode2:target:22", Naming::default()).unwrap().get("required_transfers").unwrap().py_eq(&list(&["scyarryl"])));
    assert_eq!(text(reward("sctelwyn"), "event_global"), "FLPR_EA_SCULPTOR");
    assert_eq!(text(reward("scsain"), "event_global"), "FLPR_EA_SCULPTOR");
}

#[test]
fn bg1_texts_take_the_games_numbers_and_a_journal_entry_keeps_its_title() {
    // EET numbers BG:EE's texts 200000 higher; BG2's keep their numbers in every game.
    for game in [None, Some("bgee")] {
        assert_eq!(game_text_refs("dlg:aldeth", game).unwrap(), [(5740, 1649), (26828, 1650)]);
        assert_eq!(journal_refs("dlg:aldeth", game).unwrap(), [26828]);
    }
    assert_eq!(game_text_refs("dlg:aldeth", Some("eet")).unwrap(), [(205740, 1649), (226828, 1650)]);
    assert_eq!(journal_refs("dlg:aldeth", Some("eet")).unwrap(), [226828]);
    for game in ["eet", "bg2ee"] {
        assert_eq!(game_text_refs("dlg:jugjer01", Some(game)).unwrap(), text_refs("dlg:jugjer01").unwrap());
        assert!(journal_refs("dlg:jugjer01", Some(game)).unwrap().is_empty());
    }
    // A quest entry's first line is its title; languages put one or two line breaks after it.
    assert_eq!(titled(Some("The Merchant League\nOld words."), "New words."), "The Merchant League\nNew words.");
    assert_eq!(titled(Some("Liga Kupiecka\r\n\r\nStare."), "Nowe."), "Liga Kupiecka\r\n\r\nNowe.");
    for untitled in [Some("One line only"), Some("Title\n \n"), Some("\nNo title first"), Some(""), None] {
        assert_eq!(titled(untitled, "New words."), "New words.");
    }
    // The new entry has the title of the game's own entry, in each of its text files.
    let (mut male, mut female) = (vec![String::new(); 26829], vec![String::new(); 26829]);
    male[26828] = "The Merchant League Investigation\nAldeth gave me a sword.".into();
    female[26828] = "The Merchant League Investigation (f)\nAldeth gave me a sword.".into();
    male[5740] = "Take this sword.".into();
    female[5740] = "Take this sword.".into();
    let tlk = |texts: &[String]| make_tlk(&texts.iter().map(String::as_str).collect::<Vec<_>>());
    let source = GameText { male: tlk(&male), female: Some(tlk(&female)) };
    let (text, refs) = prepare_reward_texts(&source, ["dlg:aldeth"], "en_us", Some("bgee")).unwrap();
    assert_eq!(refs["dlg:aldeth"].keys().copied().collect::<Vec<_>>(), [5740, 26828]);
    let found = cir_config::translations::lines("en_us", &text_lines("dlg:aldeth").unwrap()).unwrap();
    let (thanks, entry) = (&found[&("in-game.tra".to_string(), 1649)], &found[&("in-game.tra".to_string(), 1650)]);
    let (journal, words) = (refs["dlg:aldeth"][&26828] as u32, refs["dlg:aldeth"][&5740] as u32);
    let new_female = text.female.as_ref().unwrap();
    assert_eq!(tlk_text(&text.male, journal).unwrap(), format!("The Merchant League Investigation\n{}", entry.0));
    assert_eq!(tlk_text(new_female, journal).unwrap(), format!("The Merchant League Investigation (f)\n{}", entry.1));
    // His words are no journal entry: they need no title.
    assert_eq!((tlk_text(&text.male, words).unwrap(), tlk_text(new_female, words).unwrap()), thanks.clone());
    // EET: the numbers EET gives these texts.
    let (_text, eet) = prepare_reward_texts(&GameText::new(make_tlk(&[])), ["dlg:aldeth"], "en_us", Some("eet")).unwrap();
    assert_eq!(eet["dlg:aldeth"].keys().copied().collect::<Vec<_>>(), [205740, 226828]);
}

#[test]
fn journal_lua_files_the_new_entry_under_the_quests_of_the_one_it_replaces() {
    let lua: &[u8] = b"createQuest( 87 )\r\n\
        createEntry    ( 87, -1, 26822, {}, nil )\r\n\
        createEntry    ( 87, -1, 26828, {}, nil )\r\n\
        createEntry    ( 87, -1, 126828, {}, nil )\r\n\
        \tcreateEntry(90,-1,26828,{1,2},nil)\r\n\
        createEntry    ( 91, -1, 268280, {}, nil )\r\n";
    let texts = |resource: &str, refs: &[(i64, i64)]| -> TextRefs {
        BTreeMap::from([(resource.to_string(), refs.iter().copied().collect())])
    };
    let aldeth = texts("dlg:aldeth", &[(5740, 300000), (26828, 300001)]);
    let result = journal_lua(lua, &aldeth, None).unwrap();
    let expected = replace(
        &replace(
            lua,
            b"createEntry    ( 87, -1, 26828, {}, nil )\r\n",
            b"createEntry    ( 87, -1, 26828, {}, nil )\r\ncreateEntry    ( 87, -1, 300001, {}, nil )\r\n",
            None,
        ),
        b"\tcreateEntry(90,-1,26828,{1,2},nil)\r\n",
        b"\tcreateEntry(90,-1,26828,{1,2},nil)\r\n\tcreateEntry(90,-1,300001,{1,2},nil)\r\n",
        None,
    );
    assert_eq!(result, expected);
    // A second export lists it once; an entry no quest lists stays unlisted.
    assert_eq!(journal_lua(&result, &aldeth, None).unwrap(), result);
    let unlisted: &[u8] = b"createEntry    ( 87, -1, 26822, {}, nil )\n";
    assert_eq!(journal_lua(unlisted, &aldeth, None).unwrap(), unlisted);
    // Texts other than journal entries and other routes' texts are not filed.
    let jermien = texts("dlg:jugjer01", &[(33760, 300002), (33777, 300003)]);
    assert_eq!(journal_lua(lua, &jermien, None).unwrap(), lua);
    // EET's numbers.
    let eet: &[u8] = b"createEntry    ( 87, -1, 226828, {}, nil )\n";
    let numbers = texts("dlg:aldeth", &[(205740, 400000), (226828, 400001)]);
    assert_eq!(journal_lua(eet, &numbers, Some("eet")).unwrap(), [eet, b"createEntry    ( 87, -1, 400001, {}, nil )\n"].concat());
}

#[test]
fn sculptor_transfer_is_source12_auxiliary_copy_and_preserves_ordinary_giveitem() {
    let spec = transfer_spec("scyarryl", Naming::default()).unwrap();
    let authored = "original-source:bg2:mode2:item:12";
    assert!(spec.get("authored_source_ids").unwrap().py_eq(&list(&[authored])));
    assert!(spec.get("source_ids").unwrap().py_eq(&list(&[])));
    assert_eq!(source_rewards().get(authored).and_then(Value::as_str), Some("bhoisig"));
    // The export finds the copy in the scanned CRE; no game's bytes are pinned.
    assert_eq!(text(&spec, "source_resource"), "cre:scyarryl");
    for pinned in ["physical_source_ids", "source_record", "source_resource_sha256"] {
        assert!(!spec.contains_key(pinned), "{pinned}");
    }
    assert_eq!(text(&spec, "selected_global"), text(reward("bhoisig"), "selected_global"));
    assert!(source_spec(authored, Naming::default()).unwrap().get("required_transfers").unwrap().py_eq(&list(&["scyarryl"])));
    assert_eq!(text(&spec, "event_global"), "FLPR_EA_SCULPTOR");
    assert!(target_spec("bg2:mode2:target:22", Naming::default())
        .unwrap()
        .get("resources")
        .unwrap()
        .py_eq(&list(&["dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl"])));
    let original = dialogue_fixture(std::slice::from_ref(&spec));
    // Test the transfer branching itself independently of selected narrative.
    let result = patch_dialogue(&original, &specs("dlg:scyarryl"), &[], true, true, None).unwrap();
    check_dialogue("scyarryl", &original, &result, None, None, Naming::default());
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
        ("trgeni01", 32, 32, "ar2014", "trgeni01"),
        ("loutcapt", 39, 42, "ar0514", "Dennis"),
        ("amcler01", 84, 85, "ar5500", "amcler01"),
        ("jugjer01", 31, 31, "ar1103", "Jermien"),
    ];
    let (game_text, allocated) =
        prepare_reward_texts(&GameText::new(make_tlk(&["Unchanged"])), ["dlg:trgeni01", "dlg:amcler01"], "en_us", None).unwrap();
    assert_eq!(tlk_text(&game_text.male, allocated["dlg:amcler01"][&74025] as u32).as_deref(), Some("Please, accept this gift."));
    for (name, source, target, area, script) in cases {
        let identity = format!("original-source:bg2:mode2:item:{source}");
        let spec = transfer_spec(name, Naming::default()).unwrap();
        assert!(Value::Dict(spec.clone()).py_eq(&Value::Dict(source_spec(&identity, Naming::default()).unwrap())));
        assert!(Value::Dict(spec.clone()).py_eq(&Value::Dict(target_spec(&format!("bg2:mode2:target:{target}"), Naming::default()).unwrap())));
        assert!(spec.get("source_ids").unwrap().py_eq(&list(&[&identity])));
        assert!(spec.get("authored_source_ids").unwrap().py_eq(&list(&[&identity])));
        assert!(spec.get("required_transfers").unwrap().py_eq(&list(&[name])));
        let replacement = spec.get("replacement").and_then(Value::as_str);
        assert_eq!(replacement, if name == "trgeni01" { Some("blun05") } else { None });
        // The reward as the creature carries it: Jermien's sword keeps its daily charge.
        let charges: &[i64] = if name == "jugjer01" { &[1, 1, 0] } else { &[0, 0, 0] };
        let payload = cir_config::dict! {"resref" => text(&spec, "item"), "expiration" => 0, "charges" => ints(charges), "flags" => 0};
        assert!(spec.get("payload").unwrap().py_eq(&Value::Dict(payload)), "{name}");
        let inventory = cir_config::dict! {"area" => area, "creature_resource" => format!("cre:{name}"), "script_name" => script};
        assert!(spec.get("inventory_target").unwrap().py_eq(&Value::Dict(inventory)));
        assert_eq!(text(&spec, "source_resource"), format!("cre:{name}"));
        for pinned in ["physical_source_ids", "source_record", "source_resource_sha256"] {
            assert!(!spec.contains_key(pinned), "{name} {pinned}");
        }
        assert_eq!(text(&spec, "recipient"), "LastTalkedToBy");
        assert_eq!(text(&spec, "grant_kind"), "transfer");
        let original = dialogue_fixture(std::slice::from_ref(&spec));
        let action_refs = if name == "amcler01" { Some(allocated[&format!("dlg:{name}")].clone()) } else { None };
        let ordered: Vec<(i64, i64)> = action_refs.iter().flatten().map(|(old, new)| (*old, *new)).collect();
        let result = patch_dialogue(&original, &specs(&format!("dlg:{name}")), &ordered, true, true, None).unwrap();
        check_dialogue(name, &original, &result, action_refs.as_ref(), None, Naming::default());
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
    for name in ["trgeni01", "loutcapt", "amcler01", "jugjer01", "udlesa", "aldeth", "nemphr"] {
        let spec = specs(&format!("dlg:{name}")).remove(0);
        let original = dialogue_fixture(std::slice::from_ref(&spec));
        let patched =
            Dialogue::new(&patch_dialogue(&original, std::slice::from_ref(&spec), &[], true, true, None).unwrap(), name).unwrap();
        let target = target_number(&spec);
        // A BG1 row passes its campaign (1); M_FLPR.lua names the target from both.
        let passed = if text(&spec, "campaign") == "bg1" { format!("{target},1") } else { target.to_string() };
        let mut chunks = Vec::new();
        for (state, _ordinal) in branches(&spec) {
            for row in outgoing(&patched, state) {
                chunks.extend(lua_chunks(&patched.actions[u32_at(&row, 16) as usize]));
            }
        }
        assert_eq!(chunks.len(), branches(&spec).len() * 2, "{name}");
        for chunk in &chunks {
            assert_eq!(chunk, format!("return(FLPR_Transfer(EEex_LuaAction_Object,{passed}))").as_bytes());
            assert!(!chunk.contains(&b' ') && !chunk.contains(&b':'));
        }
        for target_ids in [
            &["sod:mode2:target:32"][..],
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
    let spec = target_spec("bg2:mode2:target:45", Naming::default()).unwrap();
    assert_eq!(physical_source_reward("cre:udlesa", "cre-template", "sw1h39"), Some("udlesa"));
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
    for pinned in ["physical_source_ids", "source_record", "source_resource_sha256"] {
        assert!(!spec.contains_key(pinned), "{pinned}");
    }
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
    check_dialogue("udlesa", &original, &result, None, None, Naming::default());
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
    let (updated, reference) = prepare_ddwell_text(&GameText::new(tlk), "en_us").unwrap();
    assert_eq!(reference, 2);
    assert_eq!(prepare_ddwell_text(&updated, "en_us").unwrap(), (updated.clone(), reference));
    assert_eq!(tlk_text(&updated.male, 1).as_deref(), Some("Existing helmet narrative"));
    assert!(tlk_text(&updated.male, reference as u32).unwrap().contains("You pull an item from the murky water"));
    let original = script_fixture("ddwell");
    let spec = &specs("bcs:ddwell");
    let result = patch_reward("ddwell", &original, None, Some(reference), Naming::default()).unwrap();
    assert_eq!(patch_reward("ddwell", &result, None, None, Naming::default()).unwrap(), result);
    assert_eq!(script_original(&result, spec).unwrap(), (original.clone(), Some(reference), true));
    assert_eq!(script_original(&original, spec).unwrap(), (original.clone(), None, false));
    let changed_ref = patch_reward("ddwell", &result, None, Some(reference + 1), Naming::default()).unwrap();
    assert_eq!(script_original(&changed_ref, spec).unwrap(), (original.clone(), Some(reference + 1), true));
    assert_eq!(patch_reward("bcs:ddwell", &result, Some(&BTreeMap::from([(40850, reference + 1)])), None, Naming::default()).unwrap(), changed_ref);
    assert!(contains(&result, action(151, 40850, "", 1, 0).as_bytes()));
    assert!(contains(&result, action(151, reference, "", 1, 0).as_bytes()));
    let gone = replace(&result, b"AR1202DragonWell", b"AR1202DragonGone", Some(1));
    assert_profile(patch_reward("ddwell", &gone, None, None, Naming::default()).unwrap_err(), "changed copy");
    assert_profile(patch_reward("ddwell", &original, None, None, Naming::default()).unwrap_err(), "allocated");
    assert_profile(patch_reward("bcs:ddwell", &original, Some(&BTreeMap::from([(40849, reference)])), None, Naming::default()).unwrap_err(), "allocated");
}

#[test]
fn the_books_two_scrolls_are_gated_apart_beside_the_rings_quest() {
    let book = specs("bcs:ppbook");
    let rows: Vec<(&str, &str, &str)> =
        book.iter().map(|spec| (text(spec, "route"), text(spec, "quest"), text(spec, "item"))).collect();
    assert_eq!(rows, [("ppbook", "ppbook", "scrl9b"), ("ppbook2", "ppbook", "scrl8z")]);
    assert!(book.iter().all(|spec| spec.get("target_ids").unwrap().py_eq(&list(&[]))));
    let payload = source_spec("original-source:bg2:mode2:item:262", Naming::default()).unwrap();
    let expected = cir_config::dict! {"resref" => "scrl8z", "expiration" => 0, "charges" => ints(&[0, 0, 0]), "flags" => 0};
    assert!(payload.get("payload").unwrap().py_eq(&Value::Dict(expected)));
    assert!(!target_rewards().values().any(|route| route.as_str() == Some("ppbook")));
    for installed in [true, false] {
        let original = book_fixture(installed);
        let result = synthetic_patch("ppbook", &original);
        assert_eq!(script_original(&result, &book).unwrap(), (original.clone(), None, true));
        let old = blocks(&original, if installed { 3 } else { 2 }, b"\n").unwrap().0;
        let new = blocks(&result, if installed { 9 } else { 5 }, b"\n").unwrap().0;
        assert_eq!(new[0], old[0]);
        // Each block of the last page became four, one for each choice of the
        // scrolls' rows; the ring's actions and guard stay.
        for (number, source) in old[1..].iter().enumerate() {
            for (index, choice) in [[false, false], [false, true], [true, false], [true, true]].into_iter().enumerate() {
                let branch = &new[1 + number * 4 + index];
                for (spec, selected) in book.iter().zip(choice) {
                    assert!(contains(branch, &global_guard(text(spec, "selected_global"), selected, b"\n").unwrap()));
                    let grant = action(140, 0, text(spec, "item"), 17, 0);
                    assert_eq!(count(branch, grant.as_bytes()), usize::from(!selected), "{installed} {number} {index}");
                    let consumed = format!("GLOBAL{}", text(spec, "consumed_global"));
                    assert_eq!(count(branch, consumed.as_bytes()), usize::from(selected));
                    assert_eq!(count(branch, format!("GLOBAL{}", text(spec, "event_global")).as_bytes()), 1);
                    let marker = format!(
                        "AC\n30OB\n{}OB\n{}OB\n{}1 0 0 0 0\"LOCALS{}\" \"\" AC\n",
                        obj(17),
                        obj(0),
                        obj(0),
                        text(spec, "recipient_local")
                    );
                    assert_eq!(count(branch, marker.as_bytes()), 1);
                }
                for kept in [
                    b"\"ring09\"".to_vec(),
                    b"GLOBALFLPR_QBOOK".to_vec(),
                    b"GLOBALFLPR_XBOOK".to_vec(),
                    b"GLOBALFLPR_EBOOK".to_vec(),
                    action(151, 50658, "", 1, 0).into_bytes(),
                ] {
                    assert_eq!(count(branch, &kept), count(source, &kept));
                }
            }
        }
    }
    // A scroll that the page gives twice leaves its route out; the other
    // scroll's route is prepared alone, and recognised again.
    let gifts = book_gifts();
    let twice = replace(&book_fixture(true), gifts.as_bytes(), (gifts.clone() + &action(140, 0, "scrl9b", 17, 0)).as_bytes(), None);
    let expected = named(&[("ppbook", "PPBOOK.BCS block 1 gives SCRL9B twice in one response")]);
    assert_eq!(reasons("ppbook", &twice), expected);
    let partial = patch_reward("ppbook", &twice, None, None, Naming::default()).unwrap();
    assert!(!contains(&partial, b"FLPR_QM2_260"));
    assert!(contains(&partial, b"FLPR_QM2_262"));
    assert_eq!(patch_reward("ppbook", &partial, None, None, Naming::default()).unwrap(), partial);
    assert!(own_patch("ppbook", &partial, Naming::default()).unwrap());
    assert_eq!(reasons("ppbook", &partial), expected);
    // Without the scrolls nothing is prepared.
    let neither = replace(&book_fixture(true), gifts.as_bytes(), b"", None);
    assert_eq!(
        reasons("ppbook", &neither),
        named(&[("ppbook", "PPBOOK.BCS has no block that gives SCRL9B"), ("ppbook2", "PPBOOK.BCS has no block that gives SCRL8Z")])
    );
    assert_profile(patch_reward("ppbook", &neither, None, None, Naming::default()).unwrap_err(), "no block that gives SCRL9B");
    // Another copy of one route's patch is not this adapter's.
    let result = patch_reward("ppbook", &book_fixture(true), None, None, Naming::default()).unwrap();
    let tampered = replace(&result, b"FLPR_RM2_262", b"FLPR_OTHERS", Some(1));
    assert_profile(patch_reward("ppbook", &tampered, None, None, Naming::default()).unwrap_err(), "carries a changed copy");
    // The script the patch was made from; a conversation is none.
    assert_eq!(unpatched_script("ppbook", &result, Naming::default()).unwrap(), book_fixture(true));
    assert_eq!(unpatched_script("bcs:ppbook", &book_fixture(false), Naming::default()).unwrap(), book_fixture(false));
    assert_profile(unpatched_script("ppbook", &tampered, Naming::default()).unwrap_err(), "carries a changed copy");
    assert_profile(unpatched_script("dlg:uhogre01", b"", Naming::default()).unwrap_err(), "UHOGRE01.DLG is not a reward script");
}

/// The patcher part of test_original_option_export's
/// test_dialogue_grant_keeps_exact_charges_through_export.
#[test]
fn dialogue_grant_keeps_exact_charges() {
    let spec = reward("ppstat01").clone();
    assert!(spec.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[1, 1, 1])));
    let original = dialogue_fixture(std::slice::from_ref(&spec));
    let changed = patch_reward("dlg:ppstat01", &original, None, None, Naming::default()).unwrap();
    assert_eq!(patch_reward("dlg:ppstat01", &changed, None, None, Naming::default()).unwrap(), changed);
    check_dialogue("ppstat01", &original, &changed, None, None, Naming::default());
    let dialogue = Dialogue::new(&changed, "ppstat01").unwrap();
    let grant: &[u8] = b"GiveItemCreate(\"ring31\",LastTalkedToBy,1,1,1)";
    for (state, _ordinal) in branches(&spec) {
        let rows = outgoing(&dialogue, state);
        let with_grant: Vec<bool> = rows.iter().map(|row| contains(&dialogue.actions[u32_at(row, 16) as usize], grant)).collect();
        assert_eq!(with_grant.iter().filter(|present| **present).count(), 1, "state {state}");
    }
}

/// The reviewed EET resources and their exact patches (text routes with their
/// own reward texts appended to an empty dialog.tlk, so a new route's texts
/// leave the others' numbers alone), as the reference test lists them:
/// regression evidence, nothing pins these.
const REVIEWED_EET: &[(&str, &str, &str)] = &[
    ("bcs:asysta01", "f0f1773357f2280b7b6a05695ff278650110dc60898e2c7ddc1241867782fadd", "26bf2a67793e7b05fe35781f85ec4885aac806520927c35deba9aed5a2f5b841"),
    ("bcs:ddwell", "4d2232892ce30f7e55333f696bb2717e5208e3d588e0ef77e990fc0077955544", "d0422206eb8c983e6fc14dea29ec78a8607674dd74548c0ff29be93fbd0accdb"),
    // The book as CIR's installer prepares it for the ring's quest; the
    // scrolls' routes gate its two blocks that give them.
    ("bcs:ppbook", "39b1f5119d8abc96d5158eec54d212475e639516cb35d86de27e7d6e4cbbd515", "e501c882a3fccf42e7cd5643d36c54dd67b5c38f3518d252cf98c453ddf1062a"),
    // The treasury's destroys of the king's two reward items, gated.
    ("bcs:sahtreas", "a3e4e14c6e0e3ea5d3c193cb30808aabb4c320f6b9e4dbbe3fadf99deee40751", "07f76c023620896b09a222f5ae2c7251ffd2b07c8ec18643ab65b7cadf8cbe9b"),
    // Tolgerias's class script destroys his ring only while it stays.
    ("bcs:tolger", "47568cd88b700ca0e9ea7baa8f77bfdf5b91cd156b8a7f6a3bb782b2ff3acab1", "40731bac4e558d8bd97209e492887dbec574f0a419d498e59435259dde484f6d"),
    ("dlg:aldeth", "c9c9ec1e62045e24c585040effad5ec9561a404df3b84c8eb971264b1ef242ef", "b6a08cfc2f712b30335ab0eb3534662785b68d8ed77b414e75377c01780d0982"),
    ("dlg:amcler01", "5bac12a183463f6faa912b15e80c45c321cd2189f318c5056cf38b2150a6d79e", "f1a7a795b6b22abf209fd9be5f3357e71ced3e432db3e86e5ee38c9ad88d65e9"),
    ("dlg:ammayor", "d37409df94285e4bdce88bdfdb38382790dca567dc6304744244d9d5504030d6", "d71af3922a33954154f7f2745df7466647465ad3920b6a04548b6a9df73a2e05"),
    ("dlg:aran", "da4bebc9ceb58fad2d34cf261b73d458ece55ad70128c656ede9e44a8631cee1", "26551e099ce0936a8e0da8e128ae41546650d058685091849cc6406a882d50a7"),
    ("dlg:bharval", "ef4e704dc616f3658410cb798bb48b323b49b3b419986ff0345eb81979fb2730", "d1ca99803daf9f609ea13f438eb75fe65ffb87d0f417b483a4873ece6006f31a"),
    ("dlg:bhnalla", "038818ff7d8e4fc3a62e10530b74764657e26b3f44203ebd1877db7535d17ab3", "69fa93487cb7f0f4aa9883733e5d8a784155dbb726e06d88c595f591dfd763d5"),
    ("dlg:bhoisig", "5a55df4f2a0b814027735467fbf69017d9a13b0cd749d8566cf4f57c1f932377", "43ca1ecf37968c0737109cb06b8c9fbb522a2becbe51a89662ef1d14df3c52f7"),
    ("dlg:gorbat1", "c1da7c158296ed5e553942a735ebc84773007b099a4eadb1cc78a50142a0088a", "39d7e944e7d9efb983bb34013e7be3a9ab7cca2825d18010f16a22da60443c27"),
    ("dlg:gorcamb", "76cdb11e99cdbd676d7af08f48713fceb01061ea6180e762e6be1c997f3b1de4", "4e881c91d3f63ef88cdbdc8cdb7c0f862d129657bb9696fb13e9d24bec915df5"),
    ("dlg:gorlum2", "8228c02ac6dee8ac4652ad550a0fef83a8ddf2d59400fcb7c1e025f6395b5e9c", "28a2a669d4388c155abfcece1857611260628717e38c3bb49b1af245ce1e4ca5"),
    ("dlg:gortan1", "27a8575386e30a36a1f00f63c2004a095f301b511cd310eb03e36606265f3f3c", "0a70f5274d7eacfd4c0c1941266c7bd9be3e5332026f24116c3cadfa6c441ede"),
    ("dlg:hendak", "0f5a4956e7ee05cb5d6a02e2d61bcf8be0ab575ecd921af31656578330b4a804", "b19099dbd5a42cbb653b1a7abba30aeda7dbdc5697ba0e95106fe39ba7b1b7c4"),
    ("dlg:jugjer01", "11b89e8c49ea1133b1402a3581e5c678b56fdf652dfeabb88abfd90faebca77b", "f0c13318d605bacd0ca9758c078d875669dd12b82454e1b7a4327d868f631084"),
    ("dlg:lehtin", "a067828ca2055d4c3fd406c43672d9da3547e92b3da67a5eb84b341fdbebd2d1", "980b634e457adec11ba48405c65dbc5f9c6d032eac0d51bfcf8703d1eb6c44bf"),
    ("dlg:loutcapt", "1bb3dc1337c087cc049f0d4fa431f4a7f909dbfacabf4dd30087d8b4f52f2d98", "9a7db245aa8f59ff1743421ed4621b96391521f48e8f6eeafb0d44070a1ddd25"),
    ("dlg:mgkhol01", "d4762221de29275f29a3516980cf2b6360612a1670ad5733f3e599a1ba72e3ea", "3a47fea6c2cdb1405267342bad65ecc58741481006153dd3a15b43c3807decfd"),
    ("dlg:nemphr", "088f49c9a4a41ed5e566f7cdad8263287bd519aa1ac1768888501ad779aa82e0", "bdc6e2d1c2d23ec77b3b489cf1f9c7d58edd3269b61561fa1168cbc6c3cff72f"),
    ("dlg:ppdjinn", "06c1671065b549921263594db378d7610223cb16b70b7f718b3885b45b32af38", "dae488faf269ff5d144fb33093c777c00f3a539f4778d85fc91bb1af60d750ca"),
    ("dlg:ppstat01", "7022962710da01f3d66088d8f7c5aa2a7963d7e994ff0d55f61dc5b64c9673ae", "69505c964ee036a6d9b974407e34cbd4ff35f4cebd10373819c5ef2f5d50f73a"),
    ("dlg:riftg03", "4bb8a0866e3470ae558c108160ebef18c33de9f14ad3181ccf33ebd0d5e4912a", "34e7c5e57d9e69394b40ce09f4c5cd5a5e6249d871b676c82bafbd3d707f3ff5"),
    ("dlg:sahkng01", "c76c8624052f7e179d42157e4081ddfcdbb101d2f00b819008bc99f5660785c1", "9a7109aa36361ce3e57183e08b735993dbb946584d4b79d50dd5cc641050c70f"),
    // The prince hands over what he carries when his job ends (state 5).
    ("dlg:sahpr2", "b70d79c4264b4dade199d86146da2d44f9db4d445dfbb4eead5b350c3a1c7dcb", "649a5d673c583c8e11bf188c9bed46a73b8bb1ec5dd08eef09c61fc28c8c403a"),
    ("dlg:scsain", "c2a43b0d67eca9b531aa591463f74d5333225a95f0be87f8d03ed1cf91f8dafe", "efbc5faa5490e6f89b6ca54a30a02ba74871353e19f7c8f640439974c175caca"),
    ("dlg:sctelwyn", "d5dd048c43e16ba937ff583eef8f5966e549fb0c4e3f92679883d35fcdda1759", "bcafc54734313b1513100537abb486fd42298c69795d647f525da7e06b5f9c39"),
    ("dlg:scyarryl", "f96880bdabcfe8393d9ad6df502d3d001e2d79913736e480f6cc717f012dd367", "33b842e20c6602bdbd9e62da78ec7d79e402d8cb2006282ce7568257d776698c"),
    ("dlg:tolger", "e1aa9422b767b370e58a600757fafc6906c130bb2f3834d36ab6bae8ec8f8899", "6a3709205dcae236e756883bc3b6ce4ea6c1b4a65eb80ca4e26f398392eae63a"),
    ("dlg:trgeni01", "4220763c55e065bfb2f500aa9d615a1a1e112ae3fc60eee061a3d9f3f59f7549", "5cb6cc95b0461934c198b687b0e6d868576e21f80cbc3050cc72b05216369877"),
    ("dlg:trhmer01", "e5651fc5ef72780fe2ce9bb6ec8c3632665a5279d41cf79244fbef393304752e", "21aac581490c963ecf778f2ffc760bb3c56cce5be72fd637f783e70eccdbdd75"),
    ("dlg:uddemon", "582fbcfecf3e71363dff1aa9bcef4dbc202d28c3af6e66e6a29840b342f1bdfd", "b1976c5f2f0b948ec00f972348b5ef208b0ba127bbc5678297b133747836a042"),
    ("dlg:udlesa", "6c518483372ebe4073986dc2d41e4f7ae73a7bce133aab4289a29ef1be7c7e50", "02e3d2ad97dad0ba3c42c82c11601c0d13373b733d8eafff1bab9799f484a8ab"),
    ("dlg:udsilver", "48f697db47942b74b4530975a5a4a46cac1a0cc850453b2b9d50ab173b01090c", "593cdf5a0313db7065fb2c1eaafc8efc309485d3b5a654c24b585f397e94917b"),
    ("dlg:udsvir03", "5918919ea366eceb27f4096f025cde79c44310a9e57bcd49d123fdbb48352994", "2003095671c146bb3d06b92fcc17df06dc18e5be4ab59a0e5d17bfd6db412e66"),
    ("dlg:uhmay01", "4213656595fdde6afeefee881597ddd62814ba886747bc4d60cfca25a2d852be", "fddebc84098847bf55786d953b1f400eba8a97250343e8076a174f6e15f3919c"),
    ("dlg:uhogre01", "8706469468f7277542ba0e6d7fcf44b47a15d36c3879f505464e0a119be00b97", "722213d1f53966780ab8bba2e2dbe400e64c14ced6da2ff9f48b43749b629e25"),
    ("dlg:varci", "78c3de46c5c07c5896e5ea568ef9a71aa6e53ff5b6cb7ec8d4274d9e5dfbb017", "2ae4a7164f4200efec5e9676d7a1fe20c6b05978523015df31302c75c8495e09"),
];

/// EET's own rename lists (EET/lib/pcu_dict.tph) name BG1's SHLD07 SHLD07A.
fn eet_names() -> EetNames {
    let empty = || Value::Dict(Dict::new());
    let tables = cir_config::dict! {
        "itm" => cir_config::dict! {"shld07" => "shld07a"}, "cre" => empty(), "dlg" => empty(), "bcs" => empty(),
        "sto" => empty(),
    };
    EetNames { tables: Arc::new(Tables::from_value(&Value::Dict(tables)).unwrap()), installed: true, notes: Vec::new() }
}

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
    let names = eet_names();
    let eet = Naming { game: Some("eet"), names: Some(&names) };
    for (resource, original_hash, patched_hash) in REVIEWED_EET {
        let (kind, name) = resource.split_once(':').unwrap();
        // EET numbers BG1's texts (Aldeth's) 200000 higher than BG:EE does.
        let (_text, allocated) =
            prepare_reward_texts(&GameText::new(make_tlk(&[])), [*resource], "en_us", Some("eet")).unwrap();
        let original = std::fs::read(cache.join(format!("{name}.{kind}"))).unwrap();
        assert_eq!(sha(&original), *original_hash, "{resource}");
        let result = patch_reward(resource, &original, allocated.get(*resource), None, eet).unwrap();
        assert_eq!(sha(&result), *patched_hash, "{resource}");
        assert_eq!(patch_reward(resource, &result, None, None, eet).unwrap(), result);
        assert!(route_reasons(resource, &original, eet).unwrap().is_empty(), "{resource}");
        assert!(route_reasons(resource, &result, eet).unwrap().is_empty(), "{resource}");
        assert!(!own_patch(resource, &original, eet).unwrap());
        assert!(own_patch(resource, &result, eet).unwrap());
        if kind == "dlg" && text_refs(resource).is_some() {
            let refs = &allocated[*resource];
            assert_eq!(text_original(&result, resource, eet).unwrap(), Some((original.clone(), refs.clone())));
            let clones = check_text_graph(resource, &original, &result, refs, eet);
            let cloned: Vec<usize> = clones.keys().copied().collect();
            if ["trgeni01", "amcler01", "jugjer01", "udlesa", "aldeth", "tolger"].contains(&name) {
                let action_refs = if name == "amcler01" { Some(refs.clone()) } else { None };
                let ordered: Vec<(i64, i64)> = action_refs.iter().flatten().map(|(old, new)| (*old, *new)).collect();
                let branched =
                    patch_dialogue(&original, &resource_specs(resource, eet).unwrap(), &ordered, true, true, None).unwrap();
                check_dialogue(name, &original, &branched, action_refs.as_ref(), None, eet);
                // Jermien names his sword when he offers it and when he hands it over;
                // Aldeth's thanks and the journal entry of his reply name his.
                // Tolgerias names his ring when he pays for the body by force.
                let expected: &[usize] = match name {
                    "trgeni01" => &[35],
                    "jugjer01" => &[17, 23],
                    "udlesa" => &[6],
                    "aldeth" => &[27],
                    "tolger" => &[44],
                    _ => &[],
                };
                assert_eq!(cloned, expected, "{resource}");
                if name == "aldeth" {
                    let dlg = Dialogue::new(&result, name).unwrap();
                    assert_eq!(u32_at(&dlg.states[clones[&27]], 0) as i64, refs[&205740]);
                    let journals = |state: usize| -> BTreeSet<i64> {
                        outgoing(&dlg, state).iter().map(|row| u32_at(row, 8) as i64).collect()
                    };
                    assert_eq!(journals(clones[&27]), BTreeSet::from([refs[&226828]]));
                    assert_eq!(journals(27), BTreeSet::from([226828]));
                }
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
        } else if destroy_gate(resource) {
            check_destroys(&original, &result, &resource_specs(resource, eet).unwrap());
            // The treasury's one destroying block in all four combinations; the one of
            // Tolgerias tests his ring, so it stays one block.
            let expected = if name == "tolger" { 9 } else { 4 };
            assert_eq!(script_blocks(&result).unwrap().blocks.len(), expected, "{resource}");
        } else if kind == "bcs" {
            assert_eq!(script_original(&result, &resource_specs(resource, eet).unwrap()).unwrap().0, original);
        } else if ["aran", "sahkng01"].contains(&name) {
            check_pair(name, &original, &result);
        } else if name == "sahpr2" {
            assert_eq!(check_job_end(&original, &result), BTreeSet::from([(5, 0)]));
        } else {
            check_dialogue(name, &original, &result, None, None, eet);
        }
    }
}
