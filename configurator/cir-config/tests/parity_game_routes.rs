//! Python-recorded patcher cases on an installed game's reward routes
//! (`configurator/parity/patchers/generate.py --game`). The fixture holds the
//! game's bytes and stays outside the repository: the test runs when
//! `CIR_PARITY_GAME_ROUTES` names it.

mod common;

use std::path::Path;

#[test]
fn matches_the_reference_on_an_installed_game() {
    let Some(path) = std::env::var_os("CIR_PARITY_GAME_ROUTES") else {
        eprintln!("skipped: CIR_PARITY_GAME_ROUTES names no fixture recorded with generate.py --game");
        return;
    };
    let (ran, _) = common::run_path(Path::new(&path));
    assert!(ran > 0, "the fixture holds no cases");
}
