[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$catalogPath = Join-Path $repositoryRoot 'lib\catalog.tpa'
$harnessPath = Join-Path $PSScriptRoot 'weidu\catalog-harness.tp2'
$baseFixture = Join-Path $PSScriptRoot 'fixtures\catalog\base.2da'
$extensionFixture = Join-Path $PSScriptRoot 'fixtures\catalog\extensions.2da'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath("C:\Games\Baldur's Gate II Enhanced Edition modded")

if (-not (Test-Path -LiteralPath $catalogPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED Catalog_ProductionApiMissing lib/catalog.tpa'
    exit 1
}

$catalogSource = [System.IO.File]::ReadAllText($catalogPath)
if ($catalogSource -notmatch 'DEFINE_ACTION_FUNCTION\s+flir_catalog_apply') {
    throw 'The production catalog does not expose the generic typed apply function.'
}
foreach ($wrapper in @(
    'flir_catalog_item',
    'flir_catalog_unit',
    'flir_catalog_source',
    'flir_catalog_slot',
    'flir_catalog_endpoint',
    'flir_catalog_group_membership',
    'flir_catalog_sparse_override'
)) {
    if ($catalogSource -notmatch ('DEFINE_ACTION_MACRO\s+' + [regex]::Escape($wrapper))) {
        throw "The production catalog does not expose row wrapper '$wrapper'."
    }
}

if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe'
}
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}

$resolvedWeidu = [System.IO.Path]::GetFullPath($WeiduPath)
$resolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot).TrimEnd('\', '/')
if (-not (Test-Path -LiteralPath $resolvedWeidu -PathType Leaf)) {
    throw 'The configured WeiDU executable does not exist.'
}
if (-not (Test-Path -LiteralPath $resolvedTempRoot -PathType Container)) {
    throw 'The configured disposable temporary root does not exist.'
}
if ($resolvedWeidu.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to use WeiDU from the forbidden live game root.'
}
if ($resolvedTempRoot.Equals($forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedTempRoot.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create catalog scratch data under the forbidden live game root.'
}

$scratchRoot = Join-Path $resolvedTempRoot ('bgee-itemrandomiser-catalog-' + [guid]::NewGuid().ToString('N'))
$scratchRoot = [System.IO.Path]::GetFullPath($scratchRoot)
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated catalog scratch directory escaped its disposable parent.'
}

function Invoke-CatalogHarness {
    param(
        [Parameter(Mandatory = $true)]
        [int] $Component,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [bool] $ExpectSuccess = $true,

        [string] $ExpectedErrorCode = ''
    )

    $runDirectory = Join-Path $script:scratchRoot ('case-' + $Component + '-' + $Name)
    $null = New-Item -ItemType Directory -Path $runDirectory
    $outputPath = Join-Path $runDirectory 'catalog-report.txt'
    $arguments = @(
        $script:harnessPath,
        '--nogame',
        '--force-install-list', [string] $Component,
        '--args', $script:catalogPath,
        '--args', $script:baseFixture,
        '--args', $script:extensionFixture,
        '--args', $outputPath,
        '--no-exit-pause',
        '--quick-log'
    )

    Push-Location $runDirectory
    try {
        $output = @(& $script:resolvedWeidu @arguments 2>&1 | ForEach-Object { [string] $_ })
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    $joined = $output -join "`n"
    if ($ExpectSuccess) {
        if ($exitCode -ne 0 -or $joined -match 'NOT INSTALLED DUE TO ERRORS') {
            throw "Catalog harness case '$Name' failed unexpectedly.`n$joined"
        }
        if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
            throw "Catalog harness case '$Name' did not produce its report."
        }
    }
    else {
        if ($exitCode -eq 0 -or $joined -notmatch 'NOT INSTALLED DUE TO ERRORS') {
            throw "Catalog harness case '$Name' did not fail as expected.`n$joined"
        }
        if ($joined -notmatch [regex]::Escape("FLIR_CATALOG_ERR $ExpectedErrorCode")) {
            throw "Catalog harness case '$Name' did not report '$ExpectedErrorCode'.`n$joined"
        }
        if (Test-Path -LiteralPath $outputPath) {
            throw "Failed catalog harness case '$Name' published an output report."
        }
    }

    [pscustomobject]@{
        Name = $Name
        ExitCode = $exitCode
        OutputPath = $outputPath
        Log = $joined
    }
}

function ConvertFrom-CatalogReport {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $lines = @([System.IO.File]::ReadAllLines($Path))
    if ($lines.Count -lt 5 -or $lines[0] -ne 'LIMITS 16 40 48') {
        throw 'Catalog report did not expose the expected conservative ID limits.'
    }

    $beforeMarker = [Array]::IndexOf($lines, 'BEFORE')
    $afterMarker = [Array]::IndexOf($lines, 'AFTER')
    if ($beforeMarker -ne 1 -or $afterMarker -le $beforeMarker + 1) {
        throw 'Catalog report markers were malformed.'
    }

    $parseRows = {
        param([string[]] $Rows)
        @(
            foreach ($line in $Rows) {
                if ($line -notmatch '^(?<kind>[a-z]+) (?<provider>[a-z0-9._-]+) (?<id>[a-z0-9._-]+) (?<enabled>[01]) (?<fingerprint>[0-9]+)$') {
                    throw "Catalog report exposed a malformed or spoiler-unsafe row: '$line'."
                }
                [pscustomobject]@{
                    Kind = $Matches.kind
                    Provider = $Matches.provider
                    Id = $Matches.id
                    Enabled = [int] $Matches.enabled
                    Fingerprint = [int] $Matches.fingerprint
                    SortKey = $Matches.kind + ':' + $Matches.provider + ':' + $Matches.id
                }
            }
        )
    }

    $beforeEnd = $afterMarker - 1
    $before = & $parseRows ([string[]] $lines[($beforeMarker + 1)..$beforeEnd])
    $after = & $parseRows ([string[]] $lines[($afterMarker + 1)..($lines.Count - 1)])
    [pscustomobject]@{ Before = $before; After = $after; Raw = ($lines -join "`n") }
}

$positiveCount = 0
$conflictCount = 0
try {
    $null = New-Item -ItemType Directory -Path $scratchRoot

    $forwardRun = Invoke-CatalogHarness -Component 0 -Name 'positive-forward'
    $reverseRun = Invoke-CatalogHarness -Component 1 -Name 'positive-reverse'
    $percentRun = Invoke-CatalogHarness -Component 2 -Name 'positive-percent-bytes'
    $crossProviderRun = Invoke-CatalogHarness -Component 3 -Name 'positive-cross-provider'
    $numericStringRun = Invoke-CatalogHarness -Component 4 -Name 'positive-numeric-string'
    $forward = ConvertFrom-CatalogReport -Path $forwardRun.OutputPath
    $reverse = ConvertFrom-CatalogReport -Path $reverseRun.OutputPath

    $percentLines = @([System.IO.File]::ReadAllLines($percentRun.OutputPath))
    if ($percentLines.Count -ne 1 -or
        $percentLines[0] -cne 'PERCENT item core percent-a 1896075581 1912853200') {
        throw 'Percent-bearing catalog data was not stored and fingerprinted byte-exactly.'
    }
    $crossProviderLines = @([System.IO.File]::ReadAllLines($crossProviderRun.OutputPath))
    if ($crossProviderLines.Count -ne 1 -or
        $crossProviderLines[0] -cne 'CROSS source base cross-source 1320750375') {
        throw 'Cross-provider replacement did not preserve target ownership and deterministic content.'
    }
    $numericStringLines = @([System.IO.File]::ReadAllLines($numericStringRun.OutputPath))
    if ($numericStringLines.Count -ne 1 -or
        $numericStringLines[0] -notmatch '^NUMERIC_STRING item core numeric-string-001 001 (?<first>[0-9]+) numeric-string-1 1 (?<second>[0-9]+)$') {
        throw 'Numeric-looking catalog strings were not reported byte-exactly.'
    }
    if ([int] $Matches.first -lt 1 -or [int] $Matches.first -gt 2147483646 -or
        [int] $Matches.second -lt 1 -or [int] $Matches.second -gt 2147483646 -or
        [int] $Matches.first -eq [int] $Matches.second) {
        throw 'Numeric-looking catalog strings did not produce distinct valid fingerprints.'
    }

    if ($forward.Raw -cne $reverse.Raw) {
        throw 'Catalog output or fingerprints changed with declaration order.'
    }
    $expectedKinds = @('endpoint', 'group', 'item', 'override', 'slot', 'source', 'unit')
    $actualKinds = @($forward.Before.Kind | Sort-Object -Unique)
    if (($actualKinds -join ',') -cne ($expectedKinds -join ',')) {
        throw 'Catalog positive coverage did not include every normalized row kind.'
    }
    $beforeKeys = @($forward.Before.SortKey)
    $sortedBeforeKeys = @($beforeKeys | Sort-Object)
    if (($beforeKeys -join "`n") -cne ($sortedBeforeKeys -join "`n")) {
        throw 'Catalog canonical rows were not sorted by kind and qualified stable ID.'
    }
    if ($forward.Before.Count -ne 10 -or $forward.After.Count -ne 10) {
        throw 'Catalog positive fixture produced an unexpected row count.'
    }
    $allReportedRows = @($forward.Before) + @($forward.After)
    $outOfRangeFingerprints = @(
        $allReportedRows | Where-Object { $_.Fingerprint -lt 1 -or $_.Fingerprint -gt 2147483646 }
    )
    if ($outOfRangeFingerprints.Count -ne 0) {
        throw 'Catalog emitted a fingerprint outside its documented positive WeiDU-integer range.'
    }
    # Independent FNV-1a vectors for the synthetic core rows lock the canonical
    # kind, qualified ID, field order, and length framing of every schema.
    $knownBeforeFingerprints = @{
        'endpoint:core:endpoint-z' = 418841383
        'group:core:member-z' = 2001915592
        'item:core:item-z' = 1094675957
        'override:core:override-z' = 1928151437
        'slot:core:slot-z' = 89160416
        'source:core:source-z' = 397891642
        'unit:core:unit-z' = 1654028548
    }
    foreach ($knownKey in $knownBeforeFingerprints.Keys) {
        $knownRow = @($forward.Before | Where-Object { $_.SortKey -ceq $knownKey })
        if ($knownRow.Count -ne 1 -or
            $knownRow[0].Fingerprint -ne $knownBeforeFingerprints[$knownKey]) {
            throw "Catalog fingerprint did not match the independent vector for '$knownKey'."
        }
    }

    $itemBefore = @($forward.Before | Where-Object { $_.SortKey -ceq 'item:core:item-z' })
    $itemAfter = @($forward.After | Where-Object { $_.SortKey -ceq 'item:core:item-z' })
    if ($itemBefore.Count -ne 1 -or $itemAfter.Count -ne 1 -or
        $itemBefore[0].Fingerprint -eq $itemAfter[0].Fingerprint -or
        $itemAfter[0].Fingerprint -ne 74646542) {
        throw 'A same-length one-field REPLACE did not change the deterministic row fingerprint.'
    }
    $sourceBefore = @($forward.Before | Where-Object { $_.SortKey -ceq 'source:core:source-z' })
    $sourceAfter = @($forward.After | Where-Object { $_.SortKey -ceq 'source:core:source-z' })
    if ($sourceBefore.Count -ne 1 -or $sourceAfter.Count -ne 1 -or
        $sourceBefore[0].Enabled -ne 1 -or $sourceAfter[0].Enabled -ne 0 -or
        $sourceBefore[0].Fingerprint -eq $sourceAfter[0].Fingerprint -or
        $sourceAfter[0].Fingerprint -ne 381114023) {
        throw 'DISABLE did not preserve the source tombstone with enabled=0 and a new fingerprint.'
    }
    $positiveCount = 5

    $failureCases = @(
        @{ Component = 10; Name = 'duplicate-add'; Code = 'DUPLICATE'; Kind = 'item'; Provider = 'core'; Id = 'dup-a' },
        @{ Component = 11; Name = 'replace-mismatch'; Code = 'FINGERPRINT_MISMATCH'; Kind = 'item'; Provider = 'core'; Id = 'replace-a' },
        @{ Component = 12; Name = 'disable-mismatch'; Code = 'FINGERPRINT_MISMATCH'; Kind = 'source'; Provider = 'core'; Id = 'disable-a' },
        @{ Component = 13; Name = 'actor-owner-conflict'; Code = 'OWNER_CONFLICT'; Kind = 'item'; Provider = 'base'; Id = 'owner-a' },
        @{ Component = 14; Name = 'uppercase-provider'; Code = 'INVALID_ID'; Kind = 'item'; Provider = 'Core'; Id = 'id-a' },
        @{ Component = 15; Name = 'empty-id'; Code = 'INVALID_ID'; Kind = 'item'; Provider = 'core'; Id = '' },
        @{ Component = 16; Name = 'multiple-separator'; Code = 'INVALID_ID'; Kind = 'item'; Provider = 'core'; Id = 'id:a:b' },
        @{ Component = 17; Name = 'uppercase-id'; Code = 'INVALID_ID'; Kind = 'item'; Provider = 'core'; Id = 'Id-a' },
        @{ Component = 18; Name = 'invalid-character'; Code = 'INVALID_ID'; Kind = 'item'; Provider = 'core'; Id = 'id/a' },
        @{ Component = 19; Name = 'provider-length'; Code = 'ID_LENGTH'; Kind = 'item'; Provider = 'abcdefghijklmnopq'; Id = 'id-a' },
        @{ Component = 20; Name = 'local-length'; Code = 'ID_LENGTH'; Kind = 'item'; Provider = 'core'; Id = 'abcdefghijklmnopqrstuvwxyzabcdefghijklmno' },
        @{ Component = 21; Name = 'qualified-length'; Code = 'ID_LENGTH'; Kind = 'item'; Provider = 'abcdefghijklmnop'; Id = 'abcdefghijklmnopqrstuvwxyzabcdef' },
        @{ Component = 22; Name = 'incomplete-row'; Code = 'INCOMPLETE_ROW'; Kind = 'item'; Provider = 'core'; Id = 'incomplete-a' },
        @{ Component = 23; Name = 'invalid-numeric'; Code = 'INVALID_NUMERIC'; Kind = 'slot'; Provider = 'core'; Id = 'numeric-a' },
        @{ Component = 24; Name = 'unsupported-kind'; Code = 'UNSUPPORTED_KIND'; Kind = 'unknown'; Provider = 'core'; Id = 'kind-a' },
        @{ Component = 25; Name = 'unsupported-operation'; Code = 'UNSUPPORTED_OPERATION'; Kind = 'item'; Provider = 'core'; Id = 'operation-a' },
        @{ Component = 26; Name = 'tombstone-reuse'; Code = 'DUPLICATE'; Kind = 'source'; Provider = 'core'; Id = 'tombstone-a' },
        @{ Component = 27; Name = 'invalid-reference'; Code = 'INVALID_REFERENCE'; Kind = 'item'; Provider = 'core'; Id = 'reference-a' },
        @{ Component = 28; Name = 'stale-argument-omission'; Code = 'INCOMPLETE_ROW'; Kind = 'item'; Provider = 'core'; Id = 'stale-second' },
        @{ Component = 29; Name = 'replace-not-found'; Code = 'NOT_FOUND'; Kind = 'item'; Provider = 'core'; Id = 'absent-a' },
        @{ Component = 30; Name = 'unit-tombstone-replace'; Code = 'TOMBSTONE_IMMUTABLE'; Kind = 'unit'; Provider = 'core'; Id = 'unit-tombstone' },
        @{ Component = 31; Name = 'slot-tombstone-replace'; Code = 'TOMBSTONE_IMMUTABLE'; Kind = 'slot'; Provider = 'core'; Id = 'slot-tombstone' },
        @{ Component = 32; Name = 'cross-provider-fingerprint'; Code = 'FINGERPRINT_MISMATCH'; Kind = 'source'; Provider = 'base'; Id = 'cross-mismatch' },
        @{ Component = 33; Name = 'stale-actor-omission'; Code = 'INVALID_ACTOR'; Kind = 'item'; Provider = 'core'; Id = 'actor-second' },
        @{ Component = 34; Name = 'percent-actor'; Code = 'INVALID_ACTOR'; Kind = 'item'; Provider = 'core'; Id = 'actor-percent' },
        @{ Component = 35; Name = 'percent-reference'; Code = 'INVALID_REFERENCE'; Kind = 'item'; Provider = 'core'; Id = 'reference-percent' },
        @{ Component = 36; Name = 'unsupported-kind-provider-length'; Code = 'ID_LENGTH'; Kind = 'unknown'; Provider = 'abcdefghijklmnopq'; Id = 'kind-overlimit' },
        @{ Component = 37; Name = 'unsupported-kind-invalid-id'; Code = 'INVALID_ID'; Kind = 'unknown'; Provider = 'core'; Id = 'Kind/Invalid' },
        @{ Component = 38; Name = 'unsupported-operation-id-length'; Code = 'ID_LENGTH'; Kind = 'item'; Provider = 'core'; Id = 'abcdefghijklmnopqrstuvwxyzabcdefghijklmno' },
        @{ Component = 39; Name = 'unsupported-operation-invalid-provider'; Code = 'INVALID_ID'; Kind = 'item'; Provider = 'Core'; Id = 'operation-invalid' }
    )
    foreach ($case in $failureCases) {
        $result = Invoke-CatalogHarness -Component $case.Component -Name $case.Name -ExpectSuccess $false -ExpectedErrorCode $case.Code
        $expectedDiagnostic = "FLIR_CATALOG_ERR $($case.Code) kind=$($case.Kind) provider=$($case.Provider) id=$($case.Id)"
        $diagnostics = @(
            [regex]::Matches($result.Log, 'FLIR_CATALOG_ERR[^\r\n"]*') |
                ForEach-Object { $_.Value }
        )
        if ($diagnostics.Count -lt 1 -or
            @($diagnostics | Where-Object { $_ -cne $expectedDiagnostic }).Count -ne 0) {
            throw "Catalog failure '$($case.Name)' exposed a diagnostic outside the opaque identity contract."
        }
        ++$conflictCount
    }

    Write-Output 'PASS Catalog_AllSevenSchemasStored'
    Write-Output 'PASS Catalog_StableIdsAndExplicitLimits'
    Write-Output 'PASS Catalog_AddReplaceDisableAndTombstones'
    Write-Output 'PASS Catalog_FingerprintStableAcrossOrderAndSensitiveToFieldDelta'
    Write-Output 'PASS Catalog_CanonicalOrderAndSpoilerFreeOutput'
    Write-Output "SUMMARY positive=$positiveCount conflicts=$conflictCount"
}
finally {
    if (Test-Path -LiteralPath $scratchRoot) {
        [System.IO.Directory]::Delete($scratchRoot, $true)
    }
    if (Test-Path -LiteralPath $scratchRoot) {
        throw 'Catalog scratch cleanup did not complete.'
    }
}
