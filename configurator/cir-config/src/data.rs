//! The mod data the configurator reads, embedded at build time: the original
//! tables under `lists/`, the BG1 name maps, game texts, the mod's stand-in
//! items and the editor UI. Paths are relative to the mod root.

mod embedded {
    include!(concat!(env!("OUT_DIR"), "/embedded.rs"));
}

/// The bytes of a mod file, by its path relative to the mod root.
pub fn file(relative: &str) -> Option<&'static [u8]> {
    let relative = relative.replace('\\', "/");
    #[cfg(feature = "parity-overrides")]
    if let Some(bytes) = parity_lists(&relative) {
        return bytes;
    }
    embedded::FILES
        .binary_search_by(|(name, _)| name.cmp(&relative.as_str()))
        .ok()
        .map(|index| embedded::FILES[index].1)
}

/// With the `parity-overrides` feature, a file below `lists/` of the folder
/// `CIR_PARITY_LISTS` names instead of the embedded one, as the reference's
/// tests point `templates.ROOT` and `original_catalog.ROOT` at a copy of the
/// lists with other tables; None for another path or without that folder.
#[cfg(feature = "parity-overrides")]
fn parity_lists(relative: &str) -> Option<Option<&'static [u8]>> {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    /// The files read from the folder, each once, kept for the process.
    type Read = Mutex<HashMap<String, Option<&'static [u8]>>>;
    static READ: OnceLock<Read> = OnceLock::new();
    if !relative.starts_with("lists/") {
        return None;
    }
    let root = std::env::var_os("CIR_PARITY_LISTS")?;
    let mut read = READ.get_or_init(Default::default).lock().expect("lists read before");
    Some(*read.entry(relative.to_string()).or_insert_with(|| {
        let path = std::path::Path::new(&root).join(relative);
        std::fs::read(path).ok().map(|bytes| &*Box::leak(bytes.into_boxed_slice()))
    }))
}

/// Every embedded path below `prefix` (a folder, without trailing slash).
pub fn list(prefix: &str) -> Vec<&'static str> {
    let folder = format!("{}/", prefix.trim_end_matches('/'));
    embedded::FILES.iter().map(|(name, _)| *name).filter(|name| name.starts_with(&folder)).collect()
}

/// A mod text file decoded as UTF-8 (`Path.read_text(encoding="utf-8")`).
pub fn text(relative: &str) -> Option<&'static str> {
    file(relative).map(|bytes| std::str::from_utf8(bytes).expect("mod data files are UTF-8"))
}

/// Whether `relative` is an embedded folder (only the `languages/` tree's
/// folders are recorded, without trailing slash).
pub fn folder(relative: &str) -> bool {
    let relative = relative.replace('\\', "/");
    embedded::FOLDERS.binary_search(&relative.as_str()).is_ok()
}
