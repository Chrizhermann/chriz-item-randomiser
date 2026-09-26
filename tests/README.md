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
and LuaJIT 2.1. Missing runtimes fail its checks. See the
[configurator notes](../docs/post-install-configurator.md) and
[campaign prototype contract](../docs/campaign-runtime-prototype.md) for scope.

The complete legacy PowerShell/WeiDU/Lua runner requires explicit tool paths
and an EET sentinel fixture (31 decompiled `fltier*.baf` scripts from a Mode 1
installation). Run from the repository root with your own paths:

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1 `
    -WeiduPath 'D:\DisposableTools\weidu.exe' `
    -LuaPath 'D:\DisposableTools\lua.exe' `
    -SentinelBafDirectory 'D:\DisposableFixtures\sentinel-final'
```

Configured paths are resolved to absolute paths, must have no junction or
symlink ancestor, and cannot be inside the hardcoded live game directory.
The runner creates a short, unique directory under the system temporary
directory (WeiDU has path-length limits) and passes it as `FL_IR_TEST_TEMP_ROOT`. It removes
that directory after success and leaves it for diagnosis after failure.

The runner first checks sentinel dependencies and the other PowerShell
`Test-*.ps1` scripts in name order. It then parse-checks `randomiser.tp2`,
top-level `lib/*.tpa`, and `tests/weidu` TP2/TPA/TPP files with WeiDU. It
runs `tests/lua/run.lua` when present and runs `Test-ModeBoundary.ps1` last.
WeiDU is called with `--nogame`; the runner does not install or launch the
game. Child output is shown, and any nonzero exit fails the run.

The old local sentinel fixture was removed with its lab. That integration check
is currently unverified; the other twelve PowerShell suites and the Lua suite
passed independently with WeiDU 249 and short temporary paths on 27 September.
