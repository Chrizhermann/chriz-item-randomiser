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
pub mod codepages;
pub mod prepared;
pub mod scripted_sources;
pub mod virtual_sources;
pub mod pins;
pub mod quest_rewards;
pub mod quest_options;
pub mod translations;
pub mod script_text;
pub mod mode2_rewards;
pub mod container_rewards;
pub mod reward_copies;
pub mod reward_routes;
pub mod duplicates;
pub mod original_options;
pub mod cursed_disguises;
pub mod spell_rewards;
pub mod store_inventory;
pub mod creature_inventory;
pub mod group_recipients;
pub mod sources;
pub mod templates;
pub mod platform;
pub mod original_catalog;
pub mod original_registrations;
pub mod campaigns;
pub mod native_export;
pub mod original_configuration;
pub mod starts;
pub mod activation;
mod processes;
pub mod editor;
pub mod game_folder;

pub use error::{Error, ErrorKind, Result};
pub use json::{Dict, Value};
