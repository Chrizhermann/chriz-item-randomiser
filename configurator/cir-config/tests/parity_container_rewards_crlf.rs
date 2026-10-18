//! Python-recorded parity cases for the `container_rewards_crlf` scenario
//! (see `configurator/parity/patchers/generate.py`).

mod common;

const SCENARIO: &str = "container_rewards_crlf";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}
