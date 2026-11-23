"""Scene assembly: optical mesh + project setup + library -> arrays and an Embree scene for the tracer."""

from __future__ import annotations

import fnmatch
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from .. import library as lib
from ..optical_mesh import triangle_normals_areas
from . import faceref, pixel_area, spectral, tags
from .kernels import SD

MAX_DETECTOR_PIXELS = 600_000  # per detector; about 160 bytes per pixel and thread (the angle rings add 12 of the 20 values, see kernels.NRING)
RING_STEP_DEG = 5.0  # width of one angle ring around the viewing cone of an exit surface


def ring_edges_deg(cone_deg: float) -> np.ndarray:
    """The 7 edge angles (deg) of the 6 rings around a viewing cone: ``cone_deg`` is edge 3, three rings lie inside it and three outside.

    The rings are ``RING_STEP_DEG`` wide (narrower for a small cone, so that the inner edge stays above 0) and clipped to 0..90.
    Ring r holds the light that leaves between edge r and edge r + 1; the cone itself is tallied separately (all light up to edge 3).
    """
    t0 = float(np.clip(cone_deg, 0.0, 90.0))
    step = min(RING_STEP_DEG, max(t0, 1.0) / 3.0)
    return np.clip(t0 + (np.arange(7) - 3) * step, 0.0, 90.0)

DEFAULT_FINISH = "black"

_THREADS = max(1, min(os.cpu_count() or 1, 16))
_POOL: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(_THREADS, thread_name_prefix="embree")
    return _POOL


# -- setup helpers ----------------------------------------------------------------------------
def name_matches(pattern: str, inst: dict) -> bool:
    """Wildcard match of a part pattern against an instance's path, name or part (definition) name."""
    pat = (pattern or "").strip().lower()
    if not pat:
        return False
    for cand in (inst["path"], inst["name"], inst.get("partName", "")):
        if fnmatch.fnmatchcase(str(cand).lower(), pat):
            return True
    return False


def resolve_part(inst: dict, rules: list[dict]) -> dict:
    """Merge every matching part rule in order (later rules override earlier ones)."""
    out = {"enabled": True, "material": None, "finish": DEFAULT_FINISH, "faces": {}, "face_sigs": {}}
    for rule in rules:
        if not name_matches(rule.get("match", ""), inst):
            continue
        for key in ("enabled", "material", "finish"):
            if key in rule:
                out[key] = rule[key]
        out["faces"].update({str(k): v for k, v in (rule.get("faces") or {}).items()})
        out["face_sigs"].update({str(k): v for k, v in (rule.get("face_sigs") or {}).items()})
    return out


@dataclass
class Detector:
    id: str
    name: str
    origin: np.ndarray
    u: np.ndarray
    v: np.ndarray
    n: np.ndarray
    ext: np.ndarray  # umin, umax, vmin, vmax
    nu: int
    nv: int
    offset: int
    cone_deg: float
    area_map: np.ndarray  # (nv, nu) surface area of the detector in each pixel, mm^2
    area_total: float
    tri_ids: np.ndarray
    corners: np.ndarray  # (4,3) world corners of the map rectangle (of the flat projection)
    mode: int = 0  # 0 flat projection along the average normal, 1 unwrapped surface (per-triangle UV)
    tri_uv: np.ndarray | None = None  # (n tris, 3, 2) mm, in the order of tri_ids, when unwrapped
    mesh: dict | None = None  # overlay mesh (positions, normals, uv in 0..1) when unwrapped
    through: bool = False  # exit surface that only measures the light leaving through it: the light goes on (to a table, a second surface ...)
    kind: str = "exit"  # "exit": light leaving a transmissive part through the faces; "receiver": light arriving on (and reflected by) the faces
    view: np.ndarray | None = None  # receiver: unit vector from the surface towards the viewer (the axis of the viewing cone); exit: the normal

    @property
    def view_cos(self) -> float:
        """Cosine of the angle between the view direction and the surface normal (1 for an exit surface, whose cone is about the normal).

        The projected solid angle of a cone of half angle a about a direction at angle t from the normal is pi sin^2(a) cos(t)
        (while the whole cone lies above the surface), so this factor turns the luminance normalisation of a normal-aligned cone
        into that of a tilted one.
        """
        if self.kind != "receiver" or self.view is None:
            return 1.0
        return float(max(np.dot(self.view, self.n), 0.0))

    @property
    def ring_deg(self) -> np.ndarray:
        """Edge angles (deg) of the angle rings around this exit surface's viewing cone."""
        return ring_edges_deg(self.cone_deg)


@dataclass
class Scene:
    V: np.ndarray
    N: np.ndarray
    T: np.ndarray
    tri_n: np.ndarray
    tri_body: np.ndarray
    tri_fin: np.ndarray
    tri_det: np.ndarray
    bodies: list[dict]
    body_mat: np.ndarray
    mat_ids: list[str]
    mat_n: np.ndarray
    mat_ka: np.ndarray
    mat_mus: np.ndarray
    mat_g: np.ndarray
    fin_ids: list[str]
    fin_type: np.ndarray
    fin_p: np.ndarray
    fin_spec: np.ndarray  # (nfin, 2, NL): colour shape of the diffuse and the specular reflectance
    detectors: list[Detector]
    det_o: np.ndarray
    det_u: np.ndarray
    det_v: np.ndarray
    det_n: np.ndarray
    det_ext: np.ndarray
    det_nuv: np.ndarray
    det_off: np.ndarray
    det_cos: np.ndarray
    det_mode: np.ndarray  # per detector: 0 flat projection, 1 unwrapped (uses tri_uv)
    det_kind: np.ndarray  # per detector: 0 exit surface, 1 receiver, 2 exit surface that lets the light go on
    det_view: np.ndarray  # (ndet, 3) per detector: direction from the surface towards the viewer (receivers; the normal otherwise)
    tri_uv: np.ndarray  # (n scene tris, 3, 2) unwrapped coordinates of detector triangles (zeros elsewhere)
    total_pix: int
    eps: float
    diag: float
    instance_to_body: dict[int, int]
    tri_opt_index: np.ndarray  # scene triangle -> optical triangle index
    warnings: list[str] = field(default_factory=list)
    resolved: dict = field(default_factory=dict)
    _embree: object = None
    # analysis tags (engine/tags.py): CAD face number of every triangle, analysis group (-1 none), compact index of the faces in a group
    tri_gface: np.ndarray | None = None
    tri_group: np.ndarray | None = None
    tri_tface: np.ndarray | None = None
    groups: list = field(default_factory=list)
    group_issues: list = field(default_factory=list)  # face selections of the analysis groups that do not resolve in this model
    gface_body: np.ndarray | None = None
    gface_face: np.ndarray | None = None
    tface_gface: np.ndarray | None = None
    coarse: dict | None = None  # how exit-map pixels fold into the coarse analysis maps
    det_ring: np.ndarray | None = None  # (ndet, 7) cos of the edges of the angle rings around every viewing cone

    # -- Embree ------------------------------------------------------------------------------
    def build_embree(self) -> None:
        from embreex import rtcore_scene as rtcs
        from embreex.mesh_construction import TriangleMesh

        sc = rtcs.EmbreeScene()
        self._mesh = TriangleMesh(sc, self.V.astype(np.float32), self.T.astype(np.int32))
        self._embree = sc

    def intersect(self, o32: np.ndarray, d32: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = len(o32)
        threads = _THREADS
        if n < 20000 or threads <= 1:
            res = self._embree.run(o32, d32, output=1)
            return res["primID"].astype(np.int32), res["tfar"].astype(np.float64)
        # Embree releases the GIL: split the batch over threads
        bounds = np.linspace(0, n, threads + 1).astype(int)
        prim = np.empty(n, dtype=np.int32)
        tfar = np.empty(n, dtype=np.float64)

        def work(k: int) -> None:
            a, b = bounds[k], bounds[k + 1]
            r = self._embree.run(o32[a:b], d32[a:b], output=1)
            prim[a:b] = r["primID"]
            tfar[a:b] = r["tfar"]

        list(_pool().map(work, range(threads)))
        return prim, tfar

    # -- lookups -------------------------------------------------------------------------------
    def body_index(self, instance_ordinal: int) -> int | None:
        return self.instance_to_body.get(instance_ordinal)


def _fit_detector(
    det_doc: dict, index: int, offset: int, V: np.ndarray, T: np.ndarray, tri_ids: np.ndarray, tri_nrm: np.ndarray, tri_area: np.ndarray, warnings: list[str]
) -> Detector:
    from . import unwrap

    tris = T[tri_ids]
    pts = V[np.unique(tris)]
    area = tri_area[tri_ids]
    total = float(area.sum())
    cen = (V[tris].mean(axis=1) * area[:, None]).sum(axis=0) / max(total, 1e-18)
    nsum = (tri_nrm[tri_ids] * area[:, None]).sum(axis=0)
    if np.linalg.norm(nsum) < 1e-9:
        nsum = np.array([0.0, 0.0, 1.0])
        warnings.append(f"Detector {det_doc.get('name', index)}: faces have no common direction; using +Z.")
    n = nsum / np.linalg.norm(nsum)
    # in-plane principal axis from the vertices
    q = pts - cen
    q = q - np.outer(q @ n, n)
    if len(q) >= 2 and np.linalg.norm(q) > 1e-12:
        cov = q.T @ q
        w, vec = np.linalg.eigh(cov)
        u = vec[:, int(np.argmax(w))]
        u = u - n * (u @ n)
    else:
        u = np.array([1.0, 0.0, 0.0]) - n * n[0]
    if np.linalg.norm(u) < 1e-9:
        u = np.cross(n, [0.0, 0.0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0.0, 0.0])
    u = u / np.linalg.norm(u)
    v = np.cross(n, u)
    flip_u, flip_v = bool(det_doc.get("flip_u", False)), bool(det_doc.get("flip_v", False))  # mirror the map left-right / top-bottom
    if flip_u:
        u = -u
    if flip_v:
        v = -v
    pu, pv = (pts - cen) @ u, (pts - cen) @ v
    ext = np.array([pu.min(), pu.max(), pv.min(), pv.max()])
    pad = 1e-6 + 1e-4 * max(ext[1] - ext[0], ext[3] - ext[2])
    ext += np.array([-pad, pad, -pad, pad])
    corners = np.array(
        [cen + u * ext[0] + v * ext[2], cen + u * ext[1] + v * ext[2], cen + u * ext[1] + v * ext[3], cen + u * ext[0] + v * ext[3]]
    )

    # -- how the surface maps to pixels: flat projection, or unwrapped for strongly curved faces -----
    mapping = str(det_doc.get("mapping", "auto"))
    mode, tri_uv = 0, None
    if mapping in ("auto", "unwrap") and len(tri_ids) >= 2:
        curvature = unwrap.surface_curvature(V, T, tri_ids, tri_nrm, tri_area)
        if mapping == "unwrap" or curvature > 0.03:
            try:
                tri_uv, rep = unwrap.unwrap_faces(V, T, tri_ids)
                tri_uv = tri_uv * np.array([-1.0 if flip_u else 1.0, -1.0 if flip_v else 1.0])
                mode = 1
                if rep["patches"] > 1:
                    warnings.append(f"Detector {det_doc.get('name', index)}: the exit surface has {rep['patches']} separate patches, placed side by side in the map.")
            except ValueError as exc:
                warnings.append(f"Detector {det_doc.get('name', index)}: could not unwrap the curved exit surface ({exc}); using a flat projection.")
    if mode == 1:
        flat = tri_uv.reshape(-1, 2)
        ext = np.array([flat[:, 0].min(), flat[:, 0].max(), flat[:, 1].min(), flat[:, 1].max()])
        pad = 1e-6 + 1e-4 * max(ext[1] - ext[0], ext[3] - ext[2])
        ext += np.array([-pad, pad, -pad, pad])
    lu, lv = ext[1] - ext[0], ext[3] - ext[2]
    res = int(det_doc.get("resolution", 160))
    res = max(8, min(res, 1600))
    pix = max(lu, lv) / res
    nu = int(np.clip(np.ceil(lu / pix), 4, 1600))
    nv = int(np.clip(np.ceil(lv / pix), 4, 1600))
    if nu * nv > MAX_DETECTOR_PIXELS:  # every worker thread keeps its own copy of the maps: bound the memory
        shrink = math.sqrt(MAX_DETECTOR_PIXELS / (nu * nv))
        nu, nv = max(4, int(nu * shrink)), max(4, int(nv * shrink))
        pix = max(lu / nu, lv / nv)
        nu, nv = int(np.clip(np.ceil(lu / pix), 4, 1600)), int(np.clip(np.ceil(lv / pix), 4, 1600))
        warnings.append(f"Detector {det_doc.get('name', index)}: the map was limited to {nu} x {nv} pixels (at most {MAX_DETECTOR_PIXELS:,} per exit surface).")

    # surface area per pixel: every detector triangle clipped against the pixel grid (exact, so no noise of its own)
    if mode == 1:
        xy = tri_uv.copy()
    else:
        tv = V[tris]
        xy = np.stack([(tv - cen) @ u, (tv - cen) @ v], axis=-1)
    xy[..., 0] = (xy[..., 0] - ext[0]) / lu * nu
    xy[..., 1] = (xy[..., 1] - ext[2]) / lv * nv
    amap = pixel_area.exact_pixel_areas(xy, area, nu, nv)
    mesh = None
    if mode == 1:
        nrm_t = tri_nrm[tri_ids]
        mesh = {
            "positions": np.round(V[tris].reshape(-1, 3), 4).ravel().tolist(),
            "normals": np.round(np.repeat(nrm_t, 3, axis=0), 4).ravel().tolist(),
            "uv": np.round(((tri_uv.reshape(-1, 2) - np.array([ext[0], ext[2]])) / np.array([lu, lv])), 5).ravel().tolist(),
        }
    kind = "receiver" if str(det_doc.get("type", "exit")) == "receiver" else "exit"
    view = n.copy()
    cone = float(det_doc.get("cone_deg", 30.0))
    if kind == "receiver":
        # the viewer: elevation above the surface plane (90 = looking straight down the normal) and azimuth in the map plane
        # (0 = along the red arrow, towards the green one)
        el = math.radians(float(det_doc.get("view_el", 90.0)))
        az = math.radians(float(det_doc.get("view_az", 0.0)))
        view = math.cos(el) * (math.cos(az) * u + math.sin(az) * v) + math.sin(el) * n
        view = view / np.linalg.norm(view)
        el_deg = math.degrees(math.asin(float(np.clip(np.dot(view, n), -1.0, 1.0))))
        name = det_doc.get("name", index)
        if el_deg <= 1e-6:
            warnings.append(f"Detector {name}: the viewing direction lies in the surface plane, so nothing is seen. Raise the view elevation.")
        elif el_deg < cone:
            warnings.append(
                f"Detector {name}: the viewing cone ({cone:g} deg) reaches below the surface at an elevation of {el_deg:.0f} deg; "
                "luminance is only exact when the whole cone is above the surface. Use a smaller cone or a higher view elevation."
            )
    return Detector(
        id=str(det_doc.get("id", f"det{index}")),
        name=str(det_doc.get("name", f"Detector {index + 1}")),
        origin=cen,
        u=u,
        v=v,
        n=n,
        ext=ext,
        nu=nu,
        nv=nv,
        offset=offset,
        cone_deg=float(det_doc.get("cone_deg", 30.0)),
        area_map=amap,
        area_total=total,
        tri_ids=tri_ids,
        corners=corners,
        mode=mode,
        tri_uv=tri_uv,
        mesh=mesh,
        kind=kind,
        through=kind == "exit" and bool(det_doc.get("pass_through", False)),
        view=view,
    )


def build_scene(opt: dict[str, np.ndarray], opt_doc: dict, setup: dict, library: dict | None = None, build_rays: bool = True) -> Scene:
    """Assemble the scene for one revision + setup. Raises ValueError for setups that cannot be simulated."""
    library = library or lib.get_library()
    warnings: list[str] = []
    insts = opt_doc["instances"]
    rules = setup.get("parts", [])

    # -- materials and finishes used -------------------------------------------------------------
    mat_ids = ["__air__"]
    resolved: list[lib.Resolved] = [lib.Resolved(np.ones(spectral.NL), np.zeros(spectral.NL), np.zeros(spectral.NL), np.zeros(spectral.NL))]
    fin_ids: list[str] = []
    fin_codes: list[tuple[int, np.ndarray]] = []
    fin_specs: list[np.ndarray] = []

    def material_index(mid: str) -> int:
        if mid not in mat_ids:
            doc = library["materials"].get(mid)
            if doc is None:
                raise ValueError(f"Material {mid!r} is not in the library")
            mat_ids.append(mid)
            resolved.append(lib.resolve_material(doc))
        return mat_ids.index(mid)

    def finish_index(fid: str) -> int:
        if fid not in fin_ids:
            doc = library["finishes"].get(fid)
            if doc is None:
                raise ValueError(f"Finish {fid!r} is not in the library")
            fin_ids.append(fid)
            fin_codes.append(lib.resolve_finish(doc))
            fin_specs.append(lib.finish_spectra(doc))
        return fin_ids.index(fid)

    def surface_index(mid: str, fid: str) -> int:
        """Finish entry composed from an opaque surface material and the part's texture finish (roughness)."""
        key = f"{mid}+{fid}"
        if key not in fin_ids:
            mdoc = library["materials"].get(mid)
            if mdoc is None:
                raise ValueError(f"Material {mid!r} is not in the library")
            if not lib.is_surface_material(mdoc):
                raise ValueError(f"Material {mid!r} is not an opaque surface material")
            doc = lib.surface_finish_doc(mdoc, library["finishes"].get(fid))
            fin_ids.append(key)
            fin_codes.append(lib.resolve_finish(doc))
            fin_specs.append(lib.finish_spectra(doc))
        return fin_ids.index(key)

    # -- bodies and triangles ---------------------------------------------------------------------
    bodies: list[dict] = []
    instance_to_body: dict[int, int] = {}
    keep_tri = np.zeros(len(opt["tris"]), dtype=bool)
    tri_fin_full = np.zeros(len(opt["tris"]), dtype=np.int32)
    body_mat: list[int] = []
    for inst in insts:
        part = resolve_part(inst, rules)
        if not part["enabled"]:
            continue
        mid = part["material"]
        if mid and library["materials"].get(mid) is None:
            raise ValueError(f"Material {mid!r} is not in the library")
        surface_mat = lib.is_surface_material(library["materials"].get(mid)) if mid else False  # opaque surface material: no volume
        transparent = bool(mid) and not surface_mat
        if transparent and not inst["closed"]:
            warnings.append(f"{inst['path']}: has material {mid!r} but its mesh is not watertight; treating it as opaque.")
            transparent = False
        bi = len(bodies)
        instance_to_body[inst["ordinal"]] = bi
        mat_idx = material_index(mid) if transparent else 0
        body_mat.append(mat_idx)
        base_fin = part["finish"] or DEFAULT_FINISH
        fdoc = library["finishes"].get(base_fin)
        if fdoc is None:
            raise ValueError(f"Finish {base_fin!r} is not in the library")
        bodies.append(
            {
                "index": bi,
                "instance": inst["ordinal"],
                "id": inst["id"],
                "path": inst["path"],
                "name": inst["name"],
                "material": mid if (transparent or surface_mat) else None,
                "finish": base_fin,
                "transparent": transparent,
            }
        )
        t0, tn = inst["triOffset"], inst["triCount"]
        keep_tri[t0 : t0 + tn] = True
        tri_face = opt["tri_face"][t0 : t0 + tn]
        # a surface material composes with the part's texture finish; a bundled opaque finish (e.g. the "*" default black) does not
        # carry a texture, so the material's own roughness applies
        fins = np.full(tn, surface_index(mid, base_fin) if surface_mat else finish_index(base_fin), dtype=np.int32)
        for face_s, ffid in part["faces"].items():
            if not ffid.startswith("mat:") and ffid not in library["finishes"]:
                raise ValueError(f"Finish {ffid!r} is not in the library")
            fidx = faceref.resolve_face(opt, inst, int(face_s), part["face_sigs"].get(str(face_s)), float(opt_doc.get("diagonal", 100.0)), f"{inst['path']} finish override", warnings)
            if fidx is None:
                warnings.append(f"{inst['path']}: the face with a finish override ({face_s}) no longer exists; override ignored.")
                continue
            if ffid.startswith("mat:"):  # face painted with an opaque surface material (keeps the part's texture)
                fins[tri_face == fidx] = surface_index(ffid[4:], base_fin)
            elif surface_mat and lib.is_texture_finish(library["finishes"][ffid]):  # texture override on a surface material: new roughness
                fins[tri_face == fidx] = surface_index(mid, ffid)
            else:
                fins[tri_face == fidx] = finish_index(ffid)
        if not transparent:  # opaque bodies only have opaque finishes
            for k, fid in enumerate(fin_ids):
                if fin_codes[k][0] == 0:
                    sel = fins == k
                    if sel.any():
                        warnings.append(f"{inst['path']}: a transparent finish on an opaque part is treated as black.")
                        fins[sel] = finish_index("black")
        tri_fin_full[t0 : t0 + tn] = fins
    if not bodies:
        raise ValueError("No part is enabled in the setup")

    tri_opt_index = np.nonzero(keep_tri)[0]
    V = opt["verts"].astype(np.float64)
    N = opt["normals"].astype(np.float64)
    T = opt["tris"][tri_opt_index].astype(np.int32)
    tri_n_all, tri_area_all = triangle_normals_areas(V, opt["tris"])
    tri_n = tri_n_all[tri_opt_index]
    tri_body = np.array([instance_to_body[i] for i in opt["tri_inst"][tri_opt_index]], dtype=np.int32)
    tri_fin = tri_fin_full[tri_opt_index]
    tri_det = np.full(len(T), -1, dtype=np.int32)

    # -- detectors -----------------------------------------------------------------------------------
    opt_to_scene = np.full(len(opt["tris"]), -1, dtype=np.int64)
    opt_to_scene[tri_opt_index] = np.arange(len(tri_opt_index))
    detectors: list[Detector] = []
    face_refs: dict = {"detectors": {}, "leds": {}}  # face references as resolved on this revision (for the UI)
    offset = 0
    for k, ddoc in enumerate(setup.get("detectors", [])):
        ids: list[np.ndarray] = []
        for sel in ddoc.get("faces", []):
            matched = [i for i in insts if name_matches(sel.get("part", ""), i) and i["ordinal"] in instance_to_body]
            if not matched:
                warnings.append(f"Detector {ddoc.get('name', k)}: no enabled part matches {sel.get('part')!r}.")
            for inst in matched:
                t0, tn = inst["triOffset"], inst["triCount"]
                faces = opt["tri_face"][t0 : t0 + tn]
                if sel.get("face") is not None:
                    fidx = faceref.resolve_face(opt, inst, int(sel["face"]), sel.get("sig"), float(opt_doc.get("diagonal", 100.0)), f"Detector {ddoc.get('name', k)}", warnings)
                    if fidx is None:
                        warnings.append(f"Detector {ddoc.get('name', k)}: face {sel['face']} of {inst['path']} no longer exists.")
                        continue
                    face_sel = faces == fidx
                    face_refs["detectors"].setdefault(str(ddoc.get("id", k)), []).append({"instance": inst["id"], "face": int(fidx)})
                else:
                    face_sel = np.ones(tn, bool)
                ids.append(opt_to_scene[t0 : t0 + tn][face_sel])
        if not ids or sum(len(a) for a in ids) == 0:
            raise ValueError(f"Detector {ddoc.get('name', k + 1)!r} has no faces. Pick the exit surface in the setup.")
        tri_ids = np.unique(np.concatenate(ids))
        det = _fit_detector(ddoc, k, offset, V, T, tri_ids, tri_n, tri_area_all[tri_opt_index], warnings)
        tri_det[tri_ids] = k
        offset += det.nu * det.nv
        detectors.append(det)

    nd = max(len(detectors), 1)
    det_o = np.zeros((nd, 3))
    det_u = np.zeros((nd, 3))
    det_v = np.zeros((nd, 3))
    det_n = np.zeros((nd, 3))
    det_ext = np.zeros((nd, 4))
    det_ext[:, 1] = 1.0
    det_ext[:, 3] = 1.0
    det_nuv = np.ones((nd, 2), dtype=np.int32)
    det_off = np.zeros(nd, dtype=np.int64)
    det_cos = np.ones(nd)
    det_ring = np.ones((nd, 7))  # cos of the 7 ring edges of every exit surface (falling), see ring_edges_deg
    det_mode = np.zeros(nd, dtype=np.int32)
    det_kind = np.zeros(nd, dtype=np.int32)
    det_view = np.zeros((nd, 3))
    det_view[:, 2] = 1.0
    tri_uv_all = np.zeros((len(T) if any(d.mode for d in detectors) else 1, 3, 2))
    for k, d in enumerate(detectors):
        det_mode[k] = d.mode
        det_kind[k] = 1 if d.kind == "receiver" else (2 if d.through else 0)
        det_view[k] = d.view if d.view is not None else d.n
        if d.mode:
            tri_uv_all[d.tri_ids] = d.tri_uv
        det_o[k], det_u[k], det_v[k], det_n[k] = d.origin, d.u, d.v, d.n
        det_ext[k] = d.ext
        det_nuv[k] = (d.nu, d.nv)
        det_off[k] = d.offset
        det_cos[k] = np.cos(np.radians(d.cone_deg))
        det_ring[k] = np.cos(np.radians(d.ring_deg))
        det_ring[k, 3] = det_cos[k]  # the cone edge is the very threshold of the cone tally

    # -- tables ---------------------------------------------------------------------------------------
    mat_n = np.stack([r.n for r in resolved])
    mat_ka = np.stack([r.ka for r in resolved])
    mat_mus = np.stack([r.mus for r in resolved])
    mat_g = np.stack([r.g for r in resolved])
    if not fin_ids:
        finish_index(DEFAULT_FINISH)
    fin_type = np.array([c[0] for c in fin_codes], dtype=np.int32)
    fin_p = np.stack([c[1] for c in fin_codes])
    fin_spec = np.stack(fin_specs)

    no_tag = None
    if any(d.kind == "receiver" for d in detectors):
        no_tag = np.zeros(len(T), dtype=bool)
        for d in detectors:
            if d.kind == "receiver":
                no_tag[d.tri_ids] = True
    tg = tags.build_tags(
        opt, opt_doc, insts, instance_to_body, setup, tri_opt_index, opt_to_scene, tri_body, tri_fin, fin_type, tri_det,
        tri_area_all[tri_opt_index], warnings, no_tag,
    )
    coarse = tags.coarse_layout(det_nuv, det_off, len(detectors), max(offset, 1))

    diag = float(opt_doc.get("diagonal") or np.linalg.norm(V.max(axis=0) - V.min(axis=0)))
    eps = float(np.clip(2e-5 * diag, 1e-4, 5e-3))
    scene = Scene(
        V=V, N=N, T=T, tri_n=tri_n, tri_body=tri_body, tri_fin=tri_fin, tri_det=tri_det,
        bodies=bodies, body_mat=np.array(body_mat, dtype=np.int32),
        mat_ids=mat_ids, mat_n=mat_n, mat_ka=mat_ka, mat_mus=mat_mus, mat_g=mat_g,
        fin_ids=fin_ids, fin_type=fin_type, fin_p=fin_p, fin_spec=fin_spec,
        detectors=detectors, det_o=det_o, det_u=det_u, det_v=det_v, det_n=det_n, det_ext=det_ext,
        det_nuv=det_nuv, det_off=det_off, det_cos=det_cos, det_mode=det_mode, det_kind=det_kind, det_view=det_view, tri_uv=tri_uv_all, total_pix=max(offset, 1),
        eps=eps, diag=diag, instance_to_body=instance_to_body, tri_opt_index=tri_opt_index, warnings=warnings,
        resolved=face_refs,
        tri_gface=tg["tri_gface"], tri_group=tg["tri_group"], tri_tface=tg["tri_tface"], groups=tg["groups"], group_issues=tg["issues"],
        gface_body=tg["gface_body"], gface_face=tg["gface_face"], tface_gface=tg["tface_gface"], coarse=coarse,
        det_ring=det_ring,
    )
    if build_rays:
        scene.build_embree()
    return scene
