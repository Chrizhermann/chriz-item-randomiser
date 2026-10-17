//! JSON with the reference's Python semantics.
//!
//! Values keep Python's distinctions (bool is not int, int is arbitrary
//! precision, float is separate) and dictionaries keep insertion order.
//! `parse_strict` is the reference's `read_json` (duplicate fields, NaN and
//! infinite numbers rejected); `parse_lenient` is plain `json.loads`.
//! The writers reproduce `json.dumps` byte for byte, including Python's float
//! `repr` and its escaping rules, so files and digests match the reference.

use crate::error::{Error, Result};
use std::cmp::Ordering;
use std::collections::HashMap;
use std::fmt::Write as _;

#[derive(Clone, Debug)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    /// A Python int outside the i64 range, as normalized decimal text.
    BigInt(Box<str>),
    Float(f64),
    Str(String),
    List(Vec<Value>),
    Dict(Dict),
}

/// An insertion-ordered dictionary with string keys (Python's dict).
///
/// Large dictionaries (catalog and world indexes) keep a hash index so that
/// lookups stay constant time; small ones are scanned linearly.
#[derive(Clone, Debug, Default)]
pub struct Dict {
    entries: Vec<(String, Value)>,
    index: Option<HashMap<String, usize>>,
}

const INDEX_THRESHOLD: usize = 24;

impl Dict {
    pub fn new() -> Self {
        Dict { entries: Vec::new(), index: None }
    }
    pub fn with_capacity(capacity: usize) -> Self {
        Dict { entries: Vec::with_capacity(capacity), index: None }
    }
    fn position(&self, key: &str) -> Option<usize> {
        match &self.index {
            Some(index) => index.get(key).copied(),
            None => self.entries.iter().position(|(name, _)| name == key),
        }
    }
    fn reindex(&mut self) {
        if self.entries.len() > INDEX_THRESHOLD {
            self.index = Some(self.entries.iter().enumerate().map(|(position, (key, _))| (key.clone(), position)).collect());
        } else {
            self.index = None;
        }
    }
    fn append(&mut self, key: String, value: Value) -> usize {
        let position = self.entries.len();
        match &mut self.index {
            Some(index) => {
                index.insert(key.clone(), position);
                self.entries.push((key, value));
            }
            None => {
                self.entries.push((key, value));
                if self.entries.len() > INDEX_THRESHOLD {
                    self.reindex();
                }
            }
        }
        position
    }
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.position(key).map(|position| &self.entries[position].1)
    }
    pub fn get_mut(&mut self, key: &str) -> Option<&mut Value> {
        self.position(key).map(|position| &mut self.entries[position].1)
    }
    pub fn contains_key(&self, key: &str) -> bool {
        self.position(key).is_some()
    }
    /// `d[key] = value`: replace in place or append.
    pub fn insert(&mut self, key: impl Into<String>, value: impl Into<Value>) {
        let key = key.into();
        let value = value.into();
        match self.position(&key) {
            Some(position) => self.entries[position].1 = value,
            None => {
                self.append(key, value);
            }
        }
    }
    /// Append a key the caller knows is new.
    pub fn push_unique(&mut self, key: String, value: Value) {
        debug_assert!(!self.contains_key(&key));
        self.append(key, value);
    }
    /// `d.pop(key, None)`.
    pub fn remove(&mut self, key: &str) -> Option<Value> {
        let position = self.position(key)?;
        let (_, value) = self.entries.remove(position);
        if self.index.is_some() {
            self.reindex();
        }
        Some(value)
    }
    /// `d.setdefault(key, default)`.
    pub fn setdefault(&mut self, key: &str, default: Value) -> &mut Value {
        let position = match self.position(key) {
            Some(position) => position,
            None => self.append(key.to_string(), default),
        };
        &mut self.entries[position].1
    }
    /// Keep the entries for which `keep` is true (dict comprehension filter).
    pub fn retain(&mut self, mut keep: impl FnMut(&str, &Value) -> bool) {
        self.entries.retain(|(key, value)| keep(key, value));
        self.reindex();
    }
    /// Sort entries by key (Python `dict(sorted(d.items()))`).
    pub fn sort_keys(&mut self) {
        self.entries.sort_by(|a, b| str_cmp(&a.0, &b.0));
        self.reindex();
    }
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn keys(&self) -> impl Iterator<Item = &str> + '_ {
        self.entries.iter().map(|(key, _)| key.as_str())
    }
    pub fn values(&self) -> impl Iterator<Item = &Value> + '_ {
        self.entries.iter().map(|(_, value)| value)
    }
    pub fn values_mut(&mut self) -> impl Iterator<Item = &mut Value> + '_ {
        self.entries.iter_mut().map(|(_, value)| value)
    }
    pub fn iter(&self) -> impl Iterator<Item = (&str, &Value)> + '_ {
        self.entries.iter().map(|(key, value)| (key.as_str(), value))
    }
    pub fn iter_mut(&mut self) -> impl Iterator<Item = (&str, &mut Value)> + '_ {
        self.entries.iter_mut().map(|(key, value)| (key.as_str(), value))
    }
    pub fn into_entries(self) -> Vec<(String, Value)> {
        self.entries
    }
    pub fn key_set(&self) -> std::collections::BTreeSet<&str> {
        self.keys().collect()
    }
    /// Whether the key set equals `expected` (Python `set(d) == {...}`).
    pub fn has_exact_keys(&self, expected: &[&str]) -> bool {
        self.len() == expected.len() && expected.iter().all(|key| self.contains_key(key))
            && {
                let mut unique: Vec<&&str> = expected.iter().collect();
                unique.sort();
                unique.dedup();
                unique.len() == expected.len()
            }
    }
    /// `{**self, **other}` / `self | other`.
    pub fn merged(&self, other: &Dict) -> Dict {
        let mut result = self.clone();
        for (key, value) in other.iter() {
            result.insert(key, value.clone());
        }
        result
    }
}

impl FromIterator<(String, Value)> for Dict {
    fn from_iter<I: IntoIterator<Item = (String, Value)>>(iter: I) -> Self {
        let mut dict = Dict::new();
        for (key, value) in iter {
            dict.insert(key, value);
        }
        dict
    }
}

#[macro_export]
macro_rules! dict {
    () => { $crate::json::Dict::new() };
    ($($key:expr => $value:expr),+ $(,)?) => {{
        let mut dict = $crate::json::Dict::new();
        $( dict.insert($key, $crate::json::Value::from($value)); )+
        dict
    }};
}

#[macro_export]
macro_rules! list {
    () => { $crate::json::Value::List(Vec::new()) };
    ($($value:expr),+ $(,)?) => { $crate::json::Value::List(vec![$($crate::json::Value::from($value)),+]) };
}

impl From<&str> for Value {
    fn from(value: &str) -> Self {
        Value::Str(value.to_string())
    }
}
impl From<String> for Value {
    fn from(value: String) -> Self {
        Value::Str(value)
    }
}
impl From<&String> for Value {
    fn from(value: &String) -> Self {
        Value::Str(value.clone())
    }
}
impl From<bool> for Value {
    fn from(value: bool) -> Self {
        Value::Bool(value)
    }
}
impl From<i64> for Value {
    fn from(value: i64) -> Self {
        Value::Int(value)
    }
}
impl From<i32> for Value {
    fn from(value: i32) -> Self {
        Value::Int(value as i64)
    }
}
impl From<u32> for Value {
    fn from(value: u32) -> Self {
        Value::Int(value as i64)
    }
}
impl From<usize> for Value {
    fn from(value: usize) -> Self {
        Value::Int(value as i64)
    }
}
impl From<i128> for Value {
    fn from(value: i128) -> Self {
        match i64::try_from(value) {
            Ok(small) => Value::Int(small),
            Err(_) => Value::BigInt(value.to_string().into_boxed_str()),
        }
    }
}
impl From<Dict> for Value {
    fn from(value: Dict) -> Self {
        Value::Dict(value)
    }
}
impl From<Vec<Value>> for Value {
    fn from(value: Vec<Value>) -> Self {
        Value::List(value)
    }
}
impl From<Vec<String>> for Value {
    fn from(value: Vec<String>) -> Self {
        Value::List(value.into_iter().map(Value::Str).collect())
    }
}
impl From<Vec<&str>> for Value {
    fn from(value: Vec<&str>) -> Self {
        Value::List(value.into_iter().map(Value::from).collect())
    }
}
impl From<Vec<i64>> for Value {
    fn from(value: Vec<i64>) -> Self {
        Value::List(value.into_iter().map(Value::Int).collect())
    }
}
impl From<Option<String>> for Value {
    fn from(value: Option<String>) -> Self {
        value.map_or(Value::Null, Value::Str)
    }
}
impl From<Option<&str>> for Value {
    fn from(value: Option<&str>) -> Self {
        value.map_or(Value::Null, Value::from)
    }
}
impl From<Option<i64>> for Value {
    fn from(value: Option<i64>) -> Self {
        value.map_or(Value::Null, Value::Int)
    }
}
impl From<&Value> for Value {
    fn from(value: &Value) -> Self {
        value.clone()
    }
}

impl Value {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(text) => Some(text),
            _ => None,
        }
    }
    pub fn as_dict(&self) -> Option<&Dict> {
        match self {
            Value::Dict(dict) => Some(dict),
            _ => None,
        }
    }
    pub fn as_dict_mut(&mut self) -> Option<&mut Dict> {
        match self {
            Value::Dict(dict) => Some(dict),
            _ => None,
        }
    }
    pub fn as_list(&self) -> Option<&Vec<Value>> {
        match self {
            Value::List(list) => Some(list),
            _ => None,
        }
    }
    pub fn as_list_mut(&mut self) -> Option<&mut Vec<Value>> {
        match self {
            Value::List(list) => Some(list),
            _ => None,
        }
    }
    /// `type(v) is int` and representable as i64.
    pub fn as_int(&self) -> Option<i64> {
        match self {
            Value::Int(number) => Some(*number),
            _ => None,
        }
    }
    /// `type(v) is int` (any size; bool excluded).
    pub fn is_int(&self) -> bool {
        matches!(self, Value::Int(_) | Value::BigInt(_))
    }
    pub fn is_str(&self) -> bool {
        matches!(self, Value::Str(_))
    }
    pub fn is_dict(&self) -> bool {
        matches!(self, Value::Dict(_))
    }
    pub fn is_list(&self) -> bool {
        matches!(self, Value::List(_))
    }
    pub fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Value::Bool(flag) => Some(*flag),
            _ => None,
        }
    }
    /// Dictionary lookup; None when the key is absent or this is not a dict.
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.as_dict().and_then(|dict| dict.get(key))
    }
    pub fn get_mut(&mut self, key: &str) -> Option<&mut Value> {
        self.as_dict_mut().and_then(|dict| dict.get_mut(key))
    }
    /// `value[key]` for a dict known to have the key (KeyError otherwise).
    pub fn at(&self, key: &str) -> Result<&Value> {
        match self {
            Value::Dict(dict) => dict.get(key).ok_or_else(|| Error::key(key)),
            _ => Err(Error::type_error(format!(
                "{} indices must be integers or slices, not str",
                self.type_name()
            ))),
        }
    }
    pub fn at_mut(&mut self, key: &str) -> Result<&mut Value> {
        let name = self.type_name();
        match self {
            Value::Dict(dict) => dict.get_mut(key).ok_or_else(|| Error::key(key)),
            _ => Err(Error::type_error(format!("{name} indices must be integers or slices, not str"))),
        }
    }
    /// Python's string of a value's type, for error messages.
    pub fn type_name(&self) -> &'static str {
        match self {
            Value::Null => "NoneType",
            Value::Bool(_) => "bool",
            Value::Int(_) | Value::BigInt(_) => "int",
            Value::Float(_) => "float",
            Value::Str(_) => "str",
            Value::List(_) => "list",
            Value::Dict(_) => "dict",
        }
    }
    /// Python truthiness.
    pub fn truthy(&self) -> bool {
        match self {
            Value::Null => false,
            Value::Bool(flag) => *flag,
            Value::Int(number) => *number != 0,
            Value::BigInt(_) => true,
            Value::Float(number) => *number != 0.0,
            Value::Str(text) => !text.is_empty(),
            Value::List(list) => !list.is_empty(),
            Value::Dict(dict) => !dict.is_empty(),
        }
    }
    /// Python `==` (True == 1 == 1.0; dicts compare without order).
    pub fn py_eq(&self, other: &Value) -> bool {
        match (self, other) {
            (Value::Null, Value::Null) => true,
            (Value::Str(a), Value::Str(b)) => a == b,
            (Value::List(a), Value::List(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.py_eq(y)),
            (Value::Dict(a), Value::Dict(b)) => {
                a.len() == b.len() && a.iter().all(|(key, value)| b.get(key).is_some_and(|other| value.py_eq(other)))
            }
            (a, b) => match (a.numeric(), b.numeric()) {
                (Some(x), Some(y)) => x == y,
                _ => false,
            },
        }
    }
    fn numeric(&self) -> Option<Numeric> {
        match self {
            Value::Bool(flag) => Some(Numeric::Int(*flag as i128)),
            Value::Int(number) => Some(Numeric::Int(*number as i128)),
            Value::BigInt(text) => Some(Numeric::Big(text.to_string())),
            Value::Float(number) => Some(Numeric::Float(*number)),
            _ => None,
        }
    }
}

#[derive(Debug)]
enum Numeric {
    Int(i128),
    Big(String),
    Float(f64),
}

impl PartialEq for Numeric {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Numeric::Int(a), Numeric::Int(b)) => a == b,
            (Numeric::Big(a), Numeric::Big(b)) => a == b,
            (Numeric::Int(_), Numeric::Big(_)) | (Numeric::Big(_), Numeric::Int(_)) => false,
            (Numeric::Float(a), Numeric::Float(b)) => a == b,
            (Numeric::Float(f), Numeric::Int(i)) | (Numeric::Int(i), Numeric::Float(f)) => {
                f.is_finite() && f.fract() == 0.0 && f.abs() < 1.7e38 && (*f as i128) == *i
            }
            (Numeric::Float(f), Numeric::Big(text)) | (Numeric::Big(text), Numeric::Float(f)) => {
                f.is_finite() && f.fract() == 0.0 && format!("{:.0}", f) == *text
            }
        }
    }
}

/// Python's ordering of strings (code points) — equal to UTF-8 byte order.
pub fn str_cmp(a: &str, b: &str) -> Ordering {
    a.as_bytes().cmp(b.as_bytes())
}

// ---------------------------------------------------------------- parsing

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Strict,
    Lenient,
}

struct Parser<'a> {
    chars: Vec<char>,
    text: &'a str,
    mode: Mode,
    lone_surrogate: bool,
    depth: usize,
}

const MAX_DEPTH: usize = 980;

/// The JSONDecodeError message Python builds for `pos` (a character index).
fn decode_error(chars: &[char], message: &str, pos: usize) -> String {
    let pos = pos.min(chars.len());
    let newlines = chars[..pos].iter().filter(|&&c| c == '\n').count();
    let column = match chars[..pos].iter().rposition(|&c| c == '\n') {
        Some(index) => pos - index,
        None => pos + 1,
    };
    format!("{message}: line {} column {column} (char {pos})", newlines + 1)
}

enum ParseError {
    /// A JSONDecodeError (message already formatted).
    Decode(String),
    /// An error raised by a hook (duplicate field, non-finite number).
    Hook(Error),
    /// Python's StopIteration from the scanner: "Expecting value" at pos.
    Stop(usize),
}

impl<'a> Parser<'a> {
    fn err(&self, message: &str, pos: usize) -> ParseError {
        ParseError::Decode(decode_error(&self.chars, message, pos))
    }

    fn is_ws(c: char) -> bool {
        matches!(c, ' ' | '\t' | '\n' | '\r')
    }

    fn skip_ws(&self, mut index: usize) -> usize {
        while index < self.chars.len() && Self::is_ws(self.chars[index]) {
            index += 1;
        }
        index
    }

    fn starts_with(&self, index: usize, word: &str) -> bool {
        let mut position = index;
        for expected in word.chars() {
            if position >= self.chars.len() || self.chars[position] != expected {
                return false;
            }
            position += 1;
        }
        true
    }

    fn scan_once(&mut self, index: usize) -> std::result::Result<(Value, usize), ParseError> {
        if index >= self.chars.len() {
            return Err(ParseError::Stop(index));
        }
        match self.chars[index] {
            '"' => {
                let (text, end) = self.scan_string(index + 1)?;
                return Ok((Value::Str(text), end));
            }
            '{' => return self.parse_object(index + 1),
            '[' => return self.parse_array(index + 1),
            'n' if self.starts_with(index, "null") => return Ok((Value::Null, index + 4)),
            't' if self.starts_with(index, "true") => return Ok((Value::Bool(true), index + 4)),
            'f' if self.starts_with(index, "false") => return Ok((Value::Bool(false), index + 5)),
            'N' if self.starts_with(index, "NaN") => return self.constant("NaN", f64::NAN, index + 3),
            'I' if self.starts_with(index, "Infinity") => return self.constant("Infinity", f64::INFINITY, index + 8),
            '-' if self.starts_with(index, "-Infinity") => {
                return self.constant("-Infinity", f64::NEG_INFINITY, index + 9)
            }
            _ => {}
        }
        self.number(index)
    }

    fn constant(&self, name: &str, value: f64, end: usize) -> std::result::Result<(Value, usize), ParseError> {
        match self.mode {
            Mode::Strict => Err(ParseError::Hook(Error::profile(format!("Non-finite JSON number: {name}")))),
            Mode::Lenient => Ok((Value::Float(value), end)),
        }
    }

    fn number(&self, start: usize) -> std::result::Result<(Value, usize), ParseError> {
        let chars = &self.chars;
        let end_index = chars.len().wrapping_sub(1);
        let mut index = start;
        let digit = |i: usize| i < chars.len() && chars[i].is_ascii_digit();
        if chars[index] == '-' {
            index += 1;
            if index >= chars.len() {
                return Err(ParseError::Stop(start));
            }
        }
        let c = chars[index];
        if ('1'..='9').contains(&c) {
            index += 1;
            while digit(index) {
                index += 1;
            }
        } else if c == '0' {
            index += 1;
        } else {
            return Err(ParseError::Stop(start));
        }
        let mut is_float = false;
        if index < end_index && chars[index] == '.' && digit(index + 1) {
            is_float = true;
            index += 2;
            while digit(index) {
                index += 1;
            }
        }
        if index < end_index && (chars[index] == 'e' || chars[index] == 'E') {
            let e_start = index;
            index += 1;
            if index < end_index && (chars[index] == '-' || chars[index] == '+') {
                index += 1;
            }
            while digit(index) {
                index += 1;
            }
            if chars[index - 1].is_ascii_digit() {
                is_float = true;
            } else {
                index = e_start;
            }
        }
        let text: String = chars[start..index].iter().collect();
        if is_float {
            let number: f64 = text.parse().unwrap_or(f64::NAN);
            if !number.is_finite() && self.mode == Mode::Strict {
                return Err(ParseError::Hook(Error::profile("JSON number is outside the finite range")));
            }
            return Ok((Value::Float(number), index));
        }
        let digits = text.trim_start_matches('-');
        if digits.len() > 4300 {
            return Err(ParseError::Hook(Error::value(format!(
                "Exceeds the limit (4300 digits) for integer string conversion: value has {} digits; \
                 use sys.set_int_max_str_digits() to increase the limit",
                digits.len()
            ))));
        }
        let value = match text.parse::<i64>() {
            Ok(number) => Value::Int(number),
            Err(_) => {
                let negative = text.starts_with('-');
                let normalized = digits.trim_start_matches('0');
                let normalized = if normalized.is_empty() { "0" } else { normalized };
                if normalized == "0" {
                    Value::Int(0)
                } else {
                    Value::BigInt(format!("{}{}", if negative { "-" } else { "" }, normalized).into_boxed_str())
                }
            }
        };
        Ok((value, index))
    }

    fn hex4(&self, from: usize) -> Option<u32> {
        let mut value = 0u32;
        for offset in 0..4 {
            let digit = self.chars.get(from + offset)?.to_digit(16)?;
            value = value << 4 | digit;
        }
        Some(value)
    }

    fn scan_string(&mut self, mut end: usize) -> std::result::Result<(String, usize), ParseError> {
        let begin = end - 1;
        let length = self.chars.len();
        let mut out = String::new();
        loop {
            let mut next = end;
            let mut c = '\0';
            while next < length {
                c = self.chars[next];
                if c == '"' || c == '\\' {
                    break;
                }
                if (c as u32) <= 0x1f {
                    return Err(self.err("Invalid control character at", next));
                }
                next += 1;
            }
            if next >= length || (c != '"' && c != '\\') {
                return Err(self.err("Unterminated string starting at", begin));
            }
            out.extend(&self.chars[end..next]);
            next += 1;
            if c == '"' {
                end = next;
                break;
            }
            if next == length {
                return Err(self.err("Unterminated string starting at", begin));
            }
            let c = self.chars[next];
            if c != 'u' {
                end = next + 1;
                let mapped = match c {
                    '"' => '"',
                    '\\' => '\\',
                    '/' => '/',
                    'b' => '\u{8}',
                    'f' => '\u{c}',
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    _ => return Err(self.err("Invalid \\escape", end - 2)),
                };
                out.push(mapped);
                continue;
            }
            next += 1;
            end = next + 4;
            if end >= length {
                return Err(self.err("Invalid \\uXXXX escape", next - 1));
            }
            let mut code = match self.hex4(next) {
                Some(code) => code,
                None => return Err(self.err("Invalid \\uXXXX escape", end - 5)),
            };
            if (0xD800..0xDC00).contains(&code)
                && end + 6 < length
                && self.chars[end] == '\\'
                && self.chars[end + 1] == 'u'
            {
                let second_start = end + 2;
                end += 6;
                let second = match self.hex4(second_start) {
                    Some(value) => value,
                    None => return Err(self.err("Invalid \\uXXXX escape", end - 5)),
                };
                if (0xDC00..0xE000).contains(&second) {
                    code = 0x10000 + ((code - 0xD800) << 10) + (second - 0xDC00);
                } else {
                    end -= 6;
                }
            }
            match char::from_u32(code) {
                Some(character) => out.push(character),
                None => {
                    self.lone_surrogate = true;
                    out.push('\u{FFFD}');
                }
            }
        }
        Ok((out, end))
    }

    fn parse_object(&mut self, start: usize) -> std::result::Result<(Value, usize), ParseError> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(ParseError::Hook(Error::value(
                "maximum recursion depth exceeded while decoding a JSON object from a unicode string",
            )));
        }
        let length = self.chars.len();
        let mut index = self.skip_ws(start);
        let mut pairs: Vec<(String, Value)> = Vec::new();
        if index >= length || self.chars[index] != '}' {
            loop {
                if index >= length || self.chars[index] != '"' {
                    return Err(self.err("Expecting property name enclosed in double quotes", index));
                }
                let (key, next) = self.scan_string(index + 1)?;
                index = self.skip_ws(next);
                if index >= length || self.chars[index] != ':' {
                    return Err(self.err("Expecting ':' delimiter", index));
                }
                index = self.skip_ws(index + 1);
                let (value, next) = self.scan_once(index)?;
                pairs.push((key, value));
                index = self.skip_ws(next);
                if index < length && self.chars[index] == '}' {
                    break;
                }
                if index >= length || self.chars[index] != ',' {
                    return Err(self.err("Expecting ',' delimiter", index));
                }
                index = self.skip_ws(index + 1);
            }
        }
        self.depth -= 1;
        let mut dict = Dict::with_capacity(pairs.len());
        match self.mode {
            Mode::Strict => {
                let mut seen = std::collections::HashSet::with_capacity(pairs.len());
                for (key, value) in pairs {
                    if !seen.insert(key.clone()) {
                        return Err(ParseError::Hook(Error::profile(format!("Duplicate JSON field: {key}"))));
                    }
                    dict.push_unique(key, value);
                }
            }
            Mode::Lenient => {
                for (key, value) in pairs {
                    dict.insert(key, value);
                }
            }
        }
        Ok((Value::Dict(dict), index + 1))
    }

    fn parse_array(&mut self, start: usize) -> std::result::Result<(Value, usize), ParseError> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(ParseError::Hook(Error::value(
                "maximum recursion depth exceeded while decoding a JSON array from a unicode string",
            )));
        }
        let length = self.chars.len();
        let mut index = self.skip_ws(start);
        let mut items = Vec::new();
        if index >= length || self.chars[index] != ']' {
            loop {
                let (value, next) = self.scan_once(index)?;
                items.push(value);
                index = self.skip_ws(next);
                if index < length && self.chars[index] == ']' {
                    break;
                }
                if index >= length || self.chars[index] != ',' {
                    return Err(self.err("Expecting ',' delimiter", index));
                }
                index = self.skip_ws(index + 1);
            }
        }
        self.depth -= 1;
        Ok((Value::List(items), index + 1))
    }

    fn run(&mut self) -> std::result::Result<Value, ParseError> {
        if self.text.starts_with('\u{feff}') {
            return Err(self.err("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0));
        }
        let start = self.skip_ws(0);
        let (value, end) = match self.scan_once(start) {
            Ok(result) => result,
            Err(ParseError::Stop(pos)) => return Err(self.err("Expecting value", pos)),
            Err(other) => return Err(other),
        };
        let end = self.skip_ws(end);
        if end != self.chars.len() {
            return Err(self.err("Extra data", end));
        }
        Ok(value)
    }
}

fn parse_text(text: &str, mode: Mode) -> std::result::Result<(Value, bool), (bool, String, Option<Error>)> {
    let mut parser = Parser { chars: text.chars().collect(), text, mode, lone_surrogate: false, depth: 0 };
    match parser.run() {
        Ok(value) => Ok((value, parser.lone_surrogate)),
        Err(ParseError::Decode(message)) => Err((false, message, None)),
        Err(ParseError::Hook(error)) => Err((true, error.message.clone(), Some(error))),
        Err(ParseError::Stop(pos)) => Err((false, decode_error(&parser.chars, "Expecting value", pos), None)),
    }
}

/// Python's message for a failed strict UTF-8 decode.
pub fn utf8_error_message(data: &[u8], error: &std::str::Utf8Error) -> String {
    let start = error.valid_up_to();
    let byte = data[start];
    let reason;
    let mut length = 1;
    let invalid_start = matches!(byte, 0x80..=0xC1 | 0xF5..=0xFF);
    if invalid_start {
        reason = "invalid start byte";
    } else {
        match error.error_len() {
            None => {
                reason = "unexpected end of data";
                length = data.len() - start;
            }
            Some(count) => {
                reason = "invalid continuation byte";
                length = count;
            }
        }
    }
    if length == 1 {
        format!("'utf-8' codec can't decode byte 0x{byte:02x} in position {start}: {reason}")
    } else {
        format!("'utf-8' codec can't decode bytes in position {start}-{}: {reason}", start + length - 1)
    }
}

/// The reference's `read_json(path)` on already-read bytes (`name` is the file name).
pub fn parse_strict(data: &[u8], name: &str) -> Result<Value> {
    let data = data.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(data);
    let text = match std::str::from_utf8(data) {
        Ok(text) => text,
        Err(error) => {
            return Err(Error::profile(format!("Invalid JSON in {name}: {}", utf8_error_message(data, &error))))
        }
    };
    // Path.read_text translates newlines before json.loads sees the text.
    let translated;
    let text = if text.contains('\r') {
        translated = text.replace("\r\n", "\n").replace('\r', "\n");
        translated.as_str()
    } else {
        text
    };
    let (value, lone_surrogate) = match parse_text(text, Mode::Strict) {
        Ok(result) => result,
        Err((true, _, Some(error))) if error.kind == crate::error::ErrorKind::Profile => return Err(error),
        Err((_, message, _)) => return Err(Error::profile(format!("Invalid JSON in {name}: {message}"))),
    };
    if !value.is_dict() {
        return Err(Error::profile("Expected a JSON object"));
    }
    if lone_surrogate {
        return Err(Error::profile("JSON strings must be valid Unicode"));
    }
    Ok(value)
}

/// Plain `json.loads(text)` (the editor's request bodies).
pub fn parse_lenient(text: &str) -> Result<Value> {
    match parse_text(text, Mode::Lenient) {
        Ok((value, _)) => Ok(value),
        Err((_, message, error)) => Err(error.unwrap_or_else(|| Error::value(message))),
    }
}

// ---------------------------------------------------------------- writing

/// Python's `repr(float)`.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific.split_once('e').expect("exponent");
    let exponent: i32 = exponent.parse().expect("exponent value");
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let decpt = exponent + 1;
    let sign = if value.is_sign_negative() { "-" } else { "" };
    if decpt <= -4 || decpt > 16 {
        let mut out = String::from(sign);
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let shown = decpt - 1;
        out.push('e');
        out.push(if shown < 0 { '-' } else { '+' });
        let _ = write!(out, "{:02}", shown.abs());
        return out;
    }
    let mut out = String::from(sign);
    if decpt <= 0 {
        out.push_str("0.");
        out.extend(std::iter::repeat('0').take((-decpt) as usize));
        out.push_str(&digits);
    } else if decpt as usize >= digits.len() {
        out.push_str(&digits);
        out.extend(std::iter::repeat('0').take(decpt as usize - digits.len()));
        out.push_str(".0");
    } else {
        out.push_str(&digits[..decpt as usize]);
        out.push('.');
        out.push_str(&digits[decpt as usize..]);
    }
    out
}

fn escape_into(out: &mut String, text: &str, ascii: bool) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c if ascii && !(' '..='~').contains(&c) => {
                let code = c as u32;
                if code > 0xFFFF {
                    let reduced = code - 0x10000;
                    let _ = write!(out, "\\u{:04x}\\u{:04x}", 0xD800 + (reduced >> 10), 0xDC00 + (reduced & 0x3FF));
                } else {
                    let _ = write!(out, "\\u{code:04x}");
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

#[derive(Clone, Copy)]
pub struct Style {
    pub indent: Option<usize>,
    pub item_separator: &'static str,
    pub key_separator: &'static str,
    pub sort_keys: bool,
    pub ensure_ascii: bool,
    pub allow_nan: bool,
}

impl Style {
    /// `json.dumps(v, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)`.
    pub const CANONICAL: Style = Style {
        indent: Some(2),
        item_separator: ",",
        key_separator: ": ",
        sort_keys: true,
        ensure_ascii: false,
        allow_nan: false,
    };
    /// `json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`.
    pub const DIGEST: Style = Style {
        indent: None,
        item_separator: ",",
        key_separator: ":",
        sort_keys: true,
        ensure_ascii: false,
        allow_nan: false,
    };
    /// `json.dumps(v, ensure_ascii=True, sort_keys=sort)`.
    pub const fn compact_ascii(sort_keys: bool) -> Style {
        Style { indent: None, item_separator: ", ", key_separator: ": ", sort_keys, ensure_ascii: true, allow_nan: true }
    }
}

/// `ValueError` from json.dumps for NaN/inf with allow_nan=False.
pub struct NonFinite;

pub fn dumps(value: &Value, style: Style) -> std::result::Result<String, NonFinite> {
    let mut out = String::new();
    write_value(&mut out, value, style, 0)?;
    Ok(out)
}

fn newline_indent(out: &mut String, style: Style, level: usize) {
    if let Some(indent) = style.indent {
        out.push('\n');
        out.extend(std::iter::repeat(' ').take(indent * level));
    }
}

fn write_value(out: &mut String, value: &Value, style: Style, level: usize) -> std::result::Result<(), NonFinite> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(number) => {
            let _ = write!(out, "{number}");
        }
        Value::BigInt(text) => out.push_str(text),
        Value::Float(number) => {
            if !number.is_finite() && !style.allow_nan {
                return Err(NonFinite);
            }
            out.push_str(&float_repr(*number));
        }
        Value::Str(text) => escape_into(out, text, style.ensure_ascii),
        Value::List(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return Ok(());
            }
            out.push('[');
            newline_indent(out, style, level + 1);
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(style.item_separator);
                    newline_indent(out, style, level + 1);
                }
                write_value(out, item, style, level + 1)?;
            }
            newline_indent(out, style, level);
            out.push(']');
        }
        Value::Dict(dict) => {
            if dict.is_empty() {
                out.push_str("{}");
                return Ok(());
            }
            out.push('{');
            newline_indent(out, style, level + 1);
            let mut entries: Vec<(&str, &Value)> = dict.iter().collect();
            if style.sort_keys {
                entries.sort_by(|a, b| str_cmp(a.0, b.0));
            }
            for (index, (key, item)) in entries.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(style.item_separator);
                    newline_indent(out, style, level + 1);
                }
                escape_into(out, key, style.ensure_ascii);
                out.push_str(style.key_separator);
                write_value(out, item, style, level + 1)?;
            }
            newline_indent(out, style, level);
            out.push('}');
        }
    }
    Ok(())
}

/// The reference's `canonical_bytes`.
pub fn canonical_bytes(value: &Value) -> Result<Vec<u8>> {
    match dumps(value, Style::CANONICAL) {
        Ok(mut text) => {
            text.push('\n');
            Ok(text.into_bytes())
        }
        Err(NonFinite) => Err(Error::profile("Configuration cannot be represented as finite UTF-8 JSON")),
    }
}

/// `json.dumps(value, ensure_ascii=True, sort_keys=...)` (CLI and editor replies).
pub fn dumps_ascii(value: &Value, sort_keys: bool) -> String {
    match dumps(value, Style::compact_ascii(sort_keys)) {
        Ok(text) => text,
        Err(NonFinite) => unreachable!("allow_nan is true"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_repr_matches_python() {
        let cases = [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (1.5, "1.5"),
            (0.1, "0.1"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (123456789012345680.0, "1.2345678901234568e+17"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e100, "1e+100"),
            (-2.5, "-2.5"),
            (1.2, "1.2"),
            (100.0, "100.0"),
        ];
        for (value, expected) in cases {
            assert_eq!(float_repr(value), expected, "{value}");
        }
    }

    #[test]
    fn decode_errors_match_python() {
        let check = |text: &str, expected: &str| {
            let error = parse_lenient(text).unwrap_err();
            assert_eq!(error.message, expected, "{text:?}");
        };
        check("", "Expecting value: line 1 column 1 (char 0)");
        check("[", "Expecting value: line 1 column 2 (char 1)");
        check("[1,", "Expecting value: line 1 column 4 (char 3)");
        check("[1", "Expecting ',' delimiter: line 1 column 3 (char 2)");
        check("{\"a\":1", "Expecting ',' delimiter: line 1 column 7 (char 6)");
        check("{", "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)");
        check("{\"a\"", "Expecting ':' delimiter: line 1 column 5 (char 4)");
        check("{\"a\":", "Expecting value: line 1 column 6 (char 5)");
        check("\"abc", "Unterminated string starting at: line 1 column 1 (char 0)");
        check("[1]x", "Extra data: line 1 column 4 (char 3)");
    }

    #[test]
    fn canonical_output_matches_python_layout() {
        let value = Value::Dict(dict! {"b" => list![1, 2], "a" => Value::Dict(Dict::new()), "c" => list![], "d" => "é\n"});
        let text = String::from_utf8(canonical_bytes(&value).unwrap()).unwrap();
        assert_eq!(text, "{\n  \"a\": {},\n  \"b\": [\n    1,\n    2\n  ],\n  \"c\": [],\n  \"d\": \"é\\n\"\n}\n");
        assert_eq!(dumps_ascii(&value, true), "{\"a\": {}, \"b\": [1, 2], \"c\": [], \"d\": \"\\u00e9\\n\"}");
    }
}
