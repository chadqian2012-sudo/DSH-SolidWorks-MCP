# =============================================================================
# DSH 0.2.0 - Engineering Mode + DSH_SW installer  (bundle path)
# =============================================================================
# Why a separate script from install.ps1 (the 0.1.x one):
#   0.1.x discovered the engineering preset by scanning
#   ~/.dsh/.agent-presets/engineering/agent.cordis.yml.
#   DSH 0.2.0 NO LONGER reads that directory. Presets are now ordinary
#   `@deepseek-ai/dsh-agent-preset` rows declared by a bundle patch, installed
#   into the profile and selected through package.json's dsh.profile.bundles.
#
# WHICH PROFILE: the Electron desktop shell boots the RESERVED profile named
#   `desktop` (~/.dsh/profiles/desktop), NOT `web`. `dsh web` boots `web`.
#   So a plugin installed only into `web` is invisible in the desktop app.
#   This script therefore installs into EVERY profile under ~/.dsh/profiles by
#   default. Pass -Profile to target specific ones.
#
# What still works unchanged (so this stays a small delta):
#   * tools/  -> ~/.dsh/.agent-presets/engineering/tools   (read by
#     dsh-engineering-ui's findToolsDir() for the workflow/mode gate state)
#   * skills/ -> ~/.dsh/skills                              (0.2.0 skill-filesystem
#     discovers user skills at <DSH_HOME>/skills, same as before)
#   Those two are shared by every profile and are installed once.
#
# SAFETY: the profile's cordis.patch.yml is NEVER overwritten.
#   [problem-2 fix] The custom `sw-single-line` permission preset and its
#   plugin (dsH-engineering-sw-single-line) were removed. This installer now
#   only CLEANS any residual `sw-single-line` block from an existing profile
#   and leaves the standard three permission tiers intact.
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads .ps1 as ANSI unless a
# UTF-8 BOM is present; Chinese literals here would be corrupted. All Chinese
# text lives in data files (profiles/web-0.2.0/*.yml, the plugin sources).
# =============================================================================

param(
    # One or more profile names. Empty = every directory under <DSH_HOME>/profiles.
    [string[]]$Profile = @()
)

$ErrorActionPreference = 'Stop'

$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root 'engineering\tools\profile-manifest.ps1')
$DshHome     = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$ProfilesDir = Join-Path $DshHome 'profiles'
$DataDir     = Join-Path $DshHome '.agent-presets\engineering'
$LogPath     = Join-Path $Root 'install-0.2.0.log'

$UI_NAME   = 'dsh-engineering-ui'
$UI_SRC    = Join-Path $Root "engineering\plugins\$UI_NAME"

$script:Log = New-Object System.Text.StringBuilder
$script:Bad = 0
function Say([string]$Text) {
    [void]$script:Log.AppendLine($Text)
    Write-Host $Text
}
function Step([string]$Text) { Say ("`n---- " + $Text + " ----") }
function WriteNoBom([string]$Path, [string]$Text) {
    # package.json / yml must be BOM-less: a BOM breaks JSON.parse at boot.
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

Say "DSH 0.2.0 Engineering Mode installer"
Say ("root      = " + $Root)
Say ("DSH_HOME  = " + $DshHome)

# ------------------------------------------------------------ 0. resolve profiles
Step "0. resolve target profiles"
if (-not (Test-Path $ProfilesDir)) {
    Say ("[X] no profiles dir: " + $ProfilesDir)
    Say "    Boot DSH once so the profile is created, then rerun."
    WriteNoBom $LogPath $script:Log.ToString(); exit 1
}
$allProfiles = @(Get-ChildItem -Directory -LiteralPath $ProfilesDir | ForEach-Object { $_.Name })
$targets = @()
if ($Profile.Count -gt 0) {
    foreach ($p in $Profile) {
        if ($allProfiles -notcontains $p) { throw ("invalid profile name: " + $p) }
        $targets += $p
    }
} else { $targets = $allProfiles }
if ($targets.Count -eq 0) { Say "[X] nothing to install into"; WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
Say ("targets   = " + ($targets -join ', '))

if (-not (Test-Path $UI_SRC))    { Say ("[X] missing plugin source " + $UI_SRC); WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
Say "[OK] preflight"

# The maintained sync owns code copying, runtime exclusions, and registration.
Step "1. sync code / skills / plugin registration (preserving runtime state)"
& (Join-Path $Root 'sync-to-dsh.ps1') -Register -Profile $targets

# the SW troubleshooting note has a Chinese filename; glob it instead of naming it
Get-ChildItem -File (Join-Path $Root 'engineering') -Filter '*.md' -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item $_.FullName -Destination $DataDir -Force; Say ("[OK] doc  ->  " + $_.Name) }

# ============================= per-profile install ===========================
foreach ($prof in $targets) {
    $Web         = Join-Path $ProfilesDir $prof
    $NodeModules = Join-Path $Web 'node_modules'
    $ProfilePkg  = Join-Path $Web 'package.json'
    $ProfilePatch= Join-Path $Web 'cordis.patch.yml'

    Step ("profile: " + $prof + "   (" + $Web + ")")
    if (-not (Test-Path $ProfilePkg)) { Say ("[X] missing " + $ProfilePkg + " - skipped"); $script:Bad++; continue }
    if (Remove-DshLegacyPermissionPreset $ProfilePatch) {
        Say "  [OK] removed legacy permission preset (changed patch backed up)"
    } else { Say "  [OK] no legacy permission preset to migrate" }

    # --- per-profile verify ---
    $checks = @(
        @{ n = 'package.json'; p = $ProfilePkg },
        @{ n = 'cordis.patch.yml'; p = $ProfilePatch },
        @{ n = ('node_modules\' + $UI_NAME + '\package.json'); p = (Join-Path $NodeModules ($UI_NAME + '\package.json')) },
        @{ n = ('node_modules\' + $UI_NAME + '\preset-engineering.patch.yml'); p = (Join-Path $NodeModules ($UI_NAME + '\preset-engineering.patch.yml')) }
    )
    foreach ($c in $checks) {
        if (Test-Path $c.p) { Say ("  [OK] " + $c.n) } else { Say ("  [MISSING] " + $c.n + " -> " + $c.p); $script:Bad++ }
    }
    $bytes = [System.IO.File]::ReadAllBytes($ProfilePkg)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Say ("  [BOM!] " + $ProfilePkg + " has a UTF-8 BOM - boot will fail"); $script:Bad++
    } else { Say "  [OK] package.json is BOM-less" }
    $pt = [System.IO.File]::ReadAllText($ProfilePatch)
    if ($pt -match 'defaultPreset')  { Say "  [OK] existing defaultPreset preserved" }
}

Step "done"
if ($script:Bad -eq 0) {
    Say "ALL GOOD."
    Say "RESTART the DSH desktop app (fully quit, then reopen) so the new bundles load."
    Say "Then: new session -> preset list should show the engineering preset."
} else {
    Say ("FINISHED WITH " + $script:Bad + " PROBLEM(S) - see [MISSING]/[BOM!]/[WARN] above.")
}
Say ("log: " + $LogPath)
WriteNoBom $LogPath $script:Log.ToString()
if ($script:Bad -gt 0) { exit 1 }
