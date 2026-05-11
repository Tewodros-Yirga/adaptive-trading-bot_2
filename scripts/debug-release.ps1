$tokenFile = Join-Path $PSScriptRoot '.github_token'
$Token = Get-Content $tokenFile -Raw | ForEach-Object { $_.Trim() }
$headers = @{
    Authorization = "Bearer $Token"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
try {
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/loriloha/adaptive-trading-bot/releases/tags/mt5-portable-5430' -Headers $headers -Method GET
    Write-Host "upload_url raw: $($release.upload_url)"
    $uploadUrl = $release.upload_url -replace '\{.*$', ''
    Write-Host "upload_url cleaned: $uploadUrl"
    $uri = "$uploadUrl?name=mt5_v5430.zip"
    Write-Host "final uri: $uri"
} catch {
    Write-Host "Error: $($_.Exception.Message)"
    if ($_.Exception.Response) {
        Write-Host "Status: $($_.Exception.Response.StatusCode.value__)"
    }
}
