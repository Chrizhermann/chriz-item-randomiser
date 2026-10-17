//! Python-recorded parity cases for the `spell_rewards` scenario
//! (see `configurator/parity/patchers/generate.py`), and the static-patch
//! test of the reference's `tests/test_spell_rewards.py`. Its two Lua
//! callback tests exercise `prototype/FLPRSpell.lua` on Lua VMs, not this
//! patcher, and stay with the reference.

mod common;

use cir_config::py::{put_u16, put_u32};
use cir_config::spell_rewards::EFFECT_OFFSET;
use common::builders::{pinned, sha};

const SCENARIO: &str = "spell_rewards";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

/// `spell_fixture()`: one ability whose third effect grants RING41.
pub fn spell_fixture() -> Vec<u8> {
    let mut data = vec![0u8; 394];
    data[..8].copy_from_slice(b"SPL V1  ");
    put_u32(&mut data, 0x64, 114);
    put_u16(&mut data, 0x68, 1);
    put_u32(&mut data, 0x6A, 154);
    data[114 + 0xC] = 1; // Living target; never assume Player1.
    put_u16(&mut data, 114 + 0x1E, 5);
    for index in 0..5 {
        data[154 + index * 48..154 + (index + 1) * 48].fill(index as u8 + 1);
    }
    let mut effect = Vec::with_capacity(48);
    effect.extend_from_slice(&122u16.to_le_bytes());
    effect.extend_from_slice(&[2, 0]);
    effect.extend_from_slice(&1u32.to_le_bytes());
    effect.extend_from_slice(&0u32.to_le_bytes());
    effect.extend_from_slice(&[9, 0]);
    effect.extend_from_slice(&0u32.to_le_bytes());
    effect.extend_from_slice(&[100, 0]);
    effect.extend_from_slice(b"ring41\0\0");
    effect.extend_from_slice(&[0; 20]);
    data[EFFECT_OFFSET..EFFECT_OFFSET + 48].copy_from_slice(&effect);
    data
}

fn expected_patch(original: &[u8]) -> Vec<u8> {
    let mut expected = original.to_vec();
    put_u16(&mut expected, EFFECT_OFFSET, 402);
    expected[EFFECT_OFFSET + 0x14..EFFECT_OFFSET + 0x1C].copy_from_slice(b"FLPRKEY\0");
    expected
}

#[test]
fn builder_matches_the_pinned_synthetic_hashes() {
    let original = spell_fixture();
    assert_eq!(sha(&original), pinned(SCENARIO, "spell_rewards", "ORIGINAL_SHA256"));
    assert_eq!(sha(&expected_patch(&original)), pinned(SCENARIO, "spell_rewards", "PATCHED_SHA256"));
}

/// test_static_patch_changes_only_opcode_and_callback_resource_and_reexports,
/// with ORIGINAL_SHA256/PATCHED_SHA256 patched to the synthetic spell.
#[cfg(feature = "parity-overrides")]
#[test]
fn static_patch_changes_only_opcode_and_callback_resource_and_reexports() {
    use cir_config::error::ErrorKind;
    use cir_config::spell_rewards::{original_key_spell, patch_key_spell};
    common::setup(SCENARIO);
    let original = spell_fixture();
    let expected = expected_patch(&original);
    let result = patch_key_spell(&original).unwrap();
    assert_eq!(result, expected);
    assert_eq!(patch_key_spell(&result).unwrap(), result);
    assert_eq!(original_key_spell(&result).unwrap(), original);
    assert_eq!(result[EFFECT_OFFSET + 2..EFFECT_OFFSET + 0x14], original[EFFECT_OFFSET + 2..EFFECT_OFFSET + 0x14]);
    assert_eq!(result[..EFFECT_OFFSET], original[..EFFECT_OFFSET]);
    assert_eq!(result[EFFECT_OFFSET + 0x1C..], original[EFFECT_OFFSET + 0x1C..]);
    let changed = [&result[..result.len() - 1], b"changed"].concat();
    let error = patch_key_spell(&changed).unwrap_err();
    assert_eq!(error.kind, ErrorKind::Profile);
    assert!(error.message.contains("changed"), "{}", error.message);
}
