# Chriz Item Randomiser (CIR)

> By Christopher Hermann, built on [Item Randomiser](https://github.com/FredrikLindgren/randomiser)
> by Fredrik Lindgren (Wisp)

Chriz Item Randomiser shuffles where items are found in Baldur's Gate, anew for each
adventure you start. It works while you play, through EEex: install it once, choose your
settings in the configurator, and every new game gets its own randomisation. Saves keep
the randomisation they started with.

CIR is a new mod built on Wisp's Item Randomiser and reuses its item and location lists,
item files and texts. It cannot be combined with Item Randomiser's own randomisation
(Mode 1 or Mode 2). It also carries the optional components of Item Randomiser that
players use, under their original numbers: Cespenar can forge SoA items (9000), fewer
Protection from Undead scrolls in stores (10100, 10200), the Duergar merchant's two
stores (10210) and Watcher's Keep statues that stay (10300). These need neither EET nor
EEex and also install on BG2:EE.

CIR is in development and has no release yet.

## Requirements

- Baldur's Gate: Enhanced Edition, Baldur's Gate II: Enhanced Edition or the Enhanced
  Edition Trilogy (EET). Siege of Dragonspear, where a store installed it as a DLC
  archive (`sod-dlc.zip`), must be merged into the game first, for example with DLC
  Merger.
- Game version 2.7.3.0 or 2.6.6.0, the versions EEex 1.2 supports.
- EEex with its LuaJIT component; start the game with `InfinityLoader.exe`.

## Installation

Extract the release into the game directory so the `cir` folder is beside `chitin.key`,
then run `setup-cir`. The Windows package (`cir-v<version>-windows.zip`) contains
`setup-cir.exe`, a copy of WeiDU, and the configurator; with the mod archive alone
(`cir-v<version>.zip`), supply a current WeiDU executable for your platform as
`setup-cir`. Install CIR after every mod that changes the user interface or the new-game
template (`UI.MENU`, `BALDUR.GAM`).

Installing changes nothing in play until a configuration is applied: after the whole
mod stack is installed, close the game and double-click `cir/cir-configurator.exe` (from
the Windows package). It scans the game and opens the configurator in the browser to
apply a configuration; it needs no Python. See the [readme](readme-cir.html).

## Development

The [post-install configurator](docs/post-install-configurator.md) scans the installed
game and applies configurations. Players use `cir-configurator.exe`, its Rust build in
[configurator](configurator/README.md); the Python reference in `tools/flir_config`
needs Python 3.10 or later. The
[packaging plan](docs/plans/2026-10-14-mode1-packaging.md) describes the installer's
contract.

Release packages are built from a commit, never the working tree, and are
byte-reproducible with the same zlib version. `tools/release-package.json` lists
the published files. `build_release.py` writes the mod archive; `build_standalone.py`
writes it together with the Windows package (WeiDU as `setup-cir.exe` and the
configurator):

```powershell
python tools/build_release.py --commit HEAD --out <directory>
python tools/build_release.py --commit <commit> --verify <zip>
python tools/build_standalone.py --commit HEAD --weidu <weidu.exe> --out <directory>
python tools/build_standalone.py --commit <commit> --weidu <weidu.exe> --verify <windows zip>
```

`python tools/engine_patterns.py <Baldur.exe>` checks whether the runtime finds all
its engine hook locations in a game executable, for example a new game version.

Use a disposable game installation (EET, BG:EE or BG2:EE) with its own profile for game tests. Tests should
cover mod behaviour and reproduced bugs. See [tests/README.md](tests/README.md) for
the test commands.

## Licence and credits

CIR would not be possible without Wisp's groundwork. The code and texts written for CIR
are under the [MIT licence](LICENSE). The material from Wisp's Item Randomiser (lists,
item files, texts, the optional components' dialogue and script changes) remains his
work and is included with credit; [LICENSE](LICENSE) lists it. The Boss Loot preset
lists in [lists/presets/boss-loot](lists/presets/boss-loot/README.md) are aShuN's work,
also included with credit.

- [Item Randomiser source](https://github.com/FredrikLindgren/randomiser)
- [Item Randomiser on Gibberlings Three](http://www.gibberlings3.net/item_rand/)
