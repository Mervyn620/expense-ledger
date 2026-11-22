[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$DurationSeconds = 20,
    [ValidateRange(1, 20)]
    [int]$Workers = 6
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CheckScript = Join-Path $PSScriptRoot "check-expense-app.ps1"
$StabilityScript = Join-Path $PSScriptRoot "maintenance\stability_suite.py"

& $CheckScript -Repair

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if ($PythonCommand) {
    $PythonCommand.Source
} elseif (Test-Path -LiteralPath $BundledPython) {
    $BundledPython
} else {
    throw "Python was not found."
}

& $Python `
    $StabilityScript `
    "--duration-seconds" "$DurationSeconds" `
    "--workers" "$Workers"
exit $LASTEXITCODE
