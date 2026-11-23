"""The solvers a run can use: ``cpu`` (Embree + Numba, always there) and ``gpu`` (NVIDIA Warp on CUDA, optional).

Both expose ``trace_channel`` with the same arguments and return the same ``Tally``, so the rest of the program does not care
which one made the numbers. The GPU module is only imported when it is asked for.
"""

from __future__ import annotations

import importlib.util
import threading
from types import ModuleType

SOLVERS = ("cpu", "gpu")
_lock = threading.Lock()
_gpu_status: tuple[bool, str] | None = None


def gpu_status(refresh: bool = False) -> tuple[bool, str]:
    """(usable, device name or the reason it is not): Warp installed and a CUDA device present. Checked once, then remembered."""
    global _gpu_status
    with _lock:
        if _gpu_status is None or refresh:
            if importlib.util.find_spec("warp") is None:
                _gpu_status = (False, "Warp is not installed (pip install warp-lang)")
            else:
                try:
                    from . import gpu_tracer

                    _gpu_status = gpu_tracer.available()
                except Exception as exc:  # any import or driver problem means "not available", never an error of the app
                    _gpu_status = (False, f"{type(exc).__name__}: {exc}")
        return _gpu_status


def describe() -> dict:
    """What the user interface offers: every solver, whether it can be used, and a name or the reason it cannot."""
    ok, name = gpu_status()
    return {
        "solvers": [
            {"id": "cpu", "available": True, "name": "CPU (Embree + Numba)"},
            {"id": "gpu", "available": ok, "name": name if ok else None, "reason": None if ok else name},
        ]
    }


def get_solver(name: str) -> ModuleType:
    """The module that traces for the named solver (it has ``trace_channel``)."""
    if name == "cpu":
        from . import tracer

        return tracer
    if name == "gpu":
        ok, why = gpu_status()
        if not ok:
            raise ValueError(f"The GPU solver is not available: {why}")
        from . import gpu_tracer

        return gpu_tracer
    raise ValueError(f"unknown solver {name!r} (use one of {', '.join(SOLVERS)})")
