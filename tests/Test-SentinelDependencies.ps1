param(
    [Parameter(Mandatory = $true)]
    [string] $BafDirectory
)

$ErrorActionPreference = 'Stop'

$expectedCampaigns = [ordered]@{
    flSqueakedar0602 = 18
    flSqueakedbg2600 = 13
}
$expectedTierFileCount = ($expectedCampaigns.Values | Measure-Object -Sum).Sum

$blockPattern = '(?ms)^[ \t]*IF[ \t]*\r?\n(?<triggers>.*?)^[ \t]*THEN[ \t]*\r?\n(?<actions>.*?)^[ \t]*END[ \t]*(?=\r?$)'
$ifMarkerPattern = '(?m)^[ \t]*IF[ \t]*\r?$'
$thenMarkerPattern = '(?m)^[ \t]*THEN[ \t]*\r?$'
$endMarkerPattern = '(?m)^[ \t]*END[ \t]*\r?$'
$orMarkerPattern = '(?im)^[ \t]*OR\s*\('
$sentinelCandidatePattern = '(?im)^[ \t]*(?:!?Global|SetGlobal)\s*\(\s*"flSqueaked'
$sentinelCallPattern = '(?im)^[ \t]*(?:!?Global|SetGlobal)\s*\(\s*"(?<name>flSqueaked[^"]+)"\s*,\s*"GLOBAL"\s*,\s*-?\d+\s*\)[ \t]*\r?$'
$doneCandidatePattern = '(?im)^[ \t]*(?:!?Global|SetGlobal)\s*\(\s*"fl[^"]*tDone"'
$doneCallPattern = '(?im)^[ \t]*(?:!?Global|SetGlobal)\s*\(\s*"(?<name>fl[^"]*tDone)"\s*,\s*"GLOBAL"\s*,\s*-?\d+\s*\)[ \t]*\r?$'
$doneWritePattern = '(?im)^[ \t]*SetGlobal\s*\(\s*"(?<name>fl[^"]*tDone)"\s*,\s*"GLOBAL"\s*,\s*1\s*\)[ \t]*\r?$'
$doneDependencyPattern = '(?im)^[ \t]*Global\s*\(\s*"(?<name>fl[^"]*tDone)"\s*,\s*"GLOBAL"\s*,\s*1\s*\)[ \t]*\r?$'
$blankMatchEvaluator = [System.Text.RegularExpressions.MatchEvaluator] {
    param($match)

    [regex]::Replace($match.Value, '[^\r\n]', ' ')
}

function Get-UniqueMatches {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text,

        [Parameter(Mandatory = $true)]
        [string] $Pattern,

        [Parameter(Mandatory = $true)]
        [string] $GroupName
    )

    @(
        [regex]::Matches($Text, $Pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase) |
            ForEach-Object { $_.Groups[$GroupName].Value } |
            Sort-Object -Unique
    )
}

function Remove-BafComments {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text
    )

    $commentPattern = '(?ms)/\*.*?\*/|^[ \t]*//[^\r\n]*'
    [regex]::Replace($Text, $commentPattern, $script:blankMatchEvaluator)
}

function Get-ParsedBaf {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text,

        [Parameter(Mandatory = $true)]
        [string] $FileName
    )

    $code = Remove-BafComments -Text $Text
    $blocks = @([regex]::Matches($code, $script:blockPattern))
    $ifCount = [regex]::Matches($code, $script:ifMarkerPattern).Count
    $thenCount = [regex]::Matches($code, $script:thenMarkerPattern).Count
    $endCount = [regex]::Matches($code, $script:endMarkerPattern).Count

    if (
        $blocks.Count -ne $ifCount -or
        $blocks.Count -ne $thenCount -or
        $blocks.Count -ne $endCount
    ) {
        throw "Structural parse failure in tier script '$FileName': parsed $($blocks.Count) blocks but found IF=$ifCount, THEN=$thenCount, END=$endCount markers."
    }

    $residue = [regex]::Replace($code, $script:blockPattern, $script:blankMatchEvaluator)
    if ([regex]::IsMatch($residue, '\S')) {
        throw "Structural parse failure in tier script '$FileName': non-comment content remains outside parsed IF/THEN/END blocks."
    }

    [pscustomobject]@{
        Code = $code
        Blocks = $blocks
    }
}

function Assert-CompleteTrackedCalls {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Text,

        [Parameter(Mandatory = $true)]
        [string] $CandidatePattern,

        [Parameter(Mandatory = $true)]
        [string] $CompletePattern,

        [Parameter(Mandatory = $true)]
        [string] $Description,

        [Parameter(Mandatory = $true)]
        [string] $FileName
    )

    $candidateCount = [regex]::Matches($Text, $CandidatePattern).Count
    $completeCount = [regex]::Matches($Text, $CompletePattern).Count
    if ($candidateCount -ne $completeCount) {
        throw "Tier script '$FileName' contains an incomplete or malformed $Description call."
    }
}

$resolvedDirectory = Resolve-Path -LiteralPath $BafDirectory
$tierScripts = @(Get-ChildItem -LiteralPath $resolvedDirectory -File -Filter 'fltier*.baf' | Sort-Object Name)

if ($tierScripts.Count -ne $expectedTierFileCount) {
    throw "Tier-script coverage failure: expected exactly $expectedTierFileCount decompiled fltier*.baf files, found $($tierScripts.Count)."
}

$tierFileNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($tierScript in $tierScripts) {
    if (-not $tierFileNames.Add($tierScript.Name)) {
        throw "Tier-script coverage failure: duplicate tier input '$($tierScript.Name)'."
    }
}

$campaigns = @{}
$doneOwners = @{}

foreach ($tierScript in $tierScripts) {
    $content = Get-Content -LiteralPath $tierScript.FullName -Raw
    $parsed = Get-ParsedBaf -Text $content -FileName $tierScript.Name

    Assert-CompleteTrackedCalls -Text $parsed.Code -CandidatePattern $sentinelCandidatePattern -CompletePattern $sentinelCallPattern -Description 'flSqueaked* sentinel' -FileName $tierScript.Name
    Assert-CompleteTrackedCalls -Text $parsed.Code -CandidatePattern $doneCandidatePattern -CompletePattern $doneCallPattern -Description 'fl*tDone tier-completion' -FileName $tierScript.Name

    $sentinels = @(Get-UniqueMatches -Text $parsed.Code -Pattern $sentinelCallPattern -GroupName 'name')
    $doneWriteMatches = @([regex]::Matches($parsed.Code, $doneWritePattern))

    if ($sentinels.Count -eq 0) {
        throw "Tier script '$($tierScript.Name)' does not reference a complete flSqueaked* sentinel call."
    }

    if ($sentinels.Count -ne 1) {
        throw "Tier script '$($tierScript.Name)' references an ambiguous set of flSqueaked* sentinels."
    }

    if ($doneWriteMatches.Count -ne 1) {
        throw "Tier script '$($tierScript.Name)' does not write exactly one complete fl*tDone global."
    }

    $sentinel = $sentinels[0]
    $doneWrite = $doneWriteMatches[0].Groups['name'].Value

    if (-not $expectedCampaigns.Contains($sentinel)) {
        throw "Tier-script coverage failure: unexpected sentinel campaign '$sentinel'."
    }

    if ($doneOwners.ContainsKey($doneWrite)) {
        throw "Tier-script coverage failure: tier-completion global '$doneWrite' is duplicated by '$($doneOwners[$doneWrite])' and '$($tierScript.Name)'."
    }
    $doneOwners[$doneWrite] = $tierScript.Name

    if (-not $campaigns.ContainsKey($sentinel)) {
        $campaigns[$sentinel] = [ordered]@{
            scriptCount = 0
            expected = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
            actual = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
            sentinelWriteBlocks = 0
        }
    }

    $campaigns[$sentinel].scriptCount += 1
    if (-not $campaigns[$sentinel].expected.Add($doneWrite)) {
        throw "Tier-script coverage failure: duplicate tier-completion input '$doneWrite' for sentinel '$sentinel'."
    }

    foreach ($block in $parsed.Blocks) {
        $sentinelWritePattern = '(?im)^[ \t]*SetGlobal\s*\(\s*"' + [regex]::Escape($sentinel) + '"\s*,\s*"GLOBAL"\s*,\s*1\s*\)[ \t]*\r?$'
        $sentinelWrites = @([regex]::Matches($block.Groups['actions'].Value, $sentinelWritePattern))
        if ($sentinelWrites.Count -eq 0) {
            continue
        }

        if ($sentinelWrites.Count -ne 1) {
            throw "Sentinel '$sentinel' is written to 1 more than once in one tier-script block."
        }

        $trackedDoneGuardCount = [regex]::Matches($block.Groups['triggers'].Value, $doneCallPattern).Count
        $positiveDoneGuardCount = [regex]::Matches($block.Groups['triggers'].Value, $doneDependencyPattern).Count
        if ($trackedDoneGuardCount -ne $positiveDoneGuardCount) {
            throw "Sentinel '$sentinel' completion trigger contains a nonpositive or negated fl*tDone guard; all tracked guards must be unnegated positive dependencies."
        }

        if ([regex]::IsMatch($block.Groups['triggers'].Value, $orMarkerPattern)) {
            throw "Sentinel '$sentinel' completion trigger uses OR(...); tier-completion dependencies must be conjunctive."
        }

        $campaigns[$sentinel].sentinelWriteBlocks += 1
        $dependencies = Get-UniqueMatches -Text $block.Groups['triggers'].Value -Pattern $doneDependencyPattern -GroupName 'name'
        foreach ($dependency in $dependencies) {
            [void] $campaigns[$sentinel].actual.Add($dependency)
        }
    }
}

$missingCampaigns = @($expectedCampaigns.Keys | Where-Object { -not $campaigns.ContainsKey($_) })
$extraCampaigns = @($campaigns.Keys | Where-Object { -not $expectedCampaigns.Contains($_) })
if ($missingCampaigns.Count -gt 0 -or $extraCampaigns.Count -gt 0) {
    throw "Tier-script coverage failure: sentinel campaign set does not match the expected EET campaign set."
}

if ($doneOwners.Count -ne $expectedTierFileCount) {
    throw "Tier-script coverage failure: expected $expectedTierFileCount unique tier-completion inputs, found $($doneOwners.Count)."
}

foreach ($sentinel in $expectedCampaigns.Keys) {
    $expectedCount = $expectedCampaigns[$sentinel]
    $campaign = $campaigns[$sentinel]
    if ($campaign.scriptCount -ne $expectedCount -or $campaign.expected.Count -ne $expectedCount) {
        throw "Tier-script coverage failure for sentinel '$sentinel': expected $expectedCount unique tier files, found $($campaign.scriptCount) files and $($campaign.expected.Count) tier-completion inputs."
    }
}

$results = @(
    foreach ($sentinel in @($campaigns.Keys | Sort-Object)) {
        $campaign = $campaigns[$sentinel]
        if ($campaign.sentinelWriteBlocks -ne 1) {
            throw "Sentinel '$sentinel' is written to 1 by $($campaign.sentinelWriteBlocks) tier-script blocks; expected exactly one."
        }

        $extraCount = @($campaign.actual | Where-Object { -not $campaign.expected.Contains($_) }).Count
        $missingCount = @($campaign.expected | Where-Object { -not $campaign.actual.Contains($_) }).Count

        [pscustomobject][ordered]@{
            sentinel = $sentinel
            expectedCount = $campaign.expected.Count
            actualCount = $campaign.actual.Count
            extraCount = $extraCount
            missingCount = $missingCount
            passed = ($extraCount -eq 0 -and $missingCount -eq 0)
        }
    }
)

$results | Format-Table -AutoSize | Out-Host

if (@($results | Where-Object { -not $_.passed }).Count -gt 0) {
    exit 1
}
