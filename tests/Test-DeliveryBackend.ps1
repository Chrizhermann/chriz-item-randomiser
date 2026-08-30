[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$deliveryManifestPath = Join-Path $repositoryRoot 'lib\delivery_manifest.tpa'
$harnessPath = Join-Path $PSScriptRoot 'weidu\delivery-backend-harness.tp2'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath("C:\Games\Baldur's Gate II Enhanced Edition modded").TrimEnd('\', '/')

if (-not (Test-Path -LiteralPath $deliveryManifestPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED DeliveryBackend_ProductionApiMissing lib/delivery_manifest.tpa'
    exit 1
}

foreach ($requiredPath in @($harnessPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required delivery-backend test input is missing: $requiredPath"
    }
}

$deliveryManifestSource = [System.IO.File]::ReadAllText($deliveryManifestPath)
if ($deliveryManifestSource -notmatch 'DEFINE_ACTION_MACRO\s+flir_delivery_select_backend\b') {
    throw "The production delivery manifest library does not expose 'flir_delivery_select_backend'."
}
if ($deliveryManifestSource -notmatch '\bflir_delivery_probe_root\b') {
    throw "The delivery-backend selector does not expose the configurable 'flir_delivery_probe_root' seam."
}
if ($deliveryManifestSource -notmatch 'FILE_EXISTS_IN_GAME\s+["~]fl#irreg\.2da["~]' -or
    $deliveryManifestSource -notmatch 'COPY_EXISTING\s+-\s+["~]fl#irreg\.2da["~]') {
    throw 'The production selector cannot preserve a registry stored in the effective game resource set.'
}
if ($deliveryManifestSource -match 'EEex_Action_QueueResponseStringOnAIBase|EEex_Sprite_GetInPortrait') {
    throw 'The production capability gate still requires an obsolete queue API or an unused portrait helper.'
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
        throw 'Delivery-backend tests accept only local drive-letter paths.'
    }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($full -notmatch '^[A-Za-z]:\\' -or $full.IndexOf('~') -ge 0) {
        throw 'Delivery-backend tests accept only long local drive-letter paths.'
    }
    $full
}

function Assert-OutsideForbiddenLiveRoot {
    param([Parameter(Mandatory = $true)][string] $Path)

    $full = ConvertTo-LocalFullPath -Path $Path
    if ($full.Equals($script:forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($script:forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to use a delivery-backend test path under the forbidden live game root.'
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

$scratchRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedTempRoot ('bgee-itemrandomiser-delivery-backend-' + [guid]::NewGuid().ToString('N'))))
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated delivery-backend scratch directory escaped its disposable parent.'
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$requiredApiSources = [ordered]@{
    'EEex_Action.lua' = @(
        'function EEex_Action_ParseResponseString(responseString) end',
        'function EEex_Action_QueueScriptFileResponseOnAIBase(response, actor) end'
    )
    'EEex_Area.lua' = @(
        'function EEex_Area_GetVisible() end'
    )
    'EEex_GameObject.lua' = @(
        'function EEex_GameObject_CastUserType(object) end',
        'function EEex_GameObject_Get(objectID) end',
        'function EEex_GameObject_IsSprite(object, allowDead) end'
    )
    'EEex_GameState.lua' = @(
        'function EEex_GameState_AddDestroyedListener(listener) end',
        'function EEex_GameState_GetGlobalInt(variableName) end',
        'function EEex_GameState_SetGlobalInt(variableName, value) end'
    )
    'EEex_Menu.lua' = @(
        'function EEex_Menu_Find(menuName, panel, state) end',
        'function EEex_Menu_GetItemFunction(funcRef) end',
        'function EEex_Menu_SetItemFunction(funcRef, func) end',
        'function EEex_Menu_LoadFile(resref) end',
        'function EEex_Menu_AddAfterMainFileLoadedListener(listener) end'
    )
    'EEex_Resource.lua' = @(
        'function EEex_Resource_Fetch(resref, extension) end'
    )
    'EEex_Sprite.lua' = @(
        'function EEex_Sprite_AddLoadedListener(listener) end'
    )
    'EEex_Trigger.lua' = @(
        'function EEex_Trigger_EvalConditionalStringAsAIBase(condition, actor) end'
    )
    'EEex_Utility.lua' = @(
        'function EEex_Utility_IterateCPtrList(list, callback) end'
    )
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string] $Text
    )

    [System.IO.File]::WriteAllText($Path, $Text, $script:utf8NoBom)
}

function New-DeliveryBackendFixture {
    param(
        [Parameter(Mandatory = $true)][string] $ProbeRoot,
        [ValidateSet('Complete', 'CompleteCRLF', 'MissingBootstrap', 'MissingApiFile', 'MissingSymbol', 'MissingSecondarySymbol', 'CommentedSymbol', 'BlockCommentedSymbol', 'CommentedIni', 'InactiveIni', 'DuplicateActiveIni', 'NearMissIni')]
        [string] $CapabilityMode,
        [string[]] $BackendRows = @()
    )

    $override = Join-Path $ProbeRoot 'override'
    $null = New-Item -ItemType Directory -Path $override
    $lineEnding = if ($CapabilityMode -eq 'CompleteCRLF') { "`r`n" } else { "`n" }

    if ($CapabilityMode -ne 'MissingBootstrap') {
        Write-Utf8NoBom -Path (Join-Path $override 'M___EEex.lua') -Text ('if not EEex_Active then return end' + $lineEnding)
    }

    foreach ($entry in $script:requiredApiSources.GetEnumerator()) {
        if ($CapabilityMode -eq 'MissingApiFile' -and $entry.Key -eq 'EEex_GameState.lua') {
            continue
        }

        [string[]] $sourceLines = $entry.Value
        if ($entry.Key -eq 'EEex_Action.lua') {
            if ($CapabilityMode -eq 'MissingSymbol') {
                $sourceLines = @('function EEex_Action_UnrelatedSyntheticApi() end')
            }
            elseif ($CapabilityMode -eq 'CommentedSymbol') {
                $sourceLines = @('-- function EEex_Action_QueueScriptFileResponseOnAIBase(response, actor) end')
            }
            elseif ($CapabilityMode -eq 'BlockCommentedSymbol') {
                $sourceLines = @(
                    '--[[',
                    'function EEex_Action_QueueScriptFileResponseOnAIBase(response, actor) end',
                    ']]'
                )
            }
        }
        elseif ($entry.Key -eq 'EEex_GameState.lua' -and $CapabilityMode -eq 'MissingSecondarySymbol') {
            $sourceLines = @(
                'function EEex_GameState_AddDestroyedListener(listener) end',
                'function EEex_GameState_GetGlobalInt(variableName) end'
            )
        }
        Write-Utf8NoBom -Path (Join-Path $override $entry.Key) -Text (($sourceLines -join $lineEnding) + $lineEnding)
    }

    $iniText = switch ($CapabilityMode) {
        'CommentedIni' { ';LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL' + $lineEnding }
        'InactiveIni' { '; LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL' + $lineEnding + 'LuaPatchMode=INTERNAL' + $lineEnding }
        'DuplicateActiveIni' { 'LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL' + $lineEnding + 'LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL' + $lineEnding }
        'NearMissIni' { 'NotLuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL' + $lineEnding + 'LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL_SUFFIX' + $lineEnding }
        default { 'LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL' + $lineEnding }
    }
    Write-Utf8NoBom -Path (Join-Path $ProbeRoot 'InfinityLoader.ini') -Text $iniText

    if ($BackendRows.Count -gt 0) {
        $registryLines = [System.Collections.Generic.List[string]]::new()
        $registryLines.Add('2DA V1.0')
        $registryLines.Add('0')
        $registryLines.Add('CAMPAIGN TIER KIND STABLE_ID COMPACT ENABLED')
        foreach ($backend in $BackendRows) {
            $registryLines.Add("@meta @backend backend $backend 1 1")
        }
        Write-Utf8NoBom -Path (Join-Path $override 'fl#irreg.2da') -Text (($registryLines -join "`n") + "`n")
    }
}

function Invoke-DeliveryBackendCase {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $CapabilityMode,
        [string[]] $BackendRows = @(),
        [string] $ExpectedBackend,
        [string] $ExpectedErrorCode,
        [bool] $IsEeGame = $true
    )

    $runDirectory = Join-Path $script:scratchRoot $Name
    $probeRoot = Join-Path $runDirectory 'probe-root'
    $null = New-Item -ItemType Directory -Path $runDirectory
    [System.IO.File]::WriteAllBytes((Join-Path $runDirectory '...blank'), @())
    New-DeliveryBackendFixture -ProbeRoot $probeRoot -CapabilityMode $CapabilityMode -BackendRows $BackendRows
    $reportPath = Join-Path $runDirectory 'backend-report.txt'

    $arguments = @(
        $script:harnessPath,
        '--nogame',
        '--force-install-list', '0',
        '--args', $script:deliveryManifestPath,
        '--args', $probeRoot,
        '--args', $reportPath,
        '--args', $(if ($IsEeGame) { '1' } else { '0' }),
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
    $joinedOutput = $weiduOutput -join "`n"

    if (-not [string]::IsNullOrWhiteSpace($ExpectedBackend)) {
        if ($weiduExitCode -ne 0 -or $joinedOutput -match 'NOT INSTALLED DUE TO ERRORS') {
            throw "Delivery-backend case '$Name' failed unexpectedly.`n$joinedOutput"
        }
        if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
            throw "Delivery-backend case '$Name' did not publish its report."
        }
        $actual = ([System.IO.File]::ReadAllText($reportPath)).Trim()
        if ($actual -cne "BACKEND $ExpectedBackend") {
            throw "Delivery-backend case '$Name' selected '$actual' instead of 'BACKEND $ExpectedBackend'."
        }
        return
    }

    if ($weiduExitCode -eq 0 -and $joinedOutput -notmatch 'NOT INSTALLED DUE TO ERRORS') {
        throw "Delivery-backend case '$Name' succeeded but should have failed."
    }
    if ($joinedOutput -notmatch 'FLIR_DELIVERY_ERR\b') {
        throw "Delivery-backend case '$Name' failed without a stable FLIR_DELIVERY_ERR marker.`n$joinedOutput"
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedErrorCode) -and
        $joinedOutput -notmatch ('FLIR_DELIVERY_ERR\s+' + [regex]::Escape($ExpectedErrorCode) + '\b')) {
        throw "Delivery-backend case '$Name' did not report $ExpectedErrorCode.`n$joinedOutput"
    }
    if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
        throw "Delivery-backend case '$Name' published a report after selection failed."
    }
}

try {
    $null = New-Item -ItemType Directory -Path $scratchRoot

    Invoke-DeliveryBackendCase -Name 'fresh-complete' -CapabilityMode Complete -ExpectedBackend 'eeex-manifest-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-complete-crlf' -CapabilityMode CompleteCRLF -ExpectedBackend 'eeex-manifest-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-classic-with-stale-eeex-layout' -CapabilityMode Complete -IsEeGame $false -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-missing-bootstrap' -CapabilityMode MissingBootstrap -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-missing-api-file' -CapabilityMode MissingApiFile -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-missing-required-symbol' -CapabilityMode MissingSymbol -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-missing-secondary-symbol' -CapabilityMode MissingSecondarySymbol -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-commented-symbol-only' -CapabilityMode CommentedSymbol -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-block-commented-symbol-only' -CapabilityMode BlockCommentedSymbol -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-commented-ini-only' -CapabilityMode CommentedIni -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-inactive-ini' -CapabilityMode InactiveIni -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-duplicate-active-ini' -CapabilityMode DuplicateActiveIni -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'fresh-near-miss-ini' -CapabilityMode NearMissIni -ExpectedBackend 'legacy-bcs-v1'

    Invoke-DeliveryBackendCase -Name 'preserved-legacy-capable' -CapabilityMode Complete -BackendRows @('legacy-bcs-v1') -ExpectedBackend 'legacy-bcs-v1'
    Invoke-DeliveryBackendCase -Name 'preserved-eeex-capable' -CapabilityMode Complete -BackendRows @('eeex-manifest-v1') -ExpectedBackend 'eeex-manifest-v1'
    Invoke-DeliveryBackendCase -Name 'preserved-eeex-capability-loss' -CapabilityMode MissingBootstrap -BackendRows @('eeex-manifest-v1') -ExpectedErrorCode 'CAPABILITY_LOSS'
    Invoke-DeliveryBackendCase -Name 'preserved-unknown-backend' -CapabilityMode Complete -BackendRows @('future-backend-v9')
    Invoke-DeliveryBackendCase -Name 'preserved-both-backends' -CapabilityMode Complete -BackendRows @('legacy-bcs-v1', 'eeex-manifest-v1')

    Write-Output 'Delivery backend tests passed (18 cases).'
}
finally {
    if (Test-Path -LiteralPath $scratchRoot -PathType Container) {
        $verifiedScratch = [System.IO.Path]::GetFullPath($scratchRoot)
        if (-not $verifiedScratch.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
            [System.IO.Path]::GetFileName($verifiedScratch) -notmatch '^bgee-itemrandomiser-delivery-backend-[0-9a-f]{32}$') {
            throw 'Refusing to clean an unverified delivery-backend scratch directory.'
        }
        Remove-Item -LiteralPath $verifiedScratch -Recurse -Force
    }
}
