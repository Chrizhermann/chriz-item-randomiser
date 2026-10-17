//! Python-recorded parity cases for the `quest_options_crlf` scenario
//! (see `configurator/parity/patchers/generate.py`).

mod common;

const SCENARIO: &str = "quest_options_crlf";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}
