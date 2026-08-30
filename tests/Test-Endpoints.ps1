[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$endpointsPath = Join-Path $repositoryRoot 'lib\endpoints.tpa'
$harnessPath = Join-Path $PSScriptRoot 'weidu\endpoints-harness.tp2'
$bg1Path = Join-Path $repositoryRoot 'lists\endpoints\base\bg1.2da'
$bg2Path = Join-Path $repositoryRoot 'lists\endpoints\base\bg2.2da'
$fallbackPath = Join-Path $repositoryRoot 'lists\endpoints\fallbacks.2da'
$extensionFixturePath = Join-Path $PSScriptRoot 'fixtures\endpoints\extensions.2da'
$invalidHeaderFixturePath = Join-Path $PSScriptRoot 'fixtures\endpoints\extensions-invalid-header.2da'
$mutationFixturePath = Join-Path $PSScriptRoot 'fixtures\endpoints\extensions-mutations.2da'
$relocationBg1LocationPath = Join-Path $PSScriptRoot 'fixtures\endpoints\relocation-location-bg1.2da'
$relocationBg2LocationPath = Join-Path $PSScriptRoot 'fixtures\endpoints\relocation-location-bg2.2da'
$relocationBg1EndpointPath = Join-Path $PSScriptRoot 'fixtures\endpoints\relocation-endpoint-bg1.2da'
$relocationBg2EndpointPath = Join-Path $PSScriptRoot 'fixtures\endpoints\relocation-endpoint-bg2.2da'
$relocationFallbackPath = Join-Path $PSScriptRoot 'fixtures\endpoints\relocation-fallbacks.2da'
$normalizedBg2LocationPath = Join-Path $PSScriptRoot 'fixtures\endpoints\normalized-location-bg2.2da'
$normalizedBg2EndpointPath = Join-Path $PSScriptRoot 'fixtures\endpoints\normalized-endpoint-bg2.2da'
$normalizedFallbackPath = Join-Path $PSScriptRoot 'fixtures\endpoints\normalized-fallbacks.2da'
$defaultExtensionPath = Join-Path $repositoryRoot 'lists\endpoints\extensions.2da'
$bg1LocationPath = Join-Path $repositoryRoot 'lists\locations\base\bg1.2da'
$bg2LocationPath = Join-Path $repositoryRoot 'lists\locations\base\bg2.2da'
$sslCompilerPath = Join-Path $repositoryRoot 'ssl\ssl.exe'
$sslTemplatePath = Join-Path $repositoryRoot 'ssl\fltier.ssl'
$sslLibraryPath = Join-Path $repositoryRoot 'ssl\library.slb'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath('C:\Games\Baldur''s Gate II Enhanced Edition modded').TrimEnd('\', '/')

if (-not (Test-Path -LiteralPath $endpointsPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED Endpoints_ProductionLibraryMissing lib/endpoints.tpa'
    exit 1
}

foreach ($requiredPath in @($harnessPath, $bg1Path, $bg2Path, $fallbackPath, $defaultExtensionPath, $extensionFixturePath, $invalidHeaderFixturePath, $mutationFixturePath, $relocationBg1LocationPath, $relocationBg2LocationPath, $relocationBg1EndpointPath, $relocationBg2EndpointPath, $relocationFallbackPath, $normalizedBg2LocationPath, $normalizedBg2EndpointPath, $normalizedFallbackPath, $bg1LocationPath, $bg2LocationPath, $sslCompilerPath, $sslTemplatePath, $sslLibraryPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required endpoint test input is missing: $requiredPath"
    }
}

$fixesPath = Join-Path $repositoryRoot 'lib\fixes.tpa'
$sslPath = Join-Path $repositoryRoot 'lib\ssl.tpa'
$removalPlanPath = Join-Path $repositoryRoot 'lib\removal_plan.tpa'
$registryPath = Join-Path $repositoryRoot 'lib\registry.tpa'
$installerPath = Join-Path $repositoryRoot 'randomiser.tp2'
$source = [System.IO.File]::ReadAllText($endpointsPath)
$fixesSource = [System.IO.File]::ReadAllText($fixesPath)
$sslSource = [System.IO.File]::ReadAllText($sslPath)
$removalPlanSource = [System.IO.File]::ReadAllText($removalPlanPath)
$registrySource = [System.IO.File]::ReadAllText($registryPath)
$installerSource = [System.IO.File]::ReadAllText($installerPath)

foreach ($symbol in @(
    'flir_endpoints_reset',
    'flir_endpoints_add_endpoint',
    'flir_endpoints_add_slot',
    'flir_endpoints_generated_slot_identity',
    'flir_endpoints_rehydrate_generated_slots',
    'flir_endpoints_load_declarations',
    'flir_endpoints_apply',
    'flir_endpoints_apply_extension_hook',
    'flir_endpoints_rebuild_candidates',
    'flir_endpoints_build_plan',
    'flir_endpoints_validate',
    'flir_endpoints_validate_planned_units',
    'flir_endpoints_assert_plan_ready',
    'flir_endpoints_allocate_rounds',
    'flir_endpoints_claim_slots',
    'flir_endpoints_register_registry_slots',
    'flir_endpoints_finalize_values',
    'flir_endpoints_register_catalog_rows',
    'flir_endpoints_build_ssl_scope'
)) {
    if ($source -notmatch ('DEFINE_ACTION_MACRO\s+' + [regex]::Escape($symbol))) {
        throw "Endpoint library does not expose '$symbol'."
    }
}
if ($fixesSource -match '\bLAM\s+flir_endpoints_build_plan\b' -or
    $fixesSource -notmatch '\bLAM\s+flir_endpoints_assert_plan_ready\b') {
    throw 'Post-removal fixes must assert the prevalidated endpoint plan instead of rebuilding it.'
}
foreach ($registrySymbol in @('flir_registry_reconcile_mappings', 'flir_registry_finalize_catalog')) {
    if ($registrySource -notmatch ('DEFINE_ACTION_MACRO\s+' + [regex]::Escape($registrySymbol))) {
        throw "Registry does not expose the endpoint integration seam '$registrySymbol'."
    }
}
if ($source -notmatch 'DEFINE_PATCH_MACRO\s+flir_endpoints_rewrite_sparse_rolls') {
    throw "Endpoint library does not expose 'flir_endpoints_rewrite_sparse_rolls'."
}
if ($source -match '(?i)item[_-]?location|location[_-]?item|joined_assignment|assignment_map') {
    throw 'Endpoint source contains a joined assignment/reporting smell.'
}
if ($source -notmatch 'randomiser/lists/endpoints/extensions\.2da') {
    throw 'Endpoint extensions have no production-reachable default fragment path.'
}
if ($source -notmatch '(?s)flir_endpoint_extension_phase\s+~ADD~.*?flir_endpoints_apply_extension_hook.*?flir_endpoints_rebuild_candidates.*?flir_endpoints_rehydrate_generated_slots.*?flir_endpoint_extension_phase\s+~MUTATE~.*?flir_endpoints_apply_extension_hook.*?flir_endpoints_rebuild_candidates') {
    throw 'Production declaration loading does not add extension rows before generated-slot rehydration and mutate them afterward.'
}
if ($fixesSource -notmatch '(?s)flir_delivery_backend.*?eeex-manifest-v1.*?flir_endpoints_assert_plan_ready') {
    throw 'fixes.tpa does not isolate the prevalidated explicit capacity plan to the EEex backend.'
}
if ($sslSource -notmatch 'flir_endpoints_build_ssl_scope' -or
    $source -notmatch 'flir_endpoint_scope_list' -or
    $source -notmatch 'flir_endpoint_scope_max') {
    throw 'ssl.tpa does not consume the explicit sparse active slot set for EEex.'
}
if ($removalPlanSource -notmatch '(?s)flir_endpoints_available.*?flir_endpoints_validate_planned_units') {
    throw 'The read-only removal plan does not validate endpoint capacity before mutation.'
}
foreach ($component in @(1100, 1200)) {
    $componentPattern = '(?s)DESIGNATED\s+' + $component + '\b.*?INCLUDE\s+"randomiser/lib/arrays\.tpa".*?INCLUDE\s+"randomiser/lib/endpoints\.tpa".*?flir_removal_plan_build.*?INCLUDE\s+"randomiser/lib/delete\.tpa"'
    if ($installerSource -notmatch $componentPattern) {
        throw "Mode 1 component $component does not load endpoint validation before removal planning and deletion."
    }
    $integrationPattern = '(?s)DESIGNATED\s+' + $component + '\b.*?flir_registry_register_applied_units.*?eeex-manifest-v1.*?flir_endpoints_register_registry_slots.*?flir_registry_reconcile_mappings.*?flir_endpoints_claim_slots.*?flir_endpoints_register_catalog_rows.*?flir_manifest_prepare_catalog.*?flir_registry_finalize_catalog.*?flir_registry_write_state.*?flir_delivery_publish_manifest.*?END\s+ELSE\s+ACTION_IF.*?legacy-bcs-v1.*?BEGIN.*?flir_registry_capture_final_slots.*?flir_registry_reconcile.*?flir_registry_write_state'
    if ($installerSource -notmatch $integrationPattern) {
        throw "Mode 1 component $component does not reconcile endpoints, registry compacts, catalog rows, and fingerprints in order."
    }
}

function Read-EndpointTable {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string[]] $ExpectedColumns
    )
    $lines = @([System.IO.File]::ReadAllLines($Path) | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and -not $_.TrimStart().StartsWith('//')
    })
    if ($lines.Count -lt 2 -or $lines[0].Trim() -ne '2DA V1.0' -or $lines[1].Trim() -ne '0') {
        throw "Endpoint table '$Path' has an invalid 2DA prologue."
    }
    $header = @($lines[2].Trim() -split '\s+')
    if (($header -join '|') -cne ($ExpectedColumns -join '|')) {
        throw "Endpoint table '$Path' has unexpected columns: $($header -join ',')."
    }
    $rows = [System.Collections.Generic.List[object]]::new()
    for ($i = 3; $i -lt $lines.Count; ++$i) {
        $fields = @($lines[$i].Trim() -split '\s+')
        if ($fields.Count -ne $header.Count) {
            throw "Endpoint table '$Path' line $($i + 1) has $($fields.Count) fields, expected $($header.Count)."
        }
        $row = [ordered]@{}
        for ($column = 0; $column -lt $header.Count; ++$column) {
            $row[$header[$column]] = $fields[$column]
        }
        $rows.Add([pscustomobject]$row)
    }
    $rows.ToArray()
}

$declarationColumns = @(
    'PROVIDER','LOCATION_ID','LEGACY_TIER','LEGACY_RAW','LEGACY_IDENT','LEGACY_OCCURRENCE',
    'ENDPOINT_ID','PROGRESSION_BAND','WEIGHT','TARGET_KIND','AREA','TARGET_IDENTITY',
    'CAPACITY','STATIC_POLICY','FALLBACK_ID','ADAPTER','ENABLED'
)
$fallbackColumns = @(
    'PROVIDER','FALLBACK_ID','PRIMARY_ENDPOINT_ID','AREA','X','Y','CAPACITY',
    'PROGRESSION_BAND','STATIC_POLICY','ENABLED'
)
$declarations = @(
    Read-EndpointTable -Path $bg1Path -ExpectedColumns $declarationColumns
    Read-EndpointTable -Path $bg2Path -ExpectedColumns $declarationColumns
)
$fallbacks = @(Read-EndpointTable -Path $fallbackPath -ExpectedColumns $fallbackColumns)

if ($declarations.Count -ne 527) {
    throw "Expected 527 normalized legacy declarations, found $($declarations.Count)."
}
$adapterRows = @($declarations | Where-Object TARGET_KIND -ceq 'legacy_adapter')
$ordinaryRows = @($declarations | Where-Object TARGET_KIND -cne 'legacy_adapter')
if ($adapterRows.Count -ne 3 -or @($adapterRows.ADAPTER | Sort-Object -Unique).Count -ne 3) {
    throw 'Exactly three distinct legacy adapters must remain externally delivered stable slots.'
}
if (@($ordinaryRows | Where-Object { $_.TARGET_KIND -cnotin @('creature','container','group') }).Count -ne 0) {
    throw 'An ordinary declaration uses an implicit or unsupported target kind.'
}
if (@($adapterRows | Where-Object { $_.FALLBACK_ID -cne '-' -or $_.CAPACITY -ne '1' }).Count -ne 0) {
    throw 'Legacy adapters must have one externally delivered slot and no EEex fallback.'
}

$locationIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($row in $declarations) {
    if (-not $locationIds.Add($row.PROVIDER + ':' + $row.LOCATION_ID)) {
        throw "Duplicate normalized location identity '$($row.PROVIDER):$($row.LOCATION_ID)'."
    }
    if ($row.ENABLED -cnotin @('0','1') -or [int]$row.WEIGHT -lt 1 -or [int]$row.LEGACY_OCCURRENCE -lt 1) {
        throw "Invalid declaration numeric fields for '$($row.PROVIDER):$($row.LOCATION_ID)'."
    }
    $campaign = $row.LOCATION_ID -match '^loc-bg1-' ? 'bg1' : 'bg2'
    if ($row.PROGRESSION_BAND -cne ($campaign + ':' + $row.LEGACY_TIER)) {
        throw "Progression band mismatch for '$($row.PROVIDER):$($row.LOCATION_ID)'."
    }
}

$fallbackById = @{}
foreach ($fallback in $fallbacks) {
    $qualified = $fallback.PROVIDER + ':' + $fallback.FALLBACK_ID
    if ($fallbackById.ContainsKey($qualified)) {
        throw "Duplicate fallback identity '$qualified'."
    }
    if ([int]$fallback.X -le 1 -or [int]$fallback.Y -le 1) {
        throw "Fallback '$qualified' uses an invalid ground anchor."
    }
    if ([int]$fallback.CAPACITY -lt 1 -or $fallback.STATIC_POLICY -cnotin @('derived-unique','authored-entrance','authored-nearest','authored-static')) {
        throw "Fallback '$qualified' has invalid capacity or provenance."
    }
    $fallbackById[$qualified] = $fallback
}
foreach ($row in $ordinaryRows) {
    $fallbackId = $row.PROVIDER + ':' + $row.FALLBACK_ID
    if (-not $fallbackById.ContainsKey($fallbackId)) {
        throw "Ordinary endpoint '$($row.PROVIDER):$($row.ENDPOINT_ID)' has no authored fallback."
    }
    $fallback = $fallbackById[$fallbackId]
    if ($fallback.AREA -cne $row.AREA -or $fallback.PRIMARY_ENDPOINT_ID -cne $row.ENDPOINT_ID -or
        ($fallback.PROGRESSION_BAND -cne '*' -and $fallback.PROGRESSION_BAND -cne $row.PROGRESSION_BAND)) {
        throw "Fallback '$fallbackId' crosses area, endpoint, or progression boundaries."
    }
}

$endpointGroups = @($ordinaryRows | Group-Object { $_.PROVIDER + ':' + $_.ENDPOINT_ID })
if (@($endpointGroups | Where-Object Count -gt 1).Count -lt 1) {
    throw 'The normalized base data does not model several slots at a shared physical endpoint.'
}
foreach ($group in $endpointGroups) {
    $capacities = @($group.Group.CAPACITY | Sort-Object -Unique)
    if ($capacities.Count -ne 1 -or [int]$capacities[0] -lt $group.Count) {
        throw "Endpoint '$($group.Name)' does not promise enough capacity for its stable slots."
    }
}

if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe'
}
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}
$resolvedWeidu = [System.IO.Path]::GetFullPath($WeiduPath)
$resolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot).TrimEnd('\','/')
if (-not (Test-Path -LiteralPath $resolvedWeidu -PathType Leaf) -or
    -not (Test-Path -LiteralPath $resolvedTempRoot -PathType Container)) {
    throw 'Configured disposable WeiDU or temporary root does not exist.'
}
if ($resolvedWeidu.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedTempRoot.Equals($forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedTempRoot.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to run endpoint tests in the forbidden live game root.'
}
$scratchRoot = Join-Path $resolvedTempRoot ('bgee-itemrandomiser-endpoints-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $scratchRoot

function Get-EndpointRowFingerprint {
    param(
        [Parameter(Mandatory = $true)][string] $QualifiedId,
        [Parameter(Mandatory = $true)][string] $Area,
        [Parameter(Mandatory = $true)][string] $TargetKind,
        [Parameter(Mandatory = $true)][string] $TargetIdentity,
        [Parameter(Mandatory = $true)][int] $X,
        [Parameter(Mandatory = $true)][int] $Y,
        [Parameter(Mandatory = $true)][int] $Capacity,
        [Parameter(Mandatory = $true)][string] $StaticPolicy,
        [Parameter(Mandatory = $true)][string] $Fallback,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string] $Adapter,
        [Parameter(Mandatory = $true)][int] $Enabled
    )
    $canonical = [System.Text.StringBuilder]::new()
    foreach ($value in @('endpoint', $QualifiedId, $Area, $TargetKind, $TargetIdentity, [string]$X, [string]$Y, [string]$Capacity, $StaticPolicy, $Fallback, $Adapter, [string]$Enabled)) {
        $null = $canonical.Append([System.Text.Encoding]::ASCII.GetByteCount($value)).Append('#').Append($value)
    }
    [uint64] $hash = 2166136261
    foreach ($byte in [System.Text.Encoding]::ASCII.GetBytes($canonical.ToString())) {
        $hash = ((($hash -bxor [uint64]$byte) * [uint64]16777619) -band [uint64]4294967295)
    }
    [uint64] $positive = $hash -band [uint64]2147483647
    [int64](($positive % [uint64]2147483646) + 1)
}

function Invoke-EndpointHarness {
    param(
        [Parameter(Mandatory = $true)][int] $Component,
        [Parameter(Mandatory = $true)][string] $Name,
        [bool] $ExpectSuccess = $true,
        [string] $ExpectedCode = '',
        [string] $InputBaf = '',
        [string] $ExtensionFixture = $script:extensionFixturePath,
        [switch] $UseRelocationFixture,
        [switch] $UseNormalizedTargetFixture
    )
    $run = Join-Path $script:scratchRoot ('case-' + $Component + '-' + $Name)
    $null = New-Item -ItemType Directory -Path $run
    $locationData = Join-Path $run 'randomiser\lists\locations\base'
    $endpointData = Join-Path $run 'randomiser\lists\endpoints\base'
    $null = New-Item -ItemType Directory -Path $locationData -Force
    $null = New-Item -ItemType Directory -Path $endpointData -Force
    if ($UseRelocationFixture) {
        Copy-Item -LiteralPath $script:relocationBg1LocationPath -Destination (Join-Path $locationData 'bg1.2da')
        Copy-Item -LiteralPath $script:relocationBg2LocationPath -Destination (Join-Path $locationData 'bg2.2da')
        Copy-Item -LiteralPath $script:relocationBg1EndpointPath -Destination (Join-Path $endpointData 'bg1.2da')
        Copy-Item -LiteralPath $script:relocationBg2EndpointPath -Destination (Join-Path $endpointData 'bg2.2da')
        Copy-Item -LiteralPath $script:relocationFallbackPath -Destination (Join-Path (Split-Path -Parent $endpointData) 'fallbacks.2da')
    }
    elseif ($UseNormalizedTargetFixture) {
        Copy-Item -LiteralPath $script:relocationBg1LocationPath -Destination (Join-Path $locationData 'bg1.2da')
        Copy-Item -LiteralPath $script:normalizedBg2LocationPath -Destination (Join-Path $locationData 'bg2.2da')
        Copy-Item -LiteralPath $script:relocationBg1EndpointPath -Destination (Join-Path $endpointData 'bg1.2da')
        Copy-Item -LiteralPath $script:normalizedBg2EndpointPath -Destination (Join-Path $endpointData 'bg2.2da')
        Copy-Item -LiteralPath $script:normalizedFallbackPath -Destination (Join-Path (Split-Path -Parent $endpointData) 'fallbacks.2da')
    }
    else {
        Copy-Item -LiteralPath $script:bg1LocationPath -Destination (Join-Path $locationData 'bg1.2da')
        Copy-Item -LiteralPath $script:bg2LocationPath -Destination (Join-Path $locationData 'bg2.2da')
        Copy-Item -LiteralPath $script:bg1Path -Destination (Join-Path $endpointData 'bg1.2da')
        Copy-Item -LiteralPath $script:bg2Path -Destination (Join-Path $endpointData 'bg2.2da')
        Copy-Item -LiteralPath $script:fallbackPath -Destination (Join-Path (Split-Path -Parent $endpointData) 'fallbacks.2da')
    }
    $report = Join-Path $run 'endpoint-report.txt'
    $arguments = @(
        $script:harnessPath, '--nogame', '--force-install-list', [string]$Component,
        '--args', $script:endpointsPath, '--args', (Join-Path $script:repositoryRoot 'lib\catalog.tpa'),
        '--args', $report, '--args', $ExtensionFixture, '--no-exit-pause', '--quick-log'
    )
    if (-not [string]::IsNullOrWhiteSpace($InputBaf)) {
        $arguments += @('--args', $InputBaf)
    }
    Push-Location $run
    try {
        $output = @(& $script:resolvedWeidu @arguments 2>&1 | ForEach-Object { [string]$_ })
        $exitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    $joined = $output -join "`n"
    if ($ExpectSuccess) {
        if ($exitCode -ne 0 -or $joined -match 'NOT INSTALLED DUE TO ERRORS' -or -not (Test-Path -LiteralPath $report)) {
            throw "Endpoint harness '$Name' failed unexpectedly.`n$joined"
        }
        return [System.IO.File]::ReadAllText($report)
    }
    if ($exitCode -eq 0 -or $joined -notmatch 'NOT INSTALLED DUE TO ERRORS' -or
        $joined -notmatch [regex]::Escape("FLIR_ENDPOINT_ERR $ExpectedCode")) {
        throw "Endpoint harness '$Name' did not fail with '$ExpectedCode'.`n$joined"
    }
    if (Test-Path -LiteralPath $report) {
        throw "Failed endpoint harness '$Name' published a report."
    }
    $diagnostics = @([regex]::Matches($joined, 'FLIR_ENDPOINT_ERR[^\r\n"]*') | ForEach-Object Value | Sort-Object -Unique)
    if ($diagnostics.Count -ne 1 -or $diagnostics[0] -notmatch '^FLIR_ENDPOINT_ERR [A-Z_]+ provider=[a-z0-9._-]+ id=[a-z0-9._-]+$') {
        throw "Endpoint harness '$Name' exposed a non-opaque diagnostic."
    }
}

try {
    $sslCompilerRoot = Join-Path $scratchRoot 'ssl-compiler'
    $sslStage = Join-Path $sslCompilerRoot 'randomiser\ssl'
    $sslOutput = Join-Path $sslStage 'ssl_out'
    $null = New-Item -ItemType Directory -Path $sslStage -Force
    $null = New-Item -ItemType Directory -Path $sslOutput -Force
    Copy-Item -LiteralPath $sslCompilerPath -Destination (Join-Path $sslStage 'ssl.exe')
    Copy-Item -LiteralPath $sslTemplatePath -Destination (Join-Path $sslStage 'fltier.ssl')
    Copy-Item -LiteralPath $sslLibraryPath -Destination (Join-Path $sslStage 'library.slb')
    $sslSpec = 'randomiser\ssl\fltier.ssl AreLost=False&ChanceNotLost=100&LostChance=0&IsTier5=False&TierList=1&LocationList=2;7;1001;1002;1003;1004&MaxRandom=1004&TierNumber=1&TokenList=1&AreaCode=bg2 -l randomiser\ssl\library'
    Push-Location $sslCompilerRoot
    try {
        $sslOutputLines = @(& (Join-Path $sslStage 'ssl.exe') $sslSpec 2>&1 | ForEach-Object { [string]$_ })
        $sslExit = $LASTEXITCODE
    }
    finally { Pop-Location }
    $realSslBaf = Join-Path $sslOutput 'fltier.baf'
    if ($sslExit -ne 0 -or -not (Test-Path -LiteralPath $realSslBaf -PathType Leaf)) {
        throw "The isolated SSL compiler did not produce its BAF fixture.`n$($sslOutputLines -join "`n")"
    }
    $realSslSource = [System.IO.File]::ReadAllText($realSslBaf)
    $sourceRolls = @([regex]::Matches($realSslSource, 'RandomNum\(([0-9]+),([0-9]+)\)') | ForEach-Object { $_.Groups[1].Value + ',' + $_.Groups[2].Value })
    $expectedSourceRolls = @('1004,2','1004,7','1004,1001','1004,1002','1004,1003','1004,1004')
    if (($sourceRolls -join ';') -cne ($expectedSourceRolls -join ';')) {
        throw "The real SSL fixture did not expose the expected sparse trigger set: $($sourceRolls -join ';')"
    }

    $report = Invoke-EndpointHarness -Component 0 -Name 'positive'
    if ($report -notmatch [regex]::Escape('ORDER core:endpoint-a@2;core:endpoint-b@7;core:endpoint-c@1001;core:endpoint-a@1002;core:endpoint-c@1003;core:endpoint-c@1004;') -or
        $report -notmatch [regex]::Escape('SCOPE 2;7;1001;1002;1003;1004 MAX 1004') -or
        $report -notmatch [regex]::Escape("SPARSE`r`nRandomNum(6,1)`r`nRandomNum(6,2)`r`nRandomNum(6,3)`r`nRandomNum(6,4)`r`nRandomNum(6,5)`r`nRandomNum(6,6)")) {
        throw "Fair rounds or sparse value preservation changed unexpectedly.`n$report"
    }
    $baseReport = Invoke-EndpointHarness -Component 1 -Name 'base-declarations'
    if ($baseReport -notmatch [regex]::Escape('DECLARATIONS 527 ENDPOINTS 693')) {
        throw "The normalized endpoint catalog does not exactly cover the active legacy set.`n$baseReport"
    }
    $plannedReport = Invoke-EndpointHarness -Component 2 -Name 'planned-demand'
    if ($plannedReport -notmatch [regex]::Escape('PLANNED_SLOTS 3')) {
        throw "Endpoint demand was not derived from the read-only removal plan.`n$plannedReport"
    }
    $historyReport = Invoke-EndpointHarness -Component 3 -Name 'generated-history'
    if ($historyReport -notmatch [regex]::Escape('HISTORY slots=1 value=7')) {
        throw "A compatible lower-demand reinstall did not retain its historical generated slot.`n$historyReport"
    }
    $tombstoneReport = Invoke-EndpointHarness -Component 4 -Name 'generated-tombstone'
    if ($tombstoneReport -notmatch [regex]::Escape('TOMBSTONE generation=1 slots=1')) {
        throw "A generated slot reused a tombstoned stable identity.`n$tombstoneReport"
    }
    $overflowReport = Invoke-EndpointHarness -Component 5 -Name 'historical-overflow'
    if ($overflowReport -notmatch [regex]::Escape('OVERFLOW active=528 matched=528 slots=528 value=300')) {
        throw "A historical overflow slot did not retain its exact physical endpoint and compact.`n$overflowReport"
    }
    $normalizedOverflowReport = Invoke-EndpointHarness -Component 36 -Name 'normalized-historical-overflow' -UseNormalizedTargetFixture
    if ($normalizedOverflowReport -notmatch [regex]::Escape('RAW_OVERFLOW active=2 matched=2 slots=2 value=300 endpoint=legacy:ep-normalized')) {
        throw "Historical overflow replay confused a raw legacy selector with its normalized endpoint identity.`n$normalizedOverflowReport"
    }
    $extensionReport = Invoke-EndpointHarness -Component 7 -Name 'extension-operations'
    if ($extensionReport -notmatch [regex]::Escape('EXTENSIONS endpoints=695 slots=529 active_ext=1 disabled_ext=1 compact=17')) {
        throw "Endpoint ADD/REPLACE/DISABLE operations did not survive loading or preserve slot identity.`n$extensionReport"
    }
    $postRehydrateReport = Invoke-EndpointHarness -Component 30 -Name 'extension-post-rehydrate'
    if ($postRehydrateReport -notmatch [regex]::Escape('POST_REHYDRATE enabled=0 value=7')) {
        throw "A post-rehydration extension could not disable a persisted generated slot.`n$postRehydrateReport"
    }
    $parsedMutationReport = Invoke-EndpointHarness -Component 33 -Name 'parsed-extension-mutations' -ExtensionFixture $mutationFixturePath
    if ($parsedMutationReport -notmatch [regex]::Escape('PARSED_MUTATIONS x=70 disabled=1')) {
        throw "Parsed REPLACE/DISABLE extension rows did not mutate their authenticated targets.`n$parsedMutationReport"
    }
    $null = Invoke-EndpointHarness -Component 34 -Name 'active-area-drift' -ExpectSuccess $false -ExpectedCode 'ACTIVE_AREA_DRIFT' -UseRelocationFixture
    $relocationPrimaryFingerprint = Get-EndpointRowFingerprint -QualifiedId 'legacy:ep-reloc' -Area 'area-old' -TargetKind 'container' -TargetIdentity 'target-old' -X 100 -Y 200 -Capacity 1 -StaticPolicy 'runtime-resolve' -Fallback 'legacy:fb-reloc' -Adapter '-' -Enabled 1
    $relocationFallbackFingerprint = Get-EndpointRowFingerprint -QualifiedId 'legacy:fb-reloc' -Area 'area-old' -TargetKind 'ground' -TargetIdentity '-' -X 100 -Y 200 -Capacity 1 -StaticPolicy 'authored-static' -Fallback '-' -Adapter '' -Enabled 1
    $relocationExtensionPath = Join-Path $scratchRoot 'relocation-extension.2da'
    $relocationExtension = @(
        '2DA V1.0',
        '0',
        'KIND OPERATION ACTOR_PROVIDER PROVIDER ID EXPECTED_FINGERPRINT CAMPAIGN TIER AREA TARGET_KIND TARGET_IDENTITY X Y CAPACITY STATIC_POLICY FALLBACK ADAPTER ENDPOINT_ID PROGRESSION_BAND WEIGHT ENABLED',
        "endpoint REPLACE compat legacy ep-reloc $relocationPrimaryFingerprint - - area-new container target-old 0 0 1 runtime-resolve legacy:fb-reloc - - - 0 1",
        "endpoint REPLACE compat legacy fb-reloc $relocationFallbackFingerprint - - area-new ground - 300 400 1 authored-static - - - - 0 1"
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($relocationExtensionPath, $relocationExtension + "`r`n", [System.Text.Encoding]::ASCII)
    $relocationReport = Invoke-EndpointHarness -Component 35 -Name 'active-area-replaced' -ExtensionFixture $relocationExtensionPath -UseRelocationFixture
    if ($relocationReport -notmatch [regex]::Escape('AREA_REPLACED primary=area-new fallback=area-new')) {
        throw "Authenticated endpoint replacements did not authorize the active area move.`n$relocationReport"
    }
    $realSslReport = Invoke-EndpointHarness -Component 6 -Name 'real-ssl-sparse' -InputBaf $realSslBaf
    $denseRolls = @([regex]::Matches($realSslReport, 'RandomNum\(([0-9]+),([0-9]+)\)') | ForEach-Object { $_.Groups[1].Value + ',' + $_.Groups[2].Value })
    $expectedDenseRolls = @('6,1','6,2','6,3','6,4','6,5','6,6')
    if (($denseRolls -join ';') -cne ($expectedDenseRolls -join ';') -or $realSslReport -match 'RandomNum\(1004,') {
        throw "Real SSL output retained a sparse trigger after production rewriting: $($denseRolls -join ';')"
    }
    foreach ($stableValue in @(2,7,1001,1002,1003,1004)) {
        if ($realSslReport -notmatch [regex]::Escape("SetGlobal(`"fl1t1`",`"GLOBAL`",$stableValue)")) {
            throw "Real SSL rewriting changed the saved stable value $stableValue."
        }
    }
    foreach ($failure in @(
        @{ Component = 10; Name = 'cross-area'; Code = 'CROSS_AREA_FALLBACK' },
        @{ Component = 11; Name = 'cycle'; Code = 'FALLBACK_CYCLE' },
        @{ Component = 12; Name = 'ground-1-1'; Code = 'INVALID_GROUND' },
        @{ Component = 13; Name = 'no-loss-shortage'; Code = 'CAPACITY_SHORTAGE' },
        @{ Component = 14; Name = 'some-loss-shortage'; Code = 'CAPACITY_SHORTAGE' },
        @{ Component = 15; Name = 'fallback-capacity'; Code = 'FALLBACK_CAPACITY' },
        @{ Component = 16; Name = 'invalid-slot-fields'; Code = 'INVALID_SLOT' },
        @{ Component = 17; Name = 'invalid-adapter'; Code = 'INVALID_ADAPTER' },
        @{ Component = 18; Name = 'invalid-policy'; Code = 'INVALID_POLICY' },
        @{ Component = 19; Name = 'extension-fingerprint'; Code = 'EXTENSION_FINGERPRINT' },
        @{ Component = 20; Name = 'extension-duplicate'; Code = 'DUPLICATE_ENDPOINT' },
        @{ Component = 21; Name = 'extension-incomplete-disable'; Code = 'EXTENSION_REFERENCE' },
        @{ Component = 22; Name = 'extension-scope-drift'; Code = 'EXTENSION_SCOPE_DRIFT' },
        @{ Component = 23; Name = 'extension-cross-area'; Code = 'CROSS_AREA_FALLBACK' },
        @{ Component = 24; Name = 'extension-invalidates-plan'; Code = 'PLAN_NOT_READY' },
        @{ Component = 25; Name = 'fallback-plus-direct-capacity'; Code = 'FALLBACK_CAPACITY' },
        @{ Component = 26; Name = 'extension-invalid-actor'; Code = 'EXTENSION_ACTOR' },
        @{ Component = 27; Name = 'extension-invalid-scope'; Code = 'INVALID_SLOT_SCOPE' },
        @{ Component = 28; Name = 'active-slot-tombstone'; Code = 'SLOT_TOMBSTONE_REAPPEAR' },
        @{ Component = 29; Name = 'endpoint-coordinate-bounds'; Code = 'INVALID_COORDINATES' },
        @{ Component = 32; Name = 'extension-invalid-header'; Code = 'EXTENSION_HEADER'; Fixture = $invalidHeaderFixturePath }
    )) {
        $fixture = if ($failure.ContainsKey('Fixture')) { $failure.Fixture } else { $extensionFixturePath }
        $null = Invoke-EndpointHarness -Component $failure.Component -Name $failure.Name -ExpectSuccess $false -ExpectedCode $failure.Code -ExtensionFixture $fixture
    }
    Write-Output 'PASS Endpoints_ExplicitKindsAndThreeAdapters'
    Write-Output 'PASS Endpoints_AllLegacyRowsNormalized'
    Write-Output 'PASS Endpoints_PlannedDemandValidatedBeforeMutation'
    Write-Output 'PASS Endpoints_GeneratedHistoryRetainedAndTombstonesNotReused'
    Write-Output 'PASS Endpoints_HistoricalOverflowRetainsPhysicalEndpoint'
    Write-Output 'PASS Endpoints_SameAreaAuthoredFallbacksAndNoOneOne'
    Write-Output 'PASS Endpoints_ProgressionBandsAndCapacity'
    Write-Output 'PASS Endpoints_FairRoundsAndSeveralSlotsPerEndpoint'
    Write-Output 'PASS Endpoints_MigratedValuesTombstoneGapAndSparseMax'
    Write-Output 'PASS Endpoints_SparseTombstoneRollsUseDenseBound'
    Write-Output 'PASS Endpoints_RealSslOutputUsesDenseSparseRolls'
    Write-Output 'PASS Endpoints_ShortageFatalInBothLossModes'
    Write-Output 'PASS Endpoints_FallbackAndSlotContractsValidated'
    Write-Output 'PASS Endpoints_ExtensionAddReplaceDisableAndConflicts'
    Write-Output "SUMMARY declarations=$($declarations.Count) ordinary=$($ordinaryRows.Count) adapters=$($adapterRows.Count) fallbacks=$($fallbacks.Count) physical=$($endpointGroups.Count)"
}
finally {
    if (Test-Path -LiteralPath $scratchRoot) {
        [System.IO.Directory]::Delete($scratchRoot, $true)
    }
}
