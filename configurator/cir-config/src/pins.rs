//! Hashes that the resource adapters pin their inputs and outputs to.
//!
//! Release builds use the reviewed values only. Built with the
//! `parity-overrides` feature, the parity tests can change entries the way
//! the reference's tests use `patch.dict`: `CIR_PIN_OVERRIDES` names a JSON
//! file mapping a table name such as `"quest_options.ORIGINAL_HASHES"` to
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

/// The draft the binding check uses instead of resolving the preset's
/// template: with `parity-overrides`, the JSON file `CIR_PARITY_REGISTRATION_DRAFT`
/// names (as the reference's tests patch `resolve_template` there); otherwise
/// none.
#[cfg(feature = "parity-overrides")]
pub fn registration_draft() -> Option<crate::json::Value> {
    let path = std::env::var_os("CIR_PARITY_REGISTRATION_DRAFT")?;
    let data = std::fs::read(&path).expect("CIR_PARITY_REGISTRATION_DRAFT names a readable file");
    Some(crate::json::parse_strict(&data, "draft").expect("CIR_PARITY_REGISTRATION_DRAFT is JSON"))
}

#[cfg(not(feature = "parity-overrides"))]
pub fn registration_draft() -> Option<crate::json::Value> {
    None
}

/// Entries a parity test puts into a reference data table (`patch.dict`):
/// with `parity-overrides`, from the JSON file `CIR_PARITY_TABLES` names,
/// mapping a table name such as `"mode2_rewards.REWARDS"` to `{key: entry}`;
/// otherwise none.
#[cfg(feature = "parity-overrides")]
pub fn table_entries(name: &str) -> Vec<(String, crate::json::Value)> {
    let Some(path) = std::env::var_os("CIR_PARITY_TABLES") else { return Vec::new() };
    let data = std::fs::read(&path).expect("CIR_PARITY_TABLES names a readable file");
    let tables = crate::json::parse_strict(&data, "tables").expect("CIR_PARITY_TABLES is a JSON object");
    tables
        .get(name)
        .and_then(crate::json::Value::as_dict)
        .map(|entries| entries.iter().map(|(key, value)| (key.to_string(), value.clone())).collect())
        .unwrap_or_default()
}

#[cfg(not(feature = "parity-overrides"))]
pub fn table_entries(_name: &str) -> Vec<(String, crate::json::Value)> {
    Vec::new()
}

/// Preset campaigns a parity test sets, as the reference's tests patch
/// `templates.CAMPAIGNS`, `templates._PARTS` and `platform.PRESET_CAMPAIGNS`
/// to add campaigns that read other tables.
pub struct Campaigns {
    /// The preset campaigns and their labels, in order.
    pub campaigns: Vec<(&'static str, &'static str)>,
    /// The table campaigns each preset campaign reads.
    pub parts: Vec<(&'static str, Vec<&'static str>)>,
    /// The preset campaigns each game can have.
    pub games: Vec<(&'static str, &'static [&'static str])>,
}

/// With `parity-overrides`, the campaigns of the JSON file
/// `CIR_PARITY_CAMPAIGNS` names: `{"campaigns": [[id, label], ...], "parts":
/// {id: [table, ...]}, "preset_campaigns": {game: [id, ...]}}`; otherwise none.
#[cfg(feature = "parity-overrides")]
pub fn campaigns() -> Option<&'static Campaigns> {
    use crate::json::Value;
    use std::sync::OnceLock;
    fn name(text: &str) -> &'static str {
        Box::leak(text.to_string().into_boxed_str())
    }
    fn names(value: &Value) -> Vec<&'static str> {
        value.as_list().expect("a list of names").iter().map(|item| name(item.as_str().expect("a name"))).collect()
    }
    static CAMPAIGNS: OnceLock<Option<Campaigns>> = OnceLock::new();
    CAMPAIGNS
        .get_or_init(|| {
            let path = std::env::var_os("CIR_PARITY_CAMPAIGNS")?;
            let data = std::fs::read(&path).expect("CIR_PARITY_CAMPAIGNS names a readable file");
            let value = crate::json::parse_strict(&data, "campaigns").expect("CIR_PARITY_CAMPAIGNS is JSON");
            let table = |key: &str| value.get(key).and_then(Value::as_dict).expect("a campaign table");
            let campaigns = value.get("campaigns").and_then(Value::as_list).expect("a campaign list");
            Some(Campaigns {
                campaigns: campaigns
                    .iter()
                    .map(|row| match names(row).as_slice() {
                        [campaign, label] => (*campaign, *label),
                        _ => panic!("a campaign is [id, label]"),
                    })
                    .collect(),
                parts: table("parts").iter().map(|(campaign, parts)| (name(campaign), names(parts))).collect(),
                games: table("preset_campaigns")
                    .iter()
                    .map(|(game, campaigns)| (name(game), &*Box::leak(names(campaigns).into_boxed_slice())))
                    .collect(),
            })
        })
        .as_ref()
}

#[cfg(not(feature = "parity-overrides"))]
pub fn campaigns() -> Option<&'static Campaigns> {
    None
}
