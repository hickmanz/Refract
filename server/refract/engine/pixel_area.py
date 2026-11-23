"""Exact surface area of every exit-map pixel.

The luminance of a pixel is its flux divided by the surface area of the exit surface inside it, so that area has to be
exact: an estimate by random sampling puts the same fixed error (about 10 % with a few dozen samples per pixel) into every
run, whatever the number of rays. Here every triangle of the exit surface is clipped against the pixel grid (in map
coordinates) and its true 3D area is shared out in proportion to the clipped parts.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit


@njit(cache=True)
def _clip(inx, iny, n, axis, val, sign, outx, outy):
    """Keep the part of a convex polygon where ``sign * (coordinate - val) >= 0``; returns the new vertex count."""
    m = 0
    if n == 0:
        return 0
    sx, sy = inx[n - 1], iny[n - 1]
    sd = sign * ((sx if axis == 0 else sy) - val)
    for i in range(n):
        ex, ey = inx[i], iny[i]
        ed = sign * ((ex if axis == 0 else ey) - val)
        if ed >= 0.0:
            if sd < 0.0:
                t = sd / (sd - ed)
                outx[m] = sx + (ex - sx) * t
                outy[m] = sy + (ey - sy) * t
                m += 1
            outx[m] = ex
            outy[m] = ey
            m += 1
        elif sd >= 0.0:
            t = sd / (sd - ed)
            outx[m] = sx + (ex - sx) * t
            outy[m] = sy + (ey - sy) * t
            m += 1
        sx, sy, sd = ex, ey, ed
    return m


@njit(cache=True)
def _area_kernel(xy, area3d, nu, nv, amap):
    ax = np.empty(16)
    ay = np.empty(16)
    bx = np.empty(16)
    by = np.empty(16)
    for t in range(xy.shape[0]):
        x0, y0 = xy[t, 0, 0], xy[t, 0, 1]
        x1, y1 = xy[t, 1, 0], xy[t, 1, 1]
        x2, y2 = xy[t, 2, 0], xy[t, 2, 1]
        a2 = 0.5 * abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))  # area in pixel units
        if a2 < 1e-14 or area3d[t] <= 0.0:
            continue  # edge-on to the map: nothing can land on it
        k = area3d[t] / a2  # mm^2 of surface per unit of map area
        fx0 = min(x0, min(x1, x2))
        fx1 = max(x0, max(x1, x2))
        fy0 = min(y0, min(y1, y2))
        fy1 = max(y0, max(y1, y2))
        ix0 = max(int(math.floor(fx0)), 0)
        ix1 = min(int(math.floor(fx1)), nu - 1)
        iy0 = max(int(math.floor(fy0)), 0)
        iy1 = min(int(math.floor(fy1)), nv - 1)
        if ix0 == ix1 and iy0 == iy1 and fx0 >= ix0 and fx1 <= ix0 + 1 and fy0 >= iy0 and fy1 <= iy0 + 1:
            amap[iy0, ix0] += area3d[t]
            continue
        for iy in range(iy0, iy1 + 1):
            for ix in range(ix0, ix1 + 1):
                ax[0], ay[0] = x0, y0
                ax[1], ay[1] = x1, y1
                ax[2], ay[2] = x2, y2
                n = _clip(ax, ay, 3, 0, float(ix), 1.0, bx, by)
                n = _clip(bx, by, n, 0, float(ix + 1), -1.0, ax, ay)
                n = _clip(ax, ay, n, 1, float(iy), 1.0, bx, by)
                n = _clip(bx, by, n, 1, float(iy + 1), -1.0, ax, ay)
                if n < 3:
                    continue
                s = 0.0
                for i in range(n):
                    j = i + 1 if i + 1 < n else 0
                    s += ax[i] * ay[j] - ax[j] * ay[i]
                amap[iy, ix] += 0.5 * abs(s) * k



def exact_pixel_areas(uv: np.ndarray, area3d: np.ndarray, nu: int, nv: int) -> np.ndarray:
    """Surface area (mm^2) per map pixel, shape (nv, nu).

    ``uv`` (ntri, 3, 2) holds the triangle corners in *pixel units* (0..nu, 0..nv); ``area3d`` their true areas in mm^2.
    Where several triangles project onto the same pixel their areas add up (as the sampled map did).
    """
    amap = np.zeros((int(nv), int(nu)))
    _area_kernel(np.ascontiguousarray(uv, dtype=np.float64), np.ascontiguousarray(area3d, dtype=np.float64), int(nu), int(nv), amap)
    return amap
