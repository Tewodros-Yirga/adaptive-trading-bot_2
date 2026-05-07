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

function New-PosixZipFromDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$RootDir,
        [Parameter(Mandatory = $true)][string]$OutZipPath
    )
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $root = (Resolve-Path $RootDir).Path.TrimEnd('\', '/')
    $zipStream = [System.IO.File]::Open($OutZipPath, [System.IO.FileMode]::Create)
    try {
        $zip = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            # Include directories (so empty dirs survive) and files.
            $entries = Get-ChildItem -Path $root -Recurse -Force

            # Create directory entries first.
            foreach ($d in $entries | Where-Object { $_.PSIsContainer }) {
                $rel = $d.FullName.Substring($root.Length).TrimStart('\','/')
                if (-not $rel) { continue }
                $rel = ($rel -replace '\\','/') + '/'
                [void]$zip.CreateEntry($rel)
            }

            foreach ($f in $entries | Where-Object { -not $_.PSIsContainer }) {
                $rel = $f.FullName.Substring($root.Length).TrimStart('\','/')
                if (-not $rel) { continue }
                $rel = ($rel -replace '\\','/')
                $entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
                $entryStream = $entry.Open()
                try {
                    $fileStream = [System.IO.File]::OpenRead($f.FullName)
                    try {
                        $fileStream.CopyTo($entryStream)
                    } finally {
                        $fileStream.Dispose()
                    }
                } finally {
                    $entryStream.Dispose()
                }
            }
        } finally {
            $zip.Dispose()
        }
    } finally {
        $zipStream.Dispose()
    }
}

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
        # IMPORTANT: CreateFromDirectory on Windows writes entries using '\'
        # separators, which 7z on Linux treats as literal characters. Build a
        # POSIX-safe zip with '/' separators to avoid flat "MetaTrader 5\file"
        # entries.
        New-PosixZipFromDirectory -RootDir $tmpRoot -OutZipPath $OutZip
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
