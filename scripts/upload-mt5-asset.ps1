$tokenFile = Join-Path $PSScriptRoot '.github_token'
$Token = Get-Content $tokenFile -Raw | ForEach-Object { $_.Trim() }
$ZipPath = 'G:\adaptive-trading-bot\mt5_v5430.zip'
$Repo = 'loriloha/adaptive-trading-bot'
$Tag = 'mt5-portable-5430'

$headers = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}

# Get release
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/$Tag" -Headers $headers -Method GET
Write-Host "Release id=$($release.id)"

# Delete existing asset
$assetName = 'mt5_v5430.zip'
$existingAsset = $release.assets | Where-Object { $_.name -eq $assetName }
if ($existingAsset) {
    Write-Host "Deleting existing asset id=$($existingAsset.id)..."
    Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/assets/$($existingAsset.id)" -Headers $headers -Method DELETE | Out-Null
}

# Upload
$uploadUrl = ($release.upload_url -replace '\{.*$', '') + '?name=' + [System.Uri]::EscapeDataString($assetName)
Write-Host "Uploading to: $uploadUrl"

$uploadHeaders = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'Content-Type' = 'application/zip'
}

# Use .NET WebClient for binary upload
$wc = New-Object System.Net.WebClient
$uploadHeaders.GetEnumerator() | ForEach-Object { $wc.Headers.Add($_.Key, $_.Value) }
$bytes = [System.IO.File]::ReadAllBytes($ZipPath)
$responseBytes = $wc.UploadData($uploadUrl, 'POST', $bytes)
$response = [System.Text.Encoding]::UTF8.GetString($responseBytes) | ConvertFrom-Json
Write-Host "SUCCESS: $($response.browser_download_url)"
