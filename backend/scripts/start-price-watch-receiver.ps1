[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"
$PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
$Python = if (Test-Path -LiteralPath $RuntimePython) {
    $RuntimePython
} elseif ($PythonCommand) {
    $PythonCommand.Source
} else {
    throw "Python was not found. Run scripts\bootstrap-expense-ledger.ps1 first."
}

Push-Location $Root
try {
    & $Python ".\scripts\price_watch\client.py" pull
    $ExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $ExitCode
