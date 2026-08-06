from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .indexer import generate_global
from .tools import ffmpeg_path, ytdlp_command


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
COOKIE_DATABASE_ERRORS = (
    "could not copy chrome cookie database",
    "permission denied",  # yt-dlp #7271 when Chromium keeps the SQLite file locked
)


def friendly_error(message: str) -> str:
    """Turn common downloader failures into one concise, actionable message."""
    cleaned = str(message or "").strip()
    while cleaned.lower().startswith("error:"):
        cleaned = cleaned[6:].strip()
    lowered = cleaned.lower()
    if "cookie" in lowered and any(marker in lowered for marker in COOKIE_DATABASE_ERRORS):
        return (
            "Chrome 正在占用登录 Cookies 数据库。请完全退出 Chrome（包括后台进程）后重试；"
            "或在设置中切换到已登录哔哩哔哩的 Edge/Firefox。 / "
            "Chrome is locking its cookie database. Fully quit Chrome, including background processes, "
            "then retry; or select a signed-in Edge/Firefox profile in Settings."
        )
    return cleaned or "Unknown downloader error"


def parse_urls(text: str) -> list[str]:
    found, seen = [], set()
    # Treat surrounding titles and notes as prose, even when they touch the URL
    # without a space (a common format when copying lists from Chinese apps).
    pattern = r'''https?://[^\s，；。！？、（）【】《》“”‘’<>'"\(\)\[\]\{\}]+'''
    for candidate in re.findall(pattern, text):
        url = candidate.strip().rstrip(".,，;；:：!?！？)]}>'\"")
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def parse_progress_line(line: str) -> dict[str, str | float | int] | None:
    """Parse the stable progress record emitted by the yt-dlp command."""
    marker = "[zzzdown-progress] "
    if not line.startswith(marker):
        return None
    fields = [field.strip() for field in line[len(marker):].split("|", 11)]
    if len(fields) < 7:
        return None
    try:
        percent = float(fields[0].replace("%", "").strip())
    except ValueError:
        return None
    cleaned = ["—" if value in {"", "NA", "N/A", "None"} else value for value in fields[1:]]
    result = {
        "percent": max(0.0, min(100.0, percent)),
        "downloaded": cleaned[0],
        "total": cleaned[1],
        "speed": cleaned[2],
        "eta": cleaned[3],
        "resolution": cleaned[4],
        "format": cleaned[5].upper() if cleaned[5] != "—" else "—",
    }
    if len(fields) >= 12:
        result.update({
            "item_index": _positive_int(fields[7]),
            "item_total": _optional_positive_int(fields[8]),
            "video_id": _clean_field(fields[9]),
            "video_url": _clean_field(fields[10]),
            "video_title": _clean_field(fields[11]),
        })
    return result


def _clean_field(value: str) -> str:
    cleaned = str(value or "").strip()
    return "" if cleaned in {"", "NA", "N/A", "None"} else cleaned


def _positive_int(value: str) -> int:
    try:
        return max(1, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 1


def _optional_positive_int(value: str) -> int:
    cleaned = _clean_field(value)
    if not cleaned:
        return 0
    try:
        return max(0, int(float(cleaned)))
    except ValueError:
        return 0


def parse_item_line(line: str) -> dict[str, str | int] | None:
    marker = "[zzzdown-item] "
    if not line.startswith(marker):
        return None
    fields = line[len(marker):].split("|", 4)
    if len(fields) != 5:
        return None
    return {
        "item_index": _positive_int(fields[0]),
        "item_total": _optional_positive_int(fields[1]),
        "video_id": _clean_field(fields[2]),
        "video_url": _clean_field(fields[3]),
        "video_title": _clean_field(fields[4]),
    }


def parse_item_done_line(line: str) -> dict[str, str | int] | None:
    marker = "[zzzdown-item-done] "
    if not line.startswith(marker):
        return None
    return parse_item_line("[zzzdown-item] " + line[len(marker):])


def aggregate_progress(item_index: int, item_total: int, item_percent: float) -> float:
    total = max(1, int(item_total))
    index = max(1, min(total, int(item_index)))
    percent = max(0.0, min(100.0, float(item_percent)))
    return ((index - 1) + percent / 100) / total * 100


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


def history_args(task_dir: Path, force_redownload: bool) -> list[str]:
    if force_redownload:
        return ["--force-overwrites"]
    return ["--download-archive", str(task_dir / ".download-archive-4k-v1.txt")]


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
    def __init__(self, settings: Settings, log=lambda _message: None, force_redownload: bool = False):
        self.settings = settings
        self.log = log
        self.force_redownload = force_redownload
        self.process: subprocess.Popen | None = None
        self._cancelled = threading.Event()
        self._paused = threading.Event()
        self._pause_gate = threading.Event()
        self._pause_gate.set()
        self._process_lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def library(self) -> Path:
        path = Path(self.settings.library_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _process_options() -> dict:
        if sys.platform == "win32":
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        return {"start_new_session": True}

    def _set_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self.process = process
        if self.paused:
            self._suspend_process(process)

    def _clear_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            if self.process is process:
                self.process = None

    @staticmethod
    def _windows_process_tree(process: subprocess.Popen) -> list:
        import psutil

        try:
            root = psutil.Process(process.pid)
            return [*root.children(recursive=True), root]
        except psutil.Error:
            return []

    def _suspend_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                for item in self._windows_process_tree(process):
                    try:
                        item.suspend()
                    except Exception:
                        pass
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGSTOP)
        except (OSError, ProcessLookupError):
            pass

    def _resume_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                for item in reversed(self._windows_process_tree(process)):
                    try:
                        item.resume()
                    except Exception:
                        pass
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGCONT)
        except (OSError, ProcessLookupError):
            pass

    def _terminate_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                for item in self._windows_process_tree(process):
                    try:
                        item.terminate()
                    except Exception:
                        pass
            else:
                group = os.getpgid(process.pid)
                os.killpg(group, signal.SIGCONT)
                os.killpg(group, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                pass

    def _wait_until_resumed(self) -> bool:
        while self.paused and not self.cancelled:
            self._pause_gate.wait(0.1)
        return not self.cancelled

    def _run(self, command: list[str], capture: bool = False) -> subprocess.CompletedProcess:
        if not self._wait_until_resumed():
            return subprocess.CompletedProcess(command, -1, "", "")
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            errors="replace",
            **self._process_options(),
        )
        self._set_process(process)
        try:
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            self._clear_process(process)

    def _metadata(self, url: str) -> dict:
        command = [
            *ytdlp_command(), *browser_args(self.settings),
            "--flat-playlist", "--dump-single-json", url,
        ]
        result = self._run(command, capture=True)
        if result.returncode != 0:
            output = (result.stderr or result.stdout).strip()
            detail = output.splitlines()
            candidate = output if "cookie" in output.lower() else (detail[-1] if detail else "Unable to read video metadata")
            raise RuntimeError(friendly_error(candidate))
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

    @staticmethod
    def _info_snapshot(task_dir: Path) -> dict[Path, int]:
        snapshot = {}
        for path in task_dir.glob("*.info.json"):
            try:
                snapshot[path] = path.stat().st_mtime_ns
            except OSError:
                pass
        return snapshot

    def _stamp_download_task(
        self,
        task_dir: Path,
        before: dict[Path, int],
        task_id: str,
        started_at: int,
        batch_index: int,
    ) -> None:
        """Attach one-click download order to metadata written by this run."""
        for info_path in task_dir.glob("*.info.json"):
            try:
                if before.get(info_path) == info_path.stat().st_mtime_ns:
                    continue
                data = json.loads(info_path.read_text(encoding="utf-8"))
                item_index = _positive_int(
                    data.get("playlist_index") or data.get("playlist_autonumber")
                ) or 1
                data["zzzdown"] = {
                    "task_id": task_id,
                    "started_at": started_at,
                    "batch_index": batch_index,
                    "item_index": item_index,
                }
                temporary = info_path.with_name(info_path.name + ".tmp")
                temporary.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(info_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                self.log(f"WARNING: Unable to record download order for {info_path.name}")

    def _stream(self, command: list[str]) -> int:
        if not self._wait_until_resumed():
            return -1
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1, **self._process_options(),
        )
        self._set_process(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                rendered = line.rstrip()
                if rendered.lower().startswith("error:"):
                    rendered = f"ERROR: {friendly_error(rendered)}"
                self.log(rendered)
            return process.wait()
        finally:
            self._clear_process(process)

    def download(self, urls: list[str]) -> int:
        failures = 0
        ffmpeg = ffmpeg_path()
        task_started_at = int(time.time() * 1000)
        task_id = f"{task_started_at}-{secrets.token_hex(4)}"
        for number, url in enumerate(urls, 1):
            if self.cancelled:
                break
            self.log(f"[{number}/{len(urls)}] {url}")
            cookie_source = self.settings.browser if self.settings.browser != "none" else "none"
            self.log(f"Cookies: {cookie_source}")
            if self.force_redownload:
                self.log("Re-download/quality upgrade: enabled")
            try:
                data = self._metadata(url)
                if self.cancelled:
                    break
                source, title = task_identity(data, url)
                kind = task_type(url, data)
                entries = data.get("entries") or []
                item_total = max(1, sum(1 for entry in entries if entry))
                self.log(f"[zzzdown-collection] {item_total}")
                task_dir = self._task_dir(source, title, url)
                info_before = self._info_snapshot(task_dir)
                self.log(f"{source} · {kind} · {title}")
                command = [
                    *ytdlp_command(), *browser_args(self.settings),
                    "--ffmpeg-location", ffmpeg,
                    *history_args(task_dir, self.force_redownload),
                    "--continue", "--ignore-errors", "--retries", "10", "--fragment-retries", "10",
                    "--concurrent-fragments", "4", "--sleep-requests", "1",
                    "--newline", "--no-colors",
                    "--no-simulate", "--print",
                    "video:[zzzdown-item] %(playlist_index)s|%(playlist_count)s|%(id)s|%(webpage_url)s|%(title)s",
                    "--print", "after_video:[zzzdown-item-done] %(playlist_index)s|%(playlist_count)s|%(id)s|%(webpage_url)s|%(title)s",
                    "--no-quiet", "--progress",
                    "--progress-template",
                    "download:[zzzdown-progress] %(progress._percent_str)s|%(progress._downloaded_bytes_str)s|%(progress._total_bytes_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(info.resolution)s|%(info.ext)s|%(info.playlist_index)s|%(info.playlist_count)s|%(info.id)s|%(info.webpage_url)s|%(info.title)s",
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
                return_code = self._stream(command)
                self._stamp_download_task(
                    task_dir, info_before, task_id, task_started_at, number
                )
                if return_code != 0 and not self.cancelled:
                    failures += 1
                if self.cancelled:
                    break
            except Exception as exc:
                if self.cancelled:
                    break
                failures += 1
                self.log(f"ERROR: {friendly_error(str(exc))}")
        generate_global(self.library, self.library)
        return failures

    def cancel(self) -> None:
        self._cancelled.set()
        self._paused.clear()
        self._pause_gate.set()
        with self._process_lock:
            process = self.process
        if process:
            self._terminate_process(process)

    def pause(self) -> bool:
        if self.cancelled:
            return False
        self._paused.set()
        self._pause_gate.clear()
        with self._process_lock:
            process = self.process
        if process:
            self._suspend_process(process)
        return True

    def resume(self) -> bool:
        if self.cancelled:
            return False
        with self._process_lock:
            process = self.process
        if process:
            self._resume_process(process)
        self._paused.clear()
        self._pause_gate.set()
        return True


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
