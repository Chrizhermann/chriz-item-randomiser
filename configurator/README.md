# Chriz Item Randomiser configurator (Rust)

The post-install configurator as a Rust library and a standalone Windows
executable, so players need no Python. It is a port of `tools/flir_config`,
which stays the reference: the commands, the editor's HTTP interface, the JSON
results, the error messages and every file written are the same (see
[Parity](#parity-with-the-python-reference)).

- `cir-config` (library `cir_config`): scan, world scan, presets and their fit
  to a game, binding checks, campaign compile and store, native export and
  apply. Chriz Easy BG links this crate.
- `cir-configurator`: the executable with the reference's command line and the
  local editor.

The mod data the configurator reads (the tables under `lists/`, the BG1 name
maps, game texts, the mod's stand-in items and the editor's files) is embedded
when it is built, so the executable runs on its own, without a checkout.

## Build

```powershell
cargo build --release --manifest-path configurator/Cargo.toml
```

The executable is `configurator/target/release/cir-configurator.exe`. The only
dependency is `sha2` (SHA-256 of every scanned and exported resource).

## Run

The commands are those of `python tools/randomiser_config.py`, described in
[the configurator documentation](../docs/post-install-configurator.md); every
command has `-h`.

```powershell
cir-configurator scan --game 'D:/DisposableEET' --language en_US --out 'D:/RandomiserDrafts/catalog.json'
cir-configurator world scan --catalog 'D:/RandomiserDrafts/catalog.json' --out 'D:/RandomiserDrafts/world.json'
cir-configurator ui --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --workspace 'D:/RandomiserDrafts/configurations'
```

`ui` opens the editor in the default browser; `--no-browser` only prints its
address, like the reference. Ctrl+C closes the editor. Game folders are only
read; outputs must be outside the scanned game, and `--apply-game` is needed to
change a test game, as with the reference.

## Library

The library works on JSON values (`cir_config::json::Value`, with Python's key
order and number rules) and returns `cir_config::Error`, whose `ErrorKind`
names the reference's exception class. The entry points are
`resources::scan_game`, `sources::scan_world`, `templates::{list_templates,
build_template, recommended_preset}`,
`original_configuration::fit_original_configuration`,
`campaigns::{compile_campaign, write_campaign}`, `native_export::export_native`,
`activation::{validate_runtime, deploy_export}` and `editor::Editor`, the
editor's requests (`get`, `post`) without the HTTP server.

## Parity with the Python reference

Build the Rust side with the test-only hash overrides (never part of a
release build), then run the checks from the repository root with Python 3.11
or later. The replay needs what the Python suite needs: `keystone-engine==0.9.2`
and `unicorn==2.1.4` on `PYTHONPATH`.

```powershell
cargo build --manifest-path configurator/Cargo.toml --features cir-config/parity-overrides --bins --examples
python configurator/parity/replay_tests.py
python configurator/parity/cli_check.py
python configurator/parity/editor_check.py
python configurator/parity/game_check.py --game '<game folder>' --language en_US --scratch '<empty folder>'
cargo test --manifest-path configurator/Cargo.toml --features cir-config/parity-overrides
```

- `replay_tests.py` runs the reference's test suite and repeats every library
  call it makes (scan, world scan, presets and their fit, binding, campaign
  compile, store and read, native export) with the Rust port, comparing
  results, errors and the files written. Tests that inject faults into Python
  internals (a patched `open` or directory listing) cannot be repeated and are
  listed as not compared.
- `cli_check.py` runs the command line on a synthetic installation: the whole
  workflow up to the native export, argparse's handling of unusual arguments,
  and every command's help at several terminal widths.
- `editor_check.py` sends the same session to both editors: static files, every
  request, malformed requests and the files written into the workspace.
- `game_check.py` compares a real installation, used read-only: catalog and
  world, then every preset with each pool and several option sets (the fitted
  preset with its left-out rows, the unfitted build with the rows the runtime
  check refuses, the compiled campaign and its native compilation) and, for
  some, the campaign store and the native export written into the scratch
  folder. The export copies game resources, so keep the scratch folder outside
  the repository.
- The Rust tests include byte-parity fixtures for the resource patchers,
  generated from synthetic inputs by `configurator/parity/patchers/generate.py`.

`CIR_PARITY_TARGET=configurator/target/release` makes the checks use a release
build.

### Known differences

- The editor's `Server` header names `cir-configurator` instead of Python's
  `BaseHTTP`.
- Errors that the reference lets escape as a Python traceback (`KeyError`,
  `TypeError` and the like from malformed input files) end with one line naming
  the same error class and message, with the same exit status 1.
- The command line writes UTF-8; Python uses the console's or locale's
  encoding, which matters only for non-ASCII text echoed in usage errors.
- Outside Windows the help text wraps at `$COLUMNS` or 80 columns rather than
  the terminal's width.
- A lone UTF-16 surrogate escape in an editor request body becomes U+FFFD;
  Rust strings cannot hold it. Saved files reject such strings in both.
- The library takes template ids and revisions as text; the reference also
  accepts other JSON values there and fails later with Python's errors.
- Python's NFKC check of the host in absolute-form request targets is not
  ported; the editor only ever receives ordinary paths from the browser.
