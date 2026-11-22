param([Parameter(Mandatory=$true)][string]$Name)
# This probe only acquires/releases a named mutex; it never invokes any workflow.
$probe = New-Object Threading.Mutex($false, $Name)
$acquired = $false
try {
    try { $acquired=$probe.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired=$true }
    if ($acquired) { exit 0 }
    exit 23
} finally {
    if ($acquired) { $probe.ReleaseMutex() }
    $probe.Dispose()
}
