from __future__ import annotations

import re
import socket
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import load_settings, save_settings
from .engine import DownloadEngine, import_library, parse_progress_line, parse_urls
from .i18n import translator
from .indexer import generate_global
from .library_server import start_server
from .paths import resource_root
from .tools import detected_browsers, update_ytdlp


APP_STYLE = """
QWidget { color: #172033; font-size: 14px; }
QMainWindow, QWidget#appRoot, QStackedWidget { background: #f6f8fc; }
QFrame#header { background: #ffffff; border-bottom: 1px solid #e3e8f0; }
QLabel#brand { color: #111827; font-size: 22px; font-weight: 700; }
QFrame#tabStrip { background: #ffffff; border-bottom: 1px solid #e3e8f0; }
QPushButton#tabButton {
    min-width: 104px; min-height: 52px; padding: 0 12px;
    color: #65718a; background: #ffffff; border: 0; border-radius: 0;
    border-bottom: 3px solid transparent; font-weight: 500;
}
QPushButton#tabButton:hover { color: #172033; background: #ffffff; }
QPushButton#tabButton:checked {
    color: #172033; background: #ffffff; border-bottom-color: #3568f0; font-weight: 600;
}
QFrame#card {
    background: #ffffff; border: 1px solid #dfe5ee; border-radius: 9px;
}
QFrame#metricCard { background: #ffffff; border: 1px solid #dfe5ee; border-radius: 7px; }
QLabel#sectionTitle { font-size: 16px; font-weight: 650; }
QLabel#hint, QLabel#metricHint, QLabel#summary { color: #687386; font-size: 12px; }
QLabel#metricValue { font-size: 16px; font-weight: 600; }
QLabel#statusChip {
    color: #2456a6; background: #eef4ff; border-radius: 12px;
    padding: 4px 10px; font-size: 12px; font-weight: 600;
}
QTextEdit, QLineEdit, QComboBox {
    background: #fbfcfe; border: 1px solid #d8e0eb; border-radius: 7px;
    padding: 8px; selection-background-color: #dce8ff;
}
QTextEdit:focus, QLineEdit:focus, QComboBox:focus { border-color: #7aa2f7; }
QComboBox { padding-right: 38px; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 36px; border: 0; background: transparent;
}
QComboBox::down-arrow {
    image: url("__COMBO_ARROW__"); width: 12px; height: 8px;
}
QPushButton {
    min-height: 38px; padding: 0 18px; border: 1px solid #d8e0eb;
    border-radius: 7px; background: #ffffff; color: #263449; font-weight: 550;
}
QPushButton:hover { background: #f4f7fb; border-color: #bfc9d8; }
QPushButton:pressed { background: #eaf0f8; }
QPushButton:disabled { color: #a2abba; background: #f4f6f9; border-color: #e4e8ee; }
QPushButton#primaryButton { color: #ffffff; background: #3568f0; border-color: #3568f0; }
QPushButton#primaryButton:hover { background: #2457dc; }
QPushButton#stopButton { color: #687386; background: #f7f9fc; }
QCheckBox { spacing: 8px; color: #4e5b70; }
QCheckBox::indicator { width: 16px; height: 16px; }
QProgressBar { height: 7px; border: 0; border-radius: 3px; background: #e9eef6; }
QProgressBar::chunk { border-radius: 3px; background: #2faee9; }
QTableWidget {
    background: #ffffff; alternate-background-color: #fbfcfe; border: 1px solid #e0e6ef;
    border-radius: 7px; gridline-color: #e7ebf2; selection-background-color: #edf3ff;
    selection-color: #172033; outline: 0;
}
QTableWidget::item { padding: 8px; }
QHeaderView::section {
    background: #f7f9fc; color: #687386; border: 0; border-bottom: 1px solid #e1e6ef;
    padding: 7px; font-size: 12px; font-weight: 600;
}
QScrollBar:vertical { width: 10px; margin: 2px; background: transparent; }
QScrollBar::handle:vertical { min-height: 28px; border-radius: 4px; background: #cbd3df; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class AppSignals(QObject):
    log = Signal(str)
    download_done = Signal(int, bool)
    utility_done = Signal(object, str, str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.t = translator(self.settings.language)
        self.engine: DownloadEngine | None = None
        self.library_port: int | None = None
        self.browser_values: list[str] = []
        self.task_urls: list[str] = []
        self.current_task = 0
        self.signals = AppSignals(self)
        self.signals.log.connect(self._append_log)
        self.signals.download_done.connect(self._download_done)
        self.signals.utility_done.connect(self._utility_done)
        self._build_ui()
        self._load_values()
        self.retranslate()

    def _build_ui(self) -> None:
        self.setMinimumSize(1080, 680)
        self.resize(1280, 820)
        arrow = (resource_root() / "resources" / "chevron-down.svg").as_posix()
        self.setStyleSheet(APP_STYLE.replace("__COMBO_ARROW__", arrow))

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 11, 24, 11)
        logo = QLabel()
        logo.setFixedSize(42, 34)
        logo.setPixmap(QIcon(str(resource_root() / "resources" / "ZZZDown.png")).pixmap(38, 38))
        self.header_title = QLabel("ZZZDown")
        self.header_title.setObjectName("brand")
        header_layout.addWidget(logo)
        header_layout.addWidget(self.header_title)
        header_layout.addStretch()
        root_layout.addWidget(header)

        tab_strip = QFrame()
        tab_strip.setObjectName("tabStrip")
        tab_strip.setFixedHeight(56)
        tab_strip_layout = QHBoxLayout(tab_strip)
        tab_strip_layout.setContentsMargins(24, 0, 24, 0)
        tab_strip_layout.setSpacing(12)
        self.download_tab_button = QPushButton()
        self.settings_tab_button = QPushButton()
        for button in (self.download_tab_button, self.settings_tab_button):
            button.setObjectName("tabButton")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFixedHeight(56)
            tab_strip_layout.addWidget(button)
        self.download_tab_button.setChecked(True)
        tab_strip_layout.addStretch()
        root_layout.addWidget(tab_strip)

        self.tabs = QStackedWidget()
        self.download_page = QWidget()
        self.settings_page = QWidget()
        self.tabs.addWidget(self.download_page)
        self.tabs.addWidget(self.settings_page)
        self.download_tab_button.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.settings_tab_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.tabs.currentChanged.connect(self._sync_tab_buttons)
        root_layout.addWidget(self.tabs, 1)
        self._build_download_page()
        self._build_settings_page()

    @staticmethod
    def _card(parent: QWidget | None = None) -> QFrame:
        card = QFrame(parent)
        card.setObjectName("card")
        return card

    @staticmethod
    def _section_label() -> QLabel:
        label = QLabel()
        label.setObjectName("sectionTitle")
        return label

    def _build_download_page(self) -> None:
        layout = QHBoxLayout(self.download_page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(12)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        input_card = self._card()
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(18, 15, 18, 15)
        input_layout.setSpacing(8)
        self.url_label = self._section_label()
        self.url_hint = QLabel()
        self.url_hint.setObjectName("hint")
        self.urls = QTextEdit()
        self.urls.setAcceptRichText(False)
        self.urls.setMinimumHeight(100)
        self.urls.setMaximumHeight(135)
        self.force_check = QCheckBox()
        input_layout.addWidget(self.url_label)
        input_layout.addWidget(self.url_hint)
        input_layout.addWidget(self.urls)
        input_layout.addWidget(self.force_check)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.start_button = QPushButton()
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_button = QPushButton()
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.setEnabled(False)
        self.library_button = QPushButton()
        self.library_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.start_button.clicked.connect(self.start_download)
        self.stop_button.clicked.connect(self.stop_download)
        self.library_button.clicked.connect(self.open_library)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        actions.addWidget(self.library_button)
        input_layout.addLayout(actions)
        left_layout.addWidget(input_card)

        log_card = self._card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(18, 13, 18, 14)
        log_header = QHBoxLayout()
        self.log_title = self._section_label()
        self.log_status = QLabel()
        self.log_status.setObjectName("statusChip")
        log_header.addWidget(self.log_title)
        log_header.addStretch()
        log_header.addWidget(self.log_status)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Menlo" if sys.platform == "darwin" else "Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.log.setFont(mono)
        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log, 1)
        left_layout.addWidget(log_card, 1)

        progress_card = self._build_progress_panel()
        layout.addWidget(left, 5)
        layout.addWidget(progress_card, 3)

    def _build_progress_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 14)
        layout.setSpacing(9)
        self.progress_title = self._section_label()
        self.task_count_label = QLabel("0 / 0")
        layout.addWidget(self.progress_title)
        layout.addWidget(self.task_count_label)
        self.overall_label, self.overall_text, self.overall_bar = self._progress_row(layout)
        self.current_label, self.current_text, self.current_bar = self._progress_row(layout)

        metrics = QGridLayout()
        metrics.setContentsMargins(0, 4, 0, 4)
        metrics.setSpacing(9)
        self.metric_widgets: dict[str, tuple[QLabel, QLabel]] = {}
        for row, column, key in (
            (0, 0, "speed"), (0, 1, "eta"), (1, 0, "size"), (1, 1, "format")
        ):
            metric = QFrame()
            metric.setObjectName("metricCard")
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(12, 9, 12, 9)
            metric_layout.setSpacing(2)
            value = QLabel("—")
            value.setObjectName("metricValue")
            hint = QLabel()
            hint.setObjectName("metricHint")
            metric_layout.addWidget(value)
            metric_layout.addWidget(hint)
            metrics.addWidget(metric, row, column)
            self.metric_widgets[key] = (value, hint)
        layout.addLayout(metrics)

        self.queue_title = self._section_label()
        layout.addWidget(self.queue_title)
        self.task_table = QTableWidget(0, 3)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.verticalHeader().setDefaultSectionSize(49)
        header = self.task_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.task_table, 1)
        self.queue_summary = QLabel("0")
        self.queue_summary.setObjectName("summary")
        layout.addWidget(self.queue_summary)
        return card

    @staticmethod
    def _progress_row(layout: QVBoxLayout) -> tuple[QLabel, QLabel, QProgressBar]:
        labels = QHBoxLayout()
        title = QLabel()
        value = QLabel("0%")
        labels.addWidget(title)
        labels.addStretch()
        labels.addWidget(value)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        layout.addLayout(labels)
        layout.addWidget(bar)
        return title, value, bar

    def _build_settings_page(self) -> None:
        page_layout = QVBoxLayout(self.settings_page)
        page_layout.setContentsMargins(28, 20, 28, 28)
        form = self._card()
        form.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        grid = QGridLayout(form)
        grid.setContentsMargins(22, 20, 22, 20)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(13)
        grid.setColumnStretch(1, 1)

        self.browser_label = QLabel()
        self.browser = QComboBox()
        self.location_label = QLabel()
        self.location_edit = QLineEdit()
        self.choose_button = QPushButton()
        self.proxy_label = QLabel()
        self.proxy_edit = QLineEdit()
        self.direct = QCheckBox()
        self.language_label = QLabel()
        self.language = QComboBox()
        self.language.addItems(("简体中文", "English"))
        self.language.currentIndexChanged.connect(self.preview_language)
        self.choose_button.clicked.connect(self.choose_location)
        grid.addWidget(self.browser_label, 0, 0)
        grid.addWidget(self.browser, 0, 1, 1, 2)
        grid.addWidget(self.location_label, 1, 0)
        grid.addWidget(self.location_edit, 1, 1)
        grid.addWidget(self.choose_button, 1, 2)
        grid.addWidget(self.proxy_label, 2, 0)
        grid.addWidget(self.proxy_edit, 2, 1, 1, 2)
        grid.addWidget(self.direct, 3, 1, 1, 2)
        grid.addWidget(self.language_label, 4, 0)
        grid.addWidget(self.language, 4, 1, 1, 2)
        page_layout.addWidget(form)

        actions = QHBoxLayout()
        self.save_button = QPushButton()
        self.save_button.setObjectName("primaryButton")
        self.update_button = QPushButton()
        self.import_button = QPushButton()
        self.save_button.clicked.connect(self.save_preferences)
        self.update_button.clicked.connect(self.update_engine)
        self.import_button.clicked.connect(self.import_existing)
        actions.addWidget(self.save_button)
        actions.addWidget(self.update_button)
        actions.addWidget(self.import_button)
        actions.addStretch()
        page_layout.addLayout(actions)
        page_layout.addStretch()

    def _browser_options(self) -> tuple[list[str], list[str]]:
        detected = detected_browsers()
        keys = ["chrome", "edge", "firefox", "none"]
        labels = ["Chrome", "Microsoft Edge", "Firefox", self.t("browser_none")]
        return keys, [f"✓ {label}" if key in detected else label for key, label in zip(keys, labels)]

    def _load_values(self) -> None:
        self.location_edit.setText(self.settings.library_dir)
        self.proxy_edit.setText(self.settings.proxy)
        self.direct.setChecked(self.settings.direct_connection)
        self.language.blockSignals(True)
        self.language.setCurrentIndex(0 if self.settings.language == "zh_CN" else 1)
        self.language.blockSignals(False)

    def retranslate(self) -> None:
        self.setWindowTitle(self.t("title"))
        self.download_tab_button.setText(self.t("download"))
        self.settings_tab_button.setText(self.t("settings"))
        self.url_label.setText(self.t("urls"))
        self.url_hint.setText(self.t("url_hint"))
        self.force_check.setText(self.t("force_redownload"))
        self.start_button.setText(self.t("start"))
        self.stop_button.setText(self.t("stop"))
        self.library_button.setText(self.t("library"))
        self.log_title.setText(self.t("activity"))
        if not self.start_button.isEnabled():
            self.log_status.setText(self.t("running"))
        elif not self.log_status.text():
            self.log_status.setText(self.t("idle"))
        self.progress_title.setText(self.t("task_progress"))
        self.overall_label.setText(self.t("overall_progress"))
        self.current_label.setText(self.t("current_video"))
        self.queue_title.setText(self.t("task_queue"))
        self.metric_widgets["speed"][1].setText(self.t("speed"))
        self.metric_widgets["eta"][1].setText(self.t("eta"))
        self.metric_widgets["size"][1].setText(self.t("downloaded_size"))
        self.metric_widgets["format"][1].setText(self.t("quality_format"))
        self.task_table.setHorizontalHeaderLabels((self.t("current_video"), self.t("status"), self.t("progress")))
        self.browser_label.setText(self.t("browser"))
        self.location_label.setText(self.t("location"))
        self.proxy_label.setText(self.t("proxy"))
        self.language_label.setText(self.t("language"))
        self.choose_button.setText(self.t("choose"))
        self.direct.setText(self.t("direct"))
        self.save_button.setText(self.t("save"))
        self.update_button.setText(self.t("update"))
        self.import_button.setText(self.t("import"))
        current = self.settings.browser
        self.browser_values, labels = self._browser_options()
        self.browser.clear()
        self.browser.addItems(labels)
        self.browser.setCurrentIndex(self.browser_values.index(current) if current in self.browser_values else 0)
        self._refresh_queue_text()

    def _sync_tab_buttons(self, index: int) -> None:
        self.download_tab_button.setChecked(index == 0)
        self.settings_tab_button.setChecked(index == 1)

    def preview_language(self) -> None:
        self.t = translator("zh_CN" if self.language.currentIndex() == 0 else "en_US")
        self.retranslate()

    def save_preferences(self) -> None:
        index = self.browser.currentIndex()
        self.settings.browser = self.browser_values[index] if index >= 0 else "none"
        self.settings.language = "zh_CN" if self.language.currentIndex() == 0 else "en_US"
        self.settings.library_dir = self.location_edit.text().strip()
        self.settings.proxy = self.proxy_edit.text().strip()
        self.settings.direct_connection = self.direct.isChecked()
        library = Path(self.settings.library_dir).expanduser()
        library.mkdir(parents=True, exist_ok=True)
        (library / ".zzzdown-language").write_text(self.settings.language + "\n", encoding="utf-8")
        save_settings(self.settings)

    def choose_location(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, self.t("location"), self.location_edit.text())
        if selected:
            self.location_edit.setText(selected)

    def _append_log(self, message: str) -> None:
        self._update_progress(message)
        lowered = message.lower()
        color = "#d13c48" if "error" in lowered else "#20a464" if "completed" in lowered or "任务完成" in message else "#3568f0" if message.startswith("[") else "#263449"
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(message + "\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _reset_progress(self, urls: list[str]) -> None:
        self.task_urls = urls
        self.current_task = 0
        self.task_count_label.setText(f"0 / {len(urls)}")
        self._set_bar(self.overall_bar, self.overall_text, 0)
        self._set_bar(self.current_bar, self.current_text, 0)
        for key in self.metric_widgets:
            self.metric_widgets[key][0].setText("—")
        self.task_table.setRowCount(len(urls))
        for row, url in enumerate(urls):
            title = re.sub(r"^https?://(?:www\.)?", "", url).split("?")[0][:52]
            self.task_table.setItem(row, 0, QTableWidgetItem(title))
            self.task_table.setItem(row, 1, QTableWidgetItem(self.t("waiting")))
            self.task_table.setItem(row, 2, QTableWidgetItem("0%"))
        self.queue_summary.setText(self.t("task_total").format(count=len(urls)))

    def _refresh_queue_text(self) -> None:
        if not self.task_urls:
            self.queue_summary.setText(self.t("task_total").format(count=0))
            return
        for row in range(self.task_table.rowCount()):
            if row + 1 < self.current_task:
                status = self.t("completed")
            elif row + 1 == self.current_task:
                status = self.t("downloading")
            else:
                status = self.t("waiting")
            self.task_table.item(row, 1).setText(status)
        self.queue_summary.setText(self.t("task_total").format(count=len(self.task_urls)))

    @staticmethod
    def _set_bar(bar: QProgressBar, label: QLabel, percent: float) -> None:
        percent = max(0.0, min(100.0, percent))
        bar.setValue(round(percent * 10))
        label.setText(f"{percent:.0f}%")

    def _update_progress(self, line: str) -> None:
        header = re.match(r"\[(\d+)/(\d+)\]\s+(https?://\S+)", line)
        if header:
            self.current_task, total = int(header.group(1)), int(header.group(2))
            self.task_count_label.setText(f"{self.current_task} / {total}")
            self._set_bar(self.current_bar, self.current_text, 0)
            self._set_bar(self.overall_bar, self.overall_text, (self.current_task - 1) / max(1, total) * 100)
            for key in self.metric_widgets:
                self.metric_widgets[key][0].setText("—")
            self._refresh_queue_text()
            row = self.current_task - 1
            if 0 <= row < self.task_table.rowCount():
                self.task_table.selectRow(row)
                self.task_table.scrollToItem(self.task_table.item(row, 0))
            return

        structured = parse_progress_line(line)
        if structured:
            percent = float(structured["percent"])
            self._set_current_progress(percent)
            self.metric_widgets["size"][0].setText(f'{structured["downloaded"]} / {structured["total"]}')
            self.metric_widgets["speed"][0].setText(str(structured["speed"]))
            self.metric_widgets["eta"][0].setText(str(structured["eta"]))
            resolution, file_format = str(structured["resolution"]), str(structured["format"])
            self.metric_widgets["format"][0].setText(f"{resolution} · {file_format}" if resolution != "—" else file_format)
            return

        identity = re.match(r"(.+?)\s+·\s+(creator|playlist|single)\s+·\s+(.+)", line)
        if identity and 0 < self.current_task <= self.task_table.rowCount():
            self.task_table.item(self.current_task - 1, 0).setText(identity.group(3)[:52])

        fallback = re.search(
            r"\[download\]\s+([\d.]+)%.*?of\s+~?\s*([^\s]+)(?:\s+at\s+([^\s]+))?(?:\s+ETA\s+([^\s]+))?",
            line,
            re.IGNORECASE,
        )
        if fallback:
            self._set_current_progress(float(fallback.group(1)))
            self.metric_widgets["size"][0].setText(fallback.group(2))
            self.metric_widgets["speed"][0].setText(fallback.group(3) or "—")
            self.metric_widgets["eta"][0].setText(fallback.group(4) or "—")
        destination = re.search(r"\[download\] Destination:\s+(.+)", line)
        if destination:
            suffix = Path(destination.group(1)).suffix.lstrip(".").upper()
            if suffix:
                self.metric_widgets["format"][0].setText(suffix)

    def _set_current_progress(self, percent: float) -> None:
        self._set_bar(self.current_bar, self.current_text, percent)
        total = max(1, len(self.task_urls))
        overall = ((max(1, self.current_task) - 1) + percent / 100) / total * 100
        self._set_bar(self.overall_bar, self.overall_text, overall)
        row = self.current_task - 1
        if 0 <= row < self.task_table.rowCount():
            self.task_table.item(row, 1).setText(self.t("downloading"))
            self.task_table.item(row, 2).setText(f"{percent:.0f}%")

    def start_download(self) -> None:
        self.save_preferences()
        urls = parse_urls(self.urls.toPlainText())
        if not urls:
            QMessageBox.information(self, self.t("title"), self.t("urls"))
            return
        self.log.clear()
        self._reset_progress(urls)
        self.log_status.setText(self.t("running"))
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.engine = DownloadEngine(
            self.settings,
            self.signals.log.emit,
            force_redownload=self.force_check.isChecked(),
        )
        threading.Thread(target=self._download_worker, args=(urls, self.engine), daemon=True).start()

    def _download_worker(self, urls: list[str], engine: DownloadEngine) -> None:
        try:
            failures = engine.download(urls)
        except Exception as exc:
            self.signals.log.emit(f"ERROR: {exc}")
            failures = 1
        self.signals.download_done.emit(failures, engine.cancelled)

    def _download_done(self, failures: int, cancelled: bool) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.force_check.setChecked(False)
        self.log_status.setText(self.t("canceled") if cancelled else self.t("failed") if failures else self.t("done"))
        if not failures and not cancelled:
            self._set_bar(self.overall_bar, self.overall_text, 100)
            self._set_bar(self.current_bar, self.current_text, 100)
            for row in range(self.task_table.rowCount()):
                self.task_table.item(row, 1).setText(self.t("completed"))
                self.task_table.item(row, 2).setText("100%")

    def stop_download(self) -> None:
        if self.engine:
            self.engine.cancel()
            self.stop_button.setEnabled(False)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def open_library(self) -> None:
        self.save_preferences()
        library = Path(self.settings.library_dir)
        generate_global(library, library)
        if self.library_port is None:
            self.library_port = self._free_port()
            threading.Thread(target=start_server, args=(library, library, self.library_port), daemon=True).start()
        QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{self.library_port}/"))

    def _run_utility(self, button: QPushButton, function, success: str) -> None:
        button.setEnabled(False)

        def worker() -> None:
            try:
                function(self.signals.log.emit)
                error = ""
            except Exception as exc:
                error = str(exc)
            self.signals.utility_done.emit(button, error, success)

        threading.Thread(target=worker, daemon=True).start()

    def _utility_done(self, button: QPushButton, error: str, success: str) -> None:
        button.setEnabled(True)
        if error:
            QMessageBox.critical(self, self.t("title"), error)
        else:
            QMessageBox.information(self, self.t("title"), success)

    def update_engine(self) -> None:
        self._run_utility(self.update_button, update_ytdlp, self.t("updated"))

    def import_existing(self) -> None:
        self.save_preferences()
        source = QFileDialog.getExistingDirectory(self, self.t("import"))
        if not source:
            return
        answer = QMessageBox.question(self, self.t("import"), self.t("import_confirm"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_utility(
            self.import_button,
            lambda log: import_library(Path(source), Path(self.settings.library_dir), log),
            self.t("done"),
        )

    def closeEvent(self, event) -> None:
        if self.engine:
            self.engine.cancel()
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ZZZDown")
    app.setOrganizationName("ZZZDown")
    icon = QIcon(str(resource_root() / "resources" / "ZZZDown.png"))
    app.setWindowIcon(icon)
    settings = load_settings()
    t = translator(settings.language)
    if not settings.disclaimer_accepted:
        answer = QMessageBox.question(
            None,
            t("disclaimer_title"),
            t("disclaimer"),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return 0
        settings.disclaimer_accepted = True
        save_settings(settings)
    window = MainWindow()
    window.show()
    return app.exec()
