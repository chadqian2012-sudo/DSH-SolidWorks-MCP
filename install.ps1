# =============================================================================
#  install.ps1 - one-click installer for DSH Engineering Mode
# =============================================================================
#  WHAT THIS DOES
#    1. Preflight: locate DSH_HOME (default ~/.dsh) and require profiles to
#       exist (start DSH once first if they do not).
#    2. Register the dsh-engineering-ui plugin in EVERY profile
#       (package.json: dependencies + dsh.profile.bundles). Without this the
#       engineering UI never loads -- this was a real gap: the 'web' profile
#       did not declare the plugin while install targeted only 'web'.
#    3. Sync engineering files to their install locations:
#         engineering\tools\   -> <DSH>\.agent-presets\engineering\tools
#         engineering\skills\  -> <DSH>\skills
#         engineering\plugins\ -> <DSH>\profiles\<profile>\node_modules\...
#         root scripts / yml   -> <DSH>\.agent-presets\engineering\
#    4. Verify and remind you to restart DSH.
#
#  USAGE
#    powershell -ExecutionPolicy Bypass -File install.ps1
#    powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun
#    powershell -ExecutionPolicy Bypass -File install.ps1 -Profile desktop
#
#  SAFETY
#    * Nothing is ever bulk-deleted: overwritten files are backed up as
#      <file>.bak-sync-<stamp>.
#    * Runtime state (current room / material / workflow) is never copied.
#    * Idempotent - safe to run repeatedly.
#
#  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a BOM-less
#        .ps1 as ANSI, so non-ASCII literals here would corrupt parsing.
# =============================================================================
param(
    [switch]$DryRun,
    [string[]]$Profile = @()
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Rule($t) {
    Say ""
    Say "================================================================" Cyan
    if ($t) { Say ("  " + $t) Cyan }
    Say "================================================================" Cyan
}

$DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }

Rule "DSH Engineering Mode - one-click install"
Say ("  source   : " + $Root) Gray
Say ("  DSH_HOME : " + $DshHome) Gray
if ($DryRun) { Say "  *** DRY RUN - nothing will be written ***" Yellow }

# ---- preflight --------------------------------------------------------------
$ProfilesDir = Join-Path $DshHome 'profiles'
if (-not (Test-Path $DshHome)) {
    Say ""
    Say ("[X] DSH_HOME not found: " + $DshHome) Red
    Say "    Start DSH once so it creates its home directory, then re-run." Yellow
    exit 1
}
if (-not (Test-Path $ProfilesDir)) {
    Say ""
    Say ("[X] profiles directory not found: " + $ProfilesDir) Red
    Say "    Start DSH once so it creates at least one profile, then re-run." Yellow
    exit 1
}

$allProfiles = @()
Get-ChildItem -Directory -LiteralPath $ProfilesDir -ErrorAction SilentlyContinue |
    ForEach-Object { $allProfiles += $_.Name }
if ($Profile.Count -gt 0) {
    $targets = @()
    foreach ($p in $Profile) {
        if ($allProfiles -contains $p) { $targets += $p }
        else { Say ("  [WARN] profile not found, skipped: " + $p) Yellow }
    }
} else {
    $targets = $allProfiles
}
if ($targets.Count -eq 0) {
    Say ""
    Say "[X] no target profile found." Red
    exit 1
}
Say ("  profiles : " + ($targets -join ', ')) Gray

$UiSrc = Join-Path $Root 'engineering\plugins\dsh-engineering-ui'
if (-not (Test-Path $UiSrc)) {
    Say ""
    Say ("[X] plugin source missing: " + $UiSrc) Red
    exit 1
}

# Sync owns registration and code copying for the selected profiles.
Rule "sync engineering code and register profiles"
$Sync = Join-Path $Root 'sync-to-dsh.ps1'
if (-not (Test-Path $Sync)) {
    Say ("  [X] sync-to-dsh.ps1 not found next to install.ps1: " + $Sync) Red
    exit 1
}
& $Sync -DryRun:$DryRun -Register -Profile $targets

if ($DryRun) {
    Rule "dry run complete"
    Say "  No files were written. Run without -DryRun to install."
    exit 0
}

# ---- 3. verify --------------------------------------------------------------
Rule "verify"
$PresetDir = Join-Path $DshHome '.agent-presets\engineering'
$bad = 0
function Check($name, $path) {
    if (Test-Path $path) { Say ("  [OK]    " + $name) Green }
    else { Say ("  [MISS]  " + $name + "  -> " + $path) Red; $script:bad++ }
}
Check "agent.cordis.yml"  (Join-Path $PresetDir 'agent.cordis.yml')
Check "preset.yml"        (Join-Path $PresetDir 'preset.yml')
Check "install-plugin.ps1" (Join-Path $PresetDir 'install-plugin.ps1')
Check "tools/swapi.py"    (Join-Path $PresetDir 'tools\swapi.py')
Check "tools/sw_bridge.py" (Join-Path $PresetDir 'tools\sw_bridge.py')
Check "tools/physics/material_db.py" (Join-Path $PresetDir 'tools\physics\material_db.py')
Check "tools/physics/gb_materials.json" (Join-Path $PresetDir 'tools\physics\gb_materials.json')
Check "skills"            (Join-Path $DshHome 'skills')
foreach ($prof in $targets) {
    Check ("plugin@" + $prof) (Join-Path $ProfilesDir ($prof + '\node_modules\dsh-engineering-ui\lib\index.js'))
}

# ---- done -------------------------------------------------------------------
Rule "done"
Say ("  target profiles     : " + ($targets -join ", ")) Gray
Say ("  verification errors : " + $bad) Gray
Say ""
Say "  NEXT STEPS" Yellow
Say "    1. FULLY QUIT and reopen DSH." Gray
Say "       The plugin host half and the .defense trust root are process-level," Gray
Say "       so a page refresh alone is not enough." Gray
Say "    2. Settings -> Agent Preset -> pick 'Engineering Mode'." Gray
Say "    3. On first run .agent-presets\engineering\tools\.defense\runtime.json" Gray
Say "       is generated and the defense endpoints become available." Gray
Say ""
Say "  Desktop and Web profiles are both supported." Gray
Say "================================================================" Cyan
if ($bad -gt 0) { exit 1 }
