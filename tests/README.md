# Repository checks

Install the test dependencies, then run the complete standalone Python suite
from the repository root. It uses synthetic fixtures and needs no game
installation:

```powershell
python -m pip install -r .\tests\requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

For a focused run:

```powershell
python -m unittest discover -s tests -p 'test_postinstall_*.py' -v
python -m unittest discover -s tests -p 'test_campaign_runtime.py' -v
```

The campaign runtime suite requires a `lupa` build with Lua 5.1, Lua 5.3,
and LuaJIT 2.1; missing runtimes fail its checks. It runs the Lua cases in
`tests/lua`. See the [configurator notes](../docs/post-install-configurator.md)
and [campaign prototype contract](../docs/campaign-runtime-prototype.md) for scope.

## Installer

`cir.tp2` has a WeiDU suite on synthetic EET games that needs only WeiDU 249
and a short temporary directory (WeiDU has path-length limits):

```powershell
pwsh -NoProfile -File .\tests\Test-CampaignRuntimeInstaller.ps1 `
    -WeiduPath 'D:\DisposableTools\weidu.exe' -TempRoot 'D:\Temp\cir'
```

It stages the files the release package publishes from the working tree. Add
`-PackagePath <zip>` to install a ZIP built by `tools/build_release.py` instead.
WeiDU is called on synthetic games only; the suite never installs into or
launches a real game.
