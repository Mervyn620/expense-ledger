[CmdletBinding()]
param(
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

$SecureKey = Read-Host "DeepSeek API key" -AsSecureString
if ($SecureKey.Length -lt 8) {
    throw "API key is empty or too short."
}

$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $PlainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    [Environment]::SetEnvironmentVariable(
        "DEEPSEEK_API_KEY",
        $PlainKey,
        [EnvironmentVariableTarget]::User
    )
    [Environment]::SetEnvironmentVariable(
        "DEEPSEEK_API_KEY",
        $PlainKey,
        [EnvironmentVariableTarget]::Process
    )
}
finally {
    if ($Pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
    $PlainKey = $null
}

Write-Host "DeepSeek API key saved for the current Windows user."
if (-not $NoRestart) {
    & (Join-Path $PSScriptRoot "stop-expense-app.ps1")
    & (Join-Path $PSScriptRoot "start-expense-app.ps1") -NoOpen
    Write-Host "Expense app restarted with DeepSeek analysis enabled."
}
