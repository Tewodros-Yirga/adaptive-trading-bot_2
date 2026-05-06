$src = "g:\adaptive-trading-bot\mt5 5640"
$dst = "g:\adaptive-trading-bot\_mt5-portable-init"
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
New-Item -ItemType Directory -Path $dst | Out-Null
Copy-Item "$src\terminal64.exe" $dst
Copy-Item "$src\MetaEditor64.exe" $dst
Copy-Item "$src\metatester64.exe" $dst
Write-Host "Copied 3 exes - launching /portable..."
$proc = Start-Process "$dst\terminal64.exe" "/portable" -PassThru
Write-Host "PID: $($proc.Id)"
$waited = 0
while ($waited -lt 300) {
    Start-Sleep 5
    $waited += 5
    $tini = if (Test-Path "$dst\terminal.ini") { (Get-Item "$dst\terminal.ini").Length } else { 0 }
    $fc = (Get-ChildItem $dst -Recurse -File -ErrorAction SilentlyContinue).Count
    $exp = Test-Path "$dst\MQL5\Experts"
    Write-Host "${waited}s: files=$fc tini=${tini}B MQL5\Experts=$exp"
    if ($exp -and ($tini -gt 500)) { Write-Host "DONE!"; break }
}
Start-Sleep 10
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Write-Host "Portable dir ready: $dst"
Write-Host "Files: $((Get-ChildItem $dst -Recurse -File -ErrorAction SilentlyContinue).Count)"
