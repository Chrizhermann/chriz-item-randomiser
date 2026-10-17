//! Chriz Item Randomiser's post-install configurator.
//!
//! A port of the reference implementation in `tools/flir_config` (Python) with
//! the same inputs, outputs and messages: scan an installed game, build and
//! edit configurations from the shipped presets, check and compile them, and
//! export or apply the result. Game folders are only read, except by the
//! explicit apply step, which writes the mod's own `randomiser-campaigns/`.

pub mod error;
pub mod json;
pub mod py;
mod unicode_tables;
pub mod data;
pub mod paths;
pub mod zip;
pub mod resources;
pub mod profiles;
pub mod world_formats;
pub mod game_names;
pub mod game_text;
pub mod prepared;
pub mod scripted_sources;
pub mod virtual_sources;
pub mod pins;
pub mod sources;
pub mod templates;
pub mod campaigns;
pub mod native_export;

pub use error::{Error, ErrorKind, Result};
pub use json::{Dict, Value};
