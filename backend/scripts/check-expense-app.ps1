[CmdletBinding()]
param(
    [int]$Port = 8765,
    [ValidateRange(1, 50)]
    [int]$PortSearchLimit = 10,
    [switch]$Repair
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"
$Requirements = Join-Path $Root "requirements-app.txt"
$RequirementsStamp = Join-Path $Root ".app-runtime\.requirements.sha256"
$StateFile = Join-Path $Root ".codex\expense-app-state.json"
$StartScript = Join-Path $Root "scripts\start-expense-app.ps1"
$StopScript = Join-Path $Root "scripts\stop-expense-app.ps1"
$SafetyScript = Join-Path $Root "scripts\maintenance\safety.py"
$Problems = @()
$SafetyFailed = $false
$DependencyMismatch = $false
$Health = $null
$State = $null

function Test-PythonModules {
    param([Parameter(Mandatory = $true)][string]$PythonPath)
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PythonPath -c `
            "import fastapi, openpyxl, uvicorn; from fastapi.testclient import TestClient" `
            2>&1 | Out-Null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
}

function Get-WorkspaceHash {
    param([string]$WorkspaceRoot = $Root)
    $PythonPath = if (Test-Path -LiteralPath $RuntimePython) {
        $RuntimePython
    } else {
        $Command = Get-Command python -ErrorAction SilentlyContinue
        if ($Command) { $Command.Source } else { "" }
    }
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $PreviousRoot = [Environment]::GetEnvironmentVariable(
            "EXPENSE_LEDGER_WORKSPACE_ROOT",
            [EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            "EXPENSE_LEDGER_WORKSPACE_ROOT",
            $WorkspaceRoot,
            [EnvironmentVariableTarget]::Process
        )
        $Script = @"
from pathlib import Path
import hashlib
import os
root = Path(os.environ["EXPENSE_LEDGER_WORKSPACE_ROOT"]).resolve()
print(hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest()[:12].upper())
"@
        $PreviousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $Output = @(& $PythonPath -c $Script 2>&1)
            $ExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $PreviousPreference
            [Environment]::SetEnvironmentVariable(
                "EXPENSE_LEDGER_WORKSPACE_ROOT",
                $PreviousRoot,
                [EnvironmentVariableTarget]::Process
            )
        }
        if ($ExitCode -eq 0 -and $Output.Count -gt 0) {
            return (($Output | Select-Object -Last 1) -as [string]).Trim()
        }
    }

    $RootBytes = [System.Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString(
            $Hasher.ComputeHash($RootBytes)
        ).Replace("-", "").Substring(0, 12)
    } finally {
        $Hasher.Dispose()
    }
}

$RootHash = Get-WorkspaceHash

function Test-SameWorkspace {
    param([Parameter(Mandatory = $true)]$Health)
    if ($Health.workspace_id -eq $RootHash) {
        return $true
    }
    if (-not [string]::IsNullOrWhiteSpace($Health.workspace)) {
        try {
            return (Get-WorkspaceHash -WorkspaceRoot $Health.workspace) -eq $RootHash
        } catch {
            return $false
        }
    }
    return $false
}

& {
if (-not (Test-Path -LiteralPath $RuntimePython)) {
    $DependencyMismatch = $true
    $Problems += "The isolated app runtime has not been created."
} else {
    if (-not (Test-PythonModules -PythonPath $RuntimePython)) {
        $DependencyMismatch = $true
        $Problems += "The isolated app runtime is missing dependencies."
    }
    if (-not (Test-Path -LiteralPath $Requirements)) {
        $DependencyMismatch = $true
        $Problems += "The app dependency manifest is missing."
    } else {
        $ExpectedRequirementsHash = (Get-FileHash `
            -LiteralPath $Requirements `
            -Algorithm SHA256).Hash
        $InstalledRequirementsHash = if (Test-Path -LiteralPath $RequirementsStamp) {
            (Get-Content -Raw -LiteralPath $RequirementsStamp).Trim()
        } else {
            ""
        }
        if ($InstalledRequirementsHash -ne $ExpectedRequirementsHash) {
            $DependencyMismatch = $true
            $Problems += "The isolated app dependencies do not match requirements-app.txt."
        }
    }
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $SafetyOutput = @(
            & $RuntimePython $SafetyScript preflight --no-backup 2>&1
        )
        $SafetyExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($SafetyExitCode -ne 0) {
        $SafetyFailed = $true
        $Problems += "Safety preflight failed: $(($SafetyOutput | Out-String).Trim())"
    }
}

if (Test-Path -LiteralPath $StateFile) {
    try {
        $State = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        if ($State.app -ne "expense-ledger") {
            $Problems += "The app state file has an unexpected identity."
        }
    } catch {
        $Problems += "The app state file is unreadable."
    }
}

$CandidatePorts = @(
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object {
            $_.LocalPort -ge $Port -and
            $_.LocalPort -lt ($Port + $PortSearchLimit)
        } |
        Select-Object -ExpandProperty LocalPort -Unique
)
foreach ($CandidatePort in $CandidatePorts) {
    try {
        $Candidate = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$CandidatePort/api/health" `
            -TimeoutSec 2
        if (
            $Candidate.app -eq "expense-ledger" -and
            (Test-SameWorkspace -Health $Candidate)
        ) {
            $Health = $Candidate
            $ActivePort = $CandidatePort
            break
        }
    } catch {
        continue
    }
}

if ($null -eq $Health) {
    $Problems += "The expense app health endpoint is not available."
} elseif ($null -ne $State) {
    if ([int]$State.pid -ne [int]$Health.pid) {
        $Problems += "The app state PID does not match the listening service."
    }
    if ([int]$State.port -ne [int]$ActivePort) {
        $Problems += "The app state port does not match the listening service."
    }
}

if ($SafetyFailed) {
    throw ($Problems -join " ")
}

if ($DependencyMismatch -and $Repair) {
    Write-Host "Repairing isolated app dependencies..."
    & $StopScript
    & $StartScript -Port $Port -PortSearchLimit $PortSearchLimit -NoOpen
    & $PSCommandPath -Port $Port -PortSearchLimit $PortSearchLimit
    return
}

if ($Problems.Count -gt 0 -and $Repair) {
    Write-Host "Repairing expense app startup..."
    & $StartScript -Port $Port -PortSearchLimit $PortSearchLimit -NoOpen
    & $PSCommandPath -Port $Port -PortSearchLimit $PortSearchLimit
    return
}

if ($Problems.Count -gt 0) {
    throw ($Problems -join " ")
}

Write-Host "Expense app health check passed."
Write-Host "URL: http://127.0.0.1:$ActivePort"
Write-Host "Version: $($Health.version)"
Write-Host "PID: $($Health.pid)"
Write-Host "Database: $($Health.status)"
}
