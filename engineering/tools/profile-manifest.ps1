# Update only plugin registration fields; preserve every other profile setting.
function Set-DshProfilePluginEntries {
    param($Package, $Dependencies, [object[]]$Bundles)
    $Package | Add-Member NoteProperty dependencies $Dependencies -Force
    if ($null -eq $Package.dsh) { $Package | Add-Member NoteProperty dsh ([pscustomobject]@{}) -Force }
    if ($null -eq $Package.dsh.profile) { $Package.dsh | Add-Member NoteProperty profile ([pscustomobject]@{}) -Force }
    $Package.dsh.profile | Add-Member NoteProperty bundles @($Bundles) -Force
}
