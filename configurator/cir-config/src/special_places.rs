//! Item Randomiser's special places, as the editor shows them (the
//! reference's `special_places.py`).
//!
//! A special place is a row of Item Randomiser's location tables whose reward
//! does not simply lie in a container, sit in a shop or go into one
//! creature's backpack: a quest reward that someone hands over, a carrier who
//! hands it over or else drops it, a reward that waits at a corpse, a
//! treasure whose taking wakes its guards, or one of several possible
//! carriers (a group, the chapter 3 vampires and thieves). Each has a short
//! label in game terms: who has the item, and what the player sees on each
//! path.
//!
//! The labels are English, as the editor is; their wording is Chris's to
//! choose. A place is recognised as the configurator recognises its delivery
//! (`original_registrations::target_kind`, `native_export`): an extended
//! reward route (`mode2_rewards`), a container quest (`container_rewards`),
//! the Deck of Many Things' Key, one of Item Randomiser's three quest scripts
//! (`quest_rewards`), the chapter 3 places (`chapter3`), a creature in several
//! versions (`original_catalog`'s creature versions) or a group of Item
//! Randomiser's group tables. A preset's row at such a place (Boss Loot's
//! Aldeth) is the same place.

use crate::error::{ErrorKind, Result};
use crate::json::{py_str, Dict, Value};
use crate::original_catalog::{
    chapter3_place, container_target_spec, creature_versions_place, is_spin621_target, mode2_route_target,
};
use crate::platform::{Part, TABLE_PARTS};

pub const KIND: &str = "special";
pub const KIND_LABEL: &str = "Special place";

/// Carriers whose reward reply hands over what they carry (`mode2_rewards`'
/// inventory targets): theft and combat still reach it.
macro_rules! carried {
    () => {
        "you can also steal it or take it from the body."
    };
}

/// The extended reward routes' places, by route (`mode2_rewards::rewards`).
const ROUTES: &[(&str, (&str, &str))] = &[
    ("uhogre01", ("Madulf the ogre (Umar Hills)", "He hands it over with his reward.")),
    (
        "asysta01",
        (
            "Chest of the statues (Spellhold maze)",
            "It appears in the chest when you place the last item in its statue.",
        ),
    ),
    ("ddwell", ("The well (Firkraag's dungeon)", "You pull it out of the well with the bucket.")),
    ("mgkhol01", ("Khollynnus Paac (planar sphere)", "He hands it over when you send the Solamnic knights home.")),
    ("ppstat01", ("Statue of Riddles (Spellhold maze)", "It gives it to you when you have answered its riddles.")),
    ("riftg03", ("Avatar of Amaunator", "It gives it to you when you return the Rift Device.")),
    ("trhmer01", ("Guildmistress Busya (Trademeet)", "She hands it over with her reward.")),
    ("udsvir03", ("Goldander Blackenrock (svirfneblin village)", "He hands it over with his payment.")),
    ("ammayor", ("Omar Haraad (Amkethran)", "He hands it over when you save his daughter Asana.")),
    (
        "sahkng01",
        ("King Ixilthetocal (Sahuagin city)", "He pays it to you when you help him; otherwise it lies at his corpse."),
    ),
    ("varci", ("Varci Roaringhorn (Baldur's Gate)", "Varci pays it to you for rescuing Casson.")),
    (
        "bhoisig",
        (
            "Temple reward (Temple District)",
            "The temple you work for hands it over with its reward: High Watcher Oisig, \
             Stormherald Nallabir or High Mornmaster Arval.",
        ),
    ),
    ("hendak", ("Hendak or Lehtinan (Copper Coronet)", "Whichever of them rewards you hands it over.")),
    (
        "udsilver",
        (
            "Adalon or the Lesser Demon Lord (Underdark)",
            "Adalon hands it over when you return her eggs; the demon lord gives it \
             when you ask him for a magic item.",
        ),
    ),
    ("gortan1", ("Tahazzar or Ka'rashur (Watcher's Keep)", "Whichever of the two you help pays it to you.")),
    (
        "gorlum2",
        ("Machine of Lum the Mad (Watcher's Keep)", "It appears in your pack when the machine gives you an item."),
    ),
    (
        "aran",
        ("Aran Linvail, first bonus item", "He hands it over with the bonus he pays when you start working for him."),
    ),
    (
        "aran2",
        ("Aran Linvail, second bonus item", "He hands it over with the bonus he pays when you start working for him."),
    ),
    ("ppdjinn", ("Genie (Spellhold maze)", "The genie gives it to you when the stone commands him.")),
    ("uhmay01", ("Minister Lloyd (Umar Hills)", "He gives it to you when you end the murders.")),
    ("gorcamb", ("Aesgareth the cambion (Watcher's Keep)", "You win it in his game, in place of the Spectral Brand.")),
    (
        "sctelwyn",
        (
            "Guardian Telwyn, Dawnbringer Sain or Talon Yarryl",
            "Whichever of the three rewards you first hands it over.",
        ),
    ),
    ("trgeni01", ("Khan Zahraa (Trademeet)", concat!("He carries it and hands it over with his reward; ", carried!()))),
    ("loutcapt", ("Captain Dennis", concat!("He carries it and hands it over with his reward; ", carried!()))),
    (
        "amcler01",
        ("Priest of Waukeen (Amkethran)", concat!("He carries it and hands it over with his thanks; ", carried!())),
    ),
    (
        "jugjer01",
        ("Jermien (Umar Hills)", concat!("He carries it and hands it over for the mimic's blood; ", carried!())),
    ),
    (
        "aldeth",
        (
            "Aldeth Sashenstar (Baldur's Gate)",
            concat!("He carries it and thanks you with it at his estate; ", carried!()),
        ),
    ),
    (
        "nemphr",
        ("Nemphre (Baldur's Gate)", concat!("She carries it and gives it to you for Arkion's amulet; ", carried!())),
    ),
    (
        "udlesa",
        ("Lasaonar (Ust Natha)", concat!("Lasaonar carries it and hands it over on yielding the duel; ", carried!())),
    ),
    (
        "sahrod",
        (
            "Sahuagin treasury (Sahuagin city)",
            "It lies in the treasury. If King Ixilthetocal pays you, it comes with his payment; \
             otherwise take it from the treasury.",
        ),
    ),
    ("tolger", ("Tolgerias", "He pays it to you for Valygar; otherwise it lies at his corpse in the planar sphere.")),
    (
        "sahpr2",
        (
            "Prince Villynaty (Sahuagin city)",
            "He carries it and hands it over when you help him take the throne; otherwise it lies at his corpse.",
        ),
    ),
    (
        "sahpr2b",
        (
            "Prince Villynaty, king-or-prince place (Sahuagin city)",
            "He carries it and hands it over when you help him take the throne; otherwise it lies at his corpse.",
        ),
    ),
    (
        "adalon",
        (
            "Adalon (Underdark)",
            concat!("She carries it and hands it over when you return her eggs; ", carried!()),
        ),
    ),
];

/// The golems' stand: picking a treasure up wakes two of them.
macro_rules! golems {
    () => {
        "It lies on the golems' stand; picking it up brings two of the golems to life."
    };
}

/// The container quests' places (`container_rewards::targets`).
const CONTAINER_QUESTS: &[(&str, (&str, &str))] = &[
    ("bg2:mode2:target:13", ("Golem treasure, Frostreaver's place (de'Arnise Keep)", golems!())),
    ("bg2:mode2:target:43", ("Golem treasure, the bow's place (de'Arnise Keep)", golems!())),
    ("bg2:mode2:target:3", ("Golem treasure, the hammer's place (de'Arnise Keep)", golems!())),
    (
        "bg2:mode2:target:53",
        (
            "King Strohm's treasure, the shield's place (Windspear Hills)",
            "It lies in the tomb's chest; once you have taken it, Samia turns on you.",
        ),
    ),
    (
        "bg2:mode2:target:28",
        (
            "King Strohm's treasure, the sword's place (Windspear Hills)",
            "It lies in the tomb's chest; once you have taken it, Samia turns on you.",
        ),
    ),
    (
        "bg1:mode2:target:22",
        ("Safana's pirate treasure", "It lies in the treasure chest; taking it completes Safana's treasure hunt."),
    ),
    (
        "bg2:mode2:target:61",
        ("Chest in the golem room (Spellhold maze)", "Taking it from the chest wakes the iron golem."),
    ),
];

/// Item Randomiser's three quest scripts (`quest_rewards::quests`).
const SCRIPTED_QUESTS: &[(&str, (&str, &str))] = &[
    ("blpool", ("Blood pool (Bodhi's lair)", "You find it at the bottom when you enter the pool.")),
    ("troalt", ("Troll altar (Spellhold)", "It appears on the altar when you place the head on it.")),
    (
        "ppbook",
        (
            "Ancient tome (Spellhold maze)",
            "The tome creates it once you have turned its pages and beaten the monsters they call.",
        ),
    ),
];

const SPELL: (&str, &str) = ("Deck of Many Things (Watcher's Keep)", "The Key card gives it when you draw it.");

const CHAPTER3: (&str, &str) = (
    "Bodhi's vampires or Aran's thieves (chapter 3)",
    "On Aran's side a vampire in Bodhi's lair carries it (Lassal, Durst or Gellal, drawn for each game). \
     On Bodhi's side it lies at the corpse of a drawn shadow thief (Booter, Haz or Arkanis Gath) \
     or of Aran Linvail, whoever dies first.",
);

/// The creatures in several versions (`original_catalog`'s creature
/// versions), by the first version.
const CREATURES: &[(&str, (&str, &str))] = &[
    (
        "pptroll1",
        (
            "Spirit Troll (Spellhold maze)",
            "It carries it; it drops when the troll dies for good (finish it with fire or acid).",
        ),
    ),
    (
        "torgal",
        ("TorGal (de'Arnise Keep)", "He carries it; it drops when he dies for good (finish him with fire or acid)."),
    ),
];

pub const GROUP_NOTE: &str =
    "One of them carries it, one who can use it if possible (drawn for each new game); it drops when that one dies.";

/// Names a group's label shows before "and N more".
const GROUP_NAMES: usize = 4;

fn lookup(table: &'static [(&str, (&'static str, &'static str))], key: &str) -> Result<(&'static str, &'static str)> {
    table.iter().find(|(known, _)| *known == key).map(|(_, texts)| *texts).ok_or_else(|| crate::error::Error::key(key))
}

/// Which special place a resolved original row (a target registration's
/// authored fields) is, or None for an ordinary one. A preset's row at a
/// route's or container quest's place is that place.
pub fn place(authored: &Value) -> Result<Option<String>> {
    if !authored.is_dict() {
        return Ok(None);
    }
    if let Some(container) = container_target_spec(authored)? {
        return Ok(Some(format!("container:{}", py_str(container.get("target_id").unwrap_or(&Value::Null)))));
    }
    if is_spin621_target(authored) {
        return Ok(Some(format!("spell:{}", py_str(authored.at("id")?))));
    }
    // As the export delivers it: also a route whose place is its conversation
    // or script itself (Madulf's), which mode2_alias_spec does not name.
    if let Some(route) = mode2_route_target(authored)? {
        return Ok(Some(format!("route:{route}")));
    }
    if let Some(creature) = creature_versions_place(authored) {
        return Ok(Some(format!("creature:{}:{creature}", py_str(authored.at("campaign")?))));
    }
    if chapter3_place(authored)? {
        return Ok(Some("chapter3".into()));
    }
    let Some(target) = authored.get("target").and_then(Value::as_str) else {
        return Ok(None);
    };
    if let Some(name) = target.strip_suffix(".bcs") {
        if crate::quest_rewards::quests().contains_key(name) {
            return Ok(Some(format!("quest:{name}")));
        }
    }
    let group = match authored.get("endpoint_kind") {
        None | Some(Value::Null) => target.ends_with(".flg"),
        Some(kind) => kind.as_str() == Some("group"),
    };
    if group {
        let field = |key: &str| py_str(authored.get(key).unwrap_or(&Value::Null));
        return Ok(Some(format!("group:{}:{target}:{}", field("campaign"), field("area_resource"))));
    }
    Ok(None)
}

/// Item Randomiser's lists (a preset campaign's original template) that hold
/// the scanned game's special places: each table of the game in one of them,
/// EET's in its own. None for a game no preset is for.
pub fn templates(world: &Value) -> Result<Vec<String>> {
    let campaigns = match crate::platform::preset_campaigns(world) {
        Ok(campaigns) => campaigns,
        Err(error) if error.kind == ErrorKind::Profile => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut result = Vec::new();
    let mut covered: Vec<&str> = Vec::new();
    for campaign in campaigns {
        let tables = crate::templates::campaign_tables(campaign)?;
        if !tables.iter().all(|table| covered.contains(table)) {
            covered.extend(tables);
            result.push(format!("{campaign}-original"));
        }
    }
    Ok(result)
}

/// Whether a configuration can have only one row of the place `key`: an
/// extended reward route or a container quest, whose game patches and runtime
/// name one row (FLPRNat's route_target). Rows of Item Randomiser's quest
/// scripts, groups and chapter 3 places are each a place of their own.
pub fn unique(key: &str) -> bool {
    key.starts_with("route:") || key.starts_with("container:")
}

/// The names a group's members have in an area (the scan's actors), for its
/// label: `names(area, creature)`.
pub type Names<'a> = dyn Fn(&str, &str) -> Vec<String> + 'a;

/// The label and note of a special place (`place`), or None.
pub fn describe(authored: &Value, names: Option<&Names<'_>>) -> Result<Option<Dict>> {
    let Some(key) = place(authored)? else {
        return Ok(None);
    };
    let (kind, rest) = key.split_once(':').unwrap_or((key.as_str(), ""));
    let (label, note) = match kind {
        "route" => {
            let route = crate::mode2_rewards::target_rewards()
                .get(rest)
                .map(py_str)
                .ok_or_else(|| crate::error::Error::key(rest))?;
            let (label, note) = lookup(ROUTES, &route)?;
            (label.to_string(), note)
        }
        "container" => {
            let (label, note) = lookup(CONTAINER_QUESTS, rest)?;
            (label.to_string(), note)
        }
        "spell" => (SPELL.0.to_string(), SPELL.1),
        "creature" => {
            let creature = key.rsplit_once(':').map_or(key.as_str(), |(_, creature)| creature);
            let (label, note) = lookup(CREATURES, creature)?;
            (label.to_string(), note)
        }
        "chapter3" => (CHAPTER3.0.to_string(), CHAPTER3.1),
        "quest" => {
            let (label, note) = lookup(SCRIPTED_QUESTS, rest)?;
            (label.to_string(), note)
        }
        _ => (group_label(authored, names), GROUP_NOTE),
    };
    Ok(Some(crate::dict! { "place" => key, "label" => label, "note" => note }))
}

/// "One of: Gall, Gaius, Rengaa, Berkref and 2 more (AR0701)": the group's
/// members by the names they have in its area.
fn group_label(authored: &Value, names: Option<&Names<'_>>) -> String {
    let area = authored.get("area_resource").and_then(Value::as_str);
    let mut shown: Vec<String> = Vec::new();
    let members = authored.get("members").and_then(Value::as_list).map(Vec::as_slice).unwrap_or(&[]);
    for member in members.iter().filter_map(Value::as_str) {
        let found = match (names, area) {
            (Some(names), Some(area)) => names(area, member),
            _ => Vec::new(),
        };
        let found = if found.is_empty() {
            vec![crate::py::upper(member.split_once(':').map_or(member, |(_, name)| name))]
        } else {
            found
        };
        for name in found {
            if !shown.contains(&name) {
                shown.push(name);
            }
        }
    }
    let listed = &shown[..shown.len().min(GROUP_NAMES)];
    let more = shown.len() - listed.len();
    let mut text = listed.join(", ");
    if more > 0 {
        text.push_str(&format!(" and {more} more"));
    }
    let place = area.and_then(|area| area.split_once(':')).map(|(_, name)| crate::py::upper(name)).unwrap_or_default();
    if place.is_empty() {
        format!("One of: {text}")
    } else {
        format!("One of: {text} ({place})")
    }
}

/// The area the place is in, as an ARE resource, where its row tells it; a
/// reward route's area, where its row has none (Tolgerias's sphere).
pub fn area(authored: &Value) -> Result<Option<String>> {
    if let Some(resource) =
        authored.get("area_resource").and_then(Value::as_str).filter(|resource| resource.starts_with("are:"))
    {
        return Ok(Some(resource.to_string()));
    }
    let bg2 = authored.get("campaign").and_then(Value::as_str) == Some("bg2");
    if let Some(raw) = authored.get("area").and_then(Value::as_str).filter(|raw| *raw != "blank") {
        if bg2 {
            // Baldur's Gate II's areas keep their names in every game.
            return Ok(Some(format!("are:{raw}")));
        }
    }
    let Some(key) = place(authored)? else {
        return Ok(None);
    };
    if let Some(route) = key.strip_prefix("route:").filter(|_| bg2) {
        let reward = crate::mode2_rewards::target_rewards()
            .get(route)
            .map(py_str)
            .ok_or_else(|| crate::error::Error::key(route))?;
        let spec = crate::mode2_rewards::rewards().get(&reward).ok_or_else(|| crate::error::Error::key(&reward))?;
        for field in ["inventory_target", "holder", "handover"] {
            if let Some(found) = spec.get(field).filter(|found| found.truthy()) {
                return Ok(Some(format!("are:{}", py_str(found.at("area")?))));
            }
        }
    }
    Ok(None)
}

/// The parts of the game a place of Baldur's Gate's or Siege of
/// Dragonspear's tables is in; EET renames their areas.
pub fn table_parts(authored: &Value) -> &'static [Part] {
    match authored.get("campaign").and_then(Value::as_str) {
        Some(campaign @ ("bg1" | "sod")) => {
            TABLE_PARTS.iter().find(|(known, _)| *known == campaign).map_or(&[], |(_, parts)| *parts)
        }
        _ => &[],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_route_and_container_quest_has_its_label() {
        // The reference's test_every_special_place_has_its_label: a label for
        // every place of mode2_rewards.TARGET_REWARDS, container_rewards.TARGETS
        // and quest_rewards.QUESTS, and none for a place that is gone.
        let routes: Vec<String> = crate::mode2_rewards::target_rewards().values().map(py_str).collect();
        for route in &routes {
            assert!(lookup(ROUTES, route).is_ok(), "{route}");
        }
        for (route, _) in ROUTES {
            assert!(routes.iter().any(|known| known == route), "{route}");
        }
        let containers = crate::container_rewards::targets();
        for quest in containers.keys() {
            assert!(lookup(CONTAINER_QUESTS, quest).is_ok(), "{quest}");
        }
        for (quest, _) in CONTAINER_QUESTS {
            assert!(containers.contains_key(quest), "{quest}");
        }
        let quests = crate::quest_rewards::quests();
        for quest in quests.keys() {
            assert!(lookup(SCRIPTED_QUESTS, quest).is_ok(), "{quest}");
        }
        for (quest, _) in SCRIPTED_QUESTS {
            assert!(quests.contains_key(quest), "{quest}");
        }
        assert!(unique("route:bg2:mode2:target:17") && unique("container:bg2:mode2:target:13"));
        assert!(!unique("group:bg2:x.flg:are:ar0701") && !unique("chapter3") && !unique("quest:blpool"));
    }

    #[test]
    fn a_groups_label_names_its_members_as_their_area_does() {
        // The reference's test_a_groups_label_names_its_members_as_their_area_does.
        let row = Value::Dict(crate::dict! {
            "id" => "bg2:base:target:9004", "campaign" => "bg2", "target" => "guards.flg", "endpoint_kind" => "group",
            "area_resource" => "are:ar0701",
            "members" => vec![
                "cre:gall", "cre:gaius", "cre:gaius2", "cre:rengaa", "cre:berkref", "cre:hendak", "cre:nameless",
            ],
        });
        let names: &Names<'_> = &|area: &str, member: &str| -> Vec<String> {
            let named = [
                ("cre:gall", "Gall"),
                ("cre:gaius", "Gaius"),
                ("cre:gaius2", "Gaius"),
                ("cre:rengaa", "Rengaa"),
                ("cre:berkref", "Berkref"),
                ("cre:hendak", "Hendak"),
            ];
            named
                .iter()
                .filter(|(known, _)| area == "are:ar0701" && *known == member)
                .map(|(_, name)| name.to_string())
                .collect()
        };
        let described = describe(&row, Some(names)).unwrap().unwrap();
        assert_eq!(described.get("place").and_then(Value::as_str), Some("group:bg2:guards.flg:are:ar0701"));
        assert_eq!(
            described.get("label").and_then(Value::as_str),
            Some("One of: Gall, Gaius, Rengaa, Berkref and 2 more (AR0701)")
        );
        assert_eq!(described.get("note").and_then(Value::as_str), Some(GROUP_NOTE));
        let plain = describe(&row, None).unwrap().unwrap();
        assert_eq!(
            plain.get("label").and_then(Value::as_str),
            Some("One of: GALL, GAIUS, GAIUS2, RENGAA and 3 more (AR0701)")
        );
    }
}
