[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$runtimeRoot = $PSScriptRoot
$setupPath = Join-Path $runtimeRoot 'setup-probe-fixture.tp2'
$wrapperPath = Join-Path $runtimeRoot 'Invoke-ProbeFixture.ps1'
$configPath = Join-Path $runtimeRoot 'M_FLRTP.lua'
$helperPath = Join-Path $runtimeRoot 'ProbeFixture.Helpers.ps1'

foreach ($required in @($setupPath, $wrapperPath, $configPath, $helperPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing runtime probe fixture source '$([System.IO.Path]::GetFileName($required))'."
    }
}

$setup = [System.IO.File]::ReadAllText($setupPath)
$wrapper = [System.IO.File]::ReadAllText($wrapperPath)
$config = [System.IO.File]::ReadAllText($configPath)
$helper = [System.IO.File]::ReadAllText($helperPath)

if ($setup -notmatch [regex]::Escape("engine_name = `"Baldur's Gate - Enhanced Edition Trilogy - IR Test b600e94`"")) {
    throw 'The TP2 does not independently require the isolated disposable engine profile.'
}
if ($setup -notmatch 'STRING_EQUAL_CASE\s+~AREAV1\.0~' -or
    $wrapper -notmatch "-cne\s+'AREAV1\.0'" -or
    $setup -match 'AREA V1\.0' -or $wrapper -match "'AREA V1\.0'") {
    throw 'The donor gate does not compare the exact eight-byte ARE signature and version.'
}
if ($wrapper -notmatch [regex]::Escape("C:\Users\chris\Games\EET-IR-Test-b600e94\bg2") -or
    $wrapper -notmatch [regex]::Escape("C:\Games\Baldur's Gate II Enhanced Edition modded")) {
    throw 'The wrapper does not name both the one allowed disposable root and forbidden live root.'
}
if ($wrapper -notmatch 'OrdinalIgnoreCase' -or $wrapper -notmatch 'ReparsePoint') {
    throw 'The wrapper does not compare canonical paths and reject reparse-point aliases.'
}
if ($wrapper -notmatch 'FLIR_PROBE_AREA' -or $wrapper -notmatch 'FLIR_PROBE_X' -or
    $wrapper -notmatch 'FLIR_PROBE_Y') {
    throw 'The current-area identity and anchor are not supplied through ephemeral environment values.'
}
if ($wrapper -match '(?im)^\s*param\s*\([^)]*AreaResref' -or
    $wrapper -match '(?im)Write-(?:Output|Host|Verbose|Debug)[^\r\n]*(?:area|FLIR_PROBE_AREA)') {
    throw 'The caller-supplied story-area identity can escape through a parameter or output channel.'
}
if ($wrapper -notmatch [regex]::Escape('--biff-get') -or
    $wrapper -notmatch [regex]::Escape('--force-install-list') -or
    $wrapper -notmatch [regex]::Escape('--force-uninstall-list')) {
    throw 'The wrapper does not provide read-only donor extraction plus reproducible install/uninstall paths.'
}
if ($wrapper -notmatch "'--nogame'\s*,\s*'--parse-check'\s*,\s*'TP2'\s*,\s*\`$setupPath" -or
    $wrapper -notmatch "FailureCode\s+'PARSE_CHECK_FAILED'") {
    throw 'The exact disposable WeiDU is not parse-checking the fixture TP2 before mutation.'
}
if ($wrapper -notmatch "--force-uninstall-list', '0',[\s\S]+--force-install-list', '0'" -or
    $wrapper -notmatch 'SETUP-PROBE-FIXTURE\.TP2') {
    throw 'An already-installed fixture cannot be atomically rebuilt from current synthetic source.'
}
if ($wrapper -notmatch 'Get-CimInstance\s+Win32_Process' -or
    $wrapper -notmatch 'Baldur\.exe|InfinityLoader\.exe') {
    throw 'The wrapper does not fail closed while an Enhanced Edition process is open.'
}
if ($wrapper -match '(?i)(Start-Process[^\r\n]*(?:Baldur|InfinityLoader)|Stop-Process|taskkill\.exe|handover_v4|Interval-Save|yaga dead)') {
    throw 'The fixture may launch/stop the game or refer to a protected repair/save artifact.'
}
if ($wrapper -notmatch [regex]::Escape(". (Join-Path `$PSScriptRoot 'ProbeFixture.Helpers.ps1')") -or
    $wrapper -notmatch 'Invoke-ProbeDonorAcquisitionOpaque' -or
    $wrapper -match '(?m)^\s*(?:Copy|Move)-Item[^\r\n]*(?:overrideDonor|extracted)' -or
    $helper -notmatch 'catch\s*\{\s*throw\s+''FLIR_PROBE_ERR DONOR_ACQUISITION_FAILED''') {
    throw 'Donor acquisition failures are not confined to the opaque helper boundary.'
}

$expectedItems = @('FLRTPIT', 'FLRTPJ', 'FLRTPK', 'FLRTPFL')
$expectedCreatures = @('FLRTPU', 'FLRTPD1', 'FLRTPD2', 'FLRTPL', 'FLRTPF')
foreach ($item in $expectedItems) {
    if ($setup -notmatch [regex]::Escape("CREATE ITM ~$item~")) {
        throw "Synthetic item '$item' is not created."
    }
}
foreach ($creature in $expectedCreatures) {
    if ($setup -notmatch [regex]::Escape("CREATE CRE ~$creature~")) {
        throw "Synthetic creature '$creature' is not created."
    }
}
foreach ($resref in @($expectedItems + $expectedCreatures + @('FLRTPRA', 'FLRTPC', 'FLRTPP', 'FLRTPD'))) {
    if ($resref.Length -gt 8 -or $resref -cnotmatch '^[A-Z0-9#_-]+$') {
        throw "Fixture resref '$resref' is not engine-safe."
    }
}
if ([System.IO.Path]::GetFileNameWithoutExtension($configPath).Length -gt 8 -or
    $config -notmatch 'FLDLVProbe\s*=\s*FLDLVProbe\s+or\s+\{\}' -or
    $config -notmatch 'creature_script\s*=\s*"FLRTPU"' -or
    $config -notmatch 'duplicate_script\s*=\s*"FLRTPD"' -or
    $config -notmatch 'full_script\s*=\s*"FLRTPF"' -or
    $config -notmatch 'container_script\s*=\s*"FLRTPC"' -or
    $config -notmatch 'pile_script\s*=\s*"FLRTPP"' -or
    $config -notmatch 'item_resrefs\s*=\s*\{' -or
    $config -notmatch 'charge_triples\s*=\s*\{' -or
    $config -notmatch 'ability_maxima\s*=\s*\{' -or
    $config -notmatch 'ability_maxima\s*=\s*\{[\s\S]*creature\s*=\s*\{\s*7\s*,\s*11\s*,\s*13\s*\}' -or
    $config -notmatch 'ability_maxima\s*=\s*\{[\s\S]*container\s*=\s*\{\s*17\s*,\s*19\s*,\s*23\s*\}' -or
    $config -notmatch 'ability_maxima\s*=\s*\{[\s\S]*pile\s*=\s*\{\s*29\s*,\s*31\s*,\s*37\s*\}' -or
    $config -notmatch 'ability_maxima\s*=\s*\{[\s\S]*filler\s*=\s*\{\s*0\s*,\s*0\s*,\s*0\s*\}' -or
    $config -notmatch 'items\s*=\s*\{' -or
    $config -notmatch 'creature\s*=\s*\{\s*resref\s*=\s*"FLRTPIT"\s*,\s*charges\s*=\s*\{\s*2\s*,\s*3\s*,\s*5\s*\}' -or
    $config -notmatch 'container\s*=\s*\{\s*resref\s*=\s*"FLRTPJ"\s*,\s*charges\s*=\s*\{\s*7\s*,\s*11\s*,\s*13\s*\}' -or
    $config -notmatch 'pile\s*=\s*\{\s*resref\s*=\s*"FLRTPK"\s*,\s*charges\s*=\s*\{\s*17\s*,\s*19\s*,\s*23\s*\}') {
    throw 'The disposable M_ config does not publish the complete synthetic probe contract.'
}
if ($config -match '(?i)(Infinity_DisplayString|io\.|os\.|AR\d{4}|BD\d{4}|OH\d{4}|Interval-Save|yaga dead)') {
    throw 'The disposable M_ config has side effects or exposes story/save identities.'
}
if ($setup -notmatch 'COPY\s+~%flir_probe_config_source%~\s+~override/M_FLRTP\.lua~' -or
    $wrapper -notmatch [regex]::Escape("'--args', `$configPath")) {
    throw 'The synthetic M_ config is not installed through the guarded disposable component.'
}
$declaredSyntheticNames = @([regex]::Matches($setup, '~(FL[A-Z0-9#_-]+)~') | ForEach-Object { $_.Groups[1].Value })
if (@($declaredSyntheticNames | Where-Object { $_.Length -gt 8 }).Count -ne 0) {
    throw 'At least one engine-loaded synthetic fixture name exceeds eight characters.'
}
if ($setup -match '(?i)\b(?:AR|BD|OH)\d{4}\b' -or
    $setup -match '(?i)(handover_v4|Interval-Save|yaga dead)') {
    throw 'The committed fixture contains a story area, location clue, or protected save/script name.'
}

if ($setup -notmatch 'WRITE_SHORT\s+\([^\r\n]+\+\s*0x22\)\s+7' -or
    $setup -notmatch 'WRITE_SHORT\s+\([^\r\n]+\+\s*0x22\)\s+11' -or
    $setup -notmatch 'WRITE_SHORT\s+\([^\r\n]+\+\s*0x22\)\s+13') {
    throw 'The charged probe item does not declare three independently recognizable use-count maxima.'
}
if ($setup -notmatch 'WRITE_ASCIIE\s+0x280\s+~FLRTPD~\s+#32') {
    throw 'The two duplicate instances do not share one deliberate synthetic script name.'
}
if ($setup -notmatch 'flir_probe_fill_count\s*<\s*15' -or
    $setup -notmatch 'flir_probe_fill_count\s*<\s*16') {
    throw 'The last-slot and completely-full inventory fixtures are not explicit.'
}
$validCoreStatBlocks = @([regex]::Matches(
    $setup,
    'WRITE_BYTE\s+0x237\s+1[\s\S]*?WRITE_BYTE\s+0x238\s+10[\s\S]*?WRITE_BYTE\s+0x23a\s+10[\s\S]*?WRITE_BYTE\s+0x23b\s+10[\s\S]*?WRITE_BYTE\s+0x23c\s+10[\s\S]*?WRITE_BYTE\s+0x23d\s+10[\s\S]*?WRITE_BYTE\s+0x23e\s+10[\s\S]*?WRITE_BYTE\s+0x23f\s+10[\s\S]*?WRITE_BYTE\s+0x27b\s+0x22'
))
if ($validCoreStatBlocks.Count -ne 5) {
    throw 'Every synthetic CRE must have valid sex, ability, morale, and alignment fields.'
}
if ($setup -notmatch 'WRITE_LONG\s+0x54\s+flir_probe_actor_offset' -or
    $setup -notmatch 'WRITE_SHORT\s+0x58\s+5' -or
    $setup -notmatch 'WRITE_LONG\s+0x70\s+flir_probe_container_offset' -or
    $setup -notmatch 'WRITE_SHORT\s+0x74\s+2' -or
    $setup -notmatch 'WRITE_ASCIIE\s+\(flir_probe_container_offset\s*\+\s*0x00\)\s+~FLRTPC~\s+#32') {
    throw 'The synthetic area does not replace its actor/container tables with the controlled targets.'
}
$externalActorFlags = @([regex]::Matches(
    $setup,
    'WRITE_LONG\s+\((?:flir_probe_actor_offset|flir_probe_actor)\s*\+\s*0x28\)\s+1'
))
$embeddedActorOffsets = @([regex]::Matches(
    $setup,
    'WRITE_LONG\s+\((?:flir_probe_actor_offset|flir_probe_actor)\s*\+\s*0x88\)\s+0'
))
$embeddedActorSizes = @([regex]::Matches(
    $setup,
    'WRITE_LONG\s+\((?:flir_probe_actor_offset|flir_probe_actor)\s*\+\s*0x8c\)\s+0'
))
if ($externalActorFlags.Count -ne 5 -or
    $embeddedActorOffsets.Count -ne 5 -or
    $embeddedActorSizes.Count -ne 5) {
    throw 'Each ARE actor binary record must set external-CRE bit 0 and clear embedded-CRE offset/size.'
}
if ($setup -notmatch 'WRITE_SHORT\s+\(flir_probe_container_offset\s*\+\s*0x24\)\s+8') {
    throw 'The controlled container is not an inert invisible type-8 container.'
}
if ($setup -notmatch 'WRITE_ASCIIE\s+\(flir_probe_pile_offset\s*\+\s*0x00\)\s+~FLRTPP~\s+#32' -or
    $setup -notmatch 'WRITE_SHORT\s+\(flir_probe_pile_offset\s*\+\s*0x24\)\s+4') {
    throw 'The named ground-pile fixture is absent or is not a type-4 pile.'
}
$actorRemovalGuards = @([regex]::Matches($setup, 'WRITE_LONG\s+\([^\r\n]+\+\s*0x38\)\s+0xffffffff'))
if ($actorRemovalGuards.Count -ne 5) {
    throw 'Every synthetic placed actor must have an infinite removal timer.'
}
foreach ($seededCreature in @('FLRTPU', 'FLRTPD1', 'FLRTPD2')) {
    $boundedCreature = "(?s)CREATE CRE ~$seededCreature~(?:(?!CREATE CRE).)*ADD_CRE_ITEM ~FLRTPFL~"
    if ($setup -notmatch $boundedCreature) {
        throw "Synthetic target '$seededCreature' has no harmless iteration seed."
    }
}
if ($setup -match 'ADD_CRE_ITEM\s+~(?:FLRTPIT|FLRTPJ|FLRTPK)~' -or
    $setup -notmatch 'WRITE_SHORT\s+0x76\s+2' -or
    $setup -notmatch 'WRITE_ASCIIE\s+\(flir_probe_item_offset\s*\+\s*0x00\)\s+~FLRTPFL~\s+#8' -or
    $setup -notmatch 'WRITE_ASCIIE\s+\(flir_probe_item_offset\s*\+\s*0x14\)\s+~FLRTPFL~\s+#8' -or
    $setup -notmatch 'WRITE_LONG\s+\(flir_probe_container_offset\s*\+\s*0x44\)\s+1' -or
    $setup -notmatch 'WRITE_LONG\s+\(flir_probe_pile_offset\s*\+\s*0x44\)\s+1') {
    throw 'Initial targets must contain only harmless filler while all transport resrefs remain absent.'
}
if ($setup -notmatch 'FLIR_PROBE_ERR SYNTHETIC_RESOURCE_COLLISION' -or
    $setup -notmatch 'FILE_EXISTS_IN_GAME') {
    throw 'Pre-existing resources cannot fail closed before the synthetic namespace is published.'
}
foreach ($gameplayTableGuard in @(
    'WRITE_SHORT\s+0x5a\s+0',
    'WRITE_LONG\s+0x64\s+0',
    'WRITE_LONG\s+0x6c\s+0',
    'WRITE_SHORT\s+0x80\s+0',
    'WRITE_SHORT\s+0x82\s+0',
    'WRITE_LONG\s+0x8c\s+0',
    'WRITE_ASCIIE\s+0x94\s+~~\s+#8',
    'WRITE_LONG\s+0xa4\s+0',
    'WRITE_LONG\s+0xac\s+0',
    'WRITE_LONG\s+0xb4\s+0',
    'WRITE_LONG\s+0xc8\s+0',
    'WRITE_LONG\s+0xd0\s+0'
)) {
    if ($setup -notmatch $gameplayTableGuard) {
        throw "The synthetic area retains a gameplay-bearing donor section ('$gameplayTableGuard')."
    }
}
if ($setup -notmatch 'WRITE_LONG\s+0x14\s+0x60' -or
    $setup -notmatch 'SET\s+flir_probe_song_offset\s*=\s*BUFFER_LENGTH' -or
    $setup -notmatch 'INSERT_BYTES\s+flir_probe_song_offset\s+0x90' -or
    $setup -notmatch 'SET\s+flir_probe_rest_offset\s*=\s*BUFFER_LENGTH' -or
    $setup -notmatch 'INSERT_BYTES\s+flir_probe_rest_offset\s+0xe4' -or
    $setup -notmatch 'WRITE_LONG\s+0xbc\s+flir_probe_song_offset' -or
    $setup -notmatch 'WRITE_LONG\s+0xc0\s+flir_probe_rest_offset') {
    throw 'The synthetic ARE lacks deterministic save/rest/travel flags or valid zeroed song/rest blocks.'
}
if ($setup -notmatch 'FLIRPB01' -or $setup -notmatch 'PATCH_FAIL') {
    throw 'The TP2 does not authenticate and validate the ephemeral donor envelope before publication.'
}

if ($wrapper -notmatch "\[switch\]\s*\`$RejectSkipping" -or
    $wrapper -notmatch "Assert-ProbeWeiDUResult[\s\S]+-RejectSkipping:\`$RejectSkipping" -or
    $helper -notmatch "\`$RejectSkipping[\s\S]+SKIPPING:" -or
    $wrapper -notmatch "Invoke-WeiDUOpaque[\s\S]+-RejectSkipping[\s\S]+\`$installArguments") {
    throw 'A zero-exit WeiDU SKIPPING result can still be reported as an installed fixture.'
}
if ($wrapper -notmatch 'Assert-FixtureInstalled' -or
    $wrapper -notmatch 'Assert-FixtureUninstalled' -or
    $helper -notmatch 'Get-ActiveFixtureComponentEntries' -or
    $helper -notmatch "activeEntries\.Count\s+-ne\s+1" -or
    $helper -notmatch "activeEntries\.Count\s+-ne\s+0" -or
    $helper -notmatch 'FLRTPRA\.ARE' -or
    $helper -notmatch 'M_FLRTP\.lua') {
    throw 'PASS is not gated on exact WeiDU.log state and the complete synthetic output set.'
}
if ($helper -notmatch 'function\s+Assert-ProbeFixtureBinaryPreflight' -or
    $helper -notmatch 'Get-ProbeUInt32\s+\$are\s+\(\$actor\s*\+\s*0x88\)' -or
    $helper -notmatch 'Get-ProbeUInt32\s+\$are\s+\(\$actor\s*\+\s*0x8c\)' -or
    $helper -notmatch "FLRTPIT\.ITM'[\s\S]+7,\s*11,\s*13" -or
    $wrapper -notmatch 'Assert-FixtureInstalled[\s\S]+Assert-ProbeFixtureBinaryPreflight[\s\S]+PASS RuntimeProbeFixture_Installed') {
    throw 'The install PASS path is not gated on the synthetic ARE/CRE/ITM binary preflight.'
}

Write-Output 'PASS RuntimeProbeFixture_DisposableOnlySyntheticTopology'
