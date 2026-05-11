#Requires -Version 5.1
param(
    [Parameter(Mandatory)]
    [string]$Token,

    [Parameter(Mandatory)]
    [string]$ZipPath,

    [string]$Repo = 'loriloha/adaptive-trading-bot',
    [string]$Tag  = 'mt5-portable-5430'
)

$ErrorActionPreference = 'Stop'

$headers = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}

# 1) Check if release already exists
try {
    $existing = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/$Tag" `
                                  -Headers $headers -Method GET
    Write-Host "Release already exists (id=$($existing.id)). Deleting old asset if present..."
    $release = $existing
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 404) {
        Write-Host "Creating new release..."
        $body = @{
            tag_name   = $Tag
            name       = 'MT5 Portable Build 5430'
            body       = 'MetaTrader 5 terminal build 5430 portable archive for Docker base image'
            draft      = $false
            prerelease = $false
        } | ConvertTo-Json

        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" `
                                     -Headers $headers -Method POST `
                                     -ContentType 'application/json' -Body $body
        Write-Host "Created release id=$($release.id)"
    } else {
        throw
    }
}

# 2) Delete existing asset with same name if present
$assetName = Split-Path $ZipPath -Leaf
$existingAsset = $release.assets | Where-Object { $_.name -eq $assetName }
if ($existingAsset) {
    Write-Host "Deleting existing asset id=$($existingAsset.id)..."
    Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/assets/$($existingAsset.id)" `
                      -Headers $headers -Method DELETE | Out-Null
}

# 3) Upload asset
$uploadUrl = $release.upload_url -replace '\{.*$', ''
$uri = "$uploadUrl?name=$assetName"
Write-Host "Uploading $assetName to $uri ..."

$uploadHeaders = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'Content-Type' = 'application/zip'
}

$bytes = [System.IO.File]::ReadAllBytes($ZipPath)
$response = Invoke-RestMethod -Uri $uri -Headers $uploadHeaders -Method POST -Body $bytes
Write-Host "Uploaded asset: $($response.browser_download_url)"
