[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$helperPath = Join-Path $PSScriptRoot 'ProbeFixture.Helpers.ps1'
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw 'Missing ProbeFixture.Helpers.ps1.'
}
. $helperPath

$skipFailure = $null
try {
    Assert-ProbeWeiDUResult `
        -ExitCode 0 `
        -CapturedText "SKIPPING: synthetic prerequisite" `
        -FailureCode 'INSTALL_FAILED' `
        -RejectSkipping
}
catch {
    $skipFailure = $_.Exception.Message
}
if ($skipFailure -cne 'FLIR_PROBE_ERR INSTALL_FAILED') {
    throw 'A zero-exit WeiDU SKIPPING result was accepted for installation.'
}
Assert-ProbeWeiDUResult `
    -ExitCode 0 `
    -CapturedText "SKIPPING: already absent" `
    -FailureCode 'UNINSTALL_FAILED'

$stateRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('flir-state-test-' + [guid]::NewGuid().ToString('N'))
$stateOverride = Join-Path $stateRoot 'override'
$stateLog = Join-Path $stateRoot 'WeiDU.log'
$null = New-Item -ItemType Directory -Path $stateOverride
try {
    $stateFailure = $null
    try {
        Assert-FixtureInstalled -GameRoot $stateRoot -WeiduLogPath $stateLog
    }
    catch {
        $stateFailure = $_.Exception.Message
    }
    if ($stateFailure -cne 'FLIR_PROBE_ERR INSTALL_STATE_INVALID') {
        throw 'A missing active component/output set was accepted as installed.'
    }

    foreach ($outputPath in @(Get-FixtureOutputPaths -GameRoot $stateRoot)) {
        [System.IO.File]::WriteAllBytes($outputPath, [byte[]] @())
    }
    [System.IO.File]::WriteAllLines($stateLog, @(
        '// ~SETUP-PROBE-FIXTURE.TP2~ #0 #0 // old entry',
        '~SETUP-PROBE-FIXTURE.TP2~ #0 #0 // active entry'
    ))
    Assert-FixtureInstalled -GameRoot $stateRoot -WeiduLogPath $stateLog

    [System.IO.File]::AppendAllText(
        $stateLog,
        "~SETUP-PROBE-FIXTURE.TP2~ #0 #0 // duplicate active entry`r`n"
    )
    $stateFailure = $null
    try {
        Assert-FixtureInstalled -GameRoot $stateRoot -WeiduLogPath $stateLog
    }
    catch {
        $stateFailure = $_.Exception.Message
    }
    if ($stateFailure -cne 'FLIR_PROBE_ERR INSTALL_STATE_INVALID') {
        throw 'Duplicate active WeiDU.log entries were accepted as installed.'
    }

    foreach ($outputPath in @(Get-FixtureOutputPaths -GameRoot $stateRoot)) {
        [System.IO.File]::Delete($outputPath)
    }
    [System.IO.File]::WriteAllLines($stateLog, @(
        '// ~SETUP-PROBE-FIXTURE.TP2~ #0 #0 // inactive entry'
    ))
    Assert-FixtureUninstalled -GameRoot $stateRoot -WeiduLogPath $stateLog
}
finally {
    if (Test-Path -LiteralPath $stateRoot -PathType Container) {
        [System.IO.Directory]::Delete($stateRoot, $true)
    }
}

$sentinel = 'SENTAREA'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('flir-helper-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $testRoot
try {
    $overrideDonor = Join-Path $testRoot ($sentinel + '.ARE')
    [System.IO.File]::WriteAllBytes($overrideDonor, [byte[]] @(1))
    $missingParent = Join-Path $testRoot 'missing-copy-parent'
    $privateDonor = Join-Path $missingParent ($sentinel + '-private.are')
    $copyFailure = $null
    $copyDiagnostic = $null
    try {
        Invoke-ProbeDonorAcquisitionOpaque `
            -OverrideDonor $overrideDonor `
            -PrivateDonor $privateDonor `
            -ExtractedDonor (Join-Path $testRoot 'unused.are') `
            -ExtractDonor { throw "unreachable $sentinel" }
    }
    catch {
        $copyFailure = $_.Exception.Message
        $copyDiagnostic = $_ | Out-String
    }
    if ($copyFailure -cne 'FLIR_PROBE_ERR DONOR_ACQUISITION_FAILED' -or
        $copyFailure.Contains($sentinel, [System.StringComparison]::OrdinalIgnoreCase) -or
        $copyDiagnostic.Contains($sentinel, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A donor copy failure exposed the private area token.'
    }

    Remove-Item -LiteralPath $overrideDonor
    $extractedDonor = Join-Path $testRoot ($sentinel + '-extracted.are')
    $missingMoveParent = Join-Path $testRoot 'missing-move-parent'
    $moveFailure = $null
    $moveDiagnostic = $null
    try {
        Invoke-ProbeDonorAcquisitionOpaque `
            -OverrideDonor $overrideDonor `
            -PrivateDonor (Join-Path $missingMoveParent ($sentinel + '-private.are')) `
            -ExtractedDonor $extractedDonor `
            -ExtractDonor { [System.IO.File]::WriteAllBytes($extractedDonor, [byte[]] @(1)) }
    }
    catch {
        $moveFailure = $_.Exception.Message
        $moveDiagnostic = $_ | Out-String
    }
    if ($moveFailure -cne 'FLIR_PROBE_ERR DONOR_ACQUISITION_FAILED' -or
        $moveFailure.Contains($sentinel, [System.StringComparison]::OrdinalIgnoreCase) -or
        $moveDiagnostic.Contains($sentinel, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A donor move failure exposed the private area token.'
    }
}
finally {
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        [System.IO.Directory]::Delete($testRoot, $true)
    }
}

Write-Output 'PASS RuntimeProbeFixture_OpaqueDonorFailures'
