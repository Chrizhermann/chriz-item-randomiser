//! The one scripted source: BLPOOL's Mace of Disruption grant (the
//! reference's `scripted_sources.py`).
//!
//! The installer splits the pool's reward block (lib/campaign_patches.tpa):
//! the selected copy, guarded by FLPR_QPOOL, gives nothing and sets
//! FLPR_XPOOL; the excluded copy keeps the original grant. The pool is offered
//! only when override/FLPRPAT.lua lists that change as applied.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::profiles::is_hex256;

pub const SOURCE_ID: &str = "bcs:blpool/grant/pool-reward";
pub const RESOURCE: &str = "bcs:blpool";
pub const ITEM: &str = "blun12";
pub const ADAPTER_ID: &str = "blpool-v1";
pub const SELECTOR: &str = "pool-reward";
/// Item Randomiser's BG2 table lists the pool.
pub const CAMPAIGN: &str = "bg2";

/// `{"resref": ITEM, "expiration": 0, "charges": [0, 0, 0], "flags": 0}`.
pub fn payload() -> Value {
    Value::Dict(crate::dict! {"resref" => ITEM, "expiration" => 0, "charges" => crate::list![0, 0, 0], "flags" => 0})
}

fn registration(sha256: &str) -> Dict {
    crate::dict! {
        "id" => SOURCE_ID, "item" => ITEM, "resource" => RESOURCE,
        "resource_sha256" => sha256, "adapter_id" => ADAPTER_ID,
        "selector" => SELECTOR, "copies" => 1, "optional" => false,
    }
}

/// The registration for the pool that the installer prepared.
pub fn blpool_registration(world: &Value) -> Result<Value> {
    let matches: Vec<&Dict> = match world.get("resources") {
        Some(Value::List(rows)) => rows
            .iter()
            .filter_map(Value::as_dict)
            .filter(|row| row.get("id").and_then(Value::as_str) == Some(RESOURCE))
            .collect(),
        _ => Vec::new(),
    };
    let sha256 = matches.first().and_then(|row| row.get("sha256")).and_then(Value::as_str);
    if matches.len() != 1
        || matches[0].get("kind").and_then(Value::as_str) != Some("bcs")
        || matches[0].get("resref").and_then(Value::as_str) != Some("blpool")
        || !sha256.is_some_and(is_hex256)
    {
        return Err(Error::profile("The scan has no BLPOOL.BCS"));
    }
    let game_root = world.at("game_root")?.as_str().ok_or_else(|| Error::type_error("game_root must be a string"))?;
    if !crate::prepared::applied(game_root, "quest_pool")? {
        return Err(Error::profile(
            "Chriz Item Randomiser did not prepare BLPOOL.BCS; reinstall it to use the pool reward",
        ));
    }
    Ok(Value::Dict(registration(sha256.expect("checked"))))
}

pub fn validate_registration(row: &Value) -> Result<()> {
    let digest = row.get("resource_sha256").and_then(Value::as_str);
    match digest {
        Some(digest) if is_hex256(digest) && row.py_eq(&Value::Dict(registration(digest))) => Ok(()),
        _ => Err(Error::profile("Unsupported registered script source")),
    }
}

pub fn campaign_record(row: &Value) -> Result<Value> {
    validate_registration(row)?;
    Ok(Value::Dict(crate::dict! {
        "id" => SOURCE_ID, "item" => ITEM, "resource" => RESOURCE,
        "owner" => RESOURCE, "kind" => "scripted", "record" => payload(),
        "adapter_id" => ADAPTER_ID, "selector" => SELECTOR, "concerns" => crate::list![],
    }))
}
