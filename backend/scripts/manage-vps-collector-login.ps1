[CmdletBinding()]
param(
    [ValidateSet("Start", "Status", "Stop", "Verify")]
    [string]$Action = "Status",
    [ValidateSet("walmart_ca", "amazon_ca", "the_ordinary_ca", "uber_eats", "fantuan")]
    [string]$Platform = "walmart_ca",
    [ValidateRange(1024, 65535)]
    [int]$LocalPort = 6088,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $Root ".codex"
$StatePath = Join-Path $StateDir "vps-collector-login-tunnel.json"
$SshHost = "ledger_vps"
$RemoteScript = "/home/ledger/apps/expense-collector/current/vps_login_session.py"

function Invoke-SshJson {
    param([Parameter(Mandatory = $true)][string[]]$RemoteArguments)
    $Output = @(
        & ssh.exe -o BatchMode=yes $SshHost `
            /usr/bin/python3 $RemoteScript @RemoteArguments 2>&1
    )
    $ExitCode = $LASTEXITCODE
    $Text = ($Output | Out-String).Trim()
    if ($ExitCode -ne 0) {
        throw "VPS login session command failed: $Text"
    }
    try {
        return $Text | ConvertFrom-Json
    } catch {
        throw "VPS login session returned invalid status."
    }
}

function Read-State {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Stop-Tunnel {
    $State = Read-State
    if ($null -ne $State -and [int]$State.pid -gt 0) {
        $Process = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $([int]$State.pid)" `
            -ErrorAction SilentlyContinue
        if (
            $null -ne $Process -and
            $Process.Name -eq "ssh.exe" -and
            $Process.CommandLine -match "127\.0\.0\.1:$([int]$State.local_port):127\.0\.0\.1:6081"
        ) {
            Stop-Process -Id ([int]$State.pid) -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
}

function Test-LocalPort {
    param([Parameter(Mandatory = $true)][int]$Port)
    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $Task = $Client.ConnectAsync("127.0.0.1", $Port)
        return $Task.Wait(250) -and $Client.Connected
    } catch {
        return $false
    } finally {
        $Client.Dispose()
    }
}

if ($Action -eq "Stop") {
    Stop-Tunnel
    $Remote = Invoke-SshJson -RemoteArguments @("stop")
    $Remote | ConvertTo-Json -Depth 6
    exit 0
}

if ($Action -eq "Status") {
    $Remote = Invoke-SshJson -RemoteArguments @("status")
    $State = Read-State
    [ordered]@{
        remote = $Remote
        tunnel = [ordered]@{
            running = (
                $null -ne $State -and
                (Test-LocalPort -Port ([int]$State.local_port))
            )
            local_port = if ($null -ne $State) { [int]$State.local_port } else { 0 }
        }
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq "Verify") {
    $Remote = $null
    $Stopped = $null
    $CleanupWarning = ""
    try {
        $Remote = Invoke-SshJson -RemoteArguments @(
            "verify",
            "--platform",
            $Platform
        )
    } finally {
        try {
            $Stopped = Invoke-SshJson -RemoteArguments @("stop")
        } catch {
            $CleanupWarning = "Remote login-session cleanup requires attention."
        } finally {
            Stop-Tunnel
        }
    }
    [ordered]@{
        remote = $Remote
        stopped = $Stopped
        cleanup_warning = $CleanupWarning
        tunnel = [ordered]@{
            running = $false
            local_port = 0
        }
    } | ConvertTo-Json -Depth 8
    exit 0
}

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
Stop-Tunnel

$Arguments = @(
    "-N",
    "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-L", "127.0.0.1:${LocalPort}:127.0.0.1:6081",
    "-R", "127.0.0.1:1080",
    $SshHost
)
$Tunnel = Start-Process `
    -FilePath "ssh.exe" `
    -ArgumentList $Arguments `
    -WindowStyle Hidden `
    -PassThru

$Deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $Deadline -and -not (Test-LocalPort -Port $LocalPort)) {
    Start-Sleep -Milliseconds 200
}
if (-not (Test-LocalPort -Port $LocalPort)) {
    Stop-Process -Id $Tunnel.Id -Force -ErrorAction SilentlyContinue
    throw "SSH login tunnel did not become ready."
}

$Remote = $null
try {
    $Remote = Invoke-SshJson -RemoteArguments @(
        "start",
        "--platform",
        $Platform
    )
} catch {
    Stop-Process -Id $Tunnel.Id -Force -ErrorAction SilentlyContinue
    throw
}

$StatePayload = [ordered]@{
    contract = "expense-collector-login-tunnel/v1"
    pid = $Tunnel.Id
    platform = $Platform
    local_port = $LocalPort
    started_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json
$Temporary = "$StatePath.tmp"
[System.IO.File]::WriteAllText(
    $Temporary,
    "$StatePayload`n",
    [System.Text.UTF8Encoding]::new($false)
)
Move-Item -LiteralPath $Temporary -Destination $StatePath -Force

$Url = "http://127.0.0.1:$LocalPort/vnc.html?autoconnect=true&resize=scale&path=websockify"
if (-not $NoOpen) {
    Start-Process $Url
}
[ordered]@{
    remote = $Remote
    tunnel = [ordered]@{
        running = $true
        local_port = $LocalPort
        url = $Url
    }
} | ConvertTo-Json -Depth 8
