from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .indexer import generate_global
from .tools import ffmpeg_path, ytdlp_command


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


def parse_urls(text: str) -> list[str]:
    found, seen = [], set()
    for candidate in re.findall(r"https?://[^\s]+", text):
        url = candidate.strip().rstrip(",，;；)]}")
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def safe_name(value: str, fallback: str = "Untitled") -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(value or "")).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return value[:150] or fallback


def browser_args(settings: Settings) -> list[str]:
    args = []
    if settings.browser and settings.browser != "none":
        args += ["--cookies-from-browser", settings.browser]
    if settings.direct_connection:
        args += ["--proxy", ""]
    elif settings.proxy.strip():
        args += ["--proxy", settings.proxy.strip()]
    return args


def task_type(url: str, data: dict) -> str:
    lowered = url.lower()
    if "space.bilibili.com" in lowered or re.search(r"(?:youtube|tiktok)\.com/@", lowered) or "douyin.com/user" in lowered or re.search(r"xinpianchang\.com/u\d+", lowered):
        return "creator"
    if data.get("_type") in {"playlist", "multi_video"} or data.get("playlist") or "list=" in lowered or "/playlist" in lowered or "/lists/" in lowered:
        return "playlist"
    return "single"


def task_identity(data: dict, url: str) -> tuple[str, str]:
    entries = data.get("entries") or []
    first = entries[0] if entries and isinstance(entries[0], dict) else {}
    extractor = data.get("extractor_key") or first.get("extractor_key") or data.get("extractor")
    source = safe_name(extractor or urlparse(url).netloc.replace("www.", ""), "Other")
    candidates = [
        data.get("playlist_title"), data.get("title"), data.get("channel"), data.get("uploader"),
        first.get("playlist_title"), first.get("channel"), first.get("uploader"), first.get("title"),
    ]
    title = next((str(item).strip() for item in candidates if item and str(item).strip()), "Untitled")
    return source, safe_name(title)


class DownloadEngine:
    def __init__(self, settings: Settings, log=lambda _message: None):
        self.settings = settings
        self.log = log
        self.process: subprocess.Popen | None = None

    @property
    def library(self) -> Path:
        path = Path(self.settings.library_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run(self, command: list[str], capture: bool = False) -> subprocess.CompletedProcess:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(command, text=True, capture_output=capture, errors="replace", creationflags=flags)

    def _metadata(self, url: str) -> dict:
        command = [
            *ytdlp_command(), *browser_args(self.settings),
            "--flat-playlist", "--playlist-items", "1", "--dump-single-json", url,
        ]
        result = self._run(command, capture=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "Unable to read video metadata")
        return json.loads(result.stdout)

    def _task_dir(self, source: str, title: str, url: str) -> Path:
        map_path = self.library / ".task-map.json"
        try:
            task_map = json.loads(map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            task_map = {}
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        stored = task_map.get(key)
        task_dir = self.library / stored if stored else self.library / source / title
        if not stored and task_dir.exists() and not (task_dir / ".source-url.txt").exists():
            task_dir = self.library / source / f"{title} [{key[:6]}]"
        task_dir.mkdir(parents=True, exist_ok=True)
        task_map[key] = task_dir.relative_to(self.library).as_posix()
        map_path.write_text(json.dumps(task_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (task_dir / ".source-url.txt").write_text(url + "\n", encoding="utf-8")
        return task_dir

    def _stream(self, command: list[str]) -> int:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1, creationflags=flags,
        )
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.log(line.rstrip())
        status = self.process.wait()
        self.process = None
        return status

    def download(self, urls: list[str]) -> int:
        failures = 0
        ffmpeg = ffmpeg_path()
        for number, url in enumerate(urls, 1):
            self.log(f"[{number}/{len(urls)}] {url}")
            try:
                data = self._metadata(url)
                source, title = task_identity(data, url)
                kind = task_type(url, data)
                task_dir = self._task_dir(source, title, url)
                self.log(f"{source} · {kind} · {title}")
                command = [
                    *ytdlp_command(), *browser_args(self.settings),
                    "--ffmpeg-location", ffmpeg,
                    "--download-archive", str(task_dir / ".download-archive-4k-v1.txt"),
                    "--continue", "--ignore-errors", "--retries", "10", "--fragment-retries", "10",
                    "--concurrent-fragments", "4", "--sleep-requests", "1",
                    "--format", "bv*[height<=2160]+ba/b[height<=2160]/b",
                    "--format-sort", "res:2160,vcodec:h264,fps,hdr:12,quality,br",
                    "--merge-output-format", "mp4", "--remux-video", "mp4",
                    "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
                    "--write-subs", "--write-auto-subs",
                    "--sub-langs", "zh.*,zh-Hans,zh-Hant,original,en.*,-live_chat",
                    "--convert-subs", "srt", "--embed-metadata", "--embed-thumbnail",
                    "--paths", str(task_dir),
                    "--output", "%(upload_date>%Y-%m-%d,release_date>%Y-%m-%d|Unknown)s_%(title).160B [%(id)s].%(ext)s",
                    url,
                ]
                if self._stream(command) != 0:
                    failures += 1
            except Exception as exc:
                failures += 1
                self.log(f"ERROR: {exc}")
        generate_global(self.library, self.library)
        return failures

    def cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


def import_library(source: Path, destination: Path, log=lambda _message: None) -> int:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents:
        raise ValueError("Invalid library import path")
    copied = 0
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if "视频库回收站" in relative.parts or path.name in {"全局视频库.html", ".video-library-token"}:
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    generate_global(destination, destination)
    log(f"Imported {copied} files")
    return copied
