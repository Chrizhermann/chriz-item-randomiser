//! The reference CLI's argument handling, as Python 3.11's argparse does it
//! for the parsers the reference builds: options with one value, repeated
//! options, flags, `-h`/`--help`, unique-prefix abbreviations, `--`, nested
//! subcommands, argparse's error messages, and the usage and help text
//! wrapped to the terminal width. Usage errors exit with status 2, help
//! with status 0.

use cir_config::error::py_repr_str;
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::PathBuf;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Text,
    Int,
    Path,
    /// `action="append"`: every occurrence adds a value.
    Append,
    /// `action="store_true"`.
    Flag,
}

/// One `--name` option.
pub struct Spec {
    pub name: &'static str,
    pub kind: Kind,
    pub required: bool,
    pub choices: Vec<String>,
    pub help: Option<&'static str>,
}

impl Spec {
    pub fn new(name: &'static str, kind: Kind) -> Spec {
        Spec { name, kind, required: false, choices: Vec::new(), help: None }
    }
    pub fn required(mut self) -> Spec {
        self.required = true;
        self
    }
    pub fn choices<I: IntoIterator<Item = S>, S: Into<String>>(mut self, choices: I) -> Spec {
        self.choices = choices.into_iter().map(Into::into).collect();
        self
    }
    pub fn help(mut self, help: &'static str) -> Spec {
        self.help = Some(help);
        self
    }
    fn option_string(&self) -> String {
        format!("--{}", self.name)
    }
    /// The default metavar: the choices, or the destination in capitals.
    fn metavar(&self) -> String {
        if self.choices.is_empty() {
            self.name.replace('-', "_").to_uppercase()
        } else {
            format!("{{{}}}", self.choices.join(","))
        }
    }
}

/// The positional that chooses the next parser (`add_subparsers`).
pub struct Commands {
    pub dest: &'static str,
    /// Each choice with the help that lists it (choices without help are not
    /// listed in the help text, as in argparse).
    pub choices: Vec<(&'static str, Option<&'static str>)>,
}

impl Commands {
    fn metavar(&self) -> String {
        let names: Vec<&str> = self.choices.iter().map(|(name, _)| *name).collect();
        format!("{{{}}}", names.join(","))
    }
}

/// One parser: the options of a command, or a level of subcommands.
pub struct Parser {
    pub prog: String,
    pub description: Option<&'static str>,
    pub options: Vec<Spec>,
    pub commands: Option<Commands>,
}

/// Parsed option values by option name (without dashes).
#[derive(Default)]
pub struct Parsed {
    values: HashMap<&'static str, Vec<String>>,
}

impl Parsed {
    pub fn text(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(|values| values.last()).map(String::as_str)
    }
    pub fn path(&self, name: &str) -> Option<PathBuf> {
        self.text(name).map(|value| PathBuf::from(cir_config::paths::display(std::path::Path::new(value))))
    }
    /// An integer option limited to the i64 range (larger values cannot
    /// change any result except where `int_value` echoes them).
    pub fn int(&self, name: &str) -> Option<i64> {
        self.text(name).map(|value| cir_config::py::int_saturating(value).expect("validated while parsing"))
    }
    /// An integer option of any size, as Python's `int` keeps it.
    pub fn int_value(&self, name: &str) -> Option<cir_config::Value> {
        self.text(name).map(|value| cir_config::py::int_value(value).expect("validated while parsing"))
    }
    pub fn all(&self, name: &str) -> Vec<String> {
        self.values.get(name).cloned().unwrap_or_default()
    }
    pub fn flag(&self, name: &str) -> bool {
        self.values.contains_key(name)
    }
}

/// An action of a parser, in argparse's order: help, the options, the
/// subcommand positional.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Target {
    Help,
    Option(usize),
    Commands,
}

/// What `_parse_optional` makes of an argument that looks like an option:
/// the action, the option string, the separator ("=", or "" for `-xVALUE`)
/// and the explicit argument.
#[derive(Clone)]
struct OptionTuple {
    target: Option<Target>,
    option_string: String,
    sep: Option<String>,
    explicit: Option<String>,
}

const HELP_TEXT: &str = "show this help message and exit";

impl Parser {
    /// `_option_string_actions` in insertion order.
    fn option_strings(&self) -> Vec<(String, Target)> {
        let mut strings = vec![("-h".to_string(), Target::Help), ("--help".to_string(), Target::Help)];
        for (index, spec) in self.options.iter().enumerate() {
            strings.push((spec.option_string(), Target::Option(index)));
        }
        strings
    }

    fn actions(&self) -> Vec<Target> {
        let mut actions = vec![Target::Help];
        actions.extend((0..self.options.len()).map(Target::Option));
        if self.commands.is_some() {
            actions.push(Target::Commands);
        }
        actions
    }

    fn exact(&self, option_string: &str) -> Option<Target> {
        self.option_strings().into_iter().find(|(string, _)| string == option_string).map(|(_, target)| target)
    }

    /// `_get_action_name`: how errors name an action.
    fn action_name(&self, target: Target) -> String {
        match target {
            Target::Help => "-h/--help".to_string(),
            Target::Option(index) => self.options[index].option_string(),
            Target::Commands => self.commands.as_ref().map_or("", |commands| commands.dest).to_string(),
        }
    }

    fn takes_value(&self, target: Target) -> bool {
        match target {
            Target::Help => false,
            Target::Option(index) => self.options[index].kind != Kind::Flag,
            Target::Commands => true,
        }
    }

    /// `_parse_optional`: None for a positional argument.
    fn parse_optional(&self, argument: &str) -> Result<Option<OptionTuple>, String> {
        let tuple = |target: Option<Target>, option_string: &str, sep: Option<&str>, explicit: Option<&str>| {
            Ok(Some(OptionTuple {
                target,
                option_string: option_string.to_string(),
                sep: sep.map(str::to_string),
                explicit: explicit.map(str::to_string),
            }))
        };
        if !argument.starts_with('-') {
            return Ok(None);
        }
        if let Some(target) = self.exact(argument) {
            return tuple(Some(target), argument, None, None);
        }
        if argument.chars().count() == 1 {
            return Ok(None);
        }
        if let Some((option_string, explicit)) = argument.split_once('=') {
            if let Some(target) = self.exact(option_string) {
                return tuple(Some(target), option_string, Some("="), Some(explicit));
            }
        }
        let tuples = self.option_tuples(argument);
        if tuples.len() > 1 {
            let matches: Vec<&str> = tuples.iter().map(|tuple| tuple.option_string.as_str()).collect();
            return Err(format!("ambiguous option: {argument} could match {}", matches.join(", ")));
        }
        if let Some(only) = tuples.into_iter().next() {
            return Ok(Some(only));
        }
        if is_negative_number(argument) || argument.contains(' ') {
            return Ok(None);
        }
        tuple(None, argument, None, None)
    }

    /// `_get_option_tuples`: the options an abbreviation could mean.
    fn option_tuples(&self, argument: &str) -> Vec<OptionTuple> {
        let mut tuples = Vec::new();
        let second = argument.chars().nth(1);
        if second == Some('-') {
            let (prefix, sep, explicit) = match argument.split_once('=') {
                Some((prefix, explicit)) => (prefix, Some("=".to_string()), Some(explicit.to_string())),
                None => (argument, None, None),
            };
            for (option_string, target) in self.option_strings() {
                if option_string.starts_with(prefix) {
                    tuples.push(OptionTuple {
                        target: Some(target),
                        option_string,
                        sep: sep.clone(),
                        explicit: explicit.clone(),
                    });
                }
            }
        } else {
            let split = argument.char_indices().nth(2).map_or(argument.len(), |(index, _)| index);
            let (short, short_explicit) = argument.split_at(split);
            for (option_string, target) in self.option_strings() {
                if option_string == short {
                    tuples.push(OptionTuple {
                        target: Some(target),
                        option_string,
                        sep: Some(String::new()),
                        explicit: Some(short_explicit.to_string()),
                    });
                } else if option_string.starts_with(argument) {
                    tuples.push(OptionTuple { target: Some(target), option_string, sep: None, explicit: None });
                }
            }
        }
        tuples
    }
}

/// argparse's `^-\d+$|^-\d*\.\d+$` (Unicode decimal digits; `$` also
/// matches before a final newline).
fn is_negative_number(argument: &str) -> bool {
    let Some(body) = argument.strip_prefix('-') else { return false };
    let body = body.strip_suffix('\n').unwrap_or(body);
    let digit = cir_config::py::is_decimal;
    if !body.is_empty() && body.chars().all(digit) {
        return true;
    }
    match body.split_once('.') {
        Some((whole, fraction)) => {
            whole.chars().all(digit) && !fraction.is_empty() && fraction.chars().all(digit)
        }
        None => false,
    }
}

/// A finished parse: the subcommand path and the chosen parser's options.
pub struct Outcome {
    pub path: Vec<String>,
    pub parsed: Parsed,
}

/// Parse the arguments with the parser tree `parser_for` describes (the top
/// parser for an empty path). `Err` holds the exit status after help or a
/// usage error has been printed.
pub fn parse(arguments: &[String], parser_for: &dyn Fn(&[String]) -> Parser) -> Result<Outcome, u8> {
    let top = parser_for(&[]);
    let mut path = Vec::new();
    let mut extras = Vec::new();
    let parsed = parse_level(&top, arguments, &mut path, parser_for, &mut extras)?;
    if !extras.is_empty() {
        return Err(usage_error(&top, &format!("unrecognized arguments: {}", extras.join(" "))));
    }
    Ok(Outcome { path, parsed })
}

/// `_parse_known_args` of one parser; arguments it does not know go to
/// `extras` for the top parser to report.
fn parse_level(
    parser: &Parser,
    arguments: &[String],
    path: &mut Vec<String>,
    parser_for: &dyn Fn(&[String]) -> Parser,
    extras: &mut Vec<String>,
) -> Result<Parsed, u8> {
    // Classify: 'O' looks like an option, 'A' is an argument, '-' is `--`.
    let mut pattern = Vec::with_capacity(arguments.len());
    let mut optionals: HashMap<usize, OptionTuple> = HashMap::new();
    let mut after_dashes = false;
    for (index, argument) in arguments.iter().enumerate() {
        if after_dashes {
            pattern.push('A');
        } else if argument == "--" {
            pattern.push('-');
            after_dashes = true;
        } else {
            match parser.parse_optional(argument) {
                Err(message) => return Err(usage_error(parser, &message)),
                Ok(None) => pattern.push('A'),
                Ok(Some(tuple)) => {
                    optionals.insert(index, tuple);
                    pattern.push('O');
                }
            }
        }
    }

    let mut state = Level { parser, arguments, pattern, seen: HashSet::new(), parsed: Parsed::default(), leaf: None };
    let mut own_extras = Vec::new();
    let mut sub_extras = Vec::new();
    let mut start = 0;
    if let Some(&last) = optionals.keys().max() {
        while start <= last {
            let next = *optionals.keys().filter(|&&index| index >= start).min().expect("an option at or after start");
            if start != next {
                let end = state.consume_positionals(start, path, parser_for, &mut sub_extras)?;
                if end > start {
                    start = end;
                    continue;
                }
            }
            if !optionals.contains_key(&start) {
                own_extras.extend(arguments[start..next].iter().cloned());
                start = next;
            }
            start = state.consume_optional(start, &optionals[&start], &mut own_extras)?;
        }
    }
    let stop = state.consume_positionals(start, path, parser_for, &mut sub_extras)?;
    own_extras.extend(arguments[stop..].iter().cloned());

    let missing: Vec<String> = parser
        .actions()
        .into_iter()
        .filter(|target| !state.seen.contains(target))
        .filter(|target| match target {
            Target::Help => false,
            Target::Option(index) => parser.options[*index].required,
            Target::Commands => true,
        })
        .map(|target| parser.action_name(target))
        .collect();
    if !missing.is_empty() {
        return Err(usage_error(parser, &format!("the following arguments are required: {}", missing.join(", "))));
    }
    extras.extend(own_extras);
    extras.extend(sub_extras);
    Ok(state.leaf.unwrap_or(state.parsed))
}

struct Level<'a> {
    parser: &'a Parser,
    arguments: &'a [String],
    pattern: Vec<char>,
    seen: HashSet<Target>,
    parsed: Parsed,
    /// The options of the subcommand's parser, once it has run.
    leaf: Option<Parsed>,
}

impl Level<'_> {
    /// `consume_positionals`: the only positional is the subcommand, which
    /// takes `-*A[-AO]*`, everything from its name on.
    fn consume_positionals(
        &mut self,
        start: usize,
        path: &mut Vec<String>,
        parser_for: &dyn Fn(&[String]) -> Parser,
        extras: &mut Vec<String>,
    ) -> Result<usize, u8> {
        let Some(commands) = &self.parser.commands else { return Ok(start) };
        if self.seen.contains(&Target::Commands) {
            return Ok(start);
        }
        let rest = &self.pattern[start..];
        let dashes = rest.iter().take_while(|&&kind| kind == '-').count();
        if rest.get(dashes) != Some(&'A') {
            return Ok(start);
        }
        self.seen.insert(Target::Commands);
        // PARSER arguments keep `--`; the first one names the command.
        let values = &self.arguments[start..];
        let name = &values[0];
        if !commands.choices.iter().any(|(choice, _)| choice == name) {
            let choices: Vec<String> = commands.choices.iter().map(|(choice, _)| py_repr_str(choice)).collect();
            let message =
                format!("argument {}: invalid choice: {} (choose from {})", commands.dest, py_repr_str(name), choices.join(", "));
            return Err(usage_error(self.parser, &message));
        }
        path.push(name.clone());
        let child = parser_for(path);
        self.leaf = Some(parse_level(&child, &values[1..], path, parser_for, extras)?);
        Ok(self.arguments.len())
    }

    /// `consume_optional`, including `-xyz` for options without values.
    fn consume_optional(&mut self, start: usize, tuple: &OptionTuple, extras: &mut Vec<String>) -> Result<usize, u8> {
        let parser = self.parser;
        let mut target = tuple.target;
        let mut option_string = tuple.option_string.clone();
        let mut sep = tuple.sep.clone();
        let mut explicit = tuple.explicit.clone();
        let mut taken: Vec<(Target, Vec<String>)> = Vec::new();
        let stop;
        loop {
            let Some(current) = target else {
                extras.push(self.arguments[start].clone());
                return Ok(start + 1);
            };
            let takes_value = parser.takes_value(current);
            match explicit.clone() {
                Some(argument) => {
                    let single_dash = option_string.chars().nth(1) != Some('-');
                    if !takes_value && single_dash && !argument.is_empty() {
                        let first = argument.chars().next().expect("not empty");
                        if sep.as_deref().is_some_and(|sep| !sep.is_empty()) || first == '-' {
                            return Err(argument_error(
                                parser,
                                current,
                                &format!("ignored explicit argument {}", py_repr_str(&argument)),
                            ));
                        }
                        taken.push((current, Vec::new()));
                        option_string = format!("-{first}");
                        let remainder = &argument[first.len_utf8()..];
                        match parser.exact(&option_string) {
                            Some(next) => {
                                target = Some(next);
                                if remainder.is_empty() {
                                    sep = None;
                                    explicit = None;
                                } else if let Some(value) = remainder.strip_prefix('=') {
                                    sep = Some("=".to_string());
                                    explicit = Some(value.to_string());
                                } else {
                                    sep = Some(String::new());
                                    explicit = Some(remainder.to_string());
                                }
                            }
                            None => {
                                // An unknown short option: the rest is left
                                // for the top parser to report.
                                extras.push(format!("-{argument}"));
                                stop = start + 1;
                                break;
                            }
                        }
                    } else if takes_value {
                        stop = start + 1;
                        taken.push((current, vec![argument]));
                        break;
                    } else {
                        return Err(argument_error(
                            parser,
                            current,
                            &format!("ignored explicit argument {}", py_repr_str(&argument)),
                        ));
                    }
                }
                None => {
                    let begin = start + 1;
                    let count = if !takes_value {
                        0
                    } else if self.pattern.get(begin) == Some(&'A') {
                        1
                    } else {
                        return Err(argument_error(parser, current, "expected one argument"));
                    };
                    stop = begin + count;
                    taken.push((current, self.arguments[begin..stop].to_vec()));
                    break;
                }
            }
        }
        for (target, values) in taken {
            self.take_action(target, values)?;
        }
        Ok(stop)
    }

    fn take_action(&mut self, target: Target, values: Vec<String>) -> Result<(), u8> {
        self.seen.insert(target);
        let parser = self.parser;
        match target {
            Target::Help => {
                write_text(&mut std::io::stdout(), &format_help(parser));
                Err(0)
            }
            Target::Commands => unreachable!("the subcommand is a positional"),
            Target::Option(index) => {
                let spec = &parser.options[index];
                if spec.kind == Kind::Flag {
                    self.parsed.values.insert(spec.name, vec![String::new()]);
                    return Ok(());
                }
                let value = values.into_iter().next().expect("one value");
                if spec.kind == Kind::Int && cir_config::py::int_value(&value).is_err() {
                    return Err(argument_error(parser, target, &format!("invalid int value: {}", py_repr_str(&value))));
                }
                if !spec.choices.is_empty() && !spec.choices.contains(&value) {
                    let choices: Vec<String> = spec.choices.iter().map(|choice| py_repr_str(choice)).collect();
                    let message = format!("invalid choice: {} (choose from {})", py_repr_str(&value), choices.join(", "));
                    return Err(argument_error(parser, target, &message));
                }
                let entry = self.parsed.values.entry(spec.name).or_default();
                if spec.kind != Kind::Append {
                    entry.clear();
                }
                entry.push(value);
                Ok(())
            }
        }
    }
}

fn argument_error(parser: &Parser, target: Target, message: &str) -> u8 {
    usage_error(parser, &format!("argument {}: {message}", parser.action_name(target)))
}

/// `ArgumentParser.error`: the usage, then the message, status 2.
fn usage_error(parser: &Parser, message: &str) -> u8 {
    let mut stderr = std::io::stderr();
    write_text(&mut stderr, &format_usage(parser));
    write_text(&mut stderr, &format!("{}: error: {message}\n", parser.prog));
    2
}

/// Write text the way Python's text streams do on this platform.
pub fn write_text(stream: &mut dyn Write, text: &str) {
    let text = if cfg!(windows) { text.replace('\n', "\r\n") } else { text.to_string() };
    let _ = stream.write_all(text.as_bytes());
    let _ = stream.flush();
}

// ---------------------------------------------------------------------------
// Help and usage text (argparse.HelpFormatter).

/// `shutil.get_terminal_size().columns`: $COLUMNS, else the console behind
/// standard output, else 80.
fn terminal_columns() -> i64 {
    let from_environment = std::env::var("COLUMNS").ok().and_then(|value| cir_config::py::int_saturating(&value).ok());
    match from_environment {
        Some(columns) if columns > 0 => columns,
        _ => console_columns().filter(|&columns| columns > 0).unwrap_or(80),
    }
}

#[cfg(windows)]
fn console_columns() -> Option<i64> {
    use std::ffi::c_void;
    #[repr(C)]
    #[derive(Default)]
    struct Coord {
        x: i16,
        y: i16,
    }
    #[repr(C)]
    #[derive(Default)]
    struct SmallRect {
        left: i16,
        top: i16,
        right: i16,
        bottom: i16,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ScreenBufferInfo {
        size: Coord,
        cursor: Coord,
        attributes: u16,
        window: SmallRect,
        maximum: Coord,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GetStdHandle(handle: u32) -> *mut c_void;
        fn GetConsoleScreenBufferInfo(console: *mut c_void, info: *mut ScreenBufferInfo) -> i32;
    }
    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    let mut info = ScreenBufferInfo::default();
    // SAFETY: GetStdHandle has no preconditions; GetConsoleScreenBufferInfo
    // fills one CONSOLE_SCREEN_BUFFER_INFO (the layout above) and fails for
    // handles that are not a console, such as pipes and files.
    let ok = unsafe {
        let handle = GetStdHandle(STD_OUTPUT_HANDLE);
        !handle.is_null() && handle as isize != -1 && GetConsoleScreenBufferInfo(handle, &mut info) != 0
    };
    ok.then(|| i64::from(info.window.right) - i64::from(info.window.left) + 1)
}

/// Other platforms honour $COLUMNS and otherwise use 80 columns.
#[cfg(not(windows))]
fn console_columns() -> Option<i64> {
    None
}

fn width() -> i64 {
    static WIDTH: std::sync::OnceLock<i64> = std::sync::OnceLock::new();
    *WIDTH.get_or_init(|| terminal_columns().saturating_sub(2))
}

fn length(text: &str) -> i64 {
    text.chars().count() as i64
}

fn spaces(count: i64) -> String {
    " ".repeat(count.unsigned_abs() as usize)
}

/// An entry of the help text: an action, or a subcommand's line.
#[derive(Clone, Copy)]
enum Entry<'a> {
    Target(Target),
    Choice(&'a str, &'a str),
}

struct Formatter<'a> {
    parser: &'a Parser,
    width: i64,
    max_help_position: i64,
    action_max_length: i64,
}

impl<'a> Formatter<'a> {
    fn new(parser: &'a Parser) -> Formatter<'a> {
        let width = width();
        Formatter { parser, width, max_help_position: 24.min((width - 20).max(4)), action_max_length: 0 }
    }

    fn help(&self, entry: Entry<'a>) -> Option<&'a str> {
        match entry {
            Entry::Target(Target::Help) => Some(HELP_TEXT),
            Entry::Target(Target::Option(index)) => self.parser.options[index].help,
            Entry::Target(Target::Commands) => None,
            Entry::Choice(_, help) => Some(help),
        }
    }

    fn subentries(&self, entry: Entry<'a>) -> Vec<Entry<'a>> {
        match (entry, &self.parser.commands) {
            (Entry::Target(Target::Commands), Some(commands)) => commands
                .choices
                .iter()
                .filter_map(|(name, help)| help.map(|help| Entry::Choice(name, help)))
                .collect(),
            _ => Vec::new(),
        }
    }

    /// `_format_action_invocation`.
    fn invocation(&self, entry: Entry<'a>) -> String {
        match entry {
            Entry::Target(Target::Help) => "-h, --help".to_string(),
            Entry::Target(Target::Option(index)) => {
                let spec = &self.parser.options[index];
                if spec.kind == Kind::Flag {
                    spec.option_string()
                } else {
                    format!("{} {}", spec.option_string(), spec.metavar())
                }
            }
            Entry::Target(Target::Commands) => self.parser.commands.as_ref().map(Commands::metavar).unwrap_or_default(),
            Entry::Choice(name, _) => name.to_string(),
        }
    }

    /// `_format_actions_usage` for optionals or positionals.
    fn actions_usage(&self, targets: &[Target]) -> String {
        let parts: Vec<String> = targets
            .iter()
            .map(|target| match *target {
                Target::Help => "[-h]".to_string(),
                Target::Option(index) => {
                    let spec = &self.parser.options[index];
                    let part = if spec.kind == Kind::Flag {
                        spec.option_string()
                    } else {
                        format!("{} {}", spec.option_string(), spec.metavar())
                    };
                    if spec.required {
                        part
                    } else {
                        format!("[{part}]")
                    }
                }
                Target::Commands => format!("{} ...", self.parser.commands.as_ref().map(Commands::metavar).unwrap_or_default()),
            })
            .collect();
        clean_separators(&parts.join(" "))
    }

    /// `_format_usage` with the "usage: " prefix, ending in two newlines.
    fn usage(&self) -> String {
        let prefix = "usage: ";
        let prog = self.parser.prog.as_str();
        let actions = self.parser.actions();
        let optionals: Vec<Target> = actions.iter().copied().filter(|target| *target != Target::Commands).collect();
        let positionals: Vec<Target> = actions.iter().copied().filter(|target| *target == Target::Commands).collect();
        let action_usage = self.actions_usage(&actions);
        let mut usage = [prog, action_usage.as_str()].iter().filter(|part| !part.is_empty()).copied().collect::<Vec<_>>().join(" ");
        let text_width = self.width;
        if length(prefix) + length(&usage) > text_width {
            let opt_parts = usage_parts(&self.actions_usage(&optionals));
            let pos_parts = usage_parts(&self.actions_usage(&positionals));
            let get_lines = |parts: &[String], indent: &str, prefix: Option<&str>| -> Vec<String> {
                let mut lines = Vec::new();
                let mut line: Vec<&str> = Vec::new();
                let mut line_length = match prefix {
                    Some(prefix) => length(prefix) - 1,
                    None => length(indent) - 1,
                };
                for part in parts {
                    if line_length + 1 + length(part) > text_width && !line.is_empty() {
                        lines.push(format!("{indent}{}", line.join(" ")));
                        line.clear();
                        line_length = length(indent) - 1;
                    }
                    line.push(part);
                    line_length += length(part) + 1;
                }
                if !line.is_empty() {
                    lines.push(format!("{indent}{}", line.join(" ")));
                }
                if prefix.is_some() {
                    lines[0] = lines[0][indent.len()..].to_string();
                }
                lines
            };
            let mut lines;
            if 4 * i128::from(length(prefix) + length(prog)) <= 3 * i128::from(text_width) {
                let indent = spaces(length(prefix) + length(prog) + 1);
                let with_prog = |parts: &[String]| {
                    let mut all = vec![prog.to_string()];
                    all.extend(parts.iter().cloned());
                    all
                };
                if !opt_parts.is_empty() {
                    lines = get_lines(&with_prog(&opt_parts), &indent, Some(prefix));
                    lines.extend(get_lines(&pos_parts, &indent, None));
                } else if !pos_parts.is_empty() {
                    lines = get_lines(&with_prog(&pos_parts), &indent, Some(prefix));
                } else {
                    lines = vec![prog.to_string()];
                }
            } else {
                let indent = spaces(length(prefix));
                let parts: Vec<String> = opt_parts.iter().chain(pos_parts.iter()).cloned().collect();
                lines = get_lines(&parts, &indent, None);
                if lines.len() > 1 {
                    lines = get_lines(&opt_parts, &indent, None);
                    lines.extend(get_lines(&pos_parts, &indent, None));
                }
                lines.insert(0, prog.to_string());
            }
            usage = lines.join("\n");
        }
        format!("{prefix}{usage}\n\n")
    }

    /// `add_argument`: the longest invocation decides where help starts.
    fn add_argument(&mut self, entry: Entry<'a>, indent: i64) {
        let mut longest = length(&self.invocation(entry));
        for sub in self.subentries(entry) {
            longest = longest.max(length(&self.invocation(sub)));
        }
        self.action_max_length = self.action_max_length.max(longest + indent);
    }

    /// `_format_action`.
    fn action(&self, entry: Entry<'a>, indent: i64) -> String {
        let help_position = (self.action_max_length + 2).min(self.max_help_position);
        let help_width = (self.width - help_position).max(11);
        let action_width = help_position - indent - 2;
        let header = self.invocation(entry);
        let help = self.help(entry);
        let mut indent_first = 0;
        let mut parts = Vec::new();
        if help.map_or(true, str::is_empty) {
            parts.push(format!("{}{header}\n", spaces(indent)));
        } else if length(&header) <= action_width {
            let padding = spaces(action_width - length(&header));
            parts.push(format!("{}{header}{padding}  ", spaces(indent)));
        } else {
            parts.push(format!("{}{header}\n", spaces(indent)));
            indent_first = help_position;
        }
        match help.filter(|help| !cir_config::py::strip(help).is_empty()) {
            Some(help) => {
                let lines = wrap(&normalize_whitespace(help), help_width);
                parts.push(format!("{}{}\n", spaces(indent_first), lines[0]));
                for line in &lines[1..] {
                    parts.push(format!("{}{line}\n", spaces(help_position)));
                }
            }
            None => {
                if !parts[0].ends_with('\n') {
                    parts.push("\n".to_string());
                }
            }
        }
        for sub in self.subentries(entry) {
            parts.push(self.action(sub, indent + 2));
        }
        parts.concat()
    }

    /// `_format_text` at the top level.
    fn text(&self, text: &str) -> String {
        let lines = wrap(&normalize_whitespace(text), self.width.max(11));
        format!("{}\n\n", lines.join("\n"))
    }
}

/// `HelpFormatter.format_help` on the assembled parts.
fn finish(parts: &[String]) -> String {
    let mut text = format!("\n{}\n", parts.concat());
    // `\n\n\n+` becomes `\n\n`.
    while text.contains("\n\n\n") {
        text = text.replace("\n\n\n", "\n\n");
    }
    format!("{}\n", text.trim_matches('\n'))
}

/// `ArgumentParser.format_usage`.
pub fn format_usage(parser: &Parser) -> String {
    finish(&[Formatter::new(parser).usage()])
}

/// `ArgumentParser.format_help`.
pub fn format_help(parser: &Parser) -> String {
    let mut formatter = Formatter::new(parser);
    let positionals: Vec<Entry> =
        parser.actions().into_iter().filter(|target| *target == Target::Commands).map(Entry::Target).collect();
    let optionals: Vec<Entry> =
        parser.actions().into_iter().filter(|target| *target != Target::Commands).map(Entry::Target).collect();
    for entry in positionals.iter().chain(optionals.iter()) {
        formatter.add_argument(*entry, 2);
    }
    let mut parts = vec![formatter.usage()];
    if let Some(description) = parser.description {
        parts.push(formatter.text(description));
    }
    for (heading, entries) in [("positional arguments", &positionals), ("options", &optionals)] {
        if entries.is_empty() {
            continue;
        }
        let items: String = entries.iter().map(|entry| formatter.action(*entry, 2)).collect();
        parts.push(format!("\n{heading}:\n{items}\n"));
    }
    finish(&parts)
}

/// The three separator clean-ups of `_format_actions_usage`.
fn clean_separators(text: &str) -> String {
    let opens = |c: char| c == '[' || c == '(';
    let closes = |c: char| c == ']' || c == ')';
    // `([\[(]) ` -> `\1`
    let mut first = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        first.push(c);
        if opens(c) && chars.peek() == Some(&' ') {
            chars.next();
        }
    }
    // ` ([\])])` -> `\1`
    let mut second = String::with_capacity(first.len());
    let mut chars = first.chars().peekable();
    while let Some(c) = chars.next() {
        if c == ' ' && chars.peek().is_some_and(|&next| closes(next)) {
            continue;
        }
        second.push(c);
    }
    // `[\[(] *[\])]` -> ``
    let characters: Vec<char> = second.chars().collect();
    let mut third = String::with_capacity(second.len());
    let mut index = 0;
    while index < characters.len() {
        if opens(characters[index]) {
            let mut end = index + 1;
            while end < characters.len() && characters[end] == ' ' {
                end += 1;
            }
            if end < characters.len() && closes(characters[end]) {
                index = end + 1;
                continue;
            }
        }
        third.push(characters[index]);
        index += 1;
    }
    cir_config::py::strip(&third).to_string()
}

/// `re.findall(r'\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+', text)`.
fn usage_parts(text: &str) -> Vec<String> {
    let characters: Vec<char> = text.chars().collect();
    let is_space = |c: char| cir_config::py::is_space(c);
    let mut parts = Vec::new();
    let mut index = 0;
    while index < characters.len() {
        let c = characters[index];
        let bracketed = match c {
            '(' => bracket_end(&characters, index, ')'),
            '[' => bracket_end(&characters, index, ']'),
            _ => None,
        };
        if let Some(end) = bracketed {
            parts.push(characters[index..end].iter().collect());
            index = end;
        } else if is_space(c) {
            index += 1;
        } else {
            let end = (index..characters.len()).find(|&end| is_space(characters[end])).unwrap_or(characters.len());
            parts.push(characters[index..end].iter().collect());
            index = end;
        }
    }
    parts
}

/// The end of `\[.*?\]+(?=\s|$)` from `start`: the first run of closing
/// brackets followed by whitespace or the end (`.` stops at a newline).
fn bracket_end(characters: &[char], start: usize, close: char) -> Option<usize> {
    let mut index = start + 1;
    while index < characters.len() {
        if characters[index] == '\n' {
            return None;
        }
        if characters[index] == close {
            let mut end = index;
            while end < characters.len() && characters[end] == close {
                end += 1;
            }
            if end == characters.len() || cir_config::py::is_space(characters[end]) {
                return Some(end);
            }
        }
        index += 1;
    }
    None
}

/// `re.sub(r'\s+', ' ', text, flags=re.ASCII).strip()`.
fn normalize_whitespace(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_space = false;
    for c in text.chars() {
        if matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c') {
            if !in_space {
                out.push(' ');
            }
            in_space = true;
        } else {
            out.push(c);
            in_space = false;
        }
    }
    cir_config::py::strip(&out).to_string()
}

// ---------------------------------------------------------------------------
// textwrap.wrap with its defaults (break_long_words, break_on_hyphens,
// drop_whitespace) for text without tabs or line breaks.

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

fn is_letter(c: char) -> bool {
    is_word(c) && !cir_config::py::is_decimal(c)
}

fn is_word_punct(c: char) -> bool {
    is_word(c) || matches!(c, '!' | '"' | '\'' | '&' | '.' | ',' | '?')
}

fn is_wrap_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ')
}

/// Whether two or more hyphens start at `index` and a word character
/// follows them.
fn em_dash_at(characters: &[char], index: usize) -> bool {
    let run = characters[index..].iter().take_while(|&&c| c == '-').count();
    run >= 2 && characters.get(index + run).is_some_and(|&c| is_word(c))
}

/// `TextWrapper.wordsep_re` splitting: whitespace runs, em-dashes and words
/// broken after hyphens between letters.
fn chunks(text: &str) -> Vec<String> {
    let characters: Vec<char> = text.chars().collect();
    let at = |index: isize| -> Option<char> {
        (index >= 0).then(|| characters.get(index as usize).copied()).flatten()
    };
    let letter = |index: isize| at(index).is_some_and(is_letter);
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < characters.len() {
        let end = if is_wrap_space(characters[start]) {
            (start..characters.len()).find(|&end| !is_wrap_space(characters[end])).unwrap_or(characters.len())
        } else if start > 0 && is_word_punct(characters[start - 1]) && em_dash_at(&characters, start) {
            start + characters[start..].iter().take_while(|&&c| c == '-').count()
        } else {
            let mut end = start + 1;
            loop {
                let e = end as isize;
                // A hyphenated word: break after a hyphen between letters.
                if at(e) == Some('-')
                    && ((letter(e - 2) && letter(e - 1)) || (letter(e - 3) && at(e - 2) == Some('-') && letter(e - 1)))
                    && letter(e + 1)
                    && (letter(e + 2) || (at(e + 2) == Some('-') && letter(e + 3)))
                {
                    end += 1;
                    break;
                }
                if end == characters.len() || is_wrap_space(characters[end]) {
                    break;
                }
                if is_word_punct(characters[end - 1]) && em_dash_at(&characters, end) {
                    break;
                }
                end += 1;
            }
            end
        };
        chunks.push(characters[start..end].iter().collect::<String>());
        start = end;
    }
    chunks
}

/// `textwrap.wrap(text, width)`.
fn wrap(text: &str, width: i64) -> Vec<String> {
    let mut chunks: Vec<String> = chunks(text);
    chunks.reverse();
    let blank = |chunk: &str| cir_config::py::strip(chunk).is_empty();
    let mut lines: Vec<String> = Vec::new();
    while !chunks.is_empty() {
        let mut line: Vec<String> = Vec::new();
        let mut line_length = 0;
        if !lines.is_empty() && blank(chunks.last().expect("not empty")) {
            chunks.pop();
        }
        while let Some(chunk) = chunks.last() {
            let size = length(chunk);
            if line_length + size <= width {
                line_length += size;
                line.push(chunks.pop().expect("not empty"));
            } else {
                break;
            }
        }
        if chunks.last().is_some_and(|chunk| length(chunk) > width) {
            // `_handle_long_word`: as much as fits, after a hyphen if possible.
            let space_left = if width < 1 { 1 } else { width - line_length };
            let chunk: Vec<char> = chunks.last().expect("not empty").chars().collect();
            let mut end = space_left.max(0) as usize;
            if (chunk.len() as i64) > space_left {
                let limit = end.min(chunk.len());
                if let Some(hyphen) = chunk[..limit].iter().rposition(|&c| c == '-') {
                    if hyphen > 0 && chunk[..hyphen].iter().any(|&c| c != '-') {
                        end = hyphen + 1;
                    }
                }
            }
            let end = end.min(chunk.len());
            line.push(chunk[..end].iter().collect());
            *chunks.last_mut().expect("not empty") = chunk[end..].iter().collect();
        }
        if line.last().is_some_and(|chunk| blank(chunk)) {
            line.pop();
        }
        if !line.is_empty() {
            lines.push(line.concat());
        }
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `textwrap.wrap(text, width)` results from Python 3.11.
    #[test]
    fn wraps_like_textwrap() {
        let cases: &[(&str, i64, &[&str])] = &[
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 11, &["Scan", "installed", "EE items", "and author", "draft", "Randomiser", "profiles.", "Game", "changes", "require the", "editor's", "explicit", "--apply-", "game opt-", "in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 12, &["Scan", "installed EE", "items and", "author draft", "Randomiser", "profiles.", "Game changes", "require the", "editor's", "explicit", "--apply-game", "opt-in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 14, &["Scan installed", "EE items and", "author draft", "Randomiser", "profiles. Game", "changes", "require the", "editor's", "explicit", "--apply-game", "opt-in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 17, &["Scan installed EE", "items and author", "draft Randomiser", "profiles. Game", "changes require", "the editor's", "explicit --apply-", "game opt-in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 20, &["Scan installed EE", "items and author", "draft Randomiser", "profiles. Game", "changes require the", "editor's explicit", "--apply-game opt-in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 25, &["Scan installed EE items", "and author draft", "Randomiser profiles. Game", "changes require the", "editor's explicit", "--apply-game opt-in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 33, &["Scan installed EE items and", "author draft Randomiser profiles.", "Game changes require the editor's", "explicit --apply-game opt-in."]),
        ("Scan installed EE items and author draft Randomiser profiles. Game changes require the editor's explicit --apply-game opt-in.", 54, &["Scan installed EE items and author draft Randomiser", "profiles. Game changes require the editor's explicit", "--apply-game opt-in."]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 11, &["a well-", "known self-", "contained", "e-mail", "look-up for", "the cre-", "template", "kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 12, &["a well-known", "self-", "contained", "e-mail look-", "up for the", "cre-template", "kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 14, &["a well-known", "self-contained", "e-mail look-up", "for the cre-", "template kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 17, &["a well-known", "self-contained", "e-mail look-up", "for the cre-", "template kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 20, &["a well-known self-", "contained e-mail", "look-up for the cre-", "template kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 25, &["a well-known self-", "contained e-mail look-up", "for the cre-template kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 33, &["a well-known self-contained", "e-mail look-up for the cre-", "template kind"]),
        ("a well-known self-contained e-mail look-up for the cre-template kind", 54, &["a well-known self-contained e-mail look-up for the", "cre-template kind"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 11, &["word--dash", "and--more", "em-dashes", "-- spaced", "--leading", "trailing--", "x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 12, &["word--dash", "and--more", "em-dashes --", "spaced", "--leading", "trailing--", "x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 14, &["word--dash and", "--more em-", "dashes --", "spaced", "--leading", "trailing--", "x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 17, &["word--dash and--", "more em-dashes --", "spaced --leading", "trailing-- x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 20, &["word--dash and--more", "em-dashes -- spaced", "--leading trailing--", "x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 25, &["word--dash and--more em-", "dashes -- spaced", "--leading trailing--", "x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 33, &["word--dash and--more em-dashes --", "spaced --leading trailing-- x-y-z"]),
        ("word--dash and--more em-dashes -- spaced --leading trailing-- x-y-z", 54, &["word--dash and--more em-dashes -- spaced --leading", "trailing-- x-y-z"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 11, &["supercalifr", "agilisticex", "pialidociou", "s-and-", "hyphenated-", "very-long-", "words here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 12, &["supercalifra", "gilisticexpi", "alidocious-", "and-", "hyphenated-", "very-long-", "words here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 14, &["supercalifragi", "listicexpialid", "ocious-and-", "hyphenated-", "very-long-", "words here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 17, &["supercalifragilis", "ticexpialidocious", "-and-hyphenated-", "very-long-words", "here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 20, &["supercalifragilistic", "expialidocious-and-", "hyphenated-very-", "long-words here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 25, &["supercalifragilisticexpia", "lidocious-and-hyphenated-", "very-long-words here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 33, &["supercalifragilisticexpialidociou", "s-and-hyphenated-very-long-words", "here"]),
        ("supercalifragilisticexpialidocious-and-hyphenated-very-long-words here", 54, &["supercalifragilisticexpialidocious-and-hyphenated-", "very-long-words here"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 11, &["---- ---a", "a--- 1-2", "ab-12 a1-bc", "ab-c a-bc", "abc-de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 12, &["---- ---a", "a--- 1-2", "ab-12 a1-bc", "ab-c a-bc", "abc-de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 14, &["---- ---a a---", "1-2 ab-12", "a1-bc ab-c", "a-bc abc-de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 17, &["---- ---a a---", "1-2 ab-12 a1-bc", "ab-c a-bc abc-", "de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 20, &["---- ---a a--- 1-2", "ab-12 a1-bc ab-c", "a-bc abc-de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 25, &["---- ---a a--- 1-2 ab-12", "a1-bc ab-c a-bc abc-de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 33, &["---- ---a a--- 1-2 ab-12 a1-bc", "ab-c a-bc abc-de-f"]),
        ("---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f", 54, &["---- ---a a--- 1-2 ab-12 a1-bc ab-c a-bc abc-de-f"]),
        ("Discover physical sources and candidate targets (contains location information)", 11, &["Discover", "physical", "sources and", "candidate", "targets", "(contains", "location in", "formation)"]),
        ("Discover physical sources and candidate targets (contains location information)", 12, &["Discover", "physical", "sources and", "candidate", "targets", "(contains", "location", "information)"]),
        ("Discover physical sources and candidate targets (contains location information)", 14, &["Discover", "physical", "sources and", "candidate", "targets", "(contains", "location", "information)"]),
        ("Discover physical sources and candidate targets (contains location information)", 17, &["Discover physical", "sources and", "candidate targets", "(contains", "location", "information)"]),
        ("Discover physical sources and candidate targets (contains location information)", 20, &["Discover physical", "sources and", "candidate targets", "(contains location", "information)"]),
        ("Discover physical sources and candidate targets (contains location information)", 25, &["Discover physical sources", "and candidate targets", "(contains location", "information)"]),
        ("Discover physical sources and candidate targets (contains location information)", 33, &["Discover physical sources and", "candidate targets (contains", "location information)"]),
        ("Discover physical sources and candidate targets (contains location information)", 54, &["Discover physical sources and candidate targets", "(contains location information)"]),
        ];
        for (text, width, expected) in cases {
            assert_eq!(wrap(text, *width), expected.to_vec(), "{text:?} at {width}");
        }
    }

    #[test]
    fn splits_usage_like_argparse() {
        assert_eq!(
            usage_parts("[-h] --catalog CATALOG [--query QUERY] [--kind {cre-template,actor}]"),
            vec!["[-h]", "--catalog", "CATALOG", "[--query QUERY]", "[--kind {cre-template,actor}]"]
        );
        assert_eq!(usage_parts("{create,edit} ..."), vec!["{create,edit}", "..."]);
        // As Python: the empty group goes, the spaces around it stay.
        assert_eq!(clean_separators("[ --a ] ( ) [b]"), "[--a]  [b]");
    }

    #[test]
    fn negative_numbers_are_arguments() {
        assert!(is_negative_number("-5"));
        assert!(is_negative_number("-.5"));
        assert!(is_negative_number("-1.25"));
        assert!(is_negative_number("-5
"));
        assert!(!is_negative_number("-5."));
        assert!(!is_negative_number("-x"));
        assert!(!is_negative_number("--5"));
    }
}
