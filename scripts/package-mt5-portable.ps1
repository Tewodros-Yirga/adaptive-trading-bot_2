param(
    [string]$SourceDir = "G:\mt5 v5640",
    [string]$OutZip = "G:\adaptive-trading-bot\mt5-portable.zip",
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $SourceDir)) {
    throw "SourceDir not found: '$SourceDir'"
}
if (-not (Test-Path (Join-Path $SourceDir "terminal64.exe"))) {
    throw "terminal64.exe missing in SourceDir: '$SourceDir'"
}

if ((Test-Path $OutZip) -and (-not $Overwrite)) {
    throw "Output zip already exists: '$OutZip' (pass -Overwrite to replace)"
}

$sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue)

$tmpRoot = Join-Path $env:TEMP ("mt5-portable-pack-" + [guid]::NewGuid().ToString("N"))
$tmpPkg = Join-Path $tmpRoot "MetaTrader 5"
New-Item -ItemType Directory -Path $tmpPkg -Force | Out-Null

try {
    Write-Host "Copying source folder..."
    Copy-Item -Path (Join-Path $SourceDir "*") -Destination $tmpPkg -Recurse -Force

    # Ensure no path entry contains literal backslashes in file names.
    $badNames = Get-ChildItem -Path $tmpPkg -Recurse -Force | Where-Object { $_.Name -like "*\*" }
    if ($badNames.Count -gt 0) {
        throw "Found invalid backslash-named entries in payload. Refusing to package."
    }

    if (Test-Path $OutZip) {
        Remove-Item $OutZip -Force
    }

    Write-Host "Creating zip archive..."
    if ($null -ne $sevenZip) {
        Push-Location $tmpRoot
        & 7z a -tzip -mx=5 $OutZip ".\MetaTrader 5\*" | Out-Host
        Pop-Location
    }
    else {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory($tmpRoot, $OutZip)
    }

    if (-not (Test-Path $OutZip)) {
        throw "Failed to create zip archive."
    }

    $size = (Get-Item $OutZip).Length
    Write-Host ("Portable zip created: {0} ({1} bytes)" -f $OutZip, $size)
}
finally {
    if (Test-Path $tmpRoot) {
        Remove-Item $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
