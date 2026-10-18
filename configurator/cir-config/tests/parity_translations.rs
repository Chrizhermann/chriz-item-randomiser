//! Python-recorded parity cases for the `translations` scenario (the code
//! pages, the shipped translations, translation folders on disk and the TRA
//! reader; see `configurator/parity/patchers/generate.py`), and the
//! reference's `tests/test_translations.py`.

mod common;

use cir_config::container_rewards::{self, prepare_container_texts};
use cir_config::game_text::GameText;
use cir_config::mode2_rewards::{self, prepare_reward_texts};
use cir_config::translations::{language, Line, Translations, CONVERTED};
use common::builders::{make_tlk, tlk_text};
use common::TempDir;
use std::collections::BTreeMap;
use std::path::PathBuf;

const SCENARIO: &str = "translations";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// The game languages BG:EE, BG2:EE and EET ship (2.7.3.0).
const SHIPPED: [&str; 15] = [
    "cs_CZ", "de_DE", "en_US", "es_ES", "fr_FR", "hu_HU", "it_IT", "ja_JP", "ko_KR", "pl_PL", "pt_BR", "ru_RU", "tr_TR",
    "uk_UA", "zh_CN",
];
/// The languages with every reward text; a new translation adds its
/// language (docs/translating-reward-texts.md).
const COMPLETE: [&str; 3] = ["en_US", "pl_PL", "de_DE"];

fn line(file: &str, number: i64) -> Line {
    (file.to_string(), number)
}

/// Every line a text route or a golem chest shows.
fn every_line() -> Vec<Line> {
    let mut resources: Vec<&str> = mode2_rewards::TEXT_REFS.iter().map(|(resource, _)| *resource).collect();
    resources.sort_unstable();
    let mut lines = Vec::new();
    for resource in resources {
        lines.extend(mode2_rewards::text_lines(resource).unwrap());
    }
    lines.extend(container_rewards::text_lines());
    lines
}

fn repository_languages() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("languages")
}

fn pair(text: &str, female: &str) -> (String, String) {
    (text.to_string(), female.to_string())
}

#[test]
fn the_complete_translations_have_every_reward_text() {
    for code in COMPLETE {
        assert_eq!(Translations::Embedded.missing(code, &every_line()).unwrap(), None, "{code}");
    }
}

#[test]
fn every_shipped_language_has_a_translation_folder_or_a_reason() {
    for code in SHIPPED {
        let (name, folder, _) = language(code).unwrap();
        let reason = Translations::Embedded.missing(code, &every_line()).unwrap();
        if !repository_languages().join(folder).is_dir() {
            assert_eq!(reason, Some(format!("Item Randomiser's reward texts have no {name} translation yet")), "{code}");
        } else if !COMPLETE.contains(&code) {
            let reason = reason.unwrap_or_default();
            assert!(reason.contains(&format!("Item Randomiser's {name} translation has no reward text")), "{reason}");
        }
    }
}

#[test]
fn texts_are_read_in_their_code_page_with_their_female_wording() {
    // Polish is Windows-1250 and words a female protagonist's lines itself.
    let polish = Translations::Embedded.lines("pl_PL", &[line("in-game.tra", 101), line("in-game.tra", 399)]).unwrap();
    let (text, female) = &polish[&line("in-game.tra", 101)];
    assert!(text.contains("podniosłeś") && female.contains("podniosłaś"), "{text} / {female}");
    let (text, female) = &polish[&line("in-game.tra", 399)];
    assert_eq!(text, female);
    let german = Translations::Embedded.lines("de_DE", &[line("in-game.tra", 104), line("ppdjinn.tra", 99)]).unwrap();
    let (text, female) = &german[&line("in-game.tra", 104)];
    assert!(text.contains("der bessere Kämpfer") && female.contains("die bessere Kämpferin"), "{text} / {female}");
    assert!(german[&line("ppdjinn.tra", 99)].0.contains("<GABBER>"));
    let english = Translations::Embedded.lines("en_US", &[line("in-game.tra", 101)]).unwrap();
    let golems = "As you pick up the item, two of the golems come to life and attack.";
    assert_eq!(english[&line("in-game.tra", 101)], pair(golems, golems));
}

#[test]
fn polish_reward_texts_go_to_both_files() {
    let start = GameText { male: make_tlk(&["a"]), female: Some(make_tlk(&["a (f)"])) };
    let (text, refs) = prepare_reward_texts(&start, ["dlg:sctelwyn", "bcs:ddwell"], "pl_PL").unwrap();
    for (resource, old, number) in [("dlg:sctelwyn", 29325, 111), ("bcs:ddwell", 40850, 399)] {
        let reference = refs[resource][&old] as u32;
        let shown = (tlk_text(&text.male, reference).unwrap(), tlk_text(text.female.as_ref().unwrap(), reference).unwrap());
        let wanted = line("in-game.tra", number);
        assert_eq!(shown, Translations::Embedded.lines("pl_PL", std::slice::from_ref(&wanted)).unwrap()[&wanted], "{resource}");
    }
    assert_eq!(prepare_reward_texts(&text, ["dlg:sctelwyn", "bcs:ddwell"], "pl_PL").unwrap(), (text.clone(), refs));
    let (golems, golem_refs) = prepare_container_texts(&text, ["bcs:kpgol01"], "pl_PL").unwrap();
    let reference = golem_refs["bcs:kpgol01"][&38758] as u32;
    assert_ne!(tlk_text(&golems.male, reference), tlk_text(golems.female.as_ref().unwrap(), reference));
}

#[test]
fn a_game_language_without_a_translation() {
    assert_eq!(
        Translations::Embedded.missing("default", &every_line()).unwrap().as_deref(),
        Some(
            "the game keeps its text outside a language directory, so CIR cannot tell which translation of Item \
             Randomiser's reward texts it needs"
        )
    );
    assert_eq!(
        Translations::Embedded.missing("nb_NO", &every_line()).unwrap().as_deref(),
        Some("Item Randomiser's reward texts have no translation for the game language nb_NO")
    );
    assert!(Translations::Embedded.texts("nb_NO", "in-game.tra").unwrap().is_empty());
}

/// Translation folders as WeiDU leaves them, in a copy outside the mod.
fn folder() -> (TempDir, PathBuf, Translations) {
    let temporary = TempDir::new("flir-translations-");
    let languages = temporary.write("languages/", None);
    let source = Translations::Folder(languages.clone());
    (temporary, languages, source)
}

/// `CZECH` in Windows-1250: "@101 = ~Zvedáš předmět.~", "@399 = ~Studna.~ ~Studna (ž).~".
const CZECH_1250: &[u8] = b"@101 = ~Zved\xe1\x9a p\xf8edm\xect.~\r\n@399 = ~Studna.~ ~Studna (\x9e).~\r\n";
const CZECH: &str = "@101 = ~Zvedáš předmět.~\r\n@399 = ~Studna.~ ~Studna (ž).~\r\n";

#[test]
fn a_folder_weidu_converted_to_utf8_and_the_ee_texts() {
    let (temporary, _, source) = folder();
    temporary.write("languages/czech/in-game.tra", Some(CZECH_1250));
    assert_eq!(
        source.lines("cs_CZ", &[line("in-game.tra", 101), line("in-game.tra", 399)]).unwrap(),
        BTreeMap::from([
            (line("in-game.tra", 101), pair("Zvedáš předmět.", "Zvedáš předmět.")),
            (line("in-game.tra", 399), pair("Studna.", "Studna (ž).")),
        ])
    );
    // Installed in Czech, the folder is UTF-8 and carries WeiDU's marker.
    temporary.write("languages/czech/in-game.tra", Some(CZECH.as_bytes()));
    temporary.write(&format!("languages/czech/{CONVERTED}"), Some(b"marker"));
    assert_eq!(source.lines("cs_CZ", &[line("in-game.tra", 101)]).unwrap()[&line("in-game.tra", 101)].0, "Zvedáš předmět.");
    // The Enhanced Edition texts are UTF-8 as written and win.
    temporary.write("languages/bgee/czech/in-game.tra", Some("@101 = ~Zvedáš věc.~\r\n".as_bytes()));
    assert_eq!(source.lines("cs_CZ", &[line("in-game.tra", 101)]).unwrap()[&line("in-game.tra", 101)].0, "Zvedáš věc.");
}

#[test]
fn a_partial_or_unreadable_translation_names_what_is_missing() {
    let (temporary, _, source) = folder();
    temporary.write("languages/czech/in-game.tra", Some(b"@101 = ~Zved\xe1\x9a p\xf8edm\xect.~\r\n"));
    assert_eq!(
        source.missing("cs_CZ", &[line("in-game.tra", 101), line("in-game.tra", 399)]).unwrap().as_deref(),
        Some("Item Randomiser's Czech translation has no reward text @399 in czech/in-game.tra")
    );
    assert_eq!(
        source.missing("cs_CZ", &[line("ppdjinn.tra", 99)]).unwrap().as_deref(),
        Some("Item Randomiser's Czech translation has no reward text @99 in czech/ppdjinn.tra")
    );
    temporary.write("languages/polish/in-game.tra", Some(b"@101 = ~\x81~\r\n"));
    assert_eq!(
        source.missing("pl_PL", &[line("in-game.tra", 101)]).unwrap().as_deref(),
        Some("languages/polish/in-game.tra is not Windows-1250 text, which this translation file must be")
    );
    temporary.write("languages/japanese/in-game.tra", Some(b"@101 = ~\x81\x20~\r\n"));
    assert_eq!(
        source.missing("ja_JP", &[line("in-game.tra", 101)]).unwrap().as_deref(),
        Some("languages/japanese/in-game.tra is not Shift-JIS (Windows-932) text, which this translation file must be")
    );
}

#[test]
fn sources_cover_what_decides_the_texts() {
    let (temporary, languages, source) = folder();
    assert_eq!(source.sources("cs_CZ").unwrap(), std::slice::from_ref(&languages));
    let tra = temporary.write("languages/czech/in-game.tra", Some(b"@101 = ~x~\r\n"));
    let ee = temporary.write("languages/bgee/czech/in-game.tra", Some(b"@101 = ~y~\r\n"));
    temporary.write("languages/bgee/polish/in-game.tra", Some(b"@101 = ~z~\r\n"));
    assert_eq!(
        source.sources("cs_CZ").unwrap(),
        [languages.clone(), languages.join("bgee"), languages.join("czech"), languages.join("bgee").join("czech"), tra, ee]
    );
    assert_eq!(source.sources("default").unwrap(), [languages.clone(), languages.join("bgee")]);
}
