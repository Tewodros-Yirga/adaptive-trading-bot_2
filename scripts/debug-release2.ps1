$tokenFile = Join-Path $PSScriptRoot '.github_token'
$Token = Get-Content $tokenFile -Raw | ForEach-Object { $_.Trim() }
$headers = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/loriloha/adaptive-trading-bot/releases/tags/mt5-portable-5430' -Headers $headers -Method GET
Write-Host "raw: $($release.upload_url)"
$cleaned = $release.upload_url -replace '\{.*$', ''
Write-Host "cleaned: [$cleaned]"
$uri = $cleaned + '?name=mt5_v5430.zip'
Write-Host "uri: [$uri]"
