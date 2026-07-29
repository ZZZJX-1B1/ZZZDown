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
    from zzzdown.app import MainWindow
    from zzzdown.config import Settings
else:
    MainWindow = Settings = None


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
            self.assertEqual(Path(window.settings.library_dir), Path(directory))
            window.close()


if __name__ == "__main__":
    unittest.main()
