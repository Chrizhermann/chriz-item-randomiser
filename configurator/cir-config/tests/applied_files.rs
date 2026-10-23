//! Apply's own files in the game are not a change of the game's mods (the
//! reference's `tests/test_applied_files.py`, on a smaller synthetic game).
//!
//! Apply writes a route's patched dialogue into override and appends its text
//! to the game text; the next scan reads both as the game had them before, so
//! the configuration applied last still belongs to the game's scan. Apply
//! works on Windows only.
#![cfg(windows)]

mod common;

use cir_config::activation::deploy_export;
use cir_config::json::{self, Value};
use cir_config::resources::{scan_game, sha256_hex};
use cir_config::sources::scan_world;
use common::builders::{make_itm, make_tlk};
use common::TempDir;
use std::fs;
use std::path::PathBuf;

const DIALOGUE: &[u8] = b"DLG V1.0 the ogre's dialogue as the game has it";
const PATCHED: &[u8] = b"DLG V1.0 the ogre's dialogue with Chriz Item Randomiser's reward";
const TEXT: [&str; 2] = ["Blade", "Test sword"];

/// A KEY V1 without archives: every resource of the game is in override.
fn empty_key() -> Vec<u8> {
    let mut data = b"KEY V1  ".to_vec();
    for value in [0u32, 0, 0x18, 0x18] {
        data.extend_from_slice(&value.to_le_bytes());
    }
    data
}

fn canonical(value: &Value) -> String {
    String::from_utf8(json::canonical_bytes(value).expect("JSON")).expect("UTF-8")
}

struct Game(TempDir);

impl Game {
    fn new() -> Game {
        let root = TempDir::new("cir-applied-files-");
        root.write("game/chitin.key", Some(&empty_key()));
        root.write("game/lang/en_us/dialog.tlk", Some(&make_tlk(&TEXT)));
        root.write("game/override/synth001.itm", Some(&make_itm(0, 1, 300)));
        // Its name points past the end of the game text, as some mods' items do.
        root.write("game/override/synth002.itm", Some(&make_itm(0, 2, 300)));
        root.write("game/override/uhogre01.dlg", Some(DIALOGUE));
        root.write("game/override/ddwell.bcs", Some(b"SC\nCR\nthe well\nSC\n"));
        Game(root)
    }

    fn game(&self) -> PathBuf {
        self.0 .0.join("game")
    }

    fn path(&self, relative: &str) -> PathBuf {
        relative.split('/').fold(self.game(), |path, part| path.join(part))
    }

    /// The catalog and world scans as canonical JSON.
    fn scans(&self) -> (String, String) {
        let catalog = scan_game(&self.game(), Some("en_us")).expect("catalog");
        let world = scan_world(&catalog).expect("world");
        (canonical(&catalog), canonical(&world))
    }

    /// Deploy an export as Apply's: the ogre's dialogue and the text one route appends.
    fn apply(&self, dialogue: &[u8]) {
        let appended = make_tlk(&[TEXT[0], TEXT[1], "You pull an item from the murky water."]);
        let files: [(&str, &[u8]); 4] = [
            ("next.lua", b"return {}\n"),
            ("source-gate.lua", b"return {}\n"),
            ("resource-overrides/UHOGRE01.DLG", dialogue),
            ("game-overrides/lang/en_us/dialog.tlk", &appended),
        ];
        for (name, data) in files {
            self.0.write(&format!("workspace/native/{name}"), Some(data));
        }
        let names: Vec<Value> = files.iter().map(|(name, _)| Value::from(*name)).collect();
        let listing = Value::Dict(cir_config::dict! {"schema" => "flir-export-files-v1", "files" => Value::List(names)});
        self.0.write("workspace/native/export-files.json", Some(&json::canonical_bytes(&listing).expect("JSON")));
        deploy_export(&self.game(), &self.0 .0.join("workspace").join("native")).expect("deployed");
    }

    fn record(&self) -> Value {
        json::parse_strict(&fs::read(self.path("randomiser-campaigns/applied-files.json")).expect("record"), "record")
            .expect("JSON")
    }
}

#[test]
fn a_rescan_after_apply_equals_the_scan_before() {
    let game = Game::new();
    let before = game.scans();
    game.apply(PATCHED);
    assert_eq!(fs::read(game.path("override/uhogre01.dlg")).unwrap(), PATCHED);
    assert_eq!(game.scans(), before);
    let text = fs::read(game.path("lang/en_us/dialog.tlk")).unwrap();
    let original = make_tlk(&TEXT);
    let expected = format!(
        r#"{{"files": {{"lang/en_us/dialog.tlk": {{"before": {{"sha256": "{}", "size": {}, "strings": 2}}, "written": "{}"}},
            "override/UHOGRE01.DLG": {{"before": {{"sha256": "{}", "size": {}}}, "written": "{}"}}}},
            "schema": "flir-applied-files-v1"}}"#,
        sha256_hex(&original),
        original.len(),
        sha256_hex(&text),
        sha256_hex(DIALOGUE),
        DIALOGUE.len(),
        sha256_hex(PATCHED)
    );
    assert_eq!(canonical(&game.record()), canonical(&json::parse_lenient(&expected).unwrap()));
    assert_eq!(fs::read(game.path("randomiser-campaigns/before-apply/override/UHOGRE01.DLG")).unwrap(), DIALOGUE);
    // Apply again with the same files: nothing changes, the record stays.
    let record = fs::read(game.path("randomiser-campaigns/applied-files.json")).unwrap();
    game.apply(PATCHED);
    assert_eq!(fs::read(game.path("randomiser-campaigns/applied-files.json")).unwrap(), record);
    assert_eq!(game.scans(), before);
}

#[test]
fn a_real_change_of_the_game_still_changes_the_scan() {
    let game = Game::new();
    let before = game.scans();
    game.apply(PATCHED);
    // Another mod changes the dialogue Apply wrote: read as it is.
    let changed = [PATCHED, b"\0"].concat();
    fs::write(game.path("override/uhogre01.dlg"), &changed).unwrap();
    let (_, world) = game.scans();
    assert_ne!(world, before.1);
    assert!(world.contains(&sha256_hex(&changed)));
    // Its uninstall puts back Apply's file: the game is as before.
    fs::write(game.path("override/uhogre01.dlg"), PATCHED).unwrap();
    assert_eq!(game.scans(), before);
    // A damaged or missing copy of the earlier file: the file is read as it is.
    let copy = game.path("randomiser-campaigns/before-apply/override/UHOGRE01.DLG");
    fs::write(&copy, b"another dialogue").unwrap();
    let (_, damaged) = game.scans();
    assert!(damaged.contains(&sha256_hex(PATCHED)) && damaged != before.1);
    fs::remove_file(&copy).unwrap();
    assert_eq!(game.scans().1, damaged);
    // A record that cannot be read is not used: Apply's files are read as they are.
    fs::write(&copy, DIALOGUE).unwrap();
    assert_eq!(game.scans(), before);
    fs::write(game.path("randomiser-campaigns/applied-files.json"), b"{").unwrap();
    let (catalog, world) = game.scans();
    assert!(world.contains(&sha256_hex(PATCHED)) && world != before.1);
    assert!(catalog.contains("murky water"));
}

#[test]
fn texts_apply_appended_name_no_item() {
    let game = Game::new();
    let (catalog, world) = game.scans();
    assert!(catalog.contains("Blade") && !catalog.contains("murky water"));
    game.apply(PATCHED);
    assert_eq!(game.scans(), (catalog.clone(), world));
    // Another mod adds to the game text after Apply: it is read as it is.
    let text = game.path("lang/en_us/dialog.tlk");
    let mut changed = fs::read(&text).unwrap();
    changed.push(0);
    fs::write(&text, changed).unwrap();
    let (rescanned, _) = game.scans();
    assert!(rescanned.contains("murky water") && rescanned != catalog);
}
