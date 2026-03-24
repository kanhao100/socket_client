param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Version -notmatch "^v") {
    throw "Version 必须以 v 开头，例如: v1.2.3"
}

$arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
$exeName = "socket_client-$Version-$arch"

Write-Host "==> Version: $Version"
Write-Host "==> Arch: $arch"
Write-Host "==> Output: dist/$exeName.exe"

if (-not $SkipInstall) {
    Write-Host "==> Installing dependencies..."
    python -m pip install --upgrade pip
    pip install pyinstaller azure-cognitiveservices-speech
}

Write-Host "==> Building executable..."
pyinstaller --noconfirm --clean --onefile --windowed `
    --name socket_client `
    --hidden-import azure.cognitiveservices.speech `
    --collect-all azure.cognitiveservices.speech `
    socket_client.py

$source = "dist/socket_client.exe"
$target = "dist/$exeName.exe"

if (-not (Test-Path $source)) {
    throw "Build 失败：未找到 $source"
}

if (Test-Path $target) {
    Remove-Item $target -Force
}

Rename-Item -Path $source -NewName "$exeName.exe"

Write-Host ""
Write-Host "Build success: $target" -ForegroundColor Green
