//! Path behaviour of the reference's `pathlib` on Windows: realpath-style
//! resolution, case-insensitive containment, and the name/suffix/stem rules.

use std::cmp::Ordering;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// `ntpath.splitdrive`.
pub fn splitdrive(path: &str) -> (&str, &str) {
    if path.len() >= 2 {
        let normalized = path.replace('/', "\\");
        if normalized.starts_with("\\\\") {
            let start = if normalized.len() >= 8 && normalized[..8].eq_ignore_ascii_case("\\\\?\\UNC\\") { 8 } else { 2 };
            let index = match normalized[start..].find('\\') {
                Some(offset) => start + offset,
                None => return (path, ""),
            };
            let index2 = match normalized[index + 1..].find('\\') {
                Some(offset) => index + 1 + offset,
                None => return (path, ""),
            };
            return (&path[..index2], &path[index2..]);
        }
        if normalized.as_bytes()[1] == b':' && path.is_char_boundary(2) {
            return (&path[..2], &path[2..]);
        }
    }
    ("", path)
}

/// `ntpath.normpath`.
pub fn normpath(path: &str) -> String {
    if path.starts_with("\\\\.\\") || path.starts_with("\\\\?\\") {
        return path.to_string();
    }
    let replaced = path.replace('/', "\\");
    let (drive, rest) = splitdrive(&replaced);
    let mut prefix = drive.to_string();
    let mut rest = rest;
    if rest.starts_with('\\') {
        prefix.push('\\');
        rest = rest.trim_start_matches('\\');
    }
    let mut comps: Vec<&str> = rest.split('\\').collect();
    let mut index = 0;
    while index < comps.len() {
        if comps[index].is_empty() || comps[index] == "." {
            comps.remove(index);
        } else if comps[index] == ".." {
            if index > 0 && comps[index - 1] != ".." {
                comps.drain(index - 1..=index);
                index -= 1;
            } else if index == 0 && prefix.ends_with('\\') {
                comps.remove(index);
            } else {
                index += 1;
            }
        } else {
            index += 1;
        }
    }
    if prefix.is_empty() && comps.is_empty() {
        comps.push(".");
    }
    prefix + &comps.join("\\")
}

/// `ntpath.isabs`.
pub fn isabs(path: &str) -> bool {
    let head: String = path.chars().take(3).collect::<String>().replace('/', "\\");
    head.starts_with('\\') || head.get(1..).is_some_and(|rest| rest.starts_with(":\\"))
}

/// The absolute, normalized spelling `realpath` resolves (and names in errors).
pub fn absolute(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    let normalized = normpath(&text);
    if normalized.starts_with("\\\\?\\") || isabs(&normalized) {
        return PathBuf::from(normalized);
    }
    match std::path::absolute(Path::new(&normalized)) {
        Ok(absolute) => PathBuf::from(normpath(&absolute.to_string_lossy())),
        Err(_) => PathBuf::from(normalized),
    }
}

fn strip_verbatim(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix("\\\\?\\UNC\\") {
        return PathBuf::from(format!("\\\\{rest}"));
    }
    if let Some(rest) = text.strip_prefix("\\\\?\\") {
        return PathBuf::from(rest);
    }
    path.to_path_buf()
}

/// Whether the unprefixed spelling might not name the same file (Python then
/// checks it with another resolution).
fn needs_prefix_check(stripped: &Path) -> bool {
    let text = stripped.to_string_lossy();
    text.len() >= 260 || text.split('\\').any(|part| part.ends_with('.') || part.ends_with(' '))
}

/// `nt._getfinalpathname` with Python's prefix handling.
fn final_path(path: &Path) -> io::Result<PathBuf> {
    let canonical = fs::canonicalize(path)?;
    let had_prefix = path.to_string_lossy().starts_with("\\\\?\\");
    if had_prefix {
        return Ok(canonical);
    }
    let stripped = strip_verbatim(&canonical);
    if stripped == canonical || !needs_prefix_check(&stripped) {
        return Ok(stripped);
    }
    match fs::canonicalize(&stripped) {
        Ok(again) if again == canonical => Ok(stripped),
        _ => Ok(canonical),
    }
}

const NONSTRICT_ERRORS: &[i32] = &[1, 2, 3, 5, 21, 32, 50, 53, 65, 67, 87, 123, 161, 1920, 1921];

/// `Path.resolve(strict)`: an absolute path with links resolved and the
/// on-disk spelling. Non-strict resolution keeps a missing tail as written.
/// Errors carry the absolute path Python names in its message.
pub fn resolve(path: &Path, strict: bool) -> Result<PathBuf, (io::Error, PathBuf)> {
    let absolute = absolute(path);
    match final_path(&absolute) {
        Ok(resolved) => Ok(resolved),
        Err(error) if strict => Err((error, absolute)),
        Err(error) => {
            let code = error.raw_os_error().unwrap_or(2);
            if !NONSTRICT_ERRORS.contains(&code) {
                return Err((error, absolute));
            }
            let mut current = absolute.to_string_lossy().into_owned();
            let mut tail = String::new();
            loop {
                if current.is_empty() {
                    return Ok(PathBuf::from(tail));
                }
                match final_path(Path::new(&current)) {
                    Ok(found) => {
                        let found = found.to_string_lossy().into_owned();
                        return Ok(PathBuf::from(if tail.is_empty() { found } else { join(&found, &tail) }));
                    }
                    Err(error) => {
                        let code = error.raw_os_error().unwrap_or(2);
                        if !NONSTRICT_ERRORS.contains(&code) {
                            return Err((error, absolute));
                        }
                        let (head, name) = split(&current);
                        if !head.is_empty() && name.is_empty() {
                            return Ok(PathBuf::from(format!("{head}{tail}")));
                        }
                        tail = if tail.is_empty() { name.to_string() } else { join(name, &tail) };
                        current = head.to_string();
                    }
                }
            }
        }
    }
}

/// `ntpath.split`.
pub fn split(path: &str) -> (String, &str) {
    let (drive, rest) = splitdrive(path);
    let cut = rest.rfind(['\\', '/']).map_or(0, |index| index + 1);
    let (head, tail) = rest.split_at(cut);
    let trimmed = head.trim_end_matches(['\\', '/']);
    let head = if trimmed.is_empty() { head } else { trimmed };
    (format!("{drive}{head}"), tail)
}

/// `ntpath.join` for two parts.
pub fn join(first: &str, second: &str) -> String {
    let (second_drive, second_rest) = splitdrive(second);
    if !second_drive.is_empty() || second_rest.starts_with(['\\', '/']) {
        return second.to_string();
    }
    if first.is_empty() || first.ends_with(['\\', '/']) || (first.len() == 2 && first.ends_with(':')) {
        return format!("{first}{second}");
    }
    format!("{first}\\{second}")
}

/// The parts `PureWindowsPath` compares (lowercased).
fn compare_parts(path: &Path) -> Vec<String> {
    parts(path).into_iter().map(|part| crate::py::lower(&part)).collect()
}

/// pathlib's `_WindowsFlavour.splitroot` for one part (separators already
/// backslashes): drive, root and the rest.
pub fn splitroot(part: &str) -> (String, String, String) {
    let part = part.replace('/', "\\");
    let mut prefix = String::new();
    let mut body: &str = &part;
    if body.starts_with("\\\\") {
        // Extended paths: \\?\ (and \\?\UNC\ for shares).
        if let Some(rest) = body.strip_prefix("\\\\?\\") {
            prefix.push_str("\\\\?\\");
            body = rest;
            if rest.len() >= 4 && rest[..4].eq_ignore_ascii_case("UNC\\") {
                prefix.push_str(&rest[..3]);
                body = &rest[3..];
            }
        }
    }
    let bytes = body.as_bytes();
    let first = bytes.first().copied();
    let second = bytes.get(1).copied();
    let third = bytes.get(2).copied();
    if first == Some(b'\\') && second == Some(b'\\') && third != Some(b'\\') {
        if let Some(index) = body[2..].find('\\').map(|offset| offset + 2) {
            let index2 = body[index + 1..].find('\\').map(|offset| offset + index + 1);
            if index2 != Some(index + 1) {
                let index2 = index2.unwrap_or(body.len());
                let drive = if prefix.is_empty() {
                    body[..index2].to_string()
                } else {
                    format!("{prefix}{}", &body[1..index2])
                };
                let rest = body.get(index2 + 1..).unwrap_or("").to_string();
                return (drive, "\\".into(), rest);
            }
        }
    }
    let mut drive = String::new();
    let mut rest = body;
    if second == Some(b':') && first.is_some_and(|byte| byte.is_ascii_alphabetic()) {
        drive = body[..2].to_string();
        rest = &body[2..];
    }
    let mut root = String::new();
    if rest.starts_with('\\') {
        root.push('\\');
        rest = rest.trim_start_matches('\\');
    }
    (format!("{prefix}{drive}"), root, rest.to_string())
}

/// pathlib's parse of one path string: drive, root and the named parts.
fn parse(path: &Path) -> (String, String, Vec<String>) {
    let text = path.to_string_lossy();
    if text.is_empty() {
        return (String::new(), String::new(), Vec::new());
    }
    let (drive, root, rest) = splitroot(&text);
    let parts = rest.split('\\').filter(|part| !part.is_empty() && *part != ".").map(str::to_string).collect();
    (drive, root, parts)
}

/// `PureWindowsPath.parts` (drive+root as the first part).
pub fn parts(path: &Path) -> Vec<String> {
    let (drive, root, named) = parse(path);
    let mut parts = Vec::with_capacity(named.len() + 1);
    if !drive.is_empty() || !root.is_empty() {
        parts.push(format!("{drive}{root}"));
    }
    parts.extend(named);
    parts
}

/// `PurePath.parent`.
pub fn parent(path: &Path) -> PathBuf {
    let (drive, root, mut named) = parse(path);
    if named.is_empty() {
        return PathBuf::from(display(path));
    }
    named.pop();
    let anchor = format!("{drive}{root}");
    if anchor.is_empty() {
        if named.is_empty() {
            return PathBuf::from(".");
        }
        return PathBuf::from(named.join("\\"));
    }
    PathBuf::from(format!("{anchor}{}", named.join("\\")))
}

/// `PurePath.is_relative_to` with Windows' case-insensitive comparison.
pub fn is_relative_to(path: &Path, root: &Path) -> bool {
    let path = compare_parts(path);
    let root = compare_parts(root);
    root.len() <= path.len() && path[..root.len()] == root[..]
}

/// `root in path.parents`: `path` lies strictly inside `root`.
pub fn is_inside(path: &Path, root: &Path) -> bool {
    let path = compare_parts(path);
    let root = compare_parts(root);
    root.len() < path.len() && path[..root.len()] == root[..]
}

/// Python's ordering of two Windows paths (`sorted(paths)`).
pub fn path_cmp(a: &Path, b: &Path) -> Ordering {
    compare_parts(a).cmp(&compare_parts(b))
}

/// `PurePath.name`.
pub fn name(path: &Path) -> String {
    parse(path).2.last().cloned().unwrap_or_default()
}

/// `PurePath.suffix` of a file name.
pub fn suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(index) if index > 0 && index < name.len() - 1 => &name[index..],
        _ => "",
    }
}

/// `PurePath.stem` of a file name.
pub fn stem(name: &str) -> &str {
    match name.rfind('.') {
        Some(index) if index > 0 && index < name.len() - 1 => &name[..index],
        _ => name,
    }
}

/// `Path(value)` of a JSON value: text is a path (`""` is the current
/// folder, as `Path("")` is `.`); another value fails with Python's
/// TypeError.
pub fn path_argument(value: &crate::json::Value) -> crate::error::Result<PathBuf> {
    match value.as_str() {
        Some("") => Ok(PathBuf::from(".")),
        Some(text) => Ok(PathBuf::from(text)),
        None => Err(crate::error::Error::type_error(format!(
            "expected str, bytes or os.PathLike object, not {}",
            value.type_name()
        ))),
    }
}

/// `str(PureWindowsPath(path))`.
pub fn display(path: &Path) -> String {
    let (drive, root, named) = parse(path);
    let anchor = format!("{drive}{root}");
    if anchor.is_empty() && named.is_empty() {
        return ".".into();
    }
    format!("{anchor}{}", named.join("\\"))
}

/// `Path.is_file()`: false for missing paths, errors for unreadable ones.
pub fn is_file(path: &Path) -> io::Result<bool> {
    match fs::metadata(path) {
        Ok(metadata) => Ok(metadata.is_file()),
        Err(error) if ignored(&error) => Ok(false),
        Err(error) => Err(error),
    }
}

/// `Path.is_dir()`.
pub fn is_dir(path: &Path) -> io::Result<bool> {
    match fs::metadata(path) {
        Ok(metadata) => Ok(metadata.is_dir()),
        Err(error) if ignored(&error) => Ok(false),
        Err(error) => Err(error),
    }
}

/// `Path.exists()`.
pub fn exists(path: &Path) -> io::Result<bool> {
    match fs::metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if ignored(&error) => Ok(false),
        Err(error) => Err(error),
    }
}

/// `Path.is_symlink()`.
pub fn is_symlink(path: &Path) -> bool {
    fs::symlink_metadata(path).map(|metadata| metadata.file_type().is_symlink()).unwrap_or(false)
}

/// pathlib's `_ignore_error`: missing paths and unresolvable links; and a
/// path with a NUL character, for which `os.stat` raises the ValueError that
/// these checks also turn into false.
fn ignored(error: &io::Error) -> bool {
    matches!(error.raw_os_error(), Some(2 | 3 | 21 | 123 | 1921))
        || error.kind() == io::ErrorKind::NotFound
        // Rust refuses a path with a NUL before asking the system.
        || (error.raw_os_error().is_none() && error.kind() == io::ErrorKind::InvalidInput)
}

/// `iterdir()` names in the operating system's order.
pub fn list_dir(path: &Path) -> io::Result<Vec<String>> {
    let mut names = Vec::new();
    for entry in fs::read_dir(path)? {
        names.push(entry?.file_name().to_string_lossy().into_owned());
    }
    Ok(names)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normpath_matches_ntpath() {
        assert_eq!(normpath("a/b/../c/./d/"), "a\\c\\d");
        assert_eq!(normpath("C:\\\\x\\..\\..\\y"), "C:\\y");
        assert_eq!(normpath(".."), "..");
        assert_eq!(normpath(""), ".");
        assert_eq!(normpath("\\\\server\\share\\a\\..\\b"), "\\\\server\\share\\b");
    }

    #[test]
    fn suffix_and_stem_follow_pathlib() {
        assert_eq!(suffix("a.itm"), ".itm");
        assert_eq!(suffix(".itm"), "");
        assert_eq!(suffix("a."), "");
        assert_eq!(stem("dw#marker_file.itm"), "dw#marker_file");
        assert_eq!(stem("a.b.c"), "a.b");
    }

    #[test]
    fn checks_of_a_path_with_a_nul_are_false() {
        // pathlib turns os.stat's ValueError into False.
        let path = Path::new("game\0root");
        assert!(!is_dir(path).unwrap());
        assert!(!is_file(path).unwrap());
        assert!(!exists(path).unwrap());
    }

    #[test]
    fn containment_ignores_case() {
        assert!(is_relative_to(Path::new("C:\\Games\\X\\override\\a.itm"), Path::new("c:\\games\\x")));
        assert!(!is_relative_to(Path::new("C:\\Games\\XY\\a.itm"), Path::new("C:\\Games\\X")));
    }

    #[test]
    fn parent_and_name_follow_pathlib() {
        assert_eq!(parent(Path::new("C:\\a\\b.json")), PathBuf::from("C:\\a"));
        assert_eq!(parent(Path::new("C:\\")), PathBuf::from("C:\\"));
        assert_eq!(parent(Path::new("x.json")), PathBuf::from("."));
        assert_eq!(parent(Path::new("out/x/y")), PathBuf::from("out\\x"));
        assert_eq!(name(Path::new("C:\\a\\b.json")), "b.json");
        assert_eq!(name(Path::new("C:\\")), "");
        assert_eq!(splitroot("\\\\server\\share\\x"), ("\\\\server\\share".into(), "\\".into(), "x".into()));
        assert_eq!(splitroot("1:\\x"), ("".into(), "".into(), "1:\\x".into()));
    }

    #[test]
    fn display_normalizes_like_pure_windows_path() {
        assert_eq!(display(Path::new("out/./x/")), "out\\x");
        assert_eq!(display(Path::new("C:/a//b")), "C:\\a\\b");
        assert_eq!(display(Path::new("")), ".");
    }
}
