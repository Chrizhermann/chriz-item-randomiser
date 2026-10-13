[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT,
    [string] $PythonPath = '',
    # A built release ZIP to install instead of the working tree.
    [string] $PackagePath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$helperPath = Join-Path $PSScriptRoot 'campaign_runtime_installer.py'

if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED CampaignRuntimeInstaller_HelperMissing tests/campaign_runtime_installer.py'
    exit 1
}
if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    throw 'The campaign runtime installer test requires an explicit WeiDU executable path.'
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
    '--repository-root', $repositoryRoot,
    '--weidu', $WeiduPath,
    '--temp-root', $TempRoot
)
if (-not [string]::IsNullOrWhiteSpace($PackagePath)) {
    $arguments += @('--package', $PackagePath)
}

$output = @(& $PythonPath @arguments 2>&1 | ForEach-Object { [string] $_ })
$exitCode = $LASTEXITCODE
$output | Write-Output
if ($exitCode -ne 0) {
    exit $exitCode
}

$passRecords = @($output | Where-Object { $_ -match '^PASS [A-Za-z0-9_.-]+$' })
$summaryRecords = @($output | Where-Object { $_ -match '^SUMMARY passed=(\d+) failed=(\d+)$' })
if ($summaryRecords.Count -ne 1) {
    throw 'The campaign runtime installer test did not emit exactly one summary record.'
}
$summaryMatch = [regex]::Match($summaryRecords[0], '^SUMMARY passed=(\d+) failed=(\d+)$')
if (
    [int]($summaryMatch.Groups[1].Value) -ne $passRecords.Count -or
    [int]($summaryMatch.Groups[2].Value) -ne 0 -or
    $passRecords.Count -eq 0
) {
    throw 'The campaign runtime installer summary did not match its PASS records.'
}
