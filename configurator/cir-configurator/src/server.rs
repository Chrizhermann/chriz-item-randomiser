//! The local editor's HTTP server: the reference's `editor.create_server`
//! and `serve` (Python's `http.server`). It listens on 127.0.0.1 only and
//! answers one HTTP/1.0 request per connection with the same routes, checks
//! and JSON replies. Each connection is read on its own thread, so a browser
//! connection that never sends a request cannot block the editor; the editor
//! itself handles one request at a time.

use cir_config::editor::Editor;
use cir_config::json::{self, Dict, Value};
use cir_config::{profiles, py, Error, ErrorKind, Result};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// `http.client`'s limits for the request line and headers.
const MAX_LINE: usize = 65536;
const MAX_HEADERS: usize = 100;
const MAX_BODY: i64 = 2_000_000;
/// How long a connection may stay silent before it is dropped.
const IDLE: Duration = Duration::from_secs(60);

const STATIC: [(&str, &str, &str); 3] = [
    ("/", "tools/flir_config/ui/index.html", "text/html"),
    ("/style.css", "tools/flir_config/ui/style.css", "text/css"),
    ("/app.js", "tools/flir_config/ui/app.js", "text/javascript"),
];

const ERROR_PAGE: &str = "<!DOCTYPE HTML>\n<html lang=\"en\">\n    <head>\n        <meta charset=\"utf-8\">\n        \
<title>Error response</title>\n    </head>\n    <body>\n        <h1>Error response</h1>\n        \
<p>Error code: {code}</p>\n        <p>Message: {message}.</p>\n        \
<p>Error code explanation: {code} - {explain}.</p>\n    </body>\n</html>\n";

static STOP: AtomicBool = AtomicBool::new(false);
static PORT: AtomicU16 = AtomicU16::new(0);

#[allow(clippy::too_many_arguments)]
pub fn serve(
    catalog: &Path,
    world: &Path,
    workspace: &Path,
    port: i64,
    profile: Option<&Path>,
    plan: Option<&Path>,
    apply_game: Option<&Path>,
    open_browser: bool,
) -> Result<Value> {
    let catalog = profiles::read_json(catalog)?;
    let world = profiles::read_json(world)?;
    let profile = profile.map(profiles::read_json).transpose()?;
    let plan = plan.map(profiles::read_json).transpose()?;
    let editor = Editor::new(catalog, world, workspace, profile, plan, apply_game)?;
    // socket.bind's checks of the port number (a C long on Windows).
    if i32::try_from(port).is_err() {
        return Err(Error::new(ErrorKind::Overflow, "Python int too large to convert to C long"));
    }
    let port = u16::try_from(port).map_err(|_| Error::new(ErrorKind::Overflow, "bind(): port must be 0-65535."))?;
    let listener = TcpListener::bind(("127.0.0.1", port)).map_err(|error| Error::os_plain(&error))?;
    let port = listener.local_addr().map_err(|error| Error::os_plain(&error))?.port();
    let url = format!("http://127.0.0.1:{port}");
    let mut announcement = Dict::new();
    announcement.insert("url", url.as_str());
    announcement.insert("workspace", editor.workspace.to_string_lossy().into_owned());
    crate::print_line(&mut std::io::stdout(), &json::dumps_ascii(&Value::Dict(announcement), false));
    if open_browser && !open_in_browser(&url) {
        crate::print_line(&mut std::io::stderr(), &format!("Could not open a browser; open {url} to use the editor."));
    }
    PORT.store(port, Ordering::SeqCst);
    install_interrupt_handler();
    let editor = Arc::new(Mutex::new(editor));
    for stream in listener.incoming() {
        if STOP.load(Ordering::SeqCst) {
            break;
        }
        let Ok(stream) = stream else { continue };
        let editor = Arc::clone(&editor);
        let _ = std::thread::Builder::new().spawn(move || {
            let _ = connection(stream, &editor, port);
        });
    }
    let mut closed = Dict::new();
    closed.insert("state", "editor-closed");
    Ok(Value::Dict(closed))
}

/// One request: read it, answer it, then close without resetting the
/// connection while the client may still be sending.
fn connection(stream: TcpStream, editor: &Mutex<Editor>, port: u16) -> std::io::Result<()> {
    stream.set_read_timeout(Some(IDLE))?;
    stream.set_write_timeout(Some(IDLE))?;
    let mut writer = stream.try_clone()?;
    let mut reader = BufReader::new(stream);
    if let Some(reply) = respond(&mut reader, editor, port)? {
        writer.write_all(&reply)?;
        writer.flush()?;
    }
    writer.shutdown(Shutdown::Write)?;
    let inner = reader.get_mut();
    inner.set_read_timeout(Some(Duration::from_secs(1)))?;
    let mut sink = [0u8; 8192];
    let mut drained = 0usize;
    while drained < 4 * MAX_BODY as usize {
        match inner.read(&mut sink) {
            Ok(0) | Err(_) => break,
            Ok(count) => drained += count,
        }
    }
    Ok(())
}

/// `rfile.readline(limit)`: bytes up to and including a newline.
fn read_line(reader: &mut impl BufRead, limit: usize) -> std::io::Result<Vec<u8>> {
    let mut line = Vec::new();
    reader.by_ref().take(limit as u64).read_until(b'\n', &mut line)?;
    Ok(line)
}

struct Request {
    command: String,
    path: String,
    /// The protocol of the request; Python's default is HTTP/0.9.
    version: String,
    headers: Vec<(String, String)>,
}

impl Request {
    /// `headers.get(name)`: the first header of that name.
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
    }
}

fn respond(reader: &mut BufReader<TcpStream>, editor: &Mutex<Editor>, port: u16) -> std::io::Result<Option<Vec<u8>>> {
    let raw = read_line(reader, MAX_LINE + 1)?;
    if raw.len() > MAX_LINE {
        return Ok(Some(error_reply(414, None, None, "", "")));
    }
    if raw.is_empty() {
        return Ok(None);
    }
    let mut request = match parse_request_line(&raw) {
        Ok(Some(request)) => request,
        Ok(None) => return Ok(None),
        Err(reply) => return Ok(Some(reply)),
    };
    match read_headers(reader)? {
        Ok(headers) => request.headers = headers,
        Err((message, explain)) => {
            return Ok(Some(error_reply(431, Some(message), Some(&explain), &request.version, &request.command)))
        }
    }
    let post = match request.command.as_str() {
        "GET" => false,
        "POST" => true,
        command => {
            let message = format!("Unsupported method ({})", py_repr(command));
            return Ok(Some(error_reply(501, Some(&message), None, &request.version, command)));
        }
    };
    dispatch(&request, reader, editor, port, post)
}

/// `BaseHTTPRequestHandler.parse_request` up to the headers: Ok(None) for
/// an empty line, Err with the error reply.
fn parse_request_line(raw: &[u8]) -> std::result::Result<Option<Request>, Vec<u8>> {
    let line = py::latin1(raw);
    let line = line.trim_end_matches(['\r', '\n']);
    let words = py::split_whitespace(line);
    if words.is_empty() {
        return Ok(None);
    }
    let mut version = "HTTP/0.9".to_string();
    if words.len() >= 3 {
        let candidate = words[words.len() - 1];
        let bad = || error_reply(400, Some(&format!("Bad request version ({})", py_repr(candidate))), None, "HTTP/0.9", "");
        let base = candidate.strip_prefix("HTTP/").ok_or_else(bad)?;
        let numbers: Vec<&str> = base.split('.').collect();
        if numbers.len() != 2
            || numbers.iter().any(|part| part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()) || part.len() > 10)
        {
            return Err(bad());
        }
        let major: u64 = numbers[0].parse().expect("ten digits at most");
        if major >= 2 {
            let message = format!("Invalid HTTP version ({base})");
            return Err(error_reply(505, Some(&message), None, "HTTP/0.9", ""));
        }
        version = candidate.to_string();
    }
    if !(2..=3).contains(&words.len()) {
        let message = format!("Bad request syntax ({})", py_repr(line));
        return Err(error_reply(400, Some(&message), None, &version, ""));
    }
    let command = words[0].to_string();
    let mut path = words[1].to_string();
    if words.len() == 2 && command != "GET" {
        // The handler has no command yet, so even HEAD gets the page.
        let message = format!("Bad HTTP/0.9 request type ({})", py_repr(&command));
        return Err(error_reply(400, Some(&message), None, &version, ""));
    }
    if path.starts_with("//") {
        path = format!("/{}", path.trim_start_matches('/'));
    }
    Ok(Some(Request { command, path, version, headers: Vec::new() }))
}

/// `http.client.parse_headers` and the email parser's header handling.
fn read_headers(reader: &mut impl BufRead) -> std::io::Result<std::result::Result<Vec<(String, String)>, (&'static str, String)>> {
    let mut block = Vec::new();
    let mut count = 0;
    loop {
        let line = read_line(reader, MAX_LINE + 1)?;
        if line.len() > MAX_LINE {
            return Ok(Err(("Line too long", format!("got more than {MAX_LINE} bytes when reading header line"))));
        }
        count += 1;
        if count > MAX_HEADERS {
            return Ok(Err(("Too many headers", format!("got more than {MAX_HEADERS} headers"))));
        }
        let end = matches!(line.as_slice(), b"\r\n" | b"\n" | b"");
        block.extend_from_slice(&line);
        if end {
            break;
        }
    }
    Ok(Ok(parse_header_block(&py::latin1(&block))))
}

/// Header fields as Python's compat32 email parser sees them.
fn parse_header_block(text: &str) -> Vec<(String, String)> {
    // Lines end at \r\n, \r or \n.
    let mut lines: Vec<&str> = Vec::new();
    let mut start = 0;
    let bytes = text.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'\r' if bytes.get(index + 1) == Some(&b'\n') => {
                lines.push(&text[start..index + 2]);
                index += 2;
                start = index;
            }
            b'\r' | b'\n' => {
                lines.push(&text[start..index + 1]);
                index += 1;
                start = index;
            }
            _ => index += 1,
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    let is_header = |line: &str| {
        line.starts_with("From ")
            || line.starts_with([' ', '\t'])
            || line.find(':').is_some_and(|colon| line[..colon].bytes().all(|b| (0x21..=0x7e).contains(&b)))
    };
    let mut fields: Vec<(String, String)> = Vec::new();
    let mut current: Option<(String, String)> = None;
    for line in lines {
        if !is_header(line) {
            break;
        }
        if line.starts_with([' ', '\t']) {
            if let Some((_, value)) = current.as_mut() {
                value.push_str(line);
            }
            continue;
        }
        if let Some(field) = current.take() {
            fields.push(field);
        }
        if line.starts_with("From ") {
            continue;
        }
        let colon = line.find(':').expect("matched a header line");
        if colon == 0 {
            continue;
        }
        let value = line[colon + 1..].trim_start_matches([' ', '\t']).to_string();
        current = Some((line[..colon].to_string(), value));
    }
    if let Some(field) = current.take() {
        fields.push(field);
    }
    for (_, value) in fields.iter_mut() {
        let trimmed = value.trim_end_matches(['\r', '\n']).len();
        value.truncate(trimmed);
    }
    fields
}

enum Answer {
    Static(&'static [u8], &'static str),
    Json(Value),
}

enum Failure {
    /// Answered with `{"ok": false, "error": ...}`.
    Reply(Error),
    /// The request body could not be read; the connection is dropped.
    Drop(std::io::Error),
}

impl From<Error> for Failure {
    fn from(error: Error) -> Self {
        Failure::Reply(error)
    }
}

fn dispatch(
    request: &Request,
    reader: &mut BufReader<TcpStream>,
    editor: &Mutex<Editor>,
    port: u16,
    post: bool,
) -> std::io::Result<Option<Vec<u8>>> {
    let address = format!("127.0.0.1:{port}");
    let answer = (|| -> std::result::Result<Answer, Failure> {
        if request.header("Host") != Some(address.as_str()) {
            return Err(Error::profile("Use the editor's local address").into());
        }
        let (path, query) = urlsplit(&request.path)?;
        if !post {
            if let Some((_, file, kind)) = STATIC.iter().find(|(route, _, _)| *route == path) {
                let data = cir_config::data::file(file).expect("the editor's files are embedded");
                return Ok(Answer::Static(data, kind));
            }
        }
        let value = if post {
            let origin = format!("http://{address}");
            let local_origin = request.header("Origin").map_or(true, |value| value == origin);
            if content_type(request) != "application/json" || !local_origin {
                return Err(Error::profile("Use the local editor to change a configuration").into());
            }
            let declared = request.header("Content-Length").unwrap_or("0");
            let length = py::int_value(declared).map_err(Error::value)?;
            let length = match length {
                Value::Int(length) if 0 < length && length <= MAX_BODY => length as u64,
                _ => return Err(Error::profile("Invalid configuration request size").into()),
            };
            let mut body = Vec::new();
            reader.by_ref().take(length).read_to_end(&mut body).map_err(Failure::Drop)?;
            let text = std::str::from_utf8(&body).map_err(|error| Error::value(json::utf8_error_message(&body, &error)))?;
            let data = json::parse_lenient(text)?;
            if !data.is_dict() {
                return Err(Error::profile("Expected a configuration object").into());
            }
            lock(editor).post(&path, &data)?
        } else {
            let params = parse_qs(&query);
            lock(editor).get(&path, &params)?
        };
        Ok(Answer::Json(value))
    })();
    Ok(match answer {
        Ok(Answer::Static(data, kind)) => Some(reply(200, data, kind, &request.version)),
        Ok(Answer::Json(value)) => {
            let mut result = Dict::new();
            result.insert("ok", true);
            result.insert("result", value);
            Some(reply(200, json::dumps_ascii(&Value::Dict(result), false).as_bytes(), "application/json", &request.version))
        }
        // The client stopped sending its request; there is nobody to answer.
        Err(Failure::Drop(error)) => return Err(error),
        // The reference answers ProfileError, OSError, ValueError, KeyError
        // and TypeError; any other error escapes its handler, and the server
        // closes the connection without an answer.
        Err(Failure::Reply(error)) if matches!(error.kind, ErrorKind::Attribute | ErrorKind::Overflow) => {
            eprintln!("Exception occurred during processing of request: {}", error.message);
            None
        }
        Err(Failure::Reply(error)) => {
            let mut result = Dict::new();
            result.insert("ok", false);
            result.insert("error", error.message);
            Some(reply(400, json::dumps_ascii(&Value::Dict(result), false).as_bytes(), "application/json", &request.version))
        }
    })
}

fn lock(editor: &Mutex<Editor>) -> std::sync::MutexGuard<'_, Editor> {
    editor.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// `Message.get_content_type()`.
fn content_type(request: &Request) -> String {
    let Some(value) = request.header("Content-Type") else {
        return "text/plain".to_string();
    };
    let main = value.split(';').next().unwrap_or("");
    let kind = py::strip(&py::lower(py::strip(main))).to_string();
    if kind.matches('/').count() != 1 {
        return "text/plain".to_string();
    }
    kind
}

/// `urllib.parse.urlsplit(url)`: the path and query of a request target.
fn urlsplit(url: &str) -> Result<(String, String)> {
    let mut url: String =
        url.trim_start_matches(|c: char| c <= ' ').chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    if let Some(colon) = url.find(':') {
        let scheme = &url[..colon];
        if colon > 0
            && scheme.starts_with(|c: char| c.is_ascii_alphabetic())
            && scheme.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        {
            url = url[colon + 1..].to_string();
        }
    }
    if url.starts_with("//") {
        let end = url[2..].find(['/', '?', '#']).map_or(url.len(), |index| index + 2);
        let netloc = url[2..end].to_string();
        url = url[end..].to_string();
        if netloc.contains('[') != netloc.contains(']') {
            return Err(Error::value("Invalid IPv6 URL"));
        }
        if netloc.contains('[') {
            let host = netloc.split_once('[').map_or("", |(_, rest)| rest.split(']').next().unwrap_or(""));
            check_bracketed_host(host)?;
        }
    }
    if let Some(hash) = url.find('#') {
        url.truncate(hash);
    }
    Ok(match url.split_once('?') {
        Some((path, query)) => (path.to_string(), query.to_string()),
        None => (url, String::new()),
    })
}

/// `urllib.parse._check_bracketed_host`.
fn check_bracketed_host(host: &str) -> Result<()> {
    if let Some(rest) = host.strip_prefix('v') {
        let valid = rest.split_once('.').is_some_and(|(hex, tail)| {
            !hex.is_empty() && hex.bytes().all(|b| b.is_ascii_hexdigit()) && !tail.is_empty() && !tail.contains('\n')
        });
        return if valid { Ok(()) } else { Err(Error::value("IPvFuture address is invalid")) };
    }
    let address = host.split_once('%').map_or(host, |(address, _)| address);
    if address.parse::<std::net::Ipv4Addr>().is_ok() && !host.contains('%') {
        return Err(Error::value("An IPv4 address cannot be in brackets"));
    }
    if address.parse::<std::net::Ipv6Addr>().is_ok() {
        return Ok(());
    }
    Err(Error::value(format!("{} does not appear to be an IPv4 or IPv6 address", py_repr(host))))
}

/// `{key: values[0] for key, values in parse_qs(query).items()}`.
fn parse_qs(query: &str) -> HashMap<String, String> {
    let mut params = HashMap::new();
    if query.is_empty() {
        return params;
    }
    for pair in query.split('&') {
        let Some((name, value)) = pair.split_once('=') else { continue };
        if value.is_empty() {
            continue;
        }
        params.entry(unquote(&name.replace('+', " "))).or_insert_with(|| unquote(&value.replace('+', " ")));
    }
    params
}

/// `urllib.parse.unquote`: %XX escapes in ASCII runs decode as UTF-8 with
/// replacement characters; other characters stay as they are.
fn unquote(text: &str) -> String {
    if !text.contains('%') {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut pending: Vec<u8> = Vec::new();
    let chars: Vec<char> = text.chars().collect();
    let mut index = 0;
    let flush = |pending: &mut Vec<u8>, out: &mut String| {
        out.push_str(&String::from_utf8_lossy(pending));
        pending.clear();
    };
    while index < chars.len() {
        let c = chars[index];
        if !c.is_ascii() {
            flush(&mut pending, &mut out);
            out.push(c);
            index += 1;
            continue;
        }
        if c == '%' && index + 2 < chars.len() && chars[index + 1].is_ascii_hexdigit() && chars[index + 2].is_ascii_hexdigit() {
            let hex: String = chars[index + 1..index + 3].iter().collect();
            pending.push(u8::from_str_radix(&hex, 16).expect("hex digits"));
            index += 3;
            continue;
        }
        pending.push(c as u8);
        index += 1;
    }
    flush(&mut pending, &mut out);
    out
}

fn py_repr(text: &str) -> String {
    cir_config::error::py_repr_str(text)
}

fn status_text(code: u16) -> (&'static str, &'static str) {
    match code {
        200 => ("OK", "Request fulfilled, document follows"),
        400 => ("Bad Request", "Bad request syntax or unsupported method"),
        414 => ("Request-URI Too Long", "URI is too long"),
        431 => (
            "Request Header Fields Too Large",
            "The server is unwilling to process the request because its header fields are too large",
        ),
        501 => ("Not Implemented", "Server does not support this operation"),
        505 => ("HTTP Version Not Supported", "Cannot fulfill request"),
        _ => ("???", "???"),
    }
}

/// The status line and common headers; HTTP/0.9 requests get neither.
fn head(code: u16, message: &str, version: &str) -> String {
    if version == "HTTP/0.9" {
        return String::new();
    }
    format!(
        "HTTP/1.0 {code} {message}\r\nServer: cir-configurator/{}\r\nDate: {}\r\n",
        env!("CARGO_PKG_VERSION"),
        http_date(SystemTime::now())
    )
}

fn reply(code: u16, body: &[u8], kind: &str, version: &str) -> Vec<u8> {
    let mut data = head(code, status_text(code).0, version).into_bytes();
    if version != "HTTP/0.9" {
        data.extend_from_slice(
            format!(
                "Content-Type: {kind}; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\n\r\n",
                body.len()
            )
            .as_bytes(),
        );
    }
    data.extend_from_slice(body);
    data
}

/// `send_error`: an HTML error page.
fn error_reply(code: u16, message: Option<&str>, explain: Option<&str>, version: &str, command: &str) -> Vec<u8> {
    let (short, long) = status_text(code);
    let message = message.unwrap_or(short);
    let explain = explain.unwrap_or(long);
    let escape = |text: &str| text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    let page = ERROR_PAGE
        .replace("{code}", &code.to_string())
        .replace("{message}", &escape(message))
        .replace("{explain}", &escape(explain));
    let mut data = head(code, message, version).into_bytes();
    if version != "HTTP/0.9" {
        data.extend_from_slice(
            format!("Connection: close\r\nContent-Type: text/html;charset=utf-8\r\nContent-Length: {}\r\n\r\n", page.len())
                .as_bytes(),
        );
    }
    if command != "HEAD" {
        data.extend_from_slice(page.as_bytes());
    }
    data
}

/// An RFC 7231 date (`email.utils.formatdate(usegmt=True)`).
fn http_date(time: SystemTime) -> String {
    let seconds = time.duration_since(UNIX_EPOCH).map_or(0, |elapsed| elapsed.as_secs());
    let days = (seconds / 86_400) as i64;
    let rest = seconds % 86_400;
    const WEEKDAYS: [&str; 7] = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"];
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = z - era * 146_097;
    let year_of_era = (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * shifted_month + 2) / 5 + 1;
    let month = if shifted_month < 10 { shifted_month + 3 } else { shifted_month - 9 };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    format!(
        "{}, {day:02} {} {year} {:02}:{:02}:{:02} GMT",
        WEEKDAYS[days.rem_euclid(7) as usize],
        MONTHS[(month - 1) as usize],
        rest / 3600,
        rest / 60 % 60,
        rest % 60
    )
}

#[cfg(windows)]
fn open_in_browser(url: &str) -> bool {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "shell32")]
    extern "system" {
        fn ShellExecuteW(
            window: *mut std::ffi::c_void,
            operation: *const u16,
            file: *const u16,
            parameters: *const u16,
            directory: *const u16,
            show: i32,
        ) -> isize;
    }
    let wide = |text: &str| std::ffi::OsStr::new(text).encode_wide().chain(Some(0)).collect::<Vec<u16>>();
    let (operation, file) = (wide("open"), wide(url));
    const SW_SHOWNORMAL: i32 = 1;
    // SAFETY: both strings are NUL-terminated and outlive the call.
    let result = unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            operation.as_ptr(),
            file.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            SW_SHOWNORMAL,
        )
    };
    result > 32
}

#[cfg(not(windows))]
fn open_in_browser(url: &str) -> bool {
    let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
    std::process::Command::new(opener).arg(url).spawn().is_ok()
}

/// Ctrl+C or Ctrl+Break closes the editor like the reference's
/// KeyboardInterrupt: the server stops and reports its closed state.
#[cfg(windows)]
fn install_interrupt_handler() {
    type Handler = unsafe extern "system" fn(u32) -> i32;
    #[link(name = "kernel32")]
    extern "system" {
        fn SetConsoleCtrlHandler(handler: Option<Handler>, add: i32) -> i32;
    }
    unsafe extern "system" fn on_control(event: u32) -> i32 {
        const CTRL_BREAK_EVENT: u32 = 1;
        if event > CTRL_BREAK_EVENT {
            return 0;
        }
        STOP.store(true, Ordering::SeqCst);
        // Wake the accepting loop so it sees the request to stop.
        let _ = TcpStream::connect(("127.0.0.1", PORT.load(Ordering::SeqCst)));
        1
    }
    // SAFETY: the handler is a plain function that lives for the program.
    unsafe {
        SetConsoleCtrlHandler(Some(on_control), 1);
    }
}

#[cfg(not(windows))]
fn install_interrupt_handler() {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates_match_python() {
        // email.utils.formatdate(0, usegmt=True) and a leap day.
        assert_eq!(http_date(UNIX_EPOCH), "Thu, 01 Jan 1970 00:00:00 GMT");
        assert_eq!(http_date(UNIX_EPOCH + Duration::from_secs(951_782_400)), "Tue, 29 Feb 2000 00:00:00 GMT");
        assert_eq!(http_date(UNIX_EPOCH + Duration::from_secs(1_792_281_599)), "Sat, 17 Oct 2026 23:59:59 GMT");
    }

    #[test]
    fn query_strings_match_parse_qs() {
        let params = parse_qs("q=a+b%20c&q=second&empty=&flag&offset=%ZZ&x=%e2%82&y=%C3%A9");
        assert_eq!(params["q"], "a b c");
        assert!(!params.contains_key("empty") && !params.contains_key("flag"));
        assert_eq!(params["offset"], "%ZZ");
        assert_eq!(params["x"], "\u{fffd}");
        assert_eq!(params["y"], "\u{e9}");
    }

    #[test]
    fn targets_split_like_urlsplit() {
        assert_eq!(urlsplit("/api/state?x=1#top").unwrap(), ("/api/state".into(), "x=1".into()));
        assert_eq!(urlsplit("http://127.0.0.1:5/api/items?q=a").unwrap(), ("/api/items".into(), "q=a".into()));
        assert_eq!(urlsplit("//x/y").unwrap().0, "/y");
        assert_eq!(urlsplit("http://[::1/x").unwrap_err().message, "Invalid IPv6 URL");
    }

    #[test]
    fn headers_follow_the_email_parser() {
        let fields = parse_header_block("Host: a \r\nX-Long: one\r\n two\r\nBad line\r\nAfter: no\r\n\r\n");
        assert_eq!(fields[0], ("Host".into(), "a ".into()));
        assert_eq!(fields[1], ("X-Long".into(), "one\r\n two".into()));
        assert_eq!(fields.len(), 2);
    }
}
