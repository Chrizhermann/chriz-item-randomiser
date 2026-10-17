//! Shared harness for the patcher parity tests: synthetic builders mirroring
//! the reference's tests, and the runner for the fixtures that
//! `configurator/parity/patchers/generate.py` records from the Python
//! implementation.
#![allow(dead_code)]

pub mod builders;
pub mod mode2;

use cir_config::json::{self, Dict, Value};
use cir_config::py::{from_hex, hex};
use cir_config::resources::sha256_hex;
use cir_config::{
    container_rewards, creature_inventory, cursed_disguises, group_recipients, mode2_rewards, original_options,
    quest_options, quest_rewards, spell_rewards, store_inventory, Error, ErrorKind, Result,
};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::Once;

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
    let path = fixtures().join(format!("{scenario}.json"));
    let data = std::fs::read(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
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
    fn blob_map(&self, key: &str) -> BTreeMap<String, Vec<u8>> {
        self.value(key)
            .as_dict()
            .expect("dict argument")
            .iter()
            .map(|(name, blob)| (name.to_string(), self.fixture.blob(blob)))
            .collect()
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
        "quest_options.prepare_quest_options" => {
            let (output, tlk) = quest_options::prepare_quest_options(
                &args.blob_map("resources"),
                &args.bytes("tlk"),
                args.bool("kangaxx"),
                &args.str("language"),
            )?;
            Value::List(vec![files(&output), bytes_value(&tlk)])
        }
        "spell_rewards.SPELL" => Value::Dict(spell_rewards::spell()),
        "spell_rewards.PAYLOAD" => spell_rewards::payload(),
        "spell_rewards.patch_key_spell" => bytes_value(&spell_rewards::patch_key_spell(&args.bytes("data"))?),
        "spell_rewards.original_key_spell" => bytes_value(&spell_rewards::original_key_spell(&args.bytes("data"))?),
        "mode2_rewards.REWARDS" => dump_dict(mode2_rewards::rewards()),
        "mode2_rewards.DIRECT_REWARDS" => Value::from(mode2_rewards::direct_rewards().to_vec()),
        "mode2_rewards.TRANSFER_SPECS" => dump_dict(mode2_rewards::transfer_specs()),
        "mode2_rewards.PHYSICAL_SOURCE_REWARDS" => dump_dict(mode2_rewards::physical_source_rewards()),
        "mode2_rewards.RESOURCE_REWARDS" => dump_dict(mode2_rewards::resource_rewards()),
        "mode2_rewards.SOURCE_REWARDS" => dump_dict(mode2_rewards::source_rewards()),
        "mode2_rewards.TARGET_REWARDS" => dump_dict(mode2_rewards::target_rewards()),
        "mode2_rewards.TEXT_REFS" => text_refs_value(),
        "mode2_rewards.ACTION_TEXT_REFS" => Value::Dict(
            mode2_rewards::ACTION_TEXT_REFS.iter().map(|(resource, refs)| (resource.to_string(), Value::from(refs.to_vec()))).collect(),
        ),
        "mode2_rewards.ADAPTER_ID" => Value::from(mode2_rewards::ADAPTER_ID),
        "mode2_rewards.source_spec" => Value::Dict(mode2_rewards::source_spec(&args.str("source_id"))?),
        "mode2_rewards.target_spec" => Value::Dict(mode2_rewards::target_spec(&args.str("target_id"))?),
        "mode2_rewards.transfer_spec" => Value::Dict(mode2_rewards::transfer_spec(&args.str("name"))?),
        "mode2_rewards.resource_specs" => {
            Value::List(mode2_rewards::resource_specs(&args.str("resource"))?.into_iter().map(Value::Dict).collect())
        }
        "mode2_rewards._dialogue_grant" => {
            bytes_value(&mode2_rewards::dialogue_grant(&args.bytes("actions"), &args.dict("spec"))?)
        }
        "mode2_rewards._patch_dialogue" => {
            let specs: Vec<Dict> =
                args.value("specs").as_list().expect("specs").iter().map(|spec| spec.as_dict().expect("spec").clone()).collect();
            bytes_value(&mode2_rewards::patch_dialogue(
                &args.bytes("data"),
                &specs,
                &args.ordered_refs("action_refs"),
                args.bool("defer_departure"),
                args.bool("numeric_target"),
            )?)
        }
        "mode2_rewards._patch_text_dialogue" => bytes_value(&mode2_rewards::patch_text_dialogue(
            &args.bytes("data"),
            &args.str("resource"),
            args.refs("refs").as_ref(),
            args.bool("defer_departure"),
            args.bool("numeric_target"),
        )?),
        "mode2_rewards._original_text_dialogue" => {
            let (original, refs) = mode2_rewards::original_text_dialogue(&args.bytes("data"), &args.str("resource"))?;
            Value::List(vec![bytes_value(&original), refs.as_ref().map_or(Value::Null, int_keys)])
        }
        "mode2_rewards._original_ddwell" => {
            let (original, reference) = mode2_rewards::original_ddwell(&args.bytes("data"))?;
            Value::List(vec![bytes_value(&original), Value::from(reference)])
        }
        "mode2_rewards.prepare_ddwell_text" => {
            let (tlk, reference) = mode2_rewards::prepare_ddwell_text(&args.bytes("tlk"), &args.str("language"))?;
            Value::List(vec![bytes_value(&tlk), Value::from(reference)])
        }
        "mode2_rewards.prepare_reward_texts" => {
            let (tlk, refs) = mode2_rewards::prepare_reward_texts(&args.bytes("tlk"), args.strings("resources"), &args.str("language"))?;
            Value::List(vec![bytes_value(&tlk), nested_refs(&refs)])
        }
        "mode2_rewards.original_reward" => {
            mode2_rewards::original_reward(&args.str("resource"), &args.bytes("data"))?.map_or(Value::Null, |data| bytes_value(&data))
        }
        "mode2_rewards.patch_reward" => bytes_value(&mode2_rewards::patch_reward(
            &args.str("resource"),
            &args.bytes("data"),
            args.refs("refs").as_ref(),
            args.optional_int("ddwell_ref"),
        )?),
        "container_rewards.TARGETS" => dump_dict(container_rewards::targets()),
        "container_rewards.RESOURCE_TARGETS" => dump_dict(container_rewards::resource_targets()),
        "container_rewards.target_spec" => Value::Dict(container_rewards::target_spec(&args.str("target_id"))?),
        "container_rewards.prepare_container_texts" => {
            let (tlk, refs) =
                container_rewards::prepare_container_texts(&args.bytes("tlk"), args.strings("resources"), &args.str("language"))?;
            Value::List(vec![bytes_value(&tlk), nested_refs(&refs)])
        }
        "container_rewards._trigger" => bytes_value(&container_rewards::trigger(
            args.int("opcode") as u32,
            args.int("value"),
            args.int("negate"),
            &args.str("text"),
            args.int("selector"),
            &args.bytes("newline"),
        )),
        "container_rewards.original_container_resource" => {
            bytes_value(&container_rewards::original_container_resource(&args.str("resource"), &args.bytes("data"))?)
        }
        "container_rewards.patch_container_resource" => bytes_value(&container_rewards::patch_container_resource(
            &args.str("resource"),
            &args.bytes("data"),
            args.refs("refs").as_ref(),
        )?),
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
        "original_options.tome_variants" => Value::from(original_options::tome_variants(&args.str("campaign"), &args.str("game"))?),
        "original_options.patch_spell_shield_stock" => {
            bytes_value(&original_options::patch_spell_shield_stock(&args.bytes("data"))?)
        }
        "original_options.is_spell_shield_row" => Value::Bool(original_options::is_spell_shield_row(args.value("row"))),
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
            let (fixture, assets, tlk) = cursed_disguises::prepare_cursed_disguises(
                args.value("spec"),
                &args.blob_map("resources"),
                &args.bytes("tlk"),
                &args.str("language"),
            )?;
            Value::List(vec![fixture, files(&assets), bytes_value(&tlk)])
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
    let fixture = load(scenario);
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
        let args = Args { fixture: &fixture, values: case.get("args").and_then(Value::as_dict).expect("args") };
        let outcome = call(function, &args);
        let expected = match (case.get("result"), case.get("error")) {
            (Some(result), _) => Ok(result.clone()),
            (None, Some(error)) => Err(Error::new(
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
            )),
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
                Err(error) => format!("{}Error: {}", kind_name(error.kind), error.message),
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
