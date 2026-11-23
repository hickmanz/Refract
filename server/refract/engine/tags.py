"""Analysis tags of a scene: which CAD face every triangle belongs to, and the analysis *groups* of faces.

A group is a set of CAD faces that the analysis treats as one surface ("reflector facet 3"). While the tracer runs, every
ray remembers the groups it has hit; the exit map can then be split by the last group the light touched, and the faces
that a design engineer changes can be related to the part of the exit surface they feed.

Group numbers run 0 .. 61 for the named groups; number 62 (``OTHER``) collects what does not fit. Triangles that belong to
no group have -1. A bit mask over group numbers therefore fits an int64.
"""

from __future__ import annotations

import re

import numpy as np

from . import faceref

NAMED = 62  # named groups 0..61
OTHER = 62  # "other tagged faces": every group beyond the named ones
MAX_GROUPS = OTHER + 1

PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080",
    "#e6beff", "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9", "#ff7f50",
    "#00ced1", "#daa520", "#7b68ee", "#2e8b57",
]


def group_color(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def display_name(inst: dict) -> str:
    """The instance name, plus the part (definition) name when that says something else, e.g. ``Reflector (Body1:1)``."""
    name = str(inst.get("name") or inst.get("path") or "part")
    part = str(inst.get("partName") or "").strip()
    base = re.sub(r":\d+$", "", name).strip()
    return f"{part} ({name})" if part and part.lower() != base.lower() else name


def _face_entry(inst: dict, face: int | None) -> dict:
    return {"instance": inst["id"], "ordinal": int(inst["ordinal"]), "path": inst["path"], "face": None if face is None else int(face)}


def build_tags(
    opt: dict,
    opt_doc: dict,
    insts: list[dict],
    instance_to_body: dict[int, int],
    setup: dict,
    tri_opt_index: np.ndarray,
    opt_to_scene: np.ndarray,
    tri_body: np.ndarray,
    tri_fin: np.ndarray,
    fin_type: np.ndarray,
    tri_det: np.ndarray,
    tri_area: np.ndarray,
    warnings: list[str],
    no_tag: np.ndarray | None = None,
) -> dict:
    """Face numbers and groups for the triangles of a scene.

    Returns ``tri_gface`` (one number per CAD face of the scene), ``tri_group`` (-1 = none), ``tri_tface`` (index among the faces
    that belong to a group, -1 = none), ``groups`` (list of dicts) and the tables ``gface_body`` / ``gface_face`` / ``tface_gface``.
    """
    from .scene import name_matches  # (scene imports this module)

    ntri = len(tri_opt_index)
    face_s = opt["tri_face"][tri_opt_index].astype(np.int64)
    mult = int(face_s.max()) + 2 if ntri else 2
    key = tri_body.astype(np.int64) * mult + face_s
    uniq, inv = np.unique(key, return_inverse=True)
    tri_gface = inv.astype(np.int32).reshape(-1)
    gface_body = (uniq // mult).astype(np.int32)
    gface_face = (uniq % mult).astype(np.int32)

    diag = float(opt_doc.get("diagonal", 100.0))
    tri_group = np.full(ntri, -1, dtype=np.int32)
    groups: list[dict] = []
    user = [g for g in (setup.get("analysisGroups") or []) if isinstance(g, dict)]
    issues: list[dict] = []  # face selections of the groups that no longer resolve (the faces are not in this model)

    def add_group(gid: str, name: str, color: str | None, tris: np.ndarray, faces: list[dict], auto: bool) -> None:
        if no_tag is not None and len(tris):
            # the faces of a receiver detector are never tagged: the light that arrives on them is deposited on the receiver map,
            # and its "last tagged surface" must be the one before the receiver (not the receiver itself)
            keep = ~no_tag[tris]
            if not keep.all():
                warnings.append(f"Analysis group {name}: faces of a receiver detector are not tagged and are left out of the group.")
                tris = tris[keep]
        idx = len(groups) if len(groups) < NAMED else OTHER
        if idx == OTHER and groups and groups[-1].get("other"):
            g = groups[-1]  # merge into the existing "other" group
            tris = tris[tri_group[tris] < 0]
            tri_group[tris] = OTHER
            g["faces"].extend(faces)
            g["ntri"] += int(len(tris))
            g["area"] += float(tri_area[tris].sum())
            return
        tris = tris[tri_group[tris] < 0]
        if len(tris) == 0:
            return
        tri_group[tris] = idx
        groups.append(
            {
                "index": idx, "id": gid, "name": name if idx != OTHER else "Other tagged faces",
                "color": color or group_color(idx), "auto": auto, "other": idx == OTHER,
                "faces": faces, "ntri": int(len(tris)), "area": float(tri_area[tris].sum()),
            }
        )

    if user:
        for gi, gdoc in enumerate(user):
            name = str(gdoc.get("name") or f"Group {gi + 1}")
            sel_tris: list[np.ndarray] = []
            faces_meta: list[dict] = []
            missing: list[dict] = []
            for sel in gdoc.get("faces", []):
                matched = [i for i in insts if name_matches(sel.get("part", ""), i) and i["ordinal"] in instance_to_body]
                if not matched:
                    missing.append({"part": sel.get("part"), "face": sel.get("face"), "reason": "part"})
                for inst in matched:
                    t0, tn = inst["triOffset"], inst["triCount"]
                    sc = opt_to_scene[t0 : t0 + tn]
                    if sel.get("face") is not None:
                        fidx = faceref.resolve_face(opt, inst, int(sel["face"]), sel.get("sig"), diag, f"Analysis group {name}", warnings)
                        if fidx is None:
                            missing.append({"part": sel.get("part"), "face": int(sel["face"]), "reason": "face"})
                            continue
                        m = opt["tri_face"][t0 : t0 + tn] == fidx
                        faces_meta.append(_face_entry(inst, fidx))
                    else:
                        m = np.ones(tn, bool)
                        faces_meta.append(_face_entry(inst, None))
                    sel_tris.append(sc[m])
            if missing:
                missing = [dict(t) for t in {tuple(sorted(m.items(), key=lambda kv: kv[0])) for m in missing}]  # one entry per selection
                missing.sort(key=lambda m: (str(m["part"]), -1 if m["face"] is None else m["face"]))
                issues.append({"id": str(gdoc.get("id") or f"g{gi}"), "name": name, "missing": missing})
            if sel_tris:
                add_group(str(gdoc.get("id") or f"g{gi}"), name, gdoc.get("color"), np.unique(np.concatenate(sel_tris)), faces_meta, False)
    if not groups:
        # automatic groups: every CAD face with an opaque finish (reflectors, housings), the largest ones first
        opaque = (fin_type[tri_fin] == 1) & (tri_det < 0)
        cand = np.nonzero(opaque)[0]
        if len(cand):
            gf = tri_gface[cand]
            fids = np.unique(gf)
            area = np.bincount(np.searchsorted(fids, gf), weights=tri_area[cand], minlength=len(fids))
            order = np.argsort(-area, kind="stable")
            srt = np.argsort(gf, kind="stable")
            bounds = np.searchsorted(gf[srt], np.concatenate([fids, [fids[-1] + 1]]))
            by_ordinal = {i["ordinal"]: i for i in insts}
            body_inst = {b: o for o, b in instance_to_body.items()}
            for k in order:
                fid = int(fids[k])
                tris = cand[srt[bounds[k] : bounds[k + 1]]]
                inst = by_ordinal[body_inst[int(gface_body[fid])]]
                face = int(gface_face[fid])
                add_group(f"auto{fid}", f"{display_name(inst)} · face {face}", None, tris, [_face_entry(inst, face)], True)

    # faces that belong to a group get a compact index for the (larger) from/to tables
    tagged = np.unique(tri_gface[tri_group >= 0])
    tri_tface = np.full(ntri, -1, dtype=np.int32)
    sel = tri_group >= 0
    tri_tface[sel] = np.searchsorted(tagged, tri_gface[sel]).astype(np.int32)
    n_missing = sum(len(i["missing"]) for i in issues)
    if n_missing:
        warnings.append(f"{n_missing} face selection{'s' if n_missing != 1 else ''} in analysis groups no longer exist in this model (see the Groups tab).")
    return {
        "issues": issues,
        "tri_gface": tri_gface, "tri_group": tri_group, "tri_tface": tri_tface, "groups": groups,
        "gface_body": gface_body, "gface_face": gface_face, "tface_gface": tagged.astype(np.int32),
    }


# -- coarse maps ---------------------------------------------------------------------------------------------
COARSE_MAX = 100  # pixels along the longer side of an analysis map


def coarse_layout(det_nuv: np.ndarray, det_off: np.ndarray, ndet: int, total_pix: int) -> dict:
    """How the exit-map pixels of every detector fold into the coarser grid of the analysis maps.

    Returns ``cmap`` (coarse pixel of every fine pixel) and, per detector, the offset and size of its coarse map.
    """
    cmap = np.zeros(max(total_pix, 1), dtype=np.int32)
    dets = []
    off = 0
    for k in range(ndet):
        nu, nv = int(det_nuv[k, 0]), int(det_nuv[k, 1])
        f = max(1, int(np.ceil(max(nu, nv) / COARSE_MAX)))
        cnu, cnv = (nu + f - 1) // f, (nv + f - 1) // f
        o = int(det_off[k])
        loc = np.arange(nu * nv)
        cmap[o : o + nu * nv] = off + (loc // nu // f) * cnu + (loc % nu) // f
        dets.append({"offset": off, "nu": cnu, "nv": cnv, "factor": f, "fine_nu": nu, "fine_nv": nv, "fine_offset": o})
        off += cnu * cnv
    return {"cmap": cmap, "dets": dets, "total": off}
