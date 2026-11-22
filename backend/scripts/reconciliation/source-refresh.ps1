function Get-ReconciliationBrowserPlatforms {
    param([bool]$CollectStores, [bool]$OpenStores, [bool]$Reconcile, [object[]]$Collectors)
    $Names = @()
    if ($CollectStores -or $OpenStores) {
        $Names += @($Collectors | ForEach-Object { [string]$_.tab_platform })
    }
    if ($Reconcile) { $Names += 'scotia' }
    return @($Names | Select-Object -Unique)
}

function Get-NeededReconciliationCollectors {
    param($Plan, [object[]]$Collectors)
    if ($Plan.contract -ne 'expense-reconciliation-source-plan/v1' -or $Plan.read_only -ne $true) {
        throw 'Reconciliation source plan contract is invalid.'
    }
    $Needed = @($Plan.platforms | Where-Object { $_.action -eq 'refresh' })
    $Allowed = @($Collectors | ForEach-Object { [string]$_.id })
    foreach ($Row in $Needed) {
        if ($Row.collector_id -notin $Allowed -or $Row.min_date -notmatch '^20\d{2}-\d{2}-\d{2}$') {
            throw 'Reconciliation source plan contains an invalid collector or date.'
        }
    }
    if (@($Needed.collector_id | Select-Object -Unique).Count -ne $Needed.Count) {
        throw 'Reconciliation source plan contains duplicate collectors.'
    }
    return @($Collectors | Where-Object { $_.id -in @($Needed.collector_id) })
}
