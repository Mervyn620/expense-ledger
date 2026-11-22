[CmdletBinding()]
param(
    [switch]$OpenLoginPages,
    [switch]$InstallShortcut,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StartApp = Join-Path $Root "scripts\start-expense-app.ps1"
$CheckApp = Join-Path $Root "scripts\check-expense-app.ps1"
$WeeklyRefresh = Join-Path $Root "scripts\weekly-expense-refresh.ps1"
$InstallShortcutScript = Join-Path $Root "scripts\install-expense-app-shortcut.ps1"
$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"
$InitializationScript = Join-Path $Root "scripts\maintenance\initialize_workspace.py"
$SafetyScript = Join-Path $Root "scripts\maintenance\safety.py"
$StatusPath = Join-Path $Root ".codex\bootstrap-status.json"

function Find-Node {
    $Command = Get-Command node -ErrorAction SilentlyContinue
    if ($Command) { return $Command.Source }
    $Bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $Bundled) { return $Bundled }
    return ""
}

function Find-Chrome {
    $Candidates = @(
        (Get-Command chrome.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)',
        (Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)'
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    return ($Candidates | Select-Object -First 1)
}

Push-Location $Root
try {
    # Starting once creates or repairs the isolated Python environment.
    & $StartApp -NoOpen
    if (-not (Test-Path -LiteralPath $RuntimePython)) {
        throw "The isolated Python runtime was not created."
    }

    & $RuntimePython $InitializationScript --root $Root
    if ($LASTEXITCODE -ne 0) {
        throw "Expense workspace initialization failed."
    }

    $RecoveryBaselineCreated = $false
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $VerifyOutput = @(& $RuntimePython $SafetyScript --root $Root verify 2>&1)
        $VerifyExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($VerifyExitCode -ne 0) {
        & $RuntimePython $SafetyScript --root $Root checkpoint --label bootstrap-baseline
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create a verified recovery baseline for this workspace."
        }
        $RecoveryBaselineCreated = $true
    }

    $NodePath = Find-Node
    $ChromePath = Find-Chrome
    $Warnings = @()
    if ([string]::IsNullOrWhiteSpace($NodePath)) {
        $Warnings += "Node.js is missing. The local ledger works, but browser collectors require Node.js 20+."
    }
    if ([string]::IsNullOrWhiteSpace($ChromePath)) {
        $Warnings += "Google Chrome is missing. The local ledger works, but store login and collection are unavailable."
    }

    if ($InstallShortcut) {
        & $InstallShortcutScript
    }
    if ($OpenLoginPages) {
        if ($Warnings.Count -gt 0) {
            Write-Warning "Login pages were not opened because a browser dependency is missing."
        } else {
            & $WeeklyRefresh -OpenLoginPages
        }
    }

    & $CheckApp

    $WorkspaceId = (& $RuntimePython -c @"
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1]).resolve()
print(hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest()[:12].upper())
"@ $Root).Trim()

    $Status = [ordered]@{
        status = if ($Warnings.Count -eq 0) { "ready" } else { "ready_with_warnings" }
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        workspace_id = $WorkspaceId
        python = ".app-runtime/Scripts/python.exe"
        node = $NodePath
        chrome = $ChromePath
        database = "data/expenses.sqlite"
        recovery_baseline_created = $RecoveryBaselineCreated
        warnings = $Warnings
    }
    $StatusTemporary = "$StatusPath.tmp"
    try {
        $Status | ConvertTo-Json -Depth 4 |
            Set-Content -LiteralPath $StatusTemporary -Encoding UTF8
        Move-Item -LiteralPath $StatusTemporary -Destination $StatusPath -Force
    } finally {
        if (Test-Path -LiteralPath $StatusTemporary) {
            Remove-Item -LiteralPath $StatusTemporary -Force
        }
    }

    foreach ($WarningMessage in $Warnings) {
        Write-Warning $WarningMessage
    }
    if (-not $NoOpen) {
        & $StartApp
    }
    Write-Host "Expense Ledger bootstrap completed."
    Write-Host "Status: $StatusPath"
} finally {
    Pop-Location
}
