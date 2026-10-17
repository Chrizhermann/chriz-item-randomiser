//! The mod data the configurator reads, embedded at build time: the original
//! tables under `lists/`, the BG1 name maps, game texts, the mod's stand-in
//! items and the editor UI. Paths are relative to the mod root.

mod embedded {
    include!(concat!(env!("OUT_DIR"), "/embedded.rs"));
}

/// The bytes of a mod file, by its path relative to the mod root.
pub fn file(relative: &str) -> Option<&'static [u8]> {
    let relative = relative.replace('\\', "/");
    embedded::FILES
        .binary_search_by(|(name, _)| name.cmp(&relative.as_str()))
        .ok()
        .map(|index| embedded::FILES[index].1)
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
