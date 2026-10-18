//! EET's own rename lists (`EET/lib/pcu_dict.tph`) and CIR's older copy:
//! the reference's `tests/test_game_names.py`.

mod common;

use cir_config::game_names::{convert_resource, eet_names, eet_tables, EetNames, Tables};
use common::TempDir;
use std::collections::BTreeMap;

/// The lists as sorted maps (Python compares dictionaries without order).
fn sorted(tables: &Tables) -> BTreeMap<String, BTreeMap<String, String>> {
    tables
        .kinds()
        .map(|kind| {
            let renames = tables.get(kind).expect("listed");
            (kind.to_string(), renames.iter().map(|(old, new)| (old.to_string(), new.to_string())).collect())
        })
        .collect()
}

fn write(root: &TempDir, text: &str) {
    root.write("EET/lib/pcu_dict.tph", Some(text.as_bytes()));
}

/// `write_eet_names`: EET's own format, one array per resource type.
fn write_lists(root: &TempDir, tables: &Tables) {
    let mut kinds: Vec<&str> = tables.kinds().collect();
    kinds.sort_unstable();
    let blocks: Vec<String> = kinds
        .into_iter()
        .map(|kind| {
            let mut entries: Vec<(&str, &str)> = tables.get(kind).expect("listed").iter().collect();
            entries.sort_unstable();
            let rows: String = entries
                .into_iter()
                .map(|(old, new)| format!("\t\"{}\"\t=>\t\"{}\"\r\n", old.to_uppercase(), new.to_uppercase()))
                .collect();
            format!("ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_{kind} BEGIN\r\n{rows}END\r\n")
        })
        .collect();
    write(root, &blocks.join("\r\n"));
}

#[test]
fn sod_names_on_games_without_eets_lists() {
    assert_eq!(convert_resource("bd0120", "are", "sod", "eet", None).unwrap(), "bd0120");
    assert_eq!(convert_resource("bdbart01", "cre", "sod", "bgee", None).unwrap(), "bdbart01");
    for campaign in ["soa", "tob", "eet"] {
        let error = convert_resource("ar0801", "are", campaign, "eet", None).unwrap_err();
        assert!(error.message.starts_with("Unsupported campaign"), "{campaign}: {}", error.message);
    }
}

#[test]
fn installed_lists_rename_bg1_and_sod_names() {
    // As EET 14 writes them: several arrays of one type, quoted and bare
    // names, comments (an em dash among them) and arrays CIR does not read.
    let root = TempDir::new("flir-eet-names-");
    write(
        &root,
        "/* EET resource lists\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\t\"OLD\" => \"WRONG\"\nEND\n*/\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_mus BEGIN\n\t1\t=>\t107\t//CDay1\nEND\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\
         \t\"SLNG03\"\t=>\t\"SLNG03_\"\t// Snare\u{2014}here\n\
         \t\"HELM15\"\t=>\t\"HELM15_\"\nEND\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\n\
         \tSKELWA02\t=>\tBGSKEL02\n\t\"BDBART01\"\t=>\t\"BDBART0_\"\nEND\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\nEND\n",
    );
    let names = eet_names(&root.0).unwrap();
    assert!(names.installed);
    assert!(names.notes.is_empty());
    let itm: Vec<(&str, &str)> = names.tables.get("itm").unwrap().iter().collect();
    assert_eq!(itm, [("slng03", "slng03_"), ("helm15", "helm15_")]);
    assert!(names.tables.get("sto").unwrap().is_empty());
    let cases = [
        ("slng03", "itm", "bg1", "slng03_"),
        ("scrl1v", "itm", "bg1", "scrl1v"),
        ("skelwa02", "cre", "bg1", "bgskel02"),
        ("bdbart01", "cre", "sod", "bdbart0_"),
        ("bag02", "sto", "bg1", "bag02"),
        ("ar0801", "bcs", "sod", "bg0801"),
        ("bd0120", "are", "sod", "bd0120"),
        ("slng03", "itm", "bg2", "slng03"),
    ];
    for (name, extension, campaign, expected) in cases {
        assert_eq!(convert_resource(name, extension, campaign, "eet", Some(&names)).unwrap(), expected, "{name}");
    }
    assert_eq!(convert_resource("slng03", "itm", "bg1", "bgee", Some(&names)).unwrap(), "slng03");
}

#[test]
fn a_later_entry_replaces_an_earlier_one_as_in_weidu() {
    let root = TempDir::new("flir-eet-names-");
    write(
        &root,
        "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 => A\nEND\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nEND\n\
         ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tslng03 => B\nEND\n",
    );
    let names = eet_names(&root.0).unwrap();
    let itm: Vec<(&str, &str)> = names.tables.get("itm").unwrap().iter().collect();
    assert_eq!(itm, [("slng03", "b")]);
}

#[test]
fn lists_written_like_cirs_copy_rename_the_same() {
    let root = TempDir::new("flir-eet-names-");
    let copy = eet_tables().unwrap();
    write_lists(&root, &copy);
    let names = eet_names(&root.0).unwrap();
    assert!(names.installed);
    assert_eq!(sorted(&names.tables), sorted(&copy));
}

#[test]
fn without_readable_lists_cirs_copy_is_used_and_named() {
    let cases = [
        ("absent", None),
        (
            "unexpected line",
            Some(
                "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 SLNG05\nEND\n\
                 ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nEND\n",
            ),
        ),
        ("no cre list", Some("ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 => SLNG03_\nEND\n")),
    ];
    for (case, text) in cases {
        let root = TempDir::new("flir-eet-names-");
        if let Some(text) = text {
            write(&root, text);
        }
        let names: EetNames = eet_names(&root.0).unwrap();
        assert!(!names.installed, "{case}");
        assert_eq!(*names.tables, *eet_tables().unwrap(), "{case}");
        assert_eq!(names.notes.len(), 1, "{case}");
        assert!(names.notes[0].contains("EET's rename lists (EET/lib/pcu_dict.tph)"), "{case}");
        assert!(names.notes[0].contains("uses its own older copy"), "{case}");
        assert_eq!(convert_resource("slng03", "itm", "bg1", "eet", Some(&names)).unwrap(), "slng05", "{case}");
    }
    let root = TempDir::new("flir-eet-names-");
    write(&root, "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 SLNG05\nEND\n");
    assert_eq!(
        eet_names(&root.0).unwrap().notes,
        ["EET's rename lists (EET/lib/pcu_dict.tph) cannot be read (unexpected itm line 'SLNG03 SLNG05'), so Chriz \
          Item Randomiser uses its own older copy; some BG1 and Siege of Dragonspear rows may not be found and are \
          left out"]
    );
}

#[test]
fn folder_names_in_any_letter_case_are_found() {
    let root = TempDir::new("flir-eet-names-");
    root.write(
        "eet/LIB/PCU_DICT.TPH",
        Some(
            b"ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 => SLNG03_\nEND\n\
              ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nEND\n",
        ),
    );
    let names = eet_names(&root.0).unwrap();
    let itm: Vec<(&str, &str)> = names.tables.get("itm").unwrap().iter().collect();
    assert_eq!(itm, [("slng03", "slng03_")]);
}
