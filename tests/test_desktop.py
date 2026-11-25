"""Exercise the desktop controls using real Tk widgets and temporary destinations."""
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk
from endoscope_degrid import desktop


@pytest.mark.parametrize("short", [True, False])
def test_choose_and_export(tmp_path, monkeypatch, short):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(str(error))
    root.withdraw()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(root, "mainloop", lambda: None)
    source = tmp_path / "source.mp4"
    output = tmp_path / "result.mp4"
    monkeypatch.setattr(desktop.filedialog, "askopenfilename", lambda **kw: str(source))
    monkeypatch.setattr(desktop.filedialog, "asksaveasfilename", lambda **kw: str(output))
    errors = []
    monkeypatch.setattr(desktop.messagebox, "showerror", lambda title, text, **kw: errors.append(text))
    calls = []

    def process(path, args, destination, *, new_report, progress):
        assert new_report
        calls.append((path, args.duration, destination, threading.current_thread()))
        progress("process")
        if not short:
            raise ValueError("unsupported video")

    monkeypatch.setattr(desktop, "run_one", process)
    try:
        desktop.main()
        panel, = root.winfo_children()
        browse, preview, export = [w for w in panel.winfo_children() if isinstance(w, ttk.Button)]
        assert preview.instate(["disabled"]) and export.instate(["disabled"])
        browse.invoke()
        button = preview if short else export
        output.touch()
        button.invoke()
        assert not calls and errors.pop() == "新しいファイル名を指定してください。"
        output.unlink()
        button.invoke()
        assert browse.instate(["disabled"])
        deadline = time.monotonic() + 5
        while browse.instate(["disabled"]) and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert browse.instate(["!disabled"])
        assert len(calls) == 1
        assert calls[0][:3] == (source, 10 if short else None, output)
        assert calls[0][3] != threading.main_thread()
        assert errors == ([] if short else ["unsupported video"])
    finally:
        root.destroy()
