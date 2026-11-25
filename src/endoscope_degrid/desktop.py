"""Local desktop UI for the same process command exposed by the CLI."""
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .cli import parser, run_one


def main():
    root = tk.Tk()
    root.title("Endoscope Degrid")
    panel = ttk.Frame(root, padding=20)
    panel.pack(fill="both", expand=True)
    source = tk.StringVar()
    status = tk.StringVar(value="MP4 または MOV を選んでください。")
    events = SimpleQueue()
    busy = False

    def choose():
        selected = filedialog.askopenfilename(parent=root, filetypes=[("動画", "*.mp4 *.mov *.MP4 *.MOV")])
        if selected:
            source.set(selected)
            status.set("書き出す範囲を選んでください。")
            preview.state(["!disabled"])
            export.state(["!disabled"])

    def start(short):
        nonlocal busy
        path = Path(source.get())
        suffix = "_preview" if short else "_degrid"
        selected = filedialog.asksaveasfilename(parent=root, initialdir=path.parent,
            initialfile=path.stem + suffix + path.suffix, defaultextension=path.suffix,
            filetypes=[("動画", "*" + path.suffix)], confirmoverwrite=False)
        if not selected:
            return
        destination = Path(selected)
        if destination.exists():
            messagebox.showerror("保存先", "新しいファイル名を指定してください。", parent=root)
            return
        arguments = ["process", str(path), "-o", selected]
        if short:
            arguments.extend(["--duration", "10"])
        args = parser().parse_args(arguments)
        busy = True
        for button in (browse, preview, export):
            button.state(["disabled"])
        status.set("動画を読み込み中…")

        def process():
            try:
                run_one(path, args, destination, progress=lambda s: events.put(("progress", s)))
            except Exception as error:
                events.put(("error", str(error) or type(error).__name__))
            else:
                events.put(("done", f"書き出しました。\n{destination}"))

        Thread(target=process).start()
        root.after(100, poll)

    def poll():
        nonlocal busy
        while True:
            try:
                kind, text = events.get_nowait()
            except Empty:
                root.after(100, poll)
                return
            if kind == "progress":
                status.set(text)
                continue
            busy = False
            for button in (browse, preview, export):
                button.state(["!disabled"])
            status.set(text)
            if kind == "error":
                messagebox.showerror("書き出しに失敗しました", text, parent=root)
            return

    def close():
        if busy:
            messagebox.showinfo("処理中", "書き出しが終わるまでお待ちください。", parent=root)
        else:
            root.destroy()

    browse = ttk.Button(panel, text="動画を選ぶ…", command=choose)
    browse.grid(row=0, column=0, sticky="w")
    ttk.Label(panel, textvariable=source, wraplength=440).grid(row=1, column=0, columnspan=2, sticky="w", pady=10)
    preview = ttk.Button(panel, text="先頭10秒を書き出す…", command=lambda: start(True), state="disabled")
    export = ttk.Button(panel, text="全編を書き出す…", command=lambda: start(False), state="disabled")
    preview.grid(row=2, column=0, sticky="w", padx=(0, 10))
    export.grid(row=2, column=1, sticky="w")
    ttk.Label(panel, textvariable=status, wraplength=440).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()
