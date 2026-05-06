# package-mt5-portable.ps1
# Initializes MT5 5640 locally in portable mode, zips the result,
# and uploads to GitHub releases as mt5-portable-5640.
#
# Prerequisites: Run from g:\adaptive-trading-bot

param(
    [string]$GhToken = $env:GITHUB_TOKEN,
    [string]$SourceDir = "g:\adaptive-trading-bot\mt5 5640",
    [string]$PortableDir = "g:\adaptive-trading-bot\_mt5-portable-init",
    [string]$ZipPath = "g:\adaptive-trading-bot\_mt5-portable-5640.zip",
    [string]$RepoOwner = "loriloha",
    [string]$RepoName = "adaptive-trading-bot",
    [string]$ReleaseTag = "mt5-portable-5640",
    [int]$MaxWaitSeconds = 300
)

if (-not $GhToken) {
    $GhToken = Read-Host "GitHub Personal Access Token (needs repo + write:packages)"
}

Write-Host "=== Step 1: Copy exes to portable dir ===" -ForegroundColor Cyan
if (Test-Path $PortableDir) { Remove-Item -Recurse -Force $PortableDir }
New-Item -ItemType Directory -Path $PortableDir | Out-Null
Copy-Item "$SourceDir\terminal64.exe"   $PortableDir
Copy-Item "$SourceDir\MetaEditor64.exe" $PortableDir
Copy-Item "$SourceDir\metatester64.exe" $PortableDir
Write-Host "Copied 3 exes to $PortableDir"

Write-Host "=== Step 2: Launch terminal64.exe /portable ===" -ForegroundColor Cyan
Write-Host "The MT5 terminal will open. Let it run until you see it has loaded."
Write-Host "This script will auto-close it once MQL5\Experts appears (~60-120s)."
$proc = Start-Process "$PortableDir\terminal64.exe" "/portable" -PassThru
Write-Host "Terminal PID: $($proc.Id)"

Write-Host "=== Step 3: Wait for MQL5\Experts (max ${MaxWaitSeconds}s) ===" -ForegroundColor Cyan
$waited = 0
$ready = $false
while ($waited -lt $MaxWaitSeconds) {
    Start-Sleep -Seconds 5
    $waited += 5
    $expertDir = "$PortableDir\MQL5\Experts"
    $tiniSize  = if (Test-Path "$PortableDir\terminal.ini") {
        (Get-Item "$PortableDir\terminal.ini").Length } else { 0 }
    $files = (Get-ChildItem $PortableDir -Recurse -File).Count
    Write-Host "  ${waited}s: files=$files  tini=${tiniSize}B  MQL5\Experts=$(Test-Path $expertDir)"

    if ((Test-Path $expertDir) -and $tiniSize -gt 500) {
        Write-Host "MQL5\Experts found and terminal.ini written — initialization complete!" -ForegroundColor Green
        $ready = $true
        break
    }
}

if (-not $ready) {
    Write-Host "WARNING: MQL5\Experts not found after ${MaxWaitSeconds}s — zipping whatever is there." -ForegroundColor Yellow
}

Write-Host "=== Step 4: Stopping terminal ===" -ForegroundColor Cyan
# Give it 10 more seconds to flush writes
Start-Sleep -Seconds 10
try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
# Kill any remaining MT5 processes
Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Write-Host "=== Step 5: Contents summary ===" -ForegroundColor Cyan
$allFiles = Get-ChildItem $PortableDir -Recurse -File
Write-Host "  Total files: $($allFiles.Count)"
$allFiles | Group-Object { Split-Path $_.DirectoryName -Leaf } |
    Sort-Object Count -Descending | Select-Object -First 10 |
    ForEach-Object { Write-Host "  $($_.Name): $($_.Count) files" }

Write-Host "=== Step 6: Creating ZIP ===" -ForegroundColor Cyan
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path "$PortableDir\*" -DestinationPath $ZipPath
$zipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "Created $ZipPath ($zipSize MB)"

Write-Host "=== Step 7: Upload to GitHub Releases ===" -ForegroundColor Cyan
$headers = @{
    "Authorization" = "token $GhToken"
    "Accept"        = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$apiBase = "https://api.github.com/repos/$RepoOwner/$RepoName"

# Delete existing release if any
try {
    $existing = Invoke-RestMethod "$apiBase/releases/tags/$ReleaseTag" -Headers $headers -ErrorAction Stop
    Write-Host "Deleting existing release $ReleaseTag..."
    Invoke-RestMethod "$apiBase/releases/$($existing.id)" -Method Delete -Headers $headers | Out-Null
} catch {}

# Delete existing tag if any
try {
    Invoke-RestMethod "$apiBase/git/refs/tags/$ReleaseTag" -Method Delete -Headers $headers -ErrorAction Stop | Out-Null
    Write-Host "Deleted old tag $ReleaseTag"
} catch {}

# Create release
$body = @{ tag_name = $ReleaseTag; name = "MT5 Portable 5640"; body = "Fully initialized MT5 5640 portable installation with MQL5/ data directory. Matches MetaTrader5==5.0.5640 on PyPI."; draft = $false; prerelease = $false } | ConvertTo-Json
$release = Invoke-RestMethod "$apiBase/releases" -Method Post -Headers $headers -Body $body -ContentType "application/json"
Write-Host "Created release: $($release.html_url)"

# Upload ZIP
$uploadUrl = $release.upload_url -replace '\{.*\}', ''
$uploadUrl = "${uploadUrl}?name=mt5-portable.zip&label=mt5-portable.zip"
Write-Host "Uploading $ZipPath ($zipSize MB)..."
$zipBytes = [System.IO.File]::ReadAllBytes($ZipPath)
$uploadResp = Invoke-RestMethod $uploadUrl -Method Post -Headers ($headers + @{ "Content-Type" = "application/zip" }) -Body $zipBytes
Write-Host "Uploaded: $($uploadResp.browser_download_url)" -ForegroundColor Green

Write-Host ""
Write-Host "=== DONE ===" -ForegroundColor Green
Write-Host "Release URL: $($release.html_url)"
Write-Host "ZIP download: $($uploadResp.browser_download_url)"
Write-Host ""
Write-Host "Use this URL in the workflow:" -ForegroundColor Yellow
Write-Host "https://github.com/$RepoOwner/$RepoName/releases/download/$ReleaseTag/mt5-portable.zip"
