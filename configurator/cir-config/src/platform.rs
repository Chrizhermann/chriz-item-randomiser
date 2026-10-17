//! The game a scan belongs to, its campaign starts and the parts each start
//! reaches (the reference's `platform.py`).
//!
//! Chriz Item Randomiser installs on BG:EE (with Siege of Dragonspear merged),
//! BG2:EE and EET. The game is identified as WeiDU's GAME_IS does it: EET by its
//! override/EET.flag, BG2:EE by OH6000.ARE, BG:EE by OH1000.ARE (EET has both
//! areas, BG2:EE has no OH1000).
//!
//! Each campaign start that the runtime can tell apart (M_FLPR.lua) gets its
//! own revision. The start that begins with the whole game (BG1, or SoA in
//! BG2:EE) uses the whole configuration; a later start (Siege of Dragonspear,
//! Shadows of Amn on EET, Throne of Bhaal) uses only rewards and places it can
//! reach. A reward that start cannot take stays as the game has it, and no
//! reward is put where the player cannot go.

use crate::error::{Error, Result};
use crate::json::Value;
use crate::paths;
use crate::templates::{template_campaign, CAMPAIGNS};
use std::path::PathBuf;

/// The games and their names.
pub const PLATFORMS: [(&str, &str); 3] = [("bgee", "BG:EE"), ("bg2ee", "BG2:EE"), ("eet", "EET")];

/// A part of the game: Baldur's Gate, Siege of Dragonspear, Shadows of Amn,
/// Watcher's Keep (which Shadows of Amn and Throne of Bhaal both reach) and
/// Throne of Bhaal.
pub type Part = &'static str;

const ALL: &[Part] = &["bg1", "sod", "soa", "wk", "tob"];

/// The game's name ("BG:EE", "BG2:EE" or "EET").
pub fn platform_name(platform: &str) -> Result<&'static str> {
    lookup(&PLATFORMS, platform)
}

/// The preset campaign of each game (templates::CAMPAIGNS).
pub fn preset_campaign(platform: &str) -> Result<&'static str> {
    lookup(&[("bgee", "bg1"), ("bg2ee", "bg2"), ("eet", "eet")], platform)
}

/// Preset campaigns whose tables a game has.
pub fn preset_campaigns(platform: &str) -> Result<&'static [&'static str]> {
    const BGEE: &[&str] = &["bg1"];
    const BG2EE: &[&str] = &["bg2"];
    const EET: &[&str] = &["eet", "bg1", "bg2"];
    lookup(&[("bgee", BGEE), ("bg2ee", BG2EE), ("eet", EET)], platform)
}

/// Campaign starts by the runtime's names (CAMPAIGN.2DA rows; BG2:EE's Throne
/// of Bhaal by its flag) and the parts of the game each reaches, in the
/// reference's order.
pub fn starts(platform: &str) -> Result<&'static [(&'static str, &'static [Part])]> {
    const EET: &[(&str, &[Part])] =
        &[("BG1", ALL), ("SOD", &["sod", "soa", "wk", "tob"]), ("SOA", &["soa", "wk", "tob"]), ("TOB", &["wk", "tob"])];
    const BGEE: &[(&str, &[Part])] = &[("BG1", &["bg1", "sod"]), ("SOD", &["sod"])];
    const BG2EE: &[(&str, &[Part])] = &[("SOA", &["soa", "wk", "tob"]), ("TOB", &["wk", "tob"])];
    lookup(&[("eet", EET), ("bgee", BGEE), ("bg2ee", BG2EE)], platform)
}

/// The start the engine makes without a campaign row (M_FLPR.lua's "MAIN").
/// EET names every start; a start without one is not randomised there.
pub fn main_start(platform: &str) -> Result<Option<&'static str>> {
    lookup(&[("eet", None), ("bgee", Some("BG1")), ("bg2ee", Some("SOA"))], platform)
}

/// The start that reaches the whole game: it keeps the whole configuration.
pub fn full_start(platform: &str) -> Result<&'static str> {
    lookup(&[("eet", "BG1"), ("bgee", "BG1"), ("bg2ee", "SOA")], platform)
}

/// A start's name for the player.
pub fn start_name(start: &str) -> Result<&'static str> {
    lookup(
        &[("BG1", "Baldur's Gate"), ("SOD", "Siege of Dragonspear"), ("SOA", "Shadows of Amn"), ("TOB", "Throne of Bhaal")],
        start,
    )
}

/// A dictionary lookup with Python's KeyError for a missing key.
fn lookup<T: Copy>(table: &[(&str, T)], key: &str) -> Result<T> {
    table.iter().find(|(name, _)| *name == key).map(|(_, value)| *value).ok_or_else(|| Error::key(key))
}

/// BG:EE's own areas with an OH name (its new companions' quests and the
/// Black Pits); EET keeps their names. BG2:EE's OH areas belong to Shadows of
/// Amn.
const BGEE_OH: [&str; 10] =
    ["oh1000", "oh2000", "oh2010", "oh3000", "oh3010", "oh3020", "oh3100", "oh9310", "oh9350", "oh9360"];

/// "eet", "bg2ee" or "bgee" for a scanned game; any other game is refused.
pub fn game_platform(world: &Value) -> Result<&'static str> {
    let root = match world.at("game_root")? {
        Value::Str(root) => PathBuf::from(root),
        other => {
            return Err(Error::type_error(format!(
                "expected str, bytes or os.PathLike object, not {}",
                other.type_name()
            )))
        }
    };
    let overrides = root.join("override");
    if paths::is_dir(&overrides).map_err(|error| Error::win(&error, &overrides))? {
        let names = paths::list_dir(&overrides).map_err(|error| Error::win(&error, &overrides))?;
        if names.iter().any(|name| crate::py::casefold(name) == "eet.flag") {
            return Ok("eet");
        }
    }
    let mut resources = std::collections::HashSet::new();
    for row in crate::py::iter_value(world.at("resources")?)? {
        match row.at("id")? {
            Value::Str(identity) => {
                resources.insert(identity.clone());
            }
            value @ (Value::List(_) | Value::Dict(_)) => {
                return Err(Error::type_error(format!("unhashable type: '{}'", value.type_name())))
            }
            _ => {}
        }
    }
    if resources.contains("are:oh6000") {
        return Ok("bg2ee");
    }
    if resources.contains("are:oh1000") {
        return Ok("bgee");
    }
    Err(Error::profile(
        "Chriz Item Randomiser works with Baldur's Gate: Enhanced Edition, \
         Baldur's Gate II: Enhanced Edition and the Enhanced Edition Trilogy (EET); \
         this game is none of them",
    ))
}

/// The scanned game, for a preset whose tables it has (an EET preset needs
/// EET).
pub fn preset_game(world: &Value, template_id: &str) -> Result<&'static str> {
    let platform = game_platform(world)?;
    let campaign = template_campaign(template_id)?;
    if !preset_campaigns(platform)?.contains(&campaign) {
        return Err(Error::profile(format!(
            "This preset is for {}; this game is {}",
            lookup(&CAMPAIGNS, campaign)?,
            platform_name(platform)?
        )));
    }
    Ok(platform)
}

/// The part of the game an area belongs to, or None when its name does not
/// tell.
pub fn area_part(platform: &str, area: &str) -> Option<Part> {
    // `(ar|bg|bd|oh)([0-9]{4})n?` on the lower-case name: only ASCII letters
    // lower-case to those, so the match is ASCII.
    let lower = crate::py::lower(area);
    let bytes = lower.as_bytes();
    let matched = (bytes.len() == 6 || (bytes.len() == 7 && bytes[6] == b'n'))
        && bytes[2..6].iter().all(u8::is_ascii_digit)
        && matches!(&bytes[..2], b"ar" | b"bg" | b"bd" | b"oh");
    if !matched {
        return None;
    }
    let prefix = &lower[..2];
    let number: u32 = lower[2..6].parse().expect("four ASCII digits");
    if prefix == "bd" {
        return ["bgee", "eet"].contains(&platform).then_some("sod");
    }
    if platform == "bgee" {
        return ["ar", "oh"].contains(&prefix).then_some("bg1");
    }
    if prefix == "bg" {
        return (platform == "eet").then_some("bg1");
    }
    if prefix == "oh" {
        if BGEE_OH.contains(&&lower[..6]) {
            return (platform == "eet").then_some("bg1");
        }
        return Some("soa");
    }
    // Baldur's Gate II areas: Watcher's Keep, Throne of Bhaal, else Shadows of
    // Amn.
    if (3000..3100).contains(&number) {
        return Some("wk");
    }
    if (4000..7000).contains(&number) {
        return Some("tob");
    }
    Some("soa")
}

/// The parts an Item Randomiser table (bg1 or bg2) covers.
pub fn campaign_parts(campaign: &str) -> &'static [Part] {
    if campaign == "bg1" {
        &["bg1"]
    } else {
        &["soa", "wk", "tob"]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn area_names_tell_their_part_of_the_game() {
        // The reference's test_area_names_tell_their_part_of_the_game.
        let cases: &[(&str, &[(&str, Option<Part>)])] = &[
            (
                "eet",
                &[
                    ("bg0100", Some("bg1")),
                    ("BG2600N", Some("bg1")),
                    ("oh1000", Some("bg1")),
                    ("oh3010", Some("bg1")),
                    ("bd0010", Some("sod")),
                    ("ar0100", Some("soa")),
                    ("oh6000", Some("soa")),
                    ("ar3000", Some("wk")),
                    ("ar3027", Some("wk")),
                    ("ar4000", Some("tob")),
                    ("ar6200", Some("tob")),
                    ("ar2600", Some("soa")),
                    ("zz0001", None),
                    ("ar01", None),
                    ("bp0001", None),
                ],
            ),
            ("bgee", &[("ar0100", Some("bg1")), ("oh1000", Some("bg1")), ("bd0010", Some("sod")), ("bg0100", None)]),
            (
                "bg2ee",
                &[
                    ("ar0100", Some("soa")),
                    ("oh6000", Some("soa")),
                    ("oh1000", None),
                    ("ar3000", Some("wk")),
                    ("ar5000", Some("tob")),
                    ("bd0010", None),
                    ("bg0100", None),
                ],
            ),
        ];
        for (platform, areas) in cases {
            for (area, part) in *areas {
                assert_eq!(area_part(platform, area), *part, "{platform} {area}");
            }
        }
        // `n?` is the only suffix; the digits are ASCII.
        assert_eq!(area_part("eet", "ar0100x"), None);
        assert_eq!(area_part("eet", "ar01\u{0661}0"), None);
        assert_eq!(area_part("eet", "AR0100n"), Some("soa"));
    }
}
