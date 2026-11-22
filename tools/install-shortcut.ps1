[CmdletBinding()]
param([string]$Version)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $Version) {
    $project = [xml](Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\Expenses.Desktop.csproj') -Raw)
    $Version = [string]$project.Project.PropertyGroup.Version
}
if ($Version -notmatch '^\d+\.\d+\.\d+([-.][A-Za-z0-9]+)*$') { throw 'Invalid version' }
$exe = Join-Path $projectRoot "releases\$Version\Expenses.Desktop.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw 'Build the desktop executable first.' }
$desktopDir = [Environment]::GetFolderPath('Desktop')
$menuDir = Join-Path ([Environment]::GetFolderPath('Programs')) 'Expenses Desktop'
New-Item -ItemType Directory -Force -Path $menuDir | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcutPaths = @((Join-Path $desktopDir 'Expenses Desktop.lnk'),(Join-Path $menuDir 'Expenses Desktop.lnk'))
# Existing web shortcuts now open the same current client.
foreach ($legacy in @((Join-Path $desktopDir 'Expenses.lnk'),(Join-Path ([Environment]::GetFolderPath('Programs')) 'Expenses.lnk'))) {
    if (Test-Path -LiteralPath $legacy) { $shortcutPaths += $legacy }
}
foreach ($shortcutPath in $shortcutPaths) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exe
    $shortcut.Arguments = ''
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = (Join-Path $projectRoot 'desktop\Assets\ledger.ico')
    $shortcut.Description = 'Expenses - current desktop client'
    $shortcut.Save()
}
Write-Host "Installed Expenses shortcuts for desktop client $Version."
