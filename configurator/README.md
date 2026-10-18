# Chriz Item Randomiser configurator (Rust)

The post-install configurator as a Rust library and a standalone Windows
executable, so players need no Python. It is a port of `tools/flir_config`,
which stays the reference: the commands, the editor's HTTP interface, the JSON
results, the error messages and every file written are the same (see
[Parity](#parity-with-the-python-reference)).

- `cir-config` (library `cir_config`): scan, world scan, presets and their fit
  to a game, binding checks, campaign compile and store, native export and
  apply, for BG:EE, BG2:EE and EET with one revision per campaign start. Chriz
  Easy BG links this crate.
- `cir-configurator`: the executable with the reference's command line and the
  local editor. Started without arguments in the game's `cir` folder, it opens
  the editor for that game ([Started without arguments](#started-without-arguments)).

The mod data the configurator reads (the tables under `lists/`, the BG1 name
maps, Item Randomiser's texts in every language under `languages/`, the mod's
stand-in items and the editor's files) is embedded when it is built, so the
executable runs on its own, without a checkout.

The presets read Item Randomiser's BG1 and BG2 tables. Siege of
Dragonspear's own tables are Chris's to design (`docs/sod-lists.md`); the code
that reads them stayed, as in the reference: a preset campaign that names them
is offered on BG:EE and EET only when the game has Siege of Dragonspear's
areas. On EET, BG1 and SoD names follow the game's own rename lists
(`EET/lib/pcu_dict.tph`), as the reference reads them; where they are missing
or cannot be read, the built-in copy of `lib/fl#bg1pal.tpa` is used and the
fit says so in its notes.

Item Randomiser's reward texts appear in the game's language (`lang/<code>`),
as with the reference: the translation is read in its language's code page
(decoded strictly, with tables taken from Python's own codecs: `codepages/`,
written by `configurator/parity/codepages.py`), and each text goes to
`dialog.tlk` and, where the language has one, to `dialogF.tlk` under the same
number, with its female wording. A language without a translation, or a
`dialogF.tlk` with another number of strings, leaves the text routes and the
golem chests out of the configuration with the reason.

## Build

```powershell
cargo build --release --manifest-path configurator/Cargo.toml
```

The executable is `configurator/target/release/cir-configurator.exe`. The only
dependency is `sha2` (SHA-256 of every scanned and exported resource).

## Windows package

Players get the executable in the standalone package, which
`tools/build_standalone.py` builds with the mod ZIP from one commit:

```powershell
python tools/build_standalone.py --commit HEAD --weidu 'D:/DisposableTools/weidu.exe' --out 'D:/CirRelease'
```

- `cir-v<VERSION>.zip` and its manifest: the mod ZIP of
  `tools/build_release.py`, byte for byte. CEBG installs this one, so the
  builder refuses it beyond CEBG's archive limits (1,024 entries, 4 MiB per
  entry, 16 MiB in total, compression ratio 100, depth 8); the executable
  alone is larger than 4 MiB.
- `cir-v<VERSION>-windows.zip` and its manifest: the same `cir/` folder with
  `cir/cir-configurator.exe`, and `setup-cir.exe` beside it. That is WeiDU
  249.00 for x86-64, the build the installer tests use: `--weidu` must be
  `WeiDU-Windows/weidu.exe` from
  [WeiDU-Windows-249-amd64.zip](https://github.com/WeiDUorg/weidu/releases/tag/v249.00)
  (1,364,992 bytes, SHA-256
  `ad70f5897a6d0ba4b0d226f845a9b14cf345f56cc9697ca8d05cac9fe4932c1a`), whose
  README allows distributing an unmodified binary copy. The manifest records
  the size and SHA-256 of both programs, WeiDU's origin and the Rust toolchain.

The executable is built from an export of the commit with the mod ZIP's line
endings, so it embeds the bytes the mod installs: `cargo build --release
--locked --bin cir-configurator --target x86_64-pc-windows-msvc`, with the C
runtime linked statically (players need not have the Visual C++ runtime), the
build folders remapped and a linker that writes no build time. The builder
refuses an executable that needs the Visual C++ runtime or names a folder of
the build machine. The same Rust and Visual C++ tools build the same
executable: `--verify <package>` instead of `--out` rebuilds the package and
compares it byte for byte. The executable runs on Windows 10 and later, as
Rust's standard library does.

Players unpack the package into the game folder, run `setup-cir.exe` after
every other mod (it finds `cir/cir.tp2` by its own name) and then double-click
`cir/cir-configurator.exe`. Neither program is signed, so Windows SmartScreen
may ask before the first start. `configurator/parity/package_check.py` goes
through these steps on synthetic games
([Parity](#parity-with-the-python-reference)).

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

### Started without arguments

After installation the executable is in the game's `cir` folder. Started
without arguments, as a double-click starts it, it opens the editor for that
game:

1. The game is the nearest folder at or above the executable's folder that
   has `chitin.key` (in any case). Nothing else on the PC is searched, so there
   is never a choice between installations to guess.
2. Chriz Item Randomiser must be installed there: `override/flrandomiser3.mrk`,
   or `WeiDU.log` recording `cir.tp2` component 0 (as `MOD_IS_INSTALLED` reads
   it: any folder, with or without `setup-`).
3. It scans the game's items, areas, creatures and shops in the language
   `weidu.conf` names (`lang_dir`, when the game has that `dialog.tlk`;
   otherwise the scan takes the game's only one) into this player's folder,
   outside the game:
   `%LOCALAPPDATA%\Chriz Item Randomiser\<game folder name>-<hash>\` (the
   name's first 40 characters and 12 hex digits of the SHA-256 of the game's
   path) with `catalog.json`, `world.json` and the editor's workspace
   `configurations\`, as `scan`, `world scan` and `ui` write them. Each game
   keeps its folder, so the next start reopens the configuration applied last
   and Apply finds the earlier revisions.
4. It opens the editor in the browser with Apply for that game (as
   `ui --apply-game`) and serves it until its window is closed or Ctrl+C.

If there is no game above the executable, or CIR is not installed in it, it
says so and opens Windows' folder window to choose the game folder. The
chosen folder is checked the same way (the nearest folder at or above it with
`chitin.key`, then CIR in it), and the window opens again, where the last
answer was, until a game with CIR is chosen or the window is closed. Errors
are told in plain words; a console window that would close with the program
waits for Enter first. Outside Windows the folder is asked for on the
terminal.

Every start scans again, so the editor sees the game as it is. After the
game's mods changed, the configurations saved before cannot be opened with the
new scan, and the editor would refuse to open a workspace that holds them (the
reference's `ui` answers its state with "Profile belongs to another scan"
then). They are moved to `configurations\earlier scans\`, where they are kept,
and the editor starts with a new configuration; Apply keeps the earlier
revisions for existing saves.

With any argument the executable is the command line above, unchanged.
`CIR_NO_BROWSER` (any value) makes the start without arguments only print the
editor's address.

## Library

The library works on JSON values (`cir_config::json::Value`, with Python's key
order and number rules) and returns `cir_config::Error`, whose `ErrorKind`
names the reference's exception class. The entry points are
`resources::scan_game`, `sources::scan_world`, `platform::{game_platform,
preset_campaigns, preset_game}` (which game a world is, which presets it is
offered and which fit it), `game_names::eet_names` (an EET game's own rename
lists, or CIR's copy with a note),
`templates::{list_templates, build_template, recommended_preset}`,
`original_configuration::fit_original_configuration`,
`campaigns::{compile_campaign, write_campaign}`, `starts::{write_starts,
describe_starts}` (one revision for each campaign start the configuration
reaches), `native_export::export_native` (one revision, or the revisions of
every start with the main start: `next.lua` schema `flir-next-v2`),
`activation::{validate_runtime, deploy_export}` and `editor::Editor`, the
editor's requests (`get`, `post`) without the HTTP server, with
`editor::{saved_files, checked_draft}` (the configurations the editor lists
and its check of one against the scans). `game_folder::{find_game,
cir_installed, game_language, game_workspace}` are the start without
arguments: the game at or above a folder, whether CIR is installed in it, the
language WeiDU installed its mods in and its folder for scans and
configurations.

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
python configurator/parity/player_check.py
python configurator/parity/package_check.py '<package ZIP>' --temp-root '<short folder>'
python configurator/parity/game_check.py --game '<game folder>' --language en_US --scratch '<empty folder>'
cargo test --manifest-path configurator/Cargo.toml --features cir-config/parity-overrides
```

A plain `cargo test` or `cargo build` replaces the runner with one that
cannot apply the reference tests' overrides; the runner then refuses them, so
build with the feature again before the checks.

- `replay_tests.py` runs the reference's test suite and repeats every library
  call it makes (scan, world scan, game detection, EET's rename lists,
  presets and their fit,
  binding, campaign compile, store and read, the revisions per campaign start,
  native export, also over earlier revisions that saved games keep) with the
  Rust port, comparing
  results, errors and the files written. Tests that inject faults into Python
  internals (a patched `open` or directory listing) cannot be repeated and are
  listed as not compared. Where a test points `translations.LANGUAGES_DIR` at a
  copy of the translations, the Rust port reads that folder
  (`CIR_PARITY_LANGUAGES`, parity builds only).
- `cli_check.py` runs the command line on a synthetic installation: the whole
  workflow up to the native export, argparse's handling of unusual arguments,
  and every command's help at several terminal widths.
- `editor_check.py` sends the same session to both editors: static files, every
  request, malformed requests and the files written into the workspace.
- `player_check.py` (Windows) starts the executable without arguments, copied
  into synthetic games, on a Windows desktop of its own that is never shown,
  and answers its folder window there as a player would. In `cir` and in
  `cir/tools` it finds its game, writes the reference's scans (in
  `weidu.conf`'s language) outside the game and serves the editor state of
  `ui --apply-game`; then Apply, reopening the applied configuration, a change
  of the game's mods, a start outside any game (a folder without a game, a
  game without CIR, then a folder inside a game whose `WeiDU.log` records CIR),
  a game without CIR whose folder window is closed, and an incomplete
  installation.
- `package_check.py` (Windows) unpacks a standalone package into the installer
  suite's synthetic BG2:EE, BG:EE and EET games, installs CIR with the
  package's `setup-cir.exe` (component 0, as batch installers run it) and
  starts its `cir/cir-configurator.exe` without arguments, as
  `player_check.py` does: the scans and the editor state equal the
  reference's, nothing is written into the game before Apply, and Apply
  works. It needs no build; WeiDU's path-length limits want a short
  `--temp-root`.
- `game_check.py` compares a real installation, used read-only: catalog and
  world, then every preset with each pool and several option sets (the fitted
  preset with its left-out rows, the unfitted build with the rows the runtime
  check refuses, the compiled campaign and its native compilation) and, for
  some, the campaign store and the native export written into the scratch
  folder: one revision as the command line exports it, and one for each
  campaign start as the editor's Apply writes them. The export copies game
  resources, so keep the scratch folder outside the repository.
- The Rust tests include byte-parity fixtures for the resource patchers,
  generated from synthetic inputs by `configurator/parity/patchers/generate.py`
  (for the extended pools' reward routes: recognition by meaning in dialogues
  and compiled scripts, what leaves a route out and why, other mods' layouts,
  copies of reward replies in other dialogues as WeiDU's interjections leave
  them, and re-export of the configurator's own earlier patches; the game text
  in `dialog.tlk` and `dialogF.tlk`; the translations of every shipped
  language, translation folders as WeiDU leaves them, the TRA reader and every
  one- and two-byte sequence of each code page), and the game folder lookup:
  the executable in `cir` and in `cir/tools`, no game above it, the nearest of
  nested games, CIR not installed (other components, other mods, commented log
  lines) and installed by marker or log.
  The folder window's interface is checked without showing the window.
  The reference's checks on its reviewed EET resources run, as there, only
  when its resource cache `work/original-preset-runtime-20260929/mode2-resources`
  is present; it is not distributed.
- `generate.py --game '<game folder>' --language en_US --out '<folder>'`
  (or another of the game's languages, such as `de_DE`)
  records the reward routes of an installed game, used read-only: each route
  resource patched as the export patches it, recognised before and after, and
  exported again, and the copies of reward replies in the game's other
  dialogues, as the scan finds them and in the game as Apply leaves it.
  `CIR_PARITY_GAME_ROUTES='<folder>/game_routes.json'` runs it
  with `cargo test --test parity_game_routes`. The fixture holds the game's
  bytes, so the folder must be outside the repository.

`CIR_PARITY_TARGET=configurator/target/release` makes the checks use a release
build.

### Known differences

- Without arguments the reference prints its usage (exit status 2); the
  executable opens the editor for its game instead.
- The editor's `Server` header names `cir-configurator` instead of Python's
  `BaseHTTP`.
- Errors that the reference lets escape as a Python traceback (`KeyError`,
  `TypeError` and the like from malformed input files) end with one line naming
  the same error class and message, with the same exit status 1.
- An editor request whose error escapes the reference's handler
  (`AttributeError` or `OverflowError` from malformed scans or saved files)
  gets no answer in either editor: the connection closes. The Rust editor
  prints one line to stderr instead of Python's traceback.
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
- The translations are the ones the configurator was built with. The reference
  reads the mod's `languages/` folder, so a translation edited in an installed
  game reaches only the reference (and its check rereads that folder when it
  changes); WeiDU's in-place conversion of that folder changes no text.
- A TRA line whose number exceeds the 64-bit range is not read; the reference
  reads it. No text CIR reads has one.
