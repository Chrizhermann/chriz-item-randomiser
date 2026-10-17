//! Native export (the reference's `native_export.py`). PORT-PENDING.

use crate::error::{Error, Result};
use crate::json::Value;
use std::path::Path;

pub fn export_native(_store: &Path, _catalog: &Value, _world: &Value, _out: &Path, _revision: Option<&str>) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: export_native"))
}

/// The reference's `_compile`: (native fixture, required resources,
/// pristine resources). PORT-PENDING.
pub fn compile(
    _manifest: &Value,
    _world: &Value,
    _index: &crate::sources::WorldIndex,
    _items: &crate::profiles::ItemIndex,
    _catalog: &Value,
    _problems: Option<&mut crate::json::Dict>,
) -> Result<(Value, std::collections::BTreeSet<String>, std::collections::BTreeSet<String>)> {
    Err(Error::profile("PORT-PENDING: native compile"))
}
