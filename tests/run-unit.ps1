[CmdletBinding()]
param(
    [string] $WeiduPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe',
    [string] $LuaPath = 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\lua.exe',
    [string] $SentinelBafDirectory = 'C:\Users\chris\Games\EET-IR-Test-b600e94\decompile-sentinel-final-20260827-064808'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$liveGameRoot = 'C:\Games\Baldur''s Gate II Enhanced Edition modded'
$knownFutureAssertion = 'FUTURE_Mode1_ExplicitManifestVersusLegacyDeliveryBranch'
$modeBoundaryAssertionNames = @(
    'Mode1_1100_SetsWeiduAction0',
    'Mode1_1200_SetsWeiduAction0',
    'Mode2_1300_SetsWeiduAction1',
    'Mode2_1400_SetsWeiduAction1',
    'Mode2_Only1300And1400IncludeWeiduAction',
    'Mode2_WeiduActionLibraryMatches1efc64a',
    'Mode2_ItemListsMatch1efc64a',
    'Mode2_LocationListsMatch1efc64a',
    'Mode2_ComponentsExcludeManifestRuntimeAssets',
    'FUTURE_Mode1_ExplicitManifestVersusLegacyDeliveryBranch'
)
$runnerExitCode = 0
$scratchRoot = $null
$scratchParent = $null
$environmentBackup = @{}
$callerTempRequest = $null
$callerTempEnvironment = [System.Environment]::GetEnvironmentVariable('TEMP', 'Process')
$callerTmpEnvironment = [System.Environment]::GetEnvironmentVariable('TMP', 'Process')

try {
    # Capture the caller's request before the compiler environment is changed. GetTempPath
    # selects a path but does not create or probe it.
    $callerTempRequest = [System.IO.Path]::GetTempPath()
}
catch {
    Write-Error 'UNEXPECTED_FAIL Caller temporary path capture failed.' -ErrorAction Continue
    exit 1
}

function ConvertTo-NormalizedFullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'A required test path was empty.'
    }

    $candidate = $Path
    if ($candidate.StartsWith('\\') -or $candidate.StartsWith('//')) {
        throw 'UNC and device paths are not accepted by the hermetic runner.'
    }
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path $script:repositoryRoot $candidate
    }

    $fullPath = [System.IO.Path]::GetFullPath($candidate)
    if ($fullPath.StartsWith('\\') -or $fullPath.StartsWith('//')) {
        throw 'UNC and device paths are not accepted by the hermetic runner.'
    }
    if ($fullPath -notmatch '^[A-Za-z]:\\') {
        throw 'Only fully qualified local drive-letter paths are accepted.'
    }
    if ($fullPath.IndexOf('~') -ge 0) {
        throw 'Potential short-name aliases are not accepted by the hermetic runner.'
    }

    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $fullPath
    }

    $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-IsWithinPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Candidate,

        [Parameter(Mandatory = $true)]
        [string] $Root
    )

    $normalizedCandidate = (ConvertTo-NormalizedFullPath -Path $Candidate)
    $normalizedRoot = (ConvertTo-NormalizedFullPath -Path $Root)

    if ($normalizedCandidate.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $rootPrefix = $normalizedRoot + [System.IO.Path]::DirectorySeparatorChar
    $normalizedCandidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function ConvertTo-ManagedBootstrapFullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'The native-helper bootstrap path was empty.'
    }
    if ($Path.StartsWith('\\') -or $Path.StartsWith('//')) {
        throw 'The native-helper bootstrap path was not local.'
    }
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw 'The native-helper bootstrap path was not absolute.'
    }

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (
        $fullPath.StartsWith('\\') -or
        $fullPath.StartsWith('//') -or
        $fullPath -notmatch '^[A-Za-z]:\\' -or
        $fullPath.IndexOf('~') -ge 0
    ) {
        throw 'The native-helper bootstrap path was not a long local drive-letter path.'
    }

    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $fullPath
    }

    $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Assert-ManagedBootstrapDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $fullPath = ConvertTo-ManagedBootstrapFullPath -Path $Path
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    $driveInfo = New-Object System.IO.DriveInfo($pathRoot)
    if (-not $driveInfo.IsReady -or $driveInfo.DriveType -ne [System.IO.DriveType]::Fixed) {
        throw 'The native-helper bootstrap path was not on a ready fixed drive.'
    }

    # DriveInfo reports SUBST aliases as fixed. A real local volume has a managed WMI
    # association to at least one disk partition; per-session DOS aliases do not.
    $driveDeviceId = $pathRoot.Substring(0, 2).Replace("'", "''")
    $diskAssociationQuery = (
        "ASSOCIATORS OF {Win32_LogicalDisk.DeviceID='$driveDeviceId'} " +
        'WHERE AssocClass=Win32_LogicalDiskToPartition'
    )
    $diskAssociationSearcher = $null
    try {
        $diskAssociationSearcher = New-Object System.Management.ManagementObjectSearcher(
            $diskAssociationQuery
        )
        $diskAssociations = @($diskAssociationSearcher.Get())
    }
    finally {
        if ($null -ne $diskAssociationSearcher) {
            $diskAssociationSearcher.Dispose()
        }
    }
    if ($diskAssociations.Count -eq 0) {
        throw 'The native-helper bootstrap drive was not backed by a local disk partition.'
    }

    $relativePart = $fullPath.Substring($pathRoot.Length)
    $segments = @($relativePart -split '[\\/]' | Where-Object { $_.Length -gt 0 })
    $currentPath = $pathRoot
    $pathsToCheck = @($pathRoot)
    foreach ($segment in $segments) {
        $currentPath = [System.IO.Path]::Combine($currentPath, $segment)
        $pathsToCheck += $currentPath
    }

    foreach ($pathToCheck in $pathsToCheck) {
        if (-not [System.IO.Directory]::Exists($pathToCheck)) {
            throw 'The native-helper bootstrap directory did not exist.'
        }
        $attributes = [System.IO.File]::GetAttributes($pathToCheck)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'The native-helper bootstrap path contained a reparse point.'
        }
    }

    $fullPath
}

function Remove-ManagedBootstrapDirectorySafely {
    param(
        [Parameter(Mandatory = $true)]
        [string] $BootstrapPath,

        [Parameter(Mandatory = $true)]
        [string] $ExpectedParent
    )

    $normalizedBootstrap = ConvertTo-ManagedBootstrapFullPath -Path $BootstrapPath
    $normalizedParent = ConvertTo-ManagedBootstrapFullPath -Path $ExpectedParent
    $leafName = [System.IO.Path]::GetFileName($normalizedBootstrap)
    $directParent = [System.IO.Path]::GetDirectoryName($normalizedBootstrap)
    if (
        -not $directParent.Equals($normalizedParent, [System.StringComparison]::OrdinalIgnoreCase) -or
        $leafName -notmatch '^bgee-itemrandomiser-native-bootstrap-[0-9a-f]{32}$'
    ) {
        throw 'Refusing to clean an unverified native-helper bootstrap directory.'
    }

    if (-not [System.IO.Directory]::Exists($normalizedBootstrap)) {
        return
    }

    $null = Assert-ManagedBootstrapDirectory -Path $normalizedBootstrap
    $pendingDirectories = New-Object 'System.Collections.Generic.Stack[string]'
    $pendingDirectories.Push($normalizedBootstrap)
    while ($pendingDirectories.Count -gt 0) {
        $directory = $pendingDirectories.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Refusing to clean a native-helper bootstrap tree containing a reparse point.'
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $pendingDirectories.Push($entry)
            }
        }
    }

    [System.IO.Directory]::Delete($normalizedBootstrap, $true)
    if ([System.IO.Directory]::Exists($normalizedBootstrap)) {
        throw 'Native-helper bootstrap cleanup did not complete.'
    }
}

$nativeBootstrapParent = $null
$nativeBootstrapChild = $null
$nativeBootstrapEnvironmentChanged = $false
$nativeBootstrapFailed = $false

try {
    if ($null -eq ('FlIrNativePath' -as [type])) {
        $localApplicationData = [System.Environment]::GetFolderPath(
            [System.Environment+SpecialFolder]::LocalApplicationData
        )
        $nativeBootstrapParent = Assert-ManagedBootstrapDirectory -Path (
            [System.IO.Path]::Combine($localApplicationData, 'Temp')
        )
        $managedLiveRoot = ConvertTo-ManagedBootstrapFullPath -Path $liveGameRoot
        if (Test-IsWithinPath -Candidate $nativeBootstrapParent -Root $managedLiveRoot) {
            throw 'The native-helper bootstrap parent was under the forbidden live root.'
        }

        $bootstrapLeaf = 'bgee-itemrandomiser-native-bootstrap-' + [guid]::NewGuid().ToString('N')
        $nativeBootstrapChild = ConvertTo-ManagedBootstrapFullPath -Path (
            [System.IO.Path]::Combine($nativeBootstrapParent, $bootstrapLeaf)
        )
        if (
            -not (Test-IsWithinPath -Candidate $nativeBootstrapChild -Root $nativeBootstrapParent) -or
            (Test-IsWithinPath -Candidate $nativeBootstrapChild -Root $managedLiveRoot)
        ) {
            throw 'The generated native-helper bootstrap path escaped its validated parent.'
        }

        $null = [System.IO.Directory]::CreateDirectory($nativeBootstrapChild)
        $nativeBootstrapChild = Assert-ManagedBootstrapDirectory -Path $nativeBootstrapChild

        $nativeBootstrapEnvironmentChanged = $true
        [System.Environment]::SetEnvironmentVariable('TEMP', $nativeBootstrapChild, 'Process')
        [System.Environment]::SetEnvironmentVariable('TMP', $nativeBootstrapChild, 'Process')

        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class FlIrNativePath
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "GetLongPathNameW")]
    public static extern uint GetLongPathName(string shortPath, StringBuilder longPath, uint bufferLength);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "QueryDosDeviceW")]
    public static extern uint QueryDosDevice(string deviceName, StringBuilder targetPath, int maximumLength);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetDriveTypeW")]
    public static extern uint GetDriveType(string rootPathName);
}
'@
    }
}
catch {
    $nativeBootstrapFailed = $true
}
finally {
    if ($nativeBootstrapEnvironmentChanged) {
        try {
            [System.Environment]::SetEnvironmentVariable('TEMP', $callerTempEnvironment, 'Process')
            [System.Environment]::SetEnvironmentVariable('TMP', $callerTmpEnvironment, 'Process')
        }
        catch {
            $nativeBootstrapFailed = $true
        }
    }

    if ($null -ne $nativeBootstrapChild -and $null -ne $nativeBootstrapParent) {
        try {
            Remove-ManagedBootstrapDirectorySafely `
                -BootstrapPath $nativeBootstrapChild `
                -ExpectedParent $nativeBootstrapParent
        }
        catch {
            $nativeBootstrapFailed = $true
        }
    }
}

if ($nativeBootstrapFailed -or $null -eq ('FlIrNativePath' -as [type])) {
    Write-Error 'UNEXPECTED_FAIL Native-helper bootstrap failed.' -ErrorAction Continue
    exit 1
}
Write-Output 'PASS Safety_NativeHelperBootstrapped'

function Get-FixedDriveIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FullPath
    )

    if ($FullPath -notmatch '^(?<drive>[A-Za-z]):\\') {
        throw 'Only fully qualified local drive-letter paths are accepted.'
    }

    $driveName = $matches['drive'].ToUpperInvariant() + ':'
    $driveRoot = $driveName + '\'
    $driveType = [FlIrNativePath]::GetDriveType($driveRoot)
    if ($driveType -ne 3) {
        throw 'Configured paths must use a fixed local drive.'
    }

    $deviceBuffer = New-Object System.Text.StringBuilder 32768
    $deviceLength = [FlIrNativePath]::QueryDosDevice($driveName, $deviceBuffer, $deviceBuffer.Capacity)
    if ($deviceLength -eq 0) {
        throw 'The configured drive could not be resolved to a physical device.'
    }

    $deviceTarget = $deviceBuffer.ToString()
    $nullIndex = $deviceTarget.IndexOf([char] 0)
    if ($nullIndex -ge 0) {
        $deviceTarget = $deviceTarget.Substring(0, $nullIndex)
    }

    if (
        $deviceTarget.StartsWith('\??\', [System.StringComparison]::OrdinalIgnoreCase) -or
        $deviceTarget.StartsWith('\DosDevices\', [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $deviceTarget.StartsWith('\Device\', [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'SUBST and non-physical drive aliases are not accepted.'
    }

    [pscustomobject]@{
        Drive = $driveName
        Root = $driveRoot
        Device = $deviceTarget.TrimEnd('\')
    }
}

function Get-LongCanonicalExistingPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FullPath
    )

    $buffer = New-Object System.Text.StringBuilder 32768
    $length = [FlIrNativePath]::GetLongPathName($FullPath, $buffer, [uint32] $buffer.Capacity)
    if ($length -eq 0) {
        throw 'An existing test path could not be expanded to its long canonical form.'
    }
    if ($length -ge $buffer.Capacity) {
        $buffer = New-Object System.Text.StringBuilder ([int] $length + 1)
        $length = [FlIrNativePath]::GetLongPathName($FullPath, $buffer, [uint32] $buffer.Capacity)
        if ($length -eq 0 -or $length -ge $buffer.Capacity) {
            throw 'An existing test path exceeded the canonical path buffer.'
        }
    }

    ConvertTo-NormalizedFullPath -Path $buffer.ToString()
}

function Get-PhysicalPathKey {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FullPath,

        [Parameter(Mandatory = $true)]
        [psobject] $DriveIdentity
    )

    $relativePath = $FullPath.Substring(3).TrimStart('\', '/')
    if ($relativePath.Length -eq 0) {
        return $DriveIdentity.Device
    }

    $DriveIdentity.Device + '\' + $relativePath
}

function Test-IsWithinPhysicalPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $CandidateKey,

        [Parameter(Mandatory = $true)]
        [string] $RootKey
    )

    if ($CandidateKey.Equals($RootKey, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $CandidateKey.StartsWith($RootKey + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparsePointInPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FullPath
    )

    $pathRoot = [System.IO.Path]::GetPathRoot($FullPath)
    if ([string]::IsNullOrEmpty($pathRoot)) {
        throw 'A test path has no filesystem root.'
    }

    $relativePart = $FullPath.Substring($pathRoot.Length)
    $segments = @($relativePart -split '[\\/]' | Where-Object { $_.Length -gt 0 })
    $currentPath = $pathRoot

    foreach ($segment in $segments) {
        $currentPath = Join-Path $currentPath $segment
        if (-not (Test-Path -LiteralPath $currentPath)) {
            throw 'A configured test path does not exist.'
        }

        $currentItem = Get-Item -LiteralPath $currentPath -Force
        if (($currentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Reparse points are not accepted in configured test paths.'
        }
    }
}

function Resolve-SafeTestPath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Leaf', 'Container')]
        [string] $PathType
    )

    # Deny the literal live-root spelling before a provider touches the candidate. Alias
    # classes and the canonical physical identity are checked separately below.
    $lexicalFullPath = ConvertTo-NormalizedFullPath -Path $Path
    if (Test-IsWithinPath -Candidate $lexicalFullPath -Root $script:liveRootLexical) {
        throw 'A configured test path is under the forbidden live game root.'
    }
    if (
        $null -ne $script:liveRootCanonical -and
        (Test-IsWithinPath -Candidate $lexicalFullPath -Root $script:liveRootCanonical)
    ) {
        throw 'A configured test path resolves under the forbidden live game root.'
    }

    $driveIdentity = Get-FixedDriveIdentity -FullPath $lexicalFullPath
    $lexicalPhysicalKey = Get-PhysicalPathKey -FullPath $lexicalFullPath -DriveIdentity $driveIdentity
    if (
        $null -ne $script:livePhysicalRootKey -and
        (Test-IsWithinPhysicalPath -CandidateKey $lexicalPhysicalKey -RootKey $script:livePhysicalRootKey)
    ) {
        throw 'A configured test path resolves under the forbidden live game root.'
    }

    Assert-NoReparsePointInPath -FullPath $lexicalFullPath
    $resolvedPath = Get-LongCanonicalExistingPath -FullPath $lexicalFullPath
    $resolvedDriveIdentity = Get-FixedDriveIdentity -FullPath $resolvedPath
    $resolvedPhysicalKey = Get-PhysicalPathKey -FullPath $resolvedPath -DriveIdentity $resolvedDriveIdentity
    if (
        $null -ne $script:liveRootCanonical -and
        (Test-IsWithinPath -Candidate $resolvedPath -Root $script:liveRootCanonical)
    ) {
        throw 'A configured test path resolves under the forbidden live game root.'
    }
    if (
        $null -ne $script:livePhysicalRootKey -and
        (Test-IsWithinPhysicalPath -CandidateKey $resolvedPhysicalKey -RootKey $script:livePhysicalRootKey)
    ) {
        throw 'A configured test path resolves under the forbidden live game root.'
    }

    $resolvedItem = Get-Item -LiteralPath $resolvedPath -Force
    if ($PathType -eq 'Leaf' -and $resolvedItem.PSIsContainer) {
        throw 'A configured executable path resolved to a directory.'
    }
    if ($PathType -eq 'Container' -and -not $resolvedItem.PSIsContainer) {
        throw 'A configured fixture path did not resolve to a directory.'
    }

    if ($PathType -eq 'Leaf' -and -not $resolvedPath.EndsWith('.exe', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A configured executable path does not name an .exe file.'
    }

    $resolvedPath
}

function Assert-ExecutableIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [ValidateSet('WeiDU', 'Lua')]
        [string] $Role
    )

    if ($Role -eq 'WeiDU') {
        $expected = [ordered]@{
            Name = 'weidu.exe'
            ProductName = 'WeiDU'
            FileDescription = 'Weimer Dialogue Utilities'
            FileVersion = '249.00'
            ProductVersion = '249.00'
            OriginalFilename = 'weidu.exe'
        }
    }
    else {
        $expected = [ordered]@{
            Name = 'lua.exe'
            ProductName = 'Lua - The Programming Language'
            FileDescription = 'Lua Console Standalone Interpreter'
            FileVersion = '5.3.3'
            ProductVersion = '5.3.3'
            OriginalFilename = 'lua53.exe'
        }
    }

    $item = Get-Item -LiteralPath $Path -Force
    $version = $item.VersionInfo
    $actualName = [System.IO.Path]::GetFileName($Path)
    if (-not $actualName.Equals($expected.Name, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Role executable identity validation failed."
    }

    foreach ($propertyName in @('ProductName', 'FileDescription', 'FileVersion', 'ProductVersion', 'OriginalFilename')) {
        $actualValue = [string] $version.$propertyName
        $expectedValue = [string] $expected[$propertyName]
        $comparison = [System.StringComparison]::Ordinal
        if ($propertyName -eq 'OriginalFilename') {
            $comparison = [System.StringComparison]::OrdinalIgnoreCase
        }
        if (-not $actualValue.Equals($expectedValue, $comparison)) {
            throw "$Role executable identity validation failed."
        }
    }
}

function Invoke-ChildPowerShell {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ScriptPath,

        [string[]] $ScriptArguments = @()
    )

    $arguments = @('-NoProfile', '-File', $ScriptPath) + $ScriptArguments
    $output = @(& $script:powerShellHostPath @arguments 2>&1 | ForEach-Object { $_.ToString() })
    $exitCode = $LASTEXITCODE

    [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
    }
}

function Get-ValidatedModeBoundaryProtocol {
    param(
        [Parameter(Mandatory = $true)]
        [psobject] $Result
    )

    try {
        $records = @{}
        $summaryMatches = @()

        foreach ($line in @($Result.Output)) {
            if ([string]::IsNullOrEmpty($line)) {
                throw 'invalid'
            }

            $recordMatch = [regex]::Match($line, '^(?<status>PASS|FAIL) (?<name>[A-Za-z0-9_]+)$')
            if ($recordMatch.Success) {
                $name = $recordMatch.Groups['name'].Value
                if ($script:modeBoundaryAssertionNames -notcontains $name -or $records.ContainsKey($name)) {
                    throw 'invalid'
                }
                $records[$name] = $recordMatch.Groups['status'].Value
                continue
            }

            $summaryMatch = [regex]::Match($line, '^SUMMARY passed=(?<passed>\d+) failed=(?<failed>\d+)$')
            if ($summaryMatch.Success) {
                $summaryMatches += $summaryMatch
                continue
            }

            throw 'invalid'
        }

        if ($records.Count -ne $script:modeBoundaryAssertionNames.Count -or $summaryMatches.Count -ne 1) {
            throw 'invalid'
        }
        foreach ($name in $script:modeBoundaryAssertionNames) {
            if (-not $records.ContainsKey($name)) {
                throw 'invalid'
            }
        }

        $passedCount = @($records.Values | Where-Object { $_ -eq 'PASS' }).Count
        $failedCount = @($records.Values | Where-Object { $_ -eq 'FAIL' }).Count
        $summaryPassed = [int] $summaryMatches[0].Groups['passed'].Value
        $summaryFailed = [int] $summaryMatches[0].Groups['failed'].Value
        if (
            $passedCount + $failedCount -ne $records.Count -or
            $summaryPassed -ne $passedCount -or
            $summaryFailed -ne $failedCount
        ) {
            throw 'invalid'
        }

        $state = $null
        if ($passedCount -eq 10 -and $failedCount -eq 0 -and $Result.ExitCode -eq 0) {
            $state = 'Pass'
        }
        elseif (
            $passedCount -eq 9 -and
            $failedCount -eq 1 -and
            $records[$script:knownFutureAssertion] -eq 'FAIL' -and
            $Result.ExitCode -eq 1
        ) {
            foreach ($name in $script:modeBoundaryAssertionNames) {
                if ($name -ne $script:knownFutureAssertion -and $records[$name] -ne 'PASS') {
                    throw 'invalid'
                }
            }
            $state = 'IntentionalRed'
        }
        else {
            throw 'invalid'
        }

        $validatedLines = @(
            foreach ($name in $script:modeBoundaryAssertionNames) {
                "$($records[$name]) $name"
            }
            "SUMMARY passed=$passedCount failed=$failedCount"
        )

        [pscustomobject]@{
            State = $state
            Lines = $validatedLines
        }
    }
    catch {
        throw 'Mode boundary protocol validation failed.'
    }
}

function Invoke-RequiredPowerShellTest {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ScriptPath,

        [string[]] $ScriptArguments = @()
    )

    $result = Invoke-ChildPowerShell -ScriptPath $ScriptPath -ScriptArguments $ScriptArguments
    if ($result.ExitCode -ne 0) {
        throw "PowerShell test '$([System.IO.Path]::GetFileName($ScriptPath))' failed with exit code $($result.ExitCode)."
    }

    Write-Output "PASS PowerShell_$([System.IO.Path]::GetFileNameWithoutExtension($ScriptPath))"
}

function Invoke-WeiduParseCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourcePath,

        [Parameter(Mandatory = $true)]
        [ValidateSet('TP2', 'TPA', 'TPP')]
        [string] $SourceType
    )

    Push-Location $script:scratchRoot
    try {
        $parseOutput = @(& $script:resolvedWeiduPath --nogame --parse-check $SourceType $SourcePath 2>&1)
        $parseExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($parseExitCode -ne 0) {
        throw "WeiDU parse-check failed for '$([System.IO.Path]::GetFileName($SourcePath))' with exit code $parseExitCode."
    }

    $relativePath = $SourcePath.Substring($script:repositoryRoot.Length).TrimStart('\', '/')
    Write-Output "PASS WeiDUParse_$($relativePath.Replace('\', '/'))"
}

function Remove-ScratchDirectorySafely {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ScratchPath,

        [Parameter(Mandatory = $true)]
        [string] $ExpectedParent
    )

    $normalizedScratch = ConvertTo-NormalizedFullPath -Path $ScratchPath
    $normalizedParent = ConvertTo-NormalizedFullPath -Path $ExpectedParent
    $leafName = [System.IO.Path]::GetFileName($normalizedScratch)

    if (
        -not (Test-IsWithinPath -Candidate $normalizedScratch -Root $normalizedParent) -or
        $normalizedScratch.Equals($normalizedParent, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $leafName.StartsWith('bgee-itemrandomiser-unit-', [System.StringComparison]::Ordinal)
    ) {
        throw 'Refusing to clean a scratch path outside the validated disposable directory.'
    }

    if (Test-Path -LiteralPath $normalizedScratch) {
        Assert-NoReparsePointInPath -FullPath $normalizedScratch
        $nestedReparsePoints = @(
            Get-ChildItem -LiteralPath $normalizedScratch -Force -Recurse |
                Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 }
        )
        if ($nestedReparsePoints.Count -ne 0) {
            throw 'Refusing to clean a scratch tree containing a reparse point.'
        }

        Remove-Item -LiteralPath $normalizedScratch -Recurse -Force
    }
}

try {
    $liveRootLexical = ConvertTo-NormalizedFullPath -Path $liveGameRoot
    $liveRootCanonical = $null
    $livePhysicalRootKey = $null
    if (Test-Path -LiteralPath $liveRootLexical) {
        Assert-NoReparsePointInPath -FullPath $liveRootLexical
        $liveRootItem = Get-Item -LiteralPath $liveRootLexical -Force
        if (-not $liveRootItem.PSIsContainer) {
            throw 'The forbidden live game root did not resolve to a directory.'
        }
        $liveRootCanonical = Get-LongCanonicalExistingPath -FullPath $liveRootLexical
        $liveDriveIdentity = Get-FixedDriveIdentity -FullPath $liveRootCanonical
        $livePhysicalRootKey = Get-PhysicalPathKey `
            -FullPath $liveRootCanonical `
            -DriveIdentity $liveDriveIdentity
    }

    $resolvedWeiduPath = Resolve-SafeTestPath -Path $WeiduPath -PathType Leaf
    $resolvedLuaPath = Resolve-SafeTestPath -Path $LuaPath -PathType Leaf
    $resolvedSentinelBafDirectory = Resolve-SafeTestPath -Path $SentinelBafDirectory -PathType Container
    Assert-ExecutableIdentity -Path $resolvedWeiduPath -Role WeiDU
    Assert-ExecutableIdentity -Path $resolvedLuaPath -Role Lua
    Write-Output 'PASS Safety_ConfiguredPathsValidatedOutsideLiveRoot'
    Write-Output 'PASS Safety_ExecutableIdentitiesValidated'

    $scratchParent = Resolve-SafeTestPath -Path $callerTempRequest -PathType Container

    $scratchLeaf = 'bgee-itemrandomiser-unit-' + [guid]::NewGuid().ToString('N')
    $scratchRoot = ConvertTo-NormalizedFullPath -Path (Join-Path $scratchParent $scratchLeaf)
    if (-not (Test-IsWithinPath -Candidate $scratchRoot -Root $scratchParent)) {
        throw 'The generated scratch directory escaped its disposable parent.'
    }
    New-Item -ItemType Directory -Path $scratchRoot -ErrorAction Stop | Out-Null
    $scratchRoot = Resolve-SafeTestPath -Path $scratchRoot -PathType Container
    Write-Output 'PASS Safety_FreshDisposableScratchCreated'

    $powerShellHostPath = (Get-Process -Id $PID).Path
    if ([string]::IsNullOrWhiteSpace($powerShellHostPath) -or -not (Test-Path -LiteralPath $powerShellHostPath -PathType Leaf)) {
        throw 'The current PowerShell host executable could not be resolved.'
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

    $sentinelTestPath = Join-Path $PSScriptRoot 'Test-SentinelDependencies.ps1'
    Invoke-RequiredPowerShellTest -ScriptPath $sentinelTestPath -ScriptArguments @('-BafDirectory', $resolvedSentinelBafDirectory)

    $deferredTestNames = @(
        'Test-SentinelDependencies.ps1',
        'Test-ModeBoundary.ps1'
    )
    $otherPowerShellTests = @(
        Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'Test-*.ps1' -File |
            Where-Object { $deferredTestNames -notcontains $_.Name } |
            Sort-Object Name
    )
    foreach ($testScript in $otherPowerShellTests) {
        Invoke-RequiredPowerShellTest -ScriptPath $testScript.FullName
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
        $luaOutput = @(& $resolvedLuaPath $luaRunnerPath 2>&1)
        $luaExitCode = $LASTEXITCODE
        if ($luaExitCode -ne 0) {
            throw "Lua unit runner failed with exit code $luaExitCode."
        }
        Write-Output 'PASS Lua53_tests/lua/run.lua'
    }
    else {
        Write-Output 'SKIP Lua53_tests/lua/run.lua_NotPresent'
    }

    # Keep the known future assertion last. It is a real RED until Task 8 adds the seam.
    $modeBoundaryPath = Join-Path $PSScriptRoot 'Test-ModeBoundary.ps1'
    $modeBoundaryResult = Invoke-ChildPowerShell -ScriptPath $modeBoundaryPath
    $validatedModeBoundary = Get-ValidatedModeBoundaryProtocol -Result $modeBoundaryResult
    foreach ($line in $validatedModeBoundary.Lines) {
        Write-Output $line
    }

    if ($validatedModeBoundary.State -eq 'Pass') {
        Write-Output 'PASS PowerShell_Test-ModeBoundary'
    }
    else {
        Write-Output "INTENTIONAL_RED $knownFutureAssertion"
        $runnerExitCode = 1
    }
}
catch {
    $runnerExitCode = 1
    Write-Error "UNEXPECTED_FAIL $($_.Exception.Message)" -ErrorAction Continue
}
finally {
    foreach ($name in $environmentBackup.Keys) {
        [System.Environment]::SetEnvironmentVariable($name, $environmentBackup[$name], 'Process')
    }

    if ($null -ne $scratchRoot -and $null -ne $scratchParent) {
        try {
            Remove-ScratchDirectorySafely -ScratchPath $scratchRoot -ExpectedParent $scratchParent
        }
        catch {
            $runnerExitCode = 1
            Write-Error "UNEXPECTED_FAIL Scratch cleanup failed: $($_.Exception.Message)" -ErrorAction Continue
        }
    }
}

exit $runnerExitCode
