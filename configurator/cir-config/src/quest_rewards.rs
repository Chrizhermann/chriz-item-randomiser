//! The original mod's three BG2 quest rewards, and compiled-BCS helpers (the
//! reference's `quest_rewards.py`).
//!
//! The installer splits each reward block when it is installed
//! (lib/campaign_patches.tpa); the configurator only binds the rewards. The
//! helpers below are shared by the Mode 2 and container reward adapters.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::py::{ascii_bytes, count_bytes, find_bytes, rfind_bytes, slice};
use crate::scripted_sources::SOURCE_ID as POOL_SOURCE_ID;
use std::sync::OnceLock;

fn quest(quest: &str, resource: &str, item: &str, globals: [&str; 3], source_ids: &[&str]) -> Dict {
    crate::dict! {
        "quest" => quest, "resource" => resource, "item" => item,
        "selected_global" => globals[0], "consumed_global" => globals[1],
        "event_global" => globals[2], "source_ids" => source_ids.to_vec(),
    }
}

/// `QUESTS`: quest script name to its binding.
pub fn quests() -> &'static Dict {
    static QUESTS: OnceLock<Dict> = OnceLock::new();
    QUESTS.get_or_init(|| {
        let mut quests = Dict::new();
        quests.insert(
            "blpool",
            quest("pool", "bcs:blpool", "blun12", ["FLPR_QPOOL", "FLPR_XPOOL", "FLPR_EPOOL"], &[POOL_SOURCE_ID]),
        );
        quests.insert(
            "troalt",
            quest(
                "altar",
                "bcs:troalt",
                "blun23",
                ["FLPR_QALTAR", "FLPR_XALTAR", "FLPR_EALTAR"],
                &["original-source:bg2:base:item:9"],
            ),
        );
        quests.insert(
            "ppbook",
            quest(
                "book",
                "bcs:ppbook",
                "ring09",
                ["FLPR_QBOOK", "FLPR_XBOOK", "FLPR_EBOOK"],
                &["original-source:bg2:base:item:109"],
            ),
        );
        quests
    })
}

/// `SOURCE_QUESTS`: source id to quest script name.
pub fn source_quests() -> &'static Dict {
    static SOURCE_QUESTS: OnceLock<Dict> = OnceLock::new();
    SOURCE_QUESTS.get_or_init(|| {
        let mut result = Dict::new();
        for (name, row) in quests().iter() {
            for source_id in row.get("source_ids").and_then(Value::as_list).into_iter().flatten() {
                result.insert(source_id.as_str().expect("source id"), name);
            }
        }
        result
    })
}

/// Return the authored source's exact quest binding for native export.
pub fn quest_spec(source_id: &str) -> Result<Dict> {
    match source_quests().get(source_id).and_then(Value::as_str) {
        Some(quest) => Ok(quests().get(quest).and_then(Value::as_dict).expect("quest row").clone()),
        None => Err(Error::profile(format!("Unsupported original quest source: {source_id}"))),
    }
}

/// The script's single line ending (`_newline`).
pub fn newline(data: &[u8]) -> Result<&'static [u8]> {
    let newline: &'static [u8] = if find_bytes(data, b"\r\n", 0).is_some() { b"\r\n" } else { b"\n" };
    if find_bytes(&crate::py::replace_bytes(data, newline, b"", None), b"\n", 0).is_some() {
        return Err(Error::profile("Quest BCS has mixed line endings"));
    }
    Ok(newline)
}

/// The compiled script's `expected` blocks, plus its `SC` head and tail
/// (`_blocks`).
pub fn blocks(data: &[u8], expected: usize, newline: &[u8]) -> Result<(Vec<Vec<u8>>, Vec<u8>, Vec<u8>)> {
    let marker = [b"CR", newline, b"CO", newline].concat();
    // re.finditer(re.escape(marker), data): non-overlapping occurrences.
    let mut starts = Vec::new();
    let mut from = 0;
    while let Some(found) = find_bytes(data, &marker, from) {
        starts.push(found);
        from = found + marker.len();
    }
    let tail = [b"SC", newline].concat();
    if starts.len() != expected || !data.starts_with(&tail) || !data.ends_with(&tail) {
        return Err(Error::profile("Quest BCS block structure changed"));
    }
    let end = data.len() - tail.len();
    let Some(&first) = starts.first() else {
        return Err(Error::value("list index out of range"));
    };
    let close = [b"CR", newline].concat();
    if first != tail.len() || end < close.len() || data[end - close.len()..end] != close[..] {
        return Err(Error::profile("Quest BCS block boundary changed"));
    }
    let blocks = starts
        .iter()
        .enumerate()
        .map(|(index, &start)| slice(data, start, starts.get(index + 1).copied().unwrap_or(end)).to_vec())
        .collect();
    Ok((blocks, data[..first].to_vec(), data[end..].to_vec()))
}

/// `_line`: a value and the script's line ending.
pub fn line(value: &[u8], newline: &[u8]) -> Vec<u8> {
    [value, newline].concat()
}

/// `re.fullmatch(r"FLPR_[A-Z0-9_]+", name)`.
fn is_flpr_name(name: &str) -> bool {
    name.strip_prefix("FLPR_").is_some_and(|rest| {
        !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    })
}

/// A compiled `SetGlobal(name,"GLOBAL",1)` action (`_global_action`).
pub fn global_action(name: &str, newline: &[u8]) -> Result<Vec<u8>> {
    if name.chars().count() > 32 || !is_flpr_name(name) {
        return Err(Error::profile("Quest GLOBAL name is invalid"));
    }
    let object = line(b"0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB", newline);
    let mut out = [b"AC", newline, b"30OB", newline].concat();
    out.extend_from_slice(&object);
    for _ in 0..2 {
        out.extend_from_slice(b"OB");
        out.extend_from_slice(newline);
        out.extend_from_slice(&object);
    }
    out.extend(line(&[b"1 0 0 0 0\"GLOBAL", name.as_bytes(), b"\" \"\" AC"].concat(), newline));
    Ok(out)
}

/// A compiled `Global(name,"GLOBAL",1)` (or negated) trigger (`_global_guard`).
pub fn global_guard(name: &str, selected: bool, newline: &[u8]) -> Result<Vec<u8>> {
    let negate: &[u8] = if selected { b"0" } else { b"1" };
    let name = ascii_bytes(name)?;
    let mut out = [b"TR", newline].concat();
    out.extend(line(&[b"16399 1 ", negate, b" 0 0 \"GLOBAL", &name, b"\" \"\" OB"].concat(), newline));
    out.extend(line(b"0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB", newline));
    out.extend_from_slice(b"TR");
    out.extend_from_slice(newline);
    Ok(out)
}

/// The one compiled action granting `item` with `opcode` (`_grant_action`).
pub fn grant_action(block: &[u8], item: &str, opcode: u32, newline: &[u8]) -> Result<Vec<u8>> {
    let parameter = line(&[b"0 0 0 0 0\"", ascii_bytes(item)?.as_slice(), b"\" \"\" AC"].concat(), newline);
    if count_bytes(block, &parameter) != 1 {
        return Err(Error::profile("Quest reward grant changed"));
    }
    let finish = find_bytes(block, &parameter, 0).expect("counted parameter") + parameter.len();
    let Some(start) = rfind_bytes(block, &[newline, b"AC", newline].concat(), 0, finish) else {
        return Err(Error::profile("Quest reward grant action changed"));
    };
    let action = &block[start + newline.len()..finish];
    let head = [line(b"AC", newline), line(format!("{opcode}OB").as_bytes(), newline)].concat();
    if !action.starts_with(&head) {
        return Err(Error::profile("Quest reward grant opcode changed"));
    }
    Ok(action.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quest_bindings_match_the_reference() {
        let spec = quest_spec("original-source:bg2:base:item:9").unwrap();
        assert_eq!(spec.get("quest").and_then(Value::as_str), Some("altar"));
        assert_eq!(quest_spec(POOL_SOURCE_ID).unwrap().get("item").and_then(Value::as_str), Some("blun12"));
        assert_eq!(
            quest_spec("x").unwrap_err().message,
            "Unsupported original quest source: x"
        );
    }

    #[test]
    fn blocks_split_compiled_scripts() {
        let data = b"SC\nCR\nCO\nA\nCR\nCR\nCO\nB\nCR\nSC\n";
        let (blocks, prefix, suffix) = blocks(data, 2, b"\n").unwrap();
        assert_eq!(blocks, vec![b"CR\nCO\nA\nCR\n".to_vec(), b"CR\nCO\nB\nCR\n".to_vec()]);
        assert_eq!((prefix.as_slice(), suffix.as_slice()), (&b"SC\n"[..], &b"SC\n"[..]));
        assert_eq!(super::blocks(data, 3, b"\n").unwrap_err().message, "Quest BCS block structure changed");
        assert_eq!(newline(b"a\r\nb\n").unwrap_err().message, "Quest BCS has mixed line endings");
        assert_eq!(global_action("flpr_x", b"\n").unwrap_err().message, "Quest GLOBAL name is invalid");
    }
}
