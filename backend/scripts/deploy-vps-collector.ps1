[CmdletBinding()]
param(
    [ValidateSet("Deploy", "Rollback")]
    [string]$Action = "Deploy",
    [string]$ReleaseId = "",
    [switch]$Plan,
    [string]$SshHost = "ledger_vps",
    [string]$RemoteBase = "/home/ledger/apps/expense-collector",
    [ValidateRange(1, 20)]
    [int]$Retain = 5
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".app-runtime\Scripts\python.exe"
$Manager = Join-Path $Root "scripts\collectors\vps_release_manager.py"
$ReleasePattern = '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}$'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "The project-owned Python runtime is unavailable."
}
if (-not (Test-Path -LiteralPath $Manager -PathType Leaf)) {
    throw "The VPS release manager is unavailable."
}
if ($RemoteBase -ne "/home/ledger/apps/expense-collector") {
    throw "The remote collector base path is not allowlisted."
}
if ($SshHost -notmatch '^[A-Za-z0-9_.-]+$') {
    throw "The SSH host alias is invalid."
}

$Commit = ""
if ($Action -eq "Deploy") {
    $GitOutput = @(& git.exe -C $Root rev-parse HEAD 2>&1)
    $GitExitCode = $LASTEXITCODE
    $Commit = [string](@(
        $GitOutput | ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ -match '^[0-9a-f]{40}$' }
    ) | Select-Object -First 1)
    if ($GitExitCode -ne 0 -or $Commit -notmatch '^[0-9a-f]{40}$') {
        throw "The source Git commit could not be determined."
    }
    if (-not $ReleaseId) {
        $Timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
        $ReleaseId = "$Timestamp-$($Commit.Substring(0, 12))"
    }
}
if ($ReleaseId -notmatch $ReleasePattern) {
    throw "ReleaseId must use YYYYMMDDTHHMMSSZ followed by a Git hash."
}

if ($Plan) {
    if ($Action -eq "Rollback") {
        [ordered]@{
            contract = "expense-collector-release-plan/v1"
            action = "rollback-plan"
            release_id = $ReleaseId
            remote_writes = $false
        } | ConvertTo-Json -Depth 4
        exit 0
    }
    & $Python $Manager plan `
        --source-root $Root `
        --release-id $ReleaseId `
        --source-commit $Commit
    exit $LASTEXITCODE
}

if ($Action -eq "Deploy") {
    $Dirty = @(& git.exe -C $Root status --porcelain --untracked-files=all 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The source Git worktree status could not be determined."
    }
    if ($Dirty.Count -gt 0) {
        throw "Refusing to deploy an uncommitted working tree. Commit and validate the release first."
    }
}

if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw "OpenSSH ssh.exe is unavailable."
}
if (-not (Get-Command scp.exe -ErrorAction SilentlyContinue)) {
    throw "OpenSSH scp.exe is unavailable."
}

$RemoteHelper = "/tmp/expense-vps-release-manager-$ReleaseId.py"
$RemoteBundle = "/tmp/expense-vps-release-$ReleaseId.zip"
$TemporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$TemporaryRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $TemporaryBase "expense-vps-release-$ReleaseId")
)
if (-not $TemporaryRoot.StartsWith(
    $TemporaryBase,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "The temporary release path escaped the system temp directory."
}
$Bundle = Join-Path $TemporaryRoot "release.zip"
New-Item -ItemType Directory -Path $TemporaryRoot -Force | Out-Null

try {
    if ($Action -eq "Deploy") {
        & $Python $Manager build `
            --source-root $Root `
            --output $Bundle `
            --release-id $ReleaseId `
            --source-commit $Commit `
            --verify-git
        if ($LASTEXITCODE -ne 0) {
            throw "Could not build a Git-verified VPS collector release bundle."
        }
    }

    & scp.exe -q -o BatchMode=yes -o ConnectTimeout=8 `
        $Manager "${SshHost}:$RemoteHelper"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not upload the VPS release manager."
    }

    if ($Action -eq "Deploy") {
        & scp.exe -q -o BatchMode=yes -o ConnectTimeout=8 `
            $Bundle "${SshHost}:$RemoteBundle"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not upload the VPS collector release bundle."
        }
        & ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 $SshHost `
            /usr/bin/python3 $RemoteHelper apply `
            --base-dir $RemoteBase `
            --bundle $RemoteBundle `
            --python /usr/bin/python3 `
            --node "$RemoteBase/runtime/node/bin/node" `
            --retain $Retain
    } else {
        & ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 $SshHost `
            /usr/bin/python3 $RemoteHelper rollback `
            --base-dir $RemoteBase `
            --release-id $ReleaseId `
            --python /usr/bin/python3
    }
    if ($LASTEXITCODE -ne 0) {
        throw "The VPS release operation failed."
    }
} finally {
    & ssh.exe -T -o BatchMode=yes -o ConnectTimeout=8 $SshHost `
        /bin/rm -f -- $RemoteHelper $RemoteBundle 2>$null | Out-Null
    Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}
