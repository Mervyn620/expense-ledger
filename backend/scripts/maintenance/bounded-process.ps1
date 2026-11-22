function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label,
        [ValidateRange(1, 300)][int]$TimeoutSeconds = 60
    )
    # These internal preparation commands use simple, whitespace-free arguments.
    # Keep executable paths separate so Python installations with spaces work.
    foreach ($Argument in $Arguments) {
        if ($Argument -notmatch '^[a-zA-Z0-9_./\\:-]+$') {
            throw "Unsupported preparation argument."
        }
    }
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    $Info.FileName = $FilePath
    $Info.Arguments = $Arguments -join ' '
    $Info.WorkingDirectory = (Get-Location).Path
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $Info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = $Info
    try {
        [void]$Process.Start()
        $Stdout = $Process.StandardOutput.ReadToEndAsync()
        $Stderr = $Process.StandardError.ReadToEndAsync()
        if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
            # Kill only this preparation process and its children, including the
            # Windows virtualenv launcher. Bound cleanup as well as execution.
            $KillInfo = New-Object System.Diagnostics.ProcessStartInfo
            $KillInfo.FileName = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            $KillInfo.Arguments = "/PID $($Process.Id) /T /F"
            $KillInfo.UseShellExecute = $false
            $KillInfo.CreateNoWindow = $true
            $Killer = [System.Diagnostics.Process]::Start($KillInfo)
            try {
                if (-not $Killer.WaitForExit(5000)) { $Killer.Kill() }
            } finally { $Killer.Dispose() }
            if (-not $Process.HasExited) { $Process.Kill() }
            [void]$Process.WaitForExit(5000)
            throw "$Label exceeded the $TimeoutSeconds-second preparation timeout. No collection was started."
        }
        if (-not $Stdout.Wait(5000) -or -not $Stderr.Wait(5000)) {
            throw "$Label output did not close after the preparation process exited."
        }
        if ($Process.ExitCode -ne 0) {
            throw "$Label failed (exit $($Process.ExitCode)): $($Stderr.Result.Trim())"
        }
        return $Stdout.Result.Trim()
    } finally { $Process.Dispose() }
}
