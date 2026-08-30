[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$productionPath = Join-Path $repositoryRoot 'lib\delivery_ground.tpa'
$harnessPath = Join-Path $PSScriptRoot 'weidu\delivery-ground-harness.tp2'
$installerPath = Join-Path $repositoryRoot 'randomiser.tp2'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath("C:\Games\Baldur's Gate II Enhanced Edition modded").TrimEnd('\', '/')

if (-not (Test-Path -LiteralPath $productionPath -PathType Leaf)) {
    Write-Output 'EXPECTED_RED DeliveryGround_ProductionApiMissing lib/delivery_ground.tpa'
    exit 1
}
if (-not (Test-Path -LiteralPath $harnessPath -PathType Leaf)) {
    throw "Required delivery-ground harness is missing: $harnessPath"
}
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Required production installer is missing: $installerPath"
}

function Get-InstallerComponentBlock {
    param(
        [Parameter(Mandatory = $true)][string] $Source,
        [Parameter(Mandatory = $true)][int] $Component
    )

    $markers = @([regex]::Matches($Source, '(?m)^BEGIN\b[^\r\n]*\bDESIGNATED\s+\d+\b'))
    $selected = @($markers | Where-Object { $_.Value -match ('\bDESIGNATED\s+' + $Component + '\b') })
    if ($selected.Count -ne 1) {
        throw "Expected exactly one installer component marker for $Component."
    }
    $start = $selected[0].Index
    $next = @($markers | Where-Object { $_.Index -gt $start } | Select-Object -First 1)
    $end = if ($next.Count -eq 1) { $next[0].Index } else { $Source.Length }
    $Source.Substring($start, $end - $start)
}

function Get-Mode1BackendBranches {
    param(
        [Parameter(Mandatory = $true)][string] $ComponentBlock,
        [Parameter(Mandatory = $true)][int] $Component
    )

    $eeexToken = 'ACTION_IF "%flir_delivery_backend%" STRING_EQUAL_CASE ~eeex-manifest-v1~ BEGIN'
    $legacyToken = 'END ELSE ACTION_IF "%flir_delivery_backend%" STRING_EQUAL_CASE ~legacy-bcs-v1~ BEGIN'
    $fallbackToken = 'END ELSE BEGIN'
    $eeexStart = $ComponentBlock.IndexOf($eeexToken, [System.StringComparison]::Ordinal)
    $legacyStart = $ComponentBlock.IndexOf($legacyToken, [System.StringComparison]::Ordinal)
    $fallbackStart = if ($legacyStart -ge 0) {
        $ComponentBlock.IndexOf($fallbackToken, $legacyStart + $legacyToken.Length, [System.StringComparison]::Ordinal)
    }
    else {
        -1
    }
    if ($eeexStart -lt 0 -or $legacyStart -lt 0 -or $fallbackStart -lt 0 -or
        $legacyStart -le $eeexStart -or $fallbackStart -le $legacyStart) {
        throw "Mode 1 component $Component does not expose the expected EEex/legacy backend branches."
    }
    [pscustomobject]@{
        Eeex = $ComponentBlock.Substring(
            $eeexStart + $eeexToken.Length,
            $legacyStart - ($eeexStart + $eeexToken.Length)
        )
        Legacy = $ComponentBlock.Substring(
            $legacyStart + $legacyToken.Length,
            $fallbackStart - ($legacyStart + $legacyToken.Length)
        )
    }
}

$productionSource = [System.IO.File]::ReadAllText($productionPath)
foreach ($requiredMacro in @(
    'flir_delivery_materialize_ground_area',
    'flir_delivery_materialize_ground_endpoints'
)) {
    if ($productionSource -notmatch ('DEFINE_(?:ACTION|PATCH)_MACRO\s+' + [regex]::Escape($requiredMacro) + '\b')) {
        throw "The production ground library does not expose '$requiredMacro'."
    }
}

foreach ($requiredGeometryWrite in @(
    'WRITE_LONG\s+\(flir_delivery_ground_container \+ 0x50\)\s+0',
    'WRITE_SHORT\s+\(flir_delivery_ground_container \+ 0x54\)\s+0'
)) {
    if ($productionSource -notmatch $requiredGeometryWrite) {
        throw 'The production ground library does not explicitly initialize the authenticated empty-pile vertex fields.'
    }
}

$installerSource = [System.IO.File]::ReadAllText($installerPath)
$groundIncludePattern = 'INCLUDE\s+["~]randomiser/lib/delivery_ground\.tpa["~]'
$groundInvokePattern = '\bLAM\s+flir_delivery_materialize_ground_endpoints\b'
foreach ($component in @(1100, 1200)) {
    $componentBlock = Get-InstallerComponentBlock -Source $installerSource -Component $component
    $branches = Get-Mode1BackendBranches -ComponentBlock $componentBlock -Component $component
    if ([regex]::Matches($branches.Eeex, $groundIncludePattern).Count -ne 1 -or
        [regex]::Matches($branches.Eeex, $groundInvokePattern).Count -ne 1) {
        throw "Mode 1 EEex component $component does not include and invoke the ground materializer exactly once."
    }
    $orderedPatterns = @(
        '\bLAM\s+flir_endpoints_lower_groups\b',
        $groundIncludePattern,
        $groundInvokePattern,
        '\bLAM\s+flir_endpoints_register_catalog_rows\b',
        '\bLAM\s+flir_registry_write_state\b',
        '\bLAM\s+flir_delivery_publish_manifest\b'
    )
    $previousPosition = -1
    foreach ($orderedPattern in $orderedPatterns) {
        $orderedMatch = [regex]::Match($branches.Eeex, $orderedPattern)
        if (-not $orderedMatch.Success -or $orderedMatch.Index -le $previousPosition) {
            throw "Mode 1 EEex component $component does not materialize ground endpoints after lowering and before publication."
        }
        $previousPosition = $orderedMatch.Index
    }
    if ($branches.Legacy -match $groundIncludePattern -or $branches.Legacy -match $groundInvokePattern) {
        throw "Mode 1 legacy component $component must not run the EEex ground materializer."
    }
}

foreach ($component in @(1300, 1400)) {
    $componentBlock = Get-InstallerComponentBlock -Source $installerSource -Component $component
    if ($componentBlock -match $groundIncludePattern -or $componentBlock -match $groundInvokePattern) {
        throw "Mode 2 component $component must not include or run the EEex ground materializer."
    }
}

if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe'
}
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}

function Resolve-LocalPathOutsideLiveRoot {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][ValidateSet('Leaf', 'Container')][string] $PathType
    )

    if ($Path.StartsWith('\\') -or $Path.StartsWith('//') -or $Path.IndexOf('~') -ge 0) {
        throw 'Delivery-ground tests accept only long local drive-letter paths.'
    }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($full -notmatch '^[A-Za-z]:\\') {
        throw 'Delivery-ground tests accept only local drive-letter paths.'
    }
    if ($full.Equals($script:forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($script:forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to use a delivery-ground test path under the forbidden live game root.'
    }
    if ($PathType -eq 'Leaf' -and -not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw 'The configured WeiDU executable does not exist.'
    }
    if ($PathType -eq 'Container' -and -not (Test-Path -LiteralPath $full -PathType Container)) {
        throw 'The configured disposable temporary root does not exist.'
    }
    $full
}

$resolvedWeidu = Resolve-LocalPathOutsideLiveRoot -Path $WeiduPath -PathType Leaf
$resolvedTempRoot = Resolve-LocalPathOutsideLiveRoot -Path $TempRoot -PathType Container
$scratchRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedTempRoot ('bgee-itemrandomiser-delivery-ground-' + [guid]::NewGuid().ToString('N'))))
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The generated delivery-ground scratch directory escaped its disposable parent.'
}

function Set-U16 {
    param([byte[]] $Bytes, [int] $Offset, [uint16] $Value)
    [System.BitConverter]::GetBytes($Value).CopyTo($Bytes, $Offset)
}

function Set-U32 {
    param([byte[]] $Bytes, [int] $Offset, [uint32] $Value)
    [System.BitConverter]::GetBytes($Value).CopyTo($Bytes, $Offset)
}

function Get-U16 {
    param([byte[]] $Bytes, [int] $Offset)
    [System.BitConverter]::ToUInt16($Bytes, $Offset)
}

function Get-U32 {
    param([byte[]] $Bytes, [int] $Offset)
    [System.BitConverter]::ToUInt32($Bytes, $Offset)
}

function Set-Ascii {
    param([byte[]] $Bytes, [int] $Offset, [int] $Length, [string] $Value)
    [Array]::Clear($Bytes, $Offset, $Length)
    $encoded = [System.Text.Encoding]::ASCII.GetBytes($Value)
    if ($encoded.Length -gt $Length) {
        throw 'Synthetic binary field overflowed.'
    }
    $encoded.CopyTo($Bytes, $Offset)
}

function Get-Ascii {
    param([byte[]] $Bytes, [int] $Offset, [int] $Length)
    ([System.Text.Encoding]::ASCII.GetString($Bytes, $Offset, $Length)).TrimEnd([char] 0)
}

function New-AreFixture {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object[]] $Containers
    )

    $containerOffset = 0x120
    $allItems = [System.Collections.Generic.List[string]]::new()
    foreach ($container in $Containers) {
        foreach ($item in @($container.Items)) {
            $allItems.Add([string] $item)
        }
    }
    $itemOffset = $containerOffset + 0xc0 * $Containers.Count
    $vertexOffset = $itemOffset + 0x14 * $allItems.Count
    $sentinel = [System.Text.Encoding]::ASCII.GetBytes('VERTEX-SENTINEL!')
    $bytes = [byte[]]::new($vertexOffset + $sentinel.Length)

    Set-Ascii $bytes 0 8 'AREAV1.0'
    Set-U32 $bytes 0x70 $containerOffset
    Set-U16 $bytes 0x74 $Containers.Count
    Set-U16 $bytes 0x76 $allItems.Count
    Set-U32 $bytes 0x78 $itemOffset
    Set-U32 $bytes 0x7c $vertexOffset
    Set-U32 $bytes 0xbc $vertexOffset
    Set-U32 $bytes 0xc0 $vertexOffset
    Set-U32 $bytes 0xc4 $vertexOffset

    $itemIndex = 0
    for ($index = 0; $index -lt $Containers.Count; ++$index) {
        $container = $Containers[$index]
        $offset = $containerOffset + 0xc0 * $index
        Set-Ascii $bytes ($offset + 0x00) 32 ([string] $container.Name)
        Set-U16 $bytes ($offset + 0x20) ([uint16] $container.X)
        Set-U16 $bytes ($offset + 0x22) ([uint16] $container.Y)
        Set-U16 $bytes ($offset + 0x24) ([uint16] $container.Type)
        Set-U32 $bytes ($offset + 0x40) $itemIndex
        Set-U32 $bytes ($offset + 0x44) @($container.Items).Count
        foreach ($item in @($container.Items)) {
            Set-Ascii $bytes ($itemOffset + 0x14 * $itemIndex) 8 ([string] $item)
            Set-U32 $bytes ($itemOffset + 0x14 * $itemIndex + 0x10) 1
            ++$itemIndex
        }
    }
    $sentinel.CopyTo($bytes, $vertexOffset)
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-MixedRelocationAreFixture {
    param([Parameter(Mandatory = $true)][string] $Path)

    # This layout intentionally does not follow header-field order. The
    # container table ends before an actor table, while one embedded CRE is
    # before the insertion and another is after it. That makes the fixture an
    # independent relocation oracle instead of a mirror of the patcher's list.
    $earlyCreOffset = 0x120
    $containerOffset = 0x160
    $actorOffset = 0x230
    $itemOffset = 0x570
    $vertexOffset = 0x590
    $lateCreOffset = 0x5a0
    $tiledFlagsOffset = 0x5d0
    $projectileOffset = 0x5e0
    $bytes = [byte[]]::new(0x680)

    Set-Ascii $bytes 0 8 'AREAV1.0'
    Set-U32 $bytes 0x54 $actorOffset
    Set-U16 $bytes 0x58 3
    Set-U32 $bytes 0x70 $containerOffset
    Set-U16 $bytes 0x74 1
    Set-U16 $bytes 0x76 1
    Set-U32 $bytes 0x78 $itemOffset
    Set-U32 $bytes 0x7c $vertexOffset
    Set-U16 $bytes 0x80 1
    Set-U16 $bytes 0x90 $tiledFlagsOffset
    Set-U16 $bytes 0x92 1
    Set-U32 $bytes 0xcc $projectileOffset
    Set-U32 $bytes 0xd0 1

    Set-Ascii $bytes $earlyCreOffset 0x20 'EARLY-CRE-BEFORE-INSERT'
    Set-Ascii $bytes $containerOffset 32 'mixed-unrelated'
    Set-U16 $bytes ($containerOffset + 0x20) 77
    Set-U16 $bytes ($containerOffset + 0x22) 88
    Set-U16 $bytes ($containerOffset + 0x24) 8
    Set-U32 $bytes ($containerOffset + 0x40) 0
    Set-U32 $bytes ($containerOffset + 0x44) 1

    for ($index = 0; $index -lt 3; ++$index) {
        $record = $actorOffset + $index * 0x110
        Set-Ascii $bytes $record 32 ("mixed-actor-$index")
        Set-Ascii $bytes ($record + 0x80) 8 ("MXACTR$index")
    }
    # External actor: no embedded payload.
    Set-U32 $bytes ($actorOffset + 0x88) 0
    Set-U32 $bytes ($actorOffset + 0x8c) 0
    # Embedded payload before insertion: the pointer must remain unchanged.
    Set-U32 $bytes ($actorOffset + 0x110 + 0x88) $earlyCreOffset
    Set-U32 $bytes ($actorOffset + 0x110 + 0x8c) 0x20
    # Embedded payload after insertion: the pointer must move with the bytes.
    Set-U32 $bytes ($actorOffset + 0x220 + 0x88) $lateCreOffset
    Set-U32 $bytes ($actorOffset + 0x220 + 0x8c) 0x20

    Set-Ascii $bytes $itemOffset 8 'MIXITEM1'
    Set-U32 $bytes ($itemOffset + 0x10) 1
    Set-Ascii $bytes $vertexOffset 0x10 'MIXED-VERTEX'
    Set-Ascii $bytes $lateCreOffset 0x20 'LATE-CRE-AFTER-INSERT'
    Set-U16 $bytes $tiledFlagsOffset 0x5a5a
    Set-Ascii $bytes $projectileOffset 0x90 'PROJECTILE-TRAP-SENTINEL'
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-TiledFlagsOverflowAreFixture {
    param([Parameter(Mandatory = $true)][string] $Path)

    $containerOffset = 0x120
    $tiledFlagsOffset = 0xff80
    $bytes = [byte[]]::new(0xff90)
    Set-Ascii $bytes 0 8 'AREAV1.0'
    Set-U32 $bytes 0x70 $containerOffset
    Set-U16 $bytes 0x74 1
    Set-U16 $bytes 0x90 $tiledFlagsOffset
    Set-U16 $bytes 0x92 1
    Set-Ascii $bytes $containerOffset 32 'overflow-unrelated'
    Set-U16 $bytes ($containerOffset + 0x20) 99
    Set-U16 $bytes ($containerOffset + 0x22) 111
    Set-U16 $bytes ($containerOffset + 0x24) 8
    Set-U16 $bytes $tiledFlagsOffset 0x6b6b
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-StraddlingEmbeddedCreAreFixture {
    param([Parameter(Mandatory = $true)][string] $Path)

    New-MixedRelocationAreFixture -Path $Path
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $actorOffset = Get-U32 $bytes 0x54
    $insertionOffset = (Get-U32 $bytes 0x70) + (Get-U16 $bytes 0x74) * 0xc0
    Set-U32 $bytes ($actorOffset + 0x110 + 0x88) ($insertionOffset - 0x10)
    Set-U32 $bytes ($actorOffset + 0x110 + 0x8c) 0x20
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-FakeGame {
    param([Parameter(Mandatory = $true)][string] $Name)

    $root = Join-Path $script:scratchRoot $Name
    $override = Join-Path $root 'override'
    $language = Join-Path $root 'lang\en_US'
    $null = New-Item -ItemType Directory -Path $override -Force
    $null = New-Item -ItemType Directory -Path $language -Force

    $key = [byte[]]::new(0x18)
    Set-Ascii $key 0 8 'KEY V1  '
    Set-U32 $key 0x10 0x18
    Set-U32 $key 0x14 0x18
    [System.IO.File]::WriteAllBytes((Join-Path $root 'chitin.key'), $key)

    $tlk = [byte[]]::new(0x2c)
    Set-Ascii $tlk 0 8 'TLK V1  '
    Set-U32 $tlk 0x0a 1
    Set-U32 $tlk 0x0e 0x2c
    [System.IO.File]::WriteAllBytes((Join-Path $root 'dialog.tlk'), $tlk)
    [System.IO.File]::WriteAllBytes((Join-Path $language 'dialog.tlk'), $tlk)
    $root
}

function Invoke-WeiDUComponent {
    param(
        [Parameter(Mandatory = $true)][string] $GameRoot,
        [Parameter(Mandatory = $true)][int] $Component
    )

    $arguments = @(
        $script:harnessPath,
        '--game', $GameRoot,
        '--force-install-list', [string] $Component,
        '--args', $script:productionPath,
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
    $debugText = @(
        Get-ChildItem -LiteralPath $GameRoot -Filter '*.DEBUG' -File -ErrorAction SilentlyContinue |
            ForEach-Object { [System.IO.File]::ReadAllText($_.FullName) }
    ) -join "`n"
    [pscustomobject]@{
        ExitCode = $exitCode
        Text = (($output -join "`n") + $(if ([string]::IsNullOrWhiteSpace($debugText)) { '' } else { "`nDEBUG:`n$debugText" }))
    }
}

function Assert-WeiDUSuccess {
    param([psobject] $Result, [string] $Case)
    if ($Result.ExitCode -ne 0 -or $Result.Text -match 'NOT INSTALLED DUE TO ERRORS') {
        throw "Delivery-ground case '$Case' failed unexpectedly.`n$($Result.Text)"
    }
}

function Assert-WeiDUFailure {
    param([psobject] $Result, [string] $Code, [string] $Case)
    if ($Result.ExitCode -eq 0 -and $Result.Text -notmatch 'NOT INSTALLED DUE TO ERRORS') {
        throw "Delivery-ground case '$Case' succeeded but should have failed."
    }
    if ($Result.Text -notmatch ('FLIR_DELIVERY_ERR\s+' + [regex]::Escape($Code) + '\b')) {
        throw "Delivery-ground case '$Case' did not report $Code.`n$($Result.Text)"
    }
}

function Get-MatchingPiles {
    param([byte[]] $Bytes, [int] $X, [int] $Y)

    $containerOffset = Get-U32 $Bytes 0x70
    $containerCount = Get-U16 $Bytes 0x74
    @(
        for ($index = 0; $index -lt $containerCount; ++$index) {
            $offset = $containerOffset + 0xc0 * $index
            if ((Get-U16 $Bytes ($offset + 0x24)) -eq 4 -and
                (Get-U16 $Bytes ($offset + 0x20)) -eq $X -and
                (Get-U16 $Bytes ($offset + 0x22)) -eq $Y) {
                [pscustomobject]@{
                    Offset = $offset
                    Name = Get-Ascii $Bytes $offset 32
                    FirstItem = Get-U32 $Bytes ($offset + 0x40)
                    ItemCount = Get-U32 $Bytes ($offset + 0x44)
                }
            }
        }
    )
}

function Assert-ByteSequenceEqual {
    param([byte[]] $Actual, [byte[]] $Expected, [string] $Message)
    if ($Actual.Length -ne $Expected.Length -or
        -not [System.Linq.Enumerable]::SequenceEqual([byte[]] $Actual, [byte[]] $Expected)) {
        throw $Message
    }
}

try {
    $null = New-Item -ItemType Directory -Path $scratchRoot

    foreach ($parse in @(
        @{ Type = 'TPA'; Path = $productionPath },
        @{ Type = 'TP2'; Path = $harnessPath }
    )) {
        Push-Location $scratchRoot
        try {
            $parseOutput = @(& $resolvedWeidu --nogame --parse-check $parse.Type $parse.Path 2>&1)
            $parseExitCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        if ($parseExitCode -ne 0) {
            throw "WeiDU parse-check failed for $($parse.Path).`n$($parseOutput -join "`n")"
        }
    }

    $baseGame = New-FakeGame -Name 'base'
    $baseAreaPath = Join-Path $baseGame 'override\FLGRD01.ARE'
    New-AreFixture -Path $baseAreaPath -Containers @(
        [pscustomobject]@{ Name = 'unrelated'; X = 50; Y = 60; Type = 8; Items = @('TSTITM01') },
        [pscustomobject]@{ Name = 'reuse-me'; X = 300; Y = 400; Type = 4; Items = @() }
    )
    $beforeBase = [System.IO.File]::ReadAllBytes($baseAreaPath)
    $beforeItemOffset = Get-U32 $beforeBase 0x78
    $beforeItemRecord = [byte[]]::new(0x14)
    [Array]::Copy($beforeBase, $beforeItemOffset, $beforeItemRecord, 0, 0x14)
    $beforeReuse = [byte[]]::new(0xc0)
    [Array]::Copy($beforeBase, (Get-U32 $beforeBase 0x70) + 0xc0, $beforeReuse, 0, 0xc0)

    $baseInstall = Invoke-WeiDUComponent -GameRoot $baseGame -Component 0
    Assert-WeiDUSuccess -Result $baseInstall -Case 'append-reuse-alias-disabled'
    $afterBase = [System.IO.File]::ReadAllBytes($baseAreaPath)
    $afterContainerCount = Get-U16 $afterBase 0x74
    $afterItemCount = Get-U16 $afterBase 0x76
    if ($afterContainerCount -ne 3 -or $afterItemCount -ne 1) {
        $overrideFiles = @(Get-ChildItem -LiteralPath (Join-Path $baseGame 'override') -File | ForEach-Object { "$($_.Name):$($_.Length)" }) -join ', '
        throw "The base case did not append exactly one empty container while preserving the item table count (containers=$afterContainerCount items=$afterItemCount files=$overrideFiles).`n$($baseInstall.Text)"
    }
    $newPiles = @(Get-MatchingPiles -Bytes $afterBase -X 100 -Y 200)
    $reusedPiles = @(Get-MatchingPiles -Bytes $afterBase -X 300 -Y 400)
    if ($newPiles.Count -ne 1 -or $newPiles[0].ItemCount -ne 0 -or $newPiles[0].FirstItem -ne 1 -or
        $newPiles[0].Name -cne 'FLDLV_100_200') {
        throw 'The missing endpoint was not materialized as one deterministic empty type-4 pile.'
    }
    $newPileOffset = $newPiles[0].Offset
    if ((Get-U16 $afterBase ($newPileOffset + 0x34)) -ne 100 -or
        (Get-U16 $afterBase ($newPileOffset + 0x36)) -ne 200 -or
        (Get-U16 $afterBase ($newPileOffset + 0x38)) -ne 84 -or
        (Get-U16 $afterBase ($newPileOffset + 0x3a)) -ne 184 -or
        (Get-U16 $afterBase ($newPileOffset + 0x3c)) -ne 116 -or
        (Get-U16 $afterBase ($newPileOffset + 0x3e)) -ne 216 -or
        (Get-U32 $afterBase ($newPileOffset + 0x50)) -ne 0 -or
        (Get-U16 $afterBase ($newPileOffset + 0x54)) -ne 0) {
        throw 'The new empty pile does not match the authenticated launch-point, bounding-box, and zero-vertex geometry.'
    }
    if ($reusedPiles.Count -ne 1 -or $reusedPiles[0].ItemCount -ne 0) {
        throw 'The existing empty type-4 pile was not reused exactly once.'
    }
    $afterReuse = [byte[]]::new(0xc0)
    [Array]::Copy($afterBase, (Get-U32 $afterBase 0x70) + 0xc0, $afterReuse, 0, 0xc0)
    Assert-ByteSequenceEqual -Actual $afterReuse -Expected $beforeReuse -Message 'Reusing an existing empty pile changed its bytes.'

    $afterItemOffset = Get-U32 $afterBase 0x78
    if ($afterItemOffset -ne $beforeItemOffset + 0xc0 -or (Get-U32 $afterBase 0x7c) -ne (Get-U32 $beforeBase 0x7c) + 0xc0) {
        throw 'Appending a ground pile did not update later ARE table offsets exactly once.'
    }
    $afterItemRecord = [byte[]]::new(0x14)
    [Array]::Copy($afterBase, $afterItemOffset, $afterItemRecord, 0, 0x14)
    Assert-ByteSequenceEqual -Actual $afterItemRecord -Expected $beforeItemRecord -Message 'Appending a ground pile changed an existing area item record.'
    if ((Get-Ascii $afterBase (Get-U32 $afterBase 0x7c) 16) -cne 'VERTEX-SENTINEL!') {
        throw 'Appending a ground pile did not preserve bytes in a shifted later table.'
    }

    $hashAfterFirst = (Get-FileHash -LiteralPath $baseAreaPath -Algorithm SHA256).Hash
    Assert-WeiDUSuccess -Result (Invoke-WeiDUComponent -GameRoot $baseGame -Component 1) -Case 'idempotent-repeat'
    $hashAfterSecond = (Get-FileHash -LiteralPath $baseAreaPath -Algorithm SHA256).Hash
    if ($hashAfterSecond -cne $hashAfterFirst) {
        throw 'The second materialization run was not byte-identical.'
    }

    $ambiguousGame = New-FakeGame -Name 'ambiguous'
    $ambiguousAreaPath = Join-Path $ambiguousGame 'override\FLGRD02.ARE'
    New-AreFixture -Path $ambiguousAreaPath -Containers @(
        [pscustomobject]@{ Name = 'pile-a'; X = 700; Y = 800; Type = 4; Items = @() },
        [pscustomobject]@{ Name = 'pile-b'; X = 700; Y = 800; Type = 4; Items = @() }
    )
    $ambiguousBefore = (Get-FileHash -LiteralPath $ambiguousAreaPath -Algorithm SHA256).Hash
    Assert-WeiDUFailure -Result (Invoke-WeiDUComponent -GameRoot $ambiguousGame -Component 2) -Code 'GROUND_AMBIGUOUS' -Case 'ambiguous'
    if ((Get-FileHash -LiteralPath $ambiguousAreaPath -Algorithm SHA256).Hash -cne $ambiguousBefore) {
        throw 'The ambiguous failure changed its area fixture.'
    }

    $occupiedGame = New-FakeGame -Name 'occupied'
    $occupiedAreaPath = Join-Path $occupiedGame 'override\FLGRD03.ARE'
    New-AreFixture -Path $occupiedAreaPath -Containers @(
        [pscustomobject]@{ Name = 'occupied'; X = 900; Y = 1000; Type = 4; Items = @('TSTITM02') }
    )
    $occupiedBefore = (Get-FileHash -LiteralPath $occupiedAreaPath -Algorithm SHA256).Hash
    Assert-WeiDUSuccess -Result (Invoke-WeiDUComponent -GameRoot $occupiedGame -Component 3) -Case 'occupied-reuse'
    if ((Get-FileHash -LiteralPath $occupiedAreaPath -Algorithm SHA256).Hash -cne $occupiedBefore) {
        throw 'Reusing an occupied pile changed its area fixture.'
    }
    $occupiedPiles = Get-MatchingPiles -Bytes ([System.IO.File]::ReadAllBytes($occupiedAreaPath)) -X 900 -Y 1000
    if ($occupiedPiles.Count -ne 1 -or $occupiedPiles[0].ItemCount -ne 1) {
        throw 'The unique occupied pile or its existing item was not preserved.'
    }

    $missingGame = New-FakeGame -Name 'missing'
    $validAreaPath = Join-Path $missingGame 'override\FLGRD04.ARE'
    New-AreFixture -Path $validAreaPath -Containers @(
        [pscustomobject]@{ Name = 'unrelated'; X = 10; Y = 20; Type = 8; Items = @() }
    )
    $validBefore = (Get-FileHash -LiteralPath $validAreaPath -Algorithm SHA256).Hash
    Assert-WeiDUFailure -Result (Invoke-WeiDUComponent -GameRoot $missingGame -Component 4) -Code 'GROUND_MISSING_AREA' -Case 'missing-preflight'
    if ((Get-FileHash -LiteralPath $validAreaPath -Algorithm SHA256).Hash -cne $validBefore) {
        throw 'A missing later area allowed an earlier valid area to be patched.'
    }

    $ambiguousRollbackGame = New-FakeGame -Name 'ambiguous-rollback'
    $validBeforeAmbiguousPath = Join-Path $ambiguousRollbackGame 'override\FLGRD05.ARE'
    $lateAmbiguousPath = Join-Path $ambiguousRollbackGame 'override\FLGRD06.ARE'
    New-AreFixture -Path $validBeforeAmbiguousPath -Containers @(
        [pscustomobject]@{ Name = 'unrelated'; X = 10; Y = 20; Type = 8; Items = @() }
    )
    New-AreFixture -Path $lateAmbiguousPath -Containers @(
        [pscustomobject]@{ Name = 'pile-a'; X = 1700; Y = 1800; Type = 4; Items = @() },
        [pscustomobject]@{ Name = 'pile-b'; X = 1700; Y = 1800; Type = 4; Items = @() }
    )
    $validBeforeAmbiguousHash = (Get-FileHash -LiteralPath $validBeforeAmbiguousPath -Algorithm SHA256).Hash
    $lateAmbiguousHash = (Get-FileHash -LiteralPath $lateAmbiguousPath -Algorithm SHA256).Hash
    Assert-WeiDUFailure -Result (Invoke-WeiDUComponent -GameRoot $ambiguousRollbackGame -Component 5) -Code 'GROUND_AMBIGUOUS' -Case 'ambiguous-component-rollback'
    if ((Get-FileHash -LiteralPath $validBeforeAmbiguousPath -Algorithm SHA256).Hash -cne $validBeforeAmbiguousHash -or
        (Get-FileHash -LiteralPath $lateAmbiguousPath -Algorithm SHA256).Hash -cne $lateAmbiguousHash) {
        throw 'A later ambiguous area was not rolled back transactionally with the earlier patched area.'
    }

    $occupiedRollbackGame = New-FakeGame -Name 'occupied-rollback'
    $validBeforeOccupiedPath = Join-Path $occupiedRollbackGame 'override\FLGRD07.ARE'
    $lateOccupiedPath = Join-Path $occupiedRollbackGame 'override\FLGRD08.ARE'
    New-AreFixture -Path $validBeforeOccupiedPath -Containers @(
        [pscustomobject]@{ Name = 'unrelated'; X = 10; Y = 20; Type = 8; Items = @() }
    )
    New-AreFixture -Path $lateOccupiedPath -Containers @(
        [pscustomobject]@{ Name = 'occupied'; X = 2100; Y = 2200; Type = 4; Items = @('TSTITM03') }
    )
    $validBeforeOccupiedHash = (Get-FileHash -LiteralPath $validBeforeOccupiedPath -Algorithm SHA256).Hash
    $lateOccupiedHash = (Get-FileHash -LiteralPath $lateOccupiedPath -Algorithm SHA256).Hash
    Assert-WeiDUSuccess -Result (Invoke-WeiDUComponent -GameRoot $occupiedRollbackGame -Component 6) -Case 'later-occupied-reuse'
    if ((Get-FileHash -LiteralPath $validBeforeOccupiedPath -Algorithm SHA256).Hash -ceq $validBeforeOccupiedHash) {
        throw 'Reusing a later occupied pile did not materialize the earlier missing pile.'
    }
    $validBeforeOccupiedBytes = [System.IO.File]::ReadAllBytes($validBeforeOccupiedPath)
    $earlyNewPiles = Get-MatchingPiles -Bytes $validBeforeOccupiedBytes -X 1900 -Y 2000
    if ((Get-U16 $validBeforeOccupiedBytes 0x74) -ne 2 -or
        $earlyNewPiles.Count -ne 1 -or $earlyNewPiles[0].ItemCount -ne 0) {
        throw 'Reusing a later occupied pile did not append exactly one earlier empty pile.'
    }
    if ((Get-FileHash -LiteralPath $lateOccupiedPath -Algorithm SHA256).Hash -cne $lateOccupiedHash) {
        throw 'Reusing a later occupied pile changed that area or its existing contents.'
    }
    $lateOccupiedPiles = Get-MatchingPiles -Bytes ([System.IO.File]::ReadAllBytes($lateOccupiedPath)) -X 2100 -Y 2200
    if ($lateOccupiedPiles.Count -ne 1 -or $lateOccupiedPiles[0].ItemCount -ne 1) {
        throw 'The later occupied pile or its existing item was not preserved.'
    }

    $overflowGame = New-FakeGame -Name 'word-offset-overflow'
    $overflowAreaPath = Join-Path $overflowGame 'override\FLGRD10.ARE'
    New-TiledFlagsOverflowAreFixture -Path $overflowAreaPath
    $overflowBefore = [System.IO.File]::ReadAllBytes($overflowAreaPath)
    Assert-WeiDUFailure -Result (Invoke-WeiDUComponent -GameRoot $overflowGame -Component 8) -Code 'GROUND_OFFSET_OVERFLOW' -Case 'tiled-flags-word-offset-overflow'
    $overflowAfter = [System.IO.File]::ReadAllBytes($overflowAreaPath)
    Assert-ByteSequenceEqual -Actual $overflowAfter -Expected $overflowBefore -Message 'A tiled-object flags word-offset overflow changed its area fixture.'

    $straddleGame = New-FakeGame -Name 'embedded-cre-straddle'
    $straddleAreaPath = Join-Path $straddleGame 'override\FLGRD11.ARE'
    New-StraddlingEmbeddedCreAreFixture -Path $straddleAreaPath
    $straddleBefore = [System.IO.File]::ReadAllBytes($straddleAreaPath)
    Assert-WeiDUFailure -Result (Invoke-WeiDUComponent -GameRoot $straddleGame -Component 10) -Code 'GROUND_INVALID_ARE' -Case 'embedded-cre-straddles-insertion'
    $straddleAfter = [System.IO.File]::ReadAllBytes($straddleAreaPath)
    Assert-ByteSequenceEqual -Actual $straddleAfter -Expected $straddleBefore -Message 'A split embedded-CRE failure changed its area fixture.'

    $mixedGame = New-FakeGame -Name 'mixed-relocation'
    $mixedAreaPath = Join-Path $mixedGame 'override\FLGRD09.ARE'
    New-MixedRelocationAreFixture -Path $mixedAreaPath
    $mixedBefore = [System.IO.File]::ReadAllBytes($mixedAreaPath)
    $mixedActorBefore = Get-U32 $mixedBefore 0x54
    $mixedInsertOffset = (Get-U32 $mixedBefore 0x70) + (Get-U16 $mixedBefore 0x74) * 0xc0
    $mixedEarlyCreBefore = Get-U32 $mixedBefore ($mixedActorBefore + 0x110 + 0x88)
    $mixedLateCreBefore = Get-U32 $mixedBefore ($mixedActorBefore + 0x220 + 0x88)
    $mixedTiledFlagsBefore = Get-U16 $mixedBefore 0x90
    $mixedProjectileBefore = Get-U32 $mixedBefore 0xcc
    $mixedItemBefore = Get-U32 $mixedBefore 0x78
    $mixedVertexBefore = Get-U32 $mixedBefore 0x7c

    Assert-WeiDUSuccess -Result (Invoke-WeiDUComponent -GameRoot $mixedGame -Component 7) -Case 'mixed-section-relocation'
    $mixedAfter = [System.IO.File]::ReadAllBytes($mixedAreaPath)
    $relocationDelta = 0xc0
    $mixedActorAfter = Get-U32 $mixedAfter 0x54
    if ($mixedActorAfter -ne $mixedActorBefore + $relocationDelta -or
        (Get-U32 $mixedAfter 0xcc) -ne $mixedProjectileBefore + $relocationDelta -or
        (Get-U16 $mixedAfter 0x90) -ne $mixedTiledFlagsBefore + $relocationDelta -or
        (Get-U32 $mixedAfter 0x78) -ne $mixedItemBefore + $relocationDelta -or
        (Get-U32 $mixedAfter 0x7c) -ne $mixedVertexBefore + $relocationDelta) {
        throw 'Mixed ARE header pointers were not relocated using their declared field widths.'
    }
    if ((Get-U32 $mixedAfter ($mixedActorAfter + 0x88)) -ne 0 -or
        (Get-U32 $mixedAfter ($mixedActorAfter + 0x110 + 0x88)) -ne $mixedEarlyCreBefore -or
        (Get-U32 $mixedAfter ($mixedActorAfter + 0x220 + 0x88)) -ne $mixedLateCreBefore + $relocationDelta) {
        throw 'Embedded actor CRE pointers did not preserve zero/before-insertion values and relocate the later payload.'
    }
    if ((Get-Ascii $mixedAfter $mixedEarlyCreBefore 0x20) -cne 'EARLY-CRE-BEFORE-INSERT' -or
        (Get-Ascii $mixedAfter ($mixedLateCreBefore + $relocationDelta) 0x20) -cne 'LATE-CRE-AFTER-INSERT' -or
        (Get-Ascii $mixedAfter ($mixedItemBefore + $relocationDelta) 8) -cne 'MIXITEM1' -or
        (Get-Ascii $mixedAfter ($mixedVertexBefore + $relocationDelta) 0x10) -cne 'MIXED-VERTEX' -or
        (Get-U16 $mixedAfter ($mixedTiledFlagsBefore + $relocationDelta)) -ne 0x5a5a -or
        (Get-Ascii $mixedAfter ($mixedProjectileBefore + $relocationDelta) 0x90) -cne 'PROJECTILE-TRAP-SENTINEL') {
        throw 'Mixed ARE section payload bytes did not follow their relocated pointers.'
    }
    $mixedPiles = @(Get-MatchingPiles -Bytes $mixedAfter -X 2300 -Y 2400)
    if ($mixedPiles.Count -ne 1 -or $mixedPiles[0].Offset -ne $mixedInsertOffset -or $mixedPiles[0].ItemCount -ne 0) {
        throw 'Mixed ARE materialization did not place exactly one empty pile at the insertion boundary.'
    }
    $mixedHashAfterFirst = (Get-FileHash -LiteralPath $mixedAreaPath -Algorithm SHA256).Hash
    Assert-WeiDUSuccess -Result (Invoke-WeiDUComponent -GameRoot $mixedGame -Component 9) -Case 'mixed-section-idempotent-repeat'
    if ((Get-FileHash -LiteralPath $mixedAreaPath -Algorithm SHA256).Hash -cne $mixedHashAfterFirst) {
        throw 'Mixed ARE materialization was not byte-identical on repeat.'
    }

    Write-Output 'Delivery ground materializer tests passed (installer seam, append, mixed-section relocation, embedded CREs, width overflow, empty and occupied reuse, alias, disabled, idempotent, failures, missing-area preflight, component rollback).'
}
finally {
    if (Test-Path -LiteralPath $scratchRoot -PathType Container) {
        $verifiedScratch = [System.IO.Path]::GetFullPath($scratchRoot)
        if (-not $verifiedScratch.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
            [System.IO.Path]::GetFileName($verifiedScratch) -notmatch '^bgee-itemrandomiser-delivery-ground-[0-9a-f]{32}$') {
            throw 'Refusing to clean an unverified delivery-ground scratch directory.'
        }
        Remove-Item -LiteralPath $verifiedScratch -Recurse -Force
    }
}
