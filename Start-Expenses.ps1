[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$project = [xml](Get-Content -LiteralPath (Join-Path $PSScriptRoot 'desktop\Expenses.Desktop.csproj') -Raw)
$version = [string]$project.Project.PropertyGroup.Version
if ($version -notmatch '^\d+\.\d+\.\d+([-.][A-Za-z0-9]+)*$') { throw 'Invalid desktop release version.' }
$exe = Join-Path $PSScriptRoot "releases\$version\Expenses.Desktop.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Desktop release $version is missing. Run tools/build.ps1 first." }
Start-Process -FilePath $exe -WorkingDirectory $PSScriptRoot -WindowStyle Normal
