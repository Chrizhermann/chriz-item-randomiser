param()

$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$tp2Path = Join-Path $repositoryRoot 'randomiser.tp2'
$baselineCommit = '1efc64a'

function Remove-BlockCommentsPreservingLines {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text
    )

    $blankComment = [System.Text.RegularExpressions.MatchEvaluator] {
        param($match)

        [regex]::Replace($match.Value, '[^\r\n]', ' ')
    }
    $activeText = [regex]::Replace($Text, '(?s)/\*.*?\*/', $blankComment)
    if ($activeText.Contains('/*') -or $activeText.Contains('*/')) {
        throw 'Malformed block comment in randomiser.tp2.'
    }

    $activeText
}

function Get-ComponentBodies {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text
    )

    $topLevelBegins = @(
        [regex]::Matches(
            $Text,
            '(?im)^BEGIN\b[^\r\n]*\bDESIGNATED\s+(?<number>\d+)\b[^\r\n]*'
        )
    )
    $bodies = @{}

    for ($index = 0; $index -lt $topLevelBegins.Count; $index++) {
        $marker = $topLevelBegins[$index]
        $bodyEnd = $Text.Length
        if ($index + 1 -lt $topLevelBegins.Count) {
            $bodyEnd = $topLevelBegins[$index + 1].Index
        }

        $componentNumber = [int] $marker.Groups['number'].Value
        if ($bodies.ContainsKey($componentNumber)) {
            throw "Duplicate component body for DESIGNATED $componentNumber."
        }

        $bodies[$componentNumber] = $Text.Substring($marker.Index, $bodyEnd - $marker.Index)
    }

    $bodies
}

function Test-SingleWeiduActionSetting {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Body,

        [Parameter(Mandatory = $true)]
        [ValidateSet(0, 1)]
        [int] $ExpectedValue
    )

    $allSettings = @([regex]::Matches($Body, '(?im)^[ \t]*OUTER_SET[ \t]+weidu_action\b[^\r\n]*\r?$'))
    if ($allSettings.Count -ne 1) {
        return $false
    }

    $setting = [regex]::Match(
        $allSettings[0].Value,
        '(?im)^[ \t]*OUTER_SET[ \t]+weidu_action[ \t]*=[ \t]*(?<value>-?\d+)[ \t]*(?://[^\r\n]*)?\r?$'
    )
    $setting.Success -and [int] $setting.Groups['value'].Value -eq $ExpectedValue
}

function Get-WeiduActionIncludeCount {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text
    )

    [regex]::Matches(
        $Text,
        '(?im)^[ \t]*INCLUDE[ \t]+["~](?:[^"~\r\n]*[/\\])?weidu_action\.tpa["~][ \t]*(?://[^\r\n]*)?\r?$'
    ).Count
}

function Test-BaselinePathUnchanged {
    param(
        [Parameter(Mandatory = $true)]
        [string] $RelativePath
    )

    Push-Location $script:repositoryRoot
    try {
        & git cat-file -e "$script:baselineCommit`^{commit}" 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Required baseline commit is unavailable."
        }

        & git diff --quiet $script:baselineCommit -- $RelativePath
        $diffExitCode = $LASTEXITCODE
        if ($diffExitCode -gt 1) {
            throw "git diff failed while checking a protected Mode 2 path."
        }

        $untracked = @(& git ls-files --others --exclude-standard -- $RelativePath)
        if ($LASTEXITCODE -ne 0) {
            throw "git ls-files failed while checking a protected Mode 2 path."
        }

        $diffExitCode -eq 0 -and $untracked.Count -eq 0
    }
    finally {
        Pop-Location
    }
}

function Test-NoManifestRuntimeReferences {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Bodies
    )

    $forbiddenNames = @(
        'M_FLDLV.lua',
        'FLDLVCor.lua',
        'FLDLVMan.lua',
        'FLDLV.menu',
        'delivery_manifest.tpa',
        'delivery_neutralize.tpa',
        'delivery_special.tpa'
    )

    foreach ($body in $Bodies) {
        foreach ($name in $forbiddenNames) {
            if ($body.IndexOf($name, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $false
            }
        }
    }

    $true
}

function Test-ExplicitMode1DeliveryBackendBranch {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Bodies
    )

    foreach ($body in $Bodies) {
        $hasBackendSelector = $body -match '(?im)^[ \t]*ACTION_IF[^\r\n]*(?:eeex-manifest-v1|legacy-bcs-v1)[^\r\n]*BEGIN[ \t]*(?://[^\r\n]*)?\r?$'
        $hasExplicitElse = $body -match '(?im)^[ \t]*END[ \t]+ELSE[ \t]+BEGIN[ \t]*(?://[^\r\n]*)?\r?$'
        $hasLegacyRoute = $body -match '(?im)^[ \t]*INCLUDE[ \t]+["~]randomiser/lib/delivery\.tpa["~]'
        $hasManifestRoute = $body -match '(?im)^[ \t]*INCLUDE[ \t]+["~]randomiser/lib/delivery_manifest\.tpa["~]'
        $hasNeutralizationRoute = $body -match '(?im)^[ \t]*INCLUDE[ \t]+["~]randomiser/lib/delivery_neutralize\.tpa["~]'

        if (-not (
            $hasBackendSelector -and
            $hasExplicitElse -and
            $hasLegacyRoute -and
            $hasManifestRoute -and
            $hasNeutralizationRoute
        )) {
            return $false
        }
    }

    $true
}

function Add-AssertionResult {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [bool] $Passed
    )

    $script:assertionResults += [pscustomobject]@{
        Name = $Name
        Passed = $Passed
    }
}

if (-not (Test-Path -LiteralPath $tp2Path -PathType Leaf)) {
    throw 'randomiser.tp2 was not found at the repository root.'
}

$tp2Text = [System.IO.File]::ReadAllText($tp2Path)
$activeTp2Text = Remove-BlockCommentsPreservingLines -Text $tp2Text
$components = Get-ComponentBodies -Text $activeTp2Text
foreach ($requiredComponent in @(1100, 1200, 1300, 1400)) {
    if (-not $components.ContainsKey($requiredComponent)) {
        throw "Required component $requiredComponent was not found."
    }
}

$assertionResults = @()
Add-AssertionResult -Name 'Mode1_1100_SetsWeiduAction0' -Passed (Test-SingleWeiduActionSetting -Body $components[1100] -ExpectedValue 0)
Add-AssertionResult -Name 'Mode1_1200_SetsWeiduAction0' -Passed (Test-SingleWeiduActionSetting -Body $components[1200] -ExpectedValue 0)
Add-AssertionResult -Name 'Mode2_1300_SetsWeiduAction1' -Passed (Test-SingleWeiduActionSetting -Body $components[1300] -ExpectedValue 1)
Add-AssertionResult -Name 'Mode2_1400_SetsWeiduAction1' -Passed (Test-SingleWeiduActionSetting -Body $components[1400] -ExpectedValue 1)

$weiduActionIncludeIsIsolated = (
    (Get-WeiduActionIncludeCount -Text $activeTp2Text) -eq 2 -and
    (Get-WeiduActionIncludeCount -Text $components[1100]) -eq 0 -and
    (Get-WeiduActionIncludeCount -Text $components[1200]) -eq 0 -and
    (Get-WeiduActionIncludeCount -Text $components[1300]) -eq 1 -and
    (Get-WeiduActionIncludeCount -Text $components[1400]) -eq 1
)
Add-AssertionResult -Name 'Mode2_Only1300And1400IncludeWeiduAction' -Passed $weiduActionIncludeIsIsolated
Add-AssertionResult -Name 'Mode2_WeiduActionLibraryMatches1efc64a' -Passed (Test-BaselinePathUnchanged -RelativePath 'lib/weidu_action.tpa')
Add-AssertionResult -Name 'Mode2_ItemListsMatch1efc64a' -Passed (Test-BaselinePathUnchanged -RelativePath 'lists/items/mode2')
Add-AssertionResult -Name 'Mode2_LocationListsMatch1efc64a' -Passed (Test-BaselinePathUnchanged -RelativePath 'lists/locations/mode2')
Add-AssertionResult -Name 'Mode2_ComponentsExcludeManifestRuntimeAssets' -Passed (Test-NoManifestRuntimeReferences -Bodies @($components[1300], $components[1400]))
Add-AssertionResult -Name 'FUTURE_Mode1_ExplicitManifestVersusLegacyDeliveryBranch' -Passed (Test-ExplicitMode1DeliveryBackendBranch -Bodies @($components[1100], $components[1200]))

foreach ($result in $assertionResults) {
    $status = 'FAIL'
    if ($result.Passed) {
        $status = 'PASS'
    }
    Write-Output "$status $($result.Name)"
}

$passedCount = @($assertionResults | Where-Object { $_.Passed }).Count
$failedCount = $assertionResults.Count - $passedCount
Write-Output "SUMMARY passed=$passedCount failed=$failedCount"

if ($failedCount -ne 0) {
    exit 1
}
