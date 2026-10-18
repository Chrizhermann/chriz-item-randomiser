//! Explicit authored stock without a physical inventory record to remove
//! (the reference's `virtual_sources.py`).

use crate::error::{Error, Result};
use crate::json::Value;
use crate::profiles::is_hex256;

pub const ADAPTER_ID: &str = "virtual-stock-v1";
const FIELDS: [&str; 9] =
    ["id", "item", "resource", "resource_sha256", "adapter_id", "selector", "copies", "optional", "payload"];
const PAYLOAD_FIELDS: [&str; 4] = ["resref", "expiration", "charges", "flags"];

/// `[a-z0-9#_-]{1,8}`.
fn is_item(text: &str) -> bool {
    (1..=8).contains(&text.len())
        && text.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"#_-".contains(&byte))
}

/// `[a-z0-9][a-z0-9:_/-]{0,63}`.
fn is_selector(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes[1..].iter().all(|&byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b":_/-".contains(&byte))
}

/// Require one exact, immutable authored row with caller-supplied ITM charges.
pub fn validate_registration(row: &Value) -> Result<()> {
    let Some(fields) = row.as_dict().filter(|fields| fields.has_exact_keys(&FIELDS)) else {
        return Err(Error::profile("Unsupported virtual source registration fields"));
    };
    let field = |key: &str| fields.get(key).expect("exact fields");
    let item = field("item").as_str().filter(|item| is_item(item));
    let selector = field("selector").as_str().filter(|selector| is_selector(selector));
    let valid = match (item, selector) {
        (Some(item), Some(selector)) => {
            field("id").py_eq(&Value::Str(format!("virtual:{selector}")))
                && field("resource").py_eq(&Value::Str(format!("itm:{item}")))
                && field("adapter_id").as_str() == Some(ADAPTER_ID)
                && matches!(field("optional"), Value::Bool(false))
                && field("resource_sha256").as_str().is_some_and(is_hex256)
                && crate::profiles::int_in(Some(field("copies")), 1, 0xFFFF_FFFF).is_some()
        }
        _ => false,
    };
    if !valid {
        return Err(Error::profile("Unsupported virtual source registration"));
    }
    let item = item.expect("checked");
    let payload = field("payload");
    let valid_payload = payload.as_dict().is_some_and(|payload| {
        payload.has_exact_keys(&PAYLOAD_FIELDS)
            && payload.get("resref").is_some_and(|resref| resref.py_eq(&Value::from(item)))
            && matches!(payload.get("expiration"), Some(Value::Int(0)))
            && matches!(payload.get("flags"), Some(Value::Int(0)))
            && match payload.get("charges") {
                Some(Value::List(charges)) => {
                    charges.len() == 3
                        && charges.iter().all(|charge| crate::profiles::int_in(Some(charge), 0, 0xFFFF).is_some())
                }
                _ => false,
            }
    });
    if !valid_payload {
        return Err(Error::profile("Invalid virtual source item payload"));
    }
    Ok(())
}

/// An authorable row from a selected item and its installed ITM charges.
pub fn virtual_registration(item: &str, selector: &str, copies: i64, resource_sha256: &str, charges: &Value) -> Result<Value> {
    let row = Value::Dict(crate::dict! {
        "id" => format!("virtual:{selector}"), "item" => item, "resource" => format!("itm:{item}"),
        "resource_sha256" => resource_sha256, "adapter_id" => ADAPTER_ID,
        "selector" => selector, "copies" => copies, "optional" => false,
        "payload" => crate::dict! {"resref" => item, "expiration" => 0, "charges" => charges.clone(), "flags" => 0},
    });
    validate_registration(&row)?;
    Ok(row)
}

pub fn campaign_record(row: &Value) -> Result<Value> {
    validate_registration(row)?;
    let field = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
    Ok(Value::Dict(crate::dict! {
        "id" => field("id"), "item" => field("item"),
        "resource" => field("resource"), "owner" => field("resource"),
        "kind" => "virtual", "record" => field("payload"),
        "adapter_id" => ADAPTER_ID, "selector" => field("selector"),
        "concerns" => crate::list![],
    }))
}
