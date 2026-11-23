"""Suggest a first-pass optical setup for a freshly imported revision from part names and geometry.

It is only a starting point (the user reviews and edits everything), but it saves a lot of clicking for the common
case of "some LEDs, a reflector/housing, a light pipe".
"""

from __future__ import annotations

import copy
import re

import numpy as np

from .engine.faceref import face_signature
from .engine.scene import name_matches
from .optical_mesh import face_info

LED_RE = re.compile(r"(^|[^a-z])(led|diode|emitter)([^a-z]|$)", re.I)
PIPE_RE = re.compile(r"(light ?pipe|lightpipe|light ?guide|lens|diffuser|lightbar|light bar|pipe|window)", re.I)
WHITE_RE = re.compile(r"(reflector|tray|cup|reflect)", re.I)
PCB_RE = re.compile(r"(pcb|board|flex|fpc)", re.I)


def esc(text: str) -> str:
    """Escape fnmatch special characters in a literal part path."""
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def pat(inst: dict, insts: list[dict]) -> str:
    """A part pattern that survives revisions: the instance name when unique, else the path below the root."""
    if sum(1 for i in insts if i["name"] == inst["name"]) == 1:
        return esc(inst["name"])
    parts = inst["path"].split("/")
    return "*/" + esc("/".join(parts[1:])) if len(parts) > 1 else esc(inst["path"])


def _bbox_center(inst: dict) -> np.ndarray:
    bb = np.asarray(inst["bbox"], dtype=float)
    return 0.5 * (bb[0] + bb[1])


def _target_for(led: dict, pipes: list[dict], pipe_center: np.ndarray | None) -> np.ndarray | None:
    """The point of the light pipe nearest to this LED (so LEDs spread along a long bar still look at the pipe, not at its middle)."""
    if not pipes:
        return None
    c = _bbox_center(led)
    lo = np.min([np.asarray(p["bbox"], float)[0] for p in pipes], axis=0)
    hi = np.max([np.asarray(p["bbox"], float)[1] for p in pipes], axis=0)
    p = np.clip(c, lo, hi)
    if np.linalg.norm(p - c) < 1e-6:  # the LED sits inside the pipe bounding box (cavity): use the pipe centre
        return pipe_center
    return p


def suggest(opt_doc: dict, opt: dict) -> dict:
    insts = opt_doc["instances"]
    leds = [i for i in insts if LED_RE.search(i["name"]) or LED_RE.search(i.get("partName", ""))]
    led_ids = {i["ordinal"] for i in leds}
    pipes = [i for i in insts if i["ordinal"] not in led_ids and PIPE_RE.search(i["name"] + " " + i.get("partName", "")) and i["closed"]]
    if not pipes:  # fall back to the largest closed non-LED part
        cand = [i for i in insts if i["ordinal"] not in led_ids and i["closed"]]
        cand.sort(key=lambda i: -(i.get("volume") or 0.0))
        pipes = cand[:1]

    notes: list[str] = []
    parts: list[dict] = [{"match": "*", "finish": "black"}]
    for i in insts:
        text = i["name"] + " " + i.get("partName", "")
        if i["ordinal"] in led_ids:
            parts.append({"match": pat(i, insts), "finish": "led_package"})
        elif i in pipes:
            parts.append({"match": pat(i, insts), "material": "pc_tio2_0p03", "finish": "polished"})
        elif WHITE_RE.search(text):
            parts.append({"match": pat(i, insts), "finish": "white_diffuse"})
        elif PCB_RE.search(text):
            parts.append({"match": pat(i, insts), "finish": "grey_diffuse"})
    if not leds:
        notes.append("No part with 'LED' in its name was found. Add LED placements manually.")
    if not pipes:
        notes.append("No light pipe candidate was found. Assign a material to the transparent part.")

    pipe_center = np.mean([_bbox_center(p) for p in pipes], axis=0) if pipes else None

    led_defs = []
    for k, led in enumerate(leds, start=1):
        target = _target_for(led, pipes, pipe_center)
        best = None
        for f in range(led["faceCount"]):
            fi = face_info(opt, led, f)
            if fi is None or not fi["planar"]:
                continue
            score = fi["area"]
            if target is not None:
                direction = target - np.asarray(fi["centroid"])
                nrm = np.linalg.norm(direction)
                if nrm > 0:
                    # face that looks at the light pipe; weight by area so tiny side faces do not win
                    score = max(0.0, float(np.dot(fi["normal"], direction / nrm))) * fi["area"] ** 0.5
            if best is None or score > best[0]:
                best = (score, f)
        if best is None:
            continue
        led_defs.append(
            {
                "id": f"led{k}",
                "name": led["name"],
                "model": "white_generic",
                "placement": {"type": "face", "part": pat(led, insts), "face": best[1], "sig": face_signature(opt, led, best[1])},
            }
        )

    detectors = []
    if pipes:
        pipe = pipes[0]
        src = np.mean([np.asarray(fd) for fd in [_face_centroid(opt, l, d["placement"]["face"]) for l, d in zip(leds, led_defs)]], axis=0) if led_defs else None
        best = None
        for f in range(pipe["faceCount"]):
            fi = face_info(opt, pipe, f)
            if fi is None or not fi["planar"]:
                continue
            if src is not None:
                direction = np.asarray(fi["centroid"]) - src
                nrm = np.linalg.norm(direction)
                facing = float(np.dot(fi["normal"], direction / nrm)) if nrm > 0 else 0.0
                score = max(0.0, facing) * fi["area"]
            else:
                score = fi["area"]
            if best is None or score > best[0]:
                best = (score, f)
        if best is not None:
            detectors.append(
                {
                    "id": "exit",
                    "name": "Exit surface",
                    "faces": [{"part": pat(pipe, insts), "face": best[1], "sig": face_signature(opt, pipe, best[1])}],
                    "cone_deg": 30,
                    "resolution": 240,
                }
            )
    if not detectors:
        notes.append("No exit surface could be suggested. Pick the exit faces on the light pipe.")

    states = [{"id": "all", "name": "All LEDs on", "drive": {"*": 1.0}}]
    for d in led_defs:
        states.append({"id": f"only_{d['id']}", "name": f"{d['name']} only", "drive": {f"{d['id']}/*": 1.0}})
    return {"parts": parts, "leds": led_defs, "detectors": detectors, "states": states, "notes": notes}


def _matching(pattern: str, insts: list[dict]) -> list[dict]:
    return [i for i in insts if name_matches(pattern, i)]


_AUTO_STATE_RE = re.compile(r"^only_(.+)$")


def reconcile(setup: dict, suggestion: dict, insts: list[dict]) -> tuple[dict, list[str]]:
    """Adapt a setup made for one geometry to another geometry of the same design (a new variant).

    ``suggestion`` is :func:`suggest` for the new geometry and ``insts`` its part instances. The setup keeps what the
    user chose (materials, finishes, LED models, detector settings, custom states) and follows the geometry:

    * LEDs whose part no longer exists are dropped, LED parts that are new get an LED with the model the others use;
    * the emitting face of a kept LED is refreshed when it still points the same way;
    * "<LED> only" states are regenerated for the LEDs that are there now;
    * parts without any rule get the suggested one; a detector whose faces no longer exist gets the suggested exit surface.

    Returns ``(new_setup, warnings)``; nothing is saved here, the user reviews the result.
    """
    out = copy.deepcopy(setup)
    warnings: list[str] = []
    sug_leds = suggestion.get("leds", [])

    # -- LEDs ---------------------------------------------------------------------------------
    kept: list[dict] = []
    dropped_ids: set[str] = set()
    for led in out.get("leds", []):
        place = led.get("placement") or {}
        if place.get("type", "face") != "face":
            kept.append(led)
            continue
        found = _matching(place.get("part", ""), insts)
        if len(found) != 1:
            dropped_ids.add(str(led.get("id")))
            warnings.append(f"LED {led.get('name', led.get('id'))}: its part {place.get('part')!r} matches {len(found)} parts in the new geometry, so the LED was removed.")
            continue
        fresh = next((s for s in sug_leds if s["placement"]["part"] == place.get("part")), None)
        old_n, new_n = (place.get("sig") or {}).get("normal"), ((fresh or {}).get("placement", {}).get("sig") or {}).get("normal")
        if fresh is not None and old_n and new_n and float(np.dot(old_n, new_n)) > 0.99:
            place["face"], place["sig"] = fresh["placement"]["face"], fresh["placement"]["sig"]
        kept.append(led)

    used_parts = {(l.get("placement") or {}).get("part") for l in kept}
    used_ids = {str(l.get("id")) for l in kept}
    models = [l.get("model") for l in kept if l.get("model")]
    model = max(set(models), key=models.count) if models else None
    n = 0
    for s in sug_leds:
        if s["placement"]["part"] in used_parts:
            continue
        while f"led{n + 1}" in used_ids:
            n += 1
        n += 1
        new = copy.deepcopy(s)
        new["id"] = f"led{n}"
        if model:
            new["model"] = model
        used_ids.add(new["id"])
        used_parts.add(new["placement"]["part"])
        kept.append(new)
        warnings.append(f"LED {new['name']}: a new LED part, added with the model {new['model']!r}.")
    out["leds"] = kept

    # -- states -------------------------------------------------------------------------------
    led_ids = [str(l["id"]) for l in kept]
    states = []
    for st in out.get("states", []):
        m = _AUTO_STATE_RE.match(str(st.get("id", "")))
        refs = {k.split("/")[0] for k in (st.get("drive") or {}) if k != "*"}
        if m and (m.group(1) in dropped_ids or m.group(1) in led_ids) and refs <= {m.group(1)}:
            continue  # regenerated below
        gone = refs & dropped_ids
        if gone:
            warnings.append(f"State {st.get('name', st.get('id'))!r} drove a removed LED and was removed.")
            continue
        states.append(st)
    if not any(s.get("id") == "all" for s in states):
        states.insert(0, {"id": "all", "name": "All LEDs on", "drive": {"*": 1.0}})
    for l in kept:
        states.append({"id": f"only_{l['id']}", "name": f"{l.get('name', l['id'])} only", "drive": {f"{l['id']}/*": 1.0}})
    out["states"] = states

    # -- parts --------------------------------------------------------------------------------
    rules = out.setdefault("parts", [])
    specific = [r for r in rules if r.get("match", "*") != "*"]
    for r in suggestion.get("parts", []):
        if r.get("match") == "*":
            if not rules:
                rules.append(copy.deepcopy(r))
            continue
        covered = all(any(name_matches(x.get("match", ""), i) for x in specific) for i in _matching(r["match"], insts))
        if not covered:
            rules.append(copy.deepcopy(r))
            warnings.append(f"Part {r['match']!r}: new in this geometry, given the suggested material/finish.")
    for r in specific:
        if not _matching(r.get("match", ""), insts):
            warnings.append(f"The part rule {r.get('match')!r} matches no part in the new geometry.")

    # -- detectors ----------------------------------------------------------------------------
    sug_dets = suggestion.get("detectors", [])
    if not out.get("detectors"):
        out["detectors"] = copy.deepcopy(sug_dets)
    else:
        for k, det in enumerate(out["detectors"]):
            ok = [f for f in det.get("faces", []) if len(_matching(f.get("part", ""), insts)) == 1]
            if not ok and sug_dets:
                det["faces"] = copy.deepcopy(sug_dets[0]["faces"])
                warnings.append(f"Detector {det.get('name', k)}: its part no longer exists, the suggested exit surface was used. Please check it.")
            elif len(ok) != len(det.get("faces", [])):
                warnings.append(f"Detector {det.get('name', k)}: some of its faces are on parts that no longer exist.")
    return out, warnings


def _face_centroid(opt: dict, inst: dict, face: int):
    fi = face_info(opt, inst, face)
    return fi["centroid"] if fi else [0.0, 0.0, 0.0]
