//! Python-recorded parity cases for the `mode2_rewards` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_mode2_rewards.py` on the same synthetic resources, plus the
//! direct grant patcher part of `tests/test_original_option_export.py`.
//!
//! The reference tests patch `mode2_rewards.ORIGINAL_HASHES` and
//! `PATCHED_HASHES` to the synthetic resources' hashes; those tests need the
//! `parity-overrides` feature. The Lua halves of the callback test run
//! `FLPR_Transfer` chunks on Lua VMs and stay with the reference, as does the
//! test that reads a private installed-resource cache.

mod common;

use cir_config::json::Value;
use cir_config::mode2_rewards::{
    direct_rewards, patch_dialogue, physical_source_rewards, prepare_ddwell_text, prepare_reward_texts, resource_rewards,
    rewards, source_rewards, source_spec, target_spec, transfer_spec, transfer_specs, TEXT_REFS,
};
use cir_config::quest_options::Dialogue;
use common::builders::{contains, make_tlk, outgoing, pinned, sha, tlk_text, u32_at};
use common::mode2::{branches, check_dialogue, dialogue_fixture, script_fixture, specs, target_number, text, text_variant};

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

/// The synthetic originals the generator pins (`mode2_inputs`).
fn originals() -> Vec<(String, Vec<u8>)> {
    let mut result = Vec::new();
    for resource in resource_rewards().keys() {
        if !resource.starts_with("dlg:") {
            continue;
        }
        let text_route = TEXT_REFS.iter().any(|(key, _)| *key == resource);
        let data = if text_route { text_variant(resource) } else { dialogue_fixture(&specs(resource)) };
        result.push((resource.to_string(), data));
    }
    result.push(("bcs:asysta01".into(), script_fixture("asysta01")));
    result.push(("bcs:ddwell".into(), script_fixture("ddwell")));
    result
}

#[test]
fn builders_match_the_pinned_synthetic_hashes() {
    for (resource, data) in originals() {
        let name = resource.split(':').nth(1).unwrap();
        assert_eq!(sha(&data), pinned(SCENARIO, "mode2_rewards.ORIGINAL_HASHES", name), "{resource}");
    }
}

#[test]
fn graph_checks_reject_an_unpatched_dialogue() {
    let original = dialogue_fixture(&specs("dlg:ppstat01"));
    let outcome = std::panic::catch_unwind(|| check_dialogue("ppstat01", &original, &original, None));
    assert!(outcome.is_err(), "check_dialogue accepted unbranched rewards");
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
}

#[test]
fn text_allocation_reuses_entries_and_appends_original_narrative() {
    // The TLK half of test_text_variants_preserve_original_routes_...
    let resources: Vec<&str> = TEXT_REFS.iter().map(|(key, _)| *key).filter(|key| key.starts_with("dlg:")).collect();
    let (tlk, allocated) = prepare_reward_texts(&make_tlk(&["Unchanged text"]), resources.clone(), "en_us").unwrap();
    assert_eq!(prepare_reward_texts(&tlk, resources, "en_us").unwrap(), (tlk.clone(), allocated.clone()));
    assert_eq!(tlk_text(&tlk, 0).as_deref(), Some("Unchanged text"));
    assert!(tlk_text(&tlk, allocated["dlg:ppdjinn"][&48995] as u32).unwrap().contains("<GABBER>"));
    assert_eq!(allocated["dlg:gorcamb"][&63681], allocated["dlg:gorcamb"][&64609]);
    assert!(target_spec("bg2:mode2:target:22").unwrap().get("required_transfers").unwrap().py_eq(&list(&["scyarryl"])));
    assert_eq!(text(rewards().get("sctelwyn").and_then(Value::as_dict).unwrap(), "event_global"), "FLPR_EA_SCULPTOR");
    assert_eq!(text(rewards().get("scsain").and_then(Value::as_dict).unwrap(), "event_global"), "FLPR_EA_SCULPTOR");
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
    let bhoisig = rewards().get("bhoisig").and_then(Value::as_dict).unwrap();
    assert_eq!(text(&spec, "selected_global"), text(bhoisig, "selected_global"));
    assert!(source_spec(authored).unwrap().get("required_transfers").unwrap().py_eq(&list(&["scyarryl"])));
    assert_eq!(text(&spec, "event_global"), "FLPR_EA_SCULPTOR");
    assert!(target_spec("bg2:mode2:target:22")
        .unwrap()
        .get("resources")
        .unwrap()
        .py_eq(&list(&["dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl"])));
    let original = dialogue_fixture(std::slice::from_ref(&spec));
    // Test the transfer branching itself independently of selected narrative.
    let result = patch_dialogue(&original, &specs("dlg:scyarryl"), &[], true, true).unwrap();
    check_dialogue("scyarryl", &original, &result, None);
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
        let result = patch_dialogue(&original, &specs(&format!("dlg:{name}")), &ordered, true, true).unwrap();
        check_dialogue(name, &original, &result, action_refs.as_ref());
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
            Dialogue::new(&patch_dialogue(&original, std::slice::from_ref(&spec), &[], true, true).unwrap(), name).unwrap();
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
            let error = patch_dialogue(&original, &[invalid], &[], true, true).unwrap_err();
            assert_eq!(error.kind, cir_config::error::ErrorKind::Profile);
            assert!(error.message.contains("transfer target changed"), "{}", error.message);
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
        let replaced = common::builders::replace(&dialogue.actions[action_id], b"Wait(1)", format!("{following}Wait(1)").as_bytes(), None);
        dialogue.actions[action_id] = replaced;
    }
    let original = dialogue.render();
    let result = patch_dialogue(&original, &specs("dlg:udlesa"), &[], true, true).unwrap();
    check_dialogue("udlesa", &original, &result, None);
    let after = Dialogue::new(&result, "udlesa").unwrap();
    for state in 6..12 {
        for transition in outgoing(&after, state) {
            let actions = &after.actions[u32_at(&transition, 16) as usize];
            assert!(contains(actions, format!("ActionOverride(Player{},JumpToPoint([608.1123]))", state - 5).as_bytes()));
            let transfer = common::builders::index(actions, b"FLPR_Transfer(");
            assert!(transfer < common::builders::index(actions, b"SetGlobal(\"WaitingToDuel\""));
            assert!(!contains(actions, b"GiveItemCreate(\"sw1h39\""));
        }
    }
}

#[test]
fn well_text_is_appended_once_after_existing_entries() {
    // The TLK half of test_well_uses_appended_original_text_only_for_...
    let tlk = make_tlk(&["Existing unrelated text", "Existing helmet narrative"]);
    let (updated, reference) = prepare_ddwell_text(&tlk, "en_us").unwrap();
    assert_eq!(reference, 2);
    assert_eq!(prepare_ddwell_text(&updated, "en_us").unwrap(), (updated.clone(), reference));
    assert_eq!(tlk_text(&updated, 1).as_deref(), Some("Existing helmet narrative"));
    assert!(tlk_text(&updated, reference as u32).unwrap().contains("You pull an item from the murky water"));
}

#[cfg(feature = "parity-overrides")]
mod pinned_synthetic_resources {
    use super::*;
    use cir_config::error::ErrorKind;
    use cir_config::json::Dict;
    use cir_config::mode2_rewards::{original_ddwell, original_reward, patch_reward};
    use cir_config::quest_rewards::blocks;
    use common::builders::{count, index, replace};
    use common::mode2::{action, check_aran, check_text_graph, obj};
    use std::collections::BTreeMap;

    fn assert_profile(error: cir_config::error::Error, needle: &str) {
        assert_eq!(error.kind, ErrorKind::Profile, "{error:?}");
        assert!(error.message.contains(needle), "{needle}: {}", error.message);
    }

    /// `synthetic_patch(name, original)`: ORIGINAL pinned to the synthetic
    /// bytes, PATCHED pinned to the hash of the patch they produce.
    fn synthetic_patch(name: &str, original: &[u8]) -> Vec<u8> {
        common::setup(SCENARIO);
        let result = patch_reward(name, original, None, Some(100001)).unwrap();
        assert_eq!(patch_reward(name, &result, None, None).unwrap(), result);
        let tampered = [&result[..result.len() - 1], b"tampered"].concat();
        assert_profile(patch_reward(name, &tampered, None, None).unwrap_err(), "changed");
        result
    }

    #[test]
    fn dialogue_both_campaign_routes_keep_exact_recipient_and_surrounding_actions() {
        for resource in resource_rewards().keys() {
            let text_route = TEXT_REFS.iter().any(|(key, _)| *key == resource);
            if resource.starts_with("dlg:") && resource != "dlg:aran" && !text_route {
                let name = &resource[4..];
                let original = dialogue_fixture(&specs(resource));
                check_dialogue(name, &original, &synthetic_patch(name, &original), None);
            }
        }
    }

    #[test]
    fn compiled_grants_keep_owner_or_original_object_and_do_not_move_other_rewards() {
        for name in ["asysta01", "ddwell"] {
            let original = script_fixture(name);
            let result = synthetic_patch(name, &original);
            let spec = rewards().get(name).and_then(Value::as_dict).unwrap();
            let old = blocks(&original, if name == "asysta01" { 1 } else { 3 }, b"\n").unwrap().0;
            let new = blocks(&result, old.len() + 1, b"\n").unwrap().0;
            let at = spec.get("branches").and_then(Value::as_list).unwrap()[0].as_int().unwrap() as usize;
            assert_eq!(new[..at], old[..at]);
            assert_eq!(new[at + 2..], old[at + 1..]);
            for (selected, branch) in new[at..at + 2].iter().enumerate() {
                let grant = if name == "asysta01" { action(82, 0, "Helm19", 0) } else { action(140, 0, "helm21", 21) };
                assert_eq!(count(branch, grant.as_bytes()), if selected == 1 { 0 } else { 1 });
                assert_eq!(count(branch, text(spec, "event_global").as_bytes()), 1);
                assert_eq!(count(branch, text(spec, "consumed_global").as_bytes()), selected);
                if name == "asysta01" {
                    assert!(!contains(branch, b"LOCALSFLPR_R"));
                    assert!(contains(branch, action(82, 0, "Misc8y", 0).as_bytes()));
                    assert!(contains(branch, action(30, 2, "AR1512PlantedItems", 0).as_bytes()));
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
                    let display = action(151, if selected == 1 { 100001 } else { 40850 }, "", 1);
                    assert!(contains(branch, display.as_bytes()));
                    assert!(contains(branch, action(177, 0, "", 0).as_bytes()));
                }
            }
        }
    }

    #[test]
    fn aran_two_grants_have_all_four_independent_campaign_combinations() {
        let original = dialogue_fixture(&specs("dlg:aran"));
        check_aran(&original, &synthetic_patch("aran", &original));
    }

    #[test]
    fn text_variants_preserve_original_routes_replies_and_verified_recovery() {
        common::setup(SCENARIO);
        let resources: Vec<&str> = TEXT_REFS.iter().map(|(key, _)| *key).filter(|key| key.starts_with("dlg:")).collect();
        let (_tlk, allocated) = prepare_reward_texts(&make_tlk(&["Unchanged text"]), resources.clone(), "en_us").unwrap();
        for resource in resources {
            let original = text_variant(resource);
            let refs = &allocated[resource];
            let result = patch_reward(resource, &original, Some(refs), None).unwrap();
            check_text_graph(resource, &original, &result, refs);
            assert_eq!(original_reward(resource, &result).unwrap(), Some(original.clone()));
            assert_eq!(patch_reward(resource, &result, None, None).unwrap(), result);
            let moved: BTreeMap<i64, i64> = refs.iter().map(|(old, new)| (*old, new + 1000)).collect();
            let revised = patch_reward(resource, &result, Some(&moved), None).unwrap();
            assert_eq!(original_reward(resource, &revised).unwrap(), Some(original.clone()));
            check_text_graph(resource, &original, &revised, &moved);
            let mut damaged = result.clone();
            damaged[100] ^= 1;
            assert_profile(original_reward(resource, &damaged).unwrap_err(), "changed");
            assert_profile(patch_reward(resource, &original, None, None).unwrap_err(), "allocated");
        }
    }

    #[test]
    fn well_uses_appended_original_text_only_for_selected_source_and_reexports() {
        common::setup(SCENARIO);
        let tlk = make_tlk(&["Existing unrelated text", "Existing helmet narrative"]);
        let (_updated, reference) = prepare_ddwell_text(&tlk, "en_us").unwrap();
        let original = script_fixture("ddwell");
        let result = patch_reward("ddwell", &original, None, Some(reference)).unwrap();
        assert_eq!(patch_reward("ddwell", &result, None, None).unwrap(), result);
        assert_eq!(original_ddwell(&result).unwrap(), (original.clone(), Some(reference)));
        let changed_ref = patch_reward("ddwell", &result, None, Some(reference + 1)).unwrap();
        assert_eq!(original_ddwell(&changed_ref).unwrap(), (original.clone(), Some(reference + 1)));
        assert!(contains(&result, action(151, 40850, "", 1).as_bytes()));
        assert!(contains(&result, action(151, reference, "", 1).as_bytes()));
        let gone = replace(&result, b"AR1202DragonWell", b"AR1202DragonGone", Some(1));
        assert_profile(patch_reward("ddwell", &gone, None, None).unwrap_err(), "changed");
        assert_profile(patch_reward("ddwell", &original, None, None).unwrap_err(), "allocated");
    }

    /// The patcher part of test_original_option_export's
    /// test_dialogue_grant_keeps_exact_charges_through_export.
    #[test]
    fn dialogue_grant_keeps_exact_charges() {
        common::setup(SCENARIO);
        let spec: Dict = rewards().get("ppstat01").and_then(Value::as_dict).unwrap().clone();
        assert!(spec.get("payload").and_then(|payload| payload.get("charges")).unwrap().py_eq(&ints(&[1, 1, 1])));
        let original = dialogue_fixture(std::slice::from_ref(&spec));
        let changed = patch_reward("dlg:ppstat01", &original, None, None).unwrap();
        assert_eq!(patch_reward("dlg:ppstat01", &changed, None, None).unwrap(), changed);
        check_dialogue("ppstat01", &original, &changed, None);
        let dialogue = Dialogue::new(&changed, "ppstat01").unwrap();
        let grant: &[u8] = b"GiveItemCreate(\"ring31\",LastTalkedToBy,1,1,1)";
        for (state, _ordinal) in branches(&spec) {
            let rows = outgoing(&dialogue, state);
            let with_grant: Vec<bool> =
                rows.iter().map(|row| contains(&dialogue.actions[u32_at(row, 16) as usize], grant)).collect();
            assert_eq!(with_grant.iter().filter(|present| **present).count(), 1, "state {state}");
        }
    }
}
