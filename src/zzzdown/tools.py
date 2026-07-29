from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import urllib.request
from pathlib import Path

from .paths import config_dir, resource_root


def _executable_name(base: str) -> str:
    return base + (".exe" if sys.platform == "win32" else "")


def updated_ytdlp_path() -> Path:
    return config_dir() / "tools" / _executable_name("yt-dlp")


def bundled_binary(name: str) -> Path:
    return resource_root() / "resources" / "bin" / _executable_name(name)


def detected_browsers() -> set[str]:
    """Detect installed browsers without reading profiles, cookies, or history."""
    candidates: dict[str, list[Path]] = {
        "chrome": [], "edge": [], "firefox": [],
    }
    if sys.platform == "darwin":
        candidates["chrome"] = [Path("/Applications/Google Chrome.app")]
        candidates["edge"] = [Path("/Applications/Microsoft Edge.app")]
        candidates["firefox"] = [Path("/Applications/Firefox.app")]
    elif sys.platform == "win32":
        roots = [Path(os.environ.get(name, "")) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        candidates["chrome"] = [root / "Google/Chrome/Application/chrome.exe" for root in roots if str(root) != "."]
        candidates["edge"] = [root / "Microsoft/Edge/Application/msedge.exe" for root in roots if str(root) != "."]
        candidates["firefox"] = [root / "Mozilla Firefox/firefox.exe" for root in roots if str(root) != "."]
    else:
        for key, commands in {"chrome": ("google-chrome", "chromium"), "edge": ("microsoft-edge",), "firefox": ("firefox",)}.items():
            if any(shutil.which(command) for command in commands):
                candidates[key] = [Path("/")]
    return {key for key, paths in candidates.items() if any(path.exists() for path in paths)}


def ytdlp_command() -> list[str]:
    updated = updated_ytdlp_path()
    if updated.is_file():
        return [str(updated)]
    bundled = bundled_binary("yt-dlp")
    if bundled.is_file():
        return [str(bundled)]
    installed = shutil.which("yt-dlp")
    if installed:
        return [installed]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--yt-dlp-cli"]
    return [sys.executable, "-m", "yt_dlp"]


def ffmpeg_path() -> str:
    bundled = bundled_binary("ffmpeg")
    if bundled.is_file():
        return str(bundled)
    installed = shutil.which("ffmpeg")
    if installed:
        return installed
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("FFmpeg is not available") from exc


def update_ytdlp(progress=lambda _message: None) -> Path:
    platform_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp_macos" if sys.platform == "darwin" else "yt-dlp"
    url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{platform_name}"
    destination = updated_ytdlp_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    progress(f"Downloading {url}")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        urllib.request.urlretrieve(url, temporary_path)
        if sys.platform != "win32":
            temporary_path.chmod(temporary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination
