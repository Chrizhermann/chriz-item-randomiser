//! Python-recorded parity cases for the `mode2_rewards_trgeni` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! test_trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch
//! on the same synthetic dialogue.

mod common;

use cir_config::error::ErrorKind;
use cir_config::mode2_rewards::{own_patch, patch_dialogue, patch_reward, patch_text_dialogue, text_original};
use cir_config::py::put_u32;
use cir_config::quest_options::Dialogue;
use common::builders::{outgoing, replace, u32_at};
use common::mode2::{check_dialogue, check_text_graph, dialogue_fixture, specs, trgeni_terminal_actions, TRGENI_DEPARTURE};
use std::collections::BTreeMap;

const SCENARIO: &str = "mode2_rewards_trgeni";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// The reviewed text route's state 35 carries original strref 43514.
fn terminal_fixture() -> Vec<u8> {
    let spec = specs("dlg:trgeni01").remove(0);
    let mut dialogue = Dialogue::new(&dialogue_fixture(&[spec]), "trgeni01").unwrap();
    put_u32(&mut dialogue.states[35], 0, 43514);
    dialogue.render()
}

#[test]
fn builder_matches_the_reference_builder() {
    let fixture = common::load(SCENARIO);
    assert_eq!(fixture.input("patch_reward original", "data"), terminal_fixture());
}

#[test]
fn trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch() {
    let resource = "dlg:trgeni01";
    let spec = specs(resource).remove(0);
    let original = terminal_fixture();
    let refs = BTreeMap::from([(43514, 100001)]);
    let legacy = patch_text_dialogue(&original, resource, Some(&refs), false, false).unwrap();
    assert_eq!(text_original(&legacy, resource).unwrap(), Some((original.clone(), refs.clone())));
    assert!(own_patch(resource, &legacy).unwrap());
    let result = patch_reward(resource, &legacy, None, None).unwrap();
    let previous = patch_text_dialogue(&original, resource, Some(&refs), true, false).unwrap();
    assert_eq!(patch_reward(resource, &previous, None, None).unwrap(), result);
    assert_ne!(result, legacy);
    assert_eq!(result, patch_reward(resource, &original, Some(&refs), None).unwrap());
    assert_eq!(patch_reward(resource, &result, None, None).unwrap(), result);
    assert_eq!(text_original(&result, resource).unwrap(), Some((original.clone(), refs.clone())));
    let branches = patch_dialogue(&original, std::slice::from_ref(&spec), &[], true, true, None).unwrap();
    check_dialogue("trgeni01", &original, &branches, None, None);
    check_text_graph(resource, &original, &result, &refs);
    for state in [37, 38] {
        let before = Dialogue::new(&legacy, "trgeni01").unwrap();
        let old = &before.actions[u32_at(&outgoing(&before, state)[0], 16) as usize];
        let after = Dialogue::new(&result, "trgeni01").unwrap();
        let changed = &after.actions[u32_at(&outgoing(&after, state)[0], 16) as usize];
        assert_eq!(old, &[trgeni_terminal_actions().as_bytes(), b"\r\n"].concat());
        assert_eq!(&replace(changed, TRGENI_DEPARTURE, b"DestroySelf()", None), old);
    }
    // The departure that waits is the speaker's own, after the replies that
    // hand the reward over; an unreachable DestroySelf stays instant.
    let mut unreached = Dialogue::new(&original, "trgeni01").unwrap();
    let first = u32_at(&unreached.states[36], 4) as usize;
    put_u32(&mut unreached.transitions[first], 28, 2);
    let unreached = unreached.render();
    let after = Dialogue::new(&patch_dialogue(&unreached, std::slice::from_ref(&spec), &[], true, true, None).unwrap(), "trgeni01").unwrap();
    for state in [37, 38] {
        let changed = &after.actions[u32_at(&outgoing(&after, state)[0], 16) as usize];
        assert!(!common::builders::contains(changed, TRGENI_DEPARTURE));
    }
    // An altered prior patch is not accepted merely because its original
    // body and recovery trailer still match.
    let tampered = replace(&legacy, b"FLPR_Transfer(", b"FLPR_Tampered(", Some(1));
    let error = patch_reward(resource, &tampered, None, None).unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    assert!(error.message.contains("changed copy"), "{}", error.message);
}
