//! Python-recorded parity cases for the `mode2_rewards_trgeni` scenario
//! (see `configurator/parity/patchers/generate.py`), and the reference's
//! test_trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch
//! on the same synthetic dialogue (it pins `ORIGINAL_HASHES["trgeni01"]`, so
//! it needs the `parity-overrides` feature).

mod common;

use cir_config::py::put_u32;
use cir_config::quest_options::Dialogue;
use common::builders::{pinned, sha};
use common::mode2::{dialogue_fixture, specs};

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
fn builder_matches_the_pinned_synthetic_hash() {
    assert_eq!(sha(&terminal_fixture()), pinned(SCENARIO, "mode2_rewards.ORIGINAL_HASHES", "trgeni01"));
}

#[cfg(feature = "parity-overrides")]
#[test]
fn trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch() {
    use cir_config::error::ErrorKind;
    use cir_config::mode2_rewards::{original_reward, patch_dialogue, patch_reward, patch_text_dialogue};
    use common::builders::{outgoing, replace, u32_at};
    use common::mode2::{check_dialogue, check_text_graph, trgeni_terminal_actions, TRGENI_DEPARTURE};
    use std::collections::BTreeMap;

    common::setup(SCENARIO);
    let resource = "dlg:trgeni01";
    let spec = specs(resource).remove(0);
    let original = terminal_fixture();
    let refs = BTreeMap::from([(43514, 100001)]);
    let legacy = patch_text_dialogue(&original, resource, Some(&refs), false, false).unwrap();
    assert_eq!(original_reward(resource, &legacy).unwrap(), Some(original.clone()));
    let result = patch_reward(resource, &legacy, None, None).unwrap();
    let previous = patch_text_dialogue(&original, resource, Some(&refs), true, false).unwrap();
    assert_eq!(patch_reward(resource, &previous, None, None).unwrap(), result);
    assert_ne!(result, legacy);
    assert_eq!(result, patch_reward(resource, &original, Some(&refs), None).unwrap());
    assert_eq!(patch_reward(resource, &result, None, None).unwrap(), result);
    assert_eq!(original_reward(resource, &result).unwrap(), Some(original.clone()));
    let branches = patch_dialogue(&original, &[spec], &[], true, true).unwrap();
    check_dialogue("trgeni01", &original, &branches, None);
    check_text_graph(resource, &original, &result, &refs);
    for state in [37, 38] {
        let before = Dialogue::new(&legacy, "trgeni01").unwrap();
        let old = &before.actions[u32_at(&outgoing(&before, state)[0], 16) as usize];
        let after = Dialogue::new(&result, "trgeni01").unwrap();
        let changed = &after.actions[u32_at(&outgoing(&after, state)[0], 16) as usize];
        assert_eq!(old, &[trgeni_terminal_actions().as_bytes(), b"\r\n"].concat());
        assert_eq!(&replace(changed, TRGENI_DEPARTURE, b"DestroySelf()", None), old);
    }
    // An altered prior patch is not accepted merely because its original
    // body and recovery trailer still match.
    let tampered = replace(&legacy, b"FLPR_Transfer(", b"FLPR_Tampered(", Some(1));
    let error = patch_reward(resource, &tampered, None, None).unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    assert!(error.message.contains("patch changed"), "{}", error.message);
}
