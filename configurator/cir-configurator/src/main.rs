//! `cir-configurator`: Chriz Item Randomiser's post-install configurator as a
//! standalone executable. The commands, JSON results and messages are those
//! of the reference `tools/randomiser_config.py`; `ui` also opens the editor
//! in the browser.

mod argparse;
mod server;

use argparse::{Commands, Kind, Parsed, Parser, Spec};
use cir_config::json::{self, Dict, Value};
use cir_config::{profiles, resources, sources, templates, Error, ErrorKind, Result};
use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

const DESCRIPTION: &str = "Scan installed EE items and author draft Randomiser profiles. \
                           Game changes require the editor's explicit --apply-game opt-in.";

/// The commands and their help, in the reference's order.
const COMMANDS: [(&str, &str); 10] = [
    ("templates", "List or export original pool drafts; not playable settings yet"),
    ("ui", "Open the local configuration editor over existing scans"),
    ("scan", "Read the effective installed item resources"),
    ("search", "Search names or resource IDs without listing everything"),
    ("profile", "Create, edit or validate a draft"),
    ("prepare", "Freeze a draft revision; does not activate it"),
    ("inspect", "Read a verified prepared draft revision"),
    ("world", "Discover physical sources and candidate targets (contains location information)"),
    ("binding", "Author explicit source and target choices; no game activation"),
    ("prototype", "Compile experimental campaign data; never activates gameplay"),
];

/// Print one line the way Python's `print` does on this platform.
fn print_line(stream: &mut dyn Write, text: &str) {
    argparse::write_text(stream, &format!("{text}\n"));
}

fn path(parsed: &Parsed, name: &str) -> PathBuf {
    parsed.path(name).expect("required option")
}

fn display(path: &std::path::Path) -> String {
    path.to_string_lossy().into_owned()
}

fn read(parsed: &Parsed, name: &str) -> Result<Value> {
    profiles::read_json(&path(parsed, name))
}


fn specs(command: &[&str]) -> Option<Vec<Spec>> {
    use Kind::*;
    let pool = || templates::POOL_CHOICES.iter().map(|choice| choice.to_string()).collect::<Vec<_>>();
    Some(match command {
        ["templates"] => vec![
            Spec::new("id", Text).choices(templates::template_ids()),
            Spec::new("items", Text)
                .choices(pool())
                .help("standard (default) or extended: also the larger mode2 tables"),
            Spec::new("targets", Text)
                .choices(pool())
                .help("standard (default) or extended: also the larger mode2 tables"),
            Spec::new("options", Path).help("JSON object of original component options"),
            Spec::new("out", Path),
        ],
        ["ui"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("workspace", Path).required(),
            Spec::new("profile", Path),
            Spec::new("plan", Path),
            Spec::new("port", Int),
            Spec::new("apply-game", Path)
                .help("Enable applying settings to this explicitly installed test runtime"),
            Spec::new("no-browser", Flag).help("Only print the editor's address; do not open it in the browser"),
        ],
        ["scan"] => vec![
            Spec::new("game", Path).required(),
            Spec::new("language", Text),
            Spec::new("out", Path).required(),
        ],
        ["search"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("query", Text),
            Spec::new("category", Int),
            Spec::new("offset", Int),
            Spec::new("limit", Int),
        ],
        ["profile", "create"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("id", Text).required(),
            Spec::new("name", Text).required(),
            Spec::new("out", Path).required(),
        ],
        ["profile", "edit"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("out", Path).required(),
            Spec::new("include", Append),
            Spec::new("exclude", Append),
            Spec::new("name", Text),
        ],
        ["profile", "validate"] => vec![Spec::new("catalog", Path).required(), Spec::new("profile", Path).required()],
        ["profile", "rebase"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("out", Path).required(),
        ],
        ["prepare"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("store", Path).required(),
        ],
        ["inspect"] | ["binding", "inspect"] | ["prototype", "inspect"] => {
            vec![Spec::new("store", Path).required(), Spec::new("revision", Text)]
        }
        ["world", "scan"] => vec![Spec::new("catalog", Path).required(), Spec::new("out", Path).required()],
        ["world", "sources"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("offset", Int),
            Spec::new("limit", Int),
            Spec::new("item", Text).required(),
        ],
        ["world", "targets"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("offset", Int),
            Spec::new("limit", Int),
            Spec::new("query", Text),
            Spec::new("kind", Text).choices(["cre-template", "actor", "container", "store"]),
        ],
        ["binding", "create"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("out", Path).required(),
        ],
        ["binding", "validate"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("plan", Path).required(),
        ],
        ["binding", "prepare"] => vec![
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("plan", Path).required(),
            Spec::new("store", Path).required(),
        ],
        ["prototype", "prepare"] => vec![
            Spec::new("store", Path).required(),
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("profile", Path).required(),
            Spec::new("plan", Path).required(),
        ],
        ["prototype", "export"] => vec![
            Spec::new("store", Path).required(),
            Spec::new("catalog", Path).required(),
            Spec::new("world", Path).required(),
            Spec::new("revision", Text),
            Spec::new("out", Path).required(),
        ],
        _ => return None,
    })
}

const GROUPS: [(&str, &[&str]); 4] = [
    ("profile", &["create", "edit", "validate", "rebase"]),
    ("world", &["scan", "sources", "targets"]),
    ("binding", &["create", "validate", "prepare", "inspect"]),
    ("prototype", &["prepare", "inspect", "export"]),
];

/// The parser for a command path: the top level, a group of operations or
/// a command with its options. `prog` is the program name argparse shows.
fn parser_for(prog: &str, path: &[String]) -> Parser {
    let names: Vec<&str> = path.iter().map(String::as_str).collect();
    let prog = std::iter::once(prog).chain(names.iter().copied()).collect::<Vec<_>>().join(" ");
    if names.is_empty() {
        return Parser {
            prog,
            description: Some(DESCRIPTION),
            options: Vec::new(),
            commands: Some(Commands {
                dest: "command",
                choices: COMMANDS.iter().map(|(name, help)| (*name, Some(*help))).collect(),
            }),
        };
    }
    if let [group] = names.as_slice() {
        if let Some((_, operations)) = GROUPS.iter().find(|(name, _)| name == group) {
            return Parser {
                prog,
                description: None,
                options: Vec::new(),
                commands: Some(Commands {
                    dest: "operation",
                    choices: operations.iter().map(|operation| (*operation, None)).collect(),
                }),
            };
        }
    }
    Parser { prog, description: None, options: specs(&names).expect("a known command"), commands: None }
}

/// The program name argparse shows: the base name of the invoked file.
fn program_name() -> String {
    std::env::args_os()
        .next()
        .and_then(|first| std::path::Path::new(&first).file_name().map(|name| name.to_string_lossy().into_owned()))
        .unwrap_or_else(|| "cir-configurator".to_string())
}

fn result_dict(entries: Vec<(&str, Value)>) -> Value {
    let mut dict = Dict::new();
    for (key, value) in entries {
        dict.insert(key, value);
    }
    Value::Dict(dict)
}

fn run(command: &[String], parsed: &Parsed) -> Result<Value> {
    let names: Vec<&str> = command.iter().map(String::as_str).collect();
    match names.as_slice() {
        ["templates"] => {
            let Some(id) = parsed.text("id") else { return Ok(result_dict(vec![("templates", templates::list_templates(None))])) };
            let build = || -> Result<Value> {
                let options = match parsed.path("options") {
                    Some(options) => Some(profiles::read_json(&options)?),
                    None => None,
                };
                let items = parsed.text("items").map(Value::from);
                let targets = parsed.text("targets").map(Value::from);
                templates::build_template(id, items.as_ref(), targets.as_ref(), options.as_ref())
            };
            let draft = build().map_err(|error| {
                if error.is_value_error() {
                    Error::profile(error.message)
                } else {
                    error
                }
            })?;
            if let Some(out) = parsed.path("out") {
                profiles::write_json(&out, &draft, false)?;
                return Ok(result_dict(vec![
                    ("path", Value::Str(display(&out))),
                    ("state", draft.at("state")?.clone()),
                    ("counts", draft.at("counts")?.clone()),
                ]));
            }
            Ok(draft)
        }
        ["ui"] => server::serve(
            &path_of(parsed, "catalog"),
            &path_of(parsed, "world"),
            &path_of(parsed, "workspace"),
            parsed.int("port").unwrap_or(0),
            parsed.path("profile").as_deref(),
            parsed.path("plan").as_deref(),
            parsed.path("apply-game").as_deref(),
            !parsed.flag("no-browser"),
        ),
        ["scan"] => {
            let game = path(parsed, "game");
            let out = path(parsed, "out");
            // Check before the potentially expensive scan and again with its
            // canonical root.
            let resolved = cir_config::paths::resolve(&game, false).map_err(|(error, at)| Error::win(&error, &at))?;
            let probe = result_dict(vec![("game_root", Value::Str(display(&resolved)))]);
            profiles::ensure_output_outside_game(&out, &probe)?;
            let catalog = resources::scan_game(&game, parsed.text("language"))?;
            profiles::validate_catalog(&catalog)?;
            let destination = profiles::ensure_output_outside_game(&out, &catalog)?;
            profiles::write_json(&destination, &catalog, false)?;
            Ok(result_dict(vec![
                ("catalog_path", Value::Str(display(&destination))),
                ("fingerprint", catalog.at("fingerprint")?.clone()),
                ("item_count", Value::from(catalog.at("items")?.as_list().map_or(0, Vec::len))),
                ("warnings", catalog.get("warnings").cloned().unwrap_or(Value::List(Vec::new()))),
            ]))
        }
        ["inspect"] => profiles::read_prepared(&path(parsed, "store"), parsed.text("revision")),
        ["binding", "inspect"] => sources::read_bound_profile(&path(parsed, "store"), parsed.text("revision")),
        ["prototype", "inspect"] => cir_config::campaigns::read_campaign(&path(parsed, "store"), parsed.text("revision")),
        _ => run_with_catalog(&names, parsed),
    }
}

fn path_of(parsed: &Parsed, name: &str) -> PathBuf {
    path(parsed, name)
}

fn run_with_catalog(names: &[&str], parsed: &Parsed) -> Result<Value> {
    let catalog = read(parsed, "catalog")?;
    profiles::validate_catalog(&catalog)?;
    match names {
        ["prototype", "export"] => cir_config::native_export::export_native(
            &path(parsed, "store"),
            &catalog,
            &read(parsed, "world")?,
            &path(parsed, "out"),
            parsed.text("revision"),
            None,
            None,
        ),
        ["prototype", "prepare"] => cir_config::campaigns::write_campaign(
            &read(parsed, "profile")?,
            &catalog,
            &read(parsed, "world")?,
            &read(parsed, "plan")?,
            &path(parsed, "store"),
        ),
        ["world", "scan"] => {
            let destination = profiles::ensure_output_outside_game(&path(parsed, "out"), &catalog)?;
            let world = sources::scan_world(&catalog)?;
            profiles::write_json(&destination, &world, false)?;
            let source_rows = world.at("sources")?.as_list().cloned().unwrap_or_default();
            let empty = source_rows
                .iter()
                .filter(|row| {
                    row.get("concerns")
                        .and_then(Value::as_list)
                        .is_some_and(|concerns| concerns.iter().any(|concern| concern.as_str() == Some("empty-item-reference")))
                })
                .count();
            let mut result = Dict::new();
            result.insert("world_path", Value::Str(display(&destination)));
            result.insert("fingerprint", world.at("fingerprint")?.clone());
            result.insert("source_records", Value::from(source_rows.len()));
            result.insert("target_candidates", Value::from(world.at("targets")?.as_list().map_or(0, Vec::len)));
            result.insert("empty_item_records", Value::from(empty));
            for (key, value) in sources::inventory_diagnostic_counts(&world)?.iter() {
                result.insert(key, value.clone());
            }
            Ok(Value::Dict(result))
        }
        ["world", "sources"] => {
            let world = read(parsed, "world")?;
            sources::inspect_sources(
                &world,
                &catalog,
                parsed.text("item").unwrap_or(""),
                parsed.int("offset").unwrap_or(0),
                parsed.int("limit").unwrap_or(20),
            )
        }
        ["world", "targets"] => {
            let world = read(parsed, "world")?;
            sources::search_targets(
                &world,
                &catalog,
                parsed.text("query").unwrap_or(""),
                parsed.text("kind"),
                parsed.int("offset").unwrap_or(0),
                parsed.int("limit").unwrap_or(20),
            )
        }
        ["binding", operation] => {
            let world = read(parsed, "world")?;
            let profile = read(parsed, "profile")?;
            if *operation == "create" {
                let plan = sources::new_binding_plan(&profile, &catalog, &world)?;
                let destination = profiles::ensure_output_outside_game(&path(parsed, "out"), &catalog)?;
                profiles::write_json(&destination, &plan, false)?;
                return Ok(result_dict(vec![
                    ("plan_path", Value::Str(display(&destination))),
                    ("state", Value::from("needs-explicit-source-and-target-choices")),
                ]));
            }
            let plan = read(parsed, "plan")?;
            if *operation == "prepare" {
                return sources::prepare_bound_profile(&profile, &catalog, &world, &plan, &path(parsed, "store"));
            }
            let body = sources::bind_profile(&profile, &catalog, &world, &plan)?;
            Ok(result_dict(vec![
                ("valid", Value::Bool(true)),
                ("state", body.at("state")?.clone()),
                ("reward_units", body.at("reward_units")?.clone()),
                ("target_capacity", body.at("target_capacity")?.clone()),
                ("skipped", body.at("skipped")?.clone()),
            ]))
        }
        ["search"] => profiles::search_items_from(
            &catalog,
            parsed.text("query").unwrap_or(""),
            parsed.int("category"),
            &parsed.int_value("offset").unwrap_or(Value::Int(0)),
            parsed.int("limit").unwrap_or(20),
        ),
        ["prepare"] => profiles::prepare_profile(&read(parsed, "profile")?, &catalog, &path(parsed, "store")),
        ["profile", operation] => {
            let profile = match *operation {
                "create" => profiles::new_profile(&catalog, parsed.text("id").unwrap_or(""), parsed.text("name").unwrap_or(""))?,
                _ => {
                    let profile = read(parsed, "profile")?;
                    match *operation {
                        "validate" => {
                            profiles::validate_profile(&profile, &catalog, false)?;
                            return Ok(result_dict(vec![
                                ("valid", Value::Bool(true)),
                                ("state", Value::from("draft-needs-source-binding")),
                                ("selected_count", Value::from(profile.at("items")?.as_list().map_or(0, Vec::len))),
                            ]));
                        }
                        "rebase" => profiles::rebase_profile(&profile, &catalog)?,
                        _ => {
                            let include = parsed.all("include");
                            let exclude = parsed.all("exclude");
                            let name = parsed.text("name").map(Value::from);
                            profiles::edit_profile(
                                &profile,
                                &catalog,
                                profiles::ProfileEdit {
                                    include: &include,
                                    exclude: &exclude,
                                    name: name.as_ref(),
                                    ..Default::default()
                                },
                            )?
                        }
                    }
                }
            };
            let destination = profiles::ensure_output_outside_game(&path(parsed, "out"), &catalog)?;
            profiles::write_json(&destination, &profile, false)?;
            Ok(result_dict(vec![
                ("profile_path", Value::Str(display(&destination))),
                ("selected_count", Value::from(profile.at("items")?.as_list().map_or(0, Vec::len))),
            ]))
        }
        _ => unreachable!("every command is dispatched"),
    }
}

fn main() -> ExitCode {
    let arguments: Vec<String> =
        std::env::args_os().skip(1).map(|argument| argument.to_string_lossy().into_owned()).collect();
    let prog = program_name();
    let (path, parsed) = match argparse::parse(&arguments, &|path| parser_for(&prog, path)) {
        Ok(outcome) => (outcome.path, outcome.parsed),
        Err(code) => return ExitCode::from(code),
    };
    match run(&path, &parsed) {
        Ok(result) => {
            let mut output = Dict::new();
            output.insert("ok", Value::Bool(true));
            output.insert("result", result);
            print_line(&mut std::io::stdout(), &json::dumps_ascii(&Value::Dict(output), true));
            ExitCode::SUCCESS
        }
        Err(error) if error.is_reported() => {
            let mut output = Dict::new();
            output.insert("ok", Value::Bool(false));
            output.insert("error", Value::Str(error.message));
            print_line(&mut std::io::stderr(), &json::dumps_ascii(&Value::Dict(output), false));
            ExitCode::from(1)
        }
        Err(error) => {
            // The reference stops with a Python traceback for these.
            let kind = match error.kind {
                ErrorKind::Key => "KeyError",
                ErrorKind::Type => "TypeError",
                ErrorKind::GameText => "GameTextError",
                ErrorKind::Overflow => "OverflowError",
                ErrorKind::Attribute => "AttributeError",
                _ => "ValueError",
            };
            print_line(&mut std::io::stderr(), &format!("{kind}: {}", error.message));
            ExitCode::from(1)
        }
    }
}
