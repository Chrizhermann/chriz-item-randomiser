//! Read the script text of dialogues and compiled scripts, without changing
//! it (the reference's `script_text.py`).
//!
//! Dialogue triggers and actions are text that the engine compiles when it
//! runs them: a sequence of calls such as
//! `GiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)`, whatever the spacing, line
//! ends or letter case. Compiled BCS scripts are blocks of conditions and
//! responses. The reward adapters use these readers to find what they change
//! by its meaning, not by its position or exact bytes.
//!
//! Numbers are read as the reference's integers saturated to `i64`: every
//! caller compares them with strrefs, opcodes or small counts, which a value
//! beyond that range never equals.

use crate::error::{Error, Result, SCRIPT_ERROR};
use crate::py::{count_bytes, split_bytes};

/// `_SPACE`: the spaces around calls and their arguments.
fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\r' | b'\n')
}

/// `bytes.isspace()` of one byte (it includes the vertical tab, which Rust's
/// ASCII whitespace does not).
fn is_py_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

/// A call in dialogue text: its byte span, its name as written (with a
/// leading "!" for a negated trigger) and the spans of its arguments, without
/// the spaces around them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Call {
    pub start: usize,
    pub end: usize,
    pub name: Vec<u8>,
    pub args: Vec<(usize, usize)>,
}

/// `_NAME.match(text, at, end)` (`!?[A-Za-z_][A-Za-z0-9_#]*`): its end.
fn name_end(text: &[u8], at: usize, end: usize) -> Option<usize> {
    let mut cursor = at;
    if cursor < end && text[cursor] == b'!' {
        cursor += 1;
    }
    if cursor >= end || !(text[cursor].is_ascii_alphabetic() || text[cursor] == b'_') {
        return None;
    }
    cursor += 1;
    while cursor < end && (text[cursor].is_ascii_alphanumeric() || matches!(text[cursor], b'_' | b'#')) {
        cursor += 1;
    }
    Some(cursor)
}

/// The top-level calls of the text, or None if it is not a call sequence.
pub fn calls(text: &[u8]) -> Option<Vec<Call>> {
    calls_in(text, (0, text.len()))
}

/// The top-level calls of `text[start..end]`, at their places in `text`, or
/// None if that span is not a call sequence (`calls(text, start, end)`).
pub fn calls_in(text: &[u8], (start, end): (usize, usize)) -> Option<Vec<Call>> {
    let mut result = Vec::new();
    let mut at = start;
    loop {
        while at < end && is_space(text[at]) {
            at += 1;
        }
        if at == end {
            return Some(result);
        }
        let name = name_end(text, at, end)?;
        let mut cursor = name;
        while cursor < end && matches!(text[cursor], b' ' | b'\t') {
            cursor += 1;
        }
        if cursor == end || text[cursor] != b'(' {
            return None;
        }
        let (mut bounds, mut depth, mut quoted, mut begin, mut close) = (Vec::new(), 0usize, false, cursor + 1, None);
        for (position, &byte) in text.iter().enumerate().take(end).skip(cursor + 1) {
            if quoted {
                quoted = byte != b'"';
            } else if byte == b'"' {
                quoted = true;
            } else if matches!(byte, b'(' | b'[') {
                depth += 1;
            } else if matches!(byte, b')' | b']') && depth > 0 {
                depth -= 1;
            } else if byte == b')' {
                bounds.push((begin, position));
                close = Some(position);
                break;
            } else if byte == b',' && depth == 0 {
                bounds.push((begin, position));
                begin = position + 1;
            }
        }
        let close = close?;
        let mut spans = Vec::with_capacity(bounds.len());
        for (mut first, mut last) in bounds {
            while first < last && is_space(text[first]) {
                first += 1;
            }
            while last > first && is_space(text[last - 1]) {
                last -= 1;
            }
            spans.push((first, last));
        }
        if spans.len() == 1 && spans[0].0 == spans[0].1 {
            spans.clear(); // No arguments, as in DestroySelf().
        }
        result.push(Call { start: at, end: close + 1, name: text[at..name].to_vec(), args: spans });
        at = close + 1;
    }
}

/// One argument of a call as written, or None when the call has fewer.
pub fn argument<'a>(text: &'a [u8], call: &Call, index: usize) -> Option<&'a [u8]> {
    call.args.get(index).map(|&(first, last)| &text[first..last])
}

/// A quoted string argument without its quotes, or None.
pub fn string_argument<'a>(text: &'a [u8], call: &Call, index: usize) -> Option<&'a [u8]> {
    let value = argument(text, call, index)?;
    (value.len() >= 2 && value[0] == b'"' && value[value.len() - 1] == b'"').then(|| &value[1..value.len() - 1])
}

/// `int(value)` of a `-?[0-9]+` match, saturated to `i64`; None otherwise.
pub fn integer(value: &[u8]) -> Option<i64> {
    let (negative, digits) = match value.split_first() {
        Some((b'-', rest)) => (true, rest),
        _ => (false, value),
    };
    if digits.is_empty() || !digits.iter().all(u8::is_ascii_digit) {
        return None;
    }
    let mut number: i64 = 0;
    for &digit in digits {
        let digit = i64::from(digit - b'0');
        number = if negative {
            number.saturating_mul(10).saturating_sub(digit)
        } else {
            number.saturating_mul(10).saturating_add(digit)
        };
    }
    Some(number)
}

/// An integer argument; a missing one is `default`, a malformed one None.
pub fn integer_argument(text: &[u8], call: &Call, index: usize, default: Option<i64>) -> Option<i64> {
    match argument(text, call, index) {
        None => default,
        Some(value) => integer(value),
    }
}

/// Whether the call has one of these names, whatever their letter case.
pub fn named(call: &Call, names: &[&str]) -> bool {
    names.iter().any(|name| call.name.eq_ignore_ascii_case(name.as_bytes()))
}

/// Append one trigger or action line to dialogue text.
///
/// Beamdog's own compiled text often ends without a line break; never run the
/// new line into the last existing call.
pub fn joined(prior: &[u8], line: &[u8]) -> Vec<u8> {
    let mut out = prior.to_vec();
    if prior.last().is_some_and(|&last| !is_py_space(last)) {
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(line);
    out
}

/// A compiled script these readers cannot read (`ScriptError`); see
/// [`naming`].
fn script_error(message: &str) -> Error {
    Error::profile_subclass(SCRIPT_ERROR, message)
}

/// Name the resource in an error about reading its compiled script.
pub fn naming<T>(label: &str, body: impl FnOnce() -> Result<T>) -> Result<T> {
    body().map_err(|error| {
        if error.is(SCRIPT_ERROR) {
            Error::profile(format!("{label} cannot be read as a compiled script: {}", error.message))
        } else {
            error
        }
    })
}

// Compiled BCS. A block is CR, CO, its triggers (TR), CO, RS, its responses
// (RE, a weight, actions AC), RS, CR. Each action has an opcode, three objects
// (OB...OB) and a parameter line; each trigger has a parameter line and one
// object. Strings never span lines.

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Script {
    pub newline: &'static [u8],
    pub prefix: Vec<u8>,
    pub blocks: Vec<Vec<u8>>,
    pub suffix: Vec<u8>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Action {
    pub start: usize,
    pub end: usize,
    pub opcode: i64,
    /// The opcode as the reference prints it (its decimal value).
    pub opcode_shown: String,
    pub objects: [Vec<u8>; 3],
    pub integer: i64,
    pub strings: [Vec<u8>; 2],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Trigger {
    pub start: usize,
    pub end: usize,
    pub opcode: i64,
    /// The opcode as the reference prints it (its decimal value).
    pub opcode_shown: String,
    pub value: i64,
    pub negated: bool,
    pub strings: [Vec<u8>; 2],
}

/// The script's single line ending.
pub fn newline_of(data: &[u8]) -> Result<&'static [u8]> {
    let newline: &'static [u8] = if data.windows(2).any(|pair| pair == b"\r\n") { b"\r\n" } else { b"\n" };
    if newline == b"\r\n" && data.iter().enumerate().any(|(at, &byte)| byte == b'\n' && (at == 0 || data[at - 1] != b'\r'))
    {
        return Err(script_error("its line endings are mixed"));
    }
    Ok(newline)
}

/// Non-overlapping occurrences, as `re.finditer(re.escape(marker), data)`.
fn occurrences(data: &[u8], marker: &[u8]) -> Vec<usize> {
    let mut starts = Vec::new();
    let mut at = 0;
    while at + marker.len() <= data.len() {
        if data[at..].starts_with(marker) {
            starts.push(at);
            at += marker.len();
        } else {
            at += 1;
        }
    }
    starts
}

/// Split a compiled BCS into its blocks; each block keeps its CR lines.
pub fn script_blocks(data: &[u8]) -> Result<Script> {
    let newline = newline_of(data)?;
    let tail = [b"SC", newline].concat();
    let marker = [b"CR", newline, b"CO", newline].concat();
    if !data.starts_with(&tail) || !data.ends_with(&tail) || data.len() < 2 * tail.len() {
        return Err(script_error("it does not start and end with SC"));
    }
    let starts = occurrences(data, &marker);
    let end = data.len() - tail.len();
    if starts.first().is_some_and(|&first| first != tail.len()) || (starts.is_empty() && end != tail.len()) {
        return Err(script_error("its first block does not follow SC"));
    }
    let close = [b"CR", newline].concat();
    if !starts.is_empty() && data[end - close.len()..end] != close[..] {
        return Err(script_error("its last block is not closed"));
    }
    let blocks = starts
        .iter()
        .enumerate()
        .map(|(index, &start)| data[start..starts.get(index + 1).copied().unwrap_or(end)].to_vec())
        .collect();
    Ok(Script { newline, prefix: data[..tail.len()].to_vec(), blocks, suffix: data[end..].to_vec() })
}

/// A block's head (CR CO), conditions and responses (from RS).
pub fn block_parts(block: &[u8], newline: &[u8]) -> Result<(Vec<u8>, Vec<u8>, Vec<u8>)> {
    let parts = split_bytes(block, &[b"CO", newline].concat());
    if parts.len() != 3 {
        return Err(script_error("a block has an unknown structure"));
    }
    Ok((parts[0].to_vec(), parts[1].to_vec(), parts[2].to_vec()))
}

/// A matcher over compiled text that mirrors the reference's regular
/// expressions, which have one way to match at each position.
struct Cursor<'a> {
    text: &'a [u8],
    at: usize,
}

impl Cursor<'_> {
    fn literal(&mut self, value: &[u8]) -> Option<()> {
        self.text[self.at..].starts_with(value).then(|| self.at += value.len())
    }

    /// `[0-9]+` (with `-?` when signed): its span.
    fn number(&mut self, signed: bool) -> Option<(usize, usize)> {
        let start = self.at;
        let mut at = start;
        if signed && self.text.get(at) == Some(&b'-') {
            at += 1;
        }
        let digits = at;
        while self.text.get(at).is_some_and(u8::is_ascii_digit) {
            at += 1;
        }
        (at > digits).then(|| {
            self.at = at;
            (start, at)
        })
    }

    /// `"([^"\r\n]*)"`: the string between the quotes.
    fn quoted(&mut self) -> Option<Vec<u8>> {
        self.literal(b"\"")?;
        let start = self.at;
        let close = start + self.text[start..].iter().position(|&byte| matches!(byte, b'"' | b'\r' | b'\n'))?;
        (self.text[close] == b'"').then(|| {
            self.at = close + 1;
            self.text[start..close].to_vec()
        })
    }

    /// `([^\r\n]*)OB<newline>`: the rest of the line before its closing OB.
    fn object(&mut self, newline: &[u8]) -> Option<Vec<u8>> {
        let start = self.at;
        let line_end =
            start + self.text[start..].iter().position(|&byte| matches!(byte, b'\r' | b'\n')).unwrap_or(self.text.len() - start);
        if line_end < start + 2 || &self.text[line_end - 2..line_end] != b"OB" || !self.text[line_end..].starts_with(newline) {
            return None;
        }
        self.at = line_end + newline.len();
        Some(self.text[start..line_end - 2].to_vec())
    }
}

/// The decimal value of a run of digits, as `str(int(digits))` prints it.
fn shown_number(digits: &[u8]) -> String {
    let trimmed = digits.iter().position(|&digit| digit != b'0').map_or(&b"0"[..], |first| &digits[first..]);
    String::from_utf8_lossy(trimmed).into_owned()
}

fn action_at(responses: &[u8], start: usize, newline: &[u8]) -> Option<Action> {
    let mut cursor = Cursor { text: responses, at: start };
    cursor.literal(b"AC")?;
    cursor.literal(newline)?;
    let (first, last) = cursor.number(false)?;
    let mut objects: [Vec<u8>; 3] = Default::default();
    for object in &mut objects {
        cursor.literal(b"OB")?;
        cursor.literal(newline)?;
        *object = cursor.object(newline)?;
    }
    let (value_start, value_end) = cursor.number(true)?;
    for _ in 0..4 {
        cursor.literal(b" ")?;
        cursor.number(true)?;
    }
    let first_string = cursor.quoted()?;
    cursor.literal(b" ")?;
    let second_string = cursor.quoted()?;
    cursor.literal(b" AC")?;
    cursor.literal(newline)?;
    Some(Action {
        start,
        end: cursor.at,
        opcode: integer(&responses[first..last]).unwrap_or(i64::MAX),
        opcode_shown: shown_number(&responses[first..last]),
        objects,
        integer: integer(&responses[value_start..value_end]).unwrap_or(0),
        strings: [first_string, second_string],
    })
}

/// `len(re.findall(rb"(?m)^(?:[0-9]+)?AC" + nl, responses))`: the action
/// lines, each with the response weight the compiler may write before it.
fn action_lines(responses: &[u8], newline: &[u8]) -> usize {
    let head = [b"AC", newline].concat();
    let (mut count, mut at) = (0, 0);
    while at < responses.len() {
        if at == 0 || responses[at - 1] == b'\n' {
            let mut cursor = at;
            while responses.get(cursor).is_some_and(u8::is_ascii_digit) {
                cursor += 1;
            }
            if responses[cursor..].starts_with(&head) {
                count += 1;
                at = cursor + head.len();
                continue;
            }
        }
        at += 1;
    }
    count
}

/// Every action of a block's responses, in order.
///
/// The span starts at the action's AC, after a response weight that the
/// compiler writes on the same line.
pub fn block_actions(responses: &[u8], newline: &[u8]) -> Result<Vec<Action>> {
    let mut result = Vec::new();
    let mut at = 0;
    while at < responses.len() {
        match action_at(responses, at, newline) {
            Some(action) => {
                at = action.end;
                result.push(action);
            }
            None => at += 1,
        }
    }
    // Each AC line opens an action; an unknown one hides what it does.
    if action_lines(responses, newline) != result.len() {
        return Err(script_error("a block has an action of unknown structure"));
    }
    Ok(result)
}

/// The span of each alternative response (`RE` ... `RE`) of a block's
/// responses. The engine runs one of them, chosen by weight.
pub fn response_spans(responses: &[u8], newline: &[u8]) -> Result<Vec<(usize, usize)>> {
    let mark = [b"RE" as &[u8], newline].concat();
    let mut marks = Vec::new();
    let mut at = 0;
    // `(?m)^RE<newline>`: at the start or after a line feed, never overlapping.
    while at + mark.len() <= responses.len() {
        if (at == 0 || responses[at - 1] == b'\n') && responses[at..].starts_with(&mark) {
            marks.push(at);
            at += mark.len();
        } else {
            at += 1;
        }
    }
    if marks.len() % 2 == 1 {
        return Err(script_error("a block has a response that is not closed"));
    }
    Ok(marks.chunks(2).map(|pair| (pair[0], pair[1] + mark.len())).collect())
}

fn trigger_at(conditions: &[u8], start: usize, newline: &[u8]) -> Option<Trigger> {
    let mut cursor = Cursor { text: conditions, at: start };
    cursor.literal(b"TR")?;
    cursor.literal(newline)?;
    let (opcode_start, opcode_end) = cursor.number(false)?;
    cursor.literal(b" ")?;
    let (value_start, value_end) = cursor.number(true)?;
    cursor.literal(b" ")?;
    let (_, flags_end) = cursor.number(false)?;
    for _ in 0..2 {
        cursor.literal(b" ")?;
        cursor.number(true)?;
    }
    cursor.literal(b" ")?;
    let first_string = cursor.quoted()?;
    cursor.literal(b" ")?;
    let second_string = cursor.quoted()?;
    cursor.literal(b" OB")?;
    cursor.literal(newline)?;
    cursor.object(newline)?;
    cursor.literal(b"TR")?;
    cursor.literal(newline)?;
    Some(Trigger {
        start,
        end: cursor.at,
        opcode: integer(&conditions[opcode_start..opcode_end]).unwrap_or(i64::MAX),
        opcode_shown: shown_number(&conditions[opcode_start..opcode_end]),
        value: integer(&conditions[value_start..value_end]).unwrap_or(0),
        // int(flags) & 1: the parity of its last digit.
        negated: (conditions[flags_end - 1] - b'0') & 1 == 1,
        strings: [first_string, second_string],
    })
}

/// Every trigger of a block's conditions, in order.
pub fn block_triggers(conditions: &[u8], newline: &[u8]) -> Result<Vec<Trigger>> {
    let mut result = Vec::new();
    let mut at = 0;
    while at < conditions.len() {
        match trigger_at(conditions, at, newline) {
            Some(trigger) => {
                at = trigger.end;
                result.push(trigger);
            }
            None => at += 1,
        }
    }
    if count_bytes(conditions, &[b"TR", newline].concat()) != 2 * result.len() {
        return Err(script_error("a block has a trigger of unknown structure"));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calls_read_dialogue_text_whatever_its_spacing() {
        let text = b" GiveItemCreate( \"Shld26\" , LastTalkedToBy , 0 , 0 , 0 ) \nDestroySelf()\r\n!Global(\"A\",\"B\",1)";
        let found = calls(text).unwrap();
        assert_eq!(found.len(), 3);
        assert_eq!(found[0].name, b"GiveItemCreate");
        assert_eq!(string_argument(text, &found[0], 0), Some(&b"Shld26"[..]));
        assert_eq!(argument(text, &found[0], 1), Some(&b"LastTalkedToBy"[..]));
        assert_eq!(integer_argument(text, &found[0], 4, None), Some(0));
        assert_eq!(integer_argument(text, &found[0], 9, Some(7)), Some(7));
        assert!(found[1].args.is_empty() && named(&found[1], &["destroyself"]));
        assert!(named(&found[2], &["!GLOBAL"]));
        assert_eq!(calls(b"Wait(1"), None);
        assert_eq!(calls(b"1Wait(1)"), None);
        assert_eq!(calls(b"A(\"x)\",[1,(2)])").unwrap()[0].args.len(), 2);
        assert_eq!(integer(b"99999999999999999999999"), Some(i64::MAX));
        assert_eq!(integer(b"-0012"), Some(-12));
        assert_eq!(integer(b"1-2"), None);
    }

    #[test]
    fn joined_never_runs_into_the_last_call() {
        assert_eq!(joined(b"A()", b"B()"), b"A()\r\nB()".to_vec());
        assert_eq!(joined(b"A()\n", b"B()"), b"A()\nB()".to_vec());
        assert_eq!(joined(b"A()\x0b", b"B()"), b"A()\x0bB()".to_vec());
        assert_eq!(joined(b"", b"B()"), b"B()".to_vec());
    }

    #[test]
    fn compiled_scripts_split_into_blocks_actions_and_triggers() {
        let object = "0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB\n";
        let action = format!("AC\n140OB\n{object}OB\n{object}OB\n{object}0 0 0 0 0\"helm21\" \"\" AC\n");
        let trigger = "TR\n16399 1 1 0 0 \"GLOBALX\" \"\" OB\n0 0 0 0 0 0 0 0 0 0 0 0 \"\"OB\nTR\n";
        let block = format!("CR\nCO\n{trigger}CO\nRS\nRE\n100{action}RE\nRS\nCR\n");
        let data = format!("SC\n{block}SC\n").into_bytes();
        let script = script_blocks(&data).unwrap();
        assert_eq!(script.blocks, vec![block.clone().into_bytes()]);
        let (_, conditions, responses) = block_parts(&script.blocks[0], b"\n").unwrap();
        let actions = block_actions(&responses, b"\n").unwrap();
        assert_eq!((actions[0].opcode, actions[0].integer, actions[0].strings[0].as_slice()), (140, 0, &b"helm21"[..]));
        let triggers = block_triggers(&conditions, b"\n").unwrap();
        assert!(triggers[0].negated && triggers[0].value == 1 && triggers[0].opcode == 16399);
        let error = naming("DDWELL.BCS", || script_blocks(b"SC\nCR\nCO\nSC\n")).unwrap_err();
        assert_eq!(error.message, "DDWELL.BCS cannot be read as a compiled script: its last block is not closed");
        assert!(!error.is(SCRIPT_ERROR));
        assert_eq!(newline_of(b"SC\r\nA\n").unwrap_err().message, "its line endings are mixed");
        let unknown = crate::py::replace_bytes(&responses, b"\"helm21\" \"\" AC", b"\"helm21\" \"\"AC", Some(1));
        assert_eq!(block_actions(&unknown, b"\n").unwrap_err().message, "a block has an action of unknown structure");
    }
}
