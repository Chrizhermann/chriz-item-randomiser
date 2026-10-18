//! Python-recorded parity cases for the `spell_rewards_layout` scenario
//! (see `configurator/parity/patchers/generate.py`).

mod common;

const SCENARIO: &str = "spell_rewards_layout";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}
