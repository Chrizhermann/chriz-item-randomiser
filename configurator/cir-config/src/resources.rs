//! Read-only, post-install resource access for BG:EE/BG2:EE and merged EET
//! (the reference's `resources.py`).
//!
//! `scan_game` indexes effective ITMs; `GameResources` reads caller-selected
//! resource types. Override entries supersede chitin.key. KEY/BIFF/TLK V1 and
//! ITM V1 are supported; compressed archives are rejected. Nothing here
//! writes to the game. Formats: https://gibberlings3.github.io/iesdp/file_formats/

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::paths;
use crate::py::{hex, u16_le, u32_le};
use crate::zip;
use sha2::{Digest, Sha256};
use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

const CHANGED: &str = "The installation changed during scanning; no catalog was produced.";

pub fn sha256_hex(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}

/// An OSError inside a scan becomes "Unable to read the supplied installation".
fn unreadable(error: Error) -> Error {
    if error.kind == crate::error::ErrorKind::Os {
        Error::scan(format!("Unable to read the supplied installation: {}", error.message))
    } else {
        error
    }
}

// ---------------------------------------------------------------- snapshot

/// What `os.stat` identifies a file by: device, index, mode, size, write and
/// creation times.
type Identity = [u64; 6];

#[cfg(windows)]
fn identity(path: &Path) -> io::Result<Identity> {
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::io::AsRawHandle;

    #[repr(C)]
    #[derive(Default)]
    struct Info {
        attributes: u32,
        creation: [u32; 2],
        access: [u32; 2],
        write: [u32; 2],
        volume_serial: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GetFileInformationByHandle(handle: *mut core::ffi::c_void, info: *mut Info) -> i32;
    }
    const FILE_READ_ATTRIBUTES: u32 = 0x80;
    const SHARE_ALL: u32 = 7;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    let file = fs::OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .share_mode(SHARE_ALL)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)?;
    let mut info = Info::default();
    // SAFETY: the handle is open for the duration of the call and `info` is a
    // correctly laid out BY_HANDLE_FILE_INFORMATION.
    let ok = unsafe { GetFileInformationByHandle(file.as_raw_handle() as *mut _, &mut info) };
    if ok == 0 {
        return Err(io::Error::last_os_error());
    }
    let pair = |high: u32, low: u32| (high as u64) << 32 | low as u64;
    Ok([
        info.volume_serial as u64,
        pair(info.index_high, info.index_low),
        info.attributes as u64,
        pair(info.size_high, info.size_low),
        pair(info.write[1], info.write[0]),
        pair(info.creation[1], info.creation[0]),
    ])
}

#[cfg(unix)]
fn identity(path: &Path) -> io::Result<Identity> {
    use std::os::unix::fs::MetadataExt;
    let info = fs::metadata(path)?;
    let nanos = |seconds: i64, nanos: i64| (seconds as i128 * 1_000_000_000 + nanos as i128) as u64;
    Ok([
        info.dev(),
        info.ino(),
        info.mode() as u64,
        info.size(),
        nanos(info.mtime(), info.mtime_nsec()),
        nanos(info.ctime(), info.ctime_nsec()),
    ])
}

fn stat(path: &Path) -> Result<Identity> {
    identity(path).map_err(|error| Error::win(&error, path))
}

fn list_dir(path: &Path) -> Result<Vec<String>> {
    paths::list_dir(path).map_err(|error| Error::win(&error, path))
}

fn is_file(path: &Path) -> Result<bool> {
    paths::is_file(path).map_err(|error| Error::win(&error, path))
}

fn is_dir(path: &Path) -> Result<bool> {
    paths::is_dir(path).map_err(|error| Error::win(&error, path))
}

/// Detect ordinary concurrent installation changes, without locking files.
///
/// Stat identities and directory inventories are checked again before
/// return. This is not an atomic snapshot or protection against deliberate
/// timestamp spoofing; the installation should stay idle during a scan.
#[derive(Default)]
pub struct ScanSnapshot {
    files: Vec<(PathBuf, Identity)>,
    file_keys: HashSet<String>,
    directories: Vec<(PathBuf, (Identity, Vec<String>))>,
    directory_keys: HashSet<String>,
}

fn path_key(path: &Path) -> String {
    paths::parts(path).iter().map(|part| crate::py::lower(part)).collect::<Vec<_>>().join("\u{0}")
}

impl ScanSnapshot {
    fn directory_state(path: &Path) -> Result<(Identity, Vec<String>)> {
        let before = stat(path)?;
        let mut names = list_dir(path)?;
        names.sort();
        if before != stat(path)? {
            return Err(Error::scan(CHANGED));
        }
        Ok((before, names))
    }

    pub fn file(&mut self, path: &Path) -> Result<()> {
        if self.file_keys.insert(path_key(path)) {
            let identity = stat(path)?;
            self.files.push((path.to_path_buf(), identity));
        }
        Ok(())
    }

    pub fn directory(&mut self, path: &Path) -> Result<()> {
        if !self.directory_keys.contains(&path_key(path)) {
            let state = Self::directory_state(path)?;
            self.directory_keys.insert(path_key(path));
            self.directories.push((path.to_path_buf(), state));
        }
        Ok(())
    }

    pub fn verify(&self) -> Result<()> {
        let check = || -> Result<()> {
            for (path, expected) in &self.files {
                if stat(path)? != *expected {
                    return Err(Error::scan(CHANGED));
                }
            }
            // Containing directories last: additions, removals, replacements
            // and new language/DLC choices after resource reads.
            for (path, expected) in self.directories.iter().rev() {
                if Self::directory_state(path)? != *expected {
                    return Err(Error::scan(CHANGED));
                }
            }
            Ok(())
        };
        check().map_err(|error| {
            if error.kind == crate::error::ErrorKind::Os {
                Error::scan(
                    "The installation changed or became unreadable during scanning; no catalog was produced.",
                )
            } else {
                error
            }
        })
    }
}

/// A file's stat identity, or a directory's with its entry names
/// ([`file_identity`]).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FileIdentity {
    File(Identity),
    Directory(Identity, Vec<String>),
}

/// A file's stat identity, or a directory's with its entry names (see
/// [`ScanSnapshot`]).
///
/// Equal identities mean the installation did not change there, as far as
/// ordinary installs show; it is no protection against deliberate spoofing.
pub fn file_identity(path: &Path) -> Result<FileIdentity> {
    if is_dir(path)? {
        let (identity, names) = ScanSnapshot::directory_state(path)?;
        return Ok(FileIdentity::Directory(identity, names));
    }
    Ok(FileIdentity::File(stat(path)?))
}

// ---------------------------------------------------------------- formats

/// Ordinary (non-tileset) resource types the configurator reads.
pub const RESOURCE_TYPES: &[(&str, u32)] = &[
    ("itm", 1005),
    ("spl", 1006),
    ("bcs", 1007),
    ("cre", 1009),
    ("are", 1010),
    ("dlg", 1011),
    ("sto", 1014),
    ("lua", 1033),
];
pub const ITM_TYPE: u32 = 1005;

pub fn resource_type(extension: &str) -> u32 {
    RESOURCE_TYPES.iter().find(|(name, _)| *name == extension).map(|(_, kind)| *kind).expect("known type")
}

pub const ITEM_SEMANTIC_FIELDS: [&str; 11] = [
    "resref",
    "name",
    "unidentified_name",
    "identified_name",
    "category",
    "flags",
    "price",
    "stack_size",
    "enchantment",
    "proficiency",
    "sha256",
];

/// Hash the catalog's semantic fields, independent of physical packaging.
pub fn catalog_fingerprint(catalog: &Value) -> Result<String> {
    let invalid =
        || Error::scan("The catalog cannot be fingerprinted; required semantic fields are invalid or missing.");
    let catalog = catalog.as_dict().ok_or_else(invalid)?;
    let schema = catalog.get("schema").ok_or_else(invalid)?;
    let language = catalog.get("language").ok_or_else(invalid)?;
    let items = catalog.get("items").ok_or_else(invalid)?;
    let entries: Vec<&Value> = match items {
        Value::List(list) => list.iter().collect(),
        Value::Dict(dict) if dict.is_empty() => Vec::new(),
        Value::Str(text) if text.is_empty() => Vec::new(),
        _ => return Err(invalid()),
    };
    let mut semantic = Vec::with_capacity(entries.len());
    for item in entries {
        let item = item.as_dict().ok_or_else(invalid)?;
        let mut fields = Dict::with_capacity(ITEM_SEMANTIC_FIELDS.len());
        for key in ITEM_SEMANTIC_FIELDS {
            fields.push_unique(key.to_string(), item.get(key).ok_or_else(invalid)?.clone());
        }
        semantic.push(Value::Dict(fields));
    }
    let mut content = Dict::new();
    content.insert("schema", schema.clone());
    content.insert("language", language.clone());
    content.insert("items", Value::List(semantic));
    let encoded = crate::json::dumps(&Value::Dict(content), crate::json::Style::DIGEST).map_err(|_| invalid())?;
    Ok(sha256_hex(encoded.as_bytes()))
}

fn span(size: usize, offset: u64, length: u64, label: &str) -> Result<()> {
    let size = size as u64;
    if offset > size || length > size - offset {
        return Err(Error::scan(format!("Invalid or truncated {label}.")));
    }
    Ok(())
}

fn contained(path: &Path, root: &Path) -> Result<PathBuf> {
    let resolved = paths::resolve(path, false).map_err(|(error, at)| Error::win(&error, &at))?;
    if !paths::is_relative_to(&resolved, root) {
        return Err(Error::scan("A resource path resolves outside the selected game directory."));
    }
    Ok(resolved)
}

fn child(parent: &Path, name: &str, root: &Path, required: bool) -> Result<Option<PathBuf>> {
    let wanted = crate::py::casefold(name);
    let matches: Vec<String> =
        list_dir(parent)?.into_iter().filter(|entry| crate::py::casefold(entry) == wanted).collect();
    if matches.len() > 1 {
        return Err(Error::scan(format!("Ambiguous case-insensitive path: {name}.")));
    }
    match matches.into_iter().next() {
        Some(entry) => Ok(Some(contained(&parent.join(entry), root)?)),
        None if required => Err(Error::scan(format!("Required game resource is missing: {name}."))),
        None => Ok(None),
    }
}

fn safe_archive_parts(name: &str) -> Result<Vec<String>> {
    let parts: Vec<String> = name.replace('\\', "/").split('/').map(str::to_string).collect();
    if parts.iter().any(|part| part.is_empty() || part == "." || part == ".." || part.contains(':')) {
        return Err(Error::scan("Invalid or unsafe archive path in chitin.key."));
    }
    Ok(parts)
}

fn relative_file(root: &Path, name: &str, snapshot: &mut ScanSnapshot) -> Result<PathBuf> {
    // KEY paths use either slash convention. Never interpret an archive-supplied
    // drive, parent traversal, or absolute path, even if it happens to exist.
    let parts = safe_archive_parts(name)?;
    let mut current = root.to_path_buf();
    for part in parts {
        snapshot.directory(&current)?;
        current = child(&current, &part, root, true)?.expect("required");
    }
    if !is_file(&current)? {
        return Err(Error::scan("A KEY archive path does not refer to a file."));
    }
    Ok(current)
}

/// Reject DLC resource layers that a root-KEY-only scan cannot include.
///
/// Ordinary downloaded mod archives are not effective game resources. Root
/// ZIPs are inspected only for KEY-bearing packages, without extracting or
/// opening their members. Any nonempty ZIP in dlc/ is outside current scope.
fn reject_unmerged_dlc(root: &Path, snapshot: &mut ScanSnapshot) -> Result<()> {
    let is_zip = |name: &str| crate::py::casefold(paths::suffix(name)) == ".zip";
    let mut candidates: Vec<(PathBuf, String, bool)> =
        list_dir(root)?.into_iter().filter(|name| is_zip(name)).map(|name| (root.join(&name), name, false)).collect();
    if let Some(dlc) = child(root, "dlc", root, false)? {
        if !is_dir(&dlc)? {
            return Err(Error::scan("The DLC path is not a directory."));
        }
        snapshot.directory(&dlc)?;
        candidates
            .extend(list_dir(&dlc)?.into_iter().filter(|name| is_zip(name)).map(|name| (dlc.join(&name), name, true)));
    }
    for (entry, name, in_dlc) in candidates {
        let archive = contained(&entry, root)?;
        if !is_file(&archive)? {
            return Err(Error::scan("A ZIP archive path does not refer to a file."));
        }
        snapshot.file(&archive)?;
        let size = fs::metadata(&archive).map_err(|error| Error::win(&error, &archive))?.len();
        if size == 0 {
            continue;
        }
        if in_dlc || crate::py::casefold(&name) == "sod-dlc.zip" {
            return Err(Error::scan(
                "Unmerged DLC ZIP archives are not supported; this scanner requires a merged root KEY resource set.",
            ));
        }
        let unreadable = || Error::scan("A root ZIP archive changed or became unreadable during scanning.");
        match zip::is_zipfile(&archive) {
            Ok(false) => continue,
            Ok(true) => {}
            Err(_) => return Err(unreadable()),
        }
        match zip::members(&archive) {
            Ok(members) => {
                if members.iter().any(|member| !member.is_dir && crate::py::casefold(&member.filename).ends_with(".key")) {
                    return Err(Error::scan(
                        "A KEY-bearing ZIP archive requires DLC resource support; merge DLC before scanning.",
                    ));
                }
            }
            Err(zip::ZipError::Bad(_)) => return Err(unreadable()),
            Err(zip::ZipError::NotImplemented(message)) => {
                return Err(Error::scan(format!("Unable to read the supplied installation: {message}")))
            }
            Err(zip::ZipError::Io(error)) => return Err(Error::os(&error, &archive)),
        }
    }
    Ok(())
}

/// A resource reference from game data, lowercased.
pub fn resref(raw: &[u8]) -> Result<String> {
    let (body, padding) = match raw.iter().position(|&byte| byte == 0) {
        Some(index) => (&raw[..index], Some(&raw[index + 1..])),
        None => (raw, None),
    };
    if let Some(padding) = padding {
        if padding.iter().any(|&byte| byte != 0) {
            return Err(Error::scan("A resource reference has nonzero data after its terminator."));
        }
    }
    if !body.is_ascii() {
        return Err(Error::scan("A resource reference is not ASCII."));
    }
    let name = std::str::from_utf8(body).expect("ascii");
    // Real EET resources include internal spaces (for example MONKTU 8.DLG).
    // Preserve those identities; never admit path components or Windows
    // metacharacters.
    if name.is_empty()
        || name != name.trim_matches(' ')
        || name == "."
        || name == ".."
        || name.bytes().any(|byte| !(32..=126).contains(&byte) || b"/\\:*?\"<>|".contains(&byte))
    {
        return Err(Error::scan("Invalid resource reference."));
    }
    Ok(name.to_ascii_lowercase())
}

fn read_file(path: &Path) -> Result<Vec<u8>> {
    if !is_file(path)? {
        return Err(Error::scan("A required resource path is not a file."));
    }
    fs::read(path).map_err(|error| Error::os(&error, path))
}

/// KEY resources of the selected types: archive names, and per extension the
/// (archive index, resource id) of each resref in KEY order.
pub struct KeyIndex {
    pub archives: Vec<String>,
    pub resources: Vec<(String, Vec<(String, (u32, u32))>)>,
}

fn key_resources(data: &[u8], types: &[(String, u32)]) -> Result<KeyIndex> {
    span(data.len(), 0, 0x18, "KEY header")?;
    if &data[..8] != b"KEY V1  " {
        return Err(Error::scan("Unsupported KEY format; expected KEY V1."));
    }
    let archives = u32_le(data, 8) as u64;
    let count = u32_le(data, 12) as u64;
    let archive_offset = u32_le(data, 16) as u64;
    let resource_offset = u32_le(data, 20) as u64;
    if (archives != 0 && archive_offset < 0x18) || (count != 0 && resource_offset < 0x18) {
        return Err(Error::scan("KEY tables overlap their header."));
    }
    span(data.len(), archive_offset, archives * 12, "KEY archive table")?;
    span(data.len(), resource_offset, count * 14, "KEY resource table")?;
    if archives != 0
        && count != 0
        && archive_offset.max(resource_offset) < (archive_offset + archives * 12).min(resource_offset + count * 14)
    {
        return Err(Error::scan("KEY archive and resource tables overlap."));
    }
    let mut names = Vec::with_capacity(archives as usize);
    for index in 0..archives {
        let base = (archive_offset + index * 12) as usize;
        let offset = u32_le(data, base + 4) as u64;
        let length = u16_le(data, base + 8) as u64;
        span(data.len(), offset, length, "KEY archive filename")?;
        let raw = &data[offset as usize..(offset + length) as usize];
        if raw.is_empty() || raw[raw.len() - 1] != 0 || raw[..raw.len() - 1].contains(&0) {
            return Err(Error::scan("Invalid KEY archive filename terminator."));
        }
        let body = &raw[..raw.len() - 1];
        if !body.is_ascii() {
            return Err(Error::scan("A KEY archive filename is not ASCII."));
        }
        let name = String::from_utf8(body.to_vec()).expect("ascii");
        safe_archive_parts(&name)?;
        names.push(name);
    }
    let mut resources: Vec<(String, Vec<(String, (u32, u32))>)> =
        types.iter().map(|(extension, _)| (extension.clone(), Vec::new())).collect();
    let mut seen: Vec<HashSet<String>> = types.iter().map(|_| HashSet::new()).collect();
    for index in 0..count {
        let base = (resource_offset + index * 14) as usize;
        let kind = u16_le(data, base + 8);
        let Some(slot) = types.iter().position(|(_, wanted)| *wanted == kind) else { continue };
        let name = resref(&data[base..base + 8])?;
        let locator = u32_le(data, base + 10);
        let (archive_index, resource_id) = (locator >> 20, locator & 0x3FFF);
        if archive_index as u64 >= archives {
            return Err(Error::scan("A resource references an invalid KEY archive index."));
        }
        if !seen[slot].insert(name.clone()) {
            return Err(Error::scan(format!("Duplicate case-insensitive KEY resource: {name}.{}.", types[slot].0)));
        }
        resources[slot].1.push((name, (archive_index, resource_id)));
    }
    Ok(KeyIndex { archives: names, resources })
}

fn read_at(handle: &mut File, size: u64, offset: u64, length: u64, label: &str, path: &Path) -> Result<Vec<u8>> {
    let size_usize = usize::try_from(size).unwrap_or(usize::MAX);
    span(size_usize, offset, length, label)?;
    handle.seek(SeekFrom::Start(offset)).map_err(|error| Error::os(&error, path))?;
    let mut data = Vec::with_capacity(length as usize);
    handle.take(length).read_to_end(&mut data).map_err(|error| Error::os(&error, path))?;
    if data.len() as u64 != length {
        return Err(Error::scan(format!("The {label} changed or was truncated during scanning.")));
    }
    Ok(data)
}

type BiffRecords = HashMap<u32, (u64, u64, u32)>;

/// One archive's ordinary-resource index, without reading payloads.
fn biff_index(handle: &mut File, size: u64, path: &Path) -> Result<BiffRecords> {
    let header = read_at(handle, size, 0, size.min(0x14), "BIFF header", path)?;
    if header.len() >= 4 && (&header[..4] == b"BIFC" || &header[..4] == b"BIF ") {
        return Err(Error::scan("Compressed BIF archives are not supported by this scanner yet."));
    }
    if header.len() < 0x14 || &header[..8] != b"BIFFV1  " {
        return Err(Error::scan("Invalid or unsupported BIFF header; expected BIFF V1."));
    }
    let count = u32_le(&header, 8) as u64;
    let tiles = u32_le(&header, 12) as u64;
    let offset = u32_le(&header, 16) as u64;
    if (count != 0 || tiles != 0) && offset < 0x14 {
        return Err(Error::scan("BIFF tables overlap their header."));
    }
    let table = read_at(handle, size, offset, count * 16, "BIFF file table", path)?;
    let size_usize = usize::try_from(size).unwrap_or(usize::MAX);
    span(size_usize, offset + count * 16, tiles * 20, "BIFF tileset table")?;
    let mut records = HashMap::with_capacity(count as usize);
    for index in 0..count as usize {
        let base = index * 16;
        let locator = u32_le(&table, base);
        let data_offset = u32_le(&table, base + 4) as u64;
        let length = u32_le(&table, base + 8) as u64;
        let kind = u16_le(&table, base + 12);
        let resource_id = locator & 0x3FFF;
        if records.contains_key(&resource_id) {
            return Err(Error::scan("Duplicate resource locator in a BIFF file table."));
        }
        span(size_usize, data_offset, length, "BIFF resource data")?;
        if length != 0
            && (data_offset < 0x14
                || data_offset.max(offset) < (data_offset + length).min(offset + count * 16 + tiles * 20))
        {
            return Err(Error::scan("BIFF resource data overlaps its header or tables."));
        }
        records.insert(resource_id, (data_offset, length, kind));
    }
    Ok(records)
}

fn biff_record(records: &BiffRecords, resource_id: u32, kind: u32) -> Result<(u64, u64)> {
    let Some(&(offset, length, actual)) = records.get(&resource_id) else {
        return Err(Error::scan("A resource locator is missing from its BIFF archive."));
    };
    if actual != kind {
        return Err(Error::scan("The BIFF resource type disagrees with its KEY entry."));
    }
    Ok((offset, length))
}

fn open_file(path: &Path) -> Result<(File, u64)> {
    let handle = File::open(path).map_err(|error| Error::os(&error, path))?;
    let size = fs::metadata(path).map_err(|error| Error::win(&error, path))?.len();
    Ok((handle, size))
}

/// Read several ITM records from one archive (`requested`: id → name).
fn biff_items(path: &Path, requested: &[(u32, String)]) -> Result<HashMap<String, Vec<u8>>> {
    let (mut handle, size) = open_file(path)?;
    let records = biff_index(&mut handle, size, path)?;
    let mut found = HashMap::with_capacity(requested.len());
    for (resource_id, name) in requested {
        let (offset, length) = biff_record(&records, *resource_id, ITM_TYPE)?;
        found.insert(name.clone(), read_at(&mut handle, size, offset, length, "ITM data", path)?);
    }
    Ok(found)
}

type Overrides = Vec<(String, HashMap<String, PathBuf>)>;

fn override_resources(
    root: &Path,
    types: &[(String, u32)],
    snapshot: &mut ScanSnapshot,
    mut long_names: Option<&mut Vec<String>>,
) -> Result<Overrides> {
    let mut resources: Overrides = types.iter().map(|(extension, _)| (extension.clone(), HashMap::new())).collect();
    let Some(override_dir) = child(root, "override", root, false)? else { return Ok(resources) };
    if !is_dir(&override_dir)? {
        return Err(Error::scan("The override path is not a directory."));
    }
    snapshot.directory(&override_dir)?;
    for entry in list_dir(&override_dir)? {
        let suffix = paths::suffix(&entry);
        let extension = crate::py::casefold(suffix.get(1..).unwrap_or(""));
        let Some(slot) = resources.iter().position(|(name, _)| *name == extension) else { continue };
        let stem = paths::stem(&entry);
        if stem.chars().count() > 8 {
            // Game data names resources with at most eight characters, so it
            // cannot name these files. Mods keep templates for WeiDU under such
            // names (SCS's dw#marker_file.itm); they are left out, not refused.
            if let Some(names) = long_names.as_deref_mut() {
                names.push(entry.clone());
            }
            continue;
        }
        if !stem.is_ascii() {
            return Err(Error::scan("An override resource reference is not ASCII."));
        }
        let name = resref(stem.as_bytes())?;
        if resources[slot].1.contains_key(&name) {
            return Err(Error::scan(format!("Duplicate case-insensitive override resource: {name}.{extension}.")));
        }
        let path = contained(&override_dir.join(&entry), root)?;
        if !is_file(&path)? {
            return Err(Error::scan("An override resource path is not a file."));
        }
        snapshot.file(&path)?;
        resources[slot].1.insert(name, path);
    }
    Ok(resources)
}

fn resolve_root(game_root: &Path) -> Result<PathBuf> {
    let root = paths::resolve(game_root, true).map_err(|(error, at)| Error::win(&error, &at))?;
    if !is_dir(&root)? {
        return Err(Error::scan("The supplied game directory is not a directory."));
    }
    Ok(root)
}

/// Effective ordinary resources read during one checked scan context.
///
/// One installation snapshot spans setup, all reads and the caller's parsing
/// until `finish`. Changed files make `finish` fail. Archive indexes are
/// cached, payloads are not; no files are written and no archive stays open
/// between calls. Override files with names longer than eight characters are
/// not resources and are left out.
pub struct GameResources {
    pub root: PathBuf,
    types: Vec<(String, u32)>,
    snapshot: ScanSnapshot,
    keyed: Vec<(String, HashMap<String, (u32, u32)>)>,
    overrides: Overrides,
    archives: HashMap<u32, PathBuf>,
    indexes: RefCell<HashMap<PathBuf, (u64, BiffRecords)>>,
}

impl GameResources {
    fn validate_types(resource_types: &[(&str, u32)]) -> Result<Vec<(String, u32)>> {
        if resource_types.is_empty() {
            return Err(Error::scan("Select at least one ordinary resource type."));
        }
        let mut types: Vec<(String, u32)> = Vec::new();
        for (extension, kind) in resource_types {
            if extension.is_empty() || !extension.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
                return Err(Error::scan("Resource extensions must be ASCII names without a dot."));
            }
            let extension = extension.to_ascii_lowercase();
            if types.iter().any(|(name, _)| *name == extension) || !(1..=0xFFFF).contains(kind) {
                return Err(Error::scan(
                    "Resource type mappings must have unique extensions and valid numeric types.",
                ));
            }
            if *kind == 1003 || types.iter().any(|(_, existing)| existing == kind) {
                return Err(Error::scan("Resource types must be distinct ordinary types; tilesets are unsupported."));
            }
            types.push((extension, *kind));
        }
        Ok(types)
    }

    fn open(game_root: &Path, types: Vec<(String, u32)>) -> Result<GameResources> {
        let root = resolve_root(game_root)?;
        let mut snapshot = ScanSnapshot::default();
        snapshot.directory(&root)?;
        reject_unmerged_dlc(&root, &mut snapshot)?;
        let key_path = child(&root, "chitin.key", &root, true)?.expect("required");
        snapshot.file(&key_path)?;
        let key = key_resources(&read_file(&key_path)?, &types)?;
        let overrides = override_resources(&root, &types, &mut snapshot, None)?;
        let mut archives = HashMap::new();
        // Capture every effective archive before handing control to the
        // caller, including archives it has not read yet.
        for ((_, entries), (_, overridden)) in key.resources.iter().zip(&overrides) {
            for (name, (archive_index, _)) in entries {
                if overridden.contains_key(name) || archives.contains_key(archive_index) {
                    continue;
                }
                let path = relative_file(&root, &key.archives[*archive_index as usize], &mut snapshot)?;
                snapshot.file(&path)?;
                archives.insert(*archive_index, path);
            }
        }
        let keyed = key
            .resources
            .into_iter()
            .map(|(extension, entries)| (extension, entries.into_iter().collect::<HashMap<_, _>>()))
            .collect();
        Ok(GameResources { root, types, snapshot, keyed, overrides, archives, indexes: RefCell::new(HashMap::new()) })
    }

    /// `with GameResources(root, types) as resources: body(resources)`.
    pub fn with<T>(
        game_root: &Path,
        resource_types: &[(&str, u32)],
        body: impl FnOnce(&GameResources) -> Result<T>,
    ) -> Result<T> {
        let types = Self::validate_types(resource_types)?;
        let resources = Self::open(game_root, types).map_err(unreadable)?;
        let result = body(&resources)?;
        resources.indexes.borrow_mut().clear();
        resources.snapshot.verify()?;
        Ok(result)
    }

    fn slot(&self, extension: &str) -> Result<usize> {
        let lowered = extension.to_ascii_lowercase();
        self.types
            .iter()
            .position(|(name, _)| *name == lowered)
            .ok_or_else(|| Error::scan("The requested resource type was not selected for this scan."))
    }

    fn name(&self, slot: usize, resref_text: &str) -> Result<String> {
        if !resref_text.is_ascii() {
            return Err(Error::scan("A resource reference is not ASCII."));
        }
        let raw = resref_text.as_bytes();
        if raw.is_empty() || raw.len() > 8 || raw.contains(&0) {
            return Err(Error::scan("Resource references must be between one and eight ASCII characters."));
        }
        let name = resref(raw)?;
        if !self.keyed[slot].1.contains_key(&name) && !self.overrides[slot].1.contains_key(&name) {
            return Err(Error::scan(format!("The requested resource is missing: {name}.{}.", self.types[slot].0)));
        }
        Ok(name)
    }

    /// Sorted lowercase resrefs of one type.
    pub fn names(&self, extension: &str) -> Result<Vec<String>> {
        let slot = self.slot(extension)?;
        let mut names: BTreeSet<&String> = self.keyed[slot].1.keys().collect();
        names.extend(self.overrides[slot].1.keys());
        Ok(names.into_iter().cloned().collect())
    }

    /// Whether a resref exists (without the reference's exception).
    pub fn contains(&self, extension: &str, resref_text: &str) -> Result<bool> {
        let slot = self.slot(extension)?;
        let name = resref_text.to_ascii_lowercase();
        Ok(self.keyed[slot].1.contains_key(&name) || self.overrides[slot].1.contains_key(&name))
    }

    /// `override` or `biff`, without reading the payload.
    pub fn origin(&self, extension: &str, resref_text: &str) -> Result<&'static str> {
        let slot = self.slot(extension)?;
        let name = self.name(slot, resref_text)?;
        Ok(if self.overrides[slot].1.contains_key(&name) { "override" } else { "biff" })
    }

    /// The file that holds a resource: its override file or its archive.
    pub fn location(&self, extension: &str, resref_text: &str) -> Result<PathBuf> {
        let slot = self.slot(extension)?;
        let name = self.name(slot, resref_text)?;
        if let Some(path) = self.overrides[slot].1.get(&name) {
            return Ok(path.clone());
        }
        let (archive_index, _) = self.keyed[slot].1[&name];
        Ok(self.archives[&archive_index].clone())
    }

    /// The files and directory that decide where each resource comes from.
    pub fn lookup_paths(&self) -> Result<Vec<PathBuf>> {
        let override_dir = child(&self.root, "override", &self.root, false)?;
        let key = child(&self.root, "chitin.key", &self.root, true)?.expect("required");
        Ok(std::iter::once(key).chain(override_dir).collect())
    }

    pub fn read(&self, extension: &str, resref_text: &str) -> Result<Vec<u8>> {
        let slot = self.slot(extension)?;
        let name = self.name(slot, resref_text)?;
        let result = (|| {
            if let Some(path) = self.overrides[slot].1.get(&name) {
                return read_file(path);
            }
            let (archive_index, resource_id) = self.keyed[slot].1[&name];
            let path = &self.archives[&archive_index];
            let (mut handle, _) = open_file(path)?;
            let mut indexes = self.indexes.borrow_mut();
            if !indexes.contains_key(path) {
                let size = fs::metadata(path).map_err(|error| Error::win(&error, path))?.len();
                let records = biff_index(&mut handle, size, path)?;
                indexes.insert(path.clone(), (size, records));
            }
            let (size, records) = &indexes[path];
            let (offset, length) = biff_record(records, resource_id, self.types[slot].1)?;
            let label = format!("{} data", self.types[slot].0.to_ascii_uppercase());
            read_at(&mut handle, *size, offset, length, &label, path)
        })();
        result.map_err(unreadable)
    }
}

// ---------------------------------------------------------------- TLK

fn select_tlk(root: &Path, language: Option<&str>, snapshot: &mut ScanSnapshot) -> Result<(PathBuf, String)> {
    let lang_dir = child(root, "lang", root, false)?;
    if let Some(language) = language {
        if language.is_empty() || !language.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-') {
            return Err(Error::scan("Language must be a single language-directory name."));
        }
        let missing = || Error::scan("The requested language directory is missing.");
        let Some(lang_dir) = lang_dir else { return Err(missing()) };
        if !is_dir(&lang_dir)? {
            return Err(missing());
        }
        snapshot.directory(&lang_dir)?;
        let selected = child(&lang_dir, language, root, true)?.expect("required");
        if !is_dir(&selected)? {
            return Err(missing());
        }
        snapshot.directory(&selected)?;
        let tlk = child(&selected, "dialog.tlk", root, true)?.expect("required");
        return Ok((tlk, crate::py::casefold(language)));
    }
    if let Some(root_tlk) = child(root, "dialog.tlk", root, false)? {
        return Ok((root_tlk, "default".into()));
    }
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();
    if let Some(lang_dir) = lang_dir {
        if !is_dir(&lang_dir)? {
            return Err(Error::scan("The lang path is not a directory."));
        }
        snapshot.directory(&lang_dir)?;
        let mut entries = list_dir(&lang_dir)?;
        entries.sort_by(|a, b| crate::py::lower(a).cmp(&crate::py::lower(b)));
        for entry in entries {
            let path = lang_dir.join(&entry);
            if !is_dir(&path)? {
                continue;
            }
            let name = crate::py::casefold(&entry);
            if !seen.insert(name.clone()) {
                return Err(Error::scan("Ambiguous case-insensitive language directories."));
            }
            let selected = contained(&path, root)?;
            snapshot.directory(&selected)?;
            if let Some(candidate) = child(&selected, "dialog.tlk", root, false)? {
                candidates.push((candidate, name));
            }
        }
    }
    if candidates.len() != 1 {
        return Err(Error::scan("Select an explicit language; no unique dialog.tlk was found."));
    }
    Ok(candidates.remove(0))
}

/// A validated TLK V1 string table.
pub struct Tlk {
    pub data: Vec<u8>,
    pub count: u32,
    pub offset: u32,
}

pub const MISSING_TLK_WARNING: &str =
    "Some item names reference missing TLK entries; available names or resource references are shown.";

impl Tlk {
    pub fn new(data: Vec<u8>) -> Result<Tlk> {
        let (count, offset) = Tlk::layout(&data)?;
        Ok(Tlk { data, count, offset })
    }

    /// The entry count and string data offset of a TLK, checked as
    /// [`Tlk::new`] checks them (every entry's text inside the file).
    pub fn layout(data: &[u8]) -> Result<(u32, u32)> {
        span(data.len(), 0, 0x12, "TLK header")?;
        if &data[..8] != b"TLK V1  " {
            return Err(Error::scan("Unsupported TLK format; expected TLK V1."));
        }
        let count = u32_le(data, 0xA);
        let offset = u32_le(data, 0xE);
        span(data.len(), 0x12, count as u64 * 0x1A, "TLK entry table")?;
        if (offset as u64) < 0x12 + count as u64 * 0x1A {
            return Err(Error::scan("TLK string data overlaps its entry table."));
        }
        span(data.len(), offset as u64, 0, "TLK string data")?;
        for index in 0..count as usize {
            let base = 0x12 + index * 0x1A;
            let string_offset = u32_le(data, base + 0x12) as u64;
            let length = u32_le(data, base + 0x16) as u64;
            span(data.len(), offset as u64 + string_offset, length, "TLK string")?;
        }
        Ok((count, offset))
    }

    /// The text of `strref`, None for no text; a missing entry adds a warning.
    pub fn text(&self, strref: u32, warnings: &mut BTreeSet<String>) -> Result<Option<String>> {
        if strref == 0xFFFF_FFFF {
            return Ok(None);
        }
        if strref >= self.count {
            warnings.insert(MISSING_TLK_WARNING.into());
            return Ok(None);
        }
        let base = 0x12 + strref as usize * 0x1A;
        if u16_le(&self.data, base) & 1 == 0 {
            return Ok(None);
        }
        let string_offset = u32_le(&self.data, base + 0x12) as usize;
        let length = u32_le(&self.data, base + 0x16) as usize;
        let start = self.offset as usize + string_offset;
        let raw = &self.data[start..start + length];
        let raw = match raw.iter().position(|&byte| byte == 0) {
            Some(index) => &raw[..index],
            None => raw,
        };
        match std::str::from_utf8(raw) {
            Ok("") => Ok(None),
            Ok(text) => Ok(Some(text.to_string())),
            Err(_) => Err(Error::scan("An item name is not valid UTF-8; this scanner supports Enhanced Edition text.")),
        }
    }
}

// ---------------------------------------------------------------- items

fn item(name: &str, data: &[u8], origin: &str, tlk: &Tlk, warnings: &mut BTreeSet<String>) -> Result<Value> {
    span(data.len(), 0, 0x72, "ITM header")?;
    if &data[..8] != b"ITM V1  " {
        return Err(Error::scan(format!("Unsupported ITM format for {name}; expected ITM V1.")));
    }
    let ability_offset = u32_le(data, 0x64) as u64;
    let ability_count = u16_le(data, 0x68) as u64;
    let effect_offset = u32_le(data, 0x6A) as u64;
    if ability_count != 0 {
        if ability_offset < 0x72 {
            return Err(Error::scan("ITM ability table overlaps its header."));
        }
        span(data.len(), ability_offset, ability_count * 0x38, "ITM ability table")?;
    }
    let mut effect_ranges = vec![(u16_le(data, 0x6E) as u64, u16_le(data, 0x70) as u64)];
    for index in 0..ability_count {
        let base = (ability_offset + index * 0x38) as usize;
        effect_ranges.push((u16_le(data, base + 0x20) as u64, u16_le(data, base + 0x1E) as u64));
    }
    for (index, count) in effect_ranges {
        if count != 0 {
            if effect_offset < 0x72 {
                return Err(Error::scan("ITM effects overlap its header."));
            }
            span(data.len(), effect_offset + index * 0x30, count * 0x30, "ITM effects")?;
            let start = effect_offset + index * 0x30;
            let end = effect_offset + (index + count) * 0x30;
            if ability_count != 0 && start.max(ability_offset) < end.min(ability_offset + ability_count * 0x38) {
                return Err(Error::scan("ITM effects overlap its ability table."));
            }
        }
    }
    let unidentified = tlk.text(u32_le(data, 8), warnings)?;
    let identified = tlk.text(u32_le(data, 12), warnings)?;
    let shown = identified.clone().or_else(|| unidentified.clone()).unwrap_or_else(|| name.to_string());
    let mut entry = Dict::with_capacity(12);
    entry.push_unique("resref".into(), Value::from(name));
    entry.push_unique("name".into(), Value::Str(shown));
    entry.push_unique("unidentified_name".into(), Value::from(unidentified));
    entry.push_unique("identified_name".into(), Value::from(identified));
    entry.push_unique("category".into(), Value::from(u16_le(data, 0x1C)));
    entry.push_unique("flags".into(), Value::from(u32_le(data, 0x18)));
    entry.push_unique("price".into(), Value::from(u32_le(data, 0x34)));
    entry.push_unique("stack_size".into(), Value::from(u16_le(data, 0x38)));
    entry.push_unique("enchantment".into(), Value::from(u32_le(data, 0x60)));
    entry.push_unique("proficiency".into(), Value::from(data[0x31] as u32));
    entry.push_unique("sha256".into(), Value::Str(sha256_hex(data)));
    entry.push_unique("resource_origin".into(), Value::from(origin));
    Ok(Value::Dict(entry))
}

/// Index effective installed items without writing anything to the game.
///
/// A root dialog.tlk takes precedence when no language is given; otherwise a
/// sole lang/*/dialog.tlk is selected. The fingerprint excludes physical
/// paths and packaging. Concurrent changes fail without retry.
pub fn scan_game(game_root: &Path, language: Option<&str>) -> Result<Value> {
    scan_game_inner(game_root, language).map_err(unreadable)
}

fn scan_game_inner(game_root: &Path, language: Option<&str>) -> Result<Value> {
    let root = resolve_root(game_root)?;
    let mut snapshot = ScanSnapshot::default();
    snapshot.directory(&root)?;
    reject_unmerged_dlc(&root, &mut snapshot)?;
    let key_path = child(&root, "chitin.key", &root, true)?.expect("required");
    snapshot.file(&key_path)?;
    let types = vec![("itm".to_string(), ITM_TYPE)];
    let key = key_resources(&read_file(&key_path)?, &types)?;
    let (tlk_path, selected_language) = select_tlk(&root, language, &mut snapshot)?;
    snapshot.file(&tlk_path)?;
    let tlk = Tlk::new(read_file(&tlk_path)?)?;
    let mut long_names = Vec::new();
    let overrides = override_resources(&root, &types, &mut snapshot, Some(&mut long_names))?.remove(0).1;
    let keyed = &key.resources[0].1;
    let mut effective: HashMap<String, (Vec<u8>, &'static str)> = HashMap::new();
    // archive index → [(resource id, names)] in first-seen order.
    let mut pending: Vec<(u32, Vec<(u32, Vec<String>)>)> = Vec::new();
    for (name, (archive_index, resource_id)) in keyed {
        if overrides.contains_key(name) {
            continue;
        }
        let archive = match pending.iter().position(|(index, _)| index == archive_index) {
            Some(position) => &mut pending[position].1,
            None => {
                pending.push((*archive_index, Vec::new()));
                &mut pending.last_mut().unwrap().1
            }
        };
        match archive.iter().position(|(id, _)| id == resource_id) {
            Some(position) => archive[position].1.push(name.clone()),
            None => archive.push((*resource_id, vec![name.clone()])),
        }
    }
    for (archive_index, requested) in &pending {
        let archive_path = relative_file(&root, &key.archives[*archive_index as usize], &mut snapshot)?;
        snapshot.file(&archive_path)?;
        // Distinct KEY names may alias one BIFF record: read it once and keep
        // every name.
        let wanted: Vec<(u32, String)> = requested.iter().map(|(id, names)| (*id, names[0].clone())).collect();
        let mut archive_data = biff_items(&archive_path, &wanted)?;
        for (_, names) in requested {
            let data = archive_data.remove(&names[0]).expect("read");
            for name in names {
                effective.insert(name.clone(), (data.clone(), "biff"));
            }
        }
    }
    for (name, path) in &overrides {
        effective.insert(name.clone(), (read_file(path)?, "override"));
    }
    let mut warnings = BTreeSet::new();
    if !long_names.is_empty() {
        long_names.sort_by_key(|name| crate::py::casefold(name));
        warnings.insert(format!(
            "Override files with names longer than eight characters were left out \
             (resource names have at most eight): {}.",
            long_names.join(", ")
        ));
    }
    let mut names: Vec<&String> = effective.keys().collect();
    names.sort();
    let mut items = Vec::with_capacity(names.len());
    for name in names {
        let (data, origin) = &effective[name];
        items.push(item(name, data, origin, &tlk, &mut warnings)?);
    }
    let mut result = Dict::new();
    result.push_unique("schema".into(), Value::from("flir-item-catalog-v1"));
    result.push_unique("game_root".into(), Value::Str(root.to_string_lossy().into_owned()));
    result.push_unique("language".into(), Value::Str(selected_language));
    result.push_unique("items".into(), Value::List(items));
    let mut result = Value::Dict(result);
    let fingerprint = catalog_fingerprint(&result)?;
    let fields = result.as_dict_mut().expect("dict");
    fields.push_unique("fingerprint".into(), Value::Str(fingerprint));
    if !warnings.is_empty() {
        fields.push_unique("warnings".into(), Value::from(warnings.into_iter().collect::<Vec<String>>()));
    }
    snapshot.verify()?;
    Ok(result)
}
