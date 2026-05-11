<#
.SYNOPSIS
    List tags for the mt5-bridge-base GHCR image.
.DESCRIPTION
    Queries the GitHub API for container package versions and lists tags.
    Useful for finding an older base image with a terminal build that matches
    an available MetaTrader5 PyPI package version.
.EXAMPLE
    $env:GITHUB_TOKEN = "ghp_..."
    .\scripts\list-ghcr-tags.ps1 | Format-Table
#>
param(
    [string]$PackageName = "mt5-bridge-base",
    [string]$Owner = "loriloha"
)

$token = $env:GITHUB_TOKEN
if (-not $token) {
    Write-Error "GITHUB_TOKEN environment variable is required (needs read:packages scope)."
    exit 1
}

$headers = @{
    Authorization = "Bearer $token"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

$uri = "https://api.github.com/users/${Owner}/packages/container/${PackageName}/versions"

try {
    $versions = Invoke-RestMethod -Uri $uri -Headers $headers
    $rows = foreach ($ver in $versions) {
        $tags = $ver.metadata.container.tags
        if ($tags) {
            [PSCustomObject]@{
                Id = $ver.id
                Name = $ver.name
                Tags = ($tags -join ", ")
                Updated = $ver.updated_at
            }
        }
    }
    $rows | Sort-Object Updated -Descending
} catch {
    Write-Error "Failed to list packages: $_"
    exit 1
}
