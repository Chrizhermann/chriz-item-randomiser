//! Native export (the reference's `native_export.py`). PORT-PENDING.

use crate::error::{Error, Result};
use crate::json::Value;
use std::path::Path;

pub fn export_native(_store: &Path, _catalog: &Value, _world: &Value, _out: &Path, _revision: Option<&str>) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: export_native"))
}
