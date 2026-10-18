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
use cir_config::game_names::{self, EetNames};
use cir_config::{
    activation, campaigns, native_export, original_catalog, original_configuration, platform, sources, starts, templates,
    Error, ErrorKind, Result,
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
        ErrorKind::Attribute => "AttributeError",
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

/// An optional `game_names.EetNames` argument.
fn names(args: &Dict) -> Result<Option<EetNames>> {
    argument(args, "names")?.filter(|value| !value.is_null()).map(|value| EetNames::from_value(&value)).transpose()
}

fn texts(values: Vec<&str>) -> Value {
    Value::List(values.into_iter().map(Value::from).collect())
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
            names(args)?.as_ref(),
        ),
        "build_template" => templates::build_template(
            &text(args, "template_id")?,
            argument(args, "items")?.as_ref(),
            argument(args, "targets")?.as_ref(),
            argument(args, "options")?.as_ref(),
        ),
        "eet_names" => game_names::eet_names(Path::new(&text(args, "game_root")?)).map(|names| names.to_value()),
        "convert_resource" => game_names::convert_resource(
            &text(args, "resref")?,
            &text(args, "extension")?,
            &text(args, "campaign")?,
            argument(args, "game")?.as_ref().and_then(Value::as_str).unwrap_or("eet"),
            names(args)?.as_ref(),
        )
        .map(Value::from),
        "has_sod" => platform::has_sod(&required(args, "world")?).map(Value::from),
        "preset_campaigns" => platform::preset_campaigns(&required(args, "world")?).map(texts),
        "recommended_campaign" => platform::recommended_campaign(&required(args, "world")?).map(Value::from),
        "campaign_tables" => templates::campaign_tables(&text(args, "campaign")?).map(texts),
        "template_tables" => templates::template_tables(&text(args, "template_id")?).map(texts),
        "recommended_preset" => templates::recommended_preset(&text(args, "campaign")?).map(Value::Dict),
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
            let stored = argument(args, "stored")?.is_some_and(|value| value.truthy());
            let mut problems = Dict::new();
            let (native, required_resources, pristine) = native_export::compile(
                &manifest,
                &world,
                &index,
                &items,
                &catalog,
                if collect { Some(&mut problems) } else { None },
                stored,
                None,
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
            let main = argument(args, "main")?;
            // The replay passes only mappings of text here.
            let starts: Option<Vec<(String, String)>> = argument(args, "starts")?.and_then(|starts| {
                starts.as_dict().map(|starts| {
                    starts.iter().map(|(start, key)| (start.to_string(), key.as_str().unwrap_or("").to_string())).collect()
                })
            });
            native_export::export_native(
                Path::new(&text(args, "store")?),
                &required(args, "catalog")?,
                &required(args, "world")?,
                Path::new(&text(args, "output")?),
                revision.as_ref().and_then(Value::as_str),
                starts.as_deref(),
                main.as_ref().and_then(Value::as_str),
            )
        }
        "write_starts" => starts::write_starts(
            &required(args, "profile")?,
            &required(args, "plan")?,
            &required(args, "catalog")?,
            &required(args, "world")?,
            Path::new(&text(args, "store")?),
        ),
        "describe_starts" => starts::describe_starts(&required(args, "written")?).map(Value::from),
        "game_platform" => platform::game_platform(&required(args, "world")?).map(Value::from),
        "preset_game" => platform::preset_game(&required(args, "world")?, &text(args, "template_id")?).map(Value::from),
        "area_part" => {
            Ok(platform::area_part(&text(args, "platform")?, &text(args, "area")?).map_or(Value::Null, Value::from))
        }
        "list_templates" => match argument(args, "campaigns")? {
            Some(Value::List(campaigns)) => {
                // `row["campaign"] in campaigns`: only text can match.
                let campaigns: Vec<&str> = campaigns.iter().filter_map(Value::as_str).collect();
                Ok(templates::list_templates(Some(&campaigns)))
            }
            _ => Ok(templates::list_templates(None)),
        },
        "item_randomiser_conflict" => activation::item_randomiser_conflict(Path::new(&text(args, "game")?))
            .map(|conflict| conflict.map_or(Value::Null, Value::from)),
        "validate_runtime" => activation::validate_runtime(Path::new(&text(args, "game")?), &required(args, "catalog")?)
            .map(|game| Value::Str(game.to_string_lossy().into_owned())),
        "deploy_export" => {
            activation::deploy_export(Path::new(&text(args, "game")?), Path::new(&text(args, "output")?)).map(|()| Value::Null)
        }
        "read_campaign" => {
            let revision = argument(args, "revision")?;
            campaigns::read_campaign(Path::new(&text(args, "store")?), revision.as_ref().and_then(Value::as_str))
        }
        other => Err(Error::value(format!("Unknown parity call: {other}"))),
    }
}

/// What the reference's tests patch reaches the port through these; a build
/// without `parity-overrides` would ignore them and report false differences.
const OVERRIDES: [&str; 6] = [
    "CIR_PIN_OVERRIDES",
    "CIR_PARITY_TABLES",
    "CIR_PARITY_REGISTRATION_DRAFT",
    "CIR_PARITY_LANGUAGES",
    "CIR_PARITY_LISTS",
    "CIR_PARITY_CAMPAIGNS",
];

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 3 {
        eprintln!("usage: parity_call <calls.json> <outcomes.jsonl>");
        std::process::exit(2);
    }
    if !cfg!(feature = "parity-overrides") {
        if let Some(name) = OVERRIDES.iter().find(|name| std::env::var_os(name).is_some()) {
            eprintln!("parity_call: {name} needs a build with --features cir-config/parity-overrides");
            std::process::exit(2);
        }
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
