$ErrorActionPreference = "Stop"

$AppDir = "$env:ProgramFiles\MHMPipeline"
$UvBin = "$AppDir\bin\uv.exe"

# Download and install uv if not present
if (-not (Test-Path $UvBin)) {
    Write-Host "Installing uv..."
    New-Item -ItemType Directory -Path "$AppDir\bin" -Force | Out-Null
    Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile "$env:TEMP\install-uv.ps1"
    & "$env:TEMP\install-uv.ps1" -InstallDir "$AppDir\bin"
}

# Install Python 3.12 via uv
Write-Host "Installing Python 3.12..."
& $UvBin python install 3.12

# Install project dependencies
Write-Host "Installing project dependencies..."
Set-Location $AppDir
& $UvBin sync --frozen --no-dev

# Write launcher .bat file
Write-Host "Creating application launcher..."
$BatContent = @"
@echo off
"$AppDir\Scripts\python.exe" -m mhm_pipeline.app %*
"@
Set-Content -Path "$AppDir\MHMPipeline.bat" -Value $BatContent

Write-Host "MHM Pipeline installed successfully."
