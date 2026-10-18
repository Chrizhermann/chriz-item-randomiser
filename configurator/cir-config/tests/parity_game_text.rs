//! Python-recorded parity cases for the `game_text` scenario (see
//! `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_game_text.py` on the same synthetic game texts.

mod common;

use cir_config::error::ErrorKind;
use cir_config::game_text::{append_texts, paired, text_files, unpaired, GameText};
use cir_config::resources::Tlk;
use common::builders::{make_tlk, tlk_text};
use common::TempDir;

const SCENARIO: &str = "game_text";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// Every text of a TLK, by number.
fn texts(data: &[u8]) -> Vec<String> {
    let count = Tlk::new(data.to_vec()).unwrap().count;
    (0..count).map(|number| tlk_text(data, number).unwrap_or_default()).collect()
}

#[test]
fn a_text_gets_one_number_in_both_files_with_its_female_wording() {
    let male = make_tlk(&["Shared", "Same", "Gendered", "Voiced"]);
    let female = make_tlk(&["Shared (f)", "Same", "Gendered (f)", "Voiced"]);
    let (text, refs) = GameText { male: male.clone(), female: Some(female) }
        .append(&[
            ("Same", "Same"),
            ("Gendered", "Gendered (f)"),
            // dialogf.tlk words entry 0 otherwise: a new entry.
            ("Shared", "Shared"),
            ("New", "New (f)"),
            ("New", "New (f)"),
        ])
        .unwrap();
    assert_eq!(refs, [1, 2, 4, 5, 5]);
    assert_eq!(texts(&text.male), ["Shared", "Same", "Gendered", "Voiced", "Shared", "New"]);
    assert_eq!(texts(text.female.as_ref().unwrap()), ["Shared (f)", "Same", "Gendered (f)", "Voiced", "Shared", "New (f)"]);
    assert_eq!(text.append(&[("New", "New (f)")]).unwrap(), (text.clone(), vec![5]));
    // Without dialogf.tlk the game has one text: the female wording is not used.
    let (alone, refs) = GameText::new(male.clone()).append(&[("Gendered", "Other"), ("New", "New (f)")]).unwrap();
    assert_eq!((alone.female.clone(), refs), (None, vec![2, 4]));
    assert_eq!(alone.male, append_texts(&male, &["Gendered".to_string(), "New".to_string()]).unwrap().0);
    let error = GameText { male, female: Some(make_tlk(&["Shared (f)"])) }.append(&[("New", "New")]).unwrap_err();
    assert_eq!(error.kind, ErrorKind::GameText);
    assert!(error.message.contains("different numbers of strings"), "{}", error.message);
}

#[test]
fn paired_copies_what_dialog_tlk_alone_gained() {
    let (male, female) = (make_tlk(&["One", "Two"]), make_tlk(&["One (f)", "Two (f)"]));
    let mut grown = append_texts(&male, &["Three".to_string(), "Four".to_string()]).unwrap().0;
    // Entries are copied as they are, sound and all.
    let row = 0x12 + 3 * 0x1A;
    grown[row..row + 2].copy_from_slice(&3u16.to_le_bytes());
    grown[row + 2..row + 10].copy_from_slice(b"FOURVOX\0");
    grown[row + 10..row + 14].copy_from_slice(&5u32.to_le_bytes());
    grown[row + 14..row + 18].copy_from_slice(&6u32.to_le_bytes());
    let text = paired(grown.clone(), Some(female)).unwrap();
    assert_eq!(text.male, grown);
    let copied = text.female.clone().unwrap();
    assert_eq!(texts(&copied), ["One (f)", "Two (f)", "Three", "Four"]);
    assert_eq!(copied[row..row + 0x12], grown[row..row + 0x12]);
    assert_eq!(paired(grown, Some(copied.clone())).unwrap(), text);
    assert_eq!(paired(male.clone(), None).unwrap(), GameText::new(male.clone()));
    let error = paired(male, Some(copied)).unwrap_err();
    assert_eq!(error.kind, ErrorKind::GameText);
    assert!(error.message.contains("beyond dialog.tlk's end"), "{}", error.message);
}

#[test]
fn the_game_text_files_of_a_language() {
    let root = TempDir::new("flir-game-text-");
    let male = root.write("lang/de_DE/dialog.tlk", Some(&make_tlk(&["a", "b"])));
    assert_eq!(text_files(&root.0, "de_DE").unwrap(), (male.clone(), None));
    assert_eq!(unpaired(&male, None).unwrap(), None);
    let female = root.write("lang/de_DE/DIALOGF.TLK", Some(&make_tlk(&["a (f)", "b (f)"])));
    assert_eq!(text_files(&root.0, "de_DE").unwrap(), (male.clone(), Some(female.clone())));
    assert_eq!(unpaired(&male, Some(&female)).unwrap(), None);
    std::fs::write(&female, make_tlk(&["a (f)"])).unwrap();
    assert_eq!(
        unpaired(&male, Some(&female)).unwrap().as_deref(),
        Some(
            "the game's DIALOGF.TLK and dialog.tlk hold different numbers of strings (1 and 2), so a new text \
             cannot get the same number in both"
        )
    );
    std::fs::write(&female, b"TLK V1").unwrap();
    assert_eq!(unpaired(&male, Some(&female)).unwrap().as_deref(), Some("the game's DIALOGF.TLK is not a TLK V1 file"));
    root.write("dialog.tlk", Some(&make_tlk(&[])));
    assert_eq!(text_files(&root.0, "default").unwrap(), (root.0.join("dialog.tlk"), None));
}
