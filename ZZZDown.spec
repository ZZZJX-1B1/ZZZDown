# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
datas = [(str(root / "src" / "zzzdown" / "resources"), "zzzdown/resources")]
datas += collect_data_files("certifi")
hiddenimports = collect_submodules("curl_cffi") + collect_submodules("yt_dlp") + collect_submodules("PySide6")
icon = root / "src" / "zzzdown" / "resources" / ("ZZZDown.icns" if __import__("sys").platform == "darwin" else "ZZZDown.ico")

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="ZZZDown", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ZZZDown")

if __import__("sys").platform == "darwin":
    app = BUNDLE(
        coll, name="ZZZDown.app", icon=str(icon) if icon.exists() else None,
        bundle_identifier="com.zzzdown.app",
        info_plist={"CFBundleDisplayName": "ZZZDown", "NSHighResolutionCapable": True},
    )
