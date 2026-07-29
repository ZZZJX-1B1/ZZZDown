$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python scripts/prepare_tools.py
$env:PYINSTALLER_CONFIG_DIR = Join-Path (Get-Location) ".pyinstaller-cache"
python -m PyInstaller --noconfirm --clean ZZZDown.spec
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 is required" }
& $iscc installer\ZZZDown.iss
Write-Host "Created dist\ZZZDown-Windows-Setup.exe"
