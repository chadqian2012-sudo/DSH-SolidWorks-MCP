# =============================================================================
# DSH 0.2.0 - Engineering Mode UNINSTALLER
# =============================================================================
# Reverses install-0.2.0.ps1 for the given profile(s):
#   1. removes node_modules/dsh-engineering-ui (and any legacy
#      dsH-engineering-sw-single-line dir, if one survives from an older install)
#   2. removes those entries from package.json (dependencies + dsh.profile.bundles)
#   3. removes only the legacy `sw-single-line:` permission preset from the
#      current cordis.patch.yml; preserves later user settings and all backups
# Shared data (~/.dsh/.agent-presets/engineering, ~/.dsh/skills) is left in place;
# pass -Shared too to remove that as well.
#
# Keep this file ASCII-only (PowerShell 5.1 reads .ps1 as ANSI without a BOM).
# =============================================================================

param(
    [string[]]$Profile = @(),
    [switch]$Shared
)

$ErrorActionPreference = 'Stop'

$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root 'engineering\tools\profile-manifest.ps1')
$DshHome     = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$ProfilesDir = Join-Path $DshHome 'profiles'
$DataDir     = Join-Path $DshHome '.agent-presets\engineering'
$UserSkills  = Join-Path $DshHome 'skills'
$LogPath     = Join-Path $Root 'uninstall-0.2.0.log'

$UI_NAME   = 'dsh-engineering-ui'
$SWSL_NAME = 'dsH-engineering-sw-single-line'

$script:Log = New-Object System.Text.StringBuilder
$script:Bad = 0
function Say([string]$Text) { [void]$script:Log.AppendLine($Text); Write-Host $Text }
function Step([string]$Text) { Say ("`n---- " + $Text + " ----") }
function WriteNoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
# Remove a path, falling back to a rename when the safe-delete hook blocks deletion.
function Zap([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop; return $true }
    catch {
        $alt = $Path + '.removed-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        try {
            Move-Item -LiteralPath $Path -Destination $alt -Force -ErrorAction Stop
            Say ("  [note] delete was blocked; renamed to " + (Split-Path $alt -Leaf) + " (safe to delete by hand)")
            return $true
        } catch { Say ("  [X] could not remove " + $Path + " :: " + $_.Exception.Message); return $false }
    }
}

Say "DSH 0.2.0 Engineering Mode uninstaller"
Say ("DSH_HOME = " + $DshHome)

Step "0. resolve target profiles"
$targets = @()
$allProfiles = @(Get-ChildItem -Directory -LiteralPath $ProfilesDir -ErrorAction SilentlyContinue)
if ($Profile.Count -gt 0) {
    foreach ($p in $Profile) {
        if ($allProfiles.Name -notcontains $p) { Say ("[WARN] profile '" + $p + "' not found") }
    }
}
foreach ($p in $allProfiles) {
    if ($Profile.Count -eq 0 -or $Profile -contains $p.Name) { $targets += $p.Name }
}
if ($targets.Count -eq 0) { Say "[X] nothing to do"; WriteNoBom $LogPath $script:Log.ToString(); exit 1 }
Say ("targets = " + ($targets -join ', '))

foreach ($prof in $targets) {
    $Web          = Join-Path $ProfilesDir $prof
    $NodeModules  = Join-Path $Web 'node_modules'
    $ProfilePkg   = Join-Path $Web 'package.json'
    $ProfilePatch = Join-Path $Web 'cordis.patch.yml'

    Step ("profile: " + $prof)

    # 1. plugin packages
    foreach ($n in @($UI_NAME, $SWSL_NAME)) {
        $d = Join-Path $NodeModules $n
        if (Test-Path -LiteralPath $d) {
            if (Zap $d) { Say ("  [OK] removed node_modules\" + $n) } else { $script:Bad++ }
        } else { Say ("  [SKIP] node_modules\" + $n + " absent") }
    }
    if ((Test-Path $NodeModules) -and ((Get-ChildItem -Force $NodeModules -ErrorAction SilentlyContinue).Count -eq 0)) {
        [void](Zap $NodeModules); Say "  [OK] removed empty node_modules"
    }

    # 2. manifest
    if (Test-Path $ProfilePkg) {
        $pkg = ([System.IO.File]::ReadAllText($ProfilePkg).TrimStart([char]0xFEFF)) | ConvertFrom-Json
        $deps = [ordered]@{}
        foreach ($p in $pkg.dependencies.PSObject.Properties) {
            if ($p.Name -ne $UI_NAME -and $p.Name -ne $SWSL_NAME) { $deps[$p.Name] = $p.Value }
        }
        $bundles = @()
        foreach ($b in $pkg.dsh.profile.bundles) {
            if ($b -ne $UI_NAME -and $b -ne $SWSL_NAME) { $bundles += $b }
        }
        Set-DshProfilePluginEntries $pkg $deps $bundles
        WriteNoBom $ProfilePkg (($pkg | ConvertTo-Json -Depth 100) + "`n")
        Say ("  [OK] package.json cleaned; bundles = " + ($bundles -join ', '))
    } else { Say ("  [WARN] no " + $ProfilePkg); $script:Bad++ }

    # 3. cordis.patch.yml
    if (Remove-DshLegacyPermissionPreset $ProfilePatch) {
        Say "  [OK] removed residual sw-single-line preset; current settings and backups kept"
    } elseif (Test-Path -LiteralPath $ProfilePatch) {
        Say "  [OK] no custom permission preset; current settings and backups kept"
    }
}

if ($Shared) {
    Step "shared data"
    if (Test-Path $DataDir) { if (Zap $DataDir) { Say ("  [OK] removed " + $DataDir) } }
    foreach ($s in @('assembly-orchestration','cad-workflow','mode-selection','physics-in-loop','solidworks-bridge','sw-design','sw-to-cad')) {
        $d = Join-Path $UserSkills $s
        if (Test-Path $d) { if (Zap $d) { Say ("  [OK] removed skill " + $s) } }
    }
} else {
    Say ""
    Say ("shared data kept: " + $DataDir + "   and   " + $UserSkills)
    Say "(rerun with -Shared to remove them too)"
}

Step "done"
if ($script:Bad -eq 0) { Say "ALL GOOD." } else { Say ("FINISHED WITH " + $script:Bad + " PROBLEM(S).") }
Say ("log: " + $LogPath)
WriteNoBom $LogPath $script:Log.ToString()
if ($script:Bad -gt 0) { exit 1 }
