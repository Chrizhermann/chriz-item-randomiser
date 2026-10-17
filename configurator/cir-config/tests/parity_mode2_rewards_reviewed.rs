//! Python-recorded parity cases for the `mode2_rewards_reviewed` scenario
//! (see `configurator/parity/patchers/generate.py`).

mod common;

const SCENARIO: &str = "mode2_rewards_reviewed";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}
