//! The local editor server. PORT-PENDING.

use cir_config::{Error, Result, Value};
use std::path::Path;

#[allow(clippy::too_many_arguments)]
pub fn serve(
    _catalog: &Path,
    _world: &Path,
    _workspace: &Path,
    _port: i64,
    _profile: Option<&Path>,
    _plan: Option<&Path>,
    _apply_game: Option<&Path>,
    _open_browser: bool,
) -> Result<Value> {
    Err(Error::profile("PORT-PENDING: editor"))
}
