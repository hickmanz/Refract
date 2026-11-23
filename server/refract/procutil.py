"""Helpers for starting this app's own helper processes (jobs, the folder picker).

In development they run as ``python -m refract --<kind> ...``; in the packaged exe (PyInstaller) the same
exe is started with ``--<kind> ...``.
"""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def child_command(kind: str, *args: str) -> list[str]:
    if is_frozen():
        return [sys.executable, f"--{kind}", *args]
    return [sys.executable, "-m", "refract", f"--{kind}", *args]


def pid_alive(pid: int) -> bool:
    """True if a process with this id exists (never signals the process: os.kill(pid, 0) kills on Windows)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
