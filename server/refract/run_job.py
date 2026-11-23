"""Job processes.

``python -m refract --run-job import <rid>``       STEP -> viewer model -> optical mesh
``python -m refract --run-job sim <rid> <runid>``  Monte Carlo simulation of a run

Each runs in its own process so that a native crash cannot take the server down and a simulation can be
cancelled by killing it.
"""

from __future__ import annotations

import sys
import time
import traceback

from . import store


class _Reporter:
    def __init__(self, path) -> None:
        self.path = path
        self.last = 0.0

    def __call__(self, stage: str, phase: str, fraction: float, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last < 0.15:
            return
        self.last = now
        store.write_json(self.path, {"stage": stage, "phase": phase, "fraction": round(float(fraction), 4)})


def run_import(rid: str) -> int:
    from .optical_mesh import build_optical_mesh
    from .step_import import import_step
    from .tessellate import build_model_files

    mdir = store.rev_dir(rid)
    meta = store.load_rev(rid) or {}
    progress = _Reporter(mdir / "progress.json")

    def set_status(**kw) -> None:
        m = store.load_rev(rid) or meta
        m.update(kw)
        store.save_rev(rid, m)

    try:
        set_status(status="running", error=None, started=time.time())
        progress("import", "Reading STEP file", 0.0, True)
        model = import_step(str(mdir / "source.step"), lambda msg: progress("import", msg, 0.0, True))
        mesh = build_model_files(
            model, rid, meta.get("filename", rid), mdir / "model.glb", mdir / "model.json",
            lambda msg, frac: progress("mesh", msg, frac),
        )
        set_status(model_ready=True)
        doc = build_optical_mesh(
            model, mdir / "optical.npz", mdir / "optical.json",
            lambda msg, frac: progress("optical", msg, frac),
            quality=meta.get("meshQuality") or "normal",
        )
        progress("done", "Done", 1.0, True)
        set_status(
            status="ready",
            finished=time.time(),
            instances=len(doc["instances"]),
            triangles=doc["triangles"],
            viewerTriangles=mesh.triangle_count,
            summary={"parts": len(doc["instances"]), "warnings": list(model.warnings) + doc["warnings"]},
        )
        return 0
    except Exception as exc:
        (mdir / "job.log").write_text(traceback.format_exc(), encoding="utf-8")
        set_status(status="failed", error=f"{type(exc).__name__}: {exc}", finished=time.time())
        return 1


def run_sim(rid: str, runid: str) -> int:
    rdir = store.run_dir(rid, runid)
    progress = _Reporter(rdir / "progress.json")

    def set_status(**kw) -> None:
        run = store.load_run(rid, runid) or {}
        run.update(kw)
        store.save_run(rid, runid, run)

    try:
        from .simrun import run_simulation

        set_status(status="running", error=None, started=time.time())
        run_simulation(rid, runid, lambda phase, frac: progress("sim", phase, frac))
        progress("done", "Done", 1.0, True)
        set_status(status="ready", finished=time.time())
        return 0
    except Exception as exc:
        (rdir / "job.log").write_text(traceback.format_exc(), encoding="utf-8")
        set_status(status="failed", error=f"{type(exc).__name__}: {exc}", finished=time.time())
        return 1


def run_render(rid: str, runid: str) -> int:
    """Mitsuba 3 picture of a finished run (settings are in ``<run>/render/render.json``)."""
    rdir = store.run_dir(rid, runid) / "render"
    rdir.mkdir(parents=True, exist_ok=True)
    progress = _Reporter(rdir / "progress.json")

    def set_status(**kw) -> None:
        meta = store.read_json(rdir / "render.json", {}) or {}
        meta.update(kw)
        store.write_json(rdir / "render.json", meta)

    try:
        from .render import mitsuba_export

        settings = (store.read_json(rdir / "render.json", {}) or {}).get("settings", {})
        set_status(status="running", error=None, started=time.time())
        t0 = time.time()
        info = mitsuba_export.render_to_png(
            rid, runid, settings.get("state"), settings, rdir, lambda phase, frac: progress("render", phase, frac)
        )
        progress("done", "Done", 1.0, True)
        set_status(status="ready", finished=time.time(), seconds=round(time.time() - t0, 2), info=info)
        return 0
    except Exception as exc:
        (rdir / "job.log").write_text(traceback.format_exc(), encoding="utf-8")
        set_status(status="failed", error=f"{type(exc).__name__}: {exc}", finished=time.time())
        return 1


def main(kind: str, rid: str, runid: str = "") -> int:
    if kind == "liveview":
        from .render.live_view import worker_main

        return worker_main(rid, runid)
    if kind == "render":
        return run_render(rid, runid)
    if kind == "import":
        return run_import(rid)
    if kind == "sim":
        return run_sim(rid, runid)
    print(f"unknown job kind {kind}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
