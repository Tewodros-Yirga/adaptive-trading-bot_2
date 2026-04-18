<#
.SYNOPSIS
  Fetches MT5 bridge diagnostic JSON and screenshot from a deployed Space (or any base URL).

.PARAMETER BaseUrl
  Root URL without trailing slash, e.g. https://your-space.hf.space

.PARAMETER HfReadToken
  Hugging Face read token (Bearer). Prefer env HF_TOKEN instead of passing on the command line.

.PARAMETER BridgeSecret
  Value for X-Bridge-Secret. Prefer env MT_BRIDGE_SECRET.

.PARAMETER OutDir
  Directory to write artifacts (created if missing).
#>
param(
    [Parameter(Mandatory = $true)]
    [string] $BaseUrl,

    [string] $HfReadToken = $env:HF_TOKEN,

    [string] $BridgeSecret = $env:MT_BRIDGE_SECRET,

    [string] $OutDir = (Join-Path $PWD "mt5-diagnostics-export")
)

$ErrorActionPreference = "Stop"
if (-not $HfReadToken) { throw "Set HF_TOKEN or pass -HfReadToken" }
if (-not $BridgeSecret) { throw "Set MT_BRIDGE_SECRET or pass -BridgeSecret" }

$BaseUrl = $BaseUrl.TrimEnd("/")
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$headers = @{
    "Authorization" = "Bearer $HfReadToken"
    "X-Bridge-Secret" = $BridgeSecret
}

function Save-Json($name, $obj) {
    $path = Join-Path $OutDir "$name.json"
    $obj | ConvertTo-Json -Depth 20 | Set-Content -Path $path -Encoding UTF8
    Write-Host "Wrote $path"
}

Write-Host "GET $BaseUrl/debug/mt5 ..."
$mt5 = Invoke-RestMethod -Uri "$BaseUrl/debug/mt5" -Headers $headers -TimeoutSec 120
Save-Json "debug-mt5" $mt5

Write-Host "GET $BaseUrl/debug/processes ..."
$proc = Invoke-RestMethod -Uri "$BaseUrl/debug/processes" -Headers $headers -TimeoutSec 30
Save-Json "debug-processes" $proc

Write-Host "GET $BaseUrl/debug/pipes ..."
$pipes = Invoke-RestMethod -Uri "$BaseUrl/debug/pipes" -Headers $headers -TimeoutSec 30
Save-Json "debug-pipes" $pipes

Write-Host "GET $BaseUrl/debug/mt5-ipc-test (may take up to ~2 min) ..."
$ipc = Invoke-RestMethod -Uri "$BaseUrl/debug/mt5-ipc-test" -Headers $headers -TimeoutSec 150
Save-Json "debug-mt5-ipc-test" $ipc

Write-Host "GET $BaseUrl/debug/screenshot ..."
$shot = Invoke-RestMethod -Uri "$BaseUrl/debug/screenshot" -Headers $headers -TimeoutSec 30
if ($shot.image_b64) {
    $png = Join-Path $OutDir "screenshot.png"
    [IO.File]::WriteAllBytes($png, [Convert]::FromBase64String($shot.image_b64))
    Write-Host "Wrote $png (tool=$($shot.tool))"
} else {
    Write-Warning "Screenshot missing or error: $($shot | ConvertTo-Json -Compress)"
}

Write-Host "Done. Summary from debug/mt5:"
Write-Host "  ipc_ready=$($mt5.bootstrap.ipc_ready) ipc_failed=$($mt5.bootstrap.ipc_failed)"
Write-Host "  mt5_ipc_probe_log_exists=$($mt5.bootstrap.mt5_ipc_probe_log_exists)"
Write-Host "  MT5_LAUNCH_TERMINAL=$($mt5.runtime_env.mt5_launch_terminal) MT5_CONTEXT_MODE=$($mt5.runtime_env.mt5_context_mode)"
