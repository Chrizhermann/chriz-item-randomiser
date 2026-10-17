//! Errors with the same classes and messages as the Python reference.
//!
//! `Profile` and `Scan` are the reference's `ProfileError` and `ScanError`;
//! `Os` is an `OSError` (message formatted like Python's); `Value`, `Key` and
//! `Type` stand for Python's built-in errors that the reference lets escape
//! from malformed input. The CLI reports `Profile`, `Scan` and `Os` as JSON
//! like the reference; the editor reports all of them as a request error.

use std::fmt;
use std::io;
use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    Profile,
    Scan,
    Os,
    Value,
    Key,
    Type,
    /// The reference's `GameTextError` (a ValueError).
    GameText,
    /// Python's OverflowError (an out-of-range port number).
    Overflow,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    pub message: String,
}

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Error { kind, message: message.into() }
    }
    pub fn profile(message: impl Into<String>) -> Self {
        Error::new(ErrorKind::Profile, message)
    }
    pub fn scan(message: impl Into<String>) -> Self {
        Error::new(ErrorKind::Scan, message)
    }
    pub fn value(message: impl Into<String>) -> Self {
        Error::new(ErrorKind::Value, message)
    }
    /// A Python KeyError: `str()` quotes the missing key.
    pub fn key(key: &str) -> Self {
        Error::new(ErrorKind::Key, py_repr_str(key))
    }
    pub fn game_text(message: impl Into<String>) -> Self {
        Error::new(ErrorKind::GameText, message)
    }
    pub fn type_error(message: impl Into<String>) -> Self {
        Error::new(ErrorKind::Type, message)
    }
    /// An OSError from a C-runtime call (`open`, `Path.read_bytes`, `read_text`,
    /// `write_bytes`): Python reports the mapped errno.
    pub fn os(error: &io::Error, path: &Path) -> Self {
        Error::new(ErrorKind::Os, errno_message(error, Some(path)))
    }
    /// An OSError from a Win32 call (`stat`, `listdir`, `mkdir`, `replace`,
    /// `realpath`): Python reports the Windows error code and text.
    pub fn win(error: &io::Error, path: &Path) -> Self {
        Error::new(ErrorKind::Os, win_message(error, Some(path)))
    }
    /// An OSError from a two-path Win32 call (`os.replace`, `os.link`).
    pub fn win_pair(error: &io::Error, source: &Path, target: &Path) -> Self {
        let mut failure = Error::win(error, source);
        failure.message.push_str(&format!(" -> {}", py_repr_str(&target.to_string_lossy())));
        failure
    }
    pub fn os_plain(error: &io::Error) -> Self {
        Error::new(ErrorKind::Os, win_message(error, None))
    }
    /// Python's `except (ProfileError, ScanError, OSError)` in the CLI.
    pub fn is_reported(&self) -> bool {
        matches!(self.kind, ErrorKind::Profile | ErrorKind::Scan | ErrorKind::Os)
    }
    /// ScanError and the reference's GameTextError derive from ValueError.
    pub fn is_value_error(&self) -> bool {
        matches!(self.kind, ErrorKind::Profile | ErrorKind::Scan | ErrorKind::Value | ErrorKind::GameText)
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for Error {}

/// Python's `repr()` of a str (used in KeyError and OSError messages).
pub fn py_repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if !crate::py::is_printable(c) => {
                let code = c as u32;
                if code <= 0xff {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code <= 0xffff {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn path_suffix(path: Option<&Path>) -> String {
    path.map(|path| format!(": {}", py_repr_str(&path.to_string_lossy()))).unwrap_or_default()
}

/// The C runtime's `_dosmaperr` for the codes file access reports.
fn errno_for(code: i32) -> (i32, &'static str) {
    match code {
        2 | 3 | 15 | 53 | 67 | 123 | 161 | 206 => (2, "No such file or directory"),
        5 | 19 | 32 | 33 | 65 | 82 | 1920 => (13, "Permission denied"),
        80 | 183 => (17, "File exists"),
        39 | 112 => (28, "No space left on device"),
        4 => (24, "Too many open files"),
        8 | 14 => (12, "Not enough space"),
        _ => (22, "Invalid argument"),
    }
}

fn errno_message(error: &io::Error, path: Option<&Path>) -> String {
    let suffix = path_suffix(path);
    match error.raw_os_error() {
        Some(code) => {
            let (number, text) = errno_for(code);
            format!("[Errno {number}] {text}{suffix}")
        }
        None => match error.kind() {
            io::ErrorKind::NotFound => format!("[Errno 2] No such file or directory{suffix}"),
            io::ErrorKind::PermissionDenied => format!("[Errno 13] Permission denied{suffix}"),
            io::ErrorKind::AlreadyExists => format!("[Errno 17] File exists{suffix}"),
            _ => format!("{error}{suffix}"),
        },
    }
}

/// FormatMessageW text without Rust's "(os error N)" suffix, trimmed like
/// Python trims it (trailing whitespace and dots).
pub fn windows_text(error: &io::Error) -> String {
    let mut text = error.to_string();
    if let Some(index) = text.find(" (os error") {
        text.truncate(index);
    }
    let trimmed = text.trim_end_matches(|c: char| c <= ' ' || c == '.');
    trimmed.to_string()
}

fn win_message(error: &io::Error, path: Option<&Path>) -> String {
    let suffix = path_suffix(path);
    match error.raw_os_error() {
        Some(code) => format!("[WinError {code}] {}{suffix}", windows_text(error)),
        None => format!("{error}{suffix}"),
    }
}
