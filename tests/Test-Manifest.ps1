[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $LuaPath = $env:FL_IR_TEST_LUA,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repositoryRoot 'lib\manifest.tpa'
$catalogPath = Join-Path $repositoryRoot 'lib\catalog.tpa'
$registryPath = Join-Path $repositoryRoot 'lib\registry.tpa'
$endpointsPath = Join-Path $repositoryRoot 'lib\endpoints.tpa'
$corePath = Join-Path $repositoryRoot 'copy\FLDLVCor.lua'
$harnessPath = Join-Path $PSScriptRoot 'weidu\manifest-harness.tp2'
$validatorPath = Join-Path $PSScriptRoot 'fixtures\manifest\validate-manifest.lua'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath("C:\Games\Baldur's Gate II Enhanced Edition modded")

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED Manifest_ProductionApiMissing lib/manifest.tpa'
    exit 1
}

$manifestSource = [System.IO.File]::ReadAllText($manifestPath)
foreach ($macro in @('flir_manifest_prepare_catalog', 'flir_manifest_write')) {
    if ($manifestSource -notmatch ('DEFINE_ACTION_MACRO\s+' + [regex]::Escape($macro))) {
        throw "The production manifest API does not expose '$macro'."
    }
}
if ($manifestSource -match '(?m)^\s*COPY\s+\+') {
    throw 'The generated manifest must be removed on uninstall; COPY + preservation is forbidden.'
}

if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe'
}
if ([string]::IsNullOrWhiteSpace($LuaPath)) {
    $LuaPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\lua.exe'
}
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}

$resolvedWeidu = [System.IO.Path]::GetFullPath($WeiduPath)
$resolvedLua = [System.IO.Path]::GetFullPath($LuaPath)
$resolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot).TrimEnd('\', '/')
foreach ($executable in @($resolvedWeidu, $resolvedLua)) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'A configured manifest-test executable does not exist.'
    }
    if ($executable.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to use an executable from the forbidden live game root.'
    }
}
if (-not (Test-Path -LiteralPath $resolvedTempRoot -PathType Container)) {
    throw 'The configured disposable temporary root does not exist.'
}
if ($resolvedTempRoot.Equals($forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedTempRoot.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create manifest scratch data under the forbidden live game root.'
}

$scratchRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedTempRoot ('bgee-itemrandomiser-manifest-' + [guid]::NewGuid().ToString('N'))))
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated manifest scratch directory escaped its disposable parent.'
}

function Test-ByteArrayEqual {
    param(
        [Parameter(Mandatory = $true)]
        [byte[]] $Left,

        [Parameter(Mandatory = $true)]
        [byte[]] $Right
    )

    if ($Left.Length -ne $Right.Length) {
        return $false
    }
    for ($index = 0; $index -lt $Left.Length; $index++) {
        if ($Left[$index] -ne $Right[$index]) {
            return $false
        }
    }
    return $true
}

function Invoke-ManifestHarness {
    param(
        [Parameter(Mandatory = $true)]
        [int] $Component,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [ValidateSet('default', 'changed', 'synthesized', 'variant', 'adapter', 'book', 'group')]
        [string] $ValidatorMode = 'default'
    )

    $runDirectory = Join-Path $script:scratchRoot $Name
    $null = New-Item -ItemType Directory -Path $runDirectory
    [System.IO.File]::WriteAllBytes((Join-Path $runDirectory '...blank'), @())
    $outputPath = Join-Path $runDirectory 'FLDLVMan.lua'
    if ([System.IO.Path]::GetFileNameWithoutExtension($outputPath) -cne 'FLDLVMan' -or
        [System.IO.Path]::GetFileNameWithoutExtension($outputPath).Length -ne 8) {
        throw 'The engine-loaded manifest basename is not exactly eight characters.'
    }

    $arguments = @(
        $script:harnessPath,
        '--nogame',
        '--force-install-list', [string] $Component,
        '--args', $script:manifestPath,
        '--args', $script:catalogPath,
        '--args', $script:registryPath,
        '--args', $script:endpointsPath,
        '--args', $outputPath,
        '--no-exit-pause',
        '--quick-log'
    )

    Push-Location $runDirectory
    try {
        $weiduOutput = @(& $script:resolvedWeidu @arguments 2>&1 | ForEach-Object { [string] $_ })
        $weiduExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $joinedWeiduOutput = $weiduOutput -join "`n"
    if ($weiduExitCode -ne 0 -or $joinedWeiduOutput -match 'NOT INSTALLED DUE TO ERRORS') {
        throw "Manifest harness case '$Name' failed unexpectedly.`n$joinedWeiduOutput"
    }
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "Manifest harness case '$Name' did not publish FLDLVMan.lua."
    }

    $bytes = [System.IO.File]::ReadAllBytes($outputPath)
    foreach ($byte in $bytes) {
        if ($byte -ne 9 -and $byte -ne 10 -and $byte -ne 13 -and ($byte -lt 32 -or $byte -gt 126)) {
            throw "Manifest harness case '$Name' emitted non-ASCII or raw control data."
        }
    }
    $source = [System.Text.Encoding]::ASCII.GetString($bytes)
    if ($source.IndexOf('%flir_manifest_probe%', [System.StringComparison]::Ordinal) -ge 0 -or
        $source.IndexOf('THIS_MUST_NOT_REPLACE_LITERAL_DATA', [System.StringComparison]::Ordinal) -ge 0) {
        throw "Manifest harness case '$Name' did not encode literal percent-bearing data safely."
    }

    $validatorArguments = @($script:validatorPath, $outputPath, $ValidatorMode, $script:corePath)
    $luaOutput = @(& $script:resolvedLua @validatorArguments 2>&1 | ForEach-Object { [string] $_ })
    $luaExitCode = $LASTEXITCODE
    if ($luaExitCode -ne 0) {
        throw "Lua 5.3 rejected manifest case '$Name'.`n$($luaOutput -join "`n")"
    }
    if ($luaOutput.Count -ne 1 -or $luaOutput[0] -notmatch '^FP (?<a>[0-9]+) (?<b>[0-9]+) (?<c>[0-9]+) (?<d>[0-9]+)$') {
        throw "Lua manifest validator returned an invalid protocol for '$Name'."
    }

    [pscustomobject]@{
        Bytes = $bytes
        Fingerprint = @([int64] $Matches.a, [int64] $Matches.b, [int64] $Matches.c, [int64] $Matches.d)
    }
}

function Invoke-ManifestHarnessFailure {
    param(
        [Parameter(Mandatory = $true)]
        [int] $Component,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $ExpectedCode,

        [string] $ErrorPrefix = 'FLIR_MANIFEST_ERR',

        [switch] $Preseed
    )

    $runDirectory = Join-Path $script:scratchRoot $Name
    $null = New-Item -ItemType Directory -Path $runDirectory
    [System.IO.File]::WriteAllBytes((Join-Path $runDirectory '...blank'), @())
    $outputPath = Join-Path $runDirectory 'FLDLVMan.lua'
    $preservedBytes = [System.Text.Encoding]::ASCII.GetBytes('-- last-known-good manifest sentinel')
    if ($Preseed) {
        [System.IO.File]::WriteAllBytes($outputPath, $preservedBytes)
    }
    $arguments = @(
        $script:harnessPath,
        '--nogame',
        '--force-install-list', [string] $Component,
        '--args', $script:manifestPath,
        '--args', $script:catalogPath,
        '--args', $script:registryPath,
        '--args', $script:endpointsPath,
        '--args', $outputPath,
        '--no-exit-pause',
        '--quick-log'
    )

    Push-Location $runDirectory
    try {
        $weiduOutput = @(& $script:resolvedWeidu @arguments 2>&1 | ForEach-Object { [string] $_ })
        $weiduExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $joinedWeiduOutput = $weiduOutput -join "`n"
    if ($weiduExitCode -eq 0 -and $joinedWeiduOutput -notmatch 'NOT INSTALLED DUE TO ERRORS') {
        throw "Manifest harness case '$Name' succeeded but should have failed with $ExpectedCode."
    }
    if ($joinedWeiduOutput -notmatch ([regex]::Escape($ErrorPrefix) + '\s+' + [regex]::Escape($ExpectedCode) + '\b')) {
        throw "Manifest harness case '$Name' did not report $ExpectedCode.`n$joinedWeiduOutput"
    }
    if ($Preseed) {
        if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
            throw "Manifest harness case '$Name' removed the last-known-good manifest after failed validation."
        }
        $actualPreservedBytes = [System.IO.File]::ReadAllBytes($outputPath)
        if (-not (Test-ByteArrayEqual -Left $preservedBytes -Right $actualPreservedBytes)) {
            throw "Manifest harness case '$Name' changed the last-known-good manifest after failed validation."
        }
    }
    elseif (Test-Path -LiteralPath $outputPath -PathType Leaf) {
        throw "Manifest harness case '$Name' published a manifest after failed validation."
    }
}

try {
    $null = New-Item -ItemType Directory -Path $scratchRoot

    $forwardOne = Invoke-ManifestHarness -Component 0 -Name 'forward-one'
    $forwardTwo = Invoke-ManifestHarness -Component 0 -Name 'forward-two'
    $reverse = Invoke-ManifestHarness -Component 1 -Name 'reverse'
    $changed = Invoke-ManifestHarness -Component 2 -Name 'changed-charge' -ValidatorMode changed
    $synthesizedOne = Invoke-ManifestHarness -Component 3 -Name 'synthesized-one' -ValidatorMode synthesized
    $synthesizedTwo = Invoke-ManifestHarness -Component 3 -Name 'synthesized-two' -ValidatorMode synthesized
    $adapter = Invoke-ManifestHarness -Component 4 -Name 'adapter-change' -ValidatorMode adapter
    $variant = Invoke-ManifestHarness -Component 5 -Name 'variant-policy' -ValidatorMode variant
    $bookOne = Invoke-ManifestHarness -Component 13 -Name 'legacy-book-one' -ValidatorMode book
    $bookTwo = Invoke-ManifestHarness -Component 13 -Name 'legacy-book-two' -ValidatorMode book
    $predeclaredUnit = Invoke-ManifestHarness -Component 15 -Name 'predeclared-unit'
    $bookReverse = Invoke-ManifestHarness -Component 16 -Name 'legacy-book-reverse' -ValidatorMode book
    $groupLowered = Invoke-ManifestHarness -Component 18 -Name 'group-lowered' -ValidatorMode group
    $groupReverse = Invoke-ManifestHarness -Component 22 -Name 'group-reverse' -ValidatorMode group

    Invoke-ManifestHarnessFailure -Component 6 -Name 'duplicate-global' -ExpectedCode 'DUPLICATE_GLOBAL'
    Invoke-ManifestHarnessFailure -Component 6 -Name 'duplicate-global-preserve' -ExpectedCode 'DUPLICATE_GLOBAL' -Preseed
    Invoke-ManifestHarnessFailure -Component 7 -Name 'duplicate-override' -ExpectedCode 'DUPLICATE_OVERRIDE'
    Invoke-ManifestHarnessFailure -Component 7 -Name 'duplicate-override-preserve' -ExpectedCode 'DUPLICATE_OVERRIDE' -Preseed
    Invoke-ManifestHarnessFailure -Component 8 -Name 'disabled-item' -ExpectedCode 'ITEM_CATALOG_DRIFT'
    Invoke-ManifestHarnessFailure -Component 9 -Name 'disabled-unit' -ExpectedCode 'UNIT_CATALOG_DRIFT'
    Invoke-ManifestHarnessFailure -Component 10 -Name 'dangling-override' -ExpectedCode 'OVERRIDE_REFERENCE'
    Invoke-ManifestHarnessFailure -Component 11 -Name 'incompatible-override' -ExpectedCode 'OVERRIDE_SCOPE'
    Invoke-ManifestHarnessFailure -Component 12 -Name 'override-capacity' -ExpectedCode 'OVERRIDE_CAPACITY'
    Invoke-ManifestHarnessFailure -Component 14 -Name 'legacy-adapter-override' -ExpectedCode 'OVERRIDE_SCOPE'
    Invoke-ManifestHarnessFailure -Component 17 -Name 'ground-override-capacity' -ExpectedCode 'OVERRIDE_CAPACITY'
    Invoke-ManifestHarnessFailure -Component 19 -Name 'group-target-override' -ExpectedCode 'OVERRIDE_SCOPE'
    Invoke-ManifestHarnessFailure -Component 20 -Name 'group-cross-area-override' -ExpectedCode 'OVERRIDE_SCOPE'
    Invoke-ManifestHarnessFailure -Component 21 -Name 'group-missing-pair' -ExpectedCode 'GROUP_OVERRIDE_MISSING'
    Invoke-ManifestHarnessFailure -Component 23 -Name 'group-alias-mismatch' -ExpectedCode 'GROUP_PHYSICAL_ALIAS' -ErrorPrefix 'FLIR_ENDPOINT_ERR'

    if (-not (Test-ByteArrayEqual -Left $forwardOne.Bytes -Right $forwardTwo.Bytes)) {
        throw 'Two identical manifest runs were not byte-identical.'
    }
    if (-not (Test-ByteArrayEqual -Left $groupLowered.Bytes -Right $groupReverse.Bytes)) {
        throw 'Group lowering changed with extension membership declaration order.'
    }
    if (-not (Test-ByteArrayEqual -Left $forwardOne.Bytes -Right $reverse.Bytes)) {
        throw 'Manifest bytes changed with declaration order.'
    }
    if (($forwardOne.Fingerprint -join ',') -cne ($reverse.Fingerprint -join ',')) {
        throw 'Manifest fingerprint changed with declaration order.'
    }
    if (($forwardOne.Fingerprint -join ',') -ceq ($changed.Fingerprint -join ',')) {
        throw 'A one-field charge change did not alter the manifest fingerprint.'
    }
    if (-not (Test-ByteArrayEqual -Left $synthesizedOne.Bytes -Right $synthesizedTwo.Bytes)) {
        throw 'Two synthesized manifest runs were not byte-identical.'
    }
    if (($forwardOne.Fingerprint -join ',') -ceq ($adapter.Fingerprint -join ',')) {
        throw 'An adapter-only endpoint change did not alter the manifest fingerprint.'
    }
    if (($forwardOne.Fingerprint -join ',') -ceq ($variant.Fingerprint -join ',')) {
        throw 'A provider variant-policy change did not alter the manifest fingerprint.'
    }
    if (-not (Test-ByteArrayEqual -Left $bookOne.Bytes -Right $bookTwo.Bytes)) {
        throw 'Two synthesized legacy-book manifest runs were not byte-identical.'
    }
    if (($synthesizedOne.Fingerprint -join ',') -ceq ($bookOne.Fingerprint -join ',')) {
        throw 'The built-in legacy-book variant policy did not alter the manifest fingerprint.'
    }
    if (-not (Test-ByteArrayEqual -Left $forwardOne.Bytes -Right $predeclaredUnit.Bytes)) {
        throw 'An equivalent predeclared unit changed the generated manifest.'
    }
    if (-not (Test-ByteArrayEqual -Left $bookOne.Bytes -Right $bookReverse.Bytes)) {
        throw 'Synthesized legacy-book bytes changed with declaration order.'
    }

    Write-Output 'PASS Manifest_ProductionArraysNormalized'
    Write-Output 'PASS Manifest_ExactSeparatedTopLevelRelations'
    Write-Output 'PASS Manifest_DeterministicBytesAcrossRunsAndDeclarationOrder'
    Write-Output 'PASS Manifest_SynthesizedItemsAndExtraTokenCharges'
    Write-Output 'PASS Manifest_Lua53EscapingAndLiteralPercentBytes'
    Write-Output 'PASS Manifest_FourNumericFingerprintWordsSensitiveToFieldChange'
    Write-Output 'PASS Manifest_AdapterSemanticsAndFingerprint'
    Write-Output 'PASS Manifest_VariantPolicyRoundTrip'
    Write-Output 'PASS Manifest_BuiltInVariantPolicyDerivedForExtraTokens'
    Write-Output 'PASS Manifest_PredeclaredUnitReconcilesWithoutSemanticDrift'
    Write-Output 'PASS Manifest_TombstonesPreservedAndDisabledRowsRejected'
    Write-Output 'PASS Manifest_DuplicateRelationsRejectedBeforePublish'
    Write-Output 'PASS Manifest_LastKnownGoodSurvivesValidationFailure'
    Write-Output 'PASS Manifest_OverrideReferencesAndCompatibilityValidated'
    Write-Output 'PASS Manifest_OverrideCapacityAndLegacyAdaptersValidated'
    Write-Output 'PASS Manifest_GroupsLoweredToConcreteUnitSlotOverrides'
    Write-Output 'PASS Manifest_GroupOverridesConcreteSameAreaAndComplete'
    Write-Output 'PASS Manifest_GroupLoweringStableUnitsPriorityFallbackAndAliases'
    Write-Output 'PASS Manifest_EightCharacterEngineBasenameAndNumericFlags'
    Write-Output 'SUMMARY passed=19 failed=0'
}
finally {
    if (Test-Path -LiteralPath $scratchRoot) {
        [System.IO.Directory]::Delete($scratchRoot, $true)
    }
    if (Test-Path -LiteralPath $scratchRoot) {
        throw 'Manifest scratch cleanup did not complete.'
    }
}
