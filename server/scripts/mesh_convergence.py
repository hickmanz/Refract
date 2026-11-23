r"""Mesh convergence check: does a run's result depend on the optical mesh?

    cd server; ..\.venv\Scripts\python.exe scripts\mesh_convergence.py <revision id> <run id> [quality ...]

Meshes the revision's STEP file again at each quality (default: normal fine) as new revisions of the same variant,
the same way as *Re-mesh* in the app, traces each with the exact setup, library and settings snapshot of the given
run, and prints efficiency, uniformity, profile peak/valley and the energy budget side by side with the original.
The original revision and its runs are not touched. Analysis passes are skipped (they do not change the results).
"""

import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from refract import run_job, store  # noqa: E402
from refract.optical_mesh import QUALITY  # noqa: E402
from refract.results import RunData  # noqa: E402


def remesh(rid: str, quality: str) -> str:
    meta = store.load_rev(rid)
    project = store.load_project(meta["projectId"])
    variant = store.variant_of_rev(project, meta)
    new_rid = store.new_id()
    ndir = store.rev_dir(new_rid)
    ndir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(store.rev_dir(rid) / "source.step", ndir / "source.step")
    base = meta.get("label") or Path(meta.get("filename", "")).stem
    store.save_rev(
        new_rid,
        {
            "id": new_rid,
            "filename": meta.get("filename", "model.step"),
            "size": meta.get("size", 0),
            "created": time.time(),
            "status": "queued",
            "projectId": meta["projectId"],
            "label": f"{base} ({quality} mesh)",
            "meshQuality": quality,
            "remeshedFrom": rid,
        },
    )
    store.add_revision(meta["projectId"], new_rid, variant["id"] if variant else None)
    if run_job.run_import(new_rid) != 0:
        raise RuntimeError(f"import failed: {store.load_rev(new_rid).get('error')}")
    return new_rid


def rerun(rid: str, base_rid: str, base_runid: str, name: str) -> str:
    src = store.run_dir(base_rid, base_runid)
    base = store.load_run(base_rid, base_runid)
    meta = store.load_rev(rid)
    runid = store.new_id()
    rdir = store.run_dir(rid, runid)
    rdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "setup.json", rdir / "setup.json")
    shutil.copy2(src / "library.json", rdir / "library.json")
    settings = {**base["settings"], "analysis": False}
    store.save_run(rid, runid, {"id": runid, "revisionId": rid, "projectId": meta["projectId"], "variantId": meta.get("variantId"),
                                "name": name, "created": time.time(), "status": "queued", "settings": settings})
    if run_job.run_sim(rid, runid) != 0:
        raise RuntimeError(f"run failed: {store.load_run(rid, runid).get('error')}")
    return runid


def metrics(rid: str, runid: str) -> dict:
    rd = RunData.load(rid, runid)
    states = rd.scene["states"]
    state = next((s for s in states if "white" in s["name"].lower()), states[0])
    r = rd.state_result(state["id"], with_led_maps=False)
    d = r["detectors"][0]
    prof = np.array([x for x in d["profileU"] if x is not None], dtype=float)
    core = prof[len(prof) // 20 : len(prof) - len(prof) // 20]  # leave out the ends of the strip
    run = store.load_run(rid, runid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    loss = {l["name"]: l["fraction"] for l in r["loss"]}
    return {
        "triangles": doc["triangles"],
        "efficiency": d["efficiency"],
        "mean": d["stats"]["mean"],
        "cv": d["stats"]["cv"],
        "minOverMax": d["stats"]["minOverMax"],
        "peakValley": float(core.max() / core.min()),
        "mapNoise": run.get("mapNoise"),
        "loss": loss,
    }


def main() -> None:
    rid, runid = sys.argv[1], sys.argv[2]
    qualities = sys.argv[3:] or ["normal", "fine"]
    for q in qualities:
        if q not in QUALITY:
            raise SystemExit(f"unknown quality {q!r}")
    rows = {"original": metrics(rid, runid)}
    for q in qualities:
        t0 = time.time()
        new_rid = remesh(rid, q)
        print(f"{q}: revision {new_rid} meshed in {time.time() - t0:.0f} s", flush=True)
        t0 = time.time()
        new_run = rerun(new_rid, rid, runid, f"Mesh check ({q})")
        print(f"{q}: run {new_run} traced in {time.time() - t0:.0f} s", flush=True)
        rows[q] = metrics(new_rid, new_run)

    names = list(rows)
    print()
    print(f"{'':28}" + "".join(f"{n:>14}" for n in names))
    for key, fmt in (("triangles", "{:,.0f}"), ("efficiency", "{:.4f}"), ("mean", "{:,.0f}"), ("cv", "{:.4f}"),
                     ("minOverMax", "{:.4f}"), ("peakValley", "{:.4f}"), ("mapNoise", "{:.4f}")):
        print(f"{key:28}" + "".join(f"{fmt.format(rows[n][key]):>14}" for n in names))
    print("energy budget (share of emitted):")
    for lname in rows["original"]["loss"]:
        vals = [rows[n]["loss"].get(lname, 0.0) for n in names]
        if max(vals) >= 0.001:
            print(f"  {lname[:26]:26}" + "".join(f"{v * 100:13.2f}%" for v in vals))


if __name__ == "__main__":
    main()
