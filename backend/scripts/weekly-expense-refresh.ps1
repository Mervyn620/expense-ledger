[CmdletBinding()]
param(
    [switch]$OpenLoginPages,
    [switch]$CollectFromBrowser,
    [switch]$ReconcileScotia,
    [Alias("KeepRawFoodDeliverySnapshots")]
    [switch]$KeepRawSnapshots,
    [switch]$RefreshAllPaymentEvidence,
    [switch]$ContinueCollectorRecovery,
    [ValidateSet("auto", "local", "vps")]
    [string]$CollectorBackend = "auto",
    [string]$CollectorPlatforms = "",
    [int]$Port = 0,
    [int]$OverlapDays = 0,
    [int]$WalmartOverlapDays = 0
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$Warnings = @()
$CollectorBundleStatus = "not_applicable"
$CollectorBundleFailedComponents = @()
$CollectorBundleCachedComponents = @()
$SelectedCollectorIds = @()
$LocalOnlyCollectorIds = @()
$OnDemandCollectorIds = @()
$ReconciliationSourcePlan = $null
$CollectedOrderCollectorIds = @()
$ReconciliationCollectorIds = @()
. (Join-Path $PSScriptRoot "reconciliation\source-refresh.ps1")
$CollectionRunId = if ($CollectFromBrowser -or $ReconcileScotia) {
    [guid]::NewGuid().ToString("N")
} else {
    ""
}
$StartedAt = Get-Date
$CurrentStage = "Preparing runtime"
$ResolvedCollectorBackend = if ($CollectFromBrowser) { "" } else { "not_applicable" }

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$CurrentExportDir = Join-Path $Root "exports\current"
$RunStatePath = Join-Path $CurrentExportDir "run-state.json"
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
$WorkflowMutex = New-Object System.Threading.Mutex(
    $false,
    "Local\ExpenseLedgerWorkflow-$RootHash"
)
$WorkflowLockAcquired = $false
try {
    $WorkflowLockAcquired = $WorkflowMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $WorkflowLockAcquired = $true
}
if (-not $WorkflowLockAcquired) {
    $WorkflowMutex.Dispose()
    [Console]::Error.WriteLine(
        "Another expense refresh is already running for this workspace."
    )
    exit 2
}

function Write-AtomicUtf8 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )
    $Directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $Temporary = Join-Path $Directory ".$([System.IO.Path]::GetFileName($Path)).$PID.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $Temporary,
            $Content,
            [System.Text.UTF8Encoding]::new($false)
        )
        # The app reads these status files while a refresh runs. Windows cannot replace a file
        # that is open at that instant, so retry briefly instead of failing the whole refresh.
        for ($Attempt = 1; ; $Attempt++) {
            try {
                Move-Item -LiteralPath $Temporary -Destination $Path -Force -ErrorAction Stop
                break
            } catch {
                if ($Attempt -ge 50) { throw }
                Start-Sleep -Milliseconds 100
            }
        }
    } finally {
        if (Test-Path -LiteralPath $Temporary) {
            Remove-Item -LiteralPath $Temporary -Force
        }
    }
}

function Convert-CollectorTimestamp {
    param($Value)
    # PowerShell 7 parses JSON ISO timestamps into DateTime; preserve the offset
    # instead of converting them to a locale-dependent month/day string.
    if ($Value -is [datetime] -or $Value -is [datetimeoffset]) {
        return $Value.ToString("o")
    }
    return [string]$Value
}

function Release-WorkflowLock {
    if ($script:WorkflowLockAcquired) {
        $script:WorkflowMutex.ReleaseMutex()
        $script:WorkflowLockAcquired = $false
    }
    if ($null -ne $script:WorkflowMutex) {
        $script:WorkflowMutex.Dispose()
        $script:WorkflowMutex = $null
    }
}

function Write-RunState {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("running", "passed", "partial", "failed")][string]$Status,
        [string]$Message = ""
    )

    New-Item -ItemType Directory -Path $CurrentExportDir -Force | Out-Null
    $Now = Get-Date
    $Payload = [ordered]@{
        status = $Status
        stage = $script:CurrentStage
        updated_at = $Now.ToString("o")
        started_at = $StartedAt.ToString("o")
        completed_at = if ($Status -eq "running") { $null } else { $Now.ToString("o") }
        duration_seconds = [math]::Round(($Now - $StartedAt).TotalSeconds, 1)
        collect_from_browser = [bool]$CollectFromBrowser
        collector_backend_requested = [string]$CollectorBackend
        collector_backend_resolved = [string]$ResolvedCollectorBackend
        collector_platforms = @($SelectedCollectorIds)
        collector_local_only_platforms = @($LocalOnlyCollectorIds)
        collector_on_demand_platforms = @($OnDemandCollectorIds)
        collection_run_id = [string]$CollectionRunId
        reconcile_scotia = [bool]$ReconcileScotia
        message = $Message
    } | ConvertTo-Json
    Write-AtomicUtf8 -Path $RunStatePath -Content ($Payload + [Environment]::NewLine)
    $script:LastRunStatus = $Status
}

trap {
    $FailureMessage = $_.Exception.Message
    try {
        Write-RunState -Status "failed" -Message $FailureMessage
        if ($Python -and (Test-Path -LiteralPath (Join-Path $Root 'data/expenses.sqlite'))) {
            & $Python -m app.data_status
        }
    } catch {
        [Console]::Error.WriteLine("Could not write failed run state: $($_.Exception.Message)")
    }
    Release-WorkflowLock
    [Console]::Error.WriteLine("Weekly expense refresh failed: $FailureMessage")
    exit 1
}

Write-RunState -Status "running"
. (Join-Path $PSScriptRoot "maintenance\bounded-process.ps1")

$RuntimePython = Join-Path $Root ".app-runtime\Scripts\python.exe"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
$PythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
# Embedded runtime (tools/setup-runtime.ps1) first; the desktop host passes its paths in.
$EmbeddedPython = if ($env:EXPENSE_RUNTIME_PYTHON) { $env:EXPENSE_RUNTIME_PYTHON } else { Join-Path $Root "..\runtime\python\python.exe" }
$Python = if (Test-Path -LiteralPath $EmbeddedPython) {
    (Resolve-Path -LiteralPath $EmbeddedPython).Path
} elseif (Test-Path -LiteralPath $RuntimePython) {
    $RuntimePython
} elseif ($PythonCommand) {
    $PythonCommand.Source
} elseif ($PythonLauncher) {
    $PythonLauncher.Source
} elseif (Test-Path -LiteralPath $BundledPython) {
    $BundledPython
} else {
    throw "Python was not found. Install Python or make python.exe available in PATH."
}

$BundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$NodeCommand = Get-Command node -ErrorAction SilentlyContinue
$EmbeddedNode = if ($env:EXPENSE_RUNTIME_NODE) { $env:EXPENSE_RUNTIME_NODE } else { Join-Path $Root "..\runtime\node\node.exe" }
$Node = if (Test-Path -LiteralPath $EmbeddedNode) {
    (Resolve-Path -LiteralPath $EmbeddedNode).Path
} elseif ($NodeCommand) {
    $NodeCommand.Source
} elseif (Test-Path -LiteralPath $BundledNode) {
    $BundledNode
} else {
    throw "Node.js was not found. Install Node.js or make node.exe available in PATH."
}

if ($CollectFromBrowser) {
    $CurrentStage = "Checking collector backend"
    Write-RunState -Status "running"
    $BackendOutput = Invoke-BoundedProcess -FilePath $Python `
        -Arguments @(".\scripts\collectors\backend_control.py", "resolve", "--mode", $CollectorBackend) `
        -Label $CurrentStage -TimeoutSeconds 45
    try {
        $BackendResolution = $BackendOutput | ConvertFrom-Json
    } catch {
        throw "Collector backend returned invalid status: $BackendOutput"
    }
    $ResolvedCollectorBackend = [string]$BackendResolution.resolved_backend
    $LocalOnlyCollectorIds = @($BackendResolution.local_only_platforms |
        ForEach-Object { [string]$_ })
    Write-Host "collector_backend_requested=$CollectorBackend"
    Write-Host "collector_backend_resolved=$ResolvedCollectorBackend"
}

$CurrentStage = "Checking ledger and creating backup"
Write-RunState -Status "running"
$SafetyOutput = Invoke-BoundedProcess -FilePath $Python `
    -Arguments @(".\scripts\maintenance\safety.py", "preflight") `
    -Label $CurrentStage -TimeoutSeconds 120
if ($SafetyOutput) {
    Write-Host $SafetyOutput
}

function Test-JsonProperty {
    param(
        [object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    return $null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name
}

$WorkflowConfigPath = Join-Path $Root "config\workflow.json"
if (-not (Test-Path -LiteralPath $WorkflowConfigPath)) {
    throw "Workflow configuration is missing: $WorkflowConfigPath"
}
$WorkflowConfig = Get-Content -Raw -LiteralPath $WorkflowConfigPath | ConvertFrom-Json
# Optional: collect several stores at once, each in its own browser window. Off by default.
$ParallelCollection = $false
$ParallelCollectionWorkers = 3
if ($null -ne $WorkflowConfig.parallel_collection) {
    $ParallelCollection = $WorkflowConfig.parallel_collection.enabled -eq $true
    if ($WorkflowConfig.parallel_collection.max_workers -is [int] -and $WorkflowConfig.parallel_collection.max_workers -ge 1) {
        $ParallelCollectionWorkers = [Math]::Min(6, [int]$WorkflowConfig.parallel_collection.max_workers)
    }
}
# This machine's choice from the desktop settings (untracked) overrides the repository default.
$LocalCollectionPath = Join-Path $Root "config\collection.local.json"
if (Test-Path -LiteralPath $LocalCollectionPath) {
    $LocalCollection = Get-Content -Raw -LiteralPath $LocalCollectionPath | ConvertFrom-Json
    if ($null -ne $LocalCollection.parallel_collection -and $null -ne $LocalCollection.parallel_collection.enabled) {
        $ParallelCollection = $LocalCollection.parallel_collection.enabled -eq $true
    }
}
if ($Port -le 0) {
    $Port = [int]$WorkflowConfig.port
}
if ($OverlapDays -le 0) {
    $OverlapDays = [int]$WorkflowConfig.overlap_days
}
if ($WalmartOverlapDays -le 0) {
    $WalmartOverlapDays = [int]$WorkflowConfig.walmart_overlap_days
}
$CollectionFloor = [datetime]::ParseExact($WorkflowConfig.collection_floor, "yyyy-MM-dd", $null)
$PaymentEvidenceStart = [string]$WorkflowConfig.payment_evidence_floor
$UberReceiptMaxOrders = [string]$WorkflowConfig.collector_max_orders.uber_eats_receipts
$PaymentEvidenceMaxOrders = [string]$WorkflowConfig.collector_max_orders.payment_evidence
$OnDemandCollectorIds = @($WorkflowConfig.on_demand_collectors |
    ForEach-Object { [string]$_ })
$CollectorManifestPath = Join-Path $Root "config\collectors.json"
$CollectorManifest = Get-Content -Raw -LiteralPath $CollectorManifestPath | ConvertFrom-Json
$OrderCollectors = @($CollectorManifest.collectors | Where-Object {
    $_.enabled -eq $true -and $_.role -eq "orders"
})
if ($OrderCollectors.Count -eq 0) {
    throw "No enabled order collectors are defined in config/collectors.json."
}
$AvailableCollectorIds = @($OrderCollectors | ForEach-Object { [string]$_.id })
$RequestedCollectorIds = @(
    $CollectorPlatforms.Split(',', [System.StringSplitOptions]::RemoveEmptyEntries) |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
if ($RequestedCollectorIds.Count -gt 0 -and -not $CollectFromBrowser) {
    throw "CollectorPlatforms can only be used with CollectFromBrowser."
}
if (@($RequestedCollectorIds | Select-Object -Unique).Count -ne $RequestedCollectorIds.Count) {
    throw "CollectorPlatforms cannot contain duplicate platform ids."
}
$UnknownCollectorIds = @($RequestedCollectorIds | Where-Object { $_ -notin $AvailableCollectorIds })
if ($UnknownCollectorIds.Count -gt 0) {
    throw "CollectorPlatforms contains unsupported ids: $($UnknownCollectorIds -join ', ')"
}
$UnknownLocalOnlyIds = @($LocalOnlyCollectorIds | Where-Object { $_ -notin $AvailableCollectorIds })
if ($UnknownLocalOnlyIds.Count -gt 0) {
    throw "Backend local_only_platforms contains unsupported ids: $($UnknownLocalOnlyIds -join ', ')"
}
$UnknownOnDemandIds = @($OnDemandCollectorIds | Where-Object { $_ -notin $AvailableCollectorIds })
if (
    $UnknownOnDemandIds.Count -gt 0 -or
    @($OnDemandCollectorIds | Select-Object -Unique).Count -ne $OnDemandCollectorIds.Count
) {
    throw "Workflow on_demand_collectors is invalid."
}
$SelectedCollectorIds = if ($RequestedCollectorIds.Count -gt 0) {
    @($RequestedCollectorIds)
} else {
    if ($CollectFromBrowser -or $OpenLoginPages) {
        @($AvailableCollectorIds | Where-Object { $_ -notin $OnDemandCollectorIds })
    } else { @() }
}
$SelectedOrderCollectors = @(
    $OrderCollectors | Where-Object { [string]$_.id -in $SelectedCollectorIds }
)
$LocalOrderCollectors = @(
    if ($ResolvedCollectorBackend -eq "vps") {
        $SelectedOrderCollectors | Where-Object { [string]$_.id -in $LocalOnlyCollectorIds }
    } else {
        $SelectedOrderCollectors
    }
)
$VpsOrderCollectors = @(
    if ($ResolvedCollectorBackend -eq "vps") {
        $SelectedOrderCollectors | Where-Object { [string]$_.id -notin $LocalOnlyCollectorIds }
    }
)
$LocalCollectorIds = @($LocalOrderCollectors | ForEach-Object { [string]$_.id })
$ActiveOrderCollectors = if ($CollectFromBrowser) {
    @($SelectedOrderCollectors)
} else {
    @($OrderCollectors)
}
if ($CollectFromBrowser) { $CollectedOrderCollectorIds = @($SelectedCollectorIds) }
$CurrentStage = "Starting workflow"
Write-RunState -Status "running"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Block
    )
    Write-Host "== $Label =="
    $script:CurrentStage = $Label
    if ($script:LastRunStatus -eq "running") { Write-RunState -Status "running" }
    & $Block
}

function Invoke-OptionalStep {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Block
    )
    Write-Host "== $Label =="
    $script:CurrentStage = $Label
    if ($script:LastRunStatus -eq "running") { Write-RunState -Status "running" }
    try {
        & $Block
    } catch {
        $script:Warnings += "$Label failed: $($_.Exception.Message)"
        Write-Warning $script:Warnings[-1]
    }
}

function Invoke-ManifestCollectorsSequentially {
    param(
        [Parameter(Mandatory = $true)][object[]]$Collectors,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Contexts
    )

    foreach ($Collector in $Collectors) {
        $Context = $Contexts[$Collector.id]
        Write-Host "== Collect $($Collector.label) orders =="
        $Arguments = @(
            ".\scripts\collectors\runner.py",
            "run",
            "--id", [string]$Collector.id,
            "--phase", "collect",
            "--port", "$Port",
            "--min-date", [string]$Context.min_date,
            "--node", "$Node"
        )
        if ($null -ne $Context.max_orders) {
            $Arguments += @("--max-orders", [string]$Context.max_orders)
        }
        $PreviousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $Output = (& $Python @Arguments 2>&1 | Out-String).Trim()
            $ExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $PreviousPreference
        }
        if ($Output) {
            Write-Host $Output
        }
        if ($ExitCode -ne 0) {
            $script:Warnings += "$($Collector.label) collector failed: $Output"
            Write-Warning $script:Warnings[-1]
        }
    }
}

function Invoke-ManifestCollectors {
    param(
        [Parameter(Mandatory = $true)][object[]]$Collectors,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Contexts
    )
    if (-not $ParallelCollection -or $Collectors.Count -lt 2) {
        Write-Host "collection_mode=sequential"
        Invoke-ManifestCollectorsSequentially -Collectors $Collectors -Contexts $Contexts
        return
    }
    Write-Host "collection_mode=parallel"
    Write-Host "== Collect $(@($Collectors | ForEach-Object { $_.label }) -join ' / ') orders in parallel =="
    $Arguments = @(
        ".\scripts\collectors\runner.py",
        "run-many",
        "--phase", "collect",
        "--port", "$Port",
        "--node", "$Node",
        "--max-workers", "$ParallelCollectionWorkers"
    )
    foreach ($Collector in $Collectors) {
        $Context = $Contexts[$Collector.id]
        $Job = "$($Collector.id)=$($Context.min_date)"
        if ($null -ne $Context.max_orders) { $Job += "=$($Context.max_orders)" }
        $Arguments += @("--job", $Job)
    }
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $Output = (& $Python @Arguments 2>&1 | Out-String).Trim()
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($Output) {
        Write-Host $Output
    }
    if ($ExitCode -ne 0) {
        # The batch itself could not start; fall back to collecting one store at a time.
        $script:Warnings += "Parallel collection could not start; collected sequentially instead: $Output"
        Write-Warning $script:Warnings[-1]
        Invoke-ManifestCollectorsSequentially -Collectors $Collectors -Contexts $Contexts
        return
    }
    foreach ($Collector in $Collectors) {
        $Pattern = "^collector_parallel_result=$([regex]::Escape([string]$Collector.id)):(-?\d+)$"
        $Line = @($Output -split "`r?`n" | Where-Object { $_ -match $Pattern }) | Select-Object -First 1
        $Code = if ($Line -and $Line -match $Pattern) { [int]$Matches[1] } else { 1 }
        if ($Code -ne 0) {
            $script:Warnings += "$($Collector.label) collector failed (parallel run); see its output above."
            Write-Warning $script:Warnings[-1]
        }
    }
}

function Invoke-ManifestImports {
    param([object[]]$Collectors)
    foreach ($Collector in $Collectors) {
        $Context = $CollectorContexts[$Collector.id]
        Invoke-Step "Import $($Collector.label) cache" {
            $Arguments = @(
                ".\scripts\collectors\runner.py",
                "run",
                "--id", [string]$Collector.id,
                "--phase", "import",
                "--port", "$Port",
                "--min-date", [string]$Context.min_date,
                "--node", "$Node"
            )
            if ($null -ne $Context.max_orders) {
                $Arguments += @("--max-orders", [string]$Context.max_orders)
            }
            Invoke-Native -FilePath $Python -Arguments $Arguments
        }
    }

}

function Test-ShopCdp {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Start-DedicatedShopBrowser {
    param([string]$StartPlatform = "walmart_ca")
    if (Test-ShopCdp) {
        return
    }

    & .\scripts\start-shop-browser.ps1 -Platform $StartPlatform -Port $Port
    for ($Attempt = 0; $Attempt -lt 15; $Attempt++) {
        Start-Sleep -Seconds 1
        if (Test-ShopCdp) {
            return
        }
    }
    throw "Dedicated shop browser did not expose CDP port $Port."
}

function Get-LedgerTransactions {
    $Json = (& $Python ".\scripts\ledger\runtime_state.py" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read runtime state from SQLite: $Json"
    }
    if (-not $Json) {
        return @()
    }
    $ParsedRows = $Json | ConvertFrom-Json
    return @($ParsedRows | ForEach-Object {
        $Date = if ($_.date -is [datetime]) {
            $_.date.ToString("yyyy-MM-dd")
        } else {
            [string]$_.date
        }
        [PSCustomObject]@{
            date = $Date
            platform = [string]$_.platform
            order_id = [string]$_.order_id
            merchant = [string]$_.merchant
            total = $_.total
            currency = [string]$_.currency
        }
    })
}

function Get-TransactionKey {
    param($Row)
    return "$($Row.platform)|$($Row.order_id)"
}

function Get-CollectionStart {
    param(
        [Parameter(Mandatory = $true)][string]$Platform,
        [Parameter(Mandatory = $true)][int]$Days,
        [Parameter(Mandatory = $true)][object[]]$Transactions
    )
    $Rows = @($Transactions | Where-Object { $_.platform -eq $Platform -and $_.date })
    if ($Rows.Count -eq 0) {
        return $CollectionFloor.ToString("yyyy-MM-dd")
    }

    $Latest = $Rows |
        ForEach-Object { [datetime]::ParseExact($_.date, "yyyy-MM-dd", $null) } |
        Sort-Object -Descending |
        Select-Object -First 1
    $Start = $Latest.AddDays(-1 * $Days)
    if ($Start -lt $CollectionFloor) {
        $Start = $CollectionFloor
    }
    return $Start.ToString("yyyy-MM-dd")
}

function Get-ScotiaCollectionStart {
    $Path = Join-Path $Root "data\scotia_transactions.json"
    if (-not (Test-Path -LiteralPath $Path)) {
        return $CollectionFloor.ToString("yyyy-MM-dd")
    }
    try {
        $Payload = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
        $Dates = @($Payload.transactions |
            Where-Object { $_.date } |
            ForEach-Object { [datetime]::ParseExact($_.date, "yyyy-MM-dd", $null) })
        if ($Dates.Count -eq 0) {
            return $CollectionFloor.ToString("yyyy-MM-dd")
        }
        $Start = ($Dates | Sort-Object -Descending | Select-Object -First 1).AddDays(-14)
        if ($Start -lt $CollectionFloor) {
            $Start = $CollectionFloor
        }
        return $Start.ToString("yyyy-MM-dd")
    } catch {
        return $CollectionFloor.ToString("yyyy-MM-dd")
    }
}

Invoke-Step "Recover committed CSV publications" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\ledger\sync.py", "--recover-csv")
}

Invoke-Step "Validate collector manifest" {
    Invoke-Native -FilePath $Python -Arguments @(
        ".\scripts\collectors\runner.py",
        "validate"
    )
}

$BeforeTransactions = @(Get-LedgerTransactions)
$BeforeKeys = @{}
foreach ($Row in $BeforeTransactions) {
    $BeforeKeys[(Get-TransactionKey $Row)] = $true
}

$CollectorContexts = [ordered]@{}
foreach ($Collector in $ActiveOrderCollectors) {
    $OverlapSetting = [string]$Collector.window.overlap_days_setting
    $OverlapProperty = $WorkflowConfig.PSObject.Properties[$OverlapSetting]
    if ($null -eq $OverlapProperty) {
        throw "Collector $($Collector.id) references an unknown overlap setting: $OverlapSetting"
    }
    $Days = [int]$OverlapProperty.Value
    $Start = Get-CollectionStart `
        -Platform ([string]$Collector.ledger_platform) `
        -Days $Days `
        -Transactions $BeforeTransactions
    $MaxOrders = $null
    if (Test-JsonProperty -Object $Collector.window -Name "max_orders_setting") {
        $MaxSetting = [string]$Collector.window.max_orders_setting
        if (-not (Test-JsonProperty -Object $WorkflowConfig.collector_max_orders -Name $MaxSetting)) {
            throw "Collector $($Collector.id) references an unknown max-orders setting: $MaxSetting"
        }
        $MaxOrders = [int]$WorkflowConfig.collector_max_orders.$MaxSetting
    }
    $CollectorContexts[$Collector.id] = [PSCustomObject]@{
        id = [string]$Collector.id
        label = [string]$Collector.label
        platform = [string]$Collector.ledger_platform
        min_date = $Start
        max_orders = $MaxOrders
    }
}

$ScotiaStart = Get-ScotiaCollectionStart
$ScotiaExportSucceeded = $false

if (
    ($CollectFromBrowser -and $LocalOrderCollectors.Count -gt 0) -or
    $OpenLoginPages -or
    $ReconcileScotia
) {
    Invoke-Step "Ensure dedicated shop browser" {
        $PlatformNames = @(Get-ReconciliationBrowserPlatforms -CollectStores ([bool]$CollectFromBrowser) -OpenStores ([bool]$OpenLoginPages) -Reconcile ([bool]$ReconcileScotia) -Collectors $LocalOrderCollectors)
        if ($PlatformNames.Count -eq 0) { throw "No browser platform was requested." }
        Start-DedicatedShopBrowser -StartPlatform $PlatformNames[0]
        $Platforms = ($PlatformNames | Select-Object -Unique) -join ","
        $TabArguments = @(
            ".\scripts\browser\ensure-shop-tabs.mjs",
            "--port", "$Port",
            "--platforms", $Platforms
        )
        # Parallel collection: each store in its own window, so none is throttled as a background tab.
        if ($ParallelCollection -and $CollectFromBrowser -and @($LocalOrderCollectors).Count -gt 1) {
            $TabArguments += "--separate-windows"
        }
        Invoke-Native -FilePath $Node -Arguments $TabArguments
    }
}

if ($CollectFromBrowser) {
    if ($ResolvedCollectorBackend -eq "local") {
        Invoke-ManifestCollectors `
            -Collectors $SelectedOrderCollectors `
            -Contexts $CollectorContexts
    } else {
        if ($VpsOrderCollectors.Count -gt 0) {
            Invoke-OptionalStep "Collect sanitized store bundle on VPS" {
            $BundleArguments = @(
                ".\scripts\collectors\vps_bundle_client.py",
                "collect",
                "--root", $Root,
                "--payment-min-date", $PaymentEvidenceStart,
                "--payment-max-orders", $PaymentEvidenceMaxOrders,
                "--collection-run-id", $CollectionRunId
            )
            if ($ContinueCollectorRecovery) {
                $BundleArguments += "--continue-recovery"
            }
            foreach ($Collector in $VpsOrderCollectors) {
                $Context = $CollectorContexts[$Collector.id]
                $CollectorLimit = if ($null -ne $Context.max_orders) {
                    [int]$Context.max_orders
                } else {
                    12
                }
                if ($RequestedCollectorIds.Count -gt 0 -or $LocalOnlyCollectorIds.Count -gt 0) {
                    $BundleArguments += @("--platform", [string]$Collector.id)
                }
                $BundleArguments += @(
                    "--window",
                    "$($Collector.id)=$($Context.min_date)",
                    "--max-order",
                    "$($Collector.id)=$CollectorLimit"
                )
            }
            $PreviousPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $BundleOutput = (& $Python @BundleArguments 2>&1 | Out-String).Trim()
                $BundleExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $PreviousPreference
            }
            if ($BundleOutput) {
                Write-Host $BundleOutput
            }
            if ($BundleExitCode -ne 0) {
                throw "$Python exited with code $BundleExitCode"
            }
            foreach ($Line in ($BundleOutput -split "`r?`n")) {
                if ($Line -match '^collector_bundle_status=(.+)$') {
                    $script:CollectorBundleStatus = $Matches[1].Trim()
                } elseif ($Line -match '^collector_bundle_failed_components=(.*)$') {
                    $MetricValue = $Matches[1].Trim()
                    $script:CollectorBundleFailedComponents = if ($MetricValue -eq "none") {
                        @()
                    } else {
                        @($MetricValue.Split(',', [System.StringSplitOptions]::RemoveEmptyEntries) |
                            ForEach-Object { $_.Trim() })
                    }
                } elseif ($Line -match '^collector_bundle_cached_components=(.*)$') {
                    $MetricValue = $Matches[1].Trim()
                    $script:CollectorBundleCachedComponents = if ($MetricValue -eq "none") {
                        @()
                    } else {
                        @($MetricValue.Split(',', [System.StringSplitOptions]::RemoveEmptyEntries) |
                            ForEach-Object { $_.Trim() })
                    }
                }
            }
            if ($script:CollectorBundleStatus -eq "partial") {
                $FailedLabel = if ($script:CollectorBundleFailedComponents.Count) {
                    $script:CollectorBundleFailedComponents -join ", "
                } else {
                    "unknown component"
                }
                $CacheLabel = if ($script:CollectorBundleCachedComponents.Count) {
                    " Last valid cache was preserved for: $($script:CollectorBundleCachedComponents -join ', ')."
                } else {
                    " No live result was claimed for failed components."
                }
                $script:Warnings += "VPS live collection was partial: $FailedLabel.$CacheLabel"
            }
            }
        }
        if ($LocalOrderCollectors.Count -gt 0) {
            Invoke-ManifestCollectors `
                -Collectors $LocalOrderCollectors `
                -Contexts $CollectorContexts
        }
    }
}

Invoke-ManifestImports -Collectors $ActiveOrderCollectors

if (
    $CollectFromBrowser -and
    "uber_eats" -in $LocalCollectorIds -and
    "uber_eats" -in $SelectedCollectorIds
) {
    Invoke-OptionalStep "Collect missing Uber Eats payment cards" {
        $Arguments = @(
            ".\scripts\collectors\export-uber-eats-receipts.mjs",
            "--port", "$Port",
            "--min-date", $PaymentEvidenceStart,
            "--max-receipts", $UberReceiptMaxOrders
        )
        if ($RefreshAllPaymentEvidence) {
            $Arguments += "--refresh-all"
        }
        Invoke-Native -FilePath $Node -Arguments $Arguments
    }

}

$SelectedExternalPaymentPlatforms = @(
    @("walmart_ca", "fantuan", "tnt_ca") | Where-Object {
        $_ -in $LocalCollectorIds
    }
)
if (
    $CollectFromBrowser -and
    $SelectedExternalPaymentPlatforms.Count -gt 0
) {
    Invoke-OptionalStep "Collect missing selected store payment evidence" {
        $Arguments = @(
            ".\scripts\collectors\export-payment-evidence.mjs",
            "--port", "$Port",
            "--min-date", $PaymentEvidenceStart,
            "--platforms", ($SelectedExternalPaymentPlatforms -join ","),
            "--max-orders", $PaymentEvidenceMaxOrders
        )
        if ($RefreshAllPaymentEvidence) {
            $Arguments += "--refresh-all"
        }
        Invoke-Native -FilePath $Node -Arguments $Arguments
    }
}

Invoke-Step "Import Uber Eats payment cards" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\importers\uber_receipt_payments.py")
}

Invoke-Step "Import store payment evidence" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\importers\payment_evidence.py")
}

if ($ReconcileScotia) {
    Write-Host "== Export sanitized Scotia transactions =="
    try {
        Invoke-Native -FilePath $Node -Arguments @(
            ".\scripts\collectors\export-scotia-transactions.mjs",
            "--port", "$Port",
            "--min-date", $ScotiaStart
        )
        $script:ScotiaExportSucceeded = $true
    } catch {
        $FailureReason = $_.Exception.Message
        $CollectorHealthPath = Join-Path $Root ".codex\collector-health.json"
        if (Test-Path -LiteralPath $CollectorHealthPath) {
            try {
                $CollectorHealth = Get-Content -Raw -LiteralPath $CollectorHealthPath | ConvertFrom-Json
                $HealthReason = [string]$CollectorHealth.platforms.scotia.reason
                if ($HealthReason) {
                    $FailureReason = $HealthReason
                }
            } catch {
                # Keep the original process failure when collector health is unreadable.
            }
        }
        $FailureReason = ([string]$FailureReason).Trim().TrimEnd('.')
        $CacheMessage = if (Test-Path -LiteralPath .\data\scotia_transactions.json) {
            "Cached sanitized transactions will be used."
        } else {
            "No cached Scotia transactions are available."
        }
        if ($FailureReason -match '(?i)log(?:ged)?.?in|sign.?in|authentication|verification|challenge') {
            $Warnings += "Scotiabank login or verification is required. $CacheMessage"
        } else {
            $Warnings += "Latest Scotiabank transaction export failed: $FailureReason. $CacheMessage"
        }
        Write-Warning $Warnings[-1]
    }
    if ($ScotiaExportSucceeded) {
        Invoke-OptionalStep "Find missing merchant order sources" {
            $PlanArguments = @(".\scripts\reconciliation\source_refresh.py", "--floor", ([string]$WorkflowConfig.collection_floor))
            if ($CollectedOrderCollectorIds.Count -gt 0) {
                $PlanArguments += @("--already-attempted", ($CollectedOrderCollectorIds -join ","))
            }
            $PlanJson = (& $Python @PlanArguments | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Merchant source planning failed; no extra page was opened." }
            $script:ReconciliationSourcePlan = $PlanJson | ConvertFrom-Json
            $NeededCollectors = @(Get-NeededReconciliationCollectors -Plan $ReconciliationSourcePlan -Collectors $OrderCollectors)
            $NeededIds = @($NeededCollectors | ForEach-Object { [string]$_.id })
            Write-Host "reconciliation_extra_platforms=$($NeededIds -join ',')"
            if ($NeededCollectors.Count -gt 0) {
                foreach ($PlanRow in @($ReconciliationSourcePlan.platforms | Where-Object { $_.action -eq 'refresh' })) {
                    $Context = $CollectorContexts[$PlanRow.collector_id]
                    if ([string]$PlanRow.min_date -lt [string]$Context.min_date) { $Context.min_date = [string]$PlanRow.min_date }
                }
                $script:ReconciliationCollectorIds = $NeededIds
                $script:CollectedOrderCollectorIds = @(@($CollectedOrderCollectorIds) + $NeededIds | Select-Object -Unique)
                $script:SelectedCollectorIds = @(@($SelectedCollectorIds) + $NeededIds | Select-Object -Unique)
                if (-not $CollectFromBrowser) { $script:ResolvedCollectorBackend = "local" }
                $TargetTabs = @($NeededCollectors | ForEach-Object { [string]$_.tab_platform }) -join ','
                Invoke-Native -FilePath $Node -Arguments @(".\scripts\browser\ensure-shop-tabs.mjs", "--port", "$Port", "--platforms", $TargetTabs)
                Invoke-ManifestCollectorsSequentially -Collectors $NeededCollectors -Contexts $CollectorContexts
                Invoke-ManifestImports -Collectors $NeededCollectors
            }
        }
    } else {
        Write-Host "reconciliation_extra_platforms=skipped_without_fresh_bank_export"
    }
    if ((Test-Path -LiteralPath .\data\scotia_transactions.json) -or (Test-Path -Path .\data\bank_statements\*.json)) {
        Invoke-Step "Reconcile bank expenses" {
            Invoke-Native -FilePath $Python -Arguments @(".\scripts\reconciliation\scotia.py")
        }
    } else {
        $Warnings += "Scotia reconciliation skipped: no sanitized transaction cache is available."
    }
} elseif (Test-Path -Path .\data\bank_statements\*.json) {
    # Imported OFX/CSV statements need no bank login, so every refresh reconciles them
    # (together with the last Scotia cache, if any).
    Invoke-Step "Reconcile bank expenses" {
        Invoke-Native -FilePath $Python -Arguments @(".\scripts\reconciliation\scotia.py")
    }
}

Invoke-Step "Refresh card assignments" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\ledger\refresh_assignments.py")
}

Invoke-Step "Reconcile SQLite ledger" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\ledger\sync.py")
}

Invoke-Step "Export accounting reports" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\reporting\export_reports.py")
}

Invoke-Step "Export monthly period reports" {
    $Months = @(Get-LedgerTransactions |
        Where-Object { $_.date -match '^\d{4}-\d{2}-\d{2}$' } |
        ForEach-Object { $_.date.Substring(0, 7) } |
        Sort-Object -Unique)
    foreach ($Month in $Months) {
        Invoke-Native -FilePath $Python -Arguments @(
            ".\scripts\reporting\export_reports.py",
            "--month",
            $Month
        )
    }
}

Invoke-Step "Validate dedupe and totals" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\reporting\validate.py")
}

Invoke-Step "Create verified post-refresh checkpoint" {
    Invoke-Native -FilePath $Python -Arguments @(
        ".\scripts\maintenance\safety.py",
        "checkpoint",
        "--label",
        "post-refresh"
    )
}

Invoke-Step "Verify complete application safety state" {
    Invoke-Native -FilePath $Python -Arguments @(
        ".\scripts\maintenance\validate_all.py",
        "--skip-ledger"
    )
}

if (-not $KeepRawSnapshots) {
    Invoke-Step "Clean raw platform snapshots" {
        if (-not (Test-Path -LiteralPath .\raw)) {
            New-Item -ItemType Directory -Path .\raw | Out-Null
        }
        $RawDir = (Resolve-Path .\raw).Path
        $WorkspaceRoot = (Resolve-Path .).Path
        if (-not $RawDir.StartsWith($WorkspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Resolved raw directory is outside the workspace: $RawDir"
        }
        $Files = @(Get-ChildItem -LiteralPath $RawDir -File |
            Where-Object { $_.Name -match '_(walmart_ca|uber_eats|fantuan)_' })
        foreach ($File in $Files) {
            Remove-Item -LiteralPath $File.FullName -Force
        }
        Write-Host "Removed $($Files.Count) raw platform snapshot(s)."
    }
}

$Transactions = @(Get-LedgerTransactions)
$NewTransactions = @($Transactions | Where-Object { -not $BeforeKeys.ContainsKey((Get-TransactionKey $_)) })
$Assignments = if (Test-Path .\data\payment_assignments.csv) {
    @(Import-Csv .\data\payment_assignments.csv)
} else {
    @()
}
$ConfirmedAssignments = @($Assignments | Where-Object { $_.assignment_status -eq "confirmed" })
$InferredAssignments = @($Assignments | Where-Object { $_.assignment_status -eq "inferred" })
$ReviewAssignments = @($Assignments | Where-Object {
    $_.card_id -eq "unassigned" -or $_.assignment_status -eq "needs_review"
})
$ScotiaReport = if (Test-Path .\exports\reconciliation\scotia.csv) {
    @(Import-Csv .\exports\reconciliation\scotia.csv)
} else {
    @()
}
$ScotiaPlatformReview = @(
    $ScotiaReport | Where-Object { $_.match_status -eq "unmatched_platform_review" }
)
if ($ScotiaPlatformReview.Count -gt 0) {
    $Warnings += (
        "$($ScotiaPlatformReview.Count) unmatched platform charge(s) were held out " +
        "of the ledger for review."
    )
}
$CollectorHealthRows = @()
$CollectorHealthPath = Join-Path $Root ".codex\collector-health.json"
if (Test-Path -LiteralPath $CollectorHealthPath) {
    try {
        $CollectorHealthPayload = Get-Content `
            -Raw `
            -LiteralPath $CollectorHealthPath |
            ConvertFrom-Json
        foreach ($Property in $CollectorHealthPayload.platforms.PSObject.Properties) {
            $Value = $Property.Value
            $CollectorHealthRows += [PSCustomObject]@{
                platform = $Property.Name
                status = [string]$Value.status
                last_success = [string]$Value.last_success
                last_failure = [string]$Value.last_failure
                blocked_until = [string]$Value.blocked_until
                reason = [string]$Value.reason
            }
        }
    } catch {
        $Warnings += "Collector health state could not be read."
    }
}

$CollectorRunRows = @()
$CollectorRunDir = Join-Path $Root ".codex\collector-runs"
foreach ($Collector in $ActiveOrderCollectors) {
    $Phases = if ($Collector.id -in $CollectedOrderCollectorIds) { @("collect", "import") } else { @("import") }
    foreach ($Phase in $Phases) {
        $RunPath = Join-Path $CollectorRunDir "$($Collector.id)-$Phase.json"
        if (-not (Test-Path -LiteralPath $RunPath)) {
            continue
        }
        try {
            $Run = Get-Content -Raw -LiteralPath $RunPath | ConvertFrom-Json
            $RunStarted = [datetime]::Parse([string]$Run.started_at)
            if ($RunStarted -lt $StartedAt.AddMinutes(-1)) {
                continue
            }
            $CollectorRunRows += [PSCustomObject]@{
                collector_id = [string]$Run.collector_id
                label = [string]$Collector.label
                phase = [string]$Run.phase
                status = [string]$Run.status
                exit_code = [int]$Run.exit_code
                started_at = Convert-CollectorTimestamp $Run.started_at
                completed_at = Convert-CollectorTimestamp $Run.completed_at
                duration_seconds = [double]$Run.duration_seconds
                message = [string]$Run.message
            }
        } catch {
            $Warnings += "Collector run state could not be read: $($Collector.id) $Phase"
        }
    }
}

$CurrentCollectRuns = @(
    $CollectorRunRows | Where-Object { $_.phase -eq "collect" }
)
foreach ($Run in $CurrentCollectRuns) {
    $HealthRow = @(
        $CollectorHealthRows |
            Where-Object { $_.platform -eq $Run.collector_id }
    ) | Select-Object -First 1
    if ($null -eq $HealthRow) {
        $HealthRow = [PSCustomObject]@{
            platform = [string]$Run.collector_id
            status = ""
            last_success = ""
            last_failure = ""
            blocked_until = ""
            reason = ""
        }
        $CollectorHealthRows += $HealthRow
    }
    if ($Run.status -eq "passed") {
        $HealthRow.status = "healthy"
        $HealthRow.last_success = [string]$Run.completed_at
        $HealthRow.reason = ""
        $HealthRow.blocked_until = ""
    } else {
        $HealthRow.status = [string]$Run.status
        $HealthRow.last_failure = [string]$Run.completed_at
        $HealthRow.reason = [string]$Run.message
        if ($Run.status -notin @("blocked", "cooldown")) {
            $HealthRow.blocked_until = ""
        }
    }
}

$LatestByPlatform = @($Transactions |
    Group-Object platform |
    ForEach-Object {
        $Latest = $_.Group | Sort-Object date -Descending | Select-Object -First 1
        [PSCustomObject]@{
            platform = $_.Name
            latest_date = $Latest.date
            orders = $_.Count
            total = [math]::Round((($_.Group | ForEach-Object { [double]$_.total } | Measure-Object -Sum).Sum), 2)
        }
    } | Sort-Object platform)

$NewTotal = [math]::Round((($NewTransactions | ForEach-Object { [double]$_.total } | Measure-Object -Sum).Sum), 2)
$DurationSeconds = [math]::Round(((Get-Date) - $StartedAt).TotalSeconds, 1)
New-Item -ItemType Directory -Path $CurrentExportDir -Force | Out-Null
$StatusPath = Join-Path $CurrentExportDir "weekly-status.md"
$StatusJsonPath = Join-Path $CurrentExportDir "weekly-status.json"
$CollectionStatus = if (-not $CollectFromBrowser -and $CollectedOrderCollectorIds.Count -eq 0) {
    "not_run"
} elseif (
    $CollectorBundleStatus -eq "partial" -or
    @($CurrentCollectRuns | Where-Object { $_.status -ne "passed" }).Count -gt 0 -or
    @($Warnings | Where-Object {
        $_ -match 'collector failed|Collect missing|sanitized store bundle'
    }).Count -gt 0
) {
    "partial"
} else {
    "passed"
}

$Lines = @(
    "# Weekly Expense Refresh",
    "",
    "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
    "",
    "## Result",
    "",
    "- New orders: $($NewTransactions.Count)",
    "- New total: CAD $($NewTotal.ToString('0.00'))",
    "- Validation: passed",
    "- Live collection: $CollectionStatus",
    "- Confirmed payment sources: $($ConfirmedAssignments.Count)",
    "- Inferred payment sources: $($InferredAssignments.Count)",
    "- Waiting for payment assignment: $($ReviewAssignments.Count)",
    "- Scotia bank-only imports: $(@($ScotiaReport | Where-Object { $_.match_status -eq 'bank_only_imported' }).Count)",
    "- Scotia platform charges requiring review: $($ScotiaPlatformReview.Count)",
    "- Duration: $DurationSeconds seconds",
    "",
    "## Platform Status",
    "",
    "| Platform | Latest Date | Orders | Total CAD |",
    "|---|---:|---:|---:|"
)
foreach ($Row in $LatestByPlatform) {
    $Lines += "| $($Row.platform) | $($Row.latest_date) | $($Row.orders) | $($Row.total.ToString('0.00')) |"
}

$Lines += @(
    "",
    "## New Orders",
    "",
    "| Date | Platform | Merchant | Order ID | Total CAD |",
    "|---|---|---|---|---:|"
)
if ($NewTransactions.Count -eq 0) {
    $Lines += "|  |  | No new orders |  | 0.00 |"
} else {
    foreach ($Row in ($NewTransactions | Sort-Object date, platform, order_id)) {
        $Lines += "| $($Row.date) | $($Row.platform) | $($Row.merchant) | $($Row.order_id) | $([double]$Row.total) |"
    }
}

if ($ReviewAssignments.Count -gt 0) {
    $Lines += @(
        "",
        "## Payment Review",
        "",
        "| Date | Platform | Merchant | Order ID | Total CAD | Notes |",
        "|---|---|---|---|---:|---|"
    )
    foreach ($Row in ($ReviewAssignments | Sort-Object date, platform, order_id)) {
        $Lines += "| $($Row.date) | $($Row.platform) | $($Row.merchant) | $($Row.order_id) | $($Row.total) | $($Row.notes) |"
    }
}

if ($Warnings.Count -gt 0) {
    $Lines += @("", "## Warnings", "")
    foreach ($Warning in $Warnings) {
        $Lines += "- $Warning"
    }
}

if ($CollectorHealthRows.Count -gt 0) {
    $Lines += @(
        "",
        "## Collector Health",
        "",
        "| Platform | Status | Last Success | Cooldown Until |",
        "|---|---|---|---|"
    )
    foreach ($Row in ($CollectorHealthRows | Sort-Object platform)) {
        $Lines += "| $($Row.platform) | $($Row.status) | $($Row.last_success) | $($Row.blocked_until) |"
    }
}

if ($CollectorRunRows.Count -gt 0) {
    $Lines += @(
        "",
        "## Component Runs",
        "",
        "| Collector | Phase | Status | Seconds | Message |",
        "|---|---|---|---:|---|"
    )
    foreach ($Row in $CollectorRunRows) {
        $Message = $Row.message.Replace("|", "\|")
        $Lines += "| $($Row.label) | $($Row.phase) | $($Row.status) | $($Row.duration_seconds) | $Message |"
    }
}

$CollectionWindowsStatus = [ordered]@{}
foreach ($Collector in $ActiveOrderCollectors) {
    $Context = $CollectorContexts[$Collector.id]
    $CollectionWindowsStatus[[string]$Collector.ledger_platform] = [string]$Context.min_date
}
$CollectionWindowsStatus["scotia"] = $ScotiaStart
$CollectionWindowsStatus["floor"] = [string]$WorkflowConfig.collection_floor
$CollectionWindowsStatus["payment_evidence_floor"] = $PaymentEvidenceStart

$Lines += @(
    "",
    "## Collection Windows",
    ""
)
foreach ($Collector in $ActiveOrderCollectors) {
    $Context = $CollectorContexts[$Collector.id]
    $Lines += "- $($Collector.label): $($Context.min_date) onward"
}

Write-AtomicUtf8 `
    -Path $StatusPath `
    -Content (($Lines -join [Environment]::NewLine) + [Environment]::NewLine)

$StatusObject = [ordered]@{
    generated_at = (Get-Date).ToString("o")
    duration_seconds = $DurationSeconds
    collect_from_browser = [bool]$CollectFromBrowser
    collector_platforms = @($SelectedCollectorIds)
    collector_local_only_platforms = @($LocalOnlyCollectorIds)
    collector_on_demand_platforms = @($OnDemandCollectorIds)
    collection_run_id = [string]$CollectionRunId
    collection_status = $CollectionStatus
    collector_bundle_status = [string]$CollectorBundleStatus
    collector_bundle_failed_components = @($CollectorBundleFailedComponents)
    collector_bundle_cached_components = @($CollectorBundleCachedComponents)
    validation = "passed"
    new_order_count = $NewTransactions.Count
    new_total = $NewTotal
    confirmed_payment_count = $ConfirmedAssignments.Count
    inferred_payment_count = $InferredAssignments.Count
    review_payment_count = $ReviewAssignments.Count
    platform_totals = $LatestByPlatform
    new_orders = @($NewTransactions | Select-Object date, platform, order_id, merchant, total, currency)
    warnings = @($Warnings)
    collector_health = @($CollectorHealthRows)
    collector_runs = @($CollectorRunRows)
    collection_windows = $CollectionWindowsStatus
    scotia_reconciliation = [ordered]@{
        enabled = [bool]$ReconcileScotia
        exported = [bool]$ScotiaExportSucceeded
        source_plan = $ReconciliationSourcePlan
        refreshed_platforms = @($ReconciliationCollectorIds)
        bank_only_imported = @($ScotiaReport | Where-Object { $_.match_status -eq "bank_only_imported" }).Count
        platform_review_required = $ScotiaPlatformReview.Count
        matched_orders = @($ScotiaReport | Where-Object { $_.match_status -eq "matched_order" }).Count
        reversed = @($ScotiaReport | Where-Object { $_.match_status -eq "reversed" }).Count
        excluded_transfers = @($ScotiaReport | Where-Object { $_.match_status -eq "excluded_transfer" }).Count
        excluded_credits = @($ScotiaReport | Where-Object { $_.match_status -eq "credit" }).Count
    }
}
Write-AtomicUtf8 `
    -Path $StatusJsonPath `
    -Content (($StatusObject | ConvertTo-Json -Depth 6) + [Environment]::NewLine)

$WorkflowStatus = if ($Warnings.Count -gt 0) { "partial" } else { "passed" }
$WorkflowMessage = if ($WorkflowStatus -eq "partial") {
    "Refresh completed with warnings; validation passed and retained cache was identified explicitly."
} else {
    "Refresh completed successfully."
}
Write-RunState -Status $WorkflowStatus -Message $WorkflowMessage
try {
    Invoke-Step "Record collection observations" {
        Invoke-Native -FilePath $Python -Arguments @("-m", "app.data_status")
    }
} catch {
    Write-RunState -Status "failed" -Message "Refresh data was processed, but local observation history could not be saved."
    throw
}
Write-Host "workflow_status=$WorkflowStatus"
Write-Host "collection_status=$CollectionStatus"
Write-Host "collector_bundle_status=$CollectorBundleStatus"

Invoke-Step "Export concise run report" {
    Invoke-Native -FilePath $Python -Arguments @(".\scripts\reporting\status_report.py")
}

Write-Host "Wrote $StatusPath"
Write-Host "Wrote $StatusJsonPath"
Release-WorkflowLock
