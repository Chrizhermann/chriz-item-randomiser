//! The game of the standalone configurator, for a start without arguments.
//!
//! After installation the executable is in the game's `cir` folder (or a
//! folder below it). Its game is the nearest folder at or above the
//! executable's folder that holds `chitin.key`. Nothing else on the PC is
//! searched, so there is never a choice between installations to guess.

use crate::activation::component_installed;
use crate::error::{Error, Result};
use sha2::{Digest, Sha256};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// Every game folder holds the engine's resource index.
pub const GAME_KEY: &str = "chitin.key";
/// cir.tp2 component 0 writes this marker into override
/// (lib/campaign_runtime.tpa).
pub const INSTALL_MARKER: &str = "flrandomiser3.mrk";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    File,
    Folder,
}

/// The entry of `folder` called `name`, ignoring ASCII case, when it is of
/// this kind. A folder that does not exist has no entries; one that cannot
/// be read is an error, so nothing is decided without looking.
fn entry(folder: &Path, name: &str, kind: Kind) -> Result<Option<PathBuf>> {
    let fits = |path: &Path| {
        fs::metadata(path).is_ok_and(|metadata| if kind == Kind::File { metadata.is_file() } else { metadata.is_dir() })
    };
    let direct = folder.join(name);
    if fits(&direct) {
        return Ok(Some(direct));
    }
    let entries = match fs::read_dir(folder) {
        Ok(entries) => entries,
        Err(error) if matches!(error.kind(), io::ErrorKind::NotFound | io::ErrorKind::NotADirectory) => {
            return Ok(None)
        }
        Err(error) => return Err(Error::win(&error, folder)),
    };
    for found in entries {
        let found = found.map_err(|error| Error::win(&error, folder))?;
        if found.file_name().to_str().is_some_and(|found| found.eq_ignore_ascii_case(name)) && fits(&found.path()) {
            return Ok(Some(found.path()));
        }
    }
    Ok(None)
}

/// The nearest of `start` and the folders above it that holds chitin.key.
/// A folder above that cannot be listed is not the game (a game's folder
/// must be readable), so the search goes on above it rather than stopping.
pub fn find_game(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .filter(|folder| !folder.as_os_str().is_empty())
        .find(|folder| matches!(entry(folder, GAME_KEY, Kind::File), Ok(Some(_))))
        .map(Path::to_path_buf)
}

/// Whether Chriz Item Randomiser is installed in this game: its marker in
/// override, or WeiDU.log recording cir.tp2 component 0.
pub fn cir_installed(game: &Path) -> Result<bool> {
    if let Some(overrides) = entry(game, "override", Kind::Folder)? {
        if entry(&overrides, INSTALL_MARKER, Kind::File)?.is_some() {
            return Ok(true);
        }
    }
    let Some(log) = entry(game, "WeiDU.log", Kind::File)? else { return Ok(false) };
    let data = fs::read(&log).map_err(|error| Error::os(&error, &log))?;
    Ok(component_installed(&data, "cir.tp2", 0))
}

/// The language folder WeiDU installed the game's mods in (weidu.conf's
/// `lang_dir`, as written there; the scan ignores its case), when it has a
/// dialog.tlk. None leaves the choice to the scan, which takes the game's
/// only dialog.tlk.
pub fn game_language(game: &Path) -> Result<Option<String>> {
    let Some(conf) = entry(game, "weidu.conf", Kind::File)? else { return Ok(None) };
    let data = fs::read(&conf).map_err(|error| Error::os(&error, &conf))?;
    let text = String::from_utf8_lossy(&data);
    let named = text.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        key.trim().eq_ignore_ascii_case("lang_dir").then(|| value.trim().to_string())
    });
    let Some(named) = named.filter(|name| {
        !name.is_empty() && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    }) else {
        return Ok(None);
    };
    let Some(languages) = entry(game, "lang", Kind::Folder)? else { return Ok(None) };
    let Some(language) = entry(&languages, &named, Kind::Folder)? else { return Ok(None) };
    Ok(entry(&language, "dialog.tlk", Kind::File)?.map(|_| named))
}

/// This game's folder below `base` for its scans and configurations: the
/// game folder's name and a hash of its full path, so each installation
/// keeps its own and finds them again on the next start.
pub fn game_workspace(base: &Path, game: &Path) -> PathBuf {
    let full = crate::paths::display(game);
    // Windows paths ignore case; the same folder gets the same workspace.
    let key = if cfg!(windows) { full.to_lowercase() } else { full };
    let hash: String = Sha256::digest(key.as_bytes()).iter().take(6).map(|byte| format!("{byte:02x}")).collect();
    let name: String =
        game.file_name().map_or_else(|| "game".to_string(), |name| name.to_string_lossy().chars().take(40).collect());
    // Windows does not keep a trailing dot or space in a folder name.
    let name = name.trim_end_matches(['.', ' ']);
    base.join(format!("{}-{hash}", if name.is_empty() { "game" } else { name }))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A folder for one test, removed afterwards.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(label: &str) -> Scratch {
            let path = std::env::temp_dir()
                .join(format!("cir-game-folder-{label}-{}-{}", std::process::id(), crate::profiles::random_hex(8)));
            fs::create_dir_all(&path).unwrap();
            Scratch(crate::paths::resolve(&path, true).unwrap())
        }

        fn file(&self, relative: &str, data: &[u8]) -> PathBuf {
            let path = self.0.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, data).unwrap();
            path
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    /// The game of an executable, as a start without arguments looks for it.
    fn game_of(exe: &Path) -> Option<PathBuf> {
        find_game(exe.parent().unwrap())
    }

    #[test]
    fn the_executable_in_cir_finds_its_game() {
        let scratch = Scratch::new("cir");
        scratch.file("Game/chitin.key", b"KEY V1  ");
        scratch.file("Game/override/flrandomiser3.mrk", b"");
        let exe = scratch.file("Game/cir/cir-configurator.exe", b"");
        let game = game_of(&exe).expect("a game");
        assert_eq!(game, scratch.0.join("Game"));
        assert!(cir_installed(&game).unwrap());
    }

    #[test]
    fn the_executable_in_cir_tools_finds_its_game() {
        let scratch = Scratch::new("tools");
        scratch.file("Game/CHITIN.KEY", b"KEY V1  ");
        scratch.file("Game/WeiDU.log", b"// Log of Currently Installed WeiDU Mods\r\n~CIR/CIR.TP2~ #0 #0 // Chriz Item Randomiser: 1.0.0\r\n");
        let exe = scratch.file("Game/cir/tools/cir-configurator.exe", b"");
        let game = game_of(&exe).expect("a game");
        assert_eq!(game, scratch.0.join("Game"));
        assert!(cir_installed(&game).unwrap());
    }

    #[test]
    fn no_game_above_the_executable() {
        let scratch = Scratch::new("none");
        // A game beside the executable's folders is not above it.
        scratch.file("Other game/chitin.key", b"KEY V1  ");
        let exe = scratch.file("Downloads/cir/cir-configurator.exe", b"");
        assert_eq!(game_of(&exe), None);
        // A folder called chitin.key is not the index.
        fs::create_dir_all(scratch.0.join("Downloads/chitin.key")).unwrap();
        assert_eq!(game_of(&exe), None);
    }

    #[test]
    fn the_nearest_game_wins() {
        let scratch = Scratch::new("nearest");
        scratch.file("Outer/chitin.key", b"KEY V1  ");
        scratch.file("Outer/Inner/chitin.key", b"KEY V1  ");
        let exe = scratch.file("Outer/Inner/cir/cir-configurator.exe", b"");
        assert_eq!(game_of(&exe), Some(scratch.0.join("Outer/Inner")));
        // The executable beside chitin.key: the game is its own folder.
        let beside = scratch.file("Outer/cir-configurator.exe", b"");
        assert_eq!(game_of(&beside), Some(scratch.0.join("Outer")));
    }

    #[test]
    fn cir_not_installed() {
        let scratch = Scratch::new("missing");
        scratch.file("Game/chitin.key", b"KEY V1  ");
        let exe = scratch.file("Game/cir/cir-configurator.exe", b"");
        let game = game_of(&exe).expect("a game");
        assert!(!cir_installed(&game).unwrap());
        // Other components, other mods and commented lines do not count.
        scratch.file(
            "Game/WeiDU.log",
            b"// Log of Currently Installed WeiDU Mods\n// ~CIR/CIR.TP2~ #0 #0 // removed\n\
              ~CIR/CIR.TP2~ #0 #9000 // Cespenar can forge SoA items\n\
              ~RANDOMISER/RANDOMISER.TP2~ #0 #0 // Item Randomiser\n~CIRX/CIRX.TP2~ #0 #0 // another mod\n",
        );
        scratch.file("Game/override/flrandomiser1.mrk", b"");
        assert!(!cir_installed(&game).unwrap());
    }

    #[test]
    fn cir_is_installed_by_marker_or_log() {
        let scratch = Scratch::new("installed");
        scratch.file("Marker/chitin.key", b"KEY V1  ");
        scratch.file("Marker/Override/FLRandomiser3.MRK", b"");
        assert!(cir_installed(&scratch.0.join("Marker")).unwrap());
        // As MOD_IS_INSTALLED: any folder, the setup- prefix, any language.
        for (index, line) in
            ["~cir/cir.tp2~ #0 #0", "~setup-cir.tp2~ #1 #0 // CIR", "  ~mods\\cir\\CIR.tp2~   #0   #0"].iter().enumerate()
        {
            let game = format!("Log{index}");
            scratch.file(&format!("{game}/chitin.key"), b"KEY V1  ");
            scratch.file(&format!("{game}/weidu.log"), format!("{line}\n").as_bytes());
            assert!(cir_installed(&scratch.0.join(&game)).unwrap(), "{line}");
        }
    }

    #[test]
    fn the_language_is_weidus() {
        let scratch = Scratch::new("language");
        let game = scratch.0.join("Game");
        scratch.file("Game/lang/en_US/dialog.tlk", b"TLK V1  ");
        scratch.file("Game/lang/de_DE/dialog.tlk", b"TLK V1  ");
        assert_eq!(game_language(&game).unwrap(), None);
        scratch.file("Game/weidu.conf", b"lang_dir = de_de\r\n");
        assert_eq!(game_language(&game).unwrap().as_deref(), Some("de_de"));
        // A language the game does not have is left to the scan.
        scratch.file("Game/weidu.conf", b"lang_dir = fr_fr\n");
        assert_eq!(game_language(&game).unwrap(), None);
        scratch.file("Game/weidu.conf", b"lang_dir = ../en_US\n");
        assert_eq!(game_language(&game).unwrap(), None);
    }

    #[test]
    fn each_game_keeps_its_workspace() {
        let base = Path::new("C:/Users/player/AppData/Local/Chriz Item Randomiser");
        let first = game_workspace(base, Path::new("D:/Games/Baldur's Gate II Enhanced Edition"));
        let second = game_workspace(base, Path::new("E:/Games/Baldur's Gate II Enhanced Edition"));
        assert_ne!(first, second);
        assert_eq!(first, game_workspace(base, Path::new("D:/Games/Baldur's Gate II Enhanced Edition")));
        let name = first.file_name().unwrap().to_string_lossy().into_owned();
        assert!(name.starts_with("Baldur's Gate II Enhanced Edition-") && name.len() == 33 + 1 + 12, "{name}");
        let long = game_workspace(base, Path::new("D:/Games/A folder name that is far longer than forty characters"));
        assert_eq!(long.file_name().unwrap().to_string_lossy().chars().count(), 40 + 1 + 12);
        let root = game_workspace(base, Path::new(if cfg!(windows) { "D:/" } else { "/" }));
        assert!(root.file_name().unwrap().to_string_lossy().starts_with("game-"));
    }
}
