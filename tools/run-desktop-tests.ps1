[CmdletBinding()]
param()
# Runs every plain .NET test project under tests\ (each exits non-zero on its first failed check).
# Uses the project-local SDK and package cache like tools/build.ps1. Exit code is non-zero if any fails.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$dotnet = Join-Path $PSScriptRoot 'dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { $dotnet = (Get-Command dotnet -ErrorAction Stop).Source }
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:NUGET_PACKAGES = (Resolve-Path (Join-Path $PSScriptRoot 'nuget')).Path

$results = @()
foreach ($project in Get-ChildItem (Join-Path $projectRoot 'tests') -Filter *.csproj -Recurse | Where-Object { $_.FullName -notmatch '\\(bin|obj)\\' }) {
    $name = $project.Directory.Name
    # Windows PowerShell turns native stderr into errors; collect it as text instead of stopping.
    $ErrorActionPreference = 'Continue'
    $output = & $dotnet run --project $project.FullName --nologo 2>&1 | ForEach-Object { "$_" } | Out-String
    $ErrorActionPreference = 'Stop'
    # Each test throws on the first failed check, so a zero exit code means every check passed.
    $passed = $LASTEXITCODE -eq 0
    $results += [pscustomobject]@{ Project = $name; Result = $(if ($passed) { 'passed' } else { 'FAILED' }) }
    if (-not $passed) { Write-Host "---- $name output ----"; Write-Host $output.Trim() }
}
$results | Format-Table -AutoSize | Out-String | Write-Host
if ($results | Where-Object Result -ne 'passed') { exit 1 }
