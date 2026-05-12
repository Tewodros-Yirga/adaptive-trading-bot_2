#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$RepoOwner = "loriloha"
$RepoName  = "adaptive-trading-bot"
$TagName   = "mt5-installer-build-5836"
$ReleaseName = "MT5 installer (Build 5836)"
$AssetName = "mt5setup.exe"

$LocalFile = Read-Host "Enter full path to your local mt5setup.exe (build 5836)"
if (-not (Test-Path $LocalFile)) { throw "File not found: $LocalFile" }

$verInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($LocalFile)
Write-Host "Detected FileVersion: $($verInfo.FileVersion)"

$token = $env:GITHUB_TOKEN
if (-not $token) {
    $token = Read-Host "Enter GitHub personal access token (needs repo scope)" -AsSecureString
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
    )
}

$authHeaders = @{ Authorization = "token $token"; Accept = "application/vnd.github+json" }

$releaseUrl = "https://api.github.com/repos/$RepoOwner/$RepoName/releases/tags/$TagName"
try {
    $existing = Invoke-RestMethod -Uri $releaseUrl -Headers $authHeaders -Method GET
    Write-Host "Release '$TagName' already exists (id=$($existing.id))."
    $releaseId = $existing.id
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 404) {
        Write-Host "Creating new release '$TagName'..."
        $body = @{ tag_name = $TagName; name = $ReleaseName; body = "MetaTrader 5 installer build 5836" } | ConvertTo-Json
        $newRelease = Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoOwner/$RepoName/releases" -Headers $authHeaders -Method POST -Body $body -ContentType "application/json"
        $releaseId = $newRelease.id
    } else { throw }
}

$releaseDetail = Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoOwner/$RepoName/releases/$releaseId" -Headers $authHeaders -Method GET
$oldAsset = $releaseDetail.assets | Where-Object { $_.name -eq $AssetName }
if ($oldAsset) {
    Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoOwner/$RepoName/releases/assets/$($oldAsset.id)" -Headers $authHeaders -Method DELETE | Out-Null
}

$uploadUrl = "https://uploads.github.com/repos/$RepoOwner/$RepoName/releases/$releaseId/assets?name=$AssetName"
Invoke-RestMethod -Uri $uploadUrl -Headers (@{ Authorization = "token $token"; Accept = "application/vnd.github+json" }) -Method POST -InFile $LocalFile -ContentType "application/octet-stream" | Out-Null

Write-Host "Done! Release URL: https://github.com/$RepoOwner/$RepoName/releases/tag/$TagName"
