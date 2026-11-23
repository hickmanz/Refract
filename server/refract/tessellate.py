"""Tessellation + GLB/JSON export in the schema the Synapse viewer expects."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap

from .gltf_writer import GltfBuilder, compute_vertex_normals
from .step_import import DEFAULT_COLOR, ImportedModel

Progress = Callable[[str, float], None]

ANGULAR_DEFLECTION = 0.4
K_DEFL = 0.0015  # fraction of model diagonal (matches Synapse "medhigh")
K_MIN = 0.0005
K_MAX = 0.09


def loc_matrix(loc: TopLoc_Location) -> np.ndarray:
    """4x4 row-major matrix for a TopLoc_Location."""
    t = loc.Transformation()
    m = np.eye(4)
    for r in range(3):
        for c in range(4):
            m[r, c] = t.Value(r + 1, c + 1)
    return m


def _gltf_matrix(m: np.ndarray) -> list[float] | None:
    if np.allclose(m, np.eye(4), atol=1e-10):
        return None
    return [float(v) for v in m.T.reshape(-1)]  # column-major


@dataclass
class DefGeom:
    pos: np.ndarray  # (N,3) float32
    nrm: np.ndarray
    idx: np.ndarray  # (M,) uint32
    face_id: np.ndarray  # (N,) float32
    edge_pos: np.ndarray
    edge_idx: np.ndarray
    edge_id: np.ndarray
    deflection: float

    @property
    def triangle_count(self) -> int:
        return int(self.idx.shape[0] // 3)


def mesh_definition(shape, deflection: float, angular: float) -> DefGeom:
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)

    pos_chunks: list[np.ndarray] = []
    idx_chunks: list[np.ndarray] = []
    fid_chunks: list[np.ndarray] = []
    base = 0

    edge_map = ShapeMap()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
    edge_done: set[int] = set()
    e_pos: list[np.ndarray] = []
    e_idx: list[np.ndarray] = []
    e_id: list[np.ndarray] = []
    e_base = 0

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
        n_nodes = tri.NbNodes()
        n_tris = tri.NbTriangles()
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

        pos_chunks.append(pts)
        idx_chunks.append((tris + base).reshape(-1))
        fid_chunks.append(np.full(n_nodes, fi, dtype=np.float32))

        # Edges belonging to this face (each B-Rep edge only once).
        eex = TopExp_Explorer(face, TopAbs_EDGE)
        while eex.More():
            edge = TopoDS.Edge(eex.Current())
            eex.Next()
            ei = edge_map.FindIndex(edge)
            if ei == 0 or ei in edge_done:
                continue
            poly = BRep_Tool.PolygonOnTriangulation_s(edge, tri, loc)
            if poly is None:
                continue
            nodes = poly.Nodes()
            ids = [nodes.Value(k) - 1 for k in range(nodes.Lower(), nodes.Upper() + 1)]
            if len(ids) < 2:
                continue
            edge_done.add(ei)
            line_pts = pts[ids]
            n = len(ids)
            e_pos.append(line_pts)
            seg = np.arange(n - 1, dtype=np.int64)
            e_idx.append((np.stack([seg, seg + 1], axis=1) + e_base).reshape(-1))
            e_id.append(np.full(n, ei - 1, dtype=np.float32))
            e_base += n

        base += n_nodes

    empty3 = np.zeros((0, 3), dtype=np.float32)
    empty_i = np.zeros((0,), dtype=np.uint32)
    empty_f = np.zeros((0,), dtype=np.float32)
    if pos_chunks:
        pos = np.concatenate(pos_chunks).astype(np.float32)
        idx = np.concatenate(idx_chunks).astype(np.uint32)
        fid = np.concatenate(fid_chunks)
        nrm = compute_vertex_normals(pos, idx)
    else:
        pos, idx, fid, nrm = empty3, empty_i, empty_f, empty3
    if e_pos:
        epos = np.concatenate(e_pos).astype(np.float32)
        eidx = np.concatenate(e_idx).astype(np.uint32)
        eid = np.concatenate(e_id)
    else:
        epos, eidx, eid = empty3, empty_i, empty_f
    return DefGeom(pos, nrm, idx, fid, epos, eidx, eid, deflection)


def mesh_shape_raw(shape, deflection: float, angular: float = 0.5):
    """Tessellate an arbitrary (possibly located) shape into world-space numpy arrays."""
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, False)
    pos_chunks, idx_chunks = [], []
    base = 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        ex.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        n_nodes, n_tris = tri.NbNodes(), tri.NbTriangles()
        if not n_nodes or not n_tris:
            continue
        pts = np.empty((n_nodes, 3))
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
        pos_chunks.append(pts)
        idx_chunks.append((tris + base).reshape(-1))
        base += n_nodes
    if not pos_chunks:
        return np.zeros((0, 3), np.float32), np.zeros((0,), np.uint32)
    return np.concatenate(pos_chunks).astype(np.float32), np.concatenate(idx_chunks).astype(np.uint32)


def _safe_id(text: str) -> str:
    return "p_" + re.sub(r"[^0-9A-Za-z_]+", "_", text)


@dataclass
class MeshResult:
    instance_bboxes: dict[int, np.ndarray]  # instance index -> (2,3) world AABB
    model_bbox: np.ndarray  # (2,3)
    triangle_count: int
    vertex_count: int
    base_deflection: float


def _definition_bbox(shape) -> np.ndarray | None:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        return None
    return np.array(
        [
            [box.GetXMin(), box.GetYMin(), box.GetZMin()],
            [box.GetXMax(), box.GetYMax(), box.GetZMax()],
        ]
    )


def _transform_bbox(bb: np.ndarray, m: np.ndarray) -> np.ndarray:
    corners = np.array([[x, y, z] for x in bb[:, 0] for y in bb[:, 1] for z in bb[:, 2]])
    w = corners @ m[:3, :3].T + m[:3, 3]
    return np.array([w.min(axis=0), w.max(axis=0)])


def build_model_files(
    model: ImportedModel,
    model_id: str,
    model_name: str,
    out_glb,
    out_json,
    progress: Progress | None = None,
) -> MeshResult:
    def report(msg: str, frac: float) -> None:
        if progress:
            progress(msg, frac)

    insts = model.instances
    leaf_insts = [i for i in insts if i.is_leaf and not model.definitions[i.def_key].shape.IsNull()]

    # --- model size -> deflection baseline
    report("Measuring model", 0.0)
    def_bb: dict[str, np.ndarray | None] = {}
    for key in {i.def_key for i in leaf_insts}:
        def_bb[key] = _definition_bbox(model.definitions[key].shape)
    mins, maxs = [], []
    for i in leaf_insts:
        bb = def_bb.get(i.def_key)
        if bb is None:
            continue
        w = _transform_bbox(bb, loc_matrix(i.global_loc))
        mins.append(w[0])
        maxs.append(w[1])
    if not mins:
        raise RuntimeError("The STEP file contains no geometry that can be displayed")
    model_bb = np.array([np.min(mins, axis=0), np.max(maxs, axis=0)])
    model_diag = float(np.linalg.norm(model_bb[1] - model_bb[0])) or 1.0
    base_defl = min(max(model_diag * K_DEFL, model_diag * K_MIN), model_diag * K_MAX)

    # --- mesh each unique definition once
    geoms: dict[str, DefGeom] = {}
    keys = sorted({i.def_key for i in leaf_insts})
    for n, key in enumerate(keys):
        d = model.definitions[key]
        bb = def_bb.get(key)
        part_diag = float(np.linalg.norm(bb[1] - bb[0])) if bb is not None else model_diag
        scale = min(max(max(part_diag, model_diag * 0.001) / model_diag, 0.05), 2.0)
        geoms[key] = mesh_definition(d.shape, base_defl * scale, ANGULAR_DEFLECTION)
        report(f"Meshing parts ({n + 1}/{len(keys)})", (n + 1) / len(keys))

    # --- world bounding boxes from mesh vertices (tight, no extra OCCT calls)
    inst_bb: dict[int, np.ndarray] = {}
    for i in leaf_insts:
        g = geoms[i.def_key]
        if g.pos.shape[0] == 0:
            continue
        m = loc_matrix(i.global_loc)
        w = g.pos.astype(np.float64) @ m[:3, :3].T + m[:3, 3]
        pad = g.deflection + 1e-6
        inst_bb[i.index] = np.array([w.min(axis=0) - pad, w.max(axis=0) + pad])

    # --- glTF
    report("Writing model", 0.0)
    gb = GltfBuilder()
    mesh_cache: dict[tuple, tuple[int | None, int | None]] = {}
    acc_cache: dict[str, tuple] = {}
    tri_total = 0
    vert_total = 0

    def def_accessors(key: str):
        if key in acc_cache:
            return acc_cache[key]
        g = geoms[key]
        faces = edges = None
        if g.idx.shape[0]:
            faces = (
                gb.add_float_accessor(g.pos, "VEC3", bounds=True),
                gb.add_float_accessor(g.nrm, "VEC3"),
                gb.add_float_accessor(g.face_id, "SCALAR"),
                gb.add_index_accessor(g.idx),
            )
        if g.edge_idx.shape[0]:
            edges = (
                gb.add_float_accessor(g.edge_pos, "VEC3", bounds=True),
                gb.add_float_accessor(g.edge_id, "SCALAR"),
                gb.add_index_accessor(g.edge_idx),
            )
        acc_cache[key] = (faces, edges)
        return acc_cache[key]

    def face_mesh(key: str, color) -> int | None:
        ck = ("f", key, tuple(round(c, 3) for c in color))
        if ck in mesh_cache:
            return mesh_cache[ck][0]
        faces, _ = def_accessors(key)
        if faces is None:
            mesh_cache[ck] = (None, None)
            return None
        mat = gb.material(color)
        gb.meshes.append(
            {
                "name": f"{_safe_id(key)}_Faces",
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": faces[0],
                            "NORMAL": faces[1],
                            "_FACE_ID": faces[2],
                        },
                        "indices": faces[3],
                        "mode": 4,
                        "material": mat,
                    }
                ],
            }
        )
        mesh_cache[ck] = (len(gb.meshes) - 1, None)
        return mesh_cache[ck][0]

    def edge_mesh(key: str) -> int | None:
        ck = ("e", key)
        if ck in mesh_cache:
            return mesh_cache[ck][0]
        _, edges = def_accessors(key)
        if edges is None:
            mesh_cache[ck] = (None, None)
            return None
        gb.meshes.append(
            {
                "name": f"{_safe_id(key)}_Edges",
                "primitives": [
                    {
                        "attributes": {"POSITION": edges[0], "_EDGE_ID": edges[1]},
                        "indices": edges[2],
                        "mode": 1,
                    }
                ],
            }
        )
        mesh_cache[ck] = (len(gb.meshes) - 1, None)
        return mesh_cache[ck][0]

    node_of: dict[int, int] = {}
    tree_entries = []
    parts: dict[str, dict] = {}
    part_stats = []
    for i in insts:
        d = model.definitions[i.def_key]
        part_id = _safe_id(i.def_key)
        parent_node = node_of.get(i.parent) if i.parent is not None else None
        node = gb.add_node(
            i.name,
            parent=parent_node,
            matrix=_gltf_matrix(loc_matrix(i.local_loc)),
            extras={"cadreview_instanceId": i.id, "cadreview_partId": part_id},
        )
        node_of[i.index] = node
        if i.is_leaf and i.def_key in geoms:
            color = i.color or d.color or DEFAULT_COLOR
            fm = face_mesh(i.def_key, color)
            if fm is not None:
                gb.add_node("Faces", parent=node, mesh=fm)
            em = edge_mesh(i.def_key)
            if em is not None:
                gb.add_node("Edges", parent=node, mesh=em)
            g = geoms[i.def_key]
            tri_total += g.triangle_count
            vert_total += int(g.pos.shape[0])

        tree_entries.append(
            {
                "id": i.id,
                "name": i.name,
                "path": i.path,
                "parentId": insts[i.parent].id if i.parent is not None else None,
                "childrenIds": [insts[c].id for c in i.children],
                "partIds": [part_id],
                "visibleDefault": True,
            }
        )
        parts.setdefault(part_id, {"name": d.name, "nodeIds": []})["nodeIds"].append(i.id)
        if i.is_leaf and i.def_key in geoms:
            g = geoms[i.def_key]
            part_stats.append(
                {
                    "partId": part_id,
                    "tris": g.triangle_count,
                    "verts": int(g.pos.shape[0]),
                    "defl": g.deflection,
                }
            )

    out_glb.write_bytes(gb.to_glb())

    meta = {
        "model": {
            "id": model_id,
            "name": model_name,
            "units": "mm",
            "bbox": {"min": model_bb[0].tolist(), "max": model_bb[1].tolist()},
        },
        "tree": tree_entries,
        "roots": [insts[r].id for r in model.roots],
        "parts": parts,
        "meshes": {},
        "export": {
            "meshQuality": "medhigh",
            "linearDeflection": base_defl,
            "angularDeflection": ANGULAR_DEFLECTION,
            "triangleCount": tri_total,
            "vertexCount": vert_total,
        },
        "partStats": part_stats,
        "gltf": {
            "faceIdAttribute": "_FACE_ID",
            "edgeIdAttribute": "_EDGE_ID",
            "facesNodeName": "Faces",
            "edgesNodeName": "Edges",
        },
    }
    out_json.write_text(json.dumps(meta), encoding="utf-8")
    report("Model ready", 1.0)
    return MeshResult(inst_bb, model_bb, tri_total, vert_total, base_defl)
