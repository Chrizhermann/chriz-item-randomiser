//! Parity driver: runs the library calls that `configurator/parity/driver.py`
//! runs with the Python reference and writes each outcome as one JSON line.
//!
//! Usage: `parity_call <calls.json> <outcomes.jsonl>`. The calls file holds
//! `{"calls": [{"call": name, "args": {...}}]}`; an argument written as
//! `{"$file": path}` is read from that JSON file. This is a test tool, not
//! part of the configurator executable.

use cir_config::json::{dumps_ascii, Dict, Value};
use cir_config::profiles::{read_json, validate_catalog};
use cir_config::sources::validate_world;
use cir_config::{
    campaigns, native_export, original_catalog, original_configuration, sources, Error, ErrorKind, Result,
};
use std::collections::HashMap;
use std::path::Path;

fn kind_name(kind: ErrorKind) -> &'static str {
    match kind {
        ErrorKind::Profile => "ProfileError",
        ErrorKind::Scan => "ScanError",
        ErrorKind::Os => "OSError",
        ErrorKind::Value => "ValueError",
        ErrorKind::Key => "KeyError",
        ErrorKind::Type => "TypeError",
        ErrorKind::GameText => "GameTextError",
        ErrorKind::Overflow => "OverflowError",
    }
}

/// An argument, reading `{"$file": path}` references.
fn argument(args: &Dict, name: &str) -> Result<Option<Value>> {
    match args.get(name) {
        Some(Value::Dict(reference)) if reference.len() == 1 && reference.contains_key("$file") => {
            let path = reference.get("$file").and_then(Value::as_str).unwrap_or("");
            Ok(Some(read_json(Path::new(path))?))
        }
        Some(value) => Ok(Some(value.clone())),
        None => Ok(None),
    }
}

fn required(args: &Dict, name: &str) -> Result<Value> {
    argument(args, name)?.ok_or_else(|| Error::key(name))
}

fn text(args: &Dict, name: &str) -> Result<String> {
    Ok(required(args, name)?.as_str().unwrap_or("").to_string())
}

fn sorted(set: std::collections::BTreeSet<String>) -> Value {
    Value::from(set.into_iter().collect::<Vec<_>>())
}

fn run(name: &str, args: &Dict) -> Result<Value> {
    match name {
        "scan_game" => {
            let language = argument(args, "language")?;
            cir_config::resources::scan_game(Path::new(&text(args, "game_root")?), language.as_ref().and_then(Value::as_str))
        }
        "scan_world" => sources::scan_world(&required(args, "catalog")?),
        "resolve_template" => original_catalog::resolve_template(
            &required(args, "catalog")?,
            &required(args, "world")?,
            &text(args, "template_id")?,
            argument(args, "game")?.as_ref().and_then(Value::as_str).unwrap_or("eet"),
            argument(args, "items")?.as_ref(),
            argument(args, "targets")?.as_ref(),
            argument(args, "options")?.as_ref(),
        ),
        "build_original_configuration" => {
            let catalog = required(args, "catalog")?;
            let world = required(args, "world")?;
            let template_id = text(args, "template_id")?;
            let exclude: HashMap<String, String> = argument(args, "exclude")?
                .and_then(|exclude| exclude.as_dict().cloned())
                .unwrap_or_default()
                .iter()
                .map(|(row, reason)| (row.to_string(), reason.as_str().unwrap_or("").to_string()))
                .collect();
            match argument(args, "draft")? {
                Some(draft) => original_configuration::build_original_configuration_from(
                    &catalog, &world, &template_id, &draft, &exclude,
                ),
                None => original_configuration::build_original_configuration(
                    &catalog,
                    &world,
                    &template_id,
                    argument(args, "items")?.as_ref(),
                    argument(args, "targets")?.as_ref(),
                    argument(args, "options")?.as_ref(),
                    &exclude,
                ),
            }
        }
        "fit_original_configuration" => {
            let catalog = required(args, "catalog")?;
            let world = required(args, "world")?;
            let template_id = required(args, "template_id")?;
            match argument(args, "draft")? {
                Some(draft) => {
                    original_configuration::fit_original_configuration_from(&catalog, &world, &template_id, &draft)
                }
                None => original_configuration::fit_original_configuration(
                    &catalog,
                    &world,
                    &template_id,
                    argument(args, "items")?.as_ref(),
                    argument(args, "targets")?.as_ref(),
                    argument(args, "options")?.as_ref(),
                ),
            }
        }
        "new_binding_plan" => {
            sources::new_binding_plan(&required(args, "profile")?, &required(args, "catalog")?, &required(args, "world")?)
        }
        "bind_profile" => sources::bind_profile(
            &required(args, "profile")?,
            &required(args, "catalog")?,
            &required(args, "world")?,
            &required(args, "plan")?,
        ),
        "prepare_bound_profile" => sources::prepare_bound_profile(
            &required(args, "profile")?,
            &required(args, "catalog")?,
            &required(args, "world")?,
            &required(args, "plan")?,
            Path::new(&text(args, "store")?),
        ),
        "read_bound_profile" => {
            let revision = argument(args, "revision")?;
            sources::read_bound_profile(Path::new(&text(args, "store")?), revision.as_ref().and_then(Value::as_str))
        }
        "compile_campaign" => campaigns::compile_campaign(
            &required(args, "profile")?,
            &required(args, "catalog")?,
            &required(args, "world")?,
            &required(args, "plan")?,
        ),
        "write_campaign" => campaigns::write_campaign(
            &required(args, "profile")?,
            &required(args, "catalog")?,
            &required(args, "world")?,
            &required(args, "plan")?,
            Path::new(&text(args, "store")?),
        ),
        "native_compile" => {
            let catalog = required(args, "catalog")?;
            let world = required(args, "world")?;
            let manifest = required(args, "manifest")?;
            let items = validate_catalog(&catalog)?;
            let index = validate_world(&world, &catalog)?;
            let collect = argument(args, "problems")?.is_some_and(|value| value.truthy());
            let mut problems = Dict::new();
            let (native, required_resources, pristine) = native_export::compile(
                &manifest,
                &world,
                &index,
                &items,
                &catalog,
                if collect { Some(&mut problems) } else { None },
            )?;
            let mut problems: Vec<(String, Value)> =
                problems.iter().map(|(identity, reason)| (identity.to_string(), reason.clone())).collect();
            problems.sort_by(|a, b| a.0.cmp(&b.0));
            let mut sorted_problems = Dict::new();
            for (identity, reason) in problems {
                sorted_problems.insert(identity, reason);
            }
            let mut result = Dict::new();
            result.insert("native", native);
            result.insert("required", sorted(required_resources));
            result.insert("pristine", sorted(pristine));
            result.insert("problems", if collect { Value::Dict(sorted_problems) } else { Value::Null });
            Ok(Value::Dict(result))
        }
        "export_native" => {
            let revision = argument(args, "revision")?;
            native_export::export_native(
                Path::new(&text(args, "store")?),
                &required(args, "catalog")?,
                &required(args, "world")?,
                Path::new(&text(args, "output")?),
                revision.as_ref().and_then(Value::as_str),
            )
        }
        "read_campaign" => {
            let revision = argument(args, "revision")?;
            campaigns::read_campaign(Path::new(&text(args, "store")?), revision.as_ref().and_then(Value::as_str))
        }
        other => Err(Error::value(format!("Unknown parity call: {other}"))),
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 3 {
        eprintln!("usage: parity_call <calls.json> <outcomes.jsonl>");
        std::process::exit(2);
    }
    let calls = read_json(Path::new(&arguments[1])).expect("readable calls file");
    let mut lines = String::new();
    for call in calls.get("calls").and_then(Value::as_list).expect("calls list") {
        let name = call.get("call").and_then(Value::as_str).expect("call name");
        let args = call.get("args").and_then(Value::as_dict).cloned().unwrap_or_default();
        let mut outcome = Dict::new();
        match run(name, &args) {
            Ok(result) => outcome.insert("result", result),
            Err(error) => {
                let mut failure = Dict::new();
                failure.insert("kind", kind_name(error.kind));
                failure.insert("message", error.message);
                outcome.insert("error", failure);
            }
        }
        lines.push_str(&dumps_ascii(&Value::Dict(outcome), false));
        lines.push('\n');
    }
    std::fs::write(&arguments[2], lines).expect("writable outcomes file");
}
