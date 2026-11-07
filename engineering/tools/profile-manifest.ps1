function Read-DshProfileManifest {
    param([string]$Path)
    $text = [System.IO.File]::ReadAllText($Path)
    $package = $text | ConvertFrom-Json
    if ($package -isnot [pscustomobject] -or -not $text.TrimStart().StartsWith('{')) {
        throw ("profile manifest must contain a JSON object: " + $Path)
    }
    return $package
}

# Update only plugin registration fields; preserve every other profile setting.
function Set-DshProfilePluginEntries {
    param($Package, $Dependencies, [object[]]$Bundles)
    $Package | Add-Member NoteProperty dependencies $Dependencies -Force
    if ($null -eq $Package.dsh) { $Package | Add-Member NoteProperty dsh ([pscustomobject]@{}) -Force }
    if ($null -eq $Package.dsh.profile) { $Package.dsh | Add-Member NoteProperty profile ([pscustomobject]@{}) -Force }
    $Package.dsh.profile | Add-Member NoteProperty bundles @($Bundles) -Force
}

# Migrate the known permission.config.presets layout; preserve custom YAML paths.
function Remove-DshLegacyPermissionPreset {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $raw = [System.IO.File]::ReadAllText($Path)
    $output = New-Object System.Text.StringBuilder
    $skipIndent = -1
    $rowIndent = -1
    $configIndent = -1
    $presetsIndent = -1
    $scalarIndent = -1
    $changed = $false
    foreach ($match in [regex]::Matches($raw, '[^\r\n]*(?:\r\n|\n|\r|$)')) {
        $line = $match.Value
        if ($line.Length -eq 0) { continue }
        $content = $line.TrimEnd([char[]]"`r`n")
        [void]($content -match '^([ \t]*)')
        $indent = $Matches[1].Length
        if ($scalarIndent -ge 0) {
            if ($content.Trim().Length -eq 0 -or $indent -gt $scalarIndent) {
                [void]$output.Append($line)
                continue
            }
            $scalarIndent = -1
        }
        if ($content.Trim().Length -gt 0 -and -not $content.TrimStart().StartsWith('#')) {
            if ($skipIndent -ge 0) {
                if ($indent -gt $skipIndent) { continue }
                $skipIndent = -1
            }
            if ($indent -le $rowIndent) { $rowIndent = -1; $configIndent = -1; $presetsIndent = -1 }
            if ($indent -le $configIndent) { $configIndent = -1; $presetsIndent = -1 }
            if ($indent -le $presetsIndent) { $presetsIndent = -1 }
            if ($content -match '^-[ \t]+id:[ \t]*(?:permission|''permission''|"permission")[ \t]*(?:#.*)?$') {
                $rowIndent = $indent
            } elseif ($rowIndent -ge 0 -and $indent -eq $rowIndent + 2 -and $content -match '^[ \t]*config:[ \t]*(?:#.*)?$') {
                $configIndent = $indent
            } elseif ($configIndent -ge 0 -and $indent -eq $configIndent + 2 -and $content -match '^[ \t]*presets:[ \t]*(?:#.*)?$') {
                $presetsIndent = $indent
            } elseif ($presetsIndent -ge 0 -and $indent -eq $presetsIndent + 2 -and $content -match '^[ \t]*sw-single-line:[ \t]*(?:#.*)?$') {
                $skipIndent = $indent
                $changed = $true
                continue
            }
            if ($content -match ':[ \t]*[|>][0-9+-]*[ \t]*(?:#.*)?$') { $scalarIndent = $indent }
        }
        [void]$output.Append($line)
    }
    if (-not $changed) { return $false }
    Copy-Item -LiteralPath $Path -Destination ($Path + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')) -ErrorAction Stop
    [System.IO.File]::WriteAllText($Path, $output.ToString(), (New-Object System.Text.UTF8Encoding($false)))
    return $true
}
