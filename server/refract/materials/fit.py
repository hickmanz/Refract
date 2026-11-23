"""Fit the scattering of a diffuser material (PC + TiO2, ...) to datasheet slab measurements.

Datasheets give the total light transmission (and often the haze) of a flat plaque of a given thickness. We model the
plaque as a slab with a uniform scattering coefficient ``mu_s`` and Henyey-Greenstein anisotropy ``g`` (the same
scattering model the simulator uses inside the light pipe), trace photons through it with Fresnel reflection and total
internal reflection at both faces, and search for the ``mu_s`` and ``g`` that reproduce the measured numbers.

* total transmission  = all light leaving the far face (including the Fresnel loss of the entry face), like an integrating sphere
* haze                = share of the transmitted light that leaves more than 2.5 degrees from the normal (ASTM D1003)

Only total transmission measured at one or more thicknesses fixes ``mu_s`` but not ``g`` (they trade off: it is the
reduced coefficient ``mu_s (1 - g)`` that matters). Without haze data ``g`` is therefore fixed to a typical value for
TiO2 in polycarbonate (0.6) and only ``mu_s`` is fitted. With haze data both are fitted.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit

from ..engine import kernels as K

HAZE_ANGLE_DEG = 2.5
DEFAULT_G = 0.6
MUS_MIN, MUS_MAX = 0.01, 400.0  # 1/mm


@njit(cache=True)
def _slab(n_photons, seed, thickness, mus, g, n_slab, ka, cos_haze):
    """-> (total transmitted fraction, transmitted fraction that is hazy). Collimated normal incidence from air."""
    st = np.empty(1, dtype=np.uint64)
    st[0] = np.uint64(seed)
    t_total = 0.0
    t_haze = 0.0
    r0, _ = K._fresnel(1.0, 1.0, n_slab)
    for _ in range(n_photons):
        w = 1.0 - r0
        z = 0.0
        dx, dy, dz = 0.0, 0.0, 1.0
        for _step in range(20000):
            # distance to the next scattering event
            if mus > 0.0:
                s = -math.log(max(K._rnd(st, 0), 1e-300)) / mus
            else:
                s = 1e30
            zn = z + dz * s
            if 0.0 < zn < thickness:
                w *= math.exp(-ka * s)
                z = zn
                dx, dy, dz = K._hg_dir(dx, dy, dz, g, K._rnd(st, 0), K._rnd(st, 0))
            else:
                zb = thickness if dz > 0.0 else 0.0
                sb = (zb - z) / dz
                w *= math.exp(-ka * sb)
                z = zb
                cosi = abs(dz)
                refl, cost = K._fresnel(cosi, n_slab, 1.0)
                if dz > 0.0:  # far face: light that gets out
                    wt = w * (1.0 - refl)
                    t_total += wt
                    if cost < cos_haze:
                        t_haze += wt
                else:
                    pass  # light leaving through the entry face is reflected light, not counted
                w *= refl
                dz = -dz
            if w < 1e-3:
                if K._rnd(st, 0) < 0.1:
                    w /= 0.1
                else:
                    break
            if w <= 0.0:
                break
    return t_total / n_photons, t_haze / n_photons


def simulate_slab(thickness_mm: float, mus: float, g: float, n: float = 1.58, ka: float = 0.0, photons: int = 40000, seed: int = 12345) -> tuple[float, float | None]:
    """Total transmission and haze of a diffusing slab at normal incidence."""
    total, hazy = _slab(int(photons), np.uint64(seed), float(thickness_mm), float(mus), float(g), float(n), float(ka), math.cos(math.radians(HAZE_ANGLE_DEG)))
    return float(total), (float(hazy / total) if total > 1e-12 else None)


def _clean_samples(samples: list[dict]) -> list[dict]:
    out = []
    for s in samples:
        try:
            t = float(s["thickness_mm"])
            tt = float(s["total_transmission"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("each sample needs a thickness_mm and a total_transmission") from None
        if t <= 0:
            raise ValueError("thickness must be > 0 mm")
        if not 0.0 < tt <= 1.0:
            raise ValueError("total transmission must be between 0 and 100 %")
        h = s.get("haze")
        haze = None if h is None or h == "" else float(h)
        if haze is not None and not 0.0 <= haze <= 1.0:
            raise ValueError("haze must be between 0 and 100 %")
        out.append({"thickness_mm": t, "total": tt, "haze": haze})
    if not out:
        raise ValueError("enter at least one thickness with its measured transmission")
    return out


def _cost(samples: list[dict], mus: float, g: float, n: float, photons: int, use_haze: bool) -> float:
    c = 0.0
    for s in samples:
        total, haze = _eval(s["thickness_mm"], mus, g, n, photons)
        c += (total - s["total"]) ** 2
        if use_haze and s["haze"] is not None and haze is not None:
            c += (haze - s["haze"]) ** 2
    return c


def _eval(thickness: float, mus: float, g: float, n: float, photons: int) -> tuple[float, float | None]:
    return simulate_slab(thickness, mus, g, n, 0.0, photons)


def _fit_mus(samples: list[dict], g: float, n: float, photons: int, use_haze: bool) -> tuple[float, float]:
    """Best mu_s for a given g: log grid, then golden-section refinement."""
    t_max = max(s["thickness_mm"] for s in samples)
    grid = np.geomspace(MUS_MIN, min(MUS_MAX, 40.0 / t_max), 18)  # optical thickness up to 40: a random walk of ~1000 steps
    costs = [_cost(samples, float(m), g, n, photons, use_haze) for m in grid]
    k = int(np.argmin(costs))
    lo = math.log(grid[max(k - 1, 0)])
    hi = math.log(grid[min(k + 1, len(grid) - 1)])
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c1, c2 = b - gr * (b - a), a + gr * (b - a)
    f1 = _cost(samples, math.exp(c1), g, n, photons, use_haze)
    f2 = _cost(samples, math.exp(c2), g, n, photons, use_haze)
    for _ in range(14):
        if f1 < f2:
            b, c2, f2 = c2, c1, f1
            c1 = b - gr * (b - a)
            f1 = _cost(samples, math.exp(c1), g, n, photons, use_haze)
        else:
            a, c1, f1 = c1, c2, f2
            c2 = a + gr * (b - a)
            f2 = _cost(samples, math.exp(c2), g, n, photons, use_haze)
    m = math.exp(0.5 * (a + b))
    return m, _cost(samples, m, g, n, photons, use_haze)


def fit_slab(samples: list[dict], host: str = "pc", rays: int = 40000, g: float | None = None) -> dict:
    """Fit ``mu_s`` (and ``g`` when haze data are given) to measured slab data."""
    from . import mie

    data = _clean_samples(samples)
    n = float(mie.host_index(host, np.array([550.0]))[0])
    photons = int(np.clip(rays, 5000, 400000))
    have_haze = any(s["haze"] is not None for s in data)
    if g is not None:
        g_grid = [float(np.clip(g, -0.95, 0.95))]
    elif have_haze:
        g_grid = [0.0, 0.3, 0.55, 0.75, 0.9]
    else:
        g_grid = [DEFAULT_G]
    best = None
    search_photons = min(photons, 20000)
    for gv in g_grid:
        m, c = _fit_mus(data, gv, n, search_photons, have_haze)
        if best is None or c < best[2]:
            best = (m, gv, c)
    mus, gv, cost = best
    predictions = []
    for s in data:
        total, haze = simulate_slab(s["thickness_mm"], mus, gv, n, 0.0, max(photons, 100000), seed=777)
        predictions.append(
            {
                "thickness_mm": s["thickness_mm"],
                "total_transmission": round(total, 4),
                "haze": None if haze is None else round(haze, 4),
                "measured_total": s["total"],
                "measured_haze": s["haze"],
            }
        )
    notes = []
    if not have_haze:
        notes.append(f"No haze was given, so g was fixed at {gv:g}. Enter haze values to fit g as well.")
    if any(abs(p["total_transmission"] - p["measured_total"]) > 0.03 for p in predictions):
        notes.append("The model cannot match every thickness closely: check the numbers, or the part may also absorb light.")
    return {
        "mus": float(mus),
        "g": float(gv),
        "cost": float(cost),
        "n": n,
        "predictions": predictions,
        "notes": notes,
        "material": {"type": "constant", "mus": round(float(mus), 5), "g": round(float(gv), 4)},
    }
