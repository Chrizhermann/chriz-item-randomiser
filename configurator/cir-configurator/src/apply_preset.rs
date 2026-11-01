//! `apply-preset`: a preset applied to a game without the editor, as an
//! installer applies the default setup once the mods are installed. The game
//! is scanned into this player's folder for it as every start without
//! arguments scans it (`player::scanned`, which carries the configurations
//! saved before the game's mods changed over), the preset is fitted as the
//! editor's preset dialog fits it, and the configuration is saved, checked
//! and applied as the editor's Apply does. The next start without arguments
//! reopens it as the configuration applied last. Nothing is shown and no
//! browser is opened: the result is the command's JSON.

use crate::argparse::Parsed;
use crate::player::{self, Stopped};
use cir_config::editor::Editor;
use cir_config::game_folder::{cir_installed, find_game};
use cir_config::json::{Dict, Value};
use cir_config::{activation, paths, profiles, sources, templates, Error, Result};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// Every refusal is the command's JSON error, also one the reference
/// commands would let escape (`KeyError` and the like): an installer reads
/// one answer.
pub fn run(parsed: &Parsed) -> Result<Value> {
    apply(parsed).map_err(|error| if error.is_reported() { error } else { Error::profile(error.message) })
}

fn apply(parsed: &Parsed) -> Result<Value> {
    let game = game(&parsed.path("game").expect("required option"))?;
    // A choice the game cannot take refuses before the scan.
    let given = parsed.path("options").map(|path| profiles::read_json(&path)).transpose()?;
    let mut options = chosen_options(given.as_ref())?;
    // Also before the scan, which an installer still running could change
    // under it; Apply asks again.
    activation::require_closed(&game)?;
    let scanned = player::scanned(&game, &mut |_| {}).map_err(|stopped| match stopped {
        Stopped::Folder(message) => Error::profile(message),
        Stopped::Read(error) => Error::profile(format!("Could not read the game: {}", error.message)),
        Stopped::CarryOver(error) => error,
    })?;
    let player::Scanned { workspace, configurations, catalog, world, carried } = scanned;
    let mut notes: Vec<Value> =
        sources::unreadable_notes(&world).into_iter().map(|note| Value::from(format!("{note}."))).collect();
    notes.extend(carried.lines().into_iter().map(Value::from));
    let (profile, plan) = Editor::new_draft(&catalog, &world, None)?;
    let mut editor = Editor::new(catalog, world, &configurations, Some(profile), Some(plan), Some(&game))?;
    // The preset dialog's choices: the game's own preset, standard pools,
    // and no option the game cannot apply.
    let recommended = editor.get("/api/recommended", &HashMap::new())?;
    let unavailable = recommended.at("unavailable")?.clone();
    for option in unavailable.as_dict().into_iter().flat_map(|reasons| reasons.keys()) {
        options.insert(option, false);
    }
    let preset = match parsed.text("preset") {
        Some(preset) => Value::from(preset),
        None => recommended.at("id")?.clone(),
    };
    let pool = |name: &str| -> Result<Value> {
        Ok(parsed.text(name).map(Value::from).unwrap_or(recommended.at(name)?.clone()))
    };
    let (items, targets) = (pool("items")?, pool("targets")?);
    let request = cir_config::dict! {
        "id" => preset.clone(), "items" => items.clone(), "targets" => targets.clone(),
        "options" => Value::Dict(options),
    };
    let state = editor.post("/api/template", &Value::Dict(request))?;
    let draft = cir_config::dict! {"profile" => state.at("profile")?.clone(), "plan" => state.at("plan")?.clone()};
    let applied = editor.post("/api/apply", &Value::Dict(draft))?;
    let profile = state.at("profile")?;
    let mut result = Dict::new();
    result.insert("game", paths::display(&game));
    result.insert("workspace", paths::display(&workspace));
    result.insert(
        "configuration",
        cir_config::dict! {"id" => profile.at("id")?.clone(), "name" => profile.at("name")?.clone()},
    );
    result.insert(
        "preset",
        cir_config::dict! {
            "id" => preset, "items" => items, "targets" => targets,
            "options" => state.at("plan")?.at("original_options")?.clone(),
        },
    );
    result.insert("unavailable", unavailable);
    for key in ["left_out", "absent_mods", "not_used"] {
        result.insert(key, state.at(key)?.clone());
    }
    for key in ["revision", "starts", "message"] {
        result.insert(key, applied.at(key)?.clone());
    }
    result.insert("notes", Value::List(notes));
    result.insert("hint", hint(&game).map_or(Value::Null, Value::from));
    Ok(Value::Dict(result))
}

/// The game of `folder`: the nearest folder at or above it with chitin.key,
/// as the start without arguments finds it, with Chriz Item Randomiser
/// installed. Its path is the one that start keeps the game's folder by.
fn game(folder: &Path) -> Result<PathBuf> {
    let folder = paths::resolve(folder, true).map_err(|(error, at)| Error::win(&error, &at))?;
    let Some(game) = find_game(&folder) else {
        return Err(Error::profile(format!(
            "{} is not a game folder, and no folder above it is one: a game folder has chitin.key",
            paths::display(&folder)
        )));
    };
    let installed = cir_installed(&game).map_err(|error| {
        Error::profile(format!(
            "Could not check whether Chriz Item Randomiser is installed in {}: {}",
            paths::display(&game),
            error.message
        ))
    })?;
    if !installed {
        return Err(Error::profile(format!("Chriz Item Randomiser is not installed in {}", paths::display(&game))));
    }
    Ok(game)
}

/// The preset's options: the preset dialog's (`recommended_options`), with
/// those `given` names instead, checked as the preset's fit checks them.
fn chosen_options(given: Option<&Value>) -> Result<Dict> {
    let recommended = templates::recommended_options();
    let chosen = match given {
        None => recommended,
        Some(Value::Dict(fields)) if fields.keys().all(|key| recommended.contains_key(key)) => {
            recommended.merged(fields)
        }
        Some(_) => return Err(Error::value("Unknown original-template option")),
    };
    templates::options(Some(&Value::Dict(chosen)))
}

/// How to change the settings later, when this executable is in the game:
/// started without arguments, it finds this game and reopens the
/// configuration applied last.
fn hint(game: &Path) -> Option<String> {
    let exe = paths::resolve(&std::env::current_exe().ok()?, true).ok()?;
    let found = find_game(&paths::parent(&exe))?;
    if paths::path_cmp(&found, game) != std::cmp::Ordering::Equal {
        return None;
    }
    let relative: Vec<String> = paths::parts(&exe).into_iter().skip(paths::parts(game).len()).collect();
    Some(format!(
        "To change these settings, close the game and double-click {} in the game folder: the configurator \
         opens this configuration",
        relative.join("\\")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn json(options: Dict) -> String {
        cir_config::json::dumps_ascii(&Value::Dict(options), false)
    }

    #[test]
    fn options_are_the_preset_dialogs_with_those_given_instead() {
        assert_eq!(json(chosen_options(None).unwrap()), json(templates::recommended_options()));
        let given = cir_config::dict! {"scrolls" => true, "totsc_tome" => false, "loss_mode" => "some", "loss_percent" => 10};
        let chosen = chosen_options(Some(&Value::Dict(given.clone()))).unwrap();
        assert_eq!(json(chosen), json(templates::recommended_options().merged(&given)));
        let chosen = chosen_options(Some(&Value::Dict(given))).unwrap();
        assert!(matches!(chosen.get("flail_heads"), Some(Value::Bool(true))));
    }

    #[test]
    fn options_the_fit_would_refuse_refuse_before_the_scan() {
        for (given, message) in [
            (Value::Dict(cir_config::dict! {"nosuch" => true}), "Unknown original-template option"),
            (Value::List(Vec::new()), "Unknown original-template option"),
            (Value::Dict(cir_config::dict! {"scrolls" => 1}), "scrolls must be a Boolean"),
            (
                Value::Dict(cir_config::dict! {"loss_percent" => 101}),
                "loss_percent must be an integer percentage from 0 to 100",
            ),
        ] {
            assert_eq!(chosen_options(Some(&given)).unwrap_err().message, message);
        }
    }
}
