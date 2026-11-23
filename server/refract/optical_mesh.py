"""Fine optical mesh of an imported assembly (the geometry the ray tracer uses).

Differences to the viewer mesh in ``tessellate.py``:

* much finer deflection, because light bounces many times off curved surfaces,
* smooth vertex normals *inside* each B-Rep face (so a tessellated bend still behaves like a smooth surface),
  while normals stay sharp across B-Rep edges,
* triangles carry the B-Rep face index of their definition. It is the same numbering as the viewer's
  ``_FACE_ID``, so a face picked in the 3D view maps straight onto optical triangles,
* every instance is checked for watertightness and inverted normals.

Output: ``optical.npz`` (arrays) + ``optical.json`` (instances and warnings).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

from .step_import import ImportedModel
from .tessellate import _definition_bbox, _transform_bbox, loc_matrix

Progress = Callable[[str, float], None]

#: optical deflection as a fraction of the model diagonal (floor in mm)
K_DEFL = 2.5e-4
MIN_DEFL = 2e-3
ANGULAR = 0.12  # radians
#: a part is also meshed to this fraction of its smallest bounding-box extent, so a small part in a large
#: assembly (a light pipe in a housing) is not meshed as coarsely as the assembly
K_THIN = 1e-2

#: mesh quality presets: (deflection factor, angular deflection in radians)
QUALITY = {"coarse": (2.0, 0.2), "normal": (1.0, ANGULAR), "fine": (0.25, 0.06)}
DEFAULT_QUALITY = "normal"


def part_deflection(model_deflection: float, part_bbox: np.ndarray | None, factor: float = 1.0) -> float:
    """Chordal deflection of one part: the finer of the assembly value and K_THIN of the part's thinnest extent."""
    d = model_deflection
    if part_bbox is not None:
        thin = float(np.min(part_bbox[1] - part_bbox[0]))
        if thin > 0:
            d = min(d, K_THIN * thin)
    return max(d * factor, MIN_DEFL)


@dataclass
class DefMesh:
    verts: np.ndarray  # (N,3) float64, definition-local
    normals: np.ndarray  # (N,3) float64 smooth per face
    tris: np.ndarray  # (M,3) int64
    face: np.ndarray  # (M,) int32 B-Rep face index of the definition


def triangle_normals_areas(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit normals and areas of triangles (zero-area triangles get a zero normal)."""
    e1 = v[t[:, 1]] - v[t[:, 0]]
    e2 = v[t[:, 2]] - v[t[:, 0]]
    c = np.cross(e1, e2)
    ln = np.linalg.norm(c, axis=1)
    n = np.zeros_like(c)
    ok = ln > 1e-18
    n[ok] = c[ok] / ln[ok, None]
    return n, 0.5 * ln


def mesh_definition_optical(shape, deflection: float, angular: float = ANGULAR) -> DefMesh:
    BRepTools.Clean_s(shape)  # drop the coarser viewer triangulation
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)

    pos_chunks: list[np.ndarray] = []
    nrm_chunks: list[np.ndarray] = []
    tri_chunks: list[np.ndarray] = []
    face_chunks: list[np.ndarray] = []
    base = 0
    face_index = 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        fi = face_index
        face_index += 1
        ex.Next()

        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        n_nodes, n_tris = tri.NbNodes(), tri.NbTriangles()
        if n_nodes == 0 or n_tris == 0:
            continue
        pts = np.empty((n_nodes, 3), dtype=np.float64)
        for i in range(n_nodes):
            p = tri.Node(i + 1)
            pts[i] = (p.X(), p.Y(), p.Z())
        tris = np.empty((n_tris, 3), dtype=np.int64)
        for i in range(n_tris):
            tris[i] = tri.Triangle(i + 1).Get()
        tris -= 1
        if not loc.IsIdentity():
            m = loc_matrix(loc)
            pts = pts @ m[:3, :3].T + m[:3, 3]
        if face.Orientation() == TopAbs_REVERSED:
            tris = tris[:, [0, 2, 1]]

        # smooth normals inside this face (area weighted): flat faces stay exactly flat
        fn, fa = triangle_normals_areas(pts, tris)
        acc = np.zeros((n_nodes, 3))
        w = fn * fa[:, None]
        for k in range(3):
            np.add.at(acc, tris[:, k], w)
        ln = np.linalg.norm(acc, axis=1)
        ln[ln < 1e-18] = 1.0
        nrm = acc / ln[:, None]

        pos_chunks.append(pts)
        nrm_chunks.append(nrm)
        tri_chunks.append(tris + base)
        face_chunks.append(np.full(n_tris, fi, dtype=np.int32))
        base += n_nodes

    if not pos_chunks:
        return DefMesh(np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0, np.int32))
    return DefMesh(
        np.concatenate(pos_chunks),
        np.concatenate(nrm_chunks),
        np.concatenate(tri_chunks),
        np.concatenate(face_chunks),
    )


def facet_angles_deg(verts: np.ndarray, normals: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Per triangle, the largest angle (degrees) between its flat normal and the smoothed normals at its corners."""
    fn, _ = triangle_normals_areas(verts.astype(np.float64), tris)
    worst = np.ones(len(tris))
    for k in range(3):
        worst = np.minimum(worst, np.einsum("ij,ij->i", fn, normals[tris[:, k]].astype(np.float64)))
    ang = np.degrees(np.arccos(np.clip(worst, -1.0, 1.0)))
    ang[np.linalg.norm(fn, axis=1) < 0.5] = 0.0  # degenerate triangles have no normal
    return ang


def _quality(verts: np.ndarray, tris: np.ndarray) -> dict:
    """Watertightness / winding / volume of one instance (vertices merged by position)."""
    import trimesh

    if len(tris) == 0:
        return {"watertight": False, "consistent": False, "volume": 0.0, "area": 0.0}
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    mesh.merge_vertices(merge_tex=False, merge_norm=False)
    mesh.update_faces(mesh.nondegenerate_faces())
    watertight = bool(mesh.is_watertight)
    consistent = bool(mesh.is_winding_consistent)
    volume = float(mesh.volume) if watertight and consistent else 0.0
    return {"watertight": watertight, "consistent": consistent, "volume": volume, "area": float(mesh.area)}


def build_optical_mesh(
    model: ImportedModel,
    out_npz: Path,
    out_json: Path,
    progress: Progress | None = None,
    quality: str = DEFAULT_QUALITY,
) -> dict:
    if quality not in QUALITY:
        raise ValueError(f"unknown mesh quality {quality!r} (expected one of {', '.join(QUALITY)})")
    factor, angular = QUALITY[quality]

    def report(msg: str, frac: float) -> None:
        if progress:
            progress(msg, frac)

    insts = [
        i for i in model.instances if i.is_leaf and not model.definitions[i.def_key].shape.IsNull()
    ]
    if not insts:
        raise RuntimeError("The STEP file contains no geometry")

    # deflection from model size
    bbs = {}
    for key in {i.def_key for i in insts}:
        bbs[key] = _definition_bbox(model.definitions[key].shape)
    mins, maxs = [], []
    for i in insts:
        bb = bbs.get(i.def_key)
        if bb is None:
            continue
        w = _transform_bbox(bb, loc_matrix(i.global_loc))
        mins.append(w[0])
        maxs.append(w[1])
    model_bb = np.array([np.min(mins, axis=0), np.max(maxs, axis=0)])
    diag = float(np.linalg.norm(model_bb[1] - model_bb[0])) or 1.0
    deflection = max(diag * K_DEFL, MIN_DEFL)

    # mesh every unique definition once
    keys = sorted({i.def_key for i in insts})
    meshes: dict[str, DefMesh] = {}
    part_defl: dict[str, float] = {}
    for n, key in enumerate(keys):
        part_defl[key] = part_deflection(deflection, bbs.get(key), factor)
        meshes[key] = mesh_definition_optical(model.definitions[key].shape, part_defl[key], angular)
        report(f"Optical meshing ({n + 1}/{len(keys)})", (n + 1) / len(keys) * 0.8)

    verts_l, nrm_l, tris_l, face_l, inst_l = [], [], [], [], []
    inst_docs = []
    warnings: list[str] = []
    v_off = 0
    t_off = 0
    for ordinal, i in enumerate(insts):
        m = meshes[i.def_key]
        if len(m.tris) == 0:
            warnings.append(f"{i.path}: could not be meshed and is ignored.")
            continue
        mat = loc_matrix(i.global_loc)
        rot = mat[:3, :3]
        verts = m.verts @ rot.T + mat[:3, 3]
        nrm = m.normals @ rot.T
        ln = np.linalg.norm(nrm, axis=1)
        ln[ln < 1e-18] = 1.0
        nrm = nrm / ln[:, None]
        tris = m.tris.copy()
        if np.linalg.det(rot) < 0:  # mirrored instance: winding flips
            tris = tris[:, [0, 2, 1]]
        q = _quality(verts, tris)
        notes: list[str] = []
        d = model.definitions[i.def_key]
        has_solid = bool(d.solids)
        if q["watertight"] and q["consistent"] and q["volume"] < 0:
            tris = tris[:, [0, 2, 1]]
            nrm = -nrm
            q["volume"] = -q["volume"]
            notes.append("normals were inverted and have been flipped")
        if has_solid and not (q["watertight"] and q["consistent"]):
            notes.append("mesh is not watertight: use it as an opaque part only")
        if not has_solid:
            notes.append("surface/shell body (no solid): opaque use only")
        closed = bool(has_solid and q["watertight"] and q["consistent"])
        bb = np.array([verts.min(axis=0), verts.max(axis=0)])
        inst_docs.append(
            {
                "ordinal": len(inst_docs),
                "id": i.id,
                "path": i.path,
                "name": i.name,
                "partName": d.name,
                "defKey": i.def_key,
                "color": list(i.color) if i.color else None,
                "vertOffset": v_off,
                "vertCount": int(len(verts)),
                "triOffset": t_off,
                "triCount": int(len(tris)),
                "faceCount": int(m.face.max()) + 1 if len(m.face) else 0,
                "deflection": part_defl[i.def_key],
                "closed": closed,
                "volume": q["volume"],
                "area": q["area"],
                "bbox": bb.tolist(),
                "notes": notes,
            }
        )
        for n in notes:
            warnings.append(f"{i.path}: {n}")
        verts_l.append(verts.astype(np.float32))
        nrm_l.append(nrm.astype(np.float32))
        tris_l.append((tris + v_off).astype(np.int32))
        face_l.append(m.face)
        inst_l.append(np.full(len(tris), len(inst_docs) - 1, dtype=np.int32))
        v_off += len(verts)
        t_off += len(tris)
        report(f"Checking meshes ({ordinal + 1}/{len(insts)})", 0.8 + 0.2 * (ordinal + 1) / len(insts))

    np.savez_compressed(
        out_npz,
        verts=np.concatenate(verts_l),
        normals=np.concatenate(nrm_l),
        tris=np.concatenate(tris_l),
        tri_face=np.concatenate(face_l),
        tri_inst=np.concatenate(inst_l),
    )
    doc = {
        "units": "mm",
        "deflection": deflection,  # assembly value; each instance carries its own (never coarser)
        "angular": angular,
        "quality": quality,
        "bbox": model_bb.tolist(),
        "diagonal": diag,
        "triangles": int(t_off),
        "instances": inst_docs,
        "warnings": warnings,
    }
    out_json.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def load_optical(npz_path: Path) -> dict[str, np.ndarray]:
    with np.load(npz_path) as z:
        return {k: z[k] for k in z.files}


def face_info(npz: dict[str, np.ndarray], inst_doc: dict, face: int) -> dict | None:
    """Centroid, area-weighted normal, area and extent of one B-Rep face of one instance."""
    t0, n = inst_doc["triOffset"], inst_doc["triCount"]
    sel = np.nonzero(npz["tri_face"][t0 : t0 + n] == face)[0] + t0
    if len(sel) == 0:
        return None
    tris = npz["tris"][sel]
    v = npz["verts"].astype(np.float64)
    nrm, area = triangle_normals_areas(v, tris)
    total = float(area.sum())
    cen = (v[tris].mean(axis=1) * area[:, None]).sum(axis=0) / max(total, 1e-18)
    nsum = (nrm * area[:, None]).sum(axis=0)
    nlen = float(np.linalg.norm(nsum))
    normal = (nsum / nlen) if nlen > 1e-12 else np.array([0.0, 0.0, 1.0])
    pts = v[np.unique(tris)]
    planar = bool(nlen / max(total, 1e-18) > 0.999)
    return {
        "centroid": cen.tolist(),
        "normal": normal.tolist(),
        "area": total,
        "planar": planar,
        "bbox": [pts.min(axis=0).tolist(), pts.max(axis=0).tolist()],
    }
