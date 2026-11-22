[CmdletBinding()]
param(
    [int]$Port = 9223
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProfileMarker = Join-Path $Root ".codex-shop-browser"

$Processes = Get-CimInstance Win32_Process -Filter "name = 'chrome.exe'" |
    Where-Object {
        $_.CommandLine -like "*--remote-debugging-port=$Port*" -or
        $_.CommandLine -like "*$ProfileMarker*"
    }

if (-not $Processes) {
    Write-Host "No dedicated Codex shop browser process found."
    exit 0
}

$ProcessIds = $Processes | Select-Object -ExpandProperty ProcessId
Stop-Process -Id $ProcessIds -Force
Write-Host "Stopped dedicated Codex shop browser PIDs: $($ProcessIds -join ', ')"

