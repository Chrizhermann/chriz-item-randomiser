[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$registryPath = Join-Path $repositoryRoot 'lib\registry.tpa'
$randomSeedPath = Join-Path $repositoryRoot 'lib\random_seed.tpa'
$randomiserPath = Join-Path $repositoryRoot 'randomiser.tp2'
$arraysPath = Join-Path $repositoryRoot 'lib\arrays.tpa'
$fixesPath = Join-Path $repositoryRoot 'lib\fixes.tpa'
$removalPlanPath = Join-Path $repositoryRoot 'lib\removal_plan.tpa'
$coreLibPath = Join-Path $repositoryRoot 'lib\lib.tpa'
$catalogPath = Join-Path $repositoryRoot 'lib\catalog.tpa'
$harnessPath = Join-Path $PSScriptRoot 'weidu\registry-harness.tp2'
$randomSeedHarnessPath = Join-Path $PSScriptRoot 'weidu\random-seed-harness.tp2'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath("C:\Games\Baldur's Gate II Enhanced Edition modded").TrimEnd('\', '/')

if (-not (Test-Path -LiteralPath $registryPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED Registry_ProductionLibraryMissing lib/registry.tpa'
    exit 1
}

foreach ($requiredPath in @($randomSeedPath, $harnessPath, $randomSeedHarnessPath, $removalPlanPath, $coreLibPath, $catalogPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required registry test input is missing: $requiredPath"
    }
}

if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe'
}
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}

function ConvertTo-LocalFullPath {
    param([Parameter(Mandatory = $true)][string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.StartsWith('\\') -or $Path.StartsWith('//')) {
        throw 'Registry tests accept only local drive-letter paths.'
    }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($full -notmatch '^[A-Za-z]:\\' -or $full.IndexOf('~') -ge 0) {
        throw 'Registry tests accept only long local drive-letter paths.'
    }
    $full
}

function Assert-OutsideForbiddenLiveRoot {
    param([Parameter(Mandatory = $true)][string] $Path)
    $full = ConvertTo-LocalFullPath -Path $Path
    if ($full.Equals($script:forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($script:forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to use a registry test path under the forbidden live game root.'
    }
    $full
}

$resolvedWeidu = Assert-OutsideForbiddenLiveRoot -Path $WeiduPath
$resolvedTempRoot = Assert-OutsideForbiddenLiveRoot -Path $TempRoot
if (-not (Test-Path -LiteralPath $resolvedWeidu -PathType Leaf)) {
    throw 'The configured WeiDU executable does not exist.'
}
if (-not (Test-Path -LiteralPath $resolvedTempRoot -PathType Container)) {
    throw 'The configured disposable temporary root does not exist.'
}

$registrySource = [System.IO.File]::ReadAllText($registryPath)
$randomSeedSource = [System.IO.File]::ReadAllText($randomSeedPath)
$randomiserSource = [System.IO.File]::ReadAllText($randomiserPath)
$arraysSource = [System.IO.File]::ReadAllText($arraysPath)
$fixesSource = [System.IO.File]::ReadAllText($fixesPath)
$removalPlanSource = [System.IO.File]::ReadAllText($removalPlanPath)
$harnessSource = [System.IO.File]::ReadAllText($harnessPath)
$staticFailures = [System.Collections.Generic.List[string]]::new()
$redFailures = [System.Collections.Generic.List[string]]::new()

foreach ($requiredSymbol in @(
    'flir_registry_reset',
    'flir_registry_preload_state',
    'flir_registry_register_applied_units',
    'flir_registry_allocate_overflow_locations',
    'flir_registry_add_current_overflow_locations',
    'flir_registry_capture_final_slots',
    'flir_registry_reconcile',
    'flir_registry_publish_state',
    'flir_mode1_publish_state',
    'flir_registry_write_state',
    'flir_registry_canonical_rows'
)) {
    if ($registrySource -notmatch ('DEFINE_(?:ACTION|PATCH)_(?:MACRO|FUNCTION)\s+' + [regex]::Escape($requiredSymbol))) {
        $staticFailures.Add("Registry library does not expose '$requiredSymbol'.")
    }
}
if ($registrySource -notmatch 'COPY\s+\+\s+"\.\.\.blank"\s+"override/fl#irreg\.2da"') {
    $staticFailures.Add('Fresh registry publication does not use preserved COPY +.')
}
if ($registrySource -notmatch 'COPY_EXISTING\s+\+\s+"fl#irreg\.2da"\s+override') {
    $staticFailures.Add('Preserve registry refresh does not use preserved COPY_EXISTING +.')
}
if ($registrySource -match '(?i)item[_-]?location|location[_-]?item|joined_assignment|assignment_map|target_identity|area\s+target') {
    $staticFailures.Add('Registry source contains a joined assignment/reporting smell.')
}
if ($randomSeedSource -notmatch 'registry\.tpa' -or $randomSeedSource -notmatch 'weidu_action\s*=\s*0') {
    $staticFailures.Add('random_seed.tpa does not load the registry behind the Mode 1 boundary.')
}
if ($registrySource -match 'ACTION_PHP_EACH\s+extra_tokens') {
    $staticFailures.Add('Applied-unit registration must use removed_item_array once and not loop over extra_tokens.')
}
if ($registrySource -match 'flir_registry_catalog_fingerprint\s*=\s*123456') {
    $staticFailures.Add('Registry tests/production must not assume the harness-only catalog fingerprint 123456.')
}
if ($registrySource -notmatch '@fingerprint') {
    $staticFailures.Add('Registry state lacks an intrinsic file fingerprint metadata row.')
}
if ($registrySource -notmatch 'flir_registry_compute_catalog_fingerprint') {
    $staticFailures.Add('Registry does not compute a deterministic catalog fingerprint from current unit/slot input.')
}
foreach ($componentId in 1100, 1200) {
    $componentPattern = "(?s)DESIGNATED\s+$componentId\b(?<body>.*?)(?=\r?\nBEGIN\s+@|\z)"
    $match = [regex]::Match($randomiserSource, $componentPattern)
    if (-not $match.Success) {
        $staticFailures.Add("Mode 1 component $componentId was not found for registry ordering checks.")
        continue
    }
    $body = $match.Groups['body'].Value
    $fixesIndex = $body.IndexOf('INCLUDE "randomiser/lib/fixes.tpa"', [System.StringComparison]::Ordinal)
    $registerIndex = $body.IndexOf('LAM flir_registry_register_applied_units', [System.StringComparison]::Ordinal)
    $captureIndex = $body.IndexOf('LAM flir_registry_capture_final_slots', [System.StringComparison]::Ordinal)
    $writeIndex = $body.IndexOf('LAM flir_registry_write_state', [System.StringComparison]::Ordinal)
    $deliveryIndex = $body.IndexOf('INCLUDE "randomiser/lib/delivery.tpa"', [System.StringComparison]::Ordinal)
    $sslIndex = $body.IndexOf('INCLUDE "randomiser/lib/ssl.tpa"', [System.StringComparison]::Ordinal)
    $publishIndex = $body.IndexOf('LAM flir_mode1_publish_state', [System.StringComparison]::Ordinal)
    $deferIndex = $body.IndexOf('OUTER_SET flir_mode1_defer_state_publication = 1', [System.StringComparison]::Ordinal)
    $seedIndex = $body.IndexOf('INCLUDE "randomiser/lib/random_seed.tpa"', [System.StringComparison]::Ordinal)
    if ($fixesIndex -lt 0 -or $registerIndex -lt 0 -or $captureIndex -lt 0 -or $writeIndex -lt 0 -or
        $deliveryIndex -lt 0 -or $sslIndex -lt 0 -or $publishIndex -lt 0 -or $deferIndex -lt 0 -or $seedIndex -lt 0 -or
        -not ($deferIndex -lt $seedIndex -and $fixesIndex -lt $registerIndex -and $registerIndex -lt $captureIndex -and
              $captureIndex -lt $writeIndex -and $writeIndex -lt $deliveryIndex -and $deliveryIndex -lt $sslIndex -and
              $sslIndex -lt $publishIndex)) {
        $staticFailures.Add("Mode 1 component $componentId does not prepare state in memory and publish it only after delivery/SSL.")
    }
}
if ($randomSeedSource -notmatch 'flir_random_seed_publish_state' -or $randomSeedSource -notmatch 'flir_mode1_defer_state_publication') {
    $staticFailures.Add('Random-seed state cannot be deferred until final Mode 1 publication.')
}
if ($removalPlanSource -notmatch 'flir_removal_plan_publish_state' -or $removalPlanSource -notmatch 'flir_mode1_defer_state_publication') {
    $staticFailures.Add('Removal-history state cannot be deferred until final Mode 1 publication.')
}
if ($arraysSource -match 'COPY_EXISTING\s+\+\s+"fl#randoptions\.2da"') {
    $staticFailures.Add('arrays.tpa publishes random options before the final Mode 1 commit seam.')
}
if ($arraysSource -notmatch '\$RandOptions\(ModCompatYes\)') {
    $staticFailures.Add('arrays.tpa no longer records the accepted compatibility option in memory.')
}
if ($arraysSource -notmatch '(?s)\$RandOptions\(ModCompatYes\).*?ACTION_IF\s+weidu_action\s*=\s*1\s+BEGIN.*?LAM\s+flir_random_seed_publish_state') {
    $staticFailures.Add('Mode 2 does not immediately persist its late compatibility option.')
}
if ($arraysSource -notmatch '(?s)LAM\s+flir_source_filter_stage1_legacy_ident_filter\b.*?LAM\s+flir_registry_capture_old_unmarked_legacy_slots\b.*?LAM\s+flir_source_apply_extension_hook\b') {
    $staticFailures.Add('Old-unmarked migration does not filter the legacy catalog before snapshotting and applying extensions.')
}
$bg2Start = $arraysSource.IndexOf('ACTION_IF BG1 = 0 OR BGT = 1 BEGIN', [System.StringComparison]::Ordinal)
$bg1Start = $arraysSource.IndexOf('ACTION_IF BG1 = 1 BEGIN', [System.StringComparison]::Ordinal)
$tailStart = $arraysSource.IndexOf('ACTION_IF FILE_EXISTS_IN_GAME lclist_scrolls.2da', [System.StringComparison]::Ordinal)
foreach ($campaignBlock in @(
    [pscustomobject]@{ Name = 'BG2'; Start = $bg2Start; End = $bg1Start },
    [pscustomobject]@{ Name = 'BG1'; Start = $bg1Start; End = $tailStart }
)) {
    if ($campaignBlock.Start -lt 0 -or $campaignBlock.End -le $campaignBlock.Start) {
        $staticFailures.Add("Could not isolate the $($campaignBlock.Name) arrays block for migration RNG-order checks.")
        continue
    }
    $campaignBody = $arraysSource.Substring($campaignBlock.Start, $campaignBlock.End - $campaignBlock.Start)
    $rawIndex = $campaignBody.IndexOf('LAUNCH_PATCH_MACRO flir_source_stage1_raw', [System.StringComparison]::Ordinal)
    $legacyFilterIndex = $campaignBody.IndexOf('LAM flir_source_filter_stage1_legacy_ident_filter', [System.StringComparison]::Ordinal)
    $locationIndex = $campaignBody.IndexOf('LAUNCH_PATCH_MACRO create_loc_array#stage1', [System.StringComparison]::Ordinal)
    if ($rawIndex -lt 0 -or $legacyFilterIndex -lt 0 -or $locationIndex -lt 0 -or
        -not ($rawIndex -lt $legacyFilterIndex -and $legacyFilterIndex -lt $locationIndex)) {
        $staticFailures.Add("$($campaignBlock.Name) legacy item filtering does not occur at the historical item-before-location RNG seam.")
    }
}
if ([regex]::Matches($arraysSource, 'LAM\s+flir_source_filter_stage1_legacy_ident_filter\b').Count -ne 2) {
    $staticFailures.Add('Legacy source filtering must run exactly once per BG2/BG1 campaign seam.')
}
if ($arraysSource -notmatch '(?s)LAM\s+flir_source_finalize_legacy_snapshot\b.*?LAM\s+flir_registry_capture_old_unmarked_legacy_slots\b.*?LAM\s+flir_source_apply_extension_hook\b') {
    $staticFailures.Add('Legacy source rows are not finalized before slot snapshot and extension application.')
}
if ($arraysSource -notmatch '(?s)LAM\s+flir_source_adopt_direct_virtual_rows\b.*?LAM\s+flir_removal_plan_load_preserve\b.*?LAM\s+flir_registry_capture_old_unmarked_legacy_slots\b.*?LAM\s+flir_source_apply_extension_hook\b') {
    $staticFailures.Add('Old-unmarked overflow reconstruction does not load removal history at the pre-extension RNG seam.')
}
if ($fixesSource -notmatch 'LAM\s+flir_registry_add_current_overflow_locations\b') {
    $staticFailures.Add('Production fixes do not use the shared registry-aware overflow allocator.')
}
$automaticMigrationComponent = [regex]::Match(
    $harnessSource,
    '(?s)BEGIN\s+~automatic old migration preserves ownership and tombstones history~\s+DESIGNATED\s+90\b(?<body>.*?)(?=\r?\nBEGIN\s+~|\z)'
)
if (-not $automaticMigrationComponent.Success) {
    $staticFailures.Add('Could not isolate registry harness component 90 for automatic migration checks.')
}
elseif ($automaticMigrationComponent.Groups['body'].Value -match '\bflir_registry_capture_old_unmarked_legacy_units\b') {
    $staticFailures.Add('Registry harness component 90 manually captures legacy units instead of exercising the production plan-builder seam.')
}
if ($removalPlanSource -notmatch 'flir_registry_validate_planned_units') {
    $staticFailures.Add('Task 4 validation hook does not call the registry validation seam for knowable planned-unit checks.')
}
if ($removalPlanSource -notmatch '(?s)DEFINE_ACTION_MACRO\s+flir_removal_emit_extra_token\b.*?flir_registry_planned_extra_compact') {
    $staticFailures.Add('Extra-token application does not consume the registry preflight compact plan.')
}
if ($registrySource -notmatch 'flir_registry_applied_extra_stable') {
    $staticFailures.Add('Applied extra units are not registered by stable logical identity.')
}
if ($registrySource -match '(?i)EDIT_SAV_FILE|PATCH_GAM|baldur\.gam|[\w.-]+\.sav|SetGlobal|SetGlobalInt|Assign\(') {
    $staticFailures.Add('Registry source must not edit saves or assign globals; new globals stay new-game-only through SSL.')
}
if ($staticFailures.Count -ne 0) {
    foreach ($failure in $staticFailures) {
        Write-Output "EXPECTED_RED Registry_StaticSpecReview $failure"
        $redFailures.Add($failure)
    }
}

function Write-AsciiFixed {
    param([byte[]] $Buffer, [int] $Offset, [string] $Value, [int] $Length)
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($Value)
    [Array]::Copy($bytes, 0, $Buffer, $Offset, [Math]::Min($bytes.Length, $Length))
}

function Write-U16 {
    param([byte[]] $Buffer, [int] $Offset, [int] $Value)
    [Array]::Copy([BitConverter]::GetBytes([UInt16] $Value), 0, $Buffer, $Offset, 2)
}

function Write-U32 {
    param([byte[]] $Buffer, [int] $Offset, [int64] $Value)
    [Array]::Copy([BitConverter]::GetBytes([UInt32] $Value), 0, $Buffer, $Offset, 4)
}

function New-MinimalTlk {
    param([string] $Path)
    $buffer = New-Object byte[] (0x2c + 26)
    Write-AsciiFixed $buffer 0 'TLK V1  ' 8
    Write-U16 $buffer 8 0
    Write-U32 $buffer 0x0a 1
    Write-U32 $buffer 0x0e 0x2c
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function New-MinimalFakeGame {
    param([string] $GameRoot)
    $null = New-Item -ItemType Directory -Path (Join-Path $GameRoot 'data') -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $GameRoot 'override') -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $GameRoot 'lang\en_US') -Force
    [System.IO.File]::WriteAllBytes((Join-Path $GameRoot '...blank'), @())

    New-MinimalTlk -Path (Join-Path $GameRoot 'dialog.tlk')
    New-MinimalTlk -Path (Join-Path $GameRoot 'lang\en_US\dialog.tlk')

    $payload = New-Object byte[] 16
    Write-AsciiFixed $payload 0 'AREA V1.0' 8

    $bifPath = Join-Path $GameRoot 'data\registry.bif'
    $bif = New-Object byte[] (0x14 + 0x10 + $payload.Length)
    Write-AsciiFixed $bif 0 'BIFF' 4
    Write-AsciiFixed $bif 4 'V1  ' 4
    Write-U32 $bif 8 1
    Write-U32 $bif 0x0c 0
    Write-U32 $bif 0x10 0x14
    Write-U32 $bif 0x14 0
    Write-U32 $bif 0x18 0x24
    Write-U32 $bif 0x1c $payload.Length
    Write-U16 $bif 0x20 1010
    Write-U16 $bif 0x22 0
    [Array]::Copy($payload, 0, $bif, 0x24, $payload.Length)
    [System.IO.File]::WriteAllBytes($bifPath, $bif)

    $bifName = 'data\registry.bif' + [char] 0
    $bifNameBytes = [System.Text.Encoding]::ASCII.GetBytes($bifName)
    $bifEntryOffset = 0x18
    $resourceEntryOffset = $bifEntryOffset + 12
    $nameOffset = $resourceEntryOffset + 14
    $key = New-Object byte[] ($nameOffset + $bifNameBytes.Length)
    Write-AsciiFixed $key 0 'KEY ' 4
    Write-AsciiFixed $key 4 'V1  ' 4
    Write-U32 $key 8 1
    Write-U32 $key 0x0c 1
    Write-U32 $key 0x10 $bifEntryOffset
    Write-U32 $key 0x14 $resourceEntryOffset
    Write-U32 $key $bifEntryOffset $bif.Length
    Write-U32 $key ($bifEntryOffset + 4) $nameOffset
    Write-U16 $key ($bifEntryOffset + 8) $bifNameBytes.Length
    Write-U16 $key ($bifEntryOffset + 10) 1
    Write-AsciiFixed $key $resourceEntryOffset 'OH6000' 8
    Write-U16 $key ($resourceEntryOffset + 8) 1010
    Write-U32 $key ($resourceEntryOffset + 10) 0
    [Array]::Copy($bifNameBytes, 0, $key, $nameOffset, $bifNameBytes.Length)
    [System.IO.File]::WriteAllBytes((Join-Path $GameRoot 'chitin.key'), $key)
}

function New-RegistryItmFixture {
    param([string] $Path, [int] $Category = 0, [int] $Charge1 = 0)
    $abilityCount = if ($Charge1 -gt 0) { 1 } else { 0 }
    $buffer = New-Object byte[] (0x72 + 0x38 * $abilityCount)
    Write-AsciiFixed $buffer 0 'ITM V1  ' 8
    Write-U16 $buffer 0x1c $Category
    Write-U32 $buffer 0x64 0x72
    Write-U16 $buffer 0x68 $abilityCount
    if ($abilityCount -ne 0) {
        Write-U16 $buffer (0x72 + 0x22) $Charge1
    }
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function New-RegistryStoreFixture {
    param([string] $Path)
    $saleOffset = 0x9c
    $buffer = New-Object byte[] ($saleOffset + 2 * 0x1c)
    Write-AsciiFixed $buffer 0 'STORV1.0' 8
    Write-U32 $buffer 0x2c $buffer.Length
    Write-U32 $buffer 0x34 $saleOffset
    Write-U32 $buffer 0x38 2
    Write-U32 $buffer 0x4c $buffer.Length
    Write-U32 $buffer 0x70 $buffer.Length
    Write-AsciiFixed $buffer $saleOffset 'synt0001' 8
    Write-U32 $buffer ($saleOffset + 0x14) 2
    Write-AsciiFixed $buffer ($saleOffset + 0x1c) 'synt0002' 8
    Write-U32 $buffer ($saleOffset + 0x1c + 0x14) 1
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function New-RegistryTokenCreFixture {
    param([string] $Path)
    $slotsOffset = 0x300
    $buffer = New-Object byte[] ($slotsOffset + 37 * 2)
    Write-AsciiFixed $buffer 0 'CRE V1.0' 8
    Write-U32 $buffer 0x2b8 $slotsOffset
    Write-U32 $buffer 0x2bc $slotsOffset
    Write-U32 $buffer 0x2c0 0
    for ($slot = 0; $slot -lt 37; $slot++) {
        Write-U16 $buffer ($slotsOffset + 2 * $slot) 0xffff
    }
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function Add-RegistryRemovalFixtures {
    param([string] $GameRoot)
    $override = Join-Path $GameRoot 'override'
    foreach ($resref in 'synt0001', 'synt0002', 'synt0004', 'anon0001', 'anon0002', 'rng00001', 'rng00002') {
        New-RegistryItmFixture -Path (Join-Path $override ($resref + '.itm'))
    }
    New-RegistryItmFixture -Path (Join-Path $override 'synt0003.itm') -Category 9 -Charge1 2
    New-RegistryStoreFixture -Path (Join-Path $override 'shop0001.sto')
    New-RegistryTokenCreFixture -Path (Join-Path $override 'fltier1.cre')
}

function Set-RegistryRemovalHistory {
    param([string] $GameRoot, [string[]] $Rows)
    $text = "Tier Token Ident`n" + (($Rows -join "`n") + "`n")
    [System.IO.File]::WriteAllText((Join-Path $GameRoot 'override\fl#removeditems.2da'), $text, [System.Text.Encoding]::ASCII)
    [System.IO.File]::WriteAllBytes(
        (Join-Path $GameRoot 'override\fl#randomseed.2da'),
        [byte[]] @(1, 0, 0, 0, 0, 0)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $GameRoot 'override\fl#randoptions.2da'),
        "Option`nweidu_action`n",
        [System.Text.Encoding]::ASCII
    )
}

function Invoke-RegistryHarness {
    param(
        [int] $Component,
        [string] $Name,
        [string] $GameRoot,
        [bool] $ExpectSuccess = $true,
        [string] $ExpectedErrorCode = '',
        [string] $ExpectedErrorDomain = 'FLIR_REGISTRY_ERR'
    )
    $reportPath = Join-Path $GameRoot ('registry-report-' + $Name + '.txt')
    $arguments = @(
        $script:harnessPath,
        '--game', $GameRoot,
        '--force-install-list', [string] $Component,
        '--args', $script:registryPath,
        '--args', $reportPath,
        '--args', $script:removalPlanPath,
        '--args', $script:coreLibPath,
        '--args', $script:catalogPath,
        '--language', '0',
        '--use-lang', 'en_US',
        '--no-exit-pause',
        '--quick-log'
    )
    Push-Location $GameRoot
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
            throw "Registry harness case '$Name' failed unexpectedly.`n$joined"
        }
    }
    else {
        if ($exitCode -eq 0 -or $joined -notmatch 'NOT INSTALLED DUE TO ERRORS') {
            throw "Registry harness case '$Name' did not fail as expected.`n$joined"
        }
        if ($joined -notmatch [regex]::Escape("$ExpectedErrorDomain $ExpectedErrorCode")) {
            throw "Registry harness case '$Name' did not report '$ExpectedErrorCode'.`n$joined"
        }
    }
    [pscustomobject]@{
        ReportPath = $reportPath
        Report = if (Test-Path -LiteralPath $reportPath) { [System.IO.File]::ReadAllText($reportPath) } else { '' }
        Log = $joined
    }
}

function Invoke-RegistryReinstall {
    param([string] $GameRoot, [string] $ReportName, [int] $Component = 0)
    $reportPath = Join-Path $GameRoot ('registry-report-' + $ReportName + '.txt')
    $arguments = @(
        $script:harnessPath,
        '--game', $GameRoot,
        '--force-uninstall-list', [string] $Component,
        '--force-install-list', [string] $Component,
        '--args', $script:registryPath,
        '--args', $reportPath,
        '--args', $script:removalPlanPath,
        '--args', $script:coreLibPath,
        '--args', $script:catalogPath,
        '--language', '0',
        '--use-lang', 'en_US',
        '--no-exit-pause',
        '--quick-log'
    )
    Push-Location $GameRoot
    try {
        $output = @(& $script:resolvedWeidu @arguments 2>&1 | ForEach-Object { [string] $_ })
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $joined = $output -join "`n"
    if ($exitCode -ne 0 -or $joined -match 'NOT INSTALLED DUE TO ERRORS') {
        throw "Registry reinstall failed unexpectedly.`n$joined"
    }
}

function Invoke-RegistryUninstallOnly {
    param([string] $GameRoot, [int] $Component = 0)
    Push-Location $GameRoot
    try {
        $output = @(& $script:resolvedWeidu $script:harnessPath --game $GameRoot --force-uninstall-list $Component --args $script:registryPath --args (Join-Path $GameRoot 'registry-report-uninstall.txt') --args $script:removalPlanPath --args $script:coreLibPath --args $script:catalogPath --language 0 --use-lang en_US --no-exit-pause --quick-log 2>&1 | ForEach-Object { [string] $_ })
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $joined = $output -join "`n"
    if ($exitCode -ne 0) {
        throw "Registry uninstall failed unexpectedly.`n$joined"
    }
}

function Set-RegistryState {
    param([string] $GameRoot, [string] $Text)
    [System.IO.File]::WriteAllText((Join-Path $GameRoot 'override\fl#irreg.2da'), $Text, [System.Text.Encoding]::ASCII)
}

function Get-RegistryState {
    param([string] $GameRoot)
    [System.IO.File]::ReadAllText((Join-Path $GameRoot 'override\fl#irreg.2da'))
}

function Assert-ContainsLine {
    param([string] $Text, [string] $Line)
    if (@($Text -split "`r?`n" | Where-Object { $_ -ceq $Line }).Count -ne 1) {
        throw "Expected line exactly once: $Line`n$Text"
    }
}

function Invoke-RandomSeedHarness {
    param(
        [string] $Name,
        [string] $GameRoot,
        [int] $Component = 0,
        [bool] $ExpectSuccess = $true,
        [string] $ExpectedErrorCode = ''
    )
    $reportPath = Join-Path $GameRoot ('random-seed-report-' + $Name + '.txt')
    $randomiserLib = Join-Path $GameRoot 'randomiser\lib'
    $null = New-Item -ItemType Directory -Path $randomiserLib -Force
    [System.IO.File]::Copy($script:registryPath, (Join-Path $randomiserLib 'registry.tpa'), $true)
    $arguments = @(
        $script:randomSeedHarnessPath,
        '--game', $GameRoot,
        '--force-install-list', [string] $Component,
        '--args', $script:randomSeedPath,
        '--args', $reportPath,
        '--args', $script:removalPlanPath,
        '--args', $script:catalogPath,
        '--language', '0',
        '--use-lang', 'en_US',
        '--no-exit-pause',
        '--quick-log'
    )
    Push-Location $GameRoot
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
            throw "Random-seed harness case '$Name' failed unexpectedly.`n$joined"
        }
    }
    else {
        if ($exitCode -eq 0 -or $joined -notmatch 'NOT INSTALLED DUE TO ERRORS') {
            throw "Random-seed harness case '$Name' did not fail as expected.`n$joined"
        }
        if ($joined -notmatch [regex]::Escape("FLIR_RANDOM_SEED_ERR $ExpectedErrorCode")) {
            throw "Random-seed harness case '$Name' did not report '$ExpectedErrorCode'.`n$joined"
        }
    }
    [pscustomobject]@{
        Report = if (Test-Path -LiteralPath $reportPath) { [System.IO.File]::ReadAllText($reportPath) } else { '' }
        Log = $joined
    }
}

function Add-RegistryRedFailure {
    param([string] $Code, [string] $Message)
    $script:redFailures.Add($Message)
    Write-Output "EXPECTED_RED $Code $Message"
}

$scratchRoot = Join-Path $resolvedTempRoot ('bgee-itemrandomiser-registry-' + [guid]::NewGuid().ToString('N'))
$scratchRoot = Assert-OutsideForbiddenLiveRoot -Path $scratchRoot
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated registry scratch directory escaped its disposable parent.'
}

try {
    $null = New-Item -ItemType Directory -Path $scratchRoot

    $productionGame = Join-Path $scratchRoot 'production-shape-game'
    New-MinimalFakeGame -GameRoot $productionGame
    try {
        $production = Invoke-RegistryHarness -Component 30 -Name 'production-shape' -GameRoot $productionGame
        if ($production.Report -match 'catalog=(0|123456)\b') {
            $redFailures.Add('Production-shaped registry catalog fingerprint is inert/harness-hardcoded.')
            Write-Output 'EXPECTED_RED Registry_ProductionShapeCatalogFingerprintInert'
        }
        foreach ($requiredLine in @(
            'ROW bg2 1 unit legacy:unit-bg2-1-1-alpha 1 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-x0-x x0 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-y0-x y0 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw100-same-1 1 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw200-same-1 2 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw300-fixes-1 3 1',
            'ROW bg1 24 slot legacy:slot-bg1-24-raw400-bg1row-1 1 1'
        )) {
            if (@($production.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                $redFailures.Add("Production-shaped registry report missing: $requiredLine")
                Write-Output "EXPECTED_RED Registry_ProductionShapeMissingLine $requiredLine"
            }
        }
        if (@($production.Report -split "`r?`n" | Where-Object { $_ -match '^ROW bg2 1 unit legacy:unit-bg2-1-x0-' }).Count -ne 1) {
            $redFailures.Add('Production-shaped registry double-registered x0 extra token.')
            Write-Output 'EXPECTED_RED Registry_ProductionShapeDuplicateExtra x0'
        }
        if (@($production.Report -split "`r?`n" | Where-Object { $_ -match '^ROW bg2 1 unit legacy:unit-bg2-1-y0-' }).Count -ne 1) {
            $redFailures.Add('Production-shaped registry double-registered y0 extra token.')
            Write-Output 'EXPECTED_RED Registry_ProductionShapeDuplicateExtra y0'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        $redFailures.Add("Production-shaped registry harness failed: $message")
        Write-Output "EXPECTED_RED Registry_ProductionShapeExecutable $message"
    }

    $migrationFilterGame = Join-Path $scratchRoot 'migration-filter-game'
    New-MinimalFakeGame -GameRoot $migrationFilterGame
    [System.IO.File]::WriteAllBytes((Join-Path $migrationFilterGame 'override\synt0001.itm'), @())
    try {
        $migrationFilter = Invoke-RegistryHarness -Component 60 -Name 'migration-filter' -GameRoot $migrationFilterGame
        foreach ($requiredLine in @(
            'ROW bg2 1 slot legacy:slot-bg2-1-raw100-keepdyn-1 1 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw050-extdyn-1 2 1'
        )) {
            if (@($migrationFilter.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                $compactReport = ($migrationFilter.Report -replace "`r?`n", ' | ')
                Add-RegistryRedFailure -Code 'Registry_MigrationFilteredPreextensionOrdering' -Message "missing $requiredLine; report=$compactReport"
            }
        }
        if ($migrationFilter.Report -match 'skipdyn') {
            Add-RegistryRedFailure -Code 'Registry_MigrationFilteredPreextensionOrdering' -Message 'dynamically filtered identity was snapshotted'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_MigrationFilteredPreextensionExecutable' -Message $message
    }

    $insertSlotGame = Join-Path $scratchRoot 'insert-slot-game'
    New-MinimalFakeGame -GameRoot $insertSlotGame
    try {
        $null = Invoke-RegistryHarness -Component 61 -Name 'seed-insert-slots' -GameRoot $insertSlotGame
        $insertSlots = Invoke-RegistryHarness -Component 62 -Name 'insert-before-existing' -GameRoot $insertSlotGame
        foreach ($requiredLine in @(
            'ROW bg2 1 slot legacy:slot-bg2-1-raw100-a-1 2 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw200-b-1 3 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw025-new-1 4 1'
        )) {
            if (@($insertSlots.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_InsertBeforeExistingKeepsCompacts' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_InsertBeforeExistingExecutable' -Message $message
    }

    $removeSlotGame = Join-Path $scratchRoot 'remove-slot-game'
    New-MinimalFakeGame -GameRoot $removeSlotGame
    try {
        $null = Invoke-RegistryHarness -Component 61 -Name 'seed-remove-slots' -GameRoot $removeSlotGame
        $removeSlots = Invoke-RegistryHarness -Component 63 -Name 'remove-before-existing' -GameRoot $removeSlotGame
        foreach ($requiredLine in @(
            'ROW bg2 1 slot legacy:slot-bg2-1-raw050-old-1 1 0',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw100-a-1 2 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw200-b-1 3 1'
        )) {
            if (@($removeSlots.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_RemoveBeforeExistingKeepsCompacts' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_RemoveBeforeExistingExecutable' -Message $message
    }

    $snapshotSurvivalGame = Join-Path $scratchRoot 'snapshot-survival-game'
    New-MinimalFakeGame -GameRoot $snapshotSurvivalGame
    try {
        $snapshotSurvival = Invoke-RegistryHarness -Component 64 -Name 'snapshot-survives-final' -GameRoot $snapshotSurvivalGame
        foreach ($requiredLine in @(
            'ROW bg2 1 slot legacy:slot-bg2-1-raw999-old_a-1 1 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw005-old_b-1 2 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw001-new-1 3 1'
        )) {
            if (@($snapshotSurvival.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_OldSnapshotSurvivesFinalCapture' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_OldSnapshotSurvivesFinalCaptureExecutable' -Message $message
    }

    $snapshotRemovalGame = Join-Path $scratchRoot 'snapshot-removal-game'
    New-MinimalFakeGame -GameRoot $snapshotRemovalGame
    try {
        $snapshotRemoval = Invoke-RegistryHarness -Component 65 -Name 'snapshot-removal' -GameRoot $snapshotRemovalGame
        foreach ($requiredLine in @(
            'ROW bg2 1 slot legacy:slot-bg2-1-raw100-keep-1 1 1',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw200-removed-1 2 0',
            'ROW bg2 1 slot legacy:slot-bg2-1-raw050-added-1 3 1'
        )) {
            if (@($snapshotRemoval.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_MigrationRemovedSlotTombstoned' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_MigrationRemovedSlotExecutable' -Message $message
    }

    $filterReuseGame = Join-Path $scratchRoot 'filter-reuse-game'
    New-MinimalFakeGame -GameRoot $filterReuseGame
    [System.IO.File]::WriteAllBytes((Join-Path $filterReuseGame 'override\synt0001.itm'), @())
    try {
        $null = Invoke-RegistryHarness -Component 66 -Name 'filter-reuse' -GameRoot $filterReuseGame
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_MigrationFilterRerolled' -Message $message
    }

    foreach ($tamperCase in @(
        [pscustomobject]@{ Seed = 70; Name = 'bad-header'; Code = 'BAD_HEADER'; Red = 'Registry_BadHeaderColumnsAccepted' },
        [pscustomobject]@{ Seed = 71; Name = 'bad-meta-kind'; Code = 'BAD_META'; Red = 'Registry_BadMetaKindAccepted' },
        [pscustomobject]@{ Seed = 72; Name = 'bad-meta-compact'; Code = 'BAD_META'; Red = 'Registry_BadMetaCompactAccepted' },
        [pscustomobject]@{ Seed = 73; Name = 'bad-meta-enabled'; Code = 'BAD_META'; Red = 'Registry_BadMetaEnabledAccepted' },
        [pscustomobject]@{ Seed = 74; Name = 'bad-stable-extra-separator'; Code = 'BAD_STABLE_ID'; Red = 'Registry_BadStableExtraSeparatorAccepted' },
        [pscustomobject]@{ Seed = 76; Name = 'unknown-origin'; Code = 'BAD_META'; Red = 'Registry_UnknownOriginAccepted' },
        [pscustomobject]@{ Seed = 77; Name = 'zero-catalog-hash'; Code = 'BAD_META'; Red = 'Registry_ZeroCatalogHashAccepted' },
        [pscustomobject]@{ Seed = 78; Name = 'zero-file-fingerprint'; Code = 'BAD_META'; Red = 'Registry_ZeroFileFingerprintAccepted' },
        [pscustomobject]@{ Seed = 79; Name = 'oversized-catalog-hash'; Code = 'BAD_META'; Red = 'Registry_OversizedCatalogHashAccepted' }
    )) {
        $tamperGame = Join-Path $scratchRoot ('tamper-' + $tamperCase.Name + '-game')
        New-MinimalFakeGame -GameRoot $tamperGame
        try {
            $null = Invoke-RegistryHarness -Component $tamperCase.Seed -Name ('seed-' + $tamperCase.Name) -GameRoot $tamperGame
            $null = Invoke-RegistryHarness -Component 3 -Name $tamperCase.Name -GameRoot $tamperGame -ExpectSuccess $false -ExpectedErrorCode $tamperCase.Code
        }
        catch {
            $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 40) -join ' | '
            Add-RegistryRedFailure -Code $tamperCase.Red -Message $message
        }
    }

    $duplicateGame = Join-Path $scratchRoot 'duplicate-row-game'
    New-MinimalFakeGame -GameRoot $duplicateGame
    try {
        $null = Invoke-RegistryHarness -Component 75 -Name 'seed-duplicate-row' -GameRoot $duplicateGame
        $null = Invoke-RegistryHarness -Component 3 -Name 'duplicate-row' -GameRoot $duplicateGame -ExpectSuccess $false -ExpectedErrorCode 'DUPLICATE_IDENTITY'
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 40) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_ExactDuplicateAccepted' -Message $message
    }

    foreach ($originCase in @(
        [pscustomobject]@{ Name = 'missing-origin'; Expected = 'MISSING_META'; Transform = 'missing'; Red = 'Registry_MissingOriginAccepted' },
        [pscustomobject]@{ Name = 'duplicate-origin'; Expected = 'MISSING_META'; Transform = 'duplicate'; Red = 'Registry_DuplicateOriginAccepted' },
        [pscustomobject]@{ Name = 'origin-only-fingerprint-tamper'; Expected = 'FILE_FINGERPRINT'; Transform = 'switch'; Red = 'Registry_OriginFingerprintTamperAccepted' }
    )) {
        $originGame = Join-Path $scratchRoot ('tamper-' + $originCase.Name + '-game')
        New-MinimalFakeGame -GameRoot $originGame
        try {
            $null = Invoke-RegistryHarness -Component 0 -Name ('seed-' + $originCase.Name) -GameRoot $originGame
            $originState = Get-RegistryState -GameRoot $originGame
            switch ($originCase.Transform) {
                'missing' {
                    $originState = $originState -replace '(?m)^@meta\s+@origin\s+origin\s+fresh-postextension-v1\s+1\s+1\r?\n', ''
                }
                'duplicate' {
                    $duplicateOriginReplacement = '$1' + "`n" + '$1'
                    $originState = $originState -replace '(?m)^(@meta\s+@origin\s+origin\s+fresh-postextension-v1\s+1\s+1\r?)$', $duplicateOriginReplacement
                }
                'switch' {
                    $originState = $originState.Replace('fresh-postextension-v1', 'legacy-preextension-v1')
                }
            }
            Set-RegistryState -GameRoot $originGame -Text $originState
            $null = Invoke-RegistryHarness -Component 3 -Name $originCase.Name -GameRoot $originGame -ExpectSuccess $false -ExpectedErrorCode $originCase.Expected
        }
        catch {
            $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 50) -join ' | '
            Add-RegistryRedFailure -Code $originCase.Red -Message $message
        }
    }

    foreach ($preflightCase in @(
        [pscustomobject]@{ Component = 80; Name = 'planned-base-collision'; Code = 'COMPACT_COLLISION'; Red = 'Registry_PlannedBaseCollisionAccepted' },
        [pscustomobject]@{ Component = 81; Name = 'planned-extra-tombstone'; Code = 'TOMBSTONE_REAPPEAR'; Red = 'Registry_PlannedExtraTombstoneAccepted' },
        [pscustomobject]@{ Component = 85; Name = 'orphaned-legacy-extra'; Code = 'LEGACY_EXTRA_DRIFT'; Red = 'Registry_OrphanedLegacyExtraAccepted' }
    )) {
        $preflightGame = Join-Path $scratchRoot ($preflightCase.Name + '-game')
        New-MinimalFakeGame -GameRoot $preflightGame
        try {
            $null = Invoke-RegistryHarness -Component $preflightCase.Component -Name $preflightCase.Name -GameRoot $preflightGame -ExpectSuccess $false -ExpectedErrorCode $preflightCase.Code
            if (Test-Path -LiteralPath (Join-Path $preflightGame 'override\fl#mutated.mrk') -PathType Leaf) {
                Add-RegistryRedFailure -Code $preflightCase.Red -Message 'mutation marker exists after expected preflight failure'
            }
        }
        catch {
            $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 40) -join ' | '
            Add-RegistryRedFailure -Code $preflightCase.Red -Message $message
        }
    }

    foreach ($planCase in @(
        [pscustomobject]@{ Component = 82; Name = 'planned-charge-extra-monotonic'; Line = 'PLAN 1 y 1 legacy:unit-bg2-1-1-alpha-extra-y1 y1' },
        [pscustomobject]@{ Component = 83; Name = 'legacy-extra-reuse'; Line = 'PLAN 1 x 1 legacy:unit-bg2-1-1-alpha-extra-x1 x0' },
        [pscustomobject]@{ Component = 84; Name = 'existing-extra-reuse'; Line = 'PLAN 1 x 1 legacy:unit-bg2-1-1-alpha-extra-x1 x0' }
    )) {
        $planGame = Join-Path $scratchRoot ($planCase.Name + '-game')
        New-MinimalFakeGame -GameRoot $planGame
        try {
            $planResult = Invoke-RegistryHarness -Component $planCase.Component -Name $planCase.Name -GameRoot $planGame
            if (@($planResult.Report -split "`r?`n" | Where-Object { $_ -ceq $planCase.Line }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_ExtraStablePlanMismatch' -Message "missing $($planCase.Line)"
            }
        }
        catch {
            $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 40) -join ' | '
            Add-RegistryRedFailure -Code 'Registry_ExtraStablePlanExecutable' -Message $message
        }
    }

    $extraApplyGame = Join-Path $scratchRoot 'planned-extra-apply-game'
    New-MinimalFakeGame -GameRoot $extraApplyGame
    try {
        $extraApply = Invoke-RegistryHarness -Component 86 -Name 'planned-extra-apply' -GameRoot $extraApplyGame
        foreach ($requiredLine in @(
            'ROW bg2 1 unit legacy:unit-bg2-1-unrelated x0 0',
            'ROW bg2 1 unit legacy:unit-bg2-1-1-alpha 1 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-1-alpha-extra-x1 x1 1'
        )) {
            if (@($extraApply.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_PlannedExtraApplyMismatch' -Message "missing $requiredLine"
            }
        }
        if ($extraApply.Report -match 'legacy:unit-bg2-1-x1-x') {
            Add-RegistryRedFailure -Code 'Registry_PlannedExtraApplyIdentity' -Message 'applied extra fell back to compact-derived identity'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_PlannedExtraApplyExecutable' -Message $message
    }

    try {
        $catalogGameA = Join-Path $scratchRoot 'catalog-fingerprint-a-game'
        $catalogGameB = Join-Path $scratchRoot 'catalog-fingerprint-b-game'
        New-MinimalFakeGame -GameRoot $catalogGameA
        New-MinimalFakeGame -GameRoot $catalogGameB
        $catalogA = Invoke-RegistryHarness -Component 87 -Name 'catalog-fingerprint-a' -GameRoot $catalogGameA
        $catalogB = Invoke-RegistryHarness -Component 88 -Name 'catalog-fingerprint-b' -GameRoot $catalogGameB
        $fingerprintA = [regex]::Match($catalogA.Report, 'catalog=(\d+)').Groups[1].Value
        $fingerprintB = [regex]::Match($catalogB.Report, 'catalog=(\d+)').Groups[1].Value
        if ([string]::IsNullOrEmpty($fingerprintA) -or $fingerprintA -eq $fingerprintB) {
            Add-RegistryRedFailure -Code 'Registry_NormalizedCatalogFingerprintOmitted' -Message "catalog fingerprints did not change: a=$fingerprintA b=$fingerprintB"
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_NormalizedCatalogFingerprintExecutable' -Message $message
    }

    $ownershipGame = Join-Path $scratchRoot 'legacy-extra-ownership-game'
    New-MinimalFakeGame -GameRoot $ownershipGame
    try {
        $ownership = Invoke-RegistryHarness -Component 89 -Name 'legacy-extra-ownership' -GameRoot $ownershipGame
        $requiredOwnership = 'PLAN 1 x 1 legacy:unit-bg2-1-2-beta-extra-x1 x1'
        if (@($ownership.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredOwnership }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_LegacyExtraOwnershipRepurposed' -Message "missing $requiredOwnership"
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 60) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_LegacyExtraOwnershipExecutable' -Message $message
    }

    $automaticMigrationGame = Join-Path $scratchRoot 'automatic-legacy-migration-game'
    New-MinimalFakeGame -GameRoot $automaticMigrationGame
    Add-RegistryRemovalFixtures -GameRoot $automaticMigrationGame
    Set-RegistryRemovalHistory -GameRoot $automaticMigrationGame -Rows @(
        '1 1 alpha',
        '1 2 beta',
        '1 3 gamma',
        '1 4 delta',
        '1 x0 x',
        '1 y0 x'
    )
    try {
        $automaticMigration = Invoke-RegistryHarness -Component 90 -Name 'automatic-legacy-migration' -GameRoot $automaticMigrationGame
        foreach ($requiredLine in @(
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-alpha 1 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-alpha-extra-x1 x0 0',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-beta 2 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-beta-extra-x1 x1 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-gamma 3 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-gamma-extra-y1 y0 0',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-delta 4 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-delta-extra-y1 y1 1',
            'CURRENT 1 1 alpha',
            'CURRENT 1 2 beta',
            'CURRENT 1 3 gamma',
            'CURRENT 1 4 delta',
            'CURRENT 1 x1 x',
            'CURRENT 1 y1 x'
        )) {
            if (@($automaticMigration.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_AutomaticLegacyMigrationMismatch' -Message "missing $requiredLine"
            }
        }
        if ($automaticMigration.Report -match '(?m)^CURRENT 1 x0 x$' -or
            $automaticMigration.Report -match '(?m)^CURRENT 1 y0 x$' -or
            $automaticMigration.Report -match 'legacy:unit-bg2-1-ident-beta-extra-x1 x0 [01]$' -or
            $automaticMigration.Report -match 'legacy:unit-bg2-1-ident-delta-extra-y1 y0 [01]$') {
            Add-RegistryRedFailure -Code 'Registry_AutomaticLegacyMigrationRepurposed' -Message 'historical x0/y0 leaked into the current applied catalog or changed owner'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_AutomaticLegacyMigrationExecutable' -Message $message
    }

    $removedBaseGame = Join-Path $scratchRoot 'removed-base-migration-game'
    New-MinimalFakeGame -GameRoot $removedBaseGame
    Add-RegistryRemovalFixtures -GameRoot $removedBaseGame
    Set-RegistryRemovalHistory -GameRoot $removedBaseGame -Rows @('1 1 alpha')
    try {
        $removedBase = Invoke-RegistryHarness -Component 91 -Name 'removed-base-migration' -GameRoot $removedBaseGame
        $requiredBaseTombstone = 'ROW bg2 1 unit legacy:unit-bg2-1-ident-alpha 1 0'
        if (@($removedBase.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredBaseTombstone }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_RemovedLegacyBaseNotTombstoned' -Message "missing $requiredBaseTombstone"
        }
        if ($removedBase.Report -match '(?m)^CURRENT ') {
            Add-RegistryRedFailure -Code 'Registry_RemovedLegacyBaseStillCurrent' -Message 'disabled historical base leaked into the current removed set'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_RemovedLegacyBaseExecutable' -Message $message
    }

    $anonymousVirtualGame = Join-Path $scratchRoot 'anonymous-virtual-game'
    New-MinimalFakeGame -GameRoot $anonymousVirtualGame
    Add-RegistryRemovalFixtures -GameRoot $anonymousVirtualGame
    try {
        $anonymousVirtual = Invoke-RegistryHarness -Component 92 -Name 'anonymous-virtual' -GameRoot $anonymousVirtualGame
        foreach ($requiredLine in @(
            'PLAN_COUNT 2',
            'PLANMETA av0 x virtual blank legacy_blank',
            'PLANMETA av1 x virtual shop0001.sto explicit'
        )) {
            if (@($anonymousVirtual.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_AnonymousVirtualMetadataMismatch' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_AnonymousVirtualMetadataExecutable' -Message $message
    }

    $rngOrderGame = Join-Path $scratchRoot 'legacy-rng-order-game'
    New-MinimalFakeGame -GameRoot $rngOrderGame
    Add-RegistryRemovalFixtures -GameRoot $rngOrderGame
    try {
        $rngOrder = Invoke-RegistryHarness -Component 93 -Name 'legacy-rng-order' -GameRoot $rngOrderGame
        if (@($rngOrder.Report -split "`r?`n" | Where-Object { $_ -ceq 'RNG_ORDER_OK 64' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_LegacyRngOrderMismatch' -Message 'missing RNG_ORDER_OK 64'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_LegacyRngOrderExecutable' -Message $message
    }

    $campaignRoutingGame = Join-Path $scratchRoot 'campaign-routing-game'
    New-MinimalFakeGame -GameRoot $campaignRoutingGame
    Add-RegistryRemovalFixtures -GameRoot $campaignRoutingGame
    try {
        $campaignRouting = Invoke-RegistryHarness -Component 94 -Name 'campaign-routing' -GameRoot $campaignRoutingGame
        foreach ($requiredLine in @(
            'ROUTE bg2 1 1',
            'ROUTE bg2 24 0',
            'ROUTE bg1 1 0',
            'ROUTE bg1 24 1',
            'CAMPAIGN 1 bg2',
            'CAMPAIGN 24 bg1',
            'DECL ident:alpha bg2_tiers',
            'DECL ident:beta bg1_tiers',
            'DECL anon:1:ax:anon0001:2 bg2_tiers'
        )) {
            if (@($campaignRouting.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_CampaignRoutingMismatch' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_CampaignRoutingExecutable' -Message $message
    }

    $crossCampaignReplaceGame = Join-Path $scratchRoot 'cross-campaign-replace-game'
    New-MinimalFakeGame -GameRoot $crossCampaignReplaceGame
    Add-RegistryRemovalFixtures -GameRoot $crossCampaignReplaceGame
    try {
        $null = Invoke-RegistryHarness -Component 95 -Name 'cross-campaign-replace' -GameRoot $crossCampaignReplaceGame -ExpectSuccess $false -ExpectedErrorCode 'HOOK_CAMPAIGN_TIER' -ExpectedErrorDomain 'FLIR_REMOVAL_ERR'
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_CrossCampaignReplaceAccepted' -Message $message
    }

    $freshRandomSeedGame = Join-Path $scratchRoot 'fresh-random-seed-game'
    New-MinimalFakeGame -GameRoot $freshRandomSeedGame
    try {
        $freshRandomSeed = Invoke-RandomSeedHarness -Name 'fresh' -GameRoot $freshRandomSeedGame
        foreach ($requiredLine in @('PRESERVE n', 'SEED 1', 'OPTIONS 1', 'REMOVED 0')) {
            if (@($freshRandomSeed.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_FreshNoninteractiveStateSelection' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_FreshNoninteractiveStateSelection' -Message $message
    }

    $seedMaskFixtures = @(
        [pscustomobject]@{ Bit = 1; Name = 'fl#randomseed.2da'; Bytes = [byte[]] @(1, 0, 0, 0, 0, 0) },
        [pscustomobject]@{ Bit = 2; Name = 'fl#removeditems.2da'; Bytes = [System.Text.Encoding]::ASCII.GetBytes("Tier Token Ident`n") },
        [pscustomobject]@{ Bit = 4; Name = 'fl#randoptions.2da'; Bytes = [System.Text.Encoding]::ASCII.GetBytes("Option`n") }
    )
    foreach ($stateMask in 1..6) {
        $partialRandomSeedGame = Join-Path $scratchRoot ("partial-mask-$stateMask-random-seed-game")
        New-MinimalFakeGame -GameRoot $partialRandomSeedGame
        foreach ($fixture in $seedMaskFixtures) {
            if (($stateMask -band $fixture.Bit) -ne 0) {
                [System.IO.File]::WriteAllBytes((Join-Path $partialRandomSeedGame ('override\' + $fixture.Name)), $fixture.Bytes)
            }
        }
        try {
            $null = Invoke-RandomSeedHarness -Name "partial-mask-$stateMask" -GameRoot $partialRandomSeedGame -ExpectSuccess $false -ExpectedErrorCode 'INCOMPLETE_STATE'
            foreach ($fixture in $seedMaskFixtures) {
                $statePath = Join-Path $partialRandomSeedGame ('override\' + $fixture.Name)
                if (($stateMask -band $fixture.Bit) -ne 0) {
                    $actualBytes = [System.IO.File]::ReadAllBytes($statePath)
                    if ([Convert]::ToBase64String($fixture.Bytes) -cne [Convert]::ToBase64String($actualBytes)) {
                        Add-RegistryRedFailure -Code 'Registry_PartialStateAcceptedOrOverwritten' -Message "mask $stateMask changed $($fixture.Name)"
                    }
                }
                elseif (Test-Path -LiteralPath $statePath -PathType Leaf) {
                    Add-RegistryRedFailure -Code 'Registry_PartialStateAcceptedOrOverwritten' -Message "mask $stateMask created missing $($fixture.Name)"
                }
            }
        }
        catch {
            $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
            Add-RegistryRedFailure -Code 'Registry_PartialStateAcceptedOrOverwritten' -Message $message
        }
    }

    $completeRandomSeedGame = Join-Path $scratchRoot 'complete-mask-7-random-seed-game'
    New-MinimalFakeGame -GameRoot $completeRandomSeedGame
    foreach ($fixture in $seedMaskFixtures) {
        [System.IO.File]::WriteAllBytes((Join-Path $completeRandomSeedGame ('override\' + $fixture.Name)), $fixture.Bytes)
    }
    try {
        $completeRandomSeed = Invoke-RandomSeedHarness -Name 'complete-mask-7' -GameRoot $completeRandomSeedGame
        foreach ($requiredLine in 'PRESERVE y', 'SEED 1', 'OPTIONS 1', 'REMOVED 1') {
            if (@($completeRandomSeed.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_CompleteStateNotPreserved' -Message "missing $requiredLine"
            }
        }
        foreach ($fixture in $seedMaskFixtures) {
            $actualBytes = [System.IO.File]::ReadAllBytes((Join-Path $completeRandomSeedGame ('override\' + $fixture.Name)))
            if ([Convert]::ToBase64String($fixture.Bytes) -cne [Convert]::ToBase64String($actualBytes)) {
                Add-RegistryRedFailure -Code 'Registry_CompleteStateNotPreserved' -Message "complete mask changed $($fixture.Name)"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_CompleteStateExecutable' -Message $message
    }

    $freshPreserveGame = Join-Path $scratchRoot 'fresh-preserve-without-legacy-state-game'
    New-MinimalFakeGame -GameRoot $freshPreserveGame
    try {
        $freshPreserve = Invoke-RegistryHarness -Component 96 -Name 'fresh-preserve-without-legacy-state' -GameRoot $freshPreserveGame
        if (@($freshPreserve.Report -split "`r?`n" | Where-Object { $_ -ceq 'OLD_UNMARKED 0' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_FreshPreserveMisclassifiedAsLegacy' -Message 'missing OLD_UNMARKED 0'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_FreshPreserveClassificationExecutable' -Message $message
    }

    foreach ($campaignGuardCase in @(
        [pscustomobject]@{ Component = 97; Name = 'missing-campaign-tier'; Code = 'CAMPAIGN_TIER_MISSING' },
        [pscustomobject]@{ Component = 98; Name = 'ambiguous-campaign-tier'; Code = 'CAMPAIGN_TIER_AMBIGUOUS' }
    )) {
        $campaignGuardGame = Join-Path $scratchRoot ($campaignGuardCase.Name + '-game')
        New-MinimalFakeGame -GameRoot $campaignGuardGame
        try {
            $null = Invoke-RegistryHarness -Component $campaignGuardCase.Component -Name $campaignGuardCase.Name -GameRoot $campaignGuardGame -ExpectSuccess $false -ExpectedErrorCode $campaignGuardCase.Code
        }
        catch {
            $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
            Add-RegistryRedFailure -Code 'Registry_CampaignTierGuardMissing' -Message $message
        }
    }

    $missingPreserveHistoryGame = Join-Path $scratchRoot 'missing-preserve-history-game'
    New-MinimalFakeGame -GameRoot $missingPreserveHistoryGame
    try {
        $null = Invoke-RegistryHarness -Component 99 -Name 'missing-preserve-history' -GameRoot $missingPreserveHistoryGame -ExpectSuccess $false -ExpectedErrorCode 'PRESERVE_STATE_MISSING' -ExpectedErrorDomain 'FLIR_REMOVAL_ERR'
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_MissingPreserveHistoryAccepted' -Message $message
    }

    $preserveNewUnitGame = Join-Path $scratchRoot 'preserve-new-unit-game'
    New-MinimalFakeGame -GameRoot $preserveNewUnitGame
    Add-RegistryRemovalFixtures -GameRoot $preserveNewUnitGame
    Set-RegistryRemovalHistory -GameRoot $preserveNewUnitGame -Rows @('1 1 alpha')
    try {
        $preserveNewUnit = Invoke-RegistryHarness -Component 100 -Name 'preserve-new-unit' -GameRoot $preserveNewUnitGame
        foreach ($requiredLine in @(
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-alpha 1 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-beta 2 1',
            'ROW bg2 1 unit legacy:unit-bg2-1-ident-beta-extra-x1 x0 1',
            'CURRENT 1 1 alpha',
            'CURRENT 1 2 beta',
            'CURRENT 1 x0 x',
            'STORE_SALE_COUNT 0'
        )) {
            if (@($preserveNewUnit.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_PreserveNewUnitOmitted' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_PreserveNewUnitExecutable' -Message $message
    }

    $duplicateAnonymousGame = Join-Path $scratchRoot 'duplicate-anonymous-unit-game'
    New-MinimalFakeGame -GameRoot $duplicateAnonymousGame
    Add-RegistryRemovalFixtures -GameRoot $duplicateAnonymousGame
    try {
        $null = Invoke-RegistryHarness -Component 101 -Name 'duplicate-anonymous-unit' -GameRoot $duplicateAnonymousGame -ExpectSuccess $false -ExpectedErrorCode 'COMPACT_COLLISION'
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_DuplicateAnonymousUnitAccepted' -Message $message
    }

    $virtualMetadataGame = Join-Path $scratchRoot 'virtual-metadata-game'
    New-MinimalFakeGame -GameRoot $virtualMetadataGame
    Add-RegistryRemovalFixtures -GameRoot $virtualMetadataGame
    try {
        $virtualMetadata = Invoke-RegistryHarness -Component 102 -Name 'virtual-metadata' -GameRoot $virtualMetadataGame
        foreach ($requiredLine in @(
            'VIRTUAL q0 2 5 6 7 2',
            'VIRTUAL c0 1 7 8 9 7',
            'CURRENT 1 q0 x',
            'CURRENT 1 x0 x',
            'CURRENT 1 c0 x',
            'CURRENT 1 y5 x'
        )) {
            if (@($virtualMetadata.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_VirtualDeclaredMetadataIgnored' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_VirtualDeclaredMetadataExecutable' -Message $message
    }

    $namedStableDriftGame = Join-Path $scratchRoot 'named-stable-drift-game'
    New-MinimalFakeGame -GameRoot $namedStableDriftGame
    try {
        $null = Invoke-RegistryHarness -Component 103 -Name 'named-stable-drift' -GameRoot $namedStableDriftGame -ExpectSuccess $false -ExpectedErrorCode 'LEGACY_ORDINAL_DRIFT'
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_NamedStableCompactDriftAccepted' -Message $message
    }

    $birthStableGame = Join-Path $scratchRoot 'birth-stable-game'
    New-MinimalFakeGame -GameRoot $birthStableGame
    Add-RegistryRemovalFixtures -GameRoot $birthStableGame
    try {
        $birthStable = Invoke-RegistryHarness -Component 104 -Name 'birth-stable' -GameRoot $birthStableGame
        if (@($birthStable.Report -split "`r?`n" | Where-Object { $_ -ceq 'STABLE legacy:unit-bg2-1-ident-alpha' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_SourceBirthStableNotCarried' -Message 'missing named birth stable identity'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_SourceBirthStableExecutable' -Message $message
    }

    $anonymousBirthGame = Join-Path $scratchRoot 'anonymous-birth-game'
    New-MinimalFakeGame -GameRoot $anonymousBirthGame
    Add-RegistryRemovalFixtures -GameRoot $anonymousBirthGame
    try {
        $anonymousBirth = Invoke-RegistryHarness -Component 105 -Name 'anonymous-birth' -GameRoot $anonymousBirthGame
        $anonLines = @($anonymousBirth.Report -split "`r?`n" | Where-Object { $_ -match '^ANON [123] legacy:unit-bg2-1-anon-[1-9][0-9]*-[12]$' })
        if ($anonLines.Count -ne 3) {
            Add-RegistryRedFailure -Code 'Registry_AnonymousBirthIdentityMissing' -Message 'missing three anonymous birth stable identities'
        }
        else {
            $anonOne = [regex]::Match($anonLines[0], '^ANON 1 legacy:unit-bg2-1-anon-([1-9][0-9]*)-1$')
            $anonTwo = [regex]::Match($anonLines[1], '^ANON 2 legacy:unit-bg2-1-anon-([1-9][0-9]*)-2$')
            $anonThree = [regex]::Match($anonLines[2], '^ANON 3 legacy:unit-bg2-1-anon-([1-9][0-9]*)-1$')
            if (-not $anonOne.Success -or -not $anonTwo.Success -or -not $anonThree.Success -or
                $anonOne.Groups[1].Value -cne $anonTwo.Groups[1].Value -or
                $anonOne.Groups[1].Value -ceq $anonThree.Groups[1].Value) {
                Add-RegistryRedFailure -Code 'Registry_AnonymousBirthTokenContamination' -Message 'anonymous token-independent fingerprint or occurrence mismatch'
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_AnonymousBirthExecutable' -Message $message
    }

    $directVirtualGame = Join-Path $scratchRoot 'direct-virtual-game'
    New-MinimalFakeGame -GameRoot $directVirtualGame
    Add-RegistryRemovalFixtures -GameRoot $directVirtualGame
    Set-RegistryRemovalHistory -GameRoot $directVirtualGame -Rows @('1 sp0 x')
    try {
        $directVirtual = Invoke-RegistryHarness -Component 106 -Name 'direct-virtual' -GameRoot $directVirtualGame
        foreach ($requiredLine in @(
            'PLAN_COUNT 1',
            'PLANMETA sp0 x virtual blank legacy_blank',
            'ROW bg2 1 unit legacy:unit-bg2-1-direct-synt0001-1 sp0 1',
            'CURRENT 1 sp0 x'
        )) {
            if (@($directVirtual.Report -split "`r?`n" | Where-Object { $_ -ceq $requiredLine }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_DirectVirtualMigrationMismatch' -Message "missing $requiredLine"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_DirectVirtualMigrationExecutable' -Message $message
    }

    $absentHistoricalBaseGame = Join-Path $scratchRoot 'absent-historical-base-game'
    New-MinimalFakeGame -GameRoot $absentHistoricalBaseGame
    Set-RegistryRemovalHistory -GameRoot $absentHistoricalBaseGame -Rows @('1 1 alpha')
    try {
        $absentHistoricalBase = Invoke-RegistryHarness -Component 107 -Name 'absent-historical-base' -GameRoot $absentHistoricalBaseGame
        if (@($absentHistoricalBase.Report -split "`r?`n" | Where-Object { $_ -ceq 'ROW bg2 1 unit legacy:unit-bg2-1-1-alpha 1 0' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_AbsentHistoricalBaseNotTombstoned' -Message 'missing disabled historical base row'
        }
        if ($absentHistoricalBase.Report -match '(?m)^CURRENT ') {
            Add-RegistryRedFailure -Code 'Registry_AbsentHistoricalBaseBecameCurrent' -Message 'absent historical base leaked into current catalog'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_AbsentHistoricalBaseExecutable' -Message $message
    }

    $orphanedHistoricalYGame = Join-Path $scratchRoot 'orphaned-historical-y-game'
    New-MinimalFakeGame -GameRoot $orphanedHistoricalYGame
    Set-RegistryRemovalHistory -GameRoot $orphanedHistoricalYGame -Rows @('1 y0 x')
    try {
        $null = Invoke-RegistryHarness -Component 108 -Name 'orphaned-historical-y' -GameRoot $orphanedHistoricalYGame -ExpectSuccess $false -ExpectedErrorCode 'LEGACY_EXTRA_DRIFT'
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_OrphanedHistoricalYAccepted' -Message $message
    }

    $historicalOverflowGame = Join-Path $scratchRoot 'historical-overflow-game'
    New-MinimalFakeGame -GameRoot $historicalOverflowGame
    Set-RegistryRemovalHistory -GameRoot $historicalOverflowGame -Rows @(
        '1 1 alpha',
        '1 2 beta',
        '1 3 gamma'
    )
    try {
        $historicalOverflow = Invoke-RegistryHarness -Component 109 -Name 'historical-overflow' -GameRoot $historicalOverflowGame
        if ($historicalOverflow.Report -notmatch '(?m)^STATE 0 1 legacy-preextension-v1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowOriginMissing' -Message 'old-unmarked migration did not select the legacy pre-extension origin'
        }
        $expectedOverflow = [regex]::Match(
            $historicalOverflow.Report,
            '(?m)^EXPECTED_OVERFLOW (legacy:slot-bg2-1-[0-9]+--1) 3\r?$'
        )
        if (-not $expectedOverflow.Success) {
            $compactReport = ($historicalOverflow.Report -replace "`r?`n", ' | ')
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowExpectationMissing' -Message "missing production-shaped expected overflow identity; report=$compactReport"
        }
        else {
            $expectedOverflowRow = 'ROW bg2 1 slot ' + $expectedOverflow.Groups[1].Value + ' 3 1'
            if (@($historicalOverflow.Report -split "`r?`n" | Where-Object { $_ -ceq $expectedOverflowRow }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowCompactRepurposed' -Message "missing $expectedOverflowRow"
            }
        }
        if (@($historicalOverflow.Report -split "`r?`n" | Where-Object { $_ -ceq 'ROW bg2 1 slot legacy:slot-bg2-1-001-ext-1 4 1' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowCompactRepurposed' -Message 'extension location did not allocate after historical overflow'
        }
        if ($historicalOverflow.Report -notmatch '(?m)^ENDPOINT target-[ab]\.cre area-[ab] 1 0\r?$') {
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowEndpointChanged' -Message 'replayed overflow row did not retain the exact historical endpoint and empty identity'
        }
        $rngNext = [regex]::Match($historicalOverflow.Report, '(?m)^RNG_NEXT ([0-9]+) ([0-9]+)\r?$')
        if (-not $rngNext.Success -or $rngNext.Groups[1].Value -cne $rngNext.Groups[2].Value) {
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowRngDrift' -Message "historical overflow draw was skipped or consumed more than once: $($rngNext.Value)"
        }
        $historicalRegistryState = Get-RegistryState -GameRoot $historicalOverflowGame
        if ($historicalRegistryState -notmatch '(?m)^@meta\s+@origin\s+origin\s+legacy-preextension-v1\s+1\s+1\s*$' -or
            $historicalRegistryState -notmatch '(?m)^@meta\s+@fingerprint\s+fingerprint\s+[0-9]+\s+1\s+1\s*$') {
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowOriginNotPersisted' -Message 'published migration registry lacks fingerprinted legacy origin metadata'
        }
        $reinstalledOverflow = Invoke-RegistryHarness -Component 113 -Name 'historical-overflow-reinstall' -GameRoot $historicalOverflowGame
        if ($reinstalledOverflow.Report -notmatch '(?m)^STATE 1 0 legacy-preextension-v1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowReinstallOrigin' -Message 'second process did not load the marked legacy-origin registry'
        }
        $reinstallExpected = [regex]::Match($reinstalledOverflow.Report, '(?m)^EXPECTED_OVERFLOW (legacy:slot-bg2-1-[0-9]+--1) 3\r?$')
        if (-not $reinstallExpected.Success -or
            @($reinstalledOverflow.Report -split "`r?`n" | Where-Object { $_ -ceq ('ROW bg2 1 slot ' + $reinstallExpected.Groups[1].Value + ' 3 1') }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_PreserveReinstallOverflowDisabled' -Message 'registered preserve reinstall did not keep the historical overflow active at compact 3'
        }
        if ($reinstalledOverflow.Report -notmatch '(?m)^ENDPOINT 1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_PreserveReinstallOverflowEndpointLost' -Message 'registered preserve reinstall did not reconstruct the exact historical endpoint'
        }
        $reinstallRng = [regex]::Match($reinstalledOverflow.Report, '(?m)^RNG_NEXT ([0-9]+) ([0-9]+)\r?$')
        if (-not $reinstallRng.Success -or $reinstallRng.Groups[1].Value -cne $reinstallRng.Groups[2].Value) {
            Add-RegistryRedFailure -Code 'Registry_PreserveReinstallOverflowRngDrift' -Message "registered preserve reinstall RNG mismatch: $($reinstallRng.Value)"
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_HistoricalOverflowExecutable' -Message $message
    }

    $namedRawDriftGame = Join-Path $scratchRoot 'named-raw-drift-game'
    New-MinimalFakeGame -GameRoot $namedRawDriftGame
    Add-RegistryRemovalFixtures -GameRoot $namedRawDriftGame
    Set-RegistryRemovalHistory -GameRoot $namedRawDriftGame -Rows @('1 1 alpha')
    try {
        $null = Invoke-RegistryHarness -Component 110 -Name 'named-raw-drift' -GameRoot $namedRawDriftGame -ExpectSuccess $false -ExpectedErrorCode 'LEGACY_ORDINAL_DRIFT'
        if (Test-Path -LiteralPath (Join-Path $namedRawDriftGame 'override\fl#mutated.mrk') -PathType Leaf) {
            Add-RegistryRedFailure -Code 'Registry_NamedRawDriftAccepted' -Message 'mutation marker exists after expected drift failure'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_NamedRawDriftAccepted' -Message $message
    }

    $deferredStateGame = Join-Path $scratchRoot 'deferred-state-rollback-game'
    New-MinimalFakeGame -GameRoot $deferredStateGame
    $baselineRegistry = [System.Text.Encoding]::ASCII.GetBytes('registry-baseline')
    $baselineRemoved = [System.Text.Encoding]::ASCII.GetBytes("Tier Token Ident`n1 1 baseline`n")
    [System.IO.File]::WriteAllBytes((Join-Path $deferredStateGame 'override\fl#irreg.2da'), $baselineRegistry)
    [System.IO.File]::WriteAllBytes((Join-Path $deferredStateGame 'override\fl#removeditems.2da'), $baselineRemoved)
    try {
        $null = Invoke-RegistryHarness -Component 111 -Name 'deferred-state-rollback' -GameRoot $deferredStateGame -ExpectSuccess $false -ExpectedErrorCode 'INTENTIONAL_AFTER_PREPARE' -ExpectedErrorDomain 'FLIR_STATE_TEST'
        $registryAfter = [System.IO.File]::ReadAllBytes((Join-Path $deferredStateGame 'override\fl#irreg.2da'))
        $removedAfter = [System.IO.File]::ReadAllBytes((Join-Path $deferredStateGame 'override\fl#removeditems.2da'))
        if ([Convert]::ToBase64String($baselineRegistry) -cne [Convert]::ToBase64String($registryAfter)) {
            Add-RegistryRedFailure -Code 'Registry_StatePublishedBeforeRollbackBoundary' -Message 'registry bytes advanced before later failure'
        }
        if ([Convert]::ToBase64String($baselineRemoved) -cne [Convert]::ToBase64String($removedAfter)) {
            Add-RegistryRedFailure -Code 'Registry_StatePublishedBeforeRollbackBoundary' -Message 'removal-history bytes advanced before later failure'
        }
        if (Test-Path -LiteralPath (Join-Path $deferredStateGame 'override\fl#rollback-control.mrk') -PathType Leaf) {
            Add-RegistryRedFailure -Code 'Registry_StateRollbackControlFailed' -Message 'ordinary rollback control marker survived expected failure'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_DeferredStateRollbackExecutable' -Message $message
    }

    $interruptedFreshGame = Join-Path $scratchRoot 'interrupted-fresh-state-game'
    New-MinimalFakeGame -GameRoot $interruptedFreshGame
    try {
        $null = Invoke-RandomSeedHarness -Component 1 -Name 'interrupted-fresh' -GameRoot $interruptedFreshGame -ExpectSuccess $false -ExpectedErrorCode 'INTENTIONAL_AFTER_PREPARE'
        foreach ($stateName in 'fl#randomseed.2da', 'fl#randoptions.2da', 'fl#removeditems.2da', 'fl#irreg.2da') {
            if (Test-Path -LiteralPath (Join-Path $interruptedFreshGame ('override\' + $stateName)) -PathType Leaf) {
                Add-RegistryRedFailure -Code 'Registry_InterruptedFreshPublishedPartialState' -Message "$stateName survived failure before final publication"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_InterruptedFreshExecutable' -Message $message
    }

    $lateOptionsGame = Join-Path $scratchRoot 'late-options-publication-game'
    New-MinimalFakeGame -GameRoot $lateOptionsGame
    try {
        $lateOptions = Invoke-RandomSeedHarness -Component 2 -Name 'late-options-publication' -GameRoot $lateOptionsGame
        if (@($lateOptions.Report -split "`r?`n" | Where-Object { $_ -ceq 'PREPUBLISHED 0 0' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_LateOptionsPublishedEarly' -Message "missing deferred prepublication proof: $($lateOptions.Report)"
        }
        foreach ($stateName in 'fl#randomseed.2da', 'fl#randoptions.2da') {
            if (-not (Test-Path -LiteralPath (Join-Path $lateOptionsGame ('override\' + $stateName)) -PathType Leaf)) {
                Add-RegistryRedFailure -Code 'Registry_LateOptionsNotPublished' -Message "$stateName was absent after explicit final publication"
            }
        }
        $optionsText = [System.IO.File]::ReadAllText((Join-Path $lateOptionsGame 'override\fl#randoptions.2da'))
        if (@($optionsText -split "`r?`n" | Where-Object { $_ -ceq 'ModCompatYes' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_LateOptionsSnapshotStale' -Message "late ModCompatYes was not published exactly once: $optionsText"
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_LateOptionsExecutable' -Message $message
    }

    $mode2LateOptionsGame = Join-Path $scratchRoot 'mode2-late-options-publication-game'
    New-MinimalFakeGame -GameRoot $mode2LateOptionsGame
    try {
        $null = Invoke-RandomSeedHarness -Component 3 -Name 'mode2-late-options' -GameRoot $mode2LateOptionsGame
        $mode2OptionsText = [System.IO.File]::ReadAllText((Join-Path $mode2LateOptionsGame 'override\fl#randoptions.2da'))
        foreach ($optionName in 'ModCompatYes', 'weidu_action') {
            if (@($mode2OptionsText -split "`r?`n" | Where-Object { $_ -ceq $optionName }).Count -ne 1) {
                Add-RegistryRedFailure -Code 'Registry_Mode2LateOptionsMissing' -Message "$optionName was not published exactly once: $mode2OptionsText"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_Mode2LateOptionsExecutable' -Message $message
    }

    $unifiedPublishGame = Join-Path $scratchRoot 'unified-state-publication-game'
    New-MinimalFakeGame -GameRoot $unifiedPublishGame
    try {
        $unifiedPublish = Invoke-RandomSeedHarness -Component 4 -Name 'unified-state-publication' -GameRoot $unifiedPublishGame
        if (@($unifiedPublish.Report -split "`r?`n" | Where-Object { $_ -ceq 'PREPUBLISHED_COUNT 0' }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_UnifiedStatePublishedEarly' -Message "missing clean prepublication proof: $($unifiedPublish.Report)"
        }
        foreach ($stateName in 'fl#randomseed.2da', 'fl#randoptions.2da', 'fl#removeditems.2da', 'fl#irreg.2da') {
            if (-not (Test-Path -LiteralPath (Join-Path $unifiedPublishGame ('override\' + $stateName)) -PathType Leaf)) {
                Add-RegistryRedFailure -Code 'Registry_UnifiedStatePublicationIncomplete' -Message "$stateName was absent after the unified final seam"
            }
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_UnifiedStatePublicationExecutable' -Message $message
    }

    $continuedOverflowGame = Join-Path $scratchRoot 'continued-overflow-game'
    New-MinimalFakeGame -GameRoot $continuedOverflowGame
    Set-RegistryRemovalHistory -GameRoot $continuedOverflowGame -Rows @(
        '1 1 alpha',
        '1 2 beta',
        '1 3 gamma'
    )
    try {
        $continuedOverflow = Invoke-RegistryHarness -Component 112 -Name 'continued-overflow' -GameRoot $continuedOverflowGame
        $expectedHistorical = [regex]::Match($continuedOverflow.Report, '(?m)^EXPECTED_HISTORICAL (legacy:slot-bg2-1-[0-9]+--1) 3\r?$')
        $expectedNew = [regex]::Match($continuedOverflow.Report, '(?m)^EXPECTED_NEW (legacy:slot-bg2-1-[0-9]+--1) 4\r?$')
        if (-not $expectedHistorical.Success -or
            @($continuedOverflow.Report -split "`r?`n" | Where-Object { $_ -ceq ('ROW bg2 1 slot ' + $expectedHistorical.Groups[1].Value + ' 3 1') }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_ContinuedOverflowHistoricalChanged' -Message 'historical overflow row lost compact 3'
        }
        if (-not $expectedNew.Success -or
            @($continuedOverflow.Report -split "`r?`n" | Where-Object { $_ -ceq ('ROW bg2 1 slot ' + $expectedNew.Groups[1].Value + ' 4 1') }).Count -ne 1) {
            Add-RegistryRedFailure -Code 'Registry_ContinuedOverflowRestartedOrdinal' -Message "new overflow row did not continue after historical d1:`n$($continuedOverflow.Report)"
        }
        $continuedRng = [regex]::Match($continuedOverflow.Report, '(?m)^RNG_NEXT ([0-9]+) ([0-9]+)\r?$')
        if (-not $continuedRng.Success -or $continuedRng.Groups[1].Value -cne $continuedRng.Groups[2].Value) {
            Add-RegistryRedFailure -Code 'Registry_ContinuedOverflowRngDrift' -Message "continued overflow RNG mismatch: $($continuedRng.Value)"
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_ContinuedOverflowExecutable' -Message $message
    }

    $originOrderGame = Join-Path $scratchRoot 'fresh-registered-order-game'
    New-MinimalFakeGame -GameRoot $originOrderGame
    Add-RegistryRemovalFixtures -GameRoot $originOrderGame
    try {
        $freshOrder = Invoke-RegistryHarness -Component 114 -Name 'fresh-order' -GameRoot $originOrderGame
        $registeredOrder = Invoke-RegistryHarness -Component 115 -Name 'registered-order' -GameRoot $originOrderGame
        $freshOrderRows = @($freshOrder.Report -split "`r?`n" | Where-Object { $_ -match '^ORDER ' })
        $registeredOrderRows = @($registeredOrder.Report -split "`r?`n" | Where-Object { $_ -match '^ORDER ' })
        if ($freshOrder.Report -notmatch '(?m)^STATE 0 0\r?$' -or $registeredOrder.Report -notmatch '(?m)^STATE 1 0\r?$') {
            Add-RegistryRedFailure -Code 'Registry_FreshRegisteredOrderOrigin' -Message "unexpected probe states: fresh=$($freshOrder.Report) registered=$($registeredOrder.Report)"
        }
        if ($freshOrderRows.Count -ne 64 -or $registeredOrderRows.Count -ne 64 -or
            ($freshOrderRows -join "`n") -cne ($registeredOrderRows -join "`n")) {
            Add-RegistryRedFailure -Code 'Registry_FreshRegisteredRngOrderDrift' -Message 'fresh and registered base item/location decisions differ'
        }
        if (@($freshOrderRows | Where-Object {
                    $parts = $_ -split ' '
                    $parts.Count -eq 5 -and $parts[2] -cne $parts[3]
                }).Count -eq 0) {
            Add-RegistryRedFailure -Code 'Registry_FreshRegisteredOrderVacuous' -Message 'probe had no seed that distinguished item and location draw identity'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_FreshRegisteredOrderExecutable' -Message $message
    }

    $freshOriginGame = Join-Path $scratchRoot 'fresh-origin-overflow-game'
    New-MinimalFakeGame -GameRoot $freshOriginGame
    Add-RegistryRemovalFixtures -GameRoot $freshOriginGame
    Set-RegistryRemovalHistory -GameRoot $freshOriginGame -Rows @(
        '1 1 alpha',
        '1 2 beta',
        '1 3 gamma'
    )
    try {
        $freshOrigin = Invoke-RegistryHarness -Component 116 -Name 'fresh-origin' -GameRoot $freshOriginGame
        $registeredOrigin = Invoke-RegistryHarness -Component 117 -Name 'registered-fresh-origin' -GameRoot $freshOriginGame
        $freshOriginRows = @($freshOrigin.Report -split "`r?`n" | Where-Object { $_ -match '^ORIGIN_ORDER ' })
        $registeredOriginRows = @($registeredOrigin.Report -split "`r?`n" | Where-Object { $_ -match '^ORIGIN_ORDER ' })
        if ($freshOrigin.Report -notmatch '(?m)^STATE 0 0 fresh-postextension-v1\r?$' -or
            $registeredOrigin.Report -notmatch '(?m)^STATE 1 0 fresh-postextension-v1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_FreshOriginStateMismatch' -Message 'fresh-origin probe did not load and retain the expected marked origin'
        }
        if ($freshOriginRows.Count -ne 64 -or $registeredOriginRows.Count -ne 64 -or
            ($freshOriginRows -join "`n") -cne ($registeredOriginRows -join "`n")) {
            Add-RegistryRedFailure -Code 'Registry_FreshOriginExtensionOverflowRngDrift' -Message 'registered fresh origin swapped extension and overflow RNG draws'
        }
        $freshOriginRegistryRows = @($freshOrigin.Report -split "`r?`n" | Where-Object { $_ -match '^ROW ' })
        $registeredOriginRegistryRows = @($registeredOrigin.Report -split "`r?`n" | Where-Object { $_ -match '^ROW ' })
        if ($freshOriginRegistryRows.Count -eq 0 -or
            ($freshOriginRegistryRows -join "`n") -cne ($registeredOriginRegistryRows -join "`n")) {
            Add-RegistryRedFailure -Code 'Registry_FreshOriginSlotReplayMismatch' -Message 'fresh-origin probe did not persist and reconcile a stable overflow slot'
        }
        $originExtensionValues = @($freshOriginRows | ForEach-Object { ($_ -split ' ')[2] } | Sort-Object -Unique)
        $originTargetValues = @($freshOriginRows | ForEach-Object { ($_ -split ' ')[4] } | Sort-Object -Unique)
        if ($originExtensionValues.Count -lt 2 -or $originTargetValues.Count -lt 2) {
            Add-RegistryRedFailure -Code 'Registry_FreshOriginProbeVacuous' -Message 'probe did not vary both extension and overflow draw identity'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 100) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_FreshOriginExecutable' -Message $message
    }

    $mixedOriginGame = Join-Path $scratchRoot 'mixed-origin-overflow-game'
    New-MinimalFakeGame -GameRoot $mixedOriginGame
    Add-RegistryRemovalFixtures -GameRoot $mixedOriginGame
    Set-RegistryRemovalHistory -GameRoot $mixedOriginGame -Rows @(
        '1 1 alpha',
        '1 2 beta',
        '1 3 gamma'
    )
    try {
        $mixedMigration = Invoke-RegistryHarness -Component 118 -Name 'mixed-origin-migration' -GameRoot $mixedOriginGame
        $mixedReinstall = Invoke-RegistryHarness -Component 119 -Name 'mixed-origin-reinstall' -GameRoot $mixedOriginGame
        if ($mixedMigration.Report -notmatch '(?m)^MIXED_STATE 0 1 legacy-preextension-v1\r?$' -or
            $mixedReinstall.Report -notmatch '(?m)^MIXED_STATE 1 0 legacy-preextension-v1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_MixedOriginStateMismatch' -Message 'mixed legacy probes did not retain the marked migration origin'
        }
        $mixedMigrationSemantics = @($mixedMigration.Report -split "`r?`n" | Where-Object { $_ -match '^MIXED_(?:SEED|EXTENSION|OVERFLOW|NEXT) ' })
        $mixedReinstallSemantics = @($mixedReinstall.Report -split "`r?`n" | Where-Object { $_ -match '^MIXED_(?:SEED|EXTENSION|OVERFLOW|NEXT) ' })
        $mixedMigrationRows = @($mixedMigration.Report -split "`r?`n" | Where-Object { $_ -match '^ROW ' })
        $mixedReinstallRows = @($mixedReinstall.Report -split "`r?`n" | Where-Object { $_ -match '^ROW ' })
        if (($mixedMigrationSemantics -join "`n") -cne ($mixedReinstallSemantics -join "`n") -or
            ($mixedMigrationRows -join "`n") -cne ($mixedReinstallRows -join "`n")) {
            Add-RegistryRedFailure -Code 'Registry_MixedOriginPrefixDrift' -Message 'registered legacy origin promoted a post-extension overflow into its pre-extension prefix'
        }
        $mixedRebase = Invoke-RegistryHarness -Component 120 -Name 'mixed-origin-rebase' -GameRoot $mixedOriginGame
        $mixedAfterRebase = Invoke-RegistryHarness -Component 121 -Name 'mixed-origin-after-rebase' -GameRoot $mixedOriginGame
        if ($mixedRebase.Report -notmatch '(?m)^MIXED_STATE 1 0 fresh-postextension-v1\r?$' -or
            $mixedAfterRebase.Report -notmatch '(?m)^MIXED_STATE 1 0 fresh-postextension-v1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_NoPreserveOriginNotRebased' -Message 'Preserve=n did not rebase legacy registry semantics to the fresh post-extension origin'
        }
        $mixedRebaseSemantics = @($mixedRebase.Report -split "`r?`n" | Where-Object { $_ -match '^MIXED_(?:SEED|EXTENSION|OVERFLOW|NEXT) ' })
        $mixedAfterRebaseSemantics = @($mixedAfterRebase.Report -split "`r?`n" | Where-Object { $_ -match '^MIXED_(?:SEED|EXTENSION|OVERFLOW|NEXT) ' })
        if (($mixedRebaseSemantics -join "`n") -cne ($mixedAfterRebaseSemantics -join "`n")) {
            Add-RegistryRedFailure -Code 'Registry_NoPreserveRebaseRngDrift' -Message 'preserve after rebase did not retain fresh extension/overflow ordering'
        }
        $mixedRebaseRows = @($mixedRebase.Report -split "`r?`n" | Where-Object { $_ -match '^ROW ' })
        $mixedAfterRebaseRows = @($mixedAfterRebase.Report -split "`r?`n" | Where-Object { $_ -match '^ROW ' })
        if ($mixedRebaseRows.Count -eq 0 -or ($mixedRebaseRows -join "`n") -cne ($mixedAfterRebaseRows -join "`n")) {
            Add-RegistryRedFailure -Code 'Registry_NoPreserveRebaseSlotDrift' -Message 'preserve after rebase did not reconcile the same stable slot rows and compacts'
        }
        if ((Get-RegistryState -GameRoot $mixedOriginGame) -notmatch '(?m)^@meta\s+@origin\s+origin\s+fresh-postextension-v1\s+1\s+1\s*$') {
            Add-RegistryRedFailure -Code 'Registry_NoPreserveOriginNotPersisted' -Message 'rebased fresh origin was not persisted in the fingerprinted registry'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 120) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_MixedOriginExecutable' -Message $message
    }

    $authoredReplayGame = Join-Path $scratchRoot 'authored-replay-identity-game'
    New-MinimalFakeGame -GameRoot $authoredReplayGame
    try {
        $authoredReplay = Invoke-RegistryHarness -Component 122 -Name 'authored-replay-identity' -GameRoot $authoredReplayGame
        if ($authoredReplay.Report -notmatch '(?m)^ELIGIBLE 1\r?$' -or
            $authoredReplay.Report -notmatch '(?m)^GENERATED 1\r?$') {
            Add-RegistryRedFailure -Code 'Registry_AuthoredReplayIdentityExcluded' -Message 'an authored location with a distinct identity was mistaken for the replayed empty-identity overflow row'
        }
    }
    catch {
        $message = (($_.Exception.Message -split "`r?`n") | Select-Object -First 80) -join ' | '
        Add-RegistryRedFailure -Code 'Registry_AuthoredReplayIdentityExecutable' -Message $message
    }

    if ($redFailures.Count -ne 0) {
        throw 'Registry RED-first remediation assertions failed.'
    }

    $freshGame = Join-Path $scratchRoot 'fresh-game'
    New-MinimalFakeGame -GameRoot $freshGame
    $fresh = Invoke-RegistryHarness -Component 0 -Name 'fresh' -GameRoot $freshGame
    $freshState = Get-RegistryState -GameRoot $freshGame
    if ($fresh.Report -notmatch 'schema=flir-registry-v1 backend=legacy-bcs-v1 catalog=\d+' -or
        $fresh.Report -match 'catalog=(0|123456)\b') {
        throw "Registry report did not include a deterministic nonzero catalog fingerprint.`n$($fresh.Report)"
    }
    foreach ($line in @(
        'ROW bg2 1 slot core:slot-a 1 1',
        'ROW bg2 1 slot core:slot-b 2 1',
        'ROW bg2 1 unit core:unit-a 1 1',
        'ROW bg2 1 unit core:unit-b 2 1',
        'ROW bg2 1 unit core:unit-extra-x0 3 1',
        'ROW bg2 1 unit core:unit-extra-y0 4 1'
    )) {
        Assert-ContainsLine -Text $fresh.Report -Line $line
    }
    if ($freshState -match '(?i)item.*location|location.*item|target-a|area-a|raw999') {
        throw "Registry state leaked joined item/location details.`n$freshState"
    }

    $freshStateBeforeReinstall = Get-RegistryState -GameRoot $freshGame
    Invoke-RegistryReinstall -GameRoot $freshGame -ReportName 'reinstall'
    if ((Get-RegistryState -GameRoot $freshGame) -notmatch '@meta @fingerprint fingerprint \d+ 1 1') {
        throw 'Registry reinstall did not preserve and republish a fingerprinted state file.'
    }
    if ($freshStateBeforeReinstall -notmatch '@meta @fingerprint fingerprint \d+ 1 1') {
        throw 'Fresh registry state was not fingerprinted before reinstall.'
    }

    $uninstallGame = Join-Path $scratchRoot 'uninstall-game'
    New-MinimalFakeGame -GameRoot $uninstallGame
    $null = Invoke-RegistryHarness -Component 0 -Name 'uninstall-seed' -GameRoot $uninstallGame
    Invoke-RegistryUninstallOnly -GameRoot $uninstallGame
    if (-not (Test-Path -LiteralPath (Join-Path $uninstallGame 'override\fl#irreg.2da') -PathType Leaf)) {
        throw 'Registry state file disappeared during uninstall-only preservation proof.'
    }

    $copyProbeGame = Join-Path $scratchRoot 'copy-probe-game'
    New-MinimalFakeGame -GameRoot $copyProbeGame
    $null = Invoke-RegistryHarness -Component 50 -Name 'copy-plus-fresh' -GameRoot $copyProbeGame
    Invoke-RegistryUninstallOnly -GameRoot $copyProbeGame -Component 50
    if (-not (Test-Path -LiteralPath (Join-Path $copyProbeGame 'override\fl#probe.2da') -PathType Leaf)) {
        throw 'COPY + probe file disappeared during uninstall-only proof.'
    }
    $null = Invoke-RegistryHarness -Component 51 -Name 'copy-existing-plus' -GameRoot $copyProbeGame
    [System.IO.File]::WriteAllText((Join-Path $copyProbeGame 'override\fl#probe.2da'), 'mutated', [System.Text.Encoding]::ASCII)
    Invoke-RegistryReinstall -GameRoot $copyProbeGame -ReportName 'copy-existing-plus-reinstall' -Component 51
    if ([System.IO.File]::ReadAllText((Join-Path $copyProbeGame 'override\fl#probe.2da')) -ne 'mutated') {
        throw 'COPY_EXISTING + did not preserve current state through one-process reinstall.'
    }

    $ordinaryProbeGame = Join-Path $scratchRoot 'ordinary-probe-game'
    New-MinimalFakeGame -GameRoot $ordinaryProbeGame
    $null = Invoke-RegistryHarness -Component 52 -Name 'ordinary-copy-fresh' -GameRoot $ordinaryProbeGame
    Invoke-RegistryUninstallOnly -GameRoot $ordinaryProbeGame -Component 52
    if (Test-Path -LiteralPath (Join-Path $ordinaryProbeGame 'override\fl#ordinary.2da') -PathType Leaf) {
        throw 'Ordinary COPY unexpectedly preserved its output during uninstall-only proof.'
    }
    [System.IO.File]::WriteAllText((Join-Path $ordinaryProbeGame 'override\fl#ordinary.2da'), 'base', [System.Text.Encoding]::ASCII)
    $null = Invoke-RegistryHarness -Component 53 -Name 'ordinary-copy-existing' -GameRoot $ordinaryProbeGame
    [System.IO.File]::WriteAllText((Join-Path $ordinaryProbeGame 'override\fl#ordinary.2da'), 'mutated', [System.Text.Encoding]::ASCII)
    Invoke-RegistryReinstall -GameRoot $ordinaryProbeGame -ReportName 'ordinary-copy-existing-reinstall' -Component 53
    if ([System.IO.File]::ReadAllText((Join-Path $ordinaryProbeGame 'override\fl#ordinary.2da')) -ne 'installed') {
        throw 'Ordinary COPY_EXISTING negative control did not restore the backed-up state.'
    }

    $monotonicGame = Join-Path $scratchRoot 'monotonic-game'
    New-MinimalFakeGame -GameRoot $monotonicGame
    $null = Invoke-RegistryHarness -Component 40 -Name 'seed-monotonic' -GameRoot $monotonicGame
    $monotonic = Invoke-RegistryHarness -Component 1 -Name 'monotonic' -GameRoot $monotonicGame
    Assert-ContainsLine -Text $monotonic.Report -Line 'ROW bg2 1 unit core:new-unit 8 1'
    Assert-ContainsLine -Text $monotonic.Report -Line 'ROW bg2 1 slot core:new-slot 10 1'

    $legacyGame = Join-Path $scratchRoot 'legacy-game'
    New-MinimalFakeGame -GameRoot $legacyGame
    $legacy = Invoke-RegistryHarness -Component 2 -Name 'legacy' -GameRoot $legacyGame
    Assert-ContainsLine -Text $legacy.Report -Line 'ROW bg2 1 slot legacy:slot-bg2-1-raw999-a-1 1 1'
    Assert-ContainsLine -Text $legacy.Report -Line 'ROW bg2 1 slot legacy:slot-bg2-1-raw005-b-1 2 1'
    Assert-ContainsLine -Text $legacy.Report -Line 'ROW bg2 1 slot core:slot-c 3 1'
    if (@($legacy.Report -split "`r?`n" | Where-Object { $_ -match '^ROW .* slot .* (raw999|raw005) [01]$' }).Count -ne 0) {
        throw "Legacy raw Location column leaked into compact slot value.`n$($legacy.Report)"
    }

    $schemaGame = Join-Path $scratchRoot 'schema-game'
    New-MinimalFakeGame -GameRoot $schemaGame
    $null = Invoke-RegistryHarness -Component 41 -Name 'seed-schema' -GameRoot $schemaGame
    $null = Invoke-RegistryHarness -Component 3 -Name 'schema' -GameRoot $schemaGame -ExpectSuccess $false -ExpectedErrorCode 'UNSUPPORTED_SCHEMA'

    $backendGame = Join-Path $scratchRoot 'backend-game'
    New-MinimalFakeGame -GameRoot $backendGame
    $null = Invoke-RegistryHarness -Component 42 -Name 'seed-backend' -GameRoot $backendGame
    $null = Invoke-RegistryHarness -Component 4 -Name 'backend' -GameRoot $backendGame -ExpectSuccess $false -ExpectedErrorCode 'BACKEND_SWITCH'

    $fingerprintGame = Join-Path $scratchRoot 'fingerprint-game'
    New-MinimalFakeGame -GameRoot $fingerprintGame
    $null = Invoke-RegistryHarness -Component 0 -Name 'seed-fingerprint' -GameRoot $fingerprintGame
    $tampered = (Get-RegistryState -GameRoot $fingerprintGame).Replace('core:unit-a', 'core:unit-z')
    Set-RegistryState -GameRoot $fingerprintGame -Text $tampered
    $null = Invoke-RegistryHarness -Component 5 -Name 'fingerprint' -GameRoot $fingerprintGame -ExpectSuccess $false -ExpectedErrorCode 'FILE_FINGERPRINT'

    $collisionGame = Join-Path $scratchRoot 'collision-game'
    New-MinimalFakeGame -GameRoot $collisionGame
    $null = Invoke-RegistryHarness -Component 43 -Name 'seed-collision' -GameRoot $collisionGame
    $null = Invoke-RegistryHarness -Component 6 -Name 'collision' -GameRoot $collisionGame -ExpectSuccess $false -ExpectedErrorCode 'COMPACT_COLLISION'

    $tombstoneGame = Join-Path $scratchRoot 'tombstone-game'
    New-MinimalFakeGame -GameRoot $tombstoneGame
    $null = Invoke-RegistryHarness -Component 44 -Name 'seed-tombstone' -GameRoot $tombstoneGame
    $null = Invoke-RegistryHarness -Component 7 -Name 'tombstone' -GameRoot $tombstoneGame -ExpectSuccess $false -ExpectedErrorCode 'TOMBSTONE_REUSE'

    $driftGame = Join-Path $scratchRoot 'drift-game'
    New-MinimalFakeGame -GameRoot $driftGame
    $null = Invoke-RegistryHarness -Component 45 -Name 'seed-drift' -GameRoot $driftGame
    $null = Invoke-RegistryHarness -Component 8 -Name 'drift' -GameRoot $driftGame -ExpectSuccess $false -ExpectedErrorCode 'LEGACY_ORDINAL_DRIFT'

    $mode2Game = Join-Path $scratchRoot 'mode2-game'
    New-MinimalFakeGame -GameRoot $mode2Game
    $mode2 = Invoke-RegistryHarness -Component 20 -Name 'mode2' -GameRoot $mode2Game
    if (Test-Path -LiteralPath (Join-Path $mode2Game 'override\fl#irreg.2da') -PathType Leaf) {
        throw 'Mode 2 harness published registry state.'
    }
    if ($mode2.Report -notmatch 'schema=flir-registry-v1 backend=legacy-bcs-v1 catalog=\d+') {
        throw "Mode 2 harness report did not include schema/backend/catalog summary.`n$($mode2.Report)"
    }

    Write-Output 'PASS Registry_SyntheticGameHasValidKeyTlk'
    Write-Output 'PASS Registry_FreshMonotonicCanonicalRows'
    Write-Output 'PASS Registry_ReinstallPreservesCurrentMutation'
    Write-Output 'PASS Registry_UninstallOnlyPreservesState'
    Write-Output 'PASS Registry_CopyPlusAndOrdinaryCopyBehavior'
    Write-Output 'PASS Registry_TombstonesAllocateAfterHistoricalMax'
    Write-Output 'PASS Registry_LegacySnapshotUsesFilteredOrdinalNotRawLocation'
    Write-Output 'PASS Registry_ExtrasAreDistinctUnits'
    Write-Output 'PASS Registry_FatalCompatibilityGuards'
    Write-Output 'PASS Registry_Mode2BoundaryProtected'
}
finally {
    if (Test-Path -LiteralPath $scratchRoot) {
        [System.IO.Directory]::Delete($scratchRoot, $true)
    }
    if (Test-Path -LiteralPath $scratchRoot) {
        throw 'Registry scratch cleanup did not complete.'
    }
}
