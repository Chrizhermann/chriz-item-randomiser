//! Hashes that the resource adapters pin their inputs and outputs to.
//!
//! Release builds use the reviewed values only. Built with the
//! `parity-overrides` feature, the parity tests can change entries the way
//! the reference's tests use `patch.dict`: `CIR_PIN_OVERRIDES` names a JSON
//! file mapping a table name such as `"mode2_rewards.ORIGINAL_HASHES"` to
//! `{key: hash}`. Overridden keys keep their place, new keys are appended.

#[cfg(feature = "parity-overrides")]
fn overrides() -> &'static std::collections::HashMap<String, Vec<(String, String)>> {
    use std::sync::OnceLock;
    static OVERRIDES: OnceLock<std::collections::HashMap<String, Vec<(String, String)>>> = OnceLock::new();
    OVERRIDES.get_or_init(|| {
        let mut tables = std::collections::HashMap::new();
        let Some(path) = std::env::var_os("CIR_PIN_OVERRIDES") else { return tables };
        let data = std::fs::read(&path).expect("CIR_PIN_OVERRIDES names a readable file");
        let value = crate::json::parse_strict(&data, "pins").expect("CIR_PIN_OVERRIDES is a JSON object");
        for (table, entries) in value.as_dict().expect("object").iter() {
            let entries = entries
                .as_dict()
                .expect("table object")
                .iter()
                .map(|(key, hash)| (key.to_string(), hash.as_str().expect("hash string").to_string()))
                .collect();
            tables.insert(table.to_string(), entries);
        }
        tables
    })
}

#[cfg(not(feature = "parity-overrides"))]
fn overrides() -> &'static std::collections::HashMap<String, Vec<(String, String)>> {
    use std::sync::OnceLock;
    static EMPTY: OnceLock<std::collections::HashMap<String, Vec<(String, String)>>> = OnceLock::new();
    EMPTY.get_or_init(std::collections::HashMap::new)
}

/// One table's entries: the pinned ones with any test overrides applied.
pub fn table(name: &str, pinned: &[(&str, &str)]) -> Vec<(String, String)> {
    let mut entries: Vec<(String, String)> =
        pinned.iter().map(|(key, hash)| (key.to_string(), hash.to_string())).collect();
    if let Some(changes) = overrides().get(name) {
        for (key, hash) in changes {
            match entries.iter_mut().find(|(existing, _)| existing == key) {
                Some(entry) => entry.1 = hash.clone(),
                None => entries.push((key.clone(), hash.clone())),
            }
        }
    }
    entries
}

/// One entry of a table (`TABLE.get(key)`).
pub fn get(name: &str, pinned: &[(&str, &str)], key: &str) -> Option<String> {
    table(name, pinned).into_iter().find(|(existing, _)| existing == key).map(|(_, hash)| hash)
}
