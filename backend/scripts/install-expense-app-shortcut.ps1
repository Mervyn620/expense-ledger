[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StartScript = Join-Path $Root "scripts\start-expense-app.ps1"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Expenses.lnk"
$PowerShell = (Get-Command powershell.exe).Source

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = "Open the local expense ledger"
$Shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,3"
$Shortcut.Save()

Write-Host "Created $ShortcutPath"
