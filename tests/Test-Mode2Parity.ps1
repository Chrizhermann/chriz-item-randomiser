[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT,
    [string] $PythonPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$helperPath = Join-Path $PSScriptRoot 'ie_fake_game.py'
$contractPath = Join-Path $PSScriptRoot 'fixtures\game\publication-contract.json'

if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED Mode2Parity_HelperMissing tests/ie_fake_game.py'
    exit 1
}
if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED Mode2Parity_ContractMissing tests/fixtures/game/publication-contract.json'
    exit 1
}

if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    throw 'Mode 2 parity requires an explicit WeiDU executable path.'
}
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $PythonPath = $pythonCommand.Source
}

$arguments = @(
    $helperPath,
    'mode2-parity',
    '--repository-root', $repositoryRoot,
    '--weidu', $WeiduPath,
    '--temp-root', $TempRoot,
    '--contract', $contractPath,
    '--baseline-commit', 'b600e94'
)

$output = @(& $PythonPath @arguments 2>&1 | ForEach-Object { [string] $_ })
$exitCode = $LASTEXITCODE
$output | Write-Output
if ($exitCode -ne 0) {
    exit $exitCode
}

$passRecords = @($output | Where-Object { $_ -match '^PASS [A-Za-z0-9_.-]+$' })
$summaryRecords = @($output | Where-Object { $_ -match '^SUMMARY passed=(\d+) failed=(\d+)$' })
if ($summaryRecords.Count -ne 1) {
    throw 'Mode 2 parity did not emit exactly one summary record.'
}
$summaryMatch = [regex]::Match($summaryRecords[0], '^SUMMARY passed=(\d+) failed=(\d+)$')
if (
    [int]($summaryMatch.Groups[1].Value) -ne $passRecords.Count -or
    [int]($summaryMatch.Groups[2].Value) -ne 0 -or
    $passRecords.Count -eq 0
) {
    throw 'Mode 2 parity summary did not match its PASS records.'
}
