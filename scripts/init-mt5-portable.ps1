param(
    [string]$TerminalExe = "C:\Program Files\MetaTrader 5\terminal64.exe",
    [string]$PortableDir = "C:\Program Files\MetaTrader 5",
    [int]$WarmupSeconds = 120,
    [int]$PostRestartStableSeconds = 45,
    [switch]$NoPrompt
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $TerminalExe)) {
    throw "terminal64.exe not found at '$TerminalExe'"
}
if (-not (Test-Path $PortableDir)) {
    throw "PortableDir not found at '$PortableDir'"
}

Write-Host "Starting MT5 in portable mode..."
Write-Host "Terminal: $TerminalExe"
Write-Host "PortableDir: $PortableDir"

if (-not $NoPrompt) {
    Write-Host ""
    Write-Host "Operator guidance:"
    Write-Host " - Do NOT complete 'Select company / open account' wizard."
    Write-Host " - If login dialog appears, use broker login/server there."
    Write-Host " - Let terminal settle; script waits for optional self-restart."
    Write-Host ""
}

$proc = Start-Process -FilePath $TerminalExe -ArgumentList "/portable" -PassThru
$initialPid = $proc.Id
Write-Host "Initial PID: $initialPid"

$elapsed = 0
$restartDetected = $false
$activePid = $initialPid
$stableAfterRestart = 0

while ($elapsed -lt $WarmupSeconds) {
    Start-Sleep -Seconds 5
    $elapsed += 5

    $active = Get-Process -Name terminal64 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $active) {
        Write-Host ("{0}s: no terminal64 process visible" -f $elapsed)
        continue
    }

    if (-not $restartDetected -and $active.Id -ne $initialPid) {
        $restartDetected = $true
        $activePid = $active.Id
        Write-Host ("{0}s: self-restart detected (new PID: {1})" -f $elapsed, $activePid)
        continue
    }

    if ($restartDetected) {
        $stableAfterRestart += 5
        Write-Host ("{0}s: restarted PID {1} alive (stable {2}s)" -f $elapsed, $active.Id, $stableAfterRestart)
        if ($stableAfterRestart -ge $PostRestartStableSeconds) {
            Write-Host "Post-restart stability window reached."
            break
        }
    } else {
        Write-Host ("{0}s: initial PID {1} still alive" -f $elapsed, $active.Id)
    }
}

Write-Host ""
Write-Host "Stopping terminal processes..."
Get-Process -Name terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$terminalIni = Join-Path $PortableDir "terminal.ini"
$mqlDir = Join-Path $PortableDir "MQL5"
$mqlFiles = 0
if (Test-Path $mqlDir) {
    $mqlFiles = (Get-ChildItem -Path $mqlDir -File -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
}
$tiniSize = 0
if (Test-Path $terminalIni) {
    $tiniSize = (Get-Item $terminalIni).Length
}

Write-Host "Warmup summary:"
Write-Host (" - restartDetected={0}" -f $restartDetected)
Write-Host (" - terminal.ini.size={0}" -f $tiniSize)
Write-Host (" - mql5.fileCount={0}" -f $mqlFiles)

if ($mqlFiles -lt 100) {
    Write-Warning "MQL5 file count is low. Consider another warm run before packaging."
}
