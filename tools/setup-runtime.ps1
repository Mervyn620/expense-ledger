[CmdletBinding()]
param(
    # A relocatable CPython folder (python.exe next to Lib\, DLLs\). Defaults to the base of the old venv.
    [string]$PythonSource,
    # node.exe to copy. Collectors only use Node built-in modules, so the single executable is enough.
    [string]$NodeSource,
    # Install packages from PyPI instead of copying them from the old venv.
    [switch]$Online,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $projectRoot 'backend'
$runtimeRoot = Join-Path $projectRoot 'runtime'
$pythonTarget = Join-Path $runtimeRoot 'python'
$nodeTarget = Join-Path $runtimeRoot 'node'
$legacyVenv = Join-Path $backendRoot '.app-runtime'
$requirements = Join-Path $backendRoot 'requirements-app.txt'

if ((Test-Path -LiteralPath $runtimeRoot) -and -not $Force) {
    throw "runtime\ already exists. Pass -Force to rebuild it (the old venv in backend\.app-runtime is never touched)."
}

function Read-VenvValue([string]$name) {
    $config = Join-Path $legacyVenv 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $config)) { return $null }
    foreach ($line in Get-Content -LiteralPath $config) {
        if ($line -match "^\s*$name\s*=\s*(.+?)\s*$") { return $Matches[1] }
    }
    return $null
}

if (-not $PythonSource) { $PythonSource = Read-VenvValue 'home' }
if (-not $PythonSource) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $PythonSource = (& $command.Source -c 'import sys; print(sys.base_prefix)').Trim() }
}
if (-not $PythonSource -or -not (Test-Path -LiteralPath (Join-Path $PythonSource 'python.exe'))) {
    throw 'No Python 3.11+ installation found. Pass -PythonSource <folder containing python.exe>.'
}
if (-not $NodeSource) {
    $command = Get-Command node -ErrorAction SilentlyContinue
    if ($command) { $NodeSource = $command.Source }
}
if (-not $NodeSource -or -not (Test-Path -LiteralPath $NodeSource)) {
    throw 'node.exe not found. Pass -NodeSource <path to node.exe>.'
}

# Build into a staging folder so a failed run never leaves a half-made runtime\ behind.
$staging = Join-Path $projectRoot ('runtime.staging-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    $stagePython = Join-Path $staging 'python'
    $stageNode = Join-Path $staging 'node'
    New-Item -ItemType Directory -Force -Path $stagePython, $stageNode | Out-Null
    Write-Host "Copying Python from $PythonSource"
    & robocopy $PythonSource $stagePython /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying Python failed (robocopy $LASTEXITCODE)." }
    Copy-Item -LiteralPath $NodeSource -Destination (Join-Path $stageNode 'node.exe')

    $python = Join-Path $stagePython 'python.exe'
    $env:PYTHONNOUSERSITE = '1'
    $version = (& $python -c 'import platform; print(platform.python_version())').Trim()
    if ([version]$version -lt [version]'3.11.0') { throw "Python $version is too old; 3.11 or later is required." }
    $venvVersion = Read-VenvValue 'version'
    $venvPackages = Join-Path $legacyVenv 'Lib\site-packages'
    $sitePackages = Join-Path $stagePython 'Lib\site-packages'
    if (-not $Online -and $venvVersion -eq $version -and (Test-Path -LiteralPath $venvPackages)) {
        # Same interpreter build: reuse the packages already verified with the desktop app (no download).
        Write-Host "Copying packages from the existing venv (Python $version)"
        & robocopy $venvPackages $sitePackages /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying packages failed (robocopy $LASTEXITCODE)." }
    } else {
        Write-Host 'Installing packages from requirements-app.txt'
        & $python -m pip install -r $requirements --disable-pip-version-check --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
    }
    & $python -c 'import fastapi, uvicorn, openpyxl; from fastapi.testclient import TestClient'
    if ($LASTEXITCODE -ne 0) { throw 'Python runtime verification failed.' }
    $nodeVersion = (& (Join-Path $stageNode 'node.exe') --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v\d+') { throw 'Node runtime verification failed.' }

    [ordered]@{
        python = $version; node = $nodeVersion
        python_source = $PythonSource; node_source = $NodeSource
        created_at = (Get-Date).ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $staging 'manifest.json') -Encoding utf8

    if (Test-Path -LiteralPath $runtimeRoot) {
        $previous = $runtimeRoot + '.previous-' + (Get-Date -Format 'yyyyMMddHHmmss')
        Move-Item -LiteralPath $runtimeRoot -Destination $previous
        Write-Host "Previous runtime kept at $previous"
    }
    Move-Item -LiteralPath $staging -Destination $runtimeRoot
    Write-Host "Embedded runtime ready: Python $version, Node $nodeVersion in runtime\"
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
