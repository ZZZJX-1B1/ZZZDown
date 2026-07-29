from __future__ import annotations

import queue
import socket
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import Settings, load_settings, save_settings
from .engine import DownloadEngine, import_library, parse_urls
from .i18n import translator
from .indexer import generate_global
from .library_server import start_server
from .paths import resource_root
from .tools import detected_browsers, update_ytdlp


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        self.t = translator(self.settings.language)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.engine: DownloadEngine | None = None
        self.library_port: int | None = None
        self.browser_values: list[str] = []
        self._build_ui()
        self._load_values()
        self.retranslate()
        self.root.after(80, self._drain_events)

    def _build_ui(self) -> None:
        self.root.minsize(820, 650)
        self.root.geometry("900x700")
        self.root.configure(background="#f6f7f8")
        style = ttk.Style(self.root)
        try:
            style.theme_use("aqua" if sys.platform == "darwin" else "vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(11, 7))
        style.configure("TNotebook", background="#f6f7f8")

        self.tabs = ttk.Notebook(self.root)
        self.download_page = ttk.Frame(self.tabs, padding=18)
        self.settings_page = ttk.Frame(self.tabs, padding=22)
        self.tabs.add(self.download_page)
        self.tabs.add(self.settings_page)
        self.tabs.pack(fill="both", expand=True)

        self.url_label = ttk.Label(self.download_page)
        self.url_label.pack(anchor="w", pady=(0, 7))
        self.urls = tk.Text(self.download_page, height=9, wrap="word", relief="solid", borderwidth=1)
        self.urls.pack(fill="x")
        buttons = ttk.Frame(self.download_page)
        buttons.pack(fill="x", pady=12)
        self.start_button = ttk.Button(buttons, command=self.start_download)
        self.stop_button = ttk.Button(buttons, command=self.stop_download, state="disabled")
        self.library_button = ttk.Button(buttons, command=self.open_library)
        self.start_button.pack(side="left")
        self.stop_button.pack(side="left", padx=8)
        self.library_button.pack(side="right")
        self.status_var = tk.StringVar()
        ttk.Label(self.download_page, textvariable=self.status_var).pack(anchor="w", pady=(0, 8))
        self.log = tk.Text(self.download_page, height=16, wrap="word", state="disabled", relief="solid", borderwidth=1)
        self.log.pack(fill="both", expand=True)

        self.browser_var = tk.StringVar()
        self.location_var = tk.StringVar()
        self.proxy_var = tk.StringVar()
        self.direct_var = tk.BooleanVar()
        self.language_var = tk.StringVar()
        form = ttk.Frame(self.settings_page)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.browser_label = ttk.Label(form)
        self.browser_label.grid(row=0, column=0, sticky="w", padx=(0, 16), pady=8)
        self.browser = ttk.Combobox(form, textvariable=self.browser_var, state="readonly")
        self.browser.grid(row=0, column=1, columnspan=2, sticky="ew", pady=8)
        self.location_label = ttk.Label(form)
        self.location_label.grid(row=1, column=0, sticky="w", padx=(0, 16), pady=8)
        ttk.Entry(form, textvariable=self.location_var).grid(row=1, column=1, sticky="ew", pady=8)
        self.choose_button = ttk.Button(form, command=self.choose_location)
        self.choose_button.grid(row=1, column=2, padx=(8, 0), pady=8)
        self.proxy_label = ttk.Label(form)
        self.proxy_label.grid(row=2, column=0, sticky="w", padx=(0, 16), pady=8)
        ttk.Entry(form, textvariable=self.proxy_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=8)
        self.direct = ttk.Checkbutton(form, variable=self.direct_var)
        self.direct.grid(row=3, column=1, columnspan=2, sticky="w", pady=8)
        self.language_label = ttk.Label(form)
        self.language_label.grid(row=4, column=0, sticky="w", padx=(0, 16), pady=8)
        self.language = ttk.Combobox(form, textvariable=self.language_var, values=("简体中文", "English"), state="readonly")
        self.language.grid(row=4, column=1, columnspan=2, sticky="ew", pady=8)
        self.language.bind("<<ComboboxSelected>>", lambda _event: self.preview_language())
        actions = ttk.Frame(self.settings_page)
        actions.pack(fill="x", pady=18)
        self.save_button = ttk.Button(actions, command=self.save_preferences)
        self.update_button = ttk.Button(actions, command=self.update_engine)
        self.import_button = ttk.Button(actions, command=self.import_existing)
        self.save_button.pack(side="left")
        self.update_button.pack(side="left", padx=8)
        self.import_button.pack(side="left")

    def _browser_options(self) -> tuple[list[str], list[str]]:
        detected = detected_browsers()
        keys = ["chrome", "edge", "firefox", "none"]
        labels = ["Chrome", "Microsoft Edge", "Firefox", self.t("browser_none")]
        return keys, [f"✓ {label}" if key in detected else label for key, label in zip(keys, labels)]

    def _load_values(self) -> None:
        self.location_var.set(self.settings.library_dir)
        self.proxy_var.set(self.settings.proxy)
        self.direct_var.set(self.settings.direct_connection)
        self.language_var.set("简体中文" if self.settings.language == "zh_CN" else "English")

    def retranslate(self) -> None:
        self.root.title(self.t("title"))
        self.tabs.tab(0, text=self.t("download"))
        self.tabs.tab(1, text=self.t("settings"))
        self.url_label.configure(text=self.t("urls"))
        self.start_button.configure(text=self.t("start"))
        self.stop_button.configure(text=self.t("stop"))
        self.library_button.configure(text=self.t("library"))
        self.status_var.set(self.t("idle"))
        self.browser_label.configure(text=self.t("browser"))
        self.location_label.configure(text=self.t("location"))
        self.proxy_label.configure(text=self.t("proxy"))
        self.language_label.configure(text=self.t("language"))
        self.choose_button.configure(text=self.t("choose"))
        self.direct.configure(text=self.t("direct"))
        self.save_button.configure(text=self.t("save"))
        self.update_button.configure(text=self.t("update"))
        self.import_button.configure(text=self.t("import"))
        current = self.settings.browser
        self.browser_values, labels = self._browser_options()
        self.browser.configure(values=labels)
        try:
            self.browser.current(self.browser_values.index(current))
        except ValueError:
            self.browser.current(0)

    def preview_language(self) -> None:
        self.t = translator("zh_CN" if self.language_var.get() == "简体中文" else "en_US")
        self.retranslate()

    def save_preferences(self) -> None:
        index = self.browser.current()
        self.settings.browser = self.browser_values[index] if index >= 0 else "none"
        self.settings.language = "zh_CN" if self.language_var.get() == "简体中文" else "en_US"
        self.settings.library_dir = self.location_var.get().strip()
        self.settings.proxy = self.proxy_var.get().strip()
        self.settings.direct_connection = self.direct_var.get()
        library = Path(self.settings.library_dir).expanduser()
        library.mkdir(parents=True, exist_ok=True)
        (library / ".zzzdown-language").write_text(self.settings.language + "\n", encoding="utf-8")
        save_settings(self.settings)

    def choose_location(self) -> None:
        selected = filedialog.askdirectory(title=self.t("location"), initialdir=self.location_var.get())
        if selected:
            self.location_var.set(selected)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "download_done":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set(self.t("failed") if payload else self.t("done"))
                elif kind == "utility_done":
                    button, error, success = payload
                    button.configure(state="normal")
                    messagebox.showerror(self.t("title"), error) if error else messagebox.showinfo(self.t("title"), success)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    def start_download(self) -> None:
        self.save_preferences()
        urls = parse_urls(self.urls.get("1.0", "end"))
        if not urls:
            messagebox.showinfo(self.t("title"), self.t("urls"))
            return
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status_var.set(self.t("running"))
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.engine = DownloadEngine(self.settings, lambda line: self.events.put(("log", line)))
        threading.Thread(target=self._download_worker, args=(urls,), daemon=True).start()

    def _download_worker(self, urls: list[str]) -> None:
        assert self.engine is not None
        try:
            failures = self.engine.download(urls)
        except Exception as exc:
            self.events.put(("log", f"ERROR: {exc}"))
            failures = 1
        self.events.put(("download_done", failures))

    def stop_download(self) -> None:
        if self.engine:
            self.engine.cancel()

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
        webbrowser.open(f"http://127.0.0.1:{self.library_port}/")

    def _run_utility(self, button: ttk.Button, function, success: str) -> None:
        button.configure(state="disabled")
        def worker() -> None:
            try:
                function(lambda line: self.events.put(("log", line)))
                error = ""
            except Exception as exc:
                error = str(exc)
            self.events.put(("utility_done", (button, error, success)))
        threading.Thread(target=worker, daemon=True).start()

    def update_engine(self) -> None:
        self._run_utility(self.update_button, update_ytdlp, self.t("updated"))

    def import_existing(self) -> None:
        self.save_preferences()
        source = filedialog.askdirectory(title=self.t("import"))
        if not source or not messagebox.askyesno(self.t("import"), self.t("import_confirm")):
            return
        self._run_utility(
            self.import_button,
            lambda log: import_library(Path(source), Path(self.settings.library_dir), log),
            self.t("done"),
        )


def main() -> int:
    root = tk.Tk()
    try:
        root.iconphoto(True, tk.PhotoImage(file=str(resource_root() / "resources" / "ZZZDown.png")))
    except tk.TclError:
        pass
    settings = load_settings()
    t = translator(settings.language)
    if not settings.disclaimer_accepted:
        root.withdraw()
        accepted = messagebox.askokcancel(t("disclaimer_title"), t("disclaimer"), parent=root)
        if not accepted:
            root.destroy()
            return 0
        settings.disclaimer_accepted = True
        save_settings(settings)
        root.deiconify()
    MainWindow(root)
    root.mainloop()
    return 0
