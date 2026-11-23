"""Where does the light of a run come from? Queries on the analysis data that the tracer saves next to the tallies.

A run with analysis keeps, besides the exit maps:

* the exit map split by the analysis group (a set of CAD faces) that the light touched last, and by the groups it touched at all
  (coarse maps, one set per source channel);
* per CAD face the weight that arrived, was absorbed, reflected and transmitted, and for the faces of a group where the light came
  from and where it went next;
* a log of every deposit on the exit surface (pixel, weight, last group, route, event counters) and of how every ray ended.

Rays can be traced again exactly (``engine.replay``), so a selection of the logs can be drawn as full 3D paths.
Like the tallies, everything is per source channel at unit drive; a state combines the channels linearly.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

import numpy as np

from . import store
from .engine import kernels, spectral, tags

ANALYSIS_FILE = "analysis.npz"
META_FILE = "analysis.json"

EVENT_LABELS = ["Total internal reflection", "Fresnel reflection", "Refraction", "Specular reflection", "Diffuse reflection", "Volume scattering"]
BOUNCE_BINS = [(1, 1, "1"), (2, 2, "2"), (3, 4, "3-4"), (5, 8, "5-8"), (9, 16, "9-16"), (17, 32, "17-32"), (33, 64, "33-64"), (65, 10**6, "65+")]
OUTCOMES = {
    "exit": None,
    "absorbed": (kernels.OUT_ABS, kernels.OUT_ROUL),
    "escaped": (kernels.OUT_ESC, kernels.OUT_LEAK),
    "lost": (kernels.OUT_MAXB, kernels.OUT_ANOM),
}


def _f(x, nd=6) -> float:
    return float(np.round(x, nd))


# -- saving -----------------------------------------------------------------------------------------------------
def save(rdir: Path, scene, used: list, tallies: list, settings: dict, seeds: list[int], n_rays: int) -> None:
    """Write analysis.npz and analysis.json for a finished run (tallies: one Tally per used channel, in order)."""
    from .optical_mesh import triangle_normals_areas

    ana = [t.analysis for t in tallies]
    nface = len(scene.gface_body)
    _, tri_area = triangle_normals_areas(scene.V, scene.T)
    gface_area = np.bincount(scene.tri_gface, weights=tri_area, minlength=nface)
    gface_group = np.full(nface, -1, dtype=np.int32)
    tg = scene.tri_group >= 0
    gface_group[scene.tri_gface[tg]] = scene.tri_group[tg]  # (a face split over several groups takes the last one)

    out: dict[str, np.ndarray] = {
        "grp_map": np.stack([a.grp_map for a in ana]).astype(np.float32),
        "touch_map": np.stack([a.touch_map for a in ana]).astype(np.float32),
        "tot_map": np.stack([a.tot_map for a in ana]).astype(np.float32),
        "face_t": np.stack([a.face_t for a in ana]),
        "face_from": np.stack([a.face_from for a in ana]),
        "face_to": np.stack([a.face_to for a in ana]),
        "gface_body": scene.gface_body, "gface_face": scene.gface_face, "gface_area": gface_area, "gface_group": gface_group,
        "tface_gface": scene.tface_gface,
    }
    for kind in ("dep", "end"):
        cols: dict[str, list] = {}
        for ci, a in enumerate(ana):
            log = getattr(a, kind)
            if not log:
                continue
            for k, v in log.items():
                cols.setdefault(k, []).append(v)
            cols.setdefault("ch", []).append(np.full(len(log["rid"]), ci, dtype=np.int16))
        for k, parts in cols.items():
            out[f"{kind}_{k}"] = np.concatenate(parts)
    np.savez_compressed(rdir / ANALYSIS_FILE, **out)

    meta = {
        "version": 1,
        "groups": [{k: g[k] for k in ("index", "id", "name", "color", "auto", "other", "faces", "ntri", "area")} for g in scene.groups],
        "bodies": [{"index": b["index"], "id": b["id"], "path": b["path"], "name": b["name"]} for b in scene.bodies],
        "coarse": {"dets": scene.coarse["dets"], "total": int(scene.coarse["total"])},
        "channels": [{"key": s.key, "led_id": s.led_id, "channel_id": s.channel_id, "seed": int(seeds[i])} for i, s in enumerate(used)],
        "logEnd": int(ana[0].log_end) if ana else 0,
        "nRays": int(n_rays),
        "solver": str(settings.get("solver") or "cpu"),  # the solver that traced these rays: they can only be replayed by the same one
        "rrThreshold": float(settings["rr_threshold"]),
        "maxBounces": int(settings["max_bounces"]),
    }
    store.write_json(rdir / META_FILE, meta)


# -- loading ----------------------------------------------------------------------------------------------------
class RunAnalysis:
    """The analysis data of one run, combined with the run's tallies (states, channel weights, pixel areas)."""

    def __init__(self, rid: str, runid: str, data) -> None:
        d = store.run_dir(rid, runid)
        meta = store.read_json(d / META_FILE)
        if meta is None or not (d / ANALYSIS_FILE).exists():
            raise FileNotFoundError("This run was made without analysis data. Run it again to get the analysis tools.")
        self.rid, self.runid, self.data, self.meta = rid, runid, data, meta
        self._z = np.load(io.BytesIO((d / ANALYSIS_FILE).read_bytes()))  # read at once: no file stays open
        self._cache: dict = {}
        self._lock = threading.Lock()
        self.groups: list[dict] = meta["groups"]
        self.ng = len(self.groups)
        self.dets: list[dict] = meta["coarse"]["dets"]

    def arr(self, name: str) -> np.ndarray:
        with self._lock:
            if name not in self._cache:
                self._cache[name] = self._z[name]
            return self._cache[name]

    # -- helpers ------------------------------------------------------------------------------------------------
    @property
    def channels(self) -> list[dict]:
        return self.data.scene["channels"]

    def _wch(self, state_id: str) -> np.ndarray:
        return self.data.weights(self.data._state(state_id))

    def _flux_w(self, state_id: str) -> float:
        st = self.data._state(state_id)
        return float(sum(st["drive"].get(ch["key"], 0.0) * ch["flux_w"] for ch in self.channels))

    @staticmethod
    def _view(view: str) -> int:
        return 1 if view == "cone" else 0

    def coarse_area(self, k: int) -> np.ndarray:
        """Surface area (mm^2) of every pixel of the coarse map of detector k."""
        c = self.dets[k]
        f, cnu, cnv = c["factor"], c["nu"], c["nv"]
        a = np.asarray(self.data.arrays[f"area_map_{k}"], dtype=np.float64)
        pad = np.zeros((cnv * f, cnu * f))
        pad[: a.shape[0], : a.shape[1]] = a
        return pad.reshape(cnv, f, cnu, f).sum(axis=(1, 3))

    def to_quantity(self, y_lm: np.ndarray, k: int, view: str) -> np.ndarray:
        """Luminous flux per coarse pixel -> luminance (cd/m2, viewing cone) or exitance (lm/m2), like the main map."""
        det = self.data.scene["detectors"][k]
        area_m2 = self.coarse_area(k) * 1e-6
        with np.errstate(divide="ignore", invalid="ignore"):
            if view == "cone":
                sin2 = np.sin(np.radians(det["cone_deg"])) ** 2 * max(float(det.get("view_cos", 1.0)), 1e-6)  # (a receiver's cone is about its view direction)
                return np.where(area_m2 > 0, y_lm / (area_m2 * np.pi * sin2), 0.0)
            return np.where(area_m2 > 0, y_lm / area_m2, 0.0)

    def _slice(self, flat: np.ndarray, k: int) -> np.ndarray:
        c = self.dets[k]
        return flat[..., c["offset"] : c["offset"] + c["nu"] * c["nv"]]

    def _combine_maps(self, name: str, state_id: str, view: str) -> np.ndarray:
        """Luminous flux (lm) of a map array (nch, 2, ..., ncoarse) combined over the channels of a state: (..., ncoarse)."""
        a = self.arr(name)[:, self._view(view)].astype(np.float64)
        return np.tensordot(self._wch(state_id), a, axes=(0, 0)) * spectral.KM

    def _valid(self, k: int, roi: list[float] | None = None) -> np.ndarray:
        area = self.coarse_area(k)
        valid = area > 0.3 * np.median(area[area > 0]) if (area > 0).any() else area > 0
        if roi:
            u0, v0, u1, v1 = roi
            nv, nu = area.shape
            xs, ys = (np.arange(nu) + 0.5) / nu, (np.arange(nv) + 0.5) / nv
            valid = valid & ((xs[None, :] >= u0) & (xs[None, :] <= u1) & (ys[:, None] >= v0) & (ys[:, None] <= v1))
        return valid

    def group_name(self, g: int) -> str:
        return "Direct (no tagged face)" if g < 0 else self.groups[g]["name"]

    def group_color(self, g: int) -> str:
        return "#9aa0a6" if g < 0 else self.groups[g]["color"]

    def _gface_lookup(self) -> dict:
        with self._lock:
            if "lookup" not in self._cache:
                body_id = {b["index"]: b["id"] for b in self.meta["bodies"]}
                gb, gf = self._z["gface_body"], self._z["gface_face"]
                self._cache["lookup"] = {(body_id[int(b)], int(f)): i for i, (b, f) in enumerate(zip(gb, gf))}
            return self._cache["lookup"]

    def gface_of(self, instance: str, face: int) -> int | None:
        return self._gface_lookup().get((instance, int(face)))

    def _inst_docs(self) -> dict:
        with self._lock:
            if "insts" not in self._cache:
                try:
                    doc = store.read_json(store.rev_dir(self.rid) / "optical.json") or {}
                    self._cache["insts"] = {i["id"]: i for i in doc.get("instances", [])}
                except Exception:  # noqa: BLE001 - the revision may be gone
                    self._cache["insts"] = {}
            return self._cache["insts"]

    def face_entry(self, gface: int) -> dict:
        body = self.meta["bodies"][int(self.arr("gface_body")[gface])]
        inst = self._inst_docs().get(body["id"])
        return {
            "gface": int(gface), "instance": body["id"], "path": body["path"],
            "partName": tags.display_name(inst) if inst else body["name"],
            "face": int(self.arr("gface_face")[gface]), "areaMm2": _f(self.arr("gface_area")[gface], 3),
        }

    def geometry(self) -> list[dict | None]:
        """Where every group is (centroid, mean normal, box, in model units), from the revision's optical mesh; ``None`` when unknown."""
        with self._lock:
            if "geometry" in self._cache:
                return self._cache["geometry"]
        from .optical_mesh import face_info, load_optical

        out: list[dict | None] = [None] * self.ng
        try:
            insts = self._inst_docs()
            opt = load_optical(store.rev_dir(self.rid) / "optical.npz")
        except Exception:  # noqa: BLE001 - the revision may be gone; geometry is only a convenience
            insts, opt = {}, {}
        for g, gd in enumerate(self.groups):
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            csum, nsum, asum = np.zeros(3), np.zeros(3), 0.0
            for f in gd["faces"][:200]:
                inst = insts.get(f.get("instance"))
                if inst is None:
                    continue
                if f.get("face") is None:  # the whole part
                    b = inst.get("bbox")
                    if b:
                        lo, hi = np.minimum(lo, b[0]), np.maximum(hi, b[1])
                    continue
                info = face_info(opt, inst, int(f["face"]))
                if info is None:
                    continue
                a = max(info["area"], 1e-12)
                lo, hi = np.minimum(lo, info["bbox"][0]), np.maximum(hi, info["bbox"][1])
                csum += np.asarray(info["centroid"]) * a
                nsum += np.asarray(info["normal"]) * a
                asum += a
            if not np.isfinite(lo).all():
                continue
            nl = float(np.linalg.norm(nsum))
            out[g] = {
                "bbox": [lo.tolist(), hi.tolist()],
                "centroid": (csum / asum).tolist() if asum > 0 else ((lo + hi) / 2).tolist(),
                "normal": (nsum / nl).tolist() if nl > 1e-9 * max(asum, 1e-12) and asum > 0 else None,
                "flat": bool(asum > 0 and nl / asum > 0.98),
            }
        with self._lock:
            self._cache["geometry"] = out
        return out

    # -- overview -----------------------------------------------------------------------------------------------
    def overview(self, state_id: str, view: str, det: int) -> dict:
        """Per group: its share of the exit flux (as last group touched, and touched at all), the flux that hits it and what it absorbs."""
        grp = self._slice(self._combine_maps("grp_map", state_id, view), det)  # (ng + 1, ncoarse)
        touch = self._slice(self._combine_maps("touch_map", state_id, view), det)
        total = float(grp.sum())
        flux_w = max(self._flux_w(state_id), 1e-30)
        face_t = np.tensordot(self._wch(state_id), self.arr("face_t"), axes=(0, 0)) / flux_w  # (nface, 4) fractions of the emitted flux
        gface_group = self.arr("gface_group")
        out_groups = []
        geom = self.geometry()
        for g in range(self.ng):
            sel = gface_group == g
            ft = face_t[sel].sum(axis=0) if sel.any() else np.zeros(4)
            gd = self.groups[g]
            out_groups.append(
                {
                    "index": g, "id": gd["id"], "name": gd["name"], "color": gd["color"], "auto": gd["auto"], "areaMm2": _f(gd["area"], 3), "faces": gd["faces"], "geometry": geom[g],
                    "lastFraction": _f(grp[g + 1].sum() / total) if total > 0 else 0.0,
                    "touchedFraction": _f(touch[g].sum() / total) if total > 0 else 0.0,
                    "incident": _f(ft[0]), "absorbed": _f(ft[1]), "reflected": _f(ft[2]), "transmitted": _f(ft[3]),
                    "absorbedOfIncident": _f(ft[1] / ft[0]) if ft[0] > 0 else 0.0,
                }
            )
        return {
            "groups": out_groups,
            "directFraction": _f(grp[0].sum() / total) if total > 0 else 0.0,
            "exitLm": _f(total), "view": view, "detector": det,
            "map": {"nu": self.dets[det]["nu"], "nv": self.dets[det]["nv"]},
            "loggedRays": int(min(self.meta["logEnd"], self.meta["nRays"])), "nRays": self.meta["nRays"],
        }

    def group_map(self, state_id: str, view: str, det: int, group: int | None, mode: str) -> dict:
        """Coarse map of one group: ``last`` (light whose last tagged face is the group), ``touched``, ``untouched`` or ``all``."""
        tot = self._slice(self._combine_maps("tot_map", state_id, view), det)
        if mode == "all" or group is None:
            m = tot
        elif mode == "touched":
            m = self._slice(self._combine_maps("touch_map", state_id, view), det)[group]
        elif mode == "untouched":
            m = tot - self._slice(self._combine_maps("touch_map", state_id, view), det)[group]
        else:  # last
            m = self._slice(self._combine_maps("grp_map", state_id, view), det)[group + 1]
        c = self.dets[det]
        total = float(tot.sum())
        return {
            "nu": c["nu"], "nv": c["nv"], "mode": mode, "group": group,
            "values": np.round(self.to_quantity(m.reshape(c["nv"], c["nu"]), det, view), 3).tolist(),
            "fraction": _f(m.sum() / total) if total > 0 else 0.0,
            "unit": "cd/m2" if view == "cone" else "lm/m2",
        }

    # -- the face inspector and the surface hit map ---------------------------------------------------------------
    def face_report(self, state_id: str, gface: int) -> dict:
        flux_w = max(self._flux_w(state_id), 1e-30)
        wch = self._wch(state_id)
        ft = np.tensordot(wch, self.arr("face_t")[:, gface], axes=(0, 0)) / flux_w
        g = int(self.arr("gface_group")[gface])
        out = {
            "face": self.face_entry(gface),
            "group": None if g < 0 else {"index": g, "name": self.groups[g]["name"], "color": self.groups[g]["color"]},
            "incident": _f(ft[0]), "absorbed": _f(ft[1]), "reflected": _f(ft[2]), "transmitted": _f(ft[3]),
            "tagged": g >= 0,
        }
        pos = np.nonzero(self.arr("tface_gface") == gface)[0]
        if len(pos):
            t = int(pos[0])
            frm = np.tensordot(wch, self.arr("face_from")[:, t], axes=(0, 0)) / flux_w
            to = np.tensordot(wch, self.arr("face_to")[:, t], axes=(0, 0)) / flux_w
            out["comesFrom"] = self._slots(frm, source=True)
            out["goesTo"] = self._slots(to, source=False)
        return out

    def _slots(self, v: np.ndarray, source: bool) -> list[dict]:
        rows = []
        tot = float(v.sum())
        for s in range(kernels.NSLOT):
            if v[s] <= 0:
                continue
            if s == 0:
                name, color, gi = ("The LED (no tagged face before)", "#f2c744", -1) if source else ("Exit surface", "#3ddc97", -2)
            elif s == kernels.SL_LOST:
                name, color, gi = "Absorbed or lost", "#8a8f98", -3
            elif s == kernels.SL_LEAK:
                name, color, gi = "Escaped through other surfaces", "#d9534f", -4
            else:
                gi = s - 1
                if gi >= self.ng:
                    continue
                name, color = self.groups[gi]["name"], self.groups[gi]["color"]
            rows.append({"slot": s, "group": gi, "name": name, "color": color, "fraction": _f(v[s] / tot) if tot > 0 else 0.0, "ofEmitted": _f(v[s])})
        rows.sort(key=lambda r: -r["fraction"])
        return rows

    def hit_map(self, state_id: str, quantity: str) -> dict:
        """Flux (fraction of the emitted flux) that arrives at / is absorbed by every CAD face, to paint on the model."""
        flux_w = max(self._flux_w(state_id), 1e-30)
        ft = np.tensordot(self._wch(state_id), self.arr("face_t"), axes=(0, 0)) / flux_w
        col = {"incident": 0, "absorbed": 1, "reflected": 2, "transmitted": 3}.get(quantity, 0)
        area = self.arr("gface_area")
        idx = np.nonzero(ft[:, col] > 0)[0]
        body_id = {b["index"]: b["id"] for b in self.meta["bodies"]}
        gb, gf = self.arr("gface_body"), self.arr("gface_face")
        faces = [
            {"instance": body_id[int(gb[i])], "face": int(gf[i]), "value": float(ft[i, col]), "areaMm2": _f(area[i], 3), "density": float(ft[i, col] / max(area[i], 1e-9))}
            for i in idx
        ]
        return {"quantity": quantity, "faces": faces, "maxDensity": max((f["density"] for f in faces), default=0.0), "total": _f(float(ft[:, col].sum()))}

    # -- logs ---------------------------------------------------------------------------------------------------
    def _dep(self) -> dict[str, np.ndarray]:
        if "dep_rid" not in self._z.files:
            return {}
        return {k: self.arr(f"dep_{k}") for k in ("rid", "pix", "wy", "wyc", "lastg", "nb", "tag", "ch")}

    def _dep_weight(self, state_id: str, view: str, dep: dict) -> np.ndarray:
        y = dep["wyc"] if view == "cone" else dep["wy"]
        return y.astype(np.float64) * self._wch(state_id)[dep["ch"]] * spectral.KM

    def region_mask(self, det: int, polygon: list[list[float]]) -> np.ndarray:
        """Boolean per fine pixel of the detector: its centre (u, v as fractions of the map) lies inside the polygon."""
        c = self.dets[det]
        nu, nv = c["fine_nu"], c["fine_nv"]
        xs, ys = (np.arange(nu) + 0.5) / nu, (np.arange(nv) + 0.5) / nv
        px, py = np.meshgrid(xs, ys)
        return _points_in_polygon(px.ravel(), py.ravel(), np.asarray(polygon, dtype=np.float64))

    def _dep_det(self, dep: dict) -> np.ndarray:
        """The detector (index) each deposit of the log landed on, from the fine pixel layout."""
        offs = np.array([c["fine_offset"] for c in self.dets], dtype=np.int64)
        return np.searchsorted(offs, dep["pix"].astype(np.int64), side="right") - 1

    def _region_rows(self, dep: dict, det: int, polygon: list[list[float]]) -> np.ndarray:
        c = self.dets[det]
        inside = self.region_mask(det, polygon)
        loc = dep["pix"].astype(np.int64) - c["fine_offset"]
        ok = (loc >= 0) & (loc < c["fine_nu"] * c["fine_nv"])
        res = np.zeros(len(loc), dtype=bool)
        res[ok] = inside[loc[ok]]
        return res

    def region_report(self, state_id: str, view: str, det: int, polygon: list[list[float]]) -> dict:
        """Where does the light that lands inside a region of the exit map come from?"""
        dep = self._dep()
        if not dep:
            raise FileNotFoundError("No exit deposits were logged for this run")
        w = self._dep_weight(state_id, view, dep)
        total = float(w.sum())
        sel = self._region_rows(dep, det, polygon)
        ws = w * sel
        flux = float(ws.sum())
        out: dict = {"fluxFraction": _f(flux / total) if total > 0 else 0.0, "rows": int(sel.sum())}
        if flux <= 0:
            return {**out, "byGroup": [], "byLed": [], "byBounces": [], "byEvent": [], "families": []}
        lg = dep["lastg"].astype(np.int64)
        by = np.bincount(lg + 1, weights=ws, minlength=self.ng + 1)
        out["byGroup"] = [
            {"group": int(g) - 1, "name": self.group_name(int(g) - 1), "color": self.group_color(int(g) - 1), "fraction": _f(by[g] / flux)}
            for g in np.argsort(-by) if by[g] > 0
        ]
        led_of = np.array([ch["led_id"] for ch in self.channels])
        names = {ch["led_id"]: ch["led_name"] for ch in self.channels}
        leds = led_of[dep["ch"]]
        out["byLed"] = sorted(
            [{"led": str(l), "name": names[str(l)], "fraction": _f(ws[leds == l].sum() / flux)} for l in np.unique(leds)], key=lambda r: -r["fraction"]
        )
        nb = dep["nb"].astype(np.int64)
        out["byBounces"] = [{"label": lab, "fraction": _f(ws[(nb >= lo) & (nb <= hi)].sum() / flux)} for lo, hi, lab in BOUNCE_BINS]
        out["meanBounces"] = _f(float((ws * nb).sum() / flux), 2)
        tag3 = dep["tag"][:, 3]
        out["byEvent"] = [{"name": EVENT_LABELS[e], "perRay": _f(float((ws * ((tag3 >> (8 * e)) & 255)).sum() / flux), 2)} for e in range(6)]
        fams = self._families(dep, w, sel)[:6]
        for f in fams:
            f["colors"] = [self.group_color(g) for g in f["route"]]
        out["families"] = fams
        return out

    # -- route families -----------------------------------------------------------------------------------------
    @staticmethod
    def _route_of(tag: np.ndarray) -> tuple[list[int], int]:
        r, ln = int(tag[1]) & kernels._M48, int(tag[1]) >> 48
        return [((r >> (6 * i)) & 63) - 1 for i in range(min(ln, 8))], ln

    def _led_index(self) -> tuple[list[str], np.ndarray]:
        """Distinct LEDs (in order) and the LED number of every channel."""
        led_ids = [ch["led_id"] for ch in self.channels]
        uniq = list(dict.fromkeys(led_ids))
        return uniq, np.array([uniq.index(l) for l in led_ids], dtype=np.int64)

    def _families(self, dep: dict, w: np.ndarray, sel: np.ndarray | None = None) -> list[dict]:
        if sel is None:
            sel = np.ones(len(w), dtype=bool)
        idx = np.nonzero(sel & (w > 0))[0]
        if not len(idx):
            return []
        uniq, led_of_ch = self._led_index()
        keys = np.stack([led_of_ch[dep["ch"][idx]], dep["tag"][idx, 2]], axis=1)
        uk, first, inv = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)
        fl = np.bincount(inv, weights=w[idx], minlength=len(uk))
        cnt = np.bincount(inv, minlength=len(uk))
        bsum = np.bincount(inv, weights=w[idx] * dep["nb"][idx], minlength=len(uk))
        total = float(w[idx].sum())
        led_names = {ch["led_id"]: ch["led_name"] for ch in self.channels}
        out = []
        for k in np.argsort(-fl)[:40]:
            route, ln = self._route_of(dep["tag"][idx[first[k]]])
            led_id = uniq[int(uk[k, 0])]
            names = [self.groups[g]["name"] if 0 <= g < self.ng else "?" for g in route]
            mid = names or ["no tagged face"]
            label = " > ".join([led_names[led_id], *mid, *(["..."] if ln > 8 else []), "Exit"])
            out.append(
                {
                    "id": f"{int(uk[k, 0])}:{int(uk[k, 1])}", "led": led_id, "ledName": led_names[led_id], "route": route, "routeLength": ln,
                    "label": label, "fraction": _f(fl[k] / total), "deposits": int(cnt[k]), "meanBounces": _f(bsum[k] / max(fl[k], 1e-30), 1),
                }
            )
        return out

    def families(self, state_id: str, view: str) -> dict:
        dep = self._dep()
        if not dep:
            raise FileNotFoundError("No exit deposits were logged for this run")
        fams = self._families(dep, self._dep_weight(state_id, view, dep))
        for f in fams:
            f["colors"] = [self.group_color(g) for g in f["route"]]
        return {"families": fams}

    def flow(self, state_id: str, view: str, det: int, bands: int = 4) -> dict:
        """Routes with the share of the exit light they deliver to each band of the exit surface (cut along its longer side)."""
        dep = self._dep()
        if not dep:
            raise FileNotFoundError("No exit deposits were logged for this run")
        w = self._dep_weight(state_id, view, dep)
        fams = self._families(dep, w)
        for f in fams:
            f["colors"] = [self.group_color(g) for g in f["route"]]
        c = self.dets[det]
        nu, nv = c["nu"], c["nv"]
        along = "u" if nu >= nv else "v"
        total = max(float(w.sum()), 1e-30)
        cp_all = self._cmap()[dep["pix"]] - c["offset"]
        in_det = (cp_all >= 0) & (cp_all < nu * nv)
        pos = np.where(in_det, (cp_all % nu) if along == "u" else (cp_all // nu), 0)
        band = np.clip((pos * bands) // (nu if along == "u" else nv), 0, bands - 1)
        matrix = []
        for f in fams:
            sel = self._family_rows(dep, f["id"]) & in_det
            matrix.append([_f(float(x) / total, 5) for x in np.bincount(band[sel], weights=w[sel], minlength=bands)])
        return {"families": fams, "bands": bands, "along": along, "matrix": matrix, "covered": _f(sum(f["fraction"] for f in fams))}

    def _family_rows(self, dep: dict, family: str) -> np.ndarray:
        led_i, _, h = family.partition(":")
        _, led_of_ch = self._led_index()
        return (led_of_ch[dep["ch"]] == int(led_i)) & (dep["tag"][:, 2] == int(h))

    def family_map(self, state_id: str, view: str, det: int, family: str) -> dict:
        dep = self._dep()
        w = self._dep_weight(state_id, view, dep)
        sel = self._family_rows(dep, family)
        c = self.dets[det]
        cp = self._cmap()[dep["pix"][sel]] - c["offset"]
        ok = (cp >= 0) & (cp < c["nu"] * c["nv"])
        m = np.bincount(cp[ok], weights=w[sel][ok], minlength=c["nu"] * c["nv"])
        return {
            "nu": c["nu"], "nv": c["nv"], "family": family, "fraction": _f(m.sum() / max(w.sum(), 1e-30)),
            "values": np.round(self.to_quantity(m.reshape(c["nv"], c["nu"]), det, view), 3).tolist(), "unit": "cd/m2" if view == "cone" else "lm/m2",
        }

    def _cmap(self) -> np.ndarray:
        """Coarse pixel of every fine pixel (rebuilt from the detector layout)."""
        with self._lock:
            if "cmap" not in self._cache:
                dets = self.data.scene["detectors"]
                nuv = np.array([[d["nu"], d["nv"]] for d in dets], dtype=np.int64)
                off = np.array([d["offset"] for d in dets], dtype=np.int64)
                total = int(sum(d["nu"] * d["nv"] for d in dets))
                self._cache["cmap"] = tags.coarse_layout(nuv, off, len(dets), total)["cmap"]
            return self._cache["cmap"]

    def _dep_select(self, state_id: str, view: str, flt: dict, dep: dict) -> tuple[np.ndarray, np.ndarray]:
        """Weight of every exit deposit and the mask of the deposits that match a ray filter (the filter keys of choose_rays)."""
        wch = self._wch(state_id)
        led_ids = np.array([ch["led_id"] for ch in self.channels])
        ch_ok = (wch > 0) & ((led_ids == flt["led"]) if flt.get("led") else True)
        w = self._dep_weight(state_id, view, dep)
        sel = ch_ok[dep["ch"]] & (w > 0)
        if flt.get("detector") is not None:  # only the light that reached this exit surface / receiver
            sel &= self._dep_det(dep) == int(flt["detector"])
        if flt.get("region"):
            sel &= self._region_rows(dep, int(flt["region"].get("detector", 0)), flt["region"]["polygon"])
        if flt.get("lastGroup") is not None:
            sel &= dep["lastg"] == int(flt["lastGroup"])
        if flt.get("touched") is not None:  # one group, or a list of groups that all have to be touched
            for g in (flt["touched"] if isinstance(flt["touched"], (list, tuple)) else [flt["touched"]]):
                sel &= ((dep["tag"][:, 0] >> int(g)) & 1).astype(bool)
        if flt.get("family"):
            sel &= self._family_rows(dep, str(flt["family"]))
        if flt.get("bounces"):
            lo, hi = flt["bounces"]
            sel &= (dep["nb"] >= lo) & (dep["nb"] <= hi)
        return w, sel

    def filter_map(self, state_id: str, view: str, det: int, flt: dict) -> dict:
        """Where the light of the rays that match a filter lands on the exit surface (only light that reaches the exit lands anywhere)."""
        dep = self._dep()
        if not dep:
            raise FileNotFoundError("No exit deposits were logged for this run")
        w, sel = self._dep_select(state_id, view, flt, dep)
        if flt.get("outcome") in ("absorbed", "escaped", "lost"):
            sel = np.zeros_like(sel)  # these rays never reach the exit surface
        c = self.dets[det]
        cp = self._cmap()[dep["pix"][sel]] - c["offset"]
        ok = (cp >= 0) & (cp < c["nu"] * c["nv"])
        m = np.bincount(cp[ok], weights=w[sel][ok], minlength=c["nu"] * c["nv"])
        return {
            "nu": c["nu"], "nv": c["nv"], "mode": "rays", "group": None, "fraction": _f(m.sum() / max(w.sum(), 1e-30)),
            "values": np.round(self.to_quantity(m.reshape(c["nv"], c["nu"]), det, view), 3).tolist(), "unit": "cd/m2" if view == "cone" else "lm/m2",
        }

    # -- choosing rays to draw ----------------------------------------------------------------------------------
    def choose_rays(self, state_id: str, view: str, flt: dict, count: int, seed: int = 1) -> dict:
        """Pick rays that match a filter, with the probability of a ray proportional to the flux it delivers to the exit.

        Filter keys (all optional): ``led``, ``detector`` (the exit surface or receiver the light reached), ``region`` ({detector, polygon}), ``lastGroup`` (-1 = none), ``touched`` (group),
        ``family``, ``outcome`` (exit / absorbed / escaped / lost), ``bounces`` ([min, max] interactions per deposit).
        Returns ``rays``: a list of (channel number, ray number, weight) and the number of rays that matched.
        """
        wch = self._wch(state_id)
        led_ids = np.array([ch["led_id"] for ch in self.channels])
        ch_ok = (wch > 0) & ((led_ids == flt["led"]) if flt.get("led") else True)
        dep_keys = ("region", "lastGroup", "family", "bounces", "detector")
        outcome = flt.get("outcome") or ("exit" if any(flt.get(k) is not None for k in dep_keys) else None)
        use_dep = outcome == "exit" or any(flt.get(k) is not None for k in dep_keys)
        rng = np.random.default_rng(seed)
        if use_dep:
            dep = self._dep()
            if not dep:
                raise FileNotFoundError("No exit deposits were logged for this run")
            w, sel = self._dep_select(state_id, view, flt, dep)
            ch, rid, wt = dep["ch"][sel].astype(np.int64), dep["rid"][sel].astype(np.int64), w[sel]
            total_all = float(w.sum())
        else:
            if "end_rid" not in self._z.files:
                raise FileNotFoundError("No ray log was kept for this run")
            end = {k: self.arr(f"end_{k}") for k in ("rid", "out", "nb", "tag", "ch")}
            sel = ch_ok[end["ch"]]
            if flt.get("touched") is not None:
                sel &= ((end["tag"][:, 0] >> int(flt["touched"])) & 1).astype(bool)
            if outcome in OUTCOMES and OUTCOMES[outcome]:
                sel &= np.isin(end["out"], OUTCOMES[outcome])
            ch, rid = end["ch"][sel].astype(np.int64), end["rid"][sel].astype(np.int64)
            wt = np.ones(len(rid))
            total_all = 0.0
        if not len(rid):
            return {"rays": [], "matched": 0, "fluxFraction": 0.0}
        key = ch * (1 << 40) + rid
        uk, inv = np.unique(key, return_inverse=True)
        wsum = np.bincount(inv.reshape(-1), weights=wt, minlength=len(uk))
        matched = len(uk)
        if matched > count:
            pick = rng.choice(matched, size=count, replace=False, p=wsum / wsum.sum())
        else:
            pick = np.arange(matched)
        chosen = [(int(uk[i] >> 40), int(uk[i] & ((1 << 40) - 1)), float(wsum[i])) for i in pick]
        return {"rays": chosen, "matched": int(matched), "fluxFraction": _f(float(wsum.sum()) / total_all) if total_all > 0 else 0.0}

    # -- guidance ------------------------------------------------------------------------------------------------
    def guidance(self, state_id: str, view: str, det: int, roi: list[float] | None = None, target: float | None = None) -> dict:
        """Deficit map (target - actual) and a first-order fit of per-group scale factors that would flatten the map."""
        from scipy.optimize import lsq_linear

        c = self.dets[det]
        grp = self._slice(self._combine_maps("grp_map", state_id, view), det)  # (ng + 1, ncoarse), lm
        cols = [self.to_quantity(grp[g].reshape(c["nv"], c["nu"]), det, view) for g in range(self.ng + 1)]
        total = np.sum(cols, axis=0)
        valid = self._valid(det, roi)
        if not valid.any():
            raise ValueError("No valid pixels in the region of interest")
        mean = float(total[valid].mean())
        tgt = float(target) if target and target > 0 else mean
        deficit = np.where(valid, tgt - total, 0.0)

        def stats(m: np.ndarray) -> dict:
            v = m[valid]
            mu = float(v.mean())
            return {
                "cv": _f(float(v.std() / mu)) if mu > 0 else 0.0,
                "minOverAvg": _f(float(v.min() / mu)) if mu > 0 else 0.0,
                "maxOverAvg": _f(float(v.max() / mu)) if mu > 0 else 0.0,
                "mean": _f(mu, 3),
            }

        A = np.stack([col[valid] for col in cols], axis=1) / max(tgt, 1e-30)  # contribution of every group relative to the target level
        active = np.nonzero(np.abs(A).sum(axis=0) > 1e-9)[0]
        s = np.ones(self.ng + 1)
        if len(active):
            Aa = A[:, active]
            npix = Aa.shape[0]
            lam = 0.02 * npix  # a mild pull towards "leave it as it is": tiny contributions do not get silly factors
            M = np.vstack([Aa, np.sqrt(lam) * 0.1 * np.eye(len(active))])
            b = np.concatenate([np.ones(npix), np.sqrt(lam) * 0.1 * np.ones(len(active))])
            s[active] = lsq_linear(M, b, bounds=(0.25, 4.0)).x
        predicted = sum(s[g] * cols[g] for g in range(self.ng + 1))
        base_total = float(total[valid].sum())
        sugg = []
        for g in active:
            share = float(cols[g][valid].sum() / max(base_total, 1e-30))
            if g == 0 or share < 0.005:
                continue
            gi = int(g) - 1
            sugg.append(
                {
                    "group": gi, "name": self.groups[gi]["name"], "color": self.groups[gi]["color"], "share": _f(share),
                    "scale": _f(float(s[g]), 3), "changePct": _f(100.0 * (float(s[g]) - 1.0), 1),
                    "advice": "send more light to the exit" if s[g] > 1.05 else "send less light to the exit" if s[g] < 0.95 else "leave as is",
                }
            )
        sugg.sort(key=lambda r: -abs(r["changePct"]) * r["share"])
        return {
            "nu": c["nu"], "nv": c["nv"], "unit": "cd/m2" if view == "cone" else "lm/m2", "target": _f(tgt, 3),
            "deficit": np.round(deficit, 3).tolist(), "actual": np.round(total, 3).tolist(), "valid": valid.astype(int).tolist(),
            "current": stats(total), "predicted": stats(np.where(valid, predicted, 0.0)), "suggestions": sugg,
            "note": "First-order estimate: each group's footprint is scaled on its own. Changing a surface also moves where its light lands.",
        }


def _points_in_polygon(px: np.ndarray, py: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd rule, vectorised over points."""
    inside = np.zeros(len(px), dtype=bool)
    n = len(poly)
    if n < 3:
        return inside
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = (yi > py) != (yj > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (xj - xi) * (py - yi) / (yj - yi) + xi
        inside ^= cond & (px < xint)
        j = i
    return inside
