#requires -Version 5.1
<#
.SYNOPSIS
    Compare local MetaTrader 5 terminal build with latest PyPI MetaTrader5 package.

.DESCRIPTION
    1. Detects terminal build from terminal64.exe (PE version info).
    2. Queries PyPI for latest MetaTrader5 version.
    3. Compares build numbers and optionally pip-installs + runs a quick init test.
#>
param(
    [switch]$TestInit
)

$ErrorActionPreference = 'Stop'

# ── 1. Locate terminal64.exe ────────────────────────────────────────
$projectRoot = Split-Path $PSScriptRoot -Parent
$candidates = @(
    'C:\Program Files\MetaTrader 5\terminal64.exe',
    'C:\Program Files (x86)\MetaTrader 5\terminal64.exe',
    "$env:LOCALAPPDATA\MetaQuotes\Terminal\*\terminal64.exe",
    "$projectRoot\mt5*\terminal64.exe",
    "$projectRoot\MT5*\terminal64.exe"
)

$exe = $null
foreach ($c in $candidates) {
    if ($c -match '\*') {
        $exe = (Get-ChildItem $c -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
    } elseif (Test-Path $c) {
        $exe = $c
    }
    if ($exe) { break }
}

if (-not $exe) {
    Write-Error "terminal64.exe not found. Searched:`n$($candidates -join "`n")"
}

Write-Host "Found: $exe" -ForegroundColor Cyan

# ── 2. Read PE build number ─────────────────────────────────────────
# FileVersion usually looks like 5.0.0.5430 (last segment = build)
$vi = (Get-Item $exe).VersionInfo
$rawVer = $vi.FileVersion.Trim()
$termBuild = ''
if ($rawVer -match '(\d+)$') {
    $termBuild = $Matches[1]
}

Write-Host "Terminal file version : $rawVer" -ForegroundColor Cyan
Write-Host "Terminal build      : $termBuild" -ForegroundColor $(if ($termBuild) { 'Green' } else { 'Red' })

# ── 3. Query PyPI ───────────────────────────────────────────────────
Write-Host "`nQuerying PyPI for MetaTrader5..." -ForegroundColor Cyan
$resp = Invoke-RestMethod -Uri 'https://pypi.org/pypi/MetaTrader5/json' -Method GET
$pypiVer = $resp.info.version
$pypiBuild = ''
if ($pypiVer -match '(\d+)$') {
    $pypiBuild = $Matches[1]
}

Write-Host "PyPI version        : $pypiVer" -ForegroundColor Cyan
Write-Host "PyPI build          : $pypiBuild" -ForegroundColor $(if ($pypiBuild) { 'Green' } else { 'Red' })

# ── 4. Compare ────────────────────────────────────────────────────────
Write-Host "`n=== Comparison ===" -ForegroundColor White
if (-not $termBuild -or -not $pypiBuild) {
    Write-Host "Could not extract one or both build numbers." -ForegroundColor Red
} elseif ($termBuild -eq $pypiBuild) {
    Write-Host "MATCH: terminal=$termBuild  package=$pypiBuild" -ForegroundColor Green
} else {
    Write-Host "MISMATCH: terminal=$termBuild  package=$pypiBuild" -ForegroundColor Red
}

# ── 5. Optional: pip install + quick init test ───────────────────────
if ($TestInit) {
    Write-Host "`n=== Pip install + init test ===" -ForegroundColor White

    # Prefer pip from a venv or system python
    $pip = Get-Command 'pip' -ErrorAction SilentlyContinue
    if (-not $pip) {
        $pip = Get-Command 'pip3' -ErrorAction SilentlyContinue
    }
    if (-not $pip) {
        Write-Error "pip/pip3 not found in PATH."
    }

    Write-Host "Installing MetaTrader5==$pypiVer ..." -ForegroundColor Yellow
    & $pip.Source install "MetaTrader5==$pypiVer" --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed."
    }

    Write-Host "Running mt5.initialize() probe ..." -ForegroundColor Yellow
    $py = (Get-Command 'python' -ErrorAction SilentlyContinue)
    if (-not $py) { $py = Get-Command 'python3' -ErrorAction SilentlyContinue }
    if (-not $py) { Write-Error "python/python3 not found." }

    $probe = @'
import MetaTrader5 as mt5, sys
ok = mt5.initialize(path=r'__PATH__', timeout=30000)
print('INIT_OK=' + str(ok))
err = mt5.last_error()
print('LAST_ERROR=' + str(err))
mt5.shutdown()
sys.exit(0 if ok else 1)
'@
    $probe = $probe.Replace('__PATH__', $exe.Replace('\', '\\'))

    $tempPy = "$env:TEMP\mt5_probe_$(Get-Random).py"
    Set-Content -Path $tempPy -Value $probe -Encoding UTF8
    try {
        $out = & $py.Source $tempPy 2>&1
        $out | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "mt5.initialize() FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
        } else {
            Write-Host "mt5.initialize() succeeded." -ForegroundColor Green
        }
    } finally {
        Remove-Item $tempPy -Force -ErrorAction SilentlyContinue
    }
}
