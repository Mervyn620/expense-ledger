[CmdletBinding()]
param([string]$Version)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $Version) {
    $project = [xml](Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\Expenses.Desktop.csproj') -Raw)
    $Version = [string]$project.Project.PropertyGroup.Version
}
if ($Version -notmatch '^\d+\.\d+\.\d+([-.][A-Za-z0-9]+)*$') { throw 'Invalid version' }
$releasePath = Join-Path $projectRoot "releases\$Version"
if (Test-Path -LiteralPath $releasePath) { throw "Release $Version already exists. Choose a new version to preserve the existing release." }
$dotnet = Join-Path $PSScriptRoot 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { $dotnet = (Get-Command dotnet -ErrorAction Stop).Source }
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$env:NUGET_PACKAGES = Join-Path $PSScriptRoot 'nuget'
& $dotnet publish (Join-Path $projectRoot 'desktop\Expenses.Desktop.csproj') -c Release -r win-x64 "-p:Version=$Version" -o $releasePath --nologo
if ($LASTEXITCODE -ne 0) { throw 'Desktop publish failed.' }
Write-Host "Built releases\$Version\Expenses.Desktop.exe"
