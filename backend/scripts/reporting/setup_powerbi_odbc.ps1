param(
    [string]$DsnName = "expenses_sqlite",
    [ValidateSet("System", "User")]
    [string]$DsnType = "System",
    [string]$Platform = "64-bit",
    [string]$DriverName = "SQLite3 ODBC Driver",
    [string]$DatabasePath = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
if (-not $DatabasePath) {
    $DatabasePath = Join-Path $Root "data\expenses.sqlite"
}
$DatabasePath = (Resolve-Path -LiteralPath $DatabasePath).Path

$driver = Get-OdbcDriver -Name $DriverName -Platform $Platform -ErrorAction SilentlyContinue
if (-not $driver) {
    throw "ODBC driver '$DriverName' ($Platform) is not installed. Install the 64-bit SQLite ODBC driver first."
}

$existing = Get-OdbcDsn -Name $DsnName -DsnType $DsnType -Platform $Platform -ErrorAction SilentlyContinue
if ($existing) {
    Remove-OdbcDsn -Name $DsnName -DsnType $DsnType -Platform $Platform
}

Add-OdbcDsn `
    -Name $DsnName `
    -DriverName $DriverName `
    -DsnType $DsnType `
    -Platform $Platform `
    -SetPropertyValue @("Database=$DatabasePath", "Timeout=100000")

Add-Type -AssemblyName System.Data
$connection = [System.Data.Odbc.OdbcConnection]::new("DSN=$DsnName")
$connection.Open()
try {
    $command = $connection.CreateCommand()
    $command.CommandText = "SELECT COUNT(*) AS orders, ROUND(SUM(total), 2) AS total, MAX(date) AS latest_date FROM orders"
    $reader = $command.ExecuteReader()
    if ($reader.Read()) {
        Write-Host ("Configured DSN={0} orders={1} total={2} latest_date={3}" -f `
            $DsnName, $reader["orders"], $reader["total"], $reader["latest_date"])
    }
    $reader.Close()
}
finally {
    $connection.Close()
}
