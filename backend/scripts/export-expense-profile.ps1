[CmdletBinding()]
param(
    [string]$DestinationDirectory = (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Expense Ledger Backups")
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"
$StartApp = Join-Path $Root "scripts\start-expense-app.ps1"
$SafetyScript = Join-Path $Root "scripts\maintenance\safety.py"

if (-not (Test-Path -LiteralPath $RuntimePython)) {
    & $StartApp -NoOpen
}
if (-not (Test-Path -LiteralPath $RuntimePython)) {
    throw "The isolated Python runtime is unavailable."
}

$Output = @(& $RuntimePython $SafetyScript checkpoint --label portable 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the portable profile backup: $(($Output | Out-String).Trim())"
}
$BackupLine = $Output | Where-Object { $_ -like "safety_backup=*" } | Select-Object -Last 1
if (-not $BackupLine) {
    throw "The backup command did not return a snapshot path."
}
$Source = ($BackupLine -replace '^safety_backup=', '').Trim()
New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
$Destination = Join-Path $DestinationDirectory (
    "ExpenseLedgerProfile-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss")
)
Copy-Item -LiteralPath $Source -Destination $Destination -Force

& $RuntimePython $SafetyScript verify `
    --path $Destination `
    --allow-workspace-move
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
    throw "The copied portable profile failed verification."
}

Write-Host "Portable Expense Ledger profile created:"
Write-Host $Destination
Write-Host "Included: normalized ledger and JSON/CSV configuration."
Write-Host "Excluded: browser profile, cookies, API keys, raw pages, logs, and exports."
