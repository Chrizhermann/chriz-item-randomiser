//! The reviewed Deck of Many Things Key spell's one Create Item effect (the
//! reference's `spell_rewards.py`).
//!
//! Original Mode 2 replaces opcode 122 through an EFF in lib/weidu_action.tpa.
//! The native campaign instead invokes FLPRKEY at that same effect position;
//! the callback retains or suppresses the original ring and records its
//! recipient.
//!
//! The reviewed hashes are read through [`crate::pins`] as table
//! `spell_rewards` with keys `ORIGINAL_SHA256` and `PATCHED_SHA256` (the
//! reference's module constants).

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::py::{put_u16, u16_le, u32_le};
use crate::resources::sha256_hex;
use crate::{dict, list};

pub const ORIGINAL_SHA256: &str = "01c39682e3f9d4d0c8274a893b784924ae5b8cd1464afdcc34d04d0190addb50";
pub const PATCHED_SHA256: &str = "94aa22201f50a94c343dbb2f5ba2946f31ffa22b71d3d1ab23051f94f79869da";
pub const EFFECT_OFFSET: usize = 0x12A;
const PINS: &[(&str, &str)] = &[("ORIGINAL_SHA256", ORIGINAL_SHA256), ("PATCHED_SHA256", PATCHED_SHA256)];

/// `ORIGINAL_SHA256` with any test override.
pub fn original_sha256() -> String {
    crate::pins::get("spell_rewards", PINS, "ORIGINAL_SHA256").expect("pinned hash")
}

/// `PATCHED_SHA256` with any test override.
pub fn patched_sha256() -> String {
    crate::pins::get("spell_rewards", PINS, "PATCHED_SHA256").expect("pinned hash")
}

/// `SPELL`: the key spell's quest binding.
pub fn spell() -> Dict {
    dict! {
        "quest" => "key", "resource" => "spl:spin621", "item" => "ring41",
        "selected_global" => "FLPR_QKEY", "consumed_global" => "FLPR_XKEY",
        "event_global" => "FLPR_EKEY", "recipient_local" => "FLPR_RKEY",
    }
}

/// `PAYLOAD`: the ring the spell creates.
pub fn payload() -> Value {
    Value::Dict(dict! {"resref" => "ring41", "expiration" => 0, "charges" => list![1, 0, 0], "flags" => 0})
}

/// Accept the reviewed original or recover only our exact static patch.
pub fn original_key_spell(data: &[u8]) -> Result<Vec<u8>> {
    let digest = sha256_hex(data);
    if digest == original_sha256() {
        return Ok(data.to_vec());
    }
    if digest == patched_sha256() {
        let mut original = data.to_vec();
        put_u16(&mut original, EFFECT_OFFSET, 122);
        original[EFFECT_OFFSET + 0x14..EFFECT_OFFSET + 0x1C].copy_from_slice(b"ring41\0\0");
        if sha256_hex(&original) == original_sha256() {
            return Ok(original);
        }
    }
    Err(Error::profile("Deck of Many Things Key spell changed; rescan and review it"))
}

/// Replace only the item's opcode and resref; retain timing and targeting.
pub fn patch_key_spell(data: &[u8]) -> Result<Vec<u8>> {
    let data = original_key_spell(data)?;
    if data.len() != 394
        || &data[..8] != b"SPL V1  "
        || (u32_le(&data, 0x64), u16_le(&data, 0x68), u32_le(&data, 0x6A), u16_le(&data, 0x6E), u16_le(&data, 0x70))
            != (114, 1, 154, 0, 0)
        || (u16_le(&data, 114 + 0x1E), u16_le(&data, 114 + 0x20)) != (5, 0)
    {
        return Err(Error::profile("Unsupported Deck of Many Things Key spell layout"));
    }
    let expected = crate::py::from_hex(concat!(
        "7a00020001000000000000000900000000006400",
        "72696e67343100000000000000000000000000000000000000000000"
    ))
    .expect("hex");
    if data[EFFECT_OFFSET..EFFECT_OFFSET + 0x30] != expected[..] {
        return Err(Error::profile("Unsupported Deck of Many Things Key item effect"));
    }
    let mut out = data;
    put_u16(&mut out, EFFECT_OFFSET, 402);
    out[EFFECT_OFFSET + 0x14..EFFECT_OFFSET + 0x1C].copy_from_slice(b"FLPRKEY\0");
    if sha256_hex(&out) != patched_sha256() {
        return Err(Error::profile("Deck of Many Things Key patch differs from its reviewed bytes"));
    }
    Ok(out)
}
