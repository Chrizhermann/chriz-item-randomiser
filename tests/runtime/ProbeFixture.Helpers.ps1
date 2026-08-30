Set-StrictMode -Version 3.0

function Assert-ProbeWeiDUResult {
    param(
        [Parameter(Mandatory = $true)][int] $ExitCode,
        [Parameter(Mandatory = $true)][string] $CapturedText,
        [Parameter(Mandatory = $true)][string] $FailureCode,
        [switch] $RejectSkipping
    )

    if ($ExitCode -ne 0 -or
        $CapturedText -match 'NOT INSTALLED DUE TO ERRORS' -or
        ($RejectSkipping -and $CapturedText -match '(?im)\bSKIPPING:')) {
        throw "FLIR_PROBE_ERR $FailureCode"
    }
}

function Get-ActiveFixtureComponentEntries {
    param([Parameter(Mandatory = $true)][string] $WeiduLogPath)

    if (-not (Test-Path -LiteralPath $WeiduLogPath -PathType Leaf)) {
        return @()
    }
    @(
        [System.IO.File]::ReadAllLines($WeiduLogPath) |
            Where-Object {
                $_ -notmatch '^\s*//' -and
                $_ -match '(?i)^\s*~.*SETUP-PROBE-FIXTURE\.TP2~\s+#\d+\s+#0(?:\s|$)'
            }
    )
}

function Get-FixtureOutputPaths {
    param([Parameter(Mandatory = $true)][string] $GameRoot)

    $overrideRoot = Join-Path $GameRoot 'override'
    @(
        'FLRTPFL.ITM',
        'FLRTPIT.ITM',
        'FLRTPJ.ITM',
        'FLRTPK.ITM',
        'FLRTPU.CRE',
        'FLRTPD1.CRE',
        'FLRTPD2.CRE',
        'FLRTPL.CRE',
        'FLRTPF.CRE',
        'FLRTPRA.ARE',
        'M_FLRTP.lua'
    ) | ForEach-Object { Join-Path $overrideRoot $_ }
}

function Assert-FixtureInstalled {
    param(
        [Parameter(Mandatory = $true)][string] $GameRoot,
        [Parameter(Mandatory = $true)][string] $WeiduLogPath
    )

    $activeEntries = @(Get-ActiveFixtureComponentEntries -WeiduLogPath $WeiduLogPath)
    $missingOutputs = @(
        Get-FixtureOutputPaths -GameRoot $GameRoot |
            Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }
    )
    if ($activeEntries.Count -ne 1 -or $missingOutputs.Count -ne 0) {
        throw 'FLIR_PROBE_ERR INSTALL_STATE_INVALID'
    }
}

function Assert-FixtureUninstalled {
    param(
        [Parameter(Mandatory = $true)][string] $GameRoot,
        [Parameter(Mandatory = $true)][string] $WeiduLogPath
    )

    $activeEntries = @(Get-ActiveFixtureComponentEntries -WeiduLogPath $WeiduLogPath)
    $remainingOutputs = @(
        Get-FixtureOutputPaths -GameRoot $GameRoot |
            Where-Object { Test-Path -LiteralPath $_ }
    )
    if ($activeEntries.Count -ne 0 -or $remainingOutputs.Count -ne 0) {
        throw 'FLIR_PROBE_ERR UNINSTALL_STATE_INVALID'
    }
}

function Invoke-ProbeDonorAcquisitionOpaque {
    param(
        [Parameter(Mandatory = $true)][string] $OverrideDonor,
        [Parameter(Mandatory = $true)][string] $PrivateDonor,
        [Parameter(Mandatory = $true)][string] $ExtractedDonor,
        [Parameter(Mandatory = $true)][scriptblock] $ExtractDonor
    )

    try {
        if (Test-Path -LiteralPath $OverrideDonor -PathType Leaf) {
            Copy-Item -LiteralPath $OverrideDonor -Destination $PrivateDonor -ErrorAction Stop
        }
        else {
            & $ExtractDonor
            if (-not (Test-Path -LiteralPath $ExtractedDonor -PathType Leaf)) {
                throw 'donor extraction produced no file'
            }
            Move-Item -LiteralPath $ExtractedDonor -Destination $PrivateDonor -ErrorAction Stop
        }
    }
    catch {
        throw 'FLIR_PROBE_ERR DONOR_ACQUISITION_FAILED'
    }
}

function Get-ProbeUInt16 {
    param([byte[]] $Bytes, [int] $Offset)
    if ($Offset -lt 0 -or $Offset -gt $Bytes.Length - 2) {
        throw 'binary word outside file'
    }
    [System.BitConverter]::ToUInt16($Bytes, $Offset)
}

function Get-ProbeUInt32 {
    param([byte[]] $Bytes, [int] $Offset)
    if ($Offset -lt 0 -or $Offset -gt $Bytes.Length - 4) {
        throw 'binary dword outside file'
    }
    [System.BitConverter]::ToUInt32($Bytes, $Offset)
}

function Get-ProbeAscii {
    param([byte[]] $Bytes, [int] $Offset, [int] $Length)
    if ($Offset -lt 0 -or $Length -lt 0 -or $Offset -gt $Bytes.Length - $Length) {
        throw 'binary string outside file'
    }
    $text = [System.Text.Encoding]::ASCII.GetString($Bytes, $Offset, $Length)
    $terminator = $text.IndexOf([char] 0)
    if ($terminator -ge 0) {
        $text = $text.Substring(0, $terminator)
    }
    $text
}

function Assert-ProbeRange {
    param([byte[]] $Bytes, [int] $Offset, [int] $Length)
    if ($Offset -lt 0 -or $Length -lt 0 -or $Offset -gt $Bytes.Length - $Length) {
        throw 'binary section outside file'
    }
}

function Test-ProbeZeroRange {
    param([byte[]] $Bytes, [int] $Offset, [int] $Length)
    Assert-ProbeRange $Bytes $Offset $Length
    for ($i = 0; $i -lt $Length; ++$i) {
        if ($Bytes[$Offset + $i] -ne 0) {
            return $false
        }
    }
    $true
}

function Assert-ProbeFixtureBinaryPreflight {
    param([Parameter(Mandatory = $true)][string] $GameRoot)

    try {
        $overrideRoot = Join-Path $GameRoot 'override'
        $are = [System.IO.File]::ReadAllBytes((Join-Path $overrideRoot 'FLRTPRA.ARE'))
        if ((Get-ProbeAscii $are 0 8) -cne 'AREAV1.0' -or
            (Get-ProbeUInt32 $are 0x14) -ne 0x60) {
            throw 'invalid synthetic area header'
        }

        $actorOffset = Get-ProbeUInt32 $are 0x54
        $actorCount = Get-ProbeUInt16 $are 0x58
        $actorResrefs = @('FLRTPU', 'FLRTPD1', 'FLRTPD2', 'FLRTPL', 'FLRTPF')
        if ($actorCount -ne $actorResrefs.Count) {
            throw 'invalid actor count'
        }
        Assert-ProbeRange $are $actorOffset ($actorCount * 0x110)
        for ($i = 0; $i -lt $actorResrefs.Count; ++$i) {
            $actor = $actorOffset + $i * 0x110
            if (((Get-ProbeUInt32 $are ($actor + 0x28)) -band 1) -ne 1 -or
                (Get-ProbeUInt32 $are ($actor + 0x38)) -ne [uint32]::MaxValue -or
                (Get-ProbeUInt32 $are ($actor + 0x40)) -ne [uint32]::MaxValue -or
                (Get-ProbeAscii $are ($actor + 0x80) 8) -cne $actorResrefs[$i] -or
                (Get-ProbeUInt32 $are ($actor + 0x88)) -ne 0 -or
                (Get-ProbeUInt32 $are ($actor + 0x8c)) -ne 0) {
                throw 'invalid external actor record'
            }
        }

        $containerOffset = Get-ProbeUInt32 $are 0x70
        $containerCount = Get-ProbeUInt16 $are 0x74
        $itemCount = Get-ProbeUInt16 $are 0x76
        $itemOffset = Get-ProbeUInt32 $are 0x78
        if ($containerCount -ne 2 -or $itemCount -ne 2) {
            throw 'invalid area target count'
        }
        Assert-ProbeRange $are $containerOffset (2 * 0xc0)
        Assert-ProbeRange $are $itemOffset (2 * 0x14)
        $pileOffset = $containerOffset + 0xc0
        if ((Get-ProbeAscii $are $containerOffset 32) -cne 'FLRTPC' -or
            (Get-ProbeUInt16 $are ($containerOffset + 0x24)) -ne 8 -or
            (Get-ProbeUInt32 $are ($containerOffset + 0x40)) -ne 0 -or
            (Get-ProbeUInt32 $are ($containerOffset + 0x44)) -ne 1 -or
            (Get-ProbeAscii $are $pileOffset 32) -cne 'FLRTPP' -or
            (Get-ProbeUInt16 $are ($pileOffset + 0x24)) -ne 4 -or
            (Get-ProbeUInt32 $are ($pileOffset + 0x40)) -ne 1 -or
            (Get-ProbeUInt32 $are ($pileOffset + 0x44)) -ne 1) {
            throw 'invalid container or pile record'
        }
        for ($i = 0; $i -lt 2; ++$i) {
            $item = $itemOffset + $i * 0x14
            if ((Get-ProbeAscii $are $item 8) -cne 'FLRTPFL' -or
                (Get-ProbeUInt32 $are ($item + 0x10)) -ne 1) {
                throw 'invalid area seed item'
            }
        }

        $songOffset = Get-ProbeUInt32 $are 0xbc
        $restOffset = Get-ProbeUInt32 $are 0xc0
        if ($restOffset -ne $songOffset + 0x90 -or
            -not (Test-ProbeZeroRange $are $songOffset 0x90) -or
            -not (Test-ProbeZeroRange $are $restOffset 0xe4)) {
            throw 'invalid song or rest section'
        }

        $creatures = @(
            @{ File = 'FLRTPU.CRE'; Script = 'FLRTPU'; Count = 1 },
            @{ File = 'FLRTPD1.CRE'; Script = 'FLRTPD'; Count = 1 },
            @{ File = 'FLRTPD2.CRE'; Script = 'FLRTPD'; Count = 1 },
            @{ File = 'FLRTPL.CRE'; Script = 'FLRTPL'; Count = 15 },
            @{ File = 'FLRTPF.CRE'; Script = 'FLRTPF'; Count = 16 }
        )
        foreach ($expected in $creatures) {
            $cre = [System.IO.File]::ReadAllBytes((Join-Path $overrideRoot $expected.File))
            if ((Get-ProbeAscii $cre 0 8) -cne 'CRE V1.0' -or
                (Get-ProbeAscii $cre 0x280 32) -cne $expected.Script -or
                $cre[0x237] -ne 1 -or
                $cre[0x238] -ne 10 -or
                $cre[0x23a] -ne 10 -or
                $cre[0x23b] -ne 10 -or
                $cre[0x23c] -ne 10 -or
                $cre[0x23d] -ne 10 -or
                $cre[0x23e] -ne 10 -or
                $cre[0x23f] -ne 10 -or
                $cre[0x27b] -ne 0x22) {
                throw 'invalid creature header'
            }
            $slotsOffset = Get-ProbeUInt32 $cre 0x2b8
            $itemsOffset = Get-ProbeUInt32 $cre 0x2bc
            $itemsCount = Get-ProbeUInt32 $cre 0x2c0
            if ($itemsCount -ne $expected.Count) {
                throw 'invalid creature item count'
            }
            Assert-ProbeRange $cre $slotsOffset (40 * 2)
            Assert-ProbeRange $cre $itemsOffset ($itemsCount * 0x14)
            for ($i = 0; $i -lt $itemsCount; ++$i) {
                $item = $itemsOffset + $i * 0x14
                if ((Get-ProbeAscii $cre $item 8) -cne 'FLRTPFL' -or
                    (Get-ProbeUInt32 $cre ($item + 0x10)) -ne 1 -or
                    (Get-ProbeUInt16 $cre ($slotsOffset + (21 + $i) * 2)) -ne $i) {
                    throw 'invalid creature inventory seed'
                }
            }
        }

        $items = @(
            @{ File = 'FLRTPFL.ITM'; Maxima = [int[]] @() },
            @{ File = 'FLRTPIT.ITM'; Maxima = [int[]] @(7, 11, 13) },
            @{ File = 'FLRTPJ.ITM'; Maxima = [int[]] @(17, 19, 23) },
            @{ File = 'FLRTPK.ITM'; Maxima = [int[]] @(29, 31, 37) }
        )
        foreach ($expected in $items) {
            $itm = [System.IO.File]::ReadAllBytes((Join-Path $overrideRoot $expected.File))
            if ((Get-ProbeAscii $itm 0 8) -cne 'ITM V1  ') {
                throw 'invalid item header'
            }
            $abilityOffset = Get-ProbeUInt32 $itm 0x64
            $abilityCount = Get-ProbeUInt16 $itm 0x68
            $featureOffset = Get-ProbeUInt32 $itm 0x6a
            $globalFeatureCount = Get-ProbeUInt16 $itm 0x70
            if ($abilityCount -ne $expected.Maxima.Count -or
                $featureOffset -ne $abilityOffset + $abilityCount * 0x38 -or
                $globalFeatureCount -ne 0) {
                throw 'invalid item table offsets'
            }
            Assert-ProbeRange $itm $abilityOffset ($abilityCount * 0x38)
            for ($i = 0; $i -lt $abilityCount; ++$i) {
                $ability = $abilityOffset + $i * 0x38
                if ($itm[$ability] -ne 3 -or
                    $itm[$ability + 0x0c] -ne 1 -or
                    (Get-ProbeUInt16 $itm ($ability + 0x0e)) -ne 1 -or
                    (Get-ProbeUInt16 $itm ($ability + 0x22)) -ne $expected.Maxima[$i]) {
                    throw 'invalid item ability maximum'
                }
            }
        }
    }
    catch {
        throw 'FLIR_PROBE_ERR BINARY_PREFLIGHT_FAILED'
    }
}
