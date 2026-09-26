[CmdletBinding()]
param(
    [string] $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe',
    [string] $LuaPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\lua.exe',
    [string] $SentinelBafDirectory = 'C:\Users\chris\Games\EET-IR-Test-b600e94\decompile-sentinel-final-20260827-064808'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$liveGameRoot = [System.IO.Path]::GetFullPath('C:\Games\Baldur''s Gate II Enhanced Edition modded')
$scratchParent = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot 'work'))
$scratchRoot = $null
$environmentBackup = @{}
$runnerExitCode = 0

function Test-IsWithinPath {
    param([string] $Candidate, [string] $Root)

    $rootWithSeparator = $Root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $Candidate.Equals($Root, [System.StringComparison]::OrdinalIgnoreCase) -or
        $Candidate.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparseAncestors {
    param([string] $FullPath)

    $root = [System.IO.Path]::GetPathRoot($FullPath)
    $current = $root
    if ((Get-Item -LiteralPath $current -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Reparse point in path: $current"
    }
    foreach ($part in ($FullPath.Substring($root.Length) -split '[\\/]' | Where-Object { $_ })) {
        $current = Join-Path $current $part
        if ((Get-Item -LiteralPath $current -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Reparse point in path: $current"
        }
    }
}

function Resolve-InputPath {
    param(
        [string] $Path,
        [ValidateSet('Leaf', 'Container')] [string] $PathType
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'A required test path is empty.'
    }
    $candidate = $Path
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path $repositoryRoot $candidate
    }
    $fullPath = [System.IO.Path]::GetFullPath($candidate)
    if (Test-IsWithinPath -Candidate $fullPath -Root $liveGameRoot) {
        throw "Refusing a test path in the live game: $fullPath"
    }
    Assert-NoReparseAncestors -FullPath $fullPath
    if (-not (Test-Path -LiteralPath $fullPath -PathType $PathType)) {
        throw "Required $PathType path does not exist: $fullPath"
    }
    $fullPath
}

function Invoke-Checked {
    param(
        [string] $Label,
        [string] $Executable,
        [string[]] $Arguments,
        [string] $WorkingDirectory
    )

    Push-Location -LiteralPath $WorkingDirectory
    try {
        $output = @(& $Executable @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    foreach ($line in $output) {
        Write-Output $line.ToString()
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
    Write-Output "PASS $Label"
}

function Invoke-PowerShellTest {
    param([string] $ScriptPath, [string[]] $ScriptArguments = @())

    $name = [System.IO.Path]::GetFileNameWithoutExtension($ScriptPath)
    Invoke-Checked -Label "PowerShell_$name" -Executable $powerShellHostPath `
        -Arguments (@('-NoProfile', '-File', $ScriptPath) + $ScriptArguments) `
        -WorkingDirectory $repositoryRoot
}

function Invoke-WeiduParseCheck {
    param(
        [string] $SourcePath,
        [ValidateSet('TP2', 'TPA', 'TPP')] [string] $SourceType
    )

    $relativePath = $SourcePath.Substring($repositoryRoot.Length).TrimStart('\', '/').Replace('\', '/')
    Invoke-Checked -Label "WeiDUParse_$relativePath" -Executable $resolvedWeiduPath `
        -Arguments @('--nogame', '--parse-check', $SourceType, $SourcePath) `
        -WorkingDirectory $scratchRoot
}

function Remove-OwnedScratch {
    param([string] $Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $leaf = [System.IO.Path]::GetFileName($fullPath)
    if (
        -not [System.IO.Path]::GetDirectoryName($fullPath).Equals($scratchParent, [System.StringComparison]::OrdinalIgnoreCase) -or
        $leaf -cnotmatch '^bgee-itemrandomiser-unit-[0-9a-f]{32}$'
    ) {
        throw "Refusing to remove an unowned scratch path: $fullPath"
    }
    Assert-NoReparseAncestors -FullPath $fullPath
    $directories = [System.Collections.Generic.Stack[string]]::new()
    $directories.Push($fullPath)
    while ($directories.Count -gt 0) {
        foreach ($entry in (Get-ChildItem -LiteralPath $directories.Pop() -Force)) {
            if ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Refusing to remove scratch containing a reparse point: $($entry.FullName)"
            }
            if ($entry.PSIsContainer) {
                $directories.Push($entry.FullName)
            }
        }
    }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
}

try {
    Assert-NoReparseAncestors -FullPath $repositoryRoot
    $resolvedWeiduPath = Resolve-InputPath -Path $WeiduPath -PathType Leaf
    $resolvedLuaPath = Resolve-InputPath -Path $LuaPath -PathType Leaf
    $resolvedSentinelBafDirectory = Resolve-InputPath -Path $SentinelBafDirectory -PathType Container

    if (Test-IsWithinPath -Candidate $scratchParent -Root $liveGameRoot) {
        throw 'The workspace scratch parent is inside the live game.'
    }
    if (-not (Test-Path -LiteralPath $scratchParent)) {
        New-Item -ItemType Directory -Path $scratchParent -ErrorAction Stop | Out-Null
    }
    if (-not (Test-Path -LiteralPath $scratchParent -PathType Container)) {
        throw "Workspace scratch parent is not a directory: $scratchParent"
    }
    Assert-NoReparseAncestors -FullPath $scratchParent
    $scratchRoot = Join-Path $scratchParent ('bgee-itemrandomiser-unit-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $scratchRoot -ErrorAction Stop | Out-Null

    $powerShellHostPath = (Get-Process -Id $PID).Path
    if ([string]::IsNullOrWhiteSpace($powerShellHostPath)) {
        throw 'Cannot resolve the current PowerShell executable.'
    }

    $testEnvironment = [ordered]@{
        FL_IR_TEST_WEIDU = $resolvedWeiduPath
        FL_IR_TEST_LUA = $resolvedLuaPath
        FL_IR_TEST_SENTINEL_BAF = $resolvedSentinelBafDirectory
        FL_IR_TEST_TEMP_ROOT = $scratchRoot
    }
    foreach ($name in $testEnvironment.Keys) {
        $environmentBackup[$name] = [System.Environment]::GetEnvironmentVariable($name, 'Process')
        [System.Environment]::SetEnvironmentVariable($name, $testEnvironment[$name], 'Process')
    }

    Invoke-PowerShellTest -ScriptPath (Join-Path $PSScriptRoot 'Test-SentinelDependencies.ps1') `
        -ScriptArguments @('-BafDirectory', $resolvedSentinelBafDirectory)

    $deferredTestNames = @('Test-SentinelDependencies.ps1', 'Test-ModeBoundary.ps1')
    $otherPowerShellTests = @(
        Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'Test-*.ps1' -File |
            Where-Object { $deferredTestNames -notcontains $_.Name } |
            Sort-Object Name
    )
    foreach ($testScript in $otherPowerShellTests) {
        Invoke-PowerShellTest -ScriptPath $testScript.FullName
    }

    $parseChecks = @()
    $parseChecks += [pscustomobject]@{ Path = (Join-Path $repositoryRoot 'randomiser.tp2'); Type = 'TP2' }
    $parseChecks += @(
        Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'lib') -Filter '*.tpa' -File |
            ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Type = 'TPA' } }
    )
    $weiduTestDirectory = Join-Path $PSScriptRoot 'weidu'
    if (Test-Path -LiteralPath $weiduTestDirectory -PathType Container) {
        foreach ($extension in @('tp2', 'tpa', 'tpp')) {
            $sourceType = $extension.ToUpperInvariant()
            $parseChecks += @(
                Get-ChildItem -LiteralPath $weiduTestDirectory -Filter "*.$extension" -File -Recurse |
                    ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Type = $sourceType } }
            )
        }
    }
    foreach ($parseCheck in @($parseChecks | Sort-Object Path -Unique)) {
        Invoke-WeiduParseCheck -SourcePath $parseCheck.Path -SourceType $parseCheck.Type
    }

    $luaRunnerPath = Join-Path (Join-Path $PSScriptRoot 'lua') 'run.lua'
    if (Test-Path -LiteralPath $luaRunnerPath -PathType Leaf) {
        Invoke-Checked -Label 'Lua53_tests/lua/run.lua' -Executable $resolvedLuaPath `
            -Arguments @($luaRunnerPath) -WorkingDirectory $repositoryRoot
    }
    else {
        Write-Output 'SKIP Lua53_tests/lua/run.lua_NotPresent'
    }

    # This final test checks the source tree after every other repository check.
    Invoke-PowerShellTest -ScriptPath (Join-Path $PSScriptRoot 'Test-ModeBoundary.ps1')
}
catch {
    $runnerExitCode = 1
    Write-Error "UNEXPECTED_FAIL $($_.Exception.Message)" -ErrorAction Continue
}
finally {
    foreach ($name in $environmentBackup.Keys) {
        [System.Environment]::SetEnvironmentVariable($name, $environmentBackup[$name], 'Process')
    }
    if ($runnerExitCode -eq 0 -and $null -ne $scratchRoot) {
        try {
            Remove-OwnedScratch -Path $scratchRoot
        }
        catch {
            $runnerExitCode = 1
            Write-Error "UNEXPECTED_FAIL Scratch cleanup failed: $($_.Exception.Message)" -ErrorAction Continue
        }
    }
    elseif ($null -ne $scratchRoot) {
        Write-Output "FAILED RUN SCRATCH: $scratchRoot"
    }
}

exit $runnerExitCode
