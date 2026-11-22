[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$WaitForLogin
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorkflowPath = Join-Path $Root "config\workflow.json"
$Workflow = Get-Content -Raw -LiteralPath $WorkflowPath | ConvertFrom-Json
if ($Port -le 0) {
    $Port = [int]$Workflow.port
}

. (Join-Path $PSScriptRoot "desktop-browser.ps1")
$LockRoot = Get-DesktopWorkflowLockRoot -WorkspaceRoot $Root
$RootBytes = [System.Text.Encoding]::UTF8.GetBytes($LockRoot.ToLowerInvariant())
$Hasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $RootHash = [System.BitConverter]::ToString(
        $Hasher.ComputeHash($RootBytes)
    ).Replace("-", "").Substring(0, 12)
} finally {
    $Hasher.Dispose()
}

$Mutex = New-Object System.Threading.Mutex(
    $false,
    "Local\ExpenseLedgerWorkflow-$RootHash"
)
$MutexAcquired = $false
try {
    try {
        $MutexAcquired = $Mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $MutexAcquired = $true
    }
    if (-not $MutexAcquired) {
        throw "Another expense refresh is already running for this workspace."
    }

    $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $BundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    $Node = if ($NodeCommand) {
        $NodeCommand.Source
    } elseif (Test-Path -LiteralPath $BundledNode) {
        $BundledNode
    } else {
        throw "Node.js was not found."
    }

    $CdpReady = $false
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2 | Out-Null
        $CdpReady = $true
    } catch {
        & (Join-Path $PSScriptRoot "start-shop-browser.ps1") `
            -StartUrl "https://secure.scotiabank.com/my-accounts" `
            -Port $Port `
            -Interactive
    }

    if (-not $CdpReady) {
        for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
            Start-Sleep -Milliseconds 250
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 1 | Out-Null
                $CdpReady = $true
                break
            } catch {
                continue
            }
        }
    }
    if (-not $CdpReady) {
        throw "The dedicated shop browser did not expose CDP port $Port."
    }

    & $Node `
        (Join-Path $Root "scripts\browser\ensure-shop-tabs.mjs") `
        "--port" "$Port" `
        "--platforms" "scotia"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not open the Scotiabank login page."
    }

    $LoginArguments = @((Join-Path $Root "scripts\browser\prepare-scotia-login.mjs"), "--port", "$Port")
    if ($WaitForLogin) { $LoginArguments += "--wait-for-login" }
    & $Node @LoginArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Could not prepare the Scotiabank login page."
    }
}
finally {
    if ($MutexAcquired) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
