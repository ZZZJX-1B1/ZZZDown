from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zzzdown.engine import import_library, parse_urls, safe_name, task_type
from zzzdown.indexer import build_library_html, generate_global


class CoreTests(unittest.TestCase):
    def test_parse_urls_deduplicates_and_trims_punctuation(self):
        text = "https://example.com/a， https://example.com/a\nhttps://example.com/b)"
        self.assertEqual(parse_urls(text), ["https://example.com/a", "https://example.com/b"])

    def test_safe_name_removes_cross_platform_invalid_characters(self):
        self.assertEqual(safe_name(' A/B:*?"<>|  C '), "A_B_______ C")

    def test_task_type(self):
        self.assertEqual(task_type("https://space.bilibili.com/123/video", {}), "creator")
        self.assertEqual(task_type("https://youtube.com/playlist?list=abc", {}), "playlist")
        self.assertEqual(task_type("https://youtube.com/watch?v=abc", {}), "single")

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

    def test_english_library_translates_filters_and_confirmation(self):
        html = build_library_html(
            "ZZZDown",
            [{"site": "哔哩哔哩", "taskType": "创作者主页"}],
            "token",
            "en_US",
        )
        self.assertIn("All sources", html)
        self.assertIn("Creator profile", html)
        self.assertIn("Type CLEAR to continue", html)
        self.assertIn("confirm:'CLEAR'", html)


if __name__ == "__main__":
    unittest.main()
