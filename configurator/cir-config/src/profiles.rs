//! Validated draft profiles and immutable preparation records (the
//! reference's `profiles.py`).
//!
//! Preparation is deliberately not runtime activation. Source binding and the
//! campaign adapter establish relocation safety before a draft can run.

use crate::error::{Error, ErrorKind, Result};
use crate::json::{self, Dict, Value};
use crate::paths;
use crate::resources::{catalog_fingerprint, sha256_hex, ITEM_SEMANTIC_FIELDS};
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};

pub const PROFILE_SCHEMA: &str = "flir-profile-v1";
pub const PREPARED_SCHEMA: &str = "flir-prepared-profile-v1";
pub const PROFILE_FIELDS: [&str; 5] = ["schema", "id", "name", "catalog_fingerprint", "items"];
pub const MAX_ITEM_PRICE: i64 = 0xFFFF_FFFF;

/// `{"percent": 120, "base_prices": {}}`.
pub fn default_shop_pricing() -> Value {
    Value::Dict(crate::dict! {"percent" => 120, "base_prices" => Dict::new()})
}

/// `HEX256.fullmatch(text)`: 64 lowercase hexadecimal digits.
pub fn is_hex256(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub fn value_is_hex256(value: Option<&Value>) -> bool {
    matches!(value, Some(Value::Str(text)) if is_hex256(text))
}

/// `PROFILE_ID.fullmatch(text)`: `[a-z0-9][a-z0-9_-]{0,63}`.
pub fn is_profile_id(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes[1..].iter().all(|&byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_' || byte == b'-')
}

/// `type(value) is int and low <= value <= high`.
pub fn int_in(value: Option<&Value>, low: i64, high: i64) -> Option<i64> {
    match value {
        Some(Value::Int(number)) if (low..=high).contains(number) => Some(*number),
        _ => None,
    }
}

/// The reference's `canonical_bytes`.
pub fn canonical_bytes(value: &Value) -> Result<Vec<u8>> {
    json::canonical_bytes(value)
}

/// The reference's `_digest`: SHA-256 of the canonical bytes.
pub fn digest(value: &Value) -> Result<String> {
    Ok(sha256_hex(&canonical_bytes(value)?))
}

/// The reference's `read_json`: a strict JSON object from a file.
pub fn read_json(path: &Path) -> Result<Value> {
    let data = fs::read(path).map_err(|error| Error::os(&error, path))?;
    json::parse_strict(&data, &paths::name(path))
}

/// Validate shop prices for selected rewards; catalogue prices stay unchanged.
pub fn validate_shop_pricing(pricing: &Value, items: &[Value], catalog: &Value, index: &ItemIndex) -> Result<Value> {
    let Some(fields) = pricing.as_dict().filter(|fields| fields.has_exact_keys(&["percent", "base_prices"])) else {
        return Err(Error::profile("Shop pricing needs percent and base_prices"));
    };
    let Some(percent) = int_in(fields.get("percent"), 0, MAX_ITEM_PRICE) else {
        return Err(Error::profile("Shop price percent must be a nonnegative 32-bit integer"));
    };
    let Some(prices) = fields.get("base_prices").and_then(Value::as_dict) else {
        return Err(Error::profile("Shop base prices must map selected items to gold amounts"));
    };
    for (resref, price) in prices.iter() {
        if !items.iter().any(|item| item.as_str() == Some(resref)) {
            return Err(Error::profile("Shop base prices must refer to selected item identities"));
        }
        if int_in(Some(price), 0, MAX_ITEM_PRICE).is_none() {
            return Err(Error::profile(format!("Invalid shop base price: {resref}")));
        }
    }
    for item in items {
        let resref = item.as_str().expect("validated profile items are strings");
        let base = match prices.get(resref) {
            Some(price) => price.as_int().expect("validated"),
            None => index.row(catalog, resref).and_then(|row| row.get("price")).and_then(Value::as_int).expect("price"),
        };
        if (base as i128 * percent as i128).div_euclid(100) > MAX_ITEM_PRICE as i128 {
            return Err(Error::profile(format!("Effective shop price exceeds 32-bit gold range: {resref}")));
        }
    }
    Ok(pricing.clone())
}

/// The catalogue's items by resref (`validate_catalog`'s result).
#[derive(Clone, Debug, Default)]
pub struct ItemIndex {
    positions: HashMap<String, usize>,
    order: Vec<String>,
}

impl ItemIndex {
    pub fn contains(&self, resref: &str) -> bool {
        self.positions.contains_key(resref)
    }
    pub fn len(&self) -> usize {
        self.order.len()
    }
    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }
    pub fn resrefs(&self) -> &[String] {
        &self.order
    }
    pub fn row<'a>(&self, catalog: &'a Value, resref: &str) -> Option<&'a Dict> {
        let position = *self.positions.get(resref)?;
        catalog.get("items")?.as_list()?.get(position)?.as_dict()
    }
    pub fn row_value<'a>(&self, catalog: &'a Value, resref: &str) -> Option<&'a Value> {
        let position = *self.positions.get(resref)?;
        catalog.get("items")?.as_list()?.get(position)
    }
}

/// `Path(text).is_absolute()` for a Windows path: a drive letter and a root,
/// or a UNC share.
pub fn is_absolute_windows(text: &str) -> bool {
    let (drive, root, _) = paths::splitroot(text);
    !drive.is_empty() && !root.is_empty()
}

pub fn validate_catalog(catalog: &Value) -> Result<ItemIndex> {
    let empty = Dict::new();
    let fields = catalog.as_dict().unwrap_or(&empty);
    if fields.get("schema").and_then(Value::as_str) != Some("flir-item-catalog-v1") {
        return Err(Error::profile("Unsupported item catalogue schema"));
    }
    let fingerprint = fields.get("fingerprint");
    if !value_is_hex256(fingerprint) {
        return Err(Error::profile("Invalid item catalogue fingerprint"));
    }
    match fields.get("game_root") {
        Some(Value::Str(root)) if is_absolute_windows(root) => {}
        _ => return Err(Error::profile("Catalogue must name an absolute game root")),
    }
    match fields.get("language") {
        Some(Value::Str(language)) if !language.is_empty() => {}
        _ => return Err(Error::profile("Catalogue language is missing")),
    }
    let Some(items) = fields.get("items").and_then(Value::as_list) else {
        return Err(Error::profile("Catalogue items must be a list"));
    };
    let mut index = ItemIndex::default();
    for (position, row) in items.iter().enumerate() {
        let Some(row) = row.as_dict() else { return Err(Error::profile("Invalid catalogue item")) };
        let resref = match row.get("resref") {
            Some(Value::Str(resref))
                if (1..=8).contains(&resref.chars().count())
                    && *resref == crate::py::lower(resref)
                    && resref.chars().all(|c| (33..=126).contains(&(c as u32)) && !"/\\:*?\"<>|".contains(c))
                    && !index.contains(resref) =>
            {
                resref.clone()
            }
            _ => return Err(Error::profile("Invalid or duplicate catalogue item identity")),
        };
        match row.get("name") {
            Some(Value::Str(name)) if !name.is_empty() => {}
            _ => return Err(Error::profile(format!("Missing item name: {resref}"))),
        }
        for field in ["unidentified_name", "identified_name"] {
            match row.get(field) {
                None | Some(Value::Null) | Some(Value::Str(_)) => {}
                _ => return Err(Error::profile(format!("Invalid item name field: {resref}"))),
            }
        }
        for field in ["category", "flags", "price", "stack_size", "enchantment", "proficiency"] {
            if int_in(row.get(field), 0, 0xFFFF_FFFF).is_none() {
                return Err(Error::profile(format!("Invalid item {field}: {resref}")));
            }
        }
        if !value_is_hex256(row.get("sha256")) {
            return Err(Error::profile(format!("Invalid item fingerprint: {resref}")));
        }
        match row.get("resource_origin").and_then(Value::as_str) {
            Some("override" | "biff") => {}
            _ => return Err(Error::profile(format!("Invalid resource origin: {resref}"))),
        }
        index.positions.insert(resref.clone(), position);
        index.order.push(resref);
    }
    if index.order.windows(2).any(|pair| pair[0] > pair[1]) {
        return Err(Error::profile("Catalogue items must be ordered by resource identity"));
    }
    if fingerprint.and_then(Value::as_str) != Some(catalog_fingerprint(catalog)?.as_str()) {
        return Err(Error::profile("Catalogue content changed; scan the installation again"));
    }
    Ok(index)
}

pub fn validate_profile(profile: &Value, catalog: &Value, allow_rebase: bool) -> Result<Value> {
    let index = validate_catalog(catalog)?;
    let unsupported = || Error::profile("Unsupported profile schema or fields");
    let Some(fields) = profile.as_dict() else { return Err(unsupported()) };
    let plain = fields.has_exact_keys(&PROFILE_FIELDS);
    let priced = fields.has_exact_keys(&["schema", "id", "name", "catalog_fingerprint", "items", "shop_pricing"]);
    if !(plain || priced) || fields.get("schema").and_then(Value::as_str) != Some(PROFILE_SCHEMA) {
        return Err(unsupported());
    }
    if !matches!(fields.get("id"), Some(Value::Str(id)) if is_profile_id(id)) {
        return Err(Error::profile(
            "Profile ID must use 1-64 lowercase letters, digits, hyphens or underscores",
        ));
    }
    let name = match fields.get("name") {
        Some(Value::Str(name)) if !crate::py::strip(name).is_empty() && name.chars().count() <= 120 => name,
        _ => return Err(Error::profile("Profile name must contain 1-120 characters")),
    };
    if name.chars().any(|c| (c as u32) < 32) {
        return Err(Error::profile("Profile name must not contain control characters"));
    }
    let fingerprint = fields.get("catalog_fingerprint");
    if !value_is_hex256(fingerprint) {
        return Err(Error::profile("Invalid profile catalogue fingerprint"));
    }
    if !allow_rebase && !fingerprint.expect("checked").py_eq(catalog.get("fingerprint").unwrap_or(&Value::Null)) {
        return Err(Error::profile("Profile belongs to another scan; explicitly rebase it before preparing"));
    }
    let items = match fields.get("items") {
        Some(Value::List(items)) if items.iter().all(Value::is_str) => items,
        _ => return Err(Error::profile("Profile items must be a list of resource identities")),
    };
    let names: Vec<&str> = items.iter().map(|item| item.as_str().expect("checked")).collect();
    if names.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(Error::profile("Profile items must be unique and sorted"));
    }
    let missing: Vec<&str> = names.iter().copied().filter(|name| !index.contains(name)).collect();
    if !missing.is_empty() {
        return Err(Error::profile(format!("Selected items absent from this scan: {}", missing.join(", "))));
    }
    if let Some(pricing) = fields.get("shop_pricing") {
        validate_shop_pricing(pricing, items, catalog, &index)?;
    }
    Ok(profile.clone())
}

pub fn new_profile(catalog: &Value, profile_id: &str, name: &str) -> Result<Value> {
    new_profile_named(catalog, profile_id, &Value::from(name))
}

/// `new_profile` with a name of any JSON type (the editor passes request data).
pub fn new_profile_named(catalog: &Value, profile_id: &str, name: &Value) -> Result<Value> {
    let mut profile = Dict::new();
    profile.insert("schema", PROFILE_SCHEMA);
    profile.insert("id", profile_id);
    profile.insert("name", name.clone());
    profile.insert("catalog_fingerprint", catalog.get("fingerprint").cloned().unwrap_or(Value::Null));
    profile.insert("items", Value::List(Vec::new()));
    profile.insert("shop_pricing", default_shop_pricing());
    validate_profile(&Value::Dict(profile), catalog, false)
}

/// Optional edits of `edit_profile`.
#[derive(Default)]
pub struct ProfileEdit<'a> {
    pub include: &'a [String],
    pub exclude: &'a [String],
    pub name: Option<&'a Value>,
    pub shop_price_percent: Option<&'a Value>,
    pub shop_base_prices: Option<&'a Value>,
}

pub fn edit_profile(profile: &Value, catalog: &Value, edit: ProfileEdit<'_>) -> Result<Value> {
    let mut edited = validate_profile(profile, catalog, false)?;
    let included: BTreeSet<String> = edit.include.iter().map(|item| crate::py::lower(item)).collect();
    let excluded: BTreeSet<String> = edit.exclude.iter().map(|item| crate::py::lower(item)).collect();
    if included.intersection(&excluded).next().is_some() {
        return Err(Error::profile("An item cannot be included and excluded in the same edit"));
    }
    let known: std::collections::HashSet<&str> = catalog
        .get("items")
        .and_then(Value::as_list)
        .map(|rows| rows.iter().filter_map(|row| row.get("resref").and_then(Value::as_str)).collect())
        .unwrap_or_default();
    let unknown: Vec<&String> = included.union(&excluded).filter(|item| !known.contains(item.as_str())).collect();
    if !unknown.is_empty() {
        let names: Vec<&str> = unknown.iter().map(|item| item.as_str()).collect();
        return Err(Error::profile(format!("Unknown item identities: {}", names.join(", "))));
    }
    let fields = edited.as_dict_mut().expect("validated profile");
    let mut selected: BTreeSet<String> = fields
        .get("items")
        .and_then(Value::as_list)
        .map(|items| items.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default();
    selected.extend(included.iter().cloned());
    for item in &excluded {
        selected.remove(item);
    }
    let selected: Vec<String> = selected.into_iter().collect();
    fields.insert("items", Value::from(selected.clone()));
    if let Some(pricing) = fields.get_mut("shop_pricing") {
        if let Some(prices) = pricing.get_mut("base_prices").and_then(Value::as_dict_mut) {
            for item in &excluded {
                prices.remove(item);
            }
        }
    }
    if edit.shop_price_percent.is_some() || edit.shop_base_prices.is_some() {
        let pricing = fields.setdefault("shop_pricing", default_shop_pricing());
        if let Some(percent) = edit.shop_price_percent {
            pricing
                .as_dict_mut()
                .ok_or_else(|| Error::type_error("'list' object does not support item assignment"))?
                .insert("percent", percent.clone());
        }
        if let Some(edits) = edit.shop_base_prices {
            let Some(edits) = edits.as_dict() else {
                return Err(Error::profile("Shop base price edits must map item identities to gold amounts"));
            };
            let mut normalized = Dict::new();
            for (key, value) in edits.iter() {
                normalized.insert(crate::py::lower(key), value.clone());
            }
            if normalized.len() != edits.len() {
                return Err(Error::profile("Duplicate shop base price item identity"));
            }
            if normalized.keys().any(|key| !selected.iter().any(|item| item == key)) {
                return Err(Error::profile("Shop base price edits must refer to selected items"));
            }
            let prices = pricing
                .get_mut("base_prices")
                .and_then(Value::as_dict_mut)
                .ok_or_else(|| Error::type_error("base prices are not a dictionary"))?;
            for (resref, price) in normalized.iter() {
                if price.is_null() {
                    prices.remove(resref);
                } else {
                    prices.insert(resref, price.clone());
                }
            }
        }
    }
    if let Some(name) = edit.name {
        fields.insert("name", name.clone());
    }
    validate_profile(&edited, catalog, false)
}

pub fn rebase_profile(profile: &Value, catalog: &Value) -> Result<Value> {
    let mut rebased = validate_profile(profile, catalog, true)?;
    rebased
        .as_dict_mut()
        .expect("validated profile")
        .insert("catalog_fingerprint", catalog.get("fingerprint").cloned().unwrap_or(Value::Null));
    validate_profile(&rebased, catalog, false)
}

pub fn search_items(catalog: &Value, query: &str, category: Option<i64>, offset: i64, limit: i64) -> Result<Value> {
    validate_catalog(catalog)?;
    let query = crate::py::casefold(crate::py::strip(query));
    if query.is_empty() && category.is_none() {
        return Err(Error::profile("Enter a search term or choose an item category"));
    }
    if !(1..=100).contains(&limit) || offset < 0 {
        return Err(Error::profile("Search limit must be 1-100 and offset must be nonnegative"));
    }
    if category.is_some_and(|category| category < 0) {
        return Err(Error::profile("Category must be a nonnegative number"));
    }
    let terms = crate::py::split_whitespace(&query);
    let rows = catalog.get("items").and_then(Value::as_list).expect("validated");
    let mut matches = Vec::new();
    for row in rows {
        if let Some(category) = category {
            if !row.get("category").is_some_and(|value| value.py_eq(&Value::Int(category))) {
                continue;
            }
        }
        let text = |key: &str| match row.get(key) {
            Some(value) if value.truthy() => crate::json::py_str(value),
            _ => String::new(),
        };
        let haystack = crate::py::casefold(
            &["resref", "name", "identified_name", "unidentified_name"].map(text).join(" "),
        );
        if terms.iter().all(|term| haystack.contains(term)) {
            matches.push(row);
        }
    }
    let start = usize::try_from(offset).unwrap_or(usize::MAX).min(matches.len());
    let end = start.saturating_add(limit as usize).min(matches.len());
    let page: Vec<Value> = matches[start..end].iter().map(|row| (*row).clone()).collect();
    let next = offset as i128 + limit as i128;
    let mut result = Dict::new();
    result.insert("total", matches.len());
    result.insert("offset", offset);
    result.insert("items", Value::List(page));
    result.insert("next_offset", if next < matches.len() as i128 { Value::from(next) } else { Value::Null });
    Ok(Value::Dict(result))
}

/// `search_items` with an offset of any size: Python's integers are
/// unbounded, and an offset beyond i64 lies past every match and is echoed.
pub fn search_items_from(catalog: &Value, query: &str, category: Option<i64>, offset: &Value, limit: i64) -> Result<Value> {
    let clamped = match offset {
        Value::Int(value) => *value,
        Value::BigInt(text) if text.starts_with('-') => i64::MIN,
        _ => i64::MAX,
    };
    let mut result = search_items(catalog, query, category, clamped, limit)?;
    if let (Value::BigInt(_), Some(fields)) = (offset, result.as_dict_mut()) {
        fields.insert("offset", offset.clone());
    }
    Ok(result)
}

/// The resolved output path, refused when it lies inside the scanned game.
pub fn ensure_output_outside_game(path: &Path, catalog: &Value) -> Result<PathBuf> {
    let output = paths::resolve(path, false).map_err(|(error, at)| Error::win(&error, &at))?;
    let game_root = match catalog.get("game_root") {
        Some(Value::Str(root)) => PathBuf::from(paths::display(Path::new(root))),
        Some(other) => {
            return Err(Error::type_error(format!(
                "expected str, bytes or os.PathLike object, not {}",
                other.type_name()
            )))
        }
        None => return Err(Error::key("game_root")),
    };
    let game_root = paths::resolve(&game_root, false).map_err(|(error, at)| Error::win(&error, &at))?;
    if paths::is_relative_to(&output, &game_root) {
        return Err(Error::profile("Configuration output must be outside the scanned game directory"));
    }
    Ok(output)
}

/// `Path.mkdir(parents=True, exist_ok=True)`.
pub fn make_dirs(path: &Path) -> Result<()> {
    match fs::create_dir(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let parent = paths::parent(path);
            if parent == path {
                return Err(Error::win(&error, path));
            }
            make_dirs(&parent)?;
            match fs::create_dir(path) {
                Ok(()) => Ok(()),
                Err(error) if paths::is_dir(path).unwrap_or(false) => {
                    let _ = error;
                    Ok(())
                }
                Err(error) => Err(Error::win(&error, path)),
            }
        }
        Err(error) => {
            if paths::is_dir(path).unwrap_or(false) {
                Ok(())
            } else {
                Err(Error::win(&error, path))
            }
        }
    }
}

/// A random file name part like `tempfile`'s (eight characters).
/// `count` random lowercase hexadecimal digits from the system's random
/// source (as `uuid4().hex[:count]`).
pub fn random_hex(count: usize) -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    let mut text = String::with_capacity(count + 16);
    while text.len() < count {
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_usize(text.len());
        text.push_str(&format!("{:016x}", hasher.finish()));
    }
    text.truncate(count);
    text
}

pub fn random_name() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    const LETTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|time| time.as_nanos() as u64).unwrap_or(0);
    let mut state = nanos
        ^ (std::process::id() as u64).rotate_left(32)
        ^ COUNTER.fetch_add(1, Ordering::Relaxed).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    let mut name = String::with_capacity(8);
    for _ in 0..8 {
        // xorshift64*
        state ^= state >> 12;
        state ^= state << 25;
        state ^= state >> 27;
        let value = state.wrapping_mul(0x2545_F491_4F6C_DD1D);
        name.push(LETTERS[(value >> 33) as usize % LETTERS.len()] as char);
    }
    name
}

/// `os.replace(source, target)` with Python's two-path message.
pub fn replace(source: &Path, target: &Path) -> Result<()> {
    fs::rename(source, target).map_err(|error| {
        let mut failure = Error::win(&error, source);
        failure.message.push_str(&format!(" -> {}", crate::error::py_repr_str(&target.to_string_lossy())));
        failure
    })
}

/// Write `data` through a temporary sibling and an atomic replace.
pub fn write_atomic(path: &Path, data: &[u8]) -> Result<()> {
    let parent = paths::parent(path);
    let mut temporary = None;
    let result = (|| {
        let mut attempts = 0;
        let (mut handle, name) = loop {
            let candidate = parent.join(format!(".flir-{}.tmp", random_name()));
            match fs::OpenOptions::new().write(true).create_new(true).open(&candidate) {
                Ok(handle) => break (handle, candidate),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && attempts < 100 => attempts += 1,
                Err(error) => return Err(Error::os(&error, &candidate)),
            }
        };
        temporary = Some(name.clone());
        handle.write_all(data).map_err(|error| Error::os(&error, &name))?;
        handle.flush().map_err(|error| Error::os(&error, &name))?;
        handle.sync_all().map_err(|error| Error::os(&error, &name))?;
        drop(handle);
        replace(&name, path)
    })();
    if let Some(name) = temporary {
        if paths::exists(&name).unwrap_or(false) {
            let _ = fs::remove_file(&name);
        }
    }
    result
}

/// Publish a complete JSON file; never expose a half-written pointer or revision.
pub fn write_json(path: &Path, value: &Value, immutable: bool) -> Result<()> {
    let data = canonical_bytes(value)?;
    make_dirs(&paths::parent(path))?;
    if paths::is_symlink(path) {
        return Err(Error::profile("Refusing to replace a linked configuration file"));
    }
    if immutable && paths::exists(path).map_err(|error| Error::win(&error, path))? {
        let existing = fs::read(path).map_err(|error| Error::os(&error, path))?;
        if existing != data {
            return Err(Error::profile("An immutable configuration revision has conflicting content"));
        }
        return Ok(());
    }
    write_atomic(path, &data)
}

pub fn prepare_profile(profile: &Value, catalog: &Value, store: &Path) -> Result<Value> {
    let frozen = validate_profile(profile, catalog, false)?;
    let selected: Vec<&str> =
        frozen.get("items").and_then(Value::as_list).expect("validated").iter().filter_map(Value::as_str).collect();
    if selected.is_empty() {
        return Err(Error::profile("Select at least one item before preparing a draft"));
    }
    let store = ensure_output_outside_game(store, catalog)?;
    let selected: std::collections::HashSet<&str> = selected.into_iter().collect();
    let mut rows = Vec::new();
    for row in catalog.get("items").and_then(Value::as_list).expect("validated") {
        let resref = row.get("resref").and_then(Value::as_str).unwrap_or("");
        if selected.contains(resref) {
            let mut semantic = Dict::new();
            for key in ITEM_SEMANTIC_FIELDS {
                semantic.insert(key, row.get(key).cloned().unwrap_or(Value::Null));
            }
            rows.push(Value::Dict(semantic));
        }
    }
    let mut body = Dict::new();
    body.insert("schema", PREPARED_SCHEMA);
    body.insert("state", "draft-needs-source-binding");
    body.insert("profile", frozen);
    body.insert("items", Value::List(rows));
    let body = Value::Dict(body);
    let revision = digest(&body)?;
    let revision_path =
        ensure_output_outside_game(&store.join("revisions").join(format!("{revision}.json")), catalog)?;
    let pointer_path = ensure_output_outside_game(&store.join("next-draft.json"), catalog)?;
    write_json(&revision_path, &body, true)?;
    let pointer = Value::Dict(crate::dict! {"schema" => "flir-next-draft-v1", "revision" => revision.clone()});
    write_json(&pointer_path, &pointer, false)?;
    Ok(Value::Dict(crate::dict! {
        "revision" => revision,
        "state" => body.get("state").cloned().unwrap_or(Value::Null),
        "revision_path" => revision_path.to_string_lossy().into_owned(),
        "pointer_path" => pointer_path.to_string_lossy().into_owned(),
    }))
}

pub fn read_prepared(store: &Path, revision: Option<&str>) -> Result<Value> {
    let revision = match revision {
        Some(revision) => Value::from(revision),
        None => {
            let pointer = read_json(&store.join("next-draft.json"))?;
            let fields = pointer.as_dict().expect("read_json returns objects");
            if !fields.has_exact_keys(&["schema", "revision"])
                || fields.get("schema").and_then(Value::as_str) != Some("flir-next-draft-v1")
            {
                return Err(Error::profile("Invalid draft pointer"));
            }
            fields.get("revision").cloned().unwrap_or(Value::Null)
        }
    };
    let Some(revision) = revision.as_str().filter(|text| is_hex256(text)) else {
        return Err(Error::profile("Invalid revision identity"));
    };
    let body = read_json(&store.join("revisions").join(format!("{revision}.json")))?;
    if body.get("schema").and_then(Value::as_str) != Some(PREPARED_SCHEMA)
        || body.get("state").and_then(Value::as_str) != Some("draft-needs-source-binding")
    {
        return Err(Error::profile("Unsupported prepared profile"));
    }
    if digest(&body)? != revision {
        return Err(Error::profile("Prepared profile was modified"));
    }
    Ok(body)
}

/// Whether an error is the reference's `ProfileError`.
pub fn is_profile_error(error: &Error) -> bool {
    error.kind == ErrorKind::Profile
}
