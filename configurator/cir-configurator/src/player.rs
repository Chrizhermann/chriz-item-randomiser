//! A start without arguments, as after a double-click on the executable in
//! the game's `cir` folder: find that game, check that Chriz Item Randomiser
//! is installed there, scan it into this player's application data and open
//! the editor in the browser with Apply for that game. When there is no game
//! above the executable, or CIR is not installed in it, the player chooses
//! the game folder. After the game's mods changed (a new version of CIR among
//! them), the configurations saved before are carried over to the new scan.
//! Explicit commands are not affected.

use crate::dialog;
use crate::server::{self, Announce};
use cir_config::editor::{self, Editor};
use cir_config::game_folder::{cir_installed, find_game, game_language, game_workspace};
use cir_config::json::{self, Dict, Value};
use cir_config::{paths, profiles, rebase, resources, sources, Error};
use std::collections::HashSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

/// The folder in this player's application data with every game's scans
/// and configurations, one folder per game.
const DATA_FOLDER: &str = "Chriz Item Randomiser";
/// The folder in a game's configurations for those that cannot be carried
/// over to its new scan, and for the earlier version of those carried over
/// in part.
const EARLIER: &str = "earlier scans";
/// The folder window's title.
const CHOOSE: &str = "Choose the game folder where Chriz Item Randomiser is installed";
/// Set to any value, the editor's address is only printed, not opened.
const NO_BROWSER: &str = "CIR_NO_BROWSER";

enum Outcome {
    /// The editor ran until it was closed.
    Closed,
    /// The player closed the folder window without choosing a folder.
    NotChosen,
}

/// Why a folder does not lead to a game with CIR.
enum Problem {
    NoGame,
    NotInstalled(PathBuf),
}

pub fn run() -> ExitCode {
    println!("Chriz Item Randomiser: configuration editor");
    match start() {
        Ok(Outcome::Closed) => {
            println!("The editor is closed.");
            ExitCode::SUCCESS
        }
        Ok(Outcome::NotChosen) => {
            println!("No game folder was chosen, so the editor was not opened.");
            ExitCode::from(1)
        }
        Err(message) => {
            eprintln!("{message}");
            pause();
            ExitCode::from(1)
        }
    }
}

fn start() -> Result<Outcome, String> {
    let unknown = |error: &dyn std::fmt::Display| format!("Could not tell where this program is: {error}");
    let exe = std::env::current_exe().map_err(|error| unknown(&error))?;
    let exe = paths::resolve(&exe, true).map_err(|(error, _)| unknown(&error))?;
    let folder = paths::parent(&exe);
    let game = match checked(&folder)? {
        Ok(game) => game,
        Err(problem) => {
            println!(
                "{}",
                match problem {
                    Problem::NoGame => format!(
                        "No game folder was found at or above {}, where this program is. After installation \
                         it is in the game's cir folder.",
                        paths::display(&folder)
                    ),
                    Problem::NotInstalled(game) => format!(
                        "Chriz Item Randomiser is not installed in {}, the game this program is in.",
                        paths::display(&game)
                    ),
                }
            );
            match choose(&folder)? {
                Some(game) => game,
                None => return Ok(Outcome::NotChosen),
            }
        }
    };
    open_editor(&game)?;
    Ok(Outcome::Closed)
}

/// The nearest game at or above `folder` when CIR is installed in it.
fn checked(folder: &Path) -> Result<Result<PathBuf, Problem>, String> {
    let Some(game) = find_game(folder) else { return Ok(Err(Problem::NoGame)) };
    let installed = cir_installed(&game).map_err(|error| {
        format!(
            "Could not check whether Chriz Item Randomiser is installed in {}: {}",
            paths::display(&game),
            error.message
        )
    })?;
    Ok(if installed { Ok(game) } else { Err(Problem::NotInstalled(game)) })
}

/// Ask for the game folder until the player chooses one with CIR, each
/// checked as the executable's own folder, or closes the window.
fn choose(start: &Path) -> Result<Option<PathBuf>, String> {
    let mut start = start.to_path_buf();
    loop {
        if cfg!(windows) {
            println!("{CHOOSE} (a folder window is open).");
        }
        let Some(picked) = dialog::pick_folder(CHOOSE, Some(&start))? else { return Ok(None) };
        let picked = paths::resolve(&picked, true).map_err(|(error, at)| Error::win(&error, &at).message)?;
        match checked(&picked)? {
            Ok(game) => return Ok(Some(game)),
            Err(Problem::NoGame) => println!(
                "{} is not a game folder, and no folder above it is one: a game folder has chitin.key.",
                paths::display(&picked)
            ),
            Err(Problem::NotInstalled(game)) => {
                println!("Chriz Item Randomiser is not installed in {}.", paths::display(&game))
            }
        }
        start = picked;
    }
}

/// This player's folder for the scans and configurations of every game,
/// outside every game.
fn data_folder() -> Result<PathBuf, String> {
    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from)
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .filter(|base| base.is_absolute())
            .or_else(|| std::env::var_os("HOME").map(|home| Path::new(&home).join(".local").join("share")))
    };
    match base.filter(|base| base.is_absolute()) {
        Some(base) => Ok(base.join(DATA_FOLDER)),
        None if cfg!(windows) => Err("Windows names no local application data folder (LOCALAPPDATA) for this user, \
                                      so there is nowhere to keep the scans."
            .into()),
        None => Err("There is no data folder (XDG_DATA_HOME or HOME) to keep the scans in.".into()),
    }
}

fn open_editor(game: &Path) -> Result<(), String> {
    println!("Game: {}", paths::display(game));
    let workspace = game_workspace(&data_folder()?, game);
    let language = game_language(game).map_err(|error| format!("Could not read the game: {}", error.message))?;
    // The world scan of the previous start, which the saved configurations
    // were made with. It stays until they are carried over to this start's
    // scan, so a start that ends sooner (its window closed) leaves it for the
    // next one to carry them over from.
    let earlier = std::fs::read(workspace.join("world.json")).ok();
    println!("Reading the game's items, areas, creatures and shops; this can take a minute.");
    let (catalog, world, world_path) = scans(game, language.as_deref(), &workspace)
        .map_err(|error| format!("Could not read the game: {}", error.message))?;
    println!("Scans and configurations: {}", paths::display(&workspace));
    // Game files the world scan could not read and left out, as `world scan` names them.
    for note in sources::unreadable_notes(&world) {
        println!("{note}.");
    }
    let configurations = workspace.join("configurations");
    let carried = carry_over(&configurations, &catalog, &world, earlier.as_deref())
        .map_err(|error| format!("Could not open the editor: {}", error.message))?;
    carried.report();
    profiles::write_json(&world_path, &world, false)
        .map_err(|error| format!("Could not read the game: {}", error.message))?;
    let editor = editor(catalog, world, &carried, &configurations, game)
        .map_err(|error| format!("Could not open the editor: {}", error.message))?;
    let open_browser = std::env::var_os(NO_BROWSER).is_none();
    server::run(editor, 0, open_browser, Announce::Player)
        .map_err(|error| format!("Could not open the editor: {}", error.message))?;
    Ok(())
}

/// The game's item and world scans. The item scan is written to
/// `catalog.json` of its workspace as the `scan` command writes it; the
/// world scan comes with where `world scan` writes it (`world.json`), which
/// the caller writes once the configurations are carried over to it.
fn scans(game: &Path, language: Option<&str>, workspace: &Path) -> cir_config::Result<(Value, Value, PathBuf)> {
    let (catalog_path, world_path) = (workspace.join("catalog.json"), workspace.join("world.json"));
    // As the scan command: checked before the scan and again with its root.
    let mut probe = Dict::new();
    probe.insert("game_root", Value::Str(paths::display(game)));
    profiles::ensure_output_outside_game(&catalog_path, &Value::Dict(probe))?;
    let catalog = resources::scan_game(game, language)?;
    profiles::validate_catalog(&catalog)?;
    let destination = profiles::ensure_output_outside_game(&catalog_path, &catalog)?;
    profiles::write_json(&destination, &catalog, false)?;
    let destination = profiles::ensure_output_outside_game(&world_path, &catalog)?;
    let world = sources::scan_world(&catalog)?;
    Ok((catalog, world, destination))
}

/// The editor with Apply for this game, once the configurations saved
/// before the game's mods changed are carried over (`carried`). It reopens
/// the configuration applied last, as the `ui` command does, or starts with a
/// new one when that cannot be reopened; the player is told so once, as it
/// is no longer the one to reopen.
fn editor(
    catalog: Value,
    world: Value,
    carried: &Carried,
    configurations: &Path,
    game: &Path,
) -> cir_config::Result<Editor> {
    let applied = configurations.join("prepared").join("last-applied.json");
    if !paths::is_file(&applied).map_err(|error| Error::win(&error, &applied))? {
        return Editor::new(catalog, world, configurations, None, None, Some(game));
    }
    let gone = match applied_last(&applied) {
        None => {
            Some("Which configuration was applied last cannot be read, so the editor starts with a new one.".into())
        }
        Some(identity) => {
            let path = configurations.join(format!("{identity}.json"));
            if paths::exists(&path).map_err(|error| Error::win(&error, &path))? {
                None
            } else {
                Some(carried.gone(&identity, configurations))
            }
        }
    };
    if let Some(message) = gone {
        println!("{message}");
        return new_editor(catalog, world, configurations, game, &applied);
    }
    match Editor::new(catalog.clone(), world.clone(), configurations, None, None, Some(game)) {
        Ok(editor) => Ok(editor),
        Err(_) => {
            // Everything before reopening is checked the same way with a new
            // configuration; when it passes, reopening was what failed.
            let editor = new_editor(catalog, world, configurations, game, &applied)?;
            println!("The configuration applied last cannot be reopened, so the editor starts with a new one.");
            Ok(editor)
        }
    }
}

/// The editor with a new configuration, the configuration applied last
/// forgotten: the player was told once, and the next start does not look for
/// it again. Apply names the configuration it applies anew.
fn new_editor(
    catalog: Value,
    world: Value,
    configurations: &Path,
    game: &Path,
    applied: &Path,
) -> cir_config::Result<Editor> {
    let (profile, plan) = Editor::new_draft(&catalog, &world, None)?;
    let editor = Editor::new(catalog, world, configurations, Some(profile), Some(plan), Some(game))?;
    let _ = std::fs::remove_file(applied);
    Ok(editor)
}

/// The identity of the configuration applied last, as Apply records it.
fn applied_last(applied: &Path) -> Option<String> {
    let pointer = profiles::read_json(applied).ok()?;
    let identity = pointer.get("id")?.as_str()?;
    profiles::is_profile_id(identity).then(|| identity.to_string())
}

/// The name a saved configuration gives itself, if it is one to show, or else
/// its file's name.
fn shown_name(draft: &Value, path: &Path) -> String {
    draft
        .get("profile")
        .and_then(|profile| profile.get("name"))
        .and_then(Value::as_str)
        .filter(|name| !name.trim().is_empty() && !name.chars().any(char::is_control))
        .map_or_else(|| paths::name(path), str::to_string)
}

/// What became of the saved configurations made before the game's mods
/// changed.
struct Carried {
    /// Where those kept aside are.
    folder: PathBuf,
    /// Carried over whole: the name and what its preset is like now, in
    /// plain words (none for most).
    whole: Vec<(String, Vec<String>)>,
    /// Carried over in part: the name and what was left out, in plain words.
    part: Vec<(String, Vec<String>)>,
    /// Kept aside: the file's identity, the name and why.
    aside: Vec<(String, String, String)>,
    /// Carried over, but no longer read-only as before: the name and why.
    writable: Vec<(String, String)>,
}

impl Carried {
    fn report(&self) {
        for line in self.lines() {
            println!("{line}");
        }
    }

    /// What the console says of them, one line each.
    fn lines(&self) -> Vec<String> {
        let folder = paths::display(&self.folder);
        let mut lines = Vec::new();
        let plain: Vec<&String> = self.whole.iter().filter(|(_, notes)| notes.is_empty()).map(|(name, _)| name).collect();
        match plain.as_slice() {
            [] => {}
            [name] => lines.push(format!(
                "The saved configuration \"{name}\" was made before the game's mods changed and is carried over \
                 to the new scan."
            )),
            names => lines.push(format!(
                "{} saved configurations were made before the game's mods changed and are carried over to the new \
                 scan: {}.",
                names.len(),
                names.iter().map(|name| format!("\"{name}\"")).collect::<Vec<_>>().join(", ")
            )),
        }
        for (name, notes) in self.whole.iter().filter(|(_, notes)| !notes.is_empty()) {
            lines.push(format!(
                "The saved configuration \"{name}\" was made before the game's mods changed and is carried over \
                 to the new scan. {}.",
                notes.join(". ")
            ));
        }
        for (name, notes) in &self.part {
            lines.push(format!(
                "The saved configuration \"{name}\" was made before the game's mods changed and is carried over \
                 to the new scan, but not all of it: {}. Its earlier version is kept in {folder}.",
                notes.join(". ")
            ));
        }
        for (name, error) in &self.writable {
            lines.push(format!(
                "The saved configuration \"{name}\" was read-only, and its carried-over version could not be made \
                 read-only again ({}).",
                error.trim_end_matches('.')
            ));
        }
        for (_, name, reason) in &self.aside {
            lines.push(format!(
                "The saved configuration \"{name}\" cannot be carried over to the new scan: {reason}. It was moved \
                 to {folder}."
            ));
        }
        lines
    }

    /// What to tell the player when the configuration applied last (this
    /// identity) is no longer among the saved configurations: kept aside by
    /// this start, kept aside by an earlier version, or gone.
    fn gone(&self, identity: &str, configurations: &Path) -> String {
        if let Some((_, name, _)) = self.aside.iter().find(|(stem, _, _)| stem == identity) {
            return format!(
                "The configuration applied last, \"{name}\", could not be carried over, so the editor starts with a \
                 new one."
            );
        }
        // An earlier version kept those it could not open in the same folder,
        // under the same name or numbered after it.
        let numbered = format!("{identity} (");
        let kept = editor::saved_files(&self.folder).unwrap_or_default().into_iter().find(|path| {
            let stem = paths::stem(&paths::name(path)).to_string();
            stem == identity || stem.strip_prefix(&numbered).is_some_and(|number| number.ends_with(')'))
        });
        let Some(kept) = kept else {
            return "The configuration applied last is no longer among the saved configurations, so the editor \
                    starts with a new one."
                .into();
        };
        let named = match profiles::read_json(&kept) {
            Ok(draft) => format!(", \"{}\",", shown_name(&draft, &kept)),
            Err(_) => String::new(),
        };
        format!(
            "The configuration applied last{named} was set aside in {} by an earlier version, so the editor starts \
             with a new one. Moved back into {}, it is carried over at the next start.",
            paths::display(&self.folder),
            paths::display(configurations)
        )
    }
}

/// Carry the saved configurations this scan cannot open (made before the
/// game's mods changed) over to it, as `rebase_drafts` does, each in its own
/// file. What no longer fits is left out; the earlier version of one carried
/// over in part is kept in the `earlier scans` folder, where the editor does
/// not look. One that cannot be carried over (or read, or written) is moved
/// there, also when the carry-over of it runs into an unexpected error; the
/// others are carried over all the same. `earlier` is the world scan they
/// were made with, if there is one.
fn carry_over(
    configurations: &Path,
    catalog: &Value,
    world: &Value,
    earlier: Option<&[u8]>,
) -> cir_config::Result<Carried> {
    let mut carried = Carried {
        folder: configurations.join(EARLIER),
        whole: Vec::new(),
        part: Vec::new(),
        aside: Vec::new(),
        writable: Vec::new(),
    };
    let mut stale = Vec::new();
    for path in editor::saved_files(configurations)? {
        match profiles::read_json(&path) {
            Ok(draft) if editor::checked_draft(catalog, world, &draft).is_ok() => {}
            Ok(draft) => stale.push((path, draft)),
            Err(_) => stale.push((path, Value::Null)),
        }
    }
    if stale.is_empty() {
        return Ok(carried);
    }
    if stale.iter().any(|(_, draft)| fits(draft, catalog)) {
        println!("Carrying the saved configurations over to the new scan; this can take a minute.");
    }
    let earlier = earlier.and_then(|data| json::parse_strict(data, "world.json").ok());
    let drafts: Vec<Value> = stale.iter().map(|(_, draft)| draft.clone()).collect();
    let results = rebase::rebase_each(catalog, world, &drafts, earlier.as_ref(), None)?;
    for ((path, saved), result) in stale.iter().zip(results) {
        let name = shown_name(saved, path);
        let result = match result {
            Ok(result) => result,
            Err(error) => {
                let reason = format!("an unexpected problem stopped it ({})", error.message.trim_end_matches('.'));
                carried.set_aside(path, name, reason)?;
                continue;
            }
        };
        let Some(draft) = result.get("draft").filter(|draft| !draft.is_null()) else {
            let reason = result.get("reason").and_then(Value::as_str).unwrap_or_default().to_string();
            carried.set_aside(path, name, reason)?;
            continue;
        };
        let left_out = result.get("left_out").is_some_and(Value::truthy);
        // The earlier version is kept first; then the carried-over one takes
        // its place in one step. When either cannot be written, the
        // configuration is kept aside as it was.
        let copy = match left_out.then(|| keep_copy(&carried.folder, path)).transpose() {
            Ok(copy) => copy,
            Err(error) => {
                let reason = format!("its earlier version could not be kept ({})", error.message.trim_end_matches('.'));
                carried.set_aside(path, name, reason)?;
                continue;
            }
        };
        match replace_saved(path, draft) {
            Ok(None) => {}
            Ok(Some(error)) => carried.writable.push((name.clone(), error)),
            Err(error) => {
                if let Some(copy) = copy {
                    let _ = std::fs::remove_file(copy);
                }
                let reason = format!("its new version could not be written ({})", error.message.trim_end_matches('.'));
                carried.set_aside(path, name, reason)?;
                continue;
            }
        }
        let notes = result.get("notes").and_then(Value::as_list).into_iter().flatten();
        let notes = notes.filter_map(Value::as_str).map(str::to_string).collect();
        if left_out {
            carried.part.push((name, notes));
        } else {
            carried.whole.push((name, notes));
        }
    }
    Ok(carried)
}

impl Carried {
    /// Move a saved configuration that is not carried over into the folder
    /// of those kept aside. When it cannot be moved, it would stay where the
    /// editor cannot list it: the start stops and says why, after what became
    /// of the others so far.
    fn set_aside(&mut self, path: &Path, name: String, reason: String) -> cir_config::Result<()> {
        if let Err(error) = keep_aside(&self.folder, path) {
            self.report();
            return Err(Error::profile(format!(
                "The saved configuration \"{name}\" cannot be carried over to the new scan ({reason}), and it could \
                 not be moved to {}: {}. Close any program that has it open, then start again",
                paths::display(&self.folder),
                error.message
            )));
        }
        self.aside.push((paths::stem(&paths::name(path)).to_string(), name, reason));
        Ok(())
    }
}

/// Whether carrying a saved configuration over fits a preset or the game's
/// special places again, which can take a while: one with any of its items
/// still in the game.
fn fits(draft: &Value, catalog: &Value) -> bool {
    let Some(plan) = draft.get("plan") else { return false };
    if plan.get("original_template_id").is_none() && !plan.get("target_registrations").is_some_and(Value::truthy) {
        return false;
    }
    let items: HashSet<&str> = catalog
        .get("items")
        .and_then(Value::as_list)
        .into_iter()
        .flatten()
        .filter_map(|row| row.get("resref").and_then(Value::as_str))
        .collect();
    draft
        .get("profile")
        .and_then(|profile| profile.get("items"))
        .and_then(Value::as_list)
        .is_some_and(|chosen| chosen.iter().filter_map(Value::as_str).any(|item| items.contains(item)))
}

/// A name in the folder of those kept aside for a saved configuration, not
/// taken there.
fn aside_name(folder: &Path, path: &Path) -> cir_config::Result<PathBuf> {
    profiles::make_dirs(folder)?;
    let name = paths::name(path);
    let mut target = folder.join(&name);
    let mut number = 2;
    while paths::exists(&target).map_err(|error| Error::win(&error, &target))? {
        target = folder.join(format!("{} ({number}).json", paths::stem(&name)));
        number += 1;
    }
    Ok(target)
}

/// Move a saved configuration into the folder of those kept aside.
fn keep_aside(folder: &Path, path: &Path) -> cir_config::Result<()> {
    profiles::replace(path, &aside_name(folder, path)?)
}

/// Copy a saved configuration into the folder of those kept aside; the copy.
fn keep_copy(folder: &Path, path: &Path) -> cir_config::Result<PathBuf> {
    let target = aside_name(folder, path)?;
    std::fs::copy(path, &target).map_err(|error| Error::win_pair(&error, path, &target))?;
    Ok(target)
}

/// Put a saved configuration's new version in its place in one step, as the
/// editor saves one; Err when it is not, and the earlier version stays as it
/// was. A read-only one stays read-only; Windows replaces it only while that
/// is lifted. When it cannot be made read-only again, the new version is in
/// place all the same: why comes back.
#[allow(clippy::permissions_set_readonly_false)] // Only the read-only attribute, on Windows.
fn replace_saved(path: &Path, draft: &Value) -> cir_config::Result<Option<String>> {
    let permissions = std::fs::metadata(path).map_err(|error| Error::win(&error, path))?.permissions();
    if !permissions.readonly() {
        return profiles::write_json(path, draft, false).map(|()| None);
    }
    if cfg!(windows) {
        let mut writable = permissions.clone();
        writable.set_readonly(false);
        std::fs::set_permissions(path, writable).map_err(|error| Error::win(&error, path))?;
    }
    let written = profiles::write_json(path, draft, false);
    // Read-only again: the new version, or the earlier one when it stayed.
    let restored = std::fs::set_permissions(path, permissions).map_err(|error| Error::win(&error, path));
    written?;
    Ok(restored.err().map(|error| error.message))
}

/// Whether this program has the console window to itself, as when it was
/// started from Explorer; that window closes with the program.
#[cfg(windows)]
fn owns_console() -> bool {
    #[link(name = "kernel32")]
    extern "system" {
        fn GetConsoleProcessList(processes: *mut u32, count: u32) -> u32;
    }
    let mut processes = [0u32; 2];
    // SAFETY: a buffer of two entries, as passed.
    unsafe { GetConsoleProcessList(processes.as_mut_ptr(), 2) == 1 }
}

#[cfg(not(windows))]
fn owns_console() -> bool {
    false
}

/// Keep a window that would close with the program open until the player
/// has read why it stopped.
fn pause() {
    if owns_console() {
        print!("Press Enter to close this window.");
        let _ = std::io::stdout().flush();
        let _ = std::io::stdin().read_line(&mut String::new());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_configuration_carried_over_whole_is_told_what_its_preset_has_now() {
        let carried = Carried {
            folder: PathBuf::from("earlier scans"),
            whole: vec![
                ("Mine".into(), Vec::new()),
                ("Boss Loot".into(), vec![rebase::PRESET_HAS_MORE.into()]),
                ("Other".into(), Vec::new()),
            ],
            part: Vec::new(),
            aside: Vec::new(),
            writable: Vec::new(),
        };
        assert_eq!(
            carried.lines(),
            [
                "2 saved configurations were made before the game's mods changed and are carried over to the new \
                 scan: \"Mine\", \"Other\"."
                    .to_string(),
                format!(
                    "The saved configuration \"Boss Loot\" was made before the game's mods changed and is carried \
                     over to the new scan. {}.",
                    rebase::PRESET_HAS_MORE
                ),
            ]
        );
    }
}
