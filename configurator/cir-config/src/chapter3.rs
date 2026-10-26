//! Item Randomiser's chapter 3 places (its chapter3.fl): a vampire or a
//! shadow thief (the reference's `chapter3.py`).
//!
//! Rows 25, 49, 215 and 252 of the extended BG2 locations table. Item
//! Randomiser's handler (lib/weidu_action.tpa) gives each of their items to two
//! creatures, since the party fights one side of the guild war only: to one of
//! Bodhi's vampires, whom Aran's side fights in her lair (AR0801.BCS creates
//! them for Aran's last job), and to one of Aran's shadow thieves, whom Bodhi's
//! side fights in his guild house (AR0329; with Rogue Rebalancing in its own
//! guild, RR#010) or at the docks (Arkanis Gath, whom AR0300.BCS creates for
//! Bodhi's first job). It chooses each among those who can use the item, else
//! among all, the rule [`crate::group_recipients`] has.
//!
//! CIR gives each place one reward. It goes to the first of these creatures
//! who can take it, in this order: the vampire drawn for the reward, alive,
//! into its backpack; the thief drawn for it, only at his corpse (alive he is
//! the party's ally on Aran's side, and Rogue Rebalancing removes the living
//! thieves once Aran is dead); Aran Linvail, also at his corpse, whom Bodhi's
//! side must kill (BODHI.DLG asks for Dead("aran")). So on Aran's side the
//! vampire carries it, and on Bodhi's side it lies at the corpse of whichever
//! of the thief and Aran dies first (FLPRNat "first-of"). Nothing is given
//! twice, so nothing needs destroying afterwards, as Item Randomiser's script
//! for the Alternatives mod does with the second copy.
//!
//! Not among Item Randomiser's creatures here: Tanova (BODTAN), whose vampire
//! script (VAMPIR01.BCS of the unmodded game) replaces her with a bat while she
//! lives once her hit points fall below 10, which would take a reward she
//! carries; and Dedral (ARNWAR07, RR#DEDRL), of whom AR0329's traps create more
//! (AMBUSH2.BCS, ARNSPW1.BCS) and who leaves in his conversation, so that his
//! corpse could not be told from another's.
//!
//! Each creature must be where the runtime looks for it
//! ([`crate::native_export`]): once in the areas Item Randomiser's handler
//! names, as an actor that appears there or as a creature its area's own script
//! creates, and the only creature there of its script name.

use crate::error::Result;
use crate::py::{latin1, lower, slice, strip};
use crate::script_text::{block_actions, block_parts, naming, script_blocks};
use std::collections::BTreeMap;

pub const TARGET: &str = "chapter3.fl";
/// The place's own resource: the vampires' area.
pub const AREA: &str = "ar0801";
/// The areas Item Randomiser's handler names; it adds its watcher to each.
pub const AREAS: [&str; 4] = ["ar0801", "ar0329", "rr#010", "ar0300"];
pub const VAMPIRES: [&str; 3] = ["lassal02", "bodvam01", "bodvam02"];
pub const THIEVES: [&str; 3] = ["booter02", "arnwar06", "arkanisg"];
/// With Rogue Rebalancing (its RR#ARAN2.CRE): its guild's thieves, as Item
/// Randomiser takes them.
pub const RR_THIEVES: [&str; 3] = ["rr#bootr", "rr#haz", "arkanisg"];
pub const RR_MARK: &str = "cre:rr#aran2";
pub const ARAN: [&str; 2] = ["aran02", "rr#aran2"];
/// The actions that create a creature and name its file first. Not
/// CreateCreatureAtLocation, which creates it at a point a variable keeps.
const CREATIONS: [i64; 11] = [7, 227, 228, 231, 232, 233, 238, 250, 252, 295, 323];

/// One group of the place's creatures.
pub struct Group {
    pub names: &'static [&'static str],
    /// Whether the group takes the reward only at a corpse.
    pub corpse_only: bool,
    /// Whether one of the group is drawn per reward.
    pub drawn: bool,
}

/// The creatures of each group in the order they take the reward, in a game
/// whose resources `present` tells.
pub fn groups(present: impl Fn(&str) -> bool) -> [Group; 3] {
    let thieves: &'static [&'static str] = if present(RR_MARK) { &RR_THIEVES } else { &THIEVES };
    [
        Group { names: &VAMPIRES, corpse_only: false, drawn: true },
        Group { names: thieves, corpse_only: true, drawn: true },
        Group { names: &ARAN, corpse_only: true, drawn: false },
    ]
}

/// Every creature of the place, as CRE resources in group order.
pub fn candidates(present: impl Fn(&str) -> bool) -> Vec<String> {
    groups(present).iter().flat_map(|group| group.names.iter().map(|name| format!("cre:{name}"))).collect()
}

/// The area's own script (the ARE header's resref at 0x94), lower case, or
/// None.
pub fn area_script(data: &[u8]) -> Option<String> {
    let field = slice(data, 0x94, 0x9C);
    let name = field.split(|&byte| byte == 0).next().unwrap_or(&[]);
    let name = lower(strip(&latin1(name)));
    (!name.is_empty()).then_some(name)
}

/// How many actions of a compiled script create each creature, by its file
/// (lower case), whatever their conditions.
pub fn created(data: &[u8], label: &str) -> Result<BTreeMap<String, usize>> {
    naming(label, || {
        let script = script_blocks(data)?;
        let mut found = BTreeMap::new();
        for block in &script.blocks {
            let (_, _, responses) = block_parts(block, script.newline)?;
            for action in block_actions(&responses, script.newline)? {
                if CREATIONS.contains(&action.opcode) {
                    *found.entry(lower(&latin1(&action.strings[0]))).or_insert(0) += 1;
                }
            }
        }
        Ok(found)
    })
}
