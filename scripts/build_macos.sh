#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-build.txt
python3 scripts/prepare_tools.py
PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache" python3 -m PyInstaller --noconfirm --clean ZZZDown.spec
hdiutil create -volname ZZZDown -srcfolder dist/ZZZDown.app -ov -format UDZO dist/ZZZDown-macOS.dmg
echo "Created dist/ZZZDown-macOS.dmg"
