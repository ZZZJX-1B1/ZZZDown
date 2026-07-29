from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import config_dir, default_language, default_library_dir


@dataclass
class Settings:
    language: str = ""
    library_dir: str = ""
    browser: str = "chrome"
    proxy: str = ""
    direct_connection: bool = False
    disclaimer_accepted: bool = False

    def normalize(self) -> "Settings":
        self.language = self.language or default_language()
        self.library_dir = self.library_dir or str(default_library_dir())
        return self


SETTINGS_PATH = config_dir() / "settings.json"


def load_settings() -> Settings:
    try:
        payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    allowed = Settings.__dataclass_fields__
    return Settings(**{key: value for key, value in payload.items() if key in allowed}).normalize()


def save_settings(settings: Settings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(SETTINGS_PATH)

