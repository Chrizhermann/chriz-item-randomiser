//! A start without arguments, as after a double-click on the executable in
//! the game's `cir` folder: find that game, check that Chriz Item Randomiser
//! is installed there, scan it into this player's application data and open
//! the editor in the browser with Apply for that game. When there is no game
//! above the executable, or CIR is not installed in it, the player chooses
//! the game folder. Explicit commands are not affected.

use crate::dialog;
use crate::server::{self, Announce};
use cir_config::editor::{self, Editor};
use cir_config::game_folder::{cir_installed, find_game, game_language, game_workspace};
use cir_config::json::{Dict, Value};
use cir_config::{paths, profiles, resources, sources, Error};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

/// The folder in this player's application data with every game's scans
/// and configurations, one folder per game.
const DATA_FOLDER: &str = "Chriz Item Randomiser";
/// The folder in a game's configurations for those its scan cannot open.
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
    println!("Reading the game's items, areas, creatures and shops; this can take a minute.");
    let (catalog, world) = scans(game, language.as_deref(), &workspace)
        .map_err(|error| format!("Could not read the game: {}", error.message))?;
    println!("Scans and configurations: {}", paths::display(&workspace));
    // Game files the world scan could not read and left out, as `world scan` names them.
    for note in sources::unreadable_notes(&world) {
        println!("{note}.");
    }
    let editor = editor(catalog, world, &workspace.join("configurations"), game)
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
/// so after its mods changed, the configurations saved before cannot be
/// opened with the new scans, and the editor would refuse to list them:
/// they are kept aside first. The editor reopens the configuration applied
/// last, as the `ui` command does, or starts with a new one when that
/// cannot be reopened.
fn editor(catalog: Value, world: Value, configurations: &Path, game: &Path) -> cir_config::Result<Editor> {
    let earlier = set_aside(configurations, &catalog, &world)?;
    if let Some((folder, count)) = &earlier {
        let (number, verb) = match count {
            1 => ("One saved configuration was".to_string(), "it was"),
            _ => (format!("{count} saved configurations were"), "they were"),
        };
        println!(
            "{number} made before the game's mods changed and cannot be opened with the new scan; \
             {verb} moved to {}.",
            paths::display(folder)
        );
    }
    let applied = configurations.join("prepared").join("last-applied.json");
    if !paths::is_file(&applied).map_err(|error| Error::win(&error, &applied))? {
        return Editor::new(catalog, world, configurations, None, None, Some(game));
    }
    match Editor::new(catalog.clone(), world.clone(), configurations, None, None, Some(game)) {
        Ok(editor) => Ok(editor),
        Err(reopening) => {
            // Everything before reopening is checked the same way with a new
            // configuration; when it passes, reopening was what failed.
            let (profile, plan) = Editor::new_draft(&catalog, &world)?;
            let editor = Editor::new(catalog, world, configurations, Some(profile), Some(plan), Some(game))?;
            let reason = if earlier.is_some() { String::new() } else { format!(" ({})", reopening.message) };
            println!("The configuration applied last cannot be reopened{reason}; the editor starts with a new one.");
            Ok(editor)
        }
    }
}

/// Move the saved configurations these scans cannot open (made before the
/// game's mods changed, or damaged) into the `earlier scans` folder, where
/// the editor does not look. The folder and how many moved, if any did.
fn set_aside(configurations: &Path, catalog: &Value, world: &Value) -> cir_config::Result<Option<(PathBuf, usize)>> {
    let folder = configurations.join(EARLIER);
    let mut moved = 0;
    for path in editor::saved_files(configurations)? {
        if profiles::read_json(&path).and_then(|draft| editor::checked_draft(catalog, world, &draft)).is_ok() {
            continue;
        }
        profiles::make_dirs(&folder)?;
        let name = paths::name(&path);
        let mut target = folder.join(&name);
        let mut number = 2;
        while paths::exists(&target).map_err(|error| Error::win(&error, &target))? {
            target = folder.join(format!("{} ({number}).json", paths::stem(&name)));
            number += 1;
        }
        profiles::replace(&path, &target)?;
        moved += 1;
    }
    Ok((moved > 0).then_some((folder, moved)))
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
