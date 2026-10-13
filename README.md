# Item Randomiser

> Original mod by Fredrik Lindgren (Wisp) <br />
> Maintained by Christopher Hermann for Chriz's BG Collection

Item Randomiser is a large-scale random treasure mod for Baldur's Gate and Baldur's
Gate II. This repository is a public maintained fork of Wisp's original project and
can be used independently of Chriz's BG Collection.

## Maintained fork

Version 8 and later focus on reliable Mode 1 delivery on EET and compatible Enhanced
Edition installations, durable EEex transactions that do not depend on recipient AI
queues, extensible item/source/endpoint registries, and validation before source
removal. The existing WeiDU-based Mode 2 path remains isolated.

## Installation

Download the latest release and extract it into the game directory so the
`randomiser` folder is beside `chitin.key`. Supply a current WeiDU executable for
your platform under the usual `setup-randomiser` name, then run it normally.

## Documentation and support

- [Full manual](readme-randomiser.html)
- [Latest release](https://github.com/Chrizhermann/chriz-item-randomiser/releases/latest)
- [Report a problem](https://github.com/Chrizhermann/chriz-item-randomiser/issues)
- [Original source](https://github.com/FredrikLindgren/randomiser)
- [Original Gibberlings Three page](http://www.gibberlings3.net/item_rand/)

The package directory, TP2 name, component numbers, and `fl*` runtime identifiers
remain unchanged for WeiDU and saved-game compatibility.

## Development

The [experimental post-install configurator](docs/post-install-configurator.md)
indexes installed items and prepares reusable configuration drafts. Component
1500 installs the campaign runtime it configures on EET with EEex and LuaJIT; see
the [packaging plan](docs/plans/2026-10-14-mode1-packaging.md) for its contract and
what still blocks a release. Collection UI integration is still in development.

Release packages are built from a commit, never the working tree, and are
byte-reproducible with the same zlib version. `tools/release-package.json` lists
the published files:

```powershell
python tools/build_release.py --commit HEAD --out <directory>
python tools/build_release.py --commit <commit> --verify <zip>
```

Use the disposable EET installation and its separate profile for game tests;
rebuild it when needed. Keep supporting tools small and reuse the existing
console and character helper. Tests should cover mod behavior and reproduced
bugs. Avoid adding lab integrity frameworks or another report for every run.
See [tests/README.md](tests/README.md) for the current test commands.

## License and provenance

The upstream repository does not declare a repository-wide license. This fork does
not claim to relicense the original work. Individual files that carry their own
license notices retain those terms.
