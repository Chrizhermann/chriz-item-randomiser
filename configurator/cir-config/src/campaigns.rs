//! Campaign compilation (the reference's `campaigns.py`). PORT-PENDING.

use crate::error::{Error, Result};
use crate::json::Value;
use std::path::Path;

pub fn write_campaign(_profile: &Value, _catalog: &Value, _world: &Value, _plan: &Value, _store: &Path) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: write_campaign"))
}

pub fn read_campaign(_store: &Path, _revision: Option<&str>) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: read_campaign"))
}

pub fn compile_campaign(_profile: &Value, _catalog: &Value, _world: &Value, _plan: &Value) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: compile_campaign"))
}
