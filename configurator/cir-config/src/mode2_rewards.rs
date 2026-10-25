//! Campaign-gated quest rewards of the extended pools (the reference's
//! `mode2_rewards.py`; the module keeps the reference's name).
//!
//! Item Randomiser's extended lists move rewards that quest dialogues and
//! scripts give. Each reward route here is recognised by its meaning, never
//! by the bytes of one reviewed game: the replies or script blocks that give
//! the reward item, the recipient they name and, for some routes, the text
//! that names the item. Everything else -- Beamdog's or WeiDU's compiled
//! spacing, line ends and letter case, other strrefs, replies and blocks that
//! another mod added -- is kept as found, and every reply or block that gives
//! the item is gated.
//!
//! Only the grant is suppressed; its surrounding actions, conditions,
//! dialogue replies and journal fields stay intact (a text route shows Item
//! Randomiser's words instead of the texts that name the reward, journal
//! entries among them, where the reward moved: [`TEXT_REFS`]). The reward
//! event captures the original recipient at the grant position. Actions that only take, show
//! or test the reward stay as they are. A route is not prepared only where
//! its grant cannot be told apart or gated (no grant, a grant without a
//! recipient or in actions that cannot be read, the item given twice by one
//! reply or response, also given by another action, or given through
//! ActionOverride()); the configuration check leaves it out with that reason
//! ([`crate::reward_routes`]). See
//! lib/weidu_action.tpa's generic BCS/DLG handlers and DDWELL special case.
//!
//! Own patches are recognised too, so an exported revision re-exports byte
//! for byte: text routes and container dialogues carry a recovery trailer,
//! scripts are reconstructed exactly, and the other dialogues are checked by
//! removing the patch and applying it again.
//!
//! The routes are registered as each campaign's own game names its resources
//! (BG:EE's names for BG1's routes). EET renames BG1's areas and the BG1 items
//! whose names Baldur's Gate II uses ([`crate::game_names`]): the functions
//! that take a [`Naming`] return a route as that game names it. Without a
//! game ([`Naming::default`]), a route keeps its campaign's own names.

use crate::error::{Error, ErrorKind, Result};
use crate::game_names::{convert_resource, EetNames};
use crate::game_text::GameText;
use crate::json::{Dict, Value};
use crate::py::{count_bytes, find_bytes, latin1, put_u32, replace_bytes, rfind_bytes, str_value, u32_le, upper};
use crate::quest_options::{pad, Dialogue};
use crate::translations::Line;
use crate::quest_rewards::{global_action, global_guard};
use crate::script_text::{
    argument, block_actions, block_parts, calls, calls_in, integer_argument, joined, named, naming, response_spans,
    script_blocks, string_argument, Action, Call, Script,
};
use crate::{dict, list};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::OnceLock;

pub const ADAPTER_ID: &str = "mode2-reward-v1";

/// How the scanned game names the routes' items and places (the reference's
/// `game` and `names` keywords): its platform ("eet", "bgee" or "bg2ee") and
/// EET's renames ([`crate::game_names::eet_names`]; without them, CIR's own
/// copy of EET's lists).
#[derive(Clone, Copy, Debug, Default)]
pub struct Naming<'a> {
    pub game: Option<&'a str>,
    pub names: Option<&'a EetNames>,
}

/// Original generic descriptions from the matching special WeiDU handlers.
/// Keys are the strrefs of the text that names the reward (BG2's the same in
/// EET and BG2:EE, BG1's as BG:EE numbers them: see [`game_text_refs`]),
/// values are source TRA entries.
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
    ("dlg:jugjer01", &[(33760, 698), (33777, 699)]),
    // Aldeth's thanks and the journal entry his reward reply adds.
    ("dlg:aldeth", &[(5740, 1649), (26828, 1650)]),
];
pub const ACTION_TEXT_REFS: &[(&str, &[i64])] = &[("dlg:amcler01", &[74025])];
/// Journal entries among the [`TEXT_REFS`]: their new text keeps the
/// original's title line, and BGEE.LUA lists it with the original's quest
/// ([`journal_lua`]).
pub const JOURNAL_REFS: &[(&str, &[i64])] = &[("dlg:aldeth", &[26828])];
/// EET numbers BG:EE's own texts 200000 higher than BG:EE does.
const EET_BG1_TEXTS: i64 = 200000;
const TEXT_TRAILER: &[u8] = b"FLPRM2T1";
/// The texts a reply shows: (its flag, the strref's offset), for its own text
/// and its journal entry.
const TEXT_FIELDS: [(u32, usize); 2] = [(1, 4), (0x10, 8)];
/// DestroySelf is instant during dialogue; LuaAction is queued. A speaker
/// whose carried reward is handed over by a queued transfer leaves after it
/// instead.
pub const DEPARTURE: &[u8] =
    b"EEex_LuaAction(\"EEex_Action_QueueResponseStringOnAIBase([[DestroySelf()]],EEex_LuaAction_Object)\")";
/// Every carried reward's transfer ([`callback`]) calls this.
const TRANSFER: &[u8] = b"FLPR_Transfer(EEex_LuaAction_Object,";
/// The engine's actions that put an item somewhere (ACTION.IDS of the 2.7
/// games): opcode, name, the dialogue argument and the compiled string that
/// name the item they give. Other actions that name the reward take, use, show
/// or test it; after a moved reward they find it gone, as triggers do when the
/// player sold it, so they never stop a route.
const GIVING: &[(i64, &str, usize, usize)] = &[
    (140, "GiveItemCreate", 0, 0),
    (82, "CreateItem", 0, 0),
    (15, "GiveItem", 0, 0),
    (14, "GetItem", 0, 0),
    (256, "CreateItemGlobal", 2, 1),
    (278, "TakeItemReplace", 0, 0),
    (356, "TransformItem", 1, 1),
    (9, "DropItem", 0, 0),
    (257, "PickUpItem", 0, 0),
    (363, "AddStoreItem", 1, 1),
];

/// A giving action of a compiled script: its name and the string that names
/// the item.
fn giving_opcode(opcode: i64) -> Option<(&'static str, usize)> {
    GIVING.iter().find(|row| row.0 == opcode).map(|row| (row.1, row.3))
}

/// A giving call of dialogue text: the argument that names the item.
fn giving_call(name: &[u8]) -> Option<usize> {
    GIVING.iter().find(|row| name.eq_ignore_ascii_case(row.1.as_bytes())).map(|row| row.2)
}

/// `TEXT_REFS.get(resource)`.
pub fn text_refs(resource: &str) -> Option<&'static [(i64, i64)]> {
    TEXT_REFS.iter().find(|(key, _)| *key == resource).map(|(_, refs)| *refs)
}

/// `_text_shift`: how much higher the game numbers a route resource's texts
/// than its campaign does.
fn text_shift(resource: &str, game: Option<&str>) -> Result<i64> {
    let keys = resource_rewards().get(resource).and_then(Value::as_list).ok_or_else(|| Error::key(resource))?;
    let mut campaigns = BTreeSet::new();
    for key in keys {
        let key = key.as_str().expect("reward key");
        let row = rewards().get(key).and_then(Value::as_dict).ok_or_else(|| Error::key(key))?;
        campaigns.insert(text(row, "campaign")?);
    }
    Ok(if game == Some("eet") && campaigns.len() == 1 && campaigns.contains("bg1") { EET_BG1_TEXTS } else { 0 })
}

/// `text_refs(resource, game)`: `TEXT_REFS[resource]` as the game numbers its
/// texts (EET numbers BG1's higher).
pub fn game_text_refs(resource: &str, game: Option<&str>) -> Result<Vec<(i64, i64)>> {
    let shift = text_shift(resource, game)?;
    let refs = text_refs(resource).ok_or_else(|| Error::key(resource))?;
    Ok(refs.iter().map(|&(old, tra)| (old + shift, tra)).collect())
}

/// `journal_refs(resource, game)`: the journal entries among the resource's
/// text refs, as the game numbers them.
pub fn journal_refs(resource: &str, game: Option<&str>) -> Result<Vec<i64>> {
    let shift = text_shift(resource, game)?;
    Ok(JOURNAL_REFS
        .iter()
        .find(|(key, _)| *key == resource)
        .map_or_else(Vec::new, |(_, refs)| refs.iter().map(|old| old + shift).collect()))
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
    campaign: &str,
) -> Dict {
    // A BG1 route's flags are its own: B1_<row> beside BG2's M2_<row>.
    let suffix = format!("{}_{source}", if campaign == "bg1" { "B1" } else { "M2" });
    let target = target.map_or_else(|| "None".to_string(), |target| target.to_string());
    let recipient_local =
        if extension == "bcs" && name == "asysta01" { Value::Null } else { Value::from(format!("FLPR_R{suffix}")) };
    dict! {
        "quest" => name,
        "resource" => format!("{extension}:{name}"),
        "item" => item,
        "campaign" => campaign,
        "source_ids" => list![format!("original-source:{campaign}:mode2:item:{source}")],
        "target_ids" => list![format!("{campaign}:mode2:target:{target}")],
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
    campaign_alias(rewards, key, item, source, target, alias, dialogues, charges, recipient, "bg2");
}

/// `_alias(..., campaign=<campaign>)`: a route of BG1's lists has BG1's flags.
#[allow(clippy::too_many_arguments)]
fn campaign_alias(
    rewards: &mut Dict,
    key: &str,
    item: &str,
    source: i64,
    target: Option<i64>,
    alias: &str,
    dialogues: &[(&str, Vec<(i64, i64)>)],
    charges: [i64; 3],
    recipient: &str,
    campaign: &str,
) {
    let (primary, branches) = &dialogues[0];
    let mut row = spec(primary, "dlg", item, source, target, recipient, charges, pairs(branches), campaign);
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

struct Registry {
    rewards: Dict,
    direct: Vec<String>,
    transfers: Dict,
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
    let direct: [(&str, &str, &str, i64, i64, &str, [i64; 3], Value); 10] = [
        ("uhogre01", "dlg", "shld26", 14, 17, "LastTalkedToBy", NO_CHARGES, pairs(&[(22, 0), (22, 1)])),
        ("asysta01", "bcs", "helm19", 5, 21, "are:ar1512/container/0", NO_CHARGES, list![0]),
        ("ddwell", "bcs", "helm21", 27, 27, "LastTrigger", NO_CHARGES, list![1]),
        ("mgkhol01", "dlg", "belt02", 33, 33, "LastTalkedToBy", NO_CHARGES, pairs(&[(4, 0)])),
        ("ppstat01", "dlg", "ring31", 28, 35, "LastTalkedToBy", [1, 1, 1], pairs(&[(5, 0)])),
        ("riftg03", "dlg", "shld27", 38, 40, "Player1", NO_CHARGES, pairs(&[(30, 0)])),
        ("trhmer01", "dlg", "shld25", 52, 44, "LastTalkedToBy", NO_CHARGES, pairs(&[(12, 0), (37, 0)])),
        ("udsvir03", "dlg", "blun18", 50, 65, "LastTalkedToBy", [1, 1, 1], pairs(&[(35, 0), (35, 1)])),
        ("ammayor", "dlg", "compon16", 83, 84, "LastTalkedToBy", [1, 0, 0], pairs(&[(12, 0)])),
        // The Sahuagin king's bracer (Item Randomiser's tadpoles1.fl). The same
        // reply also gives the treasury's rod: see "sahrod" below.
        ("sahkng01", "dlg", "brac19", 57, 58, "LastTalkedToBy", NO_CHARGES, pairs(&[(81, 0)])),
    ];
    let mut direct_names: Vec<String> = direct.iter().map(|row| row.0.to_string()).collect();
    for (name, extension, item, source, target, recipient, charges, branches) in direct {
        rewards.insert(name, spec(name, extension, item, source, Some(target), recipient, charges, branches, "bg2"));
    }
    // Varci's payment for Casson's rescue (Item Randomiser's varci.fl), the
    // shield EET names SHLD07A.
    let recipient = "LastTalkedToBy(Myself)";
    rewards.insert("varci", spec("varci", "dlg", "shld07", 20, Some(20), recipient, NO_CHARGES, pairs(&[(11, 0)]), "bg1"));
    direct_names.push("varci".to_string());
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
    // guarded copy, never another randomized source unit. Each copy is the
    // creature's records of the reward item, as the scan finds them
    // (native_export.rs), whatever their place or flags in this game.
    {
        let row = row_mut(&mut rewards, "scyarryl");
        row.insert("authored_source_ids", list!["original-source:bg2:mode2:item:12"]);
        row.insert("source_ids", Value::List(Vec::new()));
        row.insert("source_resource", "cre:scyarryl");
        row.insert("grant_kind", "transfer");
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
    type Carried = (&'static str, &'static str, i64, i64, &'static str, &'static str, Vec<(i64, i64)>, [i64; 3], &'static str);
    let carried: [Carried; 6] = [
        ("trgeni01", "sw1h23", 32, 32, "ar2014", "trgeni01", vec![(35, 0), (40, 0)], NO_CHARGES, "bg2"),
        ("loutcapt", "dagg11", 39, 42, "ar0514", "Dennis", vec![(26, 0), (30, 0)], NO_CHARGES, "bg2"),
        ("amcler01", "ring43", 84, 85, "ar5500", "amcler01", vec![(7, 0), (8, 0)], NO_CHARGES, "bg2"),
        // Jermien's sword (Item Randomiser's jugjer01.fl), with its daily charge.
        ("jugjer01", "sw1h26", 31, 31, "ar1103", "Jermien", vec![(23, 0)], [1, 1, 0], "bg2"),
        // Aldeth's sword (Item Randomiser's aldeth.fl). He thanks the party in
        // his estate (AR0128), whose script creates him there.
        ("aldeth", "sw1h03", 16, 16, "ar0128", "Aldeth", vec![(27, 0)], NO_CHARGES, "bg1"),
        // Nemphre's scroll for Arkion's amulet (Item Randomiser's nemphr.fl).
        // Item Randomiser's lists give her place scrolls only (tier sc), so her
        // words ("take this scroll") stay true.
        ("nemphr", "scrl1q", 159, 136, "ar0813", "Nemphre", vec![(3, 0)], NO_CHARGES, "bg1"),
    ];
    for (name, item, source, target, area, script, branches, charges, campaign) in carried {
        let alias_name = name.to_uppercase();
        campaign_alias(&mut rewards, name, item, source, Some(target), &alias_name, &[(name, branches)], charges, talker, campaign);
        let row = row_mut(&mut rewards, name);
        let source_ids = row.get("source_ids").cloned().expect("source ids");
        row.insert("grant_kind", "transfer");
        row.insert("authored_source_ids", source_ids);
        row.insert(
            "inventory_target",
            dict! {"area" => area, "creature_resource" => format!("cre:{name}"), "script_name" => script},
        );
        row.insert("source_resource", format!("cre:{name}"));
        row.insert("required_transfers", list![name]);
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
        row.insert("replacement", "dwsw1h02");
    }

    // The Sahuagin treasury's rod (row7, whose row names sahkng01.dlg and
    // sahtreas.bcs) is an ordinary container source, but the king's reward
    // reply creates another one (SAHTREAS.BCS then destroys the treasury's).
    // As with UDLESA's sword, the reply follows the source's kept/relocated
    // decision. The rod's place (row 6, Item Randomiser's sahtreas.fl) is the
    // treasury, the AR2300 container whose script is SAHTREAS: its reward lies
    // there until the king's reward reply hands it to the one he pays
    // ([`handover`]), and goes to that payee once he has paid. On the
    // prince's side it stays to be looted.
    alias(&mut rewards, "sahrod", "rods02", 7, Some(6), "SAHROD", &[("sahkng01", vec![(81, 0)])], NO_CHARGES, talker);
    {
        let row = row_mut(&mut rewards, "sahrod");
        row.insert("source_ids", Value::List(Vec::new()));
        row.insert("authored_source_ids", list!["original-source:bg2:mode2:item:7"]);
        row.insert("handover", dict! {"area" => "ar2300", "script" => "sahtreas"});
    }
    // Once the king's job is done (sahkngjob 2, which he sets just before his
    // reward reply), SAHTREAS.BCS destroys the items that reply gives
    // (DestroyItem), the treasury's rod among them, with every AI update of
    // the treasury. A reward that moved there under such a name must stay:
    // each destroy runs only while its route's grant is the original one
    // ([`patch_destroys`]). A game without the script needs no gate.
    for (_, keys) in DESTROY_GATES {
        for key in *keys {
            row_mut(&mut rewards, key).insert("resources", list!["dlg:sahkng01", "bcs:sahtreas"]);
        }
    }
    // The king's own place (row 58, Item Randomiser's tadpoles1.fl) is his
    // reward reply, which hands its reward to the one he pays. Item
    // Randomiser also gives the item to SAHKNG01.CRE and SAHKNG02.CRE, the
    // king who takes the throne on the prince's side, where that reply never
    // comes: there the party kills the king for the prince. So until the reply
    // pays, the place's reward waits for the king's death and then lies at his
    // corpse (`holder`): whichever king the area has, both named sahkng01. A
    // living king may still pay, or he destroys himself when the prince's job
    // begins (SAHKNG01.BCS) and the other one comes.
    row_mut(&mut rewards, "sahkng01").insert(
        "holder",
        dict! {
            "area" => "ar2300", "script_name" => "sahkng01",
            "creature_resources" => list!["cre:sahkng01", "cre:sahkng02"],
        },
    );
    // The two scrolls that the book of Lavok's sphere (PPBOOK.BCS, AR1513)
    // gives with its ring on the last page (Item Randomiser's rows 260 and
    // 262). The book's places are the ring's quest (the installer splits that
    // page for the ring: quest_rewards::QUESTS), whose event delivers what
    // they receive; a scroll's route only drops its grant where its row moved,
    // so it has no place of its own. The installed book gives them in two
    // blocks, 6 and 7.
    for (key, item, source) in [("ppbook", "scrl9b", 260), ("ppbook2", "scrl8z", 262)] {
        let mut row = spec("ppbook", "bcs", item, source, None, "LastTrigger", NO_CHARGES, list![6, 7], "bg2");
        row.insert("target_ids", Value::List(Vec::new()));
        rewards.insert(key, row);
    }
    // The Sahuagin prince's reward (Item Randomiser's tadpoles2.fl, row 59).
    // Item Randomiser gives it to SAHPR2.CRE and adds a GiveItem to the reply
    // that ends his job (villynatyjob 2), which gives nothing in the games.
    // Here he carries the place's reward, and each reply of his dialogue that
    // ends his job hands it to the one he talks to: the carried rewards'
    // transfer goes in front of its actions ([`job_replies`]). The route has
    // no item of its own to gate. A reply of another dialogue that ends his
    // job too would end it without the hand-over: where one follows from his,
    // the place is left out ([`crate::reward_copies`]).
    rewards.insert(
        "sahpr2",
        dict! {
            "quest" => "sahpr2",
            "resource" => "dlg:sahpr2",
            "item" => Value::Null,
            "campaign" => "bg2",
            "source_ids" => Value::List(Vec::new()),
            "authored_source_ids" => Value::List(Vec::new()),
            "target_ids" => list!["bg2:mode2:target:59"],
            "selected_global" => Value::Null,
            "consumed_global" => Value::Null,
            "event_global" => Value::Null,
            "recipient_local" => Value::Null,
            "recipient" => talker,
            "branches" => pairs(&[(5, 0)]),
            "payload" => Value::Null,
            "grant_kind" => "transfer",
            "job_end" => list!["villynatyjob", 2],
            "inventory_target" => dict! {"area" => "ar2300", "creature_resource" => "cre:sahpr2", "script_name" => "sahpr2"},
        },
    );
    // Row 60 (Item Randomiser's tadpoles3.fl, "Sahuagin king or prince"): a
    // coin flip at install gives the item to SAHPR2.CRE or to both kings,
    // SAHKNG01.CRE and SAHKNG02.CRE. The king leaves with what he carries on
    // either side: on his own he pays and escapes (SAHKNG01.DLG state 81), on
    // the prince's he destroys himself once that job begins (SAHKNG01.BCS)
    // and SAHKNG02 takes the throne from its own file. So here the prince
    // carries this place's reward too, and the replies that end his job hand
    // it over after row 59's.
    let mut prince = rewards.get("sahpr2").cloned().expect("sahpr2");
    prince.as_dict_mut().expect("reward row").insert("target_ids", list!["bg2:mode2:target:60"]);
    rewards.insert("sahpr2b", prince);
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
    for (resource, keys) in DESTROY_GATES {
        resources.insert(*resource, Value::List(keys.iter().map(|key| Value::from(*key)).collect()));
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
    Registry { rewards, direct: direct_names, transfers, resources, sources, targets }
}

/// `REWARDS`: every reward route by key, in the reference's order.
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

/// `PHYSICAL_SOURCE_REWARDS`: physical sources whose reward dialogue also
/// gives their item, by (owner resource, kind, item) as the catalog binds the
/// original row: the creature's or the area's container record of the item.
/// A game whose dialogue gives no such item needs no route
/// ([`gives_reward`]). Each entry is (resource, kind, item, route).
pub const PHYSICAL_SOURCE_REWARDS: &[(&str, &str, &str, &str)] =
    &[("cre:udlesa", "cre-template", "sw1h39", "udlesa"), ("are:ar2300", "container", "rods02", "sahrod")];

/// The route of a physical source of `item` (a `kind` record of `resource`),
/// if its reward dialogue gives the item too.
pub fn physical_source_reward(resource: &str, kind: &str, item: &str) -> Option<&'static str> {
    PHYSICAL_SOURCE_REWARDS.iter().find(|row| (row.0, row.1, row.2) == (resource, kind, item)).map(|row| row.3)
}

/// `DESTROY_GATES`: scripts that destroy routes' items, with those routes
/// ([`patch_destroys`]); each is also a resource of its routes.
pub const DESTROY_GATES: &[(&str, &[&str])] = &[("bcs:sahtreas", &["sahkng01", "sahrod"])];

/// Whether `resource` is one of the [`DESTROY_GATES`].
pub fn destroy_gate(resource: &str) -> bool {
    DESTROY_GATES.iter().any(|(gate, _)| *gate == resource)
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

/// The route as the scanned game names its items and places (EET renames
/// BG1's).
///
/// Its conversations keep their names: EET renames only BG1 resources whose
/// names Baldur's Gate II uses, which no route's conversation does. Were one
/// renamed, its rows would name another conversation than the route and be
/// left out.
fn in_game(mut spec: Dict, naming: Naming<'_>) -> Result<Dict> {
    let Some(game) = naming.game else {
        return Ok(spec);
    };
    let campaign = text_field(&spec, "campaign", "lower")?.to_string();
    let name = |resref: &str, kind: &str| convert_resource(resref, kind, &campaign, game, naming.names);
    // A route without a grant of its own has no item (the prince's).
    if !matches!(field(&spec, "item")?, Value::Null) {
        let item = Value::from(name(text_field(&spec, "item", "lower")?, "itm")?);
        spec.insert("item", item.clone());
        match spec.get_mut("payload").ok_or_else(|| Error::key("payload"))? {
            Value::Dict(payload) => payload.insert("resref", item),
            other => {
                return Err(Error::type_error(format!("'{}' object does not support item assignment", other.type_name())))
            }
        };
    }
    if spec.get("replacement").is_some_and(Value::truthy) {
        let replacement = name(text_field(&spec, "replacement", "lower")?, "itm")?;
        spec.insert("replacement", replacement);
    }
    if spec.get("source_resource").is_some_and(Value::truthy) {
        let source = text_field(&spec, "source_resource", "split")?;
        let parts: Vec<&str> = source.split(':').collect();
        let [kind, resref] = parts[..] else {
            return Err(Error::value(if parts.len() > 2 {
                "too many values to unpack (expected 2)".to_string()
            } else {
                format!("not enough values to unpack (expected 2, got {})", parts.len())
            }));
        };
        let source = format!("{kind}:{}", name(resref, kind)?);
        spec.insert("source_resource", source);
    }
    if let Some(Value::Dict(placement)) = spec.get("inventory_target").filter(|placement| placement.truthy()) {
        let mut placement = placement.clone();
        let area = name(text_field(&placement, "area", "lower")?, "are")?;
        let creature = text_field(&placement, "creature_resource", "lower")?.chars().skip(4).collect::<String>();
        let creature = format!("cre:{}", name(&creature, "cre")?);
        placement.insert("area", area);
        placement.insert("creature_resource", creature);
        spec.insert("inventory_target", placement);
    }
    if let Some(Value::Dict(handover)) = spec.get("handover").filter(|handover| handover.truthy()) {
        let mut handover = handover.clone();
        let area = name(text_field(&handover, "area", "lower")?, "are")?;
        handover.insert("area", area);
        spec.insert("handover", handover);
    }
    if let Some(Value::Dict(holder)) = spec.get("holder").filter(|holder| holder.truthy()) {
        let mut holder = holder.clone();
        let area = name(text_field(&holder, "area", "lower")?, "are")?;
        let resources = match field(&holder, "creature_resources")? {
            Value::List(resources) => resources.clone(),
            other => return Err(Error::type_error(format!("'{}' object is not iterable", other.type_name()))),
        };
        let mut creatures = Vec::new();
        for resource in resources {
            let creature = resource.as_str().unwrap_or("").chars().skip(4).collect::<String>();
            creatures.push(Value::from(format!("cre:{}", name(&creature, "cre")?)));
        }
        holder.insert("area", area);
        holder.insert("creature_resources", Value::List(creatures));
        spec.insert("holder", holder);
    }
    Ok(spec)
}

/// The route `key` (a [`rewards`] key) as the game names it.
pub fn reward(key: &str, naming: Naming<'_>) -> Result<Dict> {
    match rewards().get(key).and_then(Value::as_dict) {
        Some(row) => in_game(row.clone(), naming),
        None => Err(Error::profile(format!("Unsupported extended reward route: {key}"))),
    }
}

pub fn source_spec(source_id: &str, naming: Naming<'_>) -> Result<Dict> {
    match source_rewards().get(source_id).and_then(Value::as_str) {
        Some(key) => reward(key, naming),
        None => Err(Error::profile(format!("Unsupported extended reward source: {source_id}"))),
    }
}

pub fn target_spec(target_id: &str, naming: Naming<'_>) -> Result<Dict> {
    match target_rewards().get(target_id).and_then(Value::as_str) {
        Some(key) => reward(key, naming),
        None => Err(Error::profile(format!("Unsupported extended reward recipient: {target_id}"))),
    }
}

/// Inventory copies which must be guarded alongside a scripted source.
pub fn transfer_spec(name: &str, naming: Naming<'_>) -> Result<Dict> {
    match transfer_specs().get(name).and_then(Value::as_dict) {
        Some(row) => in_game(row.clone(), naming),
        None => Err(Error::profile(format!("Unsupported carried reward: {name}"))),
    }
}

/// All routes with a grant in a resource; includes both independent ARAN
/// rewards. Each copy names its route (the `REWARDS` key) under "route".
pub fn resource_specs(resource: &str, naming: Naming<'_>) -> Result<Vec<Dict>> {
    let resource = crate::py::lower(resource);
    let Some(keys) = resource_rewards().get(&resource).and_then(Value::as_list) else {
        return Err(Error::profile(format!("Unsupported extended reward resource: {resource}")));
    };
    let name = resource.split(':').nth(1).expect("kind:name").to_string();
    let mut result = Vec::new();
    for key in keys {
        let key = key.as_str().expect("reward key");
        let mut spec = reward(key, naming)?;
        spec.insert("route", key);
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

// ---------------------------------------------------------------- reading

fn field<'a>(spec: &'a Dict, key: &str) -> Result<&'a Value> {
    spec.get(key).ok_or_else(|| Error::key(key))
}

/// A field formatted into text (`f"{spec[key]}"`).
fn text(spec: &Dict, key: &str) -> Result<String> {
    field(spec, key).map(str_value)
}

/// A field the reference calls a str method on (`.encode()`, `.upper()`).
fn text_field<'a>(spec: &'a Dict, key: &str, method: &str) -> Result<&'a str> {
    match field(spec, key)? {
        Value::Str(text) => Ok(text),
        other => Err(Error::attribute(other.type_name(), method)),
    }
}

/// The route a `resource_specs` copy names (`spec["route"]`).
fn route(spec: &Dict) -> Result<String> {
    text(spec, "route")
}

fn index_error() -> Error {
    Error::value("list index out of range")
}

fn profile(message: impl Into<String>) -> Error {
    Error::profile(message)
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

/// `NAME.KIND` of a `kind:name` resource, as messages show it.
pub(crate) fn label(resource: &str) -> String {
    let (kind, name) = resource.split_once(':').unwrap_or((resource, ""));
    format!("{}.{}", upper(name), upper(kind))
}

/// Whether script text names the item as a quoted resref, in any letter case.
pub(crate) fn names(text: &[u8], item: &str) -> bool {
    let item = item.as_bytes();
    text.windows(item.len() + 2).any(|window| {
        window[0] == b'"' && window[window.len() - 1] == b'"' && window[1..window.len() - 1].eq_ignore_ascii_case(item)
    })
}

pub(crate) fn read_dialogue(data: &[u8], resource: &str) -> Result<Dialogue> {
    let name = resource.split(':').nth(1).ok_or_else(index_error)?;
    Dialogue::new(data, name).map_err(|error| match error.kind {
        ErrorKind::Profile | ErrorKind::GameText => profile(format!("{} cannot be read as a dialogue", label(resource))),
        _ => error,
    })
}

/// A state's replies: (first, count), inside the reply table.
fn outgoing(dialogue: &Dialogue, state: &[u8], label: &str) -> Result<(usize, usize)> {
    let (first, count) = (u32_le(state, 4) as usize, u32_le(state, 8) as usize);
    if first + count > dialogue.transitions.len() {
        return Err(profile(format!("{label} has a state whose replies are outside its tables")));
    }
    Ok((first, count))
}

fn string(table: &[Vec<u8>], index: u32, label: &str) -> Result<Vec<u8>> {
    table
        .get(index as usize)
        .cloned()
        .ok_or_else(|| profile(format!("{label} has a reply whose text is outside its tables")))
}

/// A byte span of text and what replaces it.
type Edit = (usize, usize, Vec<u8>);

/// Replace non-overlapping spans of the original text.
fn edited(text: &[u8], mut edits: Vec<Edit>) -> Result<Vec<u8>> {
    edits.sort();
    let mut result = Vec::with_capacity(text.len());
    let mut at = 0;
    for (start, end, replacement) in edits {
        if start < at {
            return Err(profile("Two changes of one reward action overlap"));
        }
        result.extend_from_slice(&text[at..start]);
        result.extend(replacement);
        at = end;
    }
    result.extend_from_slice(&text[at..]);
    Ok(result)
}

fn guard(spec: &Dict, selected: bool) -> Result<Vec<u8>> {
    Ok(guard_line(selected, &text(spec, "selected_global")?))
}

fn guard_line(selected: bool, flag: &str) -> Vec<u8> {
    format!("{}Global(\"{flag}\",\"GLOBAL\",1)\r\n", if selected { "" } else { "!" }).into_bytes()
}

fn consumed(spec: &Dict) -> Result<Vec<u8>> {
    Ok(format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "consumed_global")?).into_bytes())
}

pub(crate) fn event(spec: &Dict) -> Result<Vec<u8>> {
    Ok(format!("SetGlobal(\"{}\",\"GLOBAL\",1)", text(spec, "event_global")?).into_bytes())
}

fn mark(spec: &Dict, recipient: &[u8]) -> Result<Vec<u8>> {
    let local = format!(",SetGlobal(\"{}\",\"LOCALS\",1))\r\n", text(spec, "recipient_local")?);
    Ok([b"ActionOverride(" as &[u8], recipient, local.as_bytes()].concat())
}

/// `re.fullmatch(r"(bg[12]):mode2:target:([1-9][0-9]*)", target)`: the
/// campaign and the row.
fn numeric_target_row(target: &str) -> Option<(&str, &str)> {
    let (campaign, row) = target.split_once(":mode2:target:")?;
    let bytes = row.as_bytes();
    (matches!(campaign, "bg1" | "bg2")
        && !bytes.is_empty()
        && (b'1'..=b'9').contains(&bytes[0])
        && bytes[1..].iter().all(u8::is_ascii_digit))
    .then_some((campaign, row))
}

/// The campaign and row of the route's only place (`target[0] if
/// len(target) == 1`) when it is a numbered row of the extended table.
fn only_target(spec: &Dict) -> Result<Option<(String, String)>> {
    let target = field(spec, "target_ids")?;
    if py_len(target)? != 1 {
        return Ok(None);
    }
    match first_item(target)? {
        Value::Str(target) => Ok(numeric_target_row(&target).map(|(campaign, row)| (campaign.to_string(), row.to_string()))),
        other => Err(Error::type_error(format!("expected string or bytes-like object, got '{}'", other.type_name()))),
    }
}

/// The blocking Lua callback that hands a carried reward over.
fn callback(spec: &Dict, numeric: bool) -> Result<Vec<u8>> {
    let Some((campaign, row)) = only_target(spec)? else {
        return Err(profile("The carried reward's transfer target changed"));
    };
    // The dialogue parser strips spaces and colons even inside Lua strings.
    // Parenthesize return; pass the row, and a BG1 row's campaign (1).
    Ok(if numeric {
        let campaign = if campaign == "bg1" { ",1" } else { "" };
        format!("EEex_LuaAction(\"return(FLPR_Transfer(EEex_LuaAction_Object,{row}{campaign}))\")")
    } else {
        // The only target, which numeric_target_row matched whole.
        format!("EEex_LuaAction(\"return FLPR_Transfer(EEex_LuaAction_Object,[[original-target:{campaign}:mode2:target:{row}]])\")")
    }
    .into_bytes())
}

/// The condition by which a reward reply hands its place's treasury to the
/// payee (M_FLPR's FLPR_Handover, `_handover`). It is true whatever happens
/// there, so the reply always shows; without CIR's runtime it does nothing.
pub fn handover(spec: &Dict) -> Result<Vec<u8>> {
    let Some((campaign, row)) = only_target(spec)? else {
        return Err(profile("The treasury's place changed"));
    };
    // The dialogue parser strips spaces and colons, as in callback.
    let campaign = if campaign == "bg1" { ",1" } else { "" };
    Ok(format!(
        "EEex_LuaTrigger(\"return(FLPR_Handover==nil)or(FLPR_Handover(EEex_LuaTrigger_Object,{row}{campaign}))\")\r\n"
    )
    .into_bytes())
}

/// Whether the route hands its place's treasury over (`spec.get("handover")`).
fn hands_over(spec: &Dict) -> bool {
    spec.get("handover").is_some_and(Value::truthy)
}

/// The replies that give a route's item, by (state, ordinal), with the call
/// that gives it.
pub type Grants = BTreeMap<(usize, usize), Call>;

/// Replies of a dialogue: state to the ordinals of its replies.
pub type Replies = BTreeMap<usize, BTreeSet<usize>>;

/// Whether a dialogue action puts the item somewhere (see [`GIVING`]).
pub fn gives_item(text: &[u8], call: &Call, item: &str) -> bool {
    giving_call(&call.name).is_some_and(|index| {
        string_argument(text, call, index).unwrap_or(b"").to_ascii_lowercase() == item.as_bytes()
    })
}

/// Whether a dialogue action puts the item somewhere, also through
/// ActionOverride (`_gives`).
pub(crate) fn gives(text: &[u8], call: &Call, item: &str) -> bool {
    if named(call, &["ActionOverride"]) && call.args.len() == 2 {
        return match calls_in(text, call.args[1]) {
            None => true,
            Some(inner) => inner.iter().any(|nested| gives_item(text, nested, item)),
        };
    }
    gives_item(text, call, item)
}

/// The global and value that end the job of a route without a grant
/// (`spec["job_end"]`).
pub(crate) fn job_end(spec: &Dict) -> Result<(String, i64)> {
    match field(spec, "job_end")? {
        Value::List(job) => match &job[..] {
            [Value::Str(name), value] => match value.as_int() {
                Some(value) => Ok((name.clone(), value)),
                None => Err(Error::type_error("The job's value must be an integer")),
            },
            _ => Err(Error::value(format!("expected 2 values to unpack, got {}", job.len()))),
        },
        other => Err(Error::type_error(format!("cannot unpack non-iterable {} object", other.type_name()))),
    }
}

/// Whether a dialogue action ends the job: sets its global to the job's
/// value (`SetGlobal(name,"GLOBAL",value)`), also through ActionOverride.
/// None when it cannot be read (`_ends_job`).
///
/// Any other action that writes the global (IncrementGlobal and the like)
/// could end the job too, unseen: the route cannot follow it.
pub(crate) fn ends_job(text: &[u8], call: &Call, job: &(String, i64), place: &str) -> Result<Option<bool>> {
    if named(call, &["ActionOverride"]) && call.args.len() == 2 {
        let Some(inner) = calls_in(text, call.args[1]) else { return Ok(None) };
        let mut found = Vec::new();
        for nested in &inner {
            found.push(ends_job(text, nested, job, place)?);
        }
        return Ok(if found.contains(&None) { None } else { Some(found.contains(&Some(true))) });
    }
    let (name, value) = job;
    let names_job = |index: usize| string_argument(text, call, index).unwrap_or(b"").to_ascii_lowercase() == name.as_bytes();
    // The scope after each argument that names the global: the job is GLOBAL.
    let scopes: Vec<Option<&[u8]>> =
        (0..call.args.len()).filter(|&index| names_job(index)).map(|index| string_argument(text, call, index + 1)).collect();
    if scopes.contains(&None) {
        return Ok(None);
    }
    if !scopes.iter().flatten().any(|scope| scope.eq_ignore_ascii_case(b"GLOBAL")) {
        return Ok(Some(false));
    }
    if !named(call, &["SetGlobal"]) {
        return Err(profile(format!(
            "{place} changes {name} with {}(), which the route cannot follow",
            String::from_utf8_lossy(&call.name)
        )));
    }
    if call.args.len() != 3 || scopes.len() != 1 || !names_job(0) {
        return Ok(None);
    }
    Ok(integer_argument(text, call, 2, None).map(|found| found == *value))
}

/// Whether a reply's actions end the job ([`ends_job`] of each call); an
/// error where they cannot be read (`_reply_ends_job`).
pub(crate) fn reply_ends_job(text: &[u8], job: &(String, i64), place: &str) -> Result<bool> {
    let unreadable = || profile(format!("{place} names {} in actions that cannot be read", job.0));
    let found = calls(text).ok_or_else(unreadable)?;
    let mut ends = Vec::new();
    for call in &found {
        ends.push(ends_job(text, call, job, place)?);
    }
    if ends.contains(&None) {
        return Err(unreadable());
    }
    Ok(ends.contains(&Some(true)))
}

/// Every reply that ends the job of a route without a grant (its "job_end"
/// global and value), where the speaker hands over the reward it carries
/// (`_job_replies`).
///
/// A reply that names the global in actions that cannot be read cannot be
/// told apart, nor one that changes it another way ([`ends_job`]), and a
/// dialogue with no such reply has nothing to hand over.
pub(crate) fn job_replies(dialogue: &Dialogue, spec: &Dict, label: &str) -> Result<BTreeSet<(usize, usize)>> {
    let job = job_end(spec)?;
    let mut replies = BTreeSet::new();
    for (state_index, state) in dialogue.states.iter().enumerate() {
        let (first, count) = outgoing(dialogue, state, label)?;
        for ordinal in 0..count {
            let row = &dialogue.transitions[first + ordinal];
            if u32_le(row, 0) & 4 == 0 {
                continue;
            }
            let text = string(&dialogue.actions, u32_le(row, 16), label)?;
            if find_bytes(&text.to_ascii_lowercase(), job.0.as_bytes(), 0).is_none() {
                continue;
            }
            if reply_ends_job(&text, &job, &format!("{label} state {state_index}"))? {
                replies.insert((state_index, ordinal));
            }
        }
    }
    if replies.is_empty() {
        return Err(profile(format!("{label} has no reply that sets {} to {}", job.0, job.1)));
    }
    Ok(replies)
}

/// Whether a route has no grant of its own, but hands over at its job's end
/// (`spec.get("job_end")`).
pub(crate) fn ends_a_job(spec: &Dict) -> bool {
    spec.get("job_end").is_some_and(Value::truthy)
}

/// Every reply that gives the route's item, with the call that gives it.
///
/// A created reward is given with GiveItemCreate, a carried one with
/// GiveItem: that call is the grant, one per reply, and gating the grants
/// gates every way this dialogue gives the item. A reply that gives it another
/// way, with another action or through another actor, cannot be gated.
/// `only` limits the search to these replies.
pub fn dialogue_grants(dialogue: &Dialogue, spec: &Dict, label: &str, only: Option<&Replies>) -> Result<Grants> {
    let item = text_field(spec, "item", "upper")?;
    let wanted = if spec.get("grant_kind").is_some_and(|kind| kind.py_eq(&Value::from("transfer"))) {
        "GiveItem"
    } else {
        "GiveItemCreate"
    };
    let shown = upper(item);
    let mut grants = Grants::new();
    for (state_index, state) in dialogue.states.iter().enumerate() {
        let wanted_ordinals = match only {
            Some(only) => match only.get(&state_index) {
                Some(ordinals) => Some(ordinals),
                None => continue,
            },
            None => None,
        };
        let (first, count) = outgoing(dialogue, state, label)?;
        for ordinal in 0..count {
            if wanted_ordinals.is_some_and(|ordinals| !ordinals.contains(&ordinal)) {
                continue;
            }
            let row = &dialogue.transitions[first + ordinal];
            if u32_le(row, 0) & 4 == 0 {
                continue;
            }
            let text = string(&dialogue.actions, u32_le(row, 16), label)?;
            if !names(&text, item) {
                continue;
            }
            let place = format!("{label} state {state_index}");
            let Some(found) = calls(&text) else {
                return Err(profile(format!("{place} names {shown} in actions that cannot be read")));
            };
            let mut given = Vec::new();
            for call in found {
                if !names(&text[call.start..call.end], item) {
                    continue;
                }
                let argument = string_argument(&text, &call, 0).unwrap_or(b"").to_ascii_lowercase();
                if named(&call, &[wanted]) && argument == item.as_bytes() {
                    given.push(call);
                } else if named(&call, &["ActionOverride"]) {
                    // Another actor runs the action, and its recipient means
                    // what it means to that actor.
                    let inner = if call.args.len() == 2 { calls_in(&text, call.args[1]) } else { None };
                    let Some(inner) = inner else {
                        return Err(profile(format!("{place} names {shown} in actions that cannot be read")));
                    };
                    if inner.iter().any(|nested| gives_item(&text, nested, item)) {
                        return Err(profile(format!(
                            "{place} gives {shown} through ActionOverride(), whose recipient the route cannot mark"
                        )));
                    }
                } else if gives_item(&text, &call, item) {
                    return Err(profile(format!(
                        "{place} also gives {shown} with {}(), which the route cannot gate",
                        String::from_utf8_lossy(&call.name)
                    )));
                }
            }
            if given.len() > 1 {
                return Err(profile(format!("{place} gives {shown} twice in one reply")));
            }
            if let Some(call) = given.into_iter().next() {
                if argument(&text, &call, 1).is_none_or(<[u8]>::is_empty) {
                    return Err(profile(format!("{place} gives {shown} without a recipient")));
                }
                grants.insert((state_index, ordinal), call);
            }
        }
    }
    if grants.is_empty() {
        return Err(profile(format!("{label} has no reply that gives {shown}")));
    }
    Ok(grants)
}

/// Point every original strref a grant's actions show at its new text;
/// `shown` collects them.
fn display_edits(text: &[u8], refs: &[(i64, i64)], shown: &mut BTreeSet<i64>) -> Vec<Edit> {
    let mut edits = Vec::new();
    for call in calls(text).unwrap_or_default() {
        for (index, &(start, end)) in call.args.iter().enumerate() {
            let Some(old) = integer_argument(text, &call, index, None) else { continue };
            if let Some(&(_, new)) = refs.iter().find(|(known, _)| *known == old) {
                edits.push((start, end, new.to_string().into_bytes()));
                shown.insert(old);
            }
        }
    }
    edits
}

/// The speaker's own departures (`DestroySelf()`), queued after its transfer.
fn departure_edits(text: &[u8], place: &str) -> Result<Vec<Edit>> {
    match calls(text) {
        None => {
            if text.windows(b"destroyself".len()).any(|window| window.eq_ignore_ascii_case(b"destroyself")) {
                return Err(profile(format!("{place} leaves in actions that cannot be read")));
            }
            Ok(Vec::new())
        }
        Some(found) => Ok(found
            .iter()
            .filter(|call| named(call, &["DestroySelf"]) && call.args.is_empty())
            .map(|call| (call.start, call.end, DEPARTURE.to_vec()))
            .collect()),
    }
}

/// A reply's next dialogue without its padding.
fn link_name(row: &[u8]) -> &[u8] {
    let link = &row[20..28];
    &link[..link.iter().rposition(|&byte| byte != 0).map_or(0, |last| last + 1)]
}

/// States this dialogue can continue to after the given replies.
fn following(dialogue: &Dialogue, rows: &[Vec<u8>], label: &str) -> Result<Vec<usize>> {
    let name = dialogue.name.to_ascii_lowercase();
    let continuations = |replies: &[Vec<u8>]| -> Vec<usize> {
        replies
            .iter()
            .filter_map(|row| {
                let next = u32_le(row, 28) as usize;
                (u32_le(row, 0) & 8 == 0 && link_name(row).to_ascii_lowercase() == name && next < dialogue.states.len())
                    .then_some(next)
            })
            .collect()
    };
    let mut seen = BTreeSet::new();
    let mut todo = continuations(rows);
    while let Some(state) = todo.pop() {
        if !seen.insert(state) {
            continue;
        }
        let (first, count) = outgoing(dialogue, &dialogue.states[state], label)?;
        todo.extend(continuations(&dialogue.transitions[first..first + count]));
    }
    Ok(seen.into_iter().collect())
}

// ---------------------------------------------------------------- dialogues

/// Gate every reply that gives a route's item, for each recognised route
/// (`_patch_dialogue`).
///
/// A route without a grant of its own (job_end) instead hands over what its
/// speaker carries in each reply that ends its job ([`job_replies`]).
/// A route whose grant is not recognised keeps its replies as they are; its
/// reason goes to `reasons` (route to text). With no route recognised,
/// nothing is prepared. `action_refs` maps displayed original strrefs to the
/// allocated ones for selected branches.
pub fn patch_dialogue(
    data: &[u8],
    specs: &[Dict],
    action_refs: &[(i64, i64)],
    defer_departure: bool,
    numeric_target: bool,
    reasons: Option<&mut Dict>,
) -> Result<Vec<u8>> {
    patch_dialogue_in(data, specs, action_refs, defer_departure, numeric_target, reasons, None, None, None)
}

/// [`patch_dialogue`] with the reference's further keywords: `only` limits
/// each route to the given replies, as in a dialogue that holds copies of its
/// reward replies (`resource` names that dialogue); `shown` collects the
/// strrefs of `action_refs` that a grant shows.
#[allow(clippy::too_many_arguments)]
pub fn patch_dialogue_in(
    data: &[u8],
    specs: &[Dict],
    action_refs: &[(i64, i64)],
    defer_departure: bool,
    numeric_target: bool,
    reasons: Option<&mut Dict>,
    only: Option<&BTreeMap<String, Replies>>,
    resource: Option<&str>,
    shown: Option<&mut BTreeSet<i64>>,
) -> Result<Vec<u8>> {
    let first_spec = specs.first().ok_or_else(index_error)?;
    let resource = match resource {
        Some(resource) if !resource.is_empty() => resource.to_string(),
        _ => text(first_spec, "resource")?,
    };
    let label = label(&resource);
    let mut dialogue = read_dialogue(data, &resource)?;
    let mut own_shown = BTreeSet::new();
    let shown = shown.unwrap_or(&mut own_shown);
    let mut grants: HashMap<String, Grants> = HashMap::new();
    let mut added: HashMap<String, BTreeSet<(usize, usize)>> = HashMap::new();
    let mut failures = Dict::new();
    for spec in specs {
        if ends_a_job(spec) {
            match job_replies(&dialogue, spec, &label) {
                Ok(replies) => {
                    added.insert(route(spec)?, replies);
                }
                Err(error) if error.kind == ErrorKind::Profile => failures.insert(route(spec)?, error.message),
                Err(error) => return Err(error),
            }
            continue;
        }
        let replies = match only {
            Some(only) => Some(only.get(&route(spec)?).ok_or_else(|| Error::key(&route(spec).unwrap_or_default()))?),
            None => None,
        };
        match dialogue_grants(&dialogue, spec, &label, replies) {
            Ok(found) => {
                grants.insert(route(spec)?, found);
            }
            Err(error) if error.kind == ErrorKind::Profile => failures.insert(route(spec)?, error.message),
            Err(error) => return Err(error),
        }
    }
    if let Some(reasons) = reasons {
        for (key, reason) in failures.iter() {
            reasons.insert(key, reason.clone());
        }
    }
    let mut active: Vec<(&Dict, &Grants)> = Vec::new();
    let mut adding: Vec<(&Dict, &BTreeSet<(usize, usize)>)> = Vec::new();
    for spec in specs {
        if let Some(found) = grants.get(&route(spec)?) {
            active.push((spec, found));
        }
    }
    for spec in specs {
        if let Some(replies) = added.get(&route(spec)?) {
            adding.push((spec, replies));
        }
    }
    if active.is_empty() && adding.is_empty() {
        return Err(profile(failures.get(&route(first_spec)?).map(str_value).unwrap_or_default()));
    }
    let carried = defer_departure &&
        active.iter().map(|(spec, _)| *spec).chain(adding.iter().map(|(spec, _)| *spec))
            .any(|spec| spec.get("inventory_target").is_some_and(Value::truthy));
    let mut later = Vec::new();
    if carried {
        let mut granting = Vec::new();
        for (_, found) in &active {
            for &(state, ordinal) in found.keys() {
                let (first, _) = outgoing(&dialogue, &dialogue.states[state], &label)?;
                granting.push(dialogue.transitions[first + ordinal].clone());
            }
        }
        for (_, replies) in &adding {
            for &(state, ordinal) in replies.iter() {
                let (first, _) = outgoing(&dialogue, &dialogue.states[state], &label)?;
                granting.push(dialogue.transitions[first + ordinal].clone());
            }
        }
        later = following(&dialogue, &granting, &label)?;
    }
    for state_index in 0..dialogue.states.len() {
        let (first, count) = outgoing(&dialogue, &dialogue.states[state_index], &label)?;
        let originals = dialogue.transitions[first..first + count].to_vec();
        let (mut rows, mut touched) = (Vec::new(), false);
        for (ordinal, original) in originals.into_iter().enumerate() {
            let relevant: Vec<&(&Dict, &Grants)> =
                active.iter().filter(|(_, found)| found.contains_key(&(state_index, ordinal))).collect();
            let handing: Vec<&Dict> = adding
                .iter()
                .filter(|(_, replies)| replies.contains(&(state_index, ordinal)))
                .map(|(spec, _)| *spec)
                .collect();
            if relevant.is_empty() && handing.is_empty() {
                rows.push(original);
                continue;
            }
            touched = true;
            let place = format!("{label} state {state_index}");
            let flags = u32_le(&original, 0);
            let prior = if flags & 2 != 0 { string(&dialogue.triggers, u32_le(&original, 12), &label)? } else { Vec::new() };
            let text = dialogue.actions[u32_le(&original, 16) as usize].clone();
            let mut departures = if carried { departure_edits(&text, &place)? } else { Vec::new() };
            // The transfers of the routes without a grant go in front of the
            // reply's actions: the speaker hands over before anything else.
            if !handing.is_empty() {
                let mut transfers = Vec::new();
                for spec in &handing {
                    transfers.extend(callback(spec, numeric_target)?);
                    transfers.extend_from_slice(b"\r\n");
                }
                departures.push((0, 0, transfers));
            }
            if relevant.is_empty() {
                // Nothing to gate: the reply keeps its condition.
                let mut branch = original.clone();
                dialogue.actions.push(edited(&text, departures)?);
                put_u32(&mut branch, 16, dialogue.actions.len() as u32 - 1);
                rows.push(branch);
                continue;
            }
            let n = relevant.len();
            for choice in 0..(1usize << n) {
                let choices: Vec<bool> = (0..n).map(|index| choice >> (n - 1 - index) & 1 == 1).collect();
                let mut branch = original.clone();
                let mut condition = prior.clone();
                let mut edits = departures.clone();
                for ((spec, found), &selected) in relevant.iter().copied().zip(&choices) {
                    condition = joined(&condition, &guard(spec, selected)?);
                    let call = &found[&(state_index, ordinal)];
                    let grant = text[call.start..call.end].to_vec();
                    let replacement = if selected { consumed(spec)? } else { grant };
                    // Keep this beside its grant, before EscapeArea/DestroySelf.
                    let changed = if spec.get("inventory_target").is_some_and(Value::truthy) {
                        [replacement.as_slice(), b"\r\n", &callback(spec, numeric_target)?].concat()
                    } else {
                        let recipient = argument(&text, call, 1).unwrap_or(b"");
                        [mark(spec, recipient)?.as_slice(), &replacement, b"\r\n", &event(spec)?].concat()
                    };
                    edits.push((call.start, call.end, changed));
                }
                // After the campaign guards, which the recovery reads from the
                // end (guard_calls). Every variant pays, so each hands over;
                // a second call finds nothing left in the treasury.
                for (spec, _) in relevant.iter().copied() {
                    if hands_over(spec) {
                        condition = joined(&condition, &handover(spec)?);
                    }
                }
                if choices.contains(&true) && !action_refs.is_empty() {
                    edits.extend(display_edits(&text, action_refs, shown));
                }
                dialogue.triggers.push(condition);
                let actions = edited(&text, edits)?;
                dialogue.actions.push(actions);
                put_u32(&mut branch, 0, flags | 2);
                put_u32(&mut branch, 12, dialogue.triggers.len() as u32 - 1);
                put_u32(&mut branch, 16, dialogue.actions.len() as u32 - 1);
                rows.push(branch);
            }
        }
        if touched {
            let length = dialogue.transitions.len() as u32;
            put_u32(&mut dialogue.states[state_index], 4, length);
            put_u32(&mut dialogue.states[state_index], 8, rows.len() as u32);
            dialogue.transitions.extend(rows);
        }
    }
    // Keep the speaker until its queued transfer has run. ActionOverride
    // departures belong to other actors and stay intact.
    for state_index in later {
        let (first, count) = outgoing(&dialogue, &dialogue.states[state_index], &label)?;
        let originals = dialogue.transitions[first..first + count].to_vec();
        let (mut rows, mut changed) = (Vec::new(), false);
        for original in originals {
            let flags = u32_le(&original, 0);
            let text = if flags & 4 != 0 { string(&dialogue.actions, u32_le(&original, 16), &label)? } else { Vec::new() };
            let edits =
                if flags & 4 != 0 { departure_edits(&text, &format!("{label} state {state_index}"))? } else { Vec::new() };
            if edits.is_empty() {
                rows.push(original);
                continue;
            }
            let mut branch = original.clone();
            put_u32(&mut branch, 16, dialogue.actions.len() as u32);
            dialogue.actions.push(edited(&text, edits)?);
            rows.push(branch);
            changed = true;
        }
        if changed {
            let length = dialogue.transitions.len() as u32;
            put_u32(&mut dialogue.states[state_index], 4, length);
            put_u32(&mut dialogue.states[state_index], 8, rows.len() as u32);
            dialogue.transitions.extend(rows);
        }
    }
    Ok(dialogue.render())
}

/// `_valid_refs`: allocated text for exactly the route's original strrefs, as
/// the game numbers them.
fn valid_refs(refs: Option<&BTreeMap<i64, i64>>, resource: &str, game: Option<&str>) -> Result<bool> {
    let Some(refs) = refs else { return Ok(false) };
    let expected = game_text_refs(resource, game)?;
    Ok(refs.len() == expected.len()
        && expected.iter().all(|(old, _)| refs.contains_key(old))
        && refs.values().all(|value| (0..0xFFFF_FFFF).contains(value)))
}

/// Gate each cloned start state and its clone on the route's flag
/// (`_gate_start_states`).
///
/// The engine tries start states in the order of their conditions in the
/// state trigger table, so the gated pair takes the original condition's
/// place. When every other start condition comes before the cloned ones,
/// appending the pairs keeps that order (the layout earlier exports used).
/// `starts` are the cloned states (among the first `original_count`) with a
/// start condition, in state order.
fn gate_start_states(
    dlg: &mut Dialogue,
    original_count: usize,
    starts: &[(usize, u32)],
    clones: &BTreeMap<usize, usize>,
    flag: &str,
) -> Result<()> {
    if starts.is_empty() {
        return Ok(());
    }
    let cloned: BTreeSet<usize> = starts.iter().map(|&(index, _)| index).collect();
    let entries: Vec<u32> = starts.iter().map(|&(_, entry)| entry).collect();
    let others: BTreeSet<u32> = (0..original_count)
        .filter(|index| !cloned.contains(index))
        .map(|index| u32_le(&dlg.states[index], 12))
        .filter(|&entry| entry != 0xFFFF_FFFF)
        .collect();
    let old = dlg.state_triggers.clone();
    let gate = |entry: u32, selected: bool| joined(&old[entry as usize], &guard_line(selected, flag));
    let increasing = entries.windows(2).all(|pair| pair[0] < pair[1]);
    if increasing && entries.iter().all(|entry| others.iter().all(|other| entry > other)) {
        for &(index, entry) in starts {
            for (selected, target) in [(false, index), (true, clones[&index])] {
                dlg.state_triggers.push(gate(entry, selected));
                let position = dlg.state_triggers.len() as u32 - 1;
                put_u32(&mut dlg.states[target], 12, position);
            }
        }
        return Ok(());
    }
    let (mut table, mut moved, mut pairs) = (Vec::new(), HashMap::new(), HashMap::new());
    for (position, text) in old.iter().enumerate() {
        let position = position as u32;
        if others.contains(&position) || !entries.contains(&position) {
            moved.insert(position, table.len() as u32);
            table.push(text.clone());
        }
        for &(index, entry) in starts {
            if entry == position {
                pairs.insert(index, (table.len() as u32, table.len() as u32 + 1));
                table.push(gate(entry, false));
                table.push(gate(entry, true));
            }
        }
    }
    for index in 0..original_count {
        let entry = u32_le(&dlg.states[index], 12);
        if let Some(&(unselected, selected)) = pairs.get(&index) {
            put_u32(&mut dlg.states[index], 12, unselected);
            put_u32(&mut dlg.states[clones[&index]], 12, selected);
        } else if entry != 0xFFFF_FFFF {
            let position = *moved.get(&entry).ok_or_else(|| Error::new(ErrorKind::Key, entry.to_string()))?;
            put_u32(&mut dlg.states[index], 12, position);
        }
    }
    dlg.state_triggers = table;
    Ok(())
}

/// Text-route variant of a dialogue (`_patch_text_dialogue`): the states and
/// replies that show the text that names the reward get a selected copy with
/// the allocated text.
pub fn patch_text_dialogue(
    data: &[u8],
    resource: &str,
    refs: Option<&BTreeMap<i64, i64>>,
    defer_departure: bool,
    numeric_target: bool,
    naming: Naming<'_>,
) -> Result<Vec<u8>> {
    if !valid_refs(refs, resource, naming.game)? {
        return Err(profile(format!("{} requires allocated original reward text", label(resource))));
    }
    let refs = refs.expect("valid refs");
    let specs = resource_specs(resource, naming)?;
    if specs.len() != 1 {
        return Err(profile("A text route needs exactly one source flag"));
    }
    let flag = text(&specs[0], "selected_global")?;
    let action_refs: Vec<(i64, i64)> = action_text_refs(resource).iter().map(|old| (*old, refs[old])).collect();
    let mut shown = BTreeSet::new();
    let patched = patch_dialogue_in(
        data,
        &specs,
        &action_refs,
        defer_departure,
        numeric_target,
        None,
        None,
        None,
        Some(&mut shown),
    )?;
    let mut dlg = read_dialogue(&patched, resource)?;
    let label = label(resource);
    let original_count = dlg.states.len();
    let mut clones: BTreeMap<usize, usize> = BTreeMap::new();
    let mut found: BTreeSet<i64> = shown;
    let mut starts: Vec<(usize, u32)> = Vec::new();
    let lookup = |value: u32| refs.get(&(value as i64)).map_or(value, |new| *new as u32);
    for index in 0..original_count {
        let state = dlg.states[index].clone();
        let (text_ref, entry) = (u32_le(&state, 0), u32_le(&state, 12));
        let (first, count) = outgoing(&dlg, &state, &label)?;
        let transitions = dlg.transitions[first..first + count].to_vec();
        let mut affected: BTreeSet<i64> = transitions
            .iter()
            .flat_map(|row| {
                let shown = TEXT_FIELDS.iter().filter(move |(bit, _)| u32_le(row, 0) & bit != 0);
                shown.map(move |&(_, offset)| u32_le(row, offset) as i64)
            })
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
            for &(bit, offset) in &TEXT_FIELDS {
                if u32_le(row, 0) & bit != 0 {
                    let old = u32_le(row, offset);
                    put_u32(row, offset, lookup(old));
                }
            }
        }
        put_u32(&mut clone, 4, dlg.transitions.len() as u32);
        put_u32(&mut clone, 8, rows.len() as u32);
        dlg.transitions.extend(rows);
        if entry != 0xFFFF_FFFF {
            string(&dlg.state_triggers, entry, &label)?;
            starts.push((index, entry));
        }
        clones.insert(index, dlg.states.len());
        dlg.states.push(clone);
    }
    gate_start_states(&mut dlg, original_count, &starts, &clones, &flag)?;
    let all: BTreeSet<i64> = refs.keys().copied().collect();
    if found != all {
        let missing: Vec<String> = all.difference(&found).map(i64::to_string).collect();
        return Err(profile(format!("{label} no longer shows the text that names the reward ({})", missing.join(", "))));
    }
    // Redirect each incoming internal edge, including paths starting at an
    // unchanged external entry (the temple messengers enter sculptor27/28).
    // Cached cross-dialogue links never enter a changed text state directly.
    let own_name = dlg.name.to_ascii_lowercase();
    for index in 0..dlg.states.len() {
        let (first, count) = outgoing(&dlg, &dlg.states[index], &label)?;
        let mut rows = Vec::new();
        for transition in dlg.transitions[first..first + count].iter().cloned() {
            let flags = u32_le(&transition, 0);
            let destination = u32_le(&transition, 28) as usize;
            let internal = link_name(&transition).to_ascii_lowercase() == own_name;
            if flags & 8 != 0 || !internal || !clones.contains_key(&destination) {
                rows.push(transition);
                continue;
            }
            let prior = if flags & 2 != 0 { string(&dlg.triggers, u32_le(&transition, 12), &label)? } else { Vec::new() };
            for selected in [false, true] {
                let mut row = transition.clone();
                dlg.triggers.push(joined(&prior, &guard_line(selected, &flag)));
                put_u32(&mut row, 0, flags | 2);
                put_u32(&mut row, 12, dlg.triggers.len() as u32 - 1);
                if selected {
                    put_u32(&mut row, 28, clones[&destination] as u32);
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

/// The quoted names our patch writes, and the queued departure.
fn markers(specs: &[Dict]) -> Result<Vec<Vec<u8>>> {
    let mut names = BTreeSet::new();
    for spec in specs {
        for key in ["selected_global", "consumed_global", "event_global", "recipient_local"] {
            let value = field(spec, key)?;
            if value.truthy() {
                names.insert(str_value(value));
            }
        }
    }
    let mut markers: Vec<Vec<u8>> = names.into_iter().map(|name| format!("\"{name}\"").into_bytes()).collect();
    markers.push(DEPARTURE.to_vec());
    // A route without a grant leaves only its transfer, whatever row it names.
    if specs.iter().any(ends_a_job) {
        markers.push(TRANSFER.to_vec());
    }
    Ok(markers)
}

pub(crate) fn carries_patch(dialogue: &Dialogue, specs: &[Dict]) -> Result<bool> {
    let markers = markers(specs)?;
    Ok(dialogue
        .triggers
        .iter()
        .chain(&dialogue.actions)
        .any(|text| markers.iter().any(|marker| find_bytes(text, marker, 0).is_some())))
}

pub(crate) fn changed_patch(resource: &str) -> Error {
    profile(format!("{} carries a changed copy of the reward route's own patch", label(resource)))
}

/// A text route's original bytes and the text refs its patch used.
pub type TextOriginal = (Vec<u8>, BTreeMap<i64, i64>);

/// The original and text refs of a text route this adapter patched
/// (`_text_original`); None if unpatched.
pub fn text_original(data: &[u8], resource: &str, naming: Naming<'_>) -> Result<Option<TextOriginal>> {
    let specs = resource_specs(resource, naming)?;
    if data.len() < 12 || &data[data.len() - 12..data.len() - 4] != TEXT_TRAILER {
        if carries_patch(&read_dialogue(data, resource)?, &specs)? {
            return Err(changed_patch(resource));
        }
        return Ok(None);
    }
    let entries = text_refs(resource).ok_or_else(|| Error::key(resource))?.len();
    let length = u32_le(data, data.len() - 4) as usize;
    if length != 60 + entries * 8 || length > data.len() - 12 {
        return Err(changed_patch(resource));
    }
    let trailer = &data[data.len() - 12 - length..data.len() - 12];
    let (size, header, count) = (u32_le(trailer, 0) as usize, &trailer[4..56], u32_le(trailer, 56) as usize);
    if size < 52 || size > data.len() - length - 12 || count != entries {
        return Err(changed_patch(resource));
    }
    let original = [header, &data[52..size]].concat();
    let mut refs = BTreeMap::new();
    for index in 0..count {
        refs.insert(u32_le(trailer, 60 + index * 8) as i64, u32_le(trailer, 64 + index * 8) as i64);
    }
    if !valid_refs(Some(&refs), resource, naming.game)? {
        return Err(changed_patch(resource));
    }
    // Accept only the exact prior inventory callback patches on a rescan.
    // TRGENI had exports both before and after its departure-order fix.
    let mut variants = vec![(true, true)];
    if specs[0].get("inventory_target").is_some_and(Value::truthy) {
        let departures: &[bool] = if resource == "dlg:trgeni01" { &[true, false] } else { &[true] };
        variants.extend(departures.iter().map(|&departure| (departure, false)));
    }
    for (defer_departure, numeric_target) in variants {
        match patch_text_dialogue(&original, resource, Some(&refs), defer_departure, numeric_target, naming) {
            Ok(patched) if patched == data => return Ok(Some((original, refs))),
            Ok(_) => {}
            Err(error) if error.kind == ErrorKind::Profile => break,
            Err(error) => return Err(error),
        }
    }
    Err(changed_patch(resource))
}

/// A campaign guard of a reply trigger: the call, its route and whether it
/// is the selected copy.
type GuardCall<'a> = (Call, &'a Dict, bool);

/// The trailing campaign guards of a reply trigger, in order; None if
/// unreadable.
fn guard_calls<'a>(text: &[u8], routes: &[(Vec<u8>, &'a Dict)]) -> Option<Vec<GuardCall<'a>>> {
    let found = calls(text)?;
    let mut guards = Vec::new();
    for call in found.into_iter().rev() {
        let name = call.name.to_ascii_lowercase();
        let flag = string_argument(text, &call, 0);
        let spec = flag.and_then(|flag| routes.iter().rev().find(|(key, _)| key.as_slice() == flag).map(|(_, spec)| *spec));
        let Some(spec) = spec else { break };
        if !(name == b"global" || name == b"!global")
            || call.args.len() != 3
            || string_argument(text, &call, 1) != Some(b"GLOBAL")
            || integer_argument(text, &call, 2, None) != Some(1)
        {
            break;
        }
        let selected = name == b"global";
        guards.push((call, spec, selected));
    }
    guards.reverse();
    Some(guards)
}

/// `re.findall(rb'ActionOverride\([^\r\n]*?,SetGlobal\("<local>","LOCALS",1\)\)\r\n', text)`.
fn local_marks(text: &[u8], local: &[u8]) -> Vec<Vec<u8>> {
    const HEAD: &[u8] = b"ActionOverride(";
    let tail = [b",SetGlobal(\"" as &[u8], local, b"\",\"LOCALS\",1))\r\n"].concat();
    let mut marks = Vec::new();
    let mut at = 0;
    while at < text.len() {
        if text[at..].starts_with(HEAD) {
            let mut cursor = at + HEAD.len();
            let end = loop {
                if text[cursor..].starts_with(&tail) {
                    break Some(cursor + tail.len());
                }
                if cursor >= text.len() || matches!(text[cursor], b'\r' | b'\n') {
                    break None;
                }
                cursor += 1;
            };
            if let Some(end) = end {
                marks.push(text[at..end].to_vec());
                at = end;
                continue;
            }
        }
        at += 1;
    }
    marks
}

/// Undo one route's additions around its grant in an unselected reply.
fn strip_grant(text: &[u8], spec: &Dict, resource: &str) -> Result<Vec<u8>> {
    let additions = if spec.get("inventory_target").is_some_and(Value::truthy) {
        vec![[b"\r\n" as &[u8], &callback(spec, true)?].concat()]
    } else {
        let local = text_field(spec, "recipient_local", "encode")?;
        let marks = local_marks(text, local.as_bytes());
        if marks.len() != 1 {
            return Err(changed_patch(resource));
        }
        vec![marks.into_iter().next().expect("one mark"), [b"\r\n" as &[u8], &event(spec)?].concat()]
    };
    let mut text = text.to_vec();
    for addition in additions {
        if count_bytes(&text, &addition) != 1 {
            return Err(changed_patch(resource));
        }
        text = replace_bytes(&text, &addition, b"", Some(1));
    }
    Ok(text)
}

/// A dialogue reply as the engine reads it.
#[derive(PartialEq, Eq)]
struct LogicalRow {
    flags: u32,
    reply: u32,
    journal: u32,
    trigger: Option<Vec<u8>>,
    action: Option<Vec<u8>>,
    next_dialogue: Vec<u8>,
    next_state: u32,
}

/// A dialogue's content as the engine reads it, without its table layout.
#[derive(PartialEq, Eq)]
pub(crate) struct Logical {
    signature: Vec<u8>,
    flags: Vec<u8>,
    states: Vec<(u32, Option<Vec<u8>>, Vec<LogicalRow>)>,
}

pub(crate) fn logical(data: &[u8], resource: &str) -> Result<Logical> {
    let dialogue = read_dialogue(data, resource)?;
    let label = label(resource);
    let mut states = Vec::new();
    for state in &dialogue.states {
        let (first, count) = outgoing(&dialogue, state, &label)?;
        let mut rows = Vec::new();
        for row in &dialogue.transitions[first..first + count] {
            let flags = u32_le(row, 0);
            rows.push(LogicalRow {
                flags,
                reply: u32_le(row, 4),
                journal: u32_le(row, 8),
                trigger: if flags & 2 != 0 { Some(string(&dialogue.triggers, u32_le(row, 12), &label)?) } else { None },
                action: if flags & 4 != 0 { Some(string(&dialogue.actions, u32_le(row, 16), &label)?) } else { None },
                next_dialogue: row[20..28].to_vec(),
                next_state: u32_le(row, 28),
            });
        }
        let entry = u32_le(state, 12);
        let condition = if entry == 0xFFFF_FFFF { None } else { Some(string(&dialogue.state_triggers, entry, &label)?) };
        states.push((u32_le(state, 0), condition, rows));
    }
    Ok(Logical { signature: data[..8].to_vec(), flags: data[48..52].to_vec(), states })
}

/// Undo this adapter's patch of a dialogue without a trailer
/// (`_static_original`); None if unpatched.
///
/// The result reads as the original did. Patching it again must give the
/// same dialogue as `data`, or `data` is not exactly this adapter's patch.
fn static_original(data: &[u8], resource: &str, naming: Naming<'_>) -> Result<Option<Vec<u8>>> {
    let specs = resource_specs(resource, naming)?;
    let dialogue = read_dialogue(data, resource)?;
    if !carries_patch(&dialogue, &specs)? {
        return Ok(None);
    }
    let attempt = unpatched_dialogue(dialogue, &specs, resource).and_then(|original| {
        let same = logical(&patch_dialogue(&original, &specs, &[], true, true, None)?, resource)? == logical(data, resource)?;
        Ok((original, same))
    });
    match attempt {
        Ok((original, true)) => Ok(Some(original)),
        Ok((_, false)) => Err(changed_patch(resource)),
        Err(error) if error.kind == ErrorKind::Profile => Err(changed_patch(resource)),
        Err(error) => Err(error),
    }
}

/// The handover condition of one of these routes that `trigger` ends with.
fn handover_tail(trigger: &[u8], routes: &[(Vec<u8>, &Dict)]) -> Result<Option<Vec<u8>>> {
    for (_, spec) in routes {
        if hands_over(spec) {
            let line = handover(spec)?;
            if trigger.ends_with(&line) {
                return Ok(Some(line));
            }
        }
    }
    Ok(None)
}

pub(crate) fn unpatched_dialogue(mut dialogue: Dialogue, specs: &[Dict], resource: &str) -> Result<Vec<u8>> {
    let label = label(resource);
    let mut routes: Vec<(Vec<u8>, &Dict)> = Vec::new();
    for spec in specs {
        if field(spec, "selected_global")?.truthy() {
            routes.push((text_field(spec, "selected_global", "encode")?.as_bytes().to_vec(), spec));
        }
    }
    // The transfers of the routes without a grant, in front of a reply's actions.
    let mut transfers = Vec::new();
    for spec in specs.iter().filter(|spec| ends_a_job(spec)) {
        transfers.push([callback(spec, true)?.as_slice(), b"\r\n"].concat());
    }
    for state_index in 0..dialogue.states.len() {
        let (first, count) = outgoing(&dialogue, &dialogue.states[state_index], &label)?;
        let rows = dialogue.transitions[first..first + count].to_vec();
        let mut restored = Vec::new();
        let mut at = 0;
        while at < rows.len() {
            // A gated reply became 2^n replies, the first one with every
            // route unselected; it keeps each original grant.
            let mut row = rows[at].clone();
            let (flags, action_id) = (u32_le(&row, 0), u32_le(&row, 16));
            let mut trigger = if flags & 2 != 0 { string(&dialogue.triggers, u32_le(&row, 12), &label)? } else { Vec::new() };
            let mut action = if flags & 4 != 0 { string(&dialogue.actions, action_id, &label)? } else { Vec::new() };
            // The handover conditions follow the guards of their routes.
            let mut handed = Vec::new();
            if flags & 2 != 0 {
                while let Some(tail) = handover_tail(&trigger, &routes)? {
                    trigger.truncate(trigger.len() - tail.len());
                    handed.insert(0, tail);
                }
            }
            let guards = if flags & 2 != 0 { guard_calls(&trigger, &routes).unwrap_or_default() } else { Vec::new() };
            let copies = if guards.len() >= usize::BITS as usize - 1 { usize::MAX } else { 1usize << guards.len() };
            let mut expected = Vec::new();
            for (_, spec, _) in &guards {
                if hands_over(spec) {
                    expected.push(handover(spec)?);
                }
            }
            if guards.iter().any(|(_, _, selected)| *selected) || at.saturating_add(copies) > rows.len() || handed != expected {
                return Err(changed_patch(resource));
            }
            at += copies;
            if let Some((first_guard, _, _)) = guards.first() {
                let prior = trigger[..first_guard.start].to_vec();
                for (_, spec, _) in &guards {
                    action = strip_grant(&action, spec, resource)?;
                }
                if !prior.is_empty() {
                    dialogue.triggers.push(prior);
                    put_u32(&mut row, 12, dialogue.triggers.len() as u32 - 1);
                } else {
                    put_u32(&mut row, 0, flags & !2);
                }
            }
            // Every copy, in any order: one that stayed would make an original
            // with it.
            while let Some(transfer) = transfers.iter().find(|transfer| action.starts_with(transfer)) {
                action.drain(..transfer.len());
            }
            action = replace_bytes(&action, DEPARTURE, b"DestroySelf()", None);
            if flags & 4 != 0 && action != dialogue.actions[action_id as usize] {
                dialogue.actions.push(action);
                put_u32(&mut row, 16, dialogue.actions.len() as u32 - 1);
            }
            restored.push(row);
        }
        if restored != rows {
            let length = dialogue.transitions.len() as u32;
            put_u32(&mut dialogue.states[state_index], 4, length);
            put_u32(&mut dialogue.states[state_index], 8, restored.len() as u32);
            dialogue.transitions.extend(restored);
        }
    }
    Ok(dialogue.render())
}

// ---------------------------------------------------------------- scripts

fn marker_action(grant: &Action, spec: &Dict, newline: &[u8]) -> Result<Vec<u8>> {
    // In compiled ActionOverride the action's first OB is the actor override.
    // Copy GiveItemCreate's second OB verbatim, retaining every object selector.
    let local = match field(spec, "recipient_local")? {
        Value::Str(name) => name.clone(),
        other => return Err(Error::type_error(format!("object of type '{}' has no len()", other.type_name()))),
    };
    let marker = replace_bytes(&global_action(&local, newline)?, b"\"GLOBAL", b"\"LOCALS", Some(1));
    let empty: &[u8] = b"0 0 0 0 0 0 0 0 0 0 0 0 \"\"";
    let object = [grant.objects[1].as_slice(), b"OB", newline].concat();
    Ok(replace_bytes(&marker, &[empty, b"OB", newline].concat(), &object, Some(1)))
}

/// A compiled action with its first integer parameter replaced.
fn with_integer(action: &[u8], newline: &[u8], value: Option<i64>) -> Vec<u8> {
    let separator = [b"OB", newline].concat();
    let start = rfind_bytes(action, &separator, 0, action.len()).expect("a compiled action") + separator.len();
    let mut end = start + usize::from(action.get(start) == Some(&b'-'));
    while action.get(end).is_some_and(u8::is_ascii_digit) {
        end += 1;
    }
    let shown = value.map_or_else(|| "None".to_string(), |value| value.to_string());
    [&action[..start], shown.as_bytes(), &action[end..]].concat()
}

/// `(?:0 )+""`: an object that names no actor.
fn empty_object(object: &[u8]) -> bool {
    let mut rest = object;
    let mut zeros = 0;
    while let Some(next) = rest.strip_prefix(b"0 ") {
        rest = next;
        zeros += 1;
    }
    zeros > 0 && rest == b"\"\""
}

/// A block that gives a route's item: its parts, its grants and the actions
/// that show the text naming the reward.
struct ScriptGrant {
    head: Vec<u8>,
    conditions: Vec<u8>,
    responses: Vec<u8>,
    grants: Vec<Action>,
    displays: Vec<Action>,
}

/// A reward container creates the item in itself; others give it.
fn script_grant_opcode(spec: &Dict) -> Result<i64> {
    Ok(if field(spec, "recipient_local")?.is_null() { 82 } else { 140 })
}

/// Each block that gives the route's item (`_script_grants`).
///
/// A block runs one of its alternative responses, so each response may give
/// the item once, with the route's grant.
fn script_grants(script: &Script, spec: &Dict, label: &str) -> Result<BTreeMap<usize, ScriptGrant>> {
    let item_name = text_field(spec, "item", "encode")?;
    let item = item_name.to_ascii_lowercase().into_bytes();
    let container = field(spec, "recipient_local")?.is_null();
    let opcode = script_grant_opcode(spec)?;
    let shown = upper(item_name);
    // The script text routes are BG2's, which every game numbers alike (text_refs).
    let refs = text_refs(&text(spec, "resource")?).unwrap_or(&[]);
    let mut found = BTreeMap::new();
    for (index, block) in script.blocks.iter().enumerate() {
        if !names(block, item_name) {
            continue;
        }
        let place = format!("{label} block {index}");
        let (head, conditions, responses) = block_parts(block, script.newline)?;
        let actions = block_actions(&responses, script.newline)?;
        let displays: Vec<Action> =
            actions.iter().filter(|action| refs.iter().any(|(old, _)| *old == action.integer)).cloned().collect();
        let mut grants = Vec::new();
        for (first, last) in response_spans(&responses, script.newline)? {
            let mut given = Vec::new();
            for action in &actions {
                let Some((name, string)) = giving_opcode(action.opcode) else { continue };
                if !(first <= action.start && action.start < last) || action.strings[string].to_ascii_lowercase() != item {
                    continue;
                }
                if action.opcode != opcode {
                    return Err(profile(format!("{place} also gives {shown} with {name}(), which the route cannot gate")));
                }
                if !container && !empty_object(&action.objects[0]) {
                    // Its recipient means what it means to the other actor.
                    return Err(profile(format!(
                        "{place} gives {shown} through ActionOverride(), whose recipient the route cannot mark"
                    )));
                }
                given.push(action.clone());
            }
            if given.len() > 1 {
                return Err(profile(format!("{place} gives {shown} twice in one response")));
            }
            grants.extend(given);
        }
        if !grants.is_empty() {
            found.insert(index, ScriptGrant { head, conditions, responses, grants, displays });
        }
    }
    if found.is_empty() {
        return Err(profile(format!("{label} has no block that gives {shown}")));
    }
    if !refs.is_empty() && !found.values().any(|row| !row.displays.is_empty()) {
        let mut olds: Vec<i64> = refs.iter().map(|(old, _)| *old).collect();
        olds.sort_unstable();
        let missing: Vec<String> = olds.iter().map(i64::to_string).collect();
        return Err(profile(format!("{label} no longer shows the text that names the reward ({})", missing.join(", "))));
    }
    Ok(found)
}

/// Gate every block that gives the route's item (`_patch_script`);
/// `text_ref` is the allocated text of a route that shows the reward text.
pub fn patch_script(data: &[u8], spec: &Dict, text_ref: Option<i64>) -> Result<Vec<u8>> {
    let resource = text(spec, "resource")?;
    let label = label(&resource);
    if text_refs(&resource).is_some() && !text_ref.is_some_and(|value| (0..0xFFFF_FFFF).contains(&value)) {
        return Err(profile(format!("{label} requires allocated original reward text")));
    }
    let (script, grants) = naming(&label, || {
        let script = script_blocks(data)?;
        let grants = script_grants(&script, spec, &label)?;
        Ok((script, grants))
    })?;
    let newline = script.newline;
    let container = field(spec, "recipient_local")?.is_null();
    let event = global_action(text_field(spec, "event_global", "encode")?, newline)?;
    let mut blocks = script.blocks.clone();
    for (index, row) in grants {
        let mut variants = Vec::new();
        for selected in [false, true] {
            let mut edits = Vec::new();
            for grant in &row.grants {
                let mark = if container { Vec::new() } else { marker_action(grant, spec, newline)? };
                let replacement = if selected {
                    global_action(text_field(spec, "consumed_global", "encode")?, newline)?
                } else {
                    row.responses[grant.start..grant.end].to_vec()
                };
                edits.push((grant.start, grant.end, [mark.as_slice(), &replacement, &event].concat()));
            }
            if selected {
                for display in &row.displays {
                    let shown = with_integer(&row.responses[display.start..display.end], newline, text_ref);
                    edits.push((display.start, display.end, shown));
                }
            }
            let guard = global_guard(text_field(spec, "selected_global", "encode")?, selected, newline)?;
            variants.extend(
                [row.head.as_slice(), b"CO", newline, &row.conditions, &guard, b"CO", newline, &edited(&row.responses, edits)?]
                    .concat(),
            );
        }
        blocks[index] = variants;
    }
    Ok([script.prefix, blocks.concat(), script.suffix].concat())
}

/// Gate the blocks that give each route's item, one route after the other
/// (`_patch_scripts`).
///
/// A route whose grant is not recognised keeps its blocks as they are; its
/// reason goes to `reasons`. With no route recognised, nothing is prepared.
pub fn patch_scripts(data: &[u8], specs: &[Dict], text_ref: Option<i64>, reasons: Option<&mut Dict>) -> Result<Vec<u8>> {
    let mut data = data.to_vec();
    let mut failures = Dict::new();
    for spec in specs {
        match patch_script(&data, spec, text_ref) {
            Ok(patched) => data = patched,
            Err(error) if error.kind == ErrorKind::Profile => failures.insert(route(spec)?, error.message),
            Err(error) => return Err(error),
        }
    }
    if let Some(reasons) = reasons {
        for (key, reason) in failures.iter() {
            reasons.insert(key, reason.clone());
        }
    }
    if failures.len() == specs.len() {
        let first = specs.first().ok_or_else(index_error)?;
        return Err(profile(failures.get(&route(first)?).map(str_value).unwrap_or_default()));
    }
    Ok(data)
}

/// The original of a reward script, its text ref and whether we patched it
/// (`_script_original`).
///
/// The routes' patches are undone in the reverse order of [`patch_scripts`];
/// patching the result again must give `data` exactly.
pub fn script_original(data: &[u8], specs: &[Dict]) -> Result<(Vec<u8>, Option<i64>, bool)> {
    let resource = text(specs.first().ok_or_else(index_error)?, "resource")?;
    let newline: &[u8] = if find_bytes(data, b"\r\n", 0).is_some() { b"\r\n" } else { b"\n" };
    let mut patched = Vec::new();
    for spec in specs {
        let flag = text_field(spec, "selected_global", "encode")?;
        if find_bytes(data, &[b"\"GLOBAL", flag.as_bytes(), b"\""].concat(), 0).is_some() {
            patched.push(spec);
        }
    }
    if patched.is_empty() {
        return Ok((data.to_vec(), None, false));
    }
    let (mut original, mut numbers) = (data.to_vec(), BTreeSet::new());
    for spec in patched.into_iter().rev() {
        let undone = (|| {
            let flag = text_field(spec, "selected_global", "encode")?;
            unpatch_script(&original, spec, newline, &global_guard(flag, true, newline)?, &global_guard(flag, false, newline)?)
        })();
        match undone {
            Ok((restored, found)) => {
                original = restored;
                numbers.extend(found);
            }
            Err(error) if error.kind == ErrorKind::Profile => return Err(changed_patch(&resource)),
            Err(error) => return Err(error),
        }
    }
    // The allocated text is one of the selected copies' numbers; only a
    // possible strref can be it.
    let candidates: Vec<Option<i64>> = if text_refs(&resource).is_some() {
        numbers.into_iter().filter(|number| (0..0xFFFF_FFFF).contains(number)).map(Some).collect()
    } else {
        vec![None]
    };
    for reference in candidates {
        match patch_scripts(&original, specs, reference, None) {
            Ok(patched) if patched == data => return Ok((original, reference, true)),
            Ok(_) => {}
            Err(error) if error.kind == ErrorKind::Profile => break,
            Err(error) => return Err(error),
        }
    }
    Err(changed_patch(&resource))
}

/// Drop the selected copies of gated blocks and undo the unselected ones
/// (`_unpatched_script`).
///
/// Also returns the integers of the selected copies' actions, among them the
/// allocated reward text.
fn unpatch_script(
    data: &[u8],
    spec: &Dict,
    newline: &[u8],
    selected: &[u8],
    unselected: &[u8],
) -> Result<(Vec<u8>, BTreeSet<i64>)> {
    let resource = text(spec, "resource")?;
    let script = script_blocks(data)?;
    let (mut restored, mut numbers) = (Vec::new(), BTreeSet::new());
    let container = field(spec, "recipient_local")?.is_null();
    let event = global_action(text_field(spec, "event_global", "encode")?, newline)?;
    for block in &script.blocks {
        if find_bytes(block, selected, 0).is_some() {
            let (_, _, responses) = block_parts(block, newline)?;
            numbers.extend(block_actions(&responses, newline)?.iter().map(|action| action.integer));
            continue;
        }
        let mut block = block.clone();
        if find_bytes(&block, unselected, 0).is_some() {
            let (head, conditions, responses) = block_parts(&replace_bytes(&block, unselected, b"", Some(1)), newline)?;
            let item = text_field(spec, "item", "encode")?.to_ascii_lowercase().into_bytes();
            let opcode = script_grant_opcode(spec)?;
            let grants: Vec<Action> = block_actions(&responses, newline)?
                .into_iter()
                .filter(|action| action.opcode == opcode && action.strings[0].to_ascii_lowercase() == item)
                .collect();
            if grants.is_empty() {
                return Err(changed_patch(&resource));
            }
            // Each grant kept its marker before it and the event after it.
            let (mut parts, mut at) = (Vec::new(), 0);
            for grant in &grants {
                let mark = if container { Vec::new() } else { marker_action(grant, spec, newline)? };
                let (Some(start), end) = (grant.start.checked_sub(mark.len()), grant.end + event.len()) else {
                    return Err(changed_patch(&resource));
                };
                if start < at || responses[start..grant.start] != mark[..] || responses.get(grant.end..end) != Some(&event[..]) {
                    return Err(changed_patch(&resource));
                }
                parts.extend_from_slice(&responses[at..start]);
                parts.extend_from_slice(&responses[grant.start..grant.end]);
                at = end;
            }
            block = [head.as_slice(), b"CO", newline, &conditions, b"CO", newline, &parts, &responses[at..]].concat();
        }
        restored.push(block);
    }
    Ok(([script.prefix, restored.concat(), script.suffix].concat(), numbers))
}

/// A compiled NoAction() (`_no_action`).
fn no_action(newline: &[u8]) -> Vec<u8> {
    let object = [b"0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB" as &[u8], newline].concat();
    let parameters: &[u8] = b"0 0 0 0 0\"\" \"\" AC";
    [b"AC" as &[u8], newline, b"0OB", newline, &object, b"OB", newline, &object, b"OB", newline, &object, parameters, newline]
        .concat()
}

/// Gate the route's DestroyItem of its item (`_gate_destroys`): a block that
/// destroys it (itself or through ActionOverride(), where the moved reward
/// may be too) becomes two, the original while the route's grant is
/// unchanged and one with NoAction() instead of each of those destroys while
/// it is moved. Conditions may test the item; any other action with it could
/// take a moved reward, so the route cannot be used.
fn gate_destroys(data: &[u8], spec: &Dict) -> Result<Vec<u8>> {
    let label = label(&text(spec, "resource")?);
    let item_name = text_field(spec, "item", "encode")?;
    let item = item_name.to_ascii_lowercase().into_bytes();
    naming(&label, || {
        let script = script_blocks(data)?;
        let newline = script.newline;
        let mut blocks = script.blocks.clone();
        for (index, block) in script.blocks.iter().enumerate() {
            if !names(block, item_name) {
                continue;
            }
            let (head, conditions, responses) = block_parts(block, newline)?;
            let using: Vec<Action> = block_actions(&responses, newline)?
                .into_iter()
                .filter(|action| action.strings.iter().any(|text| text.to_ascii_lowercase() == item))
                .collect();
            if using.iter().any(|action| action.opcode != 169 || action.strings[0].to_ascii_lowercase() != item) {
                return Err(profile(format!(
                    "{label} block {index} uses {} in an action other than DestroyItem(), which the route cannot gate",
                    upper(item_name)
                )));
            }
            if using.is_empty() {
                continue;
            }
            let mut variants = Vec::new();
            for selected in [false, true] {
                let edits: Vec<Edit> = if selected {
                    using.iter().map(|action| (action.start, action.end, no_action(newline))).collect()
                } else {
                    Vec::new()
                };
                let guard = global_guard(text_field(spec, "selected_global", "encode")?, selected, newline)?;
                variants.extend(
                    [head.as_slice(), b"CO", newline, &conditions, &guard, b"CO", newline, &edited(&responses, edits)?].concat(),
                );
            }
            blocks[index] = variants;
        }
        Ok([script.prefix, blocks.concat(), script.suffix].concat())
    })
}

/// Gate each route's DestroyItem of its item ([`DESTROY_GATES`],
/// `_patch_destroys`), one route after the other; everything else stays as
/// found. A route whose destroys cannot be gated keeps its blocks as they
/// are; its reason goes to `reasons`. With no route gateable, nothing is
/// prepared.
pub fn patch_destroys(data: &[u8], specs: &[Dict], reasons: Option<&mut Dict>) -> Result<Vec<u8>> {
    let mut data = data.to_vec();
    let mut failures = Dict::new();
    for spec in specs {
        match gate_destroys(&data, spec) {
            Ok(patched) => data = patched,
            Err(error) if error.kind == ErrorKind::Profile => failures.insert(route(spec)?, error.message),
            Err(error) => return Err(error),
        }
    }
    if let Some(reasons) = reasons {
        for (key, reason) in failures.iter() {
            reasons.insert(key, reason.clone());
        }
    }
    if failures.len() == specs.len() {
        let first = specs.first().ok_or_else(index_error)?;
        return Err(profile(failures.get(&route(first)?).map(str_value).unwrap_or_default()));
    }
    Ok(data)
}

/// The script our destroy gates were made from, and whether `data` carries
/// them (`_destroys_original`); patching the result again must give `data`
/// exactly.
fn destroys_original(data: &[u8], specs: &[Dict]) -> Result<(Vec<u8>, bool)> {
    let resource = text(specs.first().ok_or_else(index_error)?, "resource")?;
    let mut patched = Vec::new();
    for spec in specs {
        let flag = text_field(spec, "selected_global", "encode")?;
        if find_bytes(data, &[b"\"GLOBAL", flag.as_bytes(), b"\""].concat(), 0).is_some() {
            patched.push(spec);
        }
    }
    if patched.is_empty() {
        return Ok((data.to_vec(), false));
    }
    let attempt = (|| -> Result<(bool, Vec<u8>)> {
        let mut original = data.to_vec();
        naming(&label(&resource), || {
            for spec in patched.iter().rev() {
                let script = script_blocks(&original)?;
                let flag = text_field(spec, "selected_global", "encode")?;
                let (selected, unselected) =
                    (global_guard(flag, true, script.newline)?, global_guard(flag, false, script.newline)?);
                let kept: Vec<Vec<u8>> = script
                    .blocks
                    .iter()
                    .filter(|block| find_bytes(block, &selected, 0).is_none())
                    .map(|block| replace_bytes(block, &unselected, b"", Some(1)))
                    .collect();
                original = [script.prefix, kept.concat(), script.suffix].concat();
            }
            Ok(())
        })?;
        Ok((patch_destroys(&original, specs, None)? == data, original))
    })();
    match attempt {
        Ok((true, original)) => Ok((original, true)),
        Ok((false, _)) => Err(changed_patch(&resource)),
        Err(error) if error.kind == ErrorKind::Profile => Err(changed_patch(&resource)),
        Err(error) => Err(error),
    }
}

/// The unpatched resource (exact, or as the engine reads it), whether the
/// bytes are our patch, and our patch's text refs (`_examine`).
pub(crate) struct Examined {
    pub(crate) original: Vec<u8>,
    own: bool,
    script_ref: Option<i64>,
    text_refs: Option<BTreeMap<i64, i64>>,
}

pub(crate) fn examine(resource: &str, data: &[u8], naming: Naming<'_>) -> Result<Examined> {
    if destroy_gate(resource) {
        let (original, own) = destroys_original(data, &resource_specs(resource, naming)?)?;
        return Ok(Examined { original, own, script_ref: None, text_refs: None });
    }
    if resource.starts_with("bcs:") {
        let (original, script_ref, own) = script_original(data, &resource_specs(resource, naming)?)?;
        return Ok(Examined { original, own, script_ref, text_refs: None });
    }
    let unpatched = || Examined { original: data.to_vec(), own: false, script_ref: None, text_refs: None };
    if text_refs(resource).is_some() {
        return Ok(match text_original(data, resource, naming)? {
            None => unpatched(),
            Some((original, refs)) => Examined { original, own: true, script_ref: None, text_refs: Some(refs) },
        });
    }
    Ok(match static_original(data, resource, naming)? {
        None => unpatched(),
        Some(original) => Examined { original, own: true, script_ref: None, text_refs: None },
    })
}

// ---------------------------------------------------------------- texts

/// The appended texts' numbers: resource to (original strref to new strref).
pub type TextRefs = BTreeMap<String, BTreeMap<i64, i64>>;

/// Append the original generic well description; never replace shared text.
pub fn prepare_ddwell_text(text: &GameText, language: &str) -> Result<(GameText, i64)> {
    let (updated, refs) = prepare_reward_texts(text, ["bcs:ddwell"], language, None)?;
    Ok((updated, refs["bcs:ddwell"][&40850]))
}

/// The (TRA file, number) lines of Item Randomiser's reward text that a text
/// route shows.
pub fn text_lines(resource: &str) -> Result<Vec<Line>> {
    let refs = text_refs(resource).ok_or_else(|| Error::key(resource))?;
    let file = if resource == "dlg:ppdjinn" { "ppdjinn.tra" } else { "in-game.tra" };
    let numbers: BTreeSet<i64> = refs.iter().map(|&(_, tra)| tra).collect();
    Ok(numbers.into_iter().map(|number| (file.to_string(), number)).collect())
}

/// `_titled`: `body` under the title of the journal entry `original`, as the
/// Enhanced Editions' quest entries begin; `body` alone if it has none.
///
/// The title (`_TITLE`) is the entry's first line, which has text, and the
/// line breaks after it (one or two, as the language's texts have them),
/// before more text.
pub fn titled(original: Option<&str>, body: &str) -> String {
    let original = original.unwrap_or("");
    let line = original.find(['\r', '\n']).unwrap_or(original.len());
    let mut end = line;
    loop {
        let rest = &original[end..];
        if rest.starts_with("\r\n") {
            end += 2;
        } else if rest.starts_with('\n') {
            end += 1;
        } else {
            break;
        }
    }
    let has_text = |text: &str| text.chars().any(|c| !crate::py::is_space(c));
    if end > line && has_text(&original[..line]) && has_text(&original[end..]) {
        format!("{}{body}", &original[..end])
    } else {
        body.to_string()
    }
}

/// Allocate the requested reward texts, in the game's language, in one
/// append/dedup pass.
///
/// A text's female wording goes to the game's dialogf.tlk. A journal entry
/// ([`JOURNAL_REFS`]) keeps the title line of the entry it replaces, in the
/// game's own words. Fails with MissingTranslation ([`crate::translations`])
/// when the language has no translation of one of them. The result maps each
/// resource's texts as the `game` numbers them ([`game_text_refs`]) to the
/// new ones.
pub fn prepare_reward_texts<I, S>(
    text: &GameText,
    resources: I,
    language: &str,
    game: Option<&str>,
) -> Result<(GameText, TextRefs)>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let requested: BTreeSet<String> = resources.into_iter().map(|resource| crate::py::lower(resource.as_ref())).collect();
    let needed: Vec<&str> = requested.iter().map(String::as_str).filter(|resource| text_refs(resource).is_some()).collect();
    if needed.is_empty() {
        return Ok((text.clone(), BTreeMap::new()));
    }
    let mut wanted = Vec::new();
    for resource in &needed {
        wanted.extend(text_lines(resource)?);
    }
    let found = crate::translations::lines(language, &wanted)?;
    let mut entries = Vec::new();
    let mut values = Vec::new();
    for &resource in &needed {
        let file = text_lines(resource)?.remove(0).0;
        let journals = journal_refs(resource, game)?;
        for (old, tra) in game_text_refs(resource, game)? {
            entries.push((resource, old));
            let mut wording = found[&(file.clone(), tra)].clone();
            if journals.contains(&old) {
                let (male, female) = text.string(old)?;
                wording = (titled(male.as_deref(), &wording.0), titled(female.as_deref(), &wording.1));
            }
            values.push(wording);
        }
    }
    let (updated, refs) = text.append(&values)?;
    let mut result: TextRefs =
        needed.iter().map(|resource| (resource.to_string(), BTreeMap::new())).collect();
    for ((resource, old), reference) in entries.into_iter().zip(refs) {
        result.get_mut(resource).expect("needed").insert(old, reference as i64);
    }
    Ok((updated, result))
}

/// A createEntry line of BGEE.LUA that lists a journal text: where it ends,
/// its start up to the text, its quest and the rest of it (`_journal_entries`).
struct JournalEntry {
    end: usize,
    start: Vec<u8>,
    quest: Vec<u8>,
    rest: Vec<u8>,
}

/// BGEE.LUA's createEntry lines that list the journal text `reference`
/// (`(?m)^([ \t]*createEntry[ \t]*\([ \t]*(\d+)[ \t]*,[^,\r\n]*,[ \t]*)<reference>([ \t]*,[^\r\n]*\r?\n)`).
fn journal_entries(lua: &[u8], reference: i64) -> Vec<JournalEntry> {
    let reference = reference.to_string().into_bytes();
    let blanks = |at: usize| at + lua[at..].iter().take_while(|&&byte| byte == b' ' || byte == b'\t').count();
    let entry = |line: usize| -> Option<JournalEntry> {
        let mut at = blanks(line);
        at = blanks(at + lua[at..].starts_with(b"createEntry").then_some(11)?);
        at = blanks(at + (lua.get(at) == Some(&b'(')).then_some(1)?);
        let digits = lua[at..].iter().take_while(|byte| byte.is_ascii_digit()).count();
        let quest = (digits > 0).then(|| lua[at..at + digits].to_vec())?;
        at = blanks(at + digits);
        at += (lua.get(at) == Some(&b',')).then_some(1)?;
        at += lua[at..].iter().position(|&byte| matches!(byte, b',' | b'\r' | b'\n'))?;
        at = blanks(at + (lua[at] == b',').then_some(1)?);
        let start = lua[line..at].to_vec();
        at += lua[at..].starts_with(&reference).then_some(reference.len())?;
        let after = at;
        at = blanks(at);
        at += (lua.get(at) == Some(&b',')).then_some(1)?;
        at += lua[at..].iter().position(|&byte| matches!(byte, b'\r' | b'\n'))?;
        let end = match &lua[at..] {
            [b'\n', ..] => at + 1,
            [b'\r', b'\n', ..] => at + 2,
            _ => return None,
        };
        Some(JournalEntry { end, start, quest, rest: lua[after..end].to_vec() })
    };
    let lines = std::iter::once(0).chain(lua.iter().enumerate().filter(|(_, &byte)| byte == b'\n').map(|(index, _)| index + 1));
    lines.filter(|&line| line < lua.len()).filter_map(entry).collect()
}

/// BGEE.LUA with each new journal text listed as the text it replaces is
/// (`journal_lua`).
///
/// `texts` are [`prepare_reward_texts`]' result. The Enhanced Editions'
/// journal files an entry under its quest by BGEE.LUA's createEntry lines:
/// the new text gets a copy of each line that lists the original, beside it.
/// An original that no quest lists stays unlisted, and so does its new text.
pub fn journal_lua(lua: &[u8], texts: &TextRefs, game: Option<&str>) -> Result<Vec<u8>> {
    let mut lua = lua.to_vec();
    for (resource, refs) in texts {
        for old in journal_refs(resource, game)? {
            let new = *refs.get(&old).ok_or_else(|| Error::new(ErrorKind::Key, old.to_string()))?;
            let listed: BTreeSet<Vec<u8>> = journal_entries(&lua, new).into_iter().map(|entry| entry.quest).collect();
            for entry in journal_entries(&lua, old).into_iter().rev() {
                if listed.contains(&entry.quest) {
                    continue;
                }
                let line = [entry.start.as_slice(), new.to_string().as_bytes(), entry.rest.as_slice()].concat();
                lua = [&lua[..entry.end], line.as_slice(), &lua[entry.end..]].concat();
            }
        }
    }
    Ok(lua)
}

// ---------------------------------------------------------------- routes

/// The registry resource a resource name or file name stands for.
fn resource_key(resource: &str) -> Result<String> {
    let lowered = crate::py::lower(resource);
    let last = lowered.rsplit(':').next().unwrap_or("");
    let name = last.split('.').next().unwrap_or("");
    let candidates: Vec<&str> = resource_rewards().keys().filter(|key| key.split(':').nth(1) == Some(name)).collect();
    if candidates.len() != 1 {
        return Err(profile(format!("Unsupported extended reward resource: {resource}")));
    }
    Ok(candidates[0].to_string())
}

/// Whether the bytes are this adapter's own patch, verified exactly.
///
/// False for an unpatched resource; raises for a changed copy of our patch.
pub fn own_patch(resource: &str, data: &[u8], naming: Naming<'_>) -> Result<bool> {
    Ok(examine(&resource_key(resource)?, data, naming)?.own)
}

/// The script this adapter's patch was made from, exactly; `data` itself
/// when it is not our patch. Raises for a changed copy of our patch.
pub fn unpatched_script(resource: &str, data: &[u8], naming: Naming<'_>) -> Result<Vec<u8>> {
    let resource = resource_key(resource)?;
    if !resource.starts_with("bcs:") {
        return Err(profile(format!("{} is not a reward script", label(&resource))));
    }
    Ok(examine(&resource, data, naming)?.original)
}

fn prepare_routes(resource: &str, specs: &[Dict], data: &[u8], reasons: &mut Dict, naming: Naming<'_>) -> Result<()> {
    let examined = examine(resource, data, naming)?;
    if destroy_gate(resource) {
        patch_destroys(&examined.original, specs, Some(reasons))?;
    } else if resource.starts_with("bcs:") {
        patch_scripts(&examined.original, specs, Some(0), Some(reasons))?;
    } else if text_refs(resource).is_some() {
        let stand_in: BTreeMap<i64, i64> =
            game_text_refs(resource, naming.game)?.into_iter().map(|(old, _)| (old, old)).collect();
        patch_text_dialogue(&examined.original, resource, Some(&stand_in), true, true, naming)?;
    } else {
        patch_dialogue(&examined.original, specs, &[], true, true, Some(reasons))?;
    }
    Ok(())
}

/// Why each route with a grant in this resource cannot be prepared from
/// these bytes (route to reason).
///
/// Routes missing from the result can be prepared; this runs the same patch
/// that the export runs, with stand-in text references.
pub fn route_reasons(resource: &str, data: &[u8], naming: Naming<'_>) -> Result<Dict> {
    let resource = resource_key(resource)?;
    let specs = resource_specs(&resource, naming)?;
    let mut reasons = Dict::new();
    match prepare_routes(&resource, &specs, data, &mut reasons, naming) {
        Ok(()) => Ok(reasons),
        Err(error) if error.kind == ErrorKind::Profile => {
            let mut all = Dict::new();
            for spec in &specs {
                let key = route(spec)?;
                let reason = reasons.get(&key).cloned().unwrap_or_else(|| Value::from(error.message.clone()));
                all.insert(key, reason);
            }
            Ok(all)
        }
        Err(error) => Err(error),
    }
}

/// Whether the dialogue gives the item of the route `key` at all, in a way
/// the route can gate or not.
///
/// A route that follows a physical source ([`PHYSICAL_SOURCE_REWARDS`]) is
/// needed where its dialogue gives the item. A reply whose actions cannot be
/// read counts as giving it; [`route_reasons`] says why the route cannot be
/// prepared then.
pub fn gives_reward(resource: &str, data: &[u8], key: &str, naming: Naming<'_>) -> Result<bool> {
    let resource = resource_key(resource)?;
    let mut specs = Vec::new();
    for spec in resource_specs(&resource, naming)? {
        if route(&spec)? == key {
            specs.push(spec);
        }
    }
    let label = label(&resource);
    // A route without a grant of its own (the prince's) has no item to give.
    if !resource.starts_with("dlg:") || specs.is_empty() || matches!(field(&specs[0], "item")?, Value::Null) {
        return Err(profile(format!("Unsupported extended reward route in {label}: {key}")));
    }
    let item = text_field(&specs[0], "item", "encode")?;
    let dialogue = read_dialogue(&examine(&resource, data, naming)?.original, &resource)?;
    for state in &dialogue.states {
        let (first, count) = outgoing(&dialogue, state, &label)?;
        for row in &dialogue.transitions[first..first + count] {
            if u32_le(row, 0) & 4 == 0 {
                continue;
            }
            let text = string(&dialogue.actions, u32_le(row, 16), &label)?;
            if !names(&text, item) {
                continue;
            }
            match calls(&text) {
                None => return Ok(true),
                Some(found) if found.iter().any(|call| gives(&text, call, item)) => return Ok(true),
                Some(_) => {}
            }
        }
    }
    Ok(false)
}

/// The items the script `resource` destroys by name (`destroyed_items`):
/// each DestroyItem(), also through ActionOverride(), in any block and letter
/// case.
///
/// The Sahuagin treasury's script runs with every AI update of its container,
/// trapped or not (2.7.3.0: CGameContainer's constructor sets its script,
/// AIUpdate ends in ProcessAI), so a reward of such a name could be destroyed
/// there (native_export).
pub fn destroyed_items(resource: &str, data: &[u8]) -> Result<BTreeSet<String>> {
    let resource = resource_key(resource)?;
    naming(&label(&resource), || {
        let script = script_blocks(data)?;
        let mut found = BTreeSet::new();
        for block in &script.blocks {
            let (_, _, responses) = block_parts(block, script.newline)?;
            for action in block_actions(&responses, script.newline)? {
                if action.opcode == 169 {
                    found.insert(latin1(&action.strings[0].to_ascii_lowercase()));
                }
            }
        }
        Ok(found)
    })
}

/// Patch a resource or re-export our own patch; text routes need refs on
/// their first export.
pub fn patch_reward(
    resource: &str,
    data: &[u8],
    refs: Option<&BTreeMap<i64, i64>>,
    ddwell_ref: Option<i64>,
    naming: Naming<'_>,
) -> Result<Vec<u8>> {
    let resource = resource_key(resource)?;
    let examined = examine(&resource, data, naming)?;
    let specs = resource_specs(&resource, naming)?;
    if destroy_gate(&resource) {
        return patch_destroys(&examined.original, &specs, None);
    }
    if resource.starts_with("bcs:") {
        let mut ddwell_ref = ddwell_ref;
        if let Some(refs) = refs {
            let same = match text_refs(&resource) {
                None => false,
                Some(_) => {
                    let expected = game_text_refs(&resource, naming.game)?;
                    refs.len() == expected.len() && expected.iter().all(|(old, _)| refs.contains_key(old))
                }
            };
            if !same {
                return Err(profile(format!("{} requires its allocated original reward text", label(&resource))));
            }
            ddwell_ref = refs.values().next().copied();
        }
        return patch_scripts(&examined.original, &specs, ddwell_ref.or(examined.script_ref), None);
    }
    if text_refs(&resource).is_some() {
        return patch_text_dialogue(&examined.original, &resource, refs.or(examined.text_refs.as_ref()), true, true, naming);
    }
    if examined.own {
        return Ok(data.to_vec());
    }
    patch_dialogue(data, &specs, &[], true, true, None)
}
