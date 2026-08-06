from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:
    Qt = QApplication = None

if QApplication is not None:
    from zzzdown.app import MainWindow, open_library_url
    from zzzdown.config import Settings
else:
    MainWindow = Settings = open_library_url = None


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_keeps_native_frame_and_core_views(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                language="zh_CN",
                library_dir=directory,
                browser="none",
                disclaimer_accepted=True,
            )
            with patch("zzzdown.app.load_settings", return_value=settings):
                window = MainWindow()
            self.assertFalse(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertEqual(window.tabs.count(), 2)
            self.assertIsNotNone(window.urls)
            self.assertIsNotNone(window.log)
            self.assertIsNotNone(window.task_table)
            self.assertIsNotNone(window.pause_button)
            self.assertFalse(window.pause_button.isEnabled())
            window._set_download_state("paused")
            self.assertEqual(window.pause_button.text(), "继续")
            self.assertEqual(window.log_status.text(), "已暂停")
            self.assertEqual(window.pause_button.property("paused"), True)
            window._set_download_state("stopping")
            self.assertEqual(window.stop_button.text(), "正在停止…")
            self.assertFalse(window.stop_button.isEnabled())
            window._reset_progress(["https://example.com/playlist"])
            window.current_task = 1
            window._update_progress(
                "[zzzdown-progress] 50%|5MiB|10MiB|1MiB/s|00:05|1080p|mp4|3|10|id|https://example.com/video|视频"
            )
            self.assertEqual(window.current_text.text(), "50%")
            self.assertEqual(window.overall_text.text(), "25%")
            window._update_progress("ERROR: video unavailable")
            self.assertEqual(window.task_table.rowCount(), 2)
            self.assertEqual(window.task_table.item(1, 1).text(), "未完成")
            window.task_table.selectRow(1)
            self.assertTrue(window.queue_open_button.isEnabled())
            window.copy_queue_link()
            self.assertEqual(QApplication.clipboard().text(), "https://example.com/video")
            window.copy_all_failed_links()
            self.assertEqual(QApplication.clipboard().text(), "https://example.com/video")
            window._reset_progress(["https://example.com/playlist"])
            window.current_task = 1
            window._update_progress("[zzzdown-collection] 5")
            window._update_progress(
                "[zzzdown-progress] 50%|5MiB|10MiB|1MiB/s|00:05|1080p|mp4|2|NA|id2|https://example.com/2|第二个视频"
            )
            self.assertEqual(window.current_text.text(), "50%")
            self.assertEqual(window.overall_text.text(), "30%")
            self.assertEqual(Path(window.settings.library_dir), Path(directory))
            window.close()

    def test_library_prefers_chrome_and_falls_back_to_default_browser(self):
        completed = type("Completed", (), {"returncode": 0})()
        with patch("zzzdown.app.sys.platform", "darwin"), \
                patch("zzzdown.app.detected_browsers", return_value={"chrome"}), \
                patch("zzzdown.app.subprocess.run", return_value=completed) as run, \
                patch("zzzdown.app.QDesktopServices.openUrl") as fallback:
            self.assertTrue(open_library_url("http://127.0.0.1:8765/"))
            self.assertIn("Google Chrome", run.call_args.args[0][2])
            fallback.assert_not_called()

        with patch("zzzdown.app.detected_browsers", return_value=set()), \
                patch("zzzdown.app.QDesktopServices.openUrl", return_value=True) as fallback:
            self.assertTrue(open_library_url("http://127.0.0.1:8765/"))
            fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
