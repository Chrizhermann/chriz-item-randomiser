//! Fitting the shipped original tables to a scanned game (the reference's
//! `original_configuration.py`). PORT-PENDING.

use crate::error::{Error, Result};
use crate::json::Value;

pub fn fit_original_configuration(
    _catalog: &Value,
    _world: &Value,
    _template_id: &Value,
    _items: Option<&Value>,
    _targets: Option<&Value>,
    _options: Option<&Value>,
) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: fit_original_configuration"))
}
