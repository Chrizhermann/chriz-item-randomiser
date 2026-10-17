//! Apply exported settings to an explicitly selected, preinstalled test
//! runtime (the reference's `activation.py`).
//!
//! Runtime installation is separate. Exported revisions stay for older saves;
//! the next-game pointer is published last, with the game closed.

use crate::error::{Error, Result};
use crate::json::Value;
use crate::paths;
use crate::profiles::read_json;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

/// The files of the latest export in its folder. Apply copies only these, so
/// a file an older export left there never reaches the game.
pub const EXPORT_FILES: &str = "export-files.json";
pub const EXPORT_FILES_SCHEMA: &str = "flir-export-files-v1";
/// cir.tp2 installs the runtime with this marker and UI adapter before any
/// configuration exists. A hand-staged runtime has no marker and is
/// recognised by its existing next-game pointer instead.
pub const INSTALLED_RUNTIME: [&str; 2] = ["override/flrandomiser3.mrk", "override/M_FLPRUI.lua"];
/// Applied settings and kept campaign revisions; prototype/M_FLPR.lua reads them here.
pub const CAMPAIGN_DATA: &str = "randomiser-campaigns";
/// Item Randomiser's own randomisation (Mode 1 or Mode 2) removes items when
/// it is installed; its More Spell Shield scrolls (component 560) change the
/// stock a campaign restores. Item Randomiser does not know CIR and can be
/// installed after it.
pub const ITEM_RANDOMISER_MARKERS: [&str; 2] = ["override/flrandomiser1.mrk", "override/flrandomiser2.mrk"];

fn is_hex(c: char) -> bool {
    c.is_ascii_digit() || ('a'..='f').contains(&c)
}

/// The quoted 64-digit hash at `chars[at..]`, if any.
fn quoted_hash(chars: &[char], at: usize) -> Option<String> {
    if chars.get(at) != Some(&'"') || chars.len() < at + 66 || chars[at + 65] != '"' {
        return None;
    }
    let hash = &chars[at + 1..at + 65];
    hash.iter().all(|&c| is_hex(c)).then(|| hash.iter().collect())
}

/// `return\s+(?:"<hash>"|\{\s*(?:\[\d+\]\s*=\s*"<hash>"\s*,?\s*)*\})\s*` as a full match.
fn gate_shape(chars: &[char]) -> bool {
    let space = |at: &mut usize| {
        while *at < chars.len() && crate::py::is_space(chars[*at]) {
            *at += 1;
        }
    };
    let word: Vec<char> = "return".chars().collect();
    if chars.len() < word.len() || chars[..word.len()] != word[..] {
        return false;
    }
    let mut at = word.len();
    let before = at;
    space(&mut at);
    if at == before {
        return false;
    }
    if quoted_hash(chars, at).is_some() {
        at += 66;
    } else {
        if chars.get(at) != Some(&'{') {
            return false;
        }
        at += 1;
        space(&mut at);
        loop {
            match chars.get(at) {
                Some('}') => {
                    at += 1;
                    break;
                }
                Some('[') => {
                    at += 1;
                    let digits = at;
                    while at < chars.len() && chars[at].is_numeric() && chars[at].to_digit(10).is_some() {
                        at += 1;
                    }
                    if at == digits || chars.get(at) != Some(&']') {
                        return false;
                    }
                    at += 1;
                    space(&mut at);
                    if chars.get(at) != Some(&'=') {
                        return false;
                    }
                    at += 1;
                    space(&mut at);
                    if quoted_hash(chars, at).is_none() {
                        return false;
                    }
                    at += 66;
                    space(&mut at);
                    if chars.get(at) == Some(&',') {
                        at += 1;
                    }
                    space(&mut at);
                }
                _ => return false,
            }
        }
    }
    space(&mut at);
    at == chars.len()
}

/// The campaign revisions a source-gate file lists.
fn gate_revisions(path: &Path) -> Result<BTreeSet<String>> {
    if !paths::exists(path).map_err(|error| Error::win(&error, path))? {
        return Ok(BTreeSet::new());
    }
    let data = fs::read(path).map_err(|error| Error::os(&error, path))?;
    let text = std::str::from_utf8(&data)
        .map_err(|error| Error::value(crate::json::utf8_error_message(&data, &error)))?
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    // re.sub(r"\\([0-9]{3})", chr(int(...))): Lua's decimal escapes.
    let raw: Vec<char> = text.chars().collect();
    let mut chars = Vec::with_capacity(raw.len());
    let mut index = 0;
    while index < raw.len() {
        if raw[index] == '\\' && index + 3 < raw.len() && raw[index + 1..index + 4].iter().all(char::is_ascii_digit) {
            let code: u32 = raw[index + 1..index + 4].iter().collect::<String>().parse().expect("digits");
            chars.push(char::from_u32(code).unwrap_or('\u{FFFD}'));
            index += 4;
        } else {
            chars.push(raw[index]);
            index += 1;
        }
    }
    if !gate_shape(&chars) {
        return Err(Error::profile("Unrecognized source-gate settings; keep the existing test configuration"));
    }
    let mut hashes = BTreeSet::new();
    let mut at = 0;
    while at < chars.len() {
        match quoted_hash(&chars, at) {
            Some(hash) => {
                hashes.insert(hash);
                at += 66;
            }
            None => at += 1,
        }
    }
    Ok(hashes)
}

/// Refuse while the game, its loader, WeiDU or a mod installer runs.
pub fn require_closed(game: &Path) -> Result<()> {
    if !cfg!(windows) {
        return Err(Error::profile("Test-game activation currently supports Windows only"));
    }
    let failure = || Error::profile("Could not check whether the test game is closed; settings were not applied");
    let script = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Where-Object { \
                  $_.Name -match '^(Baldur|InfinityLoader|weidu)(\\.exe|$)' -or \
                  $_.Name -like 'setup-*.exe' } | Select-Object Name,ExecutablePath) | ConvertTo-Json -Compress";
    let mut command = std::process::Command::new("powershell");
    command
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().map_err(|error| Error::win(&error, Path::new("powershell")))?;
    let stdout = child.stdout.take();
    let reader = std::thread::spawn(move || {
        let mut text = String::new();
        if let Some(mut stdout) = stdout {
            use std::io::Read;
            let _ = stdout.read_to_string(&mut text);
        }
        text
    });
    let stderr = child.stderr.take();
    let error_reader = std::thread::spawn(move || {
        if let Some(mut stderr) = stderr {
            use std::io::Read;
            let mut sink = Vec::new();
            let _ = stderr.read_to_end(&mut sink);
        }
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if std::time::Instant::now() < deadline => std::thread::sleep(std::time::Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(failure());
            }
        }
    };
    let output = reader.join().unwrap_or_default();
    let _ = error_reader.join();
    if !status.success() {
        return Err(failure());
    }
    let text = crate::py::strip(&output);
    let processes = crate::json::parse_lenient(if text.is_empty() { "[]" } else { text })?;
    let processes = match processes {
        Value::Dict(_) => vec![processes],
        Value::List(list) => list,
        _ => Vec::new(),
    };
    let game = paths::resolve(game, false).map_err(|(error, at)| Error::win(&error, &at))?;
    for process in processes {
        let executable = process.get("ExecutablePath").filter(|value| value.truthy());
        let inside = match executable.and_then(Value::as_str) {
            None => true,
            Some(executable) => {
                let resolved = paths::resolve(Path::new(executable), false).map_err(|(error, at)| Error::win(&error, &at))?;
                paths::is_relative_to(&resolved, &game)
            }
        };
        if inside {
            return Err(Error::profile("Close this test game and its installer before applying settings"));
        }
    }
    Ok(())
}

/// One WeiDU.log component line: (tp2 path, component number).
fn weidu_entries(log: &[u8]) -> Vec<(&[u8], &[u8])> {
    let is_space = |byte: u8| matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | b'\x0b' | b'\x0c');
    let is_word = |byte: u8| byte.is_ascii_alphanumeric() || byte == b'_';
    let mut result = Vec::new();
    let mut start = 0;
    while start <= log.len() {
        if start == 0 || log[start - 1] == b'\n' {
            let attempt = (|| {
                let mut at = start;
                while at < log.len() && is_space(log[at]) {
                    at += 1;
                }
                if log.get(at) != Some(&b'~') {
                    return None;
                }
                at += 1;
                let path_start = at;
                while at < log.len() && !matches!(log[at], b'~' | b'\r' | b'\n') {
                    at += 1;
                }
                if at == path_start || log.get(at) != Some(&b'~') {
                    return None;
                }
                let path = &log[path_start..at];
                at += 1;
                let gap = at;
                while at < log.len() && is_space(log[at]) {
                    at += 1;
                }
                if at == gap || log.get(at) != Some(&b'#') {
                    return None;
                }
                at += 1;
                let digits = at;
                while at < log.len() && log[at].is_ascii_digit() {
                    at += 1;
                }
                if at == digits {
                    return None;
                }
                let gap = at;
                while at < log.len() && is_space(log[at]) {
                    at += 1;
                }
                if at == gap || log.get(at) != Some(&b'#') {
                    return None;
                }
                at += 1;
                let number_start = at;
                while at < log.len() && log[at].is_ascii_digit() {
                    at += 1;
                }
                if at == number_start || log.get(at).is_some_and(|&byte| is_word(byte)) {
                    return None;
                }
                Some((path, &log[number_start..at], at))
            })();
            if let Some((path, number, end)) = attempt {
                result.push((path, number));
                start = end.max(start + 1);
                continue;
            }
        }
        start += 1;
    }
    result
}

/// What of Item Randomiser in this game CIR cannot be combined with.
pub fn item_randomiser_conflict(game: &Path) -> Result<Option<&'static str>> {
    for name in ITEM_RANDOMISER_MARKERS {
        let path = game.join(name);
        if paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
            return Ok(Some("Item Randomiser's randomisation (Mode 1 or Mode 2)"));
        }
    }
    let log = game.join("WeiDU.log");
    let data = if paths::is_file(&log).map_err(|error| Error::win(&error, &log))? {
        fs::read(&log).map_err(|error| Error::os(&error, &log))?
    } else {
        Vec::new()
    };
    // As MOD_IS_INSTALLED randomiser.tp2 560, independent of path, prefix and language.
    for (path, component) in weidu_entries(&data) {
        let normalized = crate::py::replace_bytes(path, b"\\", b"/", None);
        let name = normalized.rsplit(|&byte| byte == b'/').next().unwrap_or(&[]).to_ascii_lowercase();
        let number: u128 = std::str::from_utf8(component).ok().and_then(|text| text.parse().ok()).unwrap_or(u128::MAX);
        if (name == b"randomiser.tp2" || name == b"setup-randomiser.tp2") && number == 560 {
            return Ok(Some("Item Randomiser's More Spell Shield scrolls"));
        }
    }
    Ok(None)
}

/// The resolved game folder, when it is the scanned game and has the runtime.
pub fn validate_runtime(game: &Path, catalog: &Value) -> Result<PathBuf> {
    let game = paths::resolve(game, true).map_err(|(error, at)| Error::win(&error, &at))?;
    let scanned = catalog.at("game_root")?.as_str().ok_or_else(|| Error::type_error("game_root must be a string"))?;
    let scanned = paths::resolve(Path::new(&paths::display(Path::new(scanned))), false)
        .map_err(|(error, at)| Error::win(&error, &at))?;
    if paths::path_cmp(&game, &scanned) != std::cmp::Ordering::Equal {
        return Err(Error::profile("The activation game must be the game used by this scan"));
    }
    if let Some(conflict) = item_randomiser_conflict(&game)? {
        return Err(Error::profile(format!(
            "Chriz Item Randomiser cannot be combined with {conflict}; uninstall it from this game before applying settings"
        )));
    }
    let mut required = vec!["override/M_FLPR.lua".to_string(), "override/FLPRRun.menu".to_string()];
    let mut installed = true;
    for name in INSTALLED_RUNTIME {
        let path = game.join(name);
        if !paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
            installed = false;
            break;
        }
    }
    if !installed {
        required.push(format!("{CAMPAIGN_DATA}/next.lua"));
    }
    for name in required {
        let path = game.join(&name);
        if !paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
            return Err(Error::profile(format!("Install Chriz Item Randomiser in this game first: {name}")));
        }
    }
    Ok(game)
}

/// `PurePosixPath(name).parts`.
fn posix_parts(name: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    if name.starts_with('/') {
        parts.push("/");
    }
    parts.extend(name.split('/').filter(|part| !part.is_empty() && *part != "."));
    parts
}

/// Copy the existing export layout without touching saves or ordinary stock.
pub fn deploy_export(game: &Path, output: &Path) -> Result<()> {
    require_closed(game)?;
    let incomplete = || Error::profile("The runtime export is incomplete; export the settings again");
    let listing = match read_json(&output.join(EXPORT_FILES)) {
        Ok(listing) => listing,
        Err(error) if error.kind == crate::error::ErrorKind::Os || error.kind == crate::error::ErrorKind::Profile => {
            return Err(incomplete())
        }
        Err(error) => return Err(error),
    };
    let names = if listing.get("schema").and_then(Value::as_str) == Some(EXPORT_FILES_SCHEMA) {
        listing.get("files")
    } else {
        None
    };
    let names: Vec<String> = match names {
        Some(Value::List(list)) if list.iter().all(Value::is_str) => {
            list.iter().map(|name| name.as_str().unwrap().to_string()).collect()
        }
        _ => return Err(incomplete()),
    };
    if !names.iter().any(|name| name == "next.lua") || !names.iter().any(|name| name == "source-gate.lua") {
        return Err(incomplete());
    }
    for name in &names {
        let path = output.join(name);
        if !paths::is_file(&path).map_err(|error| Error::win(&error, &path))? {
            return Err(incomplete());
        }
    }
    let previous = gate_revisions(&game.join(CAMPAIGN_DATA).join("source-gate.lua"))?;
    let exported = gate_revisions(&output.join("source-gate.lua"))?;
    if !previous.is_subset(&exported) {
        return Err(Error::profile(
            "Use this game's previous configuration workspace: this export is missing settings needed by older saves",
        ));
    }
    let game_root = paths::resolve(game, false).map_err(|(error, at)| Error::win(&error, &at))?;
    let mut changes: Vec<(PathBuf, Vec<u8>)> = Vec::new();
    let mut positions: HashMap<String, usize> = HashMap::new();
    let mut sorted_names = names.clone();
    sorted_names.sort();
    for name in sorted_names {
        let parts = posix_parts(&name);
        if parts.first() == Some(&"/") || parts.is_empty() || parts.contains(&"..") {
            return Err(Error::profile("An export destination is outside the test game"));
        }
        let source: PathBuf = parts.iter().fold(output.to_path_buf(), |path, part| path.join(part));
        let mut relative: Vec<&str> = parts.clone();
        if relative[0] == "resource-overrides" {
            relative[0] = "override";
        } else if relative[0] == "game-overrides" {
            relative.remove(0);
        } else {
            relative.insert(0, CAMPAIGN_DATA);
        }
        let relative_text = relative.join("\\");
        let target = paths::resolve(&game.join(&relative_text), false).map_err(|(error, at)| Error::win(&error, &at))?;
        if !paths::is_relative_to(&target, &game_root) {
            return Err(Error::profile("An export destination is outside the test game"));
        }
        let content = fs::read(&source).map_err(|error| Error::os(&error, &source))?;
        let key = crate::py::lower(&target.to_string_lossy());
        if let Some(position) = positions.get(&key) {
            if changes[*position].1 != content {
                return Err(Error::profile(format!("Conflicting export files: {relative_text}")));
            }
        }
        if paths::exists(&target).map_err(|error| Error::win(&error, &target))? {
            let existing = fs::read(&target).map_err(|error| Error::os(&error, &target))?;
            if existing == content {
                continue;
            }
            if relative.join("/").starts_with(&format!("{CAMPAIGN_DATA}/campaign-store/revisions/")) {
                return Err(Error::profile(format!("An older campaign revision differs: {relative_text}")));
            }
        }
        match positions.get(&key) {
            Some(position) => changes[*position].1 = content,
            None => {
                positions.insert(key, changes.len());
                changes.push((target, content));
            }
        }
    }
    let pointer = game_root.join(CAMPAIGN_DATA).join("next.lua");
    let is_pointer = |path: &Path| paths::path_cmp(path, &pointer) == std::cmp::Ordering::Equal;
    changes.sort_by(|a, b| {
        is_pointer(&a.0).cmp(&is_pointer(&b.0)).then_with(|| a.0.to_string_lossy().as_bytes().cmp(b.0.to_string_lossy().as_bytes()))
    });
    // Roll back an actual copy error; no persistent lab-recovery framework.
    let backup_root = paths::parent(output).join(format!("apply-backup-{}", crate::profiles::random_name()));
    fs::create_dir(&backup_root).map_err(|error| Error::win(&error, &backup_root))?;
    let mut written: Vec<(PathBuf, Option<PathBuf>)> = Vec::new();
    let result = (|| -> Result<()> {
        for (index, (target, content)) in changes.iter().enumerate() {
            let backup = if paths::exists(target).map_err(|error| Error::win(&error, target))? {
                let backup = backup_root.join(index.to_string());
                fs::copy(target, &backup).map_err(|error| Error::os(&error, target))?;
                Some(backup)
            } else {
                None
            };
            crate::profiles::make_dirs(&paths::parent(target))?;
            written.push((target.clone(), backup));
            fs::write(target, content).map_err(|error| Error::os(&error, target))?;
        }
        Ok(())
    })();
    if let Err(error) = result {
        if error.kind == crate::error::ErrorKind::Os {
            for (target, backup) in written.iter().rev() {
                match backup {
                    Some(backup) => {
                        let _ = fs::copy(backup, target);
                    }
                    None => {
                        let _ = fs::remove_file(target);
                    }
                }
            }
        }
        let _ = fs::remove_dir_all(&backup_root);
        return Err(error);
    }
    let _ = fs::remove_dir_all(&backup_root);
    Ok(())
}
