//! Campaign-gated original Mode 2 direct grants and plain dialogue aliases
//! (the reference's `mode2_rewards.py`).
//!
//! The resource hashes pin the reviewed EET scripts. Only the grant is
//! suppressed; its surrounding actions, conditions, dialogue replies and
//! journal fields stay intact. The reward event captures the original
//! recipient at the grant position. See lib/weidu_action.tpa's generic BCS/DLG
//! handlers and DDWELL special case.
//!
//! The reviewed hashes are read through [`crate::pins`] as
//! `mode2_rewards.ORIGINAL_HASHES` and `mode2_rewards.PATCHED_HASHES`.

use crate::error::{Error, ErrorKind, Result};
use crate::game_text::append_texts;
use crate::json::{Dict, Value};
use crate::py::{
    count_bytes, digits_value, find_bytes, is_line_end, is_line_start, put_u32, replace_bytes, split_bytes, split3,
    starts_with_ignore_ascii_case, str_value, u32_le,
};
use crate::quest_options::{pad, texts, Dialogue};
use crate::quest_rewards::{blocks, global_action, global_guard, grant_action, newline};
use crate::resources::sha256_hex;
use crate::{dict, list};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::OnceLock;

pub const ADAPTER_ID: &str = "mode2-reward-v1";

pub const ORIGINAL_HASHES: &[(&str, &str)] = &[
    ("uhogre01", "8706469468f7277542ba0e6d7fcf44b47a15d36c3879f505464e0a119be00b97"),
    ("asysta01", "f0f1773357f2280b7b6a05695ff278650110dc60898e2c7ddc1241867782fadd"),
    ("ddwell", "4d2232892ce30f7e55333f696bb2717e5208e3d588e0ef77e990fc0077955544"),
    ("mgkhol01", "d4762221de29275f29a3516980cf2b6360612a1670ad5733f3e599a1ba72e3ea"),
    ("ppstat01", "7022962710da01f3d66088d8f7c5aa2a7963d7e994ff0d55f61dc5b64c9673ae"),
    ("riftg03", "4bb8a0866e3470ae558c108160ebef18c33de9f14ad3181ccf33ebd0d5e4912a"),
    ("trhmer01", "e5651fc5ef72780fe2ce9bb6ec8c3632665a5279d41cf79244fbef393304752e"),
    ("udsvir03", "5918919ea366eceb27f4096f025cde79c44310a9e57bcd49d123fdbb48352994"),
    ("ammayor", "d37409df94285e4bdce88bdfdb38382790dca567dc6304744244d9d5504030d6"),
    ("bhoisig", "5a55df4f2a0b814027735467fbf69017d9a13b0cd749d8566cf4f57c1f932377"),
    ("bhnalla", "038818ff7d8e4fc3a62e10530b74764657e26b3f44203ebd1877db7535d17ab3"),
    ("bharval", "ef4e704dc616f3658410cb798bb48b323b49b3b419986ff0345eb81979fb2730"),
    ("hendak", "0f5a4956e7ee05cb5d6a02e2d61bcf8be0ab575ecd921af31656578330b4a804"),
    ("lehtin", "a067828ca2055d4c3fd406c43672d9da3547e92b3da67a5eb84b341fdbebd2d1"),
    ("udsilver", "48f697db47942b74b4530975a5a4a46cac1a0cc850453b2b9d50ab173b01090c"),
    ("uddemon", "582fbcfecf3e71363dff1aa9bcef4dbc202d28c3af6e66e6a29840b342f1bdfd"),
    ("gortan1", "27a8575386e30a36a1f00f63c2004a095f301b511cd310eb03e36606265f3f3c"),
    ("gorbat1", "c1da7c158296ed5e553942a735ebc84773007b099a4eadb1cc78a50142a0088a"),
    ("gorlum2", "8228c02ac6dee8ac4652ad550a0fef83a8ddf2d59400fcb7c1e025f6395b5e9c"),
    ("aran", "da4bebc9ceb58fad2d34cf261b73d458ece55ad70128c656ede9e44a8631cee1"),
    ("ppdjinn", "06c1671065b549921263594db378d7610223cb16b70b7f718b3885b45b32af38"),
    ("uhmay01", "4213656595fdde6afeefee881597ddd62814ba886747bc4d60cfca25a2d852be"),
    ("gorcamb", "76cdb11e99cdbd676d7af08f48713fceb01061ea6180e762e6be1c997f3b1de4"),
    ("sctelwyn", "d5dd048c43e16ba937ff583eef8f5966e549fb0c4e3f92679883d35fcdda1759"),
    ("scsain", "c2a43b0d67eca9b531aa591463f74d5333225a95f0be87f8d03ed1cf91f8dafe"),
    ("scyarryl", "f96880bdabcfe8393d9ad6df502d3d001e2d79913736e480f6cc717f012dd367"),
    ("trgeni01", "4220763c55e065bfb2f500aa9d615a1a1e112ae3fc60eee061a3d9f3f59f7549"),
    ("loutcapt", "1bb3dc1337c087cc049f0d4fa431f4a7f909dbfacabf4dd30087d8b4f52f2d98"),
    ("amcler01", "5bac12a183463f6faa912b15e80c45c321cd2189f318c5056cf38b2150a6d79e"),
    ("udlesa", "6c518483372ebe4073986dc2d41e4f7ae73a7bce133aab4289a29ef1be7c7e50"),
];

/// DDWELL includes a caller-allocated TLK reference, verified by
/// reconstruction, so it has no fixed patched hash.
pub const PATCHED_HASHES: &[(&str, &str)] = &[
    ("uhogre01", "722213d1f53966780ab8bba2e2dbe400e64c14ced6da2ff9f48b43749b629e25"),
    ("asysta01", "26bf2a67793e7b05fe35781f85ec4885aac806520927c35deba9aed5a2f5b841"),
    ("mgkhol01", "3a47fea6c2cdb1405267342bad65ecc58741481006153dd3a15b43c3807decfd"),
    ("ppstat01", "69505c964ee036a6d9b974407e34cbd4ff35f4cebd10373819c5ef2f5d50f73a"),
    ("riftg03", "34e7c5e57d9e69394b40ce09f4c5cd5a5e6249d871b676c82bafbd3d707f3ff5"),
    ("trhmer01", "21aac581490c963ecf778f2ffc760bb3c56cce5be72fd637f783e70eccdbdd75"),
    ("udsvir03", "2003095671c146bb3d06b92fcc17df06dc18e5be4ab59a0e5d17bfd6db412e66"),
    ("ammayor", "d71af3922a33954154f7f2745df7466647465ad3920b6a04548b6a9df73a2e05"),
    ("bhoisig", "43ca1ecf37968c0737109cb06b8c9fbb522a2becbe51a89662ef1d14df3c52f7"),
    ("bhnalla", "69fa93487cb7f0f4aa9883733e5d8a784155dbb726e06d88c595f591dfd763d5"),
    ("bharval", "d1ca99803daf9f609ea13f438eb75fe65ffb87d0f417b483a4873ece6006f31a"),
    ("hendak", "b19099dbd5a42cbb653b1a7abba30aeda7dbdc5697ba0e95106fe39ba7b1b7c4"),
    ("lehtin", "980b634e457adec11ba48405c65dbc5f9c6d032eac0d51bfcf8703d1eb6c44bf"),
    ("udsilver", "593cdf5a0313db7065fb2c1eaafc8efc309485d3b5a654c24b585f397e94917b"),
    ("uddemon", "b1976c5f2f0b948ec00f972348b5ef208b0ba127bbc5678297b133747836a042"),
    ("gortan1", "0a70f5274d7eacfd4c0c1941266c7bd9be3e5332026f24116c3cadfa6c441ede"),
    ("gorbat1", "39d7e944e7d9efb983bb34013e7be3a9ab7cca2825d18010f16a22da60443c27"),
    ("gorlum2", "28a2a669d4388c155abfcece1857611260628717e38c3bb49b1af245ce1e4ca5"),
    ("aran", "26551e099ce0936a8e0da8e128ae41546650d058685091849cc6406a882d50a7"),
    ("loutcapt", "9a7db245aa8f59ff1743421ed4621b96391521f48e8f6eeafb0d44070a1ddd25"),
];

/// Original generic descriptions from the matching special WeiDU handlers.
/// Keys are installed EET strrefs, values are source TRA entries.
pub const TEXT_REFS: &[(&str, &[(i64, i64)])] = &[
    ("bcs:ddwell", &[(40850, 399)]),
    ("dlg:ppdjinn", &[(48995, 99)]),
    (
        "dlg:uhmay01",
        &[(32062, 1394), (32063, 1449), (32176, 1395), (32177, 1396), (32185, 1397), (32207, 1398), (49560, 1399), (32191, 1393)],
    ),
    ("dlg:gorcamb", &[(63681, 497), (64609, 497), (63683, 498), (64611, 498), (63719, 499)]),
    ("dlg:sctelwyn", &[(29325, 111)]),
    ("dlg:scsain", &[(28489, 116)]),
    ("dlg:scyarryl", &[(29378, 117)]),
    ("dlg:trgeni01", &[(43514, 110)]),
    ("dlg:amcler01", &[(74025, 100)]),
    ("dlg:udlesa", &[(52664, 104)]),
];
pub const ACTION_TEXT_REFS: &[(&str, &[i64])] = &[("dlg:amcler01", &[74025])];
const TEXT_TRAILER: &[u8] = b"FLPRM2T1";

/// `ORIGINAL_HASHES` with any test overrides.
pub fn original_hashes() -> Vec<(String, String)> {
    crate::pins::table("mode2_rewards.ORIGINAL_HASHES", ORIGINAL_HASHES)
}

/// `PATCHED_HASHES` with any test overrides.
pub fn patched_hashes() -> Vec<(String, String)> {
    crate::pins::table("mode2_rewards.PATCHED_HASHES", PATCHED_HASHES)
}

/// `ORIGINAL_HASHES[name]` (KeyError when absent).
pub fn original_hash(name: &str) -> Result<String> {
    crate::pins::get("mode2_rewards.ORIGINAL_HASHES", ORIGINAL_HASHES, name).ok_or_else(|| Error::key(name))
}

/// `PATCHED_HASHES.get(name)`.
pub fn patched_hash(name: &str) -> Option<String> {
    crate::pins::get("mode2_rewards.PATCHED_HASHES", PATCHED_HASHES, name)
}

/// `TEXT_REFS.get(resource)`.
pub fn text_refs(resource: &str) -> Option<&'static [(i64, i64)]> {
    TEXT_REFS.iter().find(|(key, _)| *key == resource).map(|(_, refs)| *refs)
}

fn action_text_refs(resource: &str) -> &'static [i64] {
    ACTION_TEXT_REFS.iter().find(|(key, _)| *key == resource).map_or(&[], |(_, refs)| *refs)
}

// ---------------------------------------------------------------- registry

fn pairs(rows: &[(i64, i64)]) -> Value {
    Value::List(rows.iter().map(|&(state, ordinal)| list![state, ordinal]).collect())
}

#[allow(clippy::too_many_arguments)]
fn spec(
    name: &str,
    extension: &str,
    item: &str,
    source: i64,
    target: Option<i64>,
    recipient: &str,
    charges: [i64; 3],
    branches: Value,
) -> Dict {
    let suffix = format!("M2_{source}");
    let target = target.map_or_else(|| "None".to_string(), |target| target.to_string());
    let recipient_local =
        if extension == "bcs" && name == "asysta01" { Value::Null } else { Value::from(format!("FLPR_R{suffix}")) };
    dict! {
        "quest" => name,
        "resource" => format!("{extension}:{name}"),
        "item" => item,
        "source_ids" => list![format!("original-source:bg2:mode2:item:{source}")],
        "target_ids" => list![format!("bg2:mode2:target:{target}")],
        "selected_global" => format!("FLPR_Q{suffix}"),
        "consumed_global" => format!("FLPR_X{suffix}"),
        "event_global" => format!("FLPR_E{suffix}"),
        "recipient_local" => recipient_local,
        "recipient" => recipient,
        "branches" => branches,
        "payload" => dict! {"resref" => item, "expiration" => 0, "charges" => list![charges[0], charges[1], charges[2]], "flags" => 0},
    }
}

#[allow(clippy::too_many_arguments)]
fn alias(
    rewards: &mut Dict,
    key: &str,
    item: &str,
    source: i64,
    target: Option<i64>,
    alias: &str,
    dialogues: &[(&str, Vec<(i64, i64)>)],
    charges: [i64; 3],
    recipient: &str,
) {
    let (primary, branches) = &dialogues[0];
    let mut row = spec(primary, "dlg", item, source, target, recipient, charges, pairs(branches));
    row.insert("resources", Value::List(dialogues.iter().map(|(name, _)| Value::from(format!("dlg:{name}"))).collect()));
    row.insert("dialogues", dialogues.iter().map(|(name, rows)| (name.to_string(), pairs(rows))).collect::<Dict>());
    row.insert("event_global", format!("FLPR_EA_{alias}"));
    row.insert("recipient_local", format!("FLPR_RA_{alias}"));
    if target.is_none() {
        row.insert("target_ids", Value::List(Vec::new()));
    }
    rewards.insert(key, row);
}

fn row_mut<'a>(rewards: &'a mut Dict, key: &str) -> &'a mut Dict {
    rewards.get_mut(key).and_then(Value::as_dict_mut).expect("registered reward")
}

fn source_record(offset: i64, index: i64, resref: &str, slot: i64) -> Dict {
    dict! {
        "byte_offset" => offset, "record_index" => index, "resref" => resref,
        "expiration" => 0, "charges" => list![0, 0, 0], "flags" => 0, "slots" => list![slot],
    }
}

struct Registry {
    rewards: Dict,
    direct: Vec<String>,
    transfers: Dict,
    physical: Dict,
    resources: Dict,
    sources: Dict,
    targets: Dict,
}

fn registry() -> &'static Registry {
    static REGISTRY: OnceLock<Registry> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        let mut registry = build_registry();
        // Test-only replacements of single entries (the reference's tests use
        // `patch.dict`); the derived indexes keep the reviewed entries' keys.
        for (key, value) in crate::pins::table_entries("mode2_rewards.REWARDS") {
            registry.rewards.insert(key, value);
        }
        for (key, value) in crate::pins::table_entries("mode2_rewards.TRANSFER_SPECS") {
            registry.transfers.insert(key, value);
        }
        registry
    })
}

fn build_registry() -> Registry {
    const NO_CHARGES: [i64; 3] = [0, 0, 0];
    let mut rewards = Dict::new();
    let direct: [(&str, &str, &str, i64, i64, &str, [i64; 3], Value); 9] = [
        ("uhogre01", "dlg", "shld26", 14, 17, "LastTalkedToBy", NO_CHARGES, pairs(&[(22, 0), (22, 1)])),
        ("asysta01", "bcs", "helm19", 5, 21, "are:ar1512/container/0", NO_CHARGES, list![0]),
        ("ddwell", "bcs", "helm21", 27, 27, "LastTrigger", NO_CHARGES, list![1]),
        ("mgkhol01", "dlg", "belt02", 33, 33, "LastTalkedToBy", NO_CHARGES, pairs(&[(4, 0)])),
        ("ppstat01", "dlg", "ring31", 28, 35, "LastTalkedToBy", [1, 1, 1], pairs(&[(5, 0)])),
        ("riftg03", "dlg", "shld27", 38, 40, "Player1", NO_CHARGES, pairs(&[(30, 0)])),
        ("trhmer01", "dlg", "shld25", 52, 44, "LastTalkedToBy", NO_CHARGES, pairs(&[(12, 0), (37, 0)])),
        ("udsvir03", "dlg", "blun18", 50, 65, "LastTalkedToBy", [1, 1, 1], pairs(&[(35, 0), (35, 1)])),
        ("ammayor", "dlg", "compon16", 83, 84, "LastTalkedToBy", [1, 0, 0], pairs(&[(12, 0)])),
    ];
    let direct_names = direct.iter().map(|row| row.0.to_string()).collect();
    for (name, extension, item, source, target, recipient, charges, branches) in direct {
        rewards.insert(name, spec(name, extension, item, source, Some(target), recipient, charges, branches));
    }
    let talker = "LastTalkedToBy";
    alias(
        &mut rewards,
        "bhoisig",
        "blun20",
        12,
        Some(48),
        "TEMPLEMESS",
        &[("bhoisig", vec![(33, 0)]), ("bhnalla", vec![(59, 0)]), ("bharval", vec![(62, 0)])],
        NO_CHARGES,
        talker,
    );
    alias(
        &mut rewards,
        "hendak",
        "sw1h03",
        30,
        Some(18),
        "COPPER",
        &[("hendak", vec![(32, 0)]), ("lehtin", vec![(23, 0), (24, 0)])],
        NO_CHARGES,
        talker,
    );
    alias(&mut rewards, "udsilver", "xbow10", 44, Some(66), "EGGDEALIO", &[("udsilver", vec![(41, 0)])], [1, 1, 1], talker);
    alias(&mut rewards, "uddemon", "halb06", 65, None, "EGGDEALIO", &[("uddemon", vec![(8, 2)])], [0, 3, 0], "Player1");
    for name in ["udsilver", "uddemon"] {
        row_mut(&mut rewards, name).insert("resources", list!["dlg:udsilver", "dlg:uddemon"]);
    }
    alias(
        &mut rewards,
        "gortan1",
        "helm29",
        80,
        Some(87),
        "FIENDS",
        &[("gortan1", vec![(12, 0)]), ("gorbat1", vec![(13, 0)])],
        NO_CHARGES,
        talker,
    );
    alias(&mut rewards, "gorlum2", "blun28", 77, Some(78), "GORLUM", &[("gorlum2", vec![(16, 0)])], NO_CHARGES, "Player1");
    let aran: Vec<(i64, i64)> = (0..8).map(|ordinal| (4, ordinal)).collect();
    alias(&mut rewards, "aran", "ring07", 47, Some(24), "ARAN1", &[("aran", aran.clone())], NO_CHARGES, talker);
    alias(&mut rewards, "aran2", "amul21", 56, Some(57), "ARAN2", &[("aran", aran)], NO_CHARGES, talker);
    alias(&mut rewards, "ppdjinn", "plat12", 24, Some(30), "PPDJINN", &[("ppdjinn", vec![(0, 0)])], NO_CHARGES, talker);
    alias(&mut rewards, "uhmay01", "leat14", 25, Some(46), "UHMAY01", &[("uhmay01", vec![(30, 0), (30, 1)])], NO_CHARGES, "[PC]");
    alias(&mut rewards, "gorcamb", "sw1h68", 71, Some(72), "GORCAMB", &[("gorcamb", vec![(56, 0)])], [0, 1, 0], talker);
    alias(&mut rewards, "sctelwyn", "helm03", 22, Some(22), "SCULPTOR", &[("sctelwyn", vec![(19, 0)])], NO_CHARGES, talker);
    alias(&mut rewards, "scsain", "amul23", 36, None, "SCULPTOR", &[("scsain", vec![(19, 0)])], [1, 1, 1], "[PC]");
    alias(&mut rewards, "scyarryl", "blun20", 12, None, "SCULPTOR", &[("scyarryl", vec![(19, 0)])], NO_CHARGES, talker);
    // Item Randomiser's lists/duplicates/bg2.2da:26 attaches this inventory
    // copy to BHOISIG source ident w8, and its lib/delete.tpa removes it only
    // when that source is selected. The sculptor target independently
    // converts its GiveItem into a created reward. This is an auxiliary
    // guarded copy, never another randomized source unit.
    {
        let row = row_mut(&mut rewards, "scyarryl");
        row.insert("authored_source_ids", list!["original-source:bg2:mode2:item:12"]);
        row.insert("source_ids", Value::List(Vec::new()));
        row.insert("source_resource", "cre:scyarryl");
        row.insert("source_resource_sha256", "15e6723046533520d542f00cb14fdc5c04d74d19b327424f212542b722f0d462");
        row.insert("physical_source_ids", list!["cre:scyarryl/item/0"]);
        row.insert("grant_kind", "transfer");
        row.insert("source_record", source_record(1308, 0, "blun20", 9));
    }
    let mut transfer_names = vec!["scyarryl"];
    for sculptor in ["sctelwyn", "scsain", "scyarryl"] {
        let row = row_mut(&mut rewards, sculptor);
        row.insert("resources", list!["dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl"]);
        row.insert("required_transfers", list!["scyarryl"]);
    }
    // Source selection alone removes its duplicate, even with no sculptor target.
    row_mut(&mut rewards, "bhoisig").insert("required_transfers", list!["scyarryl"]);

    // These rewards must remain carried by the NPC so theft and combat routes
    // still work. Their dialogue transfers the carried assignment through a
    // blocking Lua callback; it never creates a second item for the dialogue
    // recipient.
    let carried: [(&str, &str, i64, i64, &str, &str, Vec<(i64, i64)>, i64, i64, i64, &str); 3] = [
        (
            "trgeni01",
            "sw1h23",
            32,
            32,
            "ar2014",
            "trgeni01",
            vec![(35, 0), (40, 0)],
            0,
            1152,
            9,
            "cd31a342ec3e836a0e3cd2d85c183bd6b85c753c83a6083dcc4c2f21a0aa9c02",
        ),
        (
            "loutcapt",
            "dagg11",
            39,
            42,
            "ar0514",
            "Dennis",
            vec![(26, 0), (30, 0)],
            6,
            1116,
            11,
            "33bb5267dd0bb7b120e7e9b20c05e37b0cd8c73134e7ba642dbb8b075713e430",
        ),
        (
            "amcler01",
            "ring43",
            84,
            85,
            "ar5500",
            "amcler01",
            vec![(7, 0), (8, 0)],
            0,
            1272,
            4,
            "c8cdf695c04d6eb9cce075bff7fd4e2c528c480a360ffbac280ef5ec6b14d4fc",
        ),
    ];
    for (name, item, source, target, area, script, branches, record, offset, slot, hash) in carried {
        alias(&mut rewards, name, item, source, Some(target), &name.to_uppercase(), &[(name, branches)], NO_CHARGES, talker);
        let row = row_mut(&mut rewards, name);
        let source_ids = row.get("source_ids").cloned().expect("source ids");
        row.insert("grant_kind", "transfer");
        row.insert("authored_source_ids", source_ids);
        row.insert(
            "inventory_target",
            dict! {"area" => area, "creature_resource" => format!("cre:{name}"), "script_name" => script},
        );
        row.insert("source_resource", format!("cre:{name}"));
        row.insert("source_resource_sha256", hash);
        row.insert("physical_source_ids", list![format!("cre:{name}/item/{record}")]);
        row.insert("required_transfers", list![name]);
        row.insert("source_record", source_record(offset, record, item, slot));
        transfer_names.push(name);
    }
    // Item Randomiser's lists/duplicates/bg2.2da:20 replaces the selected
    // source's carried sword with BLUN05; keep that ordinary fallback separate
    // from its randomized reward.
    row_mut(&mut rewards, "trgeni01").insert("replacement", "blun05");

    // Unlike the dialogue-source duplicates above, row42 is already a physical
    // CRE source. Keep its existing primary guard/replacement and only link
    // its selected flag to the dialogue. Do not register an additional
    // scripted source or copy.
    let duel: Vec<(i64, i64)> = (6..12).map(|state| (state, 0)).collect();
    alias(&mut rewards, "udlesa", "sw1h39", 42, Some(45), "UDLESA", &[("udlesa", duel)], NO_CHARGES, talker);
    {
        let row = row_mut(&mut rewards, "udlesa");
        row.insert("source_ids", Value::List(Vec::new()));
        row.insert("authored_source_ids", list!["original-source:bg2:mode2:item:42"]);
        row.insert("grant_kind", "transfer");
        row.insert(
            "inventory_target",
            dict! {"area" => "ar2202", "creature_resource" => "cre:udlesa", "script_name" => "udlesa"},
        );
        row.insert("source_resource", "cre:udlesa");
        row.insert("source_resource_sha256", "f51eaafa2f7c4df7b0af9c76fac2576a243929c31b57a965a326fb58e2b6b8c4");
        row.insert("physical_source_ids", list!["cre:udlesa/item/2"]);
        row.insert("replacement", "dwsw1h02");
        row.insert("source_record", source_record(3676, 2, "sw1h39", 9));
    }
    let physical = dict! {"cre:udlesa/item/2" => "udlesa"};
    for row in rewards.values_mut() {
        let row = row.as_dict_mut().expect("reward row");
        let resource = row.get("resource").cloned().expect("resource");
        row.setdefault("resources", list![resource]);
    }
    let mut resources = Dict::new();
    for (name, row) in rewards.iter() {
        let resource = row.get("resource").and_then(Value::as_str).expect("resource");
        let (kind, own) = resource.split_once(':').expect("kind:name");
        let names: Vec<String> = match row.get("dialogues").and_then(Value::as_dict) {
            Some(dialogues) => dialogues.keys().map(str::to_string).collect(),
            None => vec![own.to_string()],
        };
        for dialogue in names {
            let entry = resources.setdefault(&format!("{kind}:{dialogue}"), Value::List(Vec::new()));
            entry.as_list_mut().expect("names").push(Value::from(name));
        }
    }
    let mut sources = Dict::new();
    let mut targets = Dict::new();
    for (name, row) in rewards.iter() {
        for (key, index) in [("source_ids", &mut sources), ("target_ids", &mut targets)] {
            for identity in row.get(key).and_then(Value::as_list).expect("identities") {
                index.insert(identity.as_str().expect("identity"), name);
            }
        }
    }
    let transfers = transfer_names
        .into_iter()
        .map(|name| (name.to_string(), rewards.get(name).cloned().expect("transfer row")))
        .collect();
    Registry { rewards, direct: direct_names, transfers, physical, resources, sources, targets }
}

/// `REWARDS`: every Mode 2 grant edit by key, in the reference's order.
pub fn rewards() -> &'static Dict {
    &registry().rewards
}

/// `DIRECT_REWARDS`.
pub fn direct_rewards() -> &'static [String] {
    &registry().direct
}

/// `TRANSFER_SPECS`: inventory copies guarded alongside a scripted source.
pub fn transfer_specs() -> &'static Dict {
    &registry().transfers
}

/// `PHYSICAL_SOURCE_REWARDS`.
pub fn physical_source_rewards() -> &'static Dict {
    &registry().physical
}

/// `RESOURCE_REWARDS`: resource to the reward keys that edit it.
pub fn resource_rewards() -> &'static Dict {
    &registry().resources
}

/// `SOURCE_REWARDS`: source id to reward key.
pub fn source_rewards() -> &'static Dict {
    &registry().sources
}

/// `TARGET_REWARDS`: target id to reward key.
pub fn target_rewards() -> &'static Dict {
    &registry().targets
}

fn reward(key: &str) -> Dict {
    rewards().get(key).and_then(Value::as_dict).expect("registered reward").clone()
}

pub fn source_spec(source_id: &str) -> Result<Dict> {
    match source_rewards().get(source_id).and_then(Value::as_str) {
        Some(key) => Ok(reward(key)),
        None => Err(Error::profile(format!("Unsupported Mode 2 scripted source: {source_id}"))),
    }
}

pub fn target_spec(target_id: &str) -> Result<Dict> {
    match target_rewards().get(target_id).and_then(Value::as_str) {
        Some(key) => Ok(reward(key)),
        None => Err(Error::profile(format!("Unsupported Mode 2 scripted target: {target_id}"))),
    }
}

/// Inventory copies which must be guarded alongside a scripted source.
pub fn transfer_spec(name: &str) -> Result<Dict> {
    match transfer_specs().get(name).and_then(Value::as_dict) {
        Some(row) => Ok(row.clone()),
        None => Err(Error::profile(format!("Unsupported Mode 2 inventory transfer: {name}"))),
    }
}

/// All grant edits for a resource; includes both independent ARAN rewards.
pub fn resource_specs(resource: &str) -> Result<Vec<Dict>> {
    let resource = crate::py::lower(resource);
    let Some(keys) = resource_rewards().get(&resource).and_then(Value::as_list) else {
        return Err(Error::profile(format!("Unsupported Mode 2 reward resource: {resource}")));
    };
    let name = resource.split(':').nth(1).expect("kind:name").to_string();
    let mut result = Vec::new();
    for key in keys {
        let mut spec = reward(key.as_str().expect("reward key"));
        spec.insert("resource", resource.as_str());
        spec.insert("quest", name.as_str());
        let branches = spec
            .get("dialogues")
            .and_then(Value::as_dict)
            .and_then(|dialogues| dialogues.get(&name))
            .or_else(|| spec.get("branches"))
            .cloned()
            .expect("branches");
        spec.insert("branches", branches);
        result.push(spec);
    }
    Ok(result)
}

// ---------------------------------------------------------------- dialogue

fn field<'a>(spec: &'a Dict, key: &str) -> Result<&'a Value> {
    spec.get(key).ok_or_else(|| Error::key(key))
}

fn text(spec: &Dict, key: &str) -> Result<String> {
    field(spec, key).map(str_value)
}

fn index_error() -> Error {
    Error::value("list index out of range")
}

/// `len(value)` with Python's TypeError for unsized values.
fn py_len(value: &Value) -> Result<usize> {
    match value {
        Value::List(items) => Ok(items.len()),
        Value::Dict(dict) => Ok(dict.len()),
        Value::Str(text) => Ok(text.chars().count()),
        other => Err(Error::type_error(format!("object of type '{}' has no len()", other.type_name()))),
    }
}

/// `value[0]` for a value whose `len()` is one.
fn first_item(value: &Value) -> Result<Value> {
    match value {
        Value::List(items) => items.first().cloned().ok_or_else(index_error),
        Value::Str(text) => text.chars().next().map(|c| Value::Str(c.to_string())).ok_or_else(|| Error::value("string index out of range")),
        Value::Dict(dict) => dict.get("0").cloned().ok_or_else(|| Error::new(ErrorKind::Key, "0")),
        other => Err(Error::type_error(format!("'{}' object is not subscriptable", other.type_name()))),
    }
}

/// `$` of a `re.M` pattern after `\r?` or `\r\r?` (the grant lookahead
/// `(?=\r?$|\r\r?$)`): up to two carriage returns, then a line end.
fn grant_line_end(data: &[u8], at: usize) -> bool {
    is_line_end(data, at)
        || (data.get(at) == Some(&b'\r') && is_line_end(data, at + 1))
        || (data.get(at) == Some(&b'\r') && data.get(at + 1) == Some(&b'\r') && is_line_end(data, at + 2))
}

/// Match one reviewed top-level create/transfer action, usable by aliases
/// too (`_dialogue_grant`). Hand-coded `re.finditer(rb"(?im)^(?:" + alternatives
/// + rb")(?=\r?$|\r\r?$)", actions)`; the matched bytes keep their own case.
pub fn dialogue_grant(actions: &[u8], spec: &Dict) -> Result<Vec<u8>> {
    let payload = field(spec, "payload")?;
    let charges = match payload {
        Value::Dict(payload) => payload.get("charges").ok_or_else(|| Error::key("charges"))?,
        other => return Err(Error::type_error(format!("{} indices must be integers or slices, not str", other.type_name()))),
    };
    let charges = crate::py::iter_value(charges)?
        .iter()
        .map(str_value)
        .collect::<Vec<_>>()
        .join(",");
    let create = format!("GiveItemCreate(\"{}\",{},{charges})", text(spec, "item")?, text(spec, "recipient")?);
    let transfer = format!("GiveItem(\"{}\",{})", text(spec, "item")?, text(spec, "recipient")?);
    let alternatives: Vec<&[u8]> = if spec.get("grant_kind").is_some_and(|kind| kind.py_eq(&Value::from("transfer"))) {
        vec![transfer.as_bytes()]
    } else {
        vec![create.as_bytes(), transfer.as_bytes()]
    };
    let mut matches = Vec::new();
    let mut position = 0;
    while position <= actions.len() {
        let matched = is_line_start(actions, position)
            .then(|| {
                alternatives.iter().find_map(|alternative| {
                    (starts_with_ignore_ascii_case(actions, position, alternative)
                        && grant_line_end(actions, position + alternative.len()))
                    .then_some(position + alternative.len())
                })
            })
            .flatten();
        match matched {
            Some(end) => {
                matches.push(actions[position..end].to_vec());
                position = end;
            }
            None => position += 1,
        }
    }
    if matches.len() != 1 {
        return Err(Error::profile("Mode 2 dialogue reward action changed"));
    }
    Ok(matches.remove(0))
}

/// Python's `set()` of hashable values, compared with `==`.
#[derive(Clone, Debug, Default)]
struct ValueSet(Vec<Value>);

impl ValueSet {
    fn contains(&self, value: &Value) -> bool {
        self.0.iter().any(|item| item.py_eq(value))
    }
    fn add(&mut self, value: Value) {
        if !self.contains(&value) {
            self.0.push(value);
        }
    }
    fn same(&self, other: &ValueSet) -> bool {
        self.0.len() == other.0.len() && self.0.iter().all(|item| other.contains(item))
    }
}

fn value_set(value: &Value) -> Result<ValueSet> {
    let mut set = ValueSet::default();
    for item in crate::py::iter_value(value)? {
        if item.is_dict() {
            return Err(Error::type_error("unhashable type: 'dict'"));
        }
        set.add(item);
    }
    Ok(set)
}

/// A dictionary keyed by Python values (`{key: ...}` with `==` lookup).
struct ValueMap(Vec<(Value, ValueSet)>);

impl ValueMap {
    fn position(&self, key: &Value) -> Option<usize> {
        self.0.iter().position(|(existing, _)| existing.py_eq(key))
    }
    fn insert(&mut self, key: Value, set: ValueSet) {
        match self.position(&key) {
            Some(index) => self.0[index].1 = set,
            None => self.0.push((key, set)),
        }
    }
    fn get(&self, key: &Value) -> &ValueSet {
        &self.0[self.position(key).expect("known key")].1
    }
    fn get_mut(&mut self, key: &Value) -> &mut ValueSet {
        let index = self.position(key).expect("known key");
        &mut self.0[index].1
    }
}

/// `re.fullmatch(r"bg2:mode2:target:([1-9][0-9]*)", target)` group 1.
fn numeric_target_row(target: &str) -> Option<&str> {
    let row = target.strip_prefix("bg2:mode2:target:")?;
    let bytes = row.as_bytes();
    (!bytes.is_empty() && (b'1'..=b'9').contains(&bytes[0]) && bytes[1..].iter().all(u8::is_ascii_digit)).then_some(row)
}

fn guard_line(selected: bool, flag: &str) -> Vec<u8> {
    format!("{}Global(\"{flag}\",\"GLOBAL\",1)\r\n", if selected { "" } else { "!" }).into_bytes()
}

/// `re.subn(rb"(?m)^DestroySelf\(\)(?=\r*$)", departure, actions)`.
fn replace_departures(actions: &[u8], departure: &[u8]) -> (Vec<u8>, usize) {
    const BARE: &[u8] = b"DestroySelf()";
    let mut out = Vec::with_capacity(actions.len());
    let mut replaced = 0;
    let mut position = 0;
    let mut copied = 0;
    while position <= actions.len() {
        let matched = is_line_start(actions, position) && actions[position..].starts_with(BARE) && {
            let mut at = position + BARE.len();
            while actions.get(at) == Some(&b'\r') {
                at += 1;
            }
            is_line_end(actions, at)
        };
        if matched {
            out.extend_from_slice(&actions[copied..position]);
            out.extend_from_slice(departure);
            replaced += 1;
            position += BARE.len();
            copied = position;
        } else {
            position += 1;
        }
    }
    out.extend_from_slice(&actions[copied..]);
    (out, replaced)
}

fn window(rows: &[Vec<u8>], first: usize, count: usize) -> Vec<Vec<u8>> {
    let end = first.saturating_add(count).min(rows.len());
    rows[first.min(end)..end].to_vec()
}

/// Split each reviewed reward transition by campaign flags (`_patch_dialogue`).
///
/// `action_refs` maps original DisplayStringHead strrefs to allocated ones
/// for selected branches (empty for none).
pub fn patch_dialogue(
    data: &[u8],
    specs: &[Dict],
    action_refs: &[(i64, i64)],
    defer_departure: bool,
    numeric_target: bool,
) -> Result<Vec<u8>> {
    let first_spec = specs.first().ok_or_else(index_error)?;
    let resource = text(first_spec, "resource")?;
    let name = resource.split(':').nth(1).ok_or_else(index_error)?.to_string();
    let mut dialogue = Dialogue::new(data, &name)?;
    let mut expected = ValueMap(Vec::new());
    for spec in specs {
        expected.insert(field(spec, "selected_global")?.clone(), value_set(field(spec, "branches")?)?);
    }
    let mut found = ValueMap(expected.0.iter().map(|(key, _)| (key.clone(), ValueSet::default())).collect());
    for state_index in 0..dialogue.states.len() {
        let state = dialogue.states[state_index].clone();
        let (first, count) = (u32_le(&state, 4) as usize, u32_le(&state, 8) as usize);
        let mut rows = Vec::new();
        for (ordinal, original) in window(&dialogue.transitions, first, count).into_iter().enumerate() {
            let flags = u32_le(&original, 0);
            let trigger_id = u32_le(&original, 12) as usize;
            let action_id = u32_le(&original, 16) as usize;
            let position = list![state_index, ordinal];
            let mut relevant = Vec::new();
            for spec in specs {
                if expected.get(field(spec, "selected_global")?).contains(&position) {
                    relevant.push(spec);
                }
            }
            if relevant.is_empty() {
                rows.push(original);
                continue;
            }
            if flags & 4 == 0 {
                return Err(Error::profile("Mode 2 dialogue reward transition lost its actions"));
            }
            let prior = if flags & 2 != 0 { dialogue.triggers.get(trigger_id).ok_or_else(index_error)?.clone() } else { Vec::new() };
            let actions = dialogue.actions.get(action_id).ok_or_else(index_error)?.clone();
            let n = relevant.len();
            for choice in 0..(1usize << n) {
                let mut branch = original.clone();
                let mut condition = prior.clone();
                let mut changed = actions.clone();
                for (index, spec) in relevant.iter().enumerate() {
                    let selected = choice >> (n - 1 - index) & 1 == 1;
                    condition.extend(guard_line(selected, &text(spec, "selected_global")?));
                    let grant = dialogue_grant(&actions, spec)?;
                    let replacement = if selected {
                        format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "consumed_global")?).into_bytes()
                    } else {
                        grant.clone()
                    };
                    // Keep this beside its grant, before EscapeArea/DestroySelf.
                    if spec.get("inventory_target").is_some_and(Value::truthy) {
                        let target = field(spec, "target_ids")?;
                        let first_target = if py_len(target)? == 1 { Some(first_item(target)?) } else { None };
                        let row = match &first_target {
                            Some(Value::Str(target)) => numeric_target_row(target),
                            Some(other) => {
                                return Err(Error::type_error(format!(
                                    "expected string or bytes-like object, got '{}'",
                                    other.type_name()
                                )))
                            }
                            None => None,
                        };
                        let Some(row) = row else {
                            return Err(Error::profile("Mode 2 inventory transfer target changed"));
                        };
                        // The dialogue parser strips spaces and colons even
                        // inside Lua strings. Parenthesize return; pass the row.
                        let callback = if numeric_target {
                            format!("EEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,{row}))\")")
                        } else {
                            format!(
                                "EEex_LuaAction(\"return FLPR_Transfer(EEex_LuaAction_Object,[[original-target:{}]])\")",
                                str_value(first_target.as_ref().expect("target"))
                            )
                        };
                        let with = [replacement.as_slice(), b"\r\n", callback.as_bytes()].concat();
                        changed = replace_bytes(&changed, &grant, &with, Some(1));
                    } else {
                        let mark = format!(
                            "ActionOverride({},SetGlobal(\"{}\",\"LOCALS\",1))\r\n",
                            text(spec, "recipient")?,
                            text(spec, "recipient_local")?
                        );
                        let event = format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "event_global")?);
                        let with = [mark.as_bytes(), &replacement, b"\r\n", event.as_bytes()].concat();
                        changed = replace_bytes(&changed, &grant, &with, Some(1));
                    }
                    if selected {
                        for (old, new) in action_refs {
                            let display = format!("DisplayStringHead(Myself,{old})").into_bytes();
                            if count_bytes(&changed, &display) != 1 {
                                return Err(Error::profile("Mode 2 original reward display action changed"));
                            }
                            changed = replace_bytes(&changed, &display, format!("DisplayStringHead(Myself,{new})").as_bytes(), Some(1));
                        }
                    }
                }
                dialogue.triggers.push(condition);
                dialogue.actions.push(changed);
                put_u32(&mut branch, 0, flags | 2);
                put_u32(&mut branch, 12, dialogue.triggers.len() as u32 - 1);
                put_u32(&mut branch, 16, dialogue.actions.len() as u32 - 1);
                rows.push(branch);
            }
            for spec in &relevant {
                found.get_mut(field(spec, "selected_global")?).add(position.clone());
            }
        }
        if rows.len() != count {
            let length = dialogue.transitions.len() as u32;
            put_u32(&mut dialogue.states[state_index], 4, length);
            put_u32(&mut dialogue.states[state_index], 8, rows.len() as u32);
            dialogue.transitions.extend(rows);
        }
    }
    if !expected.0.iter().all(|(key, set)| found.get(key).same(set)) {
        return Err(Error::profile("Mode 2 dialogue reward branches changed"));
    }
    if field(first_spec, "resource")?.py_eq(&Value::from("dlg:trgeni01")) && defer_departure {
        // DestroySelf is instant during dialogue; LuaAction is queued. Keep the
        // speaker alive until its earlier blocking reward transfer has run.
        // ActionOverride departures belong to the other genies and stay intact.
        let departure: &[u8] =
            b"EEex_LuaAction(\"EEex_Action_QueueResponseStringOnAIBase([[DestroySelf()]],EEex_LuaAction_Object)\")";
        for index in [37usize, 38] {
            if index >= dialogue.states.len() {
                return Err(Error::profile("TRGENI01 departure state changed"));
            }
            let (first, count) = (u32_le(&dialogue.states[index], 4) as usize, u32_le(&dialogue.states[index], 8));
            if count != 1 {
                return Err(Error::profile("TRGENI01 departure transition changed"));
            }
            let mut branch = dialogue.transitions.get(first).ok_or_else(index_error)?.clone();
            let flags = u32_le(&branch, 0);
            let action_id = u32_le(&branch, 16) as usize;
            if flags & 4 == 0 {
                return Err(Error::profile("TRGENI01 departure actions changed"));
            }
            let (actions, replaced) = replace_departures(dialogue.actions.get(action_id).ok_or_else(index_error)?, departure);
            if replaced != 1 {
                return Err(Error::profile("TRGENI01 bare departure changed"));
            }
            put_u32(&mut branch, 16, dialogue.actions.len() as u32);
            dialogue.actions.push(actions);
            let length = dialogue.transitions.len() as u32;
            put_u32(&mut dialogue.states[index], 4, length);
            dialogue.transitions.push(branch);
        }
    }
    Ok(dialogue.render())
}

/// Text-route variant of a reviewed dialogue (`_patch_text_dialogue`).
pub fn patch_text_dialogue(
    data: &[u8],
    resource: &str,
    refs: Option<&BTreeMap<i64, i64>>,
    defer_departure: bool,
    numeric_target: bool,
) -> Result<Vec<u8>> {
    let allocated = || Error::profile("Mode 2 dialogue requires allocated original reward text");
    let Some(refs) = refs else { return Err(allocated()) };
    let expected_refs = text_refs(resource).ok_or_else(|| Error::key(resource))?;
    let expected_keys: BTreeSet<i64> = expected_refs.iter().map(|(old, _)| *old).collect();
    if refs.keys().copied().collect::<BTreeSet<i64>>() != expected_keys
        || refs.values().any(|value| !(0..0xFFFF_FFFF).contains(value))
    {
        return Err(allocated());
    }
    let specs = resource_specs(resource)?;
    if specs.len() != 1 {
        return Err(Error::profile("Mode 2 text route needs one reviewed source flag"));
    }
    let flag = text(&specs[0], "selected_global")?;
    let action_refs: Vec<(i64, i64)> = action_text_refs(resource).iter().map(|old| (*old, refs[old])).collect();
    let name = resource.split(':').nth(1).ok_or_else(index_error)?;
    let mut dlg = Dialogue::new(&patch_dialogue(data, &specs, &action_refs, defer_departure, numeric_target)?, name)?;
    let original_count = dlg.states.len();
    let mut clones: HashMap<u32, u32> = HashMap::new();
    let mut found: BTreeSet<i64> = action_refs.iter().map(|(old, _)| *old).collect();
    let lookup = |value: u32| refs.get(&(value as i64)).map_or(value, |new| *new as u32);
    for index in 0..original_count {
        let state = dlg.states[index].clone();
        let (text_ref, first, count, entry) =
            (u32_le(&state, 0), u32_le(&state, 4) as usize, u32_le(&state, 8) as usize, u32_le(&state, 12));
        let transitions = window(&dlg.transitions, first, count);
        let mut affected: BTreeSet<i64> = transitions
            .iter()
            .filter(|row| u32_le(row, 0) & 1 != 0)
            .map(|row| u32_le(row, 4) as i64)
            .collect();
        affected.insert(text_ref as i64);
        affected.retain(|value| refs.contains_key(value));
        if affected.is_empty() {
            continue;
        }
        found.extend(affected);
        let mut clone = state.clone();
        put_u32(&mut clone, 0, lookup(text_ref));
        let mut rows = transitions;
        for row in rows.iter_mut() {
            if u32_le(row, 0) & 1 != 0 {
                let reply = u32_le(row, 4);
                put_u32(row, 4, lookup(reply));
            }
        }
        put_u32(&mut clone, 4, dlg.transitions.len() as u32);
        put_u32(&mut clone, 8, rows.len() as u32);
        dlg.transitions.extend(rows);
        if entry != 0xFFFF_FFFF {
            let prior = dlg.state_triggers.get(entry as usize).ok_or_else(index_error)?.clone();
            for selected in [false, true] {
                dlg.state_triggers.push([prior.as_slice(), &guard_line(selected, &flag)].concat());
                let position = dlg.state_triggers.len() as u32 - 1;
                if selected {
                    put_u32(&mut clone, 12, position);
                } else {
                    put_u32(&mut dlg.states[index], 12, position);
                }
            }
        }
        clones.insert(index as u32, dlg.states.len() as u32);
        dlg.states.push(clone);
    }
    if found != refs.keys().copied().collect::<BTreeSet<i64>>() {
        return Err(Error::profile("Mode 2 original reward text locations changed"));
    }
    // Redirect each incoming internal edge, including paths starting at an
    // unchanged external entry (the temple messengers enter sculptor27/28).
    // Cached cross-dialogue links never enter a changed text state directly.
    let own_name = dlg.name.to_ascii_lowercase();
    for index in 0..dlg.states.len() {
        let (first, count) = (u32_le(&dlg.states[index], 4) as usize, u32_le(&dlg.states[index], 8) as usize);
        let mut rows = Vec::new();
        for transition in window(&dlg.transitions, first, count) {
            let flags = u32_le(&transition, 0);
            let trigger = u32_le(&transition, 12) as usize;
            let destination = u32_le(&transition, 28);
            let link = &transition[20..28];
            let link_end = link.iter().rposition(|&byte| byte != 0).map_or(0, |last| last + 1);
            let internal = link[..link_end].to_ascii_lowercase() == own_name;
            if flags & 8 != 0 || !internal || !clones.contains_key(&destination) {
                rows.push(transition);
                continue;
            }
            let prior = if flags & 2 != 0 { dlg.triggers.get(trigger).ok_or_else(index_error)?.clone() } else { Vec::new() };
            for selected in [false, true] {
                let mut row = transition.clone();
                dlg.triggers.push([prior.as_slice(), &guard_line(selected, &flag)].concat());
                put_u32(&mut row, 0, flags | 2);
                put_u32(&mut row, 12, dlg.triggers.len() as u32 - 1);
                if selected {
                    put_u32(&mut row, 28, clones[&destination]);
                }
                rows.push(row);
            }
        }
        if rows.len() != count {
            let length = dlg.transitions.len() as u32;
            put_u32(&mut dlg.states[index], 4, length);
            put_u32(&mut dlg.states[index], 8, rows.len() as u32);
            dlg.transitions.extend(rows);
        }
    }
    let mut trailer = Vec::new();
    trailer.extend_from_slice(&(data.len() as u32).to_le_bytes());
    trailer.extend(pad(&data[..data.len().min(52)], 52));
    trailer.extend_from_slice(&(refs.len() as u32).to_le_bytes());
    for (&old, &new) in refs {
        trailer.extend_from_slice(&(old as u32).to_le_bytes());
        trailer.extend_from_slice(&(new as u32).to_le_bytes());
    }
    let mut out = dlg.render();
    out.extend_from_slice(&trailer);
    out.extend_from_slice(TEXT_TRAILER);
    out.extend_from_slice(&(trailer.len() as u32).to_le_bytes());
    Ok(out)
}

/// Recover a text dialogue's original and allocated references
/// (`_original_text_dialogue`); None references for pristine bytes.
pub fn original_text_dialogue(data: &[u8], resource: &str) -> Result<(Vec<u8>, Option<BTreeMap<i64, i64>>)> {
    let name = resource.split(':').nth(1).ok_or_else(index_error)?;
    let expected = original_hash(name)?;
    if sha256_hex(data) == expected {
        return Ok((data.to_vec(), None));
    }
    if data.len() < 12 || &data[data.len() - 12..data.len() - 4] != TEXT_TRAILER {
        return Err(Error::profile("Mode 2 text dialogue changed"));
    }
    let entries = text_refs(resource).ok_or_else(|| Error::key(resource))?.len();
    let length = u32_le(data, data.len() - 4) as usize;
    if length != 60 + entries * 8 || length > data.len() - 12 {
        return Err(Error::profile("Mode 2 text dialogue recovery bounds changed"));
    }
    let trailer = &data[data.len() - 12 - length..data.len() - 12];
    let (size, header, count) = (u32_le(trailer, 0) as usize, &trailer[4..56], u32_le(trailer, 56) as usize);
    if size < 52 || size > data.len() - length - 12 || count != entries {
        return Err(Error::profile("Mode 2 text dialogue original bounds changed"));
    }
    let original = [header, &data[52..size]].concat();
    let mut refs = BTreeMap::new();
    for index in 0..count {
        refs.insert(u32_le(trailer, 60 + index * 8) as i64, u32_le(trailer, 64 + index * 8) as i64);
    }
    if sha256_hex(&original) != expected {
        return Err(Error::profile("Mode 2 text dialogue original changed"));
    }
    if patch_text_dialogue(&original, resource, Some(&refs), true, true)? != data {
        // Accept only the exact prior inventory callback patches on a rescan.
        // TRGENI had exports both before and after its departure-order fix.
        let departures: &[bool] = if resource == "dlg:trgeni01" { &[true, false] } else { &[true] };
        let inventory = resource_specs(resource)?[0].get("inventory_target").is_some_and(Value::truthy);
        let mut accepted = false;
        if inventory {
            for &departure in departures {
                if patch_text_dialogue(&original, resource, Some(&refs), departure, false)? == data {
                    accepted = true;
                    break;
                }
            }
        }
        if !accepted {
            return Err(Error::profile("Mode 2 text dialogue patch changed"));
        }
    }
    Ok((original, Some(refs)))
}

// ---------------------------------------------------------------- scripts

fn marker_action(grant: &[u8], spec: &Dict, newline: &[u8]) -> Result<Vec<u8>> {
    // In compiled ActionOverride the action's first OB is the actor override.
    // Copy GiveItemCreate's second OB verbatim, retaining every object selector.
    let separator = [b"OB", newline].concat();
    let objects = split_bytes(grant, &separator);
    if objects.len() != 7 {
        return Err(Error::profile("Mode 2 BCS reward object layout changed"));
    }
    let empty: &[u8] = b"0 0 0 0 0 0 0 0 0 0 0 0 \"\"";
    if objects[1] != empty {
        return Err(Error::profile("Mode 2 BCS reward already has an actor override"));
    }
    let local = match field(spec, "recipient_local")? {
        Value::Str(name) => name.clone(),
        other => return Err(Error::type_error(format!("object of type '{}' has no len()", other.type_name()))),
    };
    let marker = global_action(&local, newline)?;
    let marker = replace_bytes(&marker, b"\"GLOBAL", b"\"LOCALS", Some(1));
    Ok(replace_bytes(&marker, &[empty, &separator].concat(), &[objects[3], &separator].concat(), Some(1)))
}

fn patch_script(data: &[u8], spec: &Dict, ddwell_ref: Option<i64>) -> Result<Vec<u8>> {
    let newline = newline(data)?;
    let container = field(spec, "quest")?.py_eq(&Value::from("asysta01"));
    let (mut blocks, prefix, suffix) = blocks(data, if container { 1 } else { 3 }, newline)?;
    let index = match first_item(field(spec, "branches")?)? {
        Value::Int(index) if index >= 0 => index as usize,
        _ => return Err(index_error()),
    };
    let block = blocks.get(index).ok_or_else(index_error)?.clone();
    let grant = grant_action(&block, if container { "Helm19" } else { "helm21" }, if container { 82 } else { 140 }, newline)?;
    let mark = if container { Vec::new() } else { marker_action(&grant, spec, newline)? };
    let event = global_action(&text(spec, "event_global")?, newline)?;
    let boundary = [b"CO", newline].concat();
    let (head, conditions, response) = split3(&block, &boundary)?;
    let mut variants = Vec::new();
    for selected in [false, true] {
        let replacement = if selected { global_action(&text(spec, "consumed_global")?, newline)? } else { grant.clone() };
        let mut changed = replace_bytes(&response, &grant, &[mark.as_slice(), &replacement, &event].concat(), Some(1));
        if selected && !container {
            let old = [b"40850 0 0 0 0\"\" \"\" AC" as &[u8], newline].concat();
            let reference = ddwell_ref.filter(|value| (0..0xFFFF_FFFF).contains(value));
            let Some(reference) = reference.filter(|_| count_bytes(&changed, &old) == 1) else {
                return Err(Error::profile("DDWELL requires an allocated original @399 TLK reference"));
            };
            changed = replace_bytes(&changed, &old, &[reference.to_string().as_bytes(), &old[5..]].concat(), Some(1));
        }
        variants.extend([
            head.as_slice(),
            &boundary,
            &conditions,
            &global_guard(&text(spec, "selected_global")?, selected, newline)?,
            &boundary,
            &changed,
        ]
        .concat());
    }
    blocks[index] = variants;
    Ok([prefix, blocks.concat(), suffix].concat())
}

/// Append the original generic well description; never replace shared text.
pub fn prepare_ddwell_text(tlk: &[u8], language: &str) -> Result<(Vec<u8>, i64)> {
    let (updated, refs) = prepare_reward_texts(tlk, ["bcs:ddwell"], language)?;
    Ok((updated, refs["bcs:ddwell"][&40850]))
}

/// Allocate all requested original TRA descriptions in one append/dedup pass.
pub fn prepare_reward_texts<I, S>(tlk: &[u8], resources: I, language: &str) -> Result<(Vec<u8>, BTreeMap<String, BTreeMap<i64, i64>>)>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    if crate::py::lower(language) != "en_us" {
        return Err(Error::profile("Mode 2 reward text currently requires the original English translation"));
    }
    let requested: BTreeSet<String> = resources.into_iter().map(|resource| crate::py::lower(resource.as_ref())).collect();
    let needed: Vec<&str> = requested.iter().map(String::as_str).filter(|resource| text_refs(resource).is_some()).collect();
    let mut source_texts: HashMap<&str, HashMap<i64, String>> = HashMap::new();
    let mut entries = Vec::new();
    let mut values = Vec::new();
    for &resource in &needed {
        let path = if resource == "dlg:ppdjinn" { "languages/english/ppdjinn.tra" } else { "languages/english/in-game.tra" };
        let file = source_texts.entry(path).or_insert_with(|| texts(path));
        for &(old, tra) in text_refs(resource).expect("text resource") {
            entries.push((resource, old));
            values.push(file.get(&tra).cloned().ok_or_else(|| Error::new(ErrorKind::Key, tra.to_string()))?);
        }
    }
    let (updated, refs) = append_texts(tlk, &values)?;
    let mut result: BTreeMap<String, BTreeMap<i64, i64>> =
        needed.iter().map(|resource| (resource.to_string(), BTreeMap::new())).collect();
    for ((resource, old), reference) in entries.into_iter().zip(refs) {
        result.get_mut(resource).expect("needed").insert(old, reference as i64);
    }
    Ok((updated, result))
}

/// `re.findall(rb'151OB' + nl + rb'.*?' + nl + rb'([0-9]+) 0 0 0 0"" "" AC' +
/// nl, block, re.DOTALL)`: the lazy gap takes the first complete tail.
fn ddwell_display_refs(block: &[u8], newline: &[u8]) -> Vec<i64> {
    let head = [b"151OB", newline].concat();
    let tail = [b" 0 0 0 0\"\" \"\" AC" as &[u8], newline].concat();
    let mut result = Vec::new();
    let mut from = 0;
    while let Some(start) = find_bytes(block, &head, from) {
        let mut matched = None;
        let mut gap = start + head.len();
        while gap <= block.len() {
            if block[gap..].starts_with(newline) {
                let digits_start = gap + newline.len();
                let mut at = digits_start;
                while at < block.len() && block[at].is_ascii_digit() {
                    at += 1;
                }
                if at > digits_start && block[at..].starts_with(&tail) {
                    matched = Some((digits_value(&block[digits_start..at]), at + tail.len()));
                    break;
                }
            }
            gap += 1;
        }
        match matched {
            Some((value, end)) => {
                result.push(value);
                from = end;
            }
            None => from = start + 1,
        }
    }
    result
}

/// Recover DDWELL's original and allocated reference (`_original_ddwell`).
pub fn original_ddwell(data: &[u8]) -> Result<(Vec<u8>, Option<i64>)> {
    let expected = original_hash("ddwell")?;
    if sha256_hex(data) == expected {
        return Ok((data.to_vec(), None));
    }
    let spec = reward("ddwell");
    let newline = newline(data)?;
    let (blocks, prefix, suffix) = blocks(data, 4, newline)?;
    let mut original_block = blocks[1].clone();
    let grant = grant_action(&original_block, "helm21", 140, newline)?;
    let additions = [
        global_guard(&text(&spec, "selected_global")?, false, newline)?,
        marker_action(&grant, &spec, newline)?,
        global_action(&text(&spec, "event_global")?, newline)?,
    ];
    for addition in additions {
        if count_bytes(&original_block, &addition) != 1 {
            return Err(Error::profile("DDWELL patched reward changed"));
        }
        original_block = replace_bytes(&original_block, &addition, b"", Some(1));
    }
    let original = [prefix.as_slice(), &blocks[0], &original_block, &blocks[3], &suffix].concat();
    // Read only the selected branch's DisplayString reference, then prove
    // every byte of both branches by regenerating from the verified original.
    let text = ddwell_display_refs(&blocks[2], newline);
    if sha256_hex(&original) != expected || text.len() != 1 {
        return Err(Error::profile("DDWELL patched reward changed"));
    }
    let reference = text[0];
    if patch_script(&original, &spec, Some(reference))? != data {
        return Err(Error::profile("DDWELL patched reward changed"));
    }
    Ok((original, Some(reference)))
}

fn resource_key(resource: &str) -> Result<String> {
    let lowered = crate::py::lower(resource);
    let last = lowered.rsplit(':').next().unwrap_or("");
    let name = last.split('.').next().unwrap_or("");
    let candidates: Vec<&str> =
        resource_rewards().keys().filter(|key| key.split(':').nth(1) == Some(name)).collect();
    if candidates.len() != 1 {
        return Err(Error::profile("Unsupported Mode 2 scripted reward"));
    }
    Ok(candidates[0].to_string())
}

/// Validate known bytes, recovering originals for patches with dynamic text.
///
/// Fixed-hash static patches have no recovery metadata and return None after
/// verification. Unknown/tampered bytes always raise instead of being accepted.
pub fn original_reward(resource: &str, data: &[u8]) -> Result<Option<Vec<u8>>> {
    let resource = resource_key(resource)?;
    let name = resource.split(':').nth(1).expect("kind:name");
    if name == "ddwell" {
        return Ok(Some(original_ddwell(data)?.0));
    }
    if text_refs(&resource).is_some() {
        return Ok(Some(original_text_dialogue(data, &resource)?.0));
    }
    let digest = sha256_hex(data);
    if digest == original_hash(name)? {
        return Ok(Some(data.to_vec()));
    }
    if Some(&digest) == patched_hash(name).as_ref() {
        return Ok(None);
    }
    Err(Error::profile(format!("{} changed; review the Mode 2 reward adapter", crate::py::upper(name))))
}

/// Patch reviewed/own bytes; text resources need refs on their first export.
pub fn patch_reward(
    resource: &str,
    data: &[u8],
    refs: Option<&BTreeMap<i64, i64>>,
    ddwell_ref: Option<i64>,
) -> Result<Vec<u8>> {
    let resource = resource_key(resource)?;
    let name = resource.split(':').nth(1).expect("kind:name");
    if name == "ddwell" {
        let mut ddwell_ref = ddwell_ref;
        if let Some(refs) = refs {
            if refs.len() != 1 || !refs.contains_key(&40850) {
                return Err(Error::profile("DDWELL requires its allocated original reward text"));
            }
            ddwell_ref = Some(refs[&40850]);
        }
        let (original, prior_ref) = original_ddwell(data)?;
        return patch_script(&original, &reward(name), ddwell_ref.or(prior_ref));
    }
    if text_refs(&resource).is_some() {
        let (original, prior_refs) = original_text_dialogue(data, &resource)?;
        return patch_text_dialogue(&original, &resource, refs.or(prior_refs.as_ref()), true, true);
    }
    let digest = sha256_hex(data);
    let patched = patched_hash(name);
    if Some(&digest) == patched.as_ref() {
        return Ok(data.to_vec());
    }
    if digest != original_hash(name)? {
        return Err(Error::profile(format!("{} changed; review the Mode 2 reward adapter", crate::py::upper(name))));
    }
    let specs = resource_specs(&resource)?;
    let result = if resource.starts_with("dlg:") {
        patch_dialogue(data, &specs, &[], true, true)?
    } else {
        patch_script(data, &specs[0], None)?
    };
    if let Some(expected) = patched.filter(|hash| !hash.is_empty()) {
        if sha256_hex(&result) != expected {
            return Err(Error::profile("Mode 2 reward patch differs from reviewed bytes"));
        }
    }
    Ok(result)
}
