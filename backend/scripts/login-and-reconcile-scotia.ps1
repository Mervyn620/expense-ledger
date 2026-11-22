[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
# Keep this as one job. Login failure/closed tab/timeout must never start reconciliation.
& (Join-Path $PSScriptRoot "prepare-scotia-login.ps1") -WaitForLogin
if ($LASTEXITCODE -ne 0) { throw "Scotia login was not confirmed; reconciliation was not started." }
Write-Output "scotia_login_status=logged_in"
& (Join-Path $PSScriptRoot "weekly-expense-refresh.ps1") -ReconcileScotia
exit $LASTEXITCODE
