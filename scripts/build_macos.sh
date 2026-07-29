#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
version="${APP_VERSION:-0.1.3}"
version="${version#v}"
product="ZZZDown-v${version}-macOS-Apple-Silicon"

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "The macOS release must be built on an Apple Silicon runner." >&2
  exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-build.txt
python3 scripts/prepare_tools.py
APP_VERSION="$version" PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache" python3 -m PyInstaller --noconfirm --clean ZZZDown.spec
hdiutil create -volname ZZZDown -srcfolder dist/ZZZDown.app -ov -format UDZO "dist/${product}.dmg"
ditto -c -k --sequesterRsrc --keepParent dist/ZZZDown.app "dist/${product}-portable.zip"
test -f "dist/${product}.dmg"
test -f "dist/${product}-portable.zip"
echo "Created dist/${product}.dmg and portable ZIP"
