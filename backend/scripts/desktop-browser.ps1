# Shared dedicated collection profile, explicitly selected by the user.
function Get-DesktopBrowserConfiguration {
    param([string]$WorkspaceRoot)
    $path = Join-Path $WorkspaceRoot 'config\desktop-browser.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $configuration = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $source = [IO.Path]::GetFullPath([string]$configuration.source_workspace).TrimEnd('\')
    $expected = Join-Path $source '.codex-shop-browser\chrome-profile'
    if ($configuration.contract -ne 'expense-desktop-shared-browser/v1' -or
        [IO.Path]::GetFullPath([string]$configuration.profile_path) -ne $expected -or
        -not (Test-Path -LiteralPath (Join-Path $source 'config\workflow.json'))) {
        throw 'Invalid shared dedicated browser configuration.'
    }
    return $configuration
}
function Get-DesktopWorkflowLockRoot {
    param([string]$WorkspaceRoot)
    $configuration = Get-DesktopBrowserConfiguration -WorkspaceRoot $WorkspaceRoot
    if ($null -ne $configuration) { return [IO.Path]::GetFullPath([string]$configuration.source_workspace).TrimEnd('\') }
    return $WorkspaceRoot
}
