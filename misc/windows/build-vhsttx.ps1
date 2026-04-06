$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

Write-Host "Building VHSTTX Windows bundle from $root"

pyinstaller --noconfirm --clean misc\windows\vhsttx_windows.spec

Write-Host ""
Write-Host "Build complete."
Write-Host "Run: dist\\VHSTTX-Windows\\VHSTTX.exe"
Write-Host "CLI: dist\\VHSTTX-Windows\\teletext.exe"
Write-Host "Viewer: dist\\VHSTTX-Windows\\TTViewer.exe"
