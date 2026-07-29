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
        self.root.minsize(900, 700)
        self.root.geometry("1040x780")
        self.root.configure(background="#f4f6fa")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        family = "SF Pro Text" if sys.platform == "darwin" else "Segoe UI" if sys.platform == "win32" else "Helvetica"
        style.configure(".", font=(family, 11), background="#f4f6fa", foreground="#172033")
        style.configure("App.TFrame", background="#f4f6fa")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabel", background="#ffffff", foreground="#172033")
        style.configure("Title.TLabel", background="#f4f6fa", foreground="#111827", font=(family, 24, "bold"))
        style.configure("Subtitle.TLabel", background="#f4f6fa", foreground="#667085", font=(family, 11))
        style.configure("Section.TLabel", background="#ffffff", foreground="#172033", font=(family, 13, "bold"))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#7a8495", font=(family, 10))
        style.configure("Status.TLabel", background="#eef4ff", foreground="#2456a6", padding=(12, 7), font=(family, 10, "bold"))
        style.configure("Card.TCheckbutton", background="#ffffff", foreground="#445066", padding=(0, 4))
        style.map("Card.TCheckbutton", background=[("active", "#ffffff")])
        style.configure("Primary.TButton", padding=(18, 10), background="#2563eb", foreground="#ffffff", borderwidth=0, font=(family, 11, "bold"))
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#a9bce5")])
        style.configure("Secondary.TButton", padding=(15, 10), background="#edf2f8", foreground="#263449", borderwidth=0, font=(family, 11))
        style.map("Secondary.TButton", background=[("active", "#dfe7f1")])
        style.configure("Danger.TButton", padding=(15, 10), background="#fff0f0", foreground="#b42318", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#ffe2e2")])
        style.configure("TNotebook", background="#f4f6fa", borderwidth=0, tabmargins=(26, 0, 0, 0))
        style.configure("TNotebook.Tab", padding=(18, 10), background="#e9edf4", foreground="#667085", borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#1d4ed8")])
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#d8dee9", padding=8)
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor="#d8dee9", padding=7)

        header = ttk.Frame(self.root, style="App.TFrame", padding=(28, 22, 28, 12))
        header.pack(fill="x")
        self.header_title = ttk.Label(header, style="Title.TLabel")
        self.header_title.pack(anchor="w")
        self.header_subtitle = ttk.Label(header, style="Subtitle.TLabel")
        self.header_subtitle.pack(anchor="w", pady=(5, 0))

        self.tabs = ttk.Notebook(self.root)
        self.download_page = ttk.Frame(self.tabs, style="App.TFrame", padding=(28, 22))
        self.settings_page = ttk.Frame(self.tabs, style="App.TFrame", padding=(28, 22))
        self.tabs.add(self.download_page)
        self.tabs.add(self.settings_page)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        input_card = ttk.Frame(self.download_page, style="Card.TFrame", padding=(22, 18))
        input_card.pack(fill="x")
        self.url_label = ttk.Label(input_card, style="Section.TLabel")
        self.url_label.pack(anchor="w")
        self.url_hint = ttk.Label(input_card, style="Muted.TLabel")
        self.url_hint.pack(anchor="w", pady=(4, 10))
        self.urls = tk.Text(
            input_card, height=6, wrap="word", relief="solid", borderwidth=1,
            background="#fbfcfe", foreground="#172033", insertbackground="#2563eb",
            highlightbackground="#d8dee9", highlightcolor="#7aa2f7", highlightthickness=1,
            padx=12, pady=10, font=(family, 11),
        )
        self.urls.pack(fill="x")
        options = ttk.Frame(input_card, style="Card.TFrame")
        options.pack(fill="x", pady=(10, 0))
        self.force_var = tk.BooleanVar(value=False)
        self.force_check = ttk.Checkbutton(options, variable=self.force_var, style="Card.TCheckbutton")
        self.force_check.pack(side="left")

        actions = ttk.Frame(self.download_page, style="App.TFrame")
        actions.pack(fill="x", pady=14)
        self.start_button = ttk.Button(actions, command=self.start_download, style="Primary.TButton")
        self.stop_button = ttk.Button(actions, command=self.stop_download, state="disabled", style="Danger.TButton")
        self.library_button = ttk.Button(actions, command=self.open_library, style="Secondary.TButton")
        self.start_button.pack(side="left")
        self.stop_button.pack(side="left", padx=9)
        self.library_button.pack(side="right")

        activity_card = ttk.Frame(self.download_page, style="Card.TFrame", padding=(22, 17))
        activity_card.pack(fill="both", expand=True)
        activity_header = ttk.Frame(activity_card, style="Card.TFrame")
        activity_header.pack(fill="x", pady=(0, 10))
        self.log_title = ttk.Label(activity_header, style="Section.TLabel")
        self.log_title.pack(side="left")
        self.status_var = tk.StringVar()
        ttk.Label(activity_header, textvariable=self.status_var, style="Status.TLabel").pack(side="right")
        self.log = tk.Text(
            activity_card, height=12, wrap="word", state="disabled", relief="flat", borderwidth=0,
            background="#111827", foreground="#d9e2f1", insertbackground="#ffffff",
            padx=14, pady=12, font=("Menlo" if sys.platform == "darwin" else "Consolas", 10),
        )
        self.log.pack(fill="both", expand=True)

        self.browser_var = tk.StringVar()
        self.location_var = tk.StringVar()
        self.proxy_var = tk.StringVar()
        self.direct_var = tk.BooleanVar()
        self.language_var = tk.StringVar()
        form = ttk.Frame(self.settings_page, style="Card.TFrame", padding=(24, 20))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self.browser_label = ttk.Label(form, style="Card.TLabel")
        self.browser_label.grid(row=0, column=0, sticky="w", padx=(0, 16), pady=8)
        self.browser = ttk.Combobox(form, textvariable=self.browser_var, state="readonly")
        self.browser.grid(row=0, column=1, columnspan=2, sticky="ew", pady=8)
        self.location_label = ttk.Label(form, style="Card.TLabel")
        self.location_label.grid(row=1, column=0, sticky="w", padx=(0, 16), pady=8)
        ttk.Entry(form, textvariable=self.location_var).grid(row=1, column=1, sticky="ew", pady=8)
        self.choose_button = ttk.Button(form, command=self.choose_location)
        self.choose_button.grid(row=1, column=2, padx=(8, 0), pady=8)
        self.proxy_label = ttk.Label(form, style="Card.TLabel")
        self.proxy_label.grid(row=2, column=0, sticky="w", padx=(0, 16), pady=8)
        ttk.Entry(form, textvariable=self.proxy_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=8)
        self.direct = ttk.Checkbutton(form, variable=self.direct_var, style="Card.TCheckbutton")
        self.direct.grid(row=3, column=1, columnspan=2, sticky="w", pady=8)
        self.language_label = ttk.Label(form, style="Card.TLabel")
        self.language_label.grid(row=4, column=0, sticky="w", padx=(0, 16), pady=8)
        self.language = ttk.Combobox(form, textvariable=self.language_var, values=("简体中文", "English"), state="readonly")
        self.language.grid(row=4, column=1, columnspan=2, sticky="ew", pady=8)
        self.language.bind("<<ComboboxSelected>>", lambda _event: self.preview_language())
        actions = ttk.Frame(self.settings_page, style="App.TFrame")
        actions.pack(fill="x", pady=18)
        self.save_button = ttk.Button(actions, command=self.save_preferences, style="Primary.TButton")
        self.update_button = ttk.Button(actions, command=self.update_engine, style="Secondary.TButton")
        self.import_button = ttk.Button(actions, command=self.import_existing, style="Secondary.TButton")
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
        self.header_title.configure(text="ZZZDown")
        self.header_subtitle.configure(text=self.t("tagline"))
        self.tabs.tab(0, text=self.t("download"))
        self.tabs.tab(1, text=self.t("settings"))
        self.url_label.configure(text=self.t("urls"))
        self.url_hint.configure(text=self.t("url_hint"))
        self.log_title.configure(text=self.t("activity"))
        self.start_button.configure(text=self.t("start"))
        self.stop_button.configure(text=self.t("stop"))
        self.force_check.configure(text=self.t("force_redownload"))
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
                    self.force_var.set(False)
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
        self.engine = DownloadEngine(
            self.settings,
            lambda line: self.events.put(("log", line)),
            force_redownload=self.force_var.get(),
        )
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
