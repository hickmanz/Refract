"""Runs one simulation (all source channels needed by the setup's states) and stores the raw tallies."""

from __future__ import annotations

import time
from typing import Callable

import numpy as np

from . import library as lib
from . import store
from .engine import kernels, solvers, sources, tracer
from .engine.scene import build_scene
from .optical_mesh import load_optical

DEFAULT_SETTINGS = {
    "rays": 200_000,  # rays per source channel
    "seed": 1,
    "rr_threshold": 0.01,
    "max_bounces": 500,
    "theta_bins": 12,
    "preview_paths": 16,  # recorded ray paths per LED
    "max_path_points": 128,
    "live": False,  # traced in growing passes that are saved as they finish (live preview)
    "analysis": True,  # keep what is needed to see where the light came from (face contributions, ray logs, ray replay)
    "analysis_rays": 2_000_000,  # rays per channel that are logged in detail (the maps always use every ray)
    "solver": "cpu",  # "cpu" (Embree + Numba) or "gpu" (NVIDIA Warp on CUDA, if installed): same physics, see engine/solvers.py
    "one_channel_per_led": False,  # quick look: trace only the brightest driven channel of every LED (the others are left out of the result)
    "target_noise": 0.0,  # > 0: trace in growing passes and stop once the median relative noise per map pixel is below this ("rays" is then the maximum); with "analysis" on, one extra pass over the first "analysis_rays" gives the light path analysis
}
FIRST_PASS_RAYS = 250_000  # first pass of a run that stops at a noise target; every later pass doubles the total


def effective_states(setup: dict) -> list[dict]:
    states = [s for s in (setup.get("states") or []) if s.get("id")]
    if not states:
        states = [{"id": "all", "name": "All LEDs on", "drive": {"*": 1.0}}]
    return states


def run_simulation(rid: str, runid: str, progress: Callable[[str, float], None]) -> None:
    rdir = store.run_dir(rid, runid)
    run = store.load_run(rid, runid) or {}
    settings = {**DEFAULT_SETTINGS, **(run.get("settings") or {})}
    setup = store.read_json(rdir / "setup.json") or run.get("setup")
    if not setup:
        raise ValueError("The run has no setup snapshot")
    library = store.read_json(rdir / "library.json") or lib.get_library()

    mdir = store.rev_dir(rid)
    opt = load_optical(mdir / "optical.npz")
    opt_doc = store.read_json(mdir / "optical.json")
    if not opt_doc:
        raise ValueError("The revision has no optical mesh (import did not finish)")

    t_start = time.time()
    progress("Building scene", 0.0)
    scene = build_scene(opt, opt_doc, setup, library)
    if not scene.detectors:
        raise ValueError("The setup has no detector. Pick the exit surface faces first.")
    srcs = sources.build_channel_sources(setup, opt_doc, scene, opt, library)

    states = effective_states(setup)
    resolved = []
    for s in states:
        resolved.append({"id": s["id"], "name": s.get("name", s["id"]), "drive": sources.resolve_state_drive(s, srcs)})
    used = [src for src in srcs if any(st["drive"].get(src.key, 0.0) > 0 for st in resolved)]
    if settings.get("one_channel_per_led"):
        used = one_channel_per_led(used, resolved)
    if not used:
        raise ValueError("No LED channel is driven in any state")

    n_rays = int(settings["rays"])
    nth = int(settings["theta_bins"])
    nbody, ndet = len(scene.bodies), len(scene.detectors)
    nch = len(used)

    arr = {
        "ex": np.zeros((nch, scene.total_pix, 3), dtype=np.float64),
        "cone": np.zeros((nch, scene.total_pix, 3), dtype=np.float64),
        "ex2": np.zeros((nch, scene.total_pix), dtype=np.float64),  # squared luminous deposits: the noise of the maps
        "cone2": np.zeros((nch, scene.total_pix), dtype=np.float64),
        "ring": np.zeros((nch, scene.total_pix, kernels.NRING), dtype=np.float64),  # luminous weight in the angle rings around the viewing cone
        "ring2": np.zeros((nch, scene.total_pix, kernels.NRING), dtype=np.float64),  # ... and the squares of its deposits (noise)
        "dirh": np.zeros((nch, ndet, nth, 3)),
        "det_pow": np.zeros((nch, ndet)),
        "rcv_pow": np.zeros((nch, ndet)),  # radiometric weight that arrived on receiver detectors (the rays carry on: not in the budget)
        "abs_vol": np.zeros((nch, nbody)),
        "abs_surf": np.zeros((nch, nbody)),
        "special": np.zeros((nch, 8)),
        "n_rays": np.full(nch, n_rays, dtype=np.int64),
        "w0": np.zeros(nch),
        "flux_lm": np.zeros(nch),
        "accounted": np.zeros(nch),
    }
    for k, det in enumerate(scene.detectors):
        arr[f"area_map_{k}"] = det.area_map

    scene_doc = {
        "bodies": [
            {"index": b["index"], "path": b["path"], "name": b["name"], "material": b["material"], "finish": b["finish"], "transparent": b["transparent"]}
            for b in scene.bodies
        ],
        "detectors": [
            {
                "id": d.id, "name": d.name, "origin": d.origin.tolist(), "u": d.u.tolist(), "v": d.v.tolist(), "n": d.n.tolist(),
                "ext": d.ext.tolist(), "nu": int(d.nu), "nv": int(d.nv), "offset": int(d.offset), "cone_deg": float(d.cone_deg),
                "ring_deg": [round(float(e), 6) for e in d.ring_deg],
                "area_total": float(d.area_total), "corners": d.corners.tolist(),
                "mode": int(d.mode), **({"mesh": d.mesh} if d.mesh else {}),
                "kind": d.kind, "through": d.through, "view": d.view.tolist() if d.view is not None else d.n.tolist(), "view_cos": d.view_cos,
            }
            for d in scene.detectors
        ],
        "channels": [
            {
                "key": s.key, "led_id": s.led_id, "led_name": s.led_name, "channel_id": s.channel_id, "channel_name": s.channel_name,
                "flux_lm": s.flux_lm, "flux_w": s.flux_w,
                "color": _channel_color(s),
                "origin": s.origin.tolist(), "axis": s.axis.tolist(),
            }
            for s in used
        ],
        "states": resolved,
        "warnings": list(scene.warnings),
        "settings": settings,
    }

    live = bool(settings.get("live"))
    target = float(settings.get("target_noise") or 0.0)
    until_noise = target > 0  # traced in growing passes until the maps are quiet enough (or "rays" is reached)
    analysis_wanted = bool(settings.get("analysis", True)) and not live
    log_rays = int(settings.get("analysis_rays", 2_000_000))
    solver_name = str(settings.get("solver") or "cpu")
    solver = solvers.get_solver(solver_name)
    # The GPU solver is fast enough to keep the analysis of every pass (the tables and maps take every ray, the logs the first
    # "analysis_rays"; the passes are merged). The CPU solver is 40 % slower with the analysis on: a CPU run that may stop early
    # gets it from one extra pass over its first rays when it is done (see below) instead of during the growing passes.
    do_analysis = analysis_wanted and (solver_name == "gpu" or not until_noise)
    final_analysis = analysis_wanted and until_noise and solver_name == "cpu"
    # a live run is traced in growing passes and saved after each one, so that its results can be looked at while it runs
    sizes: list[int] = []
    if live or until_noise:
        first = max(2000, min(n_rays, 4000)) if live else min(n_rays, FIRST_PASS_RAYS)
        while sum(sizes) < n_rays:
            sizes.append(min(first * 2 ** len(sizes), n_rays - sum(sizes)))
    else:
        sizes = [n_rays]
    noise_now = None

    paths_out: list[dict] = []
    tallies: list = [None] * nch
    seeds = [int(settings["seed"]) + 7919 * i for i in range(nch)]
    seen_led: set[str] = set()
    steps = 0
    done_rays = 0
    for pass_no, size in enumerate(sizes):
        for i, src in enumerate(used):
            want_paths = 0
            if pass_no == 0 and src.led_id not in seen_led and settings["preview_paths"] > 0:
                want_paths = int(settings["preview_paths"])
                seen_led.add(src.led_id)

            def prog(f: float, i=i, src=src, size=size) -> None:
                frac = (done_rays + (i + f) / nch * size) / n_rays
                progress(f"Tracing {src.led_name} / {src.channel_name} ({i + 1}/{nch}), {int(done_rays + f * size):,} of {n_rays:,} rays each", frac)

            tl = solver.trace_channel(
                scene, src, size, seed=seeds[i], start=done_rays,
                rr_threshold=float(settings["rr_threshold"]), max_bounces=int(settings["max_bounces"]),
                theta_bins=nth, record_paths=want_paths, max_path_points=int(settings["max_path_points"]),
                progress=prog, analysis=do_analysis, log_rays=log_rays,
            )
            if tallies[i] is not None and tallies[i].analysis is not None and tl.analysis is not None:
                _merge_analysis(tallies[i].analysis, tl.analysis)  # the next pass of the same channel: the logs and tables add up
                tl.analysis = tallies[i].analysis
            tallies[i] = tl
            steps += tl.steps
            arr["ex"][i] += tl.ex
            arr["cone"][i] += tl.cone
            arr["ex2"][i] += tl.ex2
            arr["cone2"][i] += tl.cone2
            if tl.ring is not None:
                arr["ring"][i] += tl.ring
                arr["ring2"][i] += tl.ring2
            arr["dirh"][i] += tl.dirh
            arr["det_pow"][i] += tl.det_pow
            if tl.rcv_pow is not None:
                arr["rcv_pow"][i] += tl.rcv_pow
            arr["abs_vol"][i] += tl.abs_vol
            arr["abs_surf"][i] += tl.abs_surf
            arr["special"][i] += tl.special
            arr["accounted"][i] += tl.total_accounted()
            if want_paths and tl.paths is not None:
                for p in range(want_paths):
                    n = int(tl.path_n[p])
                    if n >= 2:
                        pts = np.round(tl.paths[p, :n], 3).tolist()
                        paths_out.append({"led": src.led_id, "channel": src.channel_id, "points": pts})
        done_rays += size
        for i, src in enumerate(used):
            arr["n_rays"][i] = done_rays
            arr["w0"][i] = src.flux_w / done_rays
            arr["flux_lm"][i] = src.flux_lm
        if live:
            _save_partial(rdir, arr, scene_doc, paths_out, done_rays, n_rays, first=(pass_no == 0))
        if until_noise:
            noise_now = median_map_noise(arr, scene, used, resolved[0]["drive"])
            progress(f"Traced {done_rays:,} rays per channel, map noise {noise_now * 100:.1f} % (target {target * 100:.1f} %)", min(done_rays / n_rays, 0.98))
            if noise_now <= target:
                break

    if final_analysis:
        # Trace the first rays of every channel once more, now with analysis. A ray depends only on its number and the
        # channel's seed, so these are the very rays already in the maps. The tables are summed over fewer rays than the
        # maps, so they are scaled up to the same footing (an unbiased, noisier estimate); the ray logs are not scaled.
        m = min(done_rays, log_rays)
        scale = done_rays / m
        for i, src in enumerate(used):

            def prog_a(f: float, i=i, src=src) -> None:
                progress(f"Analysis pass: {src.led_name} / {src.channel_name} ({i + 1}/{nch}), {m:,} rays", 0.98 + 0.01 * (i + f) / nch)

            tl = tracer.trace_channel(
                scene, src, m, seed=seeds[i], start=0,
                rr_threshold=float(settings["rr_threshold"]), max_bounces=int(settings["max_bounces"]),
                theta_bins=nth, progress=prog_a, analysis=True, log_rays=log_rays,
            )
            a = tl.analysis
            for name in ("face_t", "face_from", "face_to", "grp_map", "touch_map", "tot_map"):
                setattr(a, name, getattr(a, name) * scale)
            tallies[i] = tl

    progress("Saving results", 0.99)
    arr["accounted"] = arr["accounted"] / done_rays
    # atomically, like the live passes: the results panel and the photoreal view read this file while a run is still saving, and a
    # half-written compressed file is "not a zip file"
    final_tmp = rdir / "tallies.tmp.npz"
    np.savez_compressed(final_tmp, **arr)
    _replace_retrying(final_tmp, rdir / "tallies.npz")
    store.write_json(rdir / "scene.json", scene_doc)
    store.write_json(rdir / "paths.json", {"paths": paths_out})
    analysis_warning = None
    if analysis_wanted:
        progress("Saving analysis", 0.995)
        try:
            from . import analysis

            analysis.save(rdir, scene, used, tallies, settings, seeds, done_rays)
        except Exception as exc:  # the results are complete without it
            analysis_warning = f"The analysis data could not be saved: {exc}"
    run = store.load_run(rid, runid) or {}
    elapsed = time.time() - t_start
    run["timing"] = {"seconds": round(elapsed, 2), "raysTotal": done_rays * nch, "raysPerSecond": round(done_rays * nch / max(elapsed, 1e-9))}
    run["solver"] = solver_name
    run["mesh"] = {"quality": opt_doc.get("quality", "normal"), "triangles": int(opt_doc.get("triangles", 0))}
    if solver_name == "gpu":
        run["solverName"] = solvers.gpu_status()[1]
    run["raysTraced"] = int(done_rays)  # less than settings.rays when the run stopped at its noise target
    if noise_now is not None:
        run["mapNoise"] = round(float(noise_now), 4)
    run["warnings"] = list(scene.warnings) + ([analysis_warning] if analysis_warning else [])
    run["hasAnalysis"] = bool(analysis_wanted and not analysis_warning)
    run["maxAccountingError"] = float(np.max(np.abs(arr["accounted"] - 1.0)))
    store.save_run(rid, runid, run)
    # summary (needs the saved files)
    from .results import RunData

    run = store.load_run(rid, runid) or {}
    run["summary"] = RunData.load(rid, runid).summary()
    store.save_run(rid, runid, run)
    progress("Done", 1.0)


def _merge_analysis(a, b) -> None:
    """Add the analysis of a later pass (b) of a channel to that of the earlier passes (a)."""
    for name in ("face_t", "face_from", "face_to", "grp_map", "touch_map", "tot_map"):
        setattr(a, name, getattr(a, name) + getattr(b, name))
    for kind in ("dep", "end"):
        la, lb = getattr(a, kind), getattr(b, kind)
        if lb:
            setattr(a, kind, {k: np.concatenate([la[k], lb[k]]) for k in lb} if la else lb)


def one_channel_per_led(used: list, resolved: list[dict]) -> list:
    """Keep only the channel that carries the most light of each LED (luminous flux times the drive it gets over all states).

    The other channels are not traced at all, so the result shows the spread of that one channel's light: right for judging how the
    light is distributed, not for colour or absolute luminance. The order of the channels is kept.
    """
    best: dict[str, tuple[float, object]] = {}
    for src in used:
        score = float(src.flux_lm) * sum(float(st["drive"].get(src.key, 0.0)) for st in resolved)
        cur = best.get(src.led_id)
        if cur is None or score > cur[0]:
            best[src.led_id] = (score, src)
    keep = {id(s) for _, s in best.values()}
    return [s for s in used if id(s) in keep]


def median_map_noise(arr: dict, scene, used: list, drive: dict) -> float:
    """Median relative standard error per exit-map pixel (viewing cone) of one state: the number a noise target is checked against.

    Pixels with too little surface in them (the edge of the exit face) are left out; a lit pixel that has caught no ray yet counts as 100 %.
    """
    wch = arr["w0"] * np.array([float(drive.get(s.key, 0.0)) for s in used])
    if not (wch > 0).any():
        return 1.0
    y = np.tensordot(wch, arr["cone"][..., 1], axes=(0, 0))
    var = np.tensordot(wch**2, arr["cone2"], axes=(0, 0))
    worst = 0.0
    for det in scene.detectors:
        sl = slice(det.offset, det.offset + det.nu * det.nv)
        area = det.area_map.reshape(-1)
        pos = area > 0
        if not pos.any():
            continue
        valid = area > 0.3 * np.median(area[pos])
        yy, vv = y[sl], var[sl]
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(yy > 0, np.sqrt(vv) / yy, 1.0)
        worst = max(worst, float(np.median(np.minimum(rel[valid], 1.0))))
    return worst


def _replace_retrying(tmp, dest) -> None:
    """``os.replace`` that waits for a reader: Windows refuses to replace a file that is open at this very moment."""
    import os

    for attempt in range(60):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if attempt == 59:
                raise
            time.sleep(0.05)


def _save_partial(rdir, arr: dict, scene_doc: dict, paths_out: list, done: int, total: int, first: bool) -> None:
    """Write the tallies of the passes so far (atomically: a reader sees the old or the new file, never half of one)."""
    part = dict(arr)
    part["accounted"] = arr["accounted"] / max(done, 1)
    tmp = rdir / "tallies.tmp.npz"
    np.savez(tmp, **part)
    if first:  # these do not change from pass to pass
        store.write_json(rdir / "scene.json", scene_doc)
        store.write_json(rdir / "paths.json", {"paths": paths_out})
    _replace_retrying(tmp, rdir / "tallies.npz")
    store.write_json(rdir / "live.json", {"rays": int(done), "target": int(total), "t": time.time()})


def _channel_color(src) -> list[int]:
    from .engine import spectral

    xyz = spectral.xyz_of_spectrum(src.spec)
    rgb = spectral.xyz_to_srgb(xyz[None, :], white_y=float(xyz[1]) * 1.2)[0]
    return [int(round(float(c) * 255)) for c in np.clip(rgb, 0, 1)]
