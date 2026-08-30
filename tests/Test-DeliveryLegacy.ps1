[CmdletBinding()]
param(
    [string] $WeiduPath = $env:FL_IR_TEST_WEIDU,
    [string] $TempRoot = $env:FL_IR_TEST_TEMP_ROOT,
    [ValidateSet('All', 'Special')]
    [string] $Case = 'All'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$specialPath = Join-Path $repositoryRoot 'lib\delivery_special.tpa'
$neutralizePath = Join-Path $repositoryRoot 'lib\delivery_neutralize.tpa'
$harnessPath = Join-Path $PSScriptRoot 'weidu\delivery-legacy-harness.tp2'
$fixtureRoot = Join-Path $PSScriptRoot 'fixtures\delivery'
$forbiddenLiveRoot = [System.IO.Path]::GetFullPath('C:\Games\Baldur''s Gate II Enhanced Edition modded')

foreach ($requiredFixture in @(
    'pool-original.baf',
    'book-original.baf',
    'altar-original.baf',
    'assignment-original.baf',
    'delivery-test.tra'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot $requiredFixture) -PathType Leaf)) {
        throw "Delivery fixture '$requiredFixture' is missing."
    }
}
if (-not (Test-Path -LiteralPath $harnessPath -PathType Leaf)) {
    throw 'The delivery legacy WeiDU harness is missing.'
}

$missingProduction = @()
foreach ($productionPath in @($specialPath, $neutralizePath)) {
    if (-not (Test-Path -LiteralPath $productionPath -PathType Leaf)) {
        $missingProduction += [System.IO.Path]::GetFileName($productionPath)
    }
}
if ($missingProduction.Count -ne 0) {
    Write-Output "EXPECTED_RED DeliveryLegacy_ProductionApiMissing $($missingProduction -join ',')"
    exit 1
}

$specialSource = [System.IO.File]::ReadAllText($specialPath)
$neutralizeSource = [System.IO.File]::ReadAllText($neutralizePath)
if ($specialSource -notmatch '(?im)^\s*DEFINE_ACTION_MACRO\s+flir_delivery_special_apply\b') {
    throw 'delivery_special.tpa does not expose flir_delivery_special_apply.'
}
if ($neutralizeSource -notmatch '(?im)^\s*DEFINE_ACTION_MACRO\s+flir_delivery_neutralize_historical_actors\b') {
    throw 'delivery_neutralize.tpa does not expose flir_delivery_neutralize_historical_actors.'
}

# These are deliberately semantic guards, not a substitute for the dynamic
# decompile assertions below. They make an accidentally omitted adapter fail
# with a useful diagnostic before WeiDU builds the synthetic fixture game.
$specialContracts = @(
    @{ Name = 'pool'; Target = 'blpool.bcs'; Baf = 'flpool'; Trigger = 'Global\(\\"Pool\\",\\"AR0801\\",1\)' },
    @{ Name = 'book'; Target = 'ppbook.bcs'; Baf = 'flbook'; Trigger = 'Global\(\\"page\\",\\"AR1513\\",5\)' },
    @{ Name = 'altar'; Target = 'troalt.bcs'; Baf = 'fltroalt'; Trigger = 'Contains\(\\"MISC9D\\",Myself\)' }
)
foreach ($contract in $specialContracts) {
    if ($specialSource.IndexOf($contract.Target, [System.StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $specialSource.IndexOf($contract.Baf, [System.StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $specialSource -notmatch $contract.Trigger) {
        throw "The $($contract.Name) special-delivery contract is incomplete."
    }
}
if ($specialSource -notmatch '(?is)blpool\.bcs.*?Get_Tra\s*=\s*1.*?tra_array\s*=\s*blpool_item_tra') {
    throw 'The translated special-delivery path lost its translation-map input.'
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
    throw 'The configured delivery-test WeiDU executable does not exist.'
}
if ($resolvedWeidu.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to use WeiDU from the forbidden live game root.'
}
if (-not (Test-Path -LiteralPath $resolvedTempRoot -PathType Container)) {
    throw 'The configured delivery-test temporary root does not exist.'
}
if ($resolvedTempRoot.Equals($forbiddenLiveRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedTempRoot.StartsWith($forbiddenLiveRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create delivery-test scratch data under the forbidden live game root.'
}

function Write-AsciiFile {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $Text
    )
    [System.IO.File]::WriteAllBytes($Path, [System.Text.Encoding]::ASCII.GetBytes($Text))
}

function Write-MinimalTlk {
    param([Parameter(Mandatory = $true)][string] $Path)

    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $writer = [System.IO.BinaryWriter]::new($stream, [System.Text.Encoding]::ASCII, $true)
        try {
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('TLK V1  '))
            $writer.Write([uint16] 0)
            $writer.Write([uint32] 1)
            $writer.Write([uint32] 0x2c)
            $writer.Write([uint16] 0)
            $writer.Write([byte[]]::new(8))
            $writer.Write([uint32] 0)
            $writer.Write([uint32] 0)
            $writer.Write([uint32] 0)
            $writer.Write([uint32] 0)
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Write-MinimalKeyAndBif {
    param([Parameter(Mandatory = $true)][string] $GameRoot)

    $dataRoot = Join-Path $GameRoot 'data'
    $null = New-Item -ItemType Directory -Path $dataRoot
    $bifRelative = 'data\flirtest.bif'
    $bifPath = Join-Path $GameRoot $bifRelative
    $bifStream = [System.IO.File]::Open($bifPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $writer = [System.IO.BinaryWriter]::new($bifStream, [System.Text.Encoding]::ASCII, $true)
        try {
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('BIFF'))
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('V1  '))
            $writer.Write([uint32] 1)
            $writer.Write([uint32] 0)
            $writer.Write([uint32] 0x14)
            $writer.Write([uint32] 0)
            $writer.Write([uint32] 0x24)
            $writer.Write([uint32] 1)
            $writer.Write([uint16] 1010)
            $writer.Write([uint16] 0)
            $writer.Write([byte] 0)
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $bifStream.Dispose()
    }

    $bifNameBytes = [System.Text.Encoding]::ASCII.GetBytes($bifRelative.Replace('\', '/') + [char] 0)
    $keyStream = [System.IO.File]::Open((Join-Path $GameRoot 'chitin.key'), [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $writer = [System.IO.BinaryWriter]::new($keyStream, [System.Text.Encoding]::ASCII, $true)
        try {
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('KEY '))
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('V1  '))
            $writer.Write([uint32] 1)
            $writer.Write([uint32] 1)
            $writer.Write([uint32] 0x18)
            $writer.Write([uint32] 0x24)
            $writer.Write([uint32] (Get-Item -LiteralPath $bifPath).Length)
            $writer.Write([uint32] 0x32)
            $writer.Write([uint16] $bifNameBytes.Length)
            $writer.Write([uint16] 0)
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('OH6000'))
            $writer.Write([byte] 0)
            $writer.Write([byte] 0)
            $writer.Write([uint16] 1010)
            $writer.Write([uint32] 0)
            $writer.Write($bifNameBytes)
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $keyStream.Dispose()
    }
}

function New-DeliveryFakeGame {
    param([Parameter(Mandatory = $true)][string] $Name)

    $gameRoot = Join-Path $script:scratchRoot $Name
    $overrideRoot = Join-Path $gameRoot 'override'
    $languageRoot = Join-Path $gameRoot 'lang\en_US'
    $null = New-Item -ItemType Directory -Path $overrideRoot -Force
    $null = New-Item -ItemType Directory -Path $languageRoot -Force
    Write-MinimalKeyAndBif -GameRoot $gameRoot
    Write-MinimalTlk -Path (Join-Path $gameRoot 'dialog.tlk')
    Write-MinimalTlk -Path (Join-Path $languageRoot 'dialog.tlk')
    Write-AsciiFile -Path (Join-Path $gameRoot 'weidu.conf') -Text "lang_dir = en_US`r`n"
    [System.IO.File]::WriteAllBytes((Join-Path $gameRoot '...blank'), [byte[]]::new(0))

    $ids = @{
        'ACTION.IDS' = @'
IDS V1.0
26 PlaySound(S:Sound*)
30 SetGlobal(S:Name*,S:Area*,I:Value*)
63 Wait(I:Time*)
82 CreateItem(S:ResRef*,I:Usage1*,I:Usage2*,I:Usage3*)
109 IncrementGlobal(S:Name*,S:Area*,I:Value*)
111 DestroySelf()
140 GiveItemCreate(S:ResRef*,O:Object*,I:Usage1*,I:Usage2*,I:Usage3*)
151 DisplayString(O:Object*,I:StrRef*)
169 DestroyItem(S:ResRef*)
181 ReallyForceSpell(O:Target,I:Spell*Spell)
254 ScreenShake(P:Point*,I:Duration*)
'@
        'TRIGGER.IDS' = @'
IDS V1.0
0x0070 Clicked(O:Object*)
0x400F Global(S:Name*,S:Area*,I:Value*)
0x4018 Range(O:Object*,I:Range*)
0x4023 True()
0x4030 False()
0x4075 Contains(S:ResRef*,O:Object*)
'@
        'OBJECT.IDS' = "IDS V1.0`r`n1 Myself`r`n17 LastTrigger`r`n"
        'EA.IDS' = "IDS V1.0`r`n0 ANYONE`r`n"
        'SPELL.IDS' = "IDS V1.0`r`n2020 TRAP_UNDERWATER_BITE`r`n"
        'GENERAL.IDS' = "IDS V1.0`r`n0 GENERAL_ANY`r`n"
        'RACE.IDS' = "IDS V1.0`r`n0 RACE_ALL`r`n"
        'CLASS.IDS' = "IDS V1.0`r`n0 CLASS_ALL`r`n"
        'SPECIFIC.IDS' = "IDS V1.0`r`n0 SPECIFIC_ALL`r`n"
        'GENDER.IDS' = "IDS V1.0`r`n0 GENDER_ALL`r`n"
        'ALIGNMEN.IDS' = "IDS V1.0`r`n0 MASK_ALL`r`n"
    }
    foreach ($entry in $ids.GetEnumerator()) {
        Write-AsciiFile -Path (Join-Path $overrideRoot $entry.Key) -Text ($entry.Value.TrimStart("`r", "`n") + "`r`n")
    }

    $junctionPath = Join-Path $gameRoot 'randomiser'
    $null = New-Item -ItemType Junction -Path $junctionPath -Target $script:repositoryRoot
    [pscustomobject]@{
        Root = $gameRoot
        Override = $overrideRoot
        Junction = $junctionPath
    }
}

function Invoke-DeliveryHarness {
    param(
        [Parameter(Mandatory = $true)][int] $Component,
        [Parameter(Mandatory = $true)][string] $Name,
        [switch] $ExpectFailure
    )

    $game = New-DeliveryFakeGame -Name $Name
    $outputRoot = Join-Path $game.Root 'fixture-output'
    $null = New-Item -ItemType Directory -Path $outputRoot
    $null = New-Item -ItemType Directory -Path (Join-Path $outputRoot 'compile')
    $arguments = @(
        $script:harnessPath,
        '--game', $game.Root,
        '--force-install-list', [string] $Component,
        '--language', '0',
        '--use-lang', 'en_US',
        '--args', (Join-Path $script:repositoryRoot 'lib\lib.tpa'),
        '--args', $script:specialPath,
        '--args', $script:neutralizePath,
        '--args', $script:fixtureRoot,
        '--args', $outputRoot,
        '--no-exit-pause',
        '--quick-log'
    )
    Push-Location $game.Root
    try {
        $output = @(& $script:resolvedWeidu @arguments 2>&1 | ForEach-Object { [string] $_ })
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $joined = $output -join "`n"
    if ($ExpectFailure) {
        if ($exitCode -eq 0 -and $joined -notmatch 'NOT INSTALLED DUE TO ERRORS') {
            throw "Delivery harness '$Name' succeeded but a controlled failure was expected.`n$joined"
        }
    }
    elseif ($exitCode -ne 0 -or $joined -match 'NOT INSTALLED DUE TO ERRORS') {
        throw "Delivery harness '$Name' failed unexpectedly.`n$joined"
    }
    [pscustomobject]@{
        Game = $game
        OutputRoot = $outputRoot
        Output = $joined
        ExitCode = $exitCode
    }
}

function Get-CompactBafBlocks {
    param([Parameter(Mandatory = $true)][string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected decompiled BAF '$Path' is missing."
    }
    $text = [System.IO.File]::ReadAllText($Path)
    $withoutComments = [regex]::Replace($text, '(?m)//.*$', '')
    $matches = [regex]::Matches($withoutComments, '(?is)\bIF\b.*?\bTHEN\b\s*RESPONSE\s+#\d+.*?\bEND\b')
    @($matches | ForEach-Object { [regex]::Replace($_.Value, '\s+', '') })
}

function Assert-BlockCount {
    param([string[]] $Blocks, [int] $Expected, [string] $Label)
    if ($Blocks.Count -ne $Expected) {
        throw "$Label expected $Expected blocks, found $($Blocks.Count)."
    }
}

function Assert-ExactBlocks {
    param([string[]] $Blocks, [string[]] $Expected, [string] $Label)
    Assert-BlockCount $Blocks $Expected.Count $Label
    for ($index = 0; $index -lt $Expected.Count; $index++) {
        if ($Blocks[$index] -cne $Expected[$index]) {
            throw "$Label block $($index + 1) differs.`nExpected: $($Expected[$index])`nActual:   $($Blocks[$index])"
        }
    }
}

function Test-BytesEqual {
    param([string] $Left, [string] $Right)
    $a = [System.IO.File]::ReadAllBytes($Left)
    $b = [System.IO.File]::ReadAllBytes($Right)
    if ($a.Length -ne $b.Length) { return $false }
    for ($i = 0; $i -lt $a.Length; $i++) {
        if ($a[$i] -ne $b[$i]) { return $false }
    }
    $true
}

$scratchRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedTempRoot ('bgee-itemrandomiser-delivery-' + [guid]::NewGuid().ToString('N'))))
if (-not $scratchRoot.StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The delivery-test scratch directory escaped its disposable parent.'
}
$null = New-Item -ItemType Directory -Path $scratchRoot

try {
    $special = Invoke-DeliveryHarness -Component 0 -Name 'special'
    $pool = Get-CompactBafBlocks -Path (Join-Path $special.OutputRoot 'pool-result.baf')
    $book = Get-CompactBafBlocks -Path (Join-Path $special.OutputRoot 'book-result.baf')
    $altar = Get-CompactBafBlocks -Path (Join-Path $special.OutputRoot 'altar-result.baf')
    Assert-ExactBlocks $pool @(
        'IFFalse()THENRESPONSE#100SetGlobal("FixturePool","GLOBAL",1)END',
        'IFClicked([ANYONE])Range(LastTrigger,10)Global("Pool","AR0801",1)Global("fla1tx0","GLOBAL",2)THENRESPONSE#100DisplayString(Myself,1)GiveItemCreate("tstp0001",LastTrigger,3,2,1)ReallyForceSpell(LastTrigger,TRAP_UNDERWATER_BITE)SetGlobal("Pool","AR0801",2)END',
        'IFClicked([ANYONE])Range(LastTrigger,10)Global("Pool","AR0801",1)THENRESPONSE#100DisplayString(Myself,2)ReallyForceSpell(LastTrigger,TRAP_UNDERWATER_BITE)SetGlobal("Pool","AR0801",2)END'
    ) 'pool transform'
    Assert-ExactBlocks $book @(
        'IFFalse()THENRESPONSE#100SetGlobal("FixtureBook","GLOBAL",1)END',
        'IFClicked([ANYONE])Global("toggle","AR1513",1)Global("page","AR1513",5)Global("fla2t2","GLOBAL",2)THENRESPONSE#100DisplayString(Myself,50658)IncrementGlobal("page","AR1513",1)GiveItemCreate("tstb0001",LastTrigger,4,5,6)GiveItemCreate("scrl8z",LastTrigger,0,0,0)GiveItemCreate("scrl9b",LastTrigger,0,0,0)END',
        'IFClicked([ANYONE])Global("toggle","AR1513",1)Global("page","AR1513",5)THENRESPONSE#100DisplayString(Myself,50658)IncrementGlobal("page","AR1513",1)GiveItemCreate("scrl8z",LastTrigger,0,0,0)GiveItemCreate("scrl9b",LastTrigger,0,0,0)END'
    ) 'book transform'
    Assert-ExactBlocks $altar @(
        'IFFalse()THENRESPONSE#100SetGlobal("FixtureAltar","GLOBAL",1)END',
        'IFContains("MISC9D",Myself)Global("fla3t3","GLOBAL",2)THENRESPONSE#100DestroyItem("MISC9D")SetGlobal("SecretThree","GLOBAL",1)PlaySound("EFF_P92")ScreenShake([20.45],15)CreateItem("tsta0001",7,8,9)Wait(2)DisplayString(Myself,47724)END',
        'IFContains("MISC9D",Myself)THENRESPONSE#100DestroyItem("MISC9D")SetGlobal("SecretThree","GLOBAL",1)PlaySound("EFF_P92")ScreenShake([20.45],15)Wait(2)DisplayString(Myself,3)END'
    ) 'altar transform'
    Write-Output 'PASS DeliverySpecial_ExactThreeTransformsAndOrdering'

    if ($Case -eq 'Special') {
        Write-Output 'SUMMARY passed=1 failed=0'
        $global:LASTEXITCODE = 0
        return
    }

    $neutral = Invoke-DeliveryHarness -Component 1 -Name 'neutralizer'
    $report = ([System.IO.File]::ReadAllText((Join-Path $neutral.OutputRoot 'neutralize-report.txt'))).Trim()
    if ($report -cne 'COUNT 4') {
        throw "Neutralizer unique-publication count mismatch: '$report'."
    }
    foreach ($name in @('flq1t2', 'flq1t5', 'flq1t7', 'flq3t3')) {
        $blocks = @(Get-CompactBafBlocks -Path (Join-Path $neutral.OutputRoot ($name + '-result.baf')))
        Assert-BlockCount $blocks 1 "$name neutralizer"
        if ($blocks[0] -cne 'IFTrue()THENRESPONSE#100DestroySelf()END') {
            throw "$name neutralizer is not the exact unconditional DestroySelf-only stub: $($blocks[0])"
        }
    }
    foreach ($forbidden in @('flq2t1.bcs', 'flq1t8.bcs')) {
        if (Test-Path -LiteralPath (Join-Path $neutral.Game.Override $forbidden)) {
            throw "Neutralizer published excluded '$forbidden'."
        }
    }
    foreach ($sentinel in @('blpool', 'ppbook', 'troalt', 'fltier1')) {
        if (-not (Test-BytesEqual (Join-Path $neutral.OutputRoot ($sentinel + '-before.bcs')) (Join-Path $neutral.OutputRoot ($sentinel + '-after.bcs')))) {
            throw "Neutralizer changed excluded sentinel '$sentinel'."
        }
    }
    $ordinaryPublished = @(Get-ChildItem -LiteralPath $neutral.Game.Override -Filter 'flq*t*.bcs' -File | Select-Object -ExpandProperty Name | Sort-Object)
    if (($ordinaryPublished -join ',') -cne 'flq1t2.bcs,flq1t5.bcs,flq1t7.bcs,flq3t3.bcs') {
        throw "Neutralizer publication set mismatch: $($ordinaryPublished -join ',')."
    }
    Write-Output 'PASS DeliveryNeutralize_HistoryUnionDedupTombstonesAndExclusions'

    $collision = Invoke-DeliveryHarness -Component 2 -Name 'assignment-collision' -ExpectFailure
    if ($collision.Output -notmatch 'FLIR_DELIVERY_ERR\s+ASSIGNMENT_NAMESPACE\b') {
        throw "Assignment-family collision did not fail with the controlled code.`n$($collision.Output)"
    }
    if (Test-Path -LiteralPath (Join-Path $collision.Game.Override 'fltiert1.bcs')) {
        throw 'Assignment-family collision published a neutralizer script.'
    }
    Write-Output 'PASS DeliveryNeutralize_AssignmentFamilyFailsClosed'
    Write-Output 'SUMMARY passed=3 failed=0'
    $global:LASTEXITCODE = 0
}
finally {
    if (Test-Path -LiteralPath $scratchRoot -PathType Container) {
        Get-ChildItem -LiteralPath $scratchRoot -Directory -Recurse -Force |
            Where-Object { $_.LinkType -eq 'Junction' } |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
        Remove-Item -LiteralPath $scratchRoot -Recurse -Force
    }
}
