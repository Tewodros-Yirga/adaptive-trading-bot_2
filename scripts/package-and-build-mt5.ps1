#requires -Version 5.1
<#
.SYNOPSIS
    Package local MT5 build 5430 and upload to GitHub release, then trigger base image build.

.DESCRIPTION
    1. Verifies a complete MT5 build 5430 installation exists (600+ files)
    2. Creates mt5_v5430.zip
    3. Uploads to GitHub release 'mt5-portable-5430'
    4. Triggers 'Build & Push MT5 Bridge Base Image' workflow dispatch

.PARAMETER Token
    GitHub Personal Access Token with repo and workflow scopes.

.EXAMPLE
    .\package-and-build-mt5.ps1 -Token ghp_xxxxxxxxxxxx
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Token
)

$ErrorActionPreference = 'Stop'

# ── Config ────────────────────────────────────────────────────────────
$Owner      = 'loriloha'
$Repo       = 'adaptive-trading-bot'
$ReleaseTag = 'mt5-portable-5430'
$AssetName  = 'mt5_v5430.zip'
$MinFiles   = 600

# ── Step 1: Locate complete MT5 installation ───────────────────────────
$mt5Paths = @(
    'C:\Program Files\MetaTrader 5'
    'C:\Program Files (x86)\MetaTrader 5'
    "$env:LOCALAPPDATA\MetaQuotes\Terminal"
)

$mt5Path = $null
foreach ($p in $mt5Paths) {
    if (Test-Path "$p\terminal64.exe") {
        $fileCount = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue).Count
        Write-Host "Found MT5 at: $p ($fileCount files)" -ForegroundColor Cyan
        if ($fileCount -ge $MinFiles) {
            $mt5Path = $p
            Write-Host "  -> Using this path (>= $MinFiles files)" -ForegroundColor Green
            break
        } else {
            Write-Host "  -> Skipping: only $fileCount files (need >= $MinFiles)" -ForegroundColor Yellow
        }
    }
}

if (-not $mt5Path) {
    Write-Error "No complete MT5 installation found with >= $MinFiles files.`n`nSearched paths:`n$($mt5Paths -join "`n")`n`nTo fix:`n  1. Install MT5 build 5430 from your saved installer`n  2. Run it once (let it download MQL5 data and complete first-run wizard)`n  3. Re-run this script"
}

# ── Step 1b: Disable auto-update so build 5430 stays pinned ──────────
Write-Host "`nDisabling auto-update in MT5 config..." -ForegroundColor Cyan

# Patch terminal.ini
$tini = "$mt5Path\terminal.ini"
if (Test-Path $tini) {
    $lines = Get-Content $tini -Raw
    if ($lines -match '\[LiveUpdate\]') {
        $lines = [regex]::Replace($lines, '(?m)^\[LiveUpdate\].*?(?=\r?\n\[|\z)', "[LiveUpdate]`r`nEnabled=0`r`nNextUpdate=9999999999", [System.Text.RegularExpressions.RegexOptions]::Singleline)
    } else {
        $lines += "`r`n[LiveUpdate]`r`nEnabled=0`r`nNextUpdate=9999999999`r`n"
    }
    # Also ensure Experts section
    if (-not ($lines -match '\[Experts\]')) {
        $lines += "[Experts]`r`nEnabled=1`r`nAllowLiveTrading=1`r`n"
    }
    Set-Content -Path $tini -Value $lines -NoNewline -Encoding UTF8
    Write-Host "  terminal.ini patched (LiveUpdate=0)" -ForegroundColor Green
} else {
    $tiniContent = @"
[Startup]
AutoStart=0

[Experts]
Enabled=1
AllowLiveTrading=1

[LiveUpdate]
Enabled=0
NextUpdate=9999999999
"@
    Set-Content -Path $tini -Value $tiniContent -Encoding UTF8
    Write-Host "  terminal.ini created (LiveUpdate=0)" -ForegroundColor Green
}

# Patch common.ini
$ciniDir = "$mt5Path\config"
$cini = "$ciniDir\common.ini"
if (-not (Test-Path $ciniDir)) { New-Item -ItemType Directory -Path $ciniDir -Force | Out-Null }
$ciniContent = @"
[Common]
NewsEnable=0
AutoSync=0
AutoUpdate=0
"@
Set-Content -Path $cini -Value $ciniContent -Encoding UTF8
Write-Host "  config/common.ini patched (AutoUpdate=0)" -ForegroundColor Green

# ── Step 2: Create ZIP ────────────────────────────────────────────────
$zipPath = "$env:TEMP\$AssetName"
Write-Host "`nCreating ZIP: $zipPath" -ForegroundColor Cyan

# Prefer 7z (what the Linux Dockerfile uses), fallback to Compress-Archive
$use7z = $false
$7z = Get-Command '7z' -ErrorAction SilentlyContinue
if ($7z) {
    $use7z = $true
    & $7z.Source "a" "-tzip" "-mx=5" "-y" $zipPath "$mt5Path\*" | Out-Null
} else {
    # Use Expand-Archive workaround: zip from parent dir to avoid root-folder issue
    $parent = Split-Path $mt5Path -Parent
    $baseName = Split-Path $mt5Path -Leaf
    Push-Location $parent
    try {
        Compress-Archive -Path "$baseName\*" -DestinationPath $zipPath -Force
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $zipPath)) {
    Write-Error "ZIP creation failed"
}
$zipSize = (Get-Item $zipPath).Length / 1MB
Write-Host "ZIP created: $([math]::Round($zipSize,2)) MB" -ForegroundColor Green

# ── Step 3: Upload to GitHub Release ──────────────────────────────────
Write-Host "`nUploading to GitHub release: $ReleaseTag" -ForegroundColor Cyan

$headers = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}

# Get or create release
$releaseUrl = "https://api.github.com/repos/$Owner/$Repo/releases/tags/$ReleaseTag"
try {
    $release = Invoke-RestMethod -Uri $releaseUrl -Headers $headers -Method GET
    Write-Host "Release exists: $($release.html_url)" -ForegroundColor Green
} catch {
    if ($_.Exception.Response.StatusCode -eq 404) {
        Write-Host "Release not found — creating..." -ForegroundColor Yellow
        $body = @{
            tag_name = $ReleaseTag
            name     = "MT5 Portable Build 5430"
            body     = "MetaTrader 5 portable archive (build 5430).`n`nUploaded via package-and-build-mt5.ps1"
        } | ConvertTo-Json
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Owner/$Repo/releases" -Headers $headers -Method POST -Body $body
        Write-Host "Release created: $($release.html_url)" -ForegroundColor Green
    } else {
        throw
    }
}

# Delete existing asset with same name (if any)
$existing = $release.assets | Where-Object { $_.name -eq $AssetName }
if ($existing) {
    Write-Host "Deleting existing asset: $AssetName" -ForegroundColor Yellow
    Invoke-RestMethod -Uri $existing.url -Headers $headers -Method DELETE | Out-Null
}

# Upload asset
$uploadUrl = $release.upload_url -replace '\{.*\}', "?name=$AssetName"
$mime = 'application/zip'
Invoke-RestMethod -Uri $uploadUrl -Headers $headers -Method POST -ContentType $mime -InFile $zipPath | Out-Null
$assetUrl = "https://github.com/$Owner/$Repo/releases/download/$ReleaseTag/$AssetName"
Write-Host "Asset uploaded: $assetUrl" -ForegroundColor Green

# ── Step 4: Trigger workflow dispatch ─────────────────────────────────
Write-Host "`nTriggering workflow: Build & Push MT5 Bridge Base Image" -ForegroundColor Cyan

$wfInputs = @{
    ref    = 'hotfix/mt5-readiness'
    inputs = @{
        mt5_url    = $assetUrl
        wine_base  = 'scottyhardy/docker-wine:stable-10.0-20250525'
    }
} | ConvertTo-Json -Depth 3

Invoke-RestMethod `
    -Uri "https://api.github.com/repos/$Owner/$Repo/actions/workflows/build-mt5-base-dispatch.yml/dispatches" `
    -Headers $headers -Method POST -Body $wfInputs | Out-Null

Write-Host "Workflow dispatch sent!" -ForegroundColor Green
Write-Host "`nCheck progress at: https://github.com/$Owner/$Repo/actions" -ForegroundColor Cyan

# Cleanup
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
Write-Host "`nDone." -ForegroundColor Green
