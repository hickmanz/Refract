"""FastAPI application: local-only REST API + static hosting of the built frontend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import desktop, procutil, store, updater
from .__version__ import __version__
from .jobs import JobManager

if procutil.is_frozen():
    FRONTEND_DIST = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "frontend" / "dist"
else:
    FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

REV_FILES = {"model.glb", "model.json", "optical.json"}

app = FastAPI(title="Refract", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
jobs = JobManager()


# -- helpers -----------------------------------------------------------------------------
def _get_rev(rid: str) -> dict:
    try:
        meta = store.load_rev(rid)
    except ValueError:
        meta = None
    if meta is None:
        raise HTTPException(404, "Revision not found")
    return meta


def _get_project(pid: str) -> dict:
    try:
        project = store.load_project(pid)
    except ValueError:
        project = None
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _get_run(runid: str) -> dict:
    try:
        run = store.find_run(runid)
    except ValueError:
        run = None
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


def _status_payload(meta: dict, key: tuple[str, str, str]) -> dict:
    out = dict(meta)
    if meta.get("status") in ("queued", "running"):
        out["progress"] = store.read_json(
            (store.rev_dir(key[1]) if key[0] == "import" else store.run_dir(key[1], key[2]) / ("render" if key[0] == "render" else "")) / "progress.json",
            {"stage": "queued", "phase": "", "fraction": 0.0},
        )
    else:
        out["progress"] = {"stage": meta.get("status"), "phase": "", "fraction": 1.0 if meta.get("status") == "ready" else 0.0}
    return out


def _rev_payload(meta: dict) -> dict:
    return _status_payload(meta, ("import", meta["id"], ""))


def _run_payload(run: dict, with_metrics: bool = False) -> dict:
    out = _status_payload(run, ("sim", run["revisionId"], run["id"]))
    if not with_metrics:
        out.pop("setup", None)
    return out


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "app": "refract", "mode": desktop.mode, "version": __version__, "dataDir": str(store.data_dir())}


@app.get("/api/solvers")
def solvers_info() -> dict:
    """The solvers a run can use and whether this machine can run them (the GPU one needs Warp and a CUDA device)."""
    from .engine import solvers

    return solvers.describe()


# -- projects ----------------------------------------------------------------------------
def _project_payload(project: dict, with_revisions: bool = False) -> dict:
    metas = [m for m in (store.load_rev(r) for r in project.get("revisions", [])) if m]
    metas.sort(key=lambda m: m.get("created", 0), reverse=True)  # newest first
    out = dict(project)
    out["latest"] = _rev_payload(metas[0]) if metas else None
    # a variant's revisions are derived from each revision's variantId (oldest first); they are not stored in the variant
    by_variant: dict[str, list[dict]] = {}
    for m in reversed(metas):
        by_variant.setdefault(m.get("variantId"), []).append(m)
    out["variants"] = [
        {
            **v,
            "revisions": [m["id"] for m in by_variant.get(v["id"], [])],
            "latest": _rev_payload(by_variant[v["id"]][-1]) if by_variant.get(v["id"]) else None,
        }
        for v in project.get("variants", [])
    ]
    if with_revisions:
        out["revisionList"] = [_rev_payload(m) for m in metas]
    return out


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [{**_project_payload(p), "sizeBytes": store.project_size(p)} for p in store.list_projects()]


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    project = _get_project(pid)
    store.touch_project(pid)
    return _project_payload(project, with_revisions=True)


class ProjectPatch(BaseModel):
    name: str | None = None


@app.post("/api/projects")
def create_project(body: ProjectPatch) -> dict:
    name = (body.name or "").strip() or "Untitled"
    return _project_payload(store.create_project(name), with_revisions=True)


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch) -> dict:
    project = _get_project(pid)
    if body.name and body.name.strip():
        project["name"] = body.name.strip()
        store.save_project(project)
    return _project_payload(project)


def _get_variant(project: dict, vid: str) -> dict:
    variant = store.find_variant(project, vid)
    if variant is None:
        raise HTTPException(404, "Variant not found")
    return variant


def _get_design(project: dict, did: str) -> dict:
    design = store.find_design(project, did)
    if design is None:
        raise HTTPException(404, "Design not found")
    return design


def _cancel_revision_jobs(rids: list[str]) -> None:
    for rid in rids:
        jobs.cancel(("import", rid, ""))
        for run in store.list_runs(rid):
            jobs.cancel(("sim", rid, run["id"]))


class DesignBody(BaseModel):
    name: str | None = None
    variantName: str | None = None  # name of the design's first variant
    copyFrom: str | None = None  # id of the variant whose setup the first variant starts with


@app.post("/api/projects/{pid}/designs")
def create_design(pid: str, body: DesignBody) -> dict:
    """A new design with one variant (the geometry still has to be uploaded as its first revision)."""
    project = _get_project(pid)
    if body.copyFrom:
        _get_variant(project, body.copyFrom)
    name = (body.name or "").strip() or f"Design {len(project.get('designs', [])) + 1}"
    store.create_design(pid, name, (body.variantName or "").strip() or None, body.copyFrom)
    return _project_payload(_get_project(pid), with_revisions=True)


@app.patch("/api/projects/{pid}/designs/{did}")
def patch_design(pid: str, did: str, body: DesignBody) -> dict:
    project = _get_project(pid)
    _get_design(project, did)
    if body.name and body.name.strip():
        store.rename_design(pid, did, body.name.strip())
    return _project_payload(_get_project(pid), with_revisions=True)


@app.delete("/api/projects/{pid}/designs/{did}")
def delete_design(pid: str, did: str) -> dict:
    project = _get_project(pid)
    _get_design(project, did)
    if len(project["designs"]) <= 1:
        raise HTTPException(400, "A project needs at least one design")
    for v in store.design_variants(project, did):
        _cancel_revision_jobs([m["id"] for m in store.variant_revisions(project, v["id"])])
    store.delete_design(pid, did)
    return _project_payload(_get_project(pid), with_revisions=True)


class VariantBody(BaseModel):
    name: str | None = None
    designId: str | None = None  # the design it goes into (new variant: default the design of copyFrom; patch: moves the variant)
    copyFrom: str | None = None  # id of the variant whose setup the new one starts with


@app.post("/api/projects/{pid}/variants")
def create_variant(pid: str, body: VariantBody) -> dict:
    project = _get_project(pid)
    if body.copyFrom:
        _get_variant(project, body.copyFrom)
    if body.designId:
        _get_design(project, body.designId)
    design_id = body.designId or (store.find_variant(project, body.copyFrom) or {}).get("designId") or (project.get("designs") or [{}])[0].get("id")
    count = len(store.design_variants(project, design_id))
    name = (body.name or "").strip() or f"Variant {count + 1}"
    store.create_variant(pid, name, design_id, body.copyFrom)
    return _project_payload(_get_project(pid), with_revisions=True)


@app.patch("/api/projects/{pid}/variants/{vid}")
def patch_variant(pid: str, vid: str, body: VariantBody) -> dict:
    project = _get_project(pid)
    _get_variant(project, vid)
    if body.name and body.name.strip():
        store.rename_variant(pid, vid, body.name.strip())
    if body.designId:
        _get_design(project, body.designId)
        store.move_variant(pid, vid, body.designId)
    return _project_payload(_get_project(pid), with_revisions=True)


@app.delete("/api/projects/{pid}/variants/{vid}")
def delete_variant(pid: str, vid: str) -> dict:
    project = _get_project(pid)
    variant = _get_variant(project, vid)
    if len(store.design_variants(project, variant.get("designId"))) <= 1:
        raise HTTPException(400, "A design needs at least one variant; delete the design instead")
    _cancel_revision_jobs([m["id"] for m in store.variant_revisions(project, vid)])
    store.delete_variant(pid, vid)
    return _project_payload(_get_project(pid), with_revisions=True)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    project = _get_project(pid)
    for rid in project.get("revisions", []):
        jobs.cancel(("import", rid, ""))
        for run in store.list_runs(rid):
            jobs.cancel(("sim", rid, run["id"]))
    store.delete_project(pid)
    return {"ok": True}


# -- revisions (uploaded CAD) -------------------------------------------------------------
@app.post("/api/revisions")
async def create_revision(
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    project_name: str | None = Form(None),
    variant_id: str | None = Form(None),
    variant_name: str | None = Form(None),
    copy_setup_from: str | None = Form(None),
    design_id: str | None = Form(None),
    design_name: str | None = Form(None),
    mesh_quality: str | None = Form(None),
) -> dict:
    """Upload a STEP file as a revision.

    project_id: existing id, "new", or empty (match by file name).
    variant_id: an existing variant of the project (the file becomes its next revision), "new" (a new variant: optional
    variant_name, and copy_setup_from, the variant whose setup it starts with) or empty (the project's first variant).
    design_id: for a new variant, the design it goes into: an existing design, "new" (a new design called design_name,
    holding the new variant as its first), or empty (the design of copy_setup_from, else the project's first design).
    """
    from .optical_mesh import QUALITY

    name = file.filename or "model.step"
    if Path(name).suffix.lower() not in (".step", ".stp"):
        raise HTTPException(400, "Please upload a .step or .stp file")
    if mesh_quality and mesh_quality not in QUALITY:
        raise HTTPException(400, f"Mesh quality must be one of {', '.join(QUALITY)}")
    project = None
    if project_id and project_id != "new":
        project = _get_project(project_id)
    elif not project_id:
        project = store.find_project_by_stem(store.stem_of(name))
    if project is not None and variant_id and variant_id != "new":
        _get_variant(project, variant_id)
    if project is not None and copy_setup_from:
        _get_variant(project, copy_setup_from)
    if design_id:
        if variant_id != "new":
            raise HTTPException(400, "design_id only applies to a new variant")
        if project is not None and design_id != "new":
            _get_design(project, design_id)

    tmp_dir = Path(tempfile.gettempdir()) / "refract-uploads"
    tmp_dir.mkdir(exist_ok=True)
    tmp = tmp_dir / f"{int(time.time() * 1000)}.part"
    size = 0
    with tmp.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            fh.write(chunk)
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "The file is empty")

    if project is None:
        project = store.create_project((project_name or Path(name).stem).strip() or Path(name).stem)
        variant_id = None  # a new project starts with its first variant
    if variant_id == "new":
        vname = (variant_name or "").strip()
        if design_id == "new":
            dname = (design_name or "").strip() or f"Design {len(project.get('designs', [])) + 1}"
            variant_id = store.create_design(project["id"], dname, vname or None, copy_setup_from or None)[1]["id"]
        else:
            did = design_id or (store.find_variant(project, copy_setup_from) or {}).get("designId") or None
            vname = vname or f"Variant {len(store.design_variants(project, did or (project.get('designs') or [{}])[0].get('id'))) + 1}"
            variant_id = store.create_variant(project["id"], vname, did, copy_setup_from or None)["id"]
    rid = store.new_id()
    rdir = store.rev_dir(rid)
    rdir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp), rdir / "source.step")
    store.save_rev(
        rid,
        {
            "id": rid,
            "filename": name,
            "size": size,
            "created": time.time(),
            "status": "queued",
            "projectId": project["id"],
            "label": "",
            "meshQuality": mesh_quality or "normal",
        },
    )
    store.add_revision(project["id"], rid, variant_id or None)
    jobs.submit(("import", rid, ""))
    return {"id": rid, "projectId": project["id"]}


@app.get("/api/revisions/{rid}")
def get_revision(rid: str) -> dict:
    return _rev_payload(_get_rev(rid))


class RevisionPatch(BaseModel):
    label: str | None = None


@app.patch("/api/revisions/{rid}")
def patch_revision(rid: str, body: RevisionPatch) -> dict:
    meta = _get_rev(rid)
    if body.label is not None:
        meta["label"] = body.label.strip()
        store.save_rev(rid, meta)
    return _rev_payload(meta)


@app.delete("/api/revisions/{rid}")
def delete_revision(rid: str) -> dict:
    _get_rev(rid)
    jobs.cancel(("import", rid, ""))
    for run in store.list_runs(rid):
        jobs.cancel(("sim", rid, run["id"]))
    store.delete_revision(rid)
    return {"ok": True}


@app.post("/api/revisions/{rid}/reimport")
def reimport(rid: str) -> dict:
    _get_rev(rid)
    if jobs.is_active(("import", rid, "")):
        raise HTTPException(409, "Already running")
    if not (store.rev_dir(rid) / "source.step").exists():
        raise HTTPException(410, "Source file is no longer available")
    jobs.submit(("import", rid, ""))
    return {"id": rid}


class RemeshBody(BaseModel):
    quality: str


@app.post("/api/revisions/{rid}/remesh")
def remesh(rid: str, body: RemeshBody) -> dict:
    """Mesh the same STEP file again at another quality, as a new revision of the same variant.

    The revision itself is left alone: its runs replay their rays against its optical mesh, so it must not change.
    """
    from .optical_mesh import QUALITY

    meta = _get_rev(rid)
    if body.quality not in QUALITY:
        raise HTTPException(400, f"Mesh quality must be one of {', '.join(QUALITY)}")
    src = store.rev_dir(rid) / "source.step"
    if not src.exists():
        raise HTTPException(410, "Source file is no longer available")
    pid = meta.get("projectId")
    project = store.load_project(pid) if pid else None
    if project is None:
        raise HTTPException(409, "The revision does not belong to a project")
    variant = store.variant_of_rev(project, meta)
    new_rid = store.new_id()
    ndir = store.rev_dir(new_rid)
    ndir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, ndir / "source.step")
    base = meta.get("label") or Path(meta.get("filename", "")).stem
    store.save_rev(
        new_rid,
        {
            "id": new_rid,
            "filename": meta.get("filename", "model.step"),
            "size": meta.get("size", src.stat().st_size),
            "created": time.time(),
            "status": "queued",
            "projectId": pid,
            "label": f"{base} ({body.quality} mesh)",
            "meshQuality": body.quality,
            "remeshedFrom": rid,
        },
    )
    store.add_revision(pid, new_rid, variant["id"] if variant else None)
    jobs.submit(("import", new_rid, ""))
    return {"id": new_rid, "projectId": pid}


class DuplicateBody(BaseModel):
    name: str | None = None


@app.post("/api/revisions/{rid}/duplicate")
def duplicate_revision(rid: str, body: DuplicateBody) -> dict:
    """Add a variant to this revision's design without uploading anything: the same CAD, imported again as Rev 1 of the
    new variant, with a copy of this variant's setup. For trying other materials or LEDs on one geometry.

    The runs of the revision are not copied; the new variant starts with none.
    """
    meta = _get_rev(rid)
    src = store.rev_dir(rid) / "source.step"
    if not src.exists():
        raise HTTPException(410, "Source file is no longer available")
    pid = meta.get("projectId")
    project = store.load_project(pid) if pid else None
    if project is None:
        raise HTTPException(409, "The revision does not belong to a project")
    parent = store.variant_of_rev(project, meta)
    if parent is None:
        raise HTTPException(409, "The revision does not belong to a variant")
    name = (body.name or "").strip() or f"{parent['name']} (copy)"
    variant = store.create_variant(pid, name, parent.get("designId"), parent["id"])
    new_rid = store.new_id()
    ndir = store.rev_dir(new_rid)
    ndir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, ndir / "source.step")
    store.save_rev(
        new_rid,
        {
            "id": new_rid,
            "filename": meta.get("filename", "model.step"),
            "size": meta.get("size", src.stat().st_size),
            "created": time.time(),
            "status": "queued",
            "projectId": pid,
            "label": "",
            "meshQuality": meta.get("meshQuality", "normal"),
        },
    )
    store.add_revision(pid, new_rid, variant["id"])
    jobs.submit(("import", new_rid, ""))
    return {"id": new_rid, "projectId": pid, "variantId": variant["id"]}


@app.post("/api/revisions/{rid}/cancel")
def cancel_import(rid: str) -> dict:
    _get_rev(rid)
    return {"cancelled": jobs.cancel(("import", rid, ""))}


@app.get("/api/revisions/{rid}/files/{name}")
def get_rev_file(rid: str, name: str) -> Response:
    _get_rev(rid)
    if name not in REV_FILES:
        raise HTTPException(404, "Unknown file")
    path = store.rev_dir(rid) / name
    if not path.exists():
        raise HTTPException(404, "File not available yet")
    media = "model/gltf-binary" if name.endswith(".glb") else "application/json"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})


@app.get("/api/revisions/{rid}/parts")
def get_parts(rid: str) -> dict:
    """Optical parts of a revision (instances with mesh quality)."""
    _get_rev(rid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    if doc is None:
        raise HTTPException(404, "The optical mesh is not ready yet")
    return doc


@app.get("/api/revisions/{rid}/optical-mesh")
def get_optical_mesh(rid: str, instances: str) -> Response:
    """The ray-traced triangles of some parts (comma-separated instance ids), for drawing the optical mesh.

    Little-endian binary: uint32 nverts, uint32 ntris, float32 positions (nverts x 3, world mm), uint32 indices
    (ntris x 3), float32 facet angle per triangle (largest angle in degrees between the flat triangle normal and the
    smoothed normals at its corners: how much the faceting departs from the CAD surface).
    """
    import numpy as np

    from .optical_mesh import facet_angles_deg, load_optical

    _get_rev(rid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    if doc is None:
        raise HTTPException(404, "The optical mesh is not ready yet")
    wanted = {s for s in instances.split(",") if s}
    insts = [i for i in doc["instances"] if i["id"] in wanted]
    if not insts:
        raise HTTPException(404, "None of these parts is in the optical mesh")
    opt = load_optical(store.rev_dir(rid) / "optical.npz")
    pos_l, idx_l, ang_l = [], [], []
    base = 0
    for inst in insts:
        v0, nv = inst["vertOffset"], inst["vertCount"]
        t0, nt = inst["triOffset"], inst["triCount"]
        tris = opt["tris"][t0 : t0 + nt].astype(np.int64) - v0
        verts = opt["verts"][v0 : v0 + nv]
        pos_l.append(verts.astype("<f4"))
        idx_l.append((tris + base).astype("<u4"))
        ang_l.append(facet_angles_deg(verts, opt["normals"][v0 : v0 + nv], tris).astype("<f4"))
        base += nv
    pos, idx, ang = np.concatenate(pos_l), np.concatenate(idx_l), np.concatenate(ang_l)
    head = np.array([len(pos), len(idx)], dtype="<u4")
    return Response(head.tobytes() + pos.tobytes() + idx.tobytes() + ang.tobytes(), media_type="application/octet-stream")


@app.get("/api/revisions/{rid}/faces/{instance_id}/{face}")
def get_face(rid: str, instance_id: str, face: int) -> dict:
    """Centroid, normal and area of one B-Rep face (used to place LEDs and detectors)."""
    from .optical_mesh import face_info, load_optical

    _get_rev(rid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    if doc is None:
        raise HTTPException(404, "The optical mesh is not ready yet")
    inst = next((i for i in doc["instances"] if i["id"] == instance_id), None)
    if inst is None:
        raise HTTPException(404, "Part not found")
    info = face_info(load_optical(store.rev_dir(rid) / "optical.npz"), inst, face)
    if info is None:
        raise HTTPException(404, "Face not found")
    return info


# -- library (materials, finishes, LEDs) -----------------------------------------------------
def _library_payload() -> dict:
    from . import library as lib

    out = lib.get_library()
    builtin = {k: set(v) for k, v in lib._BUILTINS.items()}  # noqa: SLF001
    for kind, entries in out.items():
        for id_, doc in entries.items():
            doc["builtin"] = id_ in builtin[kind]
    return out


@app.get("/api/library")
def get_library() -> dict:
    return _library_payload()


@app.post("/api/library/rays")
async def import_ray_file(request: Request) -> dict:
    """Import a text ray list (x y z l m n flux [wavelength]); returns a source description for an LED channel."""
    from .engine import rayfile

    raw = await request.body()
    if len(raw) > 300 * 1024 * 1024:
        raise HTTPException(413, "The ray file is larger than 300 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            raise HTTPException(400, "This is not a text ray list. Binary or XML ray files need to be exported to text first (x y z l m n flux [wavelength]).")
    try:
        return rayfile.import_text(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/library/{kind}")
def save_library_entry(kind: str, doc: dict) -> dict:
    from . import library as lib

    try:
        return lib.save_entry(kind, doc)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/library/{kind}/{entry_id}")
def delete_library_entry(kind: str, entry_id: str) -> dict:
    from . import library as lib

    try:
        return {"ok": lib.delete_entry(kind, entry_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class MieBody(BaseModel):
    tio2_wt_pct: float = 1.0
    diameter_um: float = 0.3
    host: str = "pc"
    sigma_g: float = 1.3
    efficiency: float = 1.0  # dispersion efficiency: scales mu_s, 1 = ideal dispersion of spheres
    thickness_mm: float = 3.0


@app.post("/api/diffuser/mie")
def mie_calculator(body: MieBody) -> dict:
    """Scattering of TiO2 particles in a host polymer from Mie theory (for the diffuser material editor)."""
    import numpy as np

    from .materials import mie

    try:
        cur = mie.scattering_curves(body.tio2_wt_pct, body.diameter_um, body.host, body.sigma_g, efficiency=body.efficiency)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))
    lam = cur["lambda"]
    mus = cur["mus"]
    ballistic = [float(x) for x in np.exp(-mus * body.thickness_mm)]
    return {
        "lambda": lam.tolist(),
        "mus": [round(float(x), 5) for x in mus],
        "g": [round(float(x), 4) for x in cur["g"]],
        "musReduced": [round(float(x), 5) for x in cur["mus_reduced"]],
        "volumeFraction": float(cur["volume_fraction"][0]),
        "ballisticTransmission": [round(x, 5) for x in ballistic],
        "thicknessMm": body.thickness_mm,
    }


class FitBody(BaseModel):
    tio2_wt_pct: float | None = None
    samples: list[dict]  # [{thickness_mm, total_transmission, haze?}]
    host: str = "pc"
    rays: int = 40000
    mode: str = "mus_g"  # "mus_g": fit mu_s and g on a grey scattering model


@app.post("/api/diffuser/fit")
def fit_diffuser(body: FitBody) -> dict:
    """Fit a grey scattering coefficient and anisotropy to datasheet total transmission (and haze) vs thickness."""
    from .materials import fit

    try:
        return fit.fit_slab(body.samples, host=body.host, rays=body.rays)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# -- setup (per variant: parts, LEDs, states, detectors) ------------------------------------
EMPTY_SETUP = {"parts": [], "leds": [], "states": [], "detectors": []}


@app.get("/api/projects/{pid}/variants/{vid}/setup")
def get_variant_setup(pid: str, vid: str) -> dict:
    _get_variant(_get_project(pid), vid)
    return store.load_setup(pid, vid) or dict(EMPTY_SETUP)


@app.put("/api/projects/{pid}/variants/{vid}/setup")
def put_variant_setup(pid: str, vid: str, setup: dict) -> dict:
    _get_variant(_get_project(pid), vid)
    store.save_setup(pid, setup, vid)
    return setup


def _preflight(rid: str, setup: dict, library: dict) -> dict:
    """Build the scene and sources without tracing; raises HTTPException(400) for setup problems."""
    from .engine import sources
    from .engine.scene import build_scene
    from .optical_mesh import load_optical

    mdir = store.rev_dir(rid)
    doc = store.read_json(mdir / "optical.json")
    if doc is None:
        raise HTTPException(409, "The revision is not imported yet")
    try:
        opt = load_optical(mdir / "optical.npz")
        scene = build_scene(opt, doc, setup, library, build_rays=False)
        srcs = sources.build_channel_sources(setup, doc, scene, opt, library)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "warnings": scene.warnings,
        "bodies": [
            {"path": b["path"], "material": b["material"], "finish": b["finish"], "transparent": b["transparent"]} for b in scene.bodies
        ],
        "detectors": [
            {"id": d.id, "name": d.name, "nu": int(d.nu), "nv": int(d.nv), "areaMm2": float(d.area_total), "corners": d.corners.tolist(), "n": d.n.tolist(),
             "kind": d.kind, "through": d.through, "view": (d.view if d.view is not None else d.n).tolist(), "origin": d.origin.tolist()}
            for d in scene.detectors
        ],
        "channels": [
            {"key": s.key, "led": s.led_name, "channel": s.channel_name, "fluxLm": s.flux_lm, "origin": s.origin.tolist(), "axis": s.axis.tolist(), "startBody": s.start_body,
             "ex": s.ex.tolist(), "ey": s.ey.tolist(), "size": sources.emitter_extent(s)}
            for s in srcs
        ],
        "triangles": int(len(scene.T)),
        "resolved": scene.resolved,
        "groups": [
            {
                "index": g["index"], "id": g["id"], "name": g["name"], "color": g["color"], "auto": g["auto"], "areaMm2": round(float(g["area"]), 3),
                "faces": [{"instance": f["instance"], "face": f["face"]} for f in g["faces"][:300]],
            }
            for g in scene.groups
        ],
        "groupIssues": scene.group_issues,
    }


@app.post("/api/revisions/{rid}/check")
def check_setup(rid: str, setup: dict | None = None) -> dict:
    """Validate a setup against a revision (defaults to the revision's project setup)."""
    from . import library as lib

    meta = _get_rev(rid)
    if setup is None:
        setup = store.load_setup_for_rev(meta) or {}
    return _preflight(rid, setup, lib.get_library())


@app.post("/api/revisions/{rid}/suggest-setup")
def suggest_setup(rid: str) -> dict:
    from . import setup_suggest

    _get_rev(rid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    if doc is None:
        raise HTTPException(409, "The revision is not imported yet")
    from .optical_mesh import load_optical

    return setup_suggest.suggest(doc, load_optical(store.rev_dir(rid) / "optical.npz"))


@app.post("/api/revisions/{rid}/reconcile-setup")
def reconcile_setup(rid: str) -> dict:
    """The variant's setup adapted to this revision's geometry (LEDs added or removed, states regenerated).

    Nothing is saved: the client shows the result and the user stores it with PUT .../variants/{vid}/setup.
    """
    from . import setup_suggest
    from .optical_mesh import load_optical

    meta = _get_rev(rid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    if doc is None:
        raise HTTPException(409, "The revision is not imported yet")
    suggestion = setup_suggest.suggest(doc, load_optical(store.rev_dir(rid) / "optical.npz"))
    current = store.load_setup_for_rev(meta)
    if not current or not (current.get("leds") or current.get("parts")):
        return {"setup": {k: suggestion[k] for k in ("parts", "leds", "detectors", "states")}, "warnings": suggestion.get("notes", []), "fromSuggestion": True}
    setup, warnings = setup_suggest.reconcile(current, suggestion, doc["instances"])
    return {"setup": setup, "warnings": warnings, "fromSuggestion": False}


@app.post("/api/revisions/{rid}/accept-face-matches")
def accept_face_matches(rid: str, setup: dict | None = None) -> dict:
    """The setup with every face reference that was re-matched on this revision stored as the new face.

    Nothing is saved here: the client applies the result (and its auto-save stores it).
    """
    from .engine import faceref
    from .optical_mesh import load_optical

    meta = _get_rev(rid)
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    if doc is None:
        raise HTTPException(409, "The revision is not imported yet")
    if setup is None:
        setup = store.load_setup_for_rev(meta) or {}
    new, changes = faceref.accept_rematches(setup, load_optical(store.rev_dir(rid) / "optical.npz"), doc)
    return {"setup": new, "changes": changes}


# -- runs -----------------------------------------------------------------------------------
class RunBody(BaseModel):
    name: str | None = None
    settings: dict | None = None


def _launch_run(rid: str, meta: dict, setup: dict, library: dict, settings: dict, name: str | None, extra: dict | None = None) -> dict:
    """Snapshot a setup and library into a new run and queue it."""
    runid = store.new_id()
    rdir = store.run_dir(rid, runid)
    rdir.mkdir(parents=True, exist_ok=True)
    store.write_json(rdir / "setup.json", setup)
    store.write_json(rdir / "library.json", library)
    count = len(store.list_runs(rid)) + 1
    run = {
        "id": runid,
        "revisionId": rid,
        "projectId": meta["projectId"],
        "variantId": meta.get("variantId"),
        "name": (name or "").strip() or f"Run {count}",
        "created": time.time(),
        "status": "queued",
        "settings": settings,
        **(extra or {}),
    }
    store.save_run(rid, runid, run)
    jobs.submit(("sim", rid, runid))
    return run


def _run_settings(body_settings: dict | None) -> dict:
    from .simrun import DEFAULT_SETTINGS

    settings = {**DEFAULT_SETTINGS, **(body_settings or {})}
    if not (1000 <= int(settings["rays"]) <= 50_000_000):
        raise HTTPException(400, "rays must be between 1,000 and 50,000,000 per channel")
    tn = float(settings.get("target_noise") or 0.0)
    if tn != 0.0 and not (0.005 <= tn <= 0.5):
        raise HTTPException(400, "target_noise must be 0 (off) or between 0.005 and 0.5")
    settings["target_noise"] = tn
    settings["one_channel_per_led"] = bool(settings.get("one_channel_per_led"))
    from .engine import solvers

    solver = str(settings.get("solver") or "cpu")
    if solver not in solvers.SOLVERS:
        raise HTTPException(400, f"solver must be one of {', '.join(solvers.SOLVERS)}")
    if solver == "gpu":
        ok, why = solvers.gpu_status()
        if not ok:
            raise HTTPException(400, f"The GPU solver is not available: {why}")
    settings["solver"] = solver
    return settings


@app.post("/api/revisions/{rid}/runs")
def create_run(rid: str, body: RunBody) -> dict:
    from . import library as lib

    meta = _get_rev(rid)
    if meta.get("status") != "ready":
        raise HTTPException(409, "The revision is not imported yet")
    setup = store.load_setup_for_rev(meta)
    if not setup:
        raise HTTPException(400, "This variant has no setup yet")
    library = lib.get_library()
    _preflight(rid, setup, library)
    settings = _run_settings(body.settings)
    return _run_payload(_launch_run(rid, meta, setup, library, settings, body.name))


class SweepBody(BaseModel):
    name: str | None = None
    settings: dict | None = None
    parameter: str
    part: str
    values: list[float]


@app.post("/api/revisions/{rid}/sweeps")
def create_sweep(rid: str, body: SweepBody) -> dict:
    """One run per value of a design parameter (TiO2 loading, particle size, scattering coefficient)."""
    from . import library as lib
    from . import sweep as sweep_mod

    meta = _get_rev(rid)
    if meta.get("status") != "ready":
        raise HTTPException(409, "The revision is not imported yet")
    setup = store.load_setup_for_rev(meta)
    if not setup:
        raise HTTPException(400, "This variant has no setup yet")
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    library = lib.get_library()
    _preflight(rid, setup, library)
    settings = _run_settings(body.settings)
    try:
        variants = sweep_mod.build_variants(setup, library, doc["instances"], body.model_dump())
        for v in variants:  # fail before queueing anything if one variant cannot be built
            _preflight(rid, v["setup"], v["library"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    sweep_id = store.new_id()
    label = (body.name or "").strip() or sweep_mod.PARAMETERS[body.parameter]["label"]
    info = sweep_mod.PARAMETERS[body.parameter]
    runs = []
    for k, v in enumerate(variants):
        extra = {"sweep": {"id": sweep_id, "name": label, "parameter": body.parameter, "label": info["label"], "unit": info["unit"], "value": v["value"], "index": k, "part": body.part}}
        runs.append(_run_payload(_launch_run(rid, meta, v["setup"], v["library"], settings, f"{label}: {v['label']}", extra)))
    return {"sweepId": sweep_id, "runs": runs}

class LiveBody(BaseModel):
    setup: dict | None = None  # the setup as it is in the editor right now (defaults to the saved one)
    rays: int = 400_000  # per channel; traced in growing passes, the results can be read after each one
    overrides: list[dict] = []  # [{"parameter": "tio2_wt_pct", "part": "Light Pipe:1", "value": 0.05}] tried without changing the setup
    max_resolution: int = 200  # live maps are coarser than a full run's so that the first passes are quick
    target_noise: float = 0.0  # > 0: stop before `rays` once the median relative noise per map pixel is below this
    solver: str = "cpu"  # "cpu" or "gpu" (see /api/solvers)


def _drop_run(rid: str, runid: str) -> None:
    """Stops a run (and the live view drawing from it) and deletes its folder."""
    from .render import live_view

    live_view.close_session(runid, wait=True)
    jobs.cancel(("sim", rid, runid))
    store.delete_run(rid, runid)


def _live_runs(rid: str) -> list[dict]:
    return [r for r in store.list_runs(rid) if (r.get("settings") or {}).get("live")]


@app.post("/api/revisions/{rid}/live")
def start_live(rid: str, body: LiveBody) -> dict:
    """Starts (or restarts) the live preview of a revision: a progressive simulation that replaces the previous one."""
    import copy

    from . import library as lib
    from . import sweep as sweep_mod

    meta = _get_rev(rid)
    if meta.get("status") != "ready":
        raise HTTPException(409, "The revision is not imported yet")
    setup = copy.deepcopy(body.setup if body.setup is not None else store.load_setup_for_rev(meta))
    if not setup:
        raise HTTPException(400, "This project has no setup yet")
    library = lib.get_library()
    doc = store.read_json(store.rev_dir(rid) / "optical.json")
    try:
        for ov in body.overrides:
            spec = {"parameter": ov.get("parameter"), "part": ov.get("part"), "values": [ov.get("value")]}
            v = sweep_mod.build_variants(setup, library, doc["instances"], spec, min_values=1)[0]
            setup, library = v["setup"], v["library"]
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    for d in setup.get("detectors", []):
        d["resolution"] = min(int(d.get("resolution", 160)), max(40, int(body.max_resolution)))
    _preflight(rid, setup, library)
    settings = _run_settings({"rays": body.rays, "live": True, "preview_paths": 4, "target_noise": body.target_noise, "solver": body.solver})
    for old in _live_runs(rid):
        _drop_run(rid, old["id"])
    return _run_payload(_launch_run(rid, meta, setup, library, settings, "Live preview"))


@app.get("/api/revisions/{rid}/live")
def get_live(rid: str) -> dict:
    """The newest live preview run of a revision (``{"run": null}`` when there is none)."""
    _get_rev(rid)
    runs = sorted(_live_runs(rid), key=lambda r: r.get("created", 0), reverse=True)
    if not runs:
        return {"run": None}
    out = _run_payload(runs[0])
    out["live"] = store.read_json(store.run_dir(rid, runs[0]["id"]) / "live.json", None)
    return {"run": out}


@app.post("/api/runs/{runid}/keep")
def keep_live(runid: str) -> dict:
    """Turns a finished live preview into an ordinary run that appears in the run list."""
    run = _get_run(runid)
    if run.get("status") != "ready":
        raise HTTPException(409, "Wait until the live preview has finished (or stop it first)")
    run["settings"] = {**(run.get("settings") or {}), "live": False}
    run["name"] = f"Live snapshot {len([r for r in store.list_runs(run['revisionId']) if not (r.get('settings') or {}).get('live')]) + 1}"
    store.save_run(run["revisionId"], runid, run)
    return _run_payload(run)


class CameraBody(BaseModel):
    position: list[float]
    target: list[float]
    up: list[float]
    fov: float = 35.0
    orthoHalfH: float | None = None
    near: float = 0.0
    far: float = 0.0


class LiveFrameBody(BaseModel):
    camera: CameraBody
    width: int = 640
    height: int = 400
    spp: int = 4
    reset: bool = False
    quality: int = 85
    state: str | None = None
    exposure: float = 0.0
    room_lux: float = 500.0  # illuminance of the room light on a surface facing the camera (lux); 0 = a dark room
    exposure_mode: str = "fixed"  # "fixed" camera, "room" (a white card in the room light is mid-grey) or "surfaces" (from the lit exit surfaces)
    smooth: float = 2.0
    white_balance: bool = True
    mode: str = "map"  # "map": the exit surface from the simulation; "transport": the full light transport (LEDs as emitters)
    hidden_exits: list[int] = []  # exit surfaces (detector indices) left dark in the picture (map mode)


@app.post("/api/runs/{runid}/live-frame")
def live_frame(runid: str, body: LiveFrameBody) -> Response:
    """One more pass of the live picture for the given camera. Answers an image, or JSON (status 202) while the
    picture cannot be drawn yet (``starting`` the renderer, ``waiting`` for the first rays, or an ``error``)."""
    from fastapi.responses import JSONResponse

    from .render import live_view, mitsuba_export

    run = _get_run(runid)
    ok, why = mitsuba_export.available()
    if not ok:
        raise HTTPException(501, why)
    if not (64 <= body.width <= 4096 and 64 <= body.height <= 4096) or not (1 <= body.spp <= 256):
        raise HTTPException(400, "width and height must be 64 to 4096 pixels and samples 1 to 256")
    if len(body.camera.position) != 3 or len(body.camera.target) != 3 or len(body.camera.up) != 3:
        raise HTTPException(400, "camera vectors need three numbers")
    req = {
        "camera": body.camera.model_dump(),
        "size": [body.width, body.height],
        "spp": body.spp,
        "reset": body.reset,
        "quality": max(30, min(body.quality, 95)),
        "view": {"state": body.state, "exposure": body.exposure, "room_lux": max(0.0, min(body.room_lux, 1.0e5)), "exposure_mode": body.exposure_mode if body.exposure_mode in ("fixed", "room", "surfaces") else "fixed", "smooth": max(0.0, min(body.smooth, 8.0)), "white_balance": body.white_balance, "mode": "transport" if body.mode == "transport" else "map", "hidden_exits": sorted({i for i in body.hidden_exits if i >= 0})},
    }
    out = live_view.request_frame(run["revisionId"], runid, req)
    if out["status"] == "ok":
        headers = {"X-Spp": str(out["spp"]), "X-Rays": str(out.get("rays") or 0), "X-Ms": str(out.get("ms", 0)), "X-Variant": str(out.get("variant", "")), "X-Lum": json.dumps(out.get("lum") or {}, separators=(",", ":")), "Cache-Control": "no-store"}
        return Response(out["jpeg"], media_type="image/jpeg", headers=headers)
    return JSONResponse(out, status_code=202)


@app.post("/api/live-view/close")
def close_live_view() -> dict:
    from .render import live_view

    live_view.close_session(wait=True)
    return {"ok": True}


@app.get("/api/revisions/{rid}/runs")
def list_runs(rid: str) -> list[dict]:
    _get_rev(rid)
    runs = [r for r in store.list_runs(rid) if not (r.get("settings") or {}).get("live")]  # live previews have their own panel
    runs.sort(key=lambda r: r.get("created", 0), reverse=True)
    return [_run_payload(r) for r in runs]


@app.get("/api/runs/{runid}")
def get_run(runid: str) -> dict:
    return _run_payload(_get_run(runid))


class RunPatch(BaseModel):
    name: str | None = None


@app.patch("/api/runs/{runid}")
def patch_run(runid: str, body: RunPatch) -> dict:
    run = _get_run(runid)
    if body.name and body.name.strip():
        run["name"] = body.name.strip()
        store.save_run(run["revisionId"], runid, run)
    return _run_payload(run)


@app.delete("/api/runs/{runid}")
def delete_run(runid: str) -> dict:
    run = _get_run(runid)
    _drop_run(run["revisionId"], runid)
    return {"ok": True}


@app.post("/api/runs/{runid}/cancel")
def cancel_run(runid: str) -> dict:
    run = _get_run(runid)
    return {"cancelled": jobs.cancel(("sim", run["revisionId"], runid))}


class RenderBody(BaseModel):
    state: str | None = None
    mode: str = "map"  # "map": exit surface from the simulation result, "transport": full path tracing in Mitsuba
    width: int = 640
    spp: int = 32
    azimuth: float = 0.0
    elevation: float = 0.0
    detector: int = 0  # the exit surface / receiver the camera looks at (index in the setup)
    zoom: float = 1.0
    fov: float = 35.0
    projection: str = "perspective"  # "orthographic" frames the same view without perspective, like the 3D view's projection
    exposure: float = 0.0
    smooth: float = 1.0
    room_lux: float = 500.0  # illuminance of the room light on a surface facing the camera (lux); 0 = a dark room
    exposure_mode: str = "fixed"  # "fixed" camera, "room" (a white card in the room light is mid-grey) or "surfaces" (from the lit exit surfaces)
    white_balance: bool = True
    hidden_exits: list[int] = []  # exit surfaces (detector indices) left dark in the picture (map mode); empty = all are drawn


def _render_payload(runid: str, run: dict) -> dict:
    from .render import mitsuba_export

    ok, why = mitsuba_export.available()
    rdir = store.run_dir(run["revisionId"], runid) / "render"
    meta = store.read_json(rdir / "render.json", None) if (rdir / "render.json").exists() else None
    out: dict = {"available": ok, "reason": why, "render": None}
    if meta:
        key = ("render", run["revisionId"], runid)
        out["render"] = _status_payload(meta, key)
        out["render"]["hasImage"] = (rdir / "image.png").exists()
    return out


@app.get("/api/runs/{runid}/render")
def get_render(runid: str) -> dict:
    return _render_payload(runid, _get_run(runid))


@app.post("/api/runs/{runid}/render")
def create_render(runid: str, body: RenderBody) -> dict:
    from .render import mitsuba_export

    run = _get_run(runid)
    if run.get("status") != "ready":
        raise HTTPException(409, "The run has no results yet")
    ok, why = mitsuba_export.available()
    if not ok:
        raise HTTPException(501, why)
    if body.mode not in ("map", "transport"):
        raise HTTPException(400, "mode must be 'map' or 'transport'")
    if body.projection not in ("perspective", "orthographic"):
        raise HTTPException(400, "projection must be 'perspective' or 'orthographic'")
    if not (160 <= body.width <= 4096) or not (1 <= body.spp <= 32768):
        raise HTTPException(400, "width must be 160 to 4096 pixels and samples 1 to 32768")
    key = ("render", run["revisionId"], runid)
    if jobs.is_active(key):
        raise HTTPException(409, "A picture of this run is already being rendered")
    rdir = store.run_dir(run["revisionId"], runid) / "render"
    rdir.mkdir(parents=True, exist_ok=True)
    store.write_json(rdir / "render.json", {"status": "queued", "settings": body.model_dump(), "created": time.time()})
    jobs.submit(key)
    return _render_payload(runid, run)


@app.post("/api/runs/{runid}/render/cancel")
def cancel_render(runid: str) -> dict:
    run = _get_run(runid)
    return {"cancelled": jobs.cancel(("render", run["revisionId"], runid))}


@app.get("/api/runs/{runid}/render.png")
def render_png(runid: str) -> Response:
    run = _get_run(runid)
    path = store.run_dir(run["revisionId"], runid) / "render" / "image.png"
    if not path.exists():
        raise HTTPException(404, "No picture has been rendered for this run")
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})


_RUNDATA_CACHE: dict[str, tuple[float, object]] = {}


def _run_data(runid: str):
    from .results import RunData

    run = _get_run(runid)
    if run.get("status") != "ready" and not ((run.get("settings") or {}).get("live") and run.get("status") == "running"):  # a live run can be read while it runs
        raise HTTPException(409, "The run has no results yet")
    path = store.run_dir(run["revisionId"], runid) / "tallies.npz"
    mtime = path.stat().st_mtime if path.exists() else 0
    hit = _RUNDATA_CACHE.get(runid)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = RunData.load(run["revisionId"], runid)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    if len(_RUNDATA_CACHE) > 6:
        _RUNDATA_CACHE.pop(next(iter(_RUNDATA_CACHE)))
    _RUNDATA_CACHE[runid] = (mtime, data)
    return data


def _analysis_from_query(roi: str | None, margin: float | None, smooth: float | None, hotspot_ratio: float | None, hotspot_mm: float | None, view: str | None, map_smooth: float | None = None, cone_deg: float | None = None) -> dict:
    an: dict = {}
    if cone_deg is not None:  # the viewing cone to show (limited per exit surface to the angles the run has rings for)
        if not 0.0 < float(cone_deg) <= 90.0:
            raise HTTPException(400, "cone_deg must be between 0 and 90")
        an["cone_deg"] = float(cone_deg)
    if map_smooth is not None:
        an["map_smooth_px"] = max(0.0, min(float(map_smooth), 8.0))
    if roi:
        try:
            vals = [float(x) for x in roi.split(",")]
            if len(vals) != 4:
                raise ValueError
            an["roi"] = vals
        except ValueError:
            raise HTTPException(400, "roi must be u0,v0,u1,v1")
    if margin is not None:
        an["margin"] = margin
    if smooth is not None:
        an["smooth_px"] = smooth
    if hotspot_ratio is not None:
        an["hotspot_ratio"] = hotspot_ratio
    if hotspot_mm is not None:
        an["hotspot_radius_mm"] = hotspot_mm
    if view in ("cone", "all"):
        an["view"] = view
    return an


@app.get("/api/runs/{runid}/results")
def run_results(
    runid: str,
    state: str | None = None,
    roi: str | None = None,
    margin: float | None = None,
    smooth: float | None = None,
    hotspot_ratio: float | None = None,
    hotspot_mm: float | None = None,
    view: str | None = None,
    led_maps: bool = True,
    map_smooth: float | None = None,
    cone_deg: float | None = None,
) -> dict:
    data = _run_data(runid)
    sid = state or data.state_ids()[0]
    if sid not in data.state_ids():
        raise HTTPException(404, "State not found")
    an = _analysis_from_query(roi, margin, smooth, hotspot_ratio, hotspot_mm, view, map_smooth, cone_deg)
    out = data.state_result(sid, an, with_led_maps=led_maps)
    out["states"] = [{"id": s["id"], "name": s["name"]} for s in data.scene["states"]]
    out["channels"] = data.scene["channels"]
    out["warnings"] = data.scene["warnings"]
    return out


@app.get("/api/runs/{runid}/scene")
def run_scene(runid: str) -> dict:
    data = _run_data(runid)
    return {k: data.scene[k] for k in ("bodies", "detectors", "channels", "states", "warnings", "settings")}


@app.get("/api/runs/{runid}/parts")
def run_parts(runid: str, state: str | None = None) -> dict:
    """What a run was simulated with: every part with its material and finish (as the run's own library snapshot defined them),
    the faces with a finish of their own, and the light each part absorbed. Parts the setup left out of the run are listed apart."""
    from . import library as lib
    from .engine.scene import resolve_part

    data = _run_data(runid)
    run = _get_run(runid)
    sid = state or data.state_ids()[0]
    if sid not in data.state_ids():
        raise HTTPException(404, "State not found")
    rdir = store.run_dir(run["revisionId"], runid)
    setup = store.read_json(rdir / "setup.json") or run.get("setup") or {}
    library = store.read_json(rdir / "library.json") or lib.get_library()  # the definitions as they were when the run started
    opt_doc = store.read_json(store.rev_dir(run["revisionId"]) / "optical.json") or {"instances": []}
    rules = setup.get("parts", [])
    materials, finishes = library.get("materials", {}), library.get("finishes", {})
    warnings = data.scene.get("warnings", [])

    comb = data.combine(sid)
    tot = max(float(comb["flux_w"]), 1e-30)

    def finish_name(fid: str) -> str:
        if fid.startswith("mat:"):
            m = materials.get(fid[4:])
            return f"{m.get('name', fid[4:])} (surface material)" if m else fid
        f = finishes.get(fid)
        return str(f.get("name", fid)) if f else fid

    def face_overrides(inst: dict, part: dict) -> list[dict]:
        out = []
        for face_s, fid in sorted(part["faces"].items(), key=lambda kv: int(kv[0]) if str(kv[0]).lstrip("-").isdigit() else 0):
            gone = any(f"{inst['path']}: the face with a finish override ({face_s}) no longer exists" in w for w in warnings)
            out.append({"face": int(face_s) if str(face_s).lstrip("-").isdigit() else face_s, "finish": fid, "name": finish_name(fid), "applied": not gone,
                        "properties": None if fid.startswith("mat:") else lib.describe_finish(finishes.get(fid))})
        return out

    by_path = {i["path"]: i for i in opt_doc.get("instances", [])}
    parts = []
    used_paths = set()
    for b in data.scene["bodies"]:
        inst = by_path.get(b["path"], {"path": b["path"], "name": b["name"], "ordinal": b.get("instance")})
        part = resolve_part(inst, rules) if inst.get("path") else {"faces": {}}
        used_paths.add(b["path"])
        mat = materials.get(b["material"]) if b["material"] else None
        kind = "volume" if b["transparent"] else ("surface" if b["material"] else "opaque")
        parts.append(
            {
                "index": b["index"],
                "path": b["path"],
                "name": b["name"],
                "kind": kind,
                "material": {"id": b["material"], "name": (mat or {}).get("name", b["material"]), "properties": lib.describe_material(mat)} if b["material"] else None,
                "finish": {"id": b["finish"], "name": finish_name(b["finish"]), "properties": lib.describe_finish(finishes.get(b["finish"]))},
                "faceOverrides": face_overrides(inst, part),
                "absorbed": {
                    "volume": round(float(comb["abs_vol"][b["index"]]) / tot, 6),
                    "surface": round(float(comb["abs_surf"][b["index"]]) / tot, 6),
                },
            }
        )
    excluded = [{"path": i["path"], "name": i["name"]} for i in opt_doc.get("instances", []) if i["path"] not in used_paths]
    return {"state": sid, "parts": parts, "excluded": excluded}


@app.get("/api/runs/{runid}/paths")
def run_paths(runid: str) -> dict:
    run = _get_run(runid)
    doc = store.read_json(store.run_dir(run["revisionId"], runid) / "paths.json")
    if doc is None:
        raise HTTPException(404, "No ray paths were recorded")
    return doc


# -- analysis: where does the light of a run come from ---------------------------------------------------------
_ANALYSIS_CACHE: dict[str, tuple[float, object]] = {}
_REPLAY_CACHE: dict[str, tuple[object, dict, dict]] = {}
_ANALYSIS_LOCK = threading.Lock()


def _run_analysis(runid: str):
    from .analysis import ANALYSIS_FILE, RunAnalysis

    data = _run_data(runid)
    run = _get_run(runid)
    path = store.run_dir(run["revisionId"], runid) / ANALYSIS_FILE
    if not path.exists():
        raise HTTPException(404, "This run was made without analysis data. Run it again to get the analysis tools.")
    mtime = path.stat().st_mtime
    with _ANALYSIS_LOCK:
        hit = _ANALYSIS_CACHE.get(runid)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            ra = RunAnalysis(run["revisionId"], runid, data)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        if len(_ANALYSIS_CACHE) > 3:
            _ANALYSIS_CACHE.pop(next(iter(_ANALYSIS_CACHE)))
        _ANALYSIS_CACHE[runid] = (mtime, ra)
        return ra


def _ana_state(ra, state: str | None) -> str:
    sid = state or ra.data.state_ids()[0]
    if sid not in ra.data.state_ids():
        raise HTTPException(404, "State not found")
    return sid


def _ana_view(view: str | None) -> str:
    return "all" if view == "all" else "cone"


def _ana_det(ra, det: int) -> int:
    if not 0 <= det < len(ra.dets):
        raise HTTPException(404, "Detector not found")
    return det


@app.get("/api/runs/{runid}/analysis")
def run_analysis(runid: str, state: str | None = None, view: str | None = None, detector: int = 0) -> dict:
    ra = _run_analysis(runid)
    return ra.overview(_ana_state(ra, state), _ana_view(view), _ana_det(ra, detector))


@app.get("/api/runs/{runid}/analysis/group-map")
def run_analysis_group_map(runid: str, group: int | None = None, mode: str = "last", state: str | None = None, view: str | None = None, detector: int = 0) -> dict:
    ra = _run_analysis(runid)
    if mode not in ("last", "touched", "untouched", "all"):
        raise HTTPException(400, "mode must be last, touched, untouched or all")
    # group -1 is the light that came straight from the LED (it met no tagged surface): only "last" makes sense for it
    if group is not None and not (-1 <= group < ra.ng and (group >= 0 or mode == "last")):
        raise HTTPException(404, "Group not found")
    return ra.group_map(_ana_state(ra, state), _ana_view(view), _ana_det(ra, detector), group, mode)


class RegionBody(BaseModel):
    polygon: list[list[float]]
    detector: int = 0
    state: str | None = None
    view: str | None = None


@app.post("/api/runs/{runid}/analysis/region")
def run_analysis_region(runid: str, body: RegionBody) -> dict:
    ra = _run_analysis(runid)
    if len(body.polygon) < 3:
        raise HTTPException(400, "The region needs at least three points")
    try:
        return ra.region_report(_ana_state(ra, body.state), _ana_view(body.view), _ana_det(ra, body.detector), body.polygon)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/runs/{runid}/analysis/face")
def run_analysis_face(runid: str, instance: str, face: int, state: str | None = None) -> dict:
    ra = _run_analysis(runid)
    gf = ra.gface_of(instance, face)
    if gf is None:
        raise HTTPException(404, "That face is not part of the simulated model")
    return ra.face_report(_ana_state(ra, state), gf)


@app.get("/api/runs/{runid}/analysis/hits")
def run_analysis_hits(runid: str, quantity: str = "incident", state: str | None = None) -> dict:
    ra = _run_analysis(runid)
    return ra.hit_map(_ana_state(ra, state), quantity)


@app.get("/api/runs/{runid}/analysis/families")
def run_analysis_families(runid: str, state: str | None = None, view: str | None = None) -> dict:
    ra = _run_analysis(runid)
    try:
        return ra.families(_ana_state(ra, state), _ana_view(view))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/runs/{runid}/analysis/flow")
def run_analysis_flow(runid: str, state: str | None = None, view: str | None = None, detector: int = 0, bands: int = 4) -> dict:
    ra = _run_analysis(runid)
    try:
        return ra.flow(_ana_state(ra, state), _ana_view(view), _ana_det(ra, detector), max(2, min(8, bands)))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/runs/{runid}/analysis/family-map")
def run_analysis_family_map(runid: str, family: str, state: str | None = None, view: str | None = None, detector: int = 0) -> dict:
    ra = _run_analysis(runid)
    try:
        return ra.family_map(_ana_state(ra, state), _ana_view(view), _ana_det(ra, detector), family)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/runs/{runid}/analysis/guidance")
def run_analysis_guidance(runid: str, state: str | None = None, view: str | None = None, detector: int = 0, roi: str | None = None, target: float | None = None) -> dict:
    ra = _run_analysis(runid)
    roi_v = _analysis_from_query(roi, None, None, None, None, None).get("roi")
    try:
        return ra.guidance(_ana_state(ra, state), _ana_view(view), _ana_det(ra, detector), roi_v, target)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class RaysBody(BaseModel):
    filter: dict = {}
    count: int = 300
    state: str | None = None
    view: str | None = None
    gface: int | None = None  # keep only rays that hit this CAD face (needs the filter to be one that stays near it, e.g. touched)
    maxPoints: int = 256
    seed: int = 1


def _replay_scene(runid: str, run: dict):
    """Scene and channel sources of a run, rebuilt from its stored setup (the rays of the run are traced again on it)."""
    from . import library as lib
    from .engine import sources
    from .engine.scene import build_scene
    from .optical_mesh import load_optical

    with _ANALYSIS_LOCK:
        hit = _REPLAY_CACHE.get(runid)
        if hit:
            return hit
        rid = run["revisionId"]
        rdir = store.run_dir(rid, runid)
        setup = store.read_json(rdir / "setup.json") or run.get("setup")
        library = store.read_json(rdir / "library.json") or lib.get_library()
        mdir = store.rev_dir(rid)
        opt = load_optical(mdir / "optical.npz")
        opt_doc = store.read_json(mdir / "optical.json")
        scene = build_scene(opt, opt_doc, setup, library)
        srcs = {s.key: s for s in sources.build_channel_sources(setup, opt_doc, scene, opt, library)}
        if len(_REPLAY_CACHE) >= 2:
            _REPLAY_CACHE.pop(next(iter(_REPLAY_CACHE)))
        _REPLAY_CACHE[runid] = (scene, srcs, {})
        return _REPLAY_CACHE[runid]


@app.post("/api/runs/{runid}/analysis/rays")
def run_analysis_rays(runid: str, body: RaysBody) -> dict:
    """Rays that match a filter (an exit region, a group, a route ...), traced again in full detail."""
    import numpy as np

    from .engine.replay import replay

    ra = _run_analysis(runid)
    run = _get_run(runid)
    state, view = _ana_state(ra, body.state), _ana_view(body.view)
    count = int(np.clip(body.count, 1, 3000))
    try:
        sel = ra.choose_rays(state, view, body.filter or {}, count * (6 if body.gface is not None else 1), body.seed)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    scene, srcs, _ = _replay_scene(runid, run)
    chans = ra.meta["channels"]
    group_of = ra.arr("gface_group")
    # the detector of every CAD face that belongs to one: the "exit" event of a path says which detector the ray arrived on
    det_of_gface = {int(g): k for k, d in enumerate(scene.detectors) for g in np.unique(scene.tri_gface[d.tri_ids])}
    total_w = sum(w for _, _, w in sel["rays"]) or 1.0
    by_ch: dict[int, list[tuple[int, float]]] = {}
    for ci, rid_, w in sel["rays"]:
        by_ch.setdefault(ci, []).append((rid_, w))
    paths = []
    for ci, items in by_ch.items():
        meta_ch = chans[ci]
        ids = [i for i, _ in items]
        wmap = {i: w for i, w in items}
        try:
            out = replay(scene, srcs[meta_ch["key"]], meta_ch["seed"], ids, ra.meta["rrThreshold"], ra.meta["maxBounces"], int(np.clip(body.maxPoints, 16, 1024)), ra.meta.get("solver", "cpu"))
        except ValueError as exc:  # a GPU run on a machine without the GPU solver
            raise HTTPException(400, str(exc))
        ch_info = ra.channels[ci]
        for r in out:
            gfs = np.asarray(r["gface"], dtype=int)
            if body.gface is not None and not np.any(gfs == body.gface):
                continue
            groups = np.where(gfs >= 0, group_of[np.clip(gfs, 0, len(group_of) - 1)], -1)
            det_at = [det_of_gface.get(int(g), -1) if e == 8 else -1 for g, e in zip(r["gface"], r["event"])]  # (8 = exit event)
            paths.append(
                {
                    **r, "group": groups.tolist(), "det": det_at, "led": ch_info["led_id"], "channel": ch_info["channel_id"], "key": meta_ch["key"],
                    "color": ch_info.get("color"), "flux": wmap[r["id"]] / total_w,
                }
            )
    paths = paths[:count]
    detectors = [{"id": d.id, "name": d.name, "kind": d.kind, "through": d.through} for d in scene.detectors]
    return {"paths": paths, "matched": sel["matched"], "fluxFraction": sel["fluxFraction"], "detectors": detectors, "events": ["start", "scatter", "specular", "diffuse", "fresnel", "tir", "transmit", "absorbed", "exit", "end"]}


class RaysMapBody(BaseModel):
    filter: dict = {}
    state: str | None = None
    view: str | None = None
    detector: int = 0


@app.post("/api/runs/{runid}/analysis/rays-map")
def run_analysis_rays_map(runid: str, body: RaysMapBody) -> dict:
    """Where the rays that match a filter (the same filter as .../analysis/rays) land on the exit surface."""
    ra = _run_analysis(runid)
    try:
        return ra.filter_map(_ana_state(ra, body.state), _ana_view(body.view), _ana_det(ra, body.detector), body.filter or {})
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))


class SliceBody(BaseModel):
    origin: list[float]
    normal: list[float]
    direction: str = "both"  # both | along | against (relative to the normal)
    outcome: str = ""  # "" (all light) | exit | absorbed | other
    count: int = 20000
    state: str | None = None
    seed: int = 1
    grid: int = 140


@app.post("/api/runs/{runid}/analysis/slice")
def run_analysis_slice(runid: str, body: SliceBody) -> dict:
    """Flux through a cut plane: a sample of the rays is traced again and the light crossing the plane is gathered on a grid."""
    import numpy as np

    from . import analysis_slice

    if len(body.origin) != 3 or len(body.normal) != 3 or not float(np.linalg.norm(body.normal)) > 0:
        raise HTTPException(400, "The cut needs an origin and a normal")
    ra = _run_analysis(runid)
    run = _get_run(runid)
    state = _ana_state(ra, body.state)
    count = int(np.clip(body.count, 500, 60000))
    scene, srcs, cache = _replay_scene(runid, run)
    key = ("slice", state, count, body.seed)
    segs = cache.get(key)
    if segs is None:
        try:
            segs = analysis_slice.build_segments(ra, scene, srcs, state, count, body.seed)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        for k in [k for k in cache if isinstance(k, tuple) and k and k[0] == "slice"]:
            cache.pop(k)  # keep only the latest sample (it is large)
        cache[key] = segs
    return analysis_slice.slice_flux(segs, scene, body.origin, body.normal, body.direction, body.outcome, int(np.clip(body.grid, 20, 400)))


@app.get("/api/runs/{runid}/compare")
def compare_runs(
    runid: str,
    other: str,
    state: str | None = None,
    other_state: str | None = None,
    detector: int = 0,
    roi: str | None = None,
    smooth: float | None = None,
    view: str | None = None,
    cone_deg: float | None = None,
) -> dict:
    from . import results

    a, b = _run_data(runid), _run_data(other)
    an = _analysis_from_query(roi, None, smooth, None, None, view, None, cone_deg)
    ra = a.state_result(state or a.state_ids()[0], an, with_led_maps=False)
    rb = b.state_result(other_state or b.state_ids()[0], an, with_led_maps=False)
    if detector >= len(ra["detectors"]) or detector >= len(rb["detectors"]):
        raise HTTPException(400, "Detector index out of range")
    da, db = ra["detectors"][detector], rb["detectors"][detector]
    maps = results.compare(da, db)

    return {"a": _compare_brief(ra, da), "b": _compare_brief(rb, db), "maps": maps, "unit": da["unit"]}


def _compare_brief(r: dict, d: dict) -> dict:
    return {
        "state": r["state"],
        "emittedLm": r["emitted"]["lm"],
        "efficiency": d["efficiency"],
        "exitLm": d["exitLm"],
        "stats": d["stats"],
        "color": d.get("color"),
        "hotspots": len(d["hotspots"]),
        "profileU": d["profileU"],
        "angular": d["angular"],
        "loss": r["loss"],
        "uExtentMm": d.get("uExtentMm"),
        "vExtentMm": d.get("vExtentMm"),
    }


@app.get("/api/projects/{pid}/compare")
def compare_project_runs(
    pid: str,
    runs: str,
    state: str | None = None,
    detector: int = 0,
    roi: str | None = None,
    smooth: float | None = None,
    view: str | None = None,
    cone_deg: float | None = None,
) -> dict:
    """Any number of finished runs of a project side by side (they may belong to different variants).

    Each run uses the requested state when it has it and falls back to "all" (or its first state) otherwise;
    the entry says which one it used. A run that cannot be read gets an ``error`` instead of failing the request.
    """
    project = _get_project(pid)
    ids = [r for r in (x.strip() for x in runs.split(",")) if r]
    if len(ids) < 2 or len(ids) > 12:
        raise HTTPException(400, "Choose between 2 and 12 runs to compare")
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Each run can be compared once")
    variants = {v["id"]: v["name"] for v in project.get("variants", [])}
    designs = {d["id"]: d["name"] for d in project.get("designs", [])}
    design_of = {v["id"]: designs.get(v.get("designId")) for v in project.get("variants", [])}
    an = _analysis_from_query(roi, None, smooth, None, None, view, None, cone_deg)
    out, unit = [], None
    for runid in ids:
        run = _get_run(runid)
        if run.get("projectId") != pid:
            raise HTTPException(400, f"Run {runid} is not part of this project")
        rev = store.load_rev(run["revisionId"]) or {}
        vid = rev.get("variantId")  # a run is in the variant of its revision
        source = store.load_rev(rev["remeshedFrom"]) if rev.get("remeshedFrom") else None
        entry: dict = {
            "runId": runid,
            "name": run.get("name"),
            "revisionId": run["revisionId"],
            "revision": store.revision_name(rev, source) if rev else run["revisionId"],
            "variantId": vid,
            "variant": variants.get(vid),
            "design": design_of.get(vid),
            "created": run.get("created"),
        }
        try:
            data = _run_data(runid)
            ids_ = data.state_ids()
            want = state if state in ids_ else ("all" if "all" in ids_ else ids_[0])
            r = data.state_result(want, an, with_led_maps=False)
            if detector >= len(r["detectors"]):
                raise ValueError("this run has no such detector")
            d = r["detectors"][detector]
            entry.update(_compare_brief(r, d))
            entry["detectorName"] = d.get("name")
            entry["quantityUnit"] = d["unit"]
            entry["requestedState"] = state
            entry["stateFallback"] = bool(state) and want != state
            unit = unit or d["unit"]
        except HTTPException as exc:
            entry["error"] = exc.detail
        except (ValueError, KeyError, IndexError) as exc:
            entry["error"] = str(exc)
        out.append(entry)
    return {"unit": unit, "runs": out}


@app.get("/api/projects/{pid}/overview")
def project_overview(pid: str) -> dict:
    """Every finished run of a project with its stored summary, grouped by the caller as design > variant.

    Nothing is traced or recomputed from the rays, except that a run saved before summaries carried the energy budget
    (``loss``) gets its summary rebuilt once from its own run data and saved again.
    """
    from .results import RunData

    project = _get_project(pid)
    variants = {v["id"]: v for v in project.get("variants", [])}
    designs = {d["id"]: d for d in project.get("designs", [])}
    revs = {}
    for rid in project.get("revisions", []):
        meta = store.load_rev(rid)
        if meta:
            revs[rid] = meta
    out = []
    for rid, rev in revs.items():
        vid = rev.get("variantId")
        variant = variants.get(vid) or {}
        design = designs.get(variant.get("designId")) or {}
        source = revs.get(rev.get("remeshedFrom")) if rev.get("remeshedFrom") else None
        for run in store.list_runs(rid):
            settings = run.get("settings") or {}
            if run.get("status") != "ready" or settings.get("live"):
                continue
            summary = run.get("summary")
            if not summary or any("loss" not in s for s in summary.get("states", [])):
                try:
                    summary = RunData.load(rid, run["id"]).summary()
                    run["summary"] = summary
                    store.save_run(rid, run["id"], run)
                except Exception:  # an unreadable run is still listed, without numbers
                    summary = summary or None
            out.append(
                {
                    "runId": run["id"],
                    "name": run.get("name"),
                    "created": run.get("created"),
                    "revisionId": rid,
                    "revision": store.revision_name(rev, source),
                    "variantId": vid,
                    "variant": variant.get("name"),
                    "designId": variant.get("designId"),
                    "design": design.get("name"),
                    "summary": summary,
                    "mapNoise": run.get("mapNoise"),
                    "raysTraced": run.get("raysTraced"),
                    "rays": settings.get("rays"),
                    "seed": settings.get("seed"),
                    "solver": run.get("solver"),
                    "mesh": run.get("mesh") or {"quality": rev.get("meshQuality") or "normal", "triangles": None},
                    "sweep": run.get("sweep"),
                    "maxAccountingError": run.get("maxAccountingError"),
                }
            )
    out.sort(key=lambda r: r.get("created") or 0)
    return {
        "designs": [{"id": d["id"], "name": d["name"]} for d in project.get("designs", [])],
        "variants": [{"id": v["id"], "name": v["name"], "designId": v.get("designId")} for v in project.get("variants", [])],
        "runs": out,
    }


@app.get("/api/runs/{runid}/image.png")
def run_image(
    runid: str,
    state: str | None = None,
    detector: int = 0,
    kind: str = "luminance",
    vmax: float | None = None,
    scale: int = 4,
    roi: str | None = None,
    smooth: float | None = None,
    view: str | None = None,
    cone_deg: float | None = None,
) -> Response:
    import numpy as np

    from . import results

    data = _run_data(runid)
    an = _analysis_from_query(roi, None, smooth, None, None, view, None, cone_deg)
    r = data.state_result(state or data.state_ids()[0], an, with_led_maps=False)
    if detector >= len(r["detectors"]):
        raise HTTPException(400, "Detector index out of range")
    d = r["detectors"][detector]
    valid = np.array(d["valid"])
    if kind == "appearance":
        if d.get("appearance") is None:
            raise HTTPException(404, "No light reached the detector")
        png = results.render_png(np.zeros(valid.shape), valid, scale=max(1, min(scale, 16)), appearance=np.array(d["appearance"]))
    else:
        key = "duvMap" if kind == "duv" else ("exitance" if kind == "exitance" else "luminance")
        if d.get(key) is None:
            raise HTTPException(404, "Map not available")
        png = results.render_png(np.array(d[key]), valid, vmax=vmax, scale=max(1, min(scale, 16)))
    return Response(png, media_type="image/png")


@app.get("/api/runs/{runid}/export.zip")
def run_export(runid: str) -> Response:
    from . import results

    run = _get_run(runid)
    data = _run_data(runid)
    body = results.export_zip(data)
    fname = "".join(c if c.isalnum() else "_" for c in run.get("name", "run")) or "run"
    return Response(body, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{fname}.zip"'})


# -- settings: where the data lives --------------------------------------------------------
def _config_payload() -> dict:
    return {
        "dataDir": str(store.data_dir()),
        "configuredDir": store.configured_data_dir(),
        "defaultDir": str(store.default_data_dir()),
        "fromEnvironment": store.env_data_dir() is not None,
        "version": __version__,
        "mode": desktop.mode,
        "update": updater.get_settings(),
        "canInstallUpdates": updater.can_install(),
    }


@app.get("/api/config")
def get_config() -> dict:
    return _config_payload()


class ConfigBody(BaseModel):
    dataDir: str | None = None
    copyExisting: bool = False


@app.put("/api/config")
def put_config(body: ConfigBody) -> dict:
    if store.env_data_dir():
        raise HTTPException(409, "The data folder is set by the REFRACT_DATA environment variable.")
    target_raw = (body.dataDir or "").strip().strip('"')
    old = store.data_dir()
    if jobs.any_active():
        raise HTTPException(409, "A job is still running. Wait for it to finish (or cancel it) first.")
    try:
        target = store.validate_data_dir(target_raw) if target_raw else store.default_data_dir()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if target.resolve() != old.resolve():
        copied = 0
        if body.copyExisting:
            try:
                copied = store.copy_data(old, target)
            except OSError as exc:
                raise HTTPException(500, f"Copying failed: {exc}")
        store.set_data_dir(target_raw or None)
        payload = _config_payload()
        payload["copied"] = copied
        return payload
    return _config_payload()


# -- updates -------------------------------------------------------------------------------
class UpdateSettingsBody(BaseModel):
    source: str | None = None
    location: str | None = None
    auto: bool | None = None
    prerelease: bool | None = None


def _update_call(fn, *args):
    try:
        return fn(*args)
    except updater.UpdateError as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/update/settings")
def put_update_settings(body: UpdateSettingsBody) -> dict:
    return _update_call(updater.set_settings, body.model_dump(exclude_none=True))


@app.post("/api/update/check")
def check_update() -> dict:
    return _update_call(updater.check)


@app.get("/api/update/status")
def update_status() -> dict:
    return updater.status()


@app.post("/api/update/download")
def download_update() -> dict:
    return _update_call(updater.start_download)


@app.post("/api/update/install")
def install_update() -> dict:
    if jobs.any_active():
        raise HTTPException(409, "A job is still running. Wait for it to finish (or cancel it) before updating.")
    result = _update_call(updater.launch_installer)
    if result["launched"]:
        threading.Timer(1.0, _exit_now).start()  # the installer would end this process anyway; leave cleanly
    return result


@app.post("/api/fs/pick-folder")
def pick_folder(initial: str | None = None) -> dict:
    native = desktop.pick_folder(initial)
    if native is not None:
        return native
    out = Path(tempfile.gettempdir()) / f"refract-pick-{uuid.uuid4().hex[:8]}.json"
    try:
        done = subprocess.run(
            procutil.child_command("pick-folder", initial or "", str(out)),
            capture_output=True,
            text=True,
            timeout=600,
        )
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"path": None, "error": (done.stderr or "The folder dialog could not be opened").strip()[-300:]}
    except subprocess.TimeoutExpired:
        return {"path": None, "error": "The folder dialog timed out"}
    finally:
        out.unlink(missing_ok=True)


def _exit_now() -> None:
    for rev in store.list_revisions():
        jobs.cancel(("import", rev["id"], ""))
        for run in store.list_runs(rev["id"]):
            jobs.cancel(("sim", rev["id"], run["id"]))
    os._exit(0)


@app.post("/api/app/focus")
def focus_app() -> dict:
    return {"focused": desktop.focus()}


@app.post("/api/app/quit")
def quit_app() -> dict:
    threading.Timer(0.4, _exit_now).start()
    return {"ok": True}


# -- static frontend ---------------------------------------------------------------------
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(404)
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
            # index.html names the hashed bundle of the current build: a browser (or the app window's WebView2) that
            # kept an old copy would keep showing the old UI after an update, so it always asks again
            return FileResponse(candidate, headers={"Cache-Control": "no-cache"} if candidate.suffix == ".html" else None)
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
else:

    @app.get("/", include_in_schema=False)
    def no_frontend() -> JSONResponse:
        return JSONResponse(
            {"message": "Frontend not built. Run `npm install && npm run build` in ./frontend, or use the Vite dev server."}
        )
