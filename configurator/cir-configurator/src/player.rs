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
    // were made with; this start's scan replaces it.
    let earlier = std::fs::read(workspace.join("world.json")).ok();
    println!("Reading the game's items, areas, creatures and shops; this can take a minute.");
    let (catalog, world) = scans(game, language.as_deref(), &workspace)
        .map_err(|error| format!("Could not read the game: {}", error.message))?;
    println!("Scans and configurations: {}", paths::display(&workspace));
    // Game files the world scan could not read and left out, as `world scan` names them.
    for note in sources::unreadable_notes(&world) {
        println!("{note}.");
    }
    let editor = editor(catalog, world, earlier.as_deref(), &workspace.join("configurations"), game)
        .map_err(|error| format!("Could not open the editor: {}", error.message))?;
    let open_browser = std::env::var_os(NO_BROWSER).is_none();
    server::run(editor, 0, open_browser, Announce::Player)
        .map_err(|error| format!("Could not open the editor: {}", error.message))?;
    Ok(())
}

/// The game's item and world scans, written to `catalog.json` and
/// `world.json` of its workspace as the `scan` and `world scan` commands
/// write them.
fn scans(game: &Path, language: Option<&str>, workspace: &Path) -> cir_config::Result<(Value, Value)> {
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
    profiles::write_json(&destination, &world, false)?;
    Ok((catalog, world))
}

/// The editor with Apply for this game. Every start scans the game again,
/// so after its mods changed, the configurations saved before do not fit the
/// new scan, and the editor would refuse to list them: they are carried over
/// to it first (`carry_over`). The editor reopens the configuration applied
/// last, as the `ui` command does, or starts with a new one when that cannot
/// be reopened. When it was kept aside, the player is told so once: it is no
/// longer the one to reopen.
fn editor(
    catalog: Value,
    world: Value,
    earlier: Option<&[u8]>,
    configurations: &Path,
    game: &Path,
) -> cir_config::Result<Editor> {
    let carried = carry_over(configurations, &catalog, &world, earlier)?;
    carried.report();
    let applied = configurations.join("prepared").join("last-applied.json");
    if !paths::is_file(&applied).map_err(|error| Error::win(&error, &applied))? {
        return Editor::new(catalog, world, configurations, None, None, Some(game));
    }
    if let Some(identity) = applied_last(&applied) {
        let path = configurations.join(format!("{identity}.json"));
        if !paths::exists(&path).map_err(|error| Error::win(&error, &path))? {
            match carried.aside.iter().find(|(stem, _, _)| *stem == identity) {
                Some((_, name, _)) => println!(
                    "The configuration applied last, \"{name}\", could not be carried over, so the editor starts \
                     with a new one."
                ),
                None => println!(
                    "The configuration applied last is no longer among the saved configurations, so the editor \
                     starts with a new one."
                ),
            }
            // Told once: the next start does not look for it again. Apply
            // names the configuration it applies anew.
            let _ = std::fs::remove_file(&applied);
            let (profile, plan) = Editor::new_draft(&catalog, &world, None)?;
            return Editor::new(catalog, world, configurations, Some(profile), Some(plan), Some(game));
        }
    }
    match Editor::new(catalog.clone(), world.clone(), configurations, None, None, Some(game)) {
        Ok(editor) => Ok(editor),
        Err(reopening) => {
            // Everything before reopening is checked the same way with a new
            // configuration; when it passes, reopening was what failed.
            let (profile, plan) = Editor::new_draft(&catalog, &world, None)?;
            let editor = Editor::new(catalog, world, configurations, Some(profile), Some(plan), Some(game))?;
            println!(
                "The configuration applied last cannot be reopened ({}); the editor starts with a new one.",
                reopening.message
            );
            Ok(editor)
        }
    }
}

/// The identity of the configuration applied last, as Apply records it.
fn applied_last(applied: &Path) -> Option<String> {
    let pointer = profiles::read_json(applied).ok()?;
    let identity = pointer.get("id")?.as_str()?;
    profiles::is_profile_id(identity).then(|| identity.to_string())
}

/// What became of the saved configurations made before the game's mods
/// changed.
struct Carried {
    /// Where those kept aside are.
    folder: PathBuf,
    /// Carried over whole: their names.
    whole: Vec<String>,
    /// Carried over in part: the name and what was left out, in plain words.
    part: Vec<(String, Vec<String>)>,
    /// Kept aside: the file's identity, the name and why.
    aside: Vec<(String, String, String)>,
}

impl Carried {
    fn report(&self) {
        let folder = paths::display(&self.folder);
        match self.whole.as_slice() {
            [] => {}
            [name] => println!(
                "The saved configuration \"{name}\" was made before the game's mods changed and is carried over \
                 to the new scan."
            ),
            names => println!(
                "{} saved configurations were made before the game's mods changed and are carried over to the new \
                 scan: {}.",
                names.len(),
                names.iter().map(|name| format!("\"{name}\"")).collect::<Vec<_>>().join(", ")
            ),
        }
        for (name, notes) in &self.part {
            println!(
                "The saved configuration \"{name}\" was made before the game's mods changed and is carried over \
                 to the new scan, but not all of it: {}. Its earlier version is kept in {folder}.",
                notes.join(". ")
            );
        }
        for (_, name, reason) in &self.aside {
            println!(
                "The saved configuration \"{name}\" cannot be carried over to the new scan: {reason}. It was moved \
                 to {folder}."
            );
        }
    }
}

/// Carry the saved configurations this scan cannot open (made before the
/// game's mods changed) over to it, as `rebase_drafts` does, each in its own
/// file. What no longer fits is left out; the earlier version of one carried
/// over in part is kept in the `earlier scans` folder, where the editor does
/// not look. One that cannot be carried over (or read) is moved there.
/// `earlier` is the world scan they were made with, if there is one.
fn carry_over(
    configurations: &Path,
    catalog: &Value,
    world: &Value,
    earlier: Option<&[u8]>,
) -> cir_config::Result<Carried> {
    let mut carried =
        Carried { folder: configurations.join(EARLIER), whole: Vec::new(), part: Vec::new(), aside: Vec::new() };
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
    println!("Carrying the saved configurations over to the new scan; this can take a minute.");
    let earlier = earlier.and_then(|data| json::parse_strict(data, "world.json").ok());
    let drafts: Vec<Value> = stale.iter().map(|(_, draft)| draft.clone()).collect();
    let results = rebase::rebase_drafts(catalog, world, &drafts, earlier.as_ref(), None)?;
    for ((path, saved), result) in stale.iter().zip(results) {
        let file = paths::name(path);
        let name = saved
            .get("profile")
            .and_then(|profile| profile.get("name"))
            .and_then(Value::as_str)
            .filter(|name| !name.trim().is_empty() && !name.chars().any(char::is_control))
            .map_or_else(|| file.clone(), str::to_string);
        match result.get("draft").filter(|draft| !draft.is_null()) {
            Some(draft) => {
                let left_out = result.get("left_out").is_some_and(Value::truthy);
                if left_out {
                    keep_aside(&carried.folder, path)?;
                }
                profiles::write_json(path, draft, false)?;
                if left_out {
                    let notes = result.get("notes").and_then(Value::as_list).into_iter().flatten();
                    carried.part.push((name, notes.filter_map(Value::as_str).map(str::to_string).collect()));
                } else {
                    carried.whole.push(name);
                }
            }
            None => {
                keep_aside(&carried.folder, path)?;
                let reason = result.get("reason").and_then(Value::as_str).unwrap_or_default().to_string();
                carried.aside.push((paths::stem(&file).to_string(), name, reason));
            }
        }
    }
    Ok(carried)
}

/// Move a saved configuration into the folder of those kept aside, under a
/// name not taken there.
fn keep_aside(folder: &Path, path: &Path) -> cir_config::Result<()> {
    profiles::make_dirs(folder)?;
    let name = paths::name(path);
    let mut target = folder.join(&name);
    let mut number = 2;
    while paths::exists(&target).map_err(|error| Error::win(&error, &target))? {
        target = folder.join(format!("{} ({number}).json", paths::stem(&name)));
        number += 1;
    }
    profiles::replace(path, &target)
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
