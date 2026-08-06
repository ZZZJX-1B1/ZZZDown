#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .indexer import generate_global


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
SERVICE_VERSION = 1
STATE_FILENAME = ".video-library-folders.json"


def parse_byte_range(value, size):
    """Parse one HTTP byte range and return an inclusive (start, end) pair."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", str(value or "").strip())
    if not match or size <= 0:
        raise ValueError("Invalid byte range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("Invalid byte range")
    if not start_text:
        length = int(end_text)
        if length <= 0:
            raise ValueError("Invalid byte range")
        return max(0, size - length), size - 1
    start = int(start_text)
    if start >= size:
        raise ValueError("Range starts beyond the file")
    end = min(size - 1, int(end_text)) if end_text else size - 1
    if end < start:
        raise ValueError("Invalid byte range")
    return start, end


class LibraryHandler(SimpleHTTPRequestHandler):
    server_version = "LocalVideoLibrary/1.0"

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}", flush=True)

    def allowed_origin(self):
        origin = self.headers.get("Origin")
        allowed = {
            "null",
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        return origin if origin in allowed else None

    def end_headers(self):
        origin = self.allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            return self.send_video_head(path)
        return super().send_head()

    def send_video_head(self, path):
        try:
            source = path.open("rb")
            stat = path.stat()
        except OSError:
            self.send_error(404, "File not found")
            return None
        size = stat.st_size
        requested = self.headers.get("Range")
        self._byte_range = None
        if requested:
            try:
                start, end = parse_byte_range(requested, size)
            except (TypeError, ValueError):
                source.close()
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return None
            self._byte_range = (start, end)
            source.seek(start)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            content_length = end - start + 1
        else:
            self.send_response(200)
            content_length = size
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.end_headers()
        return source

    def copyfile(self, source, outputfile):
        byte_range = getattr(self, "_byte_range", None)
        if byte_range is None:
            super().copyfile(source, outputfile)
            return
        remaining = byte_range[1] - byte_range[0] + 1
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        origin = self.headers.get("Origin")
        return (
            (origin is None or self.allowed_origin() is not None)
            and self.headers.get("X-Video-Library-Token") == self.server.token
        )

    def rebuild_indexes(self, task_dirs=()):
        generate_global(self.server.library, self.server.workspace)

    def related_files(self, video):
        base = video.with_suffix("")
        prefix = base.name + "."
        id_matches = re.findall(r"\[([^\]]+)\]", video.name)
        marker = f"[{id_matches[-1]}]" if id_matches else ""
        related = []
        for path in video.parent.iterdir():
            if not path.is_file():
                continue
            if path == video or (
                    path.suffix.lower() not in VIDEO_EXTENSIONS
                    and (path.name.startswith(prefix) or (marker and marker in path.name))):
                related.append(path)
        return related

    def load_library_state(self):
        state_path = self.server.library / STATE_FILENAME
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        folders = state.get("folders") if isinstance(state.get("folders"), list) else []
        assignments = state.get("assignments") if isinstance(state.get("assignments"), dict) else {}
        clean_folders = []
        seen = set()
        for folder in folders:
            if not isinstance(folder, dict):
                continue
            folder_id = str(folder.get("id") or "")
            name = str(folder.get("name") or "").strip()
            if not folder_id or folder_id in seen or not name:
                continue
            seen.add(folder_id)
            clean_folders.append({
                "id": folder_id,
                "name": name[:80],
                "parentId": str(folder.get("parentId")) if folder.get("parentId") else None,
            })
        valid_ids = {folder["id"] for folder in clean_folders}
        for folder in clean_folders:
            if folder["parentId"] not in valid_ids or folder["parentId"] == folder["id"]:
                folder["parentId"] = None
        clean_assignments = {
            str(path): str(folder_id)
            for path, folder_id in assignments.items()
            if str(folder_id) in valid_ids
        }
        return {"version": 1, "folders": clean_folders, "assignments": clean_assignments}

    def save_library_state(self, state):
        state_path = self.server.library / STATE_FILENAME
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(state_path)

    @staticmethod
    def folder_name(value):
        name = str(value or "").strip()
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            raise ValueError("文件夹名称应为 1–80 个字符")
        return name

    @staticmethod
    def folder_map(state):
        return {folder["id"]: folder for folder in state["folders"]}

    def validate_parent(self, state, parent_id, folder_id=None):
        parent_id = str(parent_id) if parent_id else None
        folders = self.folder_map(state)
        if parent_id and parent_id not in folders:
            raise ValueError("上级文件夹不存在")
        current = parent_id
        visited = set()
        while current:
            if current == folder_id:
                raise ValueError("不能把文件夹移动到自身或其子文件夹中")
            if current in visited:
                raise ValueError("文件夹层级异常")
            visited.add(current)
            current = folders[current].get("parentId") if current in folders else None
        return parent_id

    def handle_folder_action(self, payload):
        action = str(payload.get("action") or "")
        with self.server.state_lock:
            state = self.load_library_state()
            folders = self.folder_map(state)
            if action == "create":
                if len(state["folders"]) >= 500:
                    raise ValueError("自定义文件夹数量已达到上限")
                parent_id = self.validate_parent(state, payload.get("parentId"))
                folder = {"id": uuid.uuid4().hex, "name": self.folder_name(payload.get("name")), "parentId": parent_id}
                state["folders"].append(folder)
            else:
                folder_id = str(payload.get("folderId") or "")
                if folder_id not in folders:
                    raise ValueError("文件夹不存在")
                folder = folders[folder_id]
                if action == "rename":
                    folder["name"] = self.folder_name(payload.get("name"))
                elif action == "move":
                    folder["parentId"] = self.validate_parent(state, payload.get("parentId"), folder_id)
                elif action == "delete":
                    parent_id = folder.get("parentId")
                    state["folders"] = [item for item in state["folders"] if item["id"] != folder_id]
                    for item in state["folders"]:
                        if item.get("parentId") == folder_id:
                            item["parentId"] = parent_id
                    state["assignments"] = {
                        path: assigned for path, assigned in state["assignments"].items()
                        if assigned != folder_id
                    }
                else:
                    raise ValueError("不支持的文件夹操作")
            self.save_library_state(state)
        self.send_json(200, {"status": "ok", "state": state})

    def validate_video_path(self, value):
        relative = Path(str(value or ""))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError("视频路径无效")
        video = (self.server.library / relative).resolve()
        video.relative_to(self.server.library)
        if not video.is_file():
            raise FileNotFoundError(f"视频已不存在：{relative.name}")
        return relative, video

    def handle_classify(self, payload):
        requested = payload.get("videoPaths")
        if not isinstance(requested, list) or not requested or len(requested) > 500:
            raise ValueError("待分类视频数量异常")
        with self.server.state_lock:
            state = self.load_library_state()
            folder_id = str(payload.get("folderId")) if payload.get("folderId") else None
            if folder_id and folder_id not in self.folder_map(state):
                raise ValueError("目标文件夹不存在")
            paths = []
            for value in requested:
                relative, _ = self.validate_video_path(value)
                path = relative.as_posix()
                if path not in paths:
                    paths.append(path)
            for path in paths:
                if folder_id:
                    state["assignments"][path] = folder_id
                else:
                    state["assignments"].pop(path, None)
            self.save_library_state(state)
        self.send_json(200, {"status": "ok", "state": state, "updated": len(paths)})

    def trash_items(self):
        trash_root = self.server.library / "视频库回收站"
        items = []
        if not trash_root.exists():
            return items
        for video in trash_root.rglob("*"):
            if not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            relative = video.relative_to(trash_root)
            if len(relative.parts) < 3:
                continue
            deleted_stamp = relative.parts[0]
            original = Path(*relative.parts[1:])
            related = self.related_files(video)
            info_path = next((path for path in related if path.name.endswith(".info.json")), None)
            try:
                info = json.loads(info_path.read_text(encoding="utf-8")) if info_path else {}
            except (OSError, json.JSONDecodeError):
                info = {}
            image = next((
                path for path in related
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".avif"}
            ), None)
            try:
                deleted_at = datetime.strptime(deleted_stamp, "%Y-%m-%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                deleted_at = deleted_stamp
            items.append({
                "title": str(info.get("title") or video.stem),
                "id": str(info.get("id") or ""),
                "task": original.parent.name,
                "deletedAt": deleted_at,
                "fileSize": video.stat().st_size,
                "trashVideoPath": video.relative_to(self.server.library).as_posix(),
                "originalPath": original.as_posix(),
                "thumbnail": "/" + quote(image.relative_to(self.server.library).as_posix(), safe="/") if image else "",
            })
        items.sort(key=lambda item: item["deletedAt"], reverse=True)
        return items

    def restore_items(self, payload):
        requested = payload.get("trashVideoPaths")
        if not isinstance(requested, list):
            requested = [payload.get("trashVideoPath")]
        if not requested or len(requested) > 500:
            raise ValueError("待还原视频数量异常")

        trash_root = (self.server.library / "视频库回收站").resolve()
        plans = []
        planned_auxiliary_dirs = set()
        for value in requested:
            relative = Path(str(value or ""))
            if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("回收站路径无效")
            video = (self.server.library / relative).resolve()
            video.relative_to(trash_root)
            trash_relative = video.relative_to(trash_root)
            if len(trash_relative.parts) < 3 or not video.is_file():
                raise FileNotFoundError(f"回收站中找不到：{relative.name}")
            original_video = self.server.library / Path(*trash_relative.parts[1:])
            related = self.related_files(video)
            destinations = [(path, original_video.parent / path.name) for path in related]
            auxiliary_dir = video.parent / ".task-folder-files"
            auxiliary_destinations = []
            if auxiliary_dir.is_dir() and auxiliary_dir not in planned_auxiliary_dirs:
                planned_auxiliary_dirs.add(auxiliary_dir)
                auxiliary_destinations = [
                    (path, original_video.parent / path.name)
                    for path in auxiliary_dir.iterdir()
                ]
                destinations.extend(auxiliary_destinations)
            conflicts = [destination for _, destination in destinations if destination.exists()]
            if conflicts:
                raise FileExistsError(f"原位置已有同名文件，未覆盖：{conflicts[0].name}")
            plans.append((video.parent, original_video.parent, destinations, auxiliary_dir))

        restored = []
        task_dirs = set()
        for source_dir, destination_dir, destinations, auxiliary_dir in plans:
            destination_dir.mkdir(parents=True, exist_ok=True)
            task_dirs.add(destination_dir)
            for source, destination in destinations:
                if source.exists():
                    shutil.move(str(source), str(destination))
                    restored.append(str(destination.relative_to(self.server.library)))
            if auxiliary_dir.is_dir() and not any(auxiliary_dir.iterdir()):
                auxiliary_dir.rmdir()
            current = source_dir
            while current != trash_root and current.is_dir() and not any(current.iterdir()):
                current.rmdir()
                current = current.parent
        self.rebuild_indexes(task_dirs)
        self.send_json(200, {"status": "ok", "restored": restored})

    def do_OPTIONS(self):
        if not self.allowed_origin():
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Video-Library-Token")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            self.send_json(200, {"status": "ok", "version": SERVICE_VERSION})
            return
        if self.path == "/api/trash-items":
            if not self.authorized():
                self.send_json(403, {"error": "视频库管理授权已失效，请重新打开全局视频库"})
                return
            self.send_json(200, {"items": self.trash_items()})
            return
        if self.path == "/api/library-state":
            if not self.authorized():
                self.send_json(403, {"error": "视频库管理授权已失效，请重新打开全局视频库"})
                return
            with self.server.state_lock:
                state = self.load_library_state()
            self.send_json(200, state)
            return
        if urlparse(self.path).path in {"", "/"}:
            generate_global(self.server.library, self.server.workspace)
            self.path = "/%E5%85%A8%E5%B1%80%E8%A7%86%E9%A2%91%E5%BA%93.html"
        path_parts = Path(unquote(urlparse(self.path).path)).parts
        if any(part.startswith(".") for part in path_parts):
            self.send_error(404)
            return
        super().do_GET()

    def do_POST(self):
        if self.path not in {"/api/trash", "/api/restore", "/api/empty-trash", "/api/folders", "/api/classify", "/api/reveal"}:
            self.send_error(404)
            return
        if not self.allowed_origin():
            self.send_json(403, {"error": "请从本地视频库页面执行清理"})
            return
        if self.headers.get("X-Video-Library-Token") != self.server.token:
            self.send_json(403, {"error": "视频库管理授权已失效，请重新打开全局视频库"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 524288:
                raise ValueError("请求大小异常")
            payload = json.loads(self.rfile.read(length))
            if self.path == "/api/folders":
                self.handle_folder_action(payload)
                return
            if self.path == "/api/classify":
                self.handle_classify(payload)
                return
            if self.path == "/api/reveal":
                _, video = self.validate_video_path(payload.get("videoPath"))
                if sys.platform == "darwin":
                    command = ["/usr/bin/open", "-R", str(video)]
                elif sys.platform == "win32":
                    command = ["explorer", "/select,", str(video)]
                else:
                    command = ["xdg-open", str(video.parent)]
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json(200, {"status": "ok"})
                return
            if self.path == "/api/empty-trash":
                if payload.get("confirm") not in {"清空", "CLEAR"}:
                    raise ValueError("请输入“清空”确认永久删除")
                trash_root = (self.server.library / "视频库回收站").resolve()
                if trash_root.parent != self.server.library or trash_root.name != "视频库回收站":
                    raise ValueError("回收站路径校验失败")
                trash_entries = self.trash_items()
                total_size = sum(item["fileSize"] for item in trash_entries)
                with self.server.state_lock:
                    state = self.load_library_state()
                    for item in trash_entries:
                        state["assignments"].pop(item["originalPath"], None)
                    self.save_library_state(state)
                if trash_root.exists():
                    shutil.rmtree(trash_root)
                self.send_json(200, {
                    "status": "ok", "deletedVideos": len(trash_entries), "deletedBytes": total_size
                })
                return
            if self.path == "/api/restore":
                self.restore_items(payload)
                return
            requested = payload.get("videoPaths")
            if not isinstance(requested, list):
                requested = [payload.get("videoPath")]
            if not requested or len(requested) > 500:
                raise ValueError("待清理视频数量异常")
            videos = []
            for value in requested:
                _, video = self.validate_video_path(value)
                if video not in videos:
                    videos.append(video)

            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            trash_root = self.server.library / "视频库回收站" / stamp
            moved = []
            task_dirs = set()
            for video in videos:
                task_dirs.add(video.parent)
                related = self.related_files(video)
                relative_parent = video.parent.relative_to(self.server.library)
                trash_dir = trash_root / relative_parent
                trash_dir.mkdir(parents=True, exist_ok=True)
                for path in related:
                    destination = trash_dir / path.name
                    shutil.move(str(path), str(destination))
                    moved.append(str(path.relative_to(self.server.library)))

                # 最后一个视频移出后，将整个任务的辅助文件一并收起，
                # 避免留下空任务文件夹。
                remaining_videos = [
                    path for path in video.parent.iterdir()
                    if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
                ]
                if not remaining_videos:
                    auxiliary_dir = trash_dir / ".task-folder-files"
                    auxiliary_dir.mkdir(parents=True, exist_ok=True)
                    for path in list(video.parent.iterdir()):
                        shutil.move(str(path), str(auxiliary_dir / path.name))
                    video.parent.rmdir()

            self.rebuild_indexes(task_dir for task_dir in task_dirs if task_dir.exists())
            self.send_json(200, {"status": "ok", "moved": moved, "trash": str(trash_root)})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})


def start_server(library: Path, workspace: Path, port: int = 8765):
    library = Path(library).resolve()
    workspace = Path(workspace).resolve()
    generate_global(library, workspace)
    token_path = library / ".video-library-token"
    handler = partial(LibraryHandler, directory=str(library))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.library = library
    server.workspace = workspace
    server.token = token_path.read_text(encoding="utf-8").strip()
    server.state_lock = threading.Lock()
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--index-script", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    start_server(args.library, args.workspace, args.port)


if __name__ == "__main__":
    main()
