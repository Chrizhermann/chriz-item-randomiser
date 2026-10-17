//! A small parser with the behaviour of the reference's argparse interface:
//! subcommands, `--option value` / `--option=value`, unique-prefix
//! abbreviations, repeated options, choices and integer conversion. Usage
//! errors exit with status 2 like argparse.

use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Text,
    Int,
    Path,
    Append,
    Flag,
}

pub struct Spec {
    pub name: &'static str,
    pub kind: Kind,
    pub required: bool,
    pub choices: Vec<String>,
}

impl Spec {
    pub fn new(name: &'static str, kind: Kind) -> Spec {
        Spec { name, kind, required: false, choices: Vec::new() }
    }
    pub fn required(mut self) -> Spec {
        self.required = true;
        self
    }
    pub fn choices<I: IntoIterator<Item = S>, S: Into<String>>(mut self, choices: I) -> Spec {
        self.choices = choices.into_iter().map(Into::into).collect();
        self
    }
}

pub struct UsageError {
    pub prog: String,
    pub message: String,
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
        self.text(name).map(|value| cir_config::py::parse_int_saturating(value).expect("validated while parsing"))
    }
    /// An integer option of any size, as Python's `int` keeps it.
    pub fn int_value(&self, name: &str) -> Option<cir_config::Value> {
        self.text(name).map(|value| cir_config::py::parse_int_value(value).expect("validated while parsing"))
    }
    pub fn all(&self, name: &str) -> Vec<String> {
        self.values.get(name).cloned().unwrap_or_default()
    }
    pub fn flag(&self, name: &str) -> bool {
        self.values.contains_key(name)
    }
}

/// Parse `arguments` against `specs`; `prog` names the command in messages.
pub fn parse(prog: &str, arguments: &[String], specs: &[Spec]) -> Result<Parsed, UsageError> {
    let error = |message: String| UsageError { prog: prog.to_string(), message };
    let mut parsed = Parsed::default();
    let mut index = 0;
    while index < arguments.len() {
        let argument = &arguments[index];
        index += 1;
        let Some(body) = argument.strip_prefix("--") else {
            return Err(error(format!("unrecognized arguments: {}", arguments[index - 1..].join(" "))));
        };
        let (name, inline) = match body.split_once('=') {
            Some((name, value)) => (name, Some(value.to_string())),
            None => (body, None),
        };
        let exact = specs.iter().find(|spec| spec.name == name);
        let spec = match exact {
            Some(spec) => spec,
            None => {
                let candidates: Vec<&Spec> = specs.iter().filter(|spec| spec.name.starts_with(name)).collect();
                match candidates.len() {
                    1 => candidates[0],
                    0 => return Err(error(format!("unrecognized arguments: {argument}"))),
                    _ => {
                        let names: Vec<String> = candidates.iter().map(|spec| format!("--{}", spec.name)).collect();
                        return Err(error(format!(
                            "ambiguous option: --{name} could match {}",
                            names.join(", ")
                        )));
                    }
                }
            }
        };
        if spec.kind == Kind::Flag {
            if inline.is_some() {
                return Err(error(format!("argument --{}: ignored explicit argument '{}'", spec.name, inline.unwrap())));
            }
            parsed.values.entry(spec.name).or_default().push(String::new());
            continue;
        }
        let value = match inline {
            Some(value) => value,
            None => {
                if index >= arguments.len() || (arguments[index].starts_with("--") && arguments[index].len() > 2) {
                    return Err(error(format!("argument --{}: expected one argument", spec.name)));
                }
                index += 1;
                arguments[index - 1].clone()
            }
        };
        if spec.kind == Kind::Int && cir_config::py::int_literal(&value).is_none() {
            return Err(error(format!("argument --{}: invalid int value: '{value}'", spec.name)));
        }
        if !spec.choices.is_empty() && !spec.choices.contains(&value) {
            let choices: Vec<String> = spec.choices.iter().map(|choice| format!("'{choice}'")).collect();
            return Err(error(format!(
                "argument --{}: invalid choice: '{value}' (choose from {})",
                spec.name,
                choices.join(", ")
            )));
        }
        let entry = parsed.values.entry(spec.name).or_default();
        if spec.kind != Kind::Append {
            entry.clear();
        }
        entry.push(value);
    }
    let missing: Vec<String> = specs
        .iter()
        .filter(|spec| spec.required && !parsed.values.contains_key(spec.name))
        .map(|spec| format!("--{}", spec.name))
        .collect();
    if !missing.is_empty() {
        return Err(error(format!("the following arguments are required: {}", missing.join(", "))));
    }
    Ok(parsed)
}
