[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Uninstall')]
    [string] $Action,

    [string] $GameRoot = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2',

    [string] $WeiduPath = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ProbeFixture.Helpers.ps1')
$allowedRootLiteral = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2'
$forbiddenRootLiteral = "C:\Games\Baldur's Gate II Enhanced Edition modded"

function ConvertTo-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string] $Path)

    [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-IsWithinPath {
    param(
        [Parameter(Mandatory = $true)][string] $Candidate,
        [Parameter(Mandatory = $true)][string] $Root
    )

    $normalizedCandidate = ConvertTo-NormalizedFullPath $Candidate
    $normalizedRoot = ConvertTo-NormalizedFullPath $Root
    $normalizedCandidate.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $normalizedCandidate.StartsWith($normalizedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparsePointInPath {
    param([Parameter(Mandatory = $true)][string] $Path)

    $fullPath = ConvertTo-NormalizedFullPath $Path
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    $relative = $fullPath.Substring($root.Length)
    $cursor = $root
    foreach ($segment in @($relative -split '[\\/]' | Where-Object { $_.Length -gt 0 })) {
        $cursor = Join-Path $cursor $segment
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'FLIR_PROBE_ERR REPARSE_PATH_REJECTED'
            }
        }
    }
}

function Assert-DisposableRoot {
    param([Parameter(Mandatory = $true)][string] $Path)

    $resolved = ConvertTo-NormalizedFullPath $Path
    $allowed = ConvertTo-NormalizedFullPath $allowedRootLiteral
    $forbidden = ConvertTo-NormalizedFullPath $forbiddenRootLiteral
    if (-not $resolved.Equals($allowed, [System.StringComparison]::OrdinalIgnoreCase) -or
        (Test-IsWithinPath -Candidate $resolved -Root $forbidden)) {
        throw 'FLIR_PROBE_ERR DISPOSABLE_ROOT_REQUIRED'
    }
    Assert-NoReparsePointInPath $resolved
    $item = Get-Item -LiteralPath $resolved -Force
    if (-not $item.PSIsContainer -or
        -not $item.FullName.TrimEnd('\').Equals($allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'FLIR_PROBE_ERR DISPOSABLE_ROOT_REQUIRED'
    }
    $resolved
}

function Assert-NoEnhancedEditionProcess {
    $running = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -in @('Baldur.exe', 'InfinityLoader.exe') }
    )
    if ($running.Count -ne 0) {
        throw 'FLIR_PROBE_ERR GAME_PROCESS_OPEN'
    }
}

function Invoke-WeiDUOpaque {
    param(
        [Parameter(Mandatory = $true)][string] $WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [Parameter(Mandatory = $true)][string] $FailureCode,
        [switch] $RejectSkipping
    )

    Push-Location $WorkingDirectory
    try {
        $captured = @(& $script:resolvedWeidu @Arguments 2>&1 | ForEach-Object { [string] $_ })
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $joined = $captured -join "`n"
    Assert-ProbeWeiDUResult `
        -ExitCode $exitCode `
        -CapturedText $joined `
        -FailureCode $FailureCode `
        -RejectSkipping:$RejectSkipping
}

$resolvedGameRoot = Assert-DisposableRoot $GameRoot
$expectedWeidu = Join-Path $resolvedGameRoot 'EET\bin\win32\x86_64\weidu.exe'
if ([string]::IsNullOrWhiteSpace($WeiduPath)) {
    $WeiduPath = $expectedWeidu
}
$resolvedWeidu = ConvertTo-NormalizedFullPath $WeiduPath
Assert-NoReparsePointInPath $resolvedWeidu
if (-not $resolvedWeidu.Equals((ConvertTo-NormalizedFullPath $expectedWeidu), [System.StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $resolvedWeidu -PathType Leaf)) {
    throw 'FLIR_PROBE_ERR EXACT_WEIDU_REQUIRED'
}
$version = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($resolvedWeidu)
if ($version.ProductName -cne 'WeiDU' -or
    $version.FileDescription -cne 'Weimer Dialogue Utilities' -or
    $version.OriginalFilename -cne 'weidu.exe' -or
    $version.FileVersion -cne '249.00') {
    throw 'FLIR_PROBE_ERR EXACT_WEIDU_REQUIRED'
}

$engineConfig = Join-Path $resolvedGameRoot 'engine.lua'
$expectedProfileLine = "engine_name = `"Baldur's Gate - Enhanced Edition Trilogy - IR Test b600e94`""
if (-not (Test-Path -LiteralPath $engineConfig -PathType Leaf) -or
    @([System.IO.File]::ReadAllLines($engineConfig) | Where-Object { $_ -ceq $expectedProfileLine }).Count -ne 1) {
    throw 'FLIR_PROBE_ERR DISPOSABLE_PROFILE_REQUIRED'
}

Assert-NoEnhancedEditionProcess

$setupPath = Join-Path $PSScriptRoot 'setup-probe-fixture.tp2'
$configPath = Join-Path $PSScriptRoot 'M_FLRTP.lua'
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw 'FLIR_PROBE_ERR FIXTURE_SOURCE_MISSING'
}
Assert-NoReparsePointInPath $setupPath
Assert-NoReparsePointInPath $configPath
$weiduLogPath = Join-Path $resolvedGameRoot 'WeiDU.log'
Invoke-WeiDUOpaque -WorkingDirectory $PSScriptRoot -FailureCode 'PARSE_CHECK_FAILED' -Arguments @(
    '--nogame', '--parse-check', 'TP2', $setupPath
)

$temporaryDirectory = $null
try {
    if ($Action -ceq 'Uninstall') {
        Invoke-WeiDUOpaque -WorkingDirectory $resolvedGameRoot -FailureCode 'UNINSTALL_FAILED' -Arguments @(
            $setupPath,
            '--game', $resolvedGameRoot,
            '--force-uninstall-list', '0',
            '--language', '0',
            '--use-lang', 'en_US',
            '--no-exit-pause'
        )
        Assert-FixtureUninstalled -GameRoot $resolvedGameRoot -WeiduLogPath $weiduLogPath
        Write-Output 'PASS RuntimeProbeFixture_Uninstalled'
        return
    }

    $probeArea = [System.Environment]::GetEnvironmentVariable('FLIR_PROBE_AREA', 'Process')
    $probeXText = [System.Environment]::GetEnvironmentVariable('FLIR_PROBE_X', 'Process')
    $probeYText = [System.Environment]::GetEnvironmentVariable('FLIR_PROBE_Y', 'Process')
    [System.Environment]::SetEnvironmentVariable('FLIR_PROBE_AREA', $null, 'Process')
    [System.Environment]::SetEnvironmentVariable('FLIR_PROBE_X', $null, 'Process')
    [System.Environment]::SetEnvironmentVariable('FLIR_PROBE_Y', $null, 'Process')

    if ([string]::IsNullOrWhiteSpace($probeArea) -or $probeArea -cnotmatch '^[A-Za-z0-9#_-]{1,8}$' -or
        $probeArea.Equals('FLRTPRA', [System.StringComparison]::OrdinalIgnoreCase) -or
        $probeXText -cnotmatch '^[0-9]{1,5}$' -or $probeYText -cnotmatch '^[0-9]{1,5}$') {
        throw 'FLIR_PROBE_ERR PRIVATE_CURRENT_AREA_REQUIRED'
    }
    $probeX = [int]::Parse($probeXText, [System.Globalization.CultureInfo]::InvariantCulture)
    $probeY = [int]::Parse($probeYText, [System.Globalization.CultureInfo]::InvariantCulture)
    if ($probeX -lt 96 -or $probeX -gt 32000 -or $probeY -lt 96 -or $probeY -gt 32000) {
        throw 'FLIR_PROBE_ERR PRIVATE_CURRENT_AREA_REQUIRED'
    }

    $temporaryParent = ConvertTo-NormalizedFullPath ([System.IO.Path]::GetTempPath())
    if ((Test-IsWithinPath -Candidate $temporaryParent -Root $forbiddenRootLiteral) -or
        (Test-IsWithinPath -Candidate $temporaryParent -Root $resolvedGameRoot)) {
        throw 'FLIR_PROBE_ERR UNSAFE_TEMP_ROOT'
    }
    Assert-NoReparsePointInPath $temporaryParent
    $temporaryDirectory = Join-Path $temporaryParent ('flir-probe-' + [guid]::NewGuid().ToString('N'))
    if (-not (Test-IsWithinPath -Candidate $temporaryDirectory -Root $temporaryParent)) {
        throw 'FLIR_PROBE_ERR UNSAFE_TEMP_ROOT'
    }
    $null = New-Item -ItemType Directory -Path $temporaryDirectory
    Assert-NoReparsePointInPath $temporaryDirectory

    $privateDonor = Join-Path $temporaryDirectory 'private-donor.are'
    $overrideDonor = Join-Path (Join-Path $resolvedGameRoot 'override') ($probeArea + '.ARE')
    $extracted = Join-Path $temporaryDirectory ($probeArea + '.ARE')
    $extractDonor = {
        Invoke-WeiDUOpaque -WorkingDirectory $temporaryDirectory -FailureCode 'DONOR_EXTRACTION_FAILED' -Arguments @(
            '--game', $resolvedGameRoot,
            '--use-lang', 'en_US',
            '--biff-get', ($probeArea + '.ARE'),
            '--out', $temporaryDirectory,
            '--log', (Join-Path $temporaryDirectory 'extract.log'),
            '--no-exit-pause'
        )
    }
    Invoke-ProbeDonorAcquisitionOpaque `
        -OverrideDonor $overrideDonor `
        -PrivateDonor $privateDonor `
        -ExtractedDonor $extracted `
        -ExtractDonor $extractDonor

    $donorBytes = [System.IO.File]::ReadAllBytes($privateDonor)
    if ($donorBytes.Length -lt 0x11c -or
        [System.Text.Encoding]::ASCII.GetString($donorBytes, 0, 8) -cne 'AREAV1.0') {
        throw 'FLIR_PROBE_ERR INVALID_PRIVATE_DONOR'
    }
    $nonce = [guid]::NewGuid().ToString('N').Substring(0, 16)
    $stream = [System.IO.File]::Open($privateDonor, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $writer = [System.IO.BinaryWriter]::new($stream, [System.Text.Encoding]::ASCII, $true)
        try {
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes('FLIRPB01'))
            $writer.Write([System.Text.Encoding]::ASCII.GetBytes($nonce))
            $writer.Write([uint16] $probeX)
            $writer.Write([uint16] $probeY)
            $writer.Flush()
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }

    $activeFixtureEntries = @(Get-ActiveFixtureComponentEntries -WeiduLogPath $weiduLogPath)
    if ($activeFixtureEntries.Count -gt 1) {
        throw 'FLIR_PROBE_ERR INSTALL_STATE_INVALID'
    }
    $activeFixture = $activeFixtureEntries.Count -eq 1
    $installSelection = if ($activeFixture) {
        @('--force-uninstall-list', '0', '--force-install-list', '0')
    }
    else {
        @('--force-install-list', '0')
    }
    $installArguments = @(
        $setupPath,
        '--game', $resolvedGameRoot
    ) + $installSelection + @(
        '--args', $privateDonor,
        '--args', $nonce,
        '--args', $configPath,
        '--language', '0',
        '--use-lang', 'en_US',
        '--no-exit-pause'
    )
    Invoke-WeiDUOpaque -WorkingDirectory $resolvedGameRoot -FailureCode 'INSTALL_FAILED' -RejectSkipping -Arguments $installArguments
    Assert-FixtureInstalled -GameRoot $resolvedGameRoot -WeiduLogPath $weiduLogPath
    Assert-ProbeFixtureBinaryPreflight -GameRoot $resolvedGameRoot
    Write-Output 'PASS RuntimeProbeFixture_Installed'
}
finally {
    [System.Environment]::SetEnvironmentVariable('FLIR_PROBE_AREA', $null, 'Process')
    [System.Environment]::SetEnvironmentVariable('FLIR_PROBE_X', $null, 'Process')
    [System.Environment]::SetEnvironmentVariable('FLIR_PROBE_Y', $null, 'Process')
    if ($null -ne $temporaryDirectory -and (Test-Path -LiteralPath $temporaryDirectory -PathType Container)) {
        $validatedTemporary = ConvertTo-NormalizedFullPath $temporaryDirectory
        $validatedParent = ConvertTo-NormalizedFullPath ([System.IO.Path]::GetTempPath())
        if (-not (Test-IsWithinPath -Candidate $validatedTemporary -Root $validatedParent) -or
            $validatedTemporary.Equals($validatedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'FLIR_PROBE_ERR REFUSED_UNSAFE_CLEANUP'
        }
        [System.IO.Directory]::Delete($validatedTemporary, $true)
    }
}
