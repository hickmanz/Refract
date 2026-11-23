"""Stable references to B-Rep faces across revisions.

Face indices come from the STEP topology order and change when a design is edited. A face reference in a setup is
therefore ``{face: index, sig: {centroid, normal, area}}``. When a revision is loaded, the index is trusted only if
the face still looks like the one that was picked; otherwise the most similar face of the same part is used (and a
warning is reported), so setups survive typical revision edits.
"""

from __future__ import annotations

import copy

import numpy as np

from ..optical_mesh import face_info

def led_label(led: dict) -> str:
    """"LED 1" for an LED named "LED 1", "LED Top" for one named "Top"."""
    name = str(led.get("name", led.get("id")))
    return name if name.lower().startswith("led") else f"LED {name}"


def instance_face_sigs(opt: dict, inst: dict) -> list[dict | None]:
    cache = opt.setdefault("_face_sigs", {})  # lives and dies with the loaded optical mesh
    key = int(inst["ordinal"])
    if key not in cache:
        cache[key] = [face_info(opt, inst, f) for f in range(inst["faceCount"])]
    return cache[key]


def face_signature(opt: dict, inst: dict, face: int) -> dict | None:
    info = instance_face_sigs(opt, inst)
    if 0 <= face < len(info) and info[face] is not None:
        f = info[face]
        return {"centroid": f["centroid"], "normal": f["normal"], "area": f["area"]}
    return None


def resolve_face(opt: dict, inst: dict, face: int | None, sig: dict | None, diag: float, label: str, warnings: list[str]) -> int | None:
    """Face index in this revision for a stored reference (None when the face cannot be found)."""
    sigs = instance_face_sigs(opt, inst)
    if sig is None or not sig.get("centroid"):
        if face is not None and 0 <= int(face) < len(sigs) and sigs[int(face)] is not None:
            return int(face)
        return None
    c0 = np.asarray(sig["centroid"], dtype=float)
    n0 = np.asarray(sig["normal"], dtype=float)
    a0 = max(float(sig["area"]), 1e-12)

    def dist(f: dict) -> tuple[float, float, float]:
        d = float(np.linalg.norm(np.asarray(f["centroid"]) - c0))
        dn = float(np.dot(np.asarray(f["normal"]), n0))
        ra = float(f["area"]) / a0
        return d, dn, ra

    if face is not None and 0 <= int(face) < len(sigs) and sigs[int(face)] is not None:
        d, dn, ra = dist(sigs[int(face)])
        if d <= 0.01 * diag + 1e-6 and dn >= 0.99 and 0.9 <= ra <= 1.1:
            return int(face)
    best = None
    for f, info in enumerate(sigs):
        if info is None:
            continue
        d, dn, ra = dist(info)
        if dn < 0.95 or not (0.4 <= ra <= 2.5) or d > 0.15 * diag:
            continue
        score = d / max(diag, 1e-9) + (1.0 - dn) * 2.0 + abs(np.log(ra)) * 0.2
        if best is None or score < best[0]:
            best = (score, f)
    if best is None:
        return None
    if best[1] != face:
        warnings.append(f"{label}: face {face} was re-matched to face {best[1]} (the design changed). Please check it.")
    return best[1]


def accept_rematches(setup: dict, opt: dict, opt_doc: dict) -> tuple[dict, list[str]]:
    """Store the faces that resolve differently on this revision in the setup (index and signature).

    Without this a setup keeps pointing at the faces of the revision it was made on, so the "re-matched" warning
    comes back on every check and run. Returns ``(new_setup, changes)``; the given setup is not modified. Only
    references whose part pattern matches exactly one part can be updated; the others are left as they are.
    """
    from .scene import name_matches  # (scene imports this module)

    new = copy.deepcopy(setup)
    insts = opt_doc["instances"]
    diag = float(opt_doc.get("diagonal", 100.0))
    changes: list[str] = []

    def one(pattern: str | None) -> dict | None:
        found = [i for i in insts if name_matches(pattern or "", i)]
        return found[0] if len(found) == 1 else None

    def retarget(inst: dict, face, sig, label: str) -> tuple[int, dict | None] | None:
        """(new face, new signature) when the reference moved, else None."""
        if face is None:
            return None
        throwaway: list[str] = []
        fidx = resolve_face(opt, inst, int(face), sig, diag, label, throwaway)
        if fidx is None or fidx == int(face):
            return None
        changes.append(f"{label}: face {int(face)} is now face {fidx}.")
        return fidx, face_signature(opt, inst, fidx)

    for led in new.get("leds", []):
        place = led.get("placement") or {}
        if place.get("type", "face") != "face" or place.get("face") is None:
            continue
        inst = one(place.get("part"))
        if inst is None:
            continue
        hit = retarget(inst, place.get("face"), place.get("sig"), led_label(led))
        if hit:
            place["face"], place["sig"] = hit

    refs = [(f"Detector {d.get('name', d.get('id'))}", d.get("faces") or []) for d in new.get("detectors", [])]
    refs += [(f"Analysis group {g.get('name', g.get('id'))}", g.get("faces") or []) for g in new.get("analysisGroups") or [] if isinstance(g, dict)]
    for label, faces in refs:
        for sel in faces:
            inst = one(sel.get("part"))
            if inst is None:
                continue
            hit = retarget(inst, sel.get("face"), sel.get("sig"), label)
            if hit:
                sel["face"], sel["sig"] = hit

    for rule in new.get("parts", []):
        overrides = rule.get("faces") or {}
        if not overrides:
            continue
        inst = one(rule.get("match"))
        if inst is None:
            continue
        sigs = rule.get("face_sigs") or {}
        moved: dict[str, tuple[str, dict | None]] = {}
        for key in overrides:
            hit = retarget(inst, key, sigs.get(str(key)), f"{inst['path']} finish override")
            if hit:
                moved[str(key)] = (str(hit[0]), hit[1])
        if moved:
            rule["faces"] = {moved[str(k)][0] if str(k) in moved else str(k): v for k, v in overrides.items()}
            rule["face_sigs"] = {
                **{k: v for k, v in sigs.items() if str(k) not in moved},
                **{nk: ns for nk, ns in moved.values() if ns is not None},
            }
    return new, changes
