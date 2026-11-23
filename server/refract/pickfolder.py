"""Native "choose a folder" dialog, run in its own process (tkinter wants its own main thread).

The result is JSON, ``{"path": "<folder>"}`` (``null`` when cancelled), written to ``out_path`` when given
(a windowless packaged exe has no usable stdout) and to stdout otherwise.
"""

from __future__ import annotations

import json
import os
import sys


def _emit(doc: dict, out_path: str | None) -> None:
    text = json.dumps(doc)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    elif sys.stdout is not None:
        sys.stdout.write(text + "\n")


def main(initial: str | None = None, out_path: str | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        kwargs = {"title": "Choose a folder", "mustexist": False}
        if initial and os.path.isdir(initial):
            kwargs["initialdir"] = initial
        chosen = filedialog.askdirectory(**kwargs)
        root.destroy()
        _emit({"path": os.path.normpath(chosen) if chosen else None}, out_path)
        return 0
    except Exception as exc:  # no display, tkinter missing, ...
        _emit({"path": None, "error": f"{type(exc).__name__}: {exc}"}, out_path)
        return 1
