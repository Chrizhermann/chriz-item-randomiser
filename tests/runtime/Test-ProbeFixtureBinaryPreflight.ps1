[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ProbeFixture.Helpers.ps1')

function Set-U16 {
    param([byte[]] $Bytes, [int] $Offset, [uint16] $Value)
    [System.BitConverter]::GetBytes($Value).CopyTo($Bytes, $Offset)
}

function Set-U32 {
    param([byte[]] $Bytes, [int] $Offset, [uint32] $Value)
    [System.BitConverter]::GetBytes($Value).CopyTo($Bytes, $Offset)
}

function Set-Ascii {
    param([byte[]] $Bytes, [int] $Offset, [int] $Length, [string] $Value)
    [System.Array]::Clear($Bytes, $Offset, $Length)
    $encoded = [System.Text.Encoding]::ASCII.GetBytes($Value)
    if ($encoded.Length -gt $Length) {
        throw 'Test string exceeds its binary field.'
    }
    $encoded.CopyTo($Bytes, $Offset)
}

function New-TestAre {
    $actorOffset = 0x200
    $containerOffset = $actorOffset + 5 * 0x110
    $itemOffset = $containerOffset + 2 * 0xc0
    $songOffset = $itemOffset + 2 * 0x14
    $restOffset = $songOffset + 0x90
    $bytes = [byte[]]::new($restOffset + 0xe4)

    Set-Ascii $bytes 0 8 'AREAV1.0'
    Set-U32 $bytes 0x14 0x60
    Set-U32 $bytes 0x54 $actorOffset
    Set-U16 $bytes 0x58 5
    Set-U32 $bytes 0x70 $containerOffset
    Set-U16 $bytes 0x74 2
    Set-U16 $bytes 0x76 2
    Set-U32 $bytes 0x78 $itemOffset
    Set-U32 $bytes 0xbc $songOffset
    Set-U32 $bytes 0xc0 $restOffset

    $actorResrefs = @('FLRTPU', 'FLRTPD1', 'FLRTPD2', 'FLRTPL', 'FLRTPF')
    for ($i = 0; $i -lt $actorResrefs.Count; ++$i) {
        $actor = $actorOffset + $i * 0x110
        Set-Ascii $bytes ($actor + 0x00) 32 $actorResrefs[$i]
        Set-U32 $bytes ($actor + 0x28) 1
        Set-U32 $bytes ($actor + 0x38) ([uint32]::MaxValue)
        Set-U32 $bytes ($actor + 0x40) ([uint32]::MaxValue)
        Set-Ascii $bytes ($actor + 0x80) 8 $actorResrefs[$i]
        Set-U32 $bytes ($actor + 0x88) 0
        Set-U32 $bytes ($actor + 0x8c) 0
    }

    Set-Ascii $bytes ($containerOffset + 0x00) 32 'FLRTPC'
    Set-U16 $bytes ($containerOffset + 0x24) 8
    Set-U32 $bytes ($containerOffset + 0x40) 0
    Set-U32 $bytes ($containerOffset + 0x44) 1
    $pileOffset = $containerOffset + 0xc0
    Set-Ascii $bytes ($pileOffset + 0x00) 32 'FLRTPP'
    Set-U16 $bytes ($pileOffset + 0x24) 4
    Set-U32 $bytes ($pileOffset + 0x40) 1
    Set-U32 $bytes ($pileOffset + 0x44) 1

    Set-Ascii $bytes ($itemOffset + 0x00) 8 'FLRTPFL'
    Set-U32 $bytes ($itemOffset + 0x10) 1
    Set-Ascii $bytes ($itemOffset + 0x14) 8 'FLRTPFL'
    Set-U32 $bytes ($itemOffset + 0x24) 1
    $bytes
}

function New-TestCre {
    param([string] $ScriptName, [int] $ItemCount)

    $slotsOffset = 0x2d4
    $itemsOffset = 0x340
    $bytes = [byte[]]::new($itemsOffset + $ItemCount * 0x14)
    Set-Ascii $bytes 0 8 'CRE V1.0'
    $bytes[0x237] = 1
    $bytes[0x238] = 10
    $bytes[0x23a] = 10
    $bytes[0x23b] = 10
    $bytes[0x23c] = 10
    $bytes[0x23d] = 10
    $bytes[0x23e] = 10
    $bytes[0x23f] = 10
    $bytes[0x27b] = 0x22
    Set-Ascii $bytes 0x280 32 $ScriptName
    Set-U32 $bytes 0x2b8 $slotsOffset
    Set-U32 $bytes 0x2bc $itemsOffset
    Set-U32 $bytes 0x2c0 $ItemCount
    for ($i = 0; $i -lt 40; ++$i) {
        Set-U16 $bytes ($slotsOffset + $i * 2) 0xffff
    }
    for ($i = 0; $i -lt $ItemCount; ++$i) {
        Set-Ascii $bytes ($itemsOffset + $i * 0x14) 8 'FLRTPFL'
        Set-U32 $bytes ($itemsOffset + $i * 0x14 + 0x10) 1
        Set-U16 $bytes ($slotsOffset + (21 + $i) * 2) $i
    }
    $bytes
}

function New-TestItm {
    param([int[]] $Maxima)

    $abilityOffset = 0x72
    $bytes = [byte[]]::new($abilityOffset + $Maxima.Count * 0x38)
    Set-Ascii $bytes 0 8 'ITM V1  '
    Set-U32 $bytes 0x64 $abilityOffset
    Set-U16 $bytes 0x68 $Maxima.Count
    Set-U32 $bytes 0x6a ($abilityOffset + $Maxima.Count * 0x38)
    Set-U16 $bytes 0x70 0
    for ($i = 0; $i -lt $Maxima.Count; ++$i) {
        $ability = $abilityOffset + $i * 0x38
        $bytes[$ability] = 3
        $bytes[$ability + 0x0c] = 1
        Set-U16 $bytes ($ability + 0x0e) 1
        Set-U16 $bytes ($ability + 0x22) $Maxima[$i]
    }
    $bytes
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('flir-binary-test-' + [guid]::NewGuid().ToString('N'))
$overrideRoot = Join-Path $testRoot 'override'
$null = New-Item -ItemType Directory -Path $overrideRoot
try {
    $arePath = Join-Path $overrideRoot 'FLRTPRA.ARE'
    [System.IO.File]::WriteAllBytes($arePath, (New-TestAre))

    $creatures = @(
        @{ File = 'FLRTPU.CRE'; Script = 'FLRTPU'; Count = 1 },
        @{ File = 'FLRTPD1.CRE'; Script = 'FLRTPD'; Count = 1 },
        @{ File = 'FLRTPD2.CRE'; Script = 'FLRTPD'; Count = 1 },
        @{ File = 'FLRTPL.CRE'; Script = 'FLRTPL'; Count = 15 },
        @{ File = 'FLRTPF.CRE'; Script = 'FLRTPF'; Count = 16 }
    )
    foreach ($creature in $creatures) {
        [System.IO.File]::WriteAllBytes(
            (Join-Path $overrideRoot $creature.File),
            (New-TestCre -ScriptName $creature.Script -ItemCount $creature.Count)
        )
    }

    $items = @(
        @{ File = 'FLRTPFL.ITM'; Maxima = @() },
        @{ File = 'FLRTPIT.ITM'; Maxima = @(7, 11, 13) },
        @{ File = 'FLRTPJ.ITM'; Maxima = @(17, 19, 23) },
        @{ File = 'FLRTPK.ITM'; Maxima = @(29, 31, 37) }
    )
    foreach ($item in $items) {
        [System.IO.File]::WriteAllBytes(
            (Join-Path $overrideRoot $item.File),
            (New-TestItm -Maxima $item.Maxima)
        )
    }

    Assert-ProbeFixtureBinaryPreflight -GameRoot $testRoot

    $areBytes = [System.IO.File]::ReadAllBytes($arePath)
    $actorOffset = [System.BitConverter]::ToUInt32($areBytes, 0x54)
    Set-U32 $areBytes ($actorOffset + 0x28) 0
    [System.IO.File]::WriteAllBytes($arePath, $areBytes)
    $failure = $null
    try {
        Assert-ProbeFixtureBinaryPreflight -GameRoot $testRoot
    }
    catch {
        $failure = $_.Exception.Message
    }
    if ($failure -cne 'FLIR_PROBE_ERR BINARY_PREFLIGHT_FAILED') {
        throw 'The binary preflight accepted an attached/embedded ARE actor.'
    }

    [System.IO.File]::WriteAllBytes($arePath, (New-TestAre))
    $itmPath = Join-Path $overrideRoot 'FLRTPIT.ITM'
    $itmBytes = [System.IO.File]::ReadAllBytes($itmPath)
    Set-U16 $itmBytes (0x72 + 0x22) 6
    [System.IO.File]::WriteAllBytes($itmPath, $itmBytes)
    $failure = $null
    try {
        Assert-ProbeFixtureBinaryPreflight -GameRoot $testRoot
    }
    catch {
        $failure = $_.Exception.Message
    }
    if ($failure -cne 'FLIR_PROBE_ERR BINARY_PREFLIGHT_FAILED') {
        throw 'The binary preflight accepted an incorrect ITM charge maximum.'
    }

    [System.IO.File]::WriteAllBytes($itmPath, (New-TestItm -Maxima @(7, 11, 13)))
    $crePath = Join-Path $overrideRoot 'FLRTPF.CRE'
    $creBytes = [System.IO.File]::ReadAllBytes($crePath)
    Set-U32 $creBytes 0x2c0 15
    [System.IO.File]::WriteAllBytes($crePath, $creBytes)
    $failure = $null
    try {
        Assert-ProbeFixtureBinaryPreflight -GameRoot $testRoot
    }
    catch {
        $failure = $_.Exception.Message
    }
    if ($failure -cne 'FLIR_PROBE_ERR BINARY_PREFLIGHT_FAILED') {
        throw 'The binary preflight accepted an incorrect CRE inventory count.'
    }
}
finally {
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        [System.IO.Directory]::Delete($testRoot, $true)
    }
}

Write-Output 'PASS RuntimeProbeFixture_BinaryPreflight'
