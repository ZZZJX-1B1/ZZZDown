from __future__ import annotations

import locale
import sys
from pathlib import Path

try:
    from platformdirs import user_config_dir, user_videos_dir
except ImportError:  # Source-tree fallback; packaged builds include platformdirs.
    def user_config_dir(appname: str, roaming: bool = True) -> str:
        if sys.platform == "darwin":
            return str(Path.home() / "Library" / "Application Support" / appname)
        if sys.platform == "win32":
            return str(Path.home() / "AppData" / "Roaming" / appname)
        return str(Path.home() / ".config" / appname)

    def user_videos_dir() -> str:
        return str(Path.home() / ("Movies" if sys.platform == "darwin" else "Videos"))


APP_NAME = "ZZZDown"


def resource_root() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parent


def config_dir() -> Path:
    path = Path(user_config_dir(APP_NAME, roaming=True))
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_library_dir() -> Path:
    path = Path(user_videos_dir()) / APP_NAME / "全局视频库"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_language() -> str:
    language = (locale.getlocale()[0] or "").lower()
    return "zh_CN" if language.startswith("zh") else "en_US"
