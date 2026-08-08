from __future__ import annotations

import json
import os
import tempfile
import signal
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from zzzdown.config import Settings
from zzzdown.engine import DownloadEngine, aggregate_progress, friendly_error, history_args, import_library, parse_item_done_line, parse_item_line, parse_progress_line, parse_urls, safe_name, task_type
from zzzdown.indexer import build_library_html, generate_global, load_items


class CoreTests(unittest.TestCase):
    def test_windows_installer_refreshes_explorer_icons(self):
        installer = (Path(__file__).resolve().parents[1] / "installer" / "ZZZDown.iss").read_text(encoding="utf-8")
        self.assertIn("SetupIconFile=..\\src\\zzzdown\\resources\\ZZZDown.ico", installer)
        self.assertIn("ChangesAssociations=yes", installer)

    def test_parse_urls_deduplicates_and_trims_punctuation(self):
        text = "https://example.com/a， https://example.com/a\nhttps://example.com/b)"
        self.assertEqual(parse_urls(text), ["https://example.com/a", "https://example.com/b"])

    def test_parse_urls_ignores_titles_and_notes_touching_links(self):
        text = (
            "标题：https://www.bilibili.com/video/BV1abc（飞越上海，10458播）\n"
            "https://example.com/watch?id=2(子链接见下级行)\n"
            "说明 https://example.com/three，播放量 523212"
        )
        self.assertEqual(parse_urls(text), [
            "https://www.bilibili.com/video/BV1abc",
            "https://example.com/watch?id=2",
            "https://example.com/three",
        ])

    def test_structured_download_progress(self):
        progress = parse_progress_line(
            "[zzzdown-progress] 73.0%|1.28GiB|2.00GiB|8.60MiB/s|00:58|1920x1080|mp4"
        )
        self.assertEqual(progress["percent"], 73.0)
        self.assertEqual(progress["downloaded"], "1.28GiB")
        self.assertEqual(progress["resolution"], "1920x1080")
        self.assertEqual(progress["format"], "MP4")

    def test_collection_progress_tracks_all_videos(self):
        progress = parse_progress_line(
            "[zzzdown-progress] 50.0%|5MiB|10MiB|1MiB/s|00:05|1080p|mp4|3|10|BV123|https://bilibili.com/video/BV123|测试视频"
        )
        self.assertEqual(progress["item_index"], 3)
        self.assertEqual(progress["item_total"], 10)
        self.assertEqual(progress["video_title"], "测试视频")
        self.assertEqual(aggregate_progress(3, 10, 50), 25)
        self.assertEqual(aggregate_progress(3, 10, 100), 30)

    def test_item_marker_keeps_pipes_in_video_title(self):
        item = parse_item_line(
            "[zzzdown-item] 4|12|BV456|https://bilibili.com/video/BV456|标题 | 第二部分"
        )
        self.assertEqual(item["item_index"], 4)
        self.assertEqual(item["item_total"], 12)
        self.assertEqual(item["video_title"], "标题 | 第二部分")
        completed = parse_item_done_line(
            "[zzzdown-item-done] 4|12|BV456|https://bilibili.com/video/BV456|标题 | 第二部分"
        )
        self.assertEqual(completed, item)

    def test_unknown_playlist_total_is_preserved_for_metadata_fallback(self):
        item = parse_item_line("[zzzdown-item] 2|NA|BV789|https://example.com/BV789|视频")
        progress = parse_progress_line(
            "[zzzdown-progress] 10%|1MiB|10MiB|1MiB/s|00:09|1080p|mp4|2|NA|BV789|https://example.com/BV789|视频"
        )
        self.assertEqual(item["item_total"], 0)
        self.assertEqual(progress["item_total"], 0)

    def test_safe_name_removes_cross_platform_invalid_characters(self):
        self.assertEqual(safe_name(' A/B:*?"<>|  C '), "A_B_______ C")

    def test_task_type(self):
        self.assertEqual(task_type("https://space.bilibili.com/123/video", {}), "creator")
        self.assertEqual(task_type("https://youtube.com/playlist?list=abc", {}), "playlist")
        self.assertEqual(task_type("https://youtube.com/watch?v=abc", {}), "single")

    def test_chrome_cookie_lock_error_is_actionable_and_not_duplicated(self):
        message = friendly_error("ERROR: ERROR: Could not copy Chrome cookie database")
        self.assertIn("完全退出 Chrome", message)
        self.assertIn("Fully quit Chrome", message)
        self.assertNotIn("ERROR: ERROR:", message)

    def test_force_redownload_ignores_archive_and_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            args = history_args(Path(directory), True)
            self.assertEqual(args, ["--force-overwrites"])
            normal = history_args(Path(directory), False)
            self.assertEqual(normal[0], "--download-archive")

    def test_cancel_stops_before_the_next_queued_url(self):
        class CancelDuringMetadataEngine(DownloadEngine):
            def _metadata(self, url):
                self.cancel()
                return {"title": url}

            def _stream(self, command):
                raise AssertionError("download must not start after cancellation")

        with tempfile.TemporaryDirectory() as directory:
            messages = []
            engine = CancelDuringMetadataEngine(
                Settings(library_dir=directory, browser="none"),
                messages.append,
            )
            with patch("zzzdown.engine.ffmpeg_path", return_value="ffmpeg"), patch("zzzdown.engine.generate_global"):
                failures = engine.download(["https://example.com/one", "https://example.com/two"])

        self.assertTrue(engine.cancelled)
        self.assertEqual(failures, 0)
        self.assertEqual(messages, ["[1/2] https://example.com/one", "Cookies: none"])

    def test_download_command_keeps_full_logs_and_progress_with_item_markers(self):
        class CommandEngine(DownloadEngine):
            command = None

            def _metadata(self, _url):
                return {"title": "测试视频", "extractor_key": "Test"}

            def _stream(self, command):
                self.command = command
                return 0

        with tempfile.TemporaryDirectory() as directory:
            engine = CommandEngine(Settings(library_dir=directory, browser="none"))
            with patch("zzzdown.engine.ffmpeg_path", return_value="ffmpeg"), patch("zzzdown.engine.generate_global"):
                failures = engine.download(["https://example.com/video"])

        self.assertEqual(failures, 0)
        self.assertIn("--no-quiet", engine.command)
        self.assertIn("--progress", engine.command)
        self.assertIn("--progress-template", engine.command)
        printed = [engine.command[index + 1] for index, value in enumerate(engine.command) if value == "--print"]
        self.assertTrue(any("[zzzdown-item]" in value for value in printed))
        self.assertTrue(any("[zzzdown-item-done]" in value for value in printed))

    def test_download_task_order_is_written_to_new_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            info = task / "video.info.json"
            info.write_text(
                json.dumps({"id": "BV1", "playlist_index": 3}),
                encoding="utf-8",
            )
            engine = DownloadEngine(Settings(browser="none"))

            engine._stamp_download_task(task, {}, "task-123", 456000, 2)

            metadata = json.loads(info.read_text(encoding="utf-8"))["zzzdown"]
            self.assertEqual(metadata, {
                "task_id": "task-123",
                "started_at": 456000,
                "batch_index": 2,
                "item_index": 3,
            })

    def test_pause_resume_and_cancel_signal_the_active_process_group(self):
        class Process:
            pid = 4321

            @staticmethod
            def poll():
                return None

        engine = DownloadEngine(Settings(browser="none"))
        engine.process = Process()
        with patch("zzzdown.engine.os.getpgid", return_value=4321), patch("zzzdown.engine.os.killpg") as killpg:
            self.assertTrue(engine.pause())
            self.assertTrue(engine.paused)
            killpg.assert_called_with(4321, signal.SIGSTOP)

            self.assertTrue(engine.resume())
            self.assertFalse(engine.paused)
            killpg.assert_called_with(4321, signal.SIGCONT)

            engine.pause()
            engine.cancel()
            self.assertTrue(engine.cancelled)
            self.assertFalse(engine.paused)
            self.assertEqual(killpg.call_args_list[-2:], [
                unittest.mock.call(4321, signal.SIGCONT),
                unittest.mock.call(4321, signal.SIGTERM),
            ])

    def test_import_and_generate_empty_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            destination = root / "new"
            source.mkdir()
            (source / "note.txt").write_text("hello", encoding="utf-8")
            copied = import_library(source, destination)
            self.assertEqual(copied, 1)
            self.assertEqual((destination / "note.txt").read_text(encoding="utf-8"), "hello")
            index_path, count = generate_global(destination, destination)
            self.assertTrue(index_path.is_file())
            self.assertEqual(count, 0)

    def test_import_rejects_destination_inside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with self.assertRaises(ValueError):
                import_library(source, source / "nested")

    def test_library_uses_local_video_time_as_download_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "Bilibili" / "测试任务"
            task.mkdir(parents=True)
            video = task / "示例 [BV123].mp4"
            video.write_bytes(b"video")
            (task / "示例 [BV123].info.json").write_text(
                json.dumps({
                    "id": "BV123",
                    "title": "示例",
                    "upload_date": "20200102",
                    "playlist_index": 4,
                    "zzzdown": {
                        "task_id": "batch-1",
                        "started_at": 111000,
                        "batch_index": 2,
                        "item_index": 4,
                    },
                }),
                encoding="utf-8",
            )
            timestamp = datetime(2026, 8, 3, 12, 0).timestamp()
            os.utime(video, (timestamp, timestamp))

            items = load_items(root, include_collection=True)

            self.assertEqual(items[0]["date"], "2020-01-02")
            self.assertEqual(items[0]["downloadDate"], "2026-08-03")
            self.assertEqual(items[0]["downloadTimestamp"], int(timestamp * 1000))
            self.assertEqual(items[0]["downloadTaskId"], "batch-1")
            self.assertEqual(items[0]["downloadTaskStartedTimestamp"], 111000)
            self.assertEqual(items[0]["downloadBatchIndex"], 2)
            self.assertEqual(items[0]["downloadItemIndex"], 4)

    def test_english_library_translates_filters_and_confirmation(self):
        html = build_library_html(
            "ZZZDown",
            [{"site": "哔哩哔哩", "taskType": "创作者主页"}],
            "token",
            "en_US",
        )
        self.assertIn("All sources", html)
        self.assertIn("Creator profile", html)
        self.assertIn("Downloaded", html)
        self.assertIn("All dates", html)
        self.assertIn("Days 8–15", html)
        self.assertIn("Days 16–30", html)
        self.assertIn("Older", html)
        self.assertIn("Published date", html)
        self.assertIn("Download task order", html)
        self.assertIn("Show all", html)
        self.assertIn("Collapse", html)
        self.assertIn("Type CLEAR to continue", html)
        self.assertIn("confirm:'CLEAR'", html)

    def test_library_contains_download_date_filter_logic(self):
        html = build_library_html("ZZZDown", [], "token")
        self.assertIn('id="downloadDateFilters"', html)
        self.assertIn("function matchesDownloadDate", html)
        self.assertIn("第 8–15 天", html)
        self.assertIn("第 16–30 天", html)
        self.assertIn("更早", html)

    def test_library_contains_download_task_sorting(self):
        html = build_library_html("ZZZDown", [], "token")
        self.assertIn('id="sortOrder"', html)
        self.assertIn("下载任务顺序", html)
        self.assertIn("function compareDownloadTask", html)

    def test_library_download_task_filters_can_collapse(self):
        html = build_library_html("ZZZDown", [], "token")
        self.assertIn('id="taskFilterToggle"', html)
        self.assertIn("function updateTaskFilterCollapse", html)
        self.assertIn("taskFiltersCollapsed:true", html)


if __name__ == "__main__":
    unittest.main()
