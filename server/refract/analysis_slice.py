"""Flux through a cut plane.

A sample of the run's rays is traced again (exactly, from the run seed and ray numbers) and every straight piece of every
ray that crosses the plane adds the flux it carries to a grid on the plane. The grid shows where, on the cut, the light
passes through, as a fraction of the emitted flux per mm² (the same unit as the surface maps).
"""

from __future__ import annotations

import numpy as np

from .engine import kernels
from .engine.replay import replay_arrays

OUTCOME_OTHER, OUTCOME_EXIT, OUTCOME_ABSORBED = 0, 1, 2
CHUNK = 3000


def plane_frame(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """In-plane axes u, v with u × v = normal. Axis-aligned cuts get the natural cyclic frame (x: y,z · y: z,x · z: x,y)."""
    n = normal / np.linalg.norm(normal)
    for axis in range(3):
        if abs(abs(n[axis]) - 1.0) < 1e-6:
            u = np.zeros(3)
            v = np.zeros(3)
            u[(axis + 1) % 3] = 1.0
            v[(axis + 2) % 3] = 1.0
            if n[axis] < 0:  # keep u × v = n
                u = -u
            return u, v
    u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def build_segments(ra, scene, srcs, state: str, count: int, seed: int, max_points: int = 320) -> dict:
    """Straight ray pieces of a uniform sample of the rays: ends, carried weight, flux scale of their ray, outcome of their ray."""
    wch = np.asarray(ra._wch(state), dtype=np.float64)
    flux_w = max(ra._flux_w(state), 1e-30)
    n_rays = int(ra.meta["nRays"])
    chans = ra.meta["channels"]
    rng = np.random.default_rng(seed)
    total_w = float(wch.sum())
    p0s, p1s, ws, scs, outs = [], [], [], [], []
    used = 0
    if total_w > 0:
        for ci, meta in enumerate(chans):
            if wch[ci] <= 0:
                continue
            n_c = int(max(1, round(count * wch[ci] / total_w)))
            n_c = min(n_c, n_rays)
            ids = np.sort(rng.choice(n_rays, size=n_c, replace=False)) if n_c < n_rays else np.arange(n_rays)
            scale = wch[ci] * (n_rays / n_c) / flux_w  # one sampled ray stands for n_rays/n_c rays of this channel
            for a in range(0, n_c, CHUNK):
                _, buf, pn, aux = replay_arrays(scene, srcs[meta["key"]], meta["seed"], ids[a:a + CHUNK], ra.meta["rrThreshold"], ra.meta["maxBounces"], max_points, ra.meta.get("solver", "cpu"))
                m = len(buf)
                used += m
                ev = aux[:, :, 1]
                valid_pt = np.arange(max_points)[None, :] < pn[:, None]
                outcome = np.zeros(m, dtype=np.int8)
                outcome[np.any((ev == kernels.EV_ABS) & valid_pt, axis=1)] = OUTCOME_ABSORBED
                outcome[np.any((ev == kernels.EV_EXIT) & valid_pt, axis=1)] = OUTCOME_EXIT
                seg = np.arange(max_points - 1)[None, :] < (pn - 1)[:, None]
                ray_idx = np.broadcast_to(np.arange(m)[:, None], seg.shape)[seg]
                p0s.append(buf[:, :-1][seg].astype(np.float32))
                p1s.append(buf[:, 1:][seg].astype(np.float32))
                ws.append(aux[:, :-1, 2][seg].astype(np.float32))
                scs.append(np.full(len(ray_idx), scale, dtype=np.float32))
                outs.append(outcome[ray_idx])
    if not p0s:
        z3, z1 = np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
        return {"p0": z3, "p1": z3, "w": z1, "scale": z1, "outcome": np.zeros(0, np.int8), "rays": 0}
    return {
        "p0": np.concatenate(p0s), "p1": np.concatenate(p1s), "w": np.concatenate(ws),
        "scale": np.concatenate(scs), "outcome": np.concatenate(outs), "rays": used,
    }


def _extent(scene, u: np.ndarray, v: np.ndarray, origin: np.ndarray) -> tuple[float, float, float, float]:
    verts = np.asarray(scene.V, dtype=np.float64).reshape(-1, 3)
    rel = verts - origin
    a, b = rel @ u, rel @ v
    pad_a, pad_b = 0.02 * (a.max() - a.min() + 1e-9), 0.02 * (b.max() - b.min() + 1e-9)
    return float(a.min() - pad_a), float(a.max() + pad_a), float(b.min() - pad_b), float(b.max() + pad_b)


def slice_flux(segs: dict, scene, origin, normal, direction: str = "both", outcome: str = "", grid: int = 140) -> dict:
    """Flux per mm² through the plane (fraction of the emitted flux), on a grid that covers the model."""
    origin = np.asarray(origin, dtype=np.float64)
    normal = np.asarray(normal, dtype=np.float64)
    normal = normal / np.linalg.norm(normal)
    u, v = plane_frame(normal)
    a0, a1, b0, b1 = _extent(scene, u, v, origin)
    la, lb = a1 - a0, b1 - b0
    if la >= lb:
        nu = int(grid)
        nv = int(max(4, round(grid * lb / la)))
    else:
        nv = int(grid)
        nu = int(max(4, round(grid * la / lb)))
    du, dv = la / nu, lb / nv

    p0, p1 = segs["p0"].astype(np.float64), segs["p1"].astype(np.float64)
    s0, s1 = (p0 - origin) @ normal, (p1 - origin) @ normal
    cross = (s0 * s1) < 0.0
    if direction == "along":
        cross &= s1 > s0
    elif direction == "against":
        cross &= s1 < s0
    if outcome == "exit":
        cross &= segs["outcome"] == OUTCOME_EXIT
    elif outcome == "absorbed":
        cross &= segs["outcome"] == OUTCOME_ABSORBED
    elif outcome == "other":
        cross &= segs["outcome"] == OUTCOME_OTHER
    idx = np.nonzero(cross)[0]
    values = np.zeros((nv, nu))
    if len(idx):
        t = s0[idx] / (s0[idx] - s1[idx])
        hit = p0[idx] + (p1[idx] - p0[idx]) * t[:, None] - origin
        iu = np.floor((hit @ u - a0) / du).astype(int)
        iv = np.floor((hit @ v - b0) / dv).astype(int)
        ok = (iu >= 0) & (iu < nu) & (iv >= 0) & (iv < nv)
        flux = (segs["w"][idx].astype(np.float64) * segs["scale"][idx])[ok]
        np.add.at(values, (iv[ok], iu[ok]), flux)
    total = float(values.sum())
    try:
        from scipy.ndimage import gaussian_filter

        values = gaussian_filter(values, sigma=1.0, mode="constant")
    except ImportError:  # the raw splat is fine, only noisier
        pass
    values = values / (du * dv)
    corners = [
        (origin + a0 * u + b0 * v).tolist(), (origin + a1 * u + b0 * v).tolist(),
        (origin + a1 * u + b1 * v).tolist(), (origin + a0 * u + b1 * v).tolist(),
    ]
    return {
        "nu": nu, "nv": nv, "values": values.tolist(), "max": float(values.max()) if values.size else 0.0,
        "total": total, "crossings": int(len(idx)), "rays": int(segs["rays"]),
        "corners": corners, "u": u.tolist(), "v": v.tolist(), "normal": normal.tolist(),
        "uMm": float(la), "vMm": float(lb), "direction": direction, "outcome": outcome,
    }
