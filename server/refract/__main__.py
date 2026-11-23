"""Entry point: ``python -m refract`` starts the local server and opens the app window.

The same entry point also serves the helper processes the app starts for itself
(``--run-job <import|sim> <revision id> [run id]``, ``--pick-folder``), which is what lets a packaged exe
re-launch itself.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def _running_copy(host: str, port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1.0) as resp:
            info = json.loads(resp.read().decode("utf-8"))
            return info if info.get("app") == "refract" else None
    except Exception:
        return None


def _focus_running_window(host: str, port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://{host}:{port}/api/app/focus", method="POST")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("focused"))
    except Exception:
        return False


def _port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    multiprocessing.freeze_support()

    argv = sys.argv[1:]
    if argv[:1] == ["--run-job"] and len(argv) >= 3:
        from .run_job import main as run_job

        sys.exit(run_job(argv[1], argv[2], argv[3] if len(argv) > 3 else ""))
    if argv[:1] == ["--pick-folder"]:
        from .pickfolder import main as pick_folder

        sys.exit(pick_folder((argv[1] if len(argv) > 1 else "") or None, argv[2] if len(argv) > 2 else None))

    parser = argparse.ArgumentParser(description="Local LED light-pipe simulation tool")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--host", default="127.0.0.1", help="Keep 127.0.0.1 to stay local-only")
    parser.add_argument("--no-browser", action="store_true", help="server only: no window, no browser")
    parser.add_argument("--browser", action="store_true", help="use the default browser instead of the app window")
    args = parser.parse_args()
    wanted = "headless" if args.no_browser else "browser" if args.browser else "window"

    port = args.port
    if _port_busy(args.host, port):
        running = _running_copy(args.host, port)
        if running is not None:
            if wanted != "headless":
                if not (running.get("mode") == "window" and _focus_running_window(args.host, port)):
                    webbrowser.open(f"http://{args.host}:{port}")
            return
        for candidate in range(port + 1, port + 21):
            if not _port_busy(args.host, candidate):
                port = candidate
                break

    import uvicorn

    from . import desktop
    from .app import _exit_now, app, jobs

    url = f"http://{args.host}:{port}"
    log_config = None if sys.stdout is None or sys.stderr is None else uvicorn.config.LOGGING_CONFIG
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, log_level="warning", log_config=log_config))

    if wanted == "window" and not desktop.available():
        wanted = "browser"

    if wanted == "window":
        desktop.mode = "window"
        thread = threading.Thread(target=server.run, daemon=True, name="server")
        thread.start()
        for _ in range(300):
            if server.started or not thread.is_alive():
                break
            time.sleep(0.1)
        if not server.started:
            sys.exit("The server could not start (is the port in use?).")
        if desktop.run_window(url, has_active_jobs=jobs.any_active):
            _exit_now()
        desktop.mode = "browser"
        webbrowser.open(url)
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return

    desktop.mode = wanted
    if wanted == "browser":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Refract running at {url}  (Ctrl+C to stop)")
    server.run()


if __name__ == "__main__":
    main()
