$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$version = if ($env:APP_VERSION) { $env:APP_VERSION -replace '^v', '' } else { "0.1.3" }
$product = "ZZZDown-v$version-Windows-x64"
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python scripts/prepare_tools.py
$env:PYINSTALLER_CONFIG_DIR = Join-Path (Get-Location) ".pyinstaller-cache"
$env:APP_VERSION = $version
python -m PyInstaller --noconfirm --clean ZZZDown.spec
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 is required" }
& $iscc "/DMyAppVersion=$version" installer\ZZZDown.iss
$installer = "dist\$product-Setup.exe"
Move-Item "dist\ZZZDown-Windows-Setup.exe" $installer -Force
$portable = "dist\$product-portable.zip"
Compress-Archive -Path "dist\ZZZDown\*" -DestinationPath $portable -Force
if (-not (Test-Path $installer) -or -not (Test-Path $portable)) { throw "Release packages were not created" }
Write-Host "Created $installer and $portable"
