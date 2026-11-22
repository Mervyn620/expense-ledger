[CmdletBinding()]
param(
    [int]$Port = 8765,
    [ValidateRange(1, 50)]
    [int]$PortSearchLimit = 10
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $Root ".codex"
$StateFile = Join-Path $StateDir "expense-app-state.json"
$LegacyPidFile = Join-Path $StateDir "expense-app.pid"
$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"

function Get-WorkspaceHash {
    param([string]$WorkspaceRoot = $Root)
    $PythonPath = if (Test-Path -LiteralPath $RuntimePython) {
        $RuntimePython
    } else {
        $Command = Get-Command python -ErrorAction SilentlyContinue
        if ($Command) { $Command.Source } else { "" }
    }
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $Script = @"
from pathlib import Path
import hashlib
import sys
root = Path(sys.argv[1]).resolve()
print(hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest()[:12].upper())
"@
        $PreviousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $Output = @(& $PythonPath -c $Script $WorkspaceRoot 2>&1)
            $ExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $PreviousPreference
        }
        if ($ExitCode -eq 0 -and $Output.Count -gt 0) {
            return (($Output | Select-Object -Last 1) -as [string]).Trim()
        }
    }

    $RootBytes = [System.Text.Encoding]::UTF8.GetBytes(
        ([System.IO.Path]::GetFullPath($WorkspaceRoot)).ToLowerInvariant()
    )
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

function Get-ExpenseAppHealth {
    param([Parameter(Mandatory = $true)][int]$CandidatePort)
    try {
        $Response = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$CandidatePort/api/health" `
            -TimeoutSec 2
        if (
            $Response.app -eq "expense-ledger" -and
            (Test-SameWorkspace -Health $Response)
        ) {
            return $Response
        }
    } catch {
        return $null
    }
    return $null
}

function Get-AppProcess {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $ProcessInfo = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $ProcessId" `
        -ErrorAction SilentlyContinue
    if (
        $null -ne $ProcessInfo -and
        $ProcessInfo.CommandLine -match "(uvicorn\s+app\.main:app|app\.serve)"
    ) {
        return $ProcessInfo
    }
    return $null
}

function Remove-AppState {
    foreach ($Path in @($StateFile, $LegacyPidFile)) {
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
        }
    }
}

& {
$ProcessId = $null
if (Test-Path -LiteralPath $StateFile) {
    try {
        $State = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        if ($State.app -eq "expense-ledger") {
            $StateHealth = Get-ExpenseAppHealth -CandidatePort ([int]$State.port)
            $ProcessId = if ($null -ne $StateHealth) {
                [int]$StateHealth.pid
            } else {
                [int]$State.pid
            }
        }
    } catch {
        Write-Warning "Ignoring unreadable expense app state."
    }
}

if ($null -eq $ProcessId) {
    if (Test-Path -LiteralPath $LegacyPidFile) {
        $ProcessId = [int](Get-Content -LiteralPath $LegacyPidFile -Raw)
    }
}

if ($null -eq $ProcessId) {
    $CandidatePorts = @(
        Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object {
                $_.LocalPort -ge $Port -and
                $_.LocalPort -lt ($Port + $PortSearchLimit)
            } |
            Select-Object -ExpandProperty LocalPort -Unique
    )
    foreach ($CandidatePort in $CandidatePorts) {
        $Health = Get-ExpenseAppHealth -CandidatePort $CandidatePort
        if ($null -ne $Health) {
            $ProcessId = [int]$Health.pid
            break
        }
    }
}

if ($null -eq $ProcessId) {
    Remove-AppState
    Write-Host "Expense app is not running."
    return
}

$ProcessInfo = Get-AppProcess -ProcessId $ProcessId
if ($null -eq $ProcessInfo) {
    Remove-AppState
    Write-Host "Expense app state was stale; no matching process was stopped."
    return
}

Stop-Process -Id $ProcessId
for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
    Start-Sleep -Milliseconds 100
    if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        break
    }
}
Remove-AppState
Write-Host "Expense app stopped."
}
