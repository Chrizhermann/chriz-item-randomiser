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

- Baldur's Gate: Enhanced Edition Trilogy (EET). Other games are planned.
- EEex with its LuaJIT component; start the game with `InfinityLoader.exe`.

## Installation

Extract the release into the game directory so the `cir` folder is beside `chitin.key`.
Supply a current WeiDU executable for your platform as `setup-cir`, then run it. Install
CIR after every mod that changes the user interface or the new-game template (`UI.MENU`,
`BALDUR.GAM`).

Installing changes nothing in play until a configuration is applied: after the whole
mod stack is installed, close the game and use the configurator in `cir/tools` to scan
the game and apply a configuration. See the [readme](readme-cir.html).

## Development

The [post-install configurator](docs/post-install-configurator.md) scans the installed
game and applies configurations; it currently needs Python 3.10 or later. The
[packaging plan](docs/plans/2026-10-14-mode1-packaging.md) describes the installer's
contract and what still blocks the first release.

Release packages are built from a commit, never the working tree, and are
byte-reproducible with the same zlib version. `tools/release-package.json` lists
the published files:

```powershell
python tools/build_release.py --commit HEAD --out <directory>
python tools/build_release.py --commit <commit> --verify <zip>
```

Use a disposable EET installation with its own profile for game tests. Tests should
cover mod behaviour and reproduced bugs. See [tests/README.md](tests/README.md) for
the test commands.

## Licence and credits

CIR would not be possible without Wisp's groundwork. The code and texts written for CIR
are under the [MIT licence](LICENSE). The material from Wisp's Item Randomiser (lists,
item files, texts, the optional components' dialogue and script changes) remains his
work and is included with credit; [LICENSE](LICENSE) lists it.

- [Item Randomiser source](https://github.com/FredrikLindgren/randomiser)
- [Item Randomiser on Gibberlings Three](http://www.gibberlings3.net/item_rand/)
