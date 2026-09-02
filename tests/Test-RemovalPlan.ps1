[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$fixturePath = Join-Path $PSScriptRoot 'fixtures\sources\removal-fixtures.json'
$harnessPath = Join-Path $PSScriptRoot 'weidu\removal-harness.tp2'
$libPath = Join-Path $repositoryRoot 'lib\lib.tpa'
$palettePath = Join-Path $repositoryRoot 'lib\fl#bg1pal.tpa'
$catalogPath = Join-Path $repositoryRoot 'lib\catalog.tpa'
$removalPlanPath = Join-Path $repositoryRoot 'lib\removal_plan.tpa'
$tokenTemplatePath = Join-Path $repositoryRoot 'copy\flrandom.cre'
$arraysPath = Join-Path $repositoryRoot 'lib\arrays.tpa'
$selectorTablePath = Join-Path $repositoryRoot 'lists\sources\base\bg2.2da'
$bg1SelectorTablePath = Join-Path $repositoryRoot 'lists\sources\base\bg1.2da'
$bg2ItemsPath = Join-Path $repositoryRoot 'lists\items\base\bg2.2da'
$bg1ItemsPath = Join-Path $repositoryRoot 'lists\items\base\bg1.2da'
$deletePath = Join-Path $repositoryRoot 'lib\delete.tpa'
$tp2Path = Join-Path $repositoryRoot 'randomiser.tp2'
$modeBoundaryPath = Join-Path $PSScriptRoot 'Test-ModeBoundary.ps1'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath("C:\Games\Baldur's Gate II Enhanced Edition modded")

if (-not (Test-Path -LiteralPath $removalPlanPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED RemovalPlan_ProductionLibraryMissing lib/removal_plan.tpa'
    exit 1
}

foreach ($requiredPath in @($fixturePath, $harnessPath, $libPath, $palettePath, $catalogPath, $tokenTemplatePath, $arraysPath, $selectorTablePath, $bg1SelectorTablePath, $bg2ItemsPath, $bg1ItemsPath, $deletePath, $tp2Path)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required removal-plan test input is missing: $requiredPath"
    }
}

$removalPlanSource = [System.IO.File]::ReadAllText($removalPlanPath)
$staticFailures = [System.Collections.Generic.List[string]]::new()
foreach ($requiredSymbol in @(
    'flir_source_stage1_raw',
    'flir_source_apply_internal_selector_table',
    'flir_source_apply_extension_hook',
    'flir_source_filter_stage1',
    'flir_removal_plan_build',
    'flir_removal_validate_token_capacity',
    'flir_removal_recheck_script_precondition',
    'flir_removal_legacy_adapter_pre_unshey',
    'flir_removal_legacy_adapter_post_unshey',
    'flir_removal_run_legacy_adapter_post_hooks',
    'flir_removal_plan_apply'
)) {
    if ($removalPlanSource -notmatch ('DEFINE_(?:ACTION|PATCH)_MACRO\s+' + [regex]::Escape($requiredSymbol))) {
        $staticFailures.Add("Removal-plan library does not expose '$requiredSymbol'.")
    }
}

$buildMacroMatch = [regex]::Match($removalPlanSource, '(?s)DEFINE_ACTION_MACRO\s+flir_removal_plan_build\s+BEGIN(?<body>.*?)(?=\r?\nDEFINE_)')
if (-not $buildMacroMatch.Success -or $buildMacroMatch.Groups['body'].Value -notmatch 'LAM\s+flir_removal_validate_token_capacity') {
    $staticFailures.Add('flir_removal_plan_build does not run token capacity validation before returning.')
}

if ($removalPlanSource -notmatch '(?s)flir_removal_recheck_script_precondition.*?LAM\s+flir_removal_emit_plan') {
    $staticFailures.Add('Script-source token emission is not preceded by a script precondition recheck.')
}

foreach ($legacyPattern in @(
    'UNKNOWN_LEGACY_ADAPTER',
    'fl#bg1pal.*ogreunsh\.cre',
    'fl#bg1pal.*unshey\.dlg',
    'fl#bg1pal.*book32\.itm',
    'fl#bg1pal.*belt04\.itm',
    'ADD_JOURNAL\s+EXISTING',
    'COMPILE\s+EVALUATE_BUFFER\s+"randomiser/d/unshey\.d"',
    'COMPILE\s+"randomiser/d/bg1unshey\.d"',
    'COMPILE\s+"randomiser/d/bg1ubunshey\.d"'
)) {
    if ($removalPlanSource -notmatch $legacyPattern) {
        $staticFailures.Add("Legacy adapter wiring is missing pattern '$legacyPattern'.")
    }
}

$arraysSource = [System.IO.File]::ReadAllText($arraysPath)
if ($arraysSource -notmatch 'flir_source_stage1_raw' -or
    $arraysSource -notmatch 'flir_source_apply_extension_hook' -or
    $arraysSource -notmatch 'flir_source_filter_stage1') {
    throw 'arrays.tpa does not route Mode 1 item declarations through the pre-filter source hook.'
}
$selectorOrder = [regex]::Match(
    $arraysSource,
    '(?s)lists/items/base/bg2\.2da.*?flir_source_stage1_raw.*?flir_source_internal_selector_table.*?flir_source_apply_internal_selector_table.*?flir_source_filter_stage1_legacy_ident_filter'
)
if (-not $selectorOrder.Success) {
    throw 'The internal BG2 selector table is not applied between raw staging and the legacy filter.'
}
$bg1SelectorOrder = [regex]::Match(
    $arraysSource,
    '(?s)lists/items/base/bg1\.2da.*?flir_source_stage1_raw.*?flir_source_internal_selector_table.*?flir_source_apply_internal_selector_table.*?flir_source_filter_stage1_legacy_ident_filter'
)
if (-not $bg1SelectorOrder.Success) {
    throw 'The internal BG1 match-policy table is not applied between raw staging and the legacy filter.'
}

function Test-InternalMatchPolicyTable {
    param(
        [string] $TablePath,
        [string] $ItemsPath,
        [int] $ExpectedRows,
        [int] $ExpectedSingleRows,
        [int] $ExpectedGroupedRows,
        [string] $Campaign
    )
    $tableBytes = [System.IO.File]::ReadAllBytes($TablePath)
    if (@($tableBytes | Where-Object { $_ -lt 32 -and $_ -notin @(9, 10, 13) }).Count -ne 0) {
        throw "The internal $Campaign match-policy table contains non-printable bytes."
    }
    $stableIdents = @{}
    $stableDeclarationRows = @{}
    foreach ($line in Get-Content -LiteralPath $ItemsPath | Select-Object -Skip 1) {
        $parts = @($line.Trim() -split '\s+' | Where-Object { $_ -ne '' })
        if ($parts.Count -ge 7 -and $parts[5] -cne 'x') {
            $ident = $parts[5].ToLowerInvariant()
            $stableIdents[$ident] = $true
            $stableDeclarationRows[$ident] = $parts
        }
    }

    $seen = @{}
    $singleAssignments = @{}
    $singleRows = 0
    $groupedRows = 0
    $rows = @(Get-Content -LiteralPath $TablePath | Select-Object -Skip 1 | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($rows.Count -ne $ExpectedRows) {
        throw "The internal $Campaign match-policy table has an unexpected audited-row count."
    }
    foreach ($line in $rows) {
        $parts = @($line.Trim() -split '\s+' | Where-Object { $_ -ne '' })
        if ($parts.Count -ne 4 -or @($parts | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
            throw "An internal $Campaign match-policy row is malformed or empty."
        }
        $ident = $parts[0].ToLowerInvariant()
        $selectorValue = $parts[1].ToLowerInvariant()
        $policy = $parts[2].ToLowerInvariant()
        $expectedMatches = 0
        if (-not [int]::TryParse($parts[3], [ref] $expectedMatches)) {
            throw "An internal $Campaign match-policy row has a non-numeric expected count."
        }
        if ($ident -ceq 'x' -or $seen.ContainsKey($ident) -or -not $stableIdents.ContainsKey($ident)) {
            throw "An internal $Campaign match-policy row does not target one unique authored stable identity."
        }
        if ($policy -ceq 'single') {
            if ($selectorValue -cin @('*', 'any', 'none') -or $expectedMatches -ne 1) {
                throw "An internal $Campaign single-match row lacks one explicit selector."
            }
            $declaration = @($stableDeclarationRows[$ident])
            $assignmentKey = (@($declaration[0], $declaration[1], $declaration[2], $declaration[3], $declaration[6], $selectorValue) -join [char]31)
            if ($singleAssignments.ContainsKey($assignmentKey)) {
                throw "Two internal $Campaign single-match rows target the same authored source unit."
            }
            $singleAssignments[$assignmentKey] = $ident
            $singleRows++
        }
        elseif ($policy -ceq 'all_one_unit') {
            if ($selectorValue -cnotin @('*', 'any') -or $expectedMatches -ne 2) {
                throw "An internal $Campaign grouped row lacks the audited wildcard/count contract."
            }
            $groupedRows++
        }
        else {
            throw "An internal $Campaign match-policy row has an unknown policy."
        }
        $seen[$ident] = $true
    }
    if ($singleRows -ne $ExpectedSingleRows -or $groupedRows -ne $ExpectedGroupedRows) {
        throw "The internal $Campaign match-policy distribution differs from the audited contract."
    }
}

Test-InternalMatchPolicyTable -TablePath $selectorTablePath -ItemsPath $bg2ItemsPath -ExpectedRows 4 -ExpectedSingleRows 4 -ExpectedGroupedRows 0 -Campaign 'BG2'
Test-InternalMatchPolicyTable -TablePath $bg1SelectorTablePath -ItemsPath $bg1ItemsPath -ExpectedRows 3 -ExpectedSingleRows 2 -ExpectedGroupedRows 1 -Campaign 'BG1'

$deleteSource = [System.IO.File]::ReadAllText($deletePath)
if ($deleteSource -notmatch 'weidu_action\s*=\s*1' -or $deleteSource -notmatch 'flir_removal_plan_apply') {
    throw 'delete.tpa is not split between legacy Mode 2 deletion and Mode 1 plan apply.'
}

$tp2Source = [System.IO.File]::ReadAllText($tp2Path)
foreach ($component in @(1100, 1200)) {
    $pattern = '(?s)DESIGNATED\s+' + $component + '\b.*?INCLUDE\s+"randomiser/lib/removal_plan\.tpa".*?OUTER_SPRINT\s+flir_removal_token_template\s+~randomiser/copy/flrandom\.cre~.*?INCLUDE\s+"randomiser/lib/arrays\.tpa".*?flir_removal_plan_build.*?INCLUDE\s+"randomiser/lib/copy\.tpa"'
    if ($tp2Source -notmatch $pattern) {
        $staticFailures.Add("Component $component does not set the token template and build/validate the removal plan before copy.tpa.")
    }
}
foreach ($component in @(1300, 1400)) {
    $componentMatch = [regex]::Match($tp2Source, '(?s)DESIGNATED\s+' + $component + '\b(?<body>.*?)(?=\nBEGIN|\z)')
    if (-not $componentMatch.Success -or $componentMatch.Groups['body'].Value -match 'removal_plan\.tpa|flir_removal_plan_build') {
        throw "Mode 2 component $component references the Mode 1 removal-plan seam."
    }
}

if ($staticFailures.Count -ne 0) {
    foreach ($failure in $staticFailures) {
        Write-Output "EXPECTED_RED RemovalPlan_StaticSpecReview $failure"
    }
    throw 'Removal-plan static spec-review assertions failed.'
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
foreach ($path in @($resolvedWeidu, $resolvedTempRoot)) {
    if ($path.Equals($forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $path.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to use a removal-plan path under the forbidden live game root.'
    }
}

$fixtures = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json
$scratchRoot = Join-Path $resolvedTempRoot ('bgee-itemrandomiser-removal-' + [guid]::NewGuid().ToString('N'))
$scratchRoot = [System.IO.Path]::GetFullPath($scratchRoot)
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated removal scratch directory escaped its disposable parent.'
}

function Write-AsciiFixed {
    param([byte[]] $Buffer, [int] $Offset, [string] $Value, [int] $Length)
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($Value.ToLowerInvariant())
    $copyLength = [Math]::Min($bytes.Length, $Length)
    [Array]::Copy($bytes, 0, $Buffer, $Offset, $copyLength)
}

function Write-U16 {
    param([byte[]] $Buffer, [int] $Offset, [int] $Value)
    $bytes = [BitConverter]::GetBytes([UInt16] $Value)
    [Array]::Copy($bytes, 0, $Buffer, $Offset, 2)
}

function Write-U32 {
    param([byte[]] $Buffer, [int] $Offset, [int] $Value)
    $bytes = [BitConverter]::GetBytes([UInt32] $Value)
    [Array]::Copy($bytes, 0, $Buffer, $Offset, 4)
}

function New-ItmFixture {
    param([string] $Path, [psobject] $Item)
    $abilityOffset = 0x72
    $buffer = New-Object byte[] ($abilityOffset + 0x38 * 3)
    Write-AsciiFixed $buffer 0 'ITM V1  ' 8
    Write-U16 $buffer 0x1c ([int] $Item.category)
    Write-U32 $buffer 0x64 $abilityOffset
    Write-U16 $buffer 0x68 3
    for ($index = 0; $index -lt 3; $index++) {
        Write-U16 $buffer ($abilityOffset + 0x38 * $index + 0x22) ([int] $Item.charges[$index])
    }
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function Write-ItemRecord {
    param([byte[]] $Buffer, [int] $Offset, [psobject] $Item, [int] $RecordSize)
    Write-AsciiFixed $Buffer $Offset ([string] $Item.resref) 8
    $expirationProperty = $Item.PSObject.Properties['expiration']
    $expiration = if ($null -eq $expirationProperty) { 0 } else { [int] $expirationProperty.Value }
    Write-U16 $Buffer ($Offset + 0x08) $expiration
    Write-U16 $Buffer ($Offset + 0x0a) ([int] $Item.charges[0])
    Write-U16 $Buffer ($Offset + 0x0c) ([int] $Item.charges[1])
    Write-U16 $Buffer ($Offset + 0x0e) ([int] $Item.charges[2])
    Write-U32 $Buffer ($Offset + 0x10) ([int] $Item.flags)
    if ($RecordSize -eq 0x1c) {
        Write-U32 $Buffer ($Offset + 0x14) ([int] $Item.stock)
        Write-U32 $Buffer ($Offset + 0x18) ([int] $Item.infinite)
    }
}

function New-CreFixture {
    param([string] $Path, [object[]] $Items)
    $itemsOffset = 0x300
    $slotsOffset = $itemsOffset + 0x14 * $Items.Count
    $buffer = New-Object byte[] ($slotsOffset + 37 * 2)
    Write-AsciiFixed $buffer 0 'CRE V1.0' 8
    Write-U32 $buffer 0x2b8 $slotsOffset
    Write-U32 $buffer 0x2bc $itemsOffset
    Write-U32 $buffer 0x2c0 $Items.Count
    for ($slot = 0; $slot -lt 37; $slot++) {
        Write-U16 $buffer ($slotsOffset + 2 * $slot) 0xffff
    }
    for ($index = 0; $index -lt $Items.Count; $index++) {
        Write-ItemRecord $buffer ($itemsOffset + 0x14 * $index) $Items[$index] 0x14
        if ($null -ne $Items[$index].slot) {
            Write-U16 $buffer ($slotsOffset + 2 * [int] $Items[$index].slot) $index
        }
    }
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function New-AreFixture {
    param([string] $Path, [object[]] $Containers)
    $containerOffset = 0x100
    $items = @()
    foreach ($container in $Containers) {
        foreach ($item in @($container.items)) {
            $items += $item
        }
    }
    $itemsOffset = $containerOffset + 0xc0 * $Containers.Count
    $buffer = New-Object byte[] ($itemsOffset + 0x14 * $items.Count)
    Write-AsciiFixed $buffer 0 'AREA V1.0' 8
    Write-U32 $buffer 0x70 $containerOffset
    Write-U16 $buffer 0x74 $Containers.Count
    Write-U16 $buffer 0x76 $items.Count
    Write-U32 $buffer 0x78 $itemsOffset
    Write-U32 $buffer 0xcc $buffer.Length
    $itemIndex = 0
    for ($containerIndex = 0; $containerIndex -lt $Containers.Count; $containerIndex++) {
        $containerOffsetCurrent = $containerOffset + 0xc0 * $containerIndex
        Write-AsciiFixed $buffer $containerOffsetCurrent ([string] $Containers[$containerIndex].name) 32
        Write-U32 $buffer ($containerOffsetCurrent + 0x40) $itemIndex
        Write-U32 $buffer ($containerOffsetCurrent + 0x44) @($Containers[$containerIndex].items).Count
        foreach ($item in @($Containers[$containerIndex].items)) {
            Write-ItemRecord $buffer ($itemsOffset + 0x14 * $itemIndex) $item 0x14
            $itemIndex++
        }
    }
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function New-StoFixture {
    param([string] $Path, [object[]] $Items)
    $saleOffset = 0x9c
    $afterSale = $saleOffset + 0x1c * $Items.Count
    $buffer = New-Object byte[] $afterSale
    Write-AsciiFixed $buffer 0 'STORV1.0' 8
    Write-U32 $buffer 0x2c $afterSale
    Write-U32 $buffer 0x34 $saleOffset
    Write-U32 $buffer 0x38 $Items.Count
    Write-U32 $buffer 0x4c $afterSale
    Write-U32 $buffer 0x70 $afterSale
    for ($index = 0; $index -lt $Items.Count; $index++) {
        Write-ItemRecord $buffer ($saleOffset + 0x1c * $index) $Items[$index] 0x1c
    }
    [System.IO.File]::WriteAllBytes($Path, $buffer)
}

function Copy-FixtureCase {
    param([psobject] $Case, [string] $RunDirectory)
    $override = Join-Path $RunDirectory 'override'
    $null = New-Item -ItemType Directory -Path $override -Force
    [System.IO.File]::WriteAllBytes((Join-Path $RunDirectory '...blank'), @())
    foreach ($item in @($Case.items)) {
        New-ItmFixture -Path (Join-Path $override ($item.resref + '.itm')) -Item $item
    }
    foreach ($property in @($Case.creatures.PSObject.Properties)) {
        New-CreFixture -Path (Join-Path $override $property.Name) -Items @($property.Value)
    }
    $fullTokenTiersProperty = $Case.PSObject.Properties['fullTokenTiers']
    if ($null -ne $fullTokenTiersProperty) {
        foreach ($tier in @($fullTokenTiersProperty.Value)) {
            $tokenItems = @()
            for ($slot = 0; $slot -lt 37; $slot++) {
                $tokenItems += [pscustomobject]@{
                    resref = ('fill' + $slot.ToString('000'))
                    slot = $slot
                    charges = @(0, 0, 0)
                    flags = 0
                }
            }
            New-CreFixture -Path (Join-Path $override ('fltier' + $tier + '.cre')) -Items $tokenItems
        }
    }
    $areaProperty = $Case.PSObject.Properties['areas']
    if ($null -ne $areaProperty) {
        foreach ($property in @($areaProperty.Value.PSObject.Properties)) {
            New-AreFixture -Path (Join-Path $override $property.Name) -Containers @($property.Value)
        }
    }
    $storeProperty = $Case.PSObject.Properties['stores']
    if ($null -ne $storeProperty) {
        foreach ($property in @($storeProperty.Value.PSObject.Properties)) {
            New-StoFixture -Path (Join-Path $override $property.Name) -Items @($property.Value)
        }
    }
    $scriptProperty = $Case.PSObject.Properties['scripts']
    if ($null -ne $scriptProperty) {
        foreach ($property in @($scriptProperty.Value.PSObject.Properties)) {
            [System.IO.File]::WriteAllText((Join-Path $override $property.Name), [string] $property.Value, [System.Text.Encoding]::ASCII)
        }
    }
}

function Write-HookFixtures {
    param([string] $RunDirectory)
    $extensionPath = Join-Path $RunDirectory 'source-extension.2da'
    $internalSelectorPath = Join-Path $RunDirectory 'internal-source-selectors.2da'
    $selectorRawPath = Join-Path $RunDirectory 'selector-items.2da'
    $selectorExtensionPath = Join-Path $RunDirectory 'selector-extension.2da'
    $groupedPolicyPath = Join-Path $RunDirectory 'grouped-policy.2da'
    $groupedRawPath = Join-Path $RunDirectory 'grouped-items.2da'
    $groupedReplacementPath = Join-Path $RunDirectory 'grouped-replacement.2da'
    $rawPath = Join-Path $RunDirectory 'raw-items.2da'
    $semanticGlobalPath = Join-Path $RunDirectory 'semantic-global.2da'
    $semanticRowsPath = Join-Path $RunDirectory 'semantic-rows.2da'
    $sourceContentPath = Join-Path $RunDirectory 'source-content.2da'
    [System.IO.File]::WriteAllText(
        $extensionPath,
        "2DA V1.0`n0`nOP ITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE SOURCE_KIND OBJECT_OR_SLOT VIRTUAL_POLICY CHARGE1 CHARGE2 CHARGE3 EXPECTED_QUANTITY`nREPLACE movitm blank present.cre 1 8 moved-ident 100 cre_inventory * none 0 0 0 1`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $internalSelectorPath,
        "IDENT OBJECT_OR_SLOT MATCH_POLICY EXPECTED_MATCHES`nselector-stable target-two single 1`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $selectorRawPath,
        "2DA V1.0`n0`nITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE`nselitm blank select.are 1 14 selector-stable 100`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $selectorExtensionPath,
        "2DA V1.0`n0`nOP ITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE SOURCE_KIND OBJECT_OR_SLOT VIRTUAL_POLICY CHARGE1 CHARGE2 CHARGE3 EXPECTED_QUANTITY`nREPLACE selitm blank select.are 1 14 selector-stable 100 area_container target-one none 0 0 0 1`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $groupedPolicyPath,
        "IDENT OBJECT_OR_SLOT MATCH_POLICY EXPECTED_MATCHES`nambiguous * all_one_unit 2`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $groupedRawPath,
        "2DA V1.0`n0`nITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE`nambitm blank ambig.cre 1 1 ambiguous 100`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $groupedReplacementPath,
        "2DA V1.0`n0`nOP ITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE SOURCE_KIND OBJECT_OR_SLOT VIRTUAL_POLICY CHARGE1 CHARGE2 CHARGE3 EXPECTED_QUANTITY`nREPLACE ambitm blank ambig.cre 1 1 ambiguous 100 cre_inventory slot:21 none 0 0 0 1`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $rawPath,
        "2DA V1.0`n0`nITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE`nmovitm blank absent.cre 1 8 moved-ident 100`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $semanticGlobalPath,
        "2DA V1.0`n0`nITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE`nglobskp blank filter.cre 1 2 global-chance 100`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $semanticRowsPath,
        "2DA V1.0`n0`nITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE`noptskip blank filter.cre s4 1 opt-gated 100`nrowskip blank filter.cre 1 3 row-chance 0`nkeepok blank filter.cre 1 4 kept 100`n",
        [System.Text.Encoding]::ASCII
    )
    [System.IO.File]::WriteAllText(
        $sourceContentPath,
        "2DA V1.0`n0`nITEM REPLACEMENT SOURCE TIER TOKEN IDENT CHANCE`nmissbcs blank missing.bcs 1 9 missing-bcs 100`nmissdlg blank missing.dlg 1 10 missing-dlg 100`nhitbcs blank hit.bcs 1 11 hit-bcs 100`n",
        [System.Text.Encoding]::ASCII
    )
    [pscustomobject]@{
        Extension = $extensionPath
        InternalSelector = $internalSelectorPath
        SelectorRaw = $selectorRawPath
        SelectorExtension = $selectorExtensionPath
        GroupedPolicy = $groupedPolicyPath
        GroupedRaw = $groupedRawPath
        GroupedReplacement = $groupedReplacementPath
        Raw = $rawPath
        SemanticGlobal = $semanticGlobalPath
        SemanticRows = $semanticRowsPath
        SourceContent = $sourceContentPath
    }
}

function Invoke-RemovalHarness {
    param(
        [int] $Component,
        [string] $Name,
        [psobject] $Case,
        [bool] $ExpectSuccess = $true,
        [string] $ExpectedErrorCode = '',
        [string] $ForbiddenLogText = ''
    )
    $runDirectory = Join-Path $script:scratchRoot ('case-' + $Component + '-' + $Name)
    $null = New-Item -ItemType Directory -Path $runDirectory
    Copy-FixtureCase -Case $Case -RunDirectory $runDirectory
    $hookFiles = Write-HookFixtures -RunDirectory $runDirectory
    $reportPath = Join-Path $runDirectory 'removal-report.txt'
    $arguments = @(
        $script:harnessPath,
        '--nogame',
        '--force-install-list', [string] $Component,
        '--args', $script:libPath,
        '--args', $script:catalogPath,
        '--args', $script:removalPlanPath,
        '--args', $reportPath,
        '--args', $hookFiles.Extension,
        '--args', $hookFiles.Raw,
        '--args', $script:palettePath,
        '--args', $hookFiles.SemanticGlobal,
        '--args', $hookFiles.SemanticRows,
        '--args', $hookFiles.SourceContent,
        '--args', $hookFiles.InternalSelector,
        '--args', $hookFiles.SelectorRaw,
        '--args', $hookFiles.SelectorExtension,
        '--args', $hookFiles.GroupedPolicy,
        '--args', $hookFiles.GroupedRaw,
        '--args', $hookFiles.GroupedReplacement,
        '--args', $script:tokenTemplatePath,
        '--no-exit-pause',
        '--quick-log'
    )
    $before = @{}
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $runDirectory 'override') -File) {
        $before[$file.Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
    }
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
            throw "Removal harness case '$Name' failed unexpectedly.`n$joined"
        }
        if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
            throw "Removal harness case '$Name' did not write a report."
        }
    }
    else {
        if ($exitCode -eq 0 -or $joined -notmatch 'NOT INSTALLED DUE TO ERRORS') {
            throw "Removal harness case '$Name' did not fail as expected.`n$joined"
        }
        if ($joined -notmatch [regex]::Escape("FLIR_REMOVAL_ERR $ExpectedErrorCode")) {
            throw "Removal harness case '$Name' did not report '$ExpectedErrorCode'.`n$joined"
        }
        if (-not [string]::IsNullOrEmpty($ForbiddenLogText) -and $joined -match [regex]::Escape($ForbiddenLogText)) {
            throw "Removal harness case '$Name' reached forbidden marker '$ForbiddenLogText'.`n$joined"
        }
        if (Test-Path -LiteralPath $reportPath) {
            throw "Failed removal harness case '$Name' published a report."
        }
        foreach ($fileName in $before.Keys) {
            $afterHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path (Join-Path $runDirectory 'override') $fileName)).Hash
            if ($afterHash -ne $before[$fileName]) {
                throw "Failed removal harness case '$Name' left changed bytes in '$fileName'."
            }
        }
    }
    [pscustomobject]@{
        RunDirectory = $runDirectory
        ReportPath = $reportPath
        Report = if (Test-Path -LiteralPath $reportPath) { [System.IO.File]::ReadAllText($reportPath) } else { '' }
        Log = $joined
    }
}

function Assert-ReportContainsExactlyOnce {
    param([string] $Report, [string] $Line)
    $matches = @($Report -split "`r?`n" | Where-Object { $_ -ceq $Line })
    if ($matches.Count -ne 1) {
        throw "Expected report line exactly once: $Line`n$Report"
    }
}

try {
    $null = New-Item -ItemType Directory -Path $scratchRoot

    $positiveA = Invoke-RemovalHarness -Component 0 -Name 'positive-a' -Case $fixtures.positive
    $positiveB = Invoke-RemovalHarness -Component 0 -Name 'positive-b' -Case $fixtures.positive
    if ($positiveA.Report -cne $positiveB.Report) {
        throw 'Two fresh removal-plan runs produced different reports.'
    }

    foreach ($line in @(
        'PLAN applied=7 disabled=3',
        'REMOVED 1 1 cre-exact',
        'REMOVED 1 2 are-one',
        'REMOVED 1 3 are-two',
        'REMOVED 1 4 store-stock',
        'REMOVED 1 x0 x',
        'REMOVED 1 x1 x',
        'REMOVED 1 5 stack-item',
        'REMOVED 1 y0 x',
        'REMOVED 1 y1 x',
        'REMOVED 1 6 bcs-source',
        'REMOVED 1 7 virt-explicit',
        'EXTRA 1 x0 4',
        'EXTRA 1 x1 4',
        'EXTRA 1 y0 5',
        'EXTRA 1 y1 5',
        'CHARGE 1 1 5 6 7',
        'CHARGE 2 1 1 2 3',
        'CHARGE 3 1 4 5 6',
        'CHARGE 4 1 1 0 0',
        'CHARGE 5 1 1 0 0',
        'CHARGE 6 1 2 0 0',
        'CHARGE 7 1 4 0 0',
        'BCS script.bcs'
    )) {
        Assert-ReportContainsExactlyOnce -Report $positiveA.Report -Line $line
    }
    $removedLines = @($positiveA.Report -split "`r?`n" | Where-Object { $_ -like 'REMOVED *' })
    $pluginLines = @($positiveA.Report -split "`r?`n" | Where-Object { $_ -like 'PLUGIN *' })
    if ($removedLines.Count -ne 11 -or $pluginLines.Count -ne 11) {
        throw 'Each compact token did not map to exactly one applied unit/plugin row.'
    }
    if (([System.IO.File]::ReadAllBytes((Join-Path $positiveA.RunDirectory 'override\creok.cre')) -join ',') -match '99,114,101,105,116,109') {
        throw 'CRE source still contains the removed synthetic item.'
    }
    if (([System.IO.File]::ReadAllBytes((Join-Path $positiveA.RunDirectory 'override\shopok.sto')) -join ',') -match '115,116,111,105,116,109') {
        throw 'STO source still contains the removed synthetic item.'
    }
    $positiveArea = [System.IO.File]::ReadAllBytes((Join-Path $positiveA.RunDirectory 'override\areaok.are'))
    if ([BitConverter]::ToUInt32($positiveArea, 0xcc) -ne $positiveArea.Length) {
        throw 'ARE removal did not relocate the final section pointer after deleting item records.'
    }
    $removedStatePath = Join-Path $positiveA.RunDirectory 'override\fl#removeditems.2da'
    if (-not (Test-Path -LiteralPath $removedStatePath -PathType Leaf)) {
        throw 'Mode 1 did not publish fl#removeditems.2da after applying a fresh removal plan.'
    }

    $creSlots = Invoke-RemovalHarness -Component 16 -Name 'cre-slot-relocation' -Case $fixtures.creSlotRelocation
    Assert-ReportContainsExactlyOnce -Report $creSlots.Report -Line 'PLAN applied=2 disabled=0'
    Assert-ReportContainsExactlyOnce -Report $creSlots.Report -Line 'REMOVED 1 17 cre-slots-first'
    Assert-ReportContainsExactlyOnce -Report $creSlots.Report -Line 'REMOVED 1 18 cre-slots-last'
    $creSlotsBytes = [System.IO.File]::ReadAllBytes((Join-Path $creSlots.RunDirectory 'override\slots.cre'))
    $creItemsOffset = [BitConverter]::ToUInt32($creSlotsBytes, 0x2bc)
    $creItemCount = [BitConverter]::ToUInt32($creSlotsBytes, 0x2c0)
    $creSlotsOffset = [BitConverter]::ToUInt32($creSlotsBytes, 0x2b8)
    if ($creItemCount -ne 2) {
        throw 'Multiple CRE item deletions did not preserve the two unrelated item records.'
    }
    $expectedSlotsOffset = $creItemsOffset + $creItemCount * 0x14
    if ($creSlotsOffset -ne $expectedSlotsOffset) {
        throw "CRE slot table offset does not follow the compacted item table: expected $expectedSlotsOffset, found $creSlotsOffset."
    }
    $survivingResrefs = for ($index = 0; $index -lt $creItemCount; $index++) {
        [System.Text.Encoding]::ASCII.GetString($creSlotsBytes, $creItemsOffset + 0x14 * $index, 8).Trim([char] 0)
    }
    if (($survivingResrefs -join ',') -cne 'keepone,keeptwo') {
        throw "Multiple CRE item deletions retained the wrong item records: $($survivingResrefs -join ',')."
    }
    $expectedSlotReferences = @{ 22 = 0; 24 = 1 }
    for ($slot = 0; $slot -lt 37; $slot++) {
        $slotReference = [BitConverter]::ToUInt16($creSlotsBytes, $creSlotsOffset + 2 * $slot)
        if ($expectedSlotReferences.ContainsKey($slot)) {
            if ($slotReference -ne $expectedSlotReferences[$slot]) {
                throw "Surviving CRE slot $slot references item $slotReference instead of $($expectedSlotReferences[$slot])."
            }
        }
        elseif ($slotReference -ne 0xffff) {
            throw "Removed or unused CRE slot $slot contains unexpected item reference $slotReference."
        }
        if ($slotReference -ne 0xffff -and $slotReference -ge $creItemCount) {
            throw "CRE slot $slot contains out-of-range item reference $slotReference for $creItemCount items."
        }
    }

    $sourceReplace = Invoke-RemovalHarness -Component 3 -Name 'source-replace' -Case $fixtures.sourceReplace
    Assert-ReportContainsExactlyOnce -Report $sourceReplace.Report -Line 'COUNT 1'
    Assert-ReportContainsExactlyOnce -Report $sourceReplace.Report -Line 'SOURCE movitm present.cre 1 8 moved-ident'

    $selector = Invoke-RemovalHarness -Component 11 -Name 'internal-selector' -Case $fixtures.selector
    Assert-ReportContainsExactlyOnce -Report $selector.Report -Line 'PLAN applied=1 disabled=0'
    Assert-ReportContainsExactlyOnce -Report $selector.Report -Line 'REMOVED 1 14 selector-stable'
    $selectorArea = [System.IO.File]::ReadAllBytes((Join-Path $selector.RunDirectory 'override\select.are'))
    $selectorContainerOffset = [BitConverter]::ToUInt32($selectorArea, 0x70)
    if ([BitConverter]::ToUInt32($selectorArea, $selectorContainerOffset + 0x44) -ne 1 -or
        [BitConverter]::ToUInt32($selectorArea, $selectorContainerOffset + 0xc0 + 0x44) -ne 0) {
        throw 'The internal selector did not remove only the selected duplicate source instance.'
    }

    $selectorOverride = Invoke-RemovalHarness -Component 12 -Name 'selector-extension-override' -Case $fixtures.selector
    Assert-ReportContainsExactlyOnce -Report $selectorOverride.Report -Line 'PLAN applied=1 disabled=0'
    $selectorOverrideArea = [System.IO.File]::ReadAllBytes((Join-Path $selectorOverride.RunDirectory 'override\select.are'))
    $selectorOverrideContainerOffset = [BitConverter]::ToUInt32($selectorOverrideArea, 0x70)
    if ([BitConverter]::ToUInt32($selectorOverrideArea, $selectorOverrideContainerOffset + 0x44) -ne 0 -or
        [BitConverter]::ToUInt32($selectorOverrideArea, $selectorOverrideContainerOffset + 0xc0 + 0x44) -ne 1) {
        throw 'The external source replacement did not override the internal selector default.'
    }

    $grouped = Invoke-RemovalHarness -Component 13 -Name 'all-one-unit' -Case $fixtures.ambiguous
    Assert-ReportContainsExactlyOnce -Report $grouped.Report -Line 'PLAN applied=1 disabled=0'
    Assert-ReportContainsExactlyOnce -Report $grouped.Report -Line 'REMOVED 1 1 ambiguous'
    $groupedRemoved = @($grouped.Report -split "`r?`n" | Where-Object { $_ -like 'REMOVED *' })
    $groupedPlugins = @($grouped.Report -split "`r?`n" | Where-Object { $_ -like 'PLUGIN *' })
    $groupedExtras = @($grouped.Report -split "`r?`n" | Where-Object { $_ -like 'EXTRA *' })
    if ($groupedRemoved.Count -ne 1 -or $groupedPlugins.Count -ne 1 -or $groupedExtras.Count -ne 0) {
        throw 'All-one-unit physical multiplicity did not collapse to exactly one logical unit.'
    }
    $groupedSource = [System.IO.File]::ReadAllBytes((Join-Path $grouped.RunDirectory 'override\ambig.cre'))
    $groupedSourceItemOffset = [BitConverter]::ToUInt32($groupedSource, 0x2bc)
    $groupedSourceItemCount = [BitConverter]::ToUInt32($groupedSource, 0x2c0)
    $groupedSourceResref = [System.Text.Encoding]::ASCII.GetString($groupedSource, $groupedSourceItemOffset, 8).Trim([char]0)
    if ($groupedSourceItemCount -ne 1 -or $groupedSourceResref -cne 'keepitm') {
        throw 'All-one-unit did not remove both physical matches while preserving the unrelated CRE item.'
    }
    $groupedToken = [System.IO.File]::ReadAllBytes((Join-Path $grouped.RunDirectory 'override\fltier1.cre'))
    if ([BitConverter]::ToUInt32($groupedToken, 0x2c0) -ne 1) {
        throw 'All-one-unit emitted more than one base token item.'
    }

    $null = Invoke-RemovalHarness -Component 13 -Name 'all-one-unit-count-low' -Case $fixtures.ambiguousOne -ExpectSuccess $false -ExpectedErrorCode 'MATCH_COUNT_MISMATCH'
    $null = Invoke-RemovalHarness -Component 13 -Name 'all-one-unit-count-high' -Case $fixtures.ambiguousThree -ExpectSuccess $false -ExpectedErrorCode 'MATCH_COUNT_MISMATCH'
    $null = Invoke-RemovalHarness -Component 13 -Name 'all-one-unit-signature-drift' -Case $fixtures.ambiguousSignature -ExpectSuccess $false -ExpectedErrorCode 'MATCH_SIGNATURE_MISMATCH'
    $null = Invoke-RemovalHarness -Component 13 -Name 'all-one-unit-expiration-drift' -Case $fixtures.ambiguousExpiration -ExpectSuccess $false -ExpectedErrorCode 'MATCH_SIGNATURE_MISMATCH'
    $groupedPreserve = Invoke-RemovalHarness -Component 14 -Name 'all-one-unit-preserve-extension' -Case $fixtures.ambiguous
    Assert-ReportContainsExactlyOnce -Report $groupedPreserve.Report -Line 'POLICY legacy=all_one_unit/2 current=single/1'

    $semanticFilter = Invoke-RemovalHarness -Component 9 -Name 'semantic-filter' -Case $fixtures.semanticFilter
    Assert-ReportContainsExactlyOnce -Report $semanticFilter.Report -Line 'GLOBAL_COUNT 0'
    Assert-ReportContainsExactlyOnce -Report $semanticFilter.Report -Line 'SEMANTIC_COUNT 1'
    Assert-ReportContainsExactlyOnce -Report $semanticFilter.Report -Line 'SEMANTIC_SOURCE keepok filter.cre 1 4 kept'
    foreach ($forbiddenSemanticSource in @(
        'GLOBAL_SOURCE globskp filter.cre 1 2 global-chance',
        'SEMANTIC_SOURCE optskip filter.cre s4 1 opt-gated',
        'SEMANTIC_SOURCE rowskip filter.cre 1 3 row-chance'
    )) {
        if ($semanticFilter.Report -match [regex]::Escape($forbiddenSemanticSource)) {
            throw "Override-backed fixture availability re-enabled a semantic source filter skip.`n$($semanticFilter.Report)"
        }
    }

    $sourceContent = Invoke-RemovalHarness -Component 10 -Name 'source-content' -Case $fixtures.sourceContent
    Assert-ReportContainsExactlyOnce -Report $sourceContent.Report -Line 'CONTENT_COUNT 1'
    Assert-ReportContainsExactlyOnce -Report $sourceContent.Report -Line 'CONTENT_SOURCE hitbcs hit.bcs 1 11 hit-bcs'
    foreach ($forbiddenContentSource in @(
        'CONTENT_SOURCE missbcs missing.bcs 1 9 missing-bcs',
        'CONTENT_SOURCE missdlg missing.dlg 1 10 missing-dlg'
    )) {
        if ($sourceContent.Report -match [regex]::Escape($forbiddenContentSource)) {
            throw "Override-backed script source availability bypassed source-content filtering.`n$($sourceContent.Report)"
        }
    }

    $legacyAdapter = Invoke-RemovalHarness -Component 6 -Name 'legacy-adapter' -Case $fixtures.legacyAdapter
    Assert-ReportContainsExactlyOnce -Report $legacyAdapter.Report -Line 'PLAN applied=1 disabled=0'
    Assert-ReportContainsExactlyOnce -Report $legacyAdapter.Report -Line 'REMOVED 1 11 legacy-adapter'
    Assert-ReportContainsExactlyOnce -Report $legacyAdapter.Report -Line 'CHARGE 11 1 1 0 0'
    if (([System.IO.File]::ReadAllBytes((Join-Path $legacyAdapter.RunDirectory 'override\ogreunsh.cre')) -join ',') -match '108,101,103,105,116,109') {
        throw 'Legacy adapter source still contains the removed synthetic item.'
    }

    $null = Invoke-RemovalHarness -Component 1 -Name 'ambiguous' -Case $fixtures.ambiguous -ExpectSuccess $false -ExpectedErrorCode 'AMBIGUOUS_SOURCE'
    $null = Invoke-RemovalHarness -Component 2 -Name 'drift' -Case $fixtures.drift -ExpectSuccess $false -ExpectedErrorCode 'PRECONDITION_DRIFT'
    $null = Invoke-RemovalHarness -Component 4 -Name 'blank-policy' -Case $fixtures.blankPolicy -ExpectSuccess $false -ExpectedErrorCode 'VIRTUAL_SOURCE_POLICY'
    $null = Invoke-RemovalHarness -Component 5 -Name 'capacity' -Case $fixtures.capacity -ExpectSuccess $false -ExpectedErrorCode 'TOKEN_CAPACITY' -ForbiddenLogText 'MUTATION_MARKER_REACHED'
    $capacityRecheck = Invoke-RemovalHarness -Component 15 -Name 'capacity-recheck' -Case $fixtures.semanticFilter
    Assert-ReportContainsExactlyOnce -Report $capacityRecheck.Report -Line 'CAPACITY_RECHECK_OK'
    $null = Invoke-RemovalHarness -Component 7 -Name 'script-drift' -Case $fixtures.scriptDrift -ExpectSuccess $false -ExpectedErrorCode 'PRECONDITION_DRIFT'
    $null = Invoke-RemovalHarness -Component 8 -Name 'unknown-legacy-adapter' -Case $fixtures.unknownLegacyAdapter -ExpectSuccess $false -ExpectedErrorCode 'UNKNOWN_LEGACY_ADAPTER'

    $modeBoundary = @(& powershell.exe -NoProfile -File $modeBoundaryPath 2>&1 | ForEach-Object { [string] $_ })
    $modeJoined = $modeBoundary -join "`n"
    if ($LASTEXITCODE -ne 0 -or
        $modeJoined -notmatch 'PASS Mode2_ComponentsExcludeManifestRuntimeAssets' -or
        $modeJoined -notmatch 'PASS FUTURE_Mode1_ExplicitManifestVersusLegacyDeliveryBranch' -or
        $modeJoined -match 'FAIL (?:Mode1_|Mode2_|FUTURE_)') {
        throw "Mode boundary guard did not accept the completed Task 8 seam.`n$modeJoined"
    }

    Write-Output 'PASS RemovalPlan_SourceReplaceBeforeFilter'
    Write-Output 'PASS RemovalPlan_InternalSelectorDisambiguatesAuthoredSource'
    Write-Output 'PASS RemovalPlan_ExternalSelectorReplacementOverridesInternalDefault'
    Write-Output 'PASS RemovalPlan_ExplicitAllOneUnitMultiplicity'
    Write-Output 'PASS RemovalPlan_AllOneUnitExpectedMatchCount'
    Write-Output 'PASS RemovalPlan_AllOneUnitSignatureGuard'
    Write-Output 'PASS RemovalPlan_AllOneUnitPreserveAndExtensionReset'
    Write-Output 'PASS RemovalPlan_SemanticFiltersNotFixtureAvailability'
    Write-Output 'PASS RemovalPlan_OverrideScriptSourceContentChecked'
    Write-Output 'PASS RemovalPlan_ValidationBeforeMutation'
    Write-Output 'PASS RemovalPlan_MissingAndZeroSourcesDisable'
    Write-Output 'PASS RemovalPlan_CreAreStoVirtualExactApply'
    Write-Output 'PASS RemovalPlan_CreMultiDeletePreservesSlotTable'
    Write-Output 'PASS RemovalPlan_AreFinalSectionOffsetRelocated'
    Write-Output 'PASS RemovalPlan_ExtraTokensAndCharges'
    Write-Output 'PASS RemovalPlan_DriftAndDeterministicRuns'
    Write-Output 'PASS RemovalPlan_CapacityPreflight'
    Write-Output 'PASS RemovalPlan_RepeatedCapacityValidationIdempotent'
    Write-Output 'PASS RemovalPlan_LegacyAdapterPrePost'
    Write-Output 'PASS RemovalPlan_ScriptPreconditionRecheck'
    Write-Output 'PASS RemovalPlan_Mode2BoundaryProtected'
}
finally {
    if (Test-Path -LiteralPath $scratchRoot) {
        [System.IO.Directory]::Delete($scratchRoot, $true)
    }
    if (Test-Path -LiteralPath $scratchRoot) {
        throw 'Removal-plan scratch cleanup did not complete.'
    }
}
