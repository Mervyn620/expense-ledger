[CmdletBinding()]
param(
    [int]$Port = 8765,
    [ValidateRange(1, 50)]
    [int]$PortSearchLimit = 10,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeDir = Join-Path $Root ".app-runtime"
$RuntimePython = Join-Path $RuntimeDir "Scripts\python.exe"
$RequirementsStamp = Join-Path $RuntimeDir ".requirements.sha256"
$StateDir = Join-Path $Root ".codex"
$StateFile = Join-Path $StateDir "expense-app-state.json"
$LegacyPidFile = Join-Path $StateDir "expense-app.pid"
$StdoutLog = Join-Path $StateDir "expense-app.stdout.log"
$StderrLog = Join-Path $StateDir "expense-app.stderr.log"
$BootstrapLog = Join-Path $StateDir "expense-app.bootstrap.log"
$Requirements = Join-Path $Root "requirements-app.txt"

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $Output = @(& $FilePath @Arguments 2>&1)
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    return [PSCustomObject]@{
        exit_code = $ExitCode
        output = ($Output | Out-String).Trim()
    }
}

function Get-ExpenseAppHealth {
    param([Parameter(Mandatory = $true)][int]$CandidatePort)
    try {
        $Response = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$CandidatePort/api/health" `
            -TimeoutSec 2
        if (
            $Response.app -eq "expense-ledger" -and
            (Test-SameWorkspace -Health $Response) -and
            $Response.status -in @("ok", "missing_database")
        ) {
            return $Response
        }
    } catch {
        return $null
    }
    return $null
}

function Test-PortAvailable {
    param([Parameter(Mandatory = $true)][int]$CandidatePort)
    $Listener = $null
    try {
        $Listener = New-Object System.Net.Sockets.TcpListener(
            [System.Net.IPAddress]::Loopback,
            $CandidatePort
        )
        $Listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $Listener) {
            $Listener.Stop()
        }
    }
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

function Write-AppState {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][int]$SelectedPort,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$PythonPath
    )
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    $Payload = [ordered]@{
        app = "expense-ledger"
        pid = $ProcessId
        port = $SelectedPort
        url = "http://127.0.0.1:$SelectedPort"
        root = $Root
        python = if ([string]::IsNullOrWhiteSpace($PythonPath)) { $RuntimePython } else { $PythonPath }
        started_at = (Get-Date).ToString("o")
        stdout_log = $StdoutLog
        stderr_log = $StderrLog
    }
    $Temporary = "$StateFile.tmp"
    $Payload | ConvertTo-Json | Set-Content -LiteralPath $Temporary -Encoding UTF8
    Move-Item -LiteralPath $Temporary -Destination $StateFile -Force
    Set-Content -LiteralPath $LegacyPidFile -Value $ProcessId -Encoding ASCII
}

function Remove-StaleState {
    foreach ($Path in @($StateFile, $LegacyPidFile)) {
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
        }
    }
}

function Stop-AppProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $Children = Get-CimInstance Win32_Process `
        -Filter "ParentProcessId = $ProcessId" `
        -ErrorAction SilentlyContinue
    foreach ($Child in $Children) {
        Stop-AppProcessTree -ProcessId $Child.ProcessId
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Open-ExpenseApp {
    param([Parameter(Mandatory = $true)][int]$SelectedPort)
    $Url = "http://127.0.0.1:$SelectedPort"
    if (-not $NoOpen) {
        Start-Process $Url
    }
    Write-Host "Expense app is available at $Url"
    [Console]::Out.Flush()
}

function Get-BasePython {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        return $PythonCommand.Source
    }
    $PythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        return $PythonLauncher.Source
    }
    $BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $BundledPython) {
        return $BundledPython
    }
    throw "Python was not found. Install Python 3.11+ and run the bootstrap again."
}

function Get-AppRequirementsHash {
    # Use .NET directly: Windows PowerShell launched by a shortcut may inherit
    # a newer PowerShell module path where Get-FileHash is not available.
    $Stream = [System.IO.File]::OpenRead($Requirements)
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString(
            $Hasher.ComputeHash($Stream)
        ).Replace("-", "")
    } finally {
        $Hasher.Dispose()
        $Stream.Dispose()
    }
}

function Test-AppRuntimeNeedsRepair {
    if (
        -not (Test-Path -LiteralPath $RuntimePython) -or
        -not (Test-Path -LiteralPath $Requirements) -or
        -not (Test-Path -LiteralPath $RequirementsStamp)
    ) {
        return $true
    }
    $ExpectedRequirementsHash = Get-AppRequirementsHash
    $InstalledRequirementsHash = (
        Get-Content -Raw -LiteralPath $RequirementsStamp
    ).Trim()
    if ($InstalledRequirementsHash -ne $ExpectedRequirementsHash) {
        return $true
    }
    $ImportResult = Invoke-NativeCommand `
        -FilePath $RuntimePython `
        -Arguments @(
            "-c",
            "import fastapi, openpyxl, uvicorn; from fastapi.testclient import TestClient"
        )
    return $ImportResult.exit_code -ne 0
}

function Initialize-AppRuntime {
    if (-not (Test-Path -LiteralPath $Requirements)) {
        throw "App dependency manifest was not found: $Requirements"
    }
    if (-not (Test-Path -LiteralPath $RuntimePython)) {
        $BasePython = Get-BasePython
        Write-Host "Preparing the isolated expense app runtime..."
        $CreateResult = Invoke-NativeCommand `
            -FilePath $BasePython `
            -Arguments @("-m", "venv", $RuntimeDir)
        if ($CreateResult.exit_code -ne 0) {
            throw "Could not create the isolated app runtime: $($CreateResult.output)"
        }
    }

    $ImportResult = Invoke-NativeCommand `
        -FilePath $RuntimePython `
        -Arguments @(
            "-c",
            "import fastapi, openpyxl, uvicorn; from fastapi.testclient import TestClient"
        )
    $ExpectedRequirementsHash = Get-AppRequirementsHash
    $InstalledRequirementsHash = if (Test-Path -LiteralPath $RequirementsStamp) {
        (Get-Content -Raw -LiteralPath $RequirementsStamp).Trim()
    } else {
        ""
    }
    if (
        $ImportResult.exit_code -ne 0 -or
        $InstalledRequirementsHash -ne $ExpectedRequirementsHash
    ) {
        Write-Host "Installing expense app dependencies..."
        $InstallResult = Invoke-NativeCommand `
            -FilePath $RuntimePython `
            -Arguments @(
                "-m", "pip", "install",
                "--disable-pip-version-check",
                "--quiet",
                "-r", $Requirements
            )
        if ($InstallResult.exit_code -ne 0) {
            Add-Content -LiteralPath $BootstrapLog -Value $InstallResult.output
            throw "Could not install app dependencies. See $BootstrapLog."
        }
        Set-Content `
            -LiteralPath $RequirementsStamp `
            -Value $ExpectedRequirementsHash `
            -Encoding ASCII
    }
}

function Import-OptionalUserEnvironment {
    foreach ($Name in @("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL")) {
        $CurrentValue = [Environment]::GetEnvironmentVariable(
            $Name,
            [EnvironmentVariableTarget]::Process
        )
        if ([string]::IsNullOrWhiteSpace($CurrentValue)) {
            $UserValue = [Environment]::GetEnvironmentVariable(
                $Name,
                [EnvironmentVariableTarget]::User
            )
            if (-not [string]::IsNullOrWhiteSpace($UserValue)) {
                [Environment]::SetEnvironmentVariable(
                    $Name,
                    $UserValue,
                    [EnvironmentVariableTarget]::Process
                )
            }
        }
    }
}

function Get-WorkspaceHash {
    param([string]$WorkspaceRoot = $Root)
    $PythonPath = if (Test-Path -LiteralPath $RuntimePython) {
        $RuntimePython
    } else {
        Get-BasePython
    }
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
    try {
        $Result = Invoke-NativeCommand -FilePath $PythonPath -Arguments @("-c", $Script)
    } finally {
        [Environment]::SetEnvironmentVariable(
            "EXPENSE_LEDGER_WORKSPACE_ROOT",
            $PreviousRoot,
            [EnvironmentVariableTarget]::Process
        )
    }
    if ($Result.exit_code -eq 0 -and -not [string]::IsNullOrWhiteSpace($Result.output)) {
        return $Result.output.Trim().Split()[-1]
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
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$RootHash = Get-WorkspaceHash

$Mutex = New-Object System.Threading.Mutex(
    $false,
    "Local\ExpenseLedgerApp-$RootHash"
)
$MutexAcquired = $false
try {
    try {
        $MutexAcquired = $Mutex.WaitOne(10000)
    } catch [System.Threading.AbandonedMutexException] {
        $MutexAcquired = $true
    }
    if (-not $MutexAcquired) {
        throw "Another expense app startup is still in progress. Try again in a few seconds."
    }

    $RuntimeNeedsRepair = Test-AppRuntimeNeedsRepair
    if (Test-Path -LiteralPath $StateFile) {
        try {
            $State = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
            $StateProcess = Get-AppProcess -ProcessId ([int]$State.pid)
            $StateHealth = Get-ExpenseAppHealth -CandidatePort ([int]$State.port)
            if ($null -ne $StateProcess -and $null -ne $StateHealth) {
                if (-not $RuntimeNeedsRepair) {
                    if ([int]$State.pid -ne [int]$StateHealth.pid) {
                        Write-AppState `
                            -ProcessId ([int]$StateHealth.pid) `
                            -SelectedPort ([int]$State.port) `
                            -PythonPath ([string]$State.python)
                    }
                    Open-ExpenseApp -SelectedPort ([int]$State.port)
                    return
                }
                Write-Host "Restarting the expense app to apply dependency changes..."
                Stop-AppProcessTree -ProcessId ([int]$StateHealth.pid)
                for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
                    Start-Sleep -Milliseconds 100
                    if (Test-PortAvailable -CandidatePort ([int]$State.port)) {
                        break
                    }
                }
                $StateProcess = $null
                $StateHealth = $null
            }
            if ($null -ne $StateProcess) {
                Write-Warning "Stopping an unresponsive prior expense app process."
                Stop-Process -Id ([int]$State.pid) -Force
            }
        } catch {
            Write-Warning "Ignoring stale expense app state: $($_.Exception.Message)"
        }
        Remove-StaleState
    }

    $SelectedPort = $null
    for ($Offset = 0; $Offset -lt $PortSearchLimit; $Offset++) {
        $CandidatePort = $Port + $Offset
        if (Test-PortAvailable -CandidatePort $CandidatePort) {
            $SelectedPort = $CandidatePort
            break
        }
        $Health = Get-ExpenseAppHealth -CandidatePort $CandidatePort
        if ($null -ne $Health) {
            if (-not $RuntimeNeedsRepair) {
                Write-AppState `
                    -ProcessId ([int]$Health.pid) `
                    -SelectedPort $CandidatePort `
                    -PythonPath $RuntimePython
                Open-ExpenseApp -SelectedPort $CandidatePort
                return
            }
            Write-Host "Restarting the expense app to apply dependency changes..."
            Stop-AppProcessTree -ProcessId ([int]$Health.pid)
            for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
                Start-Sleep -Milliseconds 100
                if (Test-PortAvailable -CandidatePort $CandidatePort) {
                    break
                }
            }
            if (-not (Test-PortAvailable -CandidatePort $CandidatePort)) {
                throw "The prior expense app did not release port $CandidatePort."
            }
            $SelectedPort = $CandidatePort
            break
        }
    }
    if ($null -eq $SelectedPort) {
        throw "No available localhost port was found from $Port through $($Port + $PortSearchLimit - 1)."
    }

    Initialize-AppRuntime
    Import-OptionalUserEnvironment
    [System.IO.File]::WriteAllText(
        $StdoutLog,
        "",
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $StderrLog,
        "",
        [System.Text.UTF8Encoding]::new($false)
    )

    $Process = Start-Process `
        -FilePath $RuntimePython `
        -ArgumentList @("-m", "app.serve", "--port", "$SelectedPort") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Write-AppState `
        -ProcessId $Process.Id `
        -SelectedPort $SelectedPort `
        -PythonPath $RuntimePython

    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        Start-Sleep -Milliseconds 250
        if ($Process.HasExited) {
            break
        }
        if (-not (Test-PortAvailable -CandidatePort $SelectedPort)) {
            $Health = Get-ExpenseAppHealth -CandidatePort $SelectedPort
            if ($null -ne $Health) {
                Write-AppState `
                    -ProcessId ([int]$Health.pid) `
                    -SelectedPort $SelectedPort `
                    -PythonPath $RuntimePython
                Open-ExpenseApp -SelectedPort $SelectedPort
                return
            }
        }
    }

    if (-not $Process.HasExited) {
        Stop-AppProcessTree -ProcessId $Process.Id
    }
    Remove-StaleState
    $Details = if (Test-Path -LiteralPath $StderrLog) {
        (Get-Content -LiteralPath $StderrLog -Tail 12) -join " "
    } else {
        "No error log was produced."
    }
    throw "Expense app did not start. Details: $Details"
} finally {
    if ($MutexAcquired) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
}
