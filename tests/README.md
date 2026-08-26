# Hermetic unit tests

Run the repository checks from the repository root:

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1
```

The runner has three fixed, disposable defaults. It reads the WeiDU and Lua executables from
`C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64` and the final sentinel
fixture from
`C:\Users\chris\Games\EET-IR-Test-b600e94\decompile-sentinel-final-20260827-064808`.
It does not infer a game directory and does not pass a game directory to WeiDU.

Safe alternate copies can be supplied explicitly:

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1 `
    -WeiduPath 'D:\DisposableTools\weidu.exe' `
    -LuaPath 'D:\DisposableTools\lua.exe' `
    -SentinelBafDirectory 'D:\DisposableFixtures\sentinel-final'
```

Every configured path must resolve through a fixed local drive to a physical device. The runner
rejects UNC/admin-share and device paths, non-fixed roots, SUBST mappings, short-name (`8.3`)
aliases, and filesystem reparse points. It expands each existing path to its long canonical form
and compares its physical device-relative identity with the forbidden live root before use. The
forbidden live root is always denied lexically; when it exists, its own segments must also be
reparse-free and its long physical identity is used for comparisons. The same rules are applied to
the caller's captured system temporary directory before a scratch directory is created.

Native path checks require a small compiled helper. Before compiling it, the runner validates the
Local Application Data `Temp` directory using managed APIs only: it must be an existing long local
drive-letter path on a ready fixed drive backed by a disk partition, outside the live root, with no
reparse-point segment. The compiler receives a unique child of that directory as `TEMP` and `TMP`;
the caller's values are restored and the verified child is removed before the caller's captured
temporary path is physically validated. WeiDU then runs from a fresh directory beneath that
caller-requested, validated scratch parent, and only that exact directory is removed after the
run.

Executable names and Windows version resources are also mandatory. `weidu.exe` must report product
name `WeiDU`, description `Weimer Dialogue Utilities`, file/product version `249.00`, and original
filename `weidu.exe`. `lua.exe` must report product name `Lua - The Programming Language`,
description `Lua Console Standalone Interpreter`, file/product version `5.3.3`, and original
filename `lua53.exe`. Renamed or unknown executables are rejected without execution.

The runner never installs or launches a game, never writes a game profile or save, and must not be
pointed at a live installation. Behavioral WeiDU harnesses live under `tests\weidu`; their
matching PowerShell tests invoke them with fixture-specific arguments, while the common runner
parse-checks every harness and production TPA/TP2 first-class source it discovers.

The order is intentional: the campaign-sentinel fixture and other PowerShell tests run first,
then WeiDU parse-checks and any Lua unit suite, and the Mode boundary guard runs last. Lua 5.3.3
provides syntax and unit coverage when `tests\lua\run.lua` exists. There is no standalone LuaJIT
executable in this test setup; LuaJIT 5.1 coverage is performed only inside the isolated EET game
through the serialized EEex Remote Console.

## Intentional RED during the redesign

Until Task 8 implements the approved Mode 1 backend seam, the final assertion is expected to be:

```text
FAIL FUTURE_Mode1_ExplicitManifestVersusLegacyDeliveryBranch
```

The runner reports this as `INTENTIONAL_RED` and exits nonzero. It does not suppress the failure or
convert it into a pass. All Mode 2 immutability and isolation assertions must pass before the runner
reaches that known future assertion. Once the explicit EEex-manifest-versus-legacy-delivery branch
exists, the same Mode boundary test and runner are expected to exit successfully.

The runner accepts only the exact ten named assertion records, each exactly once, followed by one
count-consistent summary. It validates captured output before printing it: unexpected or malformed
child output is replaced by a generic protocol failure rather than echoed.
