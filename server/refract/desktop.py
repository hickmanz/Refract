"""The app window: the UI shown in a native window (WebView2 via pywebview) instead of a browser tab.

``mode`` is what the running copy looks like to the UI and to a second launch:

* ``window``   - native window; closing it quits the app
* ``browser``  - server only, UI in the default browser (``--browser``, or the fallback when no window is possible)
* ``headless`` - server only (``--no-browser``; tests, development)
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable

from . import store
from .envutil import env

mode = "headless"
window = None  # the pywebview window while mode == "window"
_force_close = False

TITLE = "Refract"


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def run_window(url: str, has_active_jobs: Callable[[], bool]) -> bool:
    """Show the UI in a native window and block until it is closed.

    Returns False when no window could be created (WebView2 missing, pywebview not installed), so the caller
    can fall back to the browser. Must be called on the main thread.
    """
    global window, mode
    try:
        import webview
    except Exception:
        return False

    webview.settings["ALLOW_DOWNLOADS"] = True  # CSV / zip exports
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # e.g. the release page
    try:
        window = webview.create_window(
            TITLE,
            url,
            width=1500,
            height=950,
            min_size=(1000, 650),
            text_select=True,
            zoomable=True,
        )
    except Exception:
        window = None
        return False
    mode = "window"

    def on_closing():
        """Closing while a check runs asks first; returning False keeps the window open."""
        global _force_close
        if _force_close or not has_active_jobs():
            return True

        def ask():
            global _force_close
            try:
                ok = window.create_confirmation_dialog(
                    TITLE,
                    "A simulation is still running. Closing the app cancels it.\n\nClose anyway?",
                )
            except Exception:
                ok = True
            if ok:
                _force_close = True
                window.destroy()

        threading.Thread(target=ask, daemon=True, name="confirm-close").start()
        return False

    window.events.closing += on_closing

    try:
        webview.start(
            gui="edgechromium",  # never fall back to the old Internet Explorer engine
            storage_path=str(store.config_dir() / "webview"),  # keeps local UI settings between runs
            private_mode=False,
            debug=bool(env("DEBUG")),
        )
    except Exception:
        window = None
        mode = "browser"
        return False
    return True


def focus() -> bool:
    """Bring the window to the front (a second launch asks the running copy to do this)."""
    if window is None:
        return False
    try:
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False
        return True
    except Exception:
        return False


def pick_folder(initial: str | None = None) -> dict | None:
    """The system folder dialog, parented to the app window. None if there is no window."""
    if window is None:
        return None
    try:
        import webview

        chosen = window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial or "")
    except Exception as exc:
        return {"path": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"path": os.path.normpath(chosen[0]) if chosen else None}
