from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import Config, default_config_path, load_config, save_config
from .engine import Organizer
from .metadata import exiftool_version


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Photo Organizer")
        self.geometry("980x680")
        self.minsize(780, 540)
        self.items = []
        self.events: queue.Queue = queue.Queue()
        self.inbox = tk.StringVar()
        self.library = tk.StringVar()
        self.index_dir = tk.StringVar()
        self.status = tk.StringVar(value="請先選擇 Inbox 與 Photo Library")
        self.exif_status = tk.StringVar(value="正在檢查內建 ExifTool…")
        self._build()
        self._load()
        self.after(50, self._check_exiftool)
        self.after(100, self._poll)

    def _build(self):
        shell = ttk.Frame(self, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Photo Organizer", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(shell, text="先預覽，再安全匯入。程式絕不覆寫既有照片。", foreground="#555").pack(anchor="w", pady=(2, 18))
        ttk.Label(shell, textvariable=self.exif_status, foreground="#176b3a").pack(anchor="w", pady=(0, 10))
        paths = ttk.LabelFrame(shell, text="資料夾", padding=12)
        paths.pack(fill="x")
        self._path_row(paths, "Inbox", self.inbox, 0)
        self._path_row(paths, "Photo Library", self.library, 1)
        self._path_row(paths, "索引位置", self.index_dir, 2)
        actions = ttk.Frame(shell)
        actions.pack(fill="x", pady=12)
        self.scan_button = ttk.Button(actions, text="掃描預覽", command=self.scan)
        self.scan_button.pack(side="left")
        self.import_button = ttk.Button(actions, text="開始匯入", command=self.execute, state="disabled")
        self.import_button.pack(side="left", padx=8)
        ttk.Button(actions, text="重新建立索引", command=self.reindex).pack(side="left")
        ttk.Button(actions, text="復原上一批", command=self.undo).pack(side="left", padx=8)
        columns = ("action", "name", "date", "destination", "status")
        self.table = ttk.Treeview(shell, columns=columns, show="headings")
        labels = {"action":"結果", "name":"檔名", "date":"拍攝日期", "destination":"目的地", "status":"狀態"}
        widths = {"action":90, "name":180, "date":150, "destination":390, "status":90}
        for column in columns:
            self.table.heading(column, text=labels[column])
            self.table.column(column, width=widths[column], anchor="w")
        self.table.pack(fill="both", expand=True)
        ttk.Label(shell, textvariable=self.status, relief="sunken", padding=7).pack(fill="x", pady=(10, 0))

    def _path_row(self, parent, label, variable, row):
        ttk.Label(parent, text=label, width=14).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="選擇…", command=lambda: self._choose(variable)).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)

    def _choose(self, variable):
        chosen = filedialog.askdirectory()
        if chosen:
            variable.set(chosen)
            self._save()

    def _config(self):
        if not self.inbox.get() or not self.library.get() or not self.index_dir.get():
            raise ValueError("請先選擇 Inbox、Photo Library 與索引位置。")
        config = Config(Path(self.inbox.get()), Path(self.library.get()), index_dir=Path(self.index_dir.get()))
        config.validate()
        return config

    def _save(self):
        if self.inbox.get() and self.library.get() and self.index_dir.get():
            try: save_config(self._config())
            except ValueError: pass

    def _load(self):
        path = default_config_path()
        if path.exists():
            config = load_config(path)
            self.inbox.set(config.inbox)
            self.library.set(config.library)
            self.index_dir.set(config.system_dir)

    def _check_exiftool(self):
        def check():
            version = exiftool_version()
            message = f"✓ 內建 ExifTool {version} 已啟用" if version else "⚠ ExifTool 無法啟動；無日期照片將送入 _Unsorted"
            self.events.put(("exif", message))
        threading.Thread(target=check, daemon=True).start()

    def _work(self, function):
        self.scan_button.configure(state="disabled")
        self.import_button.configure(state="disabled")
        def runner():
            try: function()
            except Exception as exc: self.events.put(("error", str(exc)))
            finally: self.events.put(("done", None))
        threading.Thread(target=runner, daemon=True).start()

    def _organizer(self):
        config = self._config()
        save_config(config)
        return Organizer(config)

    def scan(self):
        def task():
            org = self._organizer()
            try: self.items = org.scan(lambda x: self.events.put(("status", x)))
            finally: org.close()
            self.events.put(("items", self.items))
        self._work(task)

    def execute(self):
        if not self.items or not messagebox.askyesno("確認匯入", f"確定要處理 {len(self.items)} 個檔案嗎？"):
            return
        def task():
            org = self._organizer()
            try: manifest = org.execute(self.items, lambda x: self.events.put(("status", x)))
            finally: org.close()
            self.events.put(("complete", str(manifest)))
        self._work(task)

    def reindex(self):
        def task():
            org = self._organizer()
            try: result = org.reindex(lambda x: self.events.put(("status", x)))
            finally: org.close()
            self.events.put(("notice", f"索引完成：新增 {result[0]}，略過 {result[1]}。"))
        self._work(task)

    def undo(self):
        if not messagebox.askyesno("確認復原", "確定要復原最近一批已匯入的照片嗎？"):
            return
        def task():
            org = self._organizer()
            try: result = org.undo()
            finally: org.close()
            self.events.put(("notice", f"復原完成：成功 {result[0]}，失敗 {result[1]}。"))
        self._work(task)

    def _poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "status": self.status.set(value)
                elif event == "items":
                    self.table.delete(*self.table.get_children())
                    for item in value:
                        self.table.insert("", "end", values=(item.action, Path(item.source).name,
                            item.capture_date or "未知", item.destination, item.status))
                    self.status.set(f"預覽完成：{len(value)} 個檔案。尚未搬動任何照片。")
                    if value: self.import_button.configure(state="normal")
                elif event == "complete":
                    self.status.set("匯入完成")
                    messagebox.showinfo("完成", f"匯入完成。\n操作紀錄：{value}")
                elif event == "notice": messagebox.showinfo("Photo Organizer", value)
                elif event == "exif": self.exif_status.set(value)
                elif event == "error": messagebox.showerror("錯誤", value)
                elif event == "done": self.scan_button.configure(state="normal")
        except queue.Empty: pass
        self.after(100, self._poll)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
