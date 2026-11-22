[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StartScript = Join-Path $Root "scripts\start-price-watch-receiver.ps1"
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "Expenses Price Watch.lnk"

if ($Remove) {
    if (Test-Path -LiteralPath $ShortcutPath) {
        Remove-Item -LiteralPath $ShortcutPath -Force
        Write-Host "Removed $ShortcutPath"
    } else {
        Write-Host "Startup shortcut is already absent: $ShortcutPath"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $StartScript)) {
    throw "Price-watch receiver script is missing: $StartScript"
}

$PowerShell = (Get-Command powershell.exe).Source
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = (
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass " +
    "-File `"$StartScript`""
)
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = "Receive Walmart price-watch results from the configured VPS"
$Shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,167"
$Shortcut.Save()

$Verified = $Shell.CreateShortcut($ShortcutPath)
if (
    $Verified.TargetPath -ne $PowerShell -or
    $Verified.Arguments -notlike "*$StartScript*"
) {
    throw "The price-watch startup shortcut could not be verified."
}

Write-Host "Created $ShortcutPath"
