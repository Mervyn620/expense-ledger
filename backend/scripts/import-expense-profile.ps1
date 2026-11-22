[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$Confirm
)

$ErrorActionPreference = "Stop"
if (-not $Confirm) {
    throw "Import replaces the current normalized ledger and configuration. Re-run with -Confirm."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProfilePath = (Resolve-Path -LiteralPath $Path).Path
$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"
$StartApp = Join-Path $Root "scripts\start-expense-app.ps1"
$StopApp = Join-Path $Root "scripts\stop-expense-app.ps1"
$Initialize = Join-Path $Root "scripts\maintenance\initialize_workspace.py"
$SafetyScript = Join-Path $Root "scripts\maintenance\safety.py"

if (-not (Test-Path -LiteralPath $RuntimePython)) {
    & $StartApp -NoOpen
}
& $RuntimePython $Initialize --root $Root
if ($LASTEXITCODE -ne 0) {
    throw "The target workspace could not be initialized."
}

& $StopApp
try {
    & $RuntimePython $SafetyScript restore `
        --path $ProfilePath `
        --allow-workspace-move `
        --confirm MIGRATE
    if ($LASTEXITCODE -ne 0) {
        throw "Portable profile import failed."
    }
} finally {
    & $StartApp -NoOpen
}

Write-Host "Portable Expense Ledger profile imported and validated."
Write-Host "Store and bank websites must be logged in again in the dedicated browser."
