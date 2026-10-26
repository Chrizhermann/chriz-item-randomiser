//! Shared harness for the patcher parity tests: synthetic builders mirroring
//! the reference's tests, and the runner for the fixtures that
//! `configurator/parity/patchers/generate.py` records from the Python
//! implementation.
#![allow(dead_code)]

pub mod builders;
pub mod mode2;

use cir_config::error::{CHANGED_SINCE_SCAN, MISSING_TRANSLATION, SCRIPT_ERROR};
use cir_config::game_names::EetNames;
use cir_config::game_text::{self, GameText};
use cir_config::json::{self, Dict, Value};
use cir_config::mode2_rewards::Naming;
use cir_config::py::{from_hex, hex};
use cir_config::resources::sha256_hex;
use cir_config::translations::{self, Line, Translations};
use cir_config::{
    codepages, container_rewards, creature_inventory, cursed_disguises, group_recipients, mode2_rewards,
    original_catalog, original_options, quest_options, quest_rewards, reward_copies, spell_rewards, store_inventory,
    Error, ErrorKind, Result,
};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Once;

/// A folder in the system temporary folder, removed when dropped.
pub struct TempDir(pub PathBuf);

impl TempDir {
    pub fn new(prefix: &str) -> TempDir {
        static COUNT: AtomicUsize = AtomicUsize::new(0);
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_nanos());
        let path = std::env::temp_dir()
            .join(format!("{prefix}{}-{}-{nanos}", std::process::id(), COUNT.fetch_add(1, Ordering::SeqCst)));
        std::fs::create_dir_all(&path).expect("temporary folder");
        TempDir(path)
    }

    /// Write `data` to `relative` (a "/" path), or make the folder `relative`
    /// ("name/") for `None`.
    pub fn write(&self, relative: &str, data: Option<&[u8]>) -> PathBuf {
        let path = relative.trim_end_matches('/').split('/').fold(self.0.clone(), |path, part| path.join(part));
        match data {
            None => std::fs::create_dir_all(&path).expect("folder"),
            Some(data) => {
                std::fs::create_dir_all(path.parent().expect("parent")).expect("parent folder");
                std::fs::write(&path, data).expect("file");
            }
        }
        path
    }

    /// `path.relative_to(root).as_posix()`.
    pub fn relative(&self, path: &Path) -> String {
        let relative = path.strip_prefix(&self.0).expect("inside the folder");
        relative.components().map(|part| part.as_os_str().to_string_lossy().into_owned()).collect::<Vec<_>>().join("/")
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// What `configurator/parity/codepages.py`'s digests() hashes: strict
/// decoding of every one- and two-byte sequence, per code page.
fn codepage_digests() -> Value {
    let mut result = Dict::new();
    for encoding in codepages::CODE_PAGES.iter().copied().chain(["utf-8"]) {
        let mut stream = Vec::with_capacity(1 << 20);
        let mut outcome = |data: &[u8]| match codepages::decode(data, encoding) {
            Some(text) => {
                stream.push(b'=');
                stream.extend_from_slice(text.as_bytes());
                stream.push(0);
            }
            None => stream.extend_from_slice(b"!\0"),
        };
        for first in 0..=255u8 {
            outcome(&[first]);
            for second in 0..=255u8 {
                outcome(&[first, second]);
            }
        }
        result.insert(encoding, Value::from(sha256_hex(&stream)));
    }
    Value::Dict(result)
}

pub fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures").join("patchers")
}

/// Point the pinned-hash overrides at a scenario before anything reads them.
/// Every test of a binary calls this first with the same scenario.
pub fn setup(scenario: &str) {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        if cfg!(feature = "parity-overrides") {
            std::env::set_var("CIR_PIN_OVERRIDES", fixtures().join(format!("{scenario}.pins.json")));
        }
    });
}

pub struct Fixture {
    pub cases: Vec<Value>,
    pub blobs: Dict,
}

pub fn load(scenario: &str) -> Fixture {
    load_path(&fixtures().join(format!("{scenario}.json")))
}

pub fn load_path(path: &Path) -> Fixture {
    let data = std::fs::read(path).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    let document = json::parse_strict(&data, "fixture").expect("fixture JSON");
    let cases = document.get("cases").and_then(Value::as_list).expect("cases").clone();
    let blobs = document.get("blobs").and_then(Value::as_dict).expect("blobs").clone();
    Fixture { cases, blobs }
}

impl Fixture {
    pub fn blob(&self, value: &Value) -> Vec<u8> {
        let key = value.get("$blob").and_then(Value::as_str).unwrap_or_else(|| panic!("not a blob: {value:?}"));
        from_hex(self.blobs.get(key).and_then(Value::as_str).expect("blob")).expect("hex blob")
    }

    /// A case's recorded arguments by name.
    pub fn args(&self, name: &str) -> &Dict {
        self.cases
            .iter()
            .find(|case| case.get("name").and_then(Value::as_str) == Some(name))
            .and_then(|case| case.get("args"))
            .and_then(Value::as_dict)
            .unwrap_or_else(|| panic!("no case {name}"))
    }

    /// A case's input bytes argument by case and argument name.
    pub fn input(&self, name: &str, argument: &str) -> Vec<u8> {
        self.blob(self.args(name).get(argument).unwrap_or_else(|| panic!("{name} has no {argument}")))
    }
}

fn kind_name(kind: ErrorKind) -> &'static str {
    match kind {
        ErrorKind::Profile => "Profile",
        ErrorKind::Scan => "Scan",
        ErrorKind::Os => "Os",
        ErrorKind::Value => "Value",
        ErrorKind::Key => "Key",
        ErrorKind::Type => "Type",
        ErrorKind::GameText => "GameText",
        ErrorKind::Overflow => "Overflow",
        ErrorKind::Attribute => "Attribute",
    }
}

pub fn bytes_value(data: &[u8]) -> Value {
    Value::Dict(cir_config::dict! {"$blob" => sha256_hex(data)})
}

fn int_keys(map: &BTreeMap<i64, i64>) -> Value {
    Value::Dict(map.iter().map(|(key, value)| (key.to_string(), Value::from(*value))).collect())
}

fn nested_refs(map: &BTreeMap<String, BTreeMap<i64, i64>>) -> Value {
    Value::Dict(map.iter().map(|(key, refs)| (key.clone(), int_keys(refs))).collect())
}

/// A GameText as the reference's NamedTuple encodes: [dialog.tlk, dialogf.tlk or None].
pub fn game_text_value(text: &GameText) -> Value {
    Value::List(vec![bytes_value(&text.male), text.female.as_deref().map_or(Value::Null, bytes_value)])
}

fn strrefs(refs: &[u32]) -> Value {
    Value::List(refs.iter().map(|&reference| Value::from(reference as i64)).collect())
}

fn lines_value(lines: &[Line]) -> Value {
    Value::List(lines.iter().map(|(file, number)| Value::List(vec![Value::from(file.as_str()), Value::from(*number)])).collect())
}

/// `listed_lines`: [file, number, text, female wording] in line order.
fn listed_lines(found: &BTreeMap<Line, (String, String)>) -> Value {
    Value::List(
        found
            .iter()
            .map(|((file, number), (text, female))| {
                Value::List(vec![Value::from(file.as_str()), Value::from(*number), Value::from(text.as_str()), Value::from(female.as_str())])
            })
            .collect(),
    )
}

/// `listed_texts`: [number, text, female wording] in number order.
fn listed_texts(found: &translations::Texts) -> Value {
    Value::List(
        found
            .iter()
            .map(|(number, (text, female))| Value::List(vec![Value::from(*number), Value::from(text.as_str()), Value::from(female.as_str())]))
            .collect(),
    )
}

/// A recovery's (original, text ref or None, own patch), as the reference's tuple.
fn recovered((original, reference, own): (Vec<u8>, Option<i64>, bool)) -> Value {
    Value::List(vec![bytes_value(&original), reference.map_or(Value::Null, Value::from), Value::Bool(own)])
}

fn files(entries: &[(String, Vec<u8>)]) -> Value {
    Value::Dict(entries.iter().map(|(name, data)| (name.clone(), bytes_value(data))).collect())
}

struct Args<'a> {
    fixture: &'a Fixture,
    values: &'a Dict,
}

impl Args<'_> {
    fn value(&self, key: &str) -> &Value {
        self.values.get(key).unwrap_or_else(|| panic!("argument {key}"))
    }
    fn bytes(&self, key: &str) -> Vec<u8> {
        self.fixture.blob(self.value(key))
    }
    fn str(&self, key: &str) -> String {
        self.value(key).as_str().unwrap_or_else(|| panic!("str argument {key}")).to_string()
    }
    fn bool(&self, key: &str) -> bool {
        self.value(key).as_bool().unwrap_or_else(|| panic!("bool argument {key}"))
    }
    fn int(&self, key: &str) -> i64 {
        self.value(key).as_int().unwrap_or_else(|| panic!("int argument {key}"))
    }
    fn optional_int(&self, key: &str) -> Option<i64> {
        self.values.get(key).and_then(Value::as_int)
    }
    fn refs(&self, key: &str) -> Option<BTreeMap<i64, i64>> {
        self.values.get(key).and_then(Value::as_dict).map(|map| {
            map.iter().map(|(old, new)| (old.parse().expect("int key"), new.as_int().expect("int ref"))).collect()
        })
    }
    fn ordered_refs(&self, key: &str) -> Vec<(i64, i64)> {
        self.values.get(key).and_then(Value::as_dict).map_or_else(Vec::new, |map| {
            map.iter().map(|(old, new)| (old.parse().expect("int key"), new.as_int().expect("int ref"))).collect()
        })
    }
    fn dict(&self, key: &str) -> Dict {
        self.value(key).as_dict().unwrap_or_else(|| panic!("dict argument {key}")).clone()
    }
    fn strings(&self, key: &str) -> Vec<String> {
        self.value(key).as_list().expect("list argument").iter().map(|item| item.as_str().expect("str").to_string()).collect()
    }
    fn optional_bytes(&self, key: &str) -> Option<Vec<u8>> {
        match self.value(key) {
            Value::Null => None,
            value => Some(self.fixture.blob(value)),
        }
    }
    /// A GameText argument, recorded as [dialog.tlk, dialogf.tlk or None].
    fn game_text(&self, key: &str) -> GameText {
        let parts = self.value(key).as_list().unwrap_or_else(|| panic!("GameText argument {key}"));
        let female = match &parts[1] {
            Value::Null => None,
            value => Some(self.fixture.blob(value)),
        };
        GameText { male: self.fixture.blob(&parts[0]), female }
    }
    /// (male, female) text pairs.
    fn pairs(&self, key: &str) -> Vec<(String, String)> {
        self.value(key)
            .as_list()
            .expect("pairs")
            .iter()
            .map(|pair| {
                let pair = pair.as_list().expect("pair");
                (pair[0].as_str().expect("male").to_string(), pair[1].as_str().expect("female").to_string())
            })
            .collect()
    }
    fn lines(&self, key: &str) -> Vec<Line> {
        self.value(key)
            .as_list()
            .expect("lines")
            .iter()
            .map(|line| {
                let line = line.as_list().expect("line");
                (line[0].as_str().expect("file").to_string(), line[1].as_int().expect("number"))
            })
            .collect()
    }
    /// Recorded files, written to a new temporary folder (below `prefix`).
    fn written(&self, key: &str, prefix: &str) -> TempDir {
        let folder = TempDir::new("cir-parity-");
        for (relative, data) in self.value(key).as_dict().expect("files").iter() {
            let data = match data {
                Value::Null => None,
                value => Some(self.fixture.blob(value)),
            };
            folder.write(&format!("{prefix}{relative}"), data.as_deref());
        }
        folder
    }
    fn blob_map(&self, key: &str) -> BTreeMap<String, Vec<u8>> {
        self.value(key)
            .as_dict()
            .expect("dict argument")
            .iter()
            .map(|(name, blob)| (name.to_string(), self.fixture.blob(blob)))
            .collect()
    }
    /// The reward routes' `game` and EET `names` keywords (absent: each
    /// campaign's own names).
    fn named(&self) -> Named {
        let names = self.values.get("names").filter(|names| !names.is_null());
        Named(
            self.values.get("game").and_then(Value::as_str).map(str::to_string),
            names.map(|names| EetNames::from_value(names).expect("EET names")),
        )
    }
}

/// A recorded [`Naming`], owned.
struct Named(Option<String>, Option<EetNames>);

impl Named {
    fn naming(&self) -> Naming<'_> {
        Naming { game: self.0.as_deref(), names: self.1.as_ref() }
    }
}

fn dump_dict(dict: &Dict) -> Value {
    Value::Dict(dict.clone())
}

fn text_refs_value() -> Value {
    Value::Dict(
        mode2_rewards::TEXT_REFS
            .iter()
            .map(|(resource, refs)| {
                (resource.to_string(), Value::Dict(refs.iter().map(|(old, tra)| (old.to_string(), Value::from(*tra))).collect()))
            })
            .collect(),
    )
}

/// `RewardCopies` over the recorded dialogues, as the generator records it:
/// what it found (maps in key order) and the patches of each list of routes.
fn reward_copies_value(args: &Args) -> Result<Value> {
    let files = args.blob_map("files");
    let scanned: HashMap<String, Value> =
        args.dict("scanned").iter().map(|(resource, hash)| (resource.to_string(), hash.clone())).collect();
    let limit = args.optional_int("limit").map_or(reward_copies::LIMIT, |limit| limit as usize);
    let named = args.named();
    let found =
        reward_copies::RewardCopies::with_limit(&mut |name| Ok(files.get(name).cloned()), &scanned, limit, named.naming())?;
    let replies = |rows: &mode2_rewards::Replies| -> Value {
        Value::Dict(
            rows.iter()
                .map(|(state, ordinals)| (state.to_string(), Value::from(ordinals.iter().map(|&ordinal| ordinal as i64).collect::<Vec<_>>())))
                .collect(),
        )
    };
    let copies: Dict = found
        .copies
        .iter()
        .map(|(key, dialogues)| (key.clone(), Value::Dict(dialogues.iter().map(|(resource, rows)| (resource.clone(), replies(rows))).collect())))
        .collect();
    let reasons: Dict = found.reasons.iter().map(|(key, reason)| (key.clone(), Value::from(reason.as_str()))).collect();
    let checked: Dict =
        found.visited.keys().map(|key| (key.clone(), Value::from(found.checked(key).into_iter().collect::<Vec<_>>()))).collect();
    let originals: Dict = found.originals.iter().map(|(resource, data)| (resource.clone(), bytes_value(data))).collect();
    let mut patches = Vec::new();
    for keys in args.value("routes").as_list().expect("routes") {
        let keys: Vec<String> = keys.as_list().expect("route list").iter().map(|key| key.as_str().expect("route").to_string()).collect();
        let patched: Dict = found.patches(&keys)?.iter().map(|(resource, data)| (resource.clone(), bytes_value(data))).collect();
        patches.push(Value::Dict(patched));
    }
    Ok(Value::Dict(cir_config::dict! {
        "copies" => copies, "reasons" => reasons, "checked" => checked,
        "changed" => Value::from(found.changed.iter().cloned().collect::<Vec<_>>()), "originals" => originals,
        "patches" => Value::List(patches),
    }))
}

/// Call the Rust port of one reference function with the recorded arguments.
fn call(name: &str, args: &Args) -> Result<Value> {
    Ok(match name {
        "quest_rewards.QUESTS" => dump_dict(quest_rewards::quests()),
        "quest_rewards.SOURCE_QUESTS" => dump_dict(quest_rewards::source_quests()),
        "quest_rewards.quest_spec" => Value::Dict(quest_rewards::quest_spec(&args.str("source_id"))?),
        "quest_rewards._newline" => bytes_value(quest_rewards::newline(&args.bytes("data"))?),
        "quest_rewards._blocks" => {
            let (blocks, prefix, suffix) =
                quest_rewards::blocks(&args.bytes("data"), args.int("expected") as usize, &args.bytes("newline"))?;
            Value::List(vec![Value::List(blocks.iter().map(|block| bytes_value(block)).collect()), bytes_value(&prefix), bytes_value(&suffix)])
        }
        "quest_rewards._global_action" => bytes_value(&quest_rewards::global_action(&args.str("name"), &args.bytes("newline"))?),
        "quest_rewards._global_guard" => {
            bytes_value(&quest_rewards::global_guard(&args.str("name"), args.bool("selected"), &args.bytes("newline"))?)
        }
        "quest_rewards._grant_action" => bytes_value(&quest_rewards::grant_action(
            &args.bytes("block"),
            &args.str("item"),
            args.int("opcode") as u32,
            &args.bytes("newline"),
        )?),
        "quest_options.tra_variants" => {
            listed_texts(&quest_options::tra_variants(&args.str("source"))?)
        }
        "quest_options.tra_texts" => Value::Dict(
            quest_options::tra_texts(&args.str("source"))?
                .into_iter()
                .map(|(number, text)| (number.to_string(), Value::from(text)))
                .collect(),
        ),
        "codepages.digests" => codepage_digests(),
        "translations.language" => match translations::language(&args.str("code")) {
            Some((name, folder, encoding)) => Value::from(vec![name, folder, encoding]),
            None => Value::Null,
        },
        "translations.charset" => Value::from(translations::charset(&args.str("encoding"))),
        "translations.missing"
        | "translations.lines"
        | "translations.option_lines"
        | "translations.texts"
        | "translations.sources" => {
            let folder = args.values.get("files").map(|_| args.written("files", "languages/"));
            let source = folder.as_ref().map_or(Translations::Embedded, |folder| Translations::Folder(folder.0.join("languages")));
            let code = args.str("code");
            match name {
                "translations.missing" => source.missing(&code, &args.lines("wanted"))?.map_or(Value::Null, Value::from),
                "translations.lines" => listed_lines(&source.lines(&code, &args.lines("wanted"))?),
                "translations.option_lines" => listed_lines(&source.option_lines(&code, &args.lines("wanted"))?),
                "translations.texts" => listed_texts(&source.texts(&code, &args.str("name"))?),
                _ => {
                    let folder = folder.as_ref().expect("sources of a folder");
                    Value::List(source.sources(&code)?.iter().map(|path| Value::from(folder.relative(path))).collect())
                }
            }
        }
        "game_text.GameText.append" => {
            let (text, refs) = args.game_text("text").append(&args.pairs("texts"))?;
            Value::List(vec![game_text_value(&text), strrefs(&refs)])
        }
        "game_text.GameText.string" => {
            let (male, female) = args.game_text("text").string(args.int("ref"))?;
            let text = |text: Option<String>| text.map_or(Value::Null, Value::from);
            Value::List(vec![text(male), text(female)])
        }
        "game_text.paired" => game_text_value(&game_text::paired(args.bytes("male"), args.optional_bytes("female"))?),
        "game_text._append_texts" => {
            let (data, refs) = game_text::append_texts(&args.bytes("data"), &args.strings("texts"))?;
            Value::List(vec![bytes_value(&data), strrefs(&refs)])
        }
        "game_text.text_files" => {
            let folder = args.written("files", "");
            let (male, female) = game_text::text_files(&folder.0, &args.str("language"))?;
            Value::List(vec![Value::from(folder.relative(&male)), female.map_or(Value::Null, |path| Value::from(folder.relative(&path)))])
        }
        "game_text.unpaired" => {
            let folder = args.written("files", "");
            let female = args.value("female").as_str().map(|name| folder.0.join(name));
            game_text::unpaired(&folder.0.join(args.str("male")), female.as_deref())?.map_or(Value::Null, Value::from)
        }
        "quest_options._texts" => {
            let texts = quest_options::texts(&args.str("path"));
            let mut keys: Vec<&i64> = texts.keys().collect();
            keys.sort();
            Value::Dict(keys.into_iter().map(|key| (key.to_string(), Value::from(texts[key].clone()))).collect())
        }
        "quest_options._Dialogue" => {
            let dialogue = quest_options::Dialogue::new(&args.bytes("data"), &args.str("name"))?;
            let rows = |rows: &[Vec<u8>]| Value::List(rows.iter().map(|row| bytes_value(row)).collect());
            Value::Dict(cir_config::dict! {
                "states" => rows(&dialogue.states), "transitions" => rows(&dialogue.transitions),
                "state_triggers" => rows(&dialogue.state_triggers), "triggers" => rows(&dialogue.triggers),
                "actions" => rows(&dialogue.actions), "render" => bytes_value(&dialogue.render()),
            })
        }
        "quest_options.original_dialogue" => bytes_value(&quest_options::original_dialogue(&args.str("name"), &args.bytes("data"))?),
        "quest_options.patch_hlskull" => {
            bytes_value(&quest_options::patch_hlskull(&args.bytes("data"), &args.refs("refs").expect("refs"))?)
        }
        "quest_options.patch_kangaxx_journal" => {
            bytes_value(&quest_options::patch_kangaxx_journal(&args.bytes("lua"), &args.bytes("patched_dialogue"))?)
        }
        "quest_options.original_crypt" => bytes_value(&quest_options::original_crypt(&args.str("name"), &args.bytes("data"))?),
        "quest_options.patch_crypt" => bytes_value(&quest_options::patch_crypt(
            &args.str("name"),
            &args.bytes("data"),
            &args.refs("refs").expect("refs"),
        )?),
        "quest_options.ward_item" => bytes_value(&quest_options::ward_item(&args.refs("refs").expect("refs"))?),
        "quest_options.kangaxx_problem" => {
            let hashes = args.dict("hashes");
            let files = args.blob_map("files");
            let mut requested = Vec::new();
            let problem = quest_options::kangaxx_problem(
                |resource| hashes.get(resource).and_then(Value::as_str).map(str::to_string),
                |wanted| {
                    requested.push(Value::from(wanted.iter().map(|resource| resource.to_string()).collect::<Vec<_>>()));
                    wanted
                        .iter()
                        .map(|resource| {
                            let data = files.get(*resource).ok_or_else(|| Error::key(resource))?;
                            Ok((resource.to_string(), data.clone()))
                        })
                        .collect()
                },
            )?;
            Value::Dict(cir_config::dict! {"problem" => problem.map_or(Value::Null, Value::from), "read" => Value::List(requested)})
        }
        "quest_options.prepare_quest_options" => {
            let (output, text) = quest_options::prepare_quest_options(
                &args.blob_map("resources"),
                &args.game_text("text"),
                args.bool("kangaxx"),
                &args.str("language"),
            )?;
            Value::List(vec![files(&output), game_text_value(&text)])
        }
        "spell_rewards.SPELL" => Value::Dict(spell_rewards::spell()),
        "spell_rewards.PAYLOAD" => spell_rewards::payload(),
        "spell_rewards.patch_key_spell" => bytes_value(&spell_rewards::patch_key_spell(&args.bytes("data"))?),
        "spell_rewards.original_key_spell" => bytes_value(&spell_rewards::original_key_spell(&args.bytes("data"))?),
        "mode2_rewards.REWARDS" => dump_dict(mode2_rewards::rewards()),
        "mode2_rewards.DIRECT_REWARDS" => Value::from(mode2_rewards::direct_rewards().to_vec()),
        "mode2_rewards.TRANSFER_SPECS" => dump_dict(mode2_rewards::transfer_specs()),
        "mode2_rewards.PHYSICAL_SOURCE_REWARDS" => Value::List(
            mode2_rewards::PHYSICAL_SOURCE_REWARDS
                .iter()
                .map(|(resource, kind, item, route)| Value::from(vec![*resource, *kind, *item, *route]))
                .collect(),
        ),
        "mode2_rewards.RESOURCE_REWARDS" => dump_dict(mode2_rewards::resource_rewards()),
        "mode2_rewards.SOURCE_REWARDS" => dump_dict(mode2_rewards::source_rewards()),
        "mode2_rewards.TARGET_REWARDS" => dump_dict(mode2_rewards::target_rewards()),
        "mode2_rewards.TEXT_REFS" => text_refs_value(),
        "mode2_rewards.ACTION_TEXT_REFS" => Value::Dict(
            mode2_rewards::ACTION_TEXT_REFS.iter().map(|(resource, refs)| (resource.to_string(), Value::from(refs.to_vec()))).collect(),
        ),
        "mode2_rewards.ADAPTER_ID" => Value::from(mode2_rewards::ADAPTER_ID),
        "mode2_rewards.reward" => Value::Dict(mode2_rewards::reward(&args.str("key"), args.named().naming())?),
        "mode2_rewards.source_spec" => {
            Value::Dict(mode2_rewards::source_spec(&args.str("source_id"), args.named().naming())?)
        }
        "mode2_rewards.target_spec" => {
            Value::Dict(mode2_rewards::target_spec(&args.str("target_id"), args.named().naming())?)
        }
        "mode2_rewards.transfer_spec" => Value::Dict(mode2_rewards::transfer_spec(&args.str("name"), args.named().naming())?),
        "mode2_rewards.resource_specs" => Value::List(
            mode2_rewards::resource_specs(&args.str("resource"), args.named().naming())?.into_iter().map(Value::Dict).collect(),
        ),
        "mode2_rewards._patch_dialogue" => {
            let specs: Vec<Dict> =
                args.value("specs").as_list().expect("specs").iter().map(|spec| spec.as_dict().expect("spec").clone()).collect();
            let mut reasons = Dict::new();
            let result = mode2_rewards::patch_dialogue(
                &args.bytes("data"),
                &specs,
                &args.ordered_refs("action_refs"),
                args.bool("defer_departure"),
                args.bool("numeric_target"),
                Some(&mut reasons),
            )?;
            Value::List(vec![bytes_value(&result), Value::Dict(reasons)])
        }
        "mode2_rewards._patch_script" => {
            bytes_value(&mode2_rewards::patch_script(&args.bytes("data"), &args.dict("spec"), args.optional_int("text_ref"))?)
        }
        "mode2_rewards._text_original" => {
            mode2_rewards::text_original(&args.bytes("data"), &args.str("resource"), args.named().naming())?
                .map_or(Value::Null, |(original, refs)| Value::List(vec![bytes_value(&original), int_keys(&refs)]))
        }
        "mode2_rewards._script_original" => {
            let specs: Vec<Dict> =
                args.value("specs").as_list().expect("specs").iter().map(|spec| spec.as_dict().expect("spec").clone()).collect();
            recovered(mode2_rewards::script_original(&args.bytes("data"), &specs)?)
        }
        "mode2_rewards._patch_destroys" => {
            let specs: Vec<Dict> =
                args.value("specs").as_list().expect("specs").iter().map(|spec| spec.as_dict().expect("spec").clone()).collect();
            let mut reasons = Dict::new();
            let result = mode2_rewards::patch_destroys(&args.bytes("data"), &specs, Some(&mut reasons))?;
            Value::List(vec![bytes_value(&result), Value::Dict(reasons)])
        }
        "mode2_rewards._handover" => bytes_value(&mode2_rewards::handover(&args.dict("spec"))?),
        "mode2_rewards.destroyed_items" => Value::List(
            mode2_rewards::destroyed_items(&args.str("resource"), &args.bytes("data"))?.into_iter().map(Value::Str).collect(),
        ),
        "mode2_rewards.unpatched_script" => bytes_value(&mode2_rewards::unpatched_script(
            &args.str("resource"),
            &args.bytes("data"),
            args.named().naming(),
        )?),
        "mode2_rewards.own_patch" => {
            Value::Bool(mode2_rewards::own_patch(&args.str("resource"), &args.bytes("data"), args.named().naming())?)
        }
        "mode2_rewards.superseding_routes" => match mode2_rewards::superseding_routes(
            &args.str("resource"),
            &args.bytes("newer"),
            &args.bytes("earlier"),
            args.named().naming(),
        )? {
            None => Value::Null,
            Some(added) => Value::List(added.into_iter().map(Value::Dict).collect()),
        },
        "mode2_rewards.route_reasons" => {
            Value::Dict(mode2_rewards::route_reasons(&args.str("resource"), &args.bytes("data"), args.named().naming())?)
        }
        "mode2_rewards.gives_reward" => Value::Bool(mode2_rewards::gives_reward(
            &args.str("resource"),
            &args.bytes("data"),
            &args.str("key"),
            args.named().naming(),
        )?),
        "mode2_rewards._patch_text_dialogue" => bytes_value(&mode2_rewards::patch_text_dialogue(
            &args.bytes("data"),
            &args.str("resource"),
            args.refs("refs").as_ref(),
            args.bool("defer_departure"),
            args.bool("numeric_target"),
            args.named().naming(),
        )?),
        "mode2_rewards.prepare_ddwell_text" => {
            let (text, reference) = mode2_rewards::prepare_ddwell_text(&args.game_text("text"), &args.str("language"))?;
            Value::List(vec![game_text_value(&text), Value::from(reference)])
        }
        "mode2_rewards.prepare_reward_texts" => {
            let named = args.named();
            let (text, refs) = mode2_rewards::prepare_reward_texts(
                &args.game_text("text"),
                args.strings("resources"),
                &args.str("language"),
                named.naming().game,
            )?;
            Value::List(vec![game_text_value(&text), nested_refs(&refs)])
        }
        "mode2_rewards.text_refs" => {
            let refs: BTreeMap<i64, i64> =
                mode2_rewards::game_text_refs(&args.str("resource"), args.named().naming().game)?.into_iter().collect();
            Value::Dict(refs.into_iter().map(|(old, tra)| (old.to_string(), Value::from(tra))).collect())
        }
        "mode2_rewards.journal_refs" => {
            Value::from(mode2_rewards::journal_refs(&args.str("resource"), args.named().naming().game)?)
        }
        "mode2_rewards._titled" => {
            Value::from(mode2_rewards::titled(args.value("original").as_str(), &args.str("body")))
        }
        "mode2_rewards.journal_lua" => {
            let texts: mode2_rewards::TextRefs = args
                .dict("texts")
                .iter()
                .map(|(resource, refs)| {
                    let refs = refs.as_dict().expect("refs");
                    let refs = refs.iter().map(|(old, new)| (old.parse().expect("int key"), new.as_int().expect("int ref")));
                    (resource.to_string(), refs.collect())
                })
                .collect();
            bytes_value(&mode2_rewards::journal_lua(&args.bytes("lua"), &texts, args.named().naming().game)?)
        }
        "mode2_rewards.text_lines" => lines_value(&mode2_rewards::text_lines(&args.str("resource"))?),
        "mode2_rewards.patch_reward" => bytes_value(&mode2_rewards::patch_reward(
            &args.str("resource"),
            &args.bytes("data"),
            args.refs("refs").as_ref(),
            args.optional_int("ddwell_ref"),
            args.named().naming(),
        )?),
        "container_rewards.TARGETS" => dump_dict(container_rewards::targets()),
        "container_rewards.RESOURCE_TARGETS" => dump_dict(container_rewards::resource_targets()),
        "container_rewards.TEXT_RESOURCES" => Value::from(container_rewards::TEXT_RESOURCES.to_vec()),
        "container_rewards.target_spec" => Value::Dict(container_rewards::target_spec(&args.str("target_id"))?),
        "container_rewards.treasure" => Value::from(container_rewards::treasure(&args.str("target_id"))?),
        "container_rewards.treasure_area" => {
            Value::from(container_rewards::treasure_area(&args.str("target_id"), &args.str("platform"))?)
        }
        "container_rewards.treasure_container" => Value::from(container_rewards::treasure_container(
            &args.str("target_id"),
            args.value("sources").as_list().expect("sources"),
            &args.str("platform"),
        )?),
        "container_rewards.container_error" => {
            Value::from(container_rewards::container_error(&args.str("target_id"), &args.str("problem"))?.message)
        }
        "container_rewards.own_patch" => Value::Bool(container_rewards::own_patch(&args.str("resource"), &args.bytes("data"))?),
        "container_rewards.route_reasons" => {
            Value::Dict(container_rewards::route_reasons(&args.str("resource"), &args.bytes("data"))?)
        }
        "container_rewards._recover" => recovered(container_rewards::recover(&args.str("resource"), &args.bytes("data"))?),
        "container_rewards.prepare_container_texts" => {
            let (text, refs) = container_rewards::prepare_container_texts(
                &args.game_text("text"),
                args.strings("resources"),
                &args.str("language"),
            )?;
            Value::List(vec![game_text_value(&text), nested_refs(&refs)])
        }
        "container_rewards._trigger" => bytes_value(&container_rewards::trigger(
            args.int("opcode") as u32,
            args.int("value"),
            args.int("negate"),
            &args.str("text"),
            args.int("selector"),
            &args.bytes("newline"),
        )),
        "container_rewards.patch_container_resource" => bytes_value(&container_rewards::patch_container_resource(
            &args.str("resource"),
            &args.bytes("data"),
            args.refs("refs").as_ref(),
        )?),
        "reward_copies.copy_spec" => {
            Value::Dict(reward_copies::copy_spec(&args.str("key"), &args.str("resource"), args.named().naming())?)
        }
        "reward_copies.RewardCopies" => reward_copies_value(args)?,
        "store_inventory.rewrite_inventory" => {
            let removals: BTreeSet<i64> =
                args.value("removals").as_list().expect("removals").iter().map(|index| index.as_int().expect("index")).collect();
            bytes_value(&store_inventory::rewrite_inventory(&args.bytes("data"), &removals)?)
        }
        "creature_inventory.rewrite_inventory" => {
            let edits: Vec<(i64, Option<String>)> = args
                .value("edits")
                .as_list()
                .expect("edits")
                .iter()
                .map(|edit| {
                    let pair = edit.as_list().expect("pair");
                    (pair[0].as_int().expect("index"), pair[1].as_str().map(str::to_string))
                })
                .collect();
            let borrowed: Vec<(i64, Option<&str>)> = edits.iter().map(|(index, value)| (*index, value.as_deref())).collect();
            bytes_value(&creature_inventory::rewrite_inventory(&args.bytes("data"), &borrowed)?)
        }
        "group_recipients.usable_all" => {
            let item = args.bytes("item");
            Value::List(
                args.value("creatures")
                    .as_list()
                    .expect("creatures")
                    .iter()
                    .map(|creature| Value::Bool(group_recipients::usable(&item, &args.fixture.blob(creature))))
                    .collect(),
            )
        }
        "group_recipients.preferred_members" => {
            Value::from(group_recipients::preferred_members(&args.bytes("item"), &args.blob_map("members")))
        }
        "original_options.tome_variants" => Value::from(original_options::tome_variants(&args.str("campaign"), &args.str("game"), None)?),
        "original_options.patch_spell_shield_stock" => {
            bytes_value(&original_options::patch_spell_shield_stock(&args.bytes("data"))?)
        }
        "original_options.is_spell_shield_row" => Value::Bool(original_options::is_spell_shield_row(args.value("row"))),
        "original_options.spell_shield_randomized" => Value::Bool(original_options::spell_shield_randomized(
            args.value("rows").as_list().expect("list argument"),
        )),
        "original_catalog.mode2_route_source" => {
            original_catalog::mode2_route_source(args.value("row"))?.map_or(Value::Null, Value::from)
        }
        "original_catalog.mode2_route_target" => {
            original_catalog::mode2_route_target(args.value("row"))?.map_or(Value::Null, Value::from)
        }
        "original_options.spell_shield_spec" => original_options::spell_shield_spec(args.value("world"), args.bool("randomized"))?,
        "original_options.validate_spell_shield_spec" => {
            original_options::validate_spell_shield_spec(args.value("spec"))?;
            Value::Null
        }
        "original_options.cursed_disguise_choices" => Value::List(original_options::cursed_disguise_choices(
            &args.strings("available"),
            &args.str("game"),
            args.bool("item_revisions"),
        )?),
        "original_options.patch_cursed_disguise" => bytes_value(&original_options::patch_cursed_disguise(
            &args.bytes("source"),
            &args.bytes("donor"),
            args.optional_int("description_strref"),
            args.optional_int("plate_name_strref"),
        )?),
        "cursed_disguises.item_revisions_installed" => {
            Value::Bool(cursed_disguises::item_revisions_installed(&args.bytes("weidu_log")))
        }
        "cursed_disguises.cursed_disguise_spec" => cursed_disguises::cursed_disguise_spec(
            &args.dict("item_hashes"),
            &args.str("game"),
            args.bool("item_revisions"),
        )?,
        "cursed_disguises.validate_cursed_disguise_spec" => {
            cursed_disguises::validate_cursed_disguise_spec(args.value("spec"))?;
            Value::Null
        }
        "cursed_disguises.cursed_disguise_indices" => {
            Value::Dict(cursed_disguises::cursed_disguise_indices(args.value("spec"), args.value("seed"))?)
        }
        "cursed_disguises.prepare_cursed_disguises" => {
            let (fixture, assets, text) = cursed_disguises::prepare_cursed_disguises(
                args.value("spec"),
                &args.blob_map("resources"),
                &args.game_text("text"),
                &args.str("language"),
            )?;
            Value::List(vec![fixture, files(&assets), game_text_value(&text)])
        }
        other => panic!("no Rust dispatch for {other}"),
    })
}

fn render(value: &Value) -> String {
    json::dumps(value, json::Style::compact_ascii(false)).unwrap_or_else(|_| "<non-finite>".into())
}

/// Run every case of a scenario; pin-dependent cases need parity-overrides.
/// Returns (run, skipped) counts.
pub fn run(scenario: &str) -> (usize, usize) {
    setup(scenario);
    compare(scenario, &load(scenario))
}

/// A fixture recorded without pins outside the repository
/// (`generate.py --game`).
pub fn run_path(path: &Path) -> (usize, usize) {
    compare(&path.display().to_string(), &load_path(path))
}

fn compare(scenario: &str, fixture: &Fixture) -> (usize, usize) {
    let overrides = cfg!(feature = "parity-overrides");
    let (mut ran, mut skipped) = (0, 0);
    let mut failures = Vec::new();
    for case in &fixture.cases {
        let name = case.get("name").and_then(Value::as_str).expect("case name");
        if !overrides && case.get("independent").and_then(Value::as_bool) != Some(true) {
            skipped += 1;
            continue;
        }
        ran += 1;
        let function = case.get("call").and_then(Value::as_str).expect("call");
        let args = Args { fixture, values: case.get("args").and_then(Value::as_dict).expect("args") };
        let outcome = call(function, &args);
        let expected = match (case.get("result"), case.get("error")) {
            (Some(result), _) => Ok(result.clone()),
            (None, Some(error)) => {
                let mut expected = Error::new(
                    match error.get("kind").and_then(Value::as_str).expect("kind") {
                        "Profile" => ErrorKind::Profile,
                        "Scan" => ErrorKind::Scan,
                        "Value" => ErrorKind::Value,
                        "Key" => ErrorKind::Key,
                        "Type" => ErrorKind::Type,
                        "GameText" => ErrorKind::GameText,
                        "Overflow" => ErrorKind::Overflow,
                        "Attribute" => ErrorKind::Attribute,
                        other => panic!("unknown error kind {other}"),
                    },
                    error.get("message").and_then(Value::as_str).expect("message"),
                );
                expected.subclass = error.get("subclass").and_then(Value::as_str).map(|name| match name {
                    "ChangedSinceScan" => CHANGED_SINCE_SCAN,
                    "ScriptError" => SCRIPT_ERROR,
                    "MissingTranslation" => MISSING_TRANSLATION,
                    other => panic!("unknown ProfileError subclass {other}"),
                });
                Err(expected)
            }
            _ => panic!("case {name} has no outcome"),
        };
        let matches = match (&outcome, &expected) {
            (Ok(actual), Ok(expected)) => render(actual) == render(expected),
            (Err(actual), Err(expected)) => actual == expected,
            _ => false,
        };
        if !matches {
            let show = |outcome: &Result<Value>| match outcome {
                Ok(value) => render(value),
                Err(error) => format!("{}Error{}: {}", kind_name(error.kind),
                    error.subclass.map_or(String::new(), |name| format!(" ({name})")), error.message),
            };
            failures.push(format!("{name} ({function})\n  rust:   {}\n  python: {}", show(&outcome), show(&expected)));
        }
    }
    assert!(failures.is_empty(), "{} of {ran} parity cases differ in {scenario}:\n{}", failures.len(), failures.join("\n"));
    assert!(ran > 0 || !overrides, "scenario {scenario} ran no cases");
    eprintln!("{scenario}: {ran} parity cases matched, {skipped} need parity-overrides");
    (ran, skipped)
}

/// Hex dump of a byte difference, for assertion messages.
pub fn first_difference(a: &[u8], b: &[u8]) -> String {
    let index = a.iter().zip(b).position(|(x, y)| x != y).unwrap_or(a.len().min(b.len()));
    format!("lengths {} and {}, first difference at {index}: {} vs {}", a.len(), b.len(),
        hex(&a[index.min(a.len())..(index + 16).min(a.len())]), hex(&b[index.min(b.len())..(index + 16).min(b.len())]))
}
