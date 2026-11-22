[CmdletBinding()]
param(
    [string]$Platform = "walmart_ca",
    [string]$StartUrl,
    [int]$Port = 9223,
    [switch]$NoOpen,
    [switch]$NoExtensions,
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BrowserStateDir = Join-Path $Root ".codex-shop-browser"
$ProfileDir = Join-Path $BrowserStateDir "chrome-profile"
. (Join-Path $PSScriptRoot "desktop-browser.ps1")
$SharedBrowser = Get-DesktopBrowserConfiguration -WorkspaceRoot $Root
if ($null -ne $SharedBrowser) {
    $ProfileDir = [string]$SharedBrowser.profile_path
    $Port = [int]$SharedBrowser.port
}
$DownloadsDir = Join-Path $Root "downloads"
$RawDir = Join-Path $Root "raw"
$ExportsDir = Join-Path $Root "exports"
$ConfigPath = Join-Path $Root "config\platforms.json"
$WalmartExtensionDir = Join-Path $Root "local_extensions\walmart-invoice-exporter-ca"

New-Item -ItemType Directory -Force -Path $BrowserStateDir, $ProfileDir, $DownloadsDir, $RawDir, $ExportsDir | Out-Null

$ChromeCommand = Get-Command chrome.exe -ErrorAction SilentlyContinue
$ChromeCandidates = @(
    $(if ($ChromeCommand) { $ChromeCommand.Source }),
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)',
    (Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)'
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$ChromePath = $ChromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ChromePath) {
    throw "Google Chrome was not found in PATH, the standard install paths, or Windows App Paths."
}

if (-not $StartUrl) {
    if (Test-Path $ConfigPath) {
        $platforms = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($platforms.PSObject.Properties.Name -contains $Platform) {
            $StartUrl = $platforms.$Platform.ordersUrl
        }
    }
}
if (-not $StartUrl) {
    $StartUrl = "about:blank"
}

$PortFile = Join-Path $BrowserStateDir "debug-port.txt"
Set-Content -Path $PortFile -Value $Port -Encoding ASCII

$Arguments = [System.Collections.Generic.List[string]]::new()
$Arguments.Add("--user-data-dir=$ProfileDir")
$Arguments.Add("--remote-debugging-address=127.0.0.1")
$Arguments.Add("--remote-debugging-port=$Port")
$Arguments.Add("--no-first-run")
$Arguments.Add("--no-default-browser-check")
$Arguments.Add("--disable-session-crashed-bubble")
$Arguments.Add("--disable-save-password-bubble")
$Arguments.Add("--disable-features=PasswordManagerOnboarding,SigninInterceptBubble")
$Arguments.Add("--disable-backgrounding-occluded-windows")
$Arguments.Add("--disable-renderer-backgrounding")
$Arguments.Add("--window-size=1280,920")

if (-not $NoExtensions -and (Test-Path $WalmartExtensionDir)) {
    $Arguments.Add("--load-extension=$WalmartExtensionDir")
}

if (-not $NoOpen) {
    $Arguments.Add("--new-window")
    $Arguments.Add($StartUrl)
}

function ConvertTo-CommandLineArgument {
    param([Parameter(Mandatory = $true)][string]$Argument)

    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    $Escaped = $Argument -replace '\\+$', '$0$0'
    $Escaped = $Escaped.Replace('"', '\"')
    return '"' + $Escaped + '"'
}

$ArgumentLine = ($Arguments | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join ' '
$WindowStyle = if ($Interactive) { "Normal" } else { "Hidden" }
Start-Process -FilePath $ChromePath -ArgumentList $ArgumentLine -WorkingDirectory $Root -WindowStyle $WindowStyle

Write-Host "Started dedicated Codex shop browser."
Write-Host "Profile: $ProfileDir"
Write-Host "Debug endpoint: http://127.0.0.1:$Port"
Write-Host "Start URL: $StartUrl"
if (-not $NoExtensions -and (Test-Path $WalmartExtensionDir)) {
    Write-Host "Loaded extension: $WalmartExtensionDir"
}
